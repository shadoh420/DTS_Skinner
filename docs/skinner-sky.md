# Skinner Sky

Skinner's own sky format. It is made to look good, carry over to Unity unchanged and stay cheap there. A sky is a
preset of plain numbers, drawn by one shader on a sphere round the camera. The shader has no textures besides one
512² noise image, no precomputed tables, no render targets and no extra passes.

On the T1, Starsiege and Earthsiege Maps pages, the Sky list has a **Skinner Sky** group with thirteen presets: Clear
afternoon, Golden hour, Stormfront, Blue hour, Moonlit night, Aurora and Alien dusk, plus Crisp afternoon, Crisp
golden hour, Crisp moonlit night, Crisp alien dusk (sharper, darker-based clouds) and Fogbound storm (Stormfront in
thick low fog), and Deep space (no air, a sunlit planet, and the space backdrop below). Those pages draw them with volumetric clouds and height fog ([volumetrics.md](volumetrics.md)), and the
presets are tuned for that.

## Files (`static/skinner-sky`)

| File | What it is |
|---|---|
| `presets.json` | The presets: a list of flat objects of numbers and three-number lists, plus `id` and `name` |
| `SkinnerSky.cginc` | The whole look, `skinnerSky(direction)`. The page and Unity both compile this same file |
| `SkinnerSpace.cginc` | The space backdrop: Star Nest (MIT, Pablo Roman Andrioli), frozen to depend only on direction, and its hemi-octahedral texture layout |
| `SkinnerSkyNoise.png` | The noise, as linear data: R cloud shapes (Perlin-Worley), G billows, B fine fBm. `tools/make_sky_noise.py` writes it (seeded) |
| `SkinnerSky.shader` | Unity's skybox shader `Skinner/Sky`: properties, HLSL macros, then `#include "SkinnerSky.cginc"` |
| `SkinnerSkyPresets.cs` | Unity editor menu: one material per preset, and "use as sky" |

The page wraps the core in GLSL (`static/t1-maps/skies.js` `skinner`). Unity wraps it in HLSL. The core is written in
what both languages accept. On the HLSL side, `vec2`, `vec3`, `mix`, `fract` and `texture2D` are macros. One-number
vector constructors such as `vec3(0.)` are avoided, because HLSL rejects them. `tests/test_skinner_sky.py` checks that
the page's defaults, the Unity properties and HLSL uniforms, and the preset keys all match.

## Conventions

- Directions are z up, with x east and y north. An azimuth is in degrees from north toward east; an elevation is in
  degrees above the horizon. Unity's +Z is north and +X is east, and the shader swizzles its y-up direction.
- Colours are linear multipliers, not sRGB. The exception is `fogColor`, which is a display colour.
- Distances are in km and wind is in km/s.
- Output is exposure, then ACES (Narkowicz fit), then the display's 2.2, with half a step of dither.

## Keys (default)

| Key | Default | Meaning |
|---|---|---|
| `sunAzimuth`, `sunElevation` | 200, 35 | Where the sun is |
| `sunColor`, `sunIntensity` | (1, .98, .95), 20 | Sunlight above the air |
| `sunSize` | 1.2 | Disc diameter in degrees (the real sun is 0.53) |
| `moonAzimuth`, `moonElevation`, `moonSize` | 20, −30, 3.5 | The moon, or a planet when it is large. Its phase follows the sun's direction |
| `moonColor`, `moonIntensity` | (.9, .92, 1), 0 | Disc brightness, and the moonlight scattered by the air at night. 0 = no moon |
| `rayleigh` | (1, 1, 1) | Scale of Earth's air scattering per channel. Changing it changes the planet's sky colour |
| `ozone` | 1 | Ozone absorption: it keeps the zenith blue at dusk |
| `mie`, `mieG` | 1, .8 | Haze amount, and how strongly it scatters forward (the glow round the sun) |
| `groundColor` | (.25, .24, .22) | What shows below the horizon, under the haze |
| `nightColor` | (.0004, .0007, .0014) | Airglow: the sky's floor at night, brighter low down |
| `exposure` | 1.5 | Exposure. Night presets use 6 to 12 |
| `starIntensity`, `starDensity` | 0, 250 | Stars, one per cell of a grid of that many cells per radian. Twinkle, coloured |
| `milkyWay` | 0 | A band of glow, with denser stars along it |
| `cloudCoverage`, `cloudSoftness` | .5, .15 | Heaped clouds: how much of the sky, and how soft their edges |
| `cloudScale`, `cloudHeight`, `cloudSpeed` | 20, 2, (.02, .005, 0) | Noise tile size (km), layer height (km), wind (km/s) |
| `cloudColor`, `cloudDarkness`, `cloudOpacity` | (1, 1, 1), 1, 1 | Tint, how dark thick clouds get, overall opacity |
| `cirrusCoverage`, `cirrusScale`, `cirrusSpeed` | 0, 150, (.04, .01, 0) | Fibrous streaks; off unless the preset sets coverage. Streak tile (km), wind (km/s) |
| `cirrusHeight`, `cirrusFibres`, `cirrusFibreScale`, `cirrusOpacity` | 9, 1, 24, 1 | Layer height (km); fibre strength (0 smooth streaks, 2 stronger strands); fibre tile (km); optical thickness scale |
| `auroraIntensity`, `auroraColor`, `auroraColor2` | 0, (.1, 1, .4), (.6, .2, 1) | Curtains 100 to 175 km up, low colour to high colour |
| `fogColor` | (.9, .92, .93) | Unity only: `RenderSettings.fogColor`. The page measures its own haze |

The volumetric clouds and the height fog take five more keys (`skies.js` `volumetricDefaults`, and the properties of
Unity's `Skinner/Volumetric Sky` and `Hidden/Skinner Volumetrics`, which `Skinner/Sky` does not declare):
`cloudThickness` (1.5 km, the slab above `cloudHeight`), `fogDensity` (.0012 per metre at the fog's base), `fogHeight`
(40 m to thin by e), `fogBrightness` (1) and `fogBase` (0, counted from the map's start; presets leave it out).
A sixth, `spaceIntensity` (0), adds the space backdrop below.

## Space backdrop

`spaceIntensity` above 0 puts a field of stars and nebulae behind the stars, moon and sun: Star Nest, a volumetric
fractal from Shadertoy (MIT; the notice is in `SkinnerSpace.cginc`). It fades in daylight and dims through the air,
as the stars do. Deep space uses it at 1, with no air (`rayleigh`, `ozone`, `mie` and `nightColor` at 0) and no fog.
- Star Nest marches 20 steps of 17 iterations per pixel, too slow for every frame. Its eye and clock are frozen, so it
  depends only on the view direction, and it is baked once per preset into a 2048 × 2048 half-float texture (32 MB).
  The texture covers the upper hemisphere folded onto a square (hemi-octahedral): no poles and no seam, 0.04–0.09° a
  texel. Below the horizon the backdrop is not drawn.
- Colour: the original writes `v * .01` to the screen. The bake stores that decoded with 2.2, times 4.7, the inverse
  of ACES's slope at black, so it shows about as on Shadertoy at exposure 1 and `spaceIntensity` 1.
- The page bakes it on the first `update()` (`skies.js` `bakeSpace`). Unity bakes it in `Hidden/Skinner Volumetrics`
  pass 3 when `SkinnerVolumetrics.cs` sees a new sky with the key above 0, into the global `spaceMap`.
- `Skinner/Sky` has no bake, so it does not draw the backdrop.

**Cost** (Unity 6000.3.11f1, URP, D3D11, RX 7800 XT, 2560 × 1440, VolCheck's method: n draws in one command buffer,
median of 5, three runs): the bake costs 2.0 ms (1.99–2.05), once per preset. The screen sky costs 0.109–0.117 ms with the
backdrop and 0.106–0.112 ms with `spaceIntensity` 0, so +0.003 to +0.008 ms a frame (one texture read). The page's cost was not timed:
this session's browser pane was hidden, and hidden-pane timings are not usable ([volumetrics.md](volumetrics.md)).

In the volumetric clouds two of the keys above work differently from the layer: `cloudSoftness` divides the density
(higher is thinner, so you see into the clouds), and `cloudDarkness` sets the extinction (30 per km × darkness). At
the old values (.15 and 1, Stormfront's 3) a ray goes opaque in one or two of the 24 steps, so cloud bases are flat
slabs of one colour. The presets use .4 and .3 (the Crisp ones .25 and .6), and Stormfront takes its darkness from
`cloudColor` instead.

## How it draws

1. **Air**: single scattering of sun and moon light (Rayleigh, Mie and ozone, Earth's coefficients), along the view
   ray in 8 steps spaced closer together near the eye. Each step is lit through the air above it, using a shell-chord
   approximation of Chapman's function and the planet's shadow. This gives a blue day, orange sunsets with a blue
   zenith, the pink band and Earth's shadow opposite a low sun, and a blue moonlit night, all from the sun's position.
   Below the horizon, the ray is held level and blended toward the lit ground colour.
2. **Behind the air**: stars, the Milky Way glow, and the moon or planet, dimmed by the air's transmittance. Stars and
   aurora fade as the sky gets brighter. The sun disc has limb darkening.
3. **Cirrus**, then **clouds**, as layers on shells at their height (so they flatten toward the horizon and fade into
   haze with distance). The clouds use four noise reads: shape, billows, fraying wisps, and the shape one step toward
   the sun. Light through their thickness (Beer) makes cores dark and edges bright. A silver lining comes from the
   forward phase. Sunlight reaches them through the air at their height, so they turn orange and pink at sunset.
   Thick cloud also blocks skylight.
   - Cirrus (`skinnerCirrus`, six reads) is streaks along x, filled with fibres: the fine noise stretched 30:1 along
     the wind at two sizes (`cirrusFibreScale`, 24 km across a tile, and a sixth of it), bent together by one coarse read. The fibres fray the streaks'
     edges and set their optical thickness, so thin ice lets the sky through and strand cores hardly. A read of the
     streaks a step toward the sun shades the far side. Far off, mipmapping averages the fibres away, so the low sky
     looks as it did before the fibres (2026-10-17). Where the air comes from a sky texture (the Maps pages and
     Unity's volumetric sky), cirrus is drawn per pixel over it: overhead, a 2048 × 512 texture is about 0.2° a texel,
     some six times coarser than a 2560 × 1440 screen, and baked cirrus smeared.
   - Per-pixel cirrus costs the T1 Maps page 0.06 ms a frame looking up (whole frames 0.26 → 0.32 ms) and 0.14 ms
     looking along the ground (0.43–0.49 → 0.57–0.63 ms). Measured 2026-10-17: Crisp alien dusk at coverage .45
     against 0, Fog off, 2560 × 1347, RX 7800 XT, the browser pane in view, the panel's median of 60, two interleaved
     rounds each. Skipping the fibre reads past 150 km changed neither number.

**Cost.** fxc compiles the Unity pixel shader to about 861 instruction slots (ps_5_0, counting every branch; 824
before the fibres). A sky reads the noise at most 15 times: 10 with clouds and cirrus, 1 more for the moon and 4 for aurora. Branches skip
parts whose preset is off. It is one draw call over the background pixels, with no setup work. Measured on the page
at 2560 × 1440 with the sky filling the screen, on an RX 7800 XT: 0.78–0.81 ms by day, 0.86 ms at night. The per-pixel
air is most of that. `docs/volumetrics.md` draws the air into a small texture instead, at about 0.15 ms.

## Unity

1. Copy `static/skinner-sky` and `static/volumetrics` into `Assets`. `SkinnerSkyPresets.cs` imports the noise as
   linear, repeating and uncompressed.
2. Choose **Tools > Skinner Sky > Make Materials From Presets**. This writes `Sky <name>.mat` beside `presets.json`,
   with the `Skinner/Volumetric Sky` shader, so a preset looks as it does on the Maps pages. Run it again after
   editing the presets; it updates the materials in place, and turns older `Skinner/Sky` ones volumetric.
3. Select a material, then choose **Tools > Skinner Sky > Use Selected Material As Sky**. This sets the skybox and the
   fog colour, turns the Sun Source (or the first directional light) to the preset's sun, sets up the volumetrics
   (the work material, URP's fog pass and the `SkinnerVolumetrics` component; [volumetrics.md](volumetrics.md)) and
   updates the environment lighting.

`Skinner/Sky` stays as the reference shader for the core and as a skybox that needs no script or render pass. The
menu no longer makes materials for it: it costs about 0.7 ms against 0.2 ms, and has no height fog. A material made by
hand still works.

The shader is a plain CG skybox, like Unity's own procedural sky, so it should also work in Built-in and in gamma
colour space. The check below ran URP in linear colour space only. If the camera does
its own tone mapping (URP post-processing with ACES), switch off the material's **tonemap** toggle. The shader then
outputs HDR light × exposure, and the sun disc can drive bloom.

**Check (2026-10-15).** The shader body was compiled with fxc 10.0.19041 (ps_5_0 and vs_5_0) against Unity
6000.3.11f1's own `CGIncludes`, with the `/Gec` flag that Unity's headers need. Both compiled with no errors or
warnings. `SkinnerSkyPresets.cs` was compiled with Unity's own Roslyn (`DotNetSdkRoslyn\csc.dll`, with
`UNITY_EDITOR` defined) against `UnityEngine.CoreModule` and `UnityEditor.CoreModule`, and also compiled cleanly.

Then it ran in Unity. The project was a fresh scratch URP 17.3 project on Unity 6000.3.11f1, in linear colour space,
driven in batchmode by an editor script (`C:\tmp\skinner-evidence\skinner-sky\unity\SkyCheck.cs`):
- The noise imported as linear, repeating and uncompressed.
- The shader was supported, with 0 messages.
- Both Tools menus worked: Use Selected set the skybox, the preset's fog colour, and a sun pointing away from the
  preset's sun.
- Each preset was rendered at 640 × 360 with a 60° vertical field of view, with the wind stopped. The page rendered the
  same views, and the two were compared pixel by pixel.

The first run left night skies up to 6/255 darker. The cause was a 2.2 decode before Unity's sRGB encode; the shader
now decodes with the exact sRGB curve. After that fix, all seven presets match the page to 0.1–0.3/255 (mean per
channel). The only pixels more than 10/255 apart are stars on the night presets (0.5–0.7% of pixels), which should be
twinkle: Unity's clock was at 0.36 s and the page's at 0.5 s. Not run: Built-in, gamma colour space, a camera with its
own tone mapping, GPU timing.

**Volumetric presets (2026-10-17).** In the same scratch project, the menu turned the old `Skinner/Sky` materials into
`Skinner/Volumetric Sky` ones and made the five new presets. Use Selected set up the work material, the fog pass and
the component, and the work material carried each preset's cloud and fog values. All twelve presets were rendered
with fog on (1280 × 720, two views each) and compared with `static/volumetrics/index.html` drawing the same test
scene. Sky pixels differ by 0.2–2.5/255 on average, and 0–5% of them by more than 10, at cloud edges and in twinkling
stars (aurora up). The scene itself is lit differently by design. Evidence:
`C:\tmp\skinner-evidence\sky-volumetric-presets\unity`.

**Space backdrop (2026-10-17).** In the same scratch project, all three shaders compiled with 0 messages (the
volumetrics shader now has 4 passes), the menu made Deep space, and Use Selected set it up with the backdrop baked.
Looking up (1280 × 720, 70°), Unity's frame and the T1 page's were compared in 32 × 18 blocks: 2.0/255 apart on
average, correlation 0.988; flipped or mirrored, −0.1 to 0.06, so the texture is the right way round on both.
Evidence: `C:\tmp\skinner-evidence\skinner-sky-space`.

## Limits

- The fog colour is fixed per preset. Each was measured from the page's own haze probe at one moment
  (`horizonHaze`, which includes the clouds of that moment).
- The observer stands 2 m above the ground. Flying higher does not change the sky, and nothing passes through the
  clouds: they are a layer, not a volume.
- The sun does not move during the day. A preset is one moment, and only clouds, stars and aurora move.
- At high values, ACES turns the sun disc and lit cloud tops white.
- Presets are hand-tuned on Raindance in the page's browser pane. The aurora and Milky Way shapes come from the same
  noise tile, so they repeat over very large distances.
