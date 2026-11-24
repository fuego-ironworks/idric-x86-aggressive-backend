"""Adversarial IR; scripts/check_process_arguments.py runs the compiler-source witness."""
import platform
import subprocess
import tempfile
import unittest
from pathlib import Path

from backend.idric_x86 import (
    ARGUMENT_AT_FFI, ARGUMENT_COUNT_FFI, UnsupportedError,
    compile_checked_artifact, parse_checked_artifact_bytes,
)
from test_checked_scalar import artifact_bytes


def build(body, count_declaration=ARGUMENT_COUNT_FFI):
    definitions = [f"M.main = [0]: {body}", f"M.count = {count_declaration}",
                   f"M.arg = {ARGUMENT_AT_FFI}"]
    return compile_checked_artifact(parse_checked_artifact_bytes(artifact_bytes(definitions)))


class ProcessArgumentTests(unittest.TestCase):
    def test_foreign_abi_is_checked(self):
        body = "%let v1 = (M.count(v0)) in (Prelude.IO.prim__putChar(65, v0))"
        for declaration in (ARGUMENT_COUNT_FFI.replace("IORes Int", "IORes String"),
                            ARGUMENT_COUNT_FFI.replace("idris2_getArgCount", "unrelated")):
            with self.subTest(declaration=declaration), self.assertRaisesRegex(UnsupportedError, "unsupported_call"):
                build(body, declaration)

    def test_argument_address_and_unproved_count_cannot_be_written_as_bytes(self):
        with self.assertRaisesRegex(UnsupportedError, "string_as_scalar"):
            build("%let v1 = (M.arg(0, v0)) in (Prelude.IO.prim__putChar(v1, v0))")
        with self.assertRaisesRegex(UnsupportedError, "putchar_not_one_byte"):
            build("%let v1 = (M.count(v0)) in (Prelude.IO.prim__putChar(v1, v0))")

    def test_range_proof_checks_each_possible_runtime_arm(self):
        # A fake argc or checking only the first branch would incorrectly accept the bad write.
        with self.assertRaisesRegex(UnsupportedError, "putchar_not_one_byte"):
            build("%let v1 = (M.count(v0)) in (%case v1 of { "
                  "%constalt(1) => Prelude.IO.prim__putChar(65, v0) Just Prelude.IO.prim__putChar(300, v0) })")
        with self.assertRaisesRegex(UnsupportedError, "unreachable_case_reached"):
            build("%let v1 = (M.count(v0)) in (%case v1 of { "
                  "%constalt(1) => Prelude.IO.prim__putChar(65, v0) Nothing })")

    @unittest.skipUnless((platform.system(), platform.machine()) == ("Linux", "x86_64"), "Linux x86-64")
    def test_out_of_range_index_exits_before_next_effect(self):
        for index in ("-1", "v2", "9223372036854775807"):
            with self.subTest(index=index):
                compiled = build("%let v1 = (Prelude.IO.prim__putChar(33, v0)) in ("
                                 "%let v2 = (M.count(v0)) in ("
                                 f"%let v3 = (M.arg({index}, v0)) in (Prelude.IO.prim__putChar(88, v0))))")
                with tempfile.TemporaryDirectory() as work:
                    path = Path(work) / "invalid-index"
                    path.write_bytes(compiled.elf)
                    path.chmod(0o755)
                    run = subprocess.run([path, "one", "two"], capture_output=True)
                self.assertEqual((run.returncode, run.stdout, run.stderr), (2, b"!", b""))

    @unittest.skipUnless((platform.system(), platform.machine()) == ("Linux", "x86_64"), "Linux x86-64")
    def test_argv_zero_and_literal_bytes(self):
        for literal in ("", "é", "program"):
            with self.subTest(literal=literal):
                index = 0 if literal else 1
                compiled = build(f"%let v1 = (M.arg({index}, v0)) in ("
                                 f'%let v2 = (%op ==String("{literal}", v1)) in ('
                                 "%case v2 of { %constalt(1) => Prelude.IO.prim__putChar(89, v0) "
                                 "Just Prelude.IO.prim__putChar(78, v0) }))")
                with tempfile.TemporaryDirectory() as work:
                    path = Path(work) / "check-name"
                    path.write_bytes(compiled.elf)
                    path.chmod(0o755)
                    for name, expected in ((literal, b"Y"), (literal + "x", b"N")):
                        argv = [name] if index == 0 else ["program", name]
                        run = subprocess.run(argv, executable=path, capture_output=True)
                        self.assertEqual((run.returncode, run.stdout, run.stderr), (0, expected, b""))
