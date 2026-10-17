// Height fog for Skinner Sky: exponential in height, integrated along the view ray in closed form (no march), lit by
// the sky's own horizon colour in that direction (the "ring", which already holds the sun's glow and the clouds).
// Same GLSL/HLSL subset as SkinnerSky.cginc. Scene units are metres; z up.
// fogDensity: extinction per metre at fogBase (m); fogHeight: metres over which it thins by e.
// fogPatches (0-1): how patchy it is; fogScale: metres a patch tile; fogWind: the patches' drift (m/s east, north) and,
// in z, how fast the swirl that bends them turns (degrees a second).

// How much of what lies dist metres along rd from the eye at height eyeZ still shows.
float fogThrough(float eyeZ, vec3 rd, float dist) {
  if (fogDensity <= 0.) return 1.;  // Else a sky ray downward overflows f to infinity, and 0 x infinity is NaN (black).
  float x = rd.z * dist / fogHeight;
  float f = abs(x) < 1e-3 ? 1. - .5 * x : (1. - exp(-x)) / x;
  return exp(-fogDensity * exp(-(eyeZ - fogBase) / fogHeight) * dist * f);
}

// A lightning flash (SkinnerSky.cginc skinnerFlash) seen in the fog: the fog's light ring brightened by the flash's
// light averaged over the sky (about a quarter of the sky is lit cloud), and in a the scale on the world's colour for
// the flash's light on it, half the flash's share of the light round it (the world's own shading knows nothing of it).
vec4 skinnerFlashFog(vec3 ring) {
  float k = max(lightningDir.w, .01);
  vec3 add = lightningColor.rgb * lightningIntensity * (1. - exp(-2. * k)) / (2. * k) * .25;
  return vec4(ring + add, 1. + .5 * dot(add, LUMA) / max(dot(ring, LUMA), 1e-4));
}

// The patches: what the fog's density along the ray is multiplied by, 1 on average. A noise in the ground plane drifts
// with the wind, bent by a second, slower noise whose push turns with time (the swirl); read at three points along the
// part of the ray the fog fills, each weighted by the fog's density at its height. Coarse noise levels (blurred), so a
// tile holds a few broad banks rather than mottling.
float fogPatch(vec3 eye, vec3 rd, float dist) {
  if (fogPatches <= 0.) return 1.;
  float reach = min(min(dist, 3000.), (fogHeight * 3. + max(eye.z - fogBase, 0.)) / max(abs(rd.z), .01)), turn = radians(fogWind.z) * uTime;
  vec2 drift = fract(fogWind.xy * uTime / fogScale), spin = vec2(cos(turn), sin(turn));
  float n = 0., weight = 1e-4;
  for (int i = 0; i < 3; i++) {
    vec3 p = eye + rd * (reach * (float(i) + .5) / 3.);
    vec2 uv = p.xy / fogScale, bend = SAMPLE_LOD(skyNoise, uv * .37 - drift * .5, 4.).gb - .5;
    float w = exp(-max(p.z - fogBase, 0.) / fogHeight);
    n += w * SAMPLE_LOD(skyNoise, uv + drift + vec2(bend.x * spin.x - bend.y * spin.y, bend.x * spin.y + bend.y * spin.x) * .8, 3.).r;
    weight += w;
  }
  return max(1. + fogPatches * (n / weight - .5) * 6., 0.);
}

// fogThrough with the patches, from the eye at eye (metres, z up).
float fogThroughAt(vec3 eye, vec3 rd, float dist) {
  float through = fogThrough(eye.z, rd, dist);
  return through > 0. ? pow(through, fogPatch(eye, rd, dist)) : 0.;
}

// The scene's colour c seen through the fog, with the horizon colour ring (linear light) in the ray's azimuth.
vec3 skinnerFog(vec3 c, vec3 eye, vec3 rd, float dist, vec3 ring) {
  float through = fogThroughAt(eye, rd, dist);
  return c * through + ring * fogBrightness * (1. - through);
}
