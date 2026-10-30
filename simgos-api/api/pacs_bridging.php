<?php
/**
 * pacs_bridging.php - Endpoint REST API PUT /api/v1/radiologi/pacs-bridging/{id}
 * ================================================================================
 * Menerima payload JSON dari Middleware Bridging Python dan menyimpan/memperbarui
 * data ke database SIMGOS.
 *
 * Disesuaikan dengan DDL asli:
 *   - layanan.hasil_rad  : DOKTER=smallint, DIKIRIM_DARI=char(36) untuk StudyUID
 *   - ris.hl7_logs       : Audit trail setiap transaksi HL7 yang diterima
 *
 * Alur:
 *   1. Validasi API Key
 *   2. Insert ke ris.hl7_logs (REQUEST = raw payload, KIRIM=0)
 *   3. Validasi & sanitasi payload JSON
 *   4. Upsert layanan.hasil_rad (+ opsional master.pasien & layanan.tindakan_medis)
 *   5. Update ris.hl7_logs (RESPONSE = hasil, KIRIM=1 jika sukses)
 *   6. Kirim response JSON
 */

// ─── Bootstrap ───────────────────────────────────────────────────────────────
header('Content-Type: application/json; charset=utf-8');
header('X-Powered-By: SIMGOS-PACS-Bridge/1.0');

require_once __DIR__ . '/../config/database.php';
require_once __DIR__ . '/../models/PasienModel.php';
require_once __DIR__ . '/../models/TindakanModel.php';
require_once __DIR__ . '/../models/HasilRadModel.php';
require_once __DIR__ . '/../models/Hl7LogModel.php';

// ─── Konfigurasi ─────────────────────────────────────────────────────────────
// Tidak ada nilai default: API key wajib diisi lewat environment (fail-closed).
define('API_KEY_VALID',  getenv('SIMGOS_API_KEY') ?: '');
define('PACS_PROVIDER',  getenv('PACS_PROVIDER_NAME') ?: 'PACS');
define('API_LOG_FILE',   __DIR__ . '/../logs/simgos_api.log');

// ─── Helper Functions ─────────────────────────────────────────────────────────

function sendResponse(int $code, bool $success, string $message, array $data = []): void {
    http_response_code($code);
    echo json_encode([
        'success'   => $success,
        'code'      => $code,
        'message'   => $message,
        'data'      => $data,
        'timestamp' => date('Y-m-d H:i:s'),
    ], JSON_UNESCAPED_UNICODE | JSON_PRETTY_PRINT);
    exit;
}

function writeLog(string $level, string $message, array $context = []): void {
    $dir = dirname(API_LOG_FILE);
    if (!is_dir($dir)) {
        mkdir($dir, 0755, true);
    }
    $ctx  = empty($context) ? '' : ' | ' . json_encode($context, JSON_UNESCAPED_UNICODE);
    $line = sprintf("[%s] [%s] %s%s\n", date('Y-m-d H:i:s'), strtoupper($level), $message, $ctx);
    file_put_contents(API_LOG_FILE, $line, FILE_APPEND | LOCK_EX);
}

function sanitizeString(?string $val, int $maxLen = 500): ?string {
    if ($val === null) return null;
    $val = trim($val);
    if ($val === '') return null;
    return mb_substr($val, 0, $maxLen, 'UTF-8');
}

function sanitizeInt(mixed $val, int $default = 0): int {
    return is_numeric($val) ? (int)$val : $default;
}

// ─── 1. Validasi Metode HTTP ──────────────────────────────────────────────────
if ($_SERVER['REQUEST_METHOD'] !== 'PUT') {
    sendResponse(405, false, 'Method Not Allowed. Gunakan HTTP PUT.');
}

// ─── 2. Validasi Autentikasi API Key ─────────────────────────────────────────
// Fail-closed: server tanpa API key terkonfigurasi tidak menerima request apa pun.
if (API_KEY_VALID === '') {
    writeLog('error', 'SIMGOS_API_KEY belum dikonfigurasi — request ditolak', [
        'ip' => $_SERVER['REMOTE_ADDR'] ?? '?',
    ]);
    sendResponse(503, false, 'Layanan belum dikonfigurasi.');
}

$apiKeyHeader = $_SERVER['HTTP_X_API_KEY']
    ?? $_SERVER['HTTP_AUTHORIZATION']
    ?? '';
if (str_starts_with($apiKeyHeader, 'Bearer ')) {
    $apiKeyHeader = substr($apiKeyHeader, 7);
}
if (empty($apiKeyHeader) || $apiKeyHeader !== API_KEY_VALID) {
    writeLog('warning', 'Unauthorized access', ['ip' => $_SERVER['REMOTE_ADDR'] ?? '?']);
    sendResponse(401, false, 'Unauthorized. API Key tidak valid.');
}

// ─── 3. Ekstrak Identifier dari URL ──────────────────────────────────────────
$requestUri = $_SERVER['REQUEST_URI'] ?? '';
$urlParts   = explode('/', trim(parse_url($requestUri, PHP_URL_PATH), '/'));
$identifier = urldecode(end($urlParts));
if (empty($identifier) || $identifier === 'pacs-bridging') {
    sendResponse(400, false, 'Identifier (accession number) wajib ada di URL.');
}

function parseReportSections(string $text): array {
    $sections = ['klinis' => '', 'hasil' => '', 'kesan' => '', 'usul' => ''];
    if (empty($text)) return $sections;

    $patterns = [
        'klinis' => '/(?:^|\n)\s*(?:KLINIS|INDIKASI|CLINICAL(?:\s+INFORMATION|\s+INDICATION)?)\s*[:：]\s*/i',
        'hasil'  => '/(?:^|\n)\s*(?:TEMUAN(?:\s+RADIOLOGI|\s+RADIOLOGIS)?|DESKRIPSI|HASIL(?:\s+PEMERIKSAAN)?|FINDINGS)\s*[:：]\s*/i',
        'kesan'  => '/(?:^|\n)\s*(?:KESIMPULAN(?:\s*\/\s*DIAGNOSIS(?:\s+RADILOGIS|\s+RADIOLOGIS)?)?|KESAN|DIAGNOSIS(?:\s+RADILOGIS|\s+RADIOLOGIS)?|IMPRESSION|CONCLUSION)\s*[:：]\s*/i',
        'usul'   => '/(?:^|\n)\s*(?:SARAN(?:\s*\/\s*REKOMENDASI)?|REKOMENDASI|USUL|RECOMMENDATION)\s*[:：]\s*/i',
    ];

    $matches = [];
    foreach ($patterns as $sec => $regex) {
        if (preg_match_all($regex, $text, $m, PREG_OFFSET_CAPTURE)) {
            foreach ($m[0] as $match) {
                $matches[] = [
                    'start' => $match[1],
                    'end'   => $match[1] + strlen($match[0]),
                    'sec'   => $sec
                ];
            }
        }
    }

    if (empty($matches)) {
        $sections['hasil'] = trim($text);
        return $sections;
    }

    usort($matches, fn($a, $b) => $a['start'] <=> $b['start']);

    for ($i = 0; $i < count($matches); $i++) {
        $start = $matches[$i]['end'];
        $next  = ($i + 1 < count($matches)) ? $matches[$i + 1]['start'] : strlen($text);
        $sec   = $matches[$i]['sec'];
        $val   = trim(substr($text, $start, $next - $start));
        $sections[$sec] = empty($sections[$sec]) ? $val : $sections[$sec] . "\n\n" . $val;
    }

    return $sections;
}

// ─── 4. Baca Body JSON ────────────────────────────────────────────────────────
$rawBody = file_get_contents('php://input');
if (empty($rawBody)) {
    sendResponse(400, false, 'Request body kosong.');
}
$payload = json_decode($rawBody, true);
if (json_last_error() !== JSON_ERROR_NONE) {
    sendResponse(400, false, 'Format JSON tidak valid: ' . json_last_error_msg());
}

// ─── Auto-Normalize format hierarkis {"Report": {...}} jika dikirimkan ─────────
if (isset($payload['Report']) || isset($payload['report'])) {
    $rep = $payload['Report'] ?? $payload['report'];
    $pat = $rep['patient'] ?? [];
    $ord = $rep['order'] ?? [];
    $rpt = $rep['report'] ?? [];

    $descText = $rpt['description'] ?? '';
    $parsedSections = parseReportSections($descText);

    $payload = [
        'norm'                => $pat['id'] ?? null,
        'nama'                => $pat['name'] ?? null,
        'jenis_kelamin'       => (strtoupper($pat['sex'] ?? '') === 'M') ? '1' : ((strtoupper($pat['sex'] ?? '') === 'F') ? '2' : '0'),
        'tanggal_lahir'       => !empty($pat['birthDate']) ? date('Y-m-d', strtotime($pat['birthDate'])) : null,
        'accession_number'    => $ord['id'] ?? $identifier,
        'kode_tindakan'       => $ord['serviceCode'] ?? null,
        'nama_tindakan'       => $ord['serviceName'] ?? null,
        'dokter_pengirim'     => $ord['doctor'] ?? null,
        'tanggal_pemeriksaan' => $rpt['reportDate'] ?? $ord['orderDate'] ?? date('Y-m-d H:i:s'),
        'klinis'              => $parsedSections['klinis'] ?? null,
        'hasil'               => $parsedSections['hasil'] ?? null,
        'kesan'               => $parsedSections['kesan'] ?? null,
        'usul'                => $parsedSections['usul'] ?? null,
        'btk'                 => $rpt['link'] ?? null,
        'pacs_viewer_url'     => $rpt['link'] ?? null,
        'dokter_id'           => $rpt['doctorID'] ?? 0,
        'dokter_nama'         => $rpt['doctorName'] ?? null,
        'pacs_hook_uid'       => 'RIS_PACS',
        'kritis'              => 1,
        'otomatis'            => 1,
        'oleh'                => 0,
    ];
}


// ─── 5. Eksekusi Database & Audit Log ───────────────────────────────────────
$logId = null;
$hl7LogModel = null;
$pdo = null;

try {
    $pdo = Database::getInstance();
    $hl7LogModel = new Hl7LogModel();
    $refId       = substr($identifier, 0, 11);                             // char(11)
    $kode        = substr($payload['kode_tindakan'] ?? '', 0, 10);        // char(10)
    $provider    = PACS_PROVIDER;
    $jenisLog    = sanitizeInt($payload['jenis_log'] ?? Hl7LogModel::JENIS_ORU);

    $logId = $hl7LogModel->insert(
        refId:    $refId,
        jenis:    $jenisLog,
        provider: $provider,
        kode:     $kode,
        request:  $rawBody   // simpan raw JSON payload sebagai REQUEST
    );

    writeLog('info', 'PUT request diterima', [
        'identifier'  => $identifier,
        'ip'          => $_SERVER['REMOTE_ADDR'] ?? '?',
        'bytes'       => strlen($rawBody),
        'hl7_log_id'  => $logId,
    ]);

    // ─── 6. Sanitasi & Validasi Field ─────────────────────────────────────────
    $norm      = sanitizeString($payload['norm'] ?? null, 50);
    $accession = sanitizeString($payload['accession_number'] ?? $identifier, 11); // char(11)

    if (empty($accession)) {
        $errMsg = 'Field accession_number wajib ada.';
        if ($logId) {
            $hl7LogModel->updateResponse($logId, json_encode(['error' => $errMsg]), false);
        }
        sendResponse(422, false, $errMsg);
    }

    // ── Data hasil_rad ──────────────────────────────────────────────────────
    $cleanHasil = [
        'accession_number'    => $accession,
        'tanggal_pemeriksaan' => $payload['tanggal_pemeriksaan'] ?? date('Y-m-d H:i:s'),
        'klinis'              => sanitizeString($payload['klinis'] ?? null, 65535),
        'kesan'               => sanitizeString($payload['kesan'] ?? null, 65535),
        'usul'                => sanitizeString($payload['usul'] ?? null, 65535),
        'hasil'               => sanitizeString($payload['hasil'] ?? null, 65535),
        'btk'                 => sanitizeString($payload['btk'] ?? $payload['pacs_viewer_url'] ?? null, 65535),  // Bacaan Tambahan / URL PACS Viewer
        'kritis'              => sanitizeInt($payload['kritis'] ?? 1),
        'dokter_id'           => sanitizeInt($payload['dokter_id'] ?? 0),
        'pacs_hook_uid'       => sanitizeString($payload['pacs_hook_uid'] ?? null, 36),
        'oleh'                => sanitizeInt($payload['oleh'] ?? 0),
        'status'              => 1,
    ];

    // ── Data pasien (opsional, jika ada NORM) ────────────────────────────────
    $cleanPasien = null;
    if ($norm) {
        $cleanPasien = [
            'norm'          => $norm,
            'nama'          => sanitizeString($payload['nama'] ?? null, 255),
            'tanggal_lahir' => $payload['tanggal_lahir'] ?? null,
            'jenis_kelamin' => $payload['jenis_kelamin'] ?? '0',
            'status'        => 1,
        ];
    }

    // ─── 7. Eksekusi Database dalam Transaksi ─────────────────────────────────
    $pdo = Database::getInstance();
    $pdo->beginTransaction();

    $results = [
        'pasien'    => null,
        'tindakan'  => null,
        'hasil_rad' => false,
    ];

    // 7a. Upsert master.pasien (opsional)
    if ($cleanPasien) {
        try {
            $pasienModel       = new PasienModel();
            $results['pasien'] = $pasienModel->upsert($cleanPasien);
        } catch (PDOException $e) {
            // Pasien mungkin belum ada di tabel ini — log tapi lanjut
            writeLog('warning', 'Upsert pasien gagal (non-fatal)', ['err' => $e->getMessage()]);
            $results['pasien'] = false;
        }
    }

    // 7b. Upsert layanan.hasil_rad ← WAJIB, ini target utama
    $hasilModel         = new HasilRadModel();
    $results['hasil_rad'] = $hasilModel->upsert($cleanHasil);

    if (!$results['hasil_rad']) {
        throw new RuntimeException('Upsert hasil_rad gagal (execute returned false).');
    }

    $pdo->commit();

    // 7c. Update rl7_logs: KIRIM=1, RESPONSE = hasil
    $responseBody = json_encode([
        'success'    => true,
        'accession'  => $accession,
        'operations' => $results,
    ]);
    if ($logId) {
        $hl7LogModel->updateResponse($logId, $responseBody, true);
    }

    writeLog('info', '✓ Transaksi berhasil', [
        'accession'  => $accession,
        'norm'       => $norm,
        'hl7_log_id' => $logId,
        'results'    => $results,
    ]);

    sendResponse(200, true, 'Data PACS berhasil disinkronkan ke database SIMGOS.', [
        'identifier'  => $identifier,
        'accession'   => $accession,
        'norm'        => $norm,
        'hl7_log_id'  => $logId,
        'operations'  => $results,
    ]);

} catch (PDOException $e) {
    if ($pdo && $pdo->inTransaction()) $pdo->rollBack();

    $errResponse = json_encode(['error' => 'DB Error', 'message' => $e->getMessage(), 'code' => $e->getCode()]);
    if ($logId && $hl7LogModel) {
        $hl7LogModel->updateResponse($logId, $errResponse, false);
    }
    writeLog('error', '✗ Database error — ROLLBACK', [
        'error'      => $e->getMessage(),
        'accession'  => $accession ?? '?',
    ]);
    sendResponse(500, false, 'Database error: ' . $e->getMessage(), [
        'db_error_code' => $e->getCode(),
    ]);

} catch (RuntimeException $e) {
    if ($pdo && $pdo->inTransaction()) $pdo->rollBack();

    if ($logId && $hl7LogModel) {
        $hl7LogModel->updateResponse($logId, json_encode(['error' => $e->getMessage()]), false);
    }
    writeLog('error', '✗ Runtime error — ROLLBACK', ['error' => $e->getMessage()]);
    sendResponse(500, false, $e->getMessage());

} catch (Throwable $e) {
    if ($pdo && $pdo->inTransaction()) $pdo->rollBack();

    if ($logId && $hl7LogModel) {
        $hl7LogModel->updateResponse($logId, json_encode(['error' => $e->getMessage()]), false);
    }
    writeLog('error', '✗ Unexpected error', ['error' => $e->getMessage()]);
    sendResponse(500, false, 'Internal server error: ' . $e->getMessage());
}
