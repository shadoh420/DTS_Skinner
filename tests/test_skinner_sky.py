"""Skinner Sky (static/skinner-sky, docs/skinner-sky.md): the page's defaults (skies.js skinnerDefaults and
volumetricDefaults), the Unity shaders' properties and HLSL uniforms, and the presets must name the same keys, or one
side draws a different sky."""
import json
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parent.parent
SKY = ROOT / 'static' / 'skinner-sky'
VOLUMETRICS = ROOT / 'static' / 'volumetrics'


def properties(path):
    return {key: number(value) for key, value in
            re.findall(r'^\s*(?:\[\w+\]\s*)*(\w+) \("[^"]*", (?:Float|Vector)\) = (\([^)]*\)|-?[\d.]+)', path.read_text(encoding='utf-8'), re.M)}


def defaults(script, name):
    block = re.search(name + r': \{(.*?)\},\n', script, re.S).group(1)
    return {key: number(value) for key, value in re.findall(r'(\w+): (\[[^\]]*\]|-?[\d.]+)', block)}


def number(text):
    values = [float(value) for value in re.findall(r'-?[\d.]+', text)]
    return values[0] if len(values) == 1 else values[:3]


class SkinnerSkyTest(unittest.TestCase):
    def setUp(self):
        script = (ROOT / 'static' / 't1-maps' / 'skies.js').read_text(encoding='utf-8')
        self.defaults = defaults(script, 'skinnerDefaults')
        self.volumetric = defaults(script, 'volumetricDefaults')
        self.properties = properties(SKY / 'SkinnerSky.shader')
        shader = (SKY / 'SkinnerSky.shader').read_text(encoding='utf-8')
        program = re.search(r'CGPROGRAM(.*?)ENDCG', shader, re.S).group(1)
        self.uniforms = {name for line in re.findall(r'^\s*float3? ([\w ,\n]+);', program, re.M) for name in re.findall(r'\w+', line)}

    def test_web_defaults_are_the_unity_defaults(self):
        unity_only = {'fogColor', 'tonemap'}  # RenderSettings' fog, and the camera's own tone mapping.
        self.assertEqual({k: v for k, v in self.properties.items() if k not in unity_only}, self.defaults)
        self.assertEqual(self.uniforms, set(self.properties) - {'fogColor'})

    def test_volumetric_shaders_take_every_key(self):
        # The Tools menu makes Skinner/Volumetric Sky preset materials, and SkinnerVolumetrics.cs copies their values
        # to the work material property by property: both shaders declare the page's keys with the page's defaults.
        unity_only = {'fogColor', 'tonemap'}
        sky = properties(VOLUMETRICS / 'SkinnerVolumetricSky.shader')
        self.assertEqual(properties(VOLUMETRICS / 'SkinnerVolumetrics.shader'), sky)
        self.assertEqual({k: v for k, v in sky.items() if k not in unity_only}, {**self.defaults, **self.volumetric})

    def test_presets_use_known_keys(self):
        presets = json.loads((SKY / 'presets.json').read_text(encoding='utf-8'))
        known = {**self.defaults, **self.volumetric}
        self.assertEqual(len({preset['id'] for preset in presets}), len(presets))
        for preset in presets:
            self.assertLessEqual(set(preset) - {'id', 'name'}, set(self.properties) | set(self.volumetric), preset['id'])
            self.assertNotIn('fogBase', preset, preset['id'])  # The page measures it from the map's start.
            for key, value in preset.items():
                if key in known:  # A number where the shader declares a colour, or the reverse, is an authoring slip.
                    self.assertEqual(isinstance(value, list), isinstance(known[key], list), (preset['id'], key))
                if isinstance(value, list):
                    self.assertEqual(len(value), 3, (preset['id'], key))  # The Unity menu reads three-number lists only.

    def test_core_reads_only_declared_uniforms(self):
        core = (SKY / 'SkinnerSky.cginc').read_text(encoding='utf-8')
        for key in self.defaults:
            self.assertRegex(core, rf'\b{key}\b')
        for text in (core, (SKY / 'SkinnerSpace.cginc').read_text(encoding='utf-8')):
            self.assertNotRegex(text, r'\bvec[234]\(\s*[-\w.]+\s*\)')  # One-number constructors are GLSL only.


if __name__ == '__main__':
    unittest.main()
