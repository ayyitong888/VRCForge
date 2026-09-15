using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Security.Cryptography;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.SceneManagement;
using VRCForge.Core.MCP;

namespace VRCForge.Editor
{
    [VRCForgeCommand(
        toolId: "vrc_set_renderer_material_slot",
        Summary = "Preview or replace exactly one Renderer.sharedMaterials slot with one persistent writable Assets/*.mat asset. Use for one generic renderer slot; do not use for batch or hierarchy-only targeting.")]
    public static class RendererMaterialSlotTool
    {
        private const string ResultSchema = "vrcforge.renderer_material_slot.v1";

        public class Parameters
        {
            [VRCForgeInput("Optional 1..256 exact slot assignments in one saved scene; mutually exclusive with single-slot fields.", IsRequired = false)] public object[] assignments { get; set; }
            [VRCForgeInput("Exact batch evidence returned by preview.", IsRequired = false)] public object expectedBatchPlan { get; set; }

            [VRCForgeInput("Exact loaded-scene renderer hierarchy path; required when assignments is absent.", IsRequired = false)] public string rendererPath { get; set; } = "";
            [VRCForgeInput("Required stable RendererComponentIdentity id.", IsRequired = false)] public string rendererComponentId { get; set; } = "";
            [VRCForgeInput("One zero-based sharedMaterials slot; required when assignments is absent.", IsRequired = false)] public int? slotIndex { get; set; }
            [VRCForgeInput("One persistent writable Assets/*.mat main asset; required when assignments is absent.", IsRequired = false)] public string newMaterialAssetPath { get; set; } = "";
            [VRCForgeInput("Exact active Unity project root.", IsRequired = true)] public string expectedProjectPath { get; set; } = "";
            [VRCForgeInput("Return all validated preconditions without mutation.", IsRequired = false)] public bool? preview { get; set; } = false;
            [VRCForgeInput("Preview scene path.", IsRequired = false)] public string expectedScenePath { get; set; } = "";
            [VRCForgeInput("Preview scene GUID.", IsRequired = false)] public string expectedSceneGuid { get; set; } = "";
            [VRCForgeInput("Preview scene handle.", IsRequired = false)] public int? expectedSceneHandle { get; set; }
            [VRCForgeInput("Preview scene file digest.", IsRequired = false)] public string expectedSceneFileDigest { get; set; } = "";
            [VRCForgeInput("Preview renderer component type.", IsRequired = false)] public string expectedRendererComponentType { get; set; } = "";
            [VRCForgeInput("Preview renderer component index.", IsRequired = false)] public int? expectedRendererComponentIndex { get; set; }
            [VRCForgeInput("Preview before material asset path.", IsRequired = false)] public string expectedBeforeMaterialAssetPath { get; set; } = "";
            [VRCForgeInput("Explicit preview evidence that the original slot is empty; omit for a nonempty slot.", IsRequired = false)] public bool? expectedBeforeMaterialIsNull { get; set; }
            [VRCForgeInput("Preview before material GUID.", IsRequired = false)] public string expectedBeforeMaterialGuid { get; set; } = "";
            [VRCForgeInput("Preview before material file digest.", IsRequired = false)] public string expectedBeforeMaterialFileDigest { get; set; } = "";
            [VRCForgeInput("Preview before material shader.", IsRequired = false)] public string expectedBeforeMaterialShader { get; set; } = "";
            [VRCForgeInput("Preview before material render queue.", IsRequired = false)] public int? expectedBeforeMaterialRenderQueue { get; set; }
            [VRCForgeInput("Preview new material GUID.", IsRequired = false)] public string expectedNewMaterialGuid { get; set; } = "";
            [VRCForgeInput("Preview new material file digest.", IsRequired = false)] public string expectedNewMaterialFileDigest { get; set; } = "";
        }

        internal sealed class Target
        {
            internal SavedSceneSnapshot Scene;
            internal Renderer Renderer;
            internal RendererComponentIdentityEvidence Identity;
        }

        public static object HandleCommand(JObject parameters)
        {
            if (parameters?["assignments"] != null) return UnityRendererMaterialSlotsBatch.HandleCommand(parameters);
            var mutationStarted = false;
            var undoGroup = -1;
            var beforeSlots = new string[0];
            var beforeSceneDigest = string.Empty;
            Target target = null;
            try
            {
                var path = Required(parameters, "rendererPath");
                var componentId = LowerHex(parameters, "rendererComponentId", 64, !IsPreview(parameters));
                var slot = RequiredInt(parameters, "slotIndex");
                var newPath = NormalizeMaterialPath(Required(parameters, "newMaterialAssetPath"));
                RequireProject(parameters);
                var scene = ResolveTarget(path, componentId, out target);
                beforeSceneDigest = scene.FileDigest;
                var materialEvidence = ReadMaterial(newPath, "new material");
                if (slot < 0 || slot >= target.Renderer.sharedMaterials.Length)
                    throw new InvalidOperationException("slotIndex is outside sharedMaterials.");
                var before = target.Renderer.sharedMaterials[slot];
                var beforeEvidence = before == null ? null : ReadMaterialAsset(before, "before material");
                var newMaterial = materialEvidence.Material;
                if (beforeEvidence != null && beforeEvidence.Path == materialEvidence.Path && beforeEvidence.Guid == materialEvidence.Guid)
                    throw new InvalidOperationException("The selected renderer slot already uses the requested material.");
                beforeSlots = target.Renderer.sharedMaterials.Select(MaterialIdentity).ToArray();
                if (!IsPreview(parameters))
                {
                    RequireApplyFields(parameters, target, scene, componentId, slot, before, beforeEvidence, materialEvidence);
                    var current = SceneObjectCopyCore.ResolveSavedScene(scene.Path, "renderer scene");
                    RequireEqual(current.FileDigest, parameters, "expectedSceneFileDigest");
                    Undo.IncrementCurrentGroup();
                    undoGroup = Undo.GetCurrentGroup();
                    Undo.SetCurrentGroupName("Set VRCForge renderer material slot");
                    Undo.RegisterCompleteObjectUndo(target.Renderer, "Set VRCForge renderer material slot");
                    mutationStarted = true;
                    var slots = target.Renderer.sharedMaterials;
                    slots[slot] = newMaterial;
                    target.Renderer.sharedMaterials = slots;
                    EditorSceneManager.MarkSceneDirty(scene.Scene);
                    if (!EditorSceneManager.SaveScene(scene.Scene))
                        throw new InvalidOperationException("The exact renderer scene could not be saved.");
                    AssetDatabase.Refresh(ImportAssetOptions.ForceSynchronousImport);
                    ResolveTarget(path, componentId, out target);
                    var persistedScene = SceneObjectCopyCore.ResolveSavedScene(scene.Path, "persisted renderer scene");
                    if (string.Equals(persistedScene.FileDigest, current.FileDigest, StringComparison.OrdinalIgnoreCase))
                        throw new InvalidOperationException("The saved scene digest did not change after the material-slot replacement.");
                    VerifySlots(target.Renderer, beforeSlots, slot, materialEvidence);
                    var receipt = Payload(target, slot, beforeEvidence, materialEvidence, true, true, true, true, true);
                    return VRCForgeToolResult.Completed("Renderer material slot replaced and persisted.", receipt);
                }
                return VRCForgeToolResult.Completed(
                    "Renderer material slot preconditions verified; no mutation performed.",
                    Payload(target, slot, beforeEvidence, materialEvidence, false, false, false, false, false));
            }
            catch (Exception exception)
            {
                if (mutationStarted)
                {
                    try
                    {
                        if (undoGroup >= 0) Undo.RevertAllDownToGroup(undoGroup);
                        if (target == null || target.Scene == null || !EditorSceneManager.SaveScene(target.Scene.Scene))
                            throw new InvalidOperationException("The restored renderer scene could not be saved.");
                        Target restoredTarget;
                        ResolveTarget(parameters?["rendererPath"]?.ToString() ?? "", parameters?["rendererComponentId"]?.ToString() ?? "", out restoredTarget);
                        if (restoredTarget.Renderer.sharedMaterials.Select(MaterialIdentity).SequenceEqual(beforeSlots)
                            && string.Equals(restoredTarget.Scene.FileDigest, beforeSceneDigest, StringComparison.OrdinalIgnoreCase))
                            return VRCForgeToolResult.Failed("Renderer material slot apply failed; Undo restored the verified pre-state.",
                                new { schema = ResultSchema, mutationStarted = true, applied = false, committed = false, sceneSaved = false, persistedReadback = false, restored = true });
                    }
                    catch { }
                }
                return VRCForgeToolResult.Failed(exception.Message,
                    new { schema = ResultSchema, mutationStarted, applied = false, committed = false, sceneSaved = false, persistedReadback = false, restored = mutationStarted ? (bool?)false : null });
            }
        }

        private static object Payload(Target target, int slot, MaterialEvidence before, MaterialEvidence next, bool started, bool applied, bool committed, bool saved, bool readback)
        {
            var scene = target.Scene;
            var identity = target.Identity;
            return new
            {
                schema = ResultSchema, operation = "set_renderer_material_slot", preview = !started,
                mutationStarted = started, applied, committed, sceneSaved = saved, persistedReadback = readback,
                projectPath = Directory.GetParent(Application.dataPath).FullName,
                rendererPath = identity.rendererPath, rendererComponentId = identity.componentId,
                rendererComponentType = identity.componentType, rendererComponentIndex = identity.componentIndex,
                scenePath = scene.Path, sceneGuid = scene.Guid, sceneHandle = scene.Handle,
                sceneFileDigest = scene.FileDigest, slotIndex = slot,
                beforeMaterial = MaterialPayload(before), newMaterial = MaterialPayload(next),
                newMaterialAssetPath = next.Path, newMaterialAssetGuid = next.Guid, newMaterialFileDigest = next.Digest,
                preconditions = new { exactScene = true, exactRendererIdentity = true, exactSlot = true,
                    expectedBeforeMaterial = before == null ? "null" : before.Path + "|" + before.Guid, exactNewPersistentMaterial = true,
                    hierarchyPathIsDisplayOnly = true }
            };
        }

        internal static object MaterialPayload(MaterialEvidence evidence)
        {
            if (evidence == null) return null;
            var material = evidence.Material;
            if (material == null) return null;
            return new { name = material.name, shader = material.shader == null ? "" : material.shader.name,
                renderQueue = material.renderQueue, assetPath = evidence.Path,
                assetGuid = evidence.Guid, fileDigest = evidence.Digest };
        }

        private static bool IsPreview(JObject p) { return p?["preview"]?.Value<bool?>() ?? false; }
        private static string Required(JObject p, string key) { var v = p?[key]?.ToString()?.Trim(); if (string.IsNullOrEmpty(v)) throw new InvalidOperationException(key + " is required."); return v; }
        private static int RequiredInt(JObject p, string key) { if (p?[key]?.Type != JTokenType.Integer) throw new InvalidOperationException(key + " is required."); return p[key].Value<int>(); }
        private static string LowerHex(JObject p, string key, int length, bool required) { var v = p?[key]?.ToString()?.Trim() ?? ""; if (required && (v.Length != length || v.Any(c => !Uri.IsHexDigit(c)))) throw new InvalidOperationException(key + " must be a lowercase hexadecimal identity."); return v.ToLowerInvariant(); }
        internal static void RequireProject(JObject p) { var expected = Required(p, "expectedProjectPath"); var actual = Path.GetFullPath(Directory.GetParent(Application.dataPath).FullName).TrimEnd('\\', '/'); if (!string.Equals(actual, Path.GetFullPath(expected).TrimEnd('\\', '/'), StringComparison.OrdinalIgnoreCase)) throw new InvalidOperationException("The Unity project no longer matches the verified preview."); }
        internal static string NormalizeMaterialPath(string raw) { var p = raw.Replace('\\', '/'); if (!p.StartsWith("Assets/", StringComparison.Ordinal) || !p.EndsWith(".mat", StringComparison.Ordinal) || p.Contains("..") || p.StartsWith("Packages/", StringComparison.Ordinal)) throw new InvalidOperationException("newMaterialAssetPath must be one main persistent Assets/*.mat asset."); return p; }
        internal static SavedSceneSnapshot ResolveTarget(string rendererPath, string componentId, out Target output)
        {
            var scene = SceneObjectCopyCore.ResolveSavedScene(FindScenePath(rendererPath), "renderer scene");
            var gameObject = SceneObjectCopyCore.ResolveUniqueGameObject(scene.Scene, rendererPath, "renderer path");
            var renderers = gameObject.GetComponents<Renderer>().Where(r => r != null).ToList();
            var matches = string.IsNullOrEmpty(componentId)
                ? renderers
                : renderers.Where(r => { try { return RendererComponentIdentity.Create(r).componentId == componentId; } catch { return false; } }).ToList();
            if (matches.Count != 1) throw new InvalidOperationException("The renderer path or stable component identity is missing, ambiguous, or drifted.");
            var renderer = matches[0];
            output = new Target { Scene = scene, Renderer = renderer, Identity = RendererComponentIdentity.Create(renderer) };
            return scene;
        }
        private static string FindScenePath(string rendererPath)
        {
            var matches = Enumerable.Range(0, SceneManager.sceneCount).Select(SceneManager.GetSceneAt).Where(s => s.IsValid() && s.isLoaded && s.path.Length > 0).Select(s => s.path).Distinct(StringComparer.Ordinal).ToList();
            var path = rendererPath ?? "";
            var root = path.Split('/')[0];
            var candidate = matches.FirstOrDefault(s => SceneManager.GetSceneByPath(s).GetRootGameObjects().Count(g => g.name == root) == 1);
            if (candidate == null || matches.Count(s => SceneManager.GetSceneByPath(s).GetRootGameObjects().Any(g => g.name == root)) != 1) throw new InvalidOperationException("rendererPath does not select exactly one loaded scene hierarchy.");
            return candidate;
        }
        internal static MaterialEvidence ReadMaterial(string path, string label)
        {
            var material = AssetDatabase.LoadAssetAtPath<Material>(path); if (material == null || !AssetDatabase.IsMainAsset(material) || !EditorUtility.IsPersistent(material)) throw new InvalidOperationException(label + " is not a persistent main material asset.");
            var full = Path.GetFullPath(Path.Combine(Directory.GetParent(Application.dataPath).FullName, path)); RejectReparse(full); if ((File.GetAttributes(full) & FileAttributes.ReadOnly) != 0) throw new InvalidOperationException(label + " is not writable.");
            return new MaterialEvidence { Material = material, Path = path, Guid = AssetDatabase.AssetPathToGUID(path), Digest = Sha256(File.ReadAllBytes(full)) };
        }
        internal static MaterialEvidence ReadMaterialAsset(Material m, string label) { var p = NormalizeMaterialPath(AssetDatabase.GetAssetPath(m)); return ReadMaterial(p, label); }
        internal static string MaterialIdentity(Material m) { if (m == null) return "null"; var p = AssetDatabase.GetAssetPath(m); return p + "|" + AssetDatabase.AssetPathToGUID(p); }
        private static string Sha256(byte[] bytes) { using (var sha = SHA256.Create()) return BitConverter.ToString(sha.ComputeHash(bytes)).Replace("-", "").ToLowerInvariant(); }
        private static void RejectReparse(string path) { var current = Directory.GetParent(Application.dataPath).FullName; foreach (var part in path.Substring(current.Length).TrimStart('\\', '/').Split('\\', '/')) { current = Path.Combine(current, part); if ((File.GetAttributes(current) & FileAttributes.ReparsePoint) != 0) throw new InvalidOperationException("A material path contains a reparse point."); } }
        private static void RequireEqual(string actual, JObject p, string key) { if (!string.Equals(actual, Required(p, key), StringComparison.OrdinalIgnoreCase)) throw new InvalidOperationException("The verified scene or material state drifted."); }
        private static void RequireApplyFields(JObject p, Target t, SavedSceneSnapshot s, string id, int slot, Material beforeMaterial, MaterialEvidence before, MaterialEvidence next)
        {
            var nullEvidence = p["expectedBeforeMaterialIsNull"];
            var expectsNull = nullEvidence?.Type == JTokenType.Boolean && nullEvidence.Value<bool>();
            var beforeMatches = before == null
                ? beforeMaterial == null && expectsNull && !new[] { "expectedBeforeMaterialAssetPath", "expectedBeforeMaterialGuid", "expectedBeforeMaterialFileDigest", "expectedBeforeMaterialShader", "expectedBeforeMaterialRenderQueue" }.Any(key => p.Property(key) != null)
                : nullEvidence == null && beforeMaterial != null && Required(p, "expectedBeforeMaterialAssetPath") == before.Path && Required(p, "expectedBeforeMaterialGuid") == before.Guid && Required(p, "expectedBeforeMaterialFileDigest") == before.Digest && Required(p, "expectedBeforeMaterialShader") == (beforeMaterial.shader == null ? "" : beforeMaterial.shader.name) && p["expectedBeforeMaterialRenderQueue"]?.Value<int>() == beforeMaterial.renderQueue;
            if (Required(p, "expectedScenePath").Replace('\\', '/') != s.Path || Required(p, "expectedSceneGuid") != s.Guid || p["expectedSceneHandle"]?.Value<int>() != s.Handle || Required(p, "expectedRendererComponentType") != t.Identity.componentType || p["expectedRendererComponentIndex"]?.Value<int>() != t.Identity.componentIndex || Required(p, "rendererComponentId") != id || p["slotIndex"]?.Value<int>() != slot || Required(p, "expectedNewMaterialGuid") != next.Guid || Required(p, "expectedNewMaterialFileDigest") != next.Digest || !beforeMatches) throw new InvalidOperationException("The current state no longer matches the verified preview.");
        }
        private static void VerifySlots(Renderer r, string[] before, int slot, MaterialEvidence next)
        {
            var after = r.sharedMaterials; if (after.Length != before.Length || MaterialIdentity(after[slot]) != next.Path + "|" + next.Guid || Enumerable.Range(0, before.Length).Any(i => i != slot && MaterialIdentity(after[i]) != before[i])) throw new InvalidOperationException("Renderer material slots did not persist exactly.");
        }
        internal sealed class MaterialEvidence { internal Material Material; internal string Path; internal string Guid; internal string Digest; }
    }
}
