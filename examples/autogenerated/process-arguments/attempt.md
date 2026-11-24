# Native process arguments

A process supplies its argument count and a vector of terminated byte strings at entry. The count includes the executable name. The program reads a supplied argument only after checking that its index lies within the vector. Its observable result is one character indicating which command line it received.

The checked source declares the same count and indexed-string foreign contracts as the private bindings behind System.getArgs. The backend must preserve the entry stack across output and nested calls. A count is a scalar. An argument string can be compared with a literal without exposing its native address as an integer.

Compile this source with the checked one-step emitter from isomorphisms/Idric revision `29420f7e5a954ba0a66f622f0311298f823fbfff`. The existing checked-closure acceptance job runs `scripts/check_process_arguments.py` with that compiler. It regenerates the handoff twice, generates both targets twice, and runs one Linux ELF against nine argument vectors. With exactly two user arguments its second argument determines whether it writes `!Y` or `!N`. Other counts produce `!-`. The initial write happens before the process reads its count.

The emitted entry saves the original stack pointer across those writes. Indexed access checks the index against argc using an unsigned comparison, so negative indices and indices at or above argc exit with status 2 before reading a pointer. The string equality operation compares an argv value and an unescaped literal as terminated UTF-8 bytes. Unit regressions cover argv[0], non-ASCII and empty literals, read bounds, raw-address misuse, and both branches of a runtime range proof. NetBSD evidence is cross-generation and ELF inspection.

The checked 158-definition `BinaryFilePrefix.idric` handoff has SHA-256 `09780efc1a45ebb599df1b7cc11c35a088e8735da56fd20629c5ca2eac84f38d`. After reading argc in `System.getArgs`, specialization reaches the recursive call to `Prelude.Types.takeUntil` from `Prelude.Types.rangeFromTo`. The file program still requires dynamic list construction and traversal before selecting a path and opening a file.
