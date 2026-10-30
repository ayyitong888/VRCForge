using System;
using System.Linq;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEngine;
using VRCForge.Core.MCP;

namespace VRCForge.Editor.Generic
{
    [VRCForgeCommand(
        toolId: "vrc_save_asset",
        Summary = "When to use: explicitly save one existing project asset by its Assets/... path after a user-requested edit. When NOT to use: never save all assets, save implicitly, or use for scenes; use vrc_scene_save for scenes. Negative example: do not call this merely because an asset is dirty.")]
    public static class UnityAssetSaveTool
    {
        public sealed class Parameters
        {
            [VRCForgeInput("Exact existing project asset path beginning with Assets/.", IsRequired = true)]
            public string assetPath { get; set; }

            [VRCForgeInput("Return the current save plan without writing.", IsRequired = false, DefaultLiteral = "false")]
            public bool? preview { get; set; }
        }

        public static object HandleCommand(JObject parameters)
        {
            var path = parameters?["assetPath"]?.Type == JTokenType.String
                ? parameters["assetPath"].Value<string>() ?? string.Empty
                : string.Empty;
            var preview = parameters?["preview"]?.Value<bool?>() ?? false;

            if (!IsAssetPath(path))
                return VRCForgeToolResult.RejectedBeforeMutation("invalid_asset_path",
                    "assetPath must be one exact existing project asset path beginning with Assets/.", "unity_core_tool", "pre_mutation_validation");

            var asset = AssetDatabase.LoadMainAssetAtPath(path);
            if (asset == null)
                return VRCForgeToolResult.RejectedBeforeMutation("asset_not_found",
                    "No asset exists at assetPath: " + path, "unity_core_tool", "pre_mutation_validation");
            var loadedPath = (AssetDatabase.GetAssetPath(asset) ?? string.Empty).Replace('\\', '/');
            if (asset is SceneAsset)
                return VRCForgeToolResult.RejectedBeforeMutation("scene_requires_scene_save",
                    "This path is a Unity scene. Use vrc_scene_save to save scenes: " + loadedPath, "unity_core_tool", "pre_mutation_validation");
            path = loadedPath;

            var wasDirty = EditorUtility.IsDirty(asset);
            if (preview)
                return VRCForgeToolResult.Completed("Previewed one project asset save.", new
                {
                    schema = "vrcforge.asset_save.v1", ok = true, preview = true, assetPath = path, wasDirty,
                    saved = false, noChange = !wasDirty,
                    changed = false, commitState = "not_started", commitStateKnown = true,
                    mutationStarted = false, mutationApplied = false, committed = false,
                    verified = true, verification = "asset_loaded_and_dirty_state_read"
                });

            if (!wasDirty)
                return VRCForgeToolResult.Completed("Asset is already clean; nothing was saved.", new
                {
                    schema = "vrcforge.asset_save.v1", ok = true, preview = false, assetPath = path, wasDirty = false,
                    saved = false, noChange = true,
                    changed = false, commitState = "no_change", commitStateKnown = true,
                    mutationStarted = false, mutationApplied = false, committed = true,
                    verified = true, verification = "asset_is_clean"
                });

            try
            {
                AssetDatabase.SaveAssetIfDirty(asset);
            }
            catch (Exception exception)
            {
                return SaveStateUnknown(path, exception.GetType().Name + ": " + exception.Message);
            }

            var remainsDirty = EditorUtility.IsDirty(asset);
            if (remainsDirty)
                return SaveStateUnknown(path, "Unity returned from SaveAssetIfDirty, but the asset remains dirty.");

            return VRCForgeToolResult.Completed("Saved the requested asset; Unity reports it clean.", new
            {
                schema = "vrcforge.asset_save.v1", ok = true, preview = false, assetPath = path, wasDirty = true,
                saved = true, noChange = false,
                changed = true, commitState = "committed", commitStateKnown = true,
                mutationStarted = true, mutationApplied = true, committed = true,
                verified = true, verification = "asset_is_clean_after_save"
            });
        }

        private static object SaveStateUnknown(string path, string reason)
        {
            return VRCForgeToolResult.FailedWithCode("asset_save_unconfirmed", "Could not confirm the save result for " + path + ": " + reason,
                new
                {
                    schema = "vrcforge.asset_save_failure.v1", ok = false, preview = false,
                    assetPath = path, wasDirty = true, saved = (bool?)null, noChange = false,
                    mutationStarted = true, mutationApplied = (bool?)null, committed = (bool?)null,
                    verified = false, commitState = "unknown", commitStateKnown = false,
                    checkpointRecoveryRequired = true, retryable = false
                });
        }

        private static bool IsAssetPath(string path)
        {
            return !string.IsNullOrWhiteSpace(path)
                && path.StartsWith("Assets/", StringComparison.Ordinal)
                && !path.Contains("\\")
                && !path.Split('/').Any(part => part.Length == 0 || part == "." || part == "..");
        }
    }
}
