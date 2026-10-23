"""Cross-compile the existing checked Idriç scalar program to NetBSD x86-64.

Unlike the target-only file reader fixture, this uses the checked one-step
artifact parser and the regular scalar lowering. This is not yet generic
source-level Idriç File/Buffer/IO support.
"""
import struct
import unittest

from backend.elf64 import NETBSD_10_ABI_VERSION
from backend.idric_x86 import (
    UnsupportedError, compile_checked_artifact, parse_checked_artifact_bytes,
)
from test_checked_scalar import artifact_bytes, put_char_main


class CheckedNetBSDScalarTests(unittest.TestCase):
    def test_checked_scalar_netbsd_emits_syscalls_and_native_note(self):
        artifact = parse_checked_artifact_bytes(artifact_bytes([put_char_main("PrintX", "'X'")]))
        result = compile_checked_artifact(artifact, target="netbsd-x86_64")
        self.assertEqual(struct.unpack_from("<H", result.elf, 56)[0], 2)
        self.assertEqual(result.elf[0x200:0x218],
                         struct.pack("<III", 7, 4, 1) + b"NetBSD\0\0" +
                         struct.pack("<I", NETBSD_10_ABI_VERSION))
        self.assertIn("mov rax, 4 ; SYS_write", result.listing)
        self.assertIn("mov rax, 1 ; SYS_exit", result.listing)
        self.assertIn("jc process:write_failure", result.listing)
        self.assertIn("netbsd-x86_64_syscall", result.listing)
        self.assertEqual(result.elf, compile_checked_artifact(
            artifact, target="netbsd-x86_64").elf)

    def test_linux_checked_output_keeps_existing_behavior(self):
        artifact = parse_checked_artifact_bytes(artifact_bytes([put_char_main("PrintX", "'X'")]))
        first = compile_checked_artifact(artifact)
        second = compile_checked_artifact(artifact, target="linux-x86_64")
        self.assertEqual(first.elf, second.elf)
        self.assertEqual(first.listing, second.listing)
        self.assertIn("mov rax, 1 ; SYS_write", first.listing)
        self.assertIn("mov rax, 60 ; SYS_exit", first.listing)
        self.assertNotIn("jc process:write_failure", first.listing)

    def test_bad_target_refuses(self):
        artifact = parse_checked_artifact_bytes(artifact_bytes([put_char_main("PrintX", "'X'")]))
        with self.assertRaises(UnsupportedError):
            compile_checked_artifact(artifact, target="unknown-x86")


if __name__ == "__main__":
    unittest.main()
