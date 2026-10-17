// Skinner Sky: the whole look in one function, the same text for DTS Skinner's T1 Maps page (WebGL GLSL, which
// static/t1-maps/skies.js wraps) and Unity (HLSL, which SkinnerSky.shader wraps). Written in what both languages
// accept: SkinnerSky.shader maps vec2/vec3, mix, fract and texture2D to HLSL; no one-number vector constructors.
// texture2D flips v there, since Unity's v = 0 is the image's bottom row and the page's is its top.
// Directions are z up, x east, y north. Inputs are the preset's keys as uniforms of the same names
// (docs/skinner-sky.md), the noise texture skyNoise, and uTime in seconds. Returns linear light before exposure.
#define PLANET 6360.
#define TOP 6420.
#define H vec2(8., 1.2)
#define BETA_R (vec3(5.802e-3, 13.558e-3, 33.1e-3) * rayleigh)
#define BETA_O (vec3(.65e-3, 1.881e-3, .085e-3) * ozone * 1.5)
#define BETA_M (3.996e-3 * mie)
#define LUMA vec3(.2126, .7152, .0722)

vec3 toward(float azimuth, float elevation) {
  float a = radians(azimuth), e = radians(elevation);
  return vec3(sin(a) * cos(e), cos(a) * cos(e), sin(e));
}
// Air from radius r (km) to space toward cosine-of-zenith c, in scale heights: a shell's chord, close to Chapman's function.
vec2 column(float r, float c) { vec2 x = r / H; return sqrt(x * x * c * c + 2. * x + 1.) - x * c; }
// Light reaching radius r from a light at cosine c: through the air above (ozone takes orange out of a low sun, which
// keeps the zenith blue at dusk), and 0 in the planet's shadow.
vec3 toLight(float r, float c) {
  vec2 depth = exp(-(r - PLANET) / H) * H * column(r, c);
  return exp(-((BETA_R + BETA_O) * depth.x + BETA_M * 1.11 * depth.y)) * smoothstep(-.004, .004, c + sqrt(max(1. - PLANET * PLANET / (r * r), 0.)));
}
float phaseR(float mu) { return .0596831 * (1. + mu * mu); }
float phaseM(float mu, float g) { return .0795775 * (1. - g * g) / pow(1. + g * g - 2. * g * mu, 1.5); }
vec3 hash33(vec3 p) { p = fract(p * vec3(.1031, .1030, .0973)); p += dot(p, p.yxz + 33.33); return fract((p.xxy + p.yxx) * p.zyx); }
float vnoise(vec3 p) {
  vec3 i = floor(p), f = fract(p);
  f = f * f * (3. - 2. * f);
  return mix(mix(mix(hash33(i).x, hash33(i + vec3(1., 0., 0.)).x, f.x), mix(hash33(i + vec3(0., 1., 0.)).x, hash33(i + vec3(1., 1., 0.)).x, f.x), f.y),
             mix(mix(hash33(i + vec3(0., 0., 1.)).x, hash33(i + vec3(1., 0., 1.)).x, f.x), mix(hash33(i + vec3(0., 1., 1.)).x, hash33(i + 1.).x, f.x), f.y), f.z);
}
// Distance (km) from the ground to a shell at altitude km, along cosine c.
float distanceTo(float altitude, float c) { float b = PLANET * c, r = PLANET + altitude; return -b + sqrt(b * b + r * r - PLANET * PLANET); }
vec3 aces(vec3 x) { return clamp(x * (2.51 * x + .03) / (x * (2.43 * x + .59) + .14), 0., 1.); }

// Lightning in the clouds. A driver times the flashes, lightningRate a minute (skies.js lightning,
// SkinnerVolumetrics.cs), and sets two vectors that are not preset keys: lightningDir.xyz toward the flash, .w how
// tightly its glow gathers round that direction (a far flash is small), and lightningColor.rgb its light now (0 between
// flashes), times lightningIntensity. What it adds along d, where a is how much of what lies behind the clouds shows: cloud lit from inside, the
// light leaving through half its depth (Beer: a thick core glows less than its edges) and scaled by shape (0-1: the
// cloud's own structure there, where the caller has it), and faint air round it.
// Added per pixel after the sky texture, never into it: the texture is rewritten a band of rows a frame.
vec3 skinnerFlash(vec3 d, float a, float shape) {
  if (lightningRate <= 0.) return vec3(0., 0., 0.);
  return lightningColor.rgb * lightningIntensity * exp(lightningDir.w * (dot(d, lightningDir.xyz) - 1.)) * ((1. - a) * sqrt(max(a, .02)) * shape + .04);
}

// What lies behind the air, for d above the horizon: stars, the Milky Way glow, the moon, aurora and the sun's disc,
// dimmed by the air's transmittance. air is the sky's light there: how dark it is fades stars and aurora in daylight.
// Split out so a sky drawn from a texture (static/volumetrics) can add these sharp parts per pixel.
vec3 skinnerBehind(vec3 d, vec3 air, vec3 transmit) {
  vec3 sun = toward(sunAzimuth, sunElevation), moon = toward(moonAzimuth, moonElevation);
  float nu = dot(d, moon), night = exp(-dot(air, LUMA) * exposure * 25.);
  // One star per cell of a grid on the view direction (more along the Milky Way), its glow, or the moon.
  vec3 far = vec3(0., 0., 0.), sky = far;
  float across = dot(d, normalize(vec3(.35, -.6, .72))) / .22, band = exp(-across * across);
  if (starIntensity > 0.) {
    vec3 q = d * starDensity, cell = floor(q), h = hash33(cell);
    float spot = length(q - cell - .15 - .7 * h) / max(1.5 * length(fwidth(q)), 1e-4);
    float shine = pow(h.x, mix(150., 50., band * min(milkyWay, 1.))) * (.7 + .3 * sin(uTime * (2. + 4. * h.z) + 40. * h.y));
    far += mix(vec3(1., .72, .5), vec3(.62, .75, 1.), h.y) * shine * clamp(1. - spot, 0., 1.) * starIntensity;
  }
  if (milkyWay > 0.) far += vec3(.75, .8, 1.) * band * milkyWay * .02 * (.25 + .5 * vnoise(d * 7.) + .5 * vnoise(d * 19.)) * (.5 + .5 * band);
  #ifdef SKINNER_SPACE
  if (spaceIntensity > 0.) far += SAMPLE_PLAIN(spaceMap, spaceUV(d)).rgb * spaceIntensity;  // SkinnerSpace.cginc's baked backdrop.
  #endif
  // Faded out over the last 3 degrees above the horizon: with no air to hide it (Deep space), the field would otherwise
  // end in a ruler-straight line wherever the world stops short of the horizon.
  far *= night * smoothstep(0., .05, d.z);
  float moonR = radians(moonSize * .5);
  if (nu > cos(moonR) && moonIntensity > 0.) {  // A ball lit by the sun: its phase follows the two directions.
    vec3 right = normalize(cross(moon, vec3(0., 0., 1.))), up = cross(right, moon);
    vec2 q = vec2(dot(d, right), dot(d, up)) / sin(moonR);
    vec3 n = q.x * right + q.y * up - sqrt(max(1. - dot(q, q), 0.)) * moon;
    float albedo = mix(.5, 1., smoothstep(.3, .7, texture2D(skyNoise, q * .3 + .5).r));
    far = mix(far, moonColor * moonIntensity * 4. * albedo * (max(dot(n, sun), 0.) + .015), 1. - smoothstep(.96, 1., dot(q, q)));
  }
  sky += far * transmit;
  if (auroraIntensity > 0.) {  // Curtains: one level line of the cloud shapes, seen at four heights.
    vec3 glow = vec3(0., 0., 0.);
    UNROLL for (int k = 0; k < 4; k++) {
      vec2 uv = d.xy * distanceTo(100. + 25. * float(k), d.z) / 2500. + vec2(uTime * .0004, 0.);
      glow += mix(auroraColor, auroraColor2, float(k) / 3.) * pow(max(1. - abs(texture2D(skyNoise, uv).r - .5) * 14., 0.), 2.) * (1. - float(k) * .2);
    }
    sky += glow * auroraIntensity * .01 * smoothstep(0., .15, d.z) * sqrt(night);
  }
  float sunR = radians(sunSize * .5), x = acos(clamp(dot(d, sun), -1., 1.)) / sunR;
  if (x < 1.1) sky += sunColor * sunIntensity * transmit * 40. * (1. - .6 * (1. - sqrt(max(1. - x * x, 0.)))) * (1. - smoothstep(.95, 1.05, x));
  return sky;
}

// Cirrus, thin ice cirrusHeight km up, laid over sky (what lies behind it) by cover (1, or the share the clouds in front let
// through); air is the sky's light there. Also split out, and drawn per pixel where the air comes from a texture:
// overhead its fibres are finer than a sky texture's texels.
vec3 skinnerCirrus(vec3 d, vec3 sky, vec3 air, float cover) {
  float fade = smoothstep(-.01, .08, d.z);
  if (cirrusCoverage <= 0. || fade <= 0.) return sky;
  vec3 sun = toward(sunAzimuth, sunElevation), moon = toward(moonAzimuth, moonElevation);
  vec3 sunLight = sunColor * sunIntensity, moonLight = moonColor * moonIntensity * (.5 - .5 * dot(sun, moon));
  float t = distanceTo(cirrusHeight, d.z);
  vec2 uv = d.xy * t / cirrusScale + fract(cirrusSpeed.xy * uTime / cirrusScale), p = d.xy * t + cirrusSpeed.xy * uTime;
  // Streaks along x: where the ice is, from two reads stretched 3:1 (all a distant streak shows).
  float streak = texture2D(skyNoise, vec2(uv.x * .5, uv.y * 1.5)).r * .6 + texture2D(skyNoise, vec2(uv.x * 1.3, uv.y * 4.)).b * .4;
  // Their fibres: the fine noise stretched 30:1 along the wind is long parallel hairs, unevenly spaced, from a few km
  // apart down to tens of metres. Two sizes (cirrusFibreScale km across a tile, and a sixth of it), turned a little
  // against each other and bent together by one coarse read. Far off, mipmapping averages them to the noise's mean
  // (.5), so distant streaks keep their brightness.
  float bend = (texture2D(skyNoise, p * vec2(.002, .006) + .27).r - .5) * 4., fibres = 0.;
  UNROLL for (int k = 0; k < 2; k++) {
    float size = cirrusFibreScale / pow(6., float(k)), turn = .1 * float(k) - .05;
    vec2 q = vec2(cos(turn) * p.x + sin(turn) * p.y, cos(turn) * p.y - sin(turn) * p.x + bend);
    fibres += texture2D(skyNoise, vec2(q.x / (size * 30.), q.y / size) + float(k) * .37).b;
  }
  // Optical thickness: the fibres fray the streaks' edges, thin ice lets the sky through and strand cores hardly.
  // Thick cirrus a step toward the sun shades what lies behind it, so streaks have a lit side and a dim one.
  float wisp = smoothstep(1. - cirrusCoverage, 1.3 - cirrusCoverage, streak + (fibres - 1.) * .4 * cirrusFibres);
  float alpha = 1. - exp(-wisp * max(1. + (fibres * fibres - 1.) * cirrusFibres, 0.) * .5 * cirrusOpacity);
  float shadow = smoothstep(1. - cirrusCoverage, 1.3 - cirrusCoverage, texture2D(skyNoise, vec2(uv.x * .5, uv.y * 1.5) + sun.xy * .03).r * 1.1);
  vec3 light = (sunLight * toLight(PLANET + cirrusHeight, sun.z) * (.06 + .5 * phaseM(dot(d, sun), .7)) * (1. - .5 * shadow)
    + moonLight * toLight(PLANET + cirrusHeight, moon.z) * .3 + air) * cloudColor;
  return mix(sky, mix(light, air, 1. - exp(-t / 300.)), alpha * fade * cover);
}

vec3 skinnerSky(vec3 d) {
  vec3 sun = toward(sunAzimuth, sunElevation), moon = toward(moonAzimuth, moonElevation);
  vec3 sunLight = sunColor * sunIntensity, moonLight = moonColor * moonIntensity * (.5 - .5 * dot(sun, moon));
  // Single scattering along the view ray, 8 steps closer together near the eye, each lit through the air above it.
  // Below the horizon the ray stays level (the world hides the ground there; what shows is haze over the ground colour).
  vec3 e = normalize(vec3(d.x, d.y, max(d.z, 0.)) + vec3(0., 0., 1e-4));
  float r0 = PLANET + .002, b = r0 * e.z, end = -b + sqrt(b * b - r0 * r0 + TOP * TOP);
  vec2 depth = vec2(0., 0.);
  vec3 rs = vec3(0., 0., 0.), ms = rs, rm = rs, mm = rs;
  for (int i = 0; i < 8; i++) {
    float a = float(i) / 8., c = float(i + 1) / 8., t = end * (a + c) * (a + c) * .25;
    vec3 p = vec3(0., 0., r0) + e * t;
    float r = length(p);
    vec2 density = exp(-(r - PLANET) / H) * end * (c * c - a * a);
    depth += density * .5;
    vec3 seen = exp(-((BETA_R + BETA_O) * depth.x + BETA_M * 1.11 * depth.y)), ts = toLight(r, dot(p, sun) / r) * seen, tm = toLight(r, dot(p, moon) / r) * seen;
    rs += density.x * ts; ms += density.y * ts; rm += density.x * tm; mm += density.y * tm;
    depth += density * .5;
  }
  float mu = dot(e, sun), nu = dot(e, moon);
  vec3 transmit = exp(-((BETA_R + BETA_O) * depth.x + BETA_M * 1.11 * depth.y));
  vec3 air = sunLight * (BETA_R * phaseR(mu) * rs + BETA_M * phaseM(mu, mieG) * ms) + moonLight * (BETA_R * phaseR(nu) * rm + BETA_M * phaseM(nu, mieG) * mm)
    + nightColor * (1. + 2. * exp(-e.z * 8.));
  vec3 lowered = groundColor * .318 * (sunLight * toLight(PLANET, sun.z) * max(sun.z, 0.) + moonLight * toLight(PLANET, moon.z) * max(moon.z, 0.) + air);
  air = mix(air, lowered, smoothstep(0., .3, -d.z) * .9);
  vec3 sky = air;
  #ifndef SKINNER_SKIP_BEHIND
  if (d.z > 0.) sky += skinnerBehind(d, air, transmit);
  sky = skinnerCirrus(d, sky, air, 1.);
  #endif
  float fade = smoothstep(-.01, .08, d.z), cover = 0.;
  if (cloudCoverage > 0. && fade > 0.) {  // A layer of heaped clouds, shaded by a second look toward the sun.
    float t = distanceTo(cloudHeight, d.z), edge = 1. - cloudCoverage;
    vec2 uv = d.xy * t / cloudScale + fract(cloudSpeed.xy * uTime / cloudScale);
    // Shapes, bumps on their edges, finer wisps that fray them, and the shapes again a step toward the sun.
    float billow = (texture2D(skyNoise, uv * 2.1 + .37).g - .5) * .22 - texture2D(skyNoise, uv * 7.3 + .61).b * .06;
    float thick = max(texture2D(skyNoise, uv).r + billow - edge, 0.) / cloudSoftness, density = min(thick, 1.);
    float shade = max(texture2D(skyNoise, uv + sun.xy * .03).r + billow - edge, 0.) / cloudSoftness;
    // Seen from below: light comes through the cloud (Beer on its thickness here and toward the sun), so cores are
    // dark and edges bright, brighter on the side facing the sun and in a silver lining round it; skylight fills in.
    float through = exp(-cloudDarkness * 1.2 * (thick + shade)), side = clamp(.5 + 2. * (density - min(shade, 1.)), 0., 1.);
    vec3 sunAt = sunLight * toLight(PLANET + cloudHeight, sun.z), moonAt = moonLight * toLight(PLANET + cloudHeight, moon.z);
    vec3 light = ((sunAt + moonAt * .1) * (.06 * mix(through, side, .4) + phaseM(mu, .6) * through)
      + mix(vec3(1., 1., 1.) * dot(air, LUMA), air, .6) * (.3 + 1.4 * exp(-.6 * cloudDarkness * thick))) * cloudColor;
    cover = density * cloudOpacity * fade;
    sky = mix(sky, mix(light, air, 1. - exp(-t / 120.)), cover);
  }
  #ifndef SKINNER_SKIP_BEHIND
  sky += skinnerFlash(d, 1. - cover, 1.);
  #endif
  return sky;
}
