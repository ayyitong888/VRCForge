using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Tools;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEngine;

namespace VRCForge.Editor
{
    // ------------------------------------------------------------------
    // Generic Unity Asset / Prefab layer (v0.5, third cut).
    //
    // Four MCP tools. Everything sits on stable UnityEditor APIs
    // (AssetDatabase / PrefabUtility) and reuses ComponentCrudCore's
    // hierarchy/path helpers, so this stays reflection-friendly and never
    // hard-references Modular Avatar / VRChat SDK assemblies:
    //   vrc_find_assets       (read)
    //   vrc_get_asset_info    (read)
    //   vrc_instantiate_prefab(write, Undo-registered)
    //   vrc_unpack_prefab     (write, Undo-registered)
    //
    // This is the bridge toward the "add an outfit to the avatar" workflow:
    // find the outfit prefab in the project, instantiate it into the scene
    // under the avatar (prefab link preserved), and optionally unpack it so
    // its contents become plain GameObjects ready for Modular Avatar merges.
    //
    // Both write tools register a Unity Undo entry so the checkpoint timeline
    // (bound to Undo) can roll them back, and support a preview mode that
    // reports what *would* change without mutating, feeding the per-action
    // approval card. Payload keys deliberately avoid data/result/payload/value
    // so the gateway's auto-unwrap never swallows them.
    // ------------------------------------------------------------------

    internal static class AssetPrefabCore
    {
        internal static string NormalizeAssetPath(string value)
        {
            return (value ?? string.Empty).Replace("\\", "/").Trim();
        }

        // Resolve an asset path from either an explicit asset path or a GUID.
        // Throws InvalidOperationException with a helpful message when neither
        // is provided or the asset cannot be located.
        internal static string ResolveAssetPath(string assetPath, string guid)
        {
            var normalizedPath = NormalizeAssetPath(assetPath);
            if (!string.IsNullOrEmpty(normalizedPath))
            {
                return normalizedPath;
            }

            var normalizedGuid = (guid ?? string.Empty).Trim();
            if (!string.IsNullOrEmpty(normalizedGuid))
            {
                var fromGuid = AssetDatabase.GUIDToAssetPath(normalizedGuid);
                if (string.IsNullOrEmpty(fromGuid))
                {
                    throw new InvalidOperationException($"No asset found for GUID '{normalizedGuid}'.");
                }
                return fromGuid;
            }

            throw new InvalidOperationException("assetPath (or guid) is required.");
        }

        internal static string AssetDisplayName(string assetPath)
        {
            return Path.GetFileNameWithoutExtension(assetPath ?? string.Empty);
        }
    }

    [McpForUnityTool(
        name: "vrc_find_assets",
        Description = "Search the project for assets by query/type/folder via AssetDatabase (read-only)."
    )]
    public static class FindAssetsTool
    {
        public const string ToolName = "vrc_find_assets";

        public class FindAssetsParameters
        {
            [ToolParameter("Unity search filter (e.g. 'outfit' or 'l:wardrobe'). Combined with 'typeName' when both are given.", Required = false)]
            public string query { get; set; } = "";

            [ToolParameter("Restrict to an asset type by name (e.g. 'Prefab', 'Material', 'AnimationClip'); applied as a 't:' filter.", Required = false)]
            public string typeName { get; set; } = "";

            [ToolParameter("Limit the search to a project folder (e.g. 'Assets/Outfits'). Empty searches the whole project.", Required = false)]
            public string folder { get; set; } = "";

            [ToolParameter("Maximum number of results to return (default 50).", Required = false)]
            public int? limit { get; set; } = 50;
        }

        public static object HandleCommand(JObject @params)
        {
            var p = (@params ?? new JObject()).ToObject<FindAssetsParameters>() ?? new FindAssetsParameters();
            try
            {
                var limit = p.limit ?? 50;
                if (limit <= 0)
                {
                    limit = 50;
                }

                var filterParts = new List<string>();
                if (!string.IsNullOrWhiteSpace(p.typeName))
                {
                    filterParts.Add("t:" + p.typeName.Trim());
                }
                if (!string.IsNullOrWhiteSpace(p.query))
                {
                    filterParts.Add(p.query.Trim());
                }
                var filter = string.Join(" ", filterParts);

                string[] searchFolders = null;
                var folder = AssetPrefabCore.NormalizeAssetPath(p.folder).TrimEnd('/');
                if (!string.IsNullOrEmpty(folder))
                {
                    if (!AssetDatabase.IsValidFolder(folder))
                    {
                        return new ErrorResponse($"Search folder not found: '{folder}'.");
                    }
                    searchFolders = new[] { folder };
                }

                var guids = searchFolders != null
                    ? AssetDatabase.FindAssets(filter, searchFolders)
                    : AssetDatabase.FindAssets(filter);

                var assets = new List<object>();
                foreach (var guid in guids)
                {
                    if (assets.Count >= limit)
                    {
                        break;
                    }
                    var path = AssetDatabase.GUIDToAssetPath(guid);
                    if (string.IsNullOrEmpty(path))
                    {
                        continue;
                    }
                    var type = AssetDatabase.GetMainAssetTypeAtPath(path);
                    assets.Add(new
                    {
                        name = AssetPrefabCore.AssetDisplayName(path),
                        assetPath = path,
                        guid,
                        assetType = type != null ? type.FullName : null
                    });
                }

                var payload = new
                {
                    filter,
                    folder = string.IsNullOrEmpty(folder) ? null : folder,
                    totalFound = guids.Length,
                    count = assets.Count,
                    assets
                };
                return new SuccessResponse(
                    $"Found {guids.Length} asset(s) for filter '{filter}' (returning {assets.Count}).",
                    payload);
            }
            catch (Exception ex)
            {
                return new ErrorResponse($"Find assets failed: {ex.Message}");
            }
        }
    }

    [McpForUnityTool(
        name: "vrc_get_asset_info",
        Description = "Describe a project asset: path, GUID, type, importer, and prefab details when applicable (read-only)."
    )]
    public static class GetAssetInfoTool
    {
        public const string ToolName = "vrc_get_asset_info";

        public class GetAssetInfoParameters
        {
            [ToolParameter("Project-relative asset path (e.g. 'Assets/Outfits/Dress.prefab').", Required = false)]
            public string assetPath { get; set; } = "";

            [ToolParameter("Asset GUID (used when assetPath is omitted).", Required = false)]
            public string guid { get; set; } = "";
        }

        public static object HandleCommand(JObject @params)
        {
            var p = (@params ?? new JObject()).ToObject<GetAssetInfoParameters>() ?? new GetAssetInfoParameters();
            try
            {
                var path = AssetPrefabCore.ResolveAssetPath(p.assetPath, p.guid);
                var asset = AssetDatabase.LoadMainAssetAtPath(path);
                if (asset == null)
                {
                    return new ErrorResponse($"No asset found at '{path}'.");
                }

                var type = AssetDatabase.GetMainAssetTypeAtPath(path);
                var resolvedGuid = AssetDatabase.AssetPathToGUID(path);
                var importer = AssetImporter.GetAtPath(path);

                var prefabAssetType = PrefabUtility.GetPrefabAssetType(asset);
                var isPrefab = prefabAssetType != PrefabAssetType.NotAPrefab && asset is GameObject;
                string prefabRootName = null;
                int prefabChildCount = 0;
                int prefabComponentCount = 0;
                if (isPrefab)
                {
                    var root = (GameObject)asset;
                    prefabRootName = root.name;
                    prefabChildCount = root.transform.childCount;
                    prefabComponentCount = root.GetComponents<Component>().Count(c => c != null);
                }

                var payload = new
                {
                    assetPath = path,
                    guid = resolvedGuid,
                    name = asset.name,
                    assetType = type != null ? type.FullName : null,
                    importerType = importer != null ? importer.GetType().FullName : null,
                    isPrefab,
                    prefabAssetType = prefabAssetType.ToString(),
                    prefabRootName,
                    prefabChildCount,
                    prefabComponentCount
                };
                return new SuccessResponse(
                    $"Asset '{asset.name}' ({(type != null ? type.Name : "unknown")}) at '{path}'.",
                    payload);
            }
            catch (Exception ex)
            {
                return new ErrorResponse($"Get asset info failed: {ex.Message}");
            }
        }
    }

    [McpForUnityTool(
        name: "vrc_instantiate_prefab",
        Description = "Instantiate a prefab asset into the active scene, optionally under a parent, keeping the prefab link (Undo-registered). Supports preview mode."
    )]
    public static class InstantiatePrefabTool
    {
        public const string ToolName = "vrc_instantiate_prefab";

        public class InstantiatePrefabParameters
        {
            [ToolParameter("Project-relative path to the prefab asset (e.g. 'Assets/Outfits/Dress.prefab').", Required = false)]
            public string assetPath { get; set; } = "";

            [ToolParameter("Prefab asset GUID (used when assetPath is omitted).", Required = false)]
            public string guid { get; set; } = "";

            [ToolParameter("Full hierarchy path or unique name of the parent GameObject. Empty instantiates at the active scene root.", Required = false)]
            public string parentPath { get; set; } = "";

            [ToolParameter("Optional name override for the new instance.", Required = false)]
            public string name { get; set; } = "";

            [ToolParameter("Keep the instance's world position/rotation/scale when parenting (default true).", Required = false)]
            public bool? worldPositionStays { get; set; } = true;

            [ToolParameter("If true, only report what would happen without mutating the scene (default false).", Required = false)]
            public bool? preview { get; set; } = false;
        }

        public static object HandleCommand(JObject @params)
        {
            var p = (@params ?? new JObject()).ToObject<InstantiatePrefabParameters>() ?? new InstantiatePrefabParameters();
            try
            {
                var path = AssetPrefabCore.ResolveAssetPath(p.assetPath, p.guid);
                var asset = AssetDatabase.LoadMainAssetAtPath(path);
                if (asset == null)
                {
                    return new ErrorResponse($"No asset found at '{path}'.");
                }
                if (!(asset is GameObject) || PrefabUtility.GetPrefabAssetType(asset) == PrefabAssetType.NotAPrefab)
                {
                    return new ErrorResponse($"Asset at '{path}' is not a prefab (type '{asset.GetType().Name}').");
                }

                GameObject parent = null;
                var parentPath = ComponentCrudCore.NormalizePath(p.parentPath);
                if (!string.IsNullOrEmpty(parentPath))
                {
                    parent = ComponentCrudCore.ResolveGameObject(parentPath);
                }
                var resolvedParentPath = parent != null ? ComponentCrudCore.GetHierarchyPath(parent.transform) : null;
                var instanceName = string.IsNullOrWhiteSpace(p.name) ? asset.name : p.name.Trim();
                var worldPositionStays = p.worldPositionStays ?? true;

                if (p.preview ?? false)
                {
                    var previewPayload = new
                    {
                        action = "instantiate_prefab",
                        preview = true,
                        assetPath = path,
                        name = instanceName,
                        parentPath = resolvedParentPath
                    };
                    return new SuccessResponse(
                        parent != null
                            ? $"Preview: would instantiate '{path}' as '{instanceName}' under '{resolvedParentPath}'."
                            : $"Preview: would instantiate '{path}' as '{instanceName}' at the active scene root.",
                        previewPayload);
                }

                var instance = PrefabUtility.InstantiatePrefab(asset) as GameObject;
                if (instance == null)
                {
                    return new ErrorResponse($"Unity refused to instantiate the prefab at '{path}'.");
                }
                Undo.RegisterCreatedObjectUndo(instance, $"Instantiate {instanceName}");
                if (parent != null)
                {
                    Undo.SetTransformParent(
                        instance.transform,
                        parent.transform,
                        worldPositionStays,
                        $"Parent {instanceName}");
                }
                if (!string.IsNullOrWhiteSpace(p.name))
                {
                    instance.name = instanceName;
                }
                EditorUtility.SetDirty(instance);

                var goPath = ComponentCrudCore.GetHierarchyPath(instance.transform);
                var payload = new
                {
                    action = "instantiate_prefab",
                    preview = false,
                    assetPath = path,
                    gameObjectPath = goPath,
                    name = instance.name,
                    parentPath = resolvedParentPath,
                    instanceId = instance.GetInstanceID()
                };
                return new SuccessResponse($"Instantiated '{path}' as '{goPath}'.", payload);
            }
            catch (Exception ex)
            {
                return new ErrorResponse($"Instantiate prefab failed: {ex.Message}");
            }
        }
    }

    [McpForUnityTool(
        name: "vrc_unpack_prefab",
        Description = "Unpack a prefab instance in the scene so its contents become plain GameObjects (Undo-registered). Supports preview mode."
    )]
    public static class UnpackPrefabTool
    {
        public const string ToolName = "vrc_unpack_prefab";

        public class UnpackPrefabParameters
        {
            [ToolParameter("Full hierarchy path or unique name of the prefab instance root to unpack.", Required = true)]
            public string gameObjectPath { get; set; } = "";

            [ToolParameter("Unpack mode: 'outermost' (default, only this prefab layer) or 'completely' (all nested prefabs).", Required = false)]
            public string mode { get; set; } = "outermost";

            [ToolParameter("If true, only report what would happen without mutating the scene (default false).", Required = false)]
            public bool? preview { get; set; } = false;
        }

        public static object HandleCommand(JObject @params)
        {
            var p = (@params ?? new JObject()).ToObject<UnpackPrefabParameters>() ?? new UnpackPrefabParameters();
            try
            {
                var go = ComponentCrudCore.ResolveGameObject(p.gameObjectPath);
                var goPath = ComponentCrudCore.GetHierarchyPath(go.transform);

                if (!PrefabUtility.IsOutermostPrefabInstanceRoot(go))
                {
                    return new ErrorResponse(
                        $"'{goPath}' is not the outermost root of a prefab instance; nothing to unpack.");
                }

                var completely = string.Equals(
                    (p.mode ?? string.Empty).Trim(),
                    "completely",
                    StringComparison.OrdinalIgnoreCase);
                var unpackMode = completely ? PrefabUnpackMode.Completely : PrefabUnpackMode.OutermostRoot;
                var modeLabel = completely ? "completely" : "outermost";

                if (p.preview ?? false)
                {
                    var previewPayload = new
                    {
                        action = "unpack_prefab",
                        preview = true,
                        gameObjectPath = goPath,
                        unpackMode = modeLabel
                    };
                    return new SuccessResponse(
                        $"Preview: would unpack prefab instance '{goPath}' ({modeLabel}).",
                        previewPayload);
                }

                PrefabUtility.UnpackPrefabInstance(go, unpackMode, InteractionMode.UserAction);
                EditorUtility.SetDirty(go);

                var payload = new
                {
                    action = "unpack_prefab",
                    preview = false,
                    gameObjectPath = goPath,
                    unpackMode = modeLabel
                };
                return new SuccessResponse($"Unpacked prefab instance '{goPath}' ({modeLabel}).", payload);
            }
            catch (Exception ex)
            {
                return new ErrorResponse($"Unpack prefab failed: {ex.Message}");
            }
        }
    }
}
