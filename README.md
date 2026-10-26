# Bridging PACS - SIMGOS (HL7 v2 Interoperability Middleware)

Sistem **Middleware Interoperabilitas Medis** untuk menghubungkan perangkat Radiologi / Picture Archiving and Communication System (**PACS**) dengan Sistem Informasi Manajemen Rumah Sakit Generic Open Source (**SIMGOS** berbasis PHP & MySQL/MariaDB).

Middleware ini menerima pesan standar medis **HL7 v2 (`ORU^R01`)** melalui protokol **MLLP (Minimal Lower Layer Protocol)** melalui jaringan TCP/IP, mengekstrak data pasien, pemeriksaan, serta ekspertise radiologi, lalu menyinkronkannya ke database SIMGOS melalui **REST API (HTTP PUT)** dengan audit trail lengkap.

---

## 🏗️ Arsitektur Sistem

```text
+-----------------------+
|  Alat Radiologi /     |
|  PACS Workstation     |
+-----------+-----------+
            |
            | HL7 v2 (ORU^R01) via MLLP TCP/IP (Port: 2575)
            v
+-------------------------------------------------------------+
|               PYTHON BRIDGING MIDDLEWARE                    |
|                                                             |
|  1. MLLP Server (ThreadingTCPServer)                        |
|  2. HL7 Parser (MSH, PID, PV1, OBR, OBX)                    |
|  3. Converter Engine (Mapping tipe data & schema sanitasi)  |
|  4. HTTP Client (PUT with API Key / Bearer & Auto-Retry)    |
|  5. ACK Generator (AA / AE / AR response ke PACS)           |
+-----------------------------+-------------------------------+
                              |
                              | HTTP PUT JSON Payload
                              v
+-------------------------------------------------------------+
|                 SIMGOS REST API (PHP PDO)                   |
|                                                             |
|  Endpoint : /simgos-api/api/pacs_bridging.php              |
|  Security : X-API-KEY / Bearer Token Header                 |
|  Config   : schema_map.php (Single Source of Truth)         |
+-----------------------------+-------------------------------+
                              |
                              | MySQL PDO Transaction
                              v
+-------------------------------------------------------------+
|                 DATABASE SIMGOS (MySQL / MariaDB)           |
|                                                             |
|  1. ris.hl7_logs         -> Audit trail request & response  |
|  2. layanan.hasil_rad    -> Hasil bacaan/ekspertise dokter  |
|  3. master.pasien        -> Data demografi pasien (opsional)|
|  4. layanan.tindakan_medis -> Data billing/tindakan (ops)   |
+-------------------------------------------------------------+
```

---

## 📂 Struktur Direktori

```text
BRIDGING-PACS/
├── .gitignore
├── README.md
├── hasil_rad.sql                  # DDL struktur tabel layanan.hasil_rad
├── hl7_logs.sql                   # DDL struktur tabel ris.hl7_logs
├── middleware/                    # [Python Engine] MLLP Listener & HL7 Converter
│   ├── .env.example               # Template environment variable middleware
│   ├── requirements.txt           # Dependensi Python (requests, python-dotenv)
│   ├── main.py                    # Entry point MLLP listener / standalone runner
│   ├── config/
│   │   ├── __init__.py
│   │   └── settings.py            # Konfigurasi port, target API, timeouts
│   ├── hl7/
│   │   ├── __init__.py
│   │   ├── parser.py              # Parser HL7 v2 & MLLP framer/unframer
│   │   ├── converter.py           # Konversi segmen HL7 -> Schema SIMGOS
│   │   └── ack_builder.py         # Generator HL7 ACK (AA, AE, AR)
│   ├── listener/
│   │   ├── __init__.py
│   │   └── mllp_server.py         # Multi-threaded TCP socket server MLLP
│   ├── client/
│   │   ├── __init__.py
│   │   └── simgos_api.py          # HTTP Client PUT ke endpoint PHP SIMGOS
│   └── utils/
│       ├── __init__.py
│       └── logger.py              # Rotating file logger
│
├── simgos-api/                    # [PHP API Engine] REST Endpoint SIMGOS
│   ├── .htaccess                  # Apache rewrite rules & security headers
│   ├── api/
│   │   └── pacs_bridging.php      # Endpoint penerima PUT dari middleware
│   ├── config/
│   │   ├── database.php           # Koneksi PDO singleton ke MySQL
│   │   └── schema_map.php         # Pemetaan tabel & kolom database SIMGOS
│   └── models/
│       ├── HasilRadModel.php      # Upsert hasil radiologi ke layanan.hasil_rad
│       ├── Hl7LogModel.php        # Audit log ke ris.hl7_logs
│       ├── PasienModel.php        # Model tabel master.pasien
│       └── TindakanModel.php      # Model tabel layanan.tindakan_medis
│
└── testing/                       # [Testing & Simulasi]
    ├── sample_oru_message.hl7     # Contoh data riil HL7 ORU^R01 dari PACS
    ├── sample_oru_sender.py       # Simulator pengirim HL7 MLLP ke middleware
    └── test_api_put.py            # Script pengujian langsung ke PHP API
```

---

## 📋 Pemetaan Data HL7 ke Database SIMGOS

### 1. Tabel `layanan.hasil_rad`
| Kolom Database | Tipe Data | Sumber Pesan HL7 | Keterangan |
|---|---|---|---|
| `ID` | `int AUTO_INCREMENT` | Internal DB | Primary Key |
| `TINDAKAN_MEDIS` | `char(11)` | `OBR-18` / `ORC-2` | Nomor Accession / ID Pemeriksaan (**UNIQUE KEY**) |
| `TANGGAL` | `datetime` | `OBR-7` / `MSH-7` | Waktu observasi / transaksi pemeriksaan |
| `KLINIS` | `text` | `OBR-13` / `OBX[Observation=CLINICAL]` | Indikasi klinis pasien |
| `KESAN` | `text` | `OBX[Observation=IMPRESSION]` | Kesimpulan / Kesan radiologi |
| `USUL` | `text` | `OBX[Observation=SUGGESTION]` | Rekomendasi/usulan tindakan lanjut |
| `HASIL` | `longtext` | `OBX[Observation=FINDINGS]` | Uraian lengkap hasil pemeriksaan radiologi |
| `BTK` | `text` | `OBX[Observation=BTK]` | Catatan BTK (Bila Tidak Kritis / keterangan lain) |
| `KRITIS` | `tinyint` | `OBX-8` / Flag | `0` = Kritis, `1` = Tidak Kritis |
| `DOKTER` | `smallint` | Mapping Dokter | ID Dokter Pembaca Ekspertise (Default: `0`) |
| `OTOMATIS` | `tinyint` | Fixed: `1` | Penanda data masuk secara otomatis via Bridging |
| `DIKIRIM_DARI` | `char(36)` | `OBR-20` / Hook PACS | StudyInstanceUID / UID Hook gambar PACS |
| `OLEH` | `smallint` | Fixed: `0` / System ID | User ID pelaksana sistem bridging |
| `STATUS` | `tinyint` | Fixed: `1` | `1` = Aktif |

### 2. Tabel `ris.hl7_logs`
| Kolom Database | Tipe Data | Keterangan |
|---|---|---|
| `ID` | `int AUTO_INCREMENT` | Primary Key |
| `REF_ID` | `char(11)` | Accession Number (`TINDAKAN_MEDIS`) |
| `JENIS` | `tinyint` | `0` = ORM (Order), `1` = ORU (Hasil Observasi) |
| `PROVIDER` | `varchar(50)` | Identitas pengirim (misal: `PACS_RADIOLOGI`) |
| `KODE` | `char(10)` | Kode prosedur pemeriksaan (`OBR-4.1`) |
| `TGL_REQUEST` | `datetime` | Waktu pesan diterima dari PACS |
| `REQUEST` | `text` | Mentah pesan HL7 v2 asli |
| `TGL_RESPONSE` | `datetime` | Waktu respons dikembalikan |
| `RESPONSE` | `text` | Respons JSON / pesan ACK |
| `KIRIM` | `tinyint` | `0` = Gagal/Pending, `1` = Berhasil Disimpan |
| `STATUS` | `tinyint` | `1` = Aktif |

---

## 🐳 Deployment Cepat Menggunakan Docker (Rekomendasi Produksi)

Seluruh sistem (Python MLLP Middleware + PHP Apache API) dapat dijalankan secara terisolasi dan otomatis dengan **Docker & Docker Compose**:

```bash
# 1. Salin template environment produksi
copy .env.example .env

# 2. Sesuaikan konfigurasi IP Database SIMGOS RS di .env:
#    SIMGOS_DB_HOST=10.10.160.99
#    SIMGOS_DB_PASS=rahasia_database

# 3. Jalankan seluruh layanan di latar belakang
docker compose up -d --build

# 4. Cek status kontainer dan log
docker compose ps
docker compose logs -f pacs-middleware
```

Layanan otomatis aktif:
- **MLLP Listener**: `0.0.0.0:2575` (siap menerima data HL7 dari PACS)
- **SIMGOS REST API**: `http://localhost:8080/api/v1`

---

## 🚀 Panduan Instalasi Manual (Tanpa Docker)

### Prasyarat
- **Python**: 3.8+ (disertai `pip`)
- **PHP**: 7.4 / 8.0+ dengan ekstensi `pdo_mysql`
- **Web Server**: Apache (Laragon / XAMPP / Native)
- **Database**: MySQL 5.7+ / 8.0+ atau MariaDB 10.3+

---

### Langkah 1: Setup Database
Import file DDL ke instance MySQL Anda:
```bash
mysql -u root -p layanan < hasil_rad.sql
mysql -u root -p ris < hl7_logs.sql
```

---

### Langkah 2: Setup SIMGOS REST API (PHP)
1. Salin atau arahkan folder `simgos-api` ke DocumentRoot web server Anda (contoh Laragon: `C:\laragon\www\simgos-api`).
2. Sesuaikan konfigurasi database pada [simgos-api/config/database.php](file:///d:/RSUP%20IT/BRIDGING%20PACS-SIMGOS/simgos-api/config/database.php):
   ```php
   $host = '127.0.0.1'; // atau IP server database Anda
   $user = 'root';
   $pass = 'password';
   ```
3. Pastikan modul `mod_rewrite` Apache aktif dan file [.htaccess](file:///d:/RSUP%20IT/BRIDGING%20PACS-SIMGOS/simgos-api/.htaccess) terbaca.

---

### Langkah 3: Setup Middleware (Python)
1. Buka terminal pada folder `middleware`:
   ```bash
   cd middleware
   ```
2. Buat file `.env` dari template:
   ```bash
   copy .env.example .env
   ```
3. Sesuaikan konfigurasi pada `.env`:
   ```ini
   # Konfigurasi MLLP Listener (Penerima HL7 dari PACS)
   MLLP_HOST=0.0.0.0
   MLLP_PORT=2575

   # Konfigurasi Target SIMGOS API
   SIMGOS_API_BASE_URL=http://localhost/simgos-api/api/pacs_bridging.php
   SIMGOS_API_KEY=RSUP-IT-SECRET-KEY-2024
   SIMGOS_TIMEOUT_SECONDS=10
   SIMGOS_RETRY_COUNT=3
   ```
4. Install dependensi Python:
   ```bash
   pip install -r requirements.txt
   ```

---

### Langkah 4: Menjalankan Sistem
Jalankan middleware listener:
```bash
python main.py
```
Output terminal akan menampilkan:
```text
======================================================================
  PACS -> SIMGOS HL7 BRIDGING MIDDLEWARE
======================================================================
  MLLP Listener : 0.0.0.0:2575
  SIMGOS API    : http://localhost/simgos-api/api/pacs_bridging.php
  Log Level     : INFO
======================================================================
[INFO] MLLP Server started on 0.0.0.0:2575. Waiting for PACS messages...
```

---

## 🧪 Pengujian & Simulasi

Tersedia skrip pengujian untuk memverifikasi seluruh alur tanpa perlu terhubung ke alat fisik PACS:

### 1. Test Endpoint PHP API Langsung
Untuk memverifikasi koneksi database dan endpoint PUT PHP:
```bash
python testing/test_api_put.py
```

### 2. Test Pengiriman HL7 via MLLP Socket
Buka terminal baru saat `python main.py` sedang berjalan, lalu jalankan simulator pengirim PACS:
```bash
python testing/sample_oru_sender.py
```
Simulator akan:
1. Mengirim pesan `sample_oru_message.hl7` yang di-wrap dalam frame MLLP (`\x0b...\x1c\x0d`).
2. Menerima balasan HL7 **ACK (`MSA|AA|...`)** dari middleware.

---

## 🛡️ Idempotensi & Keamanan
- **Idempotensi Database**: Semua operasi penyimpanan menggunakan `INSERT ... ON DUPLICATE KEY UPDATE` berdasarkan unique constraint `TINDAKAN_MEDIS`. Pengiriman ulang pesan yang sama dari PACS tidak akan menduplikasi baris database, melainkan memperbarui record.
- **Audit Trail Terisolasi**: Setiap payload HL7 mentah dicatat ke tabel `ris.hl7_logs` sebelum disimpan ke tabel utama, memudahkan audit jika terjadi inkonsistensi data klinis.
- **API Authentication**: Endpoint API dilindungi oleh validasi `X-API-KEY` atau Bearer Token.

---

## 📄 Lisensi
Hak Cipta © 2026 RSUP IT. Dikembangkan untuk integrasi sistem rekam medis dan modalitas radiologi.
