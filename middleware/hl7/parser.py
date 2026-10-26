"""
parser.py - HL7 v2 Message Parser
==================================
Mem-parsing pesan HL7 v2 menjadi struktur Python dict yang terorganisir.
Mendukung segmen: MSH, PID, PV1, ORC, OBR, OBX (multi-baris).

Standar HL7 v2:
  - Field separator  : '|'  (Field Separator, MSH-1)
  - Component sep    : '^'  (Component Separator, MSH-2[0])
  - Repetition sep   : '~'  (Repetition Separator, MSH-2[1])
  - Escape char      : '\\' (Escape Character, MSH-2[2])
  - Subcomponent sep : '&'  (Subcomponent Separator, MSH-2[3])
"""

from __future__ import annotations
import re
from typing import Any


# ---------------------------------------------------------------------------
# Konstanta MLLP
# ---------------------------------------------------------------------------
MLLP_START_BYTE = b'\x0b'        # 0x0B - Vertical Tab (Start of Block)
MLLP_END_BYTES  = b'\x1c\x0d'   # 0x1C 0x0D - End of Block + Carriage Return


class HL7ParseError(Exception):
    """Eksepsi khusus untuk error parsing HL7."""
    pass


class HL7Field:
    """
    Merepresentasikan satu field HL7 beserta komponen dan sub-komponennya.
    Mendukung akses: field.value, field[0], field[1] (berbasis 0-index komponen).
    """
    def __init__(self, raw: str, component_sep: str = "^", sub_sep: str = "&"):
        self._raw = raw or ""
        self._components = self._raw.split(component_sep)
        self._sub_sep = sub_sep

    @property
    def value(self) -> str:
        """Nilai mentah field (semua komponen)."""
        return self._raw

    def get(self, component_idx: int = 0, sub_idx: int = 0) -> str:
        """
        Mengambil nilai komponen (0-based index).
        component_idx=0 → komponen pertama (misal, Family Name pada PID-5).
        """
        if component_idx < len(self._components):
            comp = self._components[component_idx]
            subs = comp.split(self._sub_sep)
            if sub_idx < len(subs):
                return subs[sub_idx].strip()
        return ""

    def __getitem__(self, idx: int) -> str:
        return self.get(idx)

    def __str__(self) -> str:
        return self._raw

    def __repr__(self) -> str:
        return f"HL7Field({self._raw!r})"


class HL7Segment:
    """
    Merepresentasikan satu segmen HL7 (misal: PID|...|...|).
    Mendukung akses field berbasis 1-index (sesuai standar HL7).
    """
    def __init__(
        self,
        raw_line: str,
        field_sep: str = "|",
        component_sep: str = "^",
        sub_sep: str = "&"
    ):
        self._raw = raw_line.strip()
        self._field_sep = field_sep
        self._component_sep = component_sep
        self._sub_sep = sub_sep

        parts = self._raw.split(field_sep)
        self.segment_id = parts[0].upper()

        # MSH adalah kasus khusus: MSH-1 adalah field separator itu sendiri
        if self.segment_id == "MSH":
            # Sisipkan field separator sebagai MSH-1
            self._fields = [self.segment_id, field_sep] + parts[1:]
        else:
            self._fields = parts

    def field(self, index: int) -> HL7Field:
        """
        Mengambil field berdasarkan nomor field HL7 (1-based).
        PID.field(3) → PID-3 (Patient ID List).
        """
        # index 0 = segment name, index 1 = first real field
        if index < len(self._fields):
            return HL7Field(
                self._fields[index],
                self._component_sep,
                self._sub_sep
            )
        return HL7Field("", self._component_sep, self._sub_sep)

    def __getitem__(self, index: int) -> HL7Field:
        return self.field(index)

    def __str__(self) -> str:
        return self._raw


class HL7Message:
    """
    Merepresentasikan keseluruhan pesan HL7 v2.
    Menyediakan akses ke semua segmen serta helper untuk segmen umum.
    """

    def __init__(self, raw_message: str):
        self.raw = raw_message
        self.segments: dict[str, list[HL7Segment]] = {}
        self._parse(raw_message)

    def _parse(self, raw: str) -> None:
        """Mem-parsing raw HL7 string menjadi kumpulan segmen."""
        # Bersihkan MLLP wrapper jika ada
        clean = raw.replace(MLLP_START_BYTE.decode("latin-1"), "")
        clean = clean.replace("\x1c", "").replace("\r", "\n")

        lines = [ln.strip() for ln in clean.split("\n") if ln.strip()]
        if not lines:
            raise HL7ParseError("Pesan HL7 kosong atau tidak valid.")

        # Ambil separator dari MSH
        msh_line = lines[0]
        if not msh_line.upper().startswith("MSH"):
            raise HL7ParseError(
                f"Segmen pertama bukan MSH: '{msh_line[:20]}'"
            )

        field_sep     = msh_line[3]     # MSH-1
        component_sep = msh_line[4]     # MSH-2[0]
        sub_sep       = msh_line[7] if len(msh_line) > 7 else "&"  # MSH-2[3]

        for line in lines:
            if not line:
                continue
            seg = HL7Segment(line, field_sep, component_sep, sub_sep)
            seg_id = seg.segment_id
            if seg_id not in self.segments:
                self.segments[seg_id] = []
            self.segments[seg_id].append(seg)

    def get_segment(self, seg_id: str, index: int = 0) -> HL7Segment | None:
        """Mengambil segmen berdasarkan ID (misal: 'PID', 'OBX')."""
        segs = self.segments.get(seg_id.upper(), [])
        return segs[index] if index < len(segs) else None

    def get_all_segments(self, seg_id: str) -> list[HL7Segment]:
        """Mengambil semua segmen dengan ID tertentu (misal: semua OBX)."""
        return self.segments.get(seg_id.upper(), [])

    # ── Shortcut Properties ──────────────────────────────────────────────────
    @property
    def MSH(self) -> HL7Segment | None:
        return self.get_segment("MSH")

    @property
    def PID(self) -> HL7Segment | None:
        return self.get_segment("PID")

    @property
    def PV1(self) -> HL7Segment | None:
        return self.get_segment("PV1")

    @property
    def ORC(self) -> HL7Segment | None:
        return self.get_segment("ORC")

    @property
    def OBR(self) -> HL7Segment | None:
        return self.get_segment("OBR")

    @property
    def OBX_list(self) -> list[HL7Segment]:
        return self.get_all_segments("OBX")

    def message_type(self) -> str:
        """Mengembalikan tipe pesan, misal 'ORU^R01'."""
        if self.MSH:
            return f"{self.MSH.field(9)[0]}^{self.MSH.field(9)[1]}"
        return "UNKNOWN"

    def message_control_id(self) -> str:
        """MSH-10: Message Control ID untuk ACK."""
        return self.MSH.field(10).value if self.MSH else ""

    def __repr__(self) -> str:
        segs = list(self.segments.keys())
        return f"HL7Message(type={self.message_type()!r}, segments={segs})"


# ---------------------------------------------------------------------------
# Fungsi Helper: Ekstrak pesan dari wrapper MLLP (byte)
# ---------------------------------------------------------------------------
def extract_from_mllp(data: bytes) -> str:
    """
    Mengekstrak isi pesan HL7 dari wrapper MLLP.
    Menghapus byte 0x0B di awal dan 0x1C 0x0D di akhir.
    """
    raw = data.strip()
    if raw.startswith(MLLP_START_BYTE):
        raw = raw[1:]
    if raw.endswith(MLLP_END_BYTES):
        raw = raw[:-2]
    elif raw.endswith(b'\x1c'):
        raw = raw[:-1]
    return raw.decode("utf-8", errors="replace")


def wrap_with_mllp(hl7_text: str) -> bytes:
    """
    Membungkus pesan HL7 teks dengan header/footer MLLP.
    """
    body = hl7_text.encode("utf-8")
    return MLLP_START_BYTE + body + MLLP_END_BYTES
