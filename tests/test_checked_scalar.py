import hashlib
import os
import platform
import struct
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.elf64 import CODE_OFFSET
from backend.idric_x86 import (
    ArtifactError,
    UnsupportedError,
    compile_artifact,
    compile_checked_artifact,
    parse_checked_artifact_bytes,
    render_execution_receipt,
)


def artifact_bytes(definitions: list[str], source: bytes = b"checked test-double source\n") -> bytes:
    body = ("EDRIC_ONE_STEP_BODY\t1\n" + "\n".join(definitions) + "\n").encode()
    lines = [
        "EDRIC_ONE_STEP\t1",
        "source_sha256\t" + hashlib.sha256(source).hexdigest(),
        "compiler_head\tisomorphisms/Idric\t" + "0" * 40,
        "core_typecheck\tPASS",
        "representation\tidris2-anf-show-0.8.0",
        "body_sha256\t" + hashlib.sha256(body).hexdigest(),
        "definitions_begin",
        *definitions,
        "definitions_end",
        "end",
    ]
    return ("\n".join(lines) + "\n").encode()


def put_char_main(module: str, value: str) -> str:
    return (f"{module}.main = [0]: %let v1 = ({value}) in "
            f"(Prelude.IO.prim__putChar(v1, v0))")


class CheckedScalarBackendTest(unittest.TestCase):
    def compile_and_run(self, definitions: list[str]) -> tuple[bytes, bytes, str]:
        artifact = parse_checked_artifact_bytes(artifact_bytes(definitions))
        compiled = compile_checked_artifact(artifact)
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "program"
            output.write_bytes(compiled.elf)
            output.chmod(0o755)
            run = subprocess.run([output], check=False, capture_output=True)
        self.assertEqual(run.returncode, 0)
        self.assertEqual(run.stderr, b"")
        return run.stdout, compiled.elf, compiled.listing

    def test_action_returning_main_requires_its_checked_process_entry(self) -> None:
        cases = (
            (["M.main = []: 65"], "entry_arity"),
            (["M.main = []: 65", "{__mainExpression:0} = [0]: M.main()"], "entry_arity"),
            (["M.main = []: 65",
              "{__mainExpression:0} = []: %let v0 = (M.main()) in (v0 @ ([__]))"],
             "unknown_closure_target"),
            (["M.main = []: 65",
              "{__mainExpression:0} = []: %let v0 = (%MkWorld) in (Prelude.IO.prim__putChar(v0, v0))"],
             "observable_erased_value"),
        )
        for definitions, code in cases:
            with self.subTest(definitions=definitions), self.assertRaises(UnsupportedError) as caught:
                compile_checked_artifact(parse_checked_artifact_bytes(artifact_bytes(definitions)))
            self.assertEqual(caught.exception.code, code)

    @unittest.skipUnless(platform.system() == "Linux" and platform.machine() == "x86_64",
                         "native direct ELF acceptance requires x86_64 Linux")
    def test_checked_print_x_runs_natively(self) -> None:
        stdout, image, listing = self.compile_and_run([put_char_main("PrintX", "'X'")])
        self.assertEqual(stdout, b"X")
        self.assertEqual(image[:4], b"\x7fELF")
        self.assertEqual(struct.unpack_from("<H", image, 16)[0], 2)
        self.assertEqual(struct.unpack_from("<H", image, 18)[0], 62)
        self.assertEqual(struct.unpack_from("<Q", image, 24)[0], 0x400000 + CODE_OFFSET)
        self.assertEqual(struct.unpack_from("<Q", image, 40)[0], 0)
        self.assertIn("syscall ; write", listing)
        self.assertIn("actual spill", listing)
        self.assertIn("one-step names do not imply memory stores", listing)

    @unittest.skipUnless(platform.system() == "Linux" and platform.machine() == "x86_64",
                         "native direct ELF acceptance requires x86_64 Linux")
    def test_write_failure_has_a_nonzero_process_status(self) -> None:
        artifact = parse_checked_artifact_bytes(artifact_bytes([put_char_main("PrintX", "'X'")]))
        compiled = compile_checked_artifact(artifact)
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "program"
            output.write_bytes(compiled.elf)
            output.chmod(0o755)
            run = subprocess.run([output], check=False, stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL,
                                 preexec_fn=lambda: os.close(1))
        self.assertEqual(run.returncode, 1)
        self.assertIn("jne process:write_failure", compiled.listing)

    @unittest.skipUnless(platform.system() == "Linux" and platform.machine() == "x86_64",
                         "native direct ELF acceptance requires x86_64 Linux")
    def test_scalar_arithmetic_is_computed_by_emitted_instructions(self) -> None:
        cases = [
            ("Add", "+Int", 12, 7, 19, "add"),
            ("Subtract", "-Int", 12, 7, 5, "sub"),
            ("Multiply", "*Int", 12, 7, 84, "imul"),
        ]
        for module, operation, left, right, expected, mnemonic in cases:
            with self.subTest(module=module):
                body = (f"{module}.main = [0]: %let v1 = ({left}) in "
                        f"(%let v2 = ({right}) in (%let v3 = (%op {operation}(v1, v2)) in "
                        f"(%let v4 = (%op cast-Int-Char(v3)) in "
                        f"(Prelude.IO.prim__putChar(v4, v0)))))")
                stdout, _, listing = self.compile_and_run([body])
                self.assertEqual(stdout, bytes([expected]))
                self.assertIn(mnemonic, listing)
                self.assertNotIn(f"mov rax, {expected}", listing)

    @unittest.skipUnless(platform.system() == "Linux" and platform.machine() == "x86_64",
                         "native direct ELF acceptance requires x86_64 Linux")
    def test_conditional_branches_choose_both_paths(self) -> None:
        less = ("Prelude.EqOrd.< = [0, 1]: %let v2 = (%op <Integer(v0, v1)) in "
                "(%case v2 of { %constalt(0) => 0 Just 1 })")
        for module, left, right, expected in (
            ("BranchTrue", 7, 12, 41),
            ("BranchFalse", 12, 7, 99),
        ):
            main = (f"{module}.main = [0]: %let v1 = ({left}) in "
                    f"(%let v2 = ({right}) in (%let v3 = (Prelude.EqOrd.<(v1, v2)) in "
                    "(%let v4 = (%case v3 of { %constalt(1) => 41| %constalt(0) => 99 Nothing }) in "
                    "(%let v5 = (%op cast-Int-Char(v4)) in "
                    "(Prelude.IO.prim__putChar(v5, v0))))))")
            stdout, _, listing = self.compile_and_run([main, less])
            self.assertEqual(stdout, bytes([expected]))
            self.assertIn("conditional_branch", listing)
            self.assertIn("call Prelude.EqOrd.<", listing)

    @unittest.skipUnless(platform.system() == "Linux" and platform.machine() == "x86_64",
                         "native direct ELF acceptance requires x86_64 Linux")
    def test_direct_internal_call_uses_aligned_frame_and_return(self) -> None:
        main = ("DirectCall.main = [0]: %let v1 = (40) in "
                "(%let v2 = (DirectCall.increment(v1)) in "
                "(%let v3 = (%op cast-Int-Char(v2)) in "
                "(Prelude.IO.prim__putChar(v3, v0))))")
        increment = "DirectCall.increment = [0]: %let v1 = (1) in (%op +Int(v0, v1))"
        stdout, _, listing = self.compile_and_run([main, increment])
        self.assertEqual(stdout, bytes([41]))
        self.assertIn("call DirectCall.increment", listing)
        self.assertIn("ret", listing)
        self.assertIn("direct_call", listing)

    def test_compilation_is_deterministic(self) -> None:
        artifact = parse_checked_artifact_bytes(artifact_bytes([put_char_main("PrintX", "'X'")]))
        first = compile_checked_artifact(artifact)
        second = compile_checked_artifact(artifact)
        self.assertEqual(first.elf, second.elf)
        self.assertEqual(first.listing, second.listing)

    def test_execution_receipt_only_passes_an_exact_observation(self) -> None:
        artifact = parse_checked_artifact_bytes(artifact_bytes([put_char_main("PrintX", "'X'")]))
        compiled = compile_checked_artifact(artifact)
        run = subprocess.CompletedProcess(["print-x"], 0, b"X", b"")
        verified = render_execution_receipt(compiled, run, "1" * 40, b"X")
        mismatch = render_execution_receipt(compiled, run, "1" * 40, b"Y")
        unspecified = render_execution_receipt(compiled, run, "1" * 40)
        self.assertIn("stage\tsemantic_result\tPASS\t58", verified)
        self.assertIn("stage\tsemantic_result\tFAIL\texpected=59,actual=58", mismatch)
        self.assertIn("stage\tsemantic_result\tNOT_VERIFIED", unspecified)

    def test_source_text_is_not_a_backend_input(self) -> None:
        with self.assertRaises(ArtifactError) as caught:
            parse_checked_artifact_bytes(b"module PrintX\nmain = putChar 'X'\n")
        self.assertEqual(caught.exception.code, "wrong_artifact_header")

    def test_body_digest_tampering_is_rejected(self) -> None:
        raw = artifact_bytes([put_char_main("PrintX", "'X'")]).replace(b"'X'", b"'Y'")
        with self.assertRaises(ArtifactError) as caught:
            parse_checked_artifact_bytes(raw)
        self.assertEqual(caught.exception.code, "body_digest_mismatch")

    def test_unknown_operation_and_call_fail_closed(self) -> None:
        unknown_operation = ("Unknown.main = [0]: %let v1 = (1) in "
                             "(%let v2 = (%op AVX512Magic(v1)) in "
                             "(Prelude.IO.prim__putChar(v2, v0)))")
        artifact = parse_checked_artifact_bytes(artifact_bytes([unknown_operation]))
        with self.assertRaises(UnsupportedError) as caught:
            compile_checked_artifact(artifact)
        self.assertEqual(caught.exception.code, "unsupported_operation")

        unknown_call = "Unknown.main = [0]: Missing.backend(v0)"
        artifact = parse_checked_artifact_bytes(artifact_bytes([unknown_call]))
        with self.assertRaises(UnsupportedError) as caught:
            compile_checked_artifact(artifact)
        self.assertEqual(caught.exception.code, "unsupported_call")

    def test_non_atomic_compiler_operand_fails_closed(self) -> None:
        nested = ("Nested.main = [0]: %let v1 = "
                  "(%op +Int(1, (%op +Int(2, 3)))) in "
                  "(Prelude.IO.prim__putChar(v1, v0))")
        artifact = parse_checked_artifact_bytes(artifact_bytes([nested]))
        with self.assertRaises(UnsupportedError) as caught:
            compile_checked_artifact(artifact)
        self.assertEqual(caught.exception.code, "non_atomic_operand")

    def test_unproved_integer_range_and_nonbyte_output_fail_closed(self) -> None:
        overflow = ("Overflow.main = [0]: %let v1 = (9223372036854775807) in "
                    "(%let v2 = (1) in (%let v3 = (%op +Integer(v1, v2)) in "
                    "(%let v4 = (%op cast-Integer-Char(v3)) in "
                    "(Prelude.IO.prim__putChar(v4, v0)))))")
        artifact = parse_checked_artifact_bytes(artifact_bytes([overflow]))
        with self.assertRaises(UnsupportedError) as caught:
            compile_checked_artifact(artifact)
        self.assertEqual(caught.exception.code, "unproved_integer_range")

        wide_character = put_char_main("Wide", "256")
        artifact = parse_checked_artifact_bytes(artifact_bytes([wide_character]))
        with self.assertRaises(UnsupportedError) as caught:
            compile_checked_artifact(artifact)
        self.assertEqual(caught.exception.code, "putchar_not_one_byte")

    def test_compile_path_invokes_no_external_toolchain(self) -> None:
        source = b"checked test-double source\n"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            artifact_path = root / "print-x.one-step"
            source_path = root / "PrintX.idric"
            artifact_path.write_bytes(artifact_bytes([put_char_main("PrintX", "'X'")], source))
            source_path.write_bytes(source)
            with patch("backend.idric_x86.subprocess.run",
                       side_effect=AssertionError("external tool invoked")):
                compile_artifact(artifact_path, root / "print-x", source_path=source_path)


if __name__ == "__main__":
    unittest.main()
