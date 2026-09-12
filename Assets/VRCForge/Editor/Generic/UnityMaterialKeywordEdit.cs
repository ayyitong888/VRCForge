using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEngine;
using UnityEngine.Rendering;
using VRCForge.Core.MCP;

namespace VRCForge.Editor
{
    // Keyword-only mode of vrc_set_material_shader; not a separate command.
    public static class UnityMaterialKeywordEdit
    {
        public static object HandleCommand(JObject args)
        {
            string path = null, restorePath = null;
            JObject before = null;
            byte[] original = null;
            StableAssetEvidence owned = null;
            var started = false;
            var group = -1;
            try
            {
                foreach (var key in new[] { "assignments", "shaderName", "shaderAssetPath", "rendererPath", "rendererComponentId", "slotIndex", "targetShader", "propertyChanges", "renderQueue" })
                    if (args[key] != null) throw new InvalidOperationException("Keyword editing cannot be combined with " + key + ".");
                if (!MaterialShaderTool.MatchesCurrentProject(args["expectedProjectPath"]?.ToString()))
                    throw new InvalidOperationException("Expected project does not match the active Unity project.");
                path = args["materialAssetPath"]?.ToString();
                if (string.IsNullOrEmpty(path) || !path.StartsWith("Assets/", StringComparison.Ordinal) || !path.EndsWith(".mat", StringComparison.Ordinal) || path.Contains("\\") || path.Contains(":") || path.Split('/').Any(p => p == "." || p == ".." || p == ""))
                    throw new InvalidOperationException("One exact Assets/... .mat path is required.");
                var absolute = SceneObjectCopyCore.ToAbsoluteAssetPath(path);
                var target = AssetDatabase.LoadAssetAtPath<Material>(path);
                if (target == null || AssetDatabase.GetAssetPath(target) != path) throw new InvalidOperationException("Persistent material target was not found.");
                var visited = new HashSet<Material>();
                for (var chain = target; chain != null; chain = chain.parent)
                    if (!visited.Add(chain) || EditorUtility.IsDirty(chain) || (chain.isVariant && chain.parent == null))
                        throw new InvalidOperationException("Material or ancestor is dirty or has an unresolved Variant chain.");
                if (target.isVariant || target.parent != null) throw new InvalidOperationException("Flatten the Material Variant before editing keywords.");
                if (target.shader == null) throw new InvalidOperationException("Material has no shader.");
                var rows = args["keywordChanges"] as JArray;
                if (rows == null || rows.Count < 1 || rows.Count > 128) throw new InvalidOperationException("keywordChanges requires 1..128 edits.");
                var names = new HashSet<string>(StringComparer.Ordinal);
                foreach (var token in rows)
                {
                    var row = token as JObject;
                    var name = row?["keyword"]?.Type == JTokenType.String ? row["keyword"].ToString() : null;
                    if (row == null || row.Count != 2 || string.IsNullOrEmpty(name) || name.Length > 256 || name.Trim() != name || row["enabled"]?.Type != JTokenType.Boolean || !names.Add(name))
                        throw new InvalidOperationException("Each keyword must have a unique exact name and boolean enabled value.");
                    if (!target.shader.keywordSpace.FindKeyword(name).isValid) throw new InvalidOperationException("Keyword is not declared by the current shader: " + name);
                }
                before = Evidence(path, target);
                var expectedState = (JObject)before["state"].DeepClone();
                var keywords = new HashSet<string>(target.shaderKeywords, StringComparer.Ordinal);
                foreach (var row in rows) { var name = row["keyword"].ToString(); if ((bool)row["enabled"]) keywords.Add(name); else keywords.Remove(name); }
                expectedState["keywords"] = new JArray(keywords.OrderBy(k => k, StringComparer.Ordinal));
                var changed = !JToken.DeepEquals(before["state"], expectedState);
                var preview = (bool?)args["preview"] ?? false;
                if (!preview && ((bool?)args["saveAssets"] == false || !JToken.DeepEquals(args["expectedKeywordEvidence"], before)))
                    throw new InvalidOperationException("Apply requires matching sealed material and shader evidence and persistent saving.");
                var payload = new JObject { ["schema"] = "vrcforge.material_keyword_edit.v1", ["ok"] = true, ["preview"] = preview,
                    ["verified"] = true, ["before"] = before.DeepClone(), ["after"] = expectedState, ["keywordChanges"] = rows.DeepClone(),
                    ["wouldChange"] = changed, ["mutationStarted"] = false, ["committed"] = false, ["commitState"] = "not_started", ["persistedReadback"] = false };
                if (preview) return VRCForgeToolResult.Completed("Keyword edit preview verified; no files changed.", payload);
                if (changed)
                {
                    original = File.ReadAllBytes(absolute);
                    // Seal the bytes actually backed up, before any mutation.
                    if (!JToken.DeepEquals(before, Evidence(path, target))) throw new InvalidOperationException("Material changed while preparing the write.");
                    Undo.IncrementCurrentGroup(); group = Undo.GetCurrentGroup();
                    Undo.SetCurrentGroupName("Edit VRCForge material keywords"); Undo.RegisterCompleteObjectUndo(target, "Edit VRCForge material keywords");
                    started = true;
                    foreach (var row in rows) target.SetKeyword(target.shader.keywordSpace.FindKeyword(row["keyword"].ToString()), (bool)row["enabled"]);
                    EditorUtility.SetDirty(target);
                    AssetDatabase.SaveAssetIfDirty(target);
                    owned = SceneObjectCopyCore.ReadStableAssetEvidence(path, "saved keyword ownership");
                    if (EditorUtility.IsDirty(target)) throw new InvalidOperationException("Material remained dirty after saving.");
                }
                AssetDatabase.ImportAsset(path, ImportAssetOptions.ForceSynchronousImport);
                var readback = Evidence(path, AssetDatabase.LoadAssetAtPath<Material>(path));
                if (!JToken.DeepEquals(readback["state"], expectedState) || readback["guid"].ToString() != before["guid"].ToString() || readback["metaDigest"].ToString() != before["metaDigest"].ToString())
                    throw new InvalidOperationException("Saved material keyword, property, or asset identity readback differs.");
                if (started) Undo.CollapseUndoOperations(group);
                payload["readback"] = readback; payload["persistedReadback"] = true; payload["mutationStarted"] = started;
                payload["committed"] = true; payload["commitState"] = "committed";
                return VRCForgeToolResult.Completed(changed ? "Material keywords saved and independently verified." : "Material keywords already match; readback verified.", payload);
            }
            catch (Exception ex)
            {
                var restored = false;
                if (started)
                {
                    try
                    {
                        var current = SceneObjectCopyCore.ReadStableAssetEvidence(path, "keyword rollback identity");
                        if (current.Guid != before["guid"].ToString() || current.Meta.Digest != before["metaDigest"].ToString() || current.File.Digest != (owned == null ? before["fileDigest"].ToString() : owned.File.Digest)) throw new InvalidOperationException("Target identity changed; checkpoint recovery is required.");
                        Undo.FlushUndoRecordObjects(); Undo.RevertAllDownToGroup(group);
                        var absolute = SceneObjectCopyCore.ToAbsoluteAssetPath(path);
                        restorePath = absolute + ".vrcforge-restore-" + Guid.NewGuid().ToString("N") + ".tmp";
                        File.WriteAllBytes(restorePath, original); File.Replace(restorePath, absolute, null);
                        AssetDatabase.ImportAsset(path, ImportAssetOptions.ForceSynchronousImport);
                        restored = JToken.DeepEquals(before, Evidence(path, AssetDatabase.LoadAssetAtPath<Material>(path)));
                    }
                    catch { restored = false; }
                    finally { if (restorePath != null && File.Exists(restorePath)) { try { File.Delete(restorePath); } catch { restored = false; } } }
                }
                return VRCForgeToolResult.Failed("Material keyword edit rejected or failed: " + ex.Message, new JObject {
                    ["schema"] = "vrcforge.material_keyword_edit.v1", ["ok"] = false, ["mutationStarted"] = started,
                    ["committed"] = started && !restored ? JValue.CreateNull() : new JValue(false),
                    ["commitState"] = !started ? "not_started" : restored ? "rolled_back" : "unknown",
                    ["restored"] = restored, ["checkpointRecoveryRequired"] = started && !restored });
            }
        }

        private static JObject Identity(UnityEngine.Object obj, bool shader = false)
        {
            if (obj == null) return null;
            var path = AssetDatabase.GetAssetPath(obj);
            if (!AssetDatabase.TryGetGUIDAndLocalFileIdentifier(obj, out string guid, out long localId) || string.IsNullOrEmpty(guid))
                throw new InvalidOperationException("Shader or texture lacks persistent asset identity.");
            var result = new JObject { ["name"] = obj.name, ["path"] = path, ["guid"] = guid, ["localId"] = localId };
            if (shader) result["dependencyHash"] = AssetDatabase.GetAssetDependencyHash(path).ToString();
            return result;
        }

        internal static JObject Evidence(string path, Material material)
        {
            if (material == null || material.shader == null || EditorUtility.IsDirty(material)) throw new InvalidOperationException("Material readback is unavailable or dirty.");
            var evidence = SceneObjectCopyCore.ReadStableAssetEvidence(path, "material keyword evidence");
            var state = new JObject { ["shader"] = Identity(material.shader, true), ["isVariant"] = material.isVariant,
                ["parent"] = material.parent == null ? "" : AssetDatabase.GetAssetPath(material.parent), ["renderQueue"] = material.renderQueue,
                ["enableInstancing"] = material.enableInstancing, ["doubleSidedGI"] = material.doubleSidedGI,
                ["globalIlluminationFlags"] = (int)material.globalIlluminationFlags,
                ["keywords"] = new JArray(material.shaderKeywords.OrderBy(k => k, StringComparer.Ordinal)) };
            var properties = new JArray();
            for (var i = 0; i < material.shader.GetPropertyCount(); i++)
            {
                var name = material.shader.GetPropertyName(i); var type = material.shader.GetPropertyType(i);
                var row = new JObject { ["name"] = name, ["type"] = type.ToString() };
                if (type == ShaderPropertyType.Float || type == ShaderPropertyType.Range) row["value"] = material.GetFloat(name);
                else if (type == ShaderPropertyType.Int) row["value"] = material.GetInteger(name);
                else if (type == ShaderPropertyType.Color) { var v = material.GetColor(name); row["value"] = new JArray(v.r, v.g, v.b, v.a); }
                else if (type == ShaderPropertyType.Vector) { var v = material.GetVector(name); row["value"] = new JArray(v.x, v.y, v.z, v.w); }
                else if (type == ShaderPropertyType.Texture) { row["texture"] = Identity(material.GetTexture(name)); var s = material.GetTextureScale(name); var o = material.GetTextureOffset(name); row["scale"] = new JArray(s.x, s.y); row["offset"] = new JArray(o.x, o.y); }
                properties.Add(row);
            }
            state["properties"] = properties;
            // Serialize and parse once so Unity float JValues use the same JSON numeric
            // representation after the MCP transport round trip (DeepEquals remains strict).
            var serialized = new JObject { ["materialAssetPath"] = path, ["guid"] = evidence.Guid, ["fileDigest"] = evidence.File.Digest, ["metaDigest"] = evidence.Meta.Digest, ["state"] = state };
            return JObject.Parse(serialized.ToString(Newtonsoft.Json.Formatting.None));
        }
    }
}
