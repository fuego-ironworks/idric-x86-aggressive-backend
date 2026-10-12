# Compact scalar formats on x86-64

This backend implements the five scalar specification families recorded by the
ARM Thumb repository, with the FP8 family containing its two distinct encodings.
The implementation is in [`backend/small_scalars.py`](../backend/small_scalars.py)
and native acceptance is in
[`tests/test_small_scalars.py`](../tests/test_small_scalars.py).

The implementation references the ARM specification set at
`isomorphisms/idric-arm-thumb` PR #89, head
`97e79f708d902a6896e52e5b5c97ed32213ef99b`. Those notes link the authoritative
upstream documents and separate interchange/storage semantics from backend-local
arithmetic policy.

## Formats

| Family | Scalar payload | x86-64 policy |
| --- | --- | --- |
| IEEE binary16 / Float16 | signed 16-bit `1/5/10` | exact payload; widen to Float32 for arithmetic; round-to-nearest ties-to-even back to binary16 |
| OCP OFP8 E4M3 | signed 8-bit `1/4/3` | exact payload; widen to Float32; scalar arithmetic; explicit saturating/non-saturating requantization |
| OCP OFP8 E5M2 | signed 8-bit `1/5/2` | exact payload; widen to Float32; scalar arithmetic; explicit saturating/non-saturating requantization |
| OCP FP6 E3M2 | signed 6-bit `1/3/2` | low six bits; widen to Float32; scalar arithmetic; ties-to-even with required saturation |
| Ootomo–Naruse OotomoE5M3 | unsigned 8-bit `5/3` storage | exact Figure 3 / Figure 4 conversion only; no invented arithmetic |
| Idris 2 Bits8 | unsigned 8-bit integer | low eight bits in a GPR; modular arithmetic, unsigned division/remainder/order, logical shifts and bit operations |

The small floating payload is not replaced by Float32 storage. Float32 is the
arithmetic carrier only: decode one scalar, execute one baseline scalar SSE
operation, and immediately quantize the result back to the compact payload.
There is no packed SIMD representation here.

## Floating arithmetic policy

OFP8 and E3M2 specify encodings and conversion behavior but do not define a
standalone arithmetic instruction set. This backend therefore states its local
policy explicitly:

```text
compact payload
  -> exact Float32 value
  -> scalar SSE add/subtract/multiply
  -> format conversion / round-to-nearest ties-to-even
  -> compact payload
```

This slice implements scalar add, subtract, multiply, divide, and a square-root observation path. It does not use
AVX, AVX2, AVX-512, F16C, a C compiler, an assembler, a linker, libc, or a
runtime floating-point library.

For OCP FP8, saturation is never guessed: the caller must select saturating or
non-saturating conversion. E4M3 non-saturating overflow produces NaN; E5M2
non-saturating overflow produces signed infinity. Saturating conversion clamps
to signed maximum finite magnitude.

E3M2 has no NaN encoding. OCP leaves conversion from source NaN
implementation-defined; this backend maps source NaN to positive zero and tests
that choice explicitly. Finite overflow clamps to signed maximum finite
magnitude.

Float16 keeps IEEE signed zero, subnormals, infinities and NaNs. The local
canonical NaN produced by compact conversion is `0x7e00`.

## Ootomo–Naruse OotomoE5M3

OotomoE5M3 is kept as the unsigned storage format published by Hiroyuki Ootomo and
Akira Naruse, not reinterpreted as a signed nine-bit float and not given local
arithmetic.

For a supported positive normal Float32 source the encoder follows Figure 3:

```text
u = bits32(x)
u = u - 0x38000000
code = (u >> 20) & 0xff
```

The decoder follows Figure 4:

```text
u = (uint32(code) << 20) + 0x38080000
x = fp32_from_bits(u)
```

Native acceptance executes both transformations in x86 machine code for every
one of the 256 payloads and requires exact round trip of the byte value.

## Bits8

Bits8 stays an unsigned integer rather than entering the floating path. The
native candidate covers modulo-256 add/subtract/multiply/negation, unsigned
division and remainder, AND/OR/XOR/complement, logical shifts, equality, and
unsigned less-than. Host-side contract tests also cover bit test/set/clear.

## Direct-machine evidence

The native tests build ELF64 images with the repository's in-process ELF writer
and execute the exact generated x86-64 bytes on Linux. Float16/OFP8/E3M2 tests
exercise decoding, scalar SSE arithmetic, special values, overflow modes,
signed results, midpoint ties, and requantization in the generated program.

This is target-backend primitive evidence. The compiler-owned
`EDRIC_ONE_STEP_BODY v1` handoff in [`backend/idric_x86.py`](../backend/idric_x86.py)
does not yet expose these floating operations, so this work does not claim that
ordinary `.idric` source can already reach Float16, FP8, E3M2, or OotomoE5M3 through
that handoff. Bits8 source-level coverage is likewise separate from the direct
target primitive implemented here.

## Upstream references

- IEEE Std 754-2019, binary16:
  https://standards.ieee.org/standard/754-2019.html
- OCP, *8-bit Floating Point Specification (OFP8)*, Revision 1.0:
  https://www.opencompute.org/documents/ocp-8-bit-floating-point-specification-ofp8-revision-1-0-2023-06-20-pdf
- OCP, *Microscaling Formats (MX) Specification*, Version 1.0:
  https://www.opencompute.org/documents/ocp-microscaling-formats-mx-v1-0-spec-final-pdf
- Ootomo and Naruse, *Custom 8-bit floating point value format for reducing
  shared memory bank conflict in approximate nearest neighbor search*:
  https://arxiv.org/abs/2301.06672
- Idris 2 `Bits8`, pinned by the ARM reference notes to commit
  `1c630e67c386629a0fbbc6b78a59176fde7f0a76`:
  https://github.com/idris-lang/Idris2/blob/1c630e67c386629a0fbbc6b78a59176fde7f0a76/libs/prelude/Prelude/Num.idr


## Observational narrow-float arithmetic and geometry

`make low-precision-observe` runs one numerical observation suite over
Float16, E4M3, E5M2, E3M2, and OotomoE5M3. It reports reference, observed value, and
residue for primitive arithmetic, powers, square root, the 14-by-26 Dakota
Jacobian-vector product, planar rotation, and the two-position caster
multiplier. Residue is data, not a pass/fail threshold.

Every format receives the same source numerical fixture. The established E3M2
Jacobian/direction payloads are decoded once and those numerical values are
encoded independently into each format before arithmetic. OotomoE5M3 remains a
storage format: the suite uses a test-only decode/Float32-operation/encode path
for representable positive cases and reports the zero/negative Jacobian cases
as outside its published domain.

`make e3m2-observe` remains as a filtered view of the same suite.

## Corrected public storage names

The positive-only research codec is exposed as `ootomo_e5m3_encode_f32`,
`ootomo_e5m3_decode_f32`, and `build_ootomo_e5m3_*_elf`. The observer selects it
with `--format OotomoE5M3`; the ambiguous `--format E5M3` and old bare Python
API names are rejected or absent. No compatibility aliases are supplied.

Signed numerical work selects `OFP8_E4M3` or `OFP8_E5M2`. Byte 0x78 means
256 in E4M3, 32768 in E5M2, and 1.0625 in Ootomo storage, so a shared byte
width cannot justify reinterpreting one codebook as another.

The direct native FP8 test executes every one of the 256 bytes in each format
under both saturating and non-saturating conversion. Its expected finite bytes,
NaN classes and E5M2 infinity cases are specified independently of the Python
reference encoder. This is 1024 generated-machine cases. The existing unsigned
256-code native round trip remains a separate storage test.

This correction belongs to
[Flexible Pipes issue 83](https://github.com/isomorphisms/flexible-pipes/issues/83).
It does not add floating-point operations to the compiler-owned checked-source
handoff. The owned Float32 handoff and source-to-x86 FP8 integration remain
required before that separate acceptance claim can be made.
