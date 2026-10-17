// Height fog for Skinner Sky: exponential in height, integrated along the view ray in closed form (no march), lit by
// the sky's own horizon colour in that direction (the "ring", which already holds the sun's glow and the clouds).
// Same GLSL/HLSL subset as SkinnerSky.cginc. Scene units are metres; z up.
// fogDensity: extinction per metre at fogBase (m); fogHeight: metres over which it thins by e.

// How much of what lies dist metres along rd from the eye at height eyeZ still shows.
float fogThrough(float eyeZ, vec3 rd, float dist) {
  float x = rd.z * dist / fogHeight;
  float f = abs(x) < 1e-3 ? 1. - .5 * x : (1. - exp(-x)) / x;
  return exp(-fogDensity * exp(-(eyeZ - fogBase) / fogHeight) * dist * f);
}

// The scene's colour c seen through the fog, with the horizon colour ring (linear light) in the ray's azimuth.
vec3 skinnerFog(vec3 c, float eyeZ, vec3 rd, float dist, vec3 ring) {
  float through = fogThrough(eyeZ, rd, dist);
  return c * through + ring * fogBrightness * (1. - through);
}
