using System;
using System.Collections.Generic;
using System.Linq;
using System.Reflection;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEngine;
using VRCForge.Core.MCP;

namespace VRCForge.Editor.Generic
{
    /// <summary>
    /// Read-only inventory of Unity editor extension points. This class never
    /// invokes a menu method, validator, plugin callback, or window getter.
    /// </summary>
    [VRCForgeCommand(toolId: "vrc_inspect_editor_capabilities",
        Summary = "when-to-use: discover loaded Unity plugin menu metadata and open editor windows before selecting an exact entry point. when-NOT-to-use: do not execute plugin callbacks or assume a listed menu completed setup. Negative example: use the approved menu execution tool to trigger setup.",
        Access = VRCForgeCommandAccess.ReadOnly, Category = "editor")]
    public static class UnityEditorCapabilityDiscovery
    {
        public class Parameters
        {
            [VRCForgeInput("menus or windows; defaults to menus.", IsRequired = false)] public string kind { get; set; }
            [VRCForgeInput("Case-insensitive metadata filter.", IsRequired = false)] public string search { get; set; }
            [VRCForgeInput("Zero-based page offset.", IsRequired = false)] public int offset { get; set; }
            [VRCForgeInput("Page size, at most 200.", IsRequired = false)] public int limit { get; set; }
        }
        private const int DefaultLimit = 50;
        private const int MaximumLimit = 200;

        public static object HandleCommand(JObject @params)
        {
            @params = @params ?? new JObject();
            var kind = (@params.Value<string>("kind") ?? "menus").Trim().ToLowerInvariant();
            var offset = Math.Max(0, @params.Value<int?>("offset") ?? 0);
            var limit = Math.Max(1, Math.Min(MaximumLimit, @params.Value<int?>("limit") ?? DefaultLimit));
            var search = (@params.Value<string>("search") ?? string.Empty).Trim();
            if (kind == "menus")
                return VRCForgeToolResult.Completed("Unity editor menu capabilities discovered (read-only).", PageMenus(search, offset, limit));
            if (kind == "windows")
                return VRCForgeToolResult.Completed("Live Unity editor windows discovered (read-only).", PageWindows(search, offset, limit));
            return VRCForgeToolResult.FailedWithCode(
                "unity_editor_capability_kind_invalid",
                "kind must be menus or windows.",
                new JObject { ["kind"] = kind, ["allowedKinds"] = new JArray("menus", "windows") });
        }

        private static JObject PageMenus(string search, int offset, int limit)
        {
            var rows = TypeCache.GetMethodsWithAttribute<MenuItem>()
                .Where(method => method != null && method.IsStatic)
                .SelectMany(method => method.GetCustomAttributes(typeof(MenuItem), false)
                    .OfType<MenuItem>()
                    .Select(attribute => new { Method = method, Attribute = attribute }))
                .Where(item => !string.IsNullOrWhiteSpace(item.Attribute.menuItem))
                .Select(item => new JObject
                {
                    ["menuItemPath"] = item.Attribute.menuItem,
                    ["validate"] = item.Attribute.validate,
                    ["typeFullName"] = item.Method.DeclaringType == null ? string.Empty : item.Method.DeclaringType.FullName ?? string.Empty,
                    ["assembly"] = item.Method.DeclaringType == null ? string.Empty : item.Method.DeclaringType.Assembly.GetName().Name ?? string.Empty,
                    ["staticMethodSignature"] = Signature(item.Method),
                })
                .Where(row => Matches(row, search, "menuItemPath", "typeFullName", "assembly", "staticMethodSignature"))
                .OrderBy(row => (string)row["menuItemPath"], StringComparer.Ordinal)
                .ThenBy(row => (string)row["typeFullName"], StringComparer.Ordinal)
                .ThenBy(row => (string)row["staticMethodSignature"], StringComparer.Ordinal)
                .ToList();
            return Page(rows, offset, limit, search, "menus");
        }

        private static JObject PageWindows(string search, int offset, int limit)
        {
            var rows = Resources.FindObjectsOfTypeAll<EditorWindow>()
                .Where(window => window != null)
                .Select(window => new JObject
                {
                    ["title"] = window.titleContent == null ? string.Empty : window.titleContent.text ?? string.Empty,
                    ["typeFullName"] = window.GetType().FullName ?? string.Empty,
                    ["instanceId"] = window.GetInstanceID(),
                })
                .Where(row => Matches(row, search, "title", "typeFullName"))
                .OrderBy(row => (string)row["title"], StringComparer.Ordinal)
                .ThenBy(row => (string)row["typeFullName"], StringComparer.Ordinal)
                .ThenBy(row => (int)row["instanceId"])
                .ToList();
            return Page(rows, offset, limit, search, "windows");
        }

        private static JObject Page(List<JObject> rows, int offset, int limit, string search, string kind)
        {
            var page = rows.Skip(offset).Take(limit).ToList();
            var payload = new JObject
            {
                ["schema"] = "vrcforge.unity_editor_capabilities.v1",
                ["kind"] = kind,
                ["search"] = search,
                ["offset"] = offset,
                ["limit"] = limit,
                ["total"] = rows.Count,
                ["hasMore"] = offset + page.Count < rows.Count,
                ["items"] = new JArray(page),
            };
            if (offset + page.Count < rows.Count)
                payload["nextOffset"] = offset + page.Count;
            return payload;
        }

        private static bool Matches(JObject row, string search, params string[] fields)
        {
            if (string.IsNullOrEmpty(search)) return true;
            return fields.Any(field => ((string)row[field] ?? string.Empty).IndexOf(search, StringComparison.OrdinalIgnoreCase) >= 0);
        }

        private static string Signature(MethodInfo method)
        {
            var parameters = string.Join(", ", method.GetParameters().Select(parameter => parameter.ParameterType.FullName ?? parameter.ParameterType.Name));
            return (method.ReturnType.FullName ?? method.ReturnType.Name) + " " + (method.Name ?? string.Empty) + "(" + parameters + ")";
        }
    }
}
