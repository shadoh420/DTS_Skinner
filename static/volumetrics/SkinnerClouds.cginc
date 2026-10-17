// Volumetric clouds for Skinner Sky, marched into a small sky texture (the "pano") instead of per screen pixel, so the
// cost is fixed by the pano's size, not the screen's. The pano holds the air too (skinnerSky with
// SKINNER_SKIP_BEHIND); the screen pass adds skinnerBehind's sharp parts (sun disc, stars, moon) per pixel. Include after SkinnerSky.cginc. Same GLSL/HLSL subset; the
// wrapper also defines SAMPLE_LOD(sampler, uv, lod) and, for HLSL, atan(y, x) as atan2.
// Uses the preset's cloud keys plus cloudThickness (km). The camera is at the planet's surface: clouds are kilometres
// up, so they do not move with the player.
#ifndef CLOUD_STEPS
#define CLOUD_STEPS 24
#endif

// Pano layout: u = azimuth from north toward east, v = elevation from 15 degrees below the horizon (where the haze over
// the ground has settled) to the zenith, both linear in angle.
vec3 panoDir(vec2 uv) { float az = uv.x * 6.2831853, el = uv.y * 1.8325957 - .2617994; return vec3(sin(az) * cos(el), cos(az) * cos(el), sin(el)); }
vec2 panoUV(vec3 d) { return vec2(atan(d.x, d.y) * .1591549 + 1., (asin(clamp(d.z, -1., 1.)) + .2617994) * .5456740); }

// Density at noise coordinate uv and height h (0 at the slab's base, 1 at its top): the layer clouds' shapes,
// narrowing upward with rounded bases, with billows and fraying read at points that move with height. (The noise is
// 2D: without that, an edge would be a wall the slab's height, and walls seen from below all point at the zenith.)
// cloudSpeed.z (km/s) slides the billows and fraying across the shapes, so the clouds churn as they drift.
float cloudDensity(vec2 uv, float h, float lod, bool detail) {
  vec2 churn = fract(vec2(1., -.7) * cloudSpeed.z * uTime / cloudScale);
  float base = SAMPLE_LOD(skyNoise, uv, lod).r + (SAMPLE_LOD(skyNoise, uv * 2.1 + vec2(h * .9, -h * .6) + churn, lod).g - .5) * .3
    - (1. - cloudCoverage) - .4 * h;
  if (detail && base > 0.) base -= SAMPLE_LOD(skyNoise, uv * 5.3 + vec2(h * 1.7, h * 1.1) - churn * 1.6, lod).b * .14;
  return max(base, 0.) * smoothstep(0., .1, h) * (1. - smoothstep(.6, 1., h)) / cloudSoftness;
}

// Light the clouds add along direction d (z up), and how much of what is behind them shows through (a). air is the
// sky behind them: their skylight, and the haze they fade into with distance.
vec4 skinnerClouds(vec3 d, vec3 air) {
  if (d.z < .005 || cloudCoverage <= 0.) return vec4(0., 0., 0., 1.);
  vec3 sun = toward(sunAzimuth, sunElevation), moon = toward(moonAzimuth, moonElevation);
  float mid = cloudHeight + cloudThickness * .5, mu = dot(d, sun);
  vec3 sunAt = sunColor * sunIntensity * toLight(PLANET + mid, sun.z) + moonColor * moonIntensity * (.5 - .5 * dot(sun, moon)) * toLight(PLANET + mid, moon.z) * .1;
  vec3 sky = mix(vec3(1., 1., 1.) * dot(air, LUMA), air, .6);
  float phase = mix(phaseM(mu, .75), phaseM(mu, -.15), .5), extinction = 30. * cloudDarkness;
  // The slab between the layer's base and top, at most 25 km of it (near the horizon the path is long and hazy).
  float t0 = distanceTo(cloudHeight, d.z), t1 = min(distanceTo(cloudHeight + cloudThickness, d.z), t0 + 25.), dt = (t1 - t0) / float(CLOUD_STEPS);
  // A coarser noise level further away, so the far clouds do not sparkle (a pano texel is about 1/3 degree).
  float lod = clamp(log2(t0 * .006 * 512. / cloudScale), 0., 6.);
  vec2 wind = fract(cloudSpeed.xy * uTime / cloudScale);
  vec3 light = vec3(0., 0., 0.);
  float through = 1., seen = 0., weight = 0.;
  for (int i = 0; i < CLOUD_STEPS; i++) {
    float t = t0 + (float(i) + .5) * dt;
    vec3 p = d * t;
    float h = (length(p + vec3(0., 0., PLANET)) - PLANET - cloudHeight) / cloudThickness;
    vec2 uv = p.xy / cloudScale + wind;
    float s = cloudDensity(uv, h, lod, true);
    if (s > 0.) {
      // Two looks toward the sun give the light's path through the cloud (Beer), so cores and shaded sides go dark.
      float toSun = cloudDensity(uv + sun.xy * (.12 / cloudScale), h + sun.z * .12 / cloudThickness, lod, false) * .12
        + cloudDensity(uv + sun.xy * (.45 / cloudScale), h + sun.z * .45 / cloudThickness, lod + 1., false) * .33;
      vec3 lit = sunAt * phase * exp(-extinction * toSun) + sky * (.35 + .9 * h);
      float a = exp(-extinction * s * dt), taken = through * (1. - a);
      light += taken * lit;  // Energy-conserving step: what scatters here, times what still reaches the eye.
      seen += taken * t;
      weight += taken;
      through *= a;
      if (through < .02) break;
    }
  }
  light = mix(light, air * (1. - through), 1. - exp(-seen / max(weight, 1e-4) / 120.));
  through = mix(1., through, cloudOpacity * smoothstep(.005, .06, d.z));
  return vec4(light * cloudColor * cloudOpacity * smoothstep(.005, .06, d.z), through);
}

// skinnerFlash over the sky texture's clouds, per pixel (the texture itself never holds a flash): their density a third
// of the way up the slab gives the glow the clouds' shapes, where the texture's alpha alone is flat over a thick cloud
// and the glow a smooth blob. Two noise reads at one level, no march, and none between flashes.
vec3 skinnerCloudsFlash(vec3 d, float a) {
  float shape = 1.;
  if (lightningColor.r > 0. && d.z > .005) {
    float t = distanceTo(cloudHeight + cloudThickness * .3, d.z);
    shape = smoothstep(.1, 1., cloudDensity(d.xy * t / cloudScale + fract(cloudSpeed.xy * uTime / cloudScale), .3, clamp(log2(t * .006 * 512. / cloudScale), 0., 6.), false));
  }
  return skinnerFlash(d, a, shape);
}
