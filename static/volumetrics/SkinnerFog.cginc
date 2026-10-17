// Height fog for Skinner Sky: exponential in height, integrated along the view ray in closed form (no march), lit by
// the sky's own horizon colour in that direction (the "ring", which already holds the sun's glow and the clouds).
// Same GLSL/HLSL subset as SkinnerSky.cginc. Scene units are metres; z up.
// fogDensity: extinction per metre at fogBase (m); fogHeight: metres over which it thins by e.

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

// The scene's colour c seen through the fog, with the horizon colour ring (linear light) in the ray's azimuth.
vec3 skinnerFog(vec3 c, float eyeZ, vec3 rd, float dist, vec3 ring) {
  float through = fogThrough(eyeZ, rd, dist);
  return c * through + ring * fogBrightness * (1. - through);
}
