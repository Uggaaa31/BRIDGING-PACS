# Testing — Bridging PACS-SIMGOS

Script pengujian dan pengiriman data ke SIMGOS. Semua konfigurasi dibaca dari
`.env` di root project.

## Peringatan: semua file wajib satu folder

`put_hl7.py`, `check_connection.py`, dan `check_db.py` meng-`import common.py`.
Python mencari `common.py` di folder tempat script berada, jadi keempat file
harus tinggal di `testing/`:

```
testing/
├── common.py              helper bersama (jangan dijalankan sendiri)
├── check_connection.py
├── put_hl7.py
├── check_db.py
├── test_spooler.py        uji store-and-forward (antrean lokal SQLite)
├── payload.json           payload PUT harian
├── payload_example.json   contoh payload
└── sample_oru_message.hl7 contoh pesan HL7
```

Selain itu `common.py` menghitung lokasi `.env` dari posisi folder:

```python
ROOT = Path(__file__).resolve().parent.parent   # common.py:39
```

Folder `testing/` harus tetap di dalam `D:\MagangHub\BRIDGING-PACS\`, tepat satu
level di bawah `.env`. Kalau dipindah, baris itu yang perlu disesuaikan.

Working directory bebas: menjalankan dari root project maupun dari dalam
`testing/` sama-sama bekerja.

## Dependensi

```bash
pip install requests pymysql
```

- `requests` — wajib, untuk HTTP PUT.
- `pymysql` — opsional. Jika tidak ada, script otomatis memakai container
  `mysql:8.0` lewat Docker untuk membaca database.

---

## Cara tercepat: edit, simpan, Run

`put_hl7.py` tanpa argumen sama sekali akan otomatis:

1. Membaca `testing/payload.json`
2. Mengirim HTTP PUT langsung ke API (tanpa HL7/MLLP)
3. Merekam snapshot DB sebelum PUT
4. Menampilkan diff sesudah PUT

Jadi alurnya:

1. Buka `testing/payload.json`, ubah isinya.
2. Simpan (Ctrl+S).
3. Klik tombol **Run** di kanan atas IDE.

Tidak perlu terminal, tidak perlu argumen.

Jika `payload.json` tidak ditemukan, script jatuh ke mode aman: mengirim pesan
HL7 contoh via MLLP dengan accession baru.

### Konfigurasi Run di IDE

**VS Code** — `.vscode/launch.json`:

```json
{
  "version": "0.2.0",
  "configurations": [
    {
      "name": "PUT SIMGOS",
      "type": "debugpy",
      "request": "launch",
      "program": "${workspaceFolder}/testing/put_hl7.py",
      "cwd": "${workspaceFolder}",
      "console": "integratedTerminal"
    }
  ]
}
```

**PyCharm** — Run → Edit Configurations:

- Script path: `D:\MagangHub\BRIDGING-PACS\testing\put_hl7.py`
- Working directory: `D:\MagangHub\BRIDGING-PACS`
- Script parameters: kosongkan

---

## 1. Cek koneksi

```bash
python testing/check_connection.py
```

Contoh keluaran:

```
[ OK ] TCP MLLP         127.0.0.1:2575 | 0.5ms
[ OK ] Auth & routing   auth OK, routing OK | HTTP 400
[ OK ] Koneksi & login  10.10.160.99:3306 | user=pacs_bridging
```

**Tidak menulis data apa pun.** Probe API sengaja mengirim body kosong. Urutan
validasi `simgos-api/api/pacs_bridging.php` adalah metode → autentikasi →
identifier → body, sehingga:

- API key salah → `401`
- API key benar → `400 "Request body kosong."`

Keduanya membuktikan autentikasi dan routing hidup, tanpa satu pun INSERT ke
`hasil_rad` atau `hl7_logs`.

---

## 2. Kirim data

```bash
# tanpa argumen -> testing/payload.json + --watch (cara tercepat, di atas)
python testing/put_hl7.py

# MLLP penuh: HL7 -> parser -> converter -> PUT -> DB
python testing/put_hl7.py --file testing/sample_oru_message.hl7

# HTTP PUT langsung ke API, tanpa HL7/MLLP
python testing/put_hl7.py --direct

# kirim isi file JSON tertentu
python testing/put_hl7.py --direct --payload-file testing/payload_example.json
```

### PUT mandiri lewat flag

```bash
python testing/put_hl7.py --direct --acc RADTEST01 --norm 123456 \
    --klinis "Batuk 3 minggu" \
    --hasil "Kavitas 2cm lobus kanan atas" \
    --kesan "TB paru aktif" \
    --usul "Kontrol 2 minggu" \
    --dokter-id 0
```

Accession wajib maksimal 11 karakter karena kolom `TINDAKAN_MEDIS` bertipe
`CHAR(11)`. Bila `--acc` tidak diisi, dibuat otomatis dengan format
`RAD<HHMMSS><nn>`.

### Payload harus lengkap

Mengirim payload sebagian akan **mengosongkan** kolom yang tidak disertakan.
Penyebabnya, `pacs_bridging.php:228-236` memakai `?? null` untuk setiap kolom,
lalu `HasilRadModel::upsert` mengubah `null` menjadi string kosong.

Contoh payload lengkap — lihat `payload_example.json`:

```json
{
  "norm": "99001234",
  "nama": "BAMBANG WAHONO SUHARTO",
  "tanggal_lahir": "1978-03-15",
  "jenis_kelamin": "1",
  "accession_number": "RAD18021914",
  "kode_tindakan": "24630-5",
  "nama_tindakan": "CT Scan Thoraks",
  "tanggal_pemeriksaan": "2026-10-28 18:02:00",
  "dokter_pengirim": "dr. MUHAMMAD HIDAYAT, Sp.PD",
  "klinis": "Batuk berkepanjangan > 3 minggu. Riwayat kontak TB.",
  "hasil": "Tampak infiltrat di lobus kanan atas. Kavitas diameter +/-10cm.",
  "kesan": "TB Paru aktif dengan kavitas lobus kanan atas.",
  "usul": "Saran pemeriksaan sputum BTA. Konsultasi paru.",
  "btk": "",
  "kritis": 1,
  "dokter_id": 0,
  "dokter_nama": "",
  "oleh": 0,
  "pacs_hook_uid": "1.2.840.113619.2.55.3.20261028180219"
}
```

Untuk mengubah satu kolom pada baris yang sudah ada, salin dulu nilai kolom
lain dari database (`python testing/check_db.py --acc <accession>`), ubah kolom
yang diinginkan, lalu kirim ulang.

---

## 3. Bukti INSERT vs UPDATE

Tambahkan `--watch`. Script akan:

1. Merekam keadaan DB **sebelum** PUT ke
   `testing/.snapshots/<accession>-<timestamp>.json`
2. Mengirim PUT
3. Membaca DB **sesudah**, lalu menampilkan diff kolom per kolom

```bash
# PUT pertama -> INSERT
python testing/put_hl7.py --direct --acc RADTEST01 --watch --hasil "versi 1"

# PUT kedua, accession SAMA -> UPDATE
python testing/put_hl7.py --direct --acc RADTEST01 --watch --hasil "versi 2"
```

Contoh keluaran run kedua:

```
[ OK ] Snapshot sebelum  hasil_rad=1 baris | RADTEST01-20261028-175434.json
      Accession SUDAH ADA -> PUT ini akan jadi UPDATE (upsert)
[ OK ] HTTP PUT  181.1ms  HTTP 200 | Data PACS berhasil disinkronkan...
[ OK ] STATUS  UPDATE — accession sudah ada, baris diperbarui (upsert)

      kolom            sebelum                            sesudah
      ---------------- ---------------------------------- --------------------------
      TANGGAL          2026-10-28 17:54:27                2026-10-28 17:54:34
      HASIL            versi 1: kavitas 2cm               versi 2: kavitas mengecil
      KESAN            TB Paru aktif dengan kavitas lob   TB paru membaik
```

Cara membaca:

- **`ID` tetap sama** → UPDATE
- **`ID` baru muncul** → INSERT

Perilaku ini berasal dari `ON DUPLICATE KEY UPDATE` pada UNIQUE KEY
`TINDAKAN_MEDIS` di `simgos-api/models/HasilRadModel.php`.

---

## 4. Cek isi database

```bash
# 10 transaksi terakhir di hasil_rad + hl7_logs
python testing/check_db.py

# satu accession, semua kolom
python testing/check_db.py --acc RADTEST01

# daftar file snapshot
python testing/check_db.py snapshots

# rekam snapshot manual, tanpa mengirim data
python testing/check_db.py snapshot --acc RADTEST01

# diff terhadap snapshot terakhir accession itu
python testing/check_db.py diff --acc RADTEST01
```

Label pada mode `diff`:

| Label | Arti |
|---|---|
| `INSERT` | Sebelumnya 0 baris, sekarang ada |
| `UPDATE` | Baris ada, sejumlah kolom berubah (nilai lama vs baru ditampilkan) |
| `Nilai identik` | Baris ada, isi sama |
| `HILANG` | Sebelumnya ada, sekarang 0 baris |

---

## 5. Uji Store-and-Forward (Offline Spooler)

Saat API SIMGOS mati, middleware menyimpan payload ke antrean lokal SQLite
(`middleware/data/spool.db`) dan tetap membalas `MSA|AA` ke PACS. Worker
latar belakang mengirim ulang antrean itu setiap `SPOOL_INTERVAL_SECONDS`
(default 30 detik). Detail modul: `middleware/spooler/`.

### Suite otomatis (tanpa MySQL, tanpa kredensial produksi)

```bash
python testing/test_spooler.py
```

Script menjalankan 20 asersi dengan DB antrean sementara + stub HTTP API lokal:
klasifikasi error (503/jaringan → layak retry, 422 → tidak), API mati → antrean
terisi dan PACS dapat `AA`, dedupe pesan ganda, worker menguras antrean → `SENT`,
retry berkali-kali → `ABANDONED`, payload rusak → `ABANDONED`, dan 4xx realtime →
`AE` tanpa masuk antrean. Exit code `0` = lulus.

### Inspeksi & kuras manual

```bash
# isi antrean: status, retry_count, error terakhir
python testing/test_spooler.py --status

# satu putaran worker ke API SIMGOS asli
python testing/test_spooler.py --flush
```

### Uji pemulihan sungguhan (dua terminal)

```bash
# 1. Matikan API SIMGOS
docker compose stop simgos-api

# 2. Kirim HL7 -> PACS harus menerima ACK AA (bukan AE)
python testing/put_hl7.py --file testing/sample_oru_message.hl7
python testing/test_spooler.py --status        # ada 1 baris PENDING

# 3. Hidupkan lagi API
docker compose start simgos-api

# 4. Kuras antrean tanpa menunggu interval
python testing/test_spooler.py --flush         # baris jadi SENT

# 5. Pastikan data benar-benar masuk MySQL
python testing/check_db.py --acc <ACCESSION>
```

Atau tunggu worker: baris berpindah ke `SENT` sendiri dalam ≤ 30 detik setelah
SIMGOS normal, tanpa restart middleware.

### Uji end-to-end pada middleware yang berjalan

```bash
python testing/test_spooler.py --live --host 127.0.0.1 --port 2575
```

Mengirim ORU^R01 baru via MLLP, memastikan PACS menerima `AA`, lalu menunggu
baris muncul di `hasil_rad` MySQL (dilewati bila DB tidak dapat diakses).

---

## Alur uji lengkap

```bash
python testing/check_connection.py
python testing/put_hl7.py --direct --acc RADTEST01 --watch --hasil "versi 1"
python testing/put_hl7.py --direct --acc RADTEST01 --watch --hasil "versi 2"
python testing/check_db.py --acc RADTEST01
```

Exit code `0` = lulus, `1` = gagal.

---

## Catatan

- User DB `pacs_bridging` tidak punya grant ke `master.pasien`
  (`ERROR 1142`). Upsert pasien gagal, tetapi non-fatal: transaksi
  `hasil_rad` tetap commit. Script menandainya `[SKIP]`, bukan gagal.
- Snapshot disimpan di `testing/.snapshots/`, dibuat otomatis, dan sudah masuk
  `.gitignore`.
- Membaca database memakai `pymysql` bila terpasang, atau fallback ke
  `docker run mysql:8.0` dengan SQL dikirim lewat stdin.
- Script lama `sample_oru_sender.py` dan `test_api_put.py` masih tersedia,
  tetapi fungsinya sudah tercakup oleh `put_hl7.py`.
