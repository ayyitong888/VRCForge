using System;
using System.Collections.Generic;
using System.Linq;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEngine;
using VRCForge.Core.MCP;

namespace VRCForge.Editor
{
    [VRCForgeCommand(
        toolId: "vrc_apply_material_tuning",
        Summary = "Apply validated semantic material parameter changes through shader adapters."
    )]
    public static class MaterialTuningApplier
    {
        public const string ToolName = "vrc_apply_material_tuning";

        public class Parameters
        {
            [VRCForgeInput("Optional avatar root hierarchy path used to scope material lookup.", IsRequired = false)]
            public string avatarPath { get; set; } = "";
            [VRCForgeInput("One or more material changes. Each change requires materialId/material_id, semanticProperty/semantic_property, and after, target, or value.", IsRequired = true)]
            public JArray changes { get; set; } = new JArray();
            [VRCForgeInput("Save and refresh assets after at least one material change is applied.", IsRequired = false, DefaultLiteral = "true")]
            public bool? saveAssets { get; set; } = true;
        }

        private sealed class PreparedChange
        {
            internal MaterialTarget target;
            internal IShaderMaterialAdapter adapter;
            internal string materialId, semanticProperty, assetPath, assetGuid;
            internal object requested, before, after;
        }

        public static object HandleCommand(JObject @params)
        {
            var recovery = new WriteAnimationCurveTool.AssetEditRecovery();
            var previews = new Dictionary<string, Material>(StringComparer.Ordinal);
            var mutationStarted = false;
            var phase = "preflight";
            try
            {
                var avatarPath = (@params?["avatarPath"]?.ToString() ?? string.Empty).Trim();
                var saveAssets = @params?["saveAssets"]?.Value<bool?>() ?? true;
                var changes = @params?["changes"] as JArray;
                if (changes == null || changes.Count == 0)
                    throw new InvalidOperationException("Missing required parameter: changes");
                var index = BuildMaterialIndex(avatarPath);
                var planned = new List<PreparedChange>();
                var bindings = new HashSet<string>(StringComparer.Ordinal);
                // Temporary materials belong only to this call and are destroyed in finally.
                // Validate every row before modifying any persistent asset.
                foreach (var value in changes)
                {
                    var token = value as JObject ?? throw new InvalidOperationException("Each change must be an object.");
                    var materialId = (token["material_id"]?.ToString() ?? token["materialId"]?.ToString() ?? "").Trim();
                    var semantic = (token["semantic_property"]?.ToString() ?? token["semanticProperty"]?.ToString() ?? "").Trim();
                    var requested = token["after"] ?? token["target"] ?? token["value"];
                    if (string.IsNullOrEmpty(materialId) || string.IsNullOrEmpty(semantic) || requested == null
                        || !index.TryGetValue(materialId, out var target))
                        throw new InvalidOperationException("Every change requires a current material id, semantic property, and value.");
                    var path = AssetDatabase.GetAssetPath(target.material);
                    if (string.IsNullOrEmpty(path) || !path.StartsWith("Assets/", StringComparison.Ordinal)
                        || !AssetDatabase.IsMainAsset(target.material))
                        throw new InvalidOperationException("Material tuning requires a persistent main material asset.");
                    if (UnityMaterialKeywordEdit.HasUnsavedChanges(target.material))
                        throw new InvalidOperationException("Save or discard existing material edits before tuning: " + path);
                    if (!bindings.Add(path + "\n" + semantic))
                        throw new InvalidOperationException("Duplicate semantic change to the same material asset.");
                    var adapter = ShaderAdapterRegistry.GetAdapter(target.material)
                        ?? throw new InvalidOperationException("Unsupported shader family.");
                    if (!previews.TryGetValue(path, out var preview))
                    {
                        recovery.Capture(path);
                        preview = new Material(target.material) { hideFlags = HideFlags.HideAndDontSave };
                        previews.Add(path, preview);
                    }
                    var input = ExtractValue(requested);
                    if (!adapter.TryApplyChange(preview, semantic, input, out var before, out var after, out var warning))
                        throw new InvalidOperationException(warning);
                    if (!StoredValuesEqual(after, input))
                        throw new InvalidOperationException("Requested material value would be normalized or clamped; submit the exact supported value.");
                    if (token["before"] != null && !StoredValuesEqual(before, ExtractValue(token["before"])))
                        throw new InvalidOperationException("Material before-value changed since approval.");
                    planned.Add(new PreparedChange { target = target, adapter = adapter, materialId = materialId,
                        semanticProperty = semantic, assetPath = path, assetGuid = AssetDatabase.AssetPathToGUID(path),
                        requested = input, before = before, after = after });
                }
                // Check the final staged material, including alias interactions, before mutation.
                foreach (var item in planned)
                    RequireValue(item.adapter, previews[item.assetPath], item.semanticProperty, item.after);
                recovery.Begin();
                phase = "apply";
                foreach (var item in planned)
                {
                    Undo.RecordObject(item.target.material, "Apply VRCForge material tuning");
                    mutationStarted = true;
                    if (!item.adapter.TryApplyChange(item.target.material, item.semanticProperty, item.requested,
                        out var before, out var after, out var warning)
                        || !StoredValuesEqual(before, item.before) || !StoredValuesEqual(after, item.after))
                        throw new InvalidOperationException("Material apply diverged from validated plan: " + warning);
                    EditorUtility.SetDirty(item.target.material);
                }
                phase = "persisted_readback";
                if (saveAssets)
                {
                    foreach (var group in planned.GroupBy(item => item.assetPath))
                    {
                        AssetDatabase.SaveAssetIfDirty(group.First().target.material);
                        UnityMaterialKeywordEdit.RequirePersistedMaterial(group.First().target.material);
                        AssetDatabase.ImportAsset(group.Key, ImportAssetOptions.ForceSynchronousImport | ImportAssetOptions.ForceUpdate);
                    }
                }
                var applied = new List<object>();
                var readback = new List<object>();
                foreach (var item in planned)
                {
                    var material = saveAssets ? AssetDatabase.LoadAssetAtPath<Material>(item.assetPath) : item.target.material;
                    if (material == null)
                        throw new InvalidOperationException("Material persisted readback failed: readback_asset_null.");
                    if (AssetDatabase.AssetPathToGUID(item.assetPath) != item.assetGuid)
                        throw new InvalidOperationException("Material persisted readback failed: asset_guid_changed.");
                    if (saveAssets && UnityMaterialKeywordEdit.HasUnsavedChanges(material))
                        throw new InvalidOperationException("Material persisted readback failed: readback_asset_dirty.");
                    var adapter = ShaderAdapterRegistry.GetAdapter(material)
                        ?? throw new InvalidOperationException("Saved material shader adapter is unavailable.");
                    var actual = RequireValue(adapter, material, item.semanticProperty, item.after);
                    var row = new { material_id = item.materialId, material_name = material.name,
                        renderer_path = item.target.rendererPath, slot_index = item.target.slotIndex,
                        shader_family = adapter.ShaderFamily, semantic_property = item.semanticProperty,
                        before = item.before, after = actual };
                    applied.Add(row);
                    if (saveAssets) readback.Add(new { material_id = item.materialId, semantic_property = item.semanticProperty,
                        assetPath = item.assetPath, assetGuid = item.assetGuid, before = item.before, after = actual });
                }
                recovery.Complete();
                return VRCForgeToolResult.Completed($"Applied {applied.Count} material tuning change(s).", new
                {
                    schema = "vrcforge.material_tuning_write.v1", ok = true, avatarPath,
                    appliedCount = applied.Count, skippedCount = 0, applied, skipped = new object[0],
                    saved = saveAssets, pending = !saveAssets, verified = saveAssets, persistedReadback = saveAssets,
                    readback, committed = saveAssets, commitState = saveAssets ? "committed" : "pending", commitStateKnown = true,
                    before = planned.Select(item => new { material_id = item.materialId, semantic_property = item.semanticProperty, value = item.before }),
                    after = planned.Select(item => new { material_id = item.materialId, semantic_property = item.semanticProperty, value = item.after }),
                    note = saveAssets ? "已修改并落盘回读验证" : "已修改，尚未落盘"
                });
            }
            catch (Exception ex)
            {
                return WriteAnimationCurveTool.EditFailure("material_tuning_failed", ex, mutationStarted, phase, recovery);
            }
            finally
            {
                foreach (var material in previews.Values) UnityEngine.Object.DestroyImmediate(material);
            }
        }

        private static bool StoredValuesEqual(object actual, object expected)
        {
            if (actual is float number)
                return !float.IsNaN(number) && !float.IsInfinity(number) && expected is float target && number.Equals(target);
            return actual is string text && expected is string expectedText && string.Equals(text, expectedText, StringComparison.Ordinal);
        }

        private static object RequireValue(IShaderMaterialAdapter adapter, Material material, string semantic, object expected)
        {
            var properties = adapter.ReadSupportedProperties(material);
            if (!properties.TryGetValue(semantic, out var property) || !StoredValuesEqual(property.value, expected))
                throw new InvalidOperationException("Material readback value mismatch: " + semantic);
            return property.value;
        }

        private static Dictionary<string, MaterialTarget> BuildMaterialIndex(string avatarPath)
        {
            var normalizedAvatarPath = NormalizePath(avatarPath);
            var exactScopes = ResolveExactScopes(normalizedAvatarPath);
            var index = new Dictionary<string, MaterialTarget>(StringComparer.OrdinalIgnoreCase);
            var componentSlots = new HashSet<string>(StringComparer.Ordinal);
            var renderers = Resources.FindObjectsOfTypeAll<Renderer>()
                .Where(IsSceneObject)
                .Select(renderer => new
                {
                    renderer,
                    identity = RendererComponentIdentity.Create(renderer)
                })
                .OrderBy(item => item.identity.scenePath, StringComparer.Ordinal)
                .ThenBy(item => item.identity.sceneHandle)
                .ThenBy(item => item.identity.rendererPath, StringComparer.Ordinal)
                .ThenBy(item => item.identity.componentId, StringComparer.Ordinal);

            foreach (var rendererEntry in renderers)
            {
                var renderer = rendererEntry.renderer;
                var rendererIdentity = rendererEntry.identity;
                var avatarRoot = FindAvatarRoot(renderer.transform);
                var rootPath = NormalizePath(GetTransformPath(avatarRoot));
                if (exactScopes.Count > 0)
                {
                    if (!exactScopes.Any(scope => ReferenceEquals(renderer.transform, scope) || renderer.transform.IsChildOf(scope)))
                    {
                        continue;
                    }
                }
                else if (!string.IsNullOrEmpty(normalizedAvatarPath)
                    && !string.Equals(rootPath, normalizedAvatarPath, StringComparison.OrdinalIgnoreCase)
                    && !rootPath.EndsWith("/" + normalizedAvatarPath, StringComparison.OrdinalIgnoreCase)
                    && !avatarRoot.name.Equals(normalizedAvatarPath, StringComparison.OrdinalIgnoreCase))
                {
                    continue;
                }

                var rendererPath = rendererIdentity.rendererPath;
                var sharedMaterials = renderer.sharedMaterials ?? Array.Empty<Material>();
                for (var slotIndex = 0; slotIndex < sharedMaterials.Length; slotIndex++)
                {
                    var material = sharedMaterials[slotIndex];
                    if (material == null)
                    {
                        continue;
                    }

                    var shaderName = material.shader != null ? material.shader.name : "";
                    var componentSlot = $"{rendererIdentity.componentId}:{slotIndex}";
                    if (!componentSlots.Add(componentSlot))
                    {
                        throw new InvalidOperationException("Material inventory contains a duplicate renderer component slot.");
                    }
                    var materialId = MaterialInventoryIdentity.CreateMaterialId(
                        rendererPath,
                        slotIndex,
                        material.name,
                        shaderName);
                    if (index.ContainsKey(materialId))
                    {
                        throw new InvalidOperationException("Material inventory identifier collision detected.");
                    }
                    index.Add(materialId, new MaterialTarget
                    {
                        material = material,
                        rendererPath = rendererPath,
                        slotIndex = slotIndex
                    });
                }
            }

            return index;
        }

        private static List<Transform> ResolveExactScopes(string normalizedPath)
        {
            if (string.IsNullOrEmpty(normalizedPath))
            {
                return new List<Transform>();
            }

            return Resources.FindObjectsOfTypeAll<Transform>()
                .Where(IsSceneObject)
                .Where(transform => string.Equals(
                    NormalizePath(GetTransformPath(transform)),
                    normalizedPath,
                    StringComparison.OrdinalIgnoreCase))
                .OrderBy(transform => (transform.gameObject.scene.path ?? string.Empty).Replace("\\", "/"), StringComparer.Ordinal)
                .ThenBy(transform => transform.gameObject.scene.handle)
                .ThenBy(transform => transform.GetInstanceID())
                .ToList();
        }

        private static object ExtractValue(JToken token)
        {
            if (token == null || token.Type == JTokenType.Null)
            {
                return null;
            }

            if (token.Type == JTokenType.Float || token.Type == JTokenType.Integer)
            {
                return token.Value<float>();
            }

            return token.ToString();
        }

        private static bool IsSceneObject(Component component)
        {
            return component != null
                && component.gameObject.scene.IsValid()
                && component.gameObject.scene.isLoaded
                && !EditorUtility.IsPersistent(component);
        }

        private static Transform FindAvatarRoot(Transform source)
        {
            var current = source;
            Transform fallback = source.root;
            var avatarDescriptorType = FindType("VRC.SDK3.Avatars.Components.VRCAvatarDescriptor");

            while (current != null)
            {
                if (avatarDescriptorType != null && current.GetComponent(avatarDescriptorType) != null)
                {
                    return current;
                }

                if (current.GetComponent<Animator>() != null)
                {
                    fallback = current;
                }

                current = current.parent;
            }

            return fallback;
        }

        private static Type FindType(string fullName)
        {
            foreach (var assembly in AppDomain.CurrentDomain.GetAssemblies())
            {
                try
                {
                    var type = assembly.GetType(fullName, false);
                    if (type != null)
                    {
                        return type;
                    }
                }
                catch
                {
                    // Ignore transient reflection failures from editor reloads.
                }
            }

            return null;
        }

        private static string GetTransformPath(Transform transform)
        {
            var segments = new Stack<string>();
            var current = transform;

            while (current != null)
            {
                segments.Push(current.name);
                current = current.parent;
            }

            return string.Join("/", segments);
        }

        private static string NormalizePath(string value)
        {
            return (value ?? string.Empty).Replace("\\", "/").Trim().Trim('/');
        }

        private sealed class MaterialTarget
        {
            public Material material;
            public string rendererPath;
            public int slotIndex;
        }
    }
}
