# TASK: Implementasi Store-and-Forward Local Spooler (Offline Queue) pada Middleware Python

## 1. Konteks & Masalah
Di repositori `BRIDGING-PACS`, file `middleware/listener/mllp_server.py` saat ini memproses pengiriman HTTP PUT ke SIMGOS secara sinkron (`send_to_simgos(payload)`).
Jika API SIMGOS atau database MySQL sedang down (timeout / HTTP 5xx), middleware langsung membalas `MSA|AE` (gagal) ke PACS. Mesin PACS umumnya tidak memiliki mekanisme antrean mandiri yang andal, sehingga hasil bacaan dokter radiologi berisiko tercecer atau hilang.

## 2. Sasaran Implementasi
Buat mekanisme **Store-and-Forward (Offline Buffer)** berbasis SQLite di sisi Python:
Jika API SIMGOS tidak merespons (koneksi putus/5xx), payload HL7 yang sudah di-parse disimpan ke database lokal SQLite dan di-retry otomatis oleh background worker secara berkala sampai sukses tersimpan ke SIMGOS.

## 3. Spesifikasi Teknis yang Harus Dibuat

### A. Modul Baru: `middleware/spooler/queue_manager.py`
- Gunakan SQLite lokal (file path dikonfigurasi via env: `SPOOL_DB_PATH`, default: `/app/data/spool.db` atau `middleware/data/spool.db`).
- Buat tabel `message_spool`:
  - `id` (INTEGER PRIMARY KEY AUTOINCREMENT)
  - `accession_number` (TEXT)
  - `message_control_id` (TEXT)
  - `payload_json` (TEXT)
  - `retry_count` (INTEGER DEFAULT 0)
  - `status` (TEXT: 'PENDING', 'SENT', 'ABANDONED')
  - `last_error` (TEXT)
  - `created_at` (DATETIME)
  - `updated_at` (DATETIME)
- Fungsi utama:
  - `enqueue(payload: SimgosPayload, error_reason: str) -> bool`
  - `get_pending_batch(limit: int = 10) -> list[dict]`
  - `mark_as_sent(spool_id: int)`
  - `increment_retry(spool_id: int, error_msg: str, max_retries: int = 20)`
- Pastikan thread-safety (SQLite connection per-thread atau locking).

### B. Background Worker: `middleware/spooler/spool_worker.py`
- Jalankan thread daemon latar belakang dari `main.py`.
- Interval polling berkala (default: 30 detik, configurable via `SPOOL_INTERVAL_SECONDS`).
- Mekanisme:
  1. Cek apakah ada record berstatus `PENDING` di `message_spool`.
  2. Jika ada, coba kirim via `simgos_api.send_to_simgos(payload)`.
  3. Jika berhasil (HTTP 200/201): tandai `SENT`.
  4. Jika gagal (timeout/5xx): tambah `retry_count` dan catat error.
  5. Jika gagal karena HTTP 4xx (data invalid): tandai `ABANDONED` agar tidak membajak antrean secara infinit.

### C. Modifikasi `middleware/listener/mllp_server.py`
- Pada penanganan response `api_resp = send_to_simgos(payload)`:
  - Jika `api_resp.success == False` dan error tergolong jaringan/server (bukan 4xx client validation):
    1. Panggil `spooler.enqueue(payload, api_resp.error)`.
    2. Kirim balasan ACK `MSA|AA` dengan catatan teks: `Accepted and queued for background synchronization` agar PACS menganggap data aman diterima dan tidak melempar peringatan error ke dokter.
  - Jika error 4xx: tetap kirim `MSA|AE` seperti biasa.

### D. Konfigurasi Lingkungan & Docker
- Di `docker-compose.yml`:
  - Tambahkan volume mount untuk direktori database spooler: `./middleware/data:/app/data` agar antrean tidak hilang saat kontainer di-restart.
- Di `.env.example` dan `middleware/config/settings.py`:
  - Tambahkan parameter `SPOOL_DB_PATH` dan `SPOOL_INTERVAL_SECONDS=30`.

## 4. Batasan & Syarat Pengerjaan
1. Jangan merusak logika parsing HL7 yang sudah ada di `middleware/hl7/parser.py` dan `converter.py`.
2. Jangan sentuh kredensial asli di `.env`.
3. Gunakan hanya library standar Python (`sqlite3`, `threading`, `time`, `json`) tanpa menambah library third-party baru di `requirements.txt`.
4. Buat script unit test / simulasi di folder `testing/test_spooler.py` untuk menguji skenario:
   - API SIMGOS mati -> Data masuk spool DB -> Balas ACK AA.
   - API SIMGOS hidup -> Worker flush antrean -> Data sukses tersimpan di MySQL.