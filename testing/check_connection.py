#!/usr/bin/env python3
"""
check_connection.py - Cek Koneksi MLLP, API SIMGOS, dan Database
=================================================================
Tidak menulis data apa pun ke database. Aman dijalankan kapan saja.

Yang diperiksa:
  1. MLLP listener  : TCP connect ke host:port (default 127.0.0.1:2575)
  2. SIMGOS API     : HTTP PUT dengan body kosong, untuk memastikan API key
                      valid dan routing /.htaccess hidup.
                      Key salah -> 401, key benar -> 400 "Request body kosong."
                      Tidak ada INSERT karena body ditolak sebelum transaksi.
  3. Database       : TCP connect + SELECT 1

Penggunaan:
    python testing/check_connection.py
    python testing/check_connection.py --host 10.10.160.105
    python testing/check_connection.py --api-url http://localhost:8080/api/v1

Exit code: 0 = semua hidup, 1 = ada yang gagal.
"""

from __future__ import annotations

import argparse
import socket
import sys
import time

from common import OK, FAIL, SKIP, Config, banner, cfg, db_query, step

try:
    import requests
except ImportError:
    sys.exit("Dependensi hilang: jalankan `pip install requests`.")


def check_tcp(host: str, port: int, timeout: float) -> tuple[bool, str]:
    start = time.monotonic()
    try:
        with socket.create_connection((host, port), timeout):
            pass
        return True, f"{host}:{port} | {(time.monotonic() - start) * 1000:.1f}ms"
    except OSError as e:
        return False, f"{host}:{port} | {e}"


def check_api(config: Config) -> tuple[bool, str]:
    """
    Probe autentikasi tanpa menulis data.

    Urutan validasi pacs_bridging.php: metode -> autentikasi -> identifier
    (dari URL) -> body. Body kosong ditolak di tahap 4, sebelum transaksi
    database dibuka, jadi tidak ada INSERT ke hasil_rad maupun hl7_logs.
    """
    url = f"{config.api_url}/radiologi/pacs-bridging/AUTH-PROBE"
    headers = {"Content-Type": "application/json", "X-Api-Key": config.api_key}

    try:
        resp = requests.put(url, headers=headers, data="", timeout=config.timeout)
    except requests.RequestException as e:
        return False, f"{url} | {e}"

    if resp.status_code == 401:
        return False, "API key ditolak (401). Cek SIMGOS_API_KEY di .env."
    if resp.status_code == 400:
        return True, f"auth OK, routing OK | HTTP {resp.status_code}"
    if resp.status_code == 200:
        return False, "Body kosong diterima (200) — cek urutan validasi API."

    snippet = resp.text[:120].replace("\n", " ")
    return False, f"HTTP {resp.status_code} | {snippet}"


def check_db(config: Config) -> tuple[bool, str]:
    """Koneksi TCP + login + SELECT 1 (tidak menyentuh tabel mana pun)."""
    ok, detail = check_tcp(config.db_host, config.db_port, config.timeout)
    if not ok:
        return False, detail

    try:
        blocks = db_query(config, ["SELECT 1 AS ping, NOW() AS server_time"])
        cols, rows = blocks[0]
        ping = dict(zip(cols, rows[0])) if rows else {}
        return True, (
            f"{config.db_host}:{config.db_port} | "
            f"user={config.db_user} | server_time={ping.get('server_time', '?')}"
        )
    except Exception as e:
        return False, f"{config.db_host}:{config.db_port} | {str(e)[:160]}"


def main() -> int:
    env_port = cfg("MLLP_PORT", "2575")

    parser = argparse.ArgumentParser(
        description="Cek koneksi MLLP, API SIMGOS, dan database (tanpa menulis data)"
    )
    parser.add_argument("--host", default=None, help="Host MLLP (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=None,
                        help=f"Port MLLP (default dari .env: {env_port})")
    parser.add_argument("--timeout", type=float, default=15.0, help="Timeout detik")
    parser.add_argument("--api-url", default=None, help="Base URL API SIMGOS")
    parser.add_argument("--api-key", default=None, help="API key (default dari .env)")
    args = parser.parse_args()

    config = Config(args)

    print()
    print("  Cek Koneksi — Bridging PACS-SIMGOS")
    config.summary()

    results = []

    banner("1. MLLP LISTENER")
    ok, detail = check_tcp(config.mllp_host, config.mllp_port, config.timeout)
    step(OK if ok else FAIL, "TCP MLLP", detail)
    results.append(ok)

    banner("2. SIMGOS API")
    if not config.api_key:
        step(FAIL, "API key", "SIMGOS_API_KEY kosong di .env")
        results.append(False)
    else:
        ok, detail = check_api(config)
        step(OK if ok else FAIL, "Auth & routing", detail)
        results.append(ok)

    banner("3. DATABASE")
    ok, detail = check_db(config)
    step(OK if ok else FAIL, "Koneksi & login", detail)
    results.append(ok)

    if ok:
        step(SKIP, "Tabel", "tidak diperiksa di sini — pakai check_db.py")

    banner("RINGKASAN")
    all_ok = all(results)
    print(f"  Hasil: {'SEMUA HIDUP' if all_ok else 'ADA YANG GAGAL'}")
    print()
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
