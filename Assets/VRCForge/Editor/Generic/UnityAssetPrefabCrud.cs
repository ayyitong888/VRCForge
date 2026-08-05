using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEngine;
using VRCForge.Core.MCP;

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

        internal static int CountHierarchyPath(string hierarchyPath, int sceneHandle)
        {
            var normalized = ComponentCrudCore.NormalizePath(hierarchyPath);
            return Resources.FindObjectsOfTypeAll<GameObject>().Count(go =>
                go != null
                && go.scene.IsValid()
                && go.scene.handle == sceneHandle
                && string.Equals(ComponentCrudCore.GetHierarchyPath(go.transform), normalized, StringComparison.Ordinal));
        }
    }

    [VRCForgeCommand(
        toolId: "vrc_find_assets",
        Summary = "Search the project for assets by query/type/folder via AssetDatabase (read-only).",
        Access = VRCForgeCommandAccess.ReadOnly
    )]
    public static class FindAssetsTool
    {
        public const string ToolName = "vrc_find_assets";

        public class FindAssetsParameters
        {
            [VRCForgeInput("Unity search filter (e.g. 'outfit' or 'l:wardrobe'). Combined with 'typeName' when both are given.", IsRequired = false)]
            public string query { get; set; } = "";

            [VRCForgeInput("Restrict to an asset type by name (e.g. 'Prefab', 'Material', 'AnimationClip'); applied as a 't:' filter.", IsRequired = false)]
            public string typeName { get; set; } = "";

            [VRCForgeInput("Limit the search to a project folder (e.g. 'Assets/Outfits'). Empty searches the whole project.", IsRequired = false)]
            public string folder { get; set; } = "";

            [VRCForgeInput("Maximum number of results to return (default 50).", IsRequired = false)]
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
                        return VRCForgeToolResult.Failed($"Search folder not found: '{folder}'.");
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
                return VRCForgeToolResult.Completed(
                    $"Found {guids.Length} asset(s) for filter '{filter}' (returning {assets.Count}).",
                    payload);
            }
            catch (Exception ex)
            {
                return VRCForgeToolResult.Failed($"Find assets failed: {ex.Message}");
            }
        }
    }

    [VRCForgeCommand(
        toolId: "vrc_get_asset_info",
        Summary = "Describe a project asset: path, GUID, type, importer, and prefab details when applicable (read-only).",
        Access = VRCForgeCommandAccess.ReadOnly
    )]
    public static class GetAssetInfoTool
    {
        public const string ToolName = "vrc_get_asset_info";

        public class GetAssetInfoParameters
        {
            [VRCForgeInput("Project-relative asset path (e.g. 'Assets/Outfits/Dress.prefab').", IsRequired = false)]
            public string assetPath { get; set; } = "";

            [VRCForgeInput("Asset GUID (used when assetPath is omitted).", IsRequired = false)]
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
                    return VRCForgeToolResult.Failed($"No asset found at '{path}'.");
                }
                var type = AssetDatabase.GetMainAssetTypeAtPath(path);
                var resolvedGuid = AssetDatabase.AssetPathToGUID(path);
                var dependencyHash = AssetDatabase.GetAssetDependencyHash(path).ToString();
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
                    dependencyHash,
                    name = asset.name,
                    assetType = type != null ? type.FullName : null,
                    importerType = importer != null ? importer.GetType().FullName : null,
                    isPrefab,
                    prefabAssetType = prefabAssetType.ToString(),
                    prefabRootName,
                    prefabChildCount,
                    prefabComponentCount
                };
                return VRCForgeToolResult.Completed(
                    $"Asset '{asset.name}' ({(type != null ? type.Name : "unknown")}) at '{path}'.",
                    payload);
            }
            catch (Exception ex)
            {
                return VRCForgeToolResult.Failed($"Get asset info failed: {ex.Message}");
            }
        }
    }

    [VRCForgeCommand(
        toolId: "vrc_instantiate_prefab",
        Summary = "Instantiate a prefab asset into the active scene, optionally under a parent, keeping the prefab link (Undo-registered). Supports preview mode."
    )]
    public static class InstantiatePrefabTool
    {
        public const string ToolName = "vrc_instantiate_prefab";

        public class InstantiatePrefabParameters
        {
            [VRCForgeInput("Project-relative path to the prefab asset (e.g. 'Assets/Outfits/Dress.prefab').", IsRequired = false)]
            public string assetPath { get; set; } = "";

            [VRCForgeInput("Prefab asset GUID (used when assetPath is omitted).", IsRequired = false)]
            public string guid { get; set; } = "";

            [VRCForgeInput("Full hierarchy path or unique name of the parent GameObject. Empty instantiates at the active scene root.", IsRequired = false)]
            public string parentPath { get; set; } = "";

            [VRCForgeInput("Optional name override for the new instance.", IsRequired = false)]
            public string name { get; set; } = "";

            [VRCForgeInput("Keep the instance's world position/rotation/scale when parenting (default true).", IsRequired = false)]
            public bool? worldPositionStays { get; set; } = true;

            [VRCForgeInput("Optional exact prefab GUID expected by the approved plan; mismatch fails closed.", IsRequired = false)]
            public string expectedPrefabGuid { get; set; } = "";

            [VRCForgeInput("Optional exact AssetDatabase dependency hash expected by the approved plan; mismatch fails closed.", IsRequired = false)]
            public string expectedAssetDependencyHash { get; set; } = "";

            [VRCForgeInput("Optional exact active/parent scene path expected for the new instance.", IsRequired = false)]
            public string expectedScenePath { get; set; } = "";

            [VRCForgeInput("Optional exact parent GlobalObjectId expected by the approved plan.", IsRequired = false)]
            public string expectedParentGlobalObjectId { get; set; } = "";

            [VRCForgeInput("Optional exact hierarchy path expected for the new instance; it must be absent before mutation.", IsRequired = false)]
            public string expectedResultPath { get; set; } = "";

            [VRCForgeInput("Approval-generated 64-hex nonce that binds Unity's new GlobalObjectId to the exact ordered continuation tools.", IsRequired = false)]
            public string approvedObjectReceiptNonce { get; set; } = "";

            [VRCForgeInput("Exact ordered continuation tools approved for the newly instantiated object.", IsRequired = false)]
            public string[] approvedContinuationTools { get; set; } = Array.Empty<string>();

            [VRCForgeInput("If true, only report what would happen without mutating the scene (default false).", IsRequired = false)]
            public bool? preview { get; set; } = false;
        }

        public static object HandleCommand(JObject @params)
        {
            var p = (@params ?? new JObject()).ToObject<InstantiatePrefabParameters>() ?? new InstantiatePrefabParameters();
            var mutationStarted = false;
            var mutatedPath = "";
            var mutatedGlobalObjectId = "";
            var continuationNonce = (p.approvedObjectReceiptNonce ?? "").Trim();
            var continuationCount = 0;
            var continuationReserved = false;
            try
            {
                var path = AssetPrefabCore.ResolveAssetPath(p.assetPath, p.guid);
                var asset = AssetDatabase.LoadMainAssetAtPath(path);
                if (asset == null)
                {
                    return VRCForgeToolResult.Failed($"No asset found at '{path}'.");
                }
                if (!(asset is GameObject) || PrefabUtility.GetPrefabAssetType(asset) == PrefabAssetType.NotAPrefab)
                {
                    return VRCForgeToolResult.Failed($"Asset at '{path}' is not a prefab (type '{asset.GetType().Name}').");
                }
                var prefabGuid = AssetDatabase.AssetPathToGUID(path);
                if (!string.IsNullOrWhiteSpace(p.expectedPrefabGuid)
                    && !string.Equals(prefabGuid, p.expectedPrefabGuid.Trim(), StringComparison.OrdinalIgnoreCase))
                {
                    return VRCForgeToolResult.Failed("Prefab GUID drifted from the approved expectation.");
                }
                var dependencyHash = AssetDatabase.GetAssetDependencyHash(path).ToString();
                if (!string.IsNullOrWhiteSpace(p.expectedAssetDependencyHash)
                    && !string.Equals(dependencyHash, p.expectedAssetDependencyHash.Trim(), StringComparison.Ordinal))
                {
                    return VRCForgeToolResult.Failed("Prefab dependency hash drifted from the approved expectation.");
                }

                GameObject parent = null;
                var parentPath = ComponentCrudCore.NormalizePath(p.parentPath);
                if (!string.IsNullOrEmpty(parentPath))
                {
                    parent = ComponentCrudCore.ResolveGameObject(parentPath);
                }
                var resolvedParentPath = parent != null ? ComponentCrudCore.GetHierarchyPath(parent.transform) : null;
                var scene = parent != null ? parent.scene : UnityEngine.SceneManagement.SceneManager.GetActiveScene();
                var scenePath = scene.path;
                if (!string.IsNullOrWhiteSpace(p.expectedScenePath) && !string.Equals(scenePath, p.expectedScenePath.Trim(), StringComparison.Ordinal))
                {
                    return VRCForgeToolResult.Failed("Target scene drifted from the approved expectation.");
                }
                var parentGlobalObjectId = parent != null ? GlobalObjectId.GetGlobalObjectIdSlow(parent).ToString() : "";
                if (!string.IsNullOrWhiteSpace(p.expectedParentGlobalObjectId)
                    && !string.Equals(parentGlobalObjectId, p.expectedParentGlobalObjectId.Trim(), StringComparison.Ordinal))
                {
                    return VRCForgeToolResult.Failed("Prefab parent GlobalObjectId drifted from the approved expectation.");
                }
                var instanceName = string.IsNullOrWhiteSpace(p.name) ? asset.name : p.name.Trim();
                if (instanceName.Contains("/"))
                {
                    return VRCForgeToolResult.Failed("Prefab instance name cannot contain '/'.");
                }
                var expectedResultPath = string.IsNullOrEmpty(resolvedParentPath)
                    ? instanceName
                    : resolvedParentPath + "/" + instanceName;
                if (!string.IsNullOrWhiteSpace(p.expectedResultPath)
                    && !string.Equals(expectedResultPath, ComponentCrudCore.NormalizePath(p.expectedResultPath), StringComparison.Ordinal))
                {
                    return VRCForgeToolResult.Failed("Prefab result path differs from the approved expectation.");
                }
                if (!string.IsNullOrWhiteSpace(p.expectedResultPath)
                    && AssetPrefabCore.CountHierarchyPath(expectedResultPath, scene.handle) != 0)
                {
                    return VRCForgeToolResult.Failed("Approval-bound prefab result path is no longer absent.");
                }
                var worldPositionStays = p.worldPositionStays ?? true;

                if (p.preview ?? false)
                {
                    var previewPayload = new
                    {
                        action = "instantiate_prefab",
                        preview = true,
                        assetPath = path,
                        prefabGuid,
                        dependencyHash,
                        scenePath,
                        name = instanceName,
                        parentPath = resolvedParentPath,
                        parentGlobalObjectId,
                        expectedResultPath
                    };
                    return VRCForgeToolResult.Completed(
                        parent != null
                            ? $"Preview: would instantiate '{path}' as '{instanceName}' under '{resolvedParentPath}'."
                            : $"Preview: would instantiate '{path}' as '{instanceName}' at the active scene root.",
                        previewPayload);
                }

                if (!string.IsNullOrWhiteSpace(continuationNonce) || (p.approvedContinuationTools?.Length ?? 0) > 0)
                {
                    continuationCount = VRCForgeApprovedObjectReceipt.Reserve(
                        continuationNonce,
                        p.approvedContinuationTools ?? Array.Empty<string>());
                    continuationReserved = true;
                }

                var instance = PrefabUtility.InstantiatePrefab(asset) as GameObject;
                if (instance == null)
                {
                    VRCForgeApprovedObjectReceipt.CancelReservation(continuationNonce);
                    continuationReserved = false;
                    return VRCForgeToolResult.Failed($"Unity refused to instantiate the prefab at '{path}'.");
                }
                mutationStarted = true;
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
                mutatedPath = goPath;
                if (!string.Equals(goPath, expectedResultPath, StringComparison.Ordinal)
                    || AssetPrefabCore.CountHierarchyPath(goPath, scene.handle) != 1)
                {
                    VRCForgeApprovedObjectReceipt.CancelReservation(continuationNonce);
                    continuationReserved = false;
                    return CommittedFailure("Instantiated prefab hierarchy readback did not match the approved target.", mutatedPath, mutatedGlobalObjectId);
                }
                var globalObjectId = GlobalObjectId.GetGlobalObjectIdSlow(instance).ToString();
                mutatedGlobalObjectId = globalObjectId;
                var readbackPrefabPath = PrefabUtility.GetPrefabAssetPathOfNearestInstanceRoot(instance);
                var readbackPrefabGuid = string.IsNullOrEmpty(readbackPrefabPath) ? "" : AssetDatabase.AssetPathToGUID(readbackPrefabPath);
                if (!string.Equals(readbackPrefabGuid, prefabGuid, StringComparison.OrdinalIgnoreCase))
                {
                    VRCForgeApprovedObjectReceipt.CancelReservation(continuationNonce);
                    continuationReserved = false;
                    return CommittedFailure("Instantiated prefab identity readback did not match the approved asset.", mutatedPath, mutatedGlobalObjectId);
                }
                if (continuationReserved)
                {
                    var boundGlobalObjectId = VRCForgeApprovedObjectReceipt.Bind(continuationNonce, instance);
                    continuationReserved = false;
                    if (!string.Equals(boundGlobalObjectId, globalObjectId, StringComparison.Ordinal))
                    {
                        return CommittedFailure("Instantiated prefab continuation identity did not match its readback.", mutatedPath, mutatedGlobalObjectId);
                    }
                }
                var payload = new
                {
                    action = "instantiate_prefab",
                    preview = false,
                    assetPath = path,
                    gameObjectPath = goPath,
                    name = instance.name,
                    parentPath = resolvedParentPath,
                    instanceId = instance.GetInstanceID(),
                    prefabGuid,
                    dependencyHash,
                    scenePath,
                    parentGlobalObjectId,
                    globalObjectId,
                    continuationRegistered = continuationCount > 0,
                    continuationCount
                };
                return VRCForgeToolResult.Completed($"Instantiated '{path}' as '{goPath}'.", payload);
            }
            catch (Exception ex)
            {
                if (continuationReserved)
                {
                    VRCForgeApprovedObjectReceipt.CancelReservation(continuationNonce);
                }
                if (mutationStarted)
                {
                    return CommittedFailure($"Instantiate prefab failed after mutation: {ex.Message}", mutatedPath, mutatedGlobalObjectId);
                }
                return VRCForgeToolResult.Failed($"Instantiate prefab failed: {ex.Message}");
            }
        }

        private static VRCForgeToolResult CommittedFailure(string message, string gameObjectPath, string globalObjectId)
        {
            return VRCForgeToolResult.Failed(message, new
            {
                ok = false,
                committed = true,
                commitState = "unknown",
                checkpointRecoveryRequired = true,
                gameObjectPath = gameObjectPath ?? "",
                globalObjectId = globalObjectId ?? "",
            });
        }
    }

    [VRCForgeCommand(
        toolId: "vrc_unpack_prefab",
        Summary = "Unpack a prefab instance in the scene so its contents become plain GameObjects (Undo-registered). Supports preview mode."
    )]
    public static class UnpackPrefabTool
    {
        public const string ToolName = "vrc_unpack_prefab";

        public class UnpackPrefabParameters
        {
            [VRCForgeInput("Full hierarchy path or unique name of the prefab instance root to unpack.", IsRequired = true)]
            public string gameObjectPath { get; set; } = "";

            [VRCForgeInput("Optional exact GlobalObjectId expected for the prefab instance root.", IsRequired = false)]
            public string expectedGlobalObjectId { get; set; } = "";

            [VRCForgeInput("Optional exact prefab asset GUID expected before unpacking.", IsRequired = false)]
            public string expectedPrefabGuid { get; set; } = "";

            [VRCForgeInput("Optional exact prefab dependency hash expected before unpacking.", IsRequired = false)]
            public string expectedAssetDependencyHash { get; set; } = "";

            [VRCForgeInput("Optional exact scene path expected before unpacking.", IsRequired = false)]
            public string expectedScenePath { get; set; } = "";

            [VRCForgeInput("Approval-generated continuation nonce registered by vrc_instantiate_prefab.", IsRequired = false)]
            public string approvedObjectReceiptNonce { get; set; } = "";

            [VRCForgeInput("Unpack mode: 'outermost' (default, only this prefab layer) or 'completely' (all nested prefabs).", IsRequired = false)]
            public string mode { get; set; } = "outermost";

            [VRCForgeInput("If true, only report what would happen without mutating the scene (default false).", IsRequired = false)]
            public bool? preview { get; set; } = false;
        }

        public static object HandleCommand(JObject @params)
        {
            var p = (@params ?? new JObject()).ToObject<UnpackPrefabParameters>() ?? new UnpackPrefabParameters();
            var mutationStarted = false;
            var mutatedPath = "";
            var mutatedGlobalObjectId = "";
            try
            {
                var go = ComponentCrudCore.ResolveGameObject(p.gameObjectPath);
                var goPath = ComponentCrudCore.GetHierarchyPath(go.transform);
                mutatedPath = goPath;
                var globalObjectId = GlobalObjectId.GetGlobalObjectIdSlow(go).ToString();
                mutatedGlobalObjectId = globalObjectId;
                if (!string.IsNullOrWhiteSpace(p.expectedGlobalObjectId) && !string.Equals(globalObjectId, p.expectedGlobalObjectId.Trim(), StringComparison.Ordinal))
                    return VRCForgeToolResult.Failed("Prefab instance GlobalObjectId drifted from the approved expectation.");
                var prefabPath = PrefabUtility.GetPrefabAssetPathOfNearestInstanceRoot(go);
                var prefabGuid = string.IsNullOrEmpty(prefabPath) ? "" : AssetDatabase.AssetPathToGUID(prefabPath);
                if (!string.IsNullOrWhiteSpace(p.expectedPrefabGuid) && !string.Equals(prefabGuid, p.expectedPrefabGuid.Trim(), StringComparison.OrdinalIgnoreCase))
                    return VRCForgeToolResult.Failed("Prefab asset GUID drifted from the approved expectation.");
                var dependencyHash = string.IsNullOrEmpty(prefabPath) ? "" : AssetDatabase.GetAssetDependencyHash(prefabPath).ToString();
                if (!string.IsNullOrWhiteSpace(p.expectedAssetDependencyHash)
                    && !string.Equals(dependencyHash, p.expectedAssetDependencyHash.Trim(), StringComparison.Ordinal))
                    return VRCForgeToolResult.Failed("Prefab dependency hash drifted from the approved expectation.");
                var scenePath = go.scene.path;
                if (!string.IsNullOrWhiteSpace(p.expectedScenePath)
                    && !string.Equals(scenePath, p.expectedScenePath.Trim(), StringComparison.Ordinal))
                    return VRCForgeToolResult.Failed("Prefab instance scene drifted from the approved expectation.");

                if (!PrefabUtility.IsOutermostPrefabInstanceRoot(go))
                {
                    return VRCForgeToolResult.Failed(
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
                        , globalObjectId
                        , prefabGuid
                        , dependencyHash
                        , scenePath
                    };
                    return VRCForgeToolResult.Completed(
                        $"Preview: would unpack prefab instance '{goPath}' ({modeLabel}).",
                        previewPayload);
                }

                var continuationConsumed = false;
                if (!string.IsNullOrWhiteSpace(p.approvedObjectReceiptNonce))
                {
                    VRCForgeApprovedObjectReceipt.Consume(
                        p.approvedObjectReceiptNonce.Trim(),
                        ToolName,
                        go);
                    continuationConsumed = true;
                }

                PrefabUtility.UnpackPrefabInstance(go, unpackMode, InteractionMode.UserAction);
                mutationStarted = true;
                EditorUtility.SetDirty(go);
                var readbackGlobalObjectId = GlobalObjectId.GetGlobalObjectIdSlow(go).ToString();
                var readbackPrefabPath = PrefabUtility.GetPrefabAssetPathOfNearestInstanceRoot(go);
                var unpacked = string.IsNullOrEmpty(readbackPrefabPath) && !PrefabUtility.IsPartOfAnyPrefab(go);
                if (!unpacked)
                {
                    return CommittedFailure("Prefab unpack readback still reports a prefab instance.", mutatedPath, mutatedGlobalObjectId);
                }

                var payload = new
                {
                    action = "unpack_prefab",
                    preview = false,
                    gameObjectPath = goPath,
                    unpackMode = modeLabel
                    , previousGlobalObjectId = globalObjectId
                    , globalObjectId = readbackGlobalObjectId
                    , prefabGuid
                    , dependencyHash
                    , scenePath
                    , unpacked
                    , continuationConsumed
                };
                return VRCForgeToolResult.Completed($"Unpacked prefab instance '{goPath}' ({modeLabel}).", payload);
            }
            catch (Exception ex)
            {
                if (mutationStarted)
                {
                    return CommittedFailure($"Unpack prefab failed after mutation: {ex.Message}", mutatedPath, mutatedGlobalObjectId);
                }
                return VRCForgeToolResult.Failed($"Unpack prefab failed: {ex.Message}");
            }
        }

        private static VRCForgeToolResult CommittedFailure(string message, string gameObjectPath, string globalObjectId)
        {
            return VRCForgeToolResult.Failed(message, new
            {
                ok = false,
                committed = true,
                commitState = "unknown",
                checkpointRecoveryRequired = true,
                gameObjectPath = gameObjectPath ?? "",
                globalObjectId = globalObjectId ?? "",
            });
        }
    }
}
