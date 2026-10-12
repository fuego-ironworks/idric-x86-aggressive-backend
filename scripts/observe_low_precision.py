#!/usr/bin/env python3
import argparse
import math
from dataclasses import dataclass
from fractions import Fraction
from functools import lru_cache
import platform
import subprocess
import tempfile
from pathlib import Path

from backend.small_scalars import (
    E3M2,
    FLOAT16,
    OFP8_E4M3,
    OFP8_E5M2,
    ScalarDomainError,
    SignedFloatFormat,
    build_ootomo_e5m3_binary_elf,
    build_ootomo_e5m3_unary_elf,
    build_float_binary_elf,
    build_float_unary_elf,
    ootomo_e5m3_decode_f32,
    ootomo_e5m3_encode_f32,
)


E3M2_DIRECTION = [
    0x08,0x24,0x04,0x28,0x04,0x24,0x04,0x08,0x28,0x04,0x24,0x04,
    0x01,0x21,0x01,0x21,0x01,0x21,0x01,0x21,0x01,0x21,0x01,0x21,0x01,0x21,
]

E3M2_JACOBIAN = [
    [0x0c,0x29,0x03,0x00,0x00,0x00,0x27,0x00,0x00,0x0c,0x0c,0x00,0x2a,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00],
    [0x0c,0x26,0x01,0x00,0x00,0x00,0x24,0x00,0x00,0x0c,0x0c,0x00,0x00,0x29,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00],
    [0x0c,0x23,0x00,0x00,0x00,0x00,0x22,0x00,0x00,0x0c,0x0c,0x00,0x00,0x00,0x29,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00],
    [0x0c,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x0c,0x0c,0x00,0x00,0x00,0x00,0x27,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00],
    [0x0c,0x03,0x00,0x00,0x00,0x00,0x01,0x00,0x00,0x0c,0x0c,0x00,0x00,0x00,0x00,0x00,0x26,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00],
    [0x0c,0x05,0x01,0x00,0x00,0x00,0x02,0x00,0x00,0x0c,0x0c,0x00,0x00,0x00,0x00,0x00,0x00,0x24,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00],
    [0x0c,0x08,0x02,0x00,0x00,0x00,0x02,0x00,0x00,0x0c,0x0c,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x23,0x00,0x00,0x00,0x00,0x00,0x00,0x00],
    [0x00,0x00,0x00,0x0c,0x28,0x02,0x00,0x00,0x00,0x0c,0x00,0x0c,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00],
    [0x00,0x00,0x00,0x0c,0x25,0x01,0x01,0x00,0x00,0x0c,0x00,0x0c,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x02,0x00,0x00,0x00,0x00,0x00],
    [0x00,0x00,0x00,0x0c,0x23,0x00,0x01,0x00,0x00,0x0c,0x00,0x0c,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x03,0x00,0x00,0x00,0x00],
    [0x00,0x00,0x00,0x0c,0x00,0x00,0x00,0x00,0x00,0x0c,0x00,0x0c,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x05,0x00,0x00,0x00],
    [0x00,0x00,0x00,0x0c,0x03,0x00,0x21,0x00,0x00,0x0c,0x00,0x0c,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x07,0x00,0x00],
    [0x00,0x00,0x00,0x0c,0x06,0x01,0x23,0x00,0x00,0x0c,0x00,0x0c,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x08,0x00],
    [0x00,0x00,0x00,0x0c,0x09,0x03,0x26,0x00,0x00,0x0c,0x00,0x0c,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x09],
]

DAKOTA_JV_REFERENCE = [
    0.5451388472381876,0.5927113334029114,0.4895670078008919,
    0.5289665323810473,0.45372048698916667,0.4731549501099014,
    0.42261242702714896,-0.15357695334574295,-0.08165240496535331,
    -0.05029350042927705,0.018128930745486826,-0.0026210159607702455,
    0.058420594931340275,-0.046822717143564785,
]

DAKOTA_ROW_NAMES = [
    "driver.camber[0]","driver.camber[1]","driver.camber[2]","driver.camber[3]",
    "driver.camber[4]","driver.camber[5]","driver.camber[6]",
    "passenger.camber[0]","passenger.camber[1]","passenger.camber[2]",
    "passenger.camber[3]","passenger.camber[4]","passenger.camber[5]",
    "passenger.camber[6]",
]


@dataclass(frozen=True)
class Format:
    key: str
    signed: SignedFloatFormat | None

    @property
    def width(self) -> int:
        return 2 if self.signed is FLOAT16 else 1

    @property
    def saturating(self) -> bool | None:
        if self.signed in {OFP8_E4M3, OFP8_E5M2}:
            return True
        return None

    def encode(self, value: float) -> int:
        if self.signed is None:
            return ootomo_e5m3_encode_f32(value)
        return self.signed.encode_f32(value, saturating=self.saturating)

    def decode(self, payload: int) -> float:
        if self.signed is None:
            return ootomo_e5m3_decode_f32(payload)
        return self.signed.decode(payload)


FORMATS = [
    Format("Float16", FLOAT16),
    Format("E4M3", OFP8_E4M3),
    Format("E5M2", OFP8_E5M2),
    Format("E3M2", E3M2),
    Format("OotomoE5M3", None),
]


def dyadic(value: float) -> str:
    fraction = Fraction(value).limit_denominator(16)
    if float(fraction) != value:
        return f"{value:.6f}"
    if fraction.denominator == 1:
        return str(fraction.numerator)
    return f"{fraction.numerator}/{fraction.denominator}"


def reference_text(value: float) -> str:
    fraction = Fraction(value).limit_denominator(16)
    if abs(float(fraction) - value) < 1e-12:
        if fraction.denominator == 1:
            return str(fraction.numerator)
        return f"{fraction.numerator}/{fraction.denominator}"
    return f"{value:.6f}"


def run_payload(image: bytes, width: int) -> int:
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "candidate"
        path.write_bytes(image)
        path.chmod(0o755)
        run = subprocess.run([path], capture_output=True, check=False)
    if run.returncode != 0:
        raise SystemExit(f"candidate exit={run.returncode} stderr={run.stderr!r}")
    if len(run.stdout) != width:
        raise SystemExit(f"candidate emitted {len(run.stdout)} bytes, expected {width}")
    return int.from_bytes(run.stdout, "little")


@lru_cache(maxsize=None)
def binary(key: str, left: int, right: int, operation: str) -> int:
    fmt = next(item for item in FORMATS if item.key == key)
    if fmt.signed is None:
        image = build_ootomo_e5m3_binary_elf(left, right, operation)
    else:
        image = build_float_binary_elf(
            fmt.signed, left, right, operation, saturating=fmt.saturating
        )
    return run_payload(image, fmt.width)


@lru_cache(maxsize=None)
def unary(key: str, value: int, operation: str) -> int:
    fmt = next(item for item in FORMATS if item.key == key)
    if fmt.signed is None:
        image = build_ootomo_e5m3_unary_elf(value, operation)
    else:
        image = build_float_unary_elf(
            fmt.signed, value, operation, saturating=fmt.saturating
        )
    return run_payload(image, fmt.width)


CANONICAL_DIRECTION = [E3M2.decode(payload) for payload in E3M2_DIRECTION]
CANONICAL_JACOBIAN = [
    [E3M2.decode(payload) for payload in row] for row in E3M2_JACOBIAN
]


def dot(fmt: Format, coefficients: list[float], direction: list[float]) -> int:
    accumulator = fmt.encode(0.0)
    for coefficient, component in zip(coefficients, direction):
        left = fmt.encode(coefficient)
        right = fmt.encode(component)
        product = binary(fmt.key, left, right, "mul")
        accumulator = binary(fmt.key, product, accumulator, "add")
    return accumulator


def observed_payloads(fmt: Format) -> tuple[list[int | None], list[str | None]]:
    one_point_five = fmt.encode(1.5)
    one_half = fmt.encode(0.5)
    two = fmt.encode(2.0)

    square = binary(fmt.key, one_point_five, one_point_five, "mul")
    cube = binary(fmt.key, square, one_point_five, "mul")

    payloads: list[int | None] = [
        binary(fmt.key, one_point_five, one_half, "add"),
        binary(fmt.key, one_point_five, one_half, "sub"),
        binary(fmt.key, one_point_five, one_half, "mul"),
        binary(fmt.key, one_point_five, one_half, "div"),
        square,
        cube,
        unary(fmt.key, two, "sqrt"),
    ]
    statuses: list[str | None] = [None] * len(payloads)

    for row in CANONICAL_JACOBIAN:
        try:
            payloads.append(dot(fmt, row, CANONICAL_DIRECTION))
            statuses.append(None)
        except ScalarDomainError:
            payloads.append(None)
            statuses.append("domain")

    cosine = fmt.encode(7.0 / 8.0)
    sine = fmt.encode(3.0 / 8.0)
    x = fmt.encode(3.0)
    y = fmt.encode(4.0)
    cx = binary(fmt.key, cosine, x, "mul")
    sy = binary(fmt.key, sine, y, "mul")
    sx = binary(fmt.key, sine, x, "mul")
    cy = binary(fmt.key, cosine, y, "mul")
    payloads.extend([
        binary(fmt.key, cx, sy, "sub"),
        binary(fmt.key, sx, cy, "add"),
        binary(fmt.key, fmt.encode(4.0), fmt.encode(1.5), "mul"),
    ])
    statuses.extend([None, None, None])
    return payloads, statuses


def cases() -> list[tuple[str, float]]:
    theta = math.radians(360.0 / 17.4)
    caster_multiplier = 1.0 / (2.0 * math.sin(theta))
    x = 3.0
    y = 4.0
    return [
        ("add 1.5 + 0.5", 1.5 + 0.5),
        ("subtract 1.5 - 0.5", 1.5 - 0.5),
        ("multiply 1.5 * 0.5", 1.5 * 0.5),
        ("divide 1.5 / 0.5", 1.5 / 0.5),
        ("power 1.5^2", 1.5 ** 2),
        ("power 1.5^3", 1.5 ** 3),
        ("sqrt 2", math.sqrt(2.0)),
    ] + [
        ("Jv " + name, value)
        for name, value in zip(DAKOTA_ROW_NAMES, DAKOTA_JV_REFERENCE)
    ] + [
        ("rotate (3,4) x", math.cos(theta) * x - math.sin(theta) * y),
        ("rotate (3,4) y", math.sin(theta) * x + math.cos(theta) * y),
        ("caster from 4-degree swing", 4.0 * caster_multiplier),
    ]


def observe(fmt: Format) -> None:
    theta = math.radians(360.0 / 17.4)
    caster_multiplier = 1.0 / (2.0 * math.sin(theta))
    payloads, statuses = observed_payloads(fmt)

    print(f"{fmt.key} observational measurements: x86-64")
    print(f"theta={math.degrees(theta):.9f} deg  "
          f"sin={math.sin(theta):.9f}  cos={math.cos(theta):.9f}  "
          f"caster_multiplier={caster_multiplier:.9f}")
    print("All five formats receive the same source numerical inputs before quantization.")
    print("The numeric residue is observed - reference; it is reported, not graded.")
    print()
    print(f"{'case':31} {'payload':>8} {'reference':>12} {'observed':>12} {'residue':>12}")
    for (label, reference), payload, status in zip(cases(), payloads, statuses):
        if status is not None:
            print(f"{label:31} {'--':>8} {reference_text(reference):>12} "
                  f"{status:>12} {status:>12}")
            continue
        assert payload is not None
        observed = fmt.decode(payload)
        residue = observed - reference
        digits = 4 if fmt.width == 2 else 2
        print(f"{label:31} 0x{payload:0{digits}x} {reference_text(reference):>12} "
              f"{dyadic(observed):>12} {reference_text(residue):>12}")

    print()
    print("Shared Dakota fixture:")
    print("  the established E3M2 Jacobian/direction payloads are decoded once;")
    print("  those numerical values are then encoded independently in every format")
    print("  before every multiply/add is executed and requantized in that format.")
    print("  rotation inputs are the same for all formats: cos=7/8, sin=3/8, vector=[3,4].")
    print("  caster multiplier input is the same for all formats: 3/2.")
    if fmt.key == "OotomoE5M3":
        print("  OotomoE5M3 is unsigned positive-normal storage; zero/negative Jacobian inputs")
        print("  are reported as domain rather than silently omitted or assigned fake encodings.")
        print("  OotomoE5M3 arithmetic here is test-only: storage decode -> Float32 op -> storage encode.")
    print()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--format", choices=[item.key for item in FORMATS])
    args = parser.parse_args()

    if platform.system() != "Linux" or platform.machine() != "x86_64":
        print("low-precision observations require x86_64 Linux direct execution")
        return 0

    selected = FORMATS if args.format is None else [
        item for item in FORMATS if item.key == args.format
    ]
    for fmt in selected:
        try:
            observe(fmt)
        except ScalarDomainError as error:
            raise SystemExit(f"{fmt.key}: unexpected domain failure: {error}") from error
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
