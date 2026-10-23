#!/usr/bin/env python3
"""Checked Idriç one-step-at-a-time form to direct x86-64 ELF64.

This module consumes compiler output only.  It never parses or recognizes
Idriç source text and has no alternate compiler or toolchain route.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import platform
import re
import struct
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence

try:
    from backend.elf64 import CODE_OFFSET, write_elf
except ModuleNotFoundError:  # direct `python backend/idric_x86.py ...`
    from elf64 import CODE_OFFSET, write_elf

ARTIFACT_HEADER = "EDRIC_ONE_STEP\t1"
BODY_HEADER = "EDRIC_ONE_STEP_BODY\t1"
RECEIPT_HEADER = "IDRIC_X86_EXECUTION\t1"
BACKEND_REPOSITORY = "isomorphisms/idric-x86-aggressive-backend"
SHA256 = re.compile(r"[0-9a-f]{64}\Z")
GIT_HEAD = re.compile(r"[0-9a-f]{40}\Z")
VARIABLE = re.compile(r"v([0-9]+)\Z")
INTEGER = re.compile(r"-?(?:0|[1-9][0-9]*)\Z")
FUNCTION = re.compile(r"\[([^]]*)\]: (.*)\Z")

I64_MIN = -(1 << 63)
I64_MAX = (1 << 63) - 1


class ArtifactError(ValueError):
    def __init__(self, code: str, detail: str):
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail


class UnsupportedError(ValueError):
    def __init__(self, code: str, detail: str):
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class FunctionDef:
    name: str
    arguments: tuple[int, ...]
    body_text: str


@dataclass(frozen=True)
class CheckedArtifact:
    raw: bytes
    source_sha256: str
    compiler_head: str
    representation: str
    body_sha256: str
    definitions: tuple[tuple[str, str], ...]

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.raw).hexdigest()


class Expr:
    pass


@dataclass(frozen=True)
class Var(Expr):
    number: int


@dataclass(frozen=True)
class Literal(Expr):
    value: int


@dataclass(frozen=True)
class Let(Expr):
    variable: int
    value: Expr
    body: Expr


@dataclass(frozen=True)
class Operation(Expr):
    name: str
    arguments: tuple[Expr, ...]


@dataclass(frozen=True)
class Call(Expr):
    name: str
    arguments: tuple[Expr, ...]


@dataclass(frozen=True)
class ConstCase(Expr):
    scrutinee: Expr
    alternatives: tuple[tuple[int, Expr], ...]
    default: Expr | None


@dataclass(frozen=True)
class PlanRow:
    function: str
    item: str
    realization: str
    detail: str


@dataclass(frozen=True)
class ScalarPlan:
    artifact: CheckedArtifact
    entry: str
    functions: tuple[FunctionDef, ...]
    rows: tuple[PlanRow, ...]


@dataclass(frozen=True)
class CompiledImage:
    elf: bytes
    listing: str
    plan: ScalarPlan


def _fields(line: str, count: int, where: str) -> list[str]:
    fields = line.split("\t")
    if len(fields) != count:
        raise ArtifactError("malformed_row", f"{where}: expected {count} tab-separated fields")
    return fields


def parse_checked_artifact_bytes(raw: bytes) -> CheckedArtifact:
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ArtifactError("artifact_not_utf8", str(error)) from error
    if not text.endswith("\n"):
        raise ArtifactError("missing_final_newline", "artifact must end in one newline")
    if "\r" in text or "\x00" in text:
        raise ArtifactError("invalid_artifact_byte", "CR and NUL are forbidden")
    lines = text[:-1].split("\n")
    if len(lines) < 10 or lines[0] != ARTIFACT_HEADER:
        raise ArtifactError("wrong_artifact_header", f"expected {ARTIFACT_HEADER!r}")

    source = _fields(lines[1], 2, "source_sha256")
    if source[0] != "source_sha256" or not SHA256.fullmatch(source[1]):
        raise ArtifactError("malformed_source_digest", source[1] if len(source) > 1 else "missing")
    compiler = _fields(lines[2], 3, "compiler_head")
    if (compiler[0] != "compiler_head" or compiler[1] != "isomorphisms/Idric"
            or not GIT_HEAD.fullmatch(compiler[2])):
        raise ArtifactError("malformed_compiler_head", "full isomorphisms/Idric revision required")
    if lines[3] != "core_typecheck\tPASS":
        raise ArtifactError("core_typecheck_not_pass", lines[3])
    representation = _fields(lines[4], 2, "representation")
    if representation != ["representation", "idris2-anf-show-0.8.0"]:
        raise ArtifactError("unsupported_representation", representation[-1])
    body_digest = _fields(lines[5], 2, "body_sha256")
    if body_digest[0] != "body_sha256" or not SHA256.fullmatch(body_digest[1]):
        raise ArtifactError("malformed_body_digest", body_digest[-1])
    if lines[6] != "definitions_begin" or lines[-2:] != ["definitions_end", "end"]:
        raise ArtifactError("malformed_definition_boundary", "definitions_begin/end and final end required")

    definition_lines = lines[7:-2]
    if not definition_lines:
        raise ArtifactError("empty_definitions", "compiler emitted no one-step definitions")
    body = (BODY_HEADER + "\n" + "\n".join(definition_lines) + "\n").encode()
    actual_body_digest = hashlib.sha256(body).hexdigest()
    if actual_body_digest != body_digest[1]:
        raise ArtifactError("body_digest_mismatch", f"{body_digest[1]} vs {actual_body_digest}")

    definitions: list[tuple[str, str]] = []
    names: set[str] = set()
    for number, line in enumerate(definition_lines, start=8):
        if " = " not in line:
            raise ArtifactError("malformed_definition", f"row {number}")
        name, value = line.split(" = ", 1)
        if not name or name in names:
            code = "duplicate_definition" if name in names else "empty_definition_name"
            raise ArtifactError(code, name)
        names.add(name)
        definitions.append((name, value))
    return CheckedArtifact(raw, source[1], compiler[2], representation[1], body_digest[1],
                           tuple(definitions))


def parse_checked_artifact(path: Path) -> CheckedArtifact:
    return parse_checked_artifact_bytes(path.read_bytes())


def _matching(text: str, opening: int, left: str = "(", right: str = ")") -> int:
    if opening >= len(text) or text[opening] != left:
        raise ArtifactError("malformed_one_step_expression", f"expected {left!r} at {opening}")
    depth = 0
    quote: str | None = None
    escaped = False
    for index in range(opening, len(text)):
        char = text[index]
        if quote is not None:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = None
            continue
        if char in {"'", '"'}:
            quote = char
        elif char == left:
            depth += 1
        elif char == right:
            depth -= 1
            if depth == 0:
                return index
    raise ArtifactError("unclosed_one_step_expression", text[opening:opening + 80])


def _outer_parentheses(text: str) -> bool:
    return text.startswith("(") and _matching(text, 0) == len(text) - 1


def _split_top_level(text: str, separator: str) -> list[str]:
    result: list[str] = []
    start = 0
    parens = braces = 0
    quote: str | None = None
    escaped = False
    index = 0
    while index < len(text):
        char = text[index]
        if quote is not None:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = None
            index += 1
            continue
        if char in {"'", '"'}:
            quote = char
        elif char == "(":
            parens += 1
        elif char == ")":
            parens -= 1
        elif char == "{":
            braces += 1
        elif char == "}":
            braces -= 1
        elif parens == 0 and braces == 0 and text.startswith(separator, index):
            result.append(text[start:index])
            start = index + len(separator)
            index = start
            continue
        index += 1
    result.append(text[start:])
    return result


def _split_case_default(text: str) -> tuple[str, str | None]:
    parens = braces = 0
    quote: str | None = None
    escaped = False
    candidate: tuple[int, str] | None = None
    for index, char in enumerate(text):
        if quote is not None:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = None
            continue
        if char in {"'", '"'}:
            quote = char
        elif char == "(":
            parens += 1
        elif char == ")":
            parens -= 1
        elif char == "{":
            braces += 1
        elif char == "}":
            braces -= 1
        elif parens == 0 and braces == 0:
            if text.startswith(" Nothing", index):
                candidate = (index, "Nothing")
            elif text.startswith(" Just ", index):
                candidate = (index, text[index + 6:])
    if candidate is None:
        raise ArtifactError("malformed_case_default", text)
    at, default = candidate
    return text[:at], None if default == "Nothing" else default


def parse_expression(source: str) -> Expr:
    text = source.strip()
    while _outer_parentheses(text):
        text = text[1:-1].strip()

    variable = VARIABLE.fullmatch(text)
    if variable:
        return Var(int(variable.group(1)))
    if INTEGER.fullmatch(text):
        value = int(text)
        if not I64_MIN <= value <= I64_MAX:
            raise UnsupportedError("integer_out_of_range", text)
        return Literal(value)
    if text.startswith("'"):
        try:
            value = ast.literal_eval(text)
        except (SyntaxError, ValueError) as error:
            raise ArtifactError("malformed_character", text) from error
        if not isinstance(value, str) or len(value) != 1:
            raise ArtifactError("malformed_character", text)
        return Literal(ord(value))

    let = re.match(r"%let v([0-9]+) = ", text)
    if let:
        opening = let.end()
        if opening >= len(text) or text[opening] != "(":
            raise ArtifactError("malformed_let", text)
        value_end = _matching(text, opening)
        marker = " in ("
        if not text.startswith(marker, value_end + 1):
            raise ArtifactError("malformed_let", text)
        body_open = value_end + 1 + len(marker) - 1
        body_end = _matching(text, body_open)
        if body_end != len(text) - 1:
            raise ArtifactError("trailing_one_step_expression", text[body_end + 1:])
        return Let(int(let.group(1)), parse_expression(text[opening + 1:value_end]),
                   parse_expression(text[body_open + 1:body_end]))

    if text.startswith("%op "):
        opening = text.find("(", 4)
        if opening < 0 or _matching(text, opening) != len(text) - 1:
            raise ArtifactError("malformed_operation", text)
        name = text[4:opening]
        arguments = text[opening + 1:-1]
        items = () if not arguments else tuple(parse_expression(item)
                                               for item in _split_top_level(arguments, ", "))
        return Operation(name, items)

    if text.startswith("%case "):
        marker = " of { "
        marker_at = text.find(marker, 6)
        if marker_at < 0 or not text.endswith(" }"):
            raise ArtifactError("malformed_case", text)
        scrutinee = parse_expression(text[6:marker_at])
        cases_text, default_text = _split_case_default(text[marker_at + len(marker):-2])
        alternatives: list[tuple[int, Expr]] = []
        for alternative in _split_top_level(cases_text, "| "):
            match = re.match(r"%constalt\((-?[0-9]+)\) => ", alternative)
            if not match:
                raise UnsupportedError("unsupported_case_alternative", alternative)
            alternatives.append((int(match.group(1)), parse_expression(alternative[match.end():])))
        default = None if default_text is None else parse_expression(default_text)
        return ConstCase(scrutinee, tuple(alternatives), default)

    opening = text.find("(")
    if opening > 0 and _matching(text, opening) == len(text) - 1:
        name = text[:opening]
        if name.startswith("<") or " underapp " in name:
            raise UnsupportedError("under_application", name)
        argument_text = text[opening + 1:-1]
        arguments = () if not argument_text else tuple(
            parse_expression(item) for item in _split_top_level(argument_text, ", ")
        )
        return Call(name, arguments)
    if " @ " in text:
        raise UnsupportedError("closure_application", text)
    raise UnsupportedError("unsupported_one_step_expression", text)


def parse_functions(artifact: CheckedArtifact) -> dict[str, FunctionDef]:
    functions: dict[str, FunctionDef] = {}
    for name, definition in artifact.definitions:
        match = FUNCTION.fullmatch(definition)
        if not match:
            continue
        arguments_text, body = match.groups()
        arguments: list[int] = []
        if arguments_text.strip():
            for item in arguments_text.split(", "):
                if not item.isdigit():
                    raise ArtifactError("malformed_function_argument", f"{name}: {item}")
                arguments.append(int(item))
        functions[name] = FunctionDef(name, tuple(arguments), body)
    return functions


def _main_name(functions: Mapping[str, FunctionDef]) -> str:
    candidates = [name for name in functions
                  if name == "main" or (name.endswith(".main") and "{" not in name)]
    if len(candidates) != 1:
        raise UnsupportedError("entry_function_count", f"expected one checked main, got {candidates}")
    return candidates[0]


def _let_variables(expression: Expr) -> set[int]:
    if isinstance(expression, Let):
        return {expression.variable} | _let_variables(expression.value) | _let_variables(expression.body)
    if isinstance(expression, (Operation, Call)):
        result: set[int] = set()
        for argument in expression.arguments:
            result |= _let_variables(argument)
        return result
    if isinstance(expression, ConstCase):
        result = _let_variables(expression.scrutinee)
        for _, alternative in expression.alternatives:
            result |= _let_variables(alternative)
        if expression.default is not None:
            result |= _let_variables(expression.default)
        return result
    return set()


class ExactRangeProof:
    """Prove the deliberately bounded scalar route before emitting machine code.

    The proof evaluates compiler expressions only to establish that every
    reachable scalar stays in signed i64 and every putChar observation is one
    byte.  Lowering still emits the original arithmetic, comparisons, branches,
    and calls; no proved result is substituted into the executable.
    """

    def __init__(self, functions: Mapping[str, FunctionDef]):
        self.functions = functions
        self.active: list[str] = []

    @staticmethod
    def bounded(value: int, where: str) -> int:
        if not I64_MIN <= value <= I64_MAX:
            raise UnsupportedError("unproved_integer_range", f"{where}: {value}")
        return value

    def expression(self, expression: Expr, environment: Mapping[int, int], where: str) -> int:
        if isinstance(expression, Literal):
            return self.bounded(expression.value, where)
        if isinstance(expression, Var):
            if expression.number not in environment:
                raise UnsupportedError("unproved_runtime_value", f"{where}: v{expression.number}")
            return environment[expression.number]
        if isinstance(expression, Let):
            value = self.expression(expression.value, environment, where)
            extended = dict(environment)
            extended[expression.variable] = value
            return self.expression(expression.body, extended, where)
        if isinstance(expression, Operation):
            arguments = [self.expression(item, environment, where)
                         for item in expression.arguments]
            if expression.name in {"cast-Integer-Int"} and len(arguments) == 1:
                return self.bounded(arguments[0], where)
            if expression.name in {"cast-Int-Char", "cast-Integer-Char"} and len(arguments) == 1:
                value = arguments[0]
                if not 0 <= value <= 255:
                    raise UnsupportedError("putchar_not_one_byte", f"{where}: {value}")
                return value
            arithmetic = {
                "+Int": lambda left, right: left + right,
                "+Integer": lambda left, right: left + right,
                "-Int": lambda left, right: left - right,
                "-Integer": lambda left, right: left - right,
                "*Int": lambda left, right: left * right,
                "*Integer": lambda left, right: left * right,
            }
            if expression.name in arithmetic and len(arguments) == 2:
                return self.bounded(arithmetic[expression.name](*arguments), where)
            comparisons = {"<Int", "<Integer", "<=Int", "<=Integer", "==Int", "==Integer"}
            if expression.name in comparisons and len(arguments) == 2:
                left, right = arguments
                if expression.name.startswith("<Integer") or expression.name.startswith("<Int"):
                    return int(left < right)
                if expression.name.startswith("<="):
                    return int(left <= right)
                return int(left == right)
            raise UnsupportedError("unsupported_operation", expression.name)
        if isinstance(expression, Call):
            arguments = [self.expression(item, environment, where)
                         for item in expression.arguments]
            if expression.name == "Prelude.IO.prim__putChar":
                if len(arguments) != 2:
                    raise ArtifactError("putchar_arity", str(len(arguments)))
                if not 0 <= arguments[0] <= 255:
                    raise UnsupportedError("putchar_not_one_byte", f"{where}: {arguments[0]}")
                return 0
            return self.call(expression.name, arguments)
        if isinstance(expression, ConstCase):
            scrutinee = self.expression(expression.scrutinee, environment, where)
            for constant, alternative in expression.alternatives:
                if scrutinee == constant:
                    return self.expression(alternative, environment, where)
            if expression.default is None:
                raise UnsupportedError("unreachable_case_reached", f"{where}: {scrutinee}")
            return self.expression(expression.default, environment, where)
        raise AssertionError(type(expression))

    def call(self, name: str, arguments: Sequence[int]) -> int:
        if name not in self.functions:
            raise UnsupportedError("unsupported_call", name)
        function = self.functions[name]
        if len(arguments) != len(function.arguments):
            raise ArtifactError("call_arity", f"{name}: {len(arguments)} vs {len(function.arguments)}")
        if name in self.active:
            raise UnsupportedError("recursive_call", " -> ".join((*self.active, name)))
        self.active.append(name)
        try:
            environment = dict(zip(function.arguments, arguments))
            return self.expression(parse_expression(function.body_text), environment, name)
        finally:
            self.active.pop()

    def check(self, entry: str) -> None:
        function = self.functions[entry]
        if len(function.arguments) != 1:
            raise UnsupportedError("entry_arity", f"{entry}: expected erased world argument")
        self.call(entry, [0])


class Assembler:
    def __init__(self) -> None:
        self.code = bytearray()
        self.labels: dict[str, int] = {}
        self.fixups: list[tuple[int, str]] = []
        self.records: list[tuple[int, int, str]] = []

    def emit(self, raw: bytes, text: str) -> None:
        start = len(self.code)
        self.code.extend(raw)
        self.records.append((start, len(self.code), text))

    def label(self, name: str) -> None:
        if name in self.labels:
            raise AssertionError(f"duplicate label {name}")
        self.labels[name] = len(self.code)

    def rel32(self, prefix: bytes, label: str, text: str) -> None:
        start = len(self.code)
        self.code.extend(prefix)
        at = len(self.code)
        self.code.extend(b"\x00\x00\x00\x00")
        self.fixups.append((at, label))
        self.records.append((start, len(self.code), text))

    def finish(self) -> tuple[bytes, str]:
        for at, label in self.fixups:
            if label not in self.labels:
                raise UnsupportedError("undefined_machine_label", label)
            displacement = self.labels[label] - (at + 4)
            if not -(1 << 31) <= displacement < (1 << 31):
                raise UnsupportedError("branch_out_of_range", label)
            struct.pack_into("<i", self.code, at, displacement)
        listing = "\n".join(
            f"{start:04x}  {bytes(self.code[start:end]).hex(' '):<38} {text}"
            for start, end, text in self.records
        ) + "\n"
        return bytes(self.code), listing


RAX, RCX, RDX, RBX, RSP, RBP, RSI, RDI = range(8)
R8, R9, R10, R11 = range(8, 12)
ARGUMENT_REGISTERS = (RDI, RSI, RDX, RCX, R8, R9)
REGISTER_NAMES = ("rax", "rcx", "rdx", "rbx", "rsp", "rbp", "rsi", "rdi",
                  "r8", "r9", "r10", "r11")


def _register_name(register: int) -> str:
    return REGISTER_NAMES[register]


def _rex(*, register: int = 0, index: int = 0, base: int = 0, width: int = 1) -> bytes:
    return bytes([0x40 | (width << 3) | (((register >> 3) & 1) << 2)
                  | (((index >> 3) & 1) << 1) | ((base >> 3) & 1)])


def _modrm(mode: int, register: int, base: int) -> bytes:
    return bytes([(mode << 6) | ((register & 7) << 3) | (base & 7)])


def _sib(scale: int, index: int, base: int) -> bytes:
    return bytes([(scale << 6) | ((index & 7) << 3) | (base & 7)])


def _mov_immediate(register: int, value: int) -> bytes:
    if not I64_MIN <= value <= I64_MAX:
        raise UnsupportedError("integer_out_of_range", str(value))
    if -(1 << 31) <= value < (1 << 31):
        return (_rex(base=register) + b"\xc7" + _modrm(3, 0, register)
                + struct.pack("<i", value))
    return (_rex(base=register) + bytes([0xB8 + (register & 7)])
            + struct.pack("<Q", value & ((1 << 64) - 1)))


def _mov_register(destination: int, source: int) -> bytes:
    return _rex(register=source, base=destination) + b"\x89" + _modrm(3, source, destination)


def _stack_load(destination: int, offset: int) -> bytes:
    return (_rex(register=destination, base=RSP) + b"\x8b" + _modrm(2, destination, 4)
            + _sib(0, 4, RSP) + struct.pack("<i", offset))


def _stack_store(offset: int, source: int) -> bytes:
    return (_rex(register=source, base=RSP) + b"\x89" + _modrm(2, source, 4)
            + _sib(0, 4, RSP) + struct.pack("<i", offset))


def _binary(opcode: bytes, destination: int, source: int) -> bytes:
    return _rex(register=source, base=destination) + opcode + _modrm(3, source, destination)


def _imul(destination: int, source: int) -> bytes:
    return _rex(register=destination, base=source) + b"\x0f\xaf" + _modrm(3, destination, source)


def _compare(left: int, right: int) -> bytes:
    return _rex(register=right, base=left) + b"\x39" + _modrm(3, right, left)


def _stack_adjust(subtract: bool, amount: int) -> bytes:
    return b"\x48\x81" + (b"\xec" if subtract else b"\xc4") + struct.pack("<I", amount)


def _lea_stack(destination: int, offset: int) -> bytes:
    return (_rex(register=destination, base=RSP) + b"\x8d" + _modrm(2, destination, 4)
            + _sib(0, 4, RSP) + struct.pack("<i", offset))


def _xor_register(register: int) -> bytes:
    return _rex(register=register, base=register) + b"\x31" + _modrm(3, register, register)


@dataclass(frozen=True)
class Frame:
    function: FunctionDef
    expression: Expr
    offsets: Mapping[int, int]
    output_offset: int
    size: int
    entry: bool


class Lowerer:
    def __init__(self, artifact: CheckedArtifact, functions: Mapping[str, FunctionDef],
                 *, target: str = "linux-x86_64"):
        if target not in ("linux-x86_64", "netbsd-x86_64"):
            raise UnsupportedError("unsupported_os_target", target)
        self.target = target
        self.artifact = artifact
        self.functions = functions
        self.assembler = Assembler()
        self.pending: list[str] = []
        self.compiled: set[str] = set()
        self.rows: list[PlanRow] = []
        self.case_number = 0
        self.write_count = 0
        self.needs_write_failure = False

    @staticmethod
    def require_atoms(arguments: Sequence[Expr], where: str) -> None:
        if any(not isinstance(argument, (Var, Literal)) for argument in arguments):
            raise UnsupportedError("non_atomic_operand", where)

    def frame(self, function: FunctionDef, *, entry: bool) -> Frame:
        expression = parse_expression(function.body_text)
        variables = sorted(set(function.arguments) | _let_variables(expression))
        offsets = {variable: index * 8 for index, variable in enumerate(variables)}
        output_offset = len(variables) * 8
        needed = output_offset + 8
        if entry:
            size = (needed + 15) & ~15
        else:
            size = (((max(needed, 8) - 8) + 15) & ~15) + 8
        return Frame(function, expression, offsets, output_offset, size, entry)

    def load(self, frame: Frame, expression: Expr, register: int = RAX) -> None:
        if isinstance(expression, Var):
            try:
                offset = frame.offsets[expression.number]
            except KeyError as error:
                raise ArtifactError("unknown_temporary", f"{frame.function.name}.v{expression.number}") from error
            self.assembler.emit(_stack_load(register, offset),
                                f"mov {_register_name(register)}, [rsp + {offset}] ; v{expression.number}")
            return
        if isinstance(expression, Literal):
            self.assembler.emit(_mov_immediate(register, expression.value),
                                f"mov {_register_name(register)}, {expression.value}")
            return
        self.emit_expression(frame, expression)
        if register != RAX:
            self.assembler.emit(_mov_register(register, RAX),
                                f"mov {_register_name(register)}, rax")

    def emit_expression(self, frame: Frame, expression: Expr) -> None:
        function = frame.function.name
        if isinstance(expression, (Var, Literal)):
            self.load(frame, expression)
            return
        if isinstance(expression, Let):
            self.emit_expression(frame, expression.value)
            offset = frame.offsets[expression.variable]
            self.assembler.emit(_stack_store(offset, RAX),
                                f"mov [rsp + {offset}], rax ; actual spill for v{expression.variable}")
            realization = "eliminate" if isinstance(expression.value, Operation) and expression.value.name.startswith("cast-") else "spill"
            detail = ("representation-preserving cast; same rax value" if realization == "eliminate"
                      else f"actual deterministic stack slot {offset}")
            self.rows.append(PlanRow(function, f"v{expression.variable}", realization, detail))
            self.emit_expression(frame, expression.body)
            return
        if isinstance(expression, Operation):
            self.emit_operation(frame, expression)
            return
        if isinstance(expression, Call):
            self.emit_call(frame, expression)
            return
        if isinstance(expression, ConstCase):
            self.emit_case(frame, expression)
            return
        raise AssertionError(type(expression))

    def emit_operation(self, frame: Frame, operation: Operation) -> None:
        name = operation.name
        self.require_atoms(operation.arguments, name)
        if name in {"cast-Integer-Int", "cast-Int-Char", "cast-Integer-Char"}:
            if len(operation.arguments) != 1:
                raise ArtifactError("operation_arity", name)
            self.load(frame, operation.arguments[0])
            self.rows.append(PlanRow(frame.function.name, name, "eliminate",
                                     "no representation change in the supported i64/byte range"))
            return
        arithmetic = {"+Int": b"\x01", "+Integer": b"\x01",
                      "-Int": b"\x29", "-Integer": b"\x29"}
        if name in arithmetic:
            if len(operation.arguments) != 2:
                raise ArtifactError("operation_arity", name)
            self.load(frame, operation.arguments[0], RAX)
            self.load(frame, operation.arguments[1], R11)
            self.assembler.emit(_binary(arithmetic[name], RAX, R11), f"{name} rax, r11")
            self.rows.append(PlanRow(frame.function.name, name, "reuse",
                                     "rax holds the left operand and result"))
            return
        if name in {"*Int", "*Integer"}:
            if len(operation.arguments) != 2:
                raise ArtifactError("operation_arity", name)
            self.load(frame, operation.arguments[0], RAX)
            self.load(frame, operation.arguments[1], R11)
            self.assembler.emit(_imul(RAX, R11), "imul rax, r11")
            self.rows.append(PlanRow(frame.function.name, name, "reuse",
                                     "rax holds the left operand and product"))
            return
        conditions = {"<Int": b"\x0f\x9c\xc0", "<Integer": b"\x0f\x9c\xc0",
                      "<=Int": b"\x0f\x9e\xc0", "<=Integer": b"\x0f\x9e\xc0",
                      "==Int": b"\x0f\x94\xc0", "==Integer": b"\x0f\x94\xc0"}
        if name in conditions:
            if len(operation.arguments) != 2:
                raise ArtifactError("operation_arity", name)
            self.load(frame, operation.arguments[0], RAX)
            self.load(frame, operation.arguments[1], R11)
            self.assembler.emit(_compare(RAX, R11), "cmp rax, r11")
            self.assembler.emit(conditions[name], f"setcc al ; {name}")
            self.assembler.emit(b"\x48\x0f\xb6\xc0", "movzx rax, al")
            self.rows.append(PlanRow(frame.function.name, name, "register", "FLAGS to rax boolean"))
            return
        raise UnsupportedError("unsupported_operation", name)

    def emit_call(self, frame: Frame, call: Call) -> None:
        self.require_atoms(call.arguments, call.name)
        if call.name == "Prelude.IO.prim__putChar":
            if len(call.arguments) != 2:
                raise ArtifactError("putchar_arity", str(len(call.arguments)))
            self.load(frame, call.arguments[0], RAX)
            self.assembler.emit(_stack_store(frame.output_offset, RAX),
                                f"mov [rsp + {frame.output_offset}], rax ; actual output byte")
            write_number = 4 if self.target == "netbsd-x86_64" else 1
            self.assembler.emit(_mov_immediate(RAX, write_number),
                                f"mov rax, {write_number} ; SYS_write")
            self.assembler.emit(_mov_immediate(RDI, 1), "mov rdi, 1 ; stdout")
            self.assembler.emit(_lea_stack(RSI, frame.output_offset),
                                f"lea rsi, [rsp + {frame.output_offset}]")
            self.assembler.emit(_mov_immediate(RDX, 1), "mov rdx, 1")
            self.assembler.emit(b"\x0f\x05", "syscall ; write")
            if self.target == "netbsd-x86_64":
                self.assembler.rel32(b"\x0f\x82", "process:write_failure",
                                     "jc process:write_failure ; NetBSD syscall error")
            self.assembler.emit(_mov_immediate(R11, 1), "mov r11, 1 ; required write count")
            self.assembler.emit(_compare(RAX, R11), "cmp rax, r11 ; write returned one byte")
            self.assembler.rel32(b"\x0f\x85", "process:write_failure",
                                 "jne process:write_failure")
            self.assembler.emit(_xor_register(RAX), "xor rax, rax ; IO unit result")
            self.rows.append(PlanRow(frame.function.name, "putChar", self.target + "_syscall",
                                     "direct write(1, byte, 1); world token eliminated"))
            self.write_count += 1
            self.needs_write_failure = True
            return
        if call.name not in self.functions:
            raise UnsupportedError("unsupported_call", call.name)
        if len(call.arguments) > len(ARGUMENT_REGISTERS):
            raise UnsupportedError("too_many_call_arguments", f"{call.name}: {len(call.arguments)}")
        for argument, register in zip(call.arguments, ARGUMENT_REGISTERS):
            self.load(frame, argument, register)
        label = f"function:{call.name}"
        self.assembler.rel32(b"\xe8", label, f"call {call.name}")
        self.rows.append(PlanRow(frame.function.name, call.name, "direct_call",
                                 "internal convention; result in rax"))
        if call.name not in self.compiled and call.name not in self.pending:
            self.pending.append(call.name)

    def emit_case(self, frame: Frame, case: ConstCase) -> None:
        number = self.case_number
        self.case_number += 1
        join = f"case:{number}:join"
        default_label = f"case:{number}:default"
        self.require_atoms((case.scrutinee,), "case scrutinee")
        self.load(frame, case.scrutinee, RAX)
        for index, (constant, _) in enumerate(case.alternatives):
            self.assembler.emit(_mov_immediate(R11, constant), f"mov r11, {constant}")
            self.assembler.emit(_compare(RAX, R11), "cmp rax, r11")
            self.assembler.rel32(b"\x0f\x84", f"case:{number}:alt:{index}",
                                 f"je case:{number}:alt:{index}")
        self.assembler.rel32(b"\xe9", default_label, f"jmp {default_label}")
        for index, (_, alternative) in enumerate(case.alternatives):
            self.assembler.label(f"case:{number}:alt:{index}")
            self.emit_expression(frame, alternative)
            self.assembler.rel32(b"\xe9", join, f"jmp {join}")
        self.assembler.label(default_label)
        if case.default is None:
            self.assembler.emit(b"\x0f\x0b", "ud2 ; compiler-marked unreachable case default")
        else:
            self.emit_expression(frame, case.default)
        self.assembler.label(join)
        self.rows.append(PlanRow(frame.function.name, f"case:{number}", "conditional_branch",
                                 "runtime constant alternatives with rel32 fixups"))

    def emit_function(self, name: str, *, entry: bool) -> None:
        if name in self.compiled:
            return
        function = self.functions[name]
        frame = self.frame(function, entry=entry)
        self.compiled.add(name)
        self.assembler.label("_start" if entry else f"function:{name}")
        self.assembler.emit(_stack_adjust(True, frame.size),
                            f"sub rsp, {frame.size} ; aligned frame for {name}")
        if len(function.arguments) > len(ARGUMENT_REGISTERS) and not entry:
            raise UnsupportedError("too_many_function_arguments", f"{name}: {len(function.arguments)}")
        for index, argument in enumerate(function.arguments):
            offset = frame.offsets[argument]
            if entry:
                self.assembler.emit(_mov_immediate(RAX, 0), "mov rax, 0 ; erased world token")
                source = RAX
            else:
                source = ARGUMENT_REGISTERS[index]
            self.assembler.emit(_stack_store(offset, source),
                                f"mov [rsp + {offset}], {_register_name(source)} ; actual argument home v{argument}")
        self.emit_expression(frame, frame.expression)
        if entry:
            self.assembler.emit(_stack_adjust(False, frame.size), f"add rsp, {frame.size}")
            self.assembler.emit(_mov_immediate(RDI, 0), "mov rdi, 0 ; status")
            exit_number = 1 if self.target == "netbsd-x86_64" else 60
            self.assembler.emit(_mov_immediate(RAX, exit_number),
                                f"mov rax, {exit_number} ; SYS_exit")
            self.assembler.emit(b"\x0f\x05", "syscall ; exit")
        else:
            self.assembler.emit(_stack_adjust(False, frame.size), f"add rsp, {frame.size}")
            self.assembler.emit(b"\xc3", "ret")

    def compile(self, entry: str) -> CompiledImage:
        self.emit_function(entry, entry=True)
        while self.pending:
            self.emit_function(self.pending.pop(0), entry=False)
        if self.write_count == 0:
            raise UnsupportedError("no_observable_output", "supported baseline requires checked putChar")
        if self.needs_write_failure:
            self.assembler.label("process:write_failure")
            self.assembler.emit(_mov_immediate(RDI, 1), "mov rdi, 1 ; write failure status")
            exit_number = 1 if self.target == "netbsd-x86_64" else 60
            self.assembler.emit(_mov_immediate(RAX, exit_number),
                                f"mov rax, {exit_number} ; SYS_exit")
            self.assembler.emit(b"\x0f\x05", "syscall ; exit after write failure")
        machine, instruction_listing = self.assembler.finish()
        selected = tuple(self.functions[name] for name in sorted(self.compiled))
        plan = ScalarPlan(self.artifact, entry, selected, tuple(self.rows))
        listing = instruction_listing + render_backend_plan(plan)
        return CompiledImage(write_elf(machine, target=self.target), listing, plan)


def make_scalar_plan(artifact: CheckedArtifact) -> tuple[dict[str, FunctionDef], str]:
    functions = parse_functions(artifact)
    entry = _main_name(functions)
    ExactRangeProof(functions).check(entry)
    return functions, entry


def compile_checked_artifact(artifact: CheckedArtifact, *,
                             target: str = "linux-x86_64") -> CompiledImage:
    functions, entry = make_scalar_plan(artifact)
    return Lowerer(artifact, functions, target=target).compile(entry)


def render_backend_plan(plan: ScalarPlan) -> str:
    lines = ["", "# backend plan", f"entry {plan.entry}",
             "plan scalar_i64 ; compiler one-step names do not imply memory stores"]
    lines.extend(f"plan {row.function} {row.item} {row.realization} ; {row.detail}" for row in plan.rows)
    lines.extend(("fallback RefC ABSENT", "fallback Chez_target ABSENT",
                  "fallback c_compiler ABSENT", "fallback assembler ABSENT",
                  "fallback linker ABSENT", "fallback libc ABSENT"))
    return "\n".join(lines) + "\n"


def compile_artifact(artifact_path: Path, output_path: Path,
                     listing_path: Path | None = None,
                     source_path: Path | None = None,
                     *, target: str = "linux-x86_64") -> CompiledImage:
    artifact = parse_checked_artifact(artifact_path)
    if source_path is not None:
        actual = hashlib.sha256(source_path.read_bytes()).hexdigest()
        if actual != artifact.source_sha256:
            raise ArtifactError("source_digest_mismatch", f"{artifact.source_sha256} vs {actual}")
    compiled = compile_checked_artifact(artifact, target=target)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(compiled.elf)
    output_path.chmod(0o755)
    if listing_path is not None:
        listing_path.parent.mkdir(parents=True, exist_ok=True)
        listing_path.write_text(compiled.listing)
    return compiled


def run_native(output_path: Path) -> subprocess.CompletedProcess[bytes]:
    if platform.system() != "Linux" or platform.machine() != "x86_64":
        raise UnsupportedError("native_host_required", f"{platform.system()} {platform.machine()}")
    return subprocess.run([output_path], check=False, capture_output=True)


def backend_head(repository_root: Path | None = None) -> str:
    root = repository_root or Path(__file__).resolve().parents[1]
    run = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"], check=True,
                         capture_output=True, text=True)
    value = run.stdout.strip()
    if not GIT_HEAD.fullmatch(value):
        raise ArtifactError("malformed_backend_head", value)
    return value


def render_execution_receipt(compiled: CompiledImage, run: subprocess.CompletedProcess[bytes],
                             backend_revision: str,
                             expected_stdout: bytes | None = None) -> str:
    artifact = compiled.plan.artifact
    if expected_stdout is None:
        semantic_status = "NOT_VERIFIED"
        semantic_detail = "expected stdout not supplied"
    elif run.returncode != 0:
        semantic_status = "NOT_VERIFIED"
        semantic_detail = f"native exit={run.returncode}"
    elif run.stdout == expected_stdout and run.stderr == b"":
        semantic_status = "PASS"
        semantic_detail = run.stdout.hex()
    else:
        semantic_status = "FAIL"
        semantic_detail = f"expected={expected_stdout.hex()},actual={run.stdout.hex()},stderr={run.stderr.hex()}"
    stages = (
        ("source_checked", "PASS", "compiler artifact records ordinary parse/elaboration/core check"),
        ("one_step_form", "PASS", artifact.representation),
        ("backend_lowering", "PASS", "supported scalar subset"),
        ("x86_encoding", "PASS", "direct in-process encoding"),
        ("elf_generated", "PASS", "direct in-process ELF64"),
        ("native_execution", "PASS" if run.returncode == 0 else "FAIL", f"exit={run.returncode}"),
        ("semantic_result", semantic_status, semantic_detail),
    )
    lines = [RECEIPT_HEADER,
             f"source_sha256\t{artifact.source_sha256}",
             f"compiler_head\tisomorphisms/Idric\t{artifact.compiler_head}",
             f"backend_head\t{BACKEND_REPOSITORY}\t{backend_revision}",
             f"artifact_sha256\t{artifact.sha256}",
             f"elf_sha256\t{hashlib.sha256(compiled.elf).hexdigest()}",
             "target\tx86_64-linux-user",
             f"stdout_hex\t{run.stdout.hex()}",
             f"expected_stdout_hex\t{expected_stdout.hex() if expected_stdout is not None else 'NOT_VERIFIED'}",
             f"stderr_hex\t{run.stderr.hex()}",
             f"exit_status\t{run.returncode}"]
    lines.extend(f"stage\t{name}\t{status}\t{detail}" for name, status, detail in stages)
    lines.extend(f"plan\t{row.function}\t{row.item}\t{row.realization}\t{row.detail}"
                 for row in compiled.plan.rows)
    lines.extend(("fallback\tRefC\tABSENT", "fallback\tChez_target\tABSENT",
                  "fallback\tc_compiler\tABSENT", "fallback\tassembler\tABSENT",
                  "fallback\tlinker\tABSENT", "fallback\tlibc\tABSENT"))
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("artifact", type=Path)
    parser.add_argument("-o", "--output", type=Path, required=True)
    parser.add_argument("--source", type=Path)
    parser.add_argument("--target", choices=("linux-x86_64", "netbsd-x86_64"),
                        default="linux-x86_64")
    parser.add_argument("--listing", type=Path)
    parser.add_argument("--run-receipt", type=Path)
    parser.add_argument("--expect-stdout-hex")
    arguments = parser.parse_args()
    if arguments.run_receipt is not None and arguments.target != "linux-x86_64":
        parser.error("native execution receipts are Linux-only; NetBSD needs a NetBSD runner")
    compiled = compile_artifact(arguments.artifact, arguments.output, arguments.listing,
                                arguments.source, target=arguments.target)
    if arguments.run_receipt is not None:
        try:
            expected_stdout = (None if arguments.expect_stdout_hex is None
                               else bytes.fromhex(arguments.expect_stdout_hex))
        except ValueError as error:
            parser.error(f"invalid --expect-stdout-hex: {error}")
        run = run_native(arguments.output)
        receipt = render_execution_receipt(compiled, run, backend_head(), expected_stdout)
        arguments.run_receipt.parent.mkdir(parents=True, exist_ok=True)
        arguments.run_receipt.write_text(receipt)
        if run.returncode != 0:
            raise SystemExit(run.returncode)
        if expected_stdout is not None and (run.stdout != expected_stdout or run.stderr != b""):
            raise SystemExit(1)


if __name__ == "__main__":
    main()
