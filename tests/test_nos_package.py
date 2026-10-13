"""Synthetic packages/PE data only. No installed game, key, or retail bytes."""
from pathlib import Path
import struct
import sys
import tempfile
import unittest
import zlib

from tools.nos_package import Package, aes_ecb, crc32_be, find_key, inflate, key_candidates
from tools.tas_package import MAGIC, Reader, Package as AscendPackage


def pack(fmt, *values):
    return struct.pack('<'+fmt, *values)


def frame(raw, block=64):
    pieces = [raw[i:i+block] for i in range(0, len(raw), block)]
    compressed = [zlib.compress(p) for p in pieces]
    return (pack('4I', MAGIC, block, sum(map(len, compressed)), len(raw)) +
            b''.join(pack('2I', len(c), len(p)) for c,p in zip(compressed, pieces)) + b''.join(compressed))


KEY = bytes(range(32))  # invented fixture key, not a game key


def fixture(path, mode='protected', key=KEY):
    names = ['None', 'Core', 'Class', 'StaticMesh', 'Level', 'Actor',
             'Mesh', 'PersistentLevel', 'Placed', 'Count', 'IntProperty']
    def name(s, number=0):
        return pack('2i', names.index(s), number)
    imports = b''.join(name('Core')+name('Class')+pack('i', 0)+name(s)
                       for s in ['StaticMesh', 'Level', 'Actor'])
    nt = b''.join(pack('i', len(n)+1)+n.encode()+b'\0'+bytes(8) for n in names)
    payloads = [pack('i', -1)+name('None')+b'geometry',
                pack('i', -1)+name('None')+pack('4i', 2, 2, 0, 3),
                pack('2iQHi', 1, 0, 0, 0, -1)+pack('i', -1)+name('Count')+
                name('IntProperty')+pack('3i', 4, 0, 42)+name('None')]
    no = 141 if mode == 'protected' else 256
    io = no+len(nt); eo = io+len(imports); depends = eo+3*68
    hs = no+((depends-no+20+32+15)//16)*16 if mode == 'protected' else depends
    at = hs; exports = b''
    for i, (s, raw) in enumerate(zip(['Mesh', 'PersistentLevel', 'Placed'], payloads)):
        flags = 0x0200000000000000 if i == 2 else 0
        exports += (pack('3i', -i-1, 0, 0)+name(s, 3 if i == 0 else 0)+
                    pack('iQ4i', 0, flags, len(raw), at, 0, 0)+bytes(20))
        at += len(raw)
    tables = nt+imports+exports; body = b''.join(payloads)
    ver, lic = (860, 0) if mode == 'plain' else (862, 7)
    summary = (pack('IHHIi', MAGIC, ver, lic, hs, 5)+b'None\0'+pack('I', 8)+
               pack('7i', len(names), no, 3, eo, 3, io, depends)+bytes(32)+
               pack('8i', 1, 3, len(names), 0, 42, 7, int(mode != 'plain'), int(mode == 'normal')))
    if mode == 'protected':
        chunk = frame(body)
        garbage = hs-depends-20
        summary += pack('6i', 0, 0, 0, garbage, depends-no, 0)
        self_header = tables+pack('5i', 1, hs, len(body), hs, len(chunk))+bytes(garbage)
        path.write_bytes(summary+aes_ecb(self_header, key, encrypt=True)+chunk)
    elif mode == 'normal':
        chunk = frame(tables+body)
        summary += pack('5i', no, len(tables+body), no, len(chunk), 0)
        path.write_bytes(summary.ljust(no, b'\0')+chunk)
    else:
        path.write_bytes((summary+pack('i', 0)).ljust(no, b'\0')+tables+body)
    return path


def executable_fixture(path, *, base=0x400000, section_rva=0x1000):
    """Invented PE with a CRC-builder instruction pattern and arbitrary data."""
    data = bytearray(0x800); data[:2] = b'MZ'; struct.pack_into('<I', data, 60, 0x80)
    data[0x80:0x84] = b'PE\0\0'
    struct.pack_into('<HHIIIHH', data, 0x84, 0x14c, 1, 0, 0, 0, 0xe0, 0)
    struct.pack_into('<H', data, 0x98, 0x10b); struct.pack_into('<I', data, 0xb4, base)
    section = 0x178; data[section:section+8] = b'.text\0\0\0'
    struct.pack_into('<4I', data, section+8, 0x600, section_rva, 0x600, 0x200)
    struct.pack_into('<I', data, section+36, 0x60000020)
    va = base+section_rva; seed = 1234567
    code = (bytes.fromhex('5355565768')+pack('I', seed)+bytes.fromhex('688000000068')+
            pack('I', va+0x100)+b'\xe8'+pack('i', 0x300-24)+bytes.fromhex('8b742420bf')+
            pack('I', va+0x180)+bytes.fromhex('83c40c8be82bfebb080000008d4900558d04376a0450e8')+
            pack('i', 0x300-60)+bytes.fromhex('890683c40c83c6044b75e95f5e5d5bc3'))
    data[0x200:0x200+len(code)] = code
    original = 'Example\0'.encode('utf-16le').ljust(128, b'\0')
    data[0x300:0x380] = original
    words = bytes(range(80, 112)); data[0x380:0x3a0] = words
    path.write_bytes(data)
    prefix = 'BCMP\0'.encode('utf-16le')
    seed = crc32_be(prefix+original[len(prefix):], seed)
    return b''.join(pack('I', crc32_be(words[i:i+4], seed)) for i in range(0,32,4))


class FramingTests(unittest.TestCase):
    def test_zlib_bounds_and_trailing_data(self):
        raw = b'example'*100
        self.assertEqual(inflate(zlib.compress(raw), len(raw)), raw)
        for data, size in [(zlib.compress(raw), 2), (zlib.compress(raw)[:-1], len(raw)),
                           (zlib.compress(raw)+b'x', len(raw)), (b'bad', len(raw))]:
            with self.assertRaises(ValueError):
                inflate(data, size)

    def test_plain_and_conventional_zlib_packages(self):
        with tempfile.TemporaryDirectory() as td:
            for mode in ('plain', 'normal'):
                p = Package(fixture(Path(td)/'Example.upk', mode))
                try:
                    p.validate_chunks()
                    self.assertEqual(p.entry(1)['name'], 'Mesh_2')
                    self.assertEqual(p.properties(3)[0], {'Count': 42})
                    self.assertEqual(p.level_actors(2), [3])
                    self.assertFalse(p.protected)
                    with self.assertRaises(ValueError):
                        p.read(p.virtual_size, 1)
                finally:
                    p.close()

    def test_runtime_pattern_relocation_and_bad_pe(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td)/'Invented.exe'
            for base, rva in [(0x400000,0x1000), (0x710000,0x8000)]:
                key = executable_fixture(path, base=base, section_rva=rva)
                self.assertEqual(list(key_candidates(path)), [key])
            path.write_bytes(b'not PE'.ljust(256, b'\0'))
            with self.assertRaises(ValueError):
                list(key_candidates(path))


@unittest.skipUnless(sys.platform == 'win32', 'Protected headers use Windows CNG')
class ProtectedTests(unittest.TestCase):
    def test_aes_known_answer(self):
        plain = bytes.fromhex('00112233445566778899aabbccddeeff')
        cipher = bytes.fromhex('8ea2b7ca516745bfeafc49904b496089')
        self.assertEqual(aes_ecb(plain, KEY, encrypt=True), cipher)
        self.assertEqual(aes_ecb(cipher, KEY), plain)
        with self.assertRaises(ValueError):
            aes_ecb(b'short', KEY)

    def test_protected_header_lookup_and_rejection(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); exe = root/'Invented.exe'; key = executable_fixture(exe)
            path = fixture(root/'Example.upk', key=key)
            self.assertEqual(find_key(exe, path), key)
            p = Package(path, executable=exe)
            try:
                self.assertEqual(p.table_counts, (11,3,3))
                self.assertEqual(p.properties(3)[0]['Count'], 42)
                self.assertEqual(p.level_actors(2), [3])
                self.assertTrue(p.protected)
                p.validate_chunks()
            finally:
                p.close()
            override = root/'local-key.bin'; override.write_bytes(key)
            p = Package(path, key_file=override); p.close()
            override.write_bytes(b'invalid')
            with self.assertRaisesRegex(ValueError, '32 raw bytes'):
                Package(path, key_file=override)
            with self.assertRaises(ValueError):
                Package(path, key=bytes(32))
            with self.assertRaises(ValueError):
                AscendPackage(path)
            damaged = bytearray(path.read_bytes()); damaged[-1:] = b''; path.write_bytes(damaged)
            with self.assertRaisesRegex(ValueError, 'No executable-derived key'):
                find_key(exe, path)

    def test_zlib_tfc_bulk_and_invalid_flags(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); p = Package(fixture(root/'Example.upk'), key=KEY)
            try:
                raw = b'fictional texture bytes'*20; chunk = frame(raw)
                cache = root/'Textures.tfc'; cache.write_bytes(b'prefix'+chunk)
                bulk = pack('4i', 3, len(raw), len(chunk), 6)
                self.assertEqual(p.bulk(Reader(bulk), cache), raw)
                self.assertIsNone(p.bulk(Reader(bulk), load=False))
                inline = pack('4i', 2, len(raw), len(chunk), 0)+chunk
                self.assertEqual(p.bulk(Reader(inline)), raw)
                with self.assertRaises(ValueError):
                    p.bulk(Reader(pack('4i', 3, len(raw), len(chunk), 7)), cache)
                with self.assertRaises(ValueError):
                    p.bulk(Reader(pack('4i', 18, len(raw), len(chunk), 0)+chunk))
            finally:
                p.close()


if __name__ == '__main__':
    unittest.main()
