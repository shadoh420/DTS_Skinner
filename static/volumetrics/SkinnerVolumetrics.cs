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

    void OnDisable()
    {
        if (pano) pano.Release();
        if (ring) ring.Release();
        if (space) space.Release();
        commands?.Release();
        pano = ring = space = null;
        commands = null;
        drawn = null;
    }

    void Update() => Draw(false);

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
        // The space backdrop (pass 3), baked once per sky when it has one: 2048 x 2048 over the upper hemisphere, 32 MB.
        if (whole && sky.HasFloat("spaceIntensity") && sky.GetFloat("spaceIntensity") > 0)
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
