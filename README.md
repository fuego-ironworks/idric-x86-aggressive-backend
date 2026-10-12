# Idriç x86-64 backend

This branch is the executable x86-64 compiler route:

```text
checked .idric source
  → compiler-owned one-step-at-a-time form
  → scalar backend plan
  → directly encoded x86-64
  → directly emitted ELF64
  → native Linux execution
```

The canonical `PrintX.idric` fixture writes exactly `X` and exits 0. Seven
additional fixtures exercise add, subtract, multiply, both conditional-branch
outcomes, a direct internal call, and six-argument register pressure. The
production route uses no RefC, generated C, C compiler, assembler, linker,
libc, CRT, or target-Chez fallback.

The active compiler dependency is the declared `isomorphisms/Idric` branch
recorded in [`IDRIC_COMPILER_REF`](IDRIC_COMPILER_REF), currently `Idriç`.
CI resolves that branch for every run and records the exact compiler SHA that
actually ran. Incompatibility with the current compiler fails the lane; it does
not retry the first known-green revision.

The first complete green tuple remains historical evidence: Idric PR #63 at
`dd313277fedb2b678ff0df6769ed1330a2e80523` with backend
`e11a69d4d00501531ee058fa3d880267d5fc6d2e`.

## Run the complete route

```sh
git clone https://github.com/isomorphisms/Idric.git .idric
git -C .idric checkout Idriç
.idric/_/edric bootstrap
make ci
```

`make ci` runs 54 tests with no skips, compiles all eight real `.idric`
fixtures, regenerates every artifact and executable byte-for-byte, validates
ELF64 with `file` and `readelf`, disassembles the emitted bytes with `objdump`,
runs each executable natively, and compares exact stdout and exit status.
Evidence is retained under `build/checked-x86/<fixture>/`.

## Compact scalar target primitives

[`backend/small_scalars.py`](backend/small_scalars.py) adds direct x86-64 scalar
target support for the five compact-format specification families documented by
the ARM Thumb work: Float16, OCP FP8 (E4M3 and E5M2), OCP E3M2,
Ootomo–Naruse OotomoE5M3, and Idris 2 Bits8. Float16/OFP8/E3M2 widen one value at a
time to scalar Float32 SSE arithmetic and round back immediately; OotomoE5M3 remains
storage-only; Bits8 uses scalar general-purpose-register operations.

This is target primitive evidence, not a claim that the current compiler-owned
one-step handoff already exposes those operations from ordinary `.idric`
source. See [`docs/small-scalar-formats.md`](docs/small-scalar-formats.md) for
the exact format and evidence boundary.

## Find the implementation

- [`backend/idric_x86.py`](backend/idric_x86.py): checked artifact parsing,
  scalar proof/planning, lowering, and concrete x86-64 encoding
- [`backend/small_scalars.py`](backend/small_scalars.py): compact scalar target
  primitives and direct x86-64 acceptance-image generation
- [`backend/elf64.py`](backend/elf64.py): deterministic in-process ELF64 writer
- [`fixtures/PrintX.idric`](fixtures/PrintX.idric): canonical acceptance source
- [`scripts/run_checked_integration.sh`](scripts/run_checked_integration.sh):
  real compiler/ELF/native acceptance
- [`docs/checked-x86-baseline.md`](docs/checked-x86-baseline.md): exact supported
  compiler handoff surface, process convention, inspection evidence, and stack
  reconciliation
- [`docs/small-scalar-formats.md`](docs/small-scalar-formats.md): Float16,
  FP8, E3M2, OotomoE5M3, and Bits8 target semantics and native evidence boundary

Top-level symbolic links expose the same implementation and acceptance files
without moving the preserved PR work.
