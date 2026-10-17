// The volumetrics prototype's work passes for Unity (URP); docs/volumetrics.md. SkinnerVolumetrics.cs draws passes 0
// and 1 into its textures, and URP's Full Screen Pass renderer feature draws pass 2 over the frame. The material
// takes the sky material's values (SkinnerVolumetrics.cs copies them each frame), so it declares the same properties:
// Unity keeps only declared ones on a material.
//   0  Sky texture: the air and the volumetric clouds over it, rgb, and how much of what is behind shows, a.
//   1  Fog ring: the fog's colour by azimuth, the low sky round each direction averaged.
//   2  Fog: closed-form height fog over the frame from the depth buffer, then exposure and (tonemap on) ACES.
Shader "Hidden/Skinner Volumetrics"
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
        cirrusSpeed ("Cirrus wind (km/s)", Vector) = (0.04, 0.01, 0, 0)
        auroraIntensity ("Aurora", Float) = 0
        auroraColor ("Aurora low colour", Vector) = (0.1, 1, 0.4, 0)
        auroraColor2 ("Aurora high colour", Vector) = (0.6, 0.2, 1, 0)
        fogDensity ("Fog density (per metre at its base)", Float) = 0.0012
        fogHeight ("Fog height (metres to thin by e)", Float) = 40
        fogBase ("Fog base (metres)", Float) = 0
        fogBrightness ("Fog brightness", Float) = 1
        fogColor ("Fog colour (sRGB, for RenderSettings)", Vector) = (0.9, 0.92, 0.93, 0)
        [ToggleUI] tonemap ("Exposure and ACES in the fog pass (off: HDR out, for a camera that tone-maps)", Float) = 1
    }
    SubShader
    {
        Cull Off ZWrite Off ZTest Always

        Pass
        {
            Name "Sky texture"
            CGPROGRAM
            #pragma vertex vert
            #pragma fragment frag
            #pragma target 3.5
            #include "UnityCG.cginc"
            #define SKINNER_SKIP_BEHIND
            #include "SkinnerVolumetricsHLSL.cginc"
            float4 _PanoSize;

            float4 vert(uint id : SV_VertexID) : SV_POSITION { return float4(float2((id << 1) & 2, id & 2) * 2. - 1., 0., 1.); }

            float4 frag(float4 pos : SV_POSITION) : SV_Target
            {
                // Pixel rows and texture rows run the same way on every API, so a texel's direction is its pixel's.
                float3 d = panoDir(pos.xy / _PanoSize.xy), s = skinnerSky(d);
                float4 c = skinnerClouds(d, s);
                return float4(s * c.a + c.rgb, c.a);
            }
            ENDCG
        }

        Pass
        {
            Name "Fog ring"
            CGPROGRAM
            #pragma vertex vert
            #pragma fragment frag
            #pragma target 3.5
            #include "UnityCG.cginc"
            #include "SkinnerVolumetricsHLSL.cginc"

            float4 vert(uint id : SV_VertexID) : SV_POSITION { return float4(float2((id << 1) & 2, id & 2) * 2. - 1., 0., 1.); }

            // 2 to 26 degrees up, a triangle weight 54 degrees of azimuth to each side read every 6: fog is lit by the
            // sky round it, not by one cloud, and a low sun's glow fades in and out (the page's skies.js ringScene).
            float4 frag(float4 pos : SV_POSITION) : SV_Target
            {
                float3 s = float3(0., 0., 0.);
                for (int i = -8; i <= 8; i++) for (int j = 0; j < 3; j++)
                    s += float(9 - abs(i)) * tex2D(_SkinnerPano, panoUV(toward(pos.x / 64. * 360. + float(i) * 6., 2. + float(j * j) * 6.))).rgb;
                return float4(s / 243., 1.);
            }
            ENDCG
        }

        Pass
        {
            Name "Fog"
            HLSLPROGRAM
            #pragma vertex Vert
            #pragma fragment frag
            #include "Packages/com.unity.render-pipelines.universal/ShaderLibrary/Core.hlsl"
            #include "Packages/com.unity.render-pipelines.core/Runtime/Utilities/Blit.hlsl"
            #include "Packages/com.unity.render-pipelines.universal/ShaderLibrary/DeclareDepthTexture.hlsl"
            #define SKINNER_SRP
            #include "SkinnerVolumetricsHLSL.cginc"

            float4 frag(Varyings input) : SV_Target
            {
                UNITY_SETUP_STEREO_EYE_INDEX_POST_VERTEX(input);
                float2 uv = input.texcoord;
                float3 c = SAMPLE_TEXTURE2D_X(_BlitTexture, sampler_PointClamp, uv).rgb;
                float raw = SampleSceneDepth(uv);
                float3 v = ComputeWorldSpacePosition(uv, raw, UNITY_MATRIX_I_VP) - _WorldSpaceCameraPos;
                #if UNITY_REVERSED_Z
                bool sky = raw <= 0.;
                #else
                bool sky = raw >= 1.;
                #endif
                float3 rd = normalize(v).xzy;
                c = skinnerFog(c, _WorldSpaceCameraPos.y, rd, sky ? 1e5 : length(v), SAMPLE_PLAIN(_SkinnerRing, float2(panoUV(rd).x, .5)).rgb) * exposure;
                if (tonemap < .5) return float4(c, 1.);
                // As the page: ACES, the display's 2.2 and half a step of dither, then decoded with the sRGB curve, which
                // the linear frame's output encodes again, so the frame holds the page's bytes.
                float3 shown = max(pow(aces(c), 1. / 2.2) + (hash33(float3(input.positionCS.xy, 7.)).x - .5) / 255., 0.);
                return float4(shown <= .04045 ? shown / 12.92 : pow((shown + .055) / 1.055, 2.4), 1.);
            }
            ENDHLSL
        }
    }
    Fallback Off
}
