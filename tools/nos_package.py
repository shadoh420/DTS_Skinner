"""Read Nosgoth UE3 packages; never modify the install or persist its key.

The executable is parsed as data, never loaded or executed. The key derivation
was independently checked against the installed x86 executable. Native Windows
CNG supplies AES, so no crypto/RE Python dependency is needed at run time.
"""
from pathlib import Path
import ctypes
import re
import struct
import sys
import zlib

from tools import tas_package as ue3


def aes_ecb(data, key, *, encrypt=False):
    """AES ECB without padding. Encryption is used only by synthetic fixtures."""
    if len(key) not in (16, 24, 32) or len(data) % 16 or len(data) > ue3.MAX_DATA:
        raise ValueError('Invalid AES key length or block extent')
    if sys.platform != 'win32':
        raise OSError('Nosgoth protected headers require Windows CNG (bcrypt)')
    from ctypes import wintypes as w
    c = ctypes
    dll = c.WinDLL('bcrypt')
    signatures = {
        'BCryptOpenAlgorithmProvider': [c.POINTER(w.HANDLE), w.LPCWSTR, w.LPCWSTR, w.ULONG],
        'BCryptSetProperty': [w.HANDLE, w.LPCWSTR, c.c_void_p, w.ULONG, w.ULONG],
        'BCryptGenerateSymmetricKey': [w.HANDLE, c.POINTER(w.HANDLE), c.c_void_p,
                                      w.ULONG, c.c_void_p, w.ULONG, w.ULONG],
        'BCryptDestroyKey': [w.HANDLE],
        'BCryptCloseAlgorithmProvider': [w.HANDLE, w.ULONG],
    }
    crypt_args = [w.HANDLE, c.c_void_p, w.ULONG, c.c_void_p, c.c_void_p,
                  w.ULONG, c.c_void_p, w.ULONG, c.POINTER(w.ULONG), w.ULONG]
    signatures.update(BCryptEncrypt=crypt_args, BCryptDecrypt=crypt_args)
    for name, args in signatures.items():
        fn = getattr(dll, name); fn.argtypes = args; fn.restype = c.c_long

    def check(status):
        if status:
            raise ValueError(f'Windows AES operation failed ({status & 0xffffffff:#x})')

    algorithm, handle = w.HANDLE(), w.HANDLE()
    try:
        check(dll.BCryptOpenAlgorithmProvider(c.byref(algorithm), 'AES', None, 0))
        mode = c.create_unicode_buffer('ChainingModeECB')
        check(dll.BCryptSetProperty(algorithm, 'ChainingMode', mode, c.sizeof(mode), 0))
        check(dll.BCryptGenerateSymmetricKey(algorithm, c.byref(handle), None, 0, key, len(key), 0))
        out = c.create_string_buffer(len(data)); written = w.ULONG()
        fn = dll.BCryptEncrypt if encrypt else dll.BCryptDecrypt
        check(fn(handle, data, len(data), None, None, 0, out, len(data), c.byref(written), 0))
        if written.value != len(data):
            raise ValueError('AES output length mismatch')
        return out.raw[:written.value]
    finally:
        if handle:
            dll.BCryptDestroyKey(handle)
        if algorithm:
            dll.BCryptCloseAlgorithmProvider(algorithm, 0)


def crc32_be(data, seed):
    """Non-reflected CRC-32, complemented input/output, as used by UE3."""
    value = seed ^ 0xffffffff
    for byte in data:
        value ^= byte << 24
        for _ in range(8):
            value = ((value << 1) ^ (0x04c11db7 if value & 0x80000000 else 0)) & 0xffffffff
    return value ^ 0xffffffff


# Instruction structure of the eight-word CRC key builder. All addresses,
# relative calls and the seed are captures; no key or key-producing data here.
KEY_PATTERN = re.compile(
    rb'\x53\x55\x56\x57\x68(.{4})\x68\x80\x00\x00\x00\x68(.{4})\xe8(.{4})'
    rb'\x8b\x74\x24\x20\xbf(.{4})\x83\xc4\x0c\x8b\xe8\x2b\xfe\xbb\x08\x00\x00\x00'
    rb'\x8d\x49\x00\x55\x8d\x04\x37\x6a\x04\x50\xe8(.{4})'
    rb'\x89\x06\x83\xc4\x0c\x83\xc6\x04\x4b\x75\xe9\x5f\x5e\x5d\x5b\xc3', re.DOTALL)


def key_candidates(executable):
    """Yield keys derived from matching x86 PE code/data, not fixed offsets."""
    path = Path(executable)
    if not 256 <= path.stat().st_size <= ue3.MAX_DATA:
        raise ValueError('Invalid Nosgoth executable size')
    data = path.read_bytes()
    r = ue3.Reader(data)
    if r.take(2) != b'MZ':
        raise ValueError('Expected a PE Nosgoth.exe')
    r.at = 60; pe = r.unpack('I')[0]; r.at = pe
    if r.take(4) != b'PE\0\0':
        raise ValueError('Invalid PE signature')
    machine, count = r.unpack('HH'); r.take(12); optional, flags = r.unpack('HH')
    opt = r.take(optional)
    if machine != 0x14c or len(opt) < 32 or struct.unpack_from('<H', opt)[0] != 0x10b:
        raise ValueError('Expected the 32-bit Nosgoth executable')
    base = struct.unpack_from('<I', opt, 28)[0]
    if not 1 <= count <= 96:
        raise ValueError('Invalid PE section count')
    sections = []
    for _ in range(count):
        s = r.take(40); virtual, length, offset = struct.unpack_from('<3I', s, 12)
        characteristics = struct.unpack_from('<I', s, 36)[0]
        if offset + length > len(data):
            raise ValueError('PE section outside executable')
        sections.append((base + virtual, offset, length, characteristics))

    def at_va(address, size):
        for va, offset, length, _ in sections:
            if va <= address and address + size <= va + length:
                return data[offset + address-va:offset + address-va+size]
        raise ValueError('Key-builder data outside backed PE sections')

    for va, offset, length, characteristics in sections:
        if not characteristics & 0x20000000:
            continue
        for match in KEY_PATTERN.finditer(data, offset, offset + length):
            seed, source, call1, words, call2 = (struct.unpack('<I', g)[0] for g in match.groups())
            # Both calls must target the same CRC helper inside the image.
            target1 = (va + match.start()-offset + 24 + call1) & 0xffffffff
            target2 = (va + match.start()-offset + 60 + call2) & 0xffffffff
            if target1 != target2:
                continue
            at_va(target1, 1)
            original = at_va(source, 128)
            # Startup overwrites the UTF-16 game name, including its terminator,
            # but leaves the remainder of the initialized buffer intact.
            prefix = 'BCMP\0'.encode('utf-16le')
            seed = crc32_be(prefix + original[len(prefix):], seed)
            raw = at_va(words, 32)
            yield b''.join(struct.pack('<I', crc32_be(raw[i:i+4], seed)) for i in range(0, 32, 4))


def find_key(executable, validation_package):
    """Accept a candidate only after complete table and chunk-frame validation."""
    try:
        for key in key_candidates(executable):
            try:
                p = Package(validation_package, key=key)
                try:
                    if not p.protected:
                        raise ValueError('Key validation needs a protected Nosgoth package')
                    p.validate_chunks()
                    return key
                finally:
                    p.close()
            except (ValueError, struct.error, UnicodeError):
                continue
    except (OSError, ValueError, struct.error) as exc:
        raise ValueError('Cannot derive Nosgoth header key from the installed executable; '
                         'use a supported Nosgoth.exe or an explicit local key file') from exc
    raise ValueError('No executable-derived key validated the Nosgoth package; '
                     'this executable build is unsupported (or the package is damaged). '
                     'An explicit local key file may be supplied.')


def inflate(raw, expected):
    if not 0 <= expected <= ue3.MAX_DATA:
        raise ValueError('Invalid zlib output size')
    decoder = zlib.decompressobj()
    try:
        result = decoder.decompress(raw, expected + 1)
    except zlib.error as exc:
        raise ValueError('Invalid zlib block') from exc
    if len(result) != expected or not decoder.eof or decoder.unused_data or decoder.unconsumed_tail:
        raise ValueError('Invalid zlib block length or trailing data')
    return result


class Package(ue3.Package):
    versions = ((862, 7), (860, 0))
    compressions = (0, 1)
    bulk_compressions = (2, 16)

    def __init__(self, path, key=None, executable=None, key_file=None):
        if key_file is not None:
            if key is not None:
                raise ValueError('Supply either key or key_file, not both')
            with Path(key_file).open('rb') as f:
                key = f.read(33)
            if len(key) != 32:
                raise ValueError('Local Nosgoth key file must contain exactly 32 raw bytes')
        self._key, self._executable = key, executable
        self._header = b''; self._header_start = 0
        self.protected = False; self.additional_packages = []
        super().__init__(path)
        try:
            for e in self.imports:
                if e['outer']:
                    self.entry(e['outer'])
            for e in self.exports:
                for field in ('cls', 'base', 'outer', 'archetype'):
                    if e[field]:
                        self.entry(e[field])
        except Exception:
            self.close(); raise
        finally:
            self._key = None

    def prepare_summary(self, r):
        self.package_source = r.unpack('I')[0]
        if self.compression != 1 or self.chunks:
            return
        if (self.version, self.licensee) != (862, 7) or not self.flags & 8:
            raise ValueError('Unexpected Nosgoth protected package summary')
        self.protected = True
        self.additional_packages = [r.text() for _ in range(r.count(4, 4096))]
        for _ in range(r.count(24, 100_000)):
            r.take(20); r.take(r.count(4)*4)
        self.garbage_size, self.chunk_info_offset, self.last_block_size = r.unpack('3i')
        no = self.table_offsets[0]; length = self.header_size-no
        if (r.at != no or not 0 < length <= ue3.MAX_DATA or length % 16 or
                self.header_size > self.size or not 0 <= self.garbage_size < length or
                not 0 <= self.chunk_info_offset < length-self.garbage_size or self.last_block_size != 0):
            raise ValueError('Invalid protected header extent')
        key = self._key
        if key is None:
            executable = self._executable or self.path.parent.parent.parent/'Binaries'/'Win32'/'Nosgoth.exe'
            key = find_key(executable, self.path)
        # Decrypt once; compressed bodies stay on the shared lazy block reader.
        header = aes_ecb(super().physical(no, length), key)
        cr = ue3.Reader(header, at=self.chunk_info_offset)
        self.chunks = [cr.unpack('4i') for _ in range(cr.count(16, 100_000))]
        if (not self.chunks or cr.at != length-self.garbage_size or
                no+self.chunk_info_offset != self.depends or self.chunks[0][0] != self.header_size):
            raise ValueError('Decrypted Nosgoth chunk table does not match summary')
        physical_end, virtual_end = self.header_size, self.header_size
        for uo, us, co, cs in self.chunks:
            if co != physical_end or uo != virtual_end or min(us, cs) <= 0:
                raise ValueError('Noncontiguous protected package chunks')
            physical_end, virtual_end = co+cs, uo+us
        if physical_end != self.size:
            raise ValueError('Protected chunks do not cover the physical body')
        self._header_start = no
        self._header = header

    def physical(self, offset, size):
        raw = super().physical(offset, size)
        lo = max(offset, self._header_start)
        hi = min(offset+size, self._header_start+len(self._header))
        if lo < hi:
            raw = raw[:lo-offset] + self._header[lo-self._header_start:hi-self._header_start] + raw[hi-offset:]
        return raw

    def decode(self, raw, size):
        return inflate(raw, size)

    def decode_bulk(self, raw, size, compression):
        return inflate(raw, size) if compression == 2 else ue3.Package.decode(self, raw, size)

    def properties(self, ref):
        r = self.object_reader(ref)
        if self.entry(ref)['flags'] & 0x0200000000000000:
            node, state = r.unpack('2i'); r.take(10)
            if node:
                r.take(4)  # newer UE3 has no serialized state-stack array
        r.take(4)  # NetIndex; native component preambles need the later map adapter
        return r.properties(), r

    def level_actors(self, ref):
        if self.entry(ref)['class'] != 'Level':
            raise ValueError('Expected a Level export')
        _, r = self.properties(ref)
        if r.i32() != ref:
            raise ValueError('Level owner does not match export')
        actors = [x[0] for x in r.array('i')]
        if any(x < 0 or x > len(self.exports) for x in actors):
            raise ValueError('Invalid level actor reference')
        return [x for x in actors if x]

    def validate_chunks(self):
        for uo, us, co, cs in self.chunks:
            ue3.compressed_blocks(self.physical, co, cs, us)

