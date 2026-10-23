"""Cross-platform structural and Linux-native tests of direct I/O codegen."""

import os
import platform
import struct
import subprocess
import tempfile
import unittest
from pathlib import Path

from backend.elf64 import CODE_OFFSET, ELF_BASE, NETBSD_10_ABI_VERSION, netbsd_ident_note, write_elf
from backend.native_file_io import BUFFER_BYTES, SYSCALLS, cross_compile


class NetBSDFileIOTests(unittest.TestCase):
    def test_syscall_numbers_are_target_specific(self):
        self.assertEqual(SYSCALLS['netbsd-x86_64'],
                         dict(exit=1, read=3, write=4, open=5, close=6))
        self.assertEqual(SYSCALLS['linux-x86_64'],
                         dict(exit=60, read=0, write=1, open=2, close=3))

    def test_netbsd_note_segment(self):
        binary, listing = cross_compile('netbsd-x86_64')
        self.assertEqual(binary[:7], b'\x7fELF\x02\x01\x01')
        self.assertEqual(struct.unpack_from('<H', binary, 18)[0], 62)
        self.assertEqual(struct.unpack_from('<H', binary, 56)[0], 2)
        self.assertEqual(struct.unpack_from('<Q', binary, 24)[0], ELF_BASE + CODE_OFFSET)
        self.assertEqual(struct.unpack_from('<Q', binary, 40)[0], 0)
        self.assertEqual(binary[0x200:0x218], netbsd_ident_note())
        self.assertEqual(len(netbsd_ident_note()), 24)
        # Second ELF64 program header is a four-byte-aligned PT_NOTE.
        self.assertEqual(struct.unpack_from('<IIQQQQQQ', binary, 120),
                         (4, 4, 0x200, ELF_BASE+0x200, ELF_BASE+0x200, 24, 24, 4))
        self.assertIn('jc read_error', listing)
        self.assertIn('jc write_error', listing)
        self.assertIn('jc open_error', listing)
        self.assertIn('syscall ; close', listing)
        self.assertIn('cmp rax, 4', listing)  # EINTR
        self.assertNotIn('cmp rax, -4095', listing)
        self.assertEqual(binary, cross_compile('netbsd-x86_64')[0])

    def test_linux_elf_is_backwards_compatible(self):
        image, listing = cross_compile('linux-x86_64')
        self.assertEqual(struct.unpack_from('<H', image, 56)[0], 1)
        self.assertNotIn(b'NetBSD\0', image)
        self.assertIn('cmp rax, -4095', listing)
        self.assertIn('cmp rax, -4', listing)  # EINTR
        self.assertNotIn('jc read_error', listing)
        code=b'\x0f\x0b'
        previous=(b'\x7fELF\x02\x01\x01'+b'\x00'*9
                  +struct.pack('<HHIQQQIHHHHHH',2,62,1,ELF_BASE+CODE_OFFSET,64,0,0,64,56,1,0,0,0)
                  +struct.pack('<IIQQQQQQ',1,5,0,ELF_BASE,ELF_BASE,CODE_OFFSET+len(code),CODE_OFFSET+len(code),4096))
        self.assertEqual(write_elf(code), previous.ljust(CODE_OFFSET,b'\x00')+code)

    def test_bad_abi_version_fails_closed(self):
        for invalid in (-1, 0, 2**32):
            with self.assertRaises(ValueError):
                cross_compile('netbsd-x86_64', netbsd_abi_version=invalid)
        with self.assertRaises(ValueError):
            cross_compile('windows-x86_64')

    @unittest.skipUnless(platform.system()=='Linux' and platform.machine()=='x86_64',
                         'native execution requires Linux x86-64')
    def test_linux_native_binary_file_copy_and_errors(self):
        with tempfile.TemporaryDirectory() as root:
            folder = Path(root)
            elf = folder/'file-reader'
            elf.write_bytes(cross_compile('linux-x86_64')[0])
            elf.chmod(0o755)
            for content in (b'', b'abc', bytes(range(256)),
                            b'\x00\xff\r\nFrom thing\n'+os.urandom(BUFFER_BYTES*3+19)):
                source=folder/'binary-file'
                source.write_bytes(content)
                result=subprocess.run([elf,source],capture_output=True,check=False)
                self.assertEqual(result.returncode,0,(result.stdout[:80],result.stderr))
                self.assertEqual(result.stdout,content)
                self.assertEqual(result.stderr,b'')
            self.assertEqual(subprocess.run([elf],capture_output=True).returncode,64)
            self.assertEqual(subprocess.run([elf,folder/'not-here'],capture_output=True).returncode,66)
            source.write_bytes(b'bytes')
            result=subprocess.run([elf,source],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
                                  preexec_fn=lambda: os.close(1))
            self.assertEqual(result.returncode,74)


if __name__=='__main__':
    unittest.main()
