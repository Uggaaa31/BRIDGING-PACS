"""
ack_builder.py - Generator Pesan HL7 ACK (Acknowledgement)
===========================================================
Membangun pesan HL7 ACK sesuai standar v2.x:
  - MSA|AA = Application Accept (berhasil)
  - MSA|AE = Application Error (gagal)
  - MSA|AR = Application Reject (ditolak)
"""

from __future__ import annotations
from datetime import datetime
import sys, os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from config.settings import SENDING_APP, RECEIVING_APP, HOSPITAL_NAME


def build_ack(
    original_message_control_id: str,
    success: bool = True,
    error_message: str = "",
    ack_code: str = None
) -> str:
    """
    Membangun pesan HL7 ACK lengkap.

    Args:
        original_message_control_id : MSH-10 dari pesan asli (untuk MSA-2).
        success                     : True → AA, False → AE.
        error_message               : Pesan error (opsional, untuk ERR segment).
        ack_code                    : Override kode ACK ('AA', 'AE', 'AR').

    Returns:
        String pesan HL7 ACK siap kirim (dengan \r sebagai segment terminator).
    """
    now     = datetime.now().strftime("%Y%m%d%H%M%S")
    ack_id  = f"ACK-{now}"

    if ack_code is None:
        ack_code = "AA" if success else "AE"

    # ── MSH Segment ──────────────────────────────────────────────────────────
    # MSH|^~\&|SendingApp|SendingFac|ReceivingApp|ReceivingFac|DateTime||ACK|MsgCtrlID|P|2.5
    msh = (
        f"MSH|^~\\&"
        f"|{SENDING_APP}|{HOSPITAL_NAME}"
        f"|{RECEIVING_APP}|{HOSPITAL_NAME}"
        f"|{now}"
        f"||ACK"
        f"|{ack_id}"
        f"|P|2.5"
    )

    # ── MSA Segment ──────────────────────────────────────────────────────────
    # MSA|AckCode|OriginalMsgCtrlID|TextMessage
    text_msg = "Processed successfully" if success else (error_message or "Processing error")
    msa = f"MSA|{ack_code}|{original_message_control_id}|{text_msg}"

    # ── ERR Segment (hanya jika ada error) ───────────────────────────────────
    segments = [msh, msa]
    if not success and error_message:
        # ERR|SegmentID^Sequence^FieldPos^CodeID^Severity^AppErrorCode^AppErrText
        err = f"ERR|||207^Application Error|E|||{error_message[:200]}"
        segments.append(err)

    # HL7 menggunakan \r (CR) sebagai segment terminator
    ack_message = "\r".join(segments) + "\r"
    return ack_message


def build_aa(msg_control_id: str) -> str:
    """Shortcut: ACK dengan MSA|AA (Application Accept)."""
    return build_ack(msg_control_id, success=True)


def build_ae(msg_control_id: str, error: str = "") -> str:
    """Shortcut: ACK dengan MSA|AE (Application Error)."""
    return build_ack(msg_control_id, success=False, error_message=error)


def build_ar(msg_control_id: str, reason: str = "") -> str:
    """Shortcut: ACK dengan MSA|AR (Application Reject)."""
    return build_ack(
        msg_control_id,
        success=False,
        error_message=reason,
        ack_code="AR"
    )
