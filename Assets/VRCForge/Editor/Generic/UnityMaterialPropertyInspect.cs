using System;
using System.Collections.Generic;
using System.Linq;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEngine;
using UnityEngine.Rendering;
using VRCForge.Core.MCP;

namespace VRCForge.Editor.Generic
{
    [VRCForgeCommand(
        toolId: "vrc_inspect_material_properties",
        Summary = "when-to-use: inspect actual Shader property names, types and current values of an exact material asset before editing. when-NOT-to-use: do not change material values or infer supported fields from a shader name. Negative example: a requested second texture layer requires the approved material writer after inspection.",
        Access = VRCForgeCommandAccess.ReadOnly,
        Category = "materials")]
    public static class UnityMaterialPropertyInspect
    {
        public class Parameters
        {
            [VRCForgeInput("Exact Assets/... .mat asset path.")] public string materialAssetPath { get; set; }
            [VRCForgeInput("Case-insensitive property name filter.", IsRequired = false)] public string search { get; set; }
            [VRCForgeInput("Zero-based page offset.", IsRequired = false)] public int offset { get; set; }
            [VRCForgeInput("Page size, at most 256.", IsRequired = false)] public int limit { get; set; }
        }
        private const int DefaultLimit = 64;
        private const int MaximumLimit = 256;

        public static object HandleCommand(JObject @params)
        {
            try
            {
                @params = @params ?? new JObject();
                var path = (@params.Value<string>("materialAssetPath") ?? string.Empty).Trim();
                if (!IsExactMaterialPath(path))
                    return VRCForgeToolResult.FailedWithCode("material_asset_path_invalid", "materialAssetPath must be one exact Assets/... .mat path.");
                var material = AssetDatabase.LoadAssetAtPath<Material>(path);
                if (material == null || AssetDatabase.GetAssetPath(material) != path)
                    return VRCForgeToolResult.FailedWithCode("material_asset_unavailable", "Persistent material asset was not found.");

                var search = (@params.Value<string>("search") ?? string.Empty).Trim();
                var offset = Math.Max(0, @params.Value<int?>("offset") ?? 0);
                var limit = Math.Max(1, Math.Min(MaximumLimit, @params.Value<int?>("limit") ?? DefaultLimit));
                var all = ReadProperties(material, search);
                var page = all.Skip(offset).Take(limit).ToList();
                var payload = new JObject
                {
                    ["schema"] = "vrcforge.material_property_inspection.v1",
                    ["materialAssetPath"] = path,
                    ["materialName"] = material.name,
                    ["dirty"] = EditorUtility.IsDirty(material),
                    ["isVariant"] = material.isVariant || material.parent != null,
                    ["parentMaterialPath"] = material.parent == null ? string.Empty : AssetDatabase.GetAssetPath(material.parent),
                    ["shaderName"] = material.shader == null ? string.Empty : material.shader.name,
                    ["search"] = search,
                    ["offset"] = offset,
                    ["limit"] = limit,
                    ["total"] = all.Count,
                    ["hasMore"] = offset + page.Count < all.Count,
                    ["properties"] = new JArray(page),
                };
                if (offset + page.Count < all.Count) payload["nextOffset"] = offset + page.Count;
                return VRCForgeToolResult.Completed("Material Shader properties inspected without changing the asset.", payload);
            }
            catch (Exception ex)
            {
                return VRCForgeToolResult.FailedWithException("Material property inspection failed: " + ex.Message, ex);
            }
        }

        private static List<JObject> ReadProperties(Material material, string search)
        {
            var rows = new List<JObject>();
            var shader = material.shader;
            if (shader == null) return rows;
            for (var index = 0; index < shader.GetPropertyCount(); index++)
            {
                var name = shader.GetPropertyName(index);
                var type = shader.GetPropertyType(index);
                if (!string.IsNullOrEmpty(search) && name.IndexOf(search, StringComparison.OrdinalIgnoreCase) < 0)
                    continue;
                var row = new JObject { ["name"] = name, ["type"] = type.ToString(), ["index"] = index };
                if (type == ShaderPropertyType.Float || type == ShaderPropertyType.Range)
                    row["value"] = material.GetFloat(name);
                else if (type == ShaderPropertyType.Int)
                    row["value"] = material.GetInteger(name);
                else if (type == ShaderPropertyType.Color)
                {
                    var value = material.GetColor(name);
                    row["value"] = new JArray(value.r, value.g, value.b, value.a);
                }
                else if (type == ShaderPropertyType.Vector)
                {
                    var value = material.GetVector(name);
                    row["value"] = new JArray(value.x, value.y, value.z, value.w);
                }
                else if (type == ShaderPropertyType.Texture)
                {
                    var texture = material.GetTexture(name);
                    row["texture"] = TextureIdentity(texture);
                    var scale = material.GetTextureScale(name);
                    var offset = material.GetTextureOffset(name);
                    row["scale"] = new JArray(scale.x, scale.y);
                    row["offset"] = new JArray(offset.x, offset.y);
                }
                rows.Add(row);
            }
            return rows.OrderBy(row => (string)row["name"], StringComparer.Ordinal).ToList();
        }

        private static JObject TextureIdentity(Texture texture)
        {
            if (texture == null) return null;
            var path = AssetDatabase.GetAssetPath(texture);
            AssetDatabase.TryGetGUIDAndLocalFileIdentifier(texture, out string guid, out long localId);
            return new JObject { ["name"] = texture.name, ["path"] = path ?? string.Empty, ["guid"] = guid ?? string.Empty, ["localId"] = localId };
        }

        private static bool IsExactMaterialPath(string path)
        {
            return path.StartsWith("Assets/", StringComparison.Ordinal)
                && path.EndsWith(".mat", StringComparison.Ordinal)
                && !path.Contains("\\") && !path.Contains(":")
                && path.Split('/').All(part => part != string.Empty && part != "." && part != "..");
        }
    }
}
