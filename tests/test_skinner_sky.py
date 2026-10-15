"""Skinner Sky (static/skinner-sky, docs/skinner-sky.md): the page's defaults (skies.js skinnerDefaults), the Unity
shader's properties and HLSL uniforms, and the presets must name the same keys, or one side draws a different sky."""
import json
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parent.parent
SKY = ROOT / 'static' / 'skinner-sky'


def number(text):
    values = [float(value) for value in re.findall(r'-?[\d.]+', text)]
    return values[0] if len(values) == 1 else values[:3]


class SkinnerSkyTest(unittest.TestCase):
    def setUp(self):
        script = (ROOT / 'static' / 't1-maps' / 'skies.js').read_text(encoding='utf-8')
        block = re.search(r'skinnerDefaults: \{(.*?)\},\n', script, re.S).group(1)
        self.defaults = {key: number(value) for key, value in re.findall(r'(\w+): (\[[^\]]*\]|-?[\d.]+)', block)}
        shader = (SKY / 'SkinnerSky.shader').read_text(encoding='utf-8')
        self.properties = {key: number(value) for key, value in
                           re.findall(r'^\s*(?:\[\w+\]\s*)*(\w+) \("[^"]*", (?:Float|Vector)\) = (\([^)]*\)|-?[\d.]+)', shader, re.M)}
        program = re.search(r'CGPROGRAM(.*?)ENDCG', shader, re.S).group(1)
        self.uniforms = {name for line in re.findall(r'^\s*float3? ([\w ,\n]+);', program, re.M) for name in re.findall(r'\w+', line)}

    def test_web_defaults_are_the_unity_defaults(self):
        unity_only = {'fogColor', 'tonemap'}  # RenderSettings' fog, and the camera's own tone mapping.
        self.assertEqual({k: v for k, v in self.properties.items() if k not in unity_only}, self.defaults)
        self.assertEqual(self.uniforms, set(self.properties) - {'fogColor'})

    def test_presets_use_known_keys(self):
        presets = json.loads((SKY / 'presets.json').read_text(encoding='utf-8'))
        self.assertEqual(len({preset['id'] for preset in presets}), len(presets))
        for preset in presets:
            self.assertLessEqual(set(preset) - {'id', 'name'}, set(self.properties), preset['id'])
            for key, value in preset.items():
                if isinstance(value, list):
                    self.assertEqual(len(value), 3, (preset['id'], key))  # The Unity menu reads three-number lists only.

    def test_core_reads_only_declared_uniforms(self):
        core = (SKY / 'SkinnerSky.cginc').read_text(encoding='utf-8')
        for key in self.defaults:
            self.assertRegex(core, rf'\b{key}\b')
        self.assertNotRegex(core, r'\bvec[23]\(\s*[-\d.]+\s*\)')  # One-number constructors are GLSL only.


if __name__ == '__main__':
    unittest.main()
