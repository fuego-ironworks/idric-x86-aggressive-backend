#!/usr/bin/env python3
"""Direct x86-64 ELF file-reading runtime fixture for Linux and NetBSD.

This is a target-level I/O acceptance program, NOT YET compiler-produced
from a checked Idriç IO expression. It reads argv[1] in fixed-size chunks,
retries EINTR and short writes, and copies raw bytes to stdout; it never
opens the file for writing or modifies the source. Python runs only at
cross-compile time; the output executable has no Python/libc dependencies.
"""

from __future__ import annotations

import argparse
import struct
from pathlib import Path

try:
    from backend.elf64 import NETBSD_10_ABI_VERSION, write_elf
except ModuleNotFoundError:
    from elf64 import NETBSD_10_ABI_VERSION, write_elf

# NetBSD syscall numbers: sys/sys/syscall.h; Linux numbers: asm/unistd_64.h.
SYSCALLS = {
    "linux-x86_64": dict(exit=60, read=0, write=1, open=2, close=3),
    "netbsd-x86_64": dict(exit=1, read=3, write=4, open=5, close=6),
}
BUFFER_BYTES = 65536
RAX, RCX, RDX, RBX, RSP, RBP, RSI, RDI = range(8)
R8, R9, R10, R11, R12, R13 = range(8, 14)
REGISTERS = ("rax", "rcx", "rdx", "rbx", "rsp", "rbp", "rsi", "rdi",
             "r8", "r9", "r10", "r11", "r12", "r13")


class Encoder:
    """Small explicit x86-64 instruction forms, with checked rel32 fixups."""

    def __init__(self) -> None:
        self.code = bytearray()
        self.labels: dict[str, int] = {}
        self.fixups: list[tuple[int, str]] = []
        self.rows: list[tuple[int, int, str]] = []

    def emit(self, instruction: bytes, text: str) -> None:
        before = len(self.code)
        self.code.extend(instruction)
        self.rows.append((before, len(self.code), text))

    def label(self, name: str) -> None:
        if name in self.labels:
            raise ValueError(f"duplicate x86 label: {name}")
        self.labels[name] = len(self.code)

    def jump(self, opcodes: bytes, name: str, opcode_name: str) -> None:
        before = len(self.code)
        self.code.extend(opcodes)
        displacement_at = len(self.code)
        self.code.extend(b"\0\0\0\0")
        self.fixups.append((displacement_at, name))
        self.rows.append((before, len(self.code), f"{opcode_name} {name}"))

    def reg_imm(self, register: int, value: int) -> None:
        if register not in range(14) or not 0 <= value <= (1 << 64)-1:
            raise ValueError("out-of-range register or unsigned immediate")
        prefix = 0x48 | (1 if register >= 8 else 0)
        self.emit(bytes([prefix, 0xB8 + (register & 7)]) +
                  struct.pack("<Q", value), f"mov {REGISTERS[register]}, {value}")

    def reg_reg(self, destination: int, source: int) -> None:
        if destination not in range(14) or source not in range(14):
            raise ValueError("unsupported x86 register")
        rex = 0x48 | (4 if source >= 8 else 0) | (1 if destination >= 8 else 0)
        modrm = 0xC0 | ((source & 7) << 3) | (destination & 7)
        self.emit(bytes((rex, 0x89, modrm)),
                  f"mov {REGISTERS[destination]}, {REGISTERS[source]}")

    def mov_from_rsp(self, destination: int, offset: int) -> None:
        if destination not in range(14) or not -2**31 <= offset < 2**31:
            raise ValueError("invalid stack load")
        rex = 0x48 | (4 if destination >= 8 else 0)
        self.emit(bytes((rex, 0x8B, 0x84 | ((destination & 7) << 3), 0x24)) +
                  struct.pack("<i", offset),
                  f"mov {REGISTERS[destination]}, [rsp + {offset}]")

    def lea_rsp(self, destination: int) -> None:
        if destination not in range(14):
            raise ValueError("invalid stack-relative LEA")
        rex = 0x48 | (4 if destination >= 8 else 0)
        self.emit(bytes((rex, 0x8D, ((destination & 7) << 3) | 4, 0x24)),
                  f"lea {REGISTERS[destination]}, [rsp]")

    def bin_reg(self, opcode: int, destination: int, source: int, verb: str) -> None:
        rex = 0x48 | (4 if source >= 8 else 0) | (1 if destination >= 8 else 0)
        modrm = 0xC0 | ((source & 7) << 3) | (destination & 7)
        self.emit(bytes((rex, opcode, modrm)),
                  f"{verb} {REGISTERS[destination]}, {REGISTERS[source]}")

    def cmp_rax_signed32(self, value: int) -> None:
        self.emit(b"\x48\x3d" + struct.pack("<i", value), f"cmp rax, {value}")

    def test_reg(self, register: int) -> None:
        self.bin_reg(0x85, register, register, "test")

    def cmp_argc_two(self) -> None:
        self.emit(b"\x48\x83\x3c\x24\x02", "cmp qword [rsp], 2")

    def sub_rsp(self, size: int) -> None:
        self.emit(b"\x48\x81\xec" + struct.pack("<I", size), f"sub rsp, {size}")

    def syscall(self, name: str) -> None:
        self.emit(b"\x0f\x05", f"syscall ; {name}")

    def ud2(self) -> None:
        self.emit(b"\x0f\x0b", "ud2 ; unreachable after SYS_exit")

    def finish(self) -> tuple[bytes, str]:
        for at, name in self.fixups:
            if name not in self.labels:
                raise ValueError(f"unresolved jump target: {name}")
            diff = self.labels[name] - (at + 4)
            if not -(1 << 31) <= diff < (1 << 31):
                raise ValueError(f"rel32 target out of range: {name}")
            struct.pack_into("<i", self.code, at, diff)
        out = bytes(self.code)
        listing = "\n".join(f"{start:04x}  {out[start:end].hex(' '):<36} {text}"
                            for start, end, text in self.rows) + "\n"
        return out, listing


def make_file_reader(target: str) -> tuple[bytes, str]:
    """Emit a target's native read-only stream I/O control flow."""
    if target not in SYSCALLS:
        raise ValueError(f"unsupported target: {target}")
    numbers = SYSCALLS[target]
    netbsd = target == "netbsd-x86_64"
    encoder = Encoder()

    def checked_syscall(operation: str, failure: str) -> None:
        encoder.reg_imm(RAX, numbers[operation])
        encoder.syscall(operation)
        if netbsd:
            # NetBSD sets CF on error and returns *positive* errno in RAX.
            encoder.jump(b"\x0f\x82", failure, "jc")
        else:
            # Linux returns -errno in [-4095,-1] and does not use CF.
            encoder.cmp_rax_signed32(-4095)
            encoder.jump(b"\x0f\x83", failure, "jae")

    def exit_status(status: int) -> None:
        encoder.reg_imm(RDI, status)
        encoder.reg_imm(RAX, numbers["exit"])
        encoder.syscall("exit")
        encoder.ud2()

    def retry_if_intr(label: str, failure: str) -> None:
        encoder.cmp_rax_signed32(4 if netbsd else -4)
        encoder.jump(b"\x0f\x84", label, "je")
        encoder.jump(b"\xe9", failure, "jmp")

    encoder.cmp_argc_two()
    encoder.jump(b"\x0f\x82", "usage_error", "jb")
    encoder.mov_from_rsp(RDI, 16)  # argv[1] at process entry
    encoder.reg_imm(RSI, 0)       # O_RDONLY (no mode argument needed)
    checked_syscall("open", "open_error")
    encoder.reg_reg(RBX, RAX)     # file descriptor, survives kernel syscalls
    encoder.sub_rsp(BUFFER_BYTES) # writable stack buffer; no heap or libc

    encoder.label("read_again")
    encoder.reg_reg(RDI, RBX)
    encoder.lea_rsp(RSI)
    encoder.reg_imm(RDX, BUFFER_BYTES)
    checked_syscall("read", "read_error")
    encoder.test_reg(RAX)
    encoder.jump(b"\x0f\x84", "at_eof", "jz")
    encoder.reg_reg(R12, RAX)  # number of bytes still unwritten
    encoder.lea_rsp(R13)       # next byte to write

    encoder.label("write_remaining")
    encoder.reg_imm(RDI, 1)    # stdout
    encoder.reg_reg(RSI, R13)
    encoder.reg_reg(RDX, R12)
    checked_syscall("write", "write_error")
    encoder.test_reg(RAX)
    encoder.jump(b"\x0f\x84", "io_error", "jz") # zero progress is not success
    encoder.bin_reg(0x29, R12, RAX, "sub")
    encoder.bin_reg(0x01, R13, RAX, "add")
    encoder.test_reg(R12)
    encoder.jump(b"\x0f\x85", "write_remaining", "jnz")
    encoder.jump(b"\xe9", "read_again", "jmp")

    encoder.label("at_eof")
    encoder.reg_reg(RDI, RBX)
    checked_syscall("close", "io_error")
    exit_status(0)

    encoder.label("read_error")
    retry_if_intr("read_again", "io_error")
    encoder.label("write_error")
    retry_if_intr("write_remaining", "io_error")
    encoder.label("open_error")
    exit_status(66)              # EX_NOINPUT
    encoder.label("usage_error")
    exit_status(64)              # EX_USAGE
    encoder.label("io_error")
    exit_status(74)              # EX_IOERR
    return encoder.finish()


def cross_compile(target: str, *, netbsd_abi_version: int = NETBSD_10_ABI_VERSION
                  ) -> tuple[bytes, str]:
    code, listing = make_file_reader(target)
    return write_elf(code, target=target, netbsd_abi_version=netbsd_abi_version), listing


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", required=True, choices=sorted(SYSCALLS))
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--listing", type=Path)
    parser.add_argument("--netbsd-abi-version", type=int, default=NETBSD_10_ABI_VERSION)
    options = parser.parse_args()
    image, listing = cross_compile(options.target, netbsd_abi_version=options.netbsd_abi_version)
    options.output.parent.mkdir(parents=True, exist_ok=True)
    options.output.write_bytes(image)
    options.output.chmod(0o755)
    if options.listing:
        options.listing.parent.mkdir(parents=True, exist_ok=True)
        options.listing.write_text(listing)
    print(f"CROSS_COMPILED\t{options.target}\t{options.output}")
    print("IDRIC_SOURCE_HANDOFF\tNOT_IMPLEMENTED")
    print("TARGET_EXECUTION\tNOT_VERIFIED")


if __name__ == "__main__":
    main()
