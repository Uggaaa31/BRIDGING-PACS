<?php
/**
 * database.php - Koneksi PDO MySQL/MariaDB untuk SIMGOS
 * ======================================================
 * Menyediakan koneksi PDO tunggal (singleton pattern) ke database SIMGOS.
 * Konfigurasi dapat di-override via environment variable untuk production.
 */

// ─── Konfigurasi Database ────────────────────────────────────────────────────
define('DB_HOST',    getenv('SIMGOS_DB_HOST')    ?: '127.0.0.1');
define('DB_PORT',    getenv('SIMGOS_DB_PORT')    ?: '3306');
$envDb = getenv('SIMGOS_DB_NAME');
define('DB_NAME', $envDb !== false ? trim($envDb) : '');
define('DB_USER',    getenv('SIMGOS_DB_USER')    ?: 'root');
define('DB_PASS',    getenv('SIMGOS_DB_PASS')    ?: '');
define('DB_CHARSET', 'utf8mb4');

// ─── Singleton PDO Connection ─────────────────────────────────────────────────
class Database {
    private static ?PDO $instance = null;

    /**
     * Mengembalikan instance PDO (buat baru jika belum ada).
     */
    public static function getInstance(): PDO {
        if (self::$instance === null) {
            $dbPart = DB_NAME !== '' ? sprintf('dbname=%s;', DB_NAME) : '';
            $dsn = sprintf(
                'mysql:host=%s;port=%s;%scharset=%s',
                DB_HOST, DB_PORT, $dbPart, DB_CHARSET
            );

            $options = [
                PDO::ATTR_ERRMODE            => PDO::ERRMODE_EXCEPTION,
                PDO::ATTR_DEFAULT_FETCH_MODE => PDO::FETCH_ASSOC,
                PDO::ATTR_EMULATE_PREPARES   => false,
                PDO::MYSQL_ATTR_INIT_COMMAND => "SET NAMES utf8mb4 COLLATE utf8mb4_unicode_ci",
                PDO::ATTR_TIMEOUT            => 5,
            ];

            try {
                self::$instance = new PDO($dsn, DB_USER, DB_PASS, $options);
            } catch (PDOException $e) {
                // Jangan expose detail koneksi ke response publik
                error_log('[SIMGOS-API] Database connection failed: ' . $e->getMessage());
                http_response_code(503);
                header('Content-Type: application/json');
                echo json_encode([
                    'success' => false,
                    'message' => 'Database service unavailable.',
                    'code'    => 503,
                ]);
                exit;
            }
        }

        return self::$instance;
    }

    // Cegah instantiasi langsung
    private function __construct() {}
    private function __clone() {}
}
