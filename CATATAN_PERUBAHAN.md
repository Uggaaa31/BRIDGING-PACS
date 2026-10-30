# Catatan Perubahan & Panduan Handover Proyek Bridging PACS - SIMGOS

Dokumen ini adalah **rujukan serah terima (handover)** untuk rekan tim dan senior
developer: apa yang berubah, mengapa, bagaimana mengoperasikan, dan apa yang masih
menjadi batasan.

Cara membaca:

| Peran | Baca bagian |
|---|---|
| Operator / helpdesk | §7 Operasional Harian, §8 Troubleshooting, §9 Keamanan Antrean |
| Developer baru | §2 Arsitektur, §3 Peta File, §4 Konfigurasi, §5 & §6 Riwayat Pembaruan |
| Senior / reviewer | §5 Riwayat Pembaruan, §10 Pemeliharaan, §12 Batasan & Utang Teknis |

**Daftar isi**

1. Status dokumen
2. Arsitektur & alur data
3. Peta file proyek
4. Referensi konfigurasi (environment)
5. Riwayat pembaruan — 2026-10-31: Store-and-Forward (antrean lokal)
6. Riwayat pembaruan — 2026-10-30: Security & Operational Hardening
7. Operasional harian
8. Troubleshooting
9. Keamanan & privasi antrean lokal
10. Pemeliharaan & prosedur pemulihan
11. Pengujian (testing)
12. Batasan & utang teknis yang diketahui
13. Glosarium

---

## 1. 📌 Status Dokumen

| Item | Nilai |
|---|---|
| Status working copy | Perubahan store-and-forward **belum di-commit** (branch `main`) |
| Commit terakhir di repo | `9810fc1` — 2026-10-30 — *security: isolate api logs outside webroot…* |
| Lingkungan produksi | Docker Compose (`pacs-middleware` + `simgos-api`) |
| Port | MLLP `2575`; HTTP SIMGOS API dari host `8080` |
| Pintu masuk data | HL7 v2 `ORU^R01` via MLLP TCP |

---

## 2. 🏗️ Arsitektur & Alur Data

Ringkasan jalur (versi bertahap ada di `Konsep.md`, diagram besar di `README.md`):

```text
PACS / alat radiologi
   |  HL7 v2 ORU^R01 dibungkus MLLP (0x0B ... 0x1C 0x0D), TCP 2575
   v
[pacs-middleware]  listener/mllp_server.py
   |  1. unframe MLLP  -> teks HL7
   |  2. parse         -> HL7Message        (hl7/parser.py)
   |  3. konversi      -> SimgosPayload     (hl7/converter.py)
   |  4. HTTP PUT JSON -> API SIMGOS        (client/simgos_api.py)
   |
   |-- 200/201 + body success=true ---------> ACK MSA|AA ke PACS  (selesai)
   |
   |-- jaringan/5xx (retryable) ---> spooler/queue_manager.py  (SQLite message_spool)
   |                                 +--> ACK MSA|AA + "queued for background
   |                                      synchronization"  -> PACS anggap aman
   |
   +-- 4xx / validasi gagal ----------------> ACK MSA|AE ke PACS (ditolak, tidak diantrekan)

spooler/spool_worker.py (thread daemon, interval SPOOL_INTERVAL_SECONDS)
   +-- setiap putaran: ambil batch PENDING -> PUT ulang ke API SIMGOS
         |-- sukses        -> status SENT
         |-- jaringan/5xx  -> retry_count + 1 (dicoba lagi putaran berikut)
         +-- 4xx/rusak     -> status ABANDONED (tidak diretry)

API SIMGOS (PHP): api/pacs_bridging.php -> models/HasilRadModel.php
   +-- MySQL/MariaDB: layanan.hasil_rad (upsert by TINDAKAN_MEDIS), ris.hl7_logs
```

Kunci desain: **Python tidak pernah menyentuh MySQL langsung** — semua penulisan
lewat PHP/PDO. Antrean lokal hanya menahan JSON yang sudah siap PUT.

---

## 3. 🗂️ Peta File Proyek

```text
BRIDGING-PACS/
├── docker-compose.yml              # Orkestrasi: pacs-middleware + simgos-api
├── .env / .env.example             # Kredensial & konfigurasi (JANGAN commit .env)
├── README.md                       # Gambaran besar, instalasi, pemetaan kolom DB
├── Konsep.md                       # Alur detail HL7 -> JSON -> MySQL per tahap
├── CATATAN_PERUBAHAN.md            # Dokumen ini (handover)
├── JSON Integration - General (NEW).pdf   # Referensi pemetaan JSON pihak ketiga
├── Permintaan #4.sql               # Query daftar dokter Sp.Rad (bukan DDL)
│
├── middleware/                     # [Python] MLLP listener & konverter
│   ├── main.py                     # Entry point: MLLP server + worker antrean
│   ├── Dockerfile                  # python:3.10-slim; /app/logs + /app/data
│   ├── .dockerignore               # Cegah data/ , logs/ , .env masuk image
│   ├── requirements.txt            # requests, python-dotenv (tanpa library baru)
│   ├── config/settings.py          # Semua konfigurasi + validasi fail-closed
│   ├── hl7/{parser,converter,ack_builder}.py
│   ├── listener/mllp_server.py     # Server TCP multi-thread + logika ACK/antrean
│   ├── client/simgos_api.py        # HTTP PUT + retry + klasifikasi error
│   ├── spooler/
│   │   ├── queue_manager.py        # Antrean SQLite `message_spool`
│   │   └── spool_worker.py         # Worker penguras antrean (daemon)
│   ├── utils/logger.py             # Rotating log 10 MB x 5 (konsol + file)
│   ├── logs/                       # Ter-mount ./middleware/logs (gitignored)
│   └── data/spool.db               # Antrean lokal (gitignored, berisi data pasien)
│
├── simgos-api/                     # [PHP] REST endpoint
│   ├── api/pacs_bridging.php       # Endpoint PUT dari middleware
│   ├── config/{database,schema_map}.php
│   ├── models/{HasilRad,Hl7Log,Pasien,Tindakan}Model.php
│   └── logs/                       # Ter-mount /var/log/simgos (di luar webroot)
│
└── testing/
    ├── common.py                   # Helper bersama (jangan dijalankan sendiri)
    ├── check_connection.py         # Cek MLLP + API + DB (non-destruktif)
    ├── put_hl7.py                  # Kirim HL7 via MLLP atau PUT langsung
    ├── check_db.py                 # Lihat DB, snapshot, diff
    ├── test_spooler.py             # Uji store-and-forward (--status/--flush/--live)
    └── README.md                   # Prosedur pengujian lengkap
```

---

## 4. ⚙️ Referensi Konfigurasi (Environment)

Dibaca dari `.env` di root project. Environment variable proses selalu menang atas
isi `.env`.

### Middleware Python (`middleware/config/settings.py`)

| Variabel | Default | Fungsi |
|---|---|---|
| `MLLP_HOST` | `0.0.0.0` | Bind interface listener MLLP |
| `MLLP_PORT` | `2575` | Port listener MLLP |
| `SIMGOS_API_BASE_URL` | `http://simgos-api/api/v1` (Docker) | Basis URL API SIMGOS |
| `SIMGOS_AUTH_TYPE` | `api_key` | `api_key` atau `bearer` |
| `SIMGOS_API_KEY` | *(kosong — wajib)* | Header `X-Api-Key`; kosong = middleware **menolak start** |
| `SIMGOS_API_TOKEN` | *(kosong)* | Dipakai bila `SIMGOS_AUTH_TYPE=bearer` |
| `SIMGOS_API_TIMEOUT` | `15` | Timeout HTTP PUT (detik) |
| `SIMGOS_API_MAX_RETRIES` | `3` | Percobaan PUT per pengiriman |
| `SPOOL_DB_PATH` | `middleware/data/spool.db` / `/app/data/spool.db` | File antrean SQLite |
| `SPOOL_INTERVAL_SECONDS` | `30` | Interval worker menguras antrean |
| `SPOOL_MAX_RETRIES` | `0` (= tanpa batas) | Batas retry sebelum `ABANDONED`; lihat §5 |
| `SPOOL_BATCH_SIZE` | `10` | Pesan per putaran worker |
| `LOG_LEVEL` | `INFO` | `DEBUG`/`INFO`/`WARNING`/`ERROR` |
| `LOG_DIR` | `logs` | Direktori log middleware |
| `HOSPITAL_NAME` | `RSUP` | Identitas pada MSH ACK & log |

Nilai tetap di kode (tidak via env): buffer TCP 4096 B, timeout koneksi MLLP 30 s,
jeda retry HTTP 2 s, endpoint `/radiologi/pacs-bridging`, file log
`bridging_pacs.log` (rotasi 10 MB x 5).

### Simgos API / Container (`docker-compose.yml` & PHP)

| Variabel | Default | Fungsi |
|---|---|---|
| `SIMGOS_DB_HOST` / `SIMGOS_DB_PORT` | `host.docker.internal` / `3306` | Target MySQL/MariaDB |
| `SIMGOS_DB_NAME` | *(kosong)* | Kosong = mode multi-database (`layanan`/`ris`/`master`) |
| `SIMGOS_DB_USER` / `SIMGOS_DB_PASS` | `root` / *(kosong)* | Kredensial DB |
| `SIMGOS_API_KEY` | *(wajib)* | Kunci yang divalidasi endpoint PHP |
| `SIMGOS_API_HOST_PORT` | `8080` | Port HTTP API di host |
| `SIMGOS_API_LOG_FILE` | `/var/log/simgos/simgos_api.log` | Lokasi log API (di luar webroot) |

---

## 5. 📦 Riwayat Pembaruan — 2026-10-31: Store-and-Forward (Antrean Lokal)

*Status: working copy, belum di-commit.*

**Masalah sebelumnya.** Pengiriman ke SIMGOS bersifat **sinkron**: bila API SIMGOS
atau MySQL down, middleware membalas `MSA|AE` dan hasil bacaan dokter berisiko hilang
karena PACS umumnya tidak punya antrean mandiri yang andal.

**Sekarang middleware memiliki antrean lokal SQLite (store-and-forward):**

1. Payload hasil konversi ditulis ke tabel `message_spool` **sebelum** PACS menerima
   balasan, sehingga data tetap ada meski SIMGOS lama down.
2. PACS menerima `MSA|AA` dengan catatan
   `Accepted and queued for background synchronization` — dokter tidak melihat
   peringatan error.
3. Worker daemon (`SPOOL_INTERVAL_SECONDS`, default 30 detik) mengirim ulang antrean
   sampai sukses, tanpa restart middleware.
4. Error permanen (HTTP 4xx / payload rusak) langsung `ABANDONED` agar tidak membajak
   antrean; error jaringan/5xx dicoba terus.

### File yang dibuat / diubah

| Komponen | File | Jenis | Deskripsi Singkat |
|---|---|---|---|
| Middleware | `middleware/spooler/queue_manager.py` | Baru | Antrean SQLite `message_spool`: `enqueue`, `get_pending_batch`, `mark_as_sent`, `increment_retry`, `mark_abandoned`; dedupe MsgID; satu koneksi per thread + WAL + lock tulis |
| Middleware | `middleware/spooler/spool_worker.py` | Baru | Thread daemon penguras antrean (`flush_once`), dipakai juga oleh script uji |
| Middleware | `middleware/listener/mllp_server.py` | Logic | Jaringan/5xx -> antrekan + ACK `AA`; 4xx -> ACK `AE`; gagal tulis disk -> ACK `AE` (fail-safe) |
| Middleware | `middleware/client/simgos_api.py` | Logic | `ApiResponse.retryable` + `is_retryable_failure()`: klasifikasi error hidup di satu tempat, dipakai server & worker |
| Middleware | `middleware/hl7/ack_builder.py` | Logic | `build_aa()`/`build_ack()` menerima `text_message` untuk MSA-3 kustom |
| Middleware | `middleware/config/settings.py` | Config | `SPOOL_DB_PATH`, `SPOOL_INTERVAL_SECONDS`, `SPOOL_MAX_RETRIES`, `SPOOL_BATCH_SIZE` |
| Middleware | `middleware/main.py` | Orkestrasi | Worker antrean dijalankan bersama MLLP server, dihentikan rapi saat exit |
| Container | `docker-compose.yml` | Config | Volume `./middleware/data:/app/data` (antrean bertahan saat restart) + env spooler |
| Container | `middleware/Dockerfile`, `middleware/.dockerignore` | Config | Direktori `/app/data`; cegah `spool.db`/log/`.env` ikut ke image |
| Konfigurasi | `.env.example`, `.gitignore` | Dokumentasi | Parameter spooler; `middleware/data/` diabaikan Git (berisi data pasien) |
| Testing | `testing/test_spooler.py` | Baru | 20 asersi + mode `--status`, `--flush`, `--live` |
| Dokumen | `testing/README.md` | Dokumentasi | Prosedur uji pemulihan (matikan SIMGOS -> kirim -> hidupkan -> flush -> cek DB) |

### Keputusan desain penting

- **`SPOOL_MAX_RETRIES=0` (default) = tanpa batas.** Dengan interval 30 detik, batas
  20 percobaan hanya memberi toleransi ~10 menit downtime — terlalu singkat untuk
  perbaikan server nyata. Selama SIMGOS hanya tidak merespons, data dipertahankan
  sampai terkirim. Isi angka (mis. `20`) bila memang ingin batas keras; error 4xx
  tetap langsung `ABANDONED`.
- **Fail-safe ACK.** PACS hanya menerima `AA` bila payload benar-benar tersimpan di
  disk. Gagal menulis disk -> `AE`, supaya operator tahu data belum aman.
- **Idempotensi.** Bila koneksi terputus setelah server memproses payload tetapi
  sebelum respons diterima, payload bisa terkirim dua kali. Endpoint SIMGOS melakukan
  upsert (`ON DUPLICATE KEY UPDATE` pada `TINDAKAN_MEDIS`), jadi hasilnya tetap satu
  baris.
- **Dedupe.** Pesan PACS dengan `message_control_id` sama yang masih `PENDING` tidak
  digandakan di antrean.

Arti kolom `status`:

| Status | Arti | Tindakan |
|---|---|---|
| `PENDING` | Menunggu dikirim ulang oleh worker | Tidak ada; worker menangani sendiri |
| `SENT` | Sudah tersimpan di SIMGOS | Tidak ada |
| `ABANDONED` | Ditolak permanen (4xx/payload rusak) atau melewati batas retry | Periksa `last_error`, koreksi data, kirim ulang manual bila perlu |

---

## 6. 🔒 Riwayat Pembaruan — 2026-10-30: Security & Operational Hardening

*Commit `24b7a37` dan `9810fc1`.*

Pembaruan ini berfokus pada dua area kritis:

1. **Penghapusan Kredensial Default (Fail-Closed Auth)**: mengeliminasi string rahasia
   bawaan (`PACS-BRIDGE-SECRET-KEY-2024`) dari kode sumber dan memaksa sistem menolak
   beroperasi jika kredensial belum disetel di environment.
2. **Isolasi & Proteksi Log Data Pasien**: memindahkan file log dari webroot publik
   (`DocumentRoot`) ke direktori sistem terisolasi, serta memblokir akses HTTP
   langsung guna mencegah kebocoran data medis (*Protected Health Information* / PHI).

### Daftar file yang dimodifikasi

| Komponen | File | Jenis Perubahan | Deskripsi Singkat |
|---|---|---|---|
| **Orkestrasi** | `docker-compose.yml` | Security / Config | Validasi wajib `SIMGOS_API_KEY` (`:?`) dan pemindahan mount log ke `/var/log/simgos` |
| **Konfigurasi** | `.env.example` | Dokumentasi | Penambahan parameter `SIMGOS_API_LOG_FILE` |
| **Middleware** | `middleware/config/settings.py` | Security | Hapus fallback default API key; validasi fail-closed saat startup |
| **Middleware** | `middleware/client/simgos_api.py` | Security | Throw exception jika pengiriman data dilakukan tanpa kredensial |
| **API SIMGOS** | `simgos-api/api/pacs_bridging.php` | Security / Logic | Menolak request dengan HTTP 503 jika server belum punya API key; path log dinamis |
| **Web Server** | `simgos-api/.htaccess` | Security (Apache) | Blokir akses HTTP ke folder `logs/` dan semua file berekstensi `.log` |
| **Container** | `simgos-api/Dockerfile` | Security (OS) | Direktori terisolasi `/var/log/simgos` dengan izin user `www-data` |
| **Container** | `simgos-api/.dockerignore` | Security | Cegah kebocoran file log dan `.env` ke dalam Docker image |
| **Testing** | `testing/test_api_put.py` | Utility | Flag `--key` membaca environment; proteksi exit jika key kosong |
| **Dokumen** | `Konsep.md` | Dokumentasi | Sanitasi contoh header `X-Api-Key` |

### Detail teknis

**1. Autentikasi Fail-Closed (Anti-CWE-798 Hardcoded Credentials)**

- Sebelumnya: bila `SIMGOS_API_KEY` tidak diisi di `.env`, aplikasi memakai kunci
  default `PACS-BRIDGE-SECRET-KEY-2024` — siapa pun yang membaca repository dapat
  membobol endpoint SIMRS.
- Sesudah:
  - `docker-compose.yml`: `${SIMGOS_API_KEY:?SIMGOS_API_KEY wajib diisi di .env}` —
    Docker menolak start bila variabel kosong.
  - `middleware/config/settings.py`: `RuntimeError` saat modul diimpor bila key kosong.
  - `middleware/client/simgos_api.py`: `_build_headers()` melempar exception alih-alih
    mencatat warning, menjamin tidak ada request tanpa kredensial.
  - `simgos-api/api/pacs_bridging.php`: environment kosong -> `HTTP 503`
    ("Layanan belum dikonfigurasi.").

**2. Proteksi Log & Data Pasien (Anti-Information Disclosure)**

- Sebelumnya: log di `simgos-api/logs/simgos_api.log`, di dalam DocumentRoot
  (`/var/www/html/logs`). Salah konfigurasi Apache = file berisi nama pasien, NORM, dan
  hasil ekspertise dapat diunduh lewat browser (`http://ip:8080/logs/simgos_api.log`).
- Sesudah:
  - Di Docker, log dialihkan ke `/var/log/simgos/simgos_api.log` (di luar webroot).
  - Volume `./simgos-api/logs:/var/log/simgos` pada `docker-compose.yml`.
  - `.htaccess`: `Require all denied` untuk `*.log` + blokir route folder `logs/`.
  - `.dockerignore` agar log lokal tidak ter-pack ke image saat deploy.

---

## 7. 🚀 Operasional Harian

### A. Setup lingkungan baru

```bash
cp .env.example .env        # Windows: copy .env.example .env
```

Isi **wajib** di `.env`:

```ini
SIMGOS_API_KEY=kunci_rahasia_unik_rumah_sakit_2026
```

### B. Menjalankan via Docker

```bash
docker compose up -d --build
docker compose logs -f pacs-middleware      # pantau listener + worker antrean
```

Bila `SIMGOS_API_KEY` belum diset, Docker menolak start dengan pesan:
`required variable SIMGOS_API_KEY is missing a value: SIMGOS_API_KEY wajib diisi di .env`

### C. Menjalankan tanpa Docker (standalone)

```bash
pip install -r middleware/requirements.txt
python middleware/main.py                                               # MLLP server + worker
python middleware/main.py --test-file testing/sample_oru_message.hl7     # uji lokal tanpa PACS
```

Mode `--test-file` hanya untuk diagnostik: ia tidak melewati MLLP sehingga tidak
mengisi `message_spool` — kegagalan di mode itu tidak menambah antrean.

### D. Memantau antrean & riwayat

```bash
# Isi antrean lokal: status, retry_count, error terakhir
python testing/test_spooler.py --status

# Kuras antrean sekarang (tanpa menunggu interval worker)
python testing/test_spooler.py --flush

# Riwayat PUT/POST dari docker
docker logs pacs-middleware | grep RIWAYAT
docker logs pacs-middleware | grep RIWAYAT | grep GAGAL
docker compose logs simgos-api | grep -i error
```

Log berkas: `middleware/logs/bridging_pacs.log` (rotasi otomatis 10 MB x 5) dan
`simgos-api/logs/simgos_api.log`. Satu baris `RIWAYAT` = satu HTTP request, memuat
nomor urut, status HTTP, durasi, dan penanda `retryable`.

### E. Pemeriksaan berkala yang disarankan

| Frekuensi | Periksa |
|---|---|
| Harian | `--status` (ada `PENDING`/`ABANDONED`?) dan `docker logs` untuk baris `GAGAL` |
| Mingguan | Ruang disk volume `middleware/data` dan `middleware/logs` |
| Setelah gangguan server | Jumlah `PENDING` turun sampai 0, lalu verifikasi beberapa accession di DB |

---

## 8. 🩺 Troubleshooting

| Gejala | Kemungkinan penyebab | Tindakan |
|---|---|---|
| PACS melaporkan error / respons `MSA\|AE` | Data ditolak server (4xx) atau konversi gagal | `docker logs pacs-middleware \| grep RIWAYAT`; cek `error=` dan `status=`; perbaiki data sumber |
| Data tidak ada di SIMGOS tetapi PACS dapat `AA` | SIMGOS sedang down; payload ada di antrean | `--status`; bila `PENDING`, tunggu worker atau jalankan `--flush` |
| Baris `ABANDONED` bertambah | Data invalid permanen (mis. accession tak dikenal, NORM kosong) | Baca `last_error`, koreksi payload, kirim ulang (`python testing/put_hl7.py --direct ...`) |
| Antrean tidak pernah turun | API SIMGOS masih tidak dapat dijangkau dari container | `python testing/check_connection.py`; cek `SIMGOS_API_BASE_URL` dan jaringan/DNS container |
| Log middleware: `Konfigurasi autentikasi SIMGOS tidak valid` | `SIMGOS_API_KEY` kosong (fail-closed) | Isi `.env` |
| Container `pacs-middleware` menolak start | Variabel wajib kurang | Pesan Docker menyebut nama variabel; lengkapi `.env` |
| Gagal bind port MLLP | Port sudah dipakai proses lain | Hentikan proses tersebut atau set `MLLP_PORT` lain (PACS perlu disesuaikan) |
| Antrean gagal disimpan walau file DB ada | Direktori `middleware/data` tidak writable / disk penuh | Cek izin & ruang disk; selama gagal, middleware membalas `AE` ke PACS |
| File log tidak bertambah | `LOG_DIR` tidak writable atau volume belum ter-mount | Cek `docker compose config` dan izin folder |
| `database is locked` pada `spool.db` | Ada tool lain membuka file antrean dan menahan write lock | Tutup tool tersebut; jangan menjalankan query tulis manual yang lama pada antrean produksi |

Semua script di `testing/` mengembalikan exit code `0` = lulus, `1` = gagal, sehingga
aman dipakai untuk pemeriksaan terjadwal sederhana.

---

## 9. 🔐 Keamanan & Privasi Antrean Lokal

`middleware/data/spool.db` memuat **data medis pasien** (nama, NORM, hasil bacaan,
kesan). Perlakukan setara database produksi:

- **Jangan commit** — `middleware/data/` sudah masuk `.gitignore`, dan
  `middleware/.dockerignore` mencegahnya masuk Docker image.
- **Batasi akses berkas**: gunakan izin ketat (mis. `chmod 600 spool.db*`, owner user
  yang menjalankan middleware); jangan diletakkan di share jaringan publik.
- **Jangan kirim isi antrean** ke tiket/chat/screenshot; cukup `id`, `status`, dan
  pesan error.
- **Backup terenkripsi** bila antrean disalin keluar server.
- Mode WAL membuat berkas pendamping `spool.db-wal` dan `spool.db-shm`; **salin ketiganya
  bersama-sama**, jangan hanya `spool.db`.
- **Jangan hapus `spool.db`** saat masih ada baris `PENDING` — itu membuang hasil bacaan
  yang belum terkirim.

Catatan penting: memberi `MSA|AA` berarti middleware menyatakan data aman **di sisi
middleware**, bukan di MySQL. Karena itu volume `middleware/data` wajib persisten
(`./middleware/data:/app/data`) dan tidak boleh dihapus saat pemeliharaan.

---

## 10. 🧰 Pemeliharaan & Prosedur Pemulihan

### A. Uji pemulihan penuh (SIMGOS down -> hidup lagi)

```bash
docker compose stop simgos-api
python testing/put_hl7.py --file testing/sample_oru_message.hl7
python testing/test_spooler.py --status          # 1 baris PENDING; PACS dapat AA
docker compose start simgos-api
python testing/test_spooler.py --flush           # baris menjadi SENT
python testing/check_db.py --acc <ACCESSION>     # verifikasi di MySQL
```

Tanpa `--flush`, worker memindahkan baris ke `SENT` sendiri dalam <= 30 detik setelah
SIMGOS normal.

### B. Backup antrean

```bash
docker compose stop pacs-middleware
cp middleware/data/spool.db* /backup/lokasi-aman/
docker compose start pacs-middleware
```

Salinan harus mencakup `-wal` dan `-shm`. Untuk salinan konsisten tanpa menghentikan
container (jalankan di host yang memiliki `sqlite3`):

```bash
sqlite3 middleware/data/spool.db ".backup /backup/spool-$(date +%F).db"
```

### C. Membersihkan baris lama

Baris `SENT` dan `ABANDONED` tidak dihapus otomatis, sehingga file antrean tumbuh:

1. Ekspor dulu ke arsip CSV:
   `sqlite3 middleware/data/spool.db ".mode csv" ".output arsip.csv" "SELECT * FROM message_spool WHERE status='SENT';"`
2. Baru hapus baris `SENT` yang lebih tua dari masa retensi rumah sakit.
3. Baris `ABANDONED` sebaiknya diperiksa manusia dulu (koreksi + kirim ulang) sebelum
   diarsipkan; jangan langsung dihapus.

### D. Verifikasi cepat bahwa fitur antrean hidup

```bash
python testing/test_spooler.py                              # suite, DB antrean terisolasi
docker logs pacs-middleware | grep "Worker antrean aktif"
```

---

## 11. 🧪 Pengujian (Testing)

Semua script berada di `testing/` (detail lengkap: `testing/README.md`).

| Script | Fungsi |
|---|---|
| `check_connection.py` | Cek MLLP, API, dan DB — non-destruktif |
| `put_hl7.py` | Kirim HL7 via MLLP atau HTTP PUT langsung, dengan snapshot/diff DB |
| `check_db.py` | Lihat isi DB, snapshot, dan diff sebelum/sesudah |
| `test_spooler.py` | Uji store-and-forward: suite, `--status`, `--flush`, `--live` |
| `test_api_put.py`, `sample_oru_sender.py` | Script lama; fungsinya sudah tercakup `put_hl7.py` |

Contoh:

```bash
python testing/check_connection.py
python testing/check_connection.py --api-key KUNCI_SALAH    # harus ditolak (401)
python testing/put_hl7.py --direct --acc RADTEST01 --watch --hasil "versi 1"
python testing/test_spooler.py
python testing/test_spooler.py --status
python testing/test_spooler.py --live --host 127.0.0.1 --port 2575
```

Catatan dependensi: script `testing/` membutuhkan `requests`; `common.py` memakai
`pymysql` bila tersedia (fallback `docker run mysql:8.0`). Middleware membutuhkan
`requests` + `python-dotenv` (`middleware/requirements.txt`) — jalankan dengan
interpreter yang dependensinya sudah terpasang.

---

## 12. ⚠️ Batasan & Utang Teknis yang Diketahui

Disengaja maupun belum dikerjakan — penting agar tidak dianggap bug baru:

1. **Hanya `ORU^R01` yang diproses.** Pesan lain dibalas `MSA|AE`
   (`Unsupported message type`); ORM/order belum ditangani.
2. **Tidak ada saklar on/off antrean.** Perilaku store-and-forward selalu aktif;
   mengembalikan perilaku lama (langsung `AE` saat down) berarti mengubah
   `middleware/listener/mllp_server.py`.
3. **`ABANDONED` tidak pernah dicoba ulang otomatis.** Perlu tindakan manusia.
4. **Retry berinterval tetap**, tanpa exponential backoff; saat antrean panjang dan
   SIMGOS lambat, setiap pesan dicoba pada tiap putaran.
5. **Dedupe hanya untuk baris `PENDING`** dengan `message_control_id` sama. Pesan yang
   sudah `SENT` lalu dikirim ulang PACS akan diproses sebagai UPDATE baru.
6. **Ambiguitas pengiriman ganda** (timeout setelah server memproses) — aman karena
   endpoint bersifat upsert, tetapi bukan jaminan exactly-once.
7. **`README.md` masih menyebut `hasil_rad.sql` dan `hl7_logs.sql`** di struktur
   direktori, padahal kedua file itu tidak ada di repo (yang ada `Permintaan #4.sql`,
   isinya query daftar dokter Sp.Rad).
8. **`Konsep.md` memakai nomor baris sumber** (mis. `simgos_api.py:104`) yang sudah
   bergeser setelah perubahan spooler — arah alurnya benar, nomor barisnya perlu
   diperbarui.
9. **Belum ada notifikasi otomatis** saat muncul `ABANDONED` atau `PENDING` menumpuk;
   pemantauan masih manual (`--status`, `docker logs`).

---

## 13. 📖 Glosarium

| Istilah | Arti |
|---|---|
| **MLLP** | Minimal Lower Layer Protocol — pembungkus pesan HL7 di atas TCP (0x0B ... 0x1C 0x0D) |
| **HL7 v2** | Standar pertukaran data kesehatan; dipakai varian `ORU^R01` (hasil observasi/laporan) |
| **MSH / PID / PV1 / OBR / OBX** | Segmen HL7: header, identitas pasien, kunjungan, order pemeriksaan, hasil observasi |
| **ACK / AA / AE / AR** | Balasan penerimaan: `AA` diterima, `AE` error, `AR` ditolak |
| **NORM** | Nomor Rekam Medis pasien (dari PID-3) |
| **Accession number** | ID tindakan/pemeriksaan (OBR-3); kunci upsert di `TINDAKAN_MEDIS` |
| **Store-and-forward** | Pola menahan data sementara lalu mengirim ulang sampai berhasil |
| **Spool / antrean** | Tabel `message_spool` di SQLite yang menyimpan payload tertahan |
| **Upsert** | `INSERT ... ON DUPLICATE KEY UPDATE`: kirim ulang = UPDATE, bukan baris baru |
| **PHI** | Protected Health Information — data medis yang wajib dilindungi |
| **WAL** | Write-Ahead Logging SQLite; memunculkan berkas `-wal`/`-shm` pendamping |
| **Fail-closed** | Menolak berjalan bila konfigurasi keamanan tidak lengkap |
