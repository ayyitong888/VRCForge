using System;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Security.Cryptography;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.SceneManagement;

internal static class StableExecutionTargetSceneValidation
{
    private const string ScenePath = "Assets/StableIdentityFixture/ExecutionTargetIdentity.unity";

    public static void Run()
    {
        try
        {
            Directory.CreateDirectory(Path.Combine(Application.dataPath, "StableIdentityFixture"));
            var scene = EditorSceneManager.NewScene(NewSceneSetup.EmptyScene, NewSceneMode.Single);
            var marker = new GameObject("StableIdentityMarker");
            SceneManager.MoveGameObjectToScene(marker, scene);
            Require(EditorSceneManager.SaveScene(scene, ScenePath), "initial scene save");
            AssetDatabase.Refresh(ImportAssetOptions.ForceSynchronousImport);

            var validator = FindMethod("ValidateExecutionTargetScene");
            var projectRoot = Path.GetFullPath(Path.Combine(Application.dataPath, ".."));
            var original = ReadSceneIdentity(ScenePath, projectRoot);
            Require(Invoke(validator, original, projectRoot), "original target must validate");

            marker.name = "StableIdentityMarkerChanged";
            EditorUtility.SetDirty(marker);
            EditorSceneManager.MarkSceneDirty(scene);
            Require(EditorSceneManager.SaveScene(scene), "changed scene save");
            AssetDatabase.Refresh(ImportAssetOptions.ForceSynchronousImport);
            var changed = ReadSceneIdentity(ScenePath, projectRoot);
            Require(Invoke(validator, original, projectRoot), "saved scene must retain the original target identity");
            Require(Invoke(validator, changed, projectRoot), "saved scene current identity must validate");

            var other = EditorSceneManager.NewScene(NewSceneSetup.EmptyScene, NewSceneMode.Single);
            var otherPath = "Assets/StableIdentityFixture/OtherScene.unity";
            Require(EditorSceneManager.SaveScene(other, otherPath), "alternate scene save");
            AssetDatabase.Refresh(ImportAssetOptions.ForceSynchronousImport);
            Require(!Invoke(validator, changed, projectRoot), "inactive target scene must be rejected");

            var wrongGuid = changed.DeepClone() as JObject;
            wrongGuid["guid"] = new string('0', 32);
            Require(!Invoke(validator, wrongGuid, projectRoot), "GUID drift must be rejected");
            var wrongPath = changed.DeepClone() as JObject;
            wrongPath["assetPath"] = otherPath;
            wrongPath["absolutePath"] = Path.GetFullPath(Path.Combine(projectRoot, otherPath.Replace('/', Path.DirectorySeparatorChar)));
            Require(!Invoke(validator, wrongPath, projectRoot), "path drift must be rejected");

            var coreInfo = (JObject)FindMethod("CoreInfoResult").Invoke(null, null);
            Require((string)coreInfo["coreVersion"] == "1.10.0", "Core product version");
            Require((string)coreInfo["toolContractVersion"] == "189", "Core tool contract version");
            var snapshot = (JObject)coreInfo["compileSnapshot"];
            Require((int)snapshot["errorCount"] == 0 && (int)snapshot["warningCount"] == 0, "compile snapshot diagnostics");

            var result = new JObject
            {
                ["unityVersion"] = Application.unityVersion,
                ["coreIdentity"] = (string)coreInfo["coreIdentity"],
                ["coreVersion"] = (string)coreInfo["coreVersion"],
                ["toolContractVersion"] = (string)coreInfo["toolContractVersion"],
                ["compileSnapshot"] = snapshot,
                ["originalTarget"] = true,
                ["savedSameIdentity"] = true,
                ["savedCurrentIdentity"] = true,
                ["activeSceneDrift"] = false,
                ["guidDrift"] = false,
                ["pathDrift"] = false,
            };
            var output = Path.Combine(Application.temporaryCachePath, "stable-execution-target-scene-validation.json");
            File.WriteAllText(output, result.ToString(Newtonsoft.Json.Formatting.Indented));
            Debug.Log("STABLE_EXECUTION_TARGET_SCENE_VALIDATION " + output);
            EditorApplication.Exit(0);
        }
        catch (Exception exception)
        {
            Debug.LogException(exception);
            EditorApplication.Exit(1);
        }
    }

    private static MethodInfo FindMethod(string name)
    {
        var type = AppDomain.CurrentDomain.GetAssemblies()
            .Select(assembly => assembly.GetType("VRCForge.Editor.VRCForgeMcpCoreServer", false))
            .FirstOrDefault(candidate => candidate != null);
        if (type == null) throw new InvalidOperationException("VRCForgeMcpCoreServer type not found");
        return type.GetMethod(name, BindingFlags.NonPublic | BindingFlags.Static)
            ?? throw new InvalidOperationException(name + " method not found");
    }

    private static bool Invoke(MethodInfo method, JObject scene, string projectRoot)
        => (bool)method.Invoke(null, new object[] { scene, projectRoot });

    private static JObject ReadSceneIdentity(string assetPath, string projectRoot)
    {
        var absolute = Path.GetFullPath(Path.Combine(projectRoot, assetPath.Replace('/', Path.DirectorySeparatorChar)));
        string digest;
        using (var sha = SHA256.Create())
            digest = BitConverter.ToString(sha.ComputeHash(File.ReadAllBytes(absolute))).Replace("-", string.Empty).ToLowerInvariant();
        return new JObject
        {
            ["assetPath"] = assetPath,
            ["absolutePath"] = absolute,
            ["guid"] = AssetDatabase.AssetPathToGUID(assetPath),
            ["revision"] = File.GetLastWriteTimeUtc(absolute).Ticks.ToString(System.Globalization.CultureInfo.InvariantCulture),
            ["digest"] = digest,
        };
    }

    private static void Require(bool condition, string label)
    {
        if (!condition) throw new InvalidOperationException("Stable execution-target assertion failed: " + label);
    }
}
