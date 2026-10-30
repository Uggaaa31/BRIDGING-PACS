# Konsep — Alur HL7 sampai MySQL SIMGOS

## Inti

HL7 → objek Python → JSON → HTTP PUT → PHP → MySQL.

Middleware Python tidak pernah menyentuh MySQL langsung. Semua akses database
dipegang PHP. Python hanya mengubah teks HL7 menjadi JSON dan mengirimkannya.

## 6 Langkah

```
PACS
  |  kirim HL7 v2 ORU^R01, TCP 2575, bungkus MLLP (0x0B ... 0x1C 0x0D)
  v
1. Terima     mllp_server.py:51     bytes MLLP -> teks HL7
2. Parse      parser.py:117         teks HL7  -> HL7Message (segmen/field)
3. Konversi   converter.py:515      HL7Message -> SimgosPayload
4. Serialisasi converter.py:79      SimgosPayload -> JSON
5. Kirim      simgos_api.py:104     JSON -> HTTP PUT
6. Simpan     HasilRadModel.php:68  JSON -> MySQL
```

Balik ke PACS hanya ACK `MSA|AA`, tanpa data.

---

## Contoh Nyata

### Masuk — HL7

```
OBX|1|TX|59776-5^KLINIS^LN||Batuk berkepanjangan > 3 minggu. Riwayat kontak TB.
OBX|2|TX|59780-7^HASIL^LN||Tampak infiltrat di lobus kanan atas. Kavitas +/-2cm.
OBX|3|TX|19005-8^KESAN^LN||TB Paru aktif dengan kavitas lobus kanan atas.
OBX|4|TX|18783-1^USUL^LN||Saran pemeriksaan sputum BTA. Konsultasi paru.
OBR|1|ORD-...|RADHL7DEMO1|24630-5^CT Scan Thoraks^LN|||20261028203200
PID|1||77788899^^^RSUP^PI||SUHARTO^BAMBANG^WAHONO||19780315|M
```

Kolom ditentukan **OBX-3**, bukan urutan segmen:

| OBX-3 | Kolom DB |
|---|---|
| `59776-5^KLINIS^LN` | `KLINIS` |
| `59780-7^HASIL^LN` | `HASIL` |
| `19005-8^KESAN^LN` | `KESAN` |
| `18783-1^USUL^LN` | `USUL` |

`OBR-3` = `RADHL7DEMO1` → `TINDAKAN_MEDIS` (kunci upsert)

### Tengah — JSON

```json
{
  "norm": "77788899",
  "nama": "BAMBANG WAHONO SUHARTO",
  "accession_number": "RADHL7DEMO1",
  "klinis": "Batuk berkepanjangan > 3 minggu. Riwayat kontak TB.",
  "hasil": "Tampak infiltrat di lobus kanan atas. Kavitas +/-2cm.",
  "kesan": "TB Paru aktif dengan kavitas lobus kanan atas.",
  "usul": "Saran pemeriksaan sputum BTA. Konsultasi paru.",
  "pacs_hook_uid": "1.2.840.113619.2.55.3.20261028202315",
  "otomatis": 1
}
```

Dikirim oleh middleware Python:

```http
PUT /api/v1/radiologi/pacs-bridging/RADHL7DEMO1
X-Api-Key: <ISI_API_KEY>
Content-Type: application/json
```

Diterima oleh PHP (`simgos-api/api/pacs_bridging.php`):

```php
// baris 72 — method harus PUT
if ($_SERVER['REQUEST_METHOD'] !== 'PUT') {
    sendResponse(405, false, 'Method Not Allowed. Gunakan HTTP PUT.');
}

// baris 77-86 — cek API key dari header X-Api-Key atau Authorization: Bearer
$apiKeyHeader = $_SERVER['HTTP_X_API_KEY']
    ?? $_SERVER['HTTP_AUTHORIZATION']
    ?? '';
if (str_starts_with($apiKeyHeader, 'Bearer ')) {
    $apiKeyHeader = substr($apiKeyHeader, 7);
}
if (empty($apiKeyHeader) || $apiKeyHeader !== API_KEY_VALID) {
    sendResponse(401, false, 'Unauthorized. API Key tidak valid.');
}

// baris 139-143 — baca body JSON
$rawBody = file_get_contents('php://input');
$payload = json_decode($rawBody, true);

// baris 254-282 — transaksi
$pdo->beginTransaction();
$hasilModel = new HasilRadModel();
$hasilModel->upsert($cleanHasil);
$pdo->commit();

// baris 301 — balas JSON 200 ke middleware
sendResponse(200, true, 'Data PACS berhasil disinkronkan ke database SIMGOS.');
```

Kalau API key salah, PHP membalas `401` dan **tidak** menulis apa pun ke
database. Kalau body kosong, `400`. Keduanya terjadi sebelum
`beginTransaction()`.

### Keluar — MySQL

JSON di atas diubah menjadi SQL oleh `HasilRadModel::upsert()`
(`simgos-api/models/HasilRadModel.php:68`). Nama tabel dan kolom diambil dari
`simgos-api/config/schema_map.php`, bukan hardcoded.

```php
// HasilRadModel.php:102-126 — susun kolom & nilai dari payload
foreach ($this->cols as $alias => $colName) {
    if (!isset($data[$alias]) || $data[$alias] === null) {
        continue;                          // field null dilewati
    }
    $insertCols[]   = "`{$colName}`";
    $insertVals[]   = ':ins_' . $alias;
    $params['ins_' . $alias] = $data[$alias];

    if ($colName === $this->uniqueKey) {
        continue;                          // TINDAKAN_MEDIS tidak di-update
    }
    if ($alias === 'pacs_hook_uid') {
        // DIKIRIM_DARI: jangan timpa dengan string kosong
        $updateParts[] = "`{$colName}` = IF(VALUES(`{$colName}`) != '' AND VALUES(`{$colName}`) IS NOT NULL, VALUES(`{$colName}`), `{$colName}`)";
    } elseif ($alias === 'otomatis') {
        $updateParts[] = "`{$colName}` = 1";
    } else {
        $updateParts[] = "`{$colName}` = VALUES(`{$colName}`)";
    }
}

// HasilRadModel.php:132-141 — jalankan
$sql = sprintf(
    "INSERT INTO %s (%s) VALUES (%s) ON DUPLICATE KEY UPDATE %s",
    $this->table, implode(', ', $insertCols),
    implode(', ', $insertVals), implode(', ', $updateParts)
);
$stmt = $this->db->prepare($sql);
return $stmt->execute($params);
```

SQL yang benar-benar dikirim ke MySQL:

```sql
INSERT INTO `layanan`.`hasil_rad`
  (`TINDAKAN_MEDIS`, `TANGGAL`, `KLINIS`, `KESAN`, `USUL`, `HASIL`,
   `BTK`, `KRITIS`, `DOKTER`, `OTOMATIS`, `DIKIRIM_DARI`, `OLEH`, `STATUS`)
VALUES
  (:ins_accession_number, :ins_tanggal_pemeriksaan, :ins_klinis, :ins_kesan,
   :ins_usul, :ins_hasil, :ins_btk, :ins_kritis, :ins_dokter_id,
   :ins_otomatis, :ins_pacs_hook_uid, :ins_oleh, :ins_status)
ON DUPLICATE KEY UPDATE
  `TANGGAL`      = VALUES(`TANGGAL`),
  `KLINIS`       = VALUES(`KLINIS`),
  `KESAN`        = VALUES(`KESAN`),
  `USUL`         = VALUES(`USUL`),
  `HASIL`        = VALUES(`HASIL`),
  `BTK`          = VALUES(`BTK`),
  `KRITIS`       = VALUES(`KRITIS`),
  `DOKTER`       = VALUES(`DOKTER`),
  `OTOMATIS`     = 1,
  `DIKIRIM_DARI` = IF(VALUES(`DIKIRIM_DARI`) != '' AND VALUES(`DIKIRIM_DARI`) IS NOT NULL,
                      VALUES(`DIKIRIM_DARI`), `DIKIRIM_DARI`),
  `OLEH`         = VALUES(`OLEH`),
  `STATUS`       = VALUES(`STATUS`);
```

`ON DUPLICATE KEY UPDATE` aktif karena `TINDAKAN_MEDIS` punya `UNIQUE KEY`.
Accession sama dikirim 2× → baris lama diperbarui, `ID` tidak berubah.

Hasil di `layanan.hasil_rad`:

| Kolom | Nilai |
|---|---|
| `TINDAKAN_MEDIS` | `RADHL7DEMO1` |
| `HASIL` | `Tampak infiltrat di lobus kanan atas. Kavitas +/-2cm.` |
| `KESAN` | `TB Paru aktif dengan kavitas lobus kanan atas.` |
| `DIKIRIM_DARI` | `1.2.840.113619.2.55.3.20261028202315` |
| `OTOMATIS` | 1 |

---

## Satu Nilai, Semua Tahap

| Tahap | Bentuk |
|---|---|
| HL7 | `OBX\|2\|TX\|59780-7^HASIL^LN\|\|Tampak infiltrat di lobus kanan atas.` |
| Parse | `HL7Segment("OBX\|2\|TX\|59780-7^HASIL^LN\|\|Tampak infiltrat...")` |
| Konversi | `payload.hasil = "Tampak infiltrat di lobus kanan atas."` |
| JSON | `"hasil": "Tampak infiltrat di lobus kanan atas."` |
| MySQL | `hasil_rad.HASIL = "Tampak infiltrat di lobus kanan atas."` |

Nilainya tidak berubah. Hanya dibungkus ulang.

---

## ACK Balik ke PACS

```
MSA|AA|MSG-20261028203257-33780|Processed successfully
```

- `MSA-1` = `AA` sukses, `AE` gagal, `AR` ditolak
- `MSA-2` = `MSH-10` pesan asli, jadi PACS tahu pesan mana yang dijawab

Tanpa data pasien. Hanya status.

---

## Kenapa Lewat JSON + HTTP, Bukan Langsung MySQL

- Python tidak punya kredensial database dan tidak tahu nama tabel
- PHP yang pegang keduanya: `config/database.php` + `config/schema_map.php`
- Ganti nama kolom = edit `schema_map.php` saja
- Middleware bisa dipindah ke server lain
- API key jadi satu-satunya jalur masuk

---

## Kirim Ulang = UPDATE, Bukan Duplikat

`TINDAKAN_MEDIS` punya `UNIQUE KEY`. Accession sama dikirim 2× → baris lama
diperbarui, bukan baris baru. `ID` tetap.

```
ID 286  RADHL7DEMO1  HASIL "versi 1"    <- kiriman pertama
ID 286  RADHL7DEMO1  HASIL "versi 2"    <- kiriman kedua, ID sama
```

---

## Verifikasi

```bash
python testing/check_connection.py
python testing/put_hl7.py --watch --norm 77788899 --acc RADHL7DEMO1
python testing/check_db.py --acc RADHL7DEMO1
```

`--watch` menampilkan diff kolom sebelum dan sesudah PUT.

---

## Catatan

Payload harus **lengkap**. Field yang tidak dikirim menjadi `null` dan
mengosongkan kolom lama (`pacs_bridging.php:225-238`). Untuk mengubah satu
kolom, salin dulu nilai kolom lain dari database.
