#!/usr/bin/env python3
"""
put_hl7.py - Kirim Data ke SIMGOS (MLLP atau HTTP PUT langsung)
================================================================
Dua jalur pengiriman:

  A. MLLP  (default)  : HL7 ORU^R01 -> MLLP 2575 -> middleware -> API -> DB
  B. --direct         : JSON -> HTTP PUT -> API -> DB   (tanpa HL7/MLLP)

PUT MANDIRI (payload buatan sendiri):
  --payload-file x.json     kirim isi file JSON apa adanya
  --hasil "teks"            ganti isi HASIL
  --kesan "teks"            ganti isi KESAN
  --klinis "teks"           ganti isi KLINIS
  --usul "teks"             ganti isi USUL
  --norm 123456             NORM pasien
  --acc RADTEST01           accession number (max 11 char)

BUKTI INSERT vs UPDATE:
  --watch   rekam snapshot SEBELUM kirim, lalu diff SESUDAH kirim.
            Status per accession ditampilkan otomatis:

              status=INSERT  -> accession belum ada, baris baru dibuat
              status=UPDATE  -> accession sudah ada, baris diperbarui
                                (TINDAKAN_MEDIS unik, ON DUPLICATE KEY UPDATE)

Penggunaan:
    # dasar
    python testing/put_hl7.py
    python testing/put_hl7.py --direct

    # PUT mandiri: isi laporan sendiri
    python testing/put_hl7.py --direct --acc RADTEST01 --norm 123456 \\
        --hasil "Tampak kavitas 2cm lobus kanan" --kesan "TB paru aktif"

    # PUT mandiri dari file JSON
    python testing/put_hl7.py --direct --payload-file my_payload.json

    # bukti INSERT vs UPDATE: kirim 2x accession sama sambil direkam
    python testing/put_hl7.py --direct --acc RADTEST01 --watch --hasil "versi 1"
    python testing/put_hl7.py --direct --acc RADTEST01 --watch --hasil "versi 2"

    # kirim dari file .hl7 hasil eksport PACS
    python testing/put_hl7.py --file testing/sample_oru_message.hl7 --watch

Exit code: 0 = ACK AA / HTTP 200, 1 = gagal.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from common import (
    OK, FAIL, SKIP, ROOT, Config,
    accession_from_file, banner, build_direct_payload, build_hl7,
    diff_rows, fetch_state, make_accession, print_rows, save_snapshot,
    send_direct, send_mllp, step,
)


# ---------------------------------------------------------------------------
# Snapshot & perbandingan
# ---------------------------------------------------------------------------
def record_before(config: Config, accession: str) -> tuple[dict | None, bool]:
    """
    Rekam keadaan DB sebelum PUT.

    Returns: (snapshot_dict | None, exists)
    """
    try:
        state = fetch_state(config, accession)
    except Exception as e:
        step(SKIP, "Snapshot sebelum", f"gagal baca DB: {str(e)[:120]}")
        return None, False

    exists = bool(state["hasil_rad"][1])
    path = save_snapshot(accession, state)
    jumlah = len(state["hasil_rad"][1])
    step(OK, "Snapshot sebelum", f"hasil_rad={jumlah} baris | {path.name}")
    return state, exists


def compare_after(config: Config, accession: str, before: dict | None) -> None:
    """Ambil keadaan sesudah PUT, bandingkan dengan snapshot."""
    banner("PERBANDINGAN SESUDAH PUT")

    try:
        after = fetch_state(config, accession)
    except Exception as e:
        step(FAIL, "Baca DB sesudah", str(e)[:160])
        return

    cols_h, rows_h = after["hasil_rad"]
    cols_l, rows_l = after["hl7_logs"]

    if before is None:
        step(SKIP, "Perbandingan", "snapshot sebelum tidak tersedia")
        step(OK if rows_h else FAIL, "layanan.hasil_rad", f"{len(rows_h)} baris")
        print_rows(cols_h, rows_h)
        return

    before_hasil = len(before["hasil_rad"][1])

    # ── Status: INSERT atau UPDATE ──────────────────────────────────────────
    if before_hasil == 0 and rows_h:
        step(OK, "STATUS", "INSERT — accession belum ada, baris baru dibuat")
    elif before_hasil and rows_h:
        step(OK, "STATUS", "UPDATE — accession sudah ada, baris diperbarui (upsert)")
    elif before_hasil and not rows_h:
        step(FAIL, "STATUS", "baris hilang setelah PUT")
    else:
        step(FAIL, "STATUS", "tidak ada baris sesudah PUT")

    # ── Diff kolom ──────────────────────────────────────────────────────────
    diffs = diff_rows(cols_h, before["hasil_rad"][1], rows_h)
    if diffs:
        print()
        print(f"      {'kolom':<16} {'sebelum':<34} sesudah")
        print(f"      {'-' * 16} {'-' * 34} {'-' * 34}")
        for col, old, new in diffs:
            print(f"      {col:<16} {old[:32]:<34} {new[:60]}")
    elif before_hasil == 0 and rows_h:
        pass  # INSERT: belum ada nilai lama untuk dibandingkan
    elif rows_h:
        step(SKIP, "Kolom", "nilai identik dengan sebelum PUT")

    # ── Isi baris hasil ─────────────────────────────────────────────────────
    print()
    step(OK if rows_h else FAIL, "layanan.hasil_rad", f"{len(rows_h)} baris")
    print_rows(cols_h, rows_h)
    step(OK if rows_l else FAIL, "ris.hl7_logs", f"{len(rows_l)} baris")
    print_rows(cols_l, rows_l)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> int:
    parser = argparse.ArgumentParser(
        description="Kirim data ke SIMGOS via MLLP atau HTTP PUT langsung",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Contoh PUT mandiri:\n"
            "  python testing/put_hl7.py --direct --acc RADTEST01 --norm 123456 "
            "--hasil 'Kavitas 2cm' --kesan 'TB aktif'\n"
            "  python testing/put_hl7.py --direct --payload-file my_payload.json\n"
        ),
    )

    # ── Jalur pengiriman ────────────────────────────────────────────────────
    parser.add_argument("--direct", action="store_true",
                        help="HTTP PUT langsung ke API, tanpa HL7/MLLP")
    parser.add_argument("--file", default=None, help="Kirim dari file .hl7")
    parser.add_argument("--payload-file", default=None,
                        help="Kirim isi file JSON sebagai body PUT (mode --direct)")

    # ── Identitas ───────────────────────────────────────────────────────────
    parser.add_argument("--acc", default=None,
                        help="Accession number (default: otomatis RAD<HHMMSS><nn>)")
    parser.add_argument("--norm", default="99001234", help="NORM pasien")

    # ── Isi laporan (PUT mandiri) ───────────────────────────────────────────
    parser.add_argument("--hasil",  default=None, help="Isi kolom HASIL")
    parser.add_argument("--kesan",  default=None, help="Isi kolom KESAN")
    parser.add_argument("--klinis", default=None, help="Isi kolom KLINIS")
    parser.add_argument("--usul",   default=None, help="Isi kolom USUL")
    parser.add_argument("--dokter-id", type=int, default=None,
                        help="ID dokter pembaca (kolom DOKTER, smallint)")

    # ── Kontrol ─────────────────────────────────────────────────────────────
    parser.add_argument("--repeat", type=int, default=1,
                        help="Jumlah pengiriman, accession sama (uji idempotensi)")
    parser.add_argument("--watch", action="store_true",
                        help="Rekam snapshot sebelum, tampilkan diff sesudah")
    parser.add_argument("--host", default=None, help="Host MLLP (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=None, help="Port MLLP (default dari .env)")
    parser.add_argument("--timeout", type=float, default=15.0, help="Timeout detik")
    parser.add_argument("--api-url", default=None, help="Base URL API SIMGOS")
    parser.add_argument("--api-key", default=None, help="API key (default dari .env)")
    args = parser.parse_args()

    # Tanpa argumen sama sekali (misal tekan tombol Run di IDE):
    # pakai testing/payload.json + --watch. Cukup edit file itu, simpan, Run.
    if len(sys.argv) == 1:
        default_payload = ROOT / "testing" / "payload.json"
        if default_payload.exists():
            args.payload_file = str(default_payload)
            args.watch = True
            args.direct = True
            print()
            print("  Tanpa argumen -> memakai testing/payload.json + --watch")
        else:
            print()
            print("  Tanpa argumen dan testing/payload.json tidak ada.")
            print("  Mengirim pesan HL7 contoh via MLLP dengan accession baru.")

    config = Config(args)

    print()
    print("  Kirim Data — Bridging PACS-SIMGOS")
    config.summary()
    print(f"  Mode      : {'HTTP PUT langsung (tanpa MLLP)' if args.direct else 'MLLP'}")

    # ── Siapkan pesan / payload ─────────────────────────────────────────────
    banner("PERSIAPAN")

    hl7_text = ""
    payload: dict | None = None

    if args.payload_file:
        path = Path(args.payload_file)
        if not path.is_absolute():
            path = (ROOT / args.payload_file) if (ROOT / args.payload_file).exists() \
                else Path.cwd() / args.payload_file
        if not path.exists():
            step(FAIL, "Payload file", f"tidak ditemukan: {args.payload_file}")
            return 1
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            step(FAIL, "Payload file", f"JSON tidak valid: {e}")
            return 1
        accession = args.acc or str(
            payload.get("accession_number")
            or payload.get("Report", {}).get("order", {}).get("id")
            or make_accession()
        )[:11]
        payload["accession_number"] = accession
        step(OK, "Payload file", f"{path.name} | accession={accession}")
        args.direct = True  # payload JSON hanya masuk akal lewat jalur direct

    elif args.file:
        path = Path(args.file)
        if not path.is_absolute():
            path = (ROOT / args.file) if (ROOT / args.file).exists() else Path.cwd() / args.file
        if not path.exists():
            step(FAIL, "File HL7", f"tidak ditemukan: {args.file}")
            return 1
        hl7_text = path.read_text(encoding="utf-8", errors="replace")
        accession = args.acc or accession_from_file(path)
        if not accession:
            step(FAIL, "Accession", "tidak ditemukan di OBR-3 file HL7")
            return 1
        step(OK, "File HL7", f"{path.name} | accession={accession}")

    else:
        accession = (args.acc or make_accession())[:11]
        if args.direct:
            payload = build_direct_payload(args.norm, accession, hasil=args.hasil)
            for field, value in (
                ("kesan", args.kesan), ("klinis", args.klinis),
                ("usul", args.usul), ("dokter_id", args.dokter_id),
            ):
                if value is not None:
                    payload[field] = value
            step(OK, "Payload JSON", f"accession={accession} norm={args.norm}")
        else:
            hl7_text = build_hl7(args.norm, accession)
            step(OK, "Pesan HL7", f"accession={accession} norm={args.norm}")

    if args.direct and payload is None:
        payload = build_direct_payload(args.norm, accession, hasil=args.hasil)
        for field, value in (
            ("kesan", args.kesan), ("klinis", args.klinis),
            ("usul", args.usul), ("dokter_id", args.dokter_id),
        ):
            if value is not None:
                payload[field] = value

    if payload is not None and args.watch:
        print()
        print("  Payload yang dikirim:")
        print(json.dumps(payload, indent=2, ensure_ascii=False, default=str)[:2000])

    # ── Snapshot sebelum ────────────────────────────────────────────────────
    before = None
    if args.watch:
        banner("SEBELUM PUT")
        before, exists = record_before(config, accession)
        if exists:
            print(f"      Accession SUDAH ADA -> PUT ini akan jadi UPDATE (upsert)")
        else:
            print(f"      Accession BELUM ADA -> PUT ini akan jadi INSERT")

    # ── Kirim ───────────────────────────────────────────────────────────────
    banner("KIRIM")

    all_ok = True
    for attempt in range(1, args.repeat + 1):
        label = f"percobaan {attempt}/{args.repeat}"

        if args.direct:
            ok, detail, ms = send_direct(config, payload or {}, accession)
            step(OK if ok else FAIL, f"HTTP PUT {label}  {ms:.1f}ms", detail)
        else:
            if args.repeat > 1 and not args.file:
                hl7_text = build_hl7(args.norm, accession)  # timestamp baru, accession sama
            ok, ack, ms = send_mllp(hl7_text, config.mllp_host, config.mllp_port, config.timeout)
            msa = next((ln for ln in ack.split("\n") if ln.startswith("MSA")), ack[:160])
            step(OK if ok else FAIL, f"MLLP {label}  {ms:.1f}ms", msa)

        all_ok = all_ok and ok
        if attempt < args.repeat:
            import time
            time.sleep(0.5)

    # ── Diff sesudah ────────────────────────────────────────────────────────
    if args.watch:
        compare_after(config, accession, before)

    banner("RINGKASAN")
    step(OK if all_ok else FAIL, "Pengiriman", f"accession={accession}")
    if not args.watch:
        print()
        print(f"      Untuk bukti INSERT vs UPDATE, jalankan ulang dengan --watch:")
        print(f"      python testing/put_hl7.py --direct --acc {accession} --watch "
              f"--hasil 'isi baru'")
    print()
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
