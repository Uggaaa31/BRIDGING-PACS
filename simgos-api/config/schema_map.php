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
    // =========================================================================
    'hasil_rad' => [
        'table'       => 'hasil_rad',
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
    // =========================================================================
    'hl7_logs' => [
        'table'       => 'hl7_logs',
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
