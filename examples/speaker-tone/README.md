# Speaker tone — x86-64 Linux oracle

This directory advances the `speaker_tone` row in `isomorphisms/Idric#85`.
It is a handwritten Linux/x86-64 platform oracle, not evidence that Idriç
currently emits this program.

The native no-libc ELF opens `/dev/dsp`, asks the Linux OSS PCM compatibility
layer for unsigned 8-bit mono audio near 8 kHz, constructs a 400 Hz square-wave
fixture, writes 0.25 seconds of samples, drains the playback queue, and exits.

The fixture is deliberately exact and small:

- nominal sample rate: 8000 samples/s;
- period: 20 samples;
- nominal frequency: 400 Hz;
- length: 100 cycles / 2000 samples / 0.25 s;
- high sample: 224;
- low sample: 32.

`/dev/dsp` is one Linux adapter below the semantic action “play a tone”. ALSA
OSS emulation exposes `/dev/dsp` through `snd-pcm-oss`; another Linux adapter or
Android implementation may use a different boundary without changing the
fixture. Linux documents `snd-pcm-oss` as the PCM OSS compatibility module.

The ordinary CI check assembles and inspects the no-libc ELF. When the hosted
runner has no `/dev/dsp`, it also verifies the program's explicit exit-10
no-device path. That does not establish successful `/dev/dsp` playback.

The current full-system lane exercises a separate native ALSA oracle with the
same 400 Hz / 0.25 s fixture. It boots an Ubuntu guest with QEMU AC97, requires
the guest ALSA playback PCM, captures QEMU's audio output as WAV, and verifies
frequency and duration from the capture. A green receipt establishes that
virtual ALSA/AC97 playback path; it does not establish playback by the handwritten
`/dev/dsp` ELF. Accepting that OSS adapter would require a guest exposing
`/dev/dsp` and executing the no-libc ELF against it.

Generated Idriç lowering and physical-device playback remain separate evidence.
A generated PCM buffer without device playback is only a lower-level fixture.

## C division frontend

The ALSA oracle's half-period division uses literal `÷`, compiled by ICK
`c61e448251744a2f40ad743ebef1a027bdcd2f9d` through the shared pinned producer
`ai-ci/ick-host@903b2cb27ea572c9c6cb2ffa9f39e0fbf06ec9f8`. The full-system
launcher requires an explicit `ICK` compiler and retains the original guest,
AC97, capture and frequency/duration checks. Its stage contract applies to this
handwritten C oracle; it does not relabel the backend's direct-ELF path as C.

Local original/migrated oracle executions through the real ALSA `file` PCM
produce byte-identical unsigned-eight-bit samples, SHA256
`8354d3c0db769ede086c341586ffe2e35464bbe8ebd83c1ad95cd0fb74e6cc09`.
This establishes the generated samples and ALSA write path, not AC97 or
physical playback. The full-system CI retains those separate obligations.
Existing Python capture analysis remains Python-to-Ithon migration debt;
this change adds no Python source or replaces its acceptance criteria.
