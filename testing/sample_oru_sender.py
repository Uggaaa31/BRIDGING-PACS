#!/usr/bin/env python3
"""
sample_oru_sender.py - Simulator Pengirim HL7 ORU^R01 via MLLP
================================================================
Script pengujian yang mensimulasikan mesin PACS mengirimkan
pesan HL7 ORU^R01 ke Middleware Bridging melalui protokol TCP/MLLP.

Penggunaan:
    # Kirim pesan default (built-in sample)
    python sample_oru_sender.py

    # Kirim dari file .hl7 custom
    python sample_oru_sender.py --file testing/sample_oru_message.hl7

    # Tentukan host/port berbeda
    python sample_oru_sender.py --host 192.168.1.100 --port 2575

    # Kirim N kali (stress test)
    python sample_oru_sender.py --count 5 --delay 1.0

    # Mode verbose (tampilkan raw bytes)
    python sample_oru_sender.py --verbose
"""

import socket
import time
import argparse
import sys
import os
from datetime import datetime

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# ─── Konstanta MLLP ───────────────────────────────────────────────────────────
MLLP_START  = b'\x0b'
MLLP_END    = b'\x1c\x0d'

# ─── Pesan HL7 ORU^R01 Default ───────────────────────────────────────────────
# (Sesuaikan nilai ini untuk pengujian berbeda)
def build_sample_hl7(msg_id: str = None, norm: str = "00123456") -> str:
    """
    Membangun pesan HL7 ORU^R01 sample dinamis.
    Timestamp dan Message ID dibuat otomatis agar unik setiap pengiriman.
    """
    now         = datetime.now()
    ts          = now.strftime("%Y%m%d%H%M%S")
    ts_short    = now.strftime("%Y%m%d%H%M")
    msg_ctrl_id = msg_id or f"MSG-{ts}-{os.getpid()}"
    # Format accession number unik 11 karakter (sesuai char(11) TINDAKAN_MEDIS)
    acc_number  = f"RAD{now.strftime('%H%M%S')}{now.microsecond // 10000:02d}"

    # Setiap baris diakhiri dengan \r (Carriage Return) sesuai standar HL7
    segments = [
        f"MSH|^~\\&|PACS_SIM|RSUP_RAD|SIMGOS_BRIDGE|RSUP|{ts}||ORU^R01|{msg_ctrl_id}|P|2.5|||NE|AL|IDN|UTF-8",
        f"PID|1||{norm}^^^RSUP^PI||SUHARTO^BAMBANG^WAHONO||19780315|M|||Jl. Sudirman No. 10^^Jakarta^DKI^10110^IDN",
        f"PV1|1|O|RAD^RAD-01^RSUP||||dr. HIDAYAT^MUHAMMAD^^^dr.||||||||||A|KNJ-{ts}",
        f"ORC|RE|ORD-{ts}|{acc_number}|GRP-001||||||||dr. HIDAYAT^MUHAMMAD",
        f"OBR|1|ORD-{ts}|{acc_number}|24630-5^CT Scan Thoraks^LN|||{ts_short}00|{ts_short}30|||||KLINIS: Batuk berkepanjangan|||||dr. HIDAYAT^MUHAMMAD||||dr. RADIOLOGI^AGUS",
        "OBX|1|TX|59776-5^KLINIS^LN||Batuk berkepanjangan > 3 minggu. Riwayat kontak TB. Demam subfebris.||||||F",
        "OBX|2|TX|59780-7^HASIL^LN||Tampak infiltrat di lobus kanan atas. Kavitas diameter +/-2cm. Tidak ada efusi pleura.||||||F",
        "OBX|3|TX|19005-8^KESAN^LN||TB Paru aktif dengan kavitas lobus kanan atas.||||||F",
        "OBX|4|TX|18783-1^USUL^LN||Saran pemeriksaan sputum BTA. Konsultasi paru.||||||F",
        f"OBX|5|ST|STUDY_UID^StudyInstanceUID^DICOM||1.2.840.113619.2.55.3.{ts}.123||||||F",
    ]

    return "\r".join(segments) + "\r"


def send_hl7_mllp(
    hl7_message: str,
    host: str,
    port: int,
    timeout: int = 15,
    verbose: bool = False
) -> tuple[bool, str]:
    """
    Mengirimkan satu pesan HL7 ke server MLLP dan menunggu ACK.

    Args:
        hl7_message : String pesan HL7 (tanpa MLLP wrapper).
        host        : Host MLLP server.
        port        : Port MLLP server.
        timeout     : Timeout socket dalam detik.
        verbose     : Tampilkan detail bytes.

    Returns:
        Tuple (success: bool, ack_message: str)
    """
    # Bungkus dengan MLLP
    encoded    = hl7_message.encode('utf-8')
    mllp_msg   = MLLP_START + encoded + MLLP_END

    if verbose:
        print(f"\n[DEBUG] MLLP bytes yang akan dikirim ({len(mllp_msg)} bytes):")
        print(f"  Start byte: {MLLP_START.hex()}")
        print(f"  HL7 content ({len(encoded)} bytes):")
        for line in hl7_message.split('\r'):
            if line:
                print(f"    {line}")
        print(f"  End bytes: {MLLP_END.hex()}")

    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(timeout)
            sock.connect((host, port))
            print(f"  ✓ Terhubung ke {host}:{port}")

            # Kirim
            sock.sendall(mllp_msg)
            print(f"  → Pesan terkirim ({len(mllp_msg)} bytes)")

            # Terima ACK
            ack_data = b""
            while True:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                ack_data += chunk
                if b'\x1c' in ack_data:
                    break

            # Bersihkan MLLP dari ACK
            ack_clean = ack_data.replace(MLLP_START, b'').replace(MLLP_END, b'').replace(b'\x1c', b'')
            ack_text  = ack_clean.decode('utf-8', errors='replace').replace('\r', '\n').strip()

            if verbose:
                print(f"\n[DEBUG] ACK diterima ({len(ack_data)} bytes):\n{ack_text}")

            # Tentukan sukses berdasarkan MSA|AA
            success = 'MSA|AA' in ack_text
            return success, ack_text

    except socket.timeout:
        return False, "ERROR: Timeout saat menunggu respons dari server MLLP."
    except ConnectionRefusedError:
        return False, f"ERROR: Koneksi ditolak. Pastikan MLLP server berjalan di {host}:{port}."
    except Exception as e:
        return False, f"ERROR: {e}"


def main():
    parser = argparse.ArgumentParser(
        description="Simulator pengirim HL7 ORU^R01 via MLLP (untuk testing Bridging PACS-SIMGOS)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Contoh penggunaan:
  python sample_oru_sender.py
  python sample_oru_sender.py --file testing/sample_oru_message.hl7
  python sample_oru_sender.py --host 127.0.0.1 --port 2575 --count 3 --delay 2
  python sample_oru_sender.py --norm 99001234 --verbose
        """
    )
    parser.add_argument('--host',    default='127.0.0.1',  help='Host MLLP server (default: 127.0.0.1)')
    parser.add_argument('--port',    type=int, default=2575, help='Port MLLP server (default: 2575)')
    parser.add_argument('--file',    default=None,          help='Path ke file .hl7 custom')
    parser.add_argument('--norm',    default='00123456',    help='NORM pasien untuk pesan sample')
    parser.add_argument('--count',   type=int, default=1,   help='Jumlah pengiriman (default: 1)')
    parser.add_argument('--delay',   type=float, default=0.5, help='Jeda antar pengiriman dalam detik')
    parser.add_argument('--timeout', type=int, default=15,  help='Timeout socket detik (default: 15)')
    parser.add_argument('--verbose', action='store_true',   help='Tampilkan detail bytes MLLP')
    args = parser.parse_args()

    print("=" * 60)
    print("  PACS HL7 Sender Simulator — Bridging PACS-SIMGOS")
    print("=" * 60)
    print(f"  Target  : {args.host}:{args.port}")
    print(f"  Jumlah  : {args.count} pesan")
    print(f"  Source  : {'File: ' + args.file if args.file else 'Built-in sample'}")
    print("=" * 60)

    # Baca pesan dari file jika ada
    if args.file:
        if not os.path.exists(args.file):
            print(f"✗ File tidak ditemukan: {args.file}")
            sys.exit(1)
        with open(args.file, 'r', encoding='utf-8') as f:
            hl7_template = f.read()
        use_file = True
    else:
        hl7_template = None
        use_file = False

    # Kirim pesan
    success_count = 0
    for i in range(1, args.count + 1):
        print(f"\n[{i}/{args.count}] Mengirim pesan HL7...")

        if use_file:
            # Ganti timestamp di MSH untuk membuat setiap pesan unik
            now = datetime.now().strftime("%Y%m%d%H%M%S")
            hl7_msg = hl7_template
            # Update MSH timestamp (field ke-7, 0-based setelah split |)
            lines = hl7_msg.replace('\r\n', '\n').replace('\r', '\n').split('\n')
            new_lines = []
            for line in lines:
                if line.startswith('MSH'):
                    parts = line.split('|')
                    if len(parts) > 7:
                        parts[7] = now
                    new_lines.append('|'.join(parts))
                else:
                    new_lines.append(line)
            hl7_msg = '\r'.join(ln for ln in new_lines if ln) + '\r'
        else:
            hl7_msg = build_sample_hl7(norm=args.norm)

        # Tampilkan preview pesan
        preview_lines = hl7_msg.replace('\r', '\n').strip().split('\n')
        print(f"  MSH: {preview_lines[0][:80]}..." if len(preview_lines[0]) > 80 else f"  MSH: {preview_lines[0]}")

        start = time.monotonic()
        ok, ack = send_hl7_mllp(
            hl7_msg, args.host, args.port,
            timeout=args.timeout, verbose=args.verbose
        )
        elapsed = (time.monotonic() - start) * 1000

        if ok:
            print(f"  ✓ ACK diterima (AA) dalam {elapsed:.1f}ms")
            # Tampilkan MSA segment dari ACK
            for line in ack.split('\n'):
                if line.startswith('MSA'):
                    print(f"  ACK MSA: {line}")
            success_count += 1
        else:
            print(f"  ✗ GAGAL dalam {elapsed:.1f}ms")
            print(f"  Alasan: {ack}")

        if i < args.count:
            time.sleep(args.delay)

    # Ringkasan
    print("\n" + "=" * 60)
    print(f"  Selesai: {success_count}/{args.count} pesan berhasil terkirim dan di-ACK")
    print("=" * 60)
    sys.exit(0 if success_count == args.count else 1)


if __name__ == "__main__":
    main()
