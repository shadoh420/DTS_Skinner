// Skinner Sky presets for Unity. Copy DTS Skinner's static/skinner-sky and static/volumetrics folders into Assets, then
// Tools > Skinner Sky > Make Materials From Presets writes "Sky <name>.mat" (Skinner/Volumetric Sky) beside
// presets.json, one per preset (run it again after editing presets.json: the materials are updated in place, older
// Skinner/Sky ones included). Select one and Tools > Skinner Sky > Use Selected Material As Sky makes it the scene's
// skybox, sets the fog colour, turns the scene's sun to the preset's and sets up the volumetrics (SkinnerVolumetrics.cs).
#if UNITY_EDITOR
using System.Globalization;
using System.IO;
using System.Linq;
using System.Text.RegularExpressions;
using UnityEditor;
using UnityEngine;

public class SkinnerSkyPresets : AssetPostprocessor
{
    // The noise is data: no sRGB decode, tiling, and uncompressed (block compression shifts the cloud edges).
    void OnPreprocessTexture()
    {
        if (Path.GetFileName(assetPath) != "SkinnerSkyNoise.png") return;
        var importer = (TextureImporter)assetImporter;
        importer.sRGBTexture = false;
        importer.wrapMode = TextureWrapMode.Repeat;
        importer.textureCompression = TextureImporterCompression.Uncompressed;
    }

    [MenuItem("Tools/Skinner Sky/Make Materials From Presets")]
    static void MakeMaterials()
    {
        // Keys a preset leaves out keep the shader's defaults, which are the page's (tests/test_skinner_sky.py).
        Shader shader = Shader.Find("Skinner/Volumetric Sky"), merged = Shader.Find("Skinner/Sky");
        if (!shader || !merged) { Debug.LogError("Skinner Sky: copy both static/skinner-sky and static/volumetrics into Assets."); return; }
        var folder = Path.GetDirectoryName(AssetDatabase.GetAssetPath(merged)).Replace('\\', '/');  // presets.json is beside it.
        var noise = AssetDatabase.LoadAssetAtPath<Texture2D>(folder + "/SkinnerSkyNoise.png");
        // Each preset is a flat object of numbers, three-number lists and strings; a key is the property of that name.
        foreach (Match preset in Regex.Matches(File.ReadAllText(folder + "/presets.json"), @"\{[^{}]*\}"))
        {
            var material = new Material(shader);
            material.SetTexture("skyNoise", noise);
            var name = "Sky";
            foreach (Match field in Regex.Matches(preset.Value, @"""(\w+)""\s*:\s*(""[^""]*""|\[[^\]]*\]|[-+.\deE]+)"))
            {
                string key = field.Groups[1].Value, value = field.Groups[2].Value;
                if (value[0] == '"') { if (key == "name") name = value.Trim('"'); continue; }
                var numbers = value.Trim('[', ']').Split(',').Select(v => float.Parse(v, CultureInfo.InvariantCulture)).ToArray();
                if (numbers.Length == 1) material.SetFloat(key, numbers[0]);
                else material.SetVector(key, new Vector4(numbers[0], numbers[1], numbers[2], 0));
            }
            var path = $"{folder}/Sky {name}.mat";
            var old = AssetDatabase.LoadAssetAtPath<Material>(path);
            if (old) { old.shader = shader; old.CopyPropertiesFromMaterial(material); EditorUtility.SetDirty(old); }
            else AssetDatabase.CreateAsset(material, path);
        }
        AssetDatabase.SaveAssets();
    }

    [MenuItem("Tools/Skinner Sky/Use Selected Material As Sky")]
    static void UseSelected()
    {
        var material = Selection.activeObject as Material;
        if (!material || material.shader.name != "Skinner/Volumetric Sky") { Debug.LogError("Skinner Sky: select a Skinner/Volumetric Sky material first."); return; }
        RenderSettings.skybox = material;
        var fog = material.GetVector("fogColor");
        RenderSettings.fogColor = new Color(fog.x, fog.y, fog.z);
        // The sun light: Lighting's Sun Source, else the first directional light. It shines away from the sun.
        var sun = RenderSettings.sun ? RenderSettings.sun
            : Object.FindObjectsByType<Light>(FindObjectsSortMode.None).FirstOrDefault(light => light.type == LightType.Directional);
        if (sun)
        {
            Undo.RecordObject(sun.transform, "Skinner Sky sun");
            float azimuth = material.GetFloat("sunAzimuth") * Mathf.Deg2Rad, elevation = material.GetFloat("sunElevation") * Mathf.Deg2Rad;
            sun.transform.rotation = Quaternion.LookRotation(-new Vector3(Mathf.Sin(azimuth) * Mathf.Cos(elevation), Mathf.Sin(elevation), Mathf.Cos(azimuth) * Mathf.Cos(elevation)));
        }
        SkinnerVolumetrics.SetUp();
        DynamicGI.UpdateEnvironment();
        UnityEditor.SceneManagement.EditorSceneManager.MarkSceneDirty(UnityEngine.SceneManagement.SceneManager.GetActiveScene());
    }
}
#endif
