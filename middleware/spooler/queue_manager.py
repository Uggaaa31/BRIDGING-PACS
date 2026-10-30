"""
queue_manager.py - Antrean Lokal Store-and-Forward (SQLite)
==========================================================
Menyimpan payload HL7 yang sudah dikonversi ke tabel lokal `message_spool`
ketika API SIMGOS tidak merespons, lalu menyediakannya kembali untuk
dikirim ulang oleh spool_worker.

Ketahanan (durability):
  - Payload ditulis ke disk (SQLite) SEBELUM PACS menerima ACK AA, sehingga
    hasil bacaan tidak hilang saat API SIMGOS / MySQL / jaringan down.
  - File database dikonfigurasi lewat env SPOOL_DB_PATH (default
    `middleware/data/spool.db`, di Docker `/app/data/spool.db`).

Thread-safety:
  - Satu koneksi SQLite per thread (`threading.local`) — sqlite3.Connection
    tidak boleh dipakai lintas thread.
  - Semua operasi tulis diserialisasi dengan `_WRITE_LOCK` + `busy_timeout`
    agar tidak muncul "database is locked" saat handler MLLP dan worker
    menulis bersamaan.
  - WAL mode: pembaca tidak memblokir penulis dan sebaliknya.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
from datetime import datetime
from typing import Any, Optional

import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from config.settings import SPOOL_DB_PATH, SPOOL_MAX_RETRIES
from hl7.converter import SimgosPayload
from utils.logger import get_logger

log = get_logger("spool_queue")

# Nilai kolom `status`
STATUS_PENDING   = "PENDING"      # menunggu dikirim ulang oleh worker
STATUS_SENT      = "SENT"         # berhasil tersimpan di SIMGOS
STATUS_ABANDONED = "ABANDONED"    # gagal permanen (4xx / retry habis)

_TS_FORMAT = "%Y-%m-%d %H:%M:%S"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS message_spool (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    accession_number   TEXT,
    message_control_id TEXT,
    payload_json       TEXT NOT NULL,
    retry_count        INTEGER NOT NULL DEFAULT 0,
    status             TEXT NOT NULL DEFAULT 'PENDING',
    last_error         TEXT,
    created_at         DATETIME,
    updated_at         DATETIME
);
CREATE INDEX IF NOT EXISTS idx_message_spool_status
    ON message_spool (status, id);
"""

_local       = threading.local()
_write_lock  = threading.Lock()
_init_lock   = threading.Lock()
_db_ready    = False


def _now() -> str:
    return datetime.now().strftime(_TS_FORMAT)


# ---------------------------------------------------------------------------
# Koneksi & inisialisasi skema
# ---------------------------------------------------------------------------
def db_path() -> str:
    """Path file database antrean yang sedang dipakai (untuk log/diagnostik)."""
    return SPOOL_DB_PATH


def _connect() -> sqlite3.Connection:
    """Buat koneksi SQLite baru untuk thread pemanggil."""
    directory = os.path.dirname(os.path.abspath(SPOOL_DB_PATH))
    if directory:
        os.makedirs(directory, exist_ok=True)

    conn = sqlite3.connect(SPOOL_DB_PATH, timeout=10.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 10000")
    try:
        conn.execute("PRAGMA journal_mode = WAL")       # tahan crash, pembaca tak blokir penulis
    except sqlite3.DatabaseError as e:
        # Sebagian filesystem (mount jaringan) tidak mendukung WAL — jangan gagalkan startup.
        log.warning(f"WAL tidak aktif untuk {SPOOL_DB_PATH}: {e}")
    conn.execute("PRAGMA synchronous = NORMAL")
    return conn


def init_db() -> None:
    """
    Pastikan tabel `message_spool` ada. Aman dipanggil berulang kali
    (dijalankan otomatis pada operasi pertama, dan eksplisit dari worker/main).
    """
    global _db_ready
    if _db_ready:
        return

    with _init_lock:
        if _db_ready:
            return
        conn = get_connection()
        with _write_lock:
            conn.executescript(_SCHEMA)
            conn.commit()
        _db_ready = True
        log.info(f"Database antrean siap | file={SPOOL_DB_PATH}")


def get_connection() -> sqlite3.Connection:
    """Koneksi SQLite milik thread saat ini (dibuat sekali per thread)."""
    conn = getattr(_local, "conn", None)
    if conn is None:
        conn = _connect()
        _local.conn = conn
        if not _db_ready:
            with _write_lock:
                conn.executescript(_SCHEMA)
                conn.commit()
    return conn


def close_connection() -> None:
    """Tutup koneksi milik thread saat ini (dipakai saat shutdown / test)."""
    conn = getattr(_local, "conn", None)
    if conn is not None:
        try:
            conn.close()
        finally:
            _local.conn = None


# ---------------------------------------------------------------------------
# Serialisasi payload
# ---------------------------------------------------------------------------
def payload_to_dict(payload: SimgosPayload) -> dict:
    """Serialisasi SimgosPayload ke dict JSON-serializable."""
    if hasattr(payload, "to_dict"):
        return payload.to_dict()
    if isinstance(payload, dict):
        return payload
    raise TypeError(f"Payload tidak dapat diserialisasi: {type(payload)!r}")


def payload_from_dict(data: dict) -> SimgosPayload:
    """
    Rekonstruksi SimgosPayload dari dict tersimpan.

    hanya atribut yang dikenal SimgosPayload yang dipasang; kunci asing
    diabaikan agar skema antrean versi baru tidak merusak versi lama.
    """
    obj = SimgosPayload()
    for key, value in (data or {}).items():
        if hasattr(obj, key):
            setattr(obj, key, value)
    return obj


def decode_row(row: Any) -> SimgosPayload:
    """Ubah satu baris `message_spool` menjadi SimgosPayload siap kirim."""
    raw = row["payload_json"] if isinstance(row, sqlite3.Row) else row.get("payload_json")
    return payload_from_dict(json.loads(raw))


# ---------------------------------------------------------------------------
# Operasi antrean
# ---------------------------------------------------------------------------
def enqueue(payload: SimgosPayload, error_reason: str = "") -> bool:
    """
    Simpan payload ke antrean lokal.

    Args:
        payload      : SimgosPayload hasil konversi HL7.
        error_reason : pesan error dari percobaan pengiriman terakhir.

    Returns:
        True bila payload dijamin tersimpan di disk (baris baru ATAU sudah
        ada baris PENDING dengan message_control_id sama — pengiriman ulang
        pesan yang sama dari PACS tidak digandakan).

    Raises:
        sqlite3.Error / OSError bila penulisan disk gagal — pemanggil
        (mllp_server) WAJIB membalas ACK AE dalam kasus ini, karena data
        tidak aman.
    """
    init_db()
    data        = payload_to_dict(payload)
    payload_str = json.dumps(data, ensure_ascii=False)
    acc         = data.get("accession_number") or data.get("norm") or ""
    msg_id      = data.get("message_control_id") or ""
    now         = _now()

    conn = get_connection()
    with _write_lock:
        # Deduplikasi: pesan yang sudah mengantre (belum terkirim) tidak perlu ditulis dua kali.
        if msg_id:
            existing = conn.execute(
                "SELECT id FROM message_spool "
                "WHERE status = ? AND message_control_id = ? "
                "ORDER BY id LIMIT 1",
                (STATUS_PENDING, msg_id),
            ).fetchone()
            if existing:
                conn.execute(
                    "UPDATE message_spool SET last_error = ?, updated_at = ? WHERE id = ?",
                    (error_reason or "", now, existing["id"]),
                )
                conn.commit()
                log.warning(
                    f"Pesan sudah ada di antrean (id={existing['id']}) — tidak digandakan | "
                    f"msg={msg_id} | acc={acc}"
                )
                return True

        cur = conn.execute(
            "INSERT INTO message_spool (accession_number, message_control_id, payload_json, "
            "retry_count, status, last_error, created_at, updated_at) "
            "VALUES (?, ?, ?, 0, ?, ?, ?, ?)",
            (acc, msg_id, payload_str, STATUS_PENDING, error_reason or "", now, now),
        )
        conn.commit()
        spool_id = cur.lastrowid

    log.warning(
        f"↻ Payload masuk antrean lokal | id={spool_id} | acc={acc} | msg={msg_id} | "
        f"alasan={error_reason or '-'}"
    )
    return True


def get_pending_batch(limit: int = 10) -> list[sqlite3.Row]:
    """
    Ambil sekumpulan baris berstatus PENDING, terlama lebih dulu (FIFO),
    supaya urutan bacaan radiologi tetap terjaga.
    """
    init_db()
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM message_spool WHERE status = ? ORDER BY id ASC LIMIT ?",
        (STATUS_PENDING, int(limit)),
    ).fetchall()
    return list(rows)


def mark_as_sent(spool_id: int) -> bool:
    """Tandai baris antrean sebagai SENT (berhasil tersimpan di SIMGOS)."""
    init_db()
    conn = get_connection()
    with _write_lock:
        cur = conn.execute(
            "UPDATE message_spool SET status = ?, last_error = '', updated_at = ? "
            "WHERE id = ? AND status != ?",
            (STATUS_SENT, _now(), int(spool_id), STATUS_SENT),
        )
        conn.commit()
    return cur.rowcount > 0


def mark_abandoned(spool_id: int, error_msg: str = "") -> bool:
    """
    Tandai baris antrean sebagai ABANDONED: data ditolak secara permanen
    (HTTP 4xx / payload rusak) sehingga tidak boleh membajak antrean.
    """
    init_db()
    conn = get_connection()
    with _write_lock:
        cur = conn.execute(
            "UPDATE message_spool SET status = ?, last_error = ?, updated_at = ? WHERE id = ?",
            (STATUS_ABANDONED, (error_msg or "")[:500], _now(), int(spool_id)),
        )
        conn.commit()
    if cur.rowcount:
        log.error(
            f"Data ditinggalkan (tidak akan diretry) | id={spool_id} | error={error_msg or '-'}"
        )
    return cur.rowcount > 0


def increment_retry(spool_id: int, error_msg: str = "", max_retries: Optional[int] = None) -> str:
    """
    Tambah retry_count satu baris dan catat error terakhir.

    Returns:
        Status baru baris tersebut: STATUS_PENDING bila masih boleh diretry,
        STATUS_ABANDONED bila retry_count sudah menyentuh `max_retries`.
    """
    init_db()
    limit = SPOOL_MAX_RETRIES if max_retries is None else int(max_retries)
    conn = get_connection()

    with _write_lock:
        row = conn.execute(
            "SELECT retry_count FROM message_spool WHERE id = ?", (int(spool_id),)
        ).fetchone()
        if row is None:
            log.warning(f"increment_retry: id={spool_id} tidak ditemukan.")
            return STATUS_ABANDONED

        retry_count = int(row["retry_count"] or 0) + 1
        status = STATUS_ABANDONED if (limit > 0 and retry_count >= limit) else STATUS_PENDING
        conn.execute(
            "UPDATE message_spool SET retry_count = ?, status = ?, last_error = ?, updated_at = ? "
            "WHERE id = ?",
            (retry_count, status, (error_msg or "")[:500], _now(), int(spool_id)),
        )
        conn.commit()

    if status == STATUS_ABANDONED:
        log.error(
            f"Retry habis ({retry_count}/{limit}) — data ditinggalkan | "
            f"id={spool_id} | error={error_msg or '-'}"
        )
    else:
        log.warning(
            f"Retry {retry_count}/{limit} gagal | id={spool_id} | error={error_msg or '-'}"
        )
    return status


# ---------------------------------------------------------------------------
# Diagnostik
# ---------------------------------------------------------------------------
def count_pending() -> int:
    """Jumlah baris yang masih menunggu pengiriman ulang."""
    init_db()
    conn = get_connection()
    return int(
        conn.execute(
            "SELECT COUNT(*) AS n FROM message_spool WHERE status = ?", (STATUS_PENDING,)
        ).fetchone()["n"]
    )


def counts_by_status() -> dict:
    """Rekap jumlah baris per status (PENDING / SENT / ABANDONED)."""
    init_db()
    conn = get_connection()
    rows = conn.execute(
        "SELECT status, COUNT(*) AS n FROM message_spool GROUP BY status"
    ).fetchall()
    return {r["status"]: int(r["n"]) for r in rows}


def recent_rows(limit: int = 20, status: Optional[str] = None) -> list[dict]:
    """
    Daftar baris terakhir untuk inspeksi manual (isi kolom payload diringkas
    menjadi panjang byte agar tidak membanjiri terminal).
    """
    init_db()
    conn = get_connection()
    sql = (
        "SELECT id, accession_number, message_control_id, retry_count, status, "
        "last_error, created_at, updated_at, LENGTH(payload_json) AS payload_bytes "
        "FROM message_spool"
    )
    params: tuple = ()
    if status:
        sql += " WHERE status = ?"
        params = (status,)
    sql += " ORDER BY id DESC LIMIT ?"
    params += (int(limit),)
    return [dict(r) for r in conn.execute(sql, params).fetchall()]
