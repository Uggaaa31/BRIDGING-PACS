"""
logger.py - Structured Logging Utility
======================================
Rotasi file log otomatis, format terstruktur dengan timestamp.
"""

import logging
import os
from logging.handlers import RotatingFileHandler

import sys
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
_app_logger = None


def get_logger(name: str = "pacs_bridge") -> logging.Logger:
    """
    Mengembalikan logger yang sudah dikonfigurasi.
    Dipanggil sekali, lalu di-cache.
    """
    global _logger_initialized, _app_logger

    logger = logging.getLogger(name)

    if _logger_initialized:
        return logger

    # Pastikan direktori log ada
    os.makedirs(LOG_DIR, exist_ok=True)
    log_path = os.path.join(LOG_DIR, LOG_FILE_NAME)

    level = getattr(logging, LOG_LEVEL.upper(), logging.INFO)
    logger.setLevel(level)

    # ── FORMAT ───────────────────────────────────────────────────────────────
    fmt = logging.Formatter(
        fmt="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )

    # ── HANDLER: Konsol ───────────────────────────────────────────────────────
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(fmt)
    logger.addHandler(console_handler)

    # ── HANDLER: File dengan Rotasi ───────────────────────────────────────────
    file_handler = RotatingFileHandler(
        log_path,
        maxBytes=LOG_MAX_BYTES,
        backupCount=LOG_BACKUP_COUNT,
        encoding="utf-8"
    )
    file_handler.setFormatter(fmt)
    logger.addHandler(file_handler)

    _logger_initialized = True
    _app_logger = logger

    logger.info(f"Logger aktif | level={LOG_LEVEL} | file={log_path}")
    return logger
