"""
spool_worker.py - Worker Latar Belakang Penguras Antrean
========================================================
Thread daemon yang berjalan bersama MLLP server. Setiap `SPOOL_INTERVAL_SECONDS`
ia membaca antrean lokal dan mengirim ulang payload yang tertahan saat API
SIMGOS down.

Alur satu putaran (`flush_once`):
  1. Ambil batch baris PENDING (FIFO, default 10).
  2. Kirim ke SIMGOS lewat client.simgos_api.send_to_simgos.
  3. Berhasil (HTTP 200/201/204 + body success=true) → status SENT.
  4. Gagal jaringan/5xx (retryable)              → retry_count + 1, tunggu putaran berikutnya.
  5. Gagal permanen (4xx / payload rusak)        → status ABANDONED, tidak membajak antrean.
  6. Gagal berulang kali melewati SPOOL_MAX_RETRIES → status ABANDONED + log ERROR.
     Default SPOOL_MAX_RETRIES=0 (tanpa batas): selama SIMGOS sekadar tidak
     merespons, data terus dicoba dan TIDAK dibuang — antrean medis tidak
     boleh bergantung pada lama downtime.
"""

from __future__ import annotations

import os
import threading
import time
from typing import Optional

import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from config.settings import SPOOL_INTERVAL_SECONDS, SPOOL_MAX_RETRIES, SPOOL_BATCH_SIZE
from client.simgos_api import send_to_simgos, is_retryable_failure
from spooler import queue_manager
from utils.logger import get_logger

log = get_logger("spool_worker")


class SpoolWorker(threading.Thread):
    """Thread daemon yang menguras `message_spool` secara berkala."""

    def __init__(
        self,
        interval_seconds: Optional[int] = None,
        batch_size: Optional[int] = None,
        max_retries: Optional[int] = None,
    ):
        super().__init__(name="spool-worker", daemon=True)
        self.interval_seconds = int(
            interval_seconds if interval_seconds is not None else SPOOL_INTERVAL_SECONDS
        )
        self.batch_size       = int(batch_size if batch_size is not None else SPOOL_BATCH_SIZE)
        self.max_retries      = int(max_retries if max_retries is not None else SPOOL_MAX_RETRIES)
        self._stop_event      = threading.Event()

    # ------------------------------------------------------------------
    def stop(self) -> None:
        """Minta thread berhenti (tidak lagi menunggu interval berikutnya)."""
        self._stop_event.set()

    # ------------------------------------------------------------------
    def run(self) -> None:
        queue_manager.init_db()
        pending = queue_manager.count_pending()
        retry_label = "tanpa batas" if self.max_retries <= 0 else str(self.max_retries)
        log.info(
            f"Worker antrean aktif | interval={self.interval_seconds}s | "
            f"batch={self.batch_size} | max_retry={retry_label} | "
            f"db={queue_manager.db_path()} | pending_saat_ini={pending}"
        )
        if pending:
            log.warning(f"{pending} pesan menunggu di antrean lokal — akan dikirim ulang.")

        while not self._stop_event.is_set():
            try:
                self.flush_once()
            except Exception as e:                       # worker tidak boleh mati
                log.error(f"Error tak terduga di worker antrean: {e}", exc_info=True)
            # Tidur yang bisa dibangunkan lebih awal oleh stop()
            self._stop_event.wait(self.interval_seconds)

        queue_manager.close_connection()
        log.info("Worker antrean dihentikan.")

    # ------------------------------------------------------------------
    def flush_once(self) -> dict:
        """
        Jalankan satu putaran pengurasan antrean (dipakai juga oleh test/CLI).

        Returns:
            dict penghitung: checked, sent, retried, abandoned.
        """
        counters = {"checked": 0, "sent": 0, "retried": 0, "abandoned": 0}
        rows = queue_manager.get_pending_batch(self.batch_size)
        if not rows:
            return counters

        log.info(f"Menguras antrean: {len(rows)} pesan PENDING.")

        for row in rows:
            counters["checked"] += 1
            spool_id = int(row["id"])
            acc      = row["accession_number"] or "-"

            try:
                payload = queue_manager.decode_row(row)
            except Exception as e:
                # Payload rusak tidak akan pernah berhasil dikirim — jangan retry selamanya.
                queue_manager.mark_abandoned(spool_id, f"Payload JSON rusak: {e}")
                counters["abandoned"] += 1
                continue

            start = time.monotonic()
            try:
                resp = send_to_simgos(payload)
            except Exception as e:
                # Termasuk RuntimeError fail-closed kredensial: perlakukan sebagai
                # kegagalan sementara supaya data tetap aman di disk.
                queue_manager.increment_retry(
                    spool_id, f"Exception saat kirim: {str(e)[:200]}", self.max_retries
                )
                counters["retried"] += 1
                continue

            duration_ms = (time.monotonic() - start) * 1000

            if resp.success:
                queue_manager.mark_as_sent(spool_id)
                counters["sent"] += 1
                log.info(
                    f"✓ Antrean terkirim | id={spool_id} | acc={acc} | "
                    f"HTTP {resp.status_code} | {duration_ms:.1f}ms"
                )
            elif is_retryable_failure(resp):
                new_status = queue_manager.increment_retry(
                    spool_id,
                    f"HTTP {resp.status_code} | {resp.error}".strip(),
                    self.max_retries,
                )
                # retry_count sudah menyentuh batas → baris ditinggalkan, bukan lagi menunggu.
                if new_status == queue_manager.STATUS_ABANDONED:
                    counters["abandoned"] += 1
                else:
                    counters["retried"] += 1
            else:
                queue_manager.mark_abandoned(
                    spool_id, f"HTTP {resp.status_code} | {resp.error}".strip()
                )
                counters["abandoned"] += 1

        log.info(
            f"Putaran antrean selesai | diperiksa={counters['checked']} | "
            f"terkirim={counters['sent']} | retry={counters['retried']} | "
            f"ditinggalkan={counters['abandoned']} | sisa_pending={queue_manager.count_pending()}"
        )
        return counters


# ---------------------------------------------------------------------------
# Singleton worker untuk main.py
# ---------------------------------------------------------------------------
_worker: Optional[SpoolWorker] = None
_worker_lock = threading.Lock()


def start_spool_worker(
    interval_seconds: Optional[int] = None,
    batch_size: Optional[int] = None,
    max_retries: Optional[int] = None,
) -> SpoolWorker:
    """Mulai worker antrean (sekali saja; pemanggilan berulang mengembalikan worker yang sama)."""
    global _worker
    with _worker_lock:
        if _worker is not None and _worker.is_alive():
            return _worker
        _worker = SpoolWorker(interval_seconds, batch_size, max_retries)
        _worker.start()
    return _worker


def stop_spool_worker() -> None:
    """Hentikan worker antrean (dipakai saat shutdown rapi)."""
    global _worker
    with _worker_lock:
        worker, _worker = _worker, None
    if worker is not None:
        worker.stop()
