// Skinner Volumetrics for Unity (URP): keeps the volumetrics prototype's sky texture and fog ring up to date
// (docs/volumetrics.md). The skybox is Skinner/Volumetric Sky. The fog is pass 2 of this component's material, drawn by
// URP's Full Screen Pass renderer feature. Tools > Skinner Sky > Use Selected Material As Sky sets up all three.
using System.Collections.Generic;
using UnityEngine;
using UnityEngine.Rendering;
#if UNITY_EDITOR
using System.IO;
using System.Reflection;
using UnityEditor;
using UnityEngine.Rendering.Universal;
#endif

[ExecuteAlways]
public class SkinnerVolumetrics : MonoBehaviour
{
    [Tooltip("Hidden/Skinner Volumetrics. The Full Screen Pass feature draws the fog with the same material (pass 2).")]
    public Material volumetrics;
    [Tooltip("The sky texture's size. Its cost is set by this, not by the screen's.")]
    public Vector2Int skyTextureSize = new Vector2Int(1024, 256);
    [Tooltip("Rewrite the sky texture over this many frames, a band of rows a frame.")]
    [Range(1, 16)] public int bands = 8;

    RenderTexture pano, ring, space;
    CommandBuffer commands;
    Material drawn;
    int row;
    float strikeAt = float.NegativeInfinity, nextStrike = float.NaN;
    readonly List<Vector2> pulses = new List<Vector2>();  // Each pulse's delay after the strike (s) and its size.
    static readonly int lightningDir = Shader.PropertyToID("lightningDir"), lightningColor = Shader.PropertyToID("lightningColor");

    void OnDisable()
    {
        if (pano) pano.Release();
        if (ring) ring.Release();
        if (space) space.Release();
        commands?.Release();
        pano = ring = space = null;
        commands = null;
        drawn = null;
        Shader.SetGlobalVector(lightningColor, Vector4.zero);
    }

    void Update()
    {
        if (RenderSettings.skybox) Lightning(RenderSettings.skybox, Time.realtimeSinceStartup);
        Draw(false);
    }

    // Lightning, timed as the page's (static/t1-maps/skies.js lightning): the sky material's lightningRate flashes a
    // minute, each after a random wait, in the cloud layer 3-25 km off, flickering in 2 or 3 pulses over about 0.2 s.
    // Globals, so the sky and the fog pass both see them (SkinnerSky.cginc skinnerFlash, SkinnerFog.cginc).
    void Lightning(Material sky, float seconds)
    {
        float rate = sky.GetFloat("lightningRate") / 60f;
        if (!(rate > 0f)) { Shader.SetGlobalVector(lightningColor, Vector4.zero); return; }
        float Wait() => -Mathf.Log(1f - Random.value) / rate;
        if (float.IsNaN(nextStrike) || seconds < strikeAt) nextStrike = seconds + Wait();  // First, or the clock went back.
        if (seconds >= nextStrike)
        {
            float h = 3f + 22f * Mathf.Sqrt(Random.value), around = 2f * Mathf.PI * Random.value, mid = sky.GetFloat("cloudHeight") + sky.GetFloat("cloudThickness") * .5f;
            float up = Mathf.Atan2(mid, h), far = Mathf.Sqrt(h * h + mid * mid);
            Shader.SetGlobalVector(lightningDir, new Vector4(Mathf.Sin(around) * Mathf.Cos(up), Mathf.Cos(around) * Mathf.Cos(up), Mathf.Sin(up), far * far / 8f));
            strikeAt = nextStrike;
            pulses.Clear();
            pulses.Add(new Vector2(0f, 1f));
            pulses.Add(new Vector2(.05f + .05f * Random.value, .4f + .6f * Random.value));
            if (Random.value < .5f) pulses.Add(new Vector2(.13f + .07f * Random.value, .3f + .5f * Random.value));
            nextStrike = strikeAt + .3f + Wait();
        }
        float glow = 0f;
        foreach (var pulse in pulses) if (seconds >= strikeAt + pulse.x) glow += pulse.y * Mathf.Exp((strikeAt + pulse.x - seconds) * 30f);
        Shader.SetGlobalVector(lightningColor, new Vector4(.8f, .85f, 1f, 0f) * (glow * 5f));
    }

    // Rewrites the next band of the sky texture's rows (all of them after a change of sky or size, or when whole), then
    // the ring. The sky material's values are copied to the work material first, so the fog pass has them too.
    public void Draw(bool whole)
    {
        var sky = RenderSettings.skybox;
        if (!volumetrics || !sky) return;
        if (!pano || pano.width != skyTextureSize.x || pano.height != skyTextureSize.y)
        {
            OnDisable();
            pano = new RenderTexture(skyTextureSize.x, skyTextureSize.y, 0, RenderTextureFormat.ARGBHalf)
                { name = "Skinner sky texture", wrapModeU = TextureWrapMode.Repeat, wrapModeV = TextureWrapMode.Clamp, filterMode = FilterMode.Bilinear };
            ring = new RenderTexture(64, 1, 0, RenderTextureFormat.ARGBHalf) { name = "Skinner fog ring", wrapMode = TextureWrapMode.Repeat, filterMode = FilterMode.Bilinear };
            commands = new CommandBuffer { name = "Skinner Volumetrics" };
        }
        // ponytail: copies every property each frame (microseconds); copy only on change if it ever shows in a profile.
        CopyValues(sky, volumetrics);
        whole |= sky != drawn;
        drawn = sky;
        int rows = whole ? pano.height : Mathf.CeilToInt(pano.height / (float)bands);
        if (whole) row = 0;
        commands.Clear();
        // The space backdrop (pass 3), baked once per sky when it has one (or when the key is first raised): 2048 x 2048
        // over the whole sphere, 32 MB.
        if ((whole || !space) && sky.HasFloat("spaceIntensity") && sky.GetFloat("spaceIntensity") > 0)
        {
            if (!space) space = new RenderTexture(2048, 2048, 0, RenderTextureFormat.ARGBHalf) { name = "Skinner space", wrapMode = TextureWrapMode.Clamp, filterMode = FilterMode.Bilinear };
            commands.SetGlobalVector("_SpaceSize", new Vector4(space.width, space.height));
            commands.SetGlobalTexture("spaceMap", space);
            commands.SetRenderTarget(space);
            commands.DrawProcedural(Matrix4x4.identity, volumetrics, 3, MeshTopology.Triangles, 3);
        }
        commands.SetGlobalVector("_PanoSize", new Vector4(pano.width, pano.height));
        commands.SetGlobalTexture("_SkinnerPano", pano);
        commands.SetGlobalTexture("_SkinnerRing", ring);
        commands.SetRenderTarget(pano);
        commands.SetViewport(new Rect(0, row, pano.width, rows));
        commands.DrawProcedural(Matrix4x4.identity, volumetrics, 0, MeshTopology.Triangles, 3);
        commands.SetRenderTarget(ring);
        commands.DrawProcedural(Matrix4x4.identity, volumetrics, 1, MeshTopology.Triangles, 3);
        Graphics.ExecuteCommandBuffer(commands);
        row = (row + rows) % pano.height;
    }

    // Sets each of from's shader properties on to, one by one. Not CopyPropertiesFromMaterial: that swaps in from's
    // whole set, dropping what from lacks (the fog's keys on a Skinner/Sky preset; everything for the Hidden shader,
    // which declares no properties), so the fog pass read zeros.
    static void CopyValues(Material from, Material to)
    {
        var shader = from.shader;
        for (int i = 0; i < shader.GetPropertyCount(); i++)
        {
            int id = shader.GetPropertyNameId(i);
            switch (shader.GetPropertyType(i))
            {
                case ShaderPropertyType.Texture: to.SetTexture(id, from.GetTexture(id)); break;
                case ShaderPropertyType.Color: to.SetColor(id, from.GetColor(id)); break;
                case ShaderPropertyType.Vector: to.SetVector(id, from.GetVector(id)); break;
                case ShaderPropertyType.Int: to.SetInteger(id, from.GetInteger(id)); break;
                default: to.SetFloat(id, from.GetFloat(id)); break;
            }
        }
    }

#if UNITY_EDITOR
    // For the current Skinner/Volumetric Sky skybox (Tools > Skinner Sky > Use Selected Material As Sky calls this): the
    // work material beside this shader, the fog's Full Screen Pass on the URP renderer, and this component in the
    // scene. It reuses what is there.
    public static void SetUp()
    {
        var shader = Shader.Find("Hidden/Skinner Volumetrics");
        string materialPath = Path.GetDirectoryName(AssetDatabase.GetAssetPath(shader)).Replace('\\', '/') + "/Skinner Volumetrics.mat";
        var material = AssetDatabase.LoadAssetAtPath<Material>(materialPath);
        if (!material) AssetDatabase.CreateAsset(material = new Material(shader), materialPath);

        // ponytail: the pipeline's first renderer; pick the camera's renderer if a project uses several.
        var pipeline = GraphicsSettings.currentRenderPipeline as UniversalRenderPipelineAsset;
        var data = pipeline ? pipeline.rendererDataList[0] : null;
        if (!data) Debug.LogWarning("Skinner Volumetrics: no URP renderer, so no fog pass. The sky texture still works.");
        else if (!data.rendererFeatures.Exists(feature => feature is FullScreenPassRendererFeature pass && pass.passMaterial == material))
        {
            var feature = ScriptableObject.CreateInstance<FullScreenPassRendererFeature>();
            feature.name = "Skinner fog";
            feature.passMaterial = material;
            feature.passIndex = 2;
            feature.injectionPoint = FullScreenPassRendererFeature.InjectionPoint.BeforeRenderingPostProcessing;
            feature.requirements = ScriptableRenderPassInput.Depth;
            feature.fetchColorBuffer = true;
            AssetDatabase.AddObjectToAsset(feature, data);
            data.rendererFeatures.Add(feature);
            // URP keeps a parallel list of the features' file ids, which its own editor fills in; so does this.
            AssetDatabase.TryGetGUIDAndLocalFileIdentifier(feature, out _, out long id);
            (typeof(ScriptableRendererData).GetField("m_RendererFeatureMap", BindingFlags.NonPublic | BindingFlags.Instance)?.GetValue(data) as List<long>)?.Add(id);
            data.SetDirty();
            EditorUtility.SetDirty(data);
        }
        var driver = FindFirstObjectByType<SkinnerVolumetrics>();
        if (!driver) driver = new GameObject("Skinner Volumetrics").AddComponent<SkinnerVolumetrics>();
        driver.volumetrics = material;
        driver.Draw(true);
        AssetDatabase.SaveAssets();
    }
#endif
}
