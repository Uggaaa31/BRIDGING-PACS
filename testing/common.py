#!/usr/bin/env python3
"""
common.py - Helper bersama untuk script testing Bridging PACS-SIMGOS
=====================================================================
Dipakai oleh:
  check_connection.py -> cek MLLP / API / DB
  put_hl7.py          -> kirim HL7 (MLLP) atau HTTP PUT langsung
  check_db.py         -> lihat isi DB, snapshot, dan diff sebelum/sesudah

Konfigurasi dibaca dari .env di root project. Environment variable proses
selalu menang atas isi .env.

Verifikasi DB memakai pymysql bila terpasang, atau fallback ke container
`mysql:8.0` lewat Docker (tidak perlu install client MySQL).
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

try:
    import requests
except ImportError:
    sys.exit("Dependensi hilang: jalankan `pip install requests`.")

ROOT         = Path(__file__).resolve().parent.parent
SNAPSHOT_DIR = Path(__file__).resolve().parent / ".snapshots"

MLLP_START = b"\x0b"
MLLP_END   = b"\x1c\x0d"

OK   = "[ OK ]"
FAIL = "[FAIL]"
SKIP = "[SKIP]"


# ---------------------------------------------------------------------------
# Konfigurasi
# ---------------------------------------------------------------------------
def load_env() -> dict:
    """Baca .env root project tanpa dependensi python-dotenv."""
    values: dict[str, str] = {}
    env_path = ROOT / ".env"
    if env_path.exists():
        for raw in env_path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, val = line.split("=", 1)
            values[key.strip()] = val.strip().strip('"').strip("'")
    return values


_ENV = load_env()


def cfg(key: str, default: str = "") -> str:
    """Environment variable proses menang atas .env, lalu default."""
    return os.getenv(key) or _ENV.get(key) or default


class Config:
    """Konfigurasi gabungan MLLP, API, dan database."""

    def __init__(self, args):
        self.mllp_host = getattr(args, "host", None) or "127.0.0.1"
        self.mllp_port = getattr(args, "port", None) or int(cfg("MLLP_PORT", "2575"))
        self.timeout   = getattr(args, "timeout", None) or 15.0

        api_env = cfg("SIMGOS_API_HOST_PORT", "8080")
        self.api_url = (
            getattr(args, "api_url", None) or f"http://localhost:{api_env}/api/v1"
        ).rstrip("/")
        self.api_key = getattr(args, "api_key", None) or cfg("SIMGOS_API_KEY")

        self.db_host = cfg("SIMGOS_DB_HOST", "127.0.0.1")
        self.db_port = int(cfg("SIMGOS_DB_PORT", "3306"))
        self.db_name = cfg("SIMGOS_DB_NAME")
        self.db_user = cfg("SIMGOS_DB_USER", "root")
        self.db_pass = cfg("SIMGOS_DB_PASS")

        # Sama dengan logika schema_map.php: SIMGOS_DB_NAME kosong = multi-database
        if self.db_name:
            self.tbl_hasil  = "hasil_rad"
            self.tbl_log    = "hl7_logs"
            self.tbl_pasien = "pasien"
        else:
            self.tbl_hasil  = "`layanan`.`hasil_rad`"
            self.tbl_log    = "`ris`.`hl7_logs`"
            self.tbl_pasien = "`master`.`pasien`"

    def summary(self) -> None:
        print(f"  MLLP      : {self.mllp_host}:{self.mllp_port}")
        print(f"  API       : {self.api_url}")
        print(f"  Database  : {self.db_host}:{self.db_port} "
              f"({self.db_name or 'multi-database: layanan/ris/master'})")


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------
def banner(text: str) -> None:
    print()
    print("=" * 68)
    print(f"  {text}")
    print("=" * 68)


def step(status: str, label: str, detail: str = "") -> None:
    print(f"  {status} {label}" + (f"  {detail}" if detail else ""))


def print_rows(cols: list[str], rows: list[list[str]], indent: str = "      ") -> None:
    if not rows:
        print(f"{indent}(tidak ada baris)")
        return
    for row in rows:
        print(f"{indent}---")
        for col, val in zip(cols, row):
            print(f"{indent}{col:<16}: {val[:80]}")


# ---------------------------------------------------------------------------
# Akses database
# ---------------------------------------------------------------------------
def _batchify(value) -> str:
    """Samakan format nilai dengan keluaran `mysql -B` agar pymysql dan Docker identik."""
    if value is None:
        return "NULL"
    if isinstance(value, bytes):
        value = value.decode("utf-8", errors="replace")
    text = str(value)
    return (
        text.replace("\\", "\\\\")
            .replace("\t", "\\t")
            .replace("\n", "\\n")
            .replace("\r", "\\r")
    )


def _pymysql_available() -> bool:
    try:
        import pymysql  # noqa: F401
        return True
    except ImportError:
        return False


def db_query(config: Config, statements: list[str]) -> list[tuple[list[str], list[list[str]]]]:
    """
    Jalankan beberapa SELECT sekaligus, kembalikan blok (kolom, baris) berurutan.

    Satu panggilan = satu koneksi, supaya snapshot sebelum/sesudah tetap murah.
    """
    if _pymysql_available():
        import pymysql

        conn = pymysql.connect(
            host=config.db_host, port=config.db_port,
            user=config.db_user, password=config.db_pass,
            connect_timeout=int(config.timeout), charset="utf8mb4",
        )
        blocks = []
        try:
            with conn.cursor() as cur:
                for sql in statements:
                    cur.execute(sql)
                    cols = [d[0] for d in (cur.description or [])]
                    rows = [[_batchify(v) for v in r] for r in cur.fetchall()]
                    blocks.append((cols, rows))
        finally:
            conn.close()
        return blocks

    if shutil.which("docker") is None:
        raise RuntimeError(
            "Butuh pymysql (`pip install pymysql`) atau Docker untuk akses database."
        )

    # `mysql -B` mencetak header kolom sebelum setiap hasil. Marker diberi
    # alias 'm', jadi baris header 'm' muncul tepat sebelum baris marker dan
    # harus dibuang saat parsing.
    marker = "__BLOCK_SEP__"
    joined = "\n".join(
        f"SELECT '{marker}' AS m;\n{sql.strip().rstrip(';')};\n" for sql in statements
    )
    cmd = [
        "docker", "run", "--rm", "-i",
        "-e", f"H={config.db_host}", "-e", f"P={config.db_port}",
        "-e", f"U={config.db_user}", "-e", f"PW={config.db_pass}",
        "mysql:8.0", "sh", "-c",
        'MYSQL_PWD="$PW" exec mysql -h "$H" -P "$P" -u "$U" '
        '-B --default-character-set=utf8mb4',
    ]
    proc = subprocess.run(cmd, input=joined, capture_output=True, text=True, timeout=120)

    if proc.returncode != 0:
        err = proc.stderr.strip().splitlines()
        raise RuntimeError(err[-1][:300] if err else "query gagal")

    blocks: list[tuple[list[str], list[list[str]]]] = []
    cols: list[str] | None = None
    rows: list[list[str]] = []
    inside = False
    lines = proc.stdout.splitlines()

    for i, line in enumerate(lines):
        if line == marker:
            if inside:
                blocks.append((cols or [], rows))
            cols, rows, inside = None, [], True
            continue
        if not inside:
            continue
        if line == "m" and i + 1 < len(lines) and lines[i + 1] == marker:
            # Header kolom milik SELECT marker, bukan data.
            continue
        parts = line.split("\t")
        if cols is None:
            cols = parts
        else:
            rows.append(parts)

    if inside:
        blocks.append((cols or [], rows))

    return blocks


def fetch_state(config: Config, accession: str) -> dict:
    """
    Ambil keadaan terkini satu accession dari hasil_rad dan hl7_logs.

    Returns: {"hasil_rad": (cols, rows), "hl7_logs": (cols, rows)}
    """
    acc = accession.replace("'", "''")
    sql_hasil = (
        "SELECT ID,TINDAKAN_MEDIS,TANGGAL,KRITIS,DOKTER,OTOMATIS,OLEH,STATUS,"
        "DIKIRIM_DARI,KLINIS,HASIL,KESAN,USUL,BTK "
        f"FROM {config.tbl_hasil} WHERE TINDAKAN_MEDIS='{acc}' ORDER BY ID"
    )
    sql_log = (
        "SELECT ID,REF_ID,JENIS,PROVIDER,KODE,KIRIM,TGL_REQUEST,TGL_RESPONSE "
        f"FROM {config.tbl_log} WHERE REF_ID='{acc}' ORDER BY ID"
    )
    hasil, log = db_query(config, [sql_hasil, sql_log])
    return {"hasil_rad": hasil, "hl7_logs": log}


def snapshot_to_dict(accession: str, state: dict) -> dict:
    return {
        "accession": accession,
        "taken_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "hasil_rad": {"cols": state["hasil_rad"][0], "rows": state["hasil_rad"][1]},
        "hl7_logs":  {"cols": state["hl7_logs"][0],  "rows": state["hl7_logs"][1]},
    }


def state_from_snapshot(data: dict) -> dict:
    return {
        "hasil_rad": (data["hasil_rad"]["cols"], data["hasil_rad"]["rows"]),
        "hl7_logs":  (data["hl7_logs"]["cols"],  data["hl7_logs"]["rows"]),
    }


def save_snapshot(accession: str, state: dict, path: Path | None = None) -> Path:
    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    if path is None:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        path = SNAPSHOT_DIR / f"{accession or 'unknown'}-{stamp}.json"
    path.write_text(
        json.dumps(snapshot_to_dict(accession, state), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return path


def load_snapshot(path: Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def diff_rows(cols: list[str], before: list[list[str]], after: list[list[str]]) -> list[tuple[str, str, str]]:
    """Bandingkan baris pertama sebelum/sesudah. Kembalikan [(kolom, lama, baru)]."""
    if not before or not after:
        return []
    old = dict(zip(cols, before[0]))
    new = dict(zip(cols, after[0]))
    return [
        (col, old.get(col, ""), new.get(col, ""))
        for col in cols
        if old.get(col, "") != new.get(col, "")
    ]


# ---------------------------------------------------------------------------
# Pesan HL7 & pengiriman
# ---------------------------------------------------------------------------
def make_accession() -> str:
    """Accession 11 karakter, sesuai CHAR(11) kolom TINDAKAN_MEDIS."""
    now = datetime.now()
    return f"RAD{now.strftime('%H%M%S')}{now.microsecond // 10000:02d}"


def build_hl7(norm: str, accession: str) -> str:
    """Pesan HL7 ORU^R01 dengan accession dan timestamp yang bisa dilacak."""
    now = datetime.now()
    ts = now.strftime("%Y%m%d%H%M%S")
    ts_short = now.strftime("%Y%m%d%H%M")
    msg_id = f"MSG-{ts}-{os.getpid()}"

    segments = [
        f"MSH|^~\\&|PACS_SIM|RSUP_RAD|SIMGOS_BRIDGE|RSUP|{ts}||ORU^R01|{msg_id}|P|2.5|||NE|AL|IDN|UTF-8",
        f"PID|1||{norm}^^^RSUP^PI||SUHARTO^BAMBANG^WAHONO||19780315|M|||Jl. Sudirman No. 10^^Jakarta^DKI^10110^IDN",
        f"PV1|1|O|RAD^RAD-01^RSUP||||dr. HIDAYAT^MUHAMMAD^^^dr.||||||||||A|KNJ-{ts}",
        f"ORC|RE|ORD-{ts}|{accession}|GRP-001||||||||dr. HIDAYAT^MUHAMMAD",
        f"OBR|1|ORD-{ts}|{accession}|24630-5^CT Scan Thoraks^LN|||{ts_short}00|{ts_short}30|||||KLINIS: Batuk berkepanjangan|||||dr. HIDAYAT^MUHAMMAD||||dr. RADIOLOGI^AGUS",
        "OBX|1|TX|59776-5^KLINIS^LN||Batuk berkepanjangan > 3 minggu. Riwayat kontak TB. Demam subfebris.||||||F",
        "OBX|2|TX|59780-7^HASIL^LN||Tampak infiltrat di lobus kanan atas. Kavitas diameter +/-2cm. Tidak ada efusi pleura.||||||F",
        "OBX|3|TX|19005-8^KESAN^LN||TB Paru aktif dengan kavitas lobus kanan atas.||||||F",
        "OBX|4|TX|18783-1^USUL^LN||Saran pemeriksaan sputum BTA. Konsultasi paru.||||||F",
        f"OBX|5|ST|STUDY_UID^StudyInstanceUID^DICOM||1.2.840.113619.2.55.3.{ts}.123||||||F",
    ]
    return "\r".join(segments) + "\r"


def accession_from_file(path: Path) -> str:
    """Ambil accession dari OBR-3 (fallback OBR-2) pada file .hl7."""
    text = Path(path).read_text(encoding="utf-8", errors="replace")
    for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if line.startswith("OBR"):
            parts = line.split("|")
            for idx in (3, 2):
                if idx < len(parts) and parts[idx].strip():
                    return parts[idx].split("^")[0].strip()[:11]
    return ""


def send_mllp(hl7_text: str, host: str, port: int, timeout: float) -> tuple[bool, str, float]:
    """Kirim pesan via MLLP, tunggu ACK. Returns (ok, ack, durasi_ms)."""
    payload = MLLP_START + hl7_text.encode("utf-8") + MLLP_END
    start = time.monotonic()

    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(timeout)
            sock.connect((host, port))
            sock.sendall(payload)

            ack_raw = b""
            while True:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                ack_raw += chunk
                if b"\x1c" in ack_raw:
                    break
    except socket.timeout:
        return False, f"timeout menunggu ACK setelah {timeout}s", (time.monotonic() - start) * 1000
    except OSError as e:
        return False, f"koneksi gagal: {e}", (time.monotonic() - start) * 1000

    ms = (time.monotonic() - start) * 1000
    ack = (
        ack_raw.replace(MLLP_START, b"")
        .replace(MLLP_END, b"")
        .replace(b"\x1c", b"")
        .decode("utf-8", errors="replace")
        .replace("\r", "\n")
        .strip()
    )
    return ("MSA|AA" in ack), ack, ms


def build_direct_payload(norm: str, accession: str, hasil: str | None = None) -> dict:
    """Payload JSON untuk PUT langsung ke API, tanpa HL7/MLLP."""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    return {
        "norm": norm,
        "nama": "BAMBANG WAHONO SUHARTO",
        "tanggal_lahir": "1978-03-15",
        "jenis_kelamin": "1",
        "accession_number": accession,
        "kode_tindakan": "24630-5",
        "nama_tindakan": "CT Scan Thoraks",
        "tanggal_pemeriksaan": now,
        "dokter_pengirim": "dr. MUHAMMAD HIDAYAT, Sp.PD",
        "klinis": "Batuk berkepanjangan > 3 minggu. Riwayat kontak TB.",
        "hasil": hasil or "Tampak infiltrat di lobus kanan atas. Kavitas diameter +/-2cm.",
        "kesan": "TB Paru aktif dengan kavitas lobus kanan atas.",
        "usul": "Saran sputum BTA dan kultur.",
        "btk": "",
        "kritis": 1,
        "dokter_id": 0,
        "dokter_nama": "dr. AGUS RADIOLOGI, Sp.Rad",
        "oleh": 0,
        "pacs_hook_uid": f"1.2.840.113619.2.55.3.{datetime.now().strftime('%Y%m%d%H%M%S')}",
    }


def send_direct(config: Config, payload: dict, accession: str) -> tuple[bool, str, float]:
    """HTTP PUT langsung ke endpoint PHP."""
    url = f"{config.api_url}/radiologi/pacs-bridging/{accession}"
    headers = {"Content-Type": "application/json", "X-Api-Key": config.api_key}
    start = time.monotonic()

    try:
        resp = requests.put(url, headers=headers, json=payload, timeout=config.timeout)
    except requests.RequestException as e:
        return False, str(e), (time.monotonic() - start) * 1000

    ms = (time.monotonic() - start) * 1000
    try:
        body = resp.json()
    except ValueError:
        return False, f"HTTP {resp.status_code} | {resp.text[:200]}", ms

    ok = resp.status_code == 200 and body.get("success") is True
    return ok, f"HTTP {resp.status_code} | {body.get('message', '')}", ms
