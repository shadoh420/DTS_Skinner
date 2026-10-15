// Skinner Sky for Unity (Built-in and URP): a skybox material. The look is SkinnerSky.cginc, the same file DTS
// Skinner's T1 Maps page draws; this wraps it in HLSL. Properties are the preset keys of presets.json (defaults as
// static/t1-maps/skies.js skinnerDefaults); Tools > Skinner Sky makes one material per preset. Directions: the
// preset's azimuth is degrees from +Z (north) toward +X (east), elevation from the horizon.
Shader "Skinner/Sky"
{
    Properties
    {
        [NoScaleOffset] skyNoise ("Noise (SkinnerSkyNoise.png, imported as linear, repeat)", 2D) = "gray" {}
        sunAzimuth ("Sun azimuth", Float) = 200
        sunElevation ("Sun elevation", Float) = 35
        sunColor ("Sun colour", Vector) = (1, 0.98, 0.95, 0)
        sunIntensity ("Sun intensity", Float) = 20
        sunSize ("Sun size (degrees)", Float) = 1.2
        moonAzimuth ("Moon azimuth", Float) = 20
        moonElevation ("Moon elevation", Float) = -30
        moonColor ("Moon colour", Vector) = (0.9, 0.92, 1, 0)
        moonIntensity ("Moon intensity", Float) = 0
        moonSize ("Moon size (degrees)", Float) = 3.5
        rayleigh ("Rayleigh (air colour, per channel)", Vector) = (1, 1, 1, 0)
        ozone ("Ozone", Float) = 1
        mie ("Mie (haze)", Float) = 1
        mieG ("Mie forward scattering", Float) = 0.8
        groundColor ("Ground colour", Vector) = (0.25, 0.24, 0.22, 0)
        nightColor ("Night glow", Vector) = (0.0004, 0.0007, 0.0014, 0)
        exposure ("Exposure", Float) = 1.5
        starIntensity ("Stars", Float) = 0
        starDensity ("Star grid", Float) = 250
        milkyWay ("Milky Way", Float) = 0
        cloudCoverage ("Cloud coverage", Float) = 0.5
        cloudSoftness ("Cloud softness", Float) = 0.15
        cloudScale ("Cloud tile (km)", Float) = 20
        cloudHeight ("Cloud height (km)", Float) = 2
        cloudSpeed ("Cloud wind (km/s)", Vector) = (0.02, 0.005, 0, 0)
        cloudColor ("Cloud colour", Vector) = (1, 1, 1, 0)
        cloudDarkness ("Cloud darkness", Float) = 1
        cloudOpacity ("Cloud opacity", Float) = 1
        cirrusCoverage ("Cirrus coverage", Float) = 0.3
        cirrusScale ("Cirrus tile (km)", Float) = 150
        cirrusSpeed ("Cirrus wind (km/s)", Vector) = (0.04, 0.01, 0, 0)
        auroraIntensity ("Aurora", Float) = 0
        auroraColor ("Aurora low colour", Vector) = (0.1, 1, 0.4, 0)
        auroraColor2 ("Aurora high colour", Vector) = (0.6, 0.2, 1, 0)
        fogColor ("Fog colour (sRGB, for RenderSettings)", Vector) = (0.9, 0.92, 0.93, 0)
        [ToggleUI] tonemap ("Exposure and ACES here (off: HDR out, for a camera that tone-maps)", Float) = 1
    }
    SubShader
    {
        Tags { "Queue" = "Background" "RenderType" = "Background" "PreviewType" = "Skybox" }
        Cull Off ZWrite Off

        Pass
        {
            CGPROGRAM
            #pragma vertex vert
            #pragma fragment frag
            #pragma target 3.5
            #include "UnityCG.cginc"

            #define vec2 float2
            #define vec3 float3
            #define mix lerp
            #define fract frac
            #define texture2D(s, uv) tex2D(s, float2((uv).x, -(uv).y))
            #define uTime _Time.y
            #define UNROLL [unroll]

            sampler2D skyNoise;
            float sunAzimuth, sunElevation, sunIntensity, sunSize, moonAzimuth, moonElevation, moonIntensity, moonSize, ozone, mie, mieG,
                exposure, starIntensity, starDensity, milkyWay, cloudCoverage, cloudSoftness, cloudScale, cloudHeight, cloudDarkness,
                cloudOpacity, cirrusCoverage, cirrusScale, auroraIntensity, tonemap;
            float3 sunColor, moonColor, rayleigh, groundColor, nightColor, cloudSpeed, cloudColor, cirrusSpeed, auroraColor, auroraColor2;

            #include "SkinnerSky.cginc"

            struct v2f { float4 pos : SV_POSITION; float3 dir : TEXCOORD0; UNITY_VERTEX_OUTPUT_STEREO };

            v2f vert(appdata_base v)
            {
                v2f o;
                UNITY_SETUP_INSTANCE_ID(v);
                UNITY_INITIALIZE_VERTEX_OUTPUT_STEREO(o);
                o.pos = UnityObjectToClipPos(v.vertex);
                o.dir = v.vertex.xyz;
                return o;
            }

            float4 frag(v2f i) : SV_Target
            {
                float3 c = skinnerSky(normalize(i.dir).xzy) * exposure;  // Unity is y up: the sky's z up is its y.
                if (tonemap > .5) c = aces(c);
                // Half a step of dither in display values against banding, as the page adds it.
                float3 shown = pow(max(c, 0.), 1. / 2.2) + (hash33(float3(i.pos.xy, 7.)).x - .5) / 255.;
                #ifdef UNITY_COLORSPACE_GAMMA
                return float4(shown, 1.);
                #else
                return float4(pow(max(shown, 0.), 2.2), 1.);  // A linear project encodes it again on the way out.
                #endif
            }
            ENDCG
        }
    }
    Fallback Off
}
