#!/usr/bin/env python3
"""
check_db.py - Lihat Isi Database, Snapshot, dan Diff Sebelum/Sesudah PUT
=========================================================================
Tiga fungsi:

  1. LIHAT     : tampilkan baris hasil_rad + hl7_logs untuk satu accession
                 atau N transaksi terakhir.

  2. SNAPSHOT  : rekam keadaan satu accession ke file JSON SEBELUM kirim data.
                 Lalu bandingkan dengan keadaan SESUDAH PUT, sehingga terlihat
                 kolom mana yang INSERT (baru) dan kolom mana yang UPDATE.

  3. DIFF      : tampilkan perbedaan kolom per kolom: nilai lama vs nilai baru.

Alur khas untuk membuktikan INSERT vs UPDATE:

    # 1. kirim PUT pertama (accession baru)
    python testing/put_hl7.py --acc RADTEST0001

    # 2. rekam keadaan sekarang
    python testing/check_db.py snapshot --acc RADTEST0001

    # 3. kirim PUT kedua dengan accession SAMA, isi hasil berbeda
    python testing/put_hl7.py --acc RADTEST0001 --hasil "Temuan baru: kavitas mengecil"

    # 4. lihat apa yang berubah
    python testing/check_db.py diff --acc RADTEST0001

Penggunaan:
    python testing/check_db.py                          # 10 transaksi terakhir
    python testing/check_db.py --acc RAD17474271        # satu accession
    python testing/check_db.py snapshot --acc RAD17474271
    python testing/check_db.py diff --acc RAD17474271
    python testing/check_db.py snapshots                # daftar file snapshot
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from common import (
    OK, FAIL, SKIP, SNAPSHOT_DIR, Config,
    banner, diff_rows, fetch_state, load_snapshot, print_rows,
    save_snapshot, state_from_snapshot, step,
)


def show_recent(config: Config, limit: int) -> bool:
    """Tampilkan N baris terakhir hasil_rad + hl7_logs."""
    banner(f"{limit} TRANSAKSI TERAKHIR — {config.tbl_hasil}")
    try:
        (cols_h, rows_h), (cols_l, rows_l) = __import__("common").db_query(
            config,
            [
                "SELECT ID,TINDAKAN_MEDIS,TANGGAL,KRITIS,DOKTER,OTOMATIS,"
                "DIKIRIM_DARI,LEFT(HASIL,60) AS HASIL,LEFT(KESAN,50) AS KESAN "
                f"FROM {config.tbl_hasil} ORDER BY ID DESC LIMIT {limit}",
                "SELECT ID,REF_ID,JENIS,PROVIDER,KODE,KIRIM,"
                "TGL_REQUEST,TGL_RESPONSE "
                f"FROM {config.tbl_log} ORDER BY ID DESC LIMIT {limit}",
            ],
        )
    except Exception as e:
        step(FAIL, "Query gagal", str(e)[:200])
        return False

    step(OK if rows_h else FAIL, "layanan.hasil_rad", f"{len(rows_h)} baris")
    print_rows(cols_h, rows_h)
    step(OK if rows_l else FAIL, "ris.hl7_logs", f"{len(rows_l)} baris")
    print_rows(cols_l, rows_l)
    return bool(rows_h)


def show_accession(config: Config, accession: str) -> bool:
    banner(f"ACCESSION {accession}")
    try:
        state = fetch_state(config, accession)
    except Exception as e:
        step(FAIL, "Query gagal", str(e)[:200])
        return False

    cols_h, rows_h = state["hasil_rad"]
    cols_l, rows_l = state["hl7_logs"]

    step(OK if rows_h else FAIL, "layanan.hasil_rad", f"{len(rows_h)} baris")
    print_rows(cols_h, rows_h)

    step(OK if rows_l else FAIL, "ris.hl7_logs", f"{len(rows_l)} baris")
    print_rows(cols_l, rows_l)

    if not rows_h and not rows_l:
        print("      Data belum ada. PUT belum pernah masuk untuk accession ini.")
        return False
    return bool(rows_h)


def do_snapshot(config: Config, accession: str) -> bool:
    banner("SNAPSHOT SEBELUM PUT")
    try:
        state = fetch_state(config, accession)
    except Exception as e:
        step(FAIL, "Query gagal", str(e)[:200])
        return False

    cols_h, rows_h = state["hasil_rad"]
    path = save_snapshot(accession, state)

    if rows_h:
        step(OK, "Snapshot tersimpan", str(path))
        print_rows(cols_h, rows_h)
        print()
        print("      Sekarang kirim PUT kedua dengan accession SAMA, lalu jalankan:")
        print(f"      python testing/check_db.py diff --acc {accession}")
    else:
        step(SKIP, "Snapshot tersimpan (kosong)", str(path))
        print("      Belum ada data untuk accession ini.")
        print("      Jalankan PUT dulu, atau pakai accession yang sudah ada.")
    return True


def list_snapshots() -> bool:
    banner("DAFTAR SNAPSHOT")
    if not SNAPSHOT_DIR.exists():
        step(SKIP, "Snapshot", f"belum ada file di {SNAPSHOT_DIR}")
        return True

    files = sorted(SNAPSHOT_DIR.glob("*.json"))
    if not files:
        step(SKIP, "Snapshot", f"belum ada file di {SNAPSHOT_DIR}")
        return True

    for path in files:
        try:
            data = load_snapshot(path)
            n_hasil = len(data["hasil_rad"]["rows"])
            n_log = len(data["hl7_logs"]["rows"])
            step(OK, path.name,
                 f"{data.get('taken_at', '?')} | accession={data.get('accession', '?')} "
                 f"| hasil_rad={n_hasil} hl7_logs={n_log}")
        except Exception as e:
            step(FAIL, path.name, str(e)[:120])
    return True


def do_diff(config: Config, accession: str, snapshot_path: Path | None) -> bool:
    banner(f"DIFF SEBELUM/SESUDAH — {accession}")

    if snapshot_path:
        path = snapshot_path
    else:
        candidates = sorted(SNAPSHOT_DIR.glob(f"{accession}-*.json"))
        if not candidates:
            step(FAIL, "Snapshot", f"tidak ada di {SNAPSHOT_DIR}")
            print("      Buat dulu: python testing/check_db.py snapshot --acc " + accession)
            return False
        path = candidates[-1]

    before_data = load_snapshot(path)
    before = state_from_snapshot(before_data)
    step(OK, "Snapshot", f"{path.name} | diambil {before_data.get('taken_at', '?')}")

    try:
        after = fetch_state(config, accession)
    except Exception as e:
        step(FAIL, "Query sesudah gagal", str(e)[:200])
        return False

    changed_any = False

    for key, label in (("hasil_rad", "layanan.hasil_rad"), ("hl7_logs", "ris.hl7_logs")):
        cols_b, rows_b = before[key]
        cols_a, rows_a = after[key]

        banner(label)

        if not rows_b and rows_a:
            step(OK, "INSERT", f"baru {len(rows_a)} baris (sebelumnya 0)")
            print_rows(cols_a, rows_a)
            changed_any = True
            continue

        if rows_b and not rows_a:
            step(FAIL, "HILANG", f"sebelumnya {len(rows_b)} baris, sekarang 0")
            changed_any = True
            continue

        if not rows_b and not rows_a:
            step(SKIP, "Tidak berubah", "0 baris sebelum dan sesudah")
            continue

        if len(rows_a) != len(rows_b):
            step(OK, "JUMLAH BARIS BERUBAH", f"{len(rows_b)} -> {len(rows_a)}")
            changed_any = True

        diffs = diff_rows(cols_a, rows_b, rows_a)
        if diffs:
            step(OK, "UPDATE", f"{len(diffs)} kolom berubah")
            print()
            print(f"      {'kolom':<16} {'sebelum':<34} sesudah")
            print(f"      {'-' * 16} {'-' * 34} {'-' * 34}")
            for col, old, new in diffs:
                print(f"      {col:<16} {old[:32]:<34} {new[:60]}")
            changed_any = True
        else:
            step(SKIP, "Nilai identik", "baris ada, isi kolom sama")

    print()
    if changed_any:
        print("  Kesimpulan: DATA BERUBAH sejak snapshot.")
    else:
        print("  Kesimpulan: TIDAK ADA PERUBAHAN sejak snapshot.")
    return True


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Lihat isi DB, snapshot sebelum PUT, dan diff sebelum/sesudah",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Contoh alur INSERT vs UPDATE:\n"
            "  python testing/put_hl7.py --acc RADTEST0001\n"
            "  python testing/check_db.py snapshot --acc RADTEST0001\n"
            "  python testing/put_hl7.py --acc RADTEST0001 --hasil 'Temuan baru'\n"
            "  python testing/check_db.py diff --acc RADTEST0001\n"
        ),
    )
    parser.add_argument("mode", nargs="?", default="show",
                        choices=["show", "snapshot", "diff", "snapshots"],
                        help="show (default) | snapshot | diff | snapshots")
    parser.add_argument("--acc", default=None, help="Accession number")
    parser.add_argument("--limit", type=int, default=10,
                        help="Jumlah baris terakhir untuk mode show tanpa --acc")
    parser.add_argument("--snapshot-file", default=None,
                        help="Path file snapshot tertentu untuk mode diff")
    parser.add_argument("--api-url", default=None, help="Base URL API SIMGOS")
    parser.add_argument("--api-key", default=None, help="API key (default dari .env)")
    parser.add_argument("--timeout", type=float, default=15.0, help="Timeout detik")
    args = parser.parse_args()

    config = Config(args)

    print()
    print("  Cek Database — Bridging PACS-SIMGOS")
    config.summary()

    if args.mode == "snapshots":
        return 0 if list_snapshots() else 1

    if not args.acc and args.mode in ("snapshot", "diff"):
        print()
        step(FAIL, "Accession", "--acc wajib untuk mode ini")
        return 1

    if args.mode == "show":
        if args.acc:
            return 0 if show_accession(config, args.acc) else 1
        return 0 if show_recent(config, args.limit) else 1

    if args.mode == "snapshot":
        return 0 if do_snapshot(config, args.acc) else 1

    snap = Path(args.snapshot_file) if args.snapshot_file else None
    return 0 if do_diff(config, args.acc, snap) else 1


if __name__ == "__main__":
    sys.exit(main())
