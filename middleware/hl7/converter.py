"""
converter.py - Engine Konversi Data HL7 → Format Database SIMGOS
=================================================================
Mentransformasi nilai-nilai segmen HL7 menjadi tipe data yang kompatibel
dengan skema tabel MySQL/MariaDB SIMGOS.

Mapping utama:
  PID-3  → NORM (Patient ID, hapus leading zero)
  PID-5  → NAMA (konversi format nama HL7 FAMILY^GIVEN → GIVEN FAMILY)
  PID-7  → TANGGAL_LAHIR (YYYYMMDD → YYYY-MM-DD) + hitung UMUR
  PID-8  → JENIS_KELAMIN (M/F → 1/2/0)
  OBR-3  → ACCESSION_NUMBER (ID tindakan unik)
  OBR-7  → TANGGAL_PEMERIKSAAN (YYYYMMDDHHMMSS → YYYY-MM-DD HH:MM:SS)
  OBX    → KLINIS, HASIL, KESAN, USUL, PACS_HOOK_UID
"""

from __future__ import annotations
import re
from datetime import datetime, date
from typing import Any, Optional

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from config.settings import GENDER_MAP, GENDER_DEFAULT
from hl7.parser import HL7Message, HL7Segment
from utils.logger import get_logger

log = get_logger("converter")


# ---------------------------------------------------------------------------
# Tipe data hasil konversi
# ---------------------------------------------------------------------------
class SimgosPayload:
    """
    Kontainer terstruktur untuk payload yang akan dikirim ke API SIMGOS.
    Semua field adalah Optional — hanya yang berhasil di-parse yang terisi.
    """
    def __init__(self):
        # === Data Pasien (master.pasien) ===
        self.norm: Optional[str]              = None   # Patient ID SIMGOS
        self.nama: Optional[str]              = None   # Nama lengkap
        self.tanggal_lahir: Optional[str]     = None   # YYYY-MM-DD
        self.jenis_kelamin: Optional[str]     = None   # '1'|'2'|'0'
        self.umur_tahun: Optional[int]        = None
        self.umur_bulan: Optional[int]        = None
        self.umur_hari: Optional[int]         = None

        # === Data Kunjungan / Order (layanan.tindakan_medis) ===
        self.accession_number: Optional[str]  = None   # OBR-3 / ORC-3 — max 11 char (CHAR(11))
        self.kunjungan_id: Optional[str]      = None   # PV1-19 / PV1-5
        self.tanggal_pemeriksaan: Optional[str] = None  # YYYY-MM-DD HH:MM:SS
        self.kode_tindakan: Optional[str]     = None   # OBR-4 kode prosedur — max 10 char
        self.nama_tindakan: Optional[str]     = None   # OBR-4 nama prosedur
        self.dokter_pengirim: Optional[str]   = None   # OBR-16 (nama, untuk referensi)

        # === Hasil Radiologi (layanan.hasil_rad) ===
        # Sesuai DDL asli hasil_rad.sql:
        self.klinis: Optional[str]            = None   # KLINIS text
        self.hasil: Optional[str]             = None   # HASIL longtext
        self.kesan: Optional[str]             = None   # KESAN text
        self.usul: Optional[str]              = None   # USUL text
        self.btk: Optional[str]              = None    # BTK text (Bacaan Tambahan/Keterangan)
        self.kritis: int                      = 1      # KRITIS tinyint: 0=Kritis, 1=Tidak Kritis
        self.dokter_id: int                   = 0      # DOKTER smallint — ID dokter pembaca di DB
        self.dokter_nama: Optional[str]       = None   # Nama dokter dari HL7 (untuk referensi log)
        self.otomatis: int                    = 1      # OTOMATIS=1: diisi via bridging
        self.oleh: int                        = 0      # OLEH smallint — ID user (0=system)
        # pacs_hook_uid → kolom DIKIRIM_DARI char(36) = StudyInstanceUID PACS
        self.pacs_hook_uid: Optional[str]     = None   # max 36 char (CHAR(36))

        # === Metadata ===
        self.message_control_id: Optional[str] = None
        self.message_type: Optional[str]       = None
        self.converted_at: str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    def to_dict(self) -> dict:
        """Konversi ke dict untuk serialisasi JSON."""
        return {k: v for k, v in self.__dict__.items() if v is not None}

    def __repr__(self) -> str:
        return f"SimgosPayload(norm={self.norm!r}, accession={self.accession_number!r})"


# ---------------------------------------------------------------------------
# Fungsi-fungsi konversi individual
# ---------------------------------------------------------------------------

def convert_norm(pid3_raw: str) -> Optional[str]:
    """
    Konversi PID-3 (Patient Identifier List) ke NORM SIMGOS.

    Format HL7 PID-3: ID^^^AssigningAuthority^IDType
    Strategi:
      1. Ambil komponen pertama (sebelum '^').
      2. Hapus leading zeros jika seluruh nilai adalah angka.
      3. Bersihkan karakter non-alphanumeric (kecuali strip).
    """
    if not pid3_raw:
        return None

    # Ambil komponen pertama (ID-nya saja)
    raw_id = pid3_raw.split("^")[0].strip()
    if not raw_id:
        return None

    # Hapus leading zero jika murni angka
    if raw_id.isdigit():
        norm = str(int(raw_id))  # "00123" → "123"
    else:
        # Alphanumeric — bersihkan tapi pertahankan format
        norm = re.sub(r'[^\w\-]', '', raw_id).upper()

    log.debug(f"NORM konversi: '{pid3_raw}' → '{norm}'")
    return norm or None


def convert_nama(pid5_raw: str) -> Optional[str]:
    """
    Konversi PID-5 (Patient Name) dari format HL7 ke nama lengkap.

    Format HL7: FAMILY_NAME^GIVEN_NAME^MIDDLE^SUFFIX^PREFIX
    Output    : "GIVEN_NAME MIDDLE FAMILY_NAME" (disesuaikan konvensi Indonesia)

    Contoh:
      "SUHARTO^BAMBANG^WAHONO" → "BAMBANG WAHONO SUHARTO"
      "SUTRISNO^" → "SUTRISNO"
    """
    if not pid5_raw:
        return None

    parts = pid5_raw.split("^")
    family = parts[0].strip() if len(parts) > 0 else ""
    given  = parts[1].strip() if len(parts) > 1 else ""
    middle = parts[2].strip() if len(parts) > 2 else ""

    # Susun nama: Given Middle Family (konvensi Indonesia)
    components = [c for c in [given, middle, family] if c]
    full_name = " ".join(components).upper()

    # Bersihkan spasi berlebih
    full_name = re.sub(r'\s+', ' ', full_name).strip()
    log.debug(f"Nama konversi: '{pid5_raw}' → '{full_name}'")
    return full_name or None


def convert_tanggal_lahir(pid7_raw: str) -> tuple[Optional[str], Optional[date]]:
    """
    Konversi PID-7 (Date of Birth) dari format HL7 ke SQL DATE.

    Input format : 'YYYYMMDD' atau 'YYYYMMDDHHMMSS'
    Output format: 'YYYY-MM-DD'
    Returns      : (formatted_string, date_object) — tuple untuk kalkulasi umur.
    """
    if not pid7_raw:
        return None, None

    # Ambil hanya 8 karakter pertama (YYYYMMDD)
    raw_date = re.sub(r'[^\d]', '', pid7_raw)[:8]
    if len(raw_date) < 8:
        log.warning(f"Format tanggal lahir tidak valid: '{pid7_raw}'")
        return None, None

    try:
        dob = datetime.strptime(raw_date, "%Y%m%d").date()
        formatted = dob.strftime("%Y-%m-%d")
        return formatted, dob
    except ValueError as e:
        log.warning(f"Gagal parse tanggal lahir '{pid7_raw}': {e}")
        return None, None


def hitung_umur(
    tanggal_lahir: Optional[date],
    tanggal_acuan: Optional[date] = None
) -> tuple[int, int, int]:
    """
    Menghitung umur dalam (tahun, bulan, hari) berdasarkan tanggal lahir.

    Args:
        tanggal_lahir  : Objek date tanggal lahir pasien.
        tanggal_acuan  : Tanggal pemeriksaan atau hari ini jika None.

    Returns:
        Tuple (tahun, bulan, hari)
    """
    if tanggal_lahir is None:
        return 0, 0, 0

    acuan = tanggal_acuan or date.today()

    try:
        tahun = acuan.year - tanggal_lahir.year
        bulan = acuan.month - tanggal_lahir.month
        hari  = acuan.day - tanggal_lahir.day

        if hari < 0:
            # Pinjam bulan
            from calendar import monthrange
            prev_month = acuan.month - 1 or 12
            prev_year  = acuan.year if acuan.month > 1 else acuan.year - 1
            hari += monthrange(prev_year, prev_month)[1]
            bulan -= 1

        if bulan < 0:
            bulan += 12
            tahun -= 1

        return max(tahun, 0), max(bulan, 0), max(hari, 0)
    except Exception as e:
        log.warning(f"Gagal hitung umur: {e}")
        return 0, 0, 0


def convert_jenis_kelamin(pid8_raw: str) -> str:
    """
    Konversi PID-8 (Administrative Sex) ke kode referensi SIMGOS.

    HL7 → SIMGOS:
      'M'         → '1' (Laki-laki)
      'F'         → '2' (Perempuan)
      O/U/A/N/'' → '0' (Tidak diketahui/lainnya)
    """
    code = (pid8_raw or "").strip().upper()
    result = GENDER_MAP.get(code, GENDER_DEFAULT)
    log.debug(f"Jenis kelamin: '{pid8_raw}' → '{result}'")
    return result


def convert_datetime_hl7(raw: str) -> Optional[str]:
    """
    Konversi datetime HL7 (YYYYMMDDHHMMSS atau YYYYMMDDHHMM) ke SQL DATETIME.

    Output: 'YYYY-MM-DD HH:MM:SS'
    """
    if not raw:
        return None

    digits = re.sub(r'[^\d]', '', raw)

    formats = [
        ("%Y%m%d%H%M%S", 14),
        ("%Y%m%d%H%M",   12),
        ("%Y%m%d",        8),
    ]

    for fmt, length in formats:
        if len(digits) >= length:
            try:
                dt = datetime.strptime(digits[:length], fmt)
                return dt.strftime("%Y-%m-%d %H:%M:%S")
            except ValueError:
                continue

    log.warning(f"Gagal parse datetime HL7: '{raw}'")
    return None


def convert_date_only_hl7(raw: str) -> Optional[str]:
    """
    Konversi date HL7 (YYYYMMDD) ke SQL DATE (YYYY-MM-DD).
    """
    if not raw:
        return None
    digits = re.sub(r'[^\d]', '', raw)[:8]
    if len(digits) < 8:
        return None
    try:
        return datetime.strptime(digits, "%Y%m%d").strftime("%Y-%m-%d")
    except ValueError:
        return None


def extract_obx_fields(obx_list: list[HL7Segment]) -> dict[str, str]:
    """
    Mengekstrak data dari semua segmen OBX.

    Strategi identifikasi berdasarkan OBX-3 (Observation Identifier):
      - Kode yang mengandung 'CLIN'  / 'INDIKASI'  → klinis
      - Kode yang mengandung 'HASIL' / 'DESC'       → hasil/bacaan
      - Kode yang mengandung 'KESAN' / 'IMP'        → kesan/kesimpulan
      - Kode yang mengandung 'USUL'  / 'REC'        → usul/rekomendasi
      - Kode yang mengandung 'UID'   / 'STUDY' / 'URL' / 'IMAGE' → pacs hook
      - Kode yang mengandung 'MOD'                  → modality

    Fallback: OBX-1 (Set ID, urutan) untuk menentukan posisi.
    """
    result = {
        "klinis":       "",
        "hasil":        "",
        "kesan":        "",
        "usul":         "",
        "pacs_hook_uid": "",
        "pacs_modality": "",
    }

    def _get_obx_value(seg: HL7Segment) -> str:
        """Gabungkan semua repetisi OBX-5 menjadi satu string."""
        raw = seg.field(5).value
        # Ganti karakter escape HL7 \.br\ dengan newline
        raw = raw.replace("\\n", "\n").replace("\\.br\\", "\n")
        return raw.strip()

    for i, obx in enumerate(obx_list):
        obs_id_raw = obx.field(3).value.upper()         # OBX-3: identififer
        value_type = obx.field(2).value.upper()         # OBX-2: value type (TX, ST, CE)
        obs_value  = _get_obx_value(obx)

        # === Match berdasarkan keyword di OBX-3 ===
        if any(k in obs_id_raw for k in ["CLIN", "INDIKASI", "KLINIS"]):
            result["klinis"] += obs_value + " "
        elif any(k in obs_id_raw for k in ["STUDY", "UID", "IMAGE", "URL", "HOOK", "DICOM"]):
            result["pacs_hook_uid"] = obs_value
        elif any(k in obs_id_raw for k in ["KESAN", "IMP", "CONCL", "KESIMPULAN"]):
            result["kesan"] += obs_value + " "
        elif any(k in obs_id_raw for k in ["USUL", "REC", "SARAN", "RECOM"]):
            result["usul"] += obs_value + " "
        elif any(k in obs_id_raw for k in ["HASIL", "DESC", "DESKRIPSI", "RESULT", "FIND"]):
            result["hasil"] += obs_value + " "
        elif any(k in obs_id_raw for k in ["MOD", "MODALITY"]):
            result["pacs_modality"] = obs_value
        else:
            # Fallback berbasis urutan index OBX (jika tidak ada kode identifier jelas)
            if i == 0 and not result["klinis"]:
                result["klinis"] = obs_value
            elif i == 1 and not result["hasil"]:
                result["hasil"] = obs_value
            elif i == 2 and not result["kesan"]:
                result["kesan"] = obs_value
            elif i == 3 and not result["usul"]:
                result["usul"] = obs_value

    # Bersihkan trailing spaces
    return {k: v.strip() for k, v in result.items()}


# ---------------------------------------------------------------------------
# Engine Konversi Utama
# ---------------------------------------------------------------------------

def convert_hl7_to_simgos(msg: HL7Message) -> SimgosPayload:
    """
    Engine konversi utama: mengambil semua data dari segmen HL7
    dan menghasilkan objek SimgosPayload yang siap dikirim ke API.

    Args:
        msg: Objek HL7Message hasil parsing.

    Returns:
        SimgosPayload yang sudah terisi.
    """
    payload = SimgosPayload()

    # ── Metadata pesan ───────────────────────────────────────────────────────
    payload.message_control_id = msg.message_control_id()
    payload.message_type = msg.message_type()
    log.info(f"Mulai konversi | MsgType={payload.message_type} | MsgID={payload.message_control_id}")

    pid = msg.PID
    obr = msg.OBR
    orc = msg.ORC
    pv1 = msg.PV1
    obx_list = msg.OBX_list

    # ── PID: Data Pasien ─────────────────────────────────────────────────────
    if pid:
        # PID-3: Patient Identifier (NORM)
        payload.norm = convert_norm(pid.field(3).value)

        # PID-5: Patient Name
        payload.nama = convert_nama(pid.field(5).value)

        # PID-7: Date of Birth
        payload.tanggal_lahir, dob_obj = convert_tanggal_lahir(pid.field(7).value)

        # PID-8: Administrative Sex
        payload.jenis_kelamin = convert_jenis_kelamin(pid.field(8).value)

        log.debug(f"PID | NORM={payload.norm} | NAMA={payload.nama} | "
                  f"TGL_LAHIR={payload.tanggal_lahir} | JK={payload.jenis_kelamin}")
    else:
        log.warning("Segmen PID tidak ditemukan dalam pesan HL7.")
        dob_obj = None

    # ── OBR: Data Order/Pemeriksaan ──────────────────────────────────────────
    tanggal_pemeriksaan_obj: Optional[date] = None
    if obr:
        # OBR-3: Filler Order Number (Accession Number) — char(11) di TINDAKAN_MEDIS
        acc_raw = obr.field(3).value
        if not acc_raw and orc:
            acc_raw = orc.field(3).value  # Fallback ke ORC-3
        acc_clean = acc_raw.split("^")[0].strip() if acc_raw else None
        # Batasi 11 karakter sesuai CHAR(11) di kolom TINDAKAN_MEDIS
        payload.accession_number = acc_clean[:11] if acc_clean else None

        # OBR-4: Universal Service ID (kode & nama tindakan)
        payload.kode_tindakan  = obr.field(4)[0][:10]  # max 10 char (CHAR(10))
        payload.nama_tindakan  = obr.field(4)[1]        # nama prosedur

        # OBR-7: Observation Date/Time
        tgl_raw = obr.field(7).value
        payload.tanggal_pemeriksaan = convert_datetime_hl7(tgl_raw)
        if tgl_raw:
            digits = re.sub(r'[^\d]', '', tgl_raw)[:8]
            try:
                tanggal_pemeriksaan_obj = datetime.strptime(digits, "%Y%m%d").date()
            except ValueError:
                pass

        # OBR-16: Ordering Provider (dokter pengirim — nama string, untuk referensi)
        payload.dokter_pengirim = obr.field(16)[1]  # Given name

        # OBR-32: Principal Result Interpreter (dokter pembaca — nama string)
        # Disimpan di dokter_nama (referensi). Kolom DOKTER di DB adalah integer ID.
        raw_pembaca = obr.field(32)[0] or obr.field(32)[1]
        payload.dokter_nama = raw_pembaca or None

        log.debug(f"OBR | Accession={payload.accession_number} | "
                  f"Tindakan={payload.kode_tindakan}/{payload.nama_tindakan} | "
                  f"TglPeriksa={payload.tanggal_pemeriksaan} | DokterNama={payload.dokter_nama}")
    else:
        log.warning("Segmen OBR tidak ditemukan.")

    # ── Hitung Umur ─────────────────────────────────────────────────────────
    if dob_obj:
        ref_date = tanggal_pemeriksaan_obj or date.today()
        tahun, bulan, hari = hitung_umur(dob_obj, ref_date)
        payload.umur_tahun = tahun
        payload.umur_bulan = bulan
        payload.umur_hari  = hari
        log.debug(f"Umur: {tahun}T {bulan}B {hari}H")

    # ── PV1: Data Kunjungan ──────────────────────────────────────────────────
    if pv1:
        # PV1-19: Visit Number (ID Kunjungan)
        payload.kunjungan_id = pv1.field(19).value.split("^")[0].strip() or None
        if not payload.kunjungan_id:
            # Fallback ke PV1-5 (Preadmit Number)
            payload.kunjungan_id = pv1.field(5).value.split("^")[0].strip() or None

    # ── OBX: Hasil Radiologi ─────────────────────────────────────────────────
    if obx_list:
        obx_data = extract_obx_fields(obx_list)
        payload.klinis        = obx_data["klinis"] or None
        payload.hasil         = obx_data["hasil"] or None
        payload.kesan         = obx_data["kesan"] or None
        payload.usul          = obx_data["usul"] or None
        payload.btk           = obx_data.get("btk") or None   # Bacaan Tambahan/Keterangan
        # pacs_hook_uid → disimpan ke DIKIRIM_DARI char(36) di hasil_rad
        uid_raw = obx_data["pacs_hook_uid"] or None
        payload.pacs_hook_uid = uid_raw[:36] if uid_raw else None  # Batasi 36 char
        log.debug(f"OBX | StudyUID={payload.pacs_hook_uid} | Klinis={str(payload.klinis)[:50]}")
    else:
        log.warning("Tidak ada segmen OBX dalam pesan HL7.")

    log.info(f"Konversi selesai → {payload}")
    return payload
