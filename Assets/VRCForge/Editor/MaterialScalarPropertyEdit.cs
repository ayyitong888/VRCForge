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
    // Scalar-only mode of the existing approved material command. Backup/temp files
    // belong to this one synchronous call and exact sealed material; no extra jobs.
    internal static class MaterialScalarPropertyEdit
    {
        internal static object HandleCommand(JObject args)
        {
            string path = null, restorePath = null;
            JObject before = null;
            byte[] original = null;
            StableAssetEvidence owned = null;
            var started = false;
            var group = -1;
            try
            {
                var allowed = new[] { "materialAssetPath", "propertyChanges", "renderQueue", "preview", "saveAssets", "expectedProjectPath", "expectedPropertyEvidence" };
                if (args.Properties().Any(p => !allowed.Contains(p.Name))) throw new InvalidOperationException("Scalar editing accepts only an exact asset selector, propertyChanges, optional renderQueue and sealed execution fields.");
                if (!MaterialShaderTool.MatchesCurrentProject(args["expectedProjectPath"]?.ToString())) throw new InvalidOperationException("Expected project does not match the active Unity project.");
                path = args["materialAssetPath"]?.Type == JTokenType.String ? args["materialAssetPath"].ToString() : null;
                if (string.IsNullOrEmpty(path) || !path.StartsWith("Assets/", StringComparison.Ordinal) || !path.EndsWith(".mat", StringComparison.Ordinal) || path.Contains("\\") || path.Contains(":") || path.Split('/').Any(p => p == "." || p == ".." || p == ""))
                    throw new InvalidOperationException("One exact Assets/... .mat path is required.");
                var absolute = SceneObjectCopyCore.ToAbsoluteAssetPath(path);
                var target = AssetDatabase.LoadAssetAtPath<Material>(path);
                if (target == null || AssetDatabase.GetAssetPath(target) != path || target.shader == null || EditorUtility.IsDirty(target)) throw new InvalidOperationException("A saved persistent material with an available shader is required.");
                if (target.isVariant || target.parent != null) throw new InvalidOperationException("Flatten the Material Variant before editing scalar properties.");
                var rows = ValidateChanges(args, target.shader);
                before = Evidence(path, target);
                var expectedState = ExpectedState(before, rows, args["renderQueue"]);
                var changed = !JToken.DeepEquals(before["state"], expectedState);
                var preview = (bool?)args["preview"] ?? false;
                if (!preview && ((bool?)args["saveAssets"] == false || !JToken.DeepEquals(args["expectedPropertyEvidence"], before)))
                    throw new InvalidOperationException("Apply requires matching sealed material, shader, property and shared-impact evidence and persistent saving.");
                var payload = new JObject { ["schema"] = "vrcforge.material_property_edit.v1", ["ok"] = true, ["preview"] = preview,
                    ["materialAssetPath"] = path, ["verified"] = true, ["before"] = before.DeepClone(), ["after"] = expectedState,
                    ["propertyChanges"] = rows.DeepClone(), ["wouldChange"] = changed, ["mutationStarted"] = false,
                    ["committed"] = false, ["commitState"] = "not_started", ["persistedReadback"] = false };
                if (args["renderQueue"] != null) payload["renderQueue"] = args["renderQueue"].DeepClone();
                if (preview) return VRCForgeToolResult.Completed("Scalar material edit preview verified; no files changed.", payload);
                if (changed)
                {
                    original = File.ReadAllBytes(absolute);
                    if (!JToken.DeepEquals(before, Evidence(path, target))) throw new InvalidOperationException("Material or shared impact changed while preparing the write.");
                    Undo.IncrementCurrentGroup(); group = Undo.GetCurrentGroup();
                    Undo.SetCurrentGroupName("Edit VRCForge material scalar properties"); Undo.RegisterCompleteObjectUndo(target, "Edit VRCForge material scalar properties");
                    started = true;
                    foreach (var row in rows)
                    {
                        var name = row["propertyName"].ToString();
                        if (target.shader.GetPropertyType(target.shader.FindPropertyIndex(name)) == ShaderPropertyType.Int) target.SetInteger(name, row["value"].Value<int>());
                        else target.SetFloat(name, row["value"].Value<float>());
                    }
                    if (args["renderQueue"] != null) target.renderQueue = args["renderQueue"].Value<int>();
                    EditorUtility.SetDirty(target); AssetDatabase.SaveAssetIfDirty(target);
                    owned = SceneObjectCopyCore.ReadStableAssetEvidence(path, "saved scalar ownership");
                    if (EditorUtility.IsDirty(target)) throw new InvalidOperationException("Material remained dirty after saving.");
                }
                AssetDatabase.ImportAsset(path, ImportAssetOptions.ForceSynchronousImport);
                var readback = Evidence(path, AssetDatabase.LoadAssetAtPath<Material>(path));
                if (!JToken.DeepEquals(readback["state"], expectedState) || readback["guid"].ToString() != before["guid"].ToString()
                    || readback["metaDigest"].ToString() != before["metaDigest"].ToString() || !JToken.DeepEquals(readback["sharedImpact"], before["sharedImpact"])
                    || readback["sharedImpactDigest"].ToString() != before["sharedImpactDigest"].ToString())
                    throw new InvalidOperationException("Saved material scalar, untouched property, shader, keyword or shared-impact readback differs.");
                if (started) Undo.CollapseUndoOperations(group);
                payload["readback"] = readback; payload["persistedReadback"] = true; payload["mutationStarted"] = started;
                payload["committed"] = true; payload["commitState"] = "committed";
                return VRCForgeToolResult.Completed(changed ? "Material scalars saved and independently verified." : "Material scalars already match; readback verified.", payload);
            }
            catch (Exception ex)
            {
                var restored = false;
                if (started)
                {
                    try
                    {
                        var current = SceneObjectCopyCore.ReadStableAssetEvidence(path, "scalar rollback identity");
                        if (current.Guid != before["guid"].ToString() || current.Meta.Digest != before["metaDigest"].ToString() || current.File.Digest != (owned == null ? before["fileDigest"].ToString() : owned.File.Digest))
                            throw new InvalidOperationException("Target identity changed; checkpoint recovery is required.");
                        Undo.FlushUndoRecordObjects(); Undo.RevertAllDownToGroup(group);
                        var absolute = SceneObjectCopyCore.ToAbsoluteAssetPath(path);
                        restorePath = absolute + ".vrcforge-restore-" + Guid.NewGuid().ToString("N") + ".tmp";
                        using (var stream = new FileStream(restorePath, FileMode.CreateNew, FileAccess.Write, FileShare.None))
                        { stream.Write(original, 0, original.Length); stream.Flush(true); }
                        File.Replace(restorePath, absolute, null);
                        AssetDatabase.ImportAsset(path, ImportAssetOptions.ForceSynchronousImport);
                        restored = JToken.DeepEquals(before, Evidence(path, AssetDatabase.LoadAssetAtPath<Material>(path)));
                    }
                    catch { restored = false; }
                    finally { if (restorePath != null && File.Exists(restorePath)) { try { File.Delete(restorePath); } catch { restored = false; } } }
                }
                return VRCForgeToolResult.Failed("Material scalar edit rejected or failed: " + ex.Message, new JObject {
                    ["schema"] = "vrcforge.material_property_edit.v1", ["ok"] = false, ["mutationStarted"] = started,
                    ["committed"] = started && !restored ? JValue.CreateNull() : new JValue(false),
                    ["commitState"] = !started ? "not_started" : restored ? "rolled_back" : "unknown",
                    ["restored"] = restored, ["checkpointRecoveryRequired"] = started && !restored });
            }
        }

        internal static JArray ValidateChanges(JObject args, Shader shader)
        {
            var rows = args["propertyChanges"] as JArray;
            if (rows == null || rows.Count < 1 || rows.Count > 32) throw new InvalidOperationException("propertyChanges requires 1..32 edits.");
            var names = new HashSet<string>(StringComparer.Ordinal);
            foreach (var token in rows)
            {
                var row = token as JObject;
                var name = row?["propertyName"]?.Type == JTokenType.String ? row["propertyName"].ToString() : null;
                if (row == null || row.Count != 2 || string.IsNullOrEmpty(name) || name.Length > 128 || name.Trim() != name || !names.Add(name)
                    || (row["value"]?.Type != JTokenType.Float && row["value"]?.Type != JTokenType.Integer))
                    throw new InvalidOperationException("Each scalar requires an exact unique propertyName and numeric value.");
                var index = shader.FindPropertyIndex(name);
                if (index < 0) throw new InvalidOperationException("Property is not declared by the current shader: " + name);
                var type = shader.GetPropertyType(index); var number = row["value"].Value<double>();
                if (double.IsNaN(number) || double.IsInfinity(number)) throw new InvalidOperationException("Scalar values must be finite.");
                if (type == ShaderPropertyType.Int)
                {
                    if (number != Math.Truncate(number) || number < int.MinValue || number > int.MaxValue) throw new InvalidOperationException("Int properties require exact Int32 integer values.");
                }
                else if (type == ShaderPropertyType.Float || type == ShaderPropertyType.Range)
                {
                    if (float.IsNaN((float)number) || float.IsInfinity((float)number)) throw new InvalidOperationException("Scalar value exceeds finite float32.");
                    if (type == ShaderPropertyType.Range)
                    {
                        var limits = shader.GetPropertyRangeLimits(index);
                        if (number < limits.x || number > limits.y) throw new InvalidOperationException("Scalar value is outside the declared Range: " + name);
                    }
                }
                else throw new InvalidOperationException("Only declared Int, Float and Range properties are editable.");
            }
            if (args["renderQueue"] != null && (args["renderQueue"].Type != JTokenType.Integer || args["renderQueue"].Value<long>() < -1 || args["renderQueue"].Value<long>() > 5000))
                throw new InvalidOperationException("renderQueue must be an integer from -1 to 5000.");
            return (JArray)rows.DeepClone();
        }

        internal static JObject ExpectedState(JObject before, JArray rows, JToken queue)
        {
            var state = (JObject)before["state"].DeepClone();
            foreach (var row in rows)
            {
                var property = ((JArray)state["properties"]).OfType<JObject>().Single(item => item["name"].ToString() == row["propertyName"].ToString());
                property["value"] = property["type"].ToString() == "Int" ? new JValue(row["value"].Value<int>()) : new JValue(row["value"].Value<float>());
            }
            if (queue != null)
            {
                state["rawRenderQueue"] = queue.Value<int>();
                state["renderQueue"] = queue.Value<int>() == -1 ? state["shader"]["renderQueue"].DeepClone() : queue.DeepClone();
            }
            return JObject.Parse(state.ToString(Newtonsoft.Json.Formatting.None));
        }

        internal static JObject Evidence(string path, Material material)
        {
            var evidence = UnityMaterialKeywordEdit.Evidence(path, material);
            // Unity 2022.3 exposes only effective renderQueue publicly; its public
            // serialized inspection API retains -1 without changing the material.
            using (var serialized = new SerializedObject(material))
            using (var queue = serialized.FindProperty("m_CustomRenderQueue"))
            {
                if (queue == null || queue.propertyType != SerializedPropertyType.Integer) throw new InvalidOperationException("Raw material render queue cannot be inspected.");
                evidence["state"]["rawRenderQueue"] = queue.intValue;
            }
            evidence["state"]["shader"]["renderQueue"] = material.shader.renderQueue;
            var properties = (JArray)evidence["state"]["properties"];
            for (var i = 0; i < material.shader.GetPropertyCount(); i++)
            {
                properties[i]["attributes"] = new JArray(material.shader.GetPropertyAttributes(i));
                if (material.shader.GetPropertyType(i) == ShaderPropertyType.Range)
                {
                    var limits = material.shader.GetPropertyRangeLimits(i); properties[i]["range"] = new JArray(limits.x, limits.y);
                }
            }
            var impact = MaterialShaderTool.ResolveSharedMaterialImpact(material, MaterialShaderTool.InspectWritableMaterialAsset(material), null);
            evidence["sharedImpact"] = JObject.FromObject(impact.impact);
            evidence["sharedImpactDigestSchema"] = "vrcforge.material_shader_impact.v2";
            evidence["sharedImpactDigest"] = impact.digest;
            evidence["sharedImpactDisplayDigest"] = impact.displayDigest;
            evidence["sharedImpactTailDigest"] = impact.tailDigest;
            return JObject.Parse(evidence.ToString(Newtonsoft.Json.Formatting.None));
        }
    }
}
