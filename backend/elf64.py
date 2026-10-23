"""Deterministic ELF64 images for the Linux and NetBSD AMD64 direct backends.

Both targets use the same ELF data model. NetBSD additionally requires an
allocated NetBSD ABI-identification PT_NOTE, as documented by NetBSD.
"""

from __future__ import annotations

import struct

ELF_BASE = 0x400000
CODE_OFFSET = 0x1000
NOTE_OFFSET = 0x200
NETBSD_10_ABI_VERSION = 1_000_000_000


def netbsd_ident_note(version: int = NETBSD_10_ABI_VERSION) -> bytes:
    """ELF_NT_NETBSD_IDENT (namesz=7, descsz=4, type=1), 4-byte aligned."""
    if not 0 < version <= 0xFFFFFFFF:
        raise ValueError("NetBSD ABI version must be a positive 32-bit value")
    return struct.pack("<III", 7, 4, 1) + b"NetBSD\x00\x00" + struct.pack("<I", version)


def write_elf(code_and_data: bytes, *, target: str = "linux-x86_64",
              netbsd_abi_version: int = NETBSD_10_ABI_VERSION) -> bytes:
    if target not in ("linux-x86_64", "netbsd-x86_64"):
        raise ValueError(f"unsupported ELF64 target: {target}")
    entry = ELF_BASE + CODE_OFFSET
    file_size = CODE_OFFSET + len(code_and_data)
    netbsd = target == "netbsd-x86_64"
    ident = b"\x7fELF\x02\x01\x01" + b"\x00" * 9
    elf_header = ident + struct.pack(
        "<HHIQQQIHHHHHH", 2, 62, 1, entry, 64, 0, 0, 64, 56,
        2 if netbsd else 1, 0, 0, 0
    )
    program_header = struct.pack(
        "<IIQQQQQQ", 1, 5, 0, ELF_BASE, ELF_BASE,
        file_size, file_size, 0x1000
    )
    headers = elf_header + program_header
    if netbsd:
        note = netbsd_ident_note(netbsd_abi_version)
        # PT_NOTE points inside the already mapped PT_LOAD, readable by the
        # kernel's OS-emulation selector. No sections or external linker.
        headers += struct.pack(
            "<IIQQQQQQ", 4, 4, NOTE_OFFSET, ELF_BASE + NOTE_OFFSET,
            ELF_BASE + NOTE_OFFSET, len(note), len(note), 4
        )
        if len(headers) > NOTE_OFFSET or NOTE_OFFSET + len(note) > CODE_OFFSET:
            raise ValueError("NetBSD note overlaps ELF headers or executable code")
        headers += bytes(NOTE_OFFSET - len(headers)) + note
    if len(headers) > CODE_OFFSET:
        raise ValueError("ELF headers exceed the deterministic code offset")
    return headers + bytes(CODE_OFFSET - len(headers)) + code_and_data
