// Skinner Sky's space backdrop: Star Nest's volumetric fractal, seen from one fixed point at one fixed moment, so it
// depends only on the view direction. It is too slow to run per screen pixel (20 steps x 17 iterations), so it is
// baked once per preset into a 2048x2048 texture over the whole sphere (spaceDir/spaceUV), and skinnerBehind
// reads that texture where spaceIntensity > 0. Same GLSL/HLSL subset as SkinnerSky.cginc; include it before that file.
// The wrapper declares the texture spaceMap and SAMPLE_PLAIN(sampler, uv), a read with no v flip (a render target's
// rows run the same way as its pixels on every API).
//
// Ported from "Star Nest" by Pablo Roman Andrioli (Shadertoy XlfGRj), released under the MIT licence:
//   Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated
//   documentation files (the "Software"), to deal in the Software without restriction, including without limitation
//   the rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the Software, and
//   to permit persons to whom the Software is furnished to do so, subject to the following conditions: The above
//   copyright notice and this permission notice shall be included in all copies or substantial portions of the
//   Software. THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT
//   LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT
//   SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION
//   OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER
//   DEALINGS IN THE SOFTWARE.
#define SKINNER_SPACE

// The whole sphere folded onto a square (octahedral): the upper half is the diamond in the middle, the lower half folds
// out to the corners. No poles, smooth across the horizon (an airless sky shows space below it wherever the world stops
// short), texels within 2x of even in size.
vec2 spaceFold(vec2 p) { return (1. - abs(p.yx)) * (step(0., p) * 2. - 1.); }
vec3 spaceDir(vec2 uv) { vec2 p = uv * 2. - 1.; float z = 1. - abs(p.x) - abs(p.y); if (z < 0.) p = spaceFold(p); return normalize(vec3(p, z)); }
vec2 spaceUV(vec3 d) { vec2 p = d.xy / (abs(d.x) + abs(d.y) + abs(d.z) + 1e-6); if (d.z < 0.) p = spaceFold(p); return p * .5 + .5; }

// The original's turn with the mouse at rest: xz by .5 radians, then xy by .8. GLSL's v * mat2(c, s, -s, c), written
// out because HLSL's float2x2 constructor is row-major. It keeps the original's view of the field.
vec3 starNestTurn(vec3 p) {
  float c1 = cos(.5), s1 = sin(.5), c2 = cos(.8), s2 = sin(.8);
  p = vec3(c1 * p.x + s1 * p.z, p.y, c1 * p.z - s1 * p.x);
  return vec3(c2 * p.x + s2 * p.y, c2 * p.y - s2 * p.x, p.z);
}

// Star Nest toward d (z up): linear light. Its camera is gone: d (Shadertoy's frame is y up) is the ray, and the eye is
// frozen where the original's is at iTime 0. GLSL's mod is floor-based and HLSL's fmod truncates, and the fold takes it
// of negative coordinates, so it is written out.
vec3 starNest(vec3 d) {
  const float tile = .85, formuparam = .53, brightness = .0015, darkmatter = .3, distfading = .73, saturation = .85;
  vec3 from = starNestTurn(vec3(1.5, .75, -1.5)), dir = starNestTurn(d.xzy);
  float s = .1, fade = 1.;
  vec3 v = vec3(0., 0., 0.);
  for (int r = 0; r < 20; r++) {
    vec3 p = from + s * dir * .5;
    p = abs(tile - (p - tile * 2. * floor(p / (tile * 2.))));  // Tiling fold.
    float pa = 0., a = 0.;
    for (int i = 0; i < 17; i++) {
      p = abs(p) / dot(p, p) - formuparam;  // The magic formula.
      a += abs(length(p) - pa);  // Absolute sum of average change.
      pa = length(p);
    }
    float dm = max(0., darkmatter - a * a * .001);  // Dark matter.
    a *= a * a;  // Contrast.
    if (r > 6) fade *= 1. - dm;  // Dark matter, not near.
    v += fade;
    v += vec3(s, s * s, s * s * s * s) * a * brightness * fade;  // Colour by distance.
    fade *= distfading;  // Distance fading.
    s += .1;
  }
  float l = length(v);
  v = mix(vec3(l, l, l), v, saturation);
  // The original writes v * .01 straight to the screen. Light that shows the same at exposure 1: undo the display's 2.2,
  // then the slope of ACES at black (.03 / .14), which would otherwise darken it about 4.7 times.
  return pow(max(v * .01, 0.), vec3(2.2, 2.2, 2.2)) * (.14 / .03);
}
