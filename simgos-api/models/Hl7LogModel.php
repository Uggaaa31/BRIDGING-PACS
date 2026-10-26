<?php
/**
 * Hl7LogModel.php - Model untuk Tabel ris.hl7_logs
 * ==================================================
 * Mencatat setiap transaksi HL7 yang diterima dari PACS ke tabel hl7_logs.
 * Tabel ini adalah audit trail resmi sistem RIS untuk aktivitas bridging.
 *
 * Struktur tabel ris.hl7_logs:
 *   ID           INT AUTO_INCREMENT PRIMARY KEY
 *   REF_ID       CHAR(11)    — ID referensi (accession / TINDAKAN_MEDIS)
 *   JENIS        TINYINT     — 0=ORM, 1=ORU
 *   PROVIDER     VARCHAR(50) — Nama PACS / provider pengirim
 *   KODE         CHAR(10)    — Kode tindakan radiologi
 *   TGL_REQUEST  DATETIME    — Auto = CURRENT_TIMESTAMP
 *   REQUEST      TEXT        — Raw pesan HL7 yang diterima
 *   TGL_RESPONSE DATETIME    — ON UPDATE CURRENT_TIMESTAMP
 *   RESPONSE     TEXT        — Pesan ACK yang dikirim ke PACS
 *   KIRIM        TINYINT     — 0=Belum Terkirim, 1=Terkirim
 *   STATUS       TINYINT     — 0=Batal, 1=Aktif
 *
 * UNIQUE KEY: (REF_ID, JENIS, PROVIDER)
 * Artinya: satu accession + jenis pesan + provider = satu entri unik.
 */

require_once __DIR__ . '/../config/database.php';

class Hl7LogModel {
    private PDO    $db;
    private string $table;
    private array  $cols;

    // Konstanta JENIS pesan HL7
    const JENIS_ORM = 0;  // Order Message
    const JENIS_ORU = 1;  // Observation Result (hasil radiologi)

    // Konstanta STATUS KIRIM
    const KIRIM_BELUM = 0;
    const KIRIM_SUDAH = 1;

    public function __construct() {
        $this->db    = Database::getInstance();
        $schema      = require __DIR__ . '/../config/schema_map.php';
        $cfg         = $schema['hl7_logs'];
        $this->table = $cfg['table'];
        $this->cols  = $cfg['columns'];
    }

    /**
     * Catat pesan HL7 baru ke tabel hl7_logs.
     *
     * @param  string $refId     REF_ID — accession number / ID tindakan (max 11 char)
     * @param  int    $jenis     0=ORM, 1=ORU
     * @param  string $provider  Nama PACS / sistem pengirim (max 50 char)
     * @param  string $kode      Kode tindakan (max 10 char)
     * @param  string $request   Raw pesan HL7 yang diterima
     * @return int|false         ID baris yang baru diinsert, atau false jika gagal
     */
    public function insert(
        string $refId,
        int    $jenis,
        string $provider,
        string $kode,
        string $request
    ): int|false {
        $c = $this->cols;

        $sql = "INSERT INTO {$this->table}
                    (`{$c['ref_id']}`, `{$c['jenis']}`, `{$c['provider']}`,
                     `{$c['kode']}`, `{$c['request']}`, `{$c['kirim']}`, `{$c['status']}`)
                VALUES
                    (:ref_id, :jenis, :provider, :kode, :request, :kirim, :status)
                ON DUPLICATE KEY UPDATE
                    `{$c['request']}`      = VALUES(`{$c['request']}`),
                    `{$c['kirim']}`        = 0,
                    `{$c['tgl_response']}` = NULL,
                    `{$c['response']}`     = ''";

        $stmt = $this->db->prepare($sql);
        $ok   = $stmt->execute([
            'ref_id'   => substr($refId, 0, 11),
            'jenis'    => $jenis,
            'provider' => substr($provider, 0, 50),
            'kode'     => substr($kode, 0, 10),
            'request'  => $request,
            'kirim'    => self::KIRIM_BELUM,
            'status'   => 1,
        ]);

        if ($ok) {
            $lastId = (int)$this->db->lastInsertId();
            // Jika ON DUPLICATE KEY UPDATE terpicu, lastInsertId() bisa 0
            // Fallback: cari ID berdasarkan REF_ID + JENIS + PROVIDER
            if ($lastId === 0) {
                $lastId = $this->findId($refId, $jenis, $provider) ?? 0;
            }
            return $lastId ?: false;
        }

        return false;
    }

    /**
     * Update kolom RESPONSE dan TGL_RESPONSE setelah ACK dikirim ke PACS.
     * Juga update KIRIM = 1 jika pengiriman berhasil.
     *
     * @param  int    $logId    ID baris di hl7_logs
     * @param  string $response Isi ACK yang dikirim ke PACS
     * @param  bool   $berhasil True jika ACK AA (sukses), false jika AE
     * @return bool
     */
    public function updateResponse(int $logId, string $response, bool $berhasil = true): bool {
        $c = $this->cols;

        $sql = "UPDATE {$this->table}
                SET `{$c['response']}`     = :response,
                    `{$c['tgl_response']}` = NOW(),
                    `{$c['kirim']}`        = :kirim
                WHERE `ID` = :id
                LIMIT 1";

        $stmt = $this->db->prepare($sql);
        return $stmt->execute([
            'response' => substr($response, 0, 65535),  // Batasi TEXT
            'kirim'    => $berhasil ? self::KIRIM_SUDAH : self::KIRIM_BELUM,
            'id'       => $logId,
        ]);
    }

    /**
     * Cari ID log berdasarkan REF_ID + JENIS + PROVIDER (sesuai UNIQUE KEY).
     */
    public function findId(string $refId, int $jenis, string $provider): ?int {
        $c = $this->cols;

        $sql = "SELECT `ID` FROM {$this->table}
                WHERE `{$c['ref_id']}` = :ref_id
                  AND `{$c['jenis']}`  = :jenis
                  AND `{$c['provider']}` = :provider
                LIMIT 1";

        $stmt = $this->db->prepare($sql);
        $stmt->execute([
            'ref_id'   => substr($refId, 0, 11),
            'jenis'    => $jenis,
            'provider' => substr($provider, 0, 50),
        ]);
        $row = $stmt->fetchColumn();
        return $row !== false ? (int)$row : null;
    }
}
