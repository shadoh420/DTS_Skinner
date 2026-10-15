"""Static start-state raster of an id Tech 4 .gui (Window.cpp windowDef tree) for guiSurf surfaces.

Draws rect/backcolor/background/matcolor/matscale/text/border in declaration order, children clipped
to their parent. Scripts, transitions, gui:: variables and onTime animation are not run.
"""
import io
import re

import numpy as np
from PIL import Image, ImageDraw, ImageFont

try:
    from tools.doom3_assets import dds_path, lex, material_record, path_name
except ImportError:
    from doom3_assets import dds_path, lex, material_record, path_name

WINDOWS = ('windowdef', 'editdef', 'choicedef', 'sliderdef', 'markerdef', 'binddef', 'listdef', 'fielddef',
           'renderdef', 'gamessddef', 'gamebearshootdef', 'gamebustoutdef')
NUMBER = re.compile(r'[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?$')
SIZE = (640, 480)
TYPED = dict(rect=4, backcolor=4, matcolor=4, forecolor=4, bordercolor=4, visible=1, background=1, text=1, textscale=1,
             textalign=1, bordersize=1, matscalex=1, matscaley=1, noclip=1)


def parse_gui(text):
    """The first windowDef as {kind, name, props, children}; props hold the raw tokens of each property line."""
    tokens = lex(text); at = 0
    def block_end(i):
        depth = 0
        while i < len(tokens):
            depth += (tokens[i] == '{') - (tokens[i] == '}'); i += 1
            if not depth: return i
        return i
    def window(i):
        kind, name = tokens[i].lower(), tokens[i + 1]; i += 2
        while i < len(tokens) and tokens[i] == '\n': i += 1
        if i >= len(tokens) or tokens[i] != '{': raise ValueError('Expected { after ' + name)
        node = dict(kind=kind, name=name, props={}, children=[]); i += 1
        while i < len(tokens) and tokens[i] != '}':
            t = tokens[i]
            if t == '\n': i += 1; continue
            if t.lower() in WINDOWS: child, i = window(i); node['children'].append(child); continue
            if t.lower() in TYPED:
                # Typed registers (Window.cpp regList): vec4 values are four numbers or one string/expression.
                values = []; j = i + 1
                count = TYPED[t.lower()] if j < len(tokens) and NUMBER.match(tokens[j]) else 1
                while j < len(tokens) and len(values) < count and tokens[j] not in ('\n', '{', '}'):
                    if tokens[j] != ',': values.append(tokens[j])
                    j += 1
                if len(values) == 1 and ',' in values[0]: values = [v.strip() for v in values[0].split(',')]
                node['props'].setdefault(t.lower(), values); i = j; continue
            line = []; j = i
            while j < len(tokens) and tokens[j] not in ('\n', '{', '}'): line.append(tokens[j]); j += 1
            if j < len(tokens) and tokens[j] == '{': i = block_end(j); continue   # onTime/onAction/... event blocks
            node['props'].setdefault(t.lower(), [x for x in line[1:] if x != ',']); i = j
        return node, i + 1
    while at < len(tokens):
        if tokens[at].lower() in WINDOWS: return window(at)[0]
        at += 1
    raise ValueError('No windowDef')


def numbers(props, key, default):
    values = props.get(key)
    if not values or not all(NUMBER.match(v) for v in values): return list(default)
    values = [float(v) for v in values]
    return (values + list(default)[len(values):])[:len(default)]


class GuiImages:
    """Background names are materials (their first image) or bare image paths; TGA, then the precompressed DDS."""
    def __init__(self, files, defs, policy=None):
        self.files, self.defs, self.policy, self.cache = files, defs, policy, {}

    def __call__(self, name):
        key = path_name(name)
        if key not in self.cache: self.cache[key] = self.load(key)
        return self.cache[key]

    def load(self, name):
        body = self.defs.materials.get(self.policy.material_name(name) if self.policy else name)
        record = material_record(name, body, self.policy) if body else None
        expression = path_name(record['diffuse'] if record else name).removesuffix('.tga')
        # Doom 3 implicit image materials are `blend blend` (Material.cpp SetDefaultText). Quake 4's GUI art
        # (opaque dark scanlines, low-alpha white scratches) only reads additively: hypothesis src_alpha + one.
        blend = tuple(record['blend']) if record else ('gl_src_alpha', 'gl_one') if self.policy else ()
        mode = {('gl_one', 'gl_one'): 'add', ('gl_src_alpha', 'gl_one'): 'addalpha', ('gl_dst_color', 'gl_zero'): 'filter'}.get(blend, 'blend')
        image = self.image(expression)
        return (image, mode) if image is not None else None

    def image(self, expression):
        # Image_load.cpp: a precompressed DDS wins over the source image (Quake 4 ships small TGA stand-ins).
        candidates = [self.policy.dds_path(expression) if self.policy else dds_path(expression), expression + '.tga', expression + '.png']
        for path in candidates:
            if path in self.files:
                try:
                    with Image.open(io.BytesIO(self.files[path].read())) as image: return image.convert('RGBA')
                except (OSError, ValueError): continue
        return None


def render_gui(root, images, strings=None, size=SIZE):
    """Premultiplied RGBA raster: draw the result with gl_one, gl_one_minus_src_alpha."""
    strings = strings or {}
    canvas = np.zeros((size[1], size[0], 4), dtype=np.float32); sx, sy = size[0] / 640, size[1] / 480
    def composite(layer, x, y, clip, mode='blend'):
        box = (max(int(x), clip[0]), max(int(y), clip[1]), min(int(x) + layer.width, clip[2]), min(int(y) + layer.height, clip[3]))
        if box[2] <= box[0] or box[3] <= box[1]: return
        src = np.asarray(layer.crop((box[0] - int(x), box[1] - int(y), box[2] - int(x), box[3] - int(y))), dtype=np.float32) / 255
        dst = canvas[box[1]:box[3], box[0]:box[2]]; rgb, a = src[..., :3], src[..., 3:]
        if mode == 'add': dst[..., :3] += rgb
        elif mode == 'addalpha': dst[..., :3] += rgb * a
        elif mode == 'filter': dst[..., :3] *= rgb
        else: dst[..., :3] = rgb * a + dst[..., :3] * (1 - a); dst[..., 3:] = a + dst[..., 3:] * (1 - a)
        np.clip(dst, 0, 1, out=dst)
    def draw(node, ox, oy, clip):
        p = node['props']
        if p.get('visible') and NUMBER.match(p['visible'][0]) and float(p['visible'][0]) == 0: return
        x, y, w, h = numbers(p, 'rect', (0, 0, 640, 480))
        x0, y0 = (ox + x) * sx, (oy + y) * sy; pw, ph = abs(w) * sx, abs(h) * sy
        rect = (x0 - (pw if w < 0 else 0), y0 - (ph if h < 0 else 0)); width, height = max(1, round(pw)), max(1, round(ph))
        back = numbers(p, 'backcolor', (0, 0, 0, 0))
        if back[3] > 0:
            composite(Image.new('RGBA', (width, height), tuple(round(255 * min(1, max(0, c))) for c in back)), *rect, clip)
        if p.get('background'):
            found = images(p['background'][0])
            image, mode = found if isinstance(found, tuple) else (found, 'blend')
            if image is not None:
                image = image.resize((width, height), Image.Resampling.BILINEAR)
                mx, my = numbers(p, 'matscalex', (1,))[0], numbers(p, 'matscaley', (1,))[0]
                if (w < 0) != (mx < 0): image = image.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
                if (h < 0) != (my < 0): image = image.transpose(Image.Transpose.FLIP_TOP_BOTTOM)
                tint = numbers(p, 'matcolor', (1, 1, 1, 1))
                bands = [band.point(lambda v, k=k: round(v * min(1, max(0, k)))) for band, k in zip(image.split(), tint)]
                composite(Image.merge('RGBA', bands), *rect, clip, mode)
        text = p.get('text', [''])[0] if p.get('text') else ''
        text = strings.get(text, text)
        if text and '::' not in text:
            fore = numbers(p, 'forecolor', (1, 1, 1, 1)); scale = numbers(p, 'textscale', (.35,))[0]
            font = ImageFont.load_default(size=max(6, round(scale * 48 * sy)))
            layer = Image.new('RGBA', (width, height), (0, 0, 0, 0)); d = ImageDraw.Draw(layer)
            align = int(numbers(p, 'textalign', (0,))[0]); tw = d.textlength(text, font=font)
            d.text((max(0, (width - tw) / 2) if align == 1 else max(0, width - tw) if align == 2 else 0, 0), text, font=font,
                   fill=tuple(round(255 * min(1, max(0, c))) for c in fore))
            composite(layer, *rect, clip)
        border = numbers(p, 'bordersize', (0,))[0]
        if border > 0:
            layer = Image.new('RGBA', (width, height), (0, 0, 0, 0))
            ImageDraw.Draw(layer).rectangle((0, 0, width - 1, height - 1), outline=tuple(round(255 * min(1, max(0, c))) for c in numbers(p, 'bordercolor', (1, 1, 1, 1))), width=max(1, round(border * sx)))
            composite(layer, *rect, clip)
        inner = clip if numbers(p, 'noclip', (0,))[0] else (max(clip[0], int(rect[0])), max(clip[1], int(rect[1])), min(clip[2], int(rect[0]) + width), min(clip[3], int(rect[1]) + height))
        for child in node['children']: draw(child, ox + x, oy + y, inner)
    draw(root, 0, 0, (0, 0, size[0], size[1]))
    return Image.fromarray(np.round(canvas * 255).astype(np.uint8), 'RGBA')


def gui_texture(path, files, images, strings, output):
    """Rasterise one .gui into the game's texture folder; a texture record like Textures.resolve returns."""
    try:
        from tools.doom3_assets import texture_filename
        from tools.texture_storage import write_texture
    except ImportError:
        from doom3_assets import texture_filename
        from texture_storage import write_texture
    path = path_name(path); record = dict(expression=path, status='failed')
    if path not in files: record['reason'] = 'Missing gui: ' + path; return record
    try:
        image = render_gui(parse_gui(files[path].read().decode('latin1')), images, strings)
        filename = texture_filename('guiraster/' + path.removesuffix('.gui') + '.png', files[path].campaign)
        write_texture(output, filename, image)
        record.update(status='ready', texture=filename, source=path, encoding='RGBA', precompressed=False)
    except (ValueError, OSError, IndexError) as exc: record['reason'] = str(exc)
    return record
