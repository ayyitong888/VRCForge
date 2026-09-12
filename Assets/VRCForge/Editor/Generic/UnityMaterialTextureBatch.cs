using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Text;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEngine;
using VRCForge.Core.MCP;

namespace VRCForge.Editor
{
    // Generic asset-only batch primitive used by the existing material texture tool.
    internal static class UnityMaterialTextureBatch
    {
        private const string Schema = "vrcforge.material_texture_assignment.v1";
        private const int MaxBytes = 512 * 1024;

        private sealed class Item
        {
            internal string MaterialPath;
            internal string PropertyName;
            internal string TexturePath;
            internal Material Material;
            internal Texture TargetTexture;
            internal Texture BeforeTexture;
            internal MaterialShaderTool.MaterialAssetEvidence Evidence;
            internal byte[] BeforeBytes;
            internal string BeforeTexturePath;
            internal string BeforeTextureGuid;
            internal string TextureGuid = string.Empty;
            internal string TextureDigest;
            internal string PostDigest;
            internal bool MutationOwned;
            internal StableAssetEvidence Stable, Owned;
            internal string BeforeJson, ExpectedJson;
            internal long TextureLocalId;
            internal string ShaderDependency;
            internal bool HasTransform, HasScale, HasOffset;
            internal Vector2 BeforeScale, BeforeOffset, AfterScale, AfterOffset;
        }

        internal static JArray ValidateEnvelope(JObject request)
        {
            var allowed = new HashSet<string>(new[] { "assignments", "expectedProjectPath", "preview", "expectedBatchPlan" }, StringComparer.Ordinal);
            if (request.Properties().Any(p => !allowed.Contains(p.Name))) throw new InvalidOperationException("assignments cannot be combined with single-slot or unknown fields.");
            if (Encoding.UTF8.GetByteCount(request.ToString(Formatting.None)) > MaxBytes) throw new InvalidOperationException("Texture assignments exceed 512 KiB.");
            var rows = request["assignments"] as JArray;
            if (rows == null || rows.Count < 1 || rows.Count > 128) throw new InvalidOperationException("assignments must contain 1..128 entries.");
            var seen = new HashSet<string>(StringComparer.Ordinal);
            foreach (var token in rows)
            {
                var row = token as JObject;
                if (row == null || row.Properties().Any(p => !new[] { "materialAssetPath", "propertyName", "textureAssetPath", "textureScale", "textureOffset" }.Contains(p.Name))) throw new InvalidOperationException("Each assignment must contain exact material, property and texture fields.");
                var material = AssetPath(row, "materialAssetPath", true);
                var property = Text(row, "propertyName");
                AssetPath(row, "textureAssetPath", false);
                foreach (var key in new[] { "textureScale", "textureOffset" })
                    if (row.Property(key) != null) ReadVector(row[key], key);
                if (!seen.Add(material + "\n" + property)) throw new InvalidOperationException("Duplicate material/property assignment.");
            }
            return (JArray)rows.DeepClone();
        }

        private static Vector2 ReadVector(JToken token, string label)
        {
            var value = token as JObject;
            if (value == null || value.Count != 2 || value.Property("x") == null || value.Property("y") == null)
                throw new InvalidOperationException(label + " requires exactly numeric x and y.");
            var numbers = new float[2];
            for (var index = 0; index < 2; index++)
            {
                var part = value[index == 0 ? "x" : "y"];
                if (part.Type != JTokenType.Float && part.Type != JTokenType.Integer)
                    throw new InvalidOperationException(label + " must contain finite float32 values.");
                var number = part.Value<double>();
                if (double.IsNaN(number) || double.IsInfinity(number) || Math.Abs(number) > float.MaxValue)
                    throw new InvalidOperationException(label + " must contain finite float32 values.");
                numbers[index] = (float)number;
            }
            return new Vector2(numbers[0], numbers[1]);
        }

        private static JObject VectorValue(Vector2 value)
        {
            return new JObject { ["x"] = (double)value.x, ["y"] = (double)value.y };
        }

        private static bool SameVector(Vector2 a, Vector2 b) { return a.x == b.x && a.y == b.y; }

        private static JObject NormalizeSingleTransform(JObject request)
        {
            if (request.Property("assignments") != null) return request;
            var rowKeys = new[] { "materialAssetPath", "propertyName", "textureAssetPath", "textureScale", "textureOffset" };
            var allowed = rowKeys.Concat(new[] { "expectedProjectPath", "preview", "saveAssets", "expectedBatchPlan" }).ToArray();
            if (request.Properties().Any(p => !allowed.Contains(p.Name))) throw new InvalidOperationException("Single texture transform contains unknown or legacy preconditions; use its sealed batch preview plan.");
            if (request["preview"]?.Value<bool>() != true && request["saveAssets"]?.Value<bool>() == false)
                throw new InvalidOperationException("Texture transforms must be saved and independently read back.");
            var row = new JObject(); var normalized = new JObject();
            foreach (var property in request.Properties())
            {
                if (rowKeys.Contains(property.Name)) row[property.Name] = property.Value.DeepClone();
                else if (property.Name != "saveAssets") normalized[property.Name] = property.Value.DeepClone();
            }
            normalized["assignments"] = new JArray(row);
            return normalized;
        }

        private static string Text(JObject row, string key)
        {
            if (row[key]?.Type != JTokenType.String || string.IsNullOrWhiteSpace(row[key].Value<string>())) throw new InvalidOperationException(key + " is required.");
            return row[key].Value<string>().Trim();
        }

        private static string AssetPath(JObject row, string key, bool material, bool allowEmpty = false)
        {
            var value = row[key]?.Type == JTokenType.String ? row[key].Value<string>().Replace('\\', '/').Trim() : string.Empty;
            if (allowEmpty && value.Length == 0) return string.Empty;
            if (!value.StartsWith("Assets/", StringComparison.Ordinal) || value.Split('/').Any(p => p == "" || p == "." || p == "..") || (material && !value.EndsWith(".mat", StringComparison.OrdinalIgnoreCase))) throw new InvalidOperationException(key + " must be an exact project Assets path.");
            return value;
        }

        private static string TextureFile(string path)
        {
            var full = Path.GetFullPath(Path.Combine(Application.dataPath, "..", path.Replace('/', Path.DirectorySeparatorChar)));
            var root = Path.GetFullPath(Application.dataPath).TrimEnd(Path.DirectorySeparatorChar) + Path.DirectorySeparatorChar;
            if (!full.StartsWith(root, StringComparison.OrdinalIgnoreCase) || !File.Exists(full)) throw new InvalidOperationException("Texture source is missing or outside Assets.");
            MaterialShaderTool.EnsureNoReparseBoundary(Path.GetFullPath(Application.dataPath), full);
            return full;
        }

        private static Item BuildItem(JObject row)
        {
            var item = new Item { MaterialPath = AssetPath(row, "materialAssetPath", true), PropertyName = Text(row, "propertyName"), TexturePath = AssetPath(row, "textureAssetPath", false) };
            item.Material = AssetDatabase.LoadAssetAtPath<Material>(item.MaterialPath);
            if (item.Material == null) throw new InvalidOperationException("The requested persistent material asset does not exist.");
            if (!item.Material.HasProperty(item.PropertyName) || !item.Material.GetTexturePropertyNames().Contains(item.PropertyName, StringComparer.Ordinal)) throw new InvalidOperationException("The material does not expose the requested texture property.");
            item.Evidence = MaterialShaderTool.InspectWritableMaterialAsset(item.Material);
            if (item.Material.isVariant || item.Material.parent != null) throw new InvalidOperationException("Batch targets must be independent materials; flatten variants first.");
            item.Stable = SceneObjectCopyCore.ReadStableAssetEvidence(item.MaterialPath, "texture batch target");
            item.BeforeJson = EditorJsonUtility.ToJson(item.Material);
            if (item.Material.shader == null) throw new InvalidOperationException("Material shader is missing.");
            item.ShaderDependency = AssetDatabase.GetAssetDependencyHash(AssetDatabase.GetAssetPath(item.Material.shader)).ToString();
            item.BeforeBytes = File.ReadAllBytes(item.Evidence.filePath);
            item.BeforeTexture = item.Material.GetTexture(item.PropertyName);
            item.HasScale = row.Property("textureScale") != null;
            item.HasOffset = row.Property("textureOffset") != null;
            item.HasTransform = item.HasScale || item.HasOffset;
            if (item.HasTransform)
            {
                item.BeforeScale = item.Material.GetTextureScale(item.PropertyName);
                item.BeforeOffset = item.Material.GetTextureOffset(item.PropertyName);
                item.AfterScale = item.HasScale ? ReadVector(row["textureScale"], "textureScale") : item.BeforeScale;
                item.AfterOffset = item.HasOffset ? ReadVector(row["textureOffset"], "textureOffset") : item.BeforeOffset;
                ReadVector(VectorValue(item.BeforeScale), "beforeTextureScale");
                ReadVector(VectorValue(item.BeforeOffset), "beforeTextureOffset");
            }
            item.BeforeTexturePath = item.BeforeTexture == null ? string.Empty : (AssetDatabase.GetAssetPath(item.BeforeTexture) ?? string.Empty).Replace('\\', '/');
            item.BeforeTextureGuid = string.IsNullOrEmpty(item.BeforeTexturePath) ? string.Empty : AssetDatabase.AssetPathToGUID(item.BeforeTexturePath).ToLowerInvariant();
            if (!string.IsNullOrEmpty(item.TexturePath))
            {
                item.TargetTexture = AssetDatabase.LoadAssetAtPath<Texture2D>(item.TexturePath);
                if (item.TargetTexture == null) throw new InvalidOperationException("The requested source must be one existing project Texture2D.");
                if (!AssetDatabase.TryGetGUIDAndLocalFileIdentifier(item.TargetTexture, out string textureGuid, out long textureLocalId)) throw new InvalidOperationException("Texture identity unavailable.");
                item.TextureGuid = textureGuid.ToLowerInvariant(); item.TextureLocalId = textureLocalId;
                item.TextureDigest = MaterialShaderTool.ComputeFileSha256(TextureFile(item.TexturePath));
            }
            return item;
        }

        private static string Hash(string text) { using (var h = System.Security.Cryptography.SHA256.Create()) return BitConverter.ToString(h.ComputeHash(Encoding.UTF8.GetBytes(text))).Replace("-", "").ToLowerInvariant(); }

        private static JObject Evidence(Item item)
        {
            var result = JObject.FromObject(new { materialAssetPath = item.MaterialPath, materialAssetGuid = item.Evidence.assetGuid, materialFileDigestBefore = item.Evidence.fileDigest, materialMetaDigest = item.Stable.Meta.Digest, materialStateDigest = Hash(item.BeforeJson), shaderDependencyHash = item.ShaderDependency, textureLocalId = item.TextureLocalId, propertyName = item.PropertyName, beforeTextureAssetPath = item.BeforeTexturePath, beforeTextureAssetGuid = item.BeforeTextureGuid, textureAssetPath = item.TexturePath, textureAssetGuid = item.TextureGuid, textureFileDigest = item.TextureDigest, afterTextureAssetPath = item.TexturePath, afterTextureAssetGuid = item.TextureGuid });
            if (item.HasTransform)
            {
                if (item.HasScale) result["textureScale"] = VectorValue(item.AfterScale);
                if (item.HasOffset) result["textureOffset"] = VectorValue(item.AfterOffset);
                result["beforeTextureScale"] = VectorValue(item.BeforeScale);
                result["beforeTextureOffset"] = VectorValue(item.BeforeOffset);
                result["afterTextureScale"] = VectorValue(item.AfterScale);
                result["afterTextureOffset"] = VectorValue(item.AfterOffset);
            }
            return result;
        }

        private static List<Item> Build(JArray rows, out JArray evidence)
        {
            var items = rows.OfType<JObject>().Select(BuildItem).ToList();
            evidence = new JArray(items.Select(Evidence));
            return items;
        }

        internal static object HandleCommand(JObject request)
        {
            List<Item> items = null;
            var started = false;
            try
            {
                request = NormalizeSingleTransform(request);
                var rows = ValidateEnvelope(request);
                if (!MaterialShaderTool.MatchesCurrentProject(request["expectedProjectPath"]?.ToString() ?? string.Empty)) throw new InvalidOperationException("The selected Unity project does not match the active Editor.");
                JArray planRows;
                items = Build(rows, out planRows);
                var plan = new JObject { ["assignments"] = planRows };
                var sealedRequest = (JObject)request.DeepClone(); sealedRequest["expectedBatchPlan"] = plan;
                if (Encoding.UTF8.GetByteCount(sealedRequest.ToString(Formatting.None)) > MaxBytes) throw new InvalidOperationException("Sealed texture assignments exceed 512 KiB; split the batch.");
                if (request["preview"]?.Value<bool>() == true) return VRCForgeToolResult.Completed("Material texture assignments fully preflighted; no mutation.", new { schema = Schema, batch = true, ok = true, preview = true, verified = true, changed = false, saved = false, persistedReadback = false, mutationStarted = false, committed = false, commitState = "not_started", projectPath = Path.GetFullPath(Path.Combine(Application.dataPath, "..")), assignments = planRows });
                if (!(request["expectedBatchPlan"] is JObject) || !JToken.DeepEquals(request["expectedBatchPlan"], plan)) throw new InvalidOperationException("A sealed expectedBatchPlan from preview is required.");
                var fresh = Build(rows, out var freshRows);
                if (!JToken.DeepEquals(planRows, freshRows)) throw new InvalidOperationException("Texture assignment preconditions changed since preview.");
                items = fresh;
                var changed = false;
                foreach (var item in items)
                {
                    if (item.Material.GetTexture(item.PropertyName) == item.TargetTexture
                        && (!item.HasTransform || (SameVector(item.BeforeScale, item.AfterScale) && SameVector(item.BeforeOffset, item.AfterOffset)))) continue;
                    // Mark before the setter so failures still restore the affected material.
                    started = true; item.MutationOwned = true; changed = true;
                    try
                    {
                        if (item.Material.GetTexture(item.PropertyName) != item.TargetTexture) item.Material.SetTexture(item.PropertyName, item.TargetTexture);
                        if (item.HasScale) item.Material.SetTextureScale(item.PropertyName, item.AfterScale);
                        if (item.HasOffset) item.Material.SetTextureOffset(item.PropertyName, item.AfterOffset);
                        EditorUtility.SetDirty(item.Material);
                    }
                    finally { foreach (var same in items.Where(i => i.MaterialPath == item.MaterialPath)) same.ExpectedJson = EditorJsonUtility.ToJson(item.Material); }
                }
                foreach (var group in items.GroupBy(i => i.MaterialPath))
                {
                    var item = group.First();
                    foreach (var same in group) same.ExpectedJson = EditorJsonUtility.ToJson(item.Material);
                    if (!group.Any(i => i.MutationOwned)) continue;
                    AssetDatabase.SaveAssetIfDirty(item.Material);
                    var owned = SceneObjectCopyCore.ReadStableAssetEvidence(item.MaterialPath, "saved texture batch ownership");
                    foreach (var same in group) same.Owned = owned;
                    if (EditorUtility.IsDirty(item.Material)) throw new InvalidOperationException("Saved material remained dirty.");
                }
                foreach (var group in items.GroupBy(i => i.MaterialPath)) AssetDatabase.ImportAsset(group.Key, ImportAssetOptions.ForceSynchronousImport | ImportAssetOptions.ForceUpdate);
                var readback = new JArray();
                foreach (var item in items)
                {
                    var persisted = AssetDatabase.LoadAssetAtPath<Material>(item.MaterialPath);
                    var stable = SceneObjectCopyCore.ReadStableAssetEvidence(item.MaterialPath, "texture batch readback");
                    if (persisted == null || EditorUtility.IsDirty(persisted) || EditorJsonUtility.ToJson(persisted) != item.ExpectedJson || stable.Guid != item.Stable.Guid || stable.Meta.Digest != item.Stable.Meta.Digest)
                        throw new InvalidOperationException("Material persisted state or identity differs from approved texture edit.");
                    var actual = persisted.GetTexture(item.PropertyName);
                    if (actual == null || AssetDatabase.GetAssetPath(actual) != item.TexturePath || !AssetDatabase.TryGetGUIDAndLocalFileIdentifier(actual, out string guid, out long localId) || guid != item.TextureGuid || localId != item.TextureLocalId
                        || MaterialShaderTool.ComputeFileSha256(TextureFile(item.TexturePath)) != item.TextureDigest || AssetDatabase.GetAssetDependencyHash(AssetDatabase.GetAssetPath(persisted.shader)).ToString() != item.ShaderDependency)
                        throw new InvalidOperationException("Texture or shader changed during material assignment.");
                    var resultRow = JObject.FromObject(new { materialAssetPath = item.MaterialPath, materialAssetGuid = stable.Guid, materialMetaDigest = stable.Meta.Digest, propertyName = item.PropertyName, afterTextureAssetPath = item.TexturePath, afterTextureAssetGuid = guid, textureLocalId = localId, textureFileDigest = item.TextureDigest, materialFileDigestAfter = stable.File.Digest, materialStateDigest = Hash(item.ExpectedJson) });
                    if (item.HasTransform)
                    {
                        var scale = persisted.GetTextureScale(item.PropertyName); var offset = persisted.GetTextureOffset(item.PropertyName);
                        if (!SameVector(scale, item.AfterScale) || !SameVector(offset, item.AfterOffset)) throw new InvalidOperationException("Persisted texture transform differs from approved assignment.");
                        resultRow["afterTextureScale"] = VectorValue(scale);
                        resultRow["afterTextureOffset"] = VectorValue(offset);
                    }
                    readback.Add(resultRow);
                }
                return VRCForgeToolResult.Completed("Material texture assignments saved and independently read back.", new { schema = Schema, batch = true, ok = true, preview = false, verified = true, changed, saved = changed, persistedReadback = true, mutationStarted = started, committed = true, commitState = changed ? "committed" : "no_change", projectPath = Path.GetFullPath(Path.Combine(Application.dataPath, "..")), assignments = planRows, readback });
            }
            catch (Exception exception)
            {
                var restored = true;
                if (started && items != null) foreach (var group in items.GroupBy(i => i.MaterialPath).Reverse())
                {
                    if (!group.Any(i => i.MutationOwned)) continue;
                    var item = group.First();
                    try
                    {
                        var current = SceneObjectCopyCore.ReadStableAssetEvidence(item.MaterialPath, "texture batch rollback ownership");
                        var owned = item.Owned ?? item.Stable;
                        if (current.Guid != owned.Guid || current.Meta.Digest != owned.Meta.Digest || current.File.Digest != owned.File.Digest || EditorJsonUtility.ToJson(item.Material) != item.ExpectedJson) { restored = false; continue; }
                        if (current.File.Digest != item.Stable.File.Digest)
                        {
                            var temp = item.Evidence.filePath + ".vrcforge-restore-" + Guid.NewGuid().ToString("N") + ".tmp";
                            try { File.WriteAllBytes(temp, item.BeforeBytes); File.Replace(temp, item.Evidence.filePath, null); }
                            finally { if (File.Exists(temp)) File.Delete(temp); }
                        }
                        // Even unchanged disk must reload to undo a mutation which failed before saving.
                        AssetDatabase.ImportAsset(item.MaterialPath, ImportAssetOptions.ForceSynchronousImport | ImportAssetOptions.ForceUpdate);
                        var final = SceneObjectCopyCore.ReadStableAssetEvidence(item.MaterialPath, "restored texture batch");
                        var reloaded = AssetDatabase.LoadAssetAtPath<Material>(item.MaterialPath);
                        if (reloaded == null || EditorUtility.IsDirty(reloaded) || EditorJsonUtility.ToJson(reloaded) != item.BeforeJson || final.Guid != item.Stable.Guid || final.Meta.Digest != item.Stable.Meta.Digest || final.File.Digest != item.Stable.File.Digest) restored = false;
                    }
                    catch { restored = false; }
                }
                return VRCForgeToolResult.FailedWithCode("material_texture_batch_failed", exception.Message, new { schema = Schema, batch = true, verified = false, mutationStarted = started, committed = false, commitState = started ? (restored ? "rolled_back" : "unknown") : "not_started", commitStateKnown = !started || restored, restored, checkpointRecoveryRequired = started && !restored });
            }
        }
    }
}

