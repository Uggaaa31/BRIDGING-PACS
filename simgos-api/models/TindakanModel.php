<?php
/**
 * TindakanModel.php - Model CRUD untuk Tabel Order/Tindakan Radiologi
 * ====================================================================
 */

require_once __DIR__ . '/../config/database.php';

class TindakanModel {
    private PDO    $db;
    private string $table;
    private array  $cols;
    private string $pk;

    public function __construct() {
        $this->db    = Database::getInstance();
        $schema      = require __DIR__ . '/../config/schema_map.php';
        $cfg         = $schema['tindakan'];
        $this->table = $cfg['table'];
        $this->cols  = $cfg['columns'];
        $this->pk    = $cfg['primary_key'];
    }

    /**
     * Upsert data tindakan/order radiologi.
     * Idempotent: pengiriman ulang accession yang sama tidak duplikat.
     */
    public function upsert(array $data): bool {
        $insertCols  = [];
        $insertVals  = [];
        $updateParts = [];
        $params      = [];

        foreach ($this->cols as $alias => $colName) {
            if (!isset($data[$alias]) || $data[$alias] === null) {
                continue;
            }
            $insertCols[]   = "`{$colName}`";
            $insertVals[]   = ':' . $alias;
            $params[$alias] = $data[$alias];

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
}
