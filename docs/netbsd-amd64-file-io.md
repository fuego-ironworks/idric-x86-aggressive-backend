# NetBSD amd64 cross-target: native read-only file I/O

Status: cross-compiler implementation / backend-level binary acceptance.
Not a complete Idriç System.File implementation or SDF runtime acceptance.

## Working target-level executable

The host Python 3 encoder emits static direct x86-64 ELF64. The resulting
program needs no Python, C compiler, linker, libc, CRT, RefC, or Chez.

It takes one filename in argv[1], opens O_RDONLY, reads in 65,536-byte
chunks to a stack buffer, retries EINTR and partial writes, writes raw
bytes to stdout, closes input and exits:
0 success, 64 missing argument, 66 open failure, 74 read/write/close error.
It performs no mailbox writes, renames, deletes, locking or journaling.

Code: backend/native_file_io.py
Linux native tests use NUL bytes, high bytes, CRLF and multiple chunks.
The NetBSD image passes ELF/PT_NOTE and instruction inspection only;
NetBSD native execution has NOT been performed.

## Native ABI

NetBSD sys/sys/syscall.h lists exit=1, read=3, write=4, open=5, close=6.
Syscall arguments are rdi/rsi/rdx/r10/r8/r9. The kernel sets carry for
failure and returns positive errno in rax; Linux uses negative errno
and different syscall numbers. The generated code keeps these separate.

NetBSD executable ELF requires the NetBSD identification PT_NOTE. The
direct packager places its note inside a mapped PT_LOAD segment with an
additional PT_NOTE program header. Its default OS version note is
1000000000 (NetBSD 10.0.0), overridable when target evidence requires it.
Do not infer SDF's release version.

Authoritative references:
- https://github.com/NetBSD/src/blob/trunk/sys/sys/syscall.h
- https://www.netbsd.org/docs/kernel/elf-notes.html
- https://man.netbsd.org/elf.5
- https://cdn.netbsd.org/pub/NetBSD/misc/joerg/GENERIC/src/src/sys/arch/amd64/include/frame_regs.h.html
- https://ftp.iij.ad.jp/pub/NetBSD/misc/joerg/GENERIC/src/src/sys/arch/x86/x86/syscall.c.html

## Cross-compilation on Linux (or another development host)

    python3 -m unittest discover -s tests -p test_native_file_io.py -v
    python3 -m unittest discover -s tests -p test_checked_netbsd_scalar.py -v
    python3 -m backend.native_file_io \
      --target netbsd-x86_64 \
      --output build/netbsd/file-reader-x86_64 \
      --listing build/netbsd/file-reader.intent.txt
    readelf -h -l -n build/netbsd/file-reader-x86_64

Copy the generated ELF to SDF using an established secure transfer path.
No compiler or Python is needed there. CI provides the binary, digest,
ELF report, instruction listing and precise acceptance receipt.

First run only against a small disposable SDF fixture after checking
uname -s, uname -m and uname -r. Do not dump the full 4.4 GB mailbox to
stdout; large-volume I/O, runtime limits and delivery are not qualified.

    printf 'abc\n' > "$HOME/opt/read-test.txt"
    /path/to/file-reader-x86_64 "$HOME/opt/read-test.txt" > "$HOME/opt/read-test.copy"
    cmp "$HOME/opt/read-test.txt" "$HOME/opt/read-test.copy"

## Checked Idriç boundary

backend/idric_x86.py now has a netbsd-x86_64 target for its existing
compiler-owned checked scalar one-step handoff: putChar, error handling
and process exit lower to the correct NetBSD syscalls, and ELF gets the
native NetBSD note. Unsupported Idriç operations still fail closed.

The direct file-reader fixture is BELOW this compiler handoff. It is
not claimed to be compiled from checked Idriç File/Read/Write programs.

## Remaining for full Idriç IO and mailbox work

1. Extend compiler-owned checked IR with runtime file primitives, byte
   arrays, IO sequencing, runtime inputs, errors, and file-handle lifetime.
2. Lower checked operations to open/read/write/seek/close using the
   target-level native ABI; exercise seek/tell separately with 64-bit
   offsets and NetBSD's padded lseek argument convention.
3. Compile a real Idriç file-reading program (not a synthetic backend
   fixture), compare exact binary bytes, and run it under NetBSD.
4. Qualify the SDF target and file/resource limits, recording exact
   artifact SHA and OS version.
5. Only before mutating live mail: O_NOFOLLOW/O_EXCL, stat/fstat,
   delivery locks, fsync, atomic rename, durable transaction journal,
   recovery, and source-prefix/append-tail preservation.

This branch is a cross-target foundation, not permission to remove mail.
