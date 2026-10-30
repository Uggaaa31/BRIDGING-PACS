"""
simgos_api.py - HTTP PUT Client untuk Bridging ke REST API SIMGOS
=================================================================
Mengemas payload SimgosPayload menjadi JSON dan mengirimkannya
ke endpoint PUT SIMGOS dengan retry logic dan logging transaksi.
"""

from __future__ import annotations
import itertools
import json
import threading
import time
from datetime import datetime
from typing import Optional

import requests
from requests.exceptions import Timeout, ConnectionError, RequestException

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from config.settings import (
    SIMGOS_API_BASE_URL,
    SIMGOS_API_ENDPOINT_BRIDGING,
    SIMGOS_AUTH_TYPE,
    SIMGOS_API_KEY,
    SIMGOS_API_TOKEN,
    SIMGOS_API_TIMEOUT_SECONDS,
    SIMGOS_API_MAX_RETRIES,
    SIMGOS_API_RETRY_DELAY_SEC,
)
from hl7.converter import SimgosPayload
from utils.logger import get_logger

log = get_logger("simgos_api")
history = get_logger("riwayat")   # jejak satu baris per HTTP request (grep "RIWAYAT")

# Nomor urut request agar riwayat di `docker logs` bisa diurutkan & dihitung
_req_seq = itertools.count(1)
_seq_lock = threading.Lock()

def _next_seq() -> int:
    with _seq_lock:
        return next(_req_seq)

def _history_line(
    seq: int,
    outcome: str,
    identifier: str,
    method: str,
    url: str,
    msg_id: str,
    msg_type: str,
    status: int,
    duration_ms: float,
    attempt: int,
    detail: str = "",
) -> None:
    """Satu baris riwayat per HTTP request — terlihat di `docker logs`."""
    history.info(
        f"RIWAYAT #{seq} {method} {outcome} | acc={identifier} | msg={msg_id} "
        f"| type={msg_type} | HTTP {status} | {duration_ms:.1f}ms | attempt={attempt} "
        f"| url={url}{detail}"
    )


# ---------------------------------------------------------------------------
# Tipe data response
# ---------------------------------------------------------------------------
class ApiResponse:
    """Hasil pengiriman HTTP PUT ke SIMGOS API."""

    def __init__(
        self,
        success: bool,
        status_code: int = 0,
        body: dict = None,
        error: str = "",
        duration_ms: float = 0
    ):
        self.success     = success
        self.status_code = status_code
        self.body        = body or {}
        self.error       = error
        self.duration_ms = duration_ms
        self.timestamp   = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    def __repr__(self) -> str:
        return (
            f"ApiResponse(success={self.success}, "
            f"status={self.status_code}, "
            f"duration={self.duration_ms:.1f}ms)"
        )


# ---------------------------------------------------------------------------
# Helper: Build header autentikasi
# ---------------------------------------------------------------------------
def _build_headers() -> dict:
    """Membangun header HTTP untuk request ke API SIMGOS."""
    headers = {
        "Content-Type":  "application/json",
        "Accept":        "application/json",
        "X-Source-App":  "PACS-BRIDGE",
        "X-Request-At":  datetime.now().isoformat(),
    }

    if SIMGOS_AUTH_TYPE == "bearer" and SIMGOS_API_TOKEN:
        headers["Authorization"] = f"Bearer {SIMGOS_API_TOKEN}"
    elif SIMGOS_AUTH_TYPE == "api_key" and SIMGOS_API_KEY:
        headers["X-Api-Key"] = SIMGOS_API_KEY
    else:
        # Fail-closed: jangan pernah mengirim request tanpa kredensial.
        raise RuntimeError(
            "Konfigurasi autentikasi SIMGOS tidak valid: SIMGOS_API_KEY / "
            "SIMGOS_API_TOKEN kosong."
        )

    return headers


# ---------------------------------------------------------------------------
# Helper: Bangun URL endpoint
# ---------------------------------------------------------------------------
def _build_url(identifier: str) -> str:
    """
    Bangun URL PUT endpoint SIMGOS.

    Contoh: http://localhost/simgos-api/api/v1/radiologi/pacs-bridging/ACC-2024-001
    """
    base = SIMGOS_API_BASE_URL.rstrip("/")
    endpoint = SIMGOS_API_ENDPOINT_BRIDGING.strip("/")
    safe_id  = requests.utils.quote(str(identifier), safe="")
    return f"{base}/{endpoint}/{safe_id}"


# ---------------------------------------------------------------------------
# Fungsi utama pengiriman
# ---------------------------------------------------------------------------
def send_to_simgos(payload: SimgosPayload) -> ApiResponse:
    """
    Mengirimkan payload ke endpoint PUT SIMGOS.

    Args:
        payload: Objek SimgosPayload hasil konversi HL7.

    Returns:
        ApiResponse dengan status, body respons, dan durasi.

    Strategy:
        - Gunakan accession_number sebagai identifier URL PUT.
        - Fallback ke NORM jika accession_number tidak ada.
        - Retry otomatis hingga SIMGOS_API_MAX_RETRIES kali.
    """
    # Tentukan identifier untuk URL
    identifier = payload.accession_number or payload.norm
    if not identifier:
        error_msg = "Tidak ada identifier valid (accession_number atau NORM) untuk PUT request."
        log.error(error_msg)
        _history_line(
            _next_seq(), "DITOLAK", "-", "PUT", "-",
            payload.message_control_id or "-", payload.message_type or "-",
            0, 0.0, 0, f" | error={error_msg}",
        )
        return ApiResponse(success=False, error=error_msg)

    url         = _build_url(identifier)
    headers     = _build_headers()
    payload_dict = payload.to_dict()

    seq       = _next_seq()
    msg_id    = payload.message_control_id or "-"
    msg_type  = payload.message_type or "-"
    n_bytes   = len(json.dumps(payload_dict))

    log.info(f"→ PUT {url} | Payload: {n_bytes} bytes")
    log.debug(f"Payload JSON:\n{json.dumps(payload_dict, indent=2, ensure_ascii=False)}")
    _history_line(
        seq, "MULAI", identifier, "PUT", url, msg_id, msg_type, 0, 0.0, 0,
        f" | bytes={n_bytes}",
    )

    last_error = ""
    last_status = 0

    for attempt in range(1, SIMGOS_API_MAX_RETRIES + 1):
        start_time = time.monotonic()
        try:
            response = requests.put(
                url,
                headers=headers,
                json=payload_dict,
                timeout=SIMGOS_API_TIMEOUT_SECONDS
            )
            duration_ms = (time.monotonic() - start_time) * 1000
            last_status = response.status_code

            # Parse body respons
            try:
                resp_body = response.json()
            except ValueError:
                resp_body = {"raw": response.text[:500]}

            # ── Evaluasi HTTP Status & Body ────────────────────────────────
            is_ok = (
                response.status_code in (200, 201, 204)
                and isinstance(resp_body, dict)
                and resp_body.get("success", False) is True
            )

            if is_ok:
                log.info(
                    f"✓ PUT berhasil | status={response.status_code} | "
                    f"duration={duration_ms:.1f}ms | attempt={attempt}"
                )
                _history_line(
                    seq, "OK", identifier, "PUT", url, msg_id, msg_type,
                    response.status_code, duration_ms, attempt,
                    f" | hl7_log_id={resp_body.get('data', {}).get('hl7_log_id', '-')}",
                )
                return ApiResponse(
                    success=True,
                    status_code=response.status_code,
                    body=resp_body,
                    duration_ms=duration_ms
                )
            else:
                last_error = (
                    resp_body.get("message")
                    if isinstance(resp_body, dict) and "message" in resp_body
                    else response.text[:200]
                )
                log.warning(
                    f"✗ PUT gagal | status={response.status_code} | "
                    f"attempt={attempt}/{SIMGOS_API_MAX_RETRIES} | "
                    f"error={last_error}"
                )
                # Jangan retry untuk error 4xx (client error)
                if 400 <= response.status_code < 500:
                    log.error(f"HTTP 4xx — tidak ada retry. error={last_error}")
                    _history_line(
                        seq, "GAGAL", identifier, "PUT", url, msg_id, msg_type,
                        response.status_code, duration_ms, attempt,
                        f" | error={last_error}",
                    )
                    return ApiResponse(
                        success=False,
                        status_code=response.status_code,
                        body=resp_body,
                        error=last_error,
                        duration_ms=duration_ms
                    )

        except Timeout:
            duration_ms = (time.monotonic() - start_time) * 1000
            last_error = f"Timeout setelah {SIMGOS_API_TIMEOUT_SECONDS}s"
            log.warning(f"⏱ Timeout | attempt={attempt}/{SIMGOS_API_MAX_RETRIES}")

        except ConnectionError as e:
            duration_ms = (time.monotonic() - start_time) * 1000
            last_error = f"Koneksi gagal: {str(e)[:100]}"
            log.warning(f"🔌 ConnectionError | attempt={attempt} | {last_error}")

        except RequestException as e:
            duration_ms = (time.monotonic() - start_time) * 1000
            last_error = f"Request error: {str(e)[:100]}"
            log.error(f"🔥 RequestException | attempt={attempt} | {last_error}")

        # Tunggu sebelum retry (kecuali attempt terakhir)
        if attempt < SIMGOS_API_MAX_RETRIES:
            _history_line(
                seq, "RETRY", identifier, "PUT", url, msg_id, msg_type,
                last_status, duration_ms, attempt, f" | error={last_error}",
            )
            log.info(f"Menunggu {SIMGOS_API_RETRY_DELAY_SEC}s sebelum retry...")
            time.sleep(SIMGOS_API_RETRY_DELAY_SEC)

    # Semua attempt gagal
    log.error(
        f"✗✗ PUT GAGAL setelah {SIMGOS_API_MAX_RETRIES} attempt | "
        f"URL={url} | error={last_error}"
    )
    _history_line(
        seq, "GAGAL", identifier, "PUT", url, msg_id, msg_type,
        last_status, duration_ms, SIMGOS_API_MAX_RETRIES, f" | error={last_error}",
    )
    return ApiResponse(
        success=False,
        status_code=last_status,
        error=last_error
    )
