"""
mllp_server.py - TCP/MLLP Server HL7 Listener
===============================================
Mendengarkan koneksi TCP dari mesin PACS pada port MLLP standar (2575).
Untuk setiap pesan HL7 yang diterima:
  1. Ekstrak dari wrapper MLLP
  2. Parse HL7 Message
  3. Konversi ke payload SIMGOS
  4. Kirim HTTP PUT ke API SIMGOS
  5. Kirim ACK kembali ke PACS

Mendukung multiple concurrent connections menggunakan ThreadingTCPServer.
"""

from __future__ import annotations
import socketserver
import threading
import sys, os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from config.settings import MLLP_HOST, MLLP_PORT, MLLP_BUFFER_SIZE, MLLP_TIMEOUT_SECONDS
from hl7.parser import extract_from_mllp, wrap_with_mllp, HL7Message, HL7ParseError
from hl7.converter import convert_hl7_to_simgos
from hl7.ack_builder import build_aa, build_ae
from client.simgos_api import send_to_simgos
from utils.logger import get_logger

log = get_logger("mllp_server")


# ---------------------------------------------------------------------------
# Request Handler: Dipanggil untuk setiap koneksi TCP masuk
# ---------------------------------------------------------------------------
class HL7RequestHandler(socketserver.BaseRequestHandler):
    """
    Handler untuk satu koneksi MLLP dari PACS.
    Setiap koneksi dijalankan di thread terpisah.
    """

    def handle(self):
        client_addr = f"{self.client_address[0]}:{self.client_address[1]}"
        log.info(f"[{client_addr}] Koneksi masuk dari PACS.")

        self.request.settimeout(MLLP_TIMEOUT_SECONDS)
        raw_data = b""
        msg_control_id = "UNKNOWN"

        try:
            # ── Terima data ──────────────────────────────────────────────────
            while True:
                chunk = self.request.recv(MLLP_BUFFER_SIZE)
                if not chunk:
                    break
                raw_data += chunk
                # MLLP message selesai saat byte 0x1C 0x0D ditemukan
                if b'\x1c\x0d' in raw_data or b'\x1c' in raw_data:
                    break

            if not raw_data:
                log.warning(f"[{client_addr}] Data kosong diterima.")
                return

            log.debug(f"[{client_addr}] Raw bytes diterima: {len(raw_data)} bytes")

            # ── Parse HL7 ────────────────────────────────────────────────────
            hl7_text = extract_from_mllp(raw_data)
            log.debug(f"[{client_addr}] HL7 text:\n{hl7_text[:300]}")

            try:
                hl7_msg = HL7Message(hl7_text)
                msg_control_id = hl7_msg.message_control_id()
                log.info(
                    f"[{client_addr}] Pesan diterima | type={hl7_msg.message_type()} "
                    f"| MsgID={msg_control_id}"
                )
            except HL7ParseError as pe:
                log.error(f"[{client_addr}] Parse HL7 gagal: {pe}")
                ack = build_ae(msg_control_id, str(pe))
                self.request.sendall(wrap_with_mllp(ack))
                return

            # ── Konversi HL7 → SIMGOS Payload ────────────────────────────────
            try:
                payload = convert_hl7_to_simgos(hl7_msg)
            except Exception as ce:
                log.error(f"[{client_addr}] Konversi data gagal: {ce}", exc_info=True)
                ack = build_ae(msg_control_id, f"Data conversion error: {ce}")
                self.request.sendall(wrap_with_mllp(ack))
                return

            # ── Kirim HTTP PUT ke SIMGOS ─────────────────────────────────────
            api_resp = send_to_simgos(payload)

            if api_resp.success:
                ack = build_aa(msg_control_id)
                log.info(f"[{client_addr}] ✓ ACK AA dikirim ke PACS.")
            else:
                ack = build_ae(
                    msg_control_id,
                    f"SIMGOS API error: {api_resp.error}"
                )
                log.warning(f"[{client_addr}] ✗ ACK AE dikirim ke PACS: {api_resp.error}")

            # ── Kirim ACK ────────────────────────────────────────────────────
            self.request.sendall(wrap_with_mllp(ack))

        except TimeoutError:
            log.warning(f"[{client_addr}] Koneksi timeout setelah {MLLP_TIMEOUT_SECONDS}s.")
            try:
                ack = build_ae(msg_control_id, "Connection timeout")
                self.request.sendall(wrap_with_mllp(ack))
            except Exception:
                pass

        except Exception as e:
            log.error(f"[{client_addr}] Unexpected error: {e}", exc_info=True)
            try:
                ack = build_ae(msg_control_id, f"Internal server error: {str(e)[:100]}")
                self.request.sendall(wrap_with_mllp(ack))
            except Exception:
                pass

        finally:
            log.info(f"[{client_addr}] Koneksi ditutup.")


# ---------------------------------------------------------------------------
# MLLP TCP Server
# ---------------------------------------------------------------------------
class ThreadedMLLPServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    """
    TCP Server multi-thread untuk menangani banyak koneksi PACS secara bersamaan.
    """
    daemon_threads    = True       # Thread daemon (ikut mati saat main thread berhenti)
    allow_reuse_address = True     # Izinkan reuse port (penting untuk restart cepat)


def start_mllp_server(host: str = None, port: int = None) -> None:
    """
    Memulai MLLP server dan mulai mendengarkan koneksi.
    Blocking — jalankan di thread terpisah atau sebagai main process.
    """
    bind_host = host or MLLP_HOST
    bind_port = port or MLLP_PORT

    log.info(f"Memulai MLLP Server pada {bind_host}:{bind_port}...")

    with ThreadedMLLPServer((bind_host, bind_port), HL7RequestHandler) as server:
        log.info(
            f"✓ MLLP Server aktif | {bind_host}:{bind_port} | "
            f"Menunggu koneksi dari PACS..."
        )
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            log.info("MLLP Server dihentikan oleh pengguna (Ctrl+C).")
        finally:
            server.server_close()
            log.info("MLLP Server ditutup.")
