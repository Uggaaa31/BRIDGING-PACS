<?php
/**
 * PasienModel.php - Model CRUD untuk Tabel Master Pasien
 * =======================================================
 * Menggunakan schema_map.php untuk nama tabel dan kolom.
 * Implementasi Idempotent dengan INSERT ... ON DUPLICATE KEY UPDATE.
 */

require_once __DIR__ . '/../config/database.php';

class PasienModel {
    private PDO   $db;
    private array $map;   // Konfigurasi dari schema_map.php
    private string $table;
    private array  $cols;
    private string $pk;

    public function __construct() {
        $this->db    = Database::getInstance();
        $schema      = require __DIR__ . '/../config/schema_map.php';
        $this->map   = $schema['pasien'];
        $this->table = $this->map['table'];
        $this->cols  = $this->map['columns'];
        $this->pk    = $this->map['primary_key'];
    }

    /**
     * Upsert data pasien (INSERT jika baru, UPDATE jika sudah ada).
     * Menggunakan ON DUPLICATE KEY UPDATE untuk idempotency.
     *
     * @param  array $data  Associative array dengan alias internal sebagai key.
     * @return bool         True jika berhasil.
     * @throws PDOException
     */
    public function upsert(array $data): bool {
        $insertCols = [];
        $insertVals = [];
        $updateParts = [];
        $params = [];

        foreach ($this->cols as $alias => $colName) {
            if (!isset($data[$alias]) || $data[$alias] === null) {
                continue;
            }
            $placeholder = ':' . $alias;
            $insertCols[]  = "`{$colName}`";
            $insertVals[]  = $placeholder;
            $params[$alias] = $data[$alias];

            // PRIMARY KEY tidak dimasukkan ke UPDATE
            if ($colName !== $this->pk) {
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
     * Cek apakah pasien dengan NORM tertentu sudah ada.
     */
    public function existsByNorm(string $norm): bool {
        $col = $this->cols['norm'];
        $sql = "SELECT COUNT(*) FROM {$this->table} WHERE `{$col}` = :norm LIMIT 1";
        $stmt = $this->db->prepare($sql);
        $stmt->execute(['norm' => $norm]);
        return (int) $stmt->fetchColumn() > 0;
    }
}
