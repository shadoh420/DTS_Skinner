// Skinner Volumetric Sky for Unity (URP): the skybox half of the volumetrics prototype (docs/volumetrics.md). It reads
// the sky texture that SkinnerVolumetrics.cs keeps up to date (air and volumetric clouds) and adds the sun disc, stars,
// moon and aurora per pixel. It outputs linear light before exposure: the fog pass (Hidden/Skinner Volumetrics, in
// URP's Full Screen Pass renderer feature) applies fog, exposure and tone mapping to the whole frame.
// Properties are Skinner/Sky's (same names, so its preset materials carry over) plus the clouds' and fog's keys.
Shader "Skinner/Volumetric Sky"
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
        cloudHeight ("Cloud base (km)", Float) = 2
        cloudThickness ("Cloud thickness (km)", Float) = 1.5
        cloudSpeed ("Cloud wind (km/s)", Vector) = (0.02, 0.005, 0, 0)
        cloudColor ("Cloud colour", Vector) = (1, 1, 1, 0)
        cloudDarkness ("Cloud darkness", Float) = 1
        cloudOpacity ("Cloud opacity", Float) = 1
        cirrusCoverage ("Cirrus coverage", Float) = 0
        cirrusScale ("Cirrus tile (km)", Float) = 150
        cirrusHeight ("Cirrus height (km)", Float) = 9
        cirrusFibres ("Cirrus fibres", Float) = 1
        cirrusFibreScale ("Cirrus fibre tile (km)", Float) = 24
        cirrusOpacity ("Cirrus opacity", Float) = 1
        cirrusSpeed ("Cirrus wind (km/s)", Vector) = (0.04, 0.01, 0, 0)
        auroraIntensity ("Aurora", Float) = 0
        auroraColor ("Aurora low colour", Vector) = (0.1, 1, 0.4, 0)
        auroraColor2 ("Aurora high colour", Vector) = (0.6, 0.2, 1, 0)
        lightningRate ("Lightning (flashes a minute, 0 off)", Float) = 0
        lightningIntensity ("Lightning brightness", Float) = 1
        fogDensity ("Fog density (per metre at its base)", Float) = 0.0012
        fogHeight ("Fog height (metres to thin by e)", Float) = 40
        fogBase ("Fog base (metres)", Float) = 0
        fogBrightness ("Fog brightness", Float) = 1
        spaceIntensity ("Space backdrop (Star Nest, baked)", Float) = 0
        fogColor ("Fog colour (sRGB, for RenderSettings)", Vector) = (0.9, 0.92, 0.93, 0)
        [ToggleUI] tonemap ("Exposure and ACES in the fog pass (off: HDR out, for a camera that tone-maps)", Float) = 1
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
            #include "SkinnerVolumetricsHLSL.cginc"

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
                float3 d = normalize(i.dir).xzy;  // Unity is y up: the sky's z up is its y.
                float4 c = tex2D(_SkinnerPano, panoUV(d));
                float3 s = c.rgb + (d.z > 0. ? skinnerBehind(d, c.rgb, toLight(PLANET + .002, d.z) * c.a) : float3(0., 0., 0.));
                return float4(skinnerCirrus(d, s, c.rgb, c.a * c.a) + skinnerCloudsFlash(d, c.a), 1.);  // Cirrus under the clouds, per pixel, as skies.js.
            }
            ENDCG
        }
    }
    Fallback Off
}
