"""Direct x86-64 scalar support for the compact numeric formats used by Idriç.

The floating formats use exact compact payloads in integer registers.  Arithmetic
for Float16, OCP FP8, and OCP E3M2 widens one scalar at a time to Float32, executes
a baseline scalar SSE operation, then rounds back to the compact payload.  The
Ootomo–Naruse OotomoE5M3 format remains a storage conversion only.  Bits8 stays an
unsigned low-eight-bit integer in a general-purpose register.
"""
from __future__ import annotations

import bisect
import math
import struct
from dataclasses import dataclass
from functools import cached_property

from backend.elf64 import write_elf


def f32(value: float) -> float:
    """Round a Python float to the binary32 value used by scalar SSE."""
    try:
        return struct.unpack("<f", struct.pack("<f", value))[0]
    except OverflowError:
        return math.copysign(math.inf, value)


class ScalarDomainError(ValueError):
    pass


@dataclass(frozen=True)
class SignedFloatFormat:
    name: str
    exponent_bits: int
    mantissa_bits: int
    bias: int
    finite_max_code: int
    infinity_code: int | None
    nan_code: int | None
    overflow_tie_overflows: bool
    nan_to_zero: bool = False

    @cached_property
    def sign_shift(self) -> int:
        return self.exponent_bits + self.mantissa_bits

    @cached_property
    def sign_mask(self) -> int:
        return 1 << self.sign_shift

    @cached_property
    def payload_mask(self) -> int:
        return (self.sign_mask << 1) - 1

    @cached_property
    def exponent_mask(self) -> int:
        return (1 << self.exponent_bits) - 1

    @cached_property
    def mantissa_mask(self) -> int:
        return (1 << self.mantissa_bits) - 1

    def _positive_decode(self, code: int) -> float:
        exponent = (code >> self.mantissa_bits) & self.exponent_mask
        mantissa = code & self.mantissa_mask
        if exponent == 0:
            if mantissa == 0:
                return 0.0
            return math.ldexp(mantissa / (1 << self.mantissa_bits), 1 - self.bias)
        return math.ldexp(1.0 + mantissa / (1 << self.mantissa_bits), exponent - self.bias)

    def is_nan_payload(self, payload: int) -> bool:
        code = payload & (self.sign_mask - 1)
        if self.name == "Float16":
            return (code >> self.mantissa_bits) == self.exponent_mask and (code & self.mantissa_mask) != 0
        if self.name == "OFP8 E4M3":
            return code == 0x7F
        if self.name == "OFP8 E5M2":
            return code in {0x7D, 0x7E, 0x7F}
        return False

    def is_infinity_payload(self, payload: int) -> bool:
        if self.infinity_code is None:
            return False
        return (payload & (self.sign_mask - 1)) == self.infinity_code

    def decode(self, payload: int) -> float:
        if not 0 <= payload <= self.payload_mask:
            raise ScalarDomainError(f"{self.name} payload out of range: {payload}")
        negative = bool(payload & self.sign_mask)
        code = payload & (self.sign_mask - 1)
        if self.is_nan_payload(payload):
            return math.nan
        if self.infinity_code is not None and code == self.infinity_code:
            return -math.inf if negative else math.inf
        value = self._positive_decode(code)
        return -value if negative else value

    @cached_property
    def positive_values(self) -> tuple[float, ...]:
        return tuple(self._positive_decode(code) for code in range(self.finite_max_code + 1))

    @cached_property
    def thresholds(self) -> tuple[float, ...]:
        values = self.positive_values
        return tuple((values[index] + values[index + 1]) / 2.0 for index in range(len(values) - 1))

    @cached_property
    def overflow_threshold(self) -> float:
        values = self.positive_values
        ulp = values[-1] - values[-2]
        return values[-1] + ulp / 2.0

    def _overflow(self, negative: bool, saturating: bool | None) -> int:
        sign = self.sign_mask if negative else 0
        if self.name in {"OFP8 E4M3", "OFP8 E5M2"}:
            if saturating is None:
                raise ScalarDomainError(f"{self.name} conversion requires an explicit saturating mode")
            if saturating:
                return sign | self.finite_max_code
            if self.infinity_code is not None:
                return sign | self.infinity_code
            assert self.nan_code is not None
            return self.nan_code
        if self.name == "E3M2":
            return sign | self.finite_max_code
        if self.infinity_code is not None:
            return sign | self.infinity_code
        return sign | self.finite_max_code

    def encode_f32(self, value: float, *, saturating: bool | None = None) -> int:
        if self.name in {"OFP8 E4M3", "OFP8 E5M2"} and saturating is None:
            raise ScalarDomainError(f"{self.name} conversion requires an explicit saturating mode")
        value = f32(value)
        negative = math.copysign(1.0, value) < 0.0
        sign = self.sign_mask if negative else 0
        if math.isnan(value):
            if self.nan_to_zero:
                return 0
            if self.nan_code is None:
                raise ScalarDomainError(f"{self.name} has no NaN encoding")
            return self.nan_code
        if math.isinf(value):
            return self._overflow(negative, saturating)

        magnitude = abs(value)
        threshold = self.overflow_threshold
        if magnitude > threshold or (magnitude == threshold and self.overflow_tie_overflows):
            return self._overflow(negative, saturating)

        values = self.positive_values
        at = bisect.bisect_left(values, magnitude)
        if at == 0:
            code = 0
        elif at >= len(values):
            code = self.finite_max_code
        else:
            lower = at - 1
            upper = at
            dl = magnitude - values[lower]
            du = values[upper] - magnitude
            if dl < du:
                code = lower
            elif du < dl:
                code = upper
            else:
                code = lower if (lower & 1) == 0 else upper
        return sign | code


FLOAT16 = SignedFloatFormat("Float16", 5, 10, 15, 0x7BFF, 0x7C00, 0x7E00, True)
OFP8_E4M3 = SignedFloatFormat("OFP8 E4M3", 4, 3, 7, 0x7E, None, 0x7F, False)
OFP8_E5M2 = SignedFloatFormat("OFP8 E5M2", 5, 2, 15, 0x7B, 0x7C, 0x7D, True)
E3M2 = SignedFloatFormat("E3M2", 3, 2, 3, 0x1F, None, None, False, nan_to_zero=True)


def ootomo_e5m3_encode_f32(value: float) -> int:
    """Ootomo-Naruse Figure 3 over its supported positive-normal source domain."""
    value = f32(value)
    bits = struct.unpack("<I", struct.pack("<f", value))[0]
    exponent = (bits >> 23) & 0xFF
    if bits >> 31 or exponent == 0 or exponent == 0xFF:
        raise ScalarDomainError("OotomoE5M3 requires a finite positive normal Float32 input")
    adjusted = bits - 0x38000000
    if adjusted < 0 or (adjusted >> 20) > 0xFF:
        raise ScalarDomainError("OotomoE5M3 input is outside the published storage range")
    return (adjusted >> 20) & 0xFF


def ootomo_e5m3_decode_f32(code: int) -> float:
    """Ootomo-Naruse Figure 4 midpoint representative."""
    if not 0 <= code <= 0xFF:
        raise ScalarDomainError(f"OotomoE5M3 payload out of range: {code}")
    bits = (code << 20) + 0x38080000
    return struct.unpack("<f", struct.pack("<I", bits))[0]


def bits8(value: int) -> int:
    return value & 0xFF


def bits8_add(left: int, right: int) -> int:
    return (left + right) & 0xFF


def bits8_sub(left: int, right: int) -> int:
    return (left - right) & 0xFF


def bits8_mul(left: int, right: int) -> int:
    return (left * right) & 0xFF


def bits8_neg(value: int) -> int:
    return (-value) & 0xFF


def bits8_div(left: int, right: int) -> int:
    right &= 0xFF
    if right == 0:
        raise ZeroDivisionError("Bits8 division by zero")
    return (left & 0xFF) // right


def bits8_rem(left: int, right: int) -> int:
    right &= 0xFF
    if right == 0:
        raise ZeroDivisionError("Bits8 remainder by zero")
    return (left & 0xFF) % right


def bits8_and(left: int, right: int) -> int:
    return (left & right) & 0xFF


def bits8_or(left: int, right: int) -> int:
    return (left | right) & 0xFF


def bits8_xor(left: int, right: int) -> int:
    return (left ^ right) & 0xFF


def bits8_complement(value: int) -> int:
    return value ^ 0xFF


def bits8_shift_left(value: int, amount: int) -> int:
    return ((value & 0xFF) << amount) & 0xFF if amount < 8 else 0


def bits8_shift_right(value: int, amount: int) -> int:
    return (value & 0xFF) >> amount if amount < 8 else 0


def bits8_test_bit(value: int, bit: int) -> bool:
    if not 0 <= bit < 8:
        raise ScalarDomainError(f"Bits8 bit index out of range: {bit}")
    return bool((value & 0xFF) & (1 << bit))


def bits8_set_bit(value: int, bit: int) -> int:
    if not 0 <= bit < 8:
        raise ScalarDomainError(f"Bits8 bit index out of range: {bit}")
    return (value | (1 << bit)) & 0xFF


def bits8_clear_bit(value: int, bit: int) -> int:
    if not 0 <= bit < 8:
        raise ScalarDomainError(f"Bits8 bit index out of range: {bit}")
    return (value & ~(1 << bit)) & 0xFF


class X86Scalar:
    """Tiny scalar-only encoder used by compact-format acceptance programs."""
    def __init__(self) -> None:
        self.code = bytearray()
        self.data: list[tuple[str, bytes]] = []
        self.data_names: set[str] = set()
        self.labels: dict[str, int] = {}
        self.data_patches: list[tuple[int, str]] = []
        self.code_patches: list[tuple[int, str]] = []

    def emit(self, value: bytes) -> None:
        self.code.extend(value)

    def label(self, name: str) -> None:
        if name in self.labels:
            raise ValueError(f"duplicate code label {name}")
        self.labels[name] = len(self.code)

    def branch(self, opcode: bytes, label: str) -> None:
        self.emit(opcode)
        position = len(self.code)
        self.emit(b"\x00" * 4)
        self.code_patches.append((position, label))

    def data_bytes(self, name: str, value: bytes) -> None:
        if name in self.data_names:
            raise ValueError(f"duplicate data label {name}")
        self.data_names.add(name)
        self.data.append((name, value))

    def lea_rsi_rip(self, label: str) -> None:
        self.emit(b"\x48\x8d\x35")
        position = len(self.code)
        self.emit(b"\x00" * 4)
        self.data_patches.append((position, label))

    def mov_eax(self, value: int) -> None:
        self.emit(b"\xb8" + struct.pack("<I", value & 0xFFFFFFFF))

    def mov_ecx(self, value: int) -> None:
        self.emit(b"\xb9" + struct.pack("<I", value & 0xFFFFFFFF))

    def mov_edi(self, value: int) -> None:
        self.emit(b"\xbf" + struct.pack("<I", value & 0xFFFFFFFF))

    def mov_edx(self, value: int) -> None:
        self.emit(b"\xba" + struct.pack("<I", value & 0xFFFFFFFF))

    def loadss_indexed_rax(self, xmm: int) -> None:
        self.emit(b"\xf3\x0f\x10" + bytes([0x04 | (xmm << 3), 0x86]))

    def loadss_indexed_rcx(self, xmm: int) -> None:
        self.emit(b"\xf3\x0f\x10" + bytes([0x04 | (xmm << 3), 0x8e]))

    def loadss_indexed_rdx(self, xmm: int) -> None:
        self.emit(b"\xf3\x0f\x10" + bytes([0x04 | (xmm << 3), 0x96]))

    def loadss_rip(self, xmm: int, label: str) -> None:
        self.emit(b"\xf3\x0f\x10" + bytes([0x05 | (xmm << 3)]))
        position = len(self.code)
        self.emit(b"\x00" * 4)
        self.data_patches.append((position, label))

    def movss(self, destination: int, source: int) -> None:
        self.emit(b"\xf3\x0f\x10" + bytes([0xC0 | (destination << 3) | source]))

    def addss(self, destination: int, source: int) -> None:
        self.emit(b"\xf3\x0f\x58" + bytes([0xC0 | (destination << 3) | source]))

    def subss(self, destination: int, source: int) -> None:
        self.emit(b"\xf3\x0f\x5c" + bytes([0xC0 | (destination << 3) | source]))

    def mulss(self, destination: int, source: int) -> None:
        self.emit(b"\xf3\x0f\x59" + bytes([0xC0 | (destination << 3) | source]))

    def divss(self, destination: int, source: int) -> None:
        self.emit(b"\xf3\x0f\x5e" + bytes([0xC0 | (destination << 3) | source]))

    def sqrtss(self, destination: int, source: int) -> None:
        self.emit(b"\xf3\x0f\x51" + bytes([0xC0 | (destination << 3) | source]))

    def movd_eax_xmm0(self) -> None:
        self.emit(b"\x66\x0f\x7e\xc0")

    def movd_xmm0_eax(self) -> None:
        self.emit(b"\x66\x0f\x6e\xc0")

    def movd_xmm_eax(self, destination: int) -> None:
        self.emit(b"\x66\x0f\x6e" + bytes([0xC0 | (destination << 3)]))

    def comiss_0_1(self) -> None:
        self.emit(b"\x0f\x2f\xc1")

    def sub_rsp(self, size: int) -> None:
        self.emit(b"\x48\x81\xec" + struct.pack("<I", size))

    def add_rsp(self, size: int) -> None:
        self.emit(b"\x48\x81\xc4" + struct.pack("<I", size))

    def rsi_from_rsp(self) -> None:
        self.emit(b"\x48\x89\xe6")

    def syscall(self) -> None:
        self.emit(b"\x0f\x05")

    def finish(self) -> bytes:
        while len(self.code) % 4:
            self.code.append(0x90)
        body = bytearray(self.code)
        data_labels: dict[str, int] = {}
        for label, value in self.data:
            data_labels[label] = len(body)
            body.extend(value)
        for position, label in self.data_patches:
            if label not in data_labels:
                raise ValueError(f"missing data label {label}")
            body[position:position + 4] = struct.pack("<i", data_labels[label] - (position + 4))
        for position, label in self.code_patches:
            if label not in self.labels:
                raise ValueError(f"missing code label {label}")
            body[position:position + 4] = struct.pack("<i", self.labels[label] - (position + 4))
        return bytes(body)


def _decode_table(fmt: SignedFloatFormat) -> bytes:
    items = 1 << (fmt.sign_shift + 1)
    return b"".join(struct.pack("<f", f32(fmt.decode(code))) for code in range(items))


def _threshold_table(fmt: SignedFloatFormat) -> bytes:
    return b"".join(struct.pack("<f", f32(value)) for value in fmt.thresholds)


def _emit_decode(machine: X86Scalar, table: str, payload: int, xmm: int) -> None:
    machine.mov_eax(payload)
    machine.lea_rsi_rip(table)
    machine.loadss_indexed_rax(xmm)


def _emit_quantize(machine: X86Scalar, fmt: SignedFloatFormat, *, saturating: bool | None) -> None:
    prefix = fmt.name.lower().replace(" ", "_")
    threshold_table = prefix + "_thresholds"
    overflow_constant = prefix + "_overflow"
    machine.data_bytes(threshold_table, _threshold_table(fmt))
    machine.data_bytes(overflow_constant, struct.pack("<f", f32(fmt.overflow_threshold)))

    # Keep sign in ebx and absolute binary32 bits in eax/xmm0.
    machine.movd_eax_xmm0()
    machine.emit(b"\x89\xc3")                    # mov ebx, eax
    machine.emit(b"\xc1\xeb\x1f")                # shr ebx, 31
    machine.emit(b"\x25\xff\xff\xff\x7f")        # and eax, 0x7fffffff
    machine.emit(b"\x3d\x00\x00\x80\x7f")        # cmp eax, 0x7f800000
    machine.branch(b"\x0f\x87", prefix + "_nan")
    machine.branch(b"\x0f\x84", prefix + "_inf")
    machine.movd_xmm0_eax()

    # Post-rounding overflow threshold.
    machine.loadss_rip(1, overflow_constant)
    machine.comiss_0_1()
    machine.branch(b"\x0f\x87", prefix + "_overflow")  # ja
    if fmt.overflow_tie_overflows:
        machine.branch(b"\x0f\x84", prefix + "_overflow")  # je

    # Binary-search midpoint thresholds. eax=low, ecx=high, edx=mid.
    machine.mov_eax(0)
    machine.mov_ecx(len(fmt.thresholds))
    machine.lea_rsi_rip(threshold_table)
    machine.label(prefix + "_loop")
    machine.emit(b"\x39\xc8")                     # cmp eax, ecx
    machine.branch(b"\x0f\x84", prefix + "_apply_sign")
    machine.emit(b"\x89\xc2")                     # mov edx, eax
    machine.emit(b"\x01\xca")                     # add edx, ecx
    machine.emit(b"\xd1\xea")                     # shr edx, 1
    machine.loadss_indexed_rdx(1)
    machine.comiss_0_1()
    machine.branch(b"\x0f\x82", prefix + "_set_high")  # x < threshold[mid]
    machine.branch(b"\x0f\x84", prefix + "_tie")       # x == threshold[mid]
    machine.emit(b"\x8d\x42\x01")                 # lea eax, [rdx + 1]
    machine.branch(b"\xe9", prefix + "_loop")

    machine.label(prefix + "_set_high")
    machine.emit(b"\x89\xd1")                     # mov ecx, edx
    machine.branch(b"\xe9", prefix + "_loop")

    machine.label(prefix + "_tie")
    machine.emit(b"\xf7\xc2\x01\x00\x00\x00")     # test edx, 1
    machine.emit(b"\x89\xd0")                     # mov eax, edx
    machine.branch(b"\x0f\x84", prefix + "_apply_sign")
    machine.emit(b"\xff\xc0")                     # inc eax
    machine.branch(b"\xe9", prefix + "_apply_sign")

    machine.label(prefix + "_overflow")
    if fmt.name in {"OFP8 E4M3", "OFP8 E5M2"} and saturating is None:
        raise ScalarDomainError(f"{fmt.name} x86 lowering requires explicit saturating mode")
    if fmt.name in {"OFP8 E4M3", "OFP8 E5M2"} and saturating:
        machine.mov_eax(fmt.finite_max_code)
        machine.branch(b"\xe9", prefix + "_apply_sign")
    elif fmt.infinity_code is not None:
        machine.mov_eax(fmt.infinity_code)
        machine.branch(b"\xe9", prefix + "_apply_sign")
    elif fmt.nan_code is not None:
        machine.mov_eax(fmt.nan_code)
        machine.branch(b"\xe9", prefix + "_done")
    else:
        machine.mov_eax(fmt.finite_max_code)
        machine.branch(b"\xe9", prefix + "_apply_sign")

    machine.label(prefix + "_inf")
    if fmt.name in {"OFP8 E4M3", "OFP8 E5M2"} and saturating:
        machine.mov_eax(fmt.finite_max_code)
        machine.branch(b"\xe9", prefix + "_apply_sign")
    elif fmt.infinity_code is not None:
        machine.mov_eax(fmt.infinity_code)
        machine.branch(b"\xe9", prefix + "_apply_sign")
    elif fmt.nan_code is not None:
        machine.mov_eax(fmt.nan_code)
        machine.branch(b"\xe9", prefix + "_done")
    else:
        machine.mov_eax(fmt.finite_max_code)
        machine.branch(b"\xe9", prefix + "_apply_sign")

    machine.label(prefix + "_nan")
    if fmt.nan_to_zero:
        machine.mov_eax(0)
    elif fmt.nan_code is not None:
        machine.mov_eax(fmt.nan_code)
    else:
        machine.mov_eax(0)
    machine.branch(b"\xe9", prefix + "_done")

    machine.label(prefix + "_apply_sign")
    machine.emit(b"\xc1\xe3" + bytes([fmt.sign_shift]))  # shl ebx, sign_shift
    machine.emit(b"\x09\xd8")                           # or eax, ebx
    machine.label(prefix + "_done")


def _emit_write_result(machine: X86Scalar, size: int) -> None:
    machine.sub_rsp(16)
    if size == 1:
        machine.emit(b"\x88\x04\x24")                   # mov [rsp], al
    elif size == 2:
        machine.emit(b"\x66\x89\x04\x24")               # mov [rsp], ax
    else:
        raise ValueError(size)
    machine.mov_eax(1)
    machine.mov_edi(1)
    machine.rsi_from_rsp()
    machine.mov_edx(size)
    machine.syscall()
    machine.add_rsp(16)
    machine.mov_eax(60)
    machine.emit(b"\x31\xff")                           # xor edi, edi
    machine.syscall()


def build_float_binary_elf(fmt: SignedFloatFormat, left_payload: int, right_payload: int,
                           operation: str, *, saturating: bool | None = None) -> bytes:
    """Build a direct ELF that decodes, computes with scalar SSE, and re-quantizes."""
    if operation not in {"add", "sub", "mul", "div"}:
        raise ValueError(f"unsupported scalar float operation {operation}")
    if fmt.name in {"OFP8 E4M3", "OFP8 E5M2"} and saturating is None:
        raise ScalarDomainError(f"{fmt.name} arithmetic requires explicit saturating mode")
    table = fmt.name.lower().replace(" ", "_") + "_decode"
    machine = X86Scalar()
    machine.data_bytes(table, _decode_table(fmt))
    _emit_decode(machine, table, left_payload, 0)
    _emit_decode(machine, table, right_payload, 1)
    {"add": machine.addss, "sub": machine.subss, "mul": machine.mulss,
     "div": machine.divss}[operation](0, 1)
    _emit_quantize(machine, fmt, saturating=saturating)
    _emit_write_result(machine, 2 if fmt is FLOAT16 else 1)
    return write_elf(machine.finish())


def build_float_unary_elf(fmt: SignedFloatFormat, payload: int, operation: str,
                          *, saturating: bool | None = None) -> bytes:
    """Build a direct ELF for one unary Float32-carried compact operation."""
    if operation != "sqrt":
        raise ValueError(f"unsupported scalar float unary operation {operation}")
    if fmt.name in {"OFP8 E4M3", "OFP8 E5M2"} and saturating is None:
        raise ScalarDomainError(f"{fmt.name} arithmetic requires explicit saturating mode")
    table = fmt.name.lower().replace(" ", "_") + "_decode"
    machine = X86Scalar()
    machine.data_bytes(table, _decode_table(fmt))
    _emit_decode(machine, table, payload, 0)
    machine.sqrtss(0, 0)
    _emit_quantize(machine, fmt, saturating=saturating)
    _emit_write_result(machine, 2 if fmt is FLOAT16 else 1)
    return write_elf(machine.finish())


def _emit_ootomo_e5m3_decode(machine: X86Scalar, payload: int, xmm: int) -> None:
    """Decode one Ootomo–Naruse OotomoE5M3 storage byte to its Float32 midpoint."""
    machine.mov_eax(payload & 0xFF)
    machine.emit(b"\xc1\xe0\x14")                   # shl eax, 20
    machine.emit(b"\x05\x00\x00\x08\x38")       # add eax, 0x38080000
    machine.movd_xmm_eax(xmm)


def _emit_ootomo_e5m3_encode(machine: X86Scalar) -> None:
    """Encode positive-normal Float32 in xmm0 with the published storage map."""
    machine.movd_eax_xmm0()
    machine.emit(b"\x2d\x00\x00\x00\x38")       # sub eax, 0x38000000
    machine.emit(b"\xc1\xe8\x14")                   # shr eax, 20
    machine.emit(b"\x25\xff\x00\x00\x00")       # and eax, 0xff


def build_ootomo_e5m3_binary_elf(left_payload: int, right_payload: int, operation: str) -> bytes:
    """Test-only OotomoE5M3 observation: decode, one Float32 operation, then encode."""
    if operation not in {"add", "sub", "mul", "div"}:
        raise ValueError(f"unsupported OotomoE5M3 observation operation {operation}")
    machine = X86Scalar()
    _emit_ootomo_e5m3_decode(machine, left_payload, 0)
    _emit_ootomo_e5m3_decode(machine, right_payload, 1)
    {"add": machine.addss, "sub": machine.subss, "mul": machine.mulss,
     "div": machine.divss}[operation](0, 1)
    _emit_ootomo_e5m3_encode(machine)
    _emit_write_result(machine, 1)
    return write_elf(machine.finish())


def build_ootomo_e5m3_unary_elf(payload: int, operation: str) -> bytes:
    """Test-only unary OotomoE5M3 observation; this is not a language arithmetic primitive."""
    if operation != "sqrt":
        raise ValueError(f"unsupported OotomoE5M3 observation operation {operation}")
    machine = X86Scalar()
    _emit_ootomo_e5m3_decode(machine, payload, 0)
    machine.sqrtss(0, 0)
    _emit_ootomo_e5m3_encode(machine)
    _emit_write_result(machine, 1)
    return write_elf(machine.finish())


def build_ootomo_e5m3_roundtrip_elf() -> bytes:
    """Build one direct ELF that applies Figures 4 then 3 to all 256 payloads."""
    machine = X86Scalar()
    machine.sub_rsp(272)
    for code in range(256):
        machine.mov_eax(code)
        machine.emit(b"\xc1\xe0\x14")                   # shl eax, 20
        machine.emit(b"\x05\x00\x00\x08\x38")           # add eax, 0x38080000
        machine.movd_xmm0_eax()
        machine.movd_eax_xmm0()
        machine.emit(b"\x2d\x00\x00\x00\x38")           # sub eax, 0x38000000
        machine.emit(b"\xc1\xe8\x14")                   # shr eax, 20
        machine.emit(b"\x25\xff\x00\x00\x00")           # and eax, 0xff
        machine.emit(b"\x88\x84\x24" + struct.pack("<i", code))
    machine.mov_eax(1)
    machine.mov_edi(1)
    machine.rsi_from_rsp()
    machine.mov_edx(256)
    machine.syscall()
    machine.add_rsp(272)
    machine.mov_eax(60)
    machine.emit(b"\x31\xff")
    machine.syscall()
    return write_elf(machine.finish())


def build_bits8_binary_elf(left: int, right: int, operation: str) -> bytes:
    """Build direct scalar-GPR Bits8 arithmetic and write the low byte."""
    left &= 0xFF
    right &= 0xFF
    machine = X86Scalar()
    machine.mov_eax(left)
    machine.mov_ecx(right)
    if operation == "add":
        machine.emit(b"\x01\xc8")                       # add eax, ecx
    elif operation == "sub":
        machine.emit(b"\x29\xc8")                       # sub eax, ecx
    elif operation == "mul":
        machine.emit(b"\x0f\xaf\xc1")                   # imul eax, ecx
    elif operation in {"div", "rem"}:
        if right == 0:
            raise ZeroDivisionError("Bits8 division by zero")
        machine.emit(b"\x31\xd2")                       # xor edx, edx
        machine.emit(b"\xf7\xf1")                       # div ecx
        if operation == "rem":
            machine.emit(b"\x89\xd0")                   # mov eax, edx
    elif operation == "and":
        machine.emit(b"\x21\xc8")
    elif operation == "or":
        machine.emit(b"\x09\xc8")
    elif operation == "xor":
        machine.emit(b"\x31\xc8")
    elif operation == "neg":
        machine.emit(b"\xf7\xd8")                       # neg eax
    elif operation == "complement":
        machine.emit(b"\x35\xff\x00\x00\x00")           # xor eax, 0xff
    elif operation == "shl":
        if right >= 8:
            machine.mov_eax(0)
        else:
            machine.emit(b"\xd3\xe0")                   # shl eax, cl
    elif operation == "shr":
        if right >= 8:
            machine.mov_eax(0)
        else:
            machine.emit(b"\xd3\xe8")                   # shr eax, cl
    elif operation in {"lt", "eq"}:
        machine.emit(b"\x39\xc8")                       # cmp eax, ecx
        machine.emit(b"\x0f\x92\xc0" if operation == "lt" else b"\x0f\x94\xc0")
        machine.emit(b"\x0f\xb6\xc0")                   # movzx eax, al
    else:
        raise ValueError(f"unsupported Bits8 operation {operation}")
    machine.emit(b"\x25\xff\x00\x00\x00")               # and eax, 0xff
    _emit_write_result(machine, 1)
    return write_elf(machine.finish())
