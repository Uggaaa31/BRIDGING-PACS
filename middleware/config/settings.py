"""
settings.py - Konfigurasi Sentral Middleware Bridging PACS-SIMGOS
=================================================================
Ubah nilai-nilai di sini sesuai lingkungan server Anda.
"""

import os
from dotenv import load_dotenv

# Muat variabel dari file .env (prioritas: root project, lalu current directory)
_root_env = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '.env'))
if os.path.exists(_root_env):
    load_dotenv(_root_env)
else:
    load_dotenv()

# ---------------------------------------------------------------------------
# MLLP / TCP LISTENER (sisi PACS → Middleware)
# ---------------------------------------------------------------------------
MLLP_HOST = os.getenv("MLLP_HOST", "0.0.0.0")       # Bind ke semua interface
MLLP_PORT = int(os.getenv("MLLP_PORT", "2575"))       # Port standar HL7 MLLP
MLLP_BUFFER_SIZE = 4096                                # Ukuran buffer TCP (bytes)
MLLP_TIMEOUT_SECONDS = 30                              # Timeout koneksi TCP

# ---------------------------------------------------------------------------
# SIMGOS REST API (sisi Middleware → SIMGOS)
# ---------------------------------------------------------------------------
SIMGOS_API_BASE_URL = os.getenv(
    "SIMGOS_API_BASE_URL",
    "http://localhost/simgos-api/api/v1"               # URL base API SIMGOS
)
SIMGOS_API_ENDPOINT_BRIDGING = "/radiologi/pacs-bridging"  # Endpoint PUT

# Autentikasi — pilih salah satu: Bearer Token atau API Key
# Tidak ada nilai default: kredensial wajib diisi lewat .env / environment.
SIMGOS_AUTH_TYPE = os.getenv("SIMGOS_AUTH_TYPE", "api_key")  # "bearer" | "api_key"
SIMGOS_API_KEY   = os.getenv("SIMGOS_API_KEY",   "")
SIMGOS_API_TOKEN = os.getenv("SIMGOS_API_TOKEN", "")         # Jika pakai Bearer

# Fail-closed: middleware tidak boleh berjalan tanpa kredensial yang valid.
_AUTH_CONFIG_VALID = (
    (SIMGOS_AUTH_TYPE == "bearer" and bool(SIMGOS_API_TOKEN))
    or (SIMGOS_AUTH_TYPE == "api_key" and bool(SIMGOS_API_KEY))
)
if not _AUTH_CONFIG_VALID:
    raise RuntimeError(
        "Konfigurasi autentikasi SIMGOS tidak valid. Set SIMGOS_API_KEY "
        "(atau SIMGOS_API_TOKEN dengan SIMGOS_AUTH_TYPE=bearer) di .env. "
        "Middleware tidak dijalankan tanpa kredensial."
    )

SIMGOS_API_TIMEOUT_SECONDS = int(os.getenv("SIMGOS_API_TIMEOUT", "15"))
SIMGOS_API_MAX_RETRIES     = int(os.getenv("SIMGOS_API_MAX_RETRIES", "3"))
SIMGOS_API_RETRY_DELAY_SEC = 2  # Jeda antar retry

# ---------------------------------------------------------------------------
# STORE-AND-FORWARD (Antrean Lokal / Offline Spooler)
# ---------------------------------------------------------------------------
# Lokasi file SQLite antrean. Default: <middleware>/data/spool.db
# Di Docker, docker-compose menimpanya dengan /app/data/spool.db (volume persisten).
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
SPOOL_DB_PATH = os.getenv("SPOOL_DB_PATH") or os.path.abspath(
    os.path.join(_THIS_DIR, "..", "data", "spool.db")
)

SPOOL_INTERVAL_SECONDS = int(os.getenv("SPOOL_INTERVAL_SECONDS", "30"))  # Interval polling worker
SPOOL_BATCH_SIZE       = int(os.getenv("SPOOL_BATCH_SIZE", "10"))        # Pesan per putaran worker

# Batas percobaan kirim ulang sebelum baris ditandai ABANDONED.
#   0  = tanpa batas (DEFAULT) — kegagalan jaringan/5xx boleh bertahan selama
#        SIMGOS down; data pasien tidak pernah dibuang karena waktu.
#   >0 = batas keras, mis. 20. Ingat: interval × batas = jendela toleransi,
#        20 retry @30 detik hanya ~10 menit — terlalu singkat untuk downtime nyata.
# Error permanen (HTTP 4xx / payload rusak) tetap langsung ABANDONED, tidak
# tergantung nilai ini.
SPOOL_MAX_RETRIES      = int(os.getenv("SPOOL_MAX_RETRIES", "0"))


# ---------------------------------------------------------------------------
# LOGGING
# ---------------------------------------------------------------------------
LOG_LEVEL      = os.getenv("LOG_LEVEL", "INFO")        # DEBUG | INFO | WARNING | ERROR
LOG_DIR        = os.getenv("LOG_DIR", "logs")          # Direktori file log
LOG_FILE_NAME  = "bridging_pacs.log"
LOG_MAX_BYTES  = 10 * 1024 * 1024                      # 10 MB per file log
LOG_BACKUP_COUNT = 5                                    # Jumlah file rotasi

# ---------------------------------------------------------------------------
# KONVERSI DATA — Mapping kode referensi SIMGOS
# ---------------------------------------------------------------------------
# Mapping Jenis Kelamin: kode HL7 → kode referensi SIMGOS
GENDER_MAP = {
    "M": "1",    # Laki-laki
    "F": "2",    # Perempuan
    "O": "0",    # Lain-lain
    "U": "0",    # Unknown
    "A": "0",    # Ambiguous
    "N": "0",    # Not applicable
}
GENDER_DEFAULT = "0"

# Nama rumah sakit (untuk header ACK & log)
HOSPITAL_NAME   = os.getenv("HOSPITAL_NAME", "RSUP")
SENDING_APP     = "PACS-BRIDGE"
RECEIVING_APP   = "SIMGOS"
