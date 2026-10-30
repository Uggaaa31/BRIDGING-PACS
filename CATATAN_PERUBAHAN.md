# Catatan Perubahan & Panduan Handover Proyek Bridging PACS - SIMGOS

Dokumen ini mencatat seluruh pembaruan arsitektur keamanan, perbaikan konfigurasi, serta panduan operasional agar rekan tim atau senior developer dapat memahami dan mengoperasikan sistem dengan mudah.

---

## 📌 Ringkasan Pembaruan (Security & Operational Hardening)

Pembaruan terbaru berfokus pada dua area kritis:
1. **Penghapusan Kredensial Default (Fail-Closed Auth)**: Mengeliminasi string rahasia bawaan (`PACS-BRIDGE-SECRET-KEY-2024`) dari kode sumber dan memaksa sistem menolak beroperasi jika kredensial belum disetel di environment.
2. **Isolasi & Proteksi Log Data Pasien**: Memindahkan penyimpanan file log dari webroot publik (`DocumentRoot`) ke direktori sistem yang terisolasi, serta memblokir akses HTTP langsung guna mencegah kebocoran data medis (*Protected Health Information* / PHI).

---

## 🗂️ Daftar File yang Dimodifikasi

| Komponen | File | Jenis Perubahan | Deskripsi Singkat |
|---|---|---|---|
| **Orkestrasi** | `docker-compose.yml` | Security / Config | Validasi wajib `SIMGOS_API_KEY` (`:?`) dan pemindahan mount log ke `/var/log/simgos`. |
| **Konfigurasi** | `.env.example` | Dokumentasi | Penambahan parameter `SIMGOS_API_LOG_FILE`. |
| **Middleware** | `middleware/config/settings.py` | Security | Hapus fallback default API key; validasi fail-closed saat startup. |
| **Middleware** | `middleware/client/simgos_api.py` | Security | Throw exception jika pengiriman data dilakukan tanpa kredensial. |
| **API SIMGOS** | `simgos-api/api/pacs_bridging.php` | Security / Logic | Menolak request dengan HTTP 503 jika server belum punya API key; path log dinamis. |
| **Web Server** | `simgos-api/.htaccess` | Security (Apache) | Blokir akses HTTP ke folder `logs/` dan semua file berekstensi `.log`. |
| **Container** | `simgos-api/Dockerfile` | Security (OS) | Pembuatan direktori terisolasi `/var/log/simgos` dengan izin user `www-data`. |
| **Container** | `simgos-api/.dockerignore` | Security | Cegah kebocoran file log dan file `.env` ke dalam Docker image. |
| **Testing** | `testing/test_api_put.py` | Utility | Flag `--key` membaca environment; proteksi exit jika key kosong. |
| **Dokumen** | `Konsep.md` | Dokumentasi | Sanitasi contoh header `X-Api-Key`. |

---

## 🔍 Detail Perubahan Teknis

### 1. Autentikasi Fail-Closed (Anti-CWE-798 Hardcoded Credentials)
- **Sebelumnya**: Jika developer atau admin tidak mengisi `SIMGOS_API_KEY` di file `.env`, aplikasi otomatis menggunakan kunci rahasia default `PACS-BRIDGE-SECRET-KEY-2024`. Siapa pun yang membaca repository dapat membobol endpoint SIMRS.
- **Sesudah**:
  - `docker-compose.yml`: Menggunakan sintaks `${SIMGOS_API_KEY:?SIMGOS_API_KEY wajib diisi di .env}`. Docker menolak build/run jika variabel ini kosong.
  - `middleware/config/settings.py`: Melempar `RuntimeError` saat modul diimpor jika `SIMGOS_API_KEY` kosong.
  - `middleware/client/simgos_api.py`: Fungsi `_build_headers()` melempar exception alih-alih hanya mencatat warning, menjamin tidak ada request unauthenticated yang bocor ke jaringan.
  - `simgos-api/api/pacs_bridging.php`: Jika environment server kosong, endpoint mengembalikan respon `HTTP 503 Service Unavailable ("Layanan belum dikonfigurasi.")`.

### 2. Proteksi Log & Data Pasien (Anti-Information Disclosure)
- **Sebelumnya**: Log transaksi disimpan di `simgos-api/logs/simgos_api.log` yang berada di dalam folder DocumentRoot web server Apache (`/var/www/html/logs`). Jika web server salah konfigurasi, file log berisi nama pasien, NORM, dan hasil ekspertise radiologi dapat diunduh langsung lewat browser (`http://ip:8080/logs/simgos_api.log`).
- **Sesudah**:
  - Di lingkungan Docker, log dialihkan ke `/var/log/simgos/simgos_api.log` di luar webroot.
  - Volume pada `docker-compose.yml` di-mount ke `./simgos-api/logs:/var/log/simgos`.
  - Aturan Apache `.htaccess` menambahkan proteksi berlapis: penolakan akses `Require all denied` untuk semua file `*.log` dan pemblokiran route folder `logs/`.
  - Menambahkan `.dockerignore` agar file log lokal pengembang tidak pernah ter-pack ke dalam Docker image saat di-deploy.

---

## 🚀 Panduan Operasional untuk Tim / Senior

### A. Setup Lingkungan Baru
1. Salin template environment:
   ```bash
   cp .env.example .env
   # atau di Windows PowerShell:
   copy .env.example .env
   ```
2. Buka file `.env` dan **wajib** isi variabel kredensial:
   ```ini
   SIMGOS_API_KEY=kunci_rahasia_unik_rumah_sakit_2026
   ```

### B. Menjalankan Sistem via Docker
```bash
docker compose up -d --build
```
> **Catatan**: Jika `SIMGOS_API_KEY` belum diset di file `.env`, Docker akan menolak berjalan dengan pesan:
> `required variable SIMGOS_API_KEY is missing a value: SIMGOS_API_KEY wajib diisi di .env`

---

## 🧪 Cara Menjalankan Pengujian (Testing)

Semua script pengujian berada di folder `testing/`:

### 1. Cek Koneksi & Validasi Key (Non-Destruktif)
Menguji apakah MLLP, REST API SIMGOS, dan Database terhubung dengan benar tanpa menulis data apa pun:
```bash
python testing/check_connection.py
```
- Jika API Key valid: status `[ OK ] Auth & routing`.
- Jika ingin menguji penolakan key yang salah:
  ```bash
  python testing/check_connection.py --api-key KUNCI_SALAH
  ```
  Sistem akan menampilkan `[FAIL] Auth & routing  API key ditolak (401)`.

### 2. Uji Direct HTTP PUT (Bypass HL7)
Untuk menguji pengiriman data JSON radiologi langsung ke endpoint PHP:
```bash
python testing/test_api_put.py --url http://localhost:8080/api/v1 --key <API_KEY_ANDA>
```
Script akan memvalidasi payload dan menampilkan respons JSON dari server SIMRS.
