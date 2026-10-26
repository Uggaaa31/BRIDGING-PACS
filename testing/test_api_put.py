#!/usr/bin/env python3
"""
test_api_put.py - Direct REST API Tester (Bypass HL7/MLLP)
============================================================
Mengirim HTTP PUT request langsung ke endpoint SIMGOS API
tanpa melalui alur HL7 → Middleware. Berguna untuk:

1. Menguji endpoint PHP secara terpisah (unit test API)
2. Verifikasi koneksi database dan logika upsert
3. Debugging payload JSON

Penggunaan:
    python test_api_put.py
    python test_api_put.py --url http://192.168.1.50/simgos-api/api/v1
    python test_api_put.py --key YOUR-API-KEY
    python test_api_put.py --norm 99001234 --acc ACC-2024-001
"""

import requests
import json
import argparse
import sys
from datetime import datetime


def send_test_put(
    base_url: str,
    api_key: str,
    identifier: str,
    payload: dict
) -> None:
    """Kirim PUT request dan tampilkan respons terformat."""
    url = f"{base_url.rstrip('/')}/radiologi/pacs-bridging/{identifier}"

    headers = {
        "Content-Type": "application/json",
        "Accept":       "application/json",
        "X-Api-Key":    api_key,
        "X-Source-App": "TEST-SCRIPT",
    }

    print(f"\n{'='*60}")
    print(f"  HTTP PUT → {url}")
    print(f"  API Key  : {api_key[:10]}...")
    print(f"  Payload  : {len(json.dumps(payload))} bytes")
    print(f"{'='*60}")
    print(f"\nPayload JSON yang dikirim:")
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    print()

    try:
        start = __import__('time').monotonic()
        response = requests.put(
            url,
            headers=headers,
            json=payload,
            timeout=15
        )
        elapsed = (__import__('time').monotonic() - start) * 1000

        print(f"{'─'*60}")
        print(f"  HTTP Status  : {response.status_code}")
        print(f"  Durasi       : {elapsed:.1f}ms")
        print(f"  Content-Type : {response.headers.get('Content-Type', 'N/A')}")
        print(f"{'─'*60}")
        print(f"\nRespons dari API SIMGOS:")

        try:
            resp_json = response.json()
            print(json.dumps(resp_json, indent=2, ensure_ascii=False))
        except Exception:
            print(response.text[:500])

        print()
        if response.status_code in (200, 201, 204):
            print("  ✓ TEST BERHASIL — Data berhasil disimpan ke SIMGOS")
        else:
            print(f"  ✗ TEST GAGAL — HTTP {response.status_code}")

    except requests.exceptions.ConnectionError:
        print(f"✗ Koneksi gagal: Pastikan SIMGOS API berjalan di {base_url}")
        sys.exit(1)
    except requests.exceptions.Timeout:
        print("✗ Timeout: Server tidak merespons dalam 15 detik")
        sys.exit(1)
    except Exception as e:
        print(f"✗ Error: {e}")
        sys.exit(1)


def build_test_payload(norm: str, accession: str) -> dict:
    """Membangun payload JSON test yang lengkap sesuai DDL asli SIMGOS."""
    now = datetime.now()
    # Accession max 11 char (CHAR(11) TINDAKAN_MEDIS)
    accession_11 = accession[:11]
    return {
        # Data Pasien
        "norm":                norm,
        "nama":                "BAMBANG WAHONO SUHARTO",
        "tanggal_lahir":       "1978-03-15",
        "jenis_kelamin":       "1",

        # Data Tindakan
        "accession_number":    accession_11,
        "kunjungan_id":        f"KNJ-{now.strftime('%Y%m%d%H%M')}",
        "kode_tindakan":       "24630-5",    # max 10 char (CHAR(10) KODE)
        "nama_tindakan":       "CT Scan Thoraks",
        "tanggal_pemeriksaan": now.strftime("%Y-%m-%d %H:%M:%S"),
        "dokter_pengirim":     "dr. MUHAMMAD HIDAYAT, Sp.PD",

        # Hasil Radiologi — sesuai DDL layanan.hasil_rad
        "klinis":              "Batuk berkepanjangan > 3 minggu. Riwayat kontak TB.",
        "hasil":               "Tampak infiltrat di lobus kanan atas. Kavitas diameter ±2cm.",
        "kesan":               "TB Paru aktif dengan kavitas lobus kanan atas.",
        "usul":                "Saran sputum BTA dan kultur. Konsultasi paru.",
        "btk":                 "",           # BTK: Bacaan Tambahan/Keterangan (kosong)
        "kritis":              1,            # 0=Kritis, 1=Tidak Kritis
        # DOKTER adalah smallint (ID dokter di database), bukan nama string!
        # Isi dengan ID dokter yang valid di SIMGOS. 0 = unknown/system.
        "dokter_id":           0,
        "dokter_nama":         "dr. AGUS RADIOLOGI, Sp.Rad",  # Untuk referensi log saja
        "oleh":                0,            # OLEH: ID user (0 = system bridging)

        # PACS Integration — pacs_hook_uid → DIKIRIM_DARI char(36)
        # Isi dengan StudyInstanceUID DICOM (max 36 karakter)
        "pacs_hook_uid":       "1.2.840.113619.2.55.3.60703",  # max 36 char

        # Metadata
        "kode_tindakan":       "24630-5",
        "message_type":        "ORU^R01",
        "converted_at":        now.strftime("%Y-%m-%d %H:%M:%S"),
    }


def main():
    parser = argparse.ArgumentParser(
        description="Direct API tester untuk endpoint PUT SIMGOS (tanpa HL7/MLLP)"
    )
    parser.add_argument('--url',  default='http://localhost/simgos-api/api/v1',
                        help='Base URL API SIMGOS')
    parser.add_argument('--key',  default='PACS-BRIDGE-SECRET-KEY-2024',
                        help='API Key autentikasi')
    parser.add_argument('--norm', default='123456',
                        help='NORM pasien untuk test')
    parser.add_argument('--acc',  default=None,
                        help='Accession number (default: auto-generate)')
    args = parser.parse_args()

    accession = args.acc or f"ACC-{datetime.now().strftime('%Y%m%d%H%M%S')}"
    payload   = build_test_payload(args.norm, accession)

    print("\n  ╔══════════════════════════════════════════╗")
    print("  ║  SIMGOS API — Direct PUT Tester          ║")
    print("  ╚══════════════════════════════════════════╝")

    send_test_put(args.url, args.key, accession, payload)


if __name__ == "__main__":
    main()
