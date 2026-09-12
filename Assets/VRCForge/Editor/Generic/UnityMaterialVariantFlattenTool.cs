using System;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;
using System.Security.Cryptography;
using System.Text;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEngine;
using UnityEngine.Rendering;
using VRCForge.Core.MCP;

namespace VRCForge.Editor
{
    [VRCForgeCommand(
        toolId: "vrc_flatten_material_variant",
        Summary = "when-to-use: Preview or flatten one exact persistent Material Variant while preserving its effective values. when-NOT-to-use: Do not use for shader assignment, reparenting, material creation, batches, or non-persistent materials.",
        Access = VRCForgeCommandAccess.RequiresApproval)]
    public static class UnityMaterialVariantFlattenTool
    {
        public const string ToolName = "vrc_flatten_material_variant";

        public sealed class Parameters
        {
            [VRCForgeInput("Exact project-relative Material asset path.", IsRequired = true)] public string assetPath { get; set; } = "";
            [VRCForgeInput("Expected current Material GUID from preview.", IsRequired = false)] public string expectedGuid { get; set; } = "";
            [VRCForgeInput("Expected current Material dependency hash from preview.", IsRequired = false)] public string expectedDependencyHash { get; set; } = "";
            [VRCForgeInput("Expected current Material file digest from preview.", IsRequired = false)] public string expectedFileDigest { get; set; } = "";
            [VRCForgeInput("Return the verified plan without mutation.", IsRequired = false)] public bool? preview { get; set; } = true;
        }

        public static object HandleCommand(JObject @params)
        {
            Parameters p;
            try { p = (@params ?? new JObject()).ToObject<Parameters>() ?? new Parameters(); }
            catch (Exception ex) { return VRCForgeToolResult.Failed("Invalid material flatten input: " + ex.Message); }
            var path = (p.assetPath ?? "").Replace("\\", "/").Trim();
            var preview = p.preview ?? true;
            Material target = null;
            StableAssetEvidence evidence = null;
            byte[] originalBytes = null;
            var mutationStarted = false;
            var undoGroup = -1;
            MaterialSnapshot before = null;
            try
            {
                if (string.IsNullOrWhiteSpace(path) || !path.StartsWith("Assets/", StringComparison.OrdinalIgnoreCase) || !path.EndsWith(".mat", StringComparison.OrdinalIgnoreCase))
                    throw new InvalidOperationException("assetPath must be one exact project Material path.");
                SceneObjectCopyCore.ToAbsoluteAssetPath(path);
                target = AssetDatabase.LoadAssetAtPath<Material>(path);
                if (target == null) throw new InvalidOperationException("No persistent Material exists at assetPath.");
                var guid = AssetDatabase.AssetPathToGUID(path);
                var dependencyHash = AssetDatabase.GetAssetDependencyHash(path).ToString();
                evidence = SceneObjectCopyCore.ReadStableAssetEvidence(path, "material variant flatten");
                originalBytes = System.IO.File.ReadAllBytes(SceneObjectCopyCore.ToAbsoluteAssetPath(path));
                before = Snapshot(target);
                var parent = target.parent;
                for (var chain = target; chain != null; chain = chain.parent)
                {
                    if (EditorUtility.IsDirty(chain))
                        throw new InvalidOperationException("Material or an ancestor Variant is dirty; save it before preview or apply.");
                    if (chain.isVariant && chain.parent == null)
                        throw new InvalidOperationException("Material Variant ancestor has no resolvable parent; refusing to flatten.");
                }
                var isVariant = target.isVariant;
                if (isVariant && parent == null)
                    throw new InvalidOperationException("Material Variant has no resolvable parent; refusing to flatten.");
                if (!preview && (string.IsNullOrWhiteSpace(p.expectedGuid) || string.IsNullOrWhiteSpace(p.expectedDependencyHash) || string.IsNullOrWhiteSpace(p.expectedFileDigest)))
                    throw new InvalidOperationException("Verified GUID, dependency hash, and file digest are required for apply.");
                if (!string.IsNullOrWhiteSpace(p.expectedGuid) && !string.Equals(guid, p.expectedGuid.Trim(), StringComparison.OrdinalIgnoreCase))
                    throw new InvalidOperationException("Material GUID changed after preview.");
                if (!string.IsNullOrWhiteSpace(p.expectedDependencyHash) && !string.Equals(dependencyHash, p.expectedDependencyHash.Trim(), StringComparison.Ordinal))
                    throw new InvalidOperationException("Material dependency hash changed after preview.");
                if (!string.IsNullOrWhiteSpace(p.expectedFileDigest) && !string.Equals(evidence.File.Digest, p.expectedFileDigest.Trim(), StringComparison.OrdinalIgnoreCase))
                    throw new InvalidOperationException("Material file changed after preview.");
                if (!isVariant && parent == null)
                    return VRCForgeToolResult.Completed("Material is already independent; no change was required.",
                        Payload(path, guid, dependencyHash, evidence.File.Digest, before, false, false, "not_started", "not_applicable", "not_required", "no_change"));
                if (preview)
                    return VRCForgeToolResult.Completed("Material Variant flatten preview verified; no files were changed.",
                        Payload(path, guid, dependencyHash, evidence.File.Digest, before, true, false, "not_started", "not_applicable", "not_required", "preview"));

                Undo.IncrementCurrentGroup();
                undoGroup = Undo.GetCurrentGroup();
                Undo.SetCurrentGroupName("Flatten VRCForge material variant");
                Undo.RegisterCompleteObjectUndo(target, "Flatten VRCForge material variant");
                mutationStarted = true;
                target.parent = null;
                EditorUtility.SetDirty(target);
                AssetDatabase.SaveAssetIfDirty(target);
                if (EditorUtility.IsDirty(target)) throw new InvalidOperationException("Material remained dirty after save.");
                AssetDatabase.ImportAsset(path, ImportAssetOptions.ForceSynchronousImport);
                var readback = AssetDatabase.LoadAssetAtPath<Material>(path);
                var after = Snapshot(readback);
                if (readback == null || readback.parent != null || readback.isVariant || !EquivalentEffectiveState(before, after))
                    throw new InvalidOperationException("Flatten persisted readback did not preserve effective Material state.");
                if (!ParentChainUnchanged(before.ParentChain)) throw new InvalidOperationException("A Material Variant parent chain changed during flatten.");
                var readbackGuid = AssetDatabase.AssetPathToGUID(path);
                if (!string.Equals(readbackGuid, guid, StringComparison.OrdinalIgnoreCase))
                    throw new InvalidOperationException("Material GUID changed during flatten.");
                var savedEvidence = SceneObjectCopyCore.ReadStableAssetEvidence(path, "material variant flatten readback");
                if (!string.Equals(savedEvidence.Guid, guid, StringComparison.OrdinalIgnoreCase)
                    || !string.Equals(savedEvidence.Meta.Digest, evidence.Meta.Digest, StringComparison.OrdinalIgnoreCase))
                    throw new InvalidOperationException("Material identity or meta changed during flatten.");
                Undo.CollapseUndoOperations(undoGroup);
                return VRCForgeToolResult.Completed("Material Variant flattened and verified.",
                    Payload(path, readbackGuid, ComputeDependencyHash(path), savedEvidence.File.Digest, before, after, false, true, "committed", "persisted", "verified", "flattened"));
            }
            catch (Exception ex)
            {
                if (!mutationStarted)
                    return VRCForgeToolResult.Failed("Material Variant flatten rejected: " + ex.Message,
                        new { mutationStarted = false, committed = false, commitState = "not_started", checkpointRecoveryRequired = false });
                var restored = false;
                try
                {
                    Undo.FlushUndoRecordObjects();
                    Undo.RevertAllDownToGroup(undoGroup);
                    var absolutePath = SceneObjectCopyCore.ToAbsoluteAssetPath(path);
                    var tempPath = absolutePath + ".vrcforge-restore-" + Guid.NewGuid().ToString("N") + ".tmp";
                    System.IO.File.WriteAllBytes(tempPath, originalBytes);
                    System.IO.File.Replace(tempPath, absolutePath, null);
                    AssetDatabase.ImportAsset(path, ImportAssetOptions.ForceSynchronousImport);
                    var readback = AssetDatabase.LoadAssetAtPath<Material>(path);
                    var restoredSnapshot = readback == null ? null : Snapshot(readback);
                    var restoredEvidence = SceneObjectCopyCore.ReadStableAssetEvidence(path, "material flatten restore");
                    restored = readback != null && EquivalentEffectiveState(before, restoredSnapshot)
                        && readback.isVariant == before.IsVariant
                        && string.Equals(restoredSnapshot.Parent, before.Parent, StringComparison.Ordinal)
                        && string.Equals(restoredSnapshot.ParentGuid, before.ParentGuid, StringComparison.OrdinalIgnoreCase)
                        && ParentChainUnchanged(before.ParentChain)
                        && string.Equals(AssetDatabase.AssetPathToGUID(path), evidence.Guid, StringComparison.OrdinalIgnoreCase)
                        && string.Equals(restoredEvidence.Guid, evidence.Guid, StringComparison.OrdinalIgnoreCase)
                        && string.Equals(restoredEvidence.Meta.Digest, evidence.Meta.Digest, StringComparison.OrdinalIgnoreCase)
                        && string.Equals(restoredEvidence.File.Digest, evidence.File.Digest, StringComparison.OrdinalIgnoreCase);
                    if (System.IO.File.Exists(tempPath)) System.IO.File.Delete(tempPath);
                }
                catch { restored = false; }
                return VRCForgeToolResult.Failed("Material Variant flatten failed: " + ex.Message, new
                {
                    mutationStarted = true, mutationApplied = true, committed = restored ? (bool?)false : null,
                    commitState = restored ? "rolled_back" : "unknown", checkpointRecoveryRequired = !restored,
                    restored, cleanupVerified = restored
                });
            }
        }

        private static string ComputeDependencyHash(string path) { return AssetDatabase.GetAssetDependencyHash(path).ToString(); }

        private static JObject Payload(string path, string guid, string dependencyHash, string fileDigest, MaterialSnapshot before, bool preview, bool committed, string commitState, string persistenceState, string readbackState, string state)
        { return Payload(path, guid, dependencyHash, fileDigest, before, before, preview, committed, commitState, persistenceState, readbackState, state); }

        private static JObject Payload(string path, string guid, string dependencyHash, string fileDigest, MaterialSnapshot before, MaterialSnapshot after, bool preview, bool committed, string commitState, string persistenceState, string readbackState, string state)
        {
            return new JObject { ["schema"] = "vrcforge.material_variant_flatten.v1", ["assetPath"] = path, ["guid"] = guid, ["dependencyHash"] = dependencyHash, ["fileDigest"] = fileDigest,
                ["state"] = state, ["preview"] = preview, ["before"] = before.Data, ["after"] = after.Data,
                ["beforeParent"] = before.Parent, ["parent"] = after.Parent, ["isVariant"] = after.IsVariant,
                ["readback"] = readbackState == "verified" ? new JObject {
                    ["schema"] = "vrcforge.material_variant_flatten.readback.v1", ["verified"] = true,
                    ["assetPath"] = path, ["guid"] = guid, ["dependencyHash"] = dependencyHash, ["fileDigest"] = fileDigest,
                    ["parent"] = after.Parent, ["isVariant"] = after.IsVariant, ["effectiveState"] = after.Data.DeepClone() } : null,
                ["mutationStarted"] = !preview && committed, ["committed"] = committed, ["commitState"] = commitState,
                ["persistenceState"] = persistenceState, ["readbackState"] = readbackState, ["persistedReadback"] = readbackState == "verified", ["verified"] = readbackState == "verified", ["pending"] = false };
        }

        private sealed class MaterialSnapshot
        {
            internal JObject Data;
            internal string Parent;
            internal string ParentGuid;
            internal bool IsVariant;
            internal JArray ParentChain;
        }

        private static MaterialSnapshot Snapshot(Material material)
        {
            if (material == null) throw new InvalidOperationException("Material readback is unavailable.");
            var shaderPath = material.shader == null ? "" : AssetDatabase.GetAssetPath(material.shader);
            var data = new JObject { ["shader"] = material.shader != null ? material.shader.name : "", ["shaderPath"] = shaderPath,
                ["shaderGuid"] = string.IsNullOrEmpty(shaderPath) ? "" : AssetDatabase.AssetPathToGUID(shaderPath), ["renderQueue"] = material.renderQueue,
                ["enableInstancing"] = material.enableInstancing, ["doubleSidedGI"] = material.doubleSidedGI,
                ["globalIlluminationFlags"] = (int)material.globalIlluminationFlags,
                ["keywords"] = new JArray(material.shaderKeywords.OrderBy(x => x, StringComparer.Ordinal).ToArray()) };
            var props = new JArray();
            if (material.shader != null)
                for (var i = 0; i < material.shader.GetPropertyCount(); i++)
                {
                    var name = material.shader.GetPropertyName(i); var type = material.shader.GetPropertyType(i);
                    var row = new JObject { ["name"] = name, ["type"] = type.ToString() };
                    if (type == ShaderPropertyType.Float || type == ShaderPropertyType.Range) row["value"] = material.GetFloat(name);
                    else if (type == ShaderPropertyType.Int) row["value"] = material.GetInteger(name);
                    else if (type == ShaderPropertyType.Color) { var v = material.GetColor(name); row["value"] = new JArray(v.r, v.g, v.b, v.a); }
                    else if (type == ShaderPropertyType.Vector) { var v = material.GetVector(name); row["value"] = new JArray(v.x, v.y, v.z, v.w); }
                    else if (type == ShaderPropertyType.Texture)
                    {
                        var texture = material.GetTexture(name); var texturePath = texture == null ? "" : AssetDatabase.GetAssetPath(texture);
                        var scale = material.GetTextureScale(name); var offset = material.GetTextureOffset(name);
                        row["texture"] = texturePath; row["textureInstanceId"] = texture == null ? 0 : texture.GetInstanceID();
                        row["textureGuid"] = string.IsNullOrEmpty(texturePath) ? "" : AssetDatabase.AssetPathToGUID(texturePath);
                        row["scale"] = new JArray(scale.x, scale.y); row["offset"] = new JArray(offset.x, offset.y);
                    }
                    props.Add(row);
                }
            data["properties"] = props;
            var chain = new JArray();
            for (var parent = material.parent; parent != null; parent = parent.parent)
            {
                var parentPath = AssetDatabase.GetAssetPath(parent);
                if (!string.IsNullOrEmpty(parentPath) && System.IO.File.Exists(SceneObjectCopyCore.ToAbsoluteAssetPath(parentPath)))
                {
                    var parentEvidence = SceneObjectCopyCore.ReadStableAssetEvidence(parentPath, "material variant parent chain");
                    chain.Add(new JObject { ["path"] = parentPath, ["guid"] = AssetDatabase.AssetPathToGUID(parentPath), ["dependencyHash"] = AssetDatabase.GetAssetDependencyHash(parentPath).ToString(), ["fileDigest"] = parentEvidence.File.Digest, ["metaDigest"] = parentEvidence.Meta.Digest });
                }
            }
            return new MaterialSnapshot { Data = data, Parent = material.parent == null ? "" : AssetDatabase.GetAssetPath(material.parent), ParentGuid = material.parent == null ? "" : AssetDatabase.AssetPathToGUID(AssetDatabase.GetAssetPath(material.parent)), IsVariant = material.isVariant, ParentChain = chain };
        }

        private static bool ParentChainUnchanged(JArray chain)
        { foreach (var item in chain ?? new JArray()) { var path = item["path"]?.ToString(); if (string.IsNullOrEmpty(path)) return false; var e = SceneObjectCopyCore.ReadStableAssetEvidence(path, "material variant parent readback"); if (e.Guid != item["guid"]?.ToString() || e.File.Digest != item["fileDigest"]?.ToString() || e.Meta.Digest != item["metaDigest"]?.ToString() || AssetDatabase.GetAssetDependencyHash(path).ToString() != item["dependencyHash"]?.ToString()) return false; } return true; }

        private static bool EquivalentEffectiveState(MaterialSnapshot left, MaterialSnapshot right)
        { return left != null && right != null && JToken.DeepEquals(left.Data, right.Data); }
    }
}
