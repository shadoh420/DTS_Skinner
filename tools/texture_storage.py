"""Lossless DXT containers and non-destructive shared texture output."""
from pathlib import Path
import struct


def game_texture_dir(data_root, game):
    return Path(data_root) / game / 'textures'


def dds_bytes(width, height, fourcc, mipmaps):
    """Wrap existing top-to-bottom DXT blocks, largest mip first; no re-encoding."""
    if fourcc not in ('DXT1', 'DXT3', 'DXT5') or not mipmaps:
        raise ValueError('Expected DXT mip chain')
    if min(width, height) < 1 or len(mipmaps) > max(width, height).bit_length():
        raise ValueError('Invalid DDS dimensions/mips')
    for level, payload in enumerate(mipmaps):
        w, h = max(1, width >> level), max(1, height >> level)
        expected = ((w+3)//4)*((h+3)//4)*(8 if fourcc == 'DXT1' else 16)
        if len(payload) != expected:
            raise ValueError('Invalid DXT mip size')
    header = bytearray(128); header[:4] = b'DDS '
    struct.pack_into('<7I', header, 4, 124, 0x81007 | (0x20000 if len(mipmaps)>1 else 0),
                     height, width, len(mipmaps[0]), 0, len(mipmaps))
    struct.pack_into('<II4s', header, 76, 32, 4, fourcc.encode('ascii'))
    struct.pack_into('<I', header, 108, 0x1000 | (0x400008 if len(mipmaps)>1 else 0))
    return bytes(header) + b''.join(mipmaps)


def write_texture(directory, filename, payload):
    """Imports never delete or overwrite the shared library or user PNG edits."""
    target = Path(directory) / filename
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        if isinstance(payload, bytes):
            target.write_bytes(payload)
        else:
            payload.save(target)


def map_data_path(data_root, game, filename, texture_directory=None):
    """Shared texture lookup with a read-only fallback for pre-migration maps."""
    from werkzeug.security import safe_join
    pack = Path(data_root) / (game + '-maps')
    if filename.startswith('textures/'):
        name = filename.removeprefix('textures/')
        shared = safe_join(str(texture_directory or game_texture_dir(data_root, game)), name)
        if shared and Path(shared).is_file():
            return Path(shared)
    legacy = safe_join(str(pack), filename)
    return Path(legacy) if legacy else None


class SourceTextureStore:
    """Readable source-content names; original JPEGs, lossless PNG derivatives.

    Existing bytes are never overwritten. Only unreferenced, unchanged files
    created by this instance may be retired, never another import's textures.
    """
    def __init__(self, output, assets):
        self.output = Path(output); self.output.mkdir(parents=True, exist_ok=True)
        self.existing = {p.name for p in self.output.iterdir() if p.is_file()}
        self.before = len(self.existing); self.created = {}; self.names = {}; self.sources = {}
        for name, asset in assets.items():
            if Path(name).suffix.lower() in ('.jpg','.jpeg','.tga','.png'):
                self.sources.setdefault(asset.get('content_id'), []).append((name, asset))

    def name(self, raw, source_path):
        import hashlib, re, zlib
        digest = hashlib.sha256(raw if raw is not None else b'RTCW $whiteimage').hexdigest()
        if digest not in self.names:
            smallest = source_path.replace('\\','/').lower().lstrip('/')
            if raw is not None:
                identity = f'{zlib.crc32(raw):08x}:{len(raw)}'
                for name, asset in sorted(self.sources.get(identity, []) + self.sources.get(None, [])):
                    if name >= smallest: break
                    if hashlib.sha256(asset['read']()).hexdigest() == digest:
                        smallest = name; break
            prefix = re.sub(r'[^a-z0-9_-]', '_', str(Path(smallest).with_suffix('')))[:100]
            self.names[digest] = prefix+'__'+digest[:12]
        return self.names[digest]

    def write(self, filename, payload):
        import hashlib, io
        if not isinstance(payload, bytes):
            stream=io.BytesIO();payload.save(stream,format='PNG');payload=stream.getvalue()
        absent = not (self.output/filename).exists()
        write_texture(self.output, filename, payload)
        if absent:self.created[filename]=hashlib.sha256(payload).hexdigest()
        return filename

    def convert(self, legacy, raw, source_path):
        import hashlib, io
        from PIL import Image
        name=self.name(raw,source_path)
        if raw is None:image=Image.new('RGBA',(1,1),'white');jpeg=False
        else:
            with Image.open(io.BytesIO(raw)) as source:
                image=source.convert('RGBA');jpeg=source.format=='JPEG'
        stream=io.BytesIO();image.save(stream,format='PNG');png=stream.getvalue()
        # Retain assignments to edited pre-storage PNGs (including 1c/1d names).
        digest=hashlib.sha256(raw if raw is not None else b'RTCW $whiteimage').hexdigest()
        for oldname in dict.fromkeys(n for n in (legacy,name+'.png','image_'+digest+'.png') if n):
            old=self.output/oldname
            if old.is_file() and oldname.endswith('.png'):
                if old.read_bytes()!=png:return oldname
                if oldname not in self.existing:self.created.setdefault(oldname,hashlib.sha256(png).hexdigest())
        return self.write(name+('.jpg' if jpeg else '.png'), raw if jpeg else png)

    def derived(self, raw, source_path, image):
        import hashlib, io
        stream=io.BytesIO();image.save(stream,format='PNG');payload=stream.getvalue()
        prefix=self.name(raw,source_path).rsplit('__',1)[0]
        return self.write(prefix+'__'+hashlib.sha256(payload).hexdigest()[:12]+'.png',payload)

    def finish(self, used):
        import hashlib
        removed=[]
        for name,digest in self.created.items():
            path=self.output/name
            if name not in used and path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest()==digest:
                path.unlink();removed.append(name)
        files=sorted(p.name for p in self.output.iterdir() if p.is_file() and p.suffix.lower() in ('.png','.jpg','.jpeg'))
        return dict(before=self.before,after=len(files),referenced=len(set(files)&used),removed=removed,
                    retained_unreferenced=sorted(set(files)-used))
