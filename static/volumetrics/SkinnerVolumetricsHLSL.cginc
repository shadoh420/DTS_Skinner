// The HLSL side of the volumetrics shaders: the macros that let the shared GLSL/HLSL files compile as HLSL, the preset's
// uniforms, then SkinnerSpace.cginc, SkinnerSky.cginc, SkinnerClouds.cginc and SkinnerFog.cginc. Define SKINNER_SRP first in a pass
// built on URP's ShaderLibrary (texture objects instead of sampler2D), and SKINNER_SKIP_BEHIND for the sky texture.
#define vec2 float2
#define vec3 float3
#define vec4 float4
#define mix lerp
#define fract frac
#define atan(y, x) atan2(y, x)
#define uTime _Time.y
#define UNROLL [unroll]
#ifdef SKINNER_SRP
TEXTURE2D(skyNoise); SAMPLER(samplerskyNoise);
TEXTURE2D(_SkinnerPano); SAMPLER(sampler_SkinnerPano);
TEXTURE2D(_SkinnerRing); SAMPLER(sampler_SkinnerRing);
TEXTURE2D(spaceMap); SAMPLER(samplerspaceMap);
// Unity's v = 0 is an image's bottom row and the page's is its top: the shared files' reads flip v.
#define texture2D(s, uv) SAMPLE_TEXTURE2D(s, sampler##s, float2((uv).x, -(uv).y))
#define SAMPLE_LOD(s, uv, lod) SAMPLE_TEXTURE2D_LOD(s, sampler##s, float2((uv).x, -(uv).y), lod)
#define SAMPLE_PLAIN(s, uv) SAMPLE_TEXTURE2D(s, sampler##s, uv)
#else
sampler2D skyNoise, _SkinnerPano, _SkinnerRing, spaceMap;
#define texture2D(s, uv) tex2D(s, float2((uv).x, -(uv).y))
#define SAMPLE_LOD(s, uv, lod) tex2Dlod(s, float4((uv).x, -(uv).y, 0., lod))
#define SAMPLE_PLAIN(s, uv) tex2D(s, uv)
#endif

float sunAzimuth, sunElevation, sunIntensity, sunSize, moonAzimuth, moonElevation, moonIntensity, moonSize, ozone, mie, mieG,
    exposure, starIntensity, starDensity, milkyWay, cloudCoverage, cloudSoftness, cloudScale, cloudHeight, cloudThickness, cloudDarkness,
    cloudOpacity, cirrusCoverage, cirrusScale, cirrusHeight, cirrusFibres, cirrusFibreScale, cirrusOpacity, auroraIntensity, lightningRate, lightningIntensity,
    fogDensity, fogHeight, fogBase, fogBrightness, tonemap, layerCoverage, spaceIntensity;
float3 sunColor, moonColor, rayleigh, groundColor, nightColor, cloudSpeed, cloudColor, cirrusSpeed, auroraColor, auroraColor2;
float4 lightningDir, lightningColor;  // Globals, set by SkinnerVolumetrics.cs (SkinnerSky.cginc skinnerFlash).

// The sky's own 2.5D cloud layer reads layerCoverage, never set (0): the volumetric clouds replace it.
#include "../skinner-sky/SkinnerSpace.cginc"
#define cloudCoverage layerCoverage
#include "../skinner-sky/SkinnerSky.cginc"
#undef cloudCoverage
#include "SkinnerClouds.cginc"
#include "SkinnerFog.cginc"
