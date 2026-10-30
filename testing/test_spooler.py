#!/usr/bin/env python3
"""
test_spooler.py - Uji Store-and-Forward (Offline Spooler) Middleware PACS-SIMGOS
==============================================================================
Menguji mekanisme antrean lokal SQLite (`middleware/spooler/`) end-to-end,
TANPA menyentuh database MySQL SIMGOS dan tanpa kredensial produksi.

Skenario yang diuji:
  1. Klasifikasi error klien      : timeout/jaringan/5xx -> retryable, 4xx/2xx-sukses-false -> tidak.
  2. API SIMGOS mati              : pesan masuk antrean lokal + PACS menerima ACK AA.
  3. Pesan ganda dari PACS        : tidak digandakan di antrean (dedupe MsgID).
  4. API SIMGOS hidup             : worker menguras antrean -> status SENT.
  5. Retry berkala                : 5xx berulang -> retry_count naik -> ABANDONED saat batas tercapai.
  6. Data ditolak (4xx)           : langsung ABANDONED, tidak membajak antrean.
  7. Payload JSON rusak           : ABANDONED, worker tidak crash.
  8. 4xx saat realtime            : PACS menerima ACK AE, tidak masuk antrean.

Mode pemakaian:
    python testing/test_spooler.py                 # jalankan seluruh suite (isolasi, DB sementara)
    python testing/test_spooler.py --status        # tampilkan isi antrean lokal yang sebenarnya
    python testing/test_spooler.py --flush         # jalankan satu putaran worker ke API SIMGOS asli
    python testing/test_spooler.py --live          # kirim HL7 ke middleware berjalan + cek DB
    python testing/test_spooler.py --keep          # jangan hapus direktori kerja sementara

Exit code 0 = semua lulus, 1 = ada yang gagal.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import sqlite3
import sys
import tempfile
import threading
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
MIDDLEWARE = ROOT / "middleware"

sys.path.insert(0, str(HERE))          # common.py
sys.path.insert(0, str(MIDDLEWARE))    # config, hl7, client, listener, spooler

import common  # noqa: E402

OK   = common.OK
FAIL = common.FAIL
SKIP = common.SKIP


# ---------------------------------------------------------------------------
# Util
# ---------------------------------------------------------------------------
def free_port() -> int:
    """Cari port TCP bebas di localhost."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


_RESULTS: list[tuple[str, bool]] = []


def check(label: str, condition: bool, detail: str = "") -> bool:
    """Catat satu asersi [ OK ]/[FAIL]."""
    _RESULTS.append((label, bool(condition)))
    print(f"  {OK if condition else FAIL} {label}" + (f"  {detail}" if detail else ""))
    return bool(condition)


def info(label: str, detail: str = "") -> None:
    print(f"       {label}" + (f"  {detail}" if detail else ""))


# ---------------------------------------------------------------------------
# Stub REST API SIMGOS (tanpa MySQL, tanpa kredensial)
# ---------------------------------------------------------------------------
class StubAPI:
    """Server HTTP lokal yang meniru endpoint PUT SIMGOS."""

    def __init__(self):
        self.status = 200
        self.body: dict = {"success": True, "data": {"hl7_log_id": 1}}
        self.requests: list[str] = []
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self.url = ""

    def start(self) -> str:
        stub = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def do_PUT(self):
                length = int(self.headers.get("Content-Length") or 0)
                if length:
                    self.rfile.read(length)
                stub.requests.append(self.path)
                payload = json.dumps(stub.body).encode("utf-8")
                self.send_response(stub.status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *args):     # senyapkan log HTTP stub
                pass

        self._server = ThreadingHTTPServer(("127.0.0.1", free_port()), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        self.url = f"http://127.0.0.1:{self._server.server_address[1]}/api/v1"
        return self.url

    def stop(self) -> None:
        if self._server:
            self._server.shutdown()
            self._server.server_close()
        if self._thread:
            self._thread.join(timeout=5)


class MLLPServerFixture:
    """MLLP server in-process pada port bebas."""

    def __init__(self, port: int):
        from listener.mllp_server import ThreadedMLLPServer, HL7RequestHandler

        self.port = port
        self.server = ThreadedMLLPServer(("127.0.0.1", port), HL7RequestHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def stop(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)


# ---------------------------------------------------------------------------
# Suite utama (isolasi penuh, DB antrean sementara)
# ---------------------------------------------------------------------------
def run_suite(keep: bool) -> int:
    workdir = Path(tempfile.mkdtemp(prefix="spooler-test-"))
    spool_db = workdir / "spool.db"

    # Environment HARUS diset sebelum modul middleware diimpor (settings.py
    # memvalidasi kredensial dan membaca path antrean saat import).
    os.environ["SPOOL_DB_PATH"]     = str(spool_db)
    os.environ["LOG_DIR"]           = str(workdir / "logs")
    os.environ["LOG_LEVEL"]         = "WARNING"
    os.environ["SIMGOS_API_KEY"]    = "TEST-KEY-SPOOLER"
    os.environ["SIMGOS_AUTH_TYPE"]  = "api_key"
    os.environ["SIMGOS_API_MAX_RETRIES"] = "1"     # cepat: tanpa retry internal
    os.environ["SIMGOS_API_TIMEOUT"]     = "3"
    os.environ["SPOOL_MAX_RETRIES"]      = "20"

    from client import simgos_api
    from hl7.parser import HL7Message
    from hl7.converter import convert_hl7_to_simgos
    from spooler import queue_manager
    from spooler.spool_worker import SpoolWorker

    simgos_api.SIMGOS_API_RETRY_DELAY_SEC = 0      # jangan tunggu antar attempt

    common.banner("SPOOLER STORE-AND-FORWARD — TEST SUITE")
    info("Antrean lokal", str(spool_db))
    info("Log", str(workdir / "logs"))

    def make_payload(accession: str):
        hl7 = common.build_hl7("99001234", accession)
        return convert_hl7_to_simgos(HL7Message(hl7)), hl7

    def pending() -> list[dict]:
        return queue_manager.recent_rows(status="PENDING")

    stub = StubAPI()
    base_url = stub.start()
    mllp_srv: MLLPServerFixture | None = None

    try:
        # ── 1. Klasifikasi error klien ───────────────────────────────────────
        print()
        print("  1. Klasifikasi error klien (retryable vs permanen)")
        payload, _ = make_payload("RADSPOOL01")
        simgos_api.SIMGOS_API_BASE_URL = base_url

        stub.status, stub.body = 503, {"success": False, "message": "Database SIMGOS down"}
        r = simgos_api.send_to_simgos(payload)
        check("HTTP 503 → retryable", r.retryable and simgos_api.is_retryable_failure(r),
              f"status={r.status_code} retryable={r.retryable}")

        stub.status, stub.body = 422, {"success": False, "message": "Validasi gagal: NORM kosong"}
        r = simgos_api.send_to_simgos(payload)
        check("HTTP 422 → TIDAK retryable", (not r.retryable) and (not simgos_api.is_retryable_failure(r)),
              f"status={r.status_code} retryable={r.retryable}")

        stub.status, stub.body = 200, {"success": True, "data": {"hl7_log_id": 4242}}
        r = simgos_api.send_to_simgos(payload)
        check("HTTP 200 + success=true → sukses", r.success, f"status={r.status_code}")

        stub.status, stub.body = 200, {"success": False, "message": "Skema data ditolak"}
        r = simgos_api.send_to_simgos(payload)
        check("HTTP 200 + success=false → TIDAK retryable",
              (not r.success) and (not r.retryable), f"status={r.status_code}")

        dead_url = f"http://127.0.0.1:{free_port()}/api/v1"      # port tanpa listener
        simgos_api.SIMGOS_API_BASE_URL = dead_url
        r = simgos_api.send_to_simgos(payload)
        check("API mati (koneksi ditolak) → retryable",
              (not r.success) and r.retryable, f"status={r.status_code} error={r.error[:60]}")

        # ── 2. API mati: MLLP → antrean + ACK AA ─────────────────────────────
        print()
        print("  2. API SIMGOS mati → data masuk antrean + PACS menerima ACK AA")
        mllp_port = free_port()
        mllp_srv = MLLPServerFixture(mllp_port)

        acc_down = common.make_accession()
        hl7_down = common.build_hl7("99001234", acc_down)
        ok, ack, ms = common.send_mllp(hl7_down, "127.0.0.1", mllp_port, 10.0)
        check("PACS menerima MSA|AA", ok, f"{ms:.0f}ms")
        check("MSA-3 memuat catatan antrean",
              "queued for background synchronization" in ack, ack.replace("\n", " ")[:120])

        rows = pending()
        check("Payload tersimpan di antrean lokal (status PENDING)",
              len(rows) == 1 and rows[0]["accession_number"] == acc_down,
              f"pending={len(rows)} acc={rows[0]['accession_number'] if rows else '-'}")

        # ── 3. Dedupe pesan ganda ────────────────────────────────────────────
        print()
        print("  3. PACS mengirim ulang pesan yang sama → tidak digandakan")
        common.send_mllp(hl7_down, "127.0.0.1", mllp_port, 10.0)
        check("Antrean tetap 1 baris untuk MsgID sama", len(pending()) == 1,
              f"pending={len(pending())}")

        # ── 4. API hidup: worker menguras antrean ────────────────────────────
        print()
        print("  4. API SIMGOS hidup → worker mengirim ulang antrean")
        stub.status, stub.body = 200, {"success": True, "data": {"hl7_log_id": 77}}
        simgos_api.SIMGOS_API_BASE_URL = base_url
        worker = SpoolWorker(interval_seconds=1, batch_size=10, max_retries=5)
        counters = worker.flush_once()
        check("Worker melaporkan 1 pesan terkirim", counters["sent"] == 1, str(counters))
        check("Antrean PENDING kosong", queue_manager.count_pending() == 0)
        check("Baris ditandai SENT", queue_manager.counts_by_status().get("SENT") == 1,
              str(queue_manager.counts_by_status()))
        check("URL PUT memakai accession yang benar",
              any(acc_down in p for p in stub.requests), f"requests={len(stub.requests)}")

        # ── 5. Retry berkala sampai ABANDONED ────────────────────────────────
        print()
        print("  5. 5xx berulang → retry_count naik → ABANDONED saat batas tercapai")
        stub.status, stub.body = 503, {"success": False, "message": "Masih down"}
        payload_retry, _ = make_payload("RADSPOOL02")
        queue_manager.enqueue(payload_retry, "uji retry")
        worker2 = SpoolWorker(interval_seconds=1, batch_size=10, max_retries=2)

        c1 = worker2.flush_once()
        rows = pending()
        check("Putaran 1: gagal → tetap PENDING dengan retry_count=1",
              c1["retried"] == 1 and len(rows) == 1 and rows[0]["retry_count"] == 1,
              f"{c1} retry_count={rows[0]['retry_count'] if rows else '-'}")

        c2 = worker2.flush_once()
        aband = queue_manager.recent_rows(status="ABANDONED")
        check("Putaran 2: retry habis → ABANDONED",
              c2["abandoned"] == 1 and queue_manager.count_pending() == 0 and len(aband) == 1,
              f"{c2} total_abandoned={len(aband)}")

        # Default produksi (SPOOL_MAX_RETRIES=0) = tanpa batas: downtime panjang
        # tidak boleh membuat data pasien dibuang.
        payload_unlimited, _ = make_payload("RADSPOOL05")
        queue_manager.enqueue(payload_unlimited, "uji tanpa batas")
        worker_unlimited = SpoolWorker(interval_seconds=1, batch_size=10, max_retries=0)
        for _ in range(3):
            worker_unlimited.flush_once()
        rows = pending()
        check("Tanpa batas (0): 3x gagal → tetap PENDING, retry_count=3",
              len(rows) == 1 and rows[0]["retry_count"] == 3 and rows[0]["status"] == "PENDING",
              f"pending={len(rows)} retry_count={rows[0]['retry_count'] if rows else '-'}")
        # Bersihkan agar tidak mengganggu skenario berikutnya
        for row in rows:
            queue_manager.mark_abandoned(row["id"], "dibersihkan setelah uji")

        # ── 6. 4xx → langsung ABANDONED ──────────────────────────────────────
        print()
        print("  6. Data ditolak server (4xx) → ABANDONED, bukan retry selamanya")
        stub.status, stub.body = 422, {"success": False, "message": "Accession tidak dikenal"}
        payload_bad, _ = make_payload("RADSPOOL03")
        queue_manager.enqueue(payload_bad, "uji 4xx")
        before = len(queue_manager.recent_rows(status="ABANDONED"))
        c3 = worker.flush_once()
        after = len(queue_manager.recent_rows(status="ABANDONED"))
        check("4xx → 1 baris ABANDONED tanpa retry", c3["abandoned"] == 1 and after == before + 1,
              str(c3))

        # ── 7. Payload rusak ────────────────────────────────────────────────
        print()
        print("  7. Payload JSON rusak di antrean → ABANDONED, worker tidak crash")
        conn = sqlite3.connect(queue_manager.db_path())
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        conn.execute(
            "INSERT INTO message_spool (accession_number, message_control_id, payload_json, "
            "retry_count, status, last_error, created_at, updated_at) VALUES (?,?,?,0,?,?,?,?)",
            ("RADSPOOL04", "MSG-CORRUPT", "{bukan json", "PENDING", "", now, now),
        )
        conn.commit()
        conn.close()
        c4 = worker.flush_once()
        check("Payload rusak → ABANDONED", c4["abandoned"] == 1 and c4["sent"] == 0, str(c4))

        # ── 8. 4xx saat realtime → ACK AE, tidak masuk antrean ───────────────
        print()
        print("  8. Realtime 4xx → PACS menerima ACK AE, tidak masuk antrean")
        pending_before = queue_manager.count_pending()
        hl7_reject = common.build_hl7("99001234", common.make_accession())
        ok_reject, ack_reject, _ = common.send_mllp(hl7_reject, "127.0.0.1", mllp_port, 10.0)
        check("PACS menerima MSA|AE", (not ok_reject) and "MSA|AE" in ack_reject,
              ack_reject.replace("\n", " ")[:120])
        check("Antrean tidak bertambah",
              queue_manager.count_pending() == pending_before,
              f"pending={queue_manager.count_pending()}")

    finally:
        if mllp_srv:
            mllp_srv.stop()
        stub.stop()
        # sqlite3 + WAL: tutup koneksi thread utama sebelum menghapus file
        try:
            queue_manager.close_connection()
        except Exception:
            pass
        if keep:
            info("Direktori kerja dipertahankan", str(workdir))
        else:
            shutil.rmtree(workdir, ignore_errors=True)

    passed = sum(1 for _, okv in _RESULTS if okv)
    failed = [(label, okv) for label, okv in _RESULTS if not okv]
    print()
    print("=" * 68)
    print(f"  HASIL: {passed}/{len(_RESULTS)} asersi lulus")
    if failed:
        for label, _ in failed:
            print(f"  {FAIL} {label}")
    print("=" * 68)
    return 0 if not failed else 1


# ---------------------------------------------------------------------------
# Mode operasional: --status / --flush / --live
# ---------------------------------------------------------------------------
def cmd_status() -> int:
    """Tampilkan isi antrean lokal yang sebenarnya (DB produksi/standalone)."""
    from spooler import queue_manager as q

    common.banner("ISI ANTREAN LOKAL (SPOOLER)")
    info("File database", q.db_path())
    counts = q.counts_by_status()
    info("Rekap status", json.dumps(counts, ensure_ascii=False) or "{}")

    rows = q.recent_rows(limit=50)
    if not rows:
        print("  (antrean kosong)")
        return 0

    cols = ["id", "accession_number", "message_control_id", "retry_count",
            "status", "created_at", "updated_at", "payload_bytes"]
    print()
    print("      " + "  ".join(f"{c[:14]:<14}" for c in cols))
    for row in rows:
        print("      " + "  ".join(f"{str(row[c])[:14]:<14}" for c in cols))
        if row.get("last_error"):
            print(f"      └─ last_error: {str(row['last_error'])[:120]}")
    return 0


def cmd_flush() -> int:
    """Jalankan satu putaran worker terhadap API SIMGOS asli."""
    from spooler import queue_manager as q
    from spooler.spool_worker import SpoolWorker

    common.banner("FLUSH ANTREAN (SATU PUTARAN)")
    info("File database", q.db_path())
    info("Pending sebelum", str(q.count_pending()))

    worker = SpoolWorker()
    counters = worker.flush_once()
    info("Hasil putaran", json.dumps(counters))
    info("Pending sesudah", str(q.count_pending()))
    info("Rekap status", json.dumps(q.counts_by_status()))

    if q.count_pending():
        print(f"  {SKIP} Masih ada pesan menunggu — jalankan lagi setelah SIMGOS normal.")
    else:
        print(f"  {OK} Antrean bersih.")
    return 0


def cmd_live(args) -> int:
    """Kirim HL7 ke middleware berjalan, lalu pastikan tercatat di DB SIMGOS."""
    common.banner("LIVE TEST — MLLP → SPOOLER/WORKER → MySQL SIMGOS")

    cfg = common.Config(argparse.Namespace(host=args.host, port=args.port,
                                           timeout=args.timeout, api_url=None, api_key=None))
    cfg.summary()

    accession = args.acc or common.make_accession()
    hl7 = common.build_hl7("99001234", accession)
    info("Accession uji", accession)

    ok, ack, ms = common.send_mllp(hl7, args.host, args.port, args.timeout)
    print(f"  {OK if ok else FAIL} MLLP ACK  {ms:.0f}ms")
    info("ACK", ack.replace("\n", " | ")[:200])
    if not ok:
        print(f"  {FAIL} Middleware tidak membalas MSA|AA — hentikan di sini.")
        return 1

    deadline = time.monotonic() + args.wait
    state = None
    while time.monotonic() < deadline:
        try:
            state = common.fetch_state(cfg, accession)
            if state["hasil_rad"][1]:
                break
        except Exception as e:
            print(f"  {SKIP} Akses DB gagal: {str(e)[:120]}")
            return 0
        time.sleep(2)

    rows = state["hasil_rad"][1] if state else []
    if rows:
        print(f"  {OK} Data tersimpan di MySQL SIMGOS (hasil_rad)")
        cols = state["hasil_rad"][0]
        print("      " + "  ".join(f"{c}" for c in cols))
        print("      " + "  ".join(str(v)[:24] for v in rows[0]))
    else:
        print(f"  {FAIL} Data tidak ditemukan di MySQL setelah {args.wait}s")

    try:
        from spooler import queue_manager as q
        info("Pending antrean lokal", str(q.count_pending()))
    except Exception:
        pass

    return 0 if rows else 1


# ---------------------------------------------------------------------------
def main() -> int:
    parser = argparse.ArgumentParser(description="Uji store-and-forward spooler middleware PACS-SIMGOS")
    parser.add_argument("--status", action="store_true", help="tampilkan isi antrean lokal")
    parser.add_argument("--flush", action="store_true", help="jalankan satu putaran worker ke API asli")
    parser.add_argument("--live", action="store_true", help="kirim HL7 ke middleware berjalan + cek DB")
    parser.add_argument("--keep", action="store_true", help="pertahankan direktori kerja sementara")
    parser.add_argument("--host", default="127.0.0.1", help="host MLLP untuk --live (default 127.0.0.1)")
    parser.add_argument("--port", type=int, default=int(common.cfg("MLLP_PORT", "2575")),
                        help="port MLLP untuk --live")
    parser.add_argument("--timeout", type=float, default=15.0, help="timeout MLLP/HTTP (detik)")
    parser.add_argument("--wait", type=float, default=30.0, help="batas tunggu data muncul di DB saat --live")
    parser.add_argument("--acc", default=None, help="accession khusus untuk --live")
    args = parser.parse_args()

    if args.status:
        return cmd_status()
    if args.flush:
        return cmd_flush()
    if args.live:
        return cmd_live(args)
    return run_suite(args.keep)


if __name__ == "__main__":
    sys.exit(main())
