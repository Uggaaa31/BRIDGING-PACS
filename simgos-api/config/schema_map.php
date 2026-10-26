<?php
/**
 * schema_map.php - ★ KONFIGURASI MAPPING SKEMA DATABASE SIMGOS
 * ============================================================
 * Disesuaikan dengan DDL asli database SIMGOS Anda:
 *
 * Database  : layanan   → Tabel hasil_rad
 * Database  : ris       → Tabel hl7_logs (log transaksi HL7 PACS)
 *
 * CATATAN PENTING:
 *   - layanan.hasil_rad TIDAK memiliki kolom PACS_HOOK_UID / PACS_MODALITY.
 *     StudyInstanceUID PACS disimpan di kolom DIKIRIM_DARI (char 36, oAuth2 ID).
 *   - DOKTER adalah kolom smallint (ID dokter, bukan nama).
 *   - OLEH adalah smallint (ID user yang input).
 *   - ris.hl7_logs digunakan untuk audit trail setiap pesan HL7.
 */

return [

    // =========================================================================
    // TABEL 1: Data Master Pasien
    // (Sesuaikan jika Anda memiliki DDL master.pasien)
    // =========================================================================
    'pasien' => [
        'table'       => 'master.pasien',
        'columns'     => [
            'norm'          => 'NORM',
            'nama'          => 'NAMA',
            'tanggal_lahir' => 'TANGGAL_LAHIR',
            'jenis_kelamin' => 'JENIS_KELAMIN',
            'status'        => 'STATUS',
        ],
        'primary_key' => 'NORM',
    ],

    // =========================================================================
    // TABEL 2: Order / Tindakan Medis Radiologi
    // (Sesuaikan jika Anda memiliki DDL layanan.tindakan_medis)
    // =========================================================================
    'tindakan' => [
        'table'       => 'layanan.tindakan_medis',
        'columns'     => [
            'accession_number'    => 'ID',
            'norm'                => 'NORM',
            'kunjungan_id'        => 'KUNJUNGAN',
            'kode_tindakan'       => 'TINDAKAN',
            'nama_tindakan'       => 'NAMA_TINDAKAN',
            'tanggal_pemeriksaan' => 'TANGGAL',
            'dokter_pengirim'     => 'DOKTER_PENGIRIM',
            'status'              => 'STATUS',
        ],
        'primary_key' => 'ID',
    ],

    // =========================================================================
    // TABEL 3: Hasil Pemeriksaan Radiologi
    // Database : layanan
    // DDL      : hasil_rad.sql
    //
    // KOLOM ASLI:
    //   ID             → AUTO_INCREMENT (tidak perlu di-set manual)
    //   TINDAKAN_MEDIS → char(11) UNIQUE — ID pemeriksaan/accession
    //   TANGGAL        → datetime
    //   KLINIS         → text
    //   KESAN          → text
    //   USUL           → text
    //   HASIL          → longtext
    //   BTK            → text (Bacaan Tambahan/Keterangan)
    //   KRITIS         → tinyint (0=Kritis, 1=Tidak Kritis)
    //   DOKTER         → smallint (ID dokter pembaca — bukan nama!)
    //   OTOMATIS       → tinyint (1 = diisi otomatis via bridging)
    //   DIKIRIM_DARI   → char(36) ★ Digunakan untuk menyimpan StudyInstanceUID PACS
    //   OLEH           → smallint (ID user yang melakukan input)
    //   STATUS         → tinyint (1=Aktif, 0=Batal)
    // =========================================================================
    'hasil_rad' => [
        'table'       => 'layanan.hasil_rad',
        'columns'     => [
            'accession_number'    => 'TINDAKAN_MEDIS',  // char(11) UNIQUE KEY
            'tanggal_pemeriksaan' => 'TANGGAL',
            'klinis'              => 'KLINIS',
            'kesan'               => 'KESAN',
            'usul'                => 'USUL',
            'hasil'               => 'HASIL',
            'btk'                 => 'BTK',              // Bacaan Tambahan/Keterangan
            'kritis'              => 'KRITIS',           // 0=Kritis, 1=Tidak Kritis
            'dokter_id'           => 'DOKTER',           // ★ smallint ID dokter
            'otomatis'            => 'OTOMATIS',         // 1 = via bridging PACS
            'pacs_hook_uid'       => 'DIKIRIM_DARI',     // ★ char(36) → StudyInstanceUID
            'oleh'                => 'OLEH',             // smallint ID user
            'status'              => 'STATUS',
        ],
        // UNIQUE KEY (bukan PRIMARY KEY) yang digunakan untuk ON DUPLICATE KEY UPDATE
        // PRIMARY KEY = ID (AUTO_INCREMENT), tapi identifier uniknya TINDAKAN_MEDIS
        'primary_key'   => 'ID',
        'unique_key'    => 'TINDAKAN_MEDIS',
    ],

    // =========================================================================
    // TABEL 4: Log Transaksi HL7 PACS
    // Database : ris
    // DDL      : hl7_logs.sql
    //
    // KOLOM ASLI:
    //   ID          → AUTO_INCREMENT
    //   REF_ID      → char(11) — ID referensi (accession/tindakan)
    //   JENIS       → tinyint (0=ORM, 1=ORU)
    //   PROVIDER    → varchar(50) — nama PACS/provider
    //   KODE        → char(10) — kode tindakan
    //   TGL_REQUEST → datetime (auto = CURRENT_TIMESTAMP)
    //   REQUEST     → text — raw pesan HL7 yang diterima
    //   TGL_RESPONSE→ datetime (ON UPDATE CURRENT_TIMESTAMP)
    //   RESPONSE    → text — ACK yang dikirim balik ke PACS
    //   KIRIM       → tinyint (0=Belum, 1=Terkirim)
    //   STATUS      → tinyint (1=Aktif, 0=Batal)
    // =========================================================================
    'hl7_logs' => [
        'table'       => 'ris.hl7_logs',
        'columns'     => [
            'ref_id'       => 'REF_ID',
            'jenis'        => 'JENIS',
            'provider'     => 'PROVIDER',
            'kode'         => 'KODE',
            'tgl_request'  => 'TGL_REQUEST',
            'request'      => 'REQUEST',
            'tgl_response' => 'TGL_RESPONSE',
            'response'     => 'RESPONSE',
            'kirim'        => 'KIRIM',
            'status'       => 'STATUS',
        ],
        'primary_key' => 'ID',
        'unique_key'  => 'REF_ID_JENIS',  // UNIQUE KEY (REF_ID, JENIS, PROVIDER)
    ],

];
