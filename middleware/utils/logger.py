"""
logger.py - Structured Logging Utility
======================================
Rotasi file log otomatis, format terstruktur dengan timestamp.

Handler dipasang SEKALI pada root logger sehingga semua modul
(main, mllp_server, converter, simgos_api, ...) menulis ke konsol
(docker logs) DAN file secara konsisten. Tanpa ini, hanya logger
pertama yang punya handler dan pesan INFO modul lain hilang.
"""

import logging
import os
import sys
from datetime import datetime
from logging.handlers import RotatingFileHandler

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from config.settings import (
    LOG_LEVEL, LOG_DIR, LOG_FILE_NAME,
    LOG_MAX_BYTES, LOG_BACKUP_COUNT
)

_logger_initialized = False


class _OffsetFormatter(logging.Formatter):
    """
    Formatter yang menuliskan offset zona waktu eksplisit, mis. `2026-10-29 08:00:19+0000`.
    Container berjalan di UTC sementara jam dinding RS WITA — offset mencegah
    salah baca saat riwayat di `docker logs` dibandingkan dengan jam operator.
    """

    def formatTime(self, record, datefmt=None):
        return datetime.fromtimestamp(record.created).astimezone().strftime(
            "%Y-%m-%d %H:%M:%S%z"
        )


def _configure_root() -> None:
    """Pasang handler konsol + file pada root logger, sekali saja."""
    global _logger_initialized
    if _logger_initialized:
        return

    os.makedirs(LOG_DIR, exist_ok=True)
    log_path = os.path.join(LOG_DIR, LOG_FILE_NAME)

    level = getattr(logging, LOG_LEVEL.upper(), logging.INFO)

    fmt = _OffsetFormatter(fmt="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s")

    root = logging.getLogger()
    root.setLevel(level)

    # Hindari duplikasi bila modul di-reload
    for handler in list(root.handlers):
        root.removeHandler(handler)

    # ── HANDLER: Konsol (tertangkap `docker logs`) ───────────────────────────
    console_handler = logging.StreamHandler(stream=sys.stdout)
    console_handler.setFormatter(fmt)
    root.addHandler(console_handler)

    # ── HANDLER: File dengan Rotasi ───────────────────────────────────────────
    file_handler = RotatingFileHandler(
        log_path,
        maxBytes=LOG_MAX_BYTES,
        backupCount=LOG_BACKUP_COUNT,
        encoding="utf-8"
    )
    file_handler.setFormatter(fmt)
    root.addHandler(file_handler)

    # Library pihak ketiga: cukup WARNING agar riwayat PUT tidak tenggelam
    logging.getLogger("urllib3").setLevel(logging.WARNING)

    _logger_initialized = True
    logging.getLogger("main").info(
        f"Logger aktif | level={LOG_LEVEL} | file={log_path} | tz={datetime.now().astimezone().tzname()}"
    )


def get_logger(name: str = "pacs_bridge") -> logging.Logger:
    """
    Mengembalikan logger bernama yang sudah terhubung ke handler root.
    Level mengikuti LOG_LEVEL (diwarisi dari root).
    """
    _configure_root()
    return logging.getLogger(name)
