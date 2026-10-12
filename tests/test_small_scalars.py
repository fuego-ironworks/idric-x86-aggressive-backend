import math
import platform
import struct
import subprocess
import tempfile
import unittest
from pathlib import Path

import backend.small_scalars as scalar_api

from backend.small_scalars import (
    E3M2,
    FLOAT16,
    OFP8_E4M3,
    OFP8_E5M2,
    ScalarDomainError,
    bits8_add,
    bits8_clear_bit,
    bits8_complement,
    bits8_div,
    bits8_mul,
    bits8_neg,
    bits8_rem,
    bits8_set_bit,
    bits8_shift_left,
    bits8_shift_right,
    bits8_sub,
    bits8_test_bit,
    build_bits8_binary_elf,
    build_ootomo_e5m3_roundtrip_elf,
    build_float_binary_elf,
    ootomo_e5m3_decode_f32,
    ootomo_e5m3_encode_f32,
)


def run_elf(image: bytes) -> bytes:
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "candidate"
        path.write_bytes(image)
        path.chmod(0o755)
        run = subprocess.run([path], capture_output=True, check=False)
    if run.returncode != 0:
        raise AssertionError(f"candidate exit={run.returncode} stderr={run.stderr!r}")
    return run.stdout


class SmallScalarCodecTest(unittest.TestCase):
    def test_ambiguous_storage_api_is_absent(self):
        for name in (
            "e5m3_encode_f32", "e5m3_decode_f32",
            "build_e5m3_binary_elf", "build_e5m3_unary_elf",
            "build_e5m3_roundtrip_elf",
        ):
            self.assertFalse(hasattr(scalar_api, name), name)

    def test_equal_storage_width_does_not_mean_equal_numbers(self):
        # The same byte is three different numbers in the three codebooks.
        self.assertEqual(OFP8_E4M3.decode(0x78), 256.0)
        self.assertEqual(OFP8_E5M2.decode(0x78), 32768.0)
        self.assertEqual(ootomo_e5m3_decode_f32(0x78), 1.0625)
        self.assertEqual(OFP8_E4M3.encode_f32(-1.0, saturating=True), 0xB8)
        self.assertEqual(OFP8_E5M2.encode_f32(-1.0, saturating=True), 0xBC)
        with self.assertRaises(ScalarDomainError):
            ootomo_e5m3_encode_f32(-1.0)

    def test_float16_boundaries(self):
        self.assertEqual(FLOAT16.encode_f32(0.0), 0x0000)
        self.assertEqual(FLOAT16.encode_f32(-0.0), 0x8000)
        self.assertEqual(FLOAT16.encode_f32(1.0), 0x3C00)
        self.assertEqual(FLOAT16.encode_f32(2.0 ** -24), 0x0001)
        self.assertEqual(FLOAT16.encode_f32(2.0 ** -25), 0x0000)
        self.assertEqual(FLOAT16.encode_f32(3.0 * 2.0 ** -25), 0x0002)
        self.assertEqual(FLOAT16.encode_f32(65504.0), 0x7BFF)
        self.assertEqual(FLOAT16.encode_f32(math.inf), 0x7C00)
        self.assertEqual(FLOAT16.encode_f32(-math.inf), 0xFC00)
        self.assertEqual(FLOAT16.encode_f32(math.nan), 0x7E00)

    def test_fp8_boundaries(self):
        self.assertEqual(OFP8_E4M3.encode_f32(1.0, saturating=False), 0x38)
        self.assertEqual(OFP8_E4M3.encode_f32(2.0 ** -9, saturating=False), 0x01)
        self.assertEqual(OFP8_E4M3.encode_f32(448.0, saturating=False), 0x7E)
        self.assertEqual(OFP8_E4M3.encode_f32(math.inf, saturating=False), 0x7F)
        self.assertEqual(OFP8_E4M3.encode_f32(math.inf, saturating=True), 0x7E)
        self.assertEqual(OFP8_E5M2.encode_f32(1.0, saturating=False), 0x3C)
        self.assertEqual(OFP8_E5M2.encode_f32(2.0 ** -16, saturating=False), 0x01)
        self.assertEqual(OFP8_E5M2.encode_f32(57344.0, saturating=False), 0x7B)
        self.assertEqual(OFP8_E5M2.encode_f32(math.inf, saturating=False), 0x7C)
        self.assertEqual(OFP8_E5M2.encode_f32(math.inf, saturating=True), 0x7B)
        with self.assertRaises(ScalarDomainError):
            OFP8_E4M3.encode_f32(1.0)

    def test_e3m2_boundaries(self):
        self.assertEqual(E3M2.encode_f32(0.0), 0x00)
        self.assertEqual(E3M2.encode_f32(-0.0), 0x20)
        self.assertEqual(E3M2.encode_f32(0.0625), 0x01)
        self.assertEqual(E3M2.encode_f32(1.0), 0x0C)
        self.assertEqual(E3M2.encode_f32(28.0), 0x1F)
        self.assertEqual(E3M2.encode_f32(math.inf), 0x1F)
        self.assertEqual(E3M2.encode_f32(-math.inf), 0x3F)
        self.assertEqual(E3M2.encode_f32(math.nan), 0x00)

    def test_finite_payloads_roundtrip(self):
        cases = [
            (FLOAT16, None),
            (OFP8_E4M3, False),
            (OFP8_E5M2, False),
            (E3M2, None),
        ]
        for fmt, saturating in cases:
            for payload in range(fmt.payload_mask + 1):
                if fmt.is_nan_payload(payload) or fmt.is_infinity_payload(payload):
                    continue
                value = fmt.decode(payload)
                kwargs = {} if saturating is None else {"saturating": saturating}
                self.assertEqual(fmt.encode_f32(value, **kwargs), payload, (fmt.name, payload, value))

    def test_ootomo_e5m3_exact_recipe(self):
        self.assertEqual(ootomo_e5m3_encode_f32(1.0), 0x78)
        self.assertEqual(ootomo_e5m3_decode_f32(0x78), 1.0625)
        for code in range(256):
            self.assertEqual(ootomo_e5m3_encode_f32(ootomo_e5m3_decode_f32(code)), code)
        for invalid in (0.0, -1.0, math.inf, math.nan):
            with self.assertRaises(ScalarDomainError):
                ootomo_e5m3_encode_f32(invalid)

    def test_bits8_contract(self):
        self.assertEqual(bits8_add(255, 1), 0)
        self.assertEqual(bits8_sub(0, 1), 255)
        self.assertEqual(bits8_mul(255, 255), 1)
        self.assertEqual(bits8_neg(1), 255)
        self.assertEqual(bits8_div(255, 2), 127)
        self.assertEqual(bits8_rem(255, 2), 1)
        self.assertEqual(bits8_complement(0xA5), 0x5A)
        self.assertEqual(bits8_shift_left(0x81, 1), 0x02)
        self.assertEqual(bits8_shift_right(0x81, 1), 0x40)
        self.assertEqual(bits8_shift_left(1, 8), 0)
        self.assertTrue(bits8_test_bit(0x80, 7))
        self.assertEqual(bits8_set_bit(0, 3), 8)
        self.assertEqual(bits8_clear_bit(0xFF, 3), 0xF7)


@unittest.skipUnless(platform.system() == "Linux" and platform.machine() == "x86_64",
                     "direct compact-scalar acceptance requires x86_64 Linux")
class SmallScalarNativeTest(unittest.TestCase):
    def test_signed_fp8_every_byte_executes_in_its_own_codebook(self):
        # Independently specified OCP classifications; the generated program
        # decodes, multiplies by +1 in scalar binary32, and encodes the result.
        # No Python codec computes the expected byte.
        formats = (
            (OFP8_E4M3, 0x38, 0x7F, 0x7F, None, 0x7E),
            (OFP8_E5M2, 0x3C, 0x7D, 0x7D, 0x7C, 0x7B),
        )
        for fmt, one, first_nan, canonical_nan, infinity, maximum in formats:
            for saturating in (False, True):
                for payload in range(256):
                    magnitude = payload & 0x7F
                    if magnitude >= first_nan:
                        expected = canonical_nan
                    elif saturating and magnitude == infinity:
                        expected = (payload & 0x80) | maximum
                    else:
                        expected = payload
                    with self.subTest(format=fmt.name, mode=saturating, byte=payload):
                        image = build_float_binary_elf(
                            fmt, payload, one, "mul", saturating=saturating
                        )
                        self.assertEqual(run_elf(image), bytes([expected]))

    def test_float_formats_compute_and_requantize_in_x86(self):
        cases = [
            (FLOAT16, 0x3E00, 0x3800, "add", None, 0x4000),
            (OFP8_E4M3, 0x3C, 0x30, "add", False, 0x40),
            (OFP8_E5M2, 0x3E, 0x40, "mul", False, 0x42),
            (E3M2, 0x0D, 0x08, "add", None, 0x0F),
        ]
        for fmt, left, right, op, saturating, expected in cases:
            with self.subTest(fmt=fmt.name):
                kwargs = {} if saturating is None else {"saturating": saturating}
                out = run_elf(build_float_binary_elf(fmt, left, right, op, **kwargs))
                if fmt is FLOAT16:
                    self.assertEqual(struct.unpack("<H", out)[0], expected)
                else:
                    self.assertEqual(out, bytes([expected]))

    def test_float_formats_special_values_overflow_and_ties_in_x86(self):
        cases = [
            (FLOAT16, 0x7BFF, 0x4000, "mul", None, 0x7C00),
            (FLOAT16, 0x7C00, 0x0000, "mul", None, 0x7E00),
            (FLOAT16, 0x3C00, FLOAT16.encode_f32(2.0 ** -11), "add", None, 0x3C00),
            (OFP8_E4M3, 0x7E, 0x40, "mul", False, 0x7F),
            (OFP8_E4M3, 0x7E, 0x40, "mul", True, 0x7E),
            (OFP8_E4M3, 0x38, OFP8_E4M3.encode_f32(0.0625, saturating=False), "add", False, 0x38),
            (OFP8_E5M2, 0x7B, 0x40, "mul", False, 0x7C),
            (OFP8_E5M2, 0x7B, 0x40, "mul", True, 0x7B),
            (E3M2, 0x1F, 0x10, "mul", None, 0x1F),
            (FLOAT16, 0xBC00, 0x3800, "add", None, 0xB800),
        ]
        for fmt, left, right, op, saturating, expected in cases:
            with self.subTest(fmt=fmt.name, op=op, saturating=saturating):
                kwargs = {} if saturating is None else {"saturating": saturating}
                out = run_elf(build_float_binary_elf(fmt, left, right, op, **kwargs))
                if fmt is FLOAT16:
                    self.assertEqual(struct.unpack("<H", out)[0], expected)
                else:
                    self.assertEqual(out, bytes([expected]))

    def test_ootomo_e5m3_figures_roundtrip_all_payloads_in_x86(self):
        self.assertEqual(run_elf(build_ootomo_e5m3_roundtrip_elf()), bytes(range(256)))

    def test_bits8_machine_arithmetic(self):
        cases = [
            (255, 1, "add", 0),
            (0, 1, "sub", 255),
            (255, 255, "mul", 1),
            (255, 2, "div", 127),
            (255, 2, "rem", 1),
            (0xA5, 0x0F, "and", 0x05),
            (0xA5, 0x0F, "or", 0xAF),
            (0xA5, 0x0F, "xor", 0xAA),
            (1, 0, "neg", 255),
            (0xA5, 0, "complement", 0x5A),
            (0x81, 1, "shl", 0x02),
            (0x81, 1, "shr", 0x40),
            (1, 8, "shl", 0),
            (1, 2, "lt", 1),
            (2, 1, "lt", 0),
            (255, 255, "eq", 1),
        ]
        for left, right, op, expected in cases:
            with self.subTest(op=op):
                self.assertEqual(run_elf(build_bits8_binary_elf(left, right, op)), bytes([expected]))


if __name__ == "__main__":
    unittest.main()
