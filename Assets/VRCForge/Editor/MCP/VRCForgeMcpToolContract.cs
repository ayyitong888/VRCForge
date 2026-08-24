using System;
using System.Collections.Generic;
using System.Linq;
using VRCForge.Core.MCP;

namespace VRCForge.Editor
{
    /// <summary>
    /// The packaged Core contract.  This is deliberately not a discovery
    /// mechanism: a project can add arbitrary assemblies, but it cannot add a
    /// tool to the VRCForge App surface.
    /// </summary>
    internal static class VRCForgeMcpToolContract
    {
        internal const string CoreIdentity = "vrcforge.unity-core";
        internal const string HandshakeProtocol = "vrcforge.core-handshake.v1";
        internal const string ProductVersion = "1.7.9";
        internal const string ToolContractVersion = "78";
        internal const int ToolCount = 78;

        private static readonly Dictionary<string, string> ExpectedTypes =
            new Dictionary<string, string>(StringComparer.Ordinal)
            {
                { "vrc_add_component", "VRCForge.Editor.AddComponentTool" },
                { "vrc_add_modular_avatar_component", "VRCForge.Editor.MAComponentWriter" },
                { "vrc_add_outfit_part", "VRCForge.Editor.WardrobeOutfitPartWriter" },
                { "vrc_add_wardrobe_outfit", "VRCForge.Editor.WardrobeOutfitWriter" },
                { "vrc_apply_blendshapes", "VRCForge.Editor.BlendshapeApplier" },
                { "vrc_apply_clothing_fx", "VRCForge.Editor.ClothingFxAuthor" },
                { "vrc_apply_material_tuning", "VRCForge.Editor.MaterialTuningApplier" },
                { "vrc_apply_parameter_optimization", "VRCForge.Editor.AvatarParameterOptimizationApplier" },
                { "vrc_avatar_upload_readiness", "VRCForge.Editor.VrchatAvatarUploadReadinessTool" },
                { "vrc_atomic_reference_rename", "VRCForge.Editor.AtomicReferenceRenameTool" },
                { "vrc_build_parameter_bit_packed_clone", "VRCForge.Editor.ParameterBitPackingTool" },
                { "vrc_build_and_upload_avatar", "VRCForge.Editor.VrchatAvatarUploadTool" },
                { "vrc_build_test_avatar", "VRCForge.Editor.VrchatBuildTestTool" },
                { "vrc_capture_scene_view", "VRCForge.Editor.SceneViewCaptureTool" },
                { "vrc_create_component_feature", "VRCForge.Editor.ComponentFeatureWriterTool" },
                { "vrc_create_gameobject", "VRCForge.Editor.CreateGameObjectTool" },
                { "vrc_create_safe_backup", "VRCForge.Editor.ConsoleTools" },
                { "vrc_convert_unity_constraint", "VRCForge.Editor.VrchatConstraintConversionTool" },
                { "vrc_delete_gameobject", "VRCForge.Editor.DeleteGameObjectTool" },
                { "vrc_duplicate_scene_object", "VRCForge.Editor.DuplicateSceneObjectTool" },
                { "vrc_duplicate_project_asset", "VRCForge.Editor.DuplicateProjectAssetTool" },
                { "vrc_ensure_animator_state", "VRCForge.Editor.EnsureAnimatorStateTool" },
                { "vrc_ensure_expression_menu_control", "VRCForge.Editor.EnsureExpressionMenuControlTool" },
                { "vrc_ensure_expression_parameter", "VRCForge.Editor.EnsureExpressionParameterTool" },
                { "vrc_export_blendshapes", "VRCForge.Editor.BlendshapeExporter" },
                { "vrc_export_vrm", "VRCForge.Editor.VrmExporter" },
                { "vrc_find_assets", "VRCForge.Editor.FindAssetsTool" },
                { "vrc_get_asset_info", "VRCForge.Editor.GetAssetInfoTool" },
                { "vrc_get_compile_errors", "VRCForge.Editor.CompileErrorReader" },
                { "vrc_get_gameobject", "VRCForge.Editor.GetGameObjectTool" },
                { "vrc_get_property", "VRCForge.Editor.GetPropertyTool" },
                { "vrc_gesture_manager_enter_play_mode", "VRCForge.Editor.GestureManagerEnterPlayModeTool" },
                { "vrc_gesture_manager_set_parameter", "VRCForge.Editor.GestureManagerRuntimeParameterTool" },
                { "vrc_import_unitypackage", "VRCForge.Editor.UnityPackageImporterTool" },
                { "vrc_inspect_skinned_mesh_bone_usage", "VRCForge.Editor.InspectSkinnedMeshBoneUsageTool" },
                { "vrc_inspect_modular_avatar_component", "VRCForge.Editor.MAComponentInspector" },
                { "vrc_inspect_primitive_basis_fixture", "VRCForge.Editor.PrimitiveBasisFixtureInspector" },
                { "vrc_instantiate_prefab", "VRCForge.Editor.InstantiatePrefabTool" },
                { "vrc_manage_expression_menu", "VRCForge.Editor.ManageExpressionMenuTool" },
                { "vrc_manage_expression_parameters", "VRCForge.Editor.ManageExpressionParametersTool" },
                { "vrc_manage_fx_animator", "VRCForge.Editor.ManageFxAnimatorTool" },
                { "vrc_manage_wardrobe", "VRCForge.Editor.WardrobeManagerWriter" },
                { "vrc_prepare_checkpoint", "VRCForge.Editor.CheckpointPrepareTool" },
                { "vrc_read_avatar_descriptor", "VRCForge.Editor.ReadAvatarDescriptorTool" },
                { "vrc_read_vrchat_sdk_builder_alerts", "VRCForge.Editor.VrchatSdkBuilderAlertsTool" },
                { "vrc_refresh_asset_database", "VRCForge.Editor.AssetDatabaseRefreshTool" },
                { "vrc_reload_after_checkpoint_restore", "VRCForge.Editor.CheckpointReloadTool" },
                { "vrc_reload_primitive_basis_fixture", "VRCForge.Editor.PrimitiveBasisFixtureReloader" },
                { "vrc_remove_component", "VRCForge.Editor.RemoveComponentTool" },
                { "vrc_rename_gameobject", "VRCForge.Editor.RenameGameObjectTool" },
                { "vrc_reparent_gameobject", "VRCForge.Editor.ReparentGameObjectTool" },
                { "vrc_restore_safe_backup", "VRCForge.Editor.PrefabTools" },
                { "vrc_rollback_avatar_parameters", "VRCForge.Editor.AvatarParameterRollbackTool" },
                { "vrc_save_scene_object_as_prefab", "VRCForge.Editor.SaveSceneObjectAsPrefabTool" },
                { "vrc_save_current_scene", "VRCForge.Editor.SaveCurrentSceneTool" },
                { "vrc_save_new_scene", "VRCForge.Editor.SaveNewSceneTool" },
                { "vrc_select_scene_object", "VRCForge.Editor.SelectSceneObjectTool" },
                { "vrc_scan_animation_bindings", "VRCForge.Editor.AssetTools" },
                { "vrc_scan_avatar_controls", "VRCForge.Editor.AvatarControlScanner" },
                { "vrc_scan_avatar_items", "VRCForge.Editor.GameObjectTools" },
                { "vrc_scan_avatar_materials", "VRCForge.Editor.ShaderMaterialScanner" },
                { "vrc_scan_avatar_parameters", "VRCForge.Editor.AvatarParameterScanner" },
                { "vrc_scan_avatar_performance", "VRCForge.Editor.AvatarPerformanceTool" },
                { "vrc_scan_fx_animator", "VRCForge.Editor.ComponentTools" },
                { "vrc_scan_inbound_reference_closure", "VRCForge.Editor.InboundReferenceClosureTool" },
                { "vrc_scan_thry_avatar_performance", "VRCForge.Editor.ThryAvatarPerformanceTool" },
                { "vrc_scan_wardrobe", "VRCForge.Editor.WardrobeScanner" },
                { "vrc_set_constraint_sources", "VRCForge.Editor.ConstraintSourceTool" },
                { "vrc_set_gameobject_active", "VRCForge.Editor.SetGameObjectActiveTool" },
                { "vrc_set_material_shader", "VRCForge.Editor.MaterialShaderTool" },
                { "vrc_set_play_mode", "VRCForge.Editor.SetPlayModeTool" },
                { "vrc_set_property", "VRCForge.Editor.SetPropertyTool" },
                { "vrc_set_texture_import_settings", "VRCForge.Editor.TextureImportSettingsTool" },
                { "vrc_setup_outfit", "VRCForge.Editor.SetupOutfitTool" },
                { "vrc_toggle_scene_object", "VRCForge.Editor.SceneObjectToggler" },
                { "vrc_unpack_prefab", "VRCForge.Editor.UnpackPrefabTool" },
                { "vrc_write_animation_curve", "VRCForge.Editor.WriteAnimationCurveTool" },
                { "vrc_write_avatar_descriptor", "VRCForge.Editor.WriteAvatarDescriptorTool" },
            };

        private static readonly HashSet<string> ExpectedReadOnlyNames =
            new HashSet<string>(StringComparer.Ordinal)
            {
                "vrc_find_assets",
                "vrc_get_asset_info",
                "vrc_get_compile_errors",
                "vrc_get_gameobject",
                "vrc_get_property",
                "vrc_inspect_skinned_mesh_bone_usage",
                "vrc_inspect_modular_avatar_component",
                "vrc_inspect_primitive_basis_fixture",
                "vrc_read_avatar_descriptor",
                "vrc_read_vrchat_sdk_builder_alerts",
                "vrc_avatar_upload_readiness",
                "vrc_scan_inbound_reference_closure",
            };

        // Planning exposes direct reads plus tools that Core can execute through
        // its exact no-write payload lane. All other tools remain execution-only.
        private static readonly HashSet<string> ExpectedPlanningNames =
            new HashSet<string>(ExpectedReadOnlyNames, StringComparer.Ordinal)
            {
                "vrc_capture_scene_view",
                "vrc_export_blendshapes",
                "vrc_scan_animation_bindings",
                "vrc_scan_avatar_controls",
                "vrc_scan_avatar_items",
                "vrc_scan_avatar_materials",
                "vrc_scan_avatar_parameters",
                "vrc_scan_avatar_performance",
                "vrc_scan_fx_animator",
                "vrc_scan_inbound_reference_closure",
                "vrc_scan_thry_avatar_performance",
                "vrc_scan_wardrobe",
            };

        internal static ISet<string> ExpectedToolNames
        {
            get { return new HashSet<string>(ExpectedTypes.Keys, StringComparer.Ordinal); }
        }

        internal static ISet<string> ExpectedReadOnlyToolNames
        {
            get { return new HashSet<string>(ExpectedReadOnlyNames, StringComparer.Ordinal); }
        }

        internal static ISet<string> ExpectedPlanningToolNames
        {
            get { return new HashSet<string>(ExpectedPlanningNames, StringComparer.Ordinal); }
        }

        internal static VRCForgeToolDescriptor[] SnapshotExact(IEnumerable<VRCForgeToolDescriptor> candidates)
        {
            var snapshot = (candidates ?? Enumerable.Empty<VRCForgeToolDescriptor>())
                .OrderBy(item => item == null ? string.Empty : item.Name, StringComparer.Ordinal)
                .ToArray();
            if (snapshot.Length != ToolCount || ExpectedTypes.Count != ToolCount)
            {
                throw new InvalidOperationException("The packaged VRCForge MCP tool contract is incomplete.");
            }
            var actualNames = new HashSet<string>(StringComparer.Ordinal);
            foreach (var descriptor in snapshot)
            {
                if (descriptor == null || string.IsNullOrEmpty(descriptor.Name)
                    || !actualNames.Add(descriptor.Name) || !IsExpectedDescriptor(descriptor))
                {
                    throw new InvalidOperationException("The loaded VRCForge MCP tool registry does not match the packaged contract.");
                }
            }
            if (!actualNames.SetEquals(ExpectedTypes.Keys))
            {
                throw new InvalidOperationException("The loaded VRCForge MCP tool registry does not match the packaged contract.");
            }
            return snapshot;
        }

        internal static bool IsExpectedToolName(string toolName)
        {
            return !string.IsNullOrEmpty(toolName) && ExpectedTypes.ContainsKey(toolName);
        }

        internal static bool IsExpectedDescriptor(VRCForgeToolDescriptor descriptor)
        {
            string expectedType;
            return descriptor != null
                && !string.IsNullOrEmpty(descriptor.Name)
                && ExpectedTypes.TryGetValue(descriptor.Name, out expectedType)
                && descriptor.ToolType != null
                && string.Equals(descriptor.ToolType.FullName, expectedType, StringComparison.Ordinal)
                && descriptor.Permission == (ExpectedReadOnlyNames.Contains(descriptor.Name)
                    ? VRCForgeCommandAccess.ReadOnly
                    : VRCForgeCommandAccess.RequiresApproval);
        }
    }
}
