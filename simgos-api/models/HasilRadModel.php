<?php
/**
 * HasilRadModel.php - Model CRUD untuk Tabel layanan.hasil_rad
 * =============================================================
 * Disesuaikan dengan DDL asli layanan.hasil_rad:
 *
 *   ID             INT AUTO_INCREMENT PRIMARY KEY
 *   TINDAKAN_MEDIS CHAR(11) UNIQUE KEY  ← identifier unik pemeriksaan
 *   TANGGAL        DATETIME
 *   KLINIS         TEXT
 *   KESAN          TEXT
 *   USUL           TEXT
 *   HASIL          LONGTEXT
 *   BTK            TEXT               (Bacaan Tambahan/Keterangan)
 *   KRITIS         TINYINT            (0=Kritis, 1=Tidak Kritis)
 *   DOKTER         SMALLINT           (ID dokter — bukan nama string!)
 *   OTOMATIS       TINYINT            (1 = diisi otomatis via bridging)
 *   DIKIRIM_DARI   CHAR(36)           ★ Dipakai untuk menyimpan StudyInstanceUID PACS
 *   OLEH           SMALLINT           (ID user)
 *   STATUS         TINYINT
 *
 * Strategi UPSERT:
 *   Karena PRIMARY KEY adalah ID (AUTO_INCREMENT) dan identifier uniknya
 *   adalah TINDAKAN_MEDIS (UNIQUE KEY), kita gunakan:
 *   INSERT ... ON DUPLICATE KEY UPDATE
 *   MySQL akan mendeteksi konflik pada UNIQUE KEY TINDAKAN_MEDIS
 *   dan melakukan UPDATE alih-alih INSERT.
 */

require_once __DIR__ . '/../config/database.php';

class HasilRadModel {
    private PDO    $db;
    private string $table;
    private array  $cols;
    private string $uniqueKey;

    public function __construct() {
        $this->db       = Database::getInstance();
        $schema         = require __DIR__ . '/../config/schema_map.php';
        $cfg            = $schema['hasil_rad'];
        $this->table    = $cfg['table'];
        $this->cols     = $cfg['columns'];
        $this->uniqueKey = $cfg['unique_key'];  // 'TINDAKAN_MEDIS'
    }

    /**
     * Upsert hasil pemeriksaan radiologi.
     *
     * Nilai default yang selalu di-set untuk data yang masuk via bridging:
     *   OTOMATIS    = 1  (tandai sebagai data otomatis dari PACS)
     *   KRITIS      = 1  (default: tidak kritis)
     *   STATUS      = 1  (aktif)
     *
     * Kolom DIKIRIM_DARI (alias pacs_hook_uid):
     *   Hanya diupdate jika nilai baru tidak kosong.
     *   Ini mencegah StudyInstanceUID yang sudah ada tertimpa data kosong
     *   saat bridging mengirim ulang pesan tanpa gambar.
     *
     * Kolom DOKTER & OLEH:
     *   Bertipe SMALLINT (ID integer). Jika payload tidak memberikan
     *   ID numerik dokter, akan diisi 0 (unknown).
     *
     * @param  array $data  Payload dari pacs_bridging.php (sudah disanitasi).
     * @return bool         True jika INSERT/UPDATE berhasil.
     * @throws PDOException
     */
    public function upsert(array $data): bool {
        // ── Paksa nilai default untuk data bridging otomatis ─────────────────
        $data['otomatis'] = 1;                         // Selalu 1 = via bridging
        $data['kritis']   = $data['kritis'] ?? 1;      // Default: tidak kritis
        $data['status']   = $data['status'] ?? 1;
        $data['oleh']     = $data['oleh'] ?? 0;        // 0 = system/bridging

        // DOKTER harus integer — jika string nama, set ke 0
        if (isset($data['dokter_id'])) {
            $data['dokter_id'] = is_numeric($data['dokter_id'])
                ? (int)$data['dokter_id']
                : 0;
        } else {
            $data['dokter_id'] = 0;
        }

        // ── Kolom teks: nilai kosong/null tidak dikirim sama sekali ───────────
        // INSERT memakai default kolom; UPDATE melewati kolom ini sehingga teks
        // laporan yang sudah ada tidak pernah tertimpa string kosong.
        foreach (['klinis', 'kesan', 'usul', 'hasil', 'btk'] as $textCol) {
            if (empty($data[$textCol])) {
                unset($data[$textCol]);
            }
        }

        // ── Batasi DIKIRIM_DARI (char 36) hanya 36 karakter ──────────────────
        if (!empty($data['pacs_hook_uid'])) {
            $data['pacs_hook_uid'] = substr($data['pacs_hook_uid'], 0, 36);
        }

        // ── Build INSERT ... ON DUPLICATE KEY UPDATE ──────────────────────────
        $insertCols  = [];
        $insertVals  = [];
        $updateParts = [];
        $params      = [];

        foreach ($this->cols as $alias => $colName) {
            if (!isset($data[$alias]) || $data[$alias] === null) {
                continue;
            }
            $insertCols[]   = "`{$colName}`";
            $insertVals[]   = ':ins_' . $alias;
            $params['ins_' . $alias] = $data[$alias];

            // ── Logika UPDATE per-kolom ───────────────────────────────────────
            if ($colName === $this->uniqueKey) {
                // TINDAKAN_MEDIS adalah UNIQUE KEY — tidak diupdate
                continue;
            }

            if ($alias === 'pacs_hook_uid') {
                // DIKIRIM_DARI: hanya update jika nilai baru tidak kosong
                // Fungsi SQL IF() mencegah penimpaan dengan string kosong
                $updateParts[] = "`{$colName}` = IF(VALUES(`{$colName}`) != '' AND VALUES(`{$colName}`) IS NOT NULL, VALUES(`{$colName}`), `{$colName}`)";
            } elseif (in_array($alias, ['klinis', 'kesan', 'usul', 'hasil', 'btk'], true)) {
                // Teks laporan: hanya timpa jika nilai baru tidak kosong
                $updateParts[] = "`{$colName}` = IF(VALUES(`{$colName}`) != '', VALUES(`{$colName}`), `{$colName}`)";
            } elseif ($alias === 'otomatis') {
                // OTOMATIS: tetap 1 jika sudah 1, tidak perlu diupdate terus
                $updateParts[] = "`{$colName}` = 1";
            } else {
                $updateParts[] = "`{$colName}` = VALUES(`{$colName}`)";
            }
        }

        if (empty($insertCols)) {
            return false;
        }

        $sql = sprintf(
            "INSERT INTO %s (%s) VALUES (%s) ON DUPLICATE KEY UPDATE %s",
            $this->table,
            implode(', ', $insertCols),
            implode(', ', $insertVals),
            implode(', ', $updateParts)
        );

        $stmt = $this->db->prepare($sql);
        return $stmt->execute($params);
    }

    /**
     * Update HANYA kolom DIKIRIM_DARI (StudyInstanceUID PACS)
     * berdasarkan TINDAKAN_MEDIS (accession number).
     *
     * Digunakan saat PACS mengirim pesan HL7 yang hanya berisi
     * informasi gambar (tanpa hasil klinis lengkap).
     */
    public function updateStudyUid(string $accessionNumber, string $studyUid): bool {
        $tindakanCol   = $this->cols['accession_number'];  // TINDAKAN_MEDIS
        $dikirimDariCol = $this->cols['pacs_hook_uid'];     // DIKIRIM_DARI

        $sql = "UPDATE {$this->table}
                SET `{$dikirimDariCol}` = :study_uid
                WHERE `{$tindakanCol}` = :acc
                LIMIT 1";

        $stmt = $this->db->prepare($sql);
        return $stmt->execute([
            'study_uid' => substr($studyUid, 0, 36),
            'acc'       => $accessionNumber,
        ]);
    }

    /**
     * Cek apakah hasil untuk accession number tertentu sudah ada.
     */
    public function existsByAccession(string $accessionNumber): bool {
        $col = $this->cols['accession_number'];
        $sql = "SELECT COUNT(*) FROM {$this->table} WHERE `{$col}` = :acc LIMIT 1";
        $stmt = $this->db->prepare($sql);
        $stmt->execute(['acc' => $accessionNumber]);
        return (int)$stmt->fetchColumn() > 0;
    }
}
