"""
main.py - Entry Point Middleware Bridging PACS-SIMGOS
======================================================
Menjalankan MLLP Server listener di thread utama.
Dapat juga menjalankan mode "file test" untuk pengujian lokal
tanpa koneksi ke PACS fisik:

    # Mode MLLP Server (production):
    python main.py

    # Mode test dari file HL7:
    python main.py --test-file path/ke/pesan.hl7
"""

from __future__ import annotations
import argparse
import sys
import os

# Pastikan direktori root ada di path
sys.path.insert(0, os.path.dirname(__file__))

from utils.logger import get_logger
from config.settings import MLLP_HOST, MLLP_PORT

log = get_logger("main")

BANNER = r"""
+============================================================+
|     PACS -> SIMGOS  |  HL7 Bridging Middleware  v1.0      |
|     Radiologi Integration Service - RSUP                  |
+============================================================+
"""


def run_server_mode():
    """Mode produksi: jalankan MLLP TCP server + worker antrean lokal."""
    from spooler.spool_worker import start_spool_worker, stop_spool_worker
    from listener.mllp_server import start_mllp_server

    # Worker store-and-forward: menguras antrean lokal saat SIMGOS kembali normal.
    start_spool_worker()

    log.info(f"Mode: MLLP Server | {MLLP_HOST}:{MLLP_PORT}")
    try:
        start_mllp_server()
    finally:
        stop_spool_worker()


def run_file_test_mode(filepath: str):
    """
    Mode pengujian lokal: baca file HL7 dan proses seperti dari PACS,
    tetapi tanpa TCP/MLLP. Langsung parse → konversi → PUT ke SIMGOS.
    """
    log.info(f"Mode: File Test | file={filepath}")

    if not os.path.exists(filepath):
        log.error(f"File tidak ditemukan: {filepath}")
        sys.exit(1)

    with open(filepath, "r", encoding="utf-8") as f:
        hl7_text = f.read()

    from hl7.parser import HL7Message, HL7ParseError
    from hl7.converter import convert_hl7_to_simgos
    from client.simgos_api import send_to_simgos
    import json

    try:
        msg     = HL7Message(hl7_text)
        log.info(f"Parse berhasil: {msg}")

        payload = convert_hl7_to_simgos(msg)
        log.info(f"Payload hasil konversi:\n{json.dumps(payload.to_dict(), indent=2, ensure_ascii=False)}")

        resp = send_to_simgos(payload)
        if resp.success:
            log.info(f"✓ PUT berhasil: HTTP {resp.status_code}")
        else:
            log.error(f"✗ PUT gagal: {resp.error}")
        return resp.success

    except HL7ParseError as e:
        log.error(f"Parse HL7 gagal: {e}")
        return False
    except Exception as e:
        log.error(f"Error tidak terduga: {e}", exc_info=True)
        return False


def main():
    print(BANNER)

    parser = argparse.ArgumentParser(
        description="PACS-SIMGOS HL7 Bridging Middleware"
    )
    parser.add_argument(
        "--test-file",
        metavar="PATH",
        help="Mode test lokal: proses file HL7 tanpa MLLP server"
    )
    parser.add_argument(
        "--host",
        default=MLLP_HOST,
        help=f"Bind host MLLP server (default: {MLLP_HOST})"
    )
    parser.add_argument(
        "--port",
        type=int,
        default=MLLP_PORT,
        help=f"Port MLLP server (default: {MLLP_PORT})"
    )
    args = parser.parse_args()

    if args.test_file:
        success = run_file_test_mode(args.test_file)
        sys.exit(0 if success else 1)
    else:
        run_server_mode()


if __name__ == "__main__":
    main()
