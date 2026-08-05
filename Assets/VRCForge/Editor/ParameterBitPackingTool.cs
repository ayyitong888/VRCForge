using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Runtime.InteropServices;
using System.Security.Cryptography;
using System.Text;
using Microsoft.Win32.SafeHandles;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEditor.Animations;
using UnityEditor.PackageManager;
using UnityEngine;
using UnityEngine.SceneManagement;
using VRC.SDK3.Avatars.Components;
using VRC.SDK3.Avatars.ScriptableObjects;
using VRC.SDKBase.Editor.BuildPipeline;
using Object = UnityEngine.Object;
using VRCForge.Core.MCP;

namespace VRCForge.Editor
{
    [VRCForgeCommand(
        toolId: "vrc_build_parameter_bit_packed_clone",
        Summary = "Preview or build one verified parameter-packed avatar clone through the public avatar preprocess pipeline."
    )]
    public static class ParameterBitPackingTool
    {
        public class Parameters
        {
            [VRCForgeInput("Saved source scene asset path.", IsRequired = true)] public string sourceScenePath { get; set; } = "";
            [VRCForgeInput("Source avatar hierarchy path.", IsRequired = true)] public string sourceAvatarPath { get; set; } = "";
            [VRCForgeInput("Name for the generated clone.", IsRequired = true)] public string outputCloneName { get; set; } = "";
            [VRCForgeInput("Return the verified plan without mutation.", IsRequired = false, DefaultLiteral = "false")] public bool? preview { get; set; } = false;
            [VRCForgeInput("Run public build callbacks. Must be false for preview and true for apply.", IsRequired = false, DefaultLiteral = "false")] public bool? runBuildCallbacks { get; set; } = false;
            [VRCForgeInput("Must remain false; this tool never saves a scene.", IsRequired = false, DefaultLiteral = "false")] public bool? saveScene { get; set; } = false;
            [VRCForgeInput("Verified project path from preview; required for apply.", IsRequired = false)] public string expectedProjectPath { get; set; } = "";
            [VRCForgeInput("Verified source or output digest/identity receipt from preview; required for apply where named.", IsRequired = false)] public string expectedSourceSceneGuid { get; set; } = "";
            [VRCForgeInput("Verified source scene file digest from preview; required for apply.", IsRequired = false)] public string expectedSourceSceneFileDigest { get; set; } = "";
            [VRCForgeInput("Verified source scene metadata digest from preview; required for apply.", IsRequired = false)] public string expectedSourceSceneMetaDigest { get; set; } = "";
            [VRCForgeInput("Verified source avatar global object ID from preview; required for apply.", IsRequired = false)] public string expectedSourceGlobalObjectId { get; set; } = "";
            [VRCForgeInput("Verified source hierarchy digest from preview; required for apply.", IsRequired = false)] public string expectedSourceHierarchyDigest { get; set; } = "";
            [VRCForgeInput("Verified source state digest from preview; required for apply.", IsRequired = false)] public string expectedSourceStateDigest { get; set; } = "";
            [VRCForgeInput("Verified source asset-set digest from preview; required for apply.", IsRequired = false)] public string expectedSourceAssetSetDigest { get; set; } = "";
            [VRCForgeInput("Verified source asset count from preview; required for apply.", IsRequired = false)] public int? expectedSourceAssetCount { get; set; }
            [VRCForgeInput("Verified parameter state digest from preview; required for apply.", IsRequired = false)] public string expectedParameterStateDigest { get; set; } = "";
            [VRCForgeInput("Verified controller state digest from preview; required for apply.", IsRequired = false)] public string expectedControllerStateDigest { get; set; } = "";
            [VRCForgeInput("Verified menu state digest from preview; required for apply.", IsRequired = false)] public string expectedMenuStateDigest { get; set; } = "";
            [VRCForgeInput("Verified source behavior evidence digest from preview; required for apply.", IsRequired = false)] public string expectedSourceBehaviorEvidenceDigest { get; set; } = "";
            [VRCForgeInput("Verified source cost bits from preview; required for apply.", IsRequired = false)] public int? expectedSourceCostBits { get; set; }
            [VRCForgeInput("Verified parameter count from preview; required for apply.", IsRequired = false)] public int? expectedParameterCount { get; set; }
            [VRCForgeInput("Verified safe candidate digest from preview; required for apply.", IsRequired = false)] public string expectedSafeCandidateDigest { get; set; } = "";
            [VRCForgeInput("Verified safe candidate count from preview; required for apply.", IsRequired = false)] public int? expectedSafeCandidateCount { get; set; }
            [VRCForgeInput("Verified excluded parameter digest from preview; required for apply.", IsRequired = false)] public string expectedExcludedDigest { get; set; } = "";
            [VRCForgeInput("Verified excluded parameter count from preview; required for apply.", IsRequired = false)] public int? expectedExcludedCount { get; set; }
            [VRCForgeInput("Verified capability digest from preview; required for apply.", IsRequired = false)] public string expectedCapabilityDigest { get; set; } = "";
            [VRCForgeInput("Verified package root identity digest from preview; required for apply.", IsRequired = false)] public string expectedPackageRootIdentityDigest { get; set; } = "";
            [VRCForgeInput("Verified project root identity digest from preview; required for apply.", IsRequired = false)] public string expectedRootIdentityDigest { get; set; } = "";
            [VRCForgeInput("Verified project root identity count from preview; required for apply.", IsRequired = false)] public int? expectedRootIdentityCount { get; set; }
            [VRCForgeInput("Verified generated-root existence from preview; required for apply.", IsRequired = false)] public bool? expectedGeneratedRootExistsBefore { get; set; }
            [VRCForgeInput("Verified generated tree digest from preview; required for apply.", IsRequired = false)] public string expectedGeneratedTreeDigestBefore { get; set; } = "";
            [VRCForgeInput("Verified generated tree entry count from preview; required for apply.", IsRequired = false)] public int? expectedGeneratedEntryCountBefore { get; set; }
            [VRCForgeInput("Verified generated tree content digest from preview; required for apply.", IsRequired = false)] public string expectedGeneratedContentDigestBefore { get; set; } = "";
            [VRCForgeInput("Verified generated tree byte count from preview; required for apply.", IsRequired = false)] public long? expectedGeneratedByteCountBefore { get; set; }
            [VRCForgeInput("Verified auxiliary package root identity digest from preview; required for apply.", IsRequired = false)] public string expectedAuxiliaryPackageRootIdentityDigest { get; set; } = "";
            [VRCForgeInput("Verified auxiliary package manifest digest from preview; required for apply.", IsRequired = false)] public string expectedAuxiliaryPackageManifestDigest { get; set; } = "";
            [VRCForgeInput("Verified auxiliary package manifest identity from preview; required for apply.", IsRequired = false)] public string expectedAuxiliaryPackageManifestIdentityDigest { get; set; } = "";
            [VRCForgeInput("Verified auxiliary generated-root existence from preview; required for apply.", IsRequired = false)] public bool? expectedAuxiliaryRootExistsBefore { get; set; }
            [VRCForgeInput("Verified auxiliary tree digest from preview; required for apply.", IsRequired = false)] public string expectedAuxiliaryTreeDigestBefore { get; set; } = "";
            [VRCForgeInput("Verified auxiliary tree content digest from preview; required for apply.", IsRequired = false)] public string expectedAuxiliaryContentDigestBefore { get; set; } = "";
            [VRCForgeInput("Verified auxiliary tree entry count from preview; required for apply.", IsRequired = false)] public int? expectedAuxiliaryEntryCountBefore { get; set; }
            [VRCForgeInput("Verified auxiliary tree byte count from preview; required for apply.", IsRequired = false)] public long? expectedAuxiliaryByteCountBefore { get; set; }
            [VRCForgeInput("Verified preference digest from preview; required for apply.", IsRequired = false)] public string expectedPreferenceDigest { get; set; } = "";
            [VRCForgeInput("Verified protected tree digest from preview; required for apply.", IsRequired = false)] public string expectedProtectedTreeDigestBefore { get; set; } = "";
            [VRCForgeInput("Verified protected tree entry count from preview; required for apply.", IsRequired = false)] public int? expectedProtectedEntryCountBefore { get; set; }
            [VRCForgeInput("Verified output scene name from preview; required for apply.", IsRequired = false)] public string expectedOutputSceneName { get; set; } = "";
            [VRCForgeInput("Verified output prefab path from preview; required for apply.", IsRequired = false)] public string expectedOutputPrefabPath { get; set; } = "";
            [VRCForgeInput("Verified output tree digest from preview; required for apply.", IsRequired = false)] public string expectedOutputTreeDigestBefore { get; set; } = "";
            [VRCForgeInput("Verified output tree entry count from preview; required for apply.", IsRequired = false)] public int? expectedOutputEntryCountBefore { get; set; }
            [VRCForgeInput("Verified output-root existence from preview; required for apply.", IsRequired = false)] public bool? expectedOutputRootExistsBefore { get; set; }
            [VRCForgeInput("Verified preview digest from preview; required for apply.", IsRequired = false)] public string expectedPreviewDigest { get; set; } = "";
        }

        private const string ResultSchema = "vrcforge.parameter_bit_packing.v2";
        private const string CapabilitySchema = "vrcforge.parameter_capability.v2";
        private const string CallbackAssemblySetSchema = "vrcforge.avatar_callback_assembly_set.v1";
        private const string PackageTreeSchema = "vrcforge.package_tree.v1";
        private const string GeneratedTreeSchema = "vrcforge.generated_tree.v1";
        private const string AuxiliaryGeneratedTreeSchema = "vrcforge.parameter_auxiliary_tree.v1";
        private const string AuxiliarySnapshotSchema = "vrcforge.parameter_auxiliary_snapshot.v1";
        private const string OutputTreeSchema = "vrcforge.parameter_output_tree.v1";
        private const string ProtectedTreeSchema = "vrcforge.protected_project_tree.v1";
        private const string RootIdentitySchema = "vrcforge.parameter_project_roots.v1";
        private const string ApplyReceiptSchema = "vrcforge.parameter_bit_packing_apply_receipt.v2";
        private const string SafeNamesSchema = "vrcforge.safe_parameter_names.v1";
        private const string CompressedNamesSchema = "vrcforge.compressed_parameter_names.v1";
        private const string ExcludedSchema = "vrcforge.excluded_parameters.v1";
        private const string CacheJournalSchema = "vrcforge.parameter_cache_journal.v1";
        private const string AuxiliaryJournalSchema = "vrcforge.parameter_auxiliary_journal.v1";
        private const string CacheContentSchema = "vrcforge.parameter_cache_content.v1";
        private const string OutputManifestSchema = "vrcforge.parameter_output_manifest.v1";
        private const string PreferenceSchema = "vrcforge.parameter_preferences.v1";
        private const string PackageId = "com.vrcfury.vrcfury";
        private const string PackageVersion = "1.1334.0";
        private const string PackageAuthor = "VRCFury";
        private const string PackageArchiveSha256 = "01c750a3f87d3003ac31e23345e0e3afb43a790c2aeb2c43ed933cd46efafcfe";
        private const string PackageTreeSha256 = "230340bd6eef1e633b18cc9587c91b71ff30143e85cb9732b5208fffcdd076d2";
        private const int PackageFileCount = 1255;
        private const string CallbackAssemblyName = "VRCFury-Editor-Avatars";
        private const string CallbackAssemblyVersion = "0.0.0.0";
        private const string CallbackAssemblyPublicKeyToken = "";
        private const string SdkCallbackAssemblyName = "VRCSDKBase-Editor";
        private const string SdkCallbackAssemblyVersion = "1.0.0.0";
        private const string SdkCallbackAssemblyPublicKeyToken = "";
        private static readonly HashSet<string> SdkCallbackAssemblySha256Allowlist = new HashSet<string>(StringComparer.Ordinal)
        {
            "952abdd2e9f696acba1fa773402d824fac4f0c6dd0b1b3488df8e4a3d870eba9",
            "459431464320780e90fdbccbd36c1d0657a4a74ec65e87afd8c68530725d080b"
        };
        private const string CallbackTypeName = "VRC.SDKBase.Editor.BuildPipeline.VRCBuildPipelineCallbacks";
        private const string CallbackSignature = "public static System.Boolean OnPreprocessAvatar(UnityEngine.GameObject)";
        private const string RegisteredHookType = "VF.Hooks.ParameterCompressorHook";
        private const string PackageRuntimeAssemblyName = "VRCFury";
        private const string PackageComponentTypeName = "VF.Model.VRCFury";
        private const string NonInteractiveFeatureTypeName = "VF.Model.Feature.FixWriteDefaults";
        private const string NonInteractiveFeatureModeName = "Disabled";
        private static readonly CapabilityProfile[] CapabilityProfiles =
        {
            new CapabilityProfile
            {
                Id = "embedded-minimal-v1",
                CallbackAssemblySha256 = "e568293abe29428b7fb35d805cb3053cc8437621a19ae714d5fc76931d9fe10f",
                CallbackRosterCount = 16,
                CallbackRosterDigest = "305bc43e713cc76fe13f16d99e6e1d7137d87c066d6a46a6917196b909de10ba",
                CallbackAssemblySetCount = 3,
                CallbackAssemblySetDigest = "1884970046bc7b2f7194cef03c3c085dffb02df8cc6eddc9173e90fd231794d1"
            },
            new CapabilityProfile
            {
                Id = "embedded-extended-v1",
                CallbackAssemblySha256 = "c220c73e91f69aa88425c8cd81cf271a6b484eb5b34cca15a33f6edcde89c8f4",
                CallbackRosterCount = 23,
                CallbackRosterDigest = "a345576b0aad61991a4518413a5685d3b9df85e9ad33af50ff6b04a71d0f920e",
                CallbackAssemblySetCount = 7,
                CallbackAssemblySetDigest = "2eebf5d668c881ac7b208191e488c6a69c896549473fb44281d12c07404dc221"
            },
            new CapabilityProfile
            {
                Id = "embedded-sdk-3-10-4-v1",
                CallbackAssemblySha256 = "4308c899e3b978a101e7cf3bfd117887b1fdf688ce53f559d8bf45106c6e34a0",
                CallbackRosterCount = 21,
                CallbackRosterDigest = "539f2d064d593e4e6010632f81e655c89fbece418b6249dcd6b641129ca78c96",
                CallbackAssemblySetCount = 5,
                CallbackAssemblySetDigest = "4b2ca1bdeef87a1a926e383e0036992875c4af8ee702e6da55107be5476279b6"
            }
        };
        private const string GeneratedRoot = "Packages/com.vrcfury.temp/Builds";
        private const string StagingFolderName = "VRCForge Input";
        private const string StagingRoot = GeneratedRoot + "/" + StagingFolderName;
        private const string AuxiliaryPackageRoot = "Packages/nadena.dev.ndmf";
        private const string AuxiliaryPackageManifest = AuxiliaryPackageRoot + "/package.json";
        private const string AuxiliaryGeneratedRoot = AuxiliaryPackageRoot + "/__Generated";
        private const string OutputRoot = "Assets/VRCForge/Generated";
        private const string OutputKindRoot = OutputRoot + "/ParameterBitPacking";
        private const string TempPackageManifest = "Packages/com.vrcfury.temp/package.json";
        private const string PackageAssetManifest = "Packages/com.vrcfury.vrcfury/package.json";
        private const string CompressorPreferenceKey = "com.vrcfury.parameterCompressor";
        private const string AlignMobilePreferenceKey = "com.vrcfury.alignMobile";
        private const int CacheBackupMaxEntries = 100000;
        private const long CacheBackupMaxBytes = 536870912;
        private const int RegisteredAssetPathScanLimit = 100000;
        private const int RegisteredAssetObjectScanLimit = 500000;
        private const int OpenProjectSceneScanLimit = 128;
        private const int TransactionIoRetryAttempts = 8;
        private const int TransactionIoRetryBaseDelayMilliseconds = 25;
        private const uint NativeOpenExisting = 3;
        private const uint NativeFileShareRead = 0x00000001;
        private const uint NativeFileShareWrite = 0x00000002;
        private const uint NativeFileAttributeNormal = 0x00000080;
        private const uint NativeFileFlagBackupSemantics = 0x02000000;
        private const uint NativeFileFlagOpenReparsePoint = 0x00200000;

        private static readonly string[] ProtectedProjectRoots =
        {
            "Assets",
            "Packages",
            "ProjectSettings"
        };

        private static readonly HashSet<string> WindowsReservedFileStems = new HashSet<string>(StringComparer.OrdinalIgnoreCase)
        {
            "CON", "PRN", "AUX", "NUL",
            "COM1", "COM2", "COM3", "COM4", "COM5", "COM6", "COM7", "COM8", "COM9",
            "LPT1", "LPT2", "LPT3", "LPT4", "LPT5", "LPT6", "LPT7", "LPT8", "LPT9"
        };

        private static readonly HashSet<string> PreviewKeys = new HashSet<string>(StringComparer.Ordinal)
        {
            "sourceScenePath",
            "sourceAvatarPath",
            "outputCloneName",
            "preview",
            "runBuildCallbacks",
            "saveScene"
        };

        private static readonly HashSet<string> ApplyKeys = new HashSet<string>(PreviewKeys, StringComparer.Ordinal)
        {
            "expectedProjectPath",
            "expectedSourceSceneGuid",
            "expectedSourceSceneFileDigest",
            "expectedSourceSceneMetaDigest",
            "expectedSourceGlobalObjectId",
            "expectedSourceHierarchyDigest",
            "expectedSourceStateDigest",
            "expectedSourceAssetSetDigest",
            "expectedSourceAssetCount",
            "expectedParameterStateDigest",
            "expectedControllerStateDigest",
            "expectedMenuStateDigest",
            "expectedSourceBehaviorEvidenceDigest",
            "expectedSourceCostBits",
            "expectedParameterCount",
            "expectedSafeCandidateDigest",
            "expectedSafeCandidateCount",
            "expectedExcludedDigest",
            "expectedExcludedCount",
            "expectedCapabilityDigest",
            "expectedPackageRootIdentityDigest",
            "expectedRootIdentityDigest",
            "expectedRootIdentityCount",
            "expectedGeneratedRootExistsBefore",
            "expectedGeneratedTreeDigestBefore",
            "expectedGeneratedEntryCountBefore",
            "expectedGeneratedContentDigestBefore",
            "expectedGeneratedByteCountBefore",
            "expectedAuxiliaryPackageRootIdentityDigest",
            "expectedAuxiliaryPackageManifestDigest",
            "expectedAuxiliaryPackageManifestIdentityDigest",
            "expectedAuxiliaryRootExistsBefore",
            "expectedAuxiliaryTreeDigestBefore",
            "expectedAuxiliaryContentDigestBefore",
            "expectedAuxiliaryEntryCountBefore",
            "expectedAuxiliaryByteCountBefore",
            "expectedPreferenceDigest",
            "expectedProtectedTreeDigestBefore",
            "expectedProtectedEntryCountBefore",
            "expectedOutputSceneName",
            "expectedOutputPrefabPath",
            "expectedOutputTreeDigestBefore",
            "expectedOutputEntryCountBefore",
            "expectedOutputRootExistsBefore",
            "expectedPreviewDigest"
        };

        public static object HandleCommand(JObject @params)
        {
            var mutationStarted = false;
            var operationStage = "request_validation";
            Scene outputScene = default;
            SourceSnapshot beforeSource = null;
            TreeSnapshot beforeGenerated = null;
            AuxiliaryGeneratedSnapshot beforeAuxiliary = null;
            TreeSnapshot beforeOutput = null;
            TreeSnapshot beforeProtected = null;
            RootIdentitySnapshot beforeRoots = null;
            StableInputLeases stableInputLeases = null;
            StableInputLeases stableOutputLeases = null;
            CacheTransaction cacheTransaction = null;
            AuxiliaryGeneratedTransaction auxiliaryTransaction = null;
            AssetTreeManifest stagedOutputManifest = null;
            AssetTreeManifest outputManifest = null;
            var createdOutputFolders = new List<CreatedAssetFolder>();
            string outputCloneName = null;
            try
            {
                Require(Application.platform == RuntimePlatform.WindowsEditor, "Parameter bit-packing requires the Windows editor.");
                Require(EditorUserBuildSettings.activeBuildTarget == BuildTarget.StandaloneWindows64, "Parameter bit-packing is limited to the current desktop target until paired platform proof exists.");
                Require(@params != null, "Parameter bit-packing arguments are required.");
                var preview = ReadOptionalBool(@params, "preview", false);
                var runBuildCallbacks = ReadOptionalBool(@params, "runBuildCallbacks", false);
                var saveScene = ReadOptionalBool(@params, "saveScene", false);
                ValidateRequestKeys(@params, preview ? PreviewKeys : ApplyKeys);
                Require(saveScene == false, "Parameter bit-packing never saves a scene.");
                Require(preview ? !runBuildCallbacks : runBuildCallbacks, "Parameter bit-packing callback policy is invalid.");

                var sourceScenePath = NormalizeSceneAssetPath(ReadRequiredString(@params, "sourceScenePath"));
                var sourceAvatarPath = NormalizeObjectPath(ReadRequiredString(@params, "sourceAvatarPath"));
                outputCloneName = NormalizeObjectName(ReadRequiredString(@params, "outputCloneName"));
                var outputSceneName = "VRCForge Parameter Build - " + outputCloneName;
                Require(outputSceneName.Length <= 128, "Parameter bit-packing output scene name is too long.");
                Require(!IsSceneNameLoaded(outputSceneName), "The parameter bit-packing output scene already exists.");
                var preferences = CapturePreferences();
                Require(!preferences.CompressorPresent || preferences.CompressorValue == 0, "The package compressor preference must be absent or explicitly automatic for a non-interactive build.");
                RequireNoDirtyProjectAssets();

                operationStage = "source_capture";
                beforeSource = CaptureSource(sourceScenePath, sourceAvatarPath);
                operationStage = "capability_capture";
                var capability = CaptureCapability();
                operationStage = "generated_tree_capture";
                beforeGenerated = CaptureTree(GeneratedRoot, GeneratedTreeSchema, requireExists: false);
                operationStage = "auxiliary_generated_capture";
                beforeAuxiliary = CaptureAuxiliaryGenerated();
                operationStage = "output_tree_capture";
                beforeOutput = CaptureManagedTree(OutputRoot, OutputTreeSchema);
                operationStage = "root_identity_capture";
                beforeRoots = CaptureRootIdentities(beforeGenerated);
                operationStage = "protected_tree_capture";
                beforeProtected = CaptureProtectedTree();
                operationStage = "output_preview";
                var outputPreview = new OutputPreview(outputCloneName, outputSceneName, beforeOutput);
                Require(!AssetDatabase.IsValidFolder(GeneratedRoot + "/" + outputCloneName), "The temporary build target already exists.");
                Require(!AssetDatabase.IsValidFolder(OutputKindRoot + "/" + outputCloneName), "The durable output target already exists.");
                operationStage = "preview_digest";
                var previewDigest = ComputePreviewDigest(
                    beforeSource,
                    capability,
                    beforeGenerated,
                    beforeAuxiliary,
                    beforeOutput,
                    beforeProtected,
                    beforeRoots,
                    outputPreview,
                    preferences
                );

                if (preview)
                {
                    operationStage = "preview_response";
                    return VRCForgeToolResult.Completed(
                        "Parameter bit-packing preview completed.",
                        BuildPreviewPayload(
                            beforeSource,
                            capability,
                            beforeGenerated,
                            beforeAuxiliary,
                            beforeOutput,
                            beforeProtected,
                            beforeRoots,
                            outputPreview,
                            preferences,
                            previewDigest
                        )
                    );
                }

                ValidateApplyPreconditions(
                    @params,
                    beforeSource,
                    capability,
                    beforeGenerated,
                    beforeAuxiliary,
                    beforeOutput,
                    beforeProtected,
                    beforeRoots,
                    outputPreview,
                    preferences,
                    previewDigest
                );

                stableInputLeases = HoldStableInputs(beforeSource, capability, beforeGenerated, beforeAuxiliary, beforeOutput, beforeProtected, beforeRoots);
                {
                    var leases = stableInputLeases;
                    operationStage = "stable_input_verification";
                    VerifyStableInputs(beforeSource, capability, beforeGenerated, beforeAuxiliary, beforeOutput, beforeProtected, beforeRoots, leases);
                    Require(CapturePreferences().ReceiptDigest == preferences.ReceiptDigest, "A parameter build preference changed after preview.");
                    operationStage = "project_cleanliness_recheck";
                    RequireNoDirtyProjectAssets();
                    operationStage = "cache_transaction_prepare";
                    cacheTransaction = CacheTransaction.Plan(beforeGenerated);
                    auxiliaryTransaction = AuxiliaryGeneratedTransaction.Plan(beforeAuxiliary);
                    mutationStarted = true;
                    cacheTransaction.Prepare();
                    auxiliaryTransaction.Prepare();
                    operationStage = "temporary_scene_creation";
                    outputScene = UnityEditor.SceneManagement.EditorSceneManager.NewScene(
                        UnityEditor.SceneManagement.NewSceneSetup.EmptyScene,
                        UnityEditor.SceneManagement.NewSceneMode.Additive
                    );
                    outputScene.name = outputSceneName;
                    var clone = Object.Instantiate(beforeSource.Avatar);
                    clone.name = outputCloneName;
                    SceneManager.MoveGameObjectToScene(clone, outputScene);
                    clone.SetActive(true);
                    operationStage = "clone_asset_staging";
                    PrepareCloneAssets(clone, outputScene);
                    EnsureNonInteractiveBuildPolicy(clone);
                    VerifyStableInputs(beforeSource, capability, beforeGenerated, beforeAuxiliary, beforeOutput, beforeProtected, beforeRoots, leases);

                    operationStage = "public_preprocess";
                    bool callbacksOk;
                    try
                    {
                        callbacksOk = VRCBuildPipelineCallbacks.OnPreprocessAvatar(clone);
                    }
                    finally
                    {
                        auxiliaryTransaction.ObserveMutation();
                    }
                    Require(callbacksOk, "The public avatar preprocess pipeline rejected the clone.");
                    RequireNoDirtyProjectAssets(outputScene, GeneratedRoot, AuxiliaryGeneratedRoot);
                    AssetDatabase.SaveAssets();
                    AssetDatabase.Refresh(ImportAssetOptions.ForceSynchronousImport);
                    auxiliaryTransaction.ObserveMutation();

                    operationStage = "source_verification";
                    var afterSource = CaptureSource(sourceScenePath, sourceAvatarPath);
                    Require(afterSource.SourceStateDigest == beforeSource.SourceStateDigest, "The source avatar changed during clone preprocessing.");
                    Require(afterSource.SourceAssetSetDigest == beforeSource.SourceAssetSetDigest, "The source asset set changed during clone preprocessing.");
                    Require(!afterSource.SourceDirty && !afterSource.ReferencedAssetsDirty, "The source avatar became dirty during clone preprocessing.");

                    operationStage = "capability_verification";
                    var callbackCapability = CaptureCapability();
                    Require(callbackCapability.CapabilityDigest == capability.CapabilityDigest, "The package capability changed during clone preprocessing.");
                    operationStage = "protected_tree_verification";
                    var callbackProtected = CaptureProtectedTree();
                    Require(callbackProtected.Digest == beforeProtected.Digest && callbackProtected.EntryCount == beforeProtected.EntryCount, "The public preprocess pipeline wrote outside the generated build root.");
                    var callbackRoots = CaptureRootIdentities(beforeGenerated);
                    Require(callbackRoots.Digest == beforeRoots.Digest && callbackRoots.EntryCount == beforeRoots.EntryCount, "A project root identity changed during clone preprocessing.");
                    operationStage = "generated_scope_verification";
                    var callbackGenerated = CaptureTree(GeneratedRoot, GeneratedTreeSchema, requireExists: true);
                    var callbackDelta = CompareGeneratedTrees(beforeGenerated, callbackGenerated);
                    Require(callbackDelta.Added.Count > 0, "The public preprocess pipeline produced no generated assets.");
                    RequireGeneratedSubtree(callbackDelta.Added, outputCloneName);
                    cacheTransaction.ObserveMutation(callbackGenerated);

                    operationStage = "output_descriptor_verification";
                    var cloneDescriptor = clone.GetComponent<VRCAvatarDescriptor>();
                    Require(cloneDescriptor != null, "The output clone has no avatar descriptor.");
                    operationStage = "output_null_layer_mask_restore";
                    var restoredNullLayerMasks = RestoreSourceNullLayerMasks(
                        cloneDescriptor,
                        beforeSource.BehaviorEvidence,
                        stage => operationStage = "output_null_layer_mask_restore_" + stage);
                    if (restoredNullLayerMasks > 0)
                    {
                        operationStage = "output_null_layer_mask_restore_dirty_scope";
                        RequireNoDirtyProjectAssets(outputScene, GeneratedRoot, AuxiliaryGeneratedRoot);
                        AssetDatabase.SaveAssets();
                        AssetDatabase.Refresh(ImportAssetOptions.ForceSynchronousImport);
                        auxiliaryTransaction.ObserveMutation();
                        operationStage = "output_null_layer_mask_restore_source_readback";
                        var normalizedSource = CaptureSource(sourceScenePath, sourceAvatarPath);
                        Require(
                            normalizedSource.SourceStateDigest == beforeSource.SourceStateDigest
                                && normalizedSource.SourceAssetSetDigest == beforeSource.SourceAssetSetDigest
                                && !normalizedSource.SourceDirty
                                && !normalizedSource.ReferencedAssetsDirty,
                            "The source avatar changed during output layer-mask restoration.");
                        operationStage = "output_null_layer_mask_restore_stable_input";
                        VerifyStableInputs(
                            beforeSource,
                            capability,
                            beforeGenerated,
                            beforeAuxiliary,
                            beforeOutput,
                            beforeProtected,
                            beforeRoots,
                            leases,
                            verifyAuxiliaryTree: false);
                    }
                    operationStage = "output_parameter_capture";
                    var outputParameters = RequireOutputParameters(cloneDescriptor);
                    var outputState = CaptureParameterState(outputParameters, beforeSource.MenuUsage);
                    operationStage = "output_parameter_verification";
                    var compressedNames = VerifyOutputParameters(beforeSource.ParameterState, outputState);
                    operationStage = "output_budget_verification";
                    Require(outputState.CostBits <= 256, "The output clone remains above the synchronized parameter budget.");
                    Require(outputState.CostBits < beforeSource.ParameterState.CostBits, "The output clone did not reduce synchronized parameter cost.");
                    Require(compressedNames.Count > 0, "The output clone did not compress an approved parameter.");
                    operationStage = "output_behavior_capture";
                    var cloneEvidence = ParameterBitPackingEvidence.Capture(
                        clone,
                        stage => operationStage = "output_behavior_capture_" + stage);
                    operationStage = "output_behavior_verification";
                    var behaviorProof = ParameterBitPackingEvidence.VerifyBehavior(
                        beforeSource.BehaviorEvidence,
                        cloneEvidence,
                        compressedNames,
                        beforeSource.ParameterState.Excluded.Select(item => item.Name).ToArray(),
                        stage => operationStage = "output_behavior_verification_" + stage);
                    Require(behaviorProof.PlatformScope == "current-target-only", "The behavior proof claimed unsupported cross-platform equivalence.");
                    var cloneParameterStateDigest = outputState.StateDigest;
                    var temporaryOutputRoot = GeneratedRoot + "/" + outputCloneName;
                    var durableOutputRoot = OutputKindRoot + "/" + outputCloneName;
                    var temporaryPrefabPath = temporaryOutputRoot + "/" + outputCloneName + ".prefab";
                    Require(AssetDatabase.IsValidFolder(temporaryOutputRoot), "The processed temporary output subtree is missing.");
                    operationStage = "temporary_output_prefab_save_call";
                    Require(AssetDatabase.LoadAssetAtPath<GameObject>(temporaryPrefabPath) == null, "The temporary output prefab already exists.");
                    var immediatePrefab = PrefabUtility.SaveAsPrefabAsset(clone, temporaryPrefabPath, out var stagedPrefabSaved);
                    operationStage = "temporary_output_prefab_save_flag";
                    Require(stagedPrefabSaved, "The processed clone prefab save did not report success.");
                    auxiliaryTransaction.ObserveMutation();
                    RequireNoDirtyProjectAssets(outputScene, GeneratedRoot, AuxiliaryGeneratedRoot);
                    AssetDatabase.SaveAssets();
                    AssetDatabase.ImportAsset(
                        temporaryPrefabPath,
                        ImportAssetOptions.ForceSynchronousImport | ImportAssetOptions.ForceUpdate);
                    AssetDatabase.Refresh(ImportAssetOptions.ForceSynchronousImport);
                    auxiliaryTransaction.ObserveMutation();
                    operationStage = "temporary_output_prefab_import_readback";
                    var importedMainType = AssetDatabase.GetMainAssetTypeAtPath(temporaryPrefabPath);
                    operationStage = importedMainType == null
                        ? "temporary_output_prefab_import_main_type_null"
                        : importedMainType == typeof(GameObject)
                            ? "temporary_output_prefab_import_main_type_game_object"
                            : "temporary_output_prefab_import_main_type_unexpected";
                    var importedMainAsset = AssetDatabase.LoadMainAssetAtPath(temporaryPrefabPath);
                    operationStage = importedMainAsset == null
                        ? "temporary_output_prefab_import_main_asset_null"
                        : importedMainAsset is GameObject
                            ? "temporary_output_prefab_import_main_asset_game_object"
                            : "temporary_output_prefab_import_main_asset_unexpected";
                    var importedPrefab = importedMainAsset as GameObject;
                    Require(importedPrefab != null, "The successfully saved clone prefab was unavailable after synchronous import.");
                    operationStage = "temporary_output_prefab_identity_reconciliation";
                    Require(immediatePrefab == null
                            || AssetDatabase.GetAssetPath(immediatePrefab) == temporaryPrefabPath,
                        "The immediate clone prefab resolved to another asset path after import.");
                    operationStage = "temporary_output_prefab_receipt_capture";
                    var stagedReceipt = CaptureOutputPrefab(
                        temporaryPrefabPath,
                        outputCloneName,
                        beforeSource.MenuUsage,
                        cloneEvidence,
                        behaviorProof,
                        beforeSource.BehaviorEvidence,
                        compressedNames,
                        beforeSource.ParameterState.Excluded.Select(item => item.Name).ToArray(),
                        stage => operationStage = "temporary_output_prefab_receipt_" + stage);
                    stagedOutputManifest = CaptureAssetTreeManifest(temporaryOutputRoot, temporaryPrefabPath, requireNoTemporaryReferences: false);

                    operationStage = "persistent_output_move";
                    EnsureAssetFolder(OutputKindRoot, createdOutputFolders);
                    Require(!AssetDatabase.IsValidFolder(durableOutputRoot), "The approved durable output subtree already exists.");
                    var moveError = AssetDatabase.MoveAsset(temporaryOutputRoot, durableOutputRoot);
                    Require(string.IsNullOrWhiteSpace(moveError), "The processed output subtree could not be moved into managed project assets.");
                    auxiliaryTransaction.ObserveMutation();
                    RequireNoDirtyProjectAssets(outputScene, GeneratedRoot, AuxiliaryGeneratedRoot, durableOutputRoot);
                    AssetDatabase.SaveAssets();
                    AssetDatabase.Refresh(ImportAssetOptions.ForceSynchronousImport);
                    auxiliaryTransaction.ObserveMutation();
                    Require(!AssetDatabase.IsValidFolder(temporaryOutputRoot), "The temporary processed output subtree remained after migration.");
                    Require(AssetDatabase.IsValidFolder(durableOutputRoot), "The durable processed output subtree is missing after migration.");
                    var movedManifest = CaptureAssetTreeManifest(durableOutputRoot, outputPreview.PrefabPath, requireNoTemporaryReferences: true);
                    VerifyGuidPreservingMove(stagedOutputManifest, movedManifest);
                    outputManifest = movedManifest;
                    operationStage = "persistent_output_scope_verification";
                    var afterGenerated = CaptureTree(GeneratedRoot, GeneratedTreeSchema, requireExists: true);
                    var generatedDelta = CompareGeneratedTrees(beforeGenerated, afterGenerated);
                    Require(!AssetDatabase.IsValidFolder(StagingRoot) && !AssetDatabase.IsValidFolder(temporaryOutputRoot), "The package temporary build root contains operation residue.");
                    cacheTransaction.ObserveMutation(afterGenerated);
                    var temporaryDeltaDigest = ComputeTreeDeltaDigest(generatedDelta, "vrcforge.parameter_temporary_delta.v1");
                    var afterOutput = CaptureManagedTree(OutputRoot, OutputTreeSchema);
                    var outputDelta = CompareGeneratedTrees(beforeOutput, afterOutput);
                    Require(outputDelta.Added.Count > 0, "The parameter build produced no durable output assets.");
                    Require(outputDelta.Modified.Count == 0 && outputDelta.Removed.Count == 0, "The parameter build changed a pre-existing managed output asset.");
                    RequireManagedOutputSubtree(outputDelta.Added, outputCloneName);
                    var outputAddedEntriesDigest = ComputeAddedEntriesDigest(outputDelta.Added);
                    var targetTree = CaptureTree(durableOutputRoot, OutputManifestSchema + ".tree", requireExists: true);
                    stableOutputLeases = HoldStableTree(durableOutputRoot, targetTree);
                    var leasedManifest = CaptureAssetTreeManifest(durableOutputRoot, outputPreview.PrefabPath, requireNoTemporaryReferences: true);
                    Require(leasedManifest.ReceiptDigest == movedManifest.ReceiptDigest, "The durable output changed while stable leases were acquired.");
                    var outputReceipt = CaptureOutputPrefab(
                        outputPreview.PrefabPath,
                        outputCloneName,
                        beforeSource.MenuUsage,
                        cloneEvidence,
                        behaviorProof,
                        beforeSource.BehaviorEvidence,
                        compressedNames,
                        beforeSource.ParameterState.Excluded.Select(item => item.Name).ToArray()
                    );
                    Require(outputReceipt.Guid == stagedReceipt.Guid, "The persisted prefab GUID changed during migration.");
                    operationStage = "temporary_output_cleanup";
                    var cloneInstanceId = clone.GetInstanceID();
                    Object.DestroyImmediate(clone);
                    Require(EditorUtility.InstanceIDToObject(cloneInstanceId) == null, "The temporary output clone could not be destroyed.");
                    foreach (var remainingRoot in outputScene.GetRootGameObjects()) Object.DestroyImmediate(remainingRoot);
                    Require(outputScene.GetRootGameObjects().Length == 0, "The temporary output scene still contains objects.");
                    Require(EditorSceneManagerClose(outputScene), "Failed to close the temporary output scene.");
                    outputScene = default;
                    var sceneLoadedAfter = IsSceneNameLoaded(outputSceneName);
                    var temporaryObjectResidue = EditorUtility.InstanceIDToObject(cloneInstanceId) != null;
                    Require(!sceneLoadedAfter && !temporaryObjectResidue, "The temporary output scene or clone remained loaded.");

                    operationStage = "persistent_output_readback";
                    var outputReadback = CaptureOutputPrefab(
                        outputPreview.PrefabPath,
                        outputCloneName,
                        beforeSource.MenuUsage,
                        cloneEvidence,
                        behaviorProof,
                        beforeSource.BehaviorEvidence,
                        compressedNames,
                        beforeSource.ParameterState.Excluded.Select(item => item.Name).ToArray()
                    );
                    Require(outputReadback.ReceiptDigest == outputReceipt.ReceiptDigest, "The persisted output prefab changed during cleanup.");
                    var readbackGenerated = CaptureTree(GeneratedRoot, GeneratedTreeSchema, requireExists: true);
                    Require(readbackGenerated.Digest == afterGenerated.Digest && readbackGenerated.EntryCount == afterGenerated.EntryCount, "The generated output tree changed during cleanup.");
                    var readbackOutput = CaptureManagedTree(OutputRoot, OutputTreeSchema);
                    Require(readbackOutput.Digest == afterOutput.Digest && readbackOutput.EntryCount == afterOutput.EntryCount, "The durable output tree changed during cleanup.");
                    var finalManifest = CaptureAssetTreeManifest(durableOutputRoot, outputPreview.PrefabPath, requireNoTemporaryReferences: true);
                    Require(finalManifest.ReceiptDigest == movedManifest.ReceiptDigest, "The durable output manifest changed before receipt construction.");

                    operationStage = "auxiliary_generated_restore";
                    Require(auxiliaryTransaction.Restore(allowGeneratedRootDirty: true), "The auxiliary generated root could not be restored exactly.");
                    var afterAuxiliary = CaptureAuxiliaryGenerated();
                    Require(AuxiliaryContentEquals(beforeAuxiliary, afterAuxiliary),
                        "The auxiliary generated root differs from its approved baseline.");

                    operationStage = "cache_restore";
                    Require(cacheTransaction.Restore(allowAuxiliaryRootDirty: true), "The dependency cache could not be restored exactly.");
                    RequireNoDirtyProjectAssets();
                    var restoredGenerated = CaptureTree(GeneratedRoot, GeneratedTreeSchema, requireExists: beforeGenerated.Exists);
                    Require(restoredGenerated.Exists == beforeGenerated.Exists
                        && restoredGenerated.ContentDigest == beforeGenerated.ContentDigest
                        && restoredGenerated.EntryCount == beforeGenerated.EntryCount
                        && restoredGenerated.TotalBytes == beforeGenerated.TotalBytes,
                        "The dependency cache differs from its approved baseline.");
                    afterGenerated = restoredGenerated;
                    generatedDelta = CompareGeneratedTrees(beforeGenerated, afterGenerated);
                    temporaryDeltaDigest = ComputeTreeDeltaDigest(generatedDelta, "vrcforge.parameter_temporary_delta.v1");

                    operationStage = "final_input_verification";
                    VerifyStableInputs(
                        beforeSource,
                        capability,
                        beforeGenerated,
                        beforeAuxiliary,
                        beforeOutput,
                        beforeProtected,
                        beforeRoots,
                        leases,
                        verifyOutputTree: false,
                        verifyAuxiliaryIdentity: false);
                    afterSource = CaptureSource(sourceScenePath, sourceAvatarPath);
                    Require(afterSource.SourceStateDigest == beforeSource.SourceStateDigest, "The source avatar changed before final readback.");
                    var afterCapability = CaptureCapability();
                    Require(afterCapability.CapabilityDigest == capability.CapabilityDigest, "The package capability changed before final readback.");
                    var afterPreferences = CapturePreferences();
                    Require(afterPreferences.ReceiptDigest == preferences.ReceiptDigest, "A parameter build preference changed during apply.");
                    var afterProtected = CaptureProtectedTree();
                    Require(afterProtected.Digest == beforeProtected.Digest && afterProtected.EntryCount == beforeProtected.EntryCount, "The persistent output escaped the generated build root.");
                    var afterRoots = CaptureRootIdentities(beforeGenerated);
                    Require(afterRoots.Digest == beforeRoots.Digest && afterRoots.EntryCount == beforeRoots.EntryCount, "A project root identity changed before final readback.");

                    auxiliaryTransaction.Complete();
                    cacheTransaction.Complete();
                    Require(auxiliaryTransaction.VerifyClosedTerminal()
                            && cacheTransaction.VerifyClosedTerminal(),
                        "A parameter transaction did not reach its verified closed state.");
                    var applyReceiptDigest = ComputeApplyReceiptDigest(
                        CurrentProjectPath(),
                        previewDigest,
                        afterCapability.CapabilityDigest,
                        beforeSource.ParameterState.CostBits,
                        outputState.CostBits,
                        compressedNames,
                        beforeSource.ParameterState.SafeCandidateNames,
                        beforeSource.ParameterState.Excluded,
                        beforeSource,
                        afterSource,
                        outputCloneName,
                        outputSceneName,
                        cloneEvidence.PortableAvatarDigest,
                        cloneEvidence.ReceiptDigest,
                        cloneParameterStateDigest,
                        outputReadback,
                        behaviorProof,
                        stagedOutputManifest,
                        finalManifest,
                        preferences,
                        cacheTransaction,
                        auxiliaryTransaction,
                        sceneLoadedAfter,
                        temporaryObjectResidue,
                        beforeGenerated,
                        afterGenerated,
                        generatedDelta,
                        temporaryDeltaDigest,
                        beforeAuxiliary,
                        afterAuxiliary,
                        beforeOutput,
                        afterOutput,
                        outputDelta,
                        outputAddedEntriesDigest,
                        beforeRoots,
                        afterRoots,
                        beforeProtected,
                        afterProtected,
                        true,
                        false,
                        false,
                        false,
                        "verified"
                    );
                    return VRCForgeToolResult.Completed(
                        "Parameter bit-packing clone verified.",
                        new
                        {
                            schema = ResultSchema,
                            ok = true,
                            preview = false,
                            verified = true,
                            changed = true,
                            saved = true,
                            callbacksInvoked = true,
                            mutationStarted = true,
                            restored = false,
                            cleanupRequired = false,
                            checkpointRestoreRequired = false,
                            operationState = "verified",
                            cleanupVerified = true,
                            sceneLoadedAfter,
                            temporaryObjectResidue,
                            projectPath = CurrentProjectPath(),
                            previewDigest,
                            capability = afterCapability.ToPayload(),
                            preferences = afterPreferences.ToPayload(),
                            platformProof = new
                            {
                                buildTarget = EditorUserBuildSettings.activeBuildTarget.ToString(),
                                scope = "current-target-only",
                                crossPlatformEquivalent = false,
                                localAppDataAccessed = false
                            },
                            behaviorProof = behaviorProof.ToPayload(),
                            costBeforeBits = beforeSource.ParameterState.CostBits,
                            costAfterBits = outputState.CostBits,
                            compressedParameterNames = compressedNames,
                            approvedSafeCandidateNames = beforeSource.ParameterState.SafeCandidateNames,
                            excludedParameters = beforeSource.ParameterState.Excluded.Select(item => item.ToPayload()).ToArray(),
                            sourceUnchanged = true,
                            sourceSceneDirtyAfter = false,
                            sourceStateDigestAfter = afterSource.SourceStateDigest,
                            sourceAssetSetDigestAfter = afterSource.SourceAssetSetDigest,
                            source = new
                            {
                                scenePath = beforeSource.ScenePath,
                                sceneGuid = beforeSource.SceneGuid,
                                sceneFileDigest = beforeSource.SceneFileDigest,
                                sceneMetaDigest = beforeSource.SceneMetaDigest,
                                objectPath = beforeSource.ObjectPath,
                                globalObjectId = beforeSource.GlobalObjectId,
                                hierarchyDigest = beforeSource.HierarchyDigest,
                                sourceStateDigestBefore = beforeSource.SourceStateDigest,
                                sourceStateDigestAfter = afterSource.SourceStateDigest,
                                sourceAssetSetDigestBefore = beforeSource.SourceAssetSetDigest,
                                sourceAssetSetDigestAfter = afterSource.SourceAssetSetDigest,
                                sourceAssetCount = beforeSource.SourceAssetCount,
                                parameterStateDigest = beforeSource.ParameterState.StateDigest,
                                parameterCount = beforeSource.ParameterState.Parameters.Count,
                                controllerStateDigest = beforeSource.ControllerStateDigest,
                                menuStateDigest = beforeSource.MenuStateDigest,
                                behaviorEvidence = beforeSource.BehaviorEvidence.ToPayload(),
                                sourceUnchanged = true,
                                sceneDirtyAfter = false
                            },
                            output = new
                            {
                                cloneName = outputCloneName,
                                sceneName = outputSceneName,
                                scenePath = string.Empty,
                                scenePersistent = false,
                                clonePortableAvatarDigest = cloneEvidence.PortableAvatarDigest,
                                cloneEvidenceDigest = cloneEvidence.ReceiptDigest,
                                cloneParameterStateDigest,
                                prefabPath = outputReadback.PrefabPath,
                                prefabGuid = outputReadback.Guid,
                                prefabFileDigest = outputReadback.FileDigest,
                                prefabMetaDigest = outputReadback.MetaDigest,
                                prefabRootGlobalObjectId = outputReadback.RootGlobalObjectId,
                                prefabPortableAvatarDigest = outputReadback.PortableAvatarDigest,
                                prefabOrderedParameterDigest = outputReadback.OrderedParameterDigest,
                                prefabMenuGraphDigest = outputReadback.MenuGraphDigest,
                                prefabAnimatorBehaviorDigest = outputReadback.AnimatorBehaviorDigest,
                                prefabEvidenceDigest = outputReadback.EvidenceReceiptDigest,
                                prefabBehaviorProofDigest = outputReadback.BehaviorProofDigest,
                                prefabParameterStateDigest = outputReadback.ParameterStateDigest,
                                prefabPersistent = true,
                                prefabExistsAfter = true,
                                sceneLoadedAfter,
                                temporaryObjectResidue
                            },
                            generated = new
                            {
                                root = GeneratedRoot,
                                stagingRoot = StagingRoot,
                                stagingRemoved = true,
                                rootExistsBefore = beforeGenerated.Exists,
                                rootExistsAfter = afterGenerated.Exists,
                                treeDigestBefore = beforeGenerated.Digest,
                                contentDigestBefore = beforeGenerated.ContentDigest,
                                entryCountBefore = beforeGenerated.EntryCount,
                                byteCountBefore = beforeGenerated.TotalBytes,
                                treeDigestAfter = afterGenerated.Digest,
                                contentDigestAfter = afterGenerated.ContentDigest,
                                entryCountAfter = afterGenerated.EntryCount,
                                byteCountAfter = afterGenerated.TotalBytes,
                                addedEntryCount = generatedDelta.Added.Count,
                                modifiedEntryCount = generatedDelta.Modified.Count,
                                removedEntryCount = generatedDelta.Removed.Count,
                                targetResidue = false,
                                deltaDigest = temporaryDeltaDigest,
                                cacheRestored = true,
                                backupBounded = true,
                                backupMaxEntries = CacheBackupMaxEntries,
                                backupMaxBytes = CacheBackupMaxBytes,
                                journalSchema = CacheJournalSchema,
                                journalId = cacheTransaction.JournalId,
                                journalClosed = cacheTransaction.Completed
                            },
                            auxiliaryGenerated = BuildAuxiliaryApplyPayload(
                                beforeAuxiliary,
                                afterAuxiliary,
                                auxiliaryTransaction
                            ),
                            managedOutput = new
                            {
                                root = OutputRoot,
                                kindRoot = OutputKindRoot,
                                targetRoot = OutputKindRoot + "/" + outputCloneName,
                                rootExistsBefore = beforeOutput.Exists,
                                rootExistsAfter = afterOutput.Exists,
                                treeDigestBefore = beforeOutput.Digest,
                                entryCountBefore = beforeOutput.EntryCount,
                                treeDigestAfter = afterOutput.Digest,
                                entryCountAfter = afterOutput.EntryCount,
                                addedEntryCount = outputDelta.Added.Count,
                                targetSubtreeCount = 1,
                                modifiedEntryCount = outputDelta.Modified.Count,
                                removedEntryCount = outputDelta.Removed.Count,
                                addedEntriesDigest = outputAddedEntriesDigest,
                                leaseBound = true,
                                stageSavedBeforeMove = true,
                                guidPreservingWholeTreeMove = true,
                                temporaryTreeRemoved = true,
                                prefabGuidPreserved = true,
                                stagedManifest = stagedOutputManifest.ToPayload(),
                                finalManifest = finalManifest.ToPayload(),
                                manifestSchema = OutputManifestSchema,
                                manifestDigest = finalManifest.ReceiptDigest,
                                manifestEntryCount = finalManifest.EntryCount,
                                manifestByteCount = finalManifest.TotalBytes,
                                manifestContentDigest = finalManifest.ContentDigest,
                                manifestHandleEvidenceDigest = finalManifest.HandleEvidenceDigest,
                                guidMapDigest = finalManifest.GuidMapDigest,
                                dependencyGuidDigest = finalManifest.DependencyGuidDigest,
                                referenceClosureDigest = finalManifest.ReferenceClosureDigest,
                                noTemporaryReferences = finalManifest.NoTemporaryReferences,
                                reparseFree = finalManifest.ReparseFree,
                                singleLink = finalManifest.SingleLink,
                                handleHashed = finalManifest.HandleHashed,
                                finalEnumerationVerified = finalManifest.FinalEnumerationVerified
                            },
                            protectedProjectTree = new
                            {
                                rootIdentityDigestBefore = beforeRoots.Digest,
                                rootIdentityDigestAfter = afterRoots.Digest,
                                rootIdentityCountBefore = beforeRoots.EntryCount,
                                rootIdentityCountAfter = afterRoots.EntryCount,
                                treeDigestBefore = beforeProtected.Digest,
                                treeDigestAfter = afterProtected.Digest,
                                entryCountBefore = beforeProtected.EntryCount,
                                entryCountAfter = afterProtected.EntryCount
                            },
                            applyReceiptDigest
                        }
                    );
                }
            }
            catch (Exception exception) when (mutationStarted)
            {
                stableOutputLeases?.Dispose();
                stableOutputLeases = null;
                var restored = TryCleanupFailure(
                    outputScene,
                    beforeGenerated,
                    beforeAuxiliary,
                    beforeOutput,
                    beforeProtected,
                    beforeRoots,
                    beforeSource,
                    outputCloneName,
                    cacheTransaction,
                    auxiliaryTransaction,
                    stagedOutputManifest,
                    outputManifest,
                    createdOutputFolders);
                var reason = exception is ParameterBitPackingException
                    ? " " + exception.Message
                    : string.Empty;
                return VRCForgeToolResult.Failed(
                    restored
                        ? "Parameter bit-packing failed after restoring the verified pre-state." + reason
                        : "Parameter bit-packing failed; checkpoint restore is required." + reason,
                    new
                    {
                        schema = ResultSchema,
                        mutationStarted = true,
                        restored,
                        cleanupVerified = restored,
                        cleanupRequired = !restored,
                        checkpointRestoreRequired = !restored,
                        operationState = restored ? "restored" : "checkpoint_restore_required",
                        failureStage = operationStage
                    }
                );
            }
            catch (ParameterBitPackingException exception)
            {
                return VRCForgeToolResult.Failed(exception.Message);
            }
            catch (Exception exception)
            {
                return VRCForgeToolResult.Failed(
                    "Parameter bit-packing operation failed closed during "
                    + operationStage
                    + " ("
                    + exception.GetType().Name
                    + ")."
                );
            }
            finally
            {
                stableOutputLeases?.Dispose();
                stableInputLeases?.Dispose();
            }
        }

        private static object BuildPreviewPayload(
            SourceSnapshot source,
            CapabilitySnapshot capability,
            TreeSnapshot generated,
            AuxiliaryGeneratedSnapshot auxiliary,
            TreeSnapshot outputTree,
            TreeSnapshot protectedTree,
            RootIdentitySnapshot roots,
            OutputPreview output,
            PreferenceSnapshot preferences,
            string previewDigest)
        {
            return new
            {
                schema = ResultSchema,
                ok = true,
                preview = true,
                verified = true,
                changed = false,
                saved = false,
                callbacksInvoked = false,
                mutationStarted = false,
                mutationCount = 0,
                projectPath = CurrentProjectPath(),
                source = source.ToPayload(),
                capability = capability.ToPayload(),
                generated = new
                {
                    root = GeneratedRoot,
                    treeDigestBefore = generated.Digest,
                    contentDigestBefore = generated.ContentDigest,
                    entryCountBefore = generated.EntryCount,
                    byteCountBefore = generated.TotalBytes,
                    backupMaxEntries = CacheBackupMaxEntries,
                    backupMaxBytes = CacheBackupMaxBytes,
                    journalSchema = CacheJournalSchema,
                    protectedTreeDigestBefore = protectedTree.Digest,
                    protectedEntryCountBefore = protectedTree.EntryCount,
                    rootIdentityDigestBefore = roots.Digest,
                    rootIdentityCountBefore = roots.EntryCount,
                    exists = generated.Exists,
                    reparseFree = true
                },
                auxiliaryGenerated = new
                {
                    root = AuxiliaryGeneratedRoot,
                    packageRoot = AuxiliaryPackageRoot,
                    packageRootIdentityDigestBefore = auxiliary.PackageRootIdentityDigest,
                    packageManifestDigestBefore = auxiliary.PackageManifestDigest,
                    packageManifestIdentityDigestBefore = auxiliary.PackageManifestIdentityDigest,
                    rootExistsBefore = auxiliary.Tree.Exists,
                    treeDigestBefore = auxiliary.Tree.Digest,
                    contentDigestBefore = auxiliary.Tree.ContentDigest,
                    entryCountBefore = auxiliary.Tree.EntryCount,
                    byteCountBefore = auxiliary.Tree.TotalBytes,
                    backupMaxEntries = CacheBackupMaxEntries,
                    backupMaxBytes = CacheBackupMaxBytes,
                    journalSchema = AuxiliaryJournalSchema,
                    reparseFree = true
                },
                preferences = preferences.ToPayload(),
                platformProof = new
                {
                    buildTarget = EditorUserBuildSettings.activeBuildTarget.ToString(),
                    scope = "current-target-only",
                    crossPlatformEquivalent = false,
                    localAppDataAccessed = false
                },
                output = output.ToPayload(),
                previewDigest
            };
        }

        private static object BuildAuxiliaryApplyPayload(
            AuxiliaryGeneratedSnapshot before,
            AuxiliaryGeneratedSnapshot after,
            AuxiliaryGeneratedTransaction transaction)
        {
            Require(before != null && after != null && transaction != null && transaction.Observed != null,
                "The auxiliary generated receipt is incomplete.");
            return new
            {
                root = AuxiliaryGeneratedRoot,
                packageRoot = AuxiliaryPackageRoot,
                packageRootIdentityDigestBefore = before.PackageRootIdentityDigest,
                packageRootIdentityDigestAfter = after.PackageRootIdentityDigest,
                packageManifestDigestBefore = before.PackageManifestDigest,
                packageManifestDigestAfter = after.PackageManifestDigest,
                packageManifestIdentityDigestBefore = before.PackageManifestIdentityDigest,
                packageManifestIdentityDigestAfter = after.PackageManifestIdentityDigest,
                rootExistsBefore = before.Tree.Exists,
                rootExistsAfter = after.Tree.Exists,
                treeDigestBefore = before.Tree.Digest,
                treeDigestAfter = after.Tree.Digest,
                contentDigestBefore = before.Tree.ContentDigest,
                contentDigestAfter = after.Tree.ContentDigest,
                entryCountBefore = before.Tree.EntryCount,
                entryCountAfter = after.Tree.EntryCount,
                byteCountBefore = before.Tree.TotalBytes,
                byteCountAfter = after.Tree.TotalBytes,
                observedRootExists = transaction.Observed.Tree.Exists,
                observedTreeDigest = transaction.Observed.Tree.Digest,
                observedContentDigest = transaction.Observed.Tree.ContentDigest,
                observedEntryCount = transaction.Observed.Tree.EntryCount,
                observedByteCount = transaction.Observed.Tree.TotalBytes,
                ownedRootIdentityDigest = transaction.OwnedRootIdentityDigest,
                createdByOperation = transaction.CreatedByOperation,
                restorationMode = transaction.RestorationMode,
                restoreVerified = true,
                backupBounded = true,
                backupMaxEntries = CacheBackupMaxEntries,
                backupMaxBytes = CacheBackupMaxBytes,
                journalSchema = AuxiliaryJournalSchema,
                journalId = transaction.JournalId,
                journalClosed = transaction.Completed
            };
        }

        private static void ValidateApplyPreconditions(
            JObject request,
            SourceSnapshot source,
            CapabilitySnapshot capability,
            TreeSnapshot generated,
            AuxiliaryGeneratedSnapshot auxiliary,
            TreeSnapshot outputTree,
            TreeSnapshot protectedTree,
            RootIdentitySnapshot roots,
            OutputPreview output,
            PreferenceSnapshot preferences,
            string previewDigest)
        {
            Require(ProjectPathsEqual(ReadExpectedString(request, "expectedProjectPath"), CurrentProjectPath()), "The selected Unity project changed after preview.");
            Require(ReadExpectedString(request, "expectedSourceSceneGuid") == source.SceneGuid, "The source scene GUID changed after preview.");
            Require(ReadExpectedString(request, "expectedSourceSceneFileDigest") == source.SceneFileDigest, "The source scene changed after preview.");
            Require(ReadExpectedString(request, "expectedSourceSceneMetaDigest") == source.SceneMetaDigest, "The source scene metadata changed after preview.");
            Require(ReadExpectedString(request, "expectedSourceGlobalObjectId") == source.GlobalObjectId, "The source avatar identity changed after preview.");
            Require(ReadExpectedString(request, "expectedSourceHierarchyDigest") == source.HierarchyDigest, "The source avatar hierarchy changed after preview.");
            Require(ReadExpectedString(request, "expectedSourceStateDigest") == source.SourceStateDigest, "The source avatar state changed after preview.");
            Require(ReadExpectedString(request, "expectedSourceAssetSetDigest") == source.SourceAssetSetDigest, "The source asset set changed after preview.");
            Require(ReadExpectedInt(request, "expectedSourceAssetCount") == source.SourceAssetCount, "The source asset count changed after preview.");
            Require(ReadExpectedString(request, "expectedParameterStateDigest") == source.ParameterState.StateDigest, "The source parameter state changed after preview.");
            Require(ReadExpectedString(request, "expectedControllerStateDigest") == source.ControllerStateDigest, "The source controller state changed after preview.");
            Require(ReadExpectedString(request, "expectedMenuStateDigest") == source.MenuStateDigest, "The source menu state changed after preview.");
            Require(ReadExpectedString(request, "expectedSourceBehaviorEvidenceDigest") == source.BehaviorEvidence.ReceiptDigest, "The source behavior evidence changed after preview.");
            Require(ReadExpectedInt(request, "expectedSourceCostBits") == source.ParameterState.CostBits, "The source parameter cost changed after preview.");
            Require(ReadExpectedInt(request, "expectedParameterCount") == source.ParameterState.Parameters.Count, "The source parameter count changed after preview.");
            Require(ReadExpectedString(request, "expectedSafeCandidateDigest") == source.ParameterState.SafeCandidateDigest, "The safe candidate set changed after preview.");
            Require(ReadExpectedInt(request, "expectedSafeCandidateCount") == source.ParameterState.SafeCandidateNames.Count, "The safe candidate count changed after preview.");
            Require(ReadExpectedString(request, "expectedExcludedDigest") == source.ParameterState.ExcludedDigest, "The parameter exclusions changed after preview.");
            Require(ReadExpectedInt(request, "expectedExcludedCount") == source.ParameterState.Excluded.Count, "The parameter exclusion count changed after preview.");
            Require(ReadExpectedString(request, "expectedCapabilityDigest") == capability.CapabilityDigest, "The package capability changed after preview.");
            Require(ReadExpectedString(request, "expectedPackageRootIdentityDigest") == capability.PackageRootIdentityDigest, "The package root identity changed after preview.");
            Require(ReadExpectedString(request, "expectedRootIdentityDigest") == roots.Digest, "A project root identity changed after preview.");
            Require(ReadExpectedInt(request, "expectedRootIdentityCount") == roots.EntryCount, "The project root identity set changed after preview.");
            Require(ReadExpectedBool(request, "expectedGeneratedRootExistsBefore") == generated.Exists, "The generated build root state changed after preview.");
            Require(ReadExpectedString(request, "expectedGeneratedTreeDigestBefore") == generated.Digest, "The generated build root changed after preview.");
            Require(ReadExpectedInt(request, "expectedGeneratedEntryCountBefore") == generated.EntryCount, "The generated build root count changed after preview.");
            Require(ReadExpectedString(request, "expectedGeneratedContentDigestBefore") == generated.ContentDigest, "The generated build root content changed after preview.");
            Require(ReadExpectedLong(request, "expectedGeneratedByteCountBefore") == generated.TotalBytes, "The generated build root byte count changed after preview.");
            Require(ReadExpectedString(request, "expectedAuxiliaryPackageRootIdentityDigest") == auxiliary.PackageRootIdentityDigest, "The auxiliary package root identity changed after preview.");
            Require(ReadExpectedString(request, "expectedAuxiliaryPackageManifestDigest") == auxiliary.PackageManifestDigest, "The auxiliary package manifest changed after preview.");
            Require(ReadExpectedString(request, "expectedAuxiliaryPackageManifestIdentityDigest") == auxiliary.PackageManifestIdentityDigest, "The auxiliary package manifest identity changed after preview.");
            Require(ReadExpectedBool(request, "expectedAuxiliaryRootExistsBefore") == auxiliary.Tree.Exists, "The auxiliary generated root state changed after preview.");
            Require(ReadExpectedString(request, "expectedAuxiliaryTreeDigestBefore") == auxiliary.Tree.Digest, "The auxiliary generated tree changed after preview.");
            Require(ReadExpectedString(request, "expectedAuxiliaryContentDigestBefore") == auxiliary.Tree.ContentDigest, "The auxiliary generated content changed after preview.");
            Require(ReadExpectedInt(request, "expectedAuxiliaryEntryCountBefore") == auxiliary.Tree.EntryCount, "The auxiliary generated entry count changed after preview.");
            Require(ReadExpectedLong(request, "expectedAuxiliaryByteCountBefore") == auxiliary.Tree.TotalBytes, "The auxiliary generated byte count changed after preview.");
            Require(ReadExpectedString(request, "expectedPreferenceDigest") == preferences.ReceiptDigest, "A parameter build preference changed after preview.");
            Require(ReadExpectedString(request, "expectedProtectedTreeDigestBefore") == protectedTree.Digest, "The protected project tree changed after preview.");
            Require(ReadExpectedInt(request, "expectedProtectedEntryCountBefore") == protectedTree.EntryCount, "The protected project tree count changed after preview.");
            Require(ReadExpectedString(request, "expectedOutputSceneName") == output.SceneName, "The output scene changed after preview.");
            Require(ReadExpectedString(request, "expectedOutputPrefabPath") == output.PrefabPath, "The output prefab changed after preview.");
            Require(ReadExpectedString(request, "expectedOutputTreeDigestBefore") == outputTree.Digest, "The managed output tree changed after preview.");
            Require(ReadExpectedInt(request, "expectedOutputEntryCountBefore") == outputTree.EntryCount, "The managed output tree count changed after preview.");
            Require(ReadExpectedBool(request, "expectedOutputRootExistsBefore") == outputTree.Exists, "The managed output root state changed after preview.");
            Require(ReadExpectedString(request, "expectedPreviewDigest") == previewDigest, "The parameter bit-packing preview receipt changed.");
        }

        private static SourceSnapshot CaptureSource(string scenePath, string objectPath)
        {
            var scene = SceneManager.GetSceneByPath(scenePath);
            Require(scene.IsValid() && scene.isLoaded, "The source scene is not loaded.");
            Require(!scene.isDirty, "The source scene must be saved and clean.");
            var sceneGuid = AssetDatabase.AssetPathToGUID(scenePath).ToLowerInvariant();
            Require(IsGuid(sceneGuid), "The source scene GUID is invalid.");
            var sceneFilePath = AbsoluteProjectPath(scenePath);
            var sceneMetaPath = sceneFilePath + ".meta";
            RequireStableRegularFile(sceneFilePath);
            RequireStableRegularFile(sceneMetaPath);
            var avatar = FindUniqueGameObject(scene, objectPath);
            var descriptor = avatar.GetComponent<VRCAvatarDescriptor>();
            Require(descriptor != null, "The source object has no avatar descriptor.");
            Require(avatar.GetComponentsInChildren<Component>(true).Any(component => component != null && component.GetType().FullName == "VF.Model.VRCFury"), "The source avatar has no package component for the public build pipeline.");
            var globalObjectId = GlobalObjectId.GetGlobalObjectIdSlow(avatar).ToString();
            Require(!string.IsNullOrWhiteSpace(globalObjectId), "The source avatar identity is unavailable.");
            var parameters = RequireParameters(descriptor);
            var menuUsage = CaptureMenuUsage(descriptor.expressionsMenu);
            var parameterState = CaptureParameterState(parameters, menuUsage);
            Require(parameterState.CostBits > 256, "The source avatar is already within the synchronized parameter budget.");
            Require(parameterState.SafeCandidateNames.Count > 0, "The source avatar has no safe boolean toggle candidates.");
            Require(parameterState.CostBits - parameterState.SafeCandidateNames.Count <= 248, "The safe candidate set cannot cover compressor overhead.");
            Require(!parameterState.HasUnsafeCompressibleCandidate, "A dangerous parameter is exposed to the package compressor.");

            var primaryAssets = CollectPrimaryAssets(descriptor);
            var assetReceipts = CaptureAssetReceipts(primaryAssets);
            var referencedDirty = primaryAssets.Any(IsAssetDirty);
            Require(!EditorUtility.IsDirty(avatar) && !EditorUtility.IsDirty(descriptor), "The source avatar must be clean.");
            Require(!referencedDirty, "The source referenced assets must be clean.");
            var hierarchyDigest = ComputeHierarchyDigest(avatar);
            var controllerDigest = ComputeControllerDigest(descriptor);
            var menuDigest = ComputeMenuDigest(descriptor.expressionsMenu);
            var behaviorEvidence = ParameterBitPackingEvidence.Capture(avatar);
            var assetSetDigest = ComputeAssetSetDigest(assetReceipts);
            var sourceStateDigest = Sha256Framed(
                "vrcforge.parameter_source_state.v1",
                scenePath,
                sceneGuid,
                Sha256File(sceneFilePath),
                Sha256File(sceneMetaPath),
                objectPath,
                globalObjectId,
                hierarchyDigest,
                assetSetDigest,
                parameterState.StateDigest,
                controllerDigest,
                menuDigest,
                behaviorEvidence.ReceiptDigest
            );
            return new SourceSnapshot
            {
                Avatar = avatar,
                ScenePath = scenePath,
                SceneGuid = sceneGuid,
                SceneFilePath = sceneFilePath,
                SceneMetaPath = sceneMetaPath,
                SceneFileDigest = Sha256File(sceneFilePath),
                SceneMetaDigest = Sha256File(sceneMetaPath),
                ObjectPath = objectPath,
                GlobalObjectId = globalObjectId,
                HierarchyDigest = hierarchyDigest,
                SourceAssetSetDigest = assetSetDigest,
                SourceAssetCount = assetReceipts.Count,
                SourceAssetFilePaths = assetReceipts.SelectMany(receipt => receipt.AbsoluteFilePaths).Distinct(StringComparer.OrdinalIgnoreCase).ToList(),
                ParameterState = parameterState,
                ControllerStateDigest = controllerDigest,
                MenuStateDigest = menuDigest,
                BehaviorEvidence = behaviorEvidence,
                SourceStateDigest = sourceStateDigest,
                SourceDirty = false,
                ReferencedAssetsDirty = false,
                MenuUsage = menuUsage
            };
        }

        private static CapabilitySnapshot CaptureCapability()
        {
            var packageInfo = UnityEditor.PackageManager.PackageInfo.FindForAssetPath(PackageAssetManifest);
            Require(packageInfo != null, "The required package is not resolved.");
            Require(packageInfo.name == PackageId && packageInfo.version == PackageVersion, "The package id or version is not allowlisted.");
            var packageRoot = Path.GetFullPath(packageInfo.resolvedPath).TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar);
            var expectedRoot = Path.GetFullPath(Path.Combine(CurrentProjectPath(), "Packages", PackageId));
            Require(ProjectPathsEqual(packageRoot, expectedRoot), "The package must be an exact embedded project package.");
            var manifest = JObject.Parse(File.ReadAllText(Path.Combine(packageRoot, "package.json"), Encoding.UTF8));
            Require((string)manifest["name"] == PackageId && (string)manifest["version"] == PackageVersion, "The package manifest identity is invalid.");
            Require((string)manifest["author"]?["name"] == PackageAuthor, "The package manifest author is invalid.");
            var packageTree = CapturePackageTree(packageRoot);
            Require(packageTree.EntryCount == PackageFileCount && packageTree.Digest == PackageTreeSha256, "The package source tree is not allowlisted.");
            var rootIdentity = CaptureIdentity(packageRoot, isDirectory: true);
            Require(!rootIdentity.IsReparsePoint && rootIdentity.NumberOfLinks == 1, "The package source root is linked or reparsed.");

            var callbackAssembly = AppDomain.CurrentDomain.GetAssemblies().SingleOrDefault(assembly => assembly.GetName().Name == CallbackAssemblyName);
            Require(callbackAssembly != null && !string.IsNullOrWhiteSpace(callbackAssembly.Location), "The package callback assembly is unavailable.");
            var callbackName = callbackAssembly.GetName();
            var callbackHash = Sha256File(callbackAssembly.Location);
            Require(callbackName.Version.ToString() == CallbackAssemblyVersion, "The package callback assembly version is not allowlisted.");
            Require(PublicKeyToken(callbackName) == CallbackAssemblyPublicKeyToken, "The package callback assembly signature state is not allowlisted.");

            var sdkAssembly = typeof(VRCBuildPipelineCallbacks).Assembly;
            var sdkName = sdkAssembly.GetName();
            Require(sdkName.Name == SdkCallbackAssemblyName && sdkName.Version.ToString() == SdkCallbackAssemblyVersion, "The public callback assembly identity is not allowlisted.");
            Require(PublicKeyToken(sdkName) == SdkCallbackAssemblyPublicKeyToken, "The public callback assembly signature state is not allowlisted.");
            var sdkHash = Sha256File(sdkAssembly.Location);
            Require(SdkCallbackAssemblySha256Allowlist.Contains(sdkHash), "The public callback assembly bytes are not allowlisted.");
            var callbackMethod = typeof(VRCBuildPipelineCallbacks).GetMethod(
                "OnPreprocessAvatar",
                BindingFlags.Public | BindingFlags.Static,
                null,
                new[] { typeof(GameObject) },
                null
            );
            Require(callbackMethod != null && callbackMethod.ReturnType == typeof(bool), "The public avatar callback signature is unavailable.");
            var callbackTypes = TypeCache.GetTypesDerivedFrom<IVRCSDKPreprocessAvatarCallback>()
                .Where(type => !type.IsAbstract)
                .ToArray();
            var registered = callbackTypes
                .Where(type => type.FullName == RegisteredHookType && type.Assembly.GetName().Name == CallbackAssemblyName)
                .ToArray();
            Require(registered.Length == 1, "The package compressor hook registration is not allowlisted.");
            var callbackRoster = callbackTypes
                .Select(type => type.Assembly.GetName().Name + ":" + type.FullName)
                .OrderBy(value => value, StringComparer.Ordinal)
                .ToArray();
            Require(
                callbackRoster.Distinct(StringComparer.Ordinal).Count() == callbackRoster.Length,
                "The avatar preprocess callback roster contains duplicate identities."
            );
            var callbackRosterDigest = Sha256Framed(
                "vrcforge.avatar_callback_roster.v1",
                callbackRoster.Cast<object>().ToArray()
            );

            var runtimeAssembly = AppDomain.CurrentDomain.GetAssemblies()
                .SingleOrDefault(assembly => assembly.GetName().Name == PackageRuntimeAssemblyName);
            Require(runtimeAssembly != null && !string.IsNullOrWhiteSpace(runtimeAssembly.Location), "The package runtime assembly is unavailable.");
            var callbackAssemblyPaths = new List<string>();
            var callbackAssemblySetRows = new List<string>();
            foreach (var group in callbackTypes
                .Select(type => type.Assembly)
                .Concat(new[] { runtimeAssembly })
                .GroupBy(assembly => assembly.GetName().Name, StringComparer.Ordinal)
                .OrderBy(value => value.Key, StringComparer.Ordinal))
            {
                Require(!string.IsNullOrWhiteSpace(group.Key), "A callback assembly has no stable name.");
                var assemblies = group
                    .Where(assembly => assembly != null && !string.IsNullOrWhiteSpace(assembly.Location))
                    .GroupBy(assembly => Path.GetFullPath(assembly.Location), StringComparer.OrdinalIgnoreCase)
                    .Select(value => value.First())
                    .ToArray();
                Require(assemblies.Length == 1, "A callback assembly name resolves to multiple loaded binaries.");
                var assembly = assemblies[0];
                var name = assembly.GetName();
                Require(name.Name == group.Key && name.Version != null, "A callback assembly identity is incomplete.");
                var path = Path.GetFullPath(assembly.Location);
                var hash = Sha256File(path);
                callbackAssemblyPaths.Add(path);
                callbackAssemblySetRows.Add(
                    name.Name + "|" + name.Version + "|" + PublicKeyToken(name) + "|" + hash
                );
            }
            Require(
                callbackAssemblyPaths.Distinct(StringComparer.OrdinalIgnoreCase).Count() == callbackAssemblyPaths.Count,
                "The callback assembly path set contains aliases."
            );
            var callbackAssemblySetDigest = Sha256Framed(
                CallbackAssemblySetSchema,
                callbackAssemblySetRows.Cast<object>().ToArray()
            );
            var profile = CapabilityProfiles.SingleOrDefault(value =>
                value.CallbackAssemblySha256 == callbackHash
                    && value.CallbackRosterCount == callbackRoster.Length
                    && value.CallbackRosterDigest == callbackRosterDigest
                    && value.CallbackAssemblySetCount == callbackAssemblySetRows.Count
                    && value.CallbackAssemblySetDigest == callbackAssemblySetDigest);
            Require(
                profile != null,
                "The complete avatar preprocess capability profile is not allowlisted."
            );
            Require(
                callbackAssemblyPaths.Any(path => ProjectPathsEqual(path, callbackAssembly.Location))
                    && callbackAssemblyPaths.Any(path => ProjectPathsEqual(path, sdkAssembly.Location)),
                "The callback assembly set is incomplete."
            );

            var snapshot = new CapabilitySnapshot
            {
                PackageRootPath = packageRoot,
                PackageRootIdentityDigest = rootIdentity.Digest,
                ProfileId = profile.Id,
                CallbackAssemblySha256 = callbackHash,
                SdkCallbackAssemblySha256 = sdkHash,
                CallbackRosterCount = callbackRoster.Length,
                CallbackRosterDigest = callbackRosterDigest,
                CallbackAssemblySetCount = callbackAssemblySetRows.Count,
                CallbackAssemblySetDigest = callbackAssemblySetDigest,
                CallbackAssemblyPaths = callbackAssemblyPaths
            };
            snapshot.CapabilityDigest = Sha256Framed(
                CapabilitySchema,
                PackageId,
                PackageVersion,
                PackageAuthor,
                PackageArchiveSha256,
                PackageTreeSha256,
                PackageFileCount,
                snapshot.PackageRootIdentityDigest,
                snapshot.ProfileId,
                CallbackAssemblyName,
                CallbackAssemblyVersion,
                CallbackAssemblyPublicKeyToken,
                snapshot.CallbackAssemblySha256,
                SdkCallbackAssemblyName,
                SdkCallbackAssemblyVersion,
                SdkCallbackAssemblyPublicKeyToken,
                snapshot.SdkCallbackAssemblySha256,
                CallbackTypeName,
                CallbackSignature,
                RegisteredHookType,
                1,
                snapshot.CallbackRosterCount,
                snapshot.CallbackRosterDigest,
                snapshot.CallbackAssemblySetCount,
                snapshot.CallbackAssemblySetDigest
            );
            return snapshot;
        }

        private static PreferenceSnapshot CapturePreferences()
        {
            var compressorPresent = EditorPrefs.HasKey(CompressorPreferenceKey);
            var compressorValue = EditorPrefs.GetInt(CompressorPreferenceKey, 0);
            var alignMobilePresent = EditorPrefs.HasKey(AlignMobilePreferenceKey);
            var alignMobileValue = EditorPrefs.GetBool(AlignMobilePreferenceKey, true);
            return new PreferenceSnapshot
            {
                CompressorPresent = compressorPresent,
                CompressorValue = compressorValue,
                AlignMobilePresent = alignMobilePresent,
                AlignMobileValue = alignMobileValue,
                BuildTarget = EditorUserBuildSettings.activeBuildTarget.ToString()
            };
        }

        private static void PrepareCloneAssets(GameObject clone, Scene outputScene)
        {
            var descriptor = clone.GetComponent<VRCAvatarDescriptor>();
            Require(descriptor != null, "The output clone has no avatar descriptor.");
            Require(AssetDatabase.IsValidFolder(GeneratedRoot), "The generated build root is unavailable.");
            Require(!AssetDatabase.IsValidFolder(StagingRoot), "The generated input staging root already exists.");
            Require(!string.IsNullOrWhiteSpace(AssetDatabase.CreateFolder(GeneratedRoot, StagingFolderName)), "The generated input staging root could not be created.");

            if (descriptor.expressionParameters != null)
            {
                var parameters = Object.Instantiate(descriptor.expressionParameters);
                parameters.name = "Expression Parameters";
                AssetDatabase.CreateAsset(parameters, StagingRoot + "/Expression Parameters.asset");
                descriptor.expressionParameters = parameters;
            }

            if (descriptor.expressionsMenu != null)
            {
                var menuCopies = new Dictionary<VRCExpressionsMenu, VRCExpressionsMenu>();
                var menuIndex = 0;
                descriptor.expressionsMenu = CloneMenuGraph(descriptor.expressionsMenu, menuCopies, ref menuIndex);
            }

            var controllerCopies = new Dictionary<RuntimeAnimatorController, RuntimeAnimatorController>();
            var controllerIndex = 0;
            descriptor.baseAnimationLayers = CloneLayerControllers(descriptor.baseAnimationLayers, controllerCopies, ref controllerIndex);
            descriptor.specialAnimationLayers = CloneLayerControllers(descriptor.specialAnimationLayers, controllerCopies, ref controllerIndex);
            var animator = clone.GetComponent<Animator>();
            if (animator != null && animator.runtimeAnimatorController != null)
            {
                animator.runtimeAnimatorController = CloneController(animator.runtimeAnimatorController, controllerCopies, ref controllerIndex);
            }
            EnsureStagingAnchor(descriptor);
            EditorUtility.SetDirty(descriptor);
            RequireNoDirtyProjectAssets(outputScene, StagingRoot);
            AssetDatabase.SaveAssets();
            AssetDatabase.Refresh(ImportAssetOptions.ForceSynchronousImport);
            Require(IsStagedAsset(descriptor.expressionParameters), "The clone expression parameters escaped the generated input staging root.");
            Require(IsStagedAsset(descriptor.expressionsMenu), "The clone expression menu escaped the generated input staging root.");
        }

        private static void EnsureNonInteractiveBuildPolicy(GameObject clone)
        {
            var runtimeAssembly = AppDomain.CurrentDomain.GetAssemblies()
                .SingleOrDefault(assembly => assembly.GetName().Name == PackageRuntimeAssemblyName);
            Require(runtimeAssembly != null, "The package runtime assembly is unavailable for non-interactive policy.");
            var componentType = runtimeAssembly.GetType(PackageComponentTypeName, false);
            var featureType = runtimeAssembly.GetType(NonInteractiveFeatureTypeName, false);
            Require(
                componentType != null
                    && typeof(Component).IsAssignableFrom(componentType)
                    && !componentType.IsAbstract
                    && featureType != null
                    && !featureType.IsAbstract,
                "The package non-interactive policy types are unavailable."
            );
            var contentField = componentType.GetField("content", BindingFlags.Public | BindingFlags.Instance);
            var modeField = featureType.GetField("mode", BindingFlags.Public | BindingFlags.Instance);
            Require(
                contentField != null
                    && contentField.FieldType.IsAssignableFrom(featureType)
                    && modeField != null
                    && modeField.FieldType.IsEnum,
                "The package non-interactive policy contract changed."
            );
            var modeNames = Enum.GetNames(modeField.FieldType).OrderBy(value => value, StringComparer.Ordinal).ToArray();
            Require(
                modeNames.SequenceEqual(
                    new[] { "Auto", "Disabled", "ForceOff", "ForceOn" },
                    StringComparer.Ordinal
                ),
                "The package non-interactive policy modes changed."
            );

            var existingPolicies = clone.GetComponentsInChildren<Component>(true)
                .Where(component => component != null && componentType.IsInstanceOfType(component))
                .Where(component =>
                {
                    var content = contentField.GetValue(component);
                    return content != null && featureType.IsInstanceOfType(content);
                })
                .ToArray();
            Require(existingPolicies.Length <= 1, "The clone has duplicate non-interactive policy features.");
            if (existingPolicies.Length == 1) return;

            var policyComponent = clone.AddComponent(componentType);
            var policyFeature = Activator.CreateInstance(featureType);
            Require(policyComponent != null && policyFeature != null, "The clone non-interactive policy could not be created.");
            modeField.SetValue(policyFeature, Enum.Parse(modeField.FieldType, NonInteractiveFeatureModeName, false));
            contentField.SetValue(policyComponent, policyFeature);
            Require(
                featureType.IsInstanceOfType(contentField.GetValue(policyComponent))
                    && modeField.GetValue(policyFeature).ToString() == NonInteractiveFeatureModeName,
                "The clone non-interactive policy readback failed."
            );
            EditorUtility.SetDirty(policyComponent);
        }

        private static void EnsureAssetFolder(
            string assetPath,
            ICollection<CreatedAssetFolder> createdFolders)
        {
            Require(createdFolders != null, "The managed output folder ownership ledger is unavailable.");
            var normalized = assetPath.Replace('\\', '/').TrimEnd('/');
            Require(normalized.StartsWith("Assets/", StringComparison.Ordinal), "The managed output folder is outside project assets.");
            var parts = normalized.Split('/');
            var current = parts[0];
            for (var index = 1; index < parts.Length; index++)
            {
                var next = current + "/" + parts[index];
                if (!AssetDatabase.IsValidFolder(next))
                {
                    Require(AssetDatabase.LoadMainAssetAtPath(next) == null, "A managed output folder path is occupied by an asset.");
                    var createdGuid = AssetDatabase.CreateFolder(current, parts[index]);
                    Require(IsGuid(createdGuid), "A managed output folder could not be created.");
                    createdFolders.Add(CaptureCreatedAssetFolder(next, createdGuid.ToLowerInvariant()));
                }
                var identity = CaptureIdentity(AbsoluteProjectPath(next), true);
                Require(!identity.IsReparsePoint && identity.NumberOfLinks == 1, "A managed output folder is linked or reparsed.");
                current = next;
            }
        }

        private static CreatedAssetFolder CaptureCreatedAssetFolder(string assetPath, string expectedGuid)
        {
            Require(assetPath == OutputRoot || assetPath == OutputKindRoot,
                "The managed output folder ownership path is invalid.");
            var absolute = AbsoluteProjectPath(assetPath);
            Require(Directory.Exists(absolute), "An operation-created managed output folder is missing.");
            var directoryIdentity = CaptureIdentity(absolute, true);
            Require(!directoryIdentity.IsReparsePoint && directoryIdentity.NumberOfLinks == 1,
                "An operation-created managed output folder is linked or reparsed.");
            var metaPath = absolute + ".meta";
            RequireStableRegularFile(metaPath);
            var metaIdentity = CaptureIdentity(metaPath, false);
            var databaseGuid = AssetDatabase.AssetPathToGUID(assetPath).ToLowerInvariant();
            var metaGuid = ParseMetaGuid(ReadStableFileBytes(metaPath));
            Require(IsGuid(expectedGuid)
                    && databaseGuid == expectedGuid
                    && metaGuid == expectedGuid,
                "An operation-created managed output folder GUID is inconsistent.");
            return new CreatedAssetFolder
            {
                AssetPath = assetPath,
                Guid = expectedGuid,
                DirectoryIdentityDigest = directoryIdentity.Digest,
                MetaIdentityDigest = metaIdentity.Digest,
                MetaDigest = Sha256File(metaPath)
            };
        }

        private static void VerifyCreatedAssetFolder(CreatedAssetFolder expected)
        {
            Require(expected != null
                    && (expected.AssetPath == OutputRoot || expected.AssetPath == OutputKindRoot),
                "The managed output folder ownership receipt is invalid.");
            var actual = CaptureCreatedAssetFolder(expected.AssetPath, expected.Guid);
            Require(actual.DirectoryIdentityDigest == expected.DirectoryIdentityDigest
                    && actual.MetaIdentityDigest == expected.MetaIdentityDigest
                    && actual.MetaDigest == expected.MetaDigest,
                "An operation-created managed output folder changed before cleanup.");
        }

        private static VRCExpressionsMenu CloneMenuGraph(
            VRCExpressionsMenu source,
            Dictionary<VRCExpressionsMenu, VRCExpressionsMenu> copies,
            ref int index)
        {
            if (copies.TryGetValue(source, out var existing)) return existing;
            var clone = Object.Instantiate(source);
            clone.name = "Expression Menu " + index.ToString("000", CultureInfo.InvariantCulture);
            var path = StagingRoot + "/Menu " + index.ToString("000", CultureInfo.InvariantCulture) + ".asset";
            index++;
            AssetDatabase.CreateAsset(clone, path);
            copies.Add(source, clone);
            Require(clone.controls != null, "An expression menu has no control list.");
            foreach (var control in clone.controls)
            {
                if (control != null && control.subMenu != null)
                {
                    control.subMenu = CloneMenuGraph(control.subMenu, copies, ref index);
                }
            }
            EditorUtility.SetDirty(clone);
            return clone;
        }

        private static VRCAvatarDescriptor.CustomAnimLayer[] CloneLayerControllers(
            VRCAvatarDescriptor.CustomAnimLayer[] layers,
            Dictionary<RuntimeAnimatorController, RuntimeAnimatorController> copies,
            ref int index)
        {
            var result = (layers ?? Array.Empty<VRCAvatarDescriptor.CustomAnimLayer>()).ToArray();
            for (var layerIndex = 0; layerIndex < result.Length; layerIndex++)
            {
                var layer = result[layerIndex];
                if (!layer.isDefault && layer.animatorController != null)
                {
                    layer.animatorController = CloneController(layer.animatorController, copies, ref index);
                    result[layerIndex] = layer;
                }
            }
            return result;
        }

        private static RuntimeAnimatorController CloneController(
            RuntimeAnimatorController source,
            Dictionary<RuntimeAnimatorController, RuntimeAnimatorController> copies,
            ref int index)
        {
            if (copies.TryGetValue(source, out var existing)) return existing;
            var sourcePath = AssetDatabase.GetAssetPath(source);
            Require(!string.IsNullOrWhiteSpace(sourcePath), "A clone controller is not a persistent asset.");
            var extension = Path.GetExtension(sourcePath);
            Require(extension == ".controller" || extension == ".overrideController", "A clone controller type is not allowlisted.");
            var destination = StagingRoot + "/Controller " + index.ToString("000", CultureInfo.InvariantCulture) + extension;
            index++;
            Require(AssetDatabase.CopyAsset(sourcePath, destination), "A clone controller could not be copied.");
            var clone = AssetDatabase.LoadAssetAtPath<RuntimeAnimatorController>(destination);
            Require(clone != null, "A copied clone controller is unavailable.");
            copies.Add(source, clone);
            return clone;
        }

        private static void EnsureStagingAnchor(VRCAvatarDescriptor descriptor)
        {
            var layers = (descriptor.baseAnimationLayers ?? Array.Empty<VRCAvatarDescriptor.CustomAnimLayer>()).ToList();
            if (layers.Any(layer => !layer.isDefault && IsStagedAsset(layer.animatorController))) return;
            var anchorPath = StagingRoot + "/Anchor.controller";
            var anchor = AnimatorController.CreateAnimatorControllerAtPath(anchorPath);
            Require(anchor != null, "The generated input staging anchor could not be created.");
            var index = layers.FindIndex(layer => layer.type == VRCAvatarDescriptor.AnimLayerType.FX);
            if (index < 0)
            {
                layers.Add(
                    new VRCAvatarDescriptor.CustomAnimLayer
                    {
                        type = VRCAvatarDescriptor.AnimLayerType.FX,
                        isDefault = false,
                        animatorController = anchor
                    }
                );
            }
            else
            {
                var layer = layers[index];
                layer.isDefault = false;
                layer.animatorController = anchor;
                layers[index] = layer;
            }
            descriptor.baseAnimationLayers = layers.ToArray();
        }

        private static int RestoreSourceNullLayerMasks(
            VRCAvatarDescriptor descriptor,
            ParameterBehaviorEvidence sourceEvidence,
            Action<string> setStage)
        {
            setStage?.Invoke("evidence");
            Require(descriptor != null && sourceEvidence != null, "Layer-mask restoration evidence is unavailable.");
            var expectedNullMasks = new HashSet<string>(
                sourceEvidence.AnimatorRows
                    .Where(row => row.Kind == "layer"
                        && row.SemanticFields.Length > 2
                        && row.SemanticFields[2] == "null")
                    .Select(row => Frame(row.Scope) + Frame(row.SemanticName)),
                StringComparer.Ordinal);
            var observed = new HashSet<string>(StringComparer.Ordinal);
            var restored = 0;
            setStage?.Invoke("base");
            restored += RestoreSourceNullLayerMasks(
                descriptor.baseAnimationLayers,
                "base",
                expectedNullMasks,
                observed,
                setStage);
            setStage?.Invoke("special");
            restored += RestoreSourceNullLayerMasks(
                descriptor.specialAnimationLayers,
                "special",
                expectedNullMasks,
                observed,
                setStage);
            setStage?.Invoke("complete");
            Require(observed.SetEquals(expectedNullMasks), "An existing null layer mask could not be matched after preprocessing.");
            return restored;
        }

        private static int RestoreSourceNullLayerMasks(
            VRCAvatarDescriptor.CustomAnimLayer[] descriptorLayers,
            string group,
            ISet<string> expectedNullMasks,
            ISet<string> observed,
            Action<string> setStage)
        {
            var restored = 0;
            foreach (var descriptorLayer in descriptorLayers ?? Array.Empty<VRCAvatarDescriptor.CustomAnimLayer>())
            {
                if (descriptorLayer.animatorController == null) continue;
                setStage?.Invoke(group + "_controller_type");
                AnimatorController controller;
                if (descriptorLayer.animatorController is AnimatorController directController)
                {
                    controller = directController;
                }
                else if (descriptorLayer.animatorController is AnimatorOverrideController overrideController)
                {
                    controller = overrideController.runtimeAnimatorController as AnimatorController;
                    Require(controller != null, "An output animator override controller has no direct base controller.");
                }
                else
                {
                    throw new InvalidOperationException("The output animator controller type is unsupported for layer-mask restoration.");
                }

                var role = group + ":" + descriptorLayer.type;
                var layers = controller.layers ?? Array.Empty<AnimatorControllerLayer>();
                var changed = false;
                for (var index = 0; index < layers.Length; index++)
                {
                    var layer = layers[index];
                    var identity = Frame(role) + Frame(layer.name ?? string.Empty);
                    if (!expectedNullMasks.Contains(identity)) continue;
                    setStage?.Invoke(group + "_layer_unique");
                    Require(observed.Add(identity), "An existing null layer mask matched more than one output layer.");
                    if (layer.avatarMask == null) continue;
                    var controllerPath = AssetDatabase.GetAssetPath(controller);
                    var ownershipCategory = !EditorUtility.IsPersistent(controller)
                        ? "transient"
                        : IsGeneratedMutationPath(controllerPath) ? "generated" : "persistent_other";
                    setStage?.Invoke(group + "_ownership_" + ownershipCategory);
                    Require(
                        !EditorUtility.IsPersistent(controller)
                            || IsGeneratedMutationPath(controllerPath),
                        "A source animator controller cannot be mutated during layer-mask restoration.");
                    setStage?.Invoke(group + "_write");
                    layer.avatarMask = null;
                    layers[index] = layer;
                    changed = true;
                    restored++;
                }
                if (!changed) continue;
                controller.layers = layers;
                EditorUtility.SetDirty(controller);
            }
            return restored;
        }

        private static bool IsGeneratedMutationPath(string assetPath)
        {
            return !string.IsNullOrWhiteSpace(assetPath)
                && (assetPath.StartsWith(GeneratedRoot + "/", StringComparison.Ordinal)
                    || assetPath.StartsWith(AuxiliaryGeneratedRoot + "/", StringComparison.Ordinal));
        }

        private static bool IsStagedAsset(Object asset)
        {
            if (asset == null) return false;
            var path = AssetDatabase.GetAssetPath(asset);
            return path == StagingRoot || path.StartsWith(StagingRoot + "/", StringComparison.Ordinal);
        }

        private static ParameterState CaptureParameterState(
            VRCExpressionParameters parameters,
            IReadOnlyDictionary<string, HashSet<string>> menuUsage)
        {
            Require(parameters != null && parameters.parameters != null, "Expression parameters are unavailable.");
            var networkSyncedField = typeof(VRCExpressionParameters.Parameter).GetField("networkSynced", BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Instance);
            Require(networkSyncedField != null && networkSyncedField.FieldType == typeof(bool), "The expression parameter synchronization field is not allowlisted.");
            var rows = new List<ParameterRow>();
            var names = new HashSet<string>(StringComparer.Ordinal);
            foreach (var parameter in parameters.parameters)
            {
                Require(parameter != null && !string.IsNullOrWhiteSpace(parameter.name), "Expression parameter name is invalid.");
                Require(names.Add(parameter.name), "Expression parameter names must be unique.");
                var typeName = parameter.valueType.ToString();
                Require(typeName == "Bool" || typeName == "Int" || typeName == "Float", "Expression parameter type is not allowlisted.");
                var networkSynced = (bool)networkSyncedField.GetValue(parameter);
                rows.Add(
                    new ParameterRow
                    {
                        Name = parameter.name,
                        Type = typeName,
                        DefaultValue = parameter.defaultValue,
                        Saved = parameter.saved,
                        NetworkSynced = networkSynced
                    }
                );
            }
            rows.Sort((left, right) => string.CompareOrdinal(left.Name, right.Name));

            var safe = new List<string>();
            var excluded = new List<ExcludedParameter>();
            var unsafeCompressible = false;
            foreach (var row in rows)
            {
                var reasons = new SortedSet<string>(StringComparer.Ordinal);
                var usage = menuUsage.TryGetValue(row.Name, out var controls)
                    ? controls
                    : new HashSet<string>(StringComparer.Ordinal);
                if (row.Type != "Bool") reasons.Add("float_or_int");
                if (!row.NetworkSynced) reasons.Add("not_network_synced");
                if (usage.Contains("RadialPuppet") || usage.Contains("TwoAxisPuppet") || usage.Contains("FourAxisPuppet")) reasons.Add("puppet");
                if (usage.Count == 0) reasons.Add("osc_or_unmapped");
                if (!(usage.Count == 1 && usage.Contains("Toggle"))) reasons.Add("not_toggle_only");
                if (IsFaceTrackingName(row.Name)) reasons.Add("face_tracking");
                var isSafe = row.Type == "Bool"
                    && row.NetworkSynced
                    && usage.Count == 1
                    && usage.Contains("Toggle")
                    && !IsFaceTrackingName(row.Name);
                if (isSafe)
                {
                    safe.Add(row.Name);
                }
                else
                {
                    if (row.NetworkSynced && usage.Any(IsPackageCompressibleMenuType)) unsafeCompressible = true;
                    excluded.Add(
                        new ExcludedParameter
                        {
                            Name = row.Name,
                            Type = row.Type,
                            NetworkSynced = row.NetworkSynced,
                            Reasons = reasons.ToList(),
                            StateDigest = row.StateDigest
                        }
                    );
                }
            }
            safe.Sort(StringComparer.Ordinal);
            excluded.Sort((left, right) => string.CompareOrdinal(left.Name, right.Name));
            var stateDigest = Sha256Utf8(
                "vrcforge.parameter_state.v1\n"
                + string.Concat(rows.Select(row => Frame(row.Name) + Frame(row.Type) + Frame(FloatText(row.DefaultValue)) + Frame(row.Saved) + Frame(row.NetworkSynced)))
            );
            var safeDigest = Sha256Utf8(SafeNamesSchema + "\n" + string.Concat(safe.Select(Frame)));
            var excludedDigest = ComputeExcludedDigest(excluded);
            return new ParameterState
            {
                Parameters = rows,
                CostBits = rows.Where(row => row.NetworkSynced).Sum(row => row.Type == "Bool" ? 1 : 8),
                SafeCandidateNames = safe,
                SafeCandidateDigest = safeDigest,
                Excluded = excluded,
                ExcludedDigest = excludedDigest,
                StateDigest = stateDigest,
                HasUnsafeCompressibleCandidate = unsafeCompressible
            };
        }

        private static List<string> VerifyOutputParameters(ParameterState before, ParameterState after)
        {
            var afterByName = after.Parameters.ToDictionary(row => row.Name, StringComparer.Ordinal);
            var safe = new HashSet<string>(before.SafeCandidateNames, StringComparer.Ordinal);
            var compressed = new List<string>();
            foreach (var row in before.Parameters)
            {
                Require(afterByName.TryGetValue(row.Name, out var output), "The output clone removed a source parameter.");
                Require(output.Type == row.Type && output.DefaultValue.Equals(row.DefaultValue) && output.Saved == row.Saved, "The output clone changed source parameter semantics.");
                if (safe.Contains(row.Name))
                {
                    Require(row.NetworkSynced, "An approved safe parameter was not synchronized before the build.");
                    if (!output.NetworkSynced) compressed.Add(row.Name);
                }
                else
                {
                    Require(output.NetworkSynced == row.NetworkSynced && output.StateDigest == row.StateDigest, "A dangerous or excluded parameter drifted.");
                }
            }
            compressed.Sort(StringComparer.Ordinal);
            return compressed;
        }

        private static IReadOnlyDictionary<string, HashSet<string>> CaptureMenuUsage(VRCExpressionsMenu root)
        {
            var usage = new Dictionary<string, HashSet<string>>(StringComparer.Ordinal);
            var visited = new HashSet<VRCExpressionsMenu>();
            void Add(string name, string controlType)
            {
                if (string.IsNullOrWhiteSpace(name)) return;
                if (!usage.TryGetValue(name, out var types))
                {
                    types = new HashSet<string>(StringComparer.Ordinal);
                    usage[name] = types;
                }
                types.Add(controlType);
            }
            void Walk(VRCExpressionsMenu menu)
            {
                if (menu == null || !visited.Add(menu)) return;
                foreach (var control in menu.controls ?? new List<VRCExpressionsMenu.Control>())
                {
                    Require(control != null, "Expression menu contains an invalid control.");
                    var type = control.type.ToString();
                    Add(control.parameter?.name, type);
                    foreach (var parameter in control.subParameters ?? Array.Empty<VRCExpressionsMenu.Control.Parameter>())
                    {
                        Add(parameter?.name, type);
                    }
                    Walk(control.subMenu);
                }
            }
            Walk(root);
            return usage;
        }

        private static List<Object> CollectPrimaryAssets(VRCAvatarDescriptor descriptor)
        {
            var assets = new List<Object>();
            if (descriptor.expressionParameters != null) assets.Add(descriptor.expressionParameters);
            var visitedMenus = new HashSet<VRCExpressionsMenu>();
            void AddMenu(VRCExpressionsMenu menu)
            {
                if (menu == null || !visitedMenus.Add(menu)) return;
                assets.Add(menu);
                foreach (var control in menu.controls ?? new List<VRCExpressionsMenu.Control>()) AddMenu(control?.subMenu);
            }
            AddMenu(descriptor.expressionsMenu);
            foreach (var layer in descriptor.baseAnimationLayers ?? Array.Empty<VRCAvatarDescriptor.CustomAnimLayer>())
            {
                if (layer.animatorController != null) assets.Add(layer.animatorController);
            }
            foreach (var layer in descriptor.specialAnimationLayers ?? Array.Empty<VRCAvatarDescriptor.CustomAnimLayer>())
            {
                if (layer.animatorController != null) assets.Add(layer.animatorController);
            }
            return assets.Distinct().ToList();
        }

        private static List<AssetReceipt> CaptureAssetReceipts(List<Object> primaryAssets)
        {
            var primaryPaths = primaryAssets.Select(AssetDatabase.GetAssetPath).Where(path => !string.IsNullOrWhiteSpace(path)).Distinct(StringComparer.Ordinal).ToArray();
            Require(primaryPaths.Length >= 1, "The source avatar has no persistent parameter assets.");
            var dependencies = AssetDatabase.GetDependencies(primaryPaths, true)
                .Where(path => path.StartsWith("Assets/", StringComparison.Ordinal) || path.StartsWith("Packages/", StringComparison.Ordinal))
                .Concat(primaryPaths)
                .Distinct(StringComparer.Ordinal)
                .OrderBy(path => path, StringComparer.Ordinal)
                .ToArray();
            var receipts = new List<AssetReceipt>();
            foreach (var assetPath in dependencies)
            {
                var absolute = AbsoluteProjectPath(assetPath);
                if (!File.Exists(absolute)) continue;
                RequireStableRegularFile(absolute);
                var files = new List<string> { absolute };
                var meta = absolute + ".meta";
                if (File.Exists(meta))
                {
                    RequireStableRegularFile(meta);
                    files.Add(meta);
                }
                receipts.Add(
                    new AssetReceipt
                    {
                        AssetPath = assetPath,
                        Guid = AssetDatabase.AssetPathToGUID(assetPath).ToLowerInvariant(),
                        FileDigest = Sha256File(absolute),
                        MetaDigest = File.Exists(meta) ? Sha256File(meta) : string.Empty,
                        AbsoluteFilePaths = files
                    }
                );
            }
            Require(receipts.Count > 0, "The source asset set is empty.");
            return receipts;
        }

        private static string ComputeAssetSetDigest(List<AssetReceipt> receipts)
        {
            return Sha256Utf8(
                "vrcforge.parameter_source_assets.v1\n"
                + string.Concat(receipts.OrderBy(receipt => receipt.AssetPath, StringComparer.Ordinal).Select(receipt =>
                    Frame(receipt.AssetPath) + Frame(receipt.Guid) + Frame(receipt.FileDigest) + Frame(receipt.MetaDigest)))
            );
        }

        private static string ComputeControllerDigest(VRCAvatarDescriptor descriptor)
        {
            var controllers = new List<RuntimeAnimatorController>();
            controllers.AddRange((descriptor.baseAnimationLayers ?? Array.Empty<VRCAvatarDescriptor.CustomAnimLayer>()).Select(layer => layer.animatorController).Where(controller => controller != null));
            controllers.AddRange((descriptor.specialAnimationLayers ?? Array.Empty<VRCAvatarDescriptor.CustomAnimLayer>()).Select(layer => layer.animatorController).Where(controller => controller != null));
            var rows = controllers.Distinct().Select(controller =>
            {
                var path = AssetDatabase.GetAssetPath(controller);
                return Frame(path) + Frame(AssetDatabase.AssetPathToGUID(path).ToLowerInvariant()) + Frame(EditorJsonUtility.ToJson(controller, false));
            }).OrderBy(row => row, StringComparer.Ordinal);
            return Sha256Utf8("vrcforge.parameter_controllers.v1\n" + string.Concat(rows));
        }

        private static string ComputeMenuDigest(VRCExpressionsMenu root)
        {
            var rows = new List<string>();
            var visited = new HashSet<VRCExpressionsMenu>();
            void Walk(VRCExpressionsMenu menu)
            {
                if (menu == null || !visited.Add(menu)) return;
                var path = AssetDatabase.GetAssetPath(menu);
                rows.Add(Frame(path) + Frame(AssetDatabase.AssetPathToGUID(path).ToLowerInvariant()) + Frame(EditorJsonUtility.ToJson(menu, false)));
                foreach (var control in menu.controls ?? new List<VRCExpressionsMenu.Control>()) Walk(control?.subMenu);
            }
            Walk(root);
            rows.Sort(StringComparer.Ordinal);
            return Sha256Utf8("vrcforge.parameter_menus.v1\n" + string.Concat(rows));
        }

        private static string ComputeHierarchyDigest(GameObject root)
        {
            var builder = new StringBuilder("vrcforge.parameter_hierarchy.v1\n");
            void Walk(Transform transform, string path)
            {
                builder.Append(Frame(path));
                builder.Append(Frame(transform.gameObject.activeSelf));
                builder.Append(Frame(VectorText(transform.localPosition)));
                builder.Append(Frame(QuaternionText(transform.localRotation)));
                builder.Append(Frame(VectorText(transform.localScale)));
                foreach (var component in transform.GetComponents<Component>())
                {
                    Require(component != null, "The source hierarchy contains a missing component.");
                    builder.Append(Frame(component.GetType().AssemblyQualifiedName));
                    builder.Append(Frame(EditorJsonUtility.ToJson(component, false)));
                }
                for (var index = 0; index < transform.childCount; index++)
                {
                    var child = transform.GetChild(index);
                    Walk(child, path + "/" + child.name);
                }
            }
            Walk(root.transform, root.name);
            return Sha256Utf8(builder.ToString());
        }

        private static string ComputeSourceObjectPath(GameObject obj)
        {
            var names = new List<string>();
            var current = obj.transform;
            while (current != null)
            {
                names.Add(current.name);
                current = current.parent;
            }
            names.Reverse();
            return string.Join("/", names);
        }

        private static GameObject FindUniqueGameObject(Scene scene, string objectPath)
        {
            var matches = scene.GetRootGameObjects()
                .SelectMany(root => root.GetComponentsInChildren<Transform>(true))
                .Select(transform => transform.gameObject)
                .Where(obj => ComputeSourceObjectPath(obj) == objectPath)
                .ToArray();
            Require(matches.Length == 1, "The source avatar path is missing or ambiguous.");
            return matches[0];
        }

        private static VRCExpressionParameters RequireParameters(VRCAvatarDescriptor descriptor)
        {
            Require(descriptor.expressionParameters != null, "The avatar has no expression parameter asset.");
            var path = AssetDatabase.GetAssetPath(descriptor.expressionParameters);
            Require(path.StartsWith("Assets/", StringComparison.Ordinal), "Expression parameters must be a persistent project asset.");
            return descriptor.expressionParameters;
        }

        private static VRCExpressionParameters RequireOutputParameters(VRCAvatarDescriptor descriptor)
        {
            var parameters = descriptor.expressionParameters;
            Require(parameters != null && parameters.parameters != null, "Output expression parameters are unavailable.");
            var path = AssetDatabase.GetAssetPath(parameters);
            Require(
                string.IsNullOrWhiteSpace(path)
                    || path.StartsWith(GeneratedRoot + "/", StringComparison.Ordinal)
                    || path.StartsWith(OutputKindRoot + "/", StringComparison.Ordinal),
                "Output expression parameters escaped the generated build scope."
            );
            return parameters;
        }

        private static TreeSnapshot CaptureProtectedTree()
        {
            var project = CurrentProjectPath();
            var entries = new SortedDictionary<string, TreeEntry>(StringComparer.Ordinal);
            foreach (var relativeRoot in ProtectedProjectRoots)
            {
                CaptureProtectedRoot(Path.Combine(project, relativeRoot), relativeRoot, entries);
            }
            return TreeSnapshot.FromEntries(ProtectedTreeSchema, entries);
        }

        private static AuxiliaryGeneratedSnapshot CaptureAuxiliaryGenerated()
        {
            var packageRoot = AbsoluteProjectPath(AuxiliaryPackageRoot);
            Require(Directory.Exists(packageRoot), "The auxiliary package root is missing.");
            var packageIdentity = CaptureIdentity(packageRoot, true);
            Require(!packageIdentity.IsReparsePoint && packageIdentity.NumberOfLinks == 1,
                "The auxiliary package root is linked or reparsed.");
            var packageManifest = AbsoluteProjectPath(AuxiliaryPackageManifest);
            RequireStableRegularFile(packageManifest);
            var manifestIdentity = CaptureIdentity(packageManifest, false);
            var tree = CaptureManagedTree(AuxiliaryGeneratedRoot, AuxiliaryGeneratedTreeSchema);
            Require(tree.EntryCount <= CacheBackupMaxEntries,
                "The auxiliary generated root exceeds the backup entry limit.");
            Require(tree.TotalBytes <= CacheBackupMaxBytes,
                "The auxiliary generated root exceeds the backup byte limit.");
            var snapshot = new AuxiliaryGeneratedSnapshot
            {
                Tree = tree,
                PackageRootIdentityDigest = packageIdentity.Digest,
                PackageManifestDigest = Sha256File(packageManifest),
                PackageManifestIdentityDigest = manifestIdentity.Digest
            };
            snapshot.ReceiptDigest = Sha256Framed(
                AuxiliarySnapshotSchema,
                AuxiliaryPackageRoot,
                snapshot.PackageRootIdentityDigest,
                AuxiliaryPackageManifest,
                snapshot.PackageManifestDigest,
                snapshot.PackageManifestIdentityDigest,
                AuxiliaryGeneratedRoot,
                tree.Exists,
                tree.Digest,
                tree.ContentDigest,
                tree.EntryCount,
                tree.TotalBytes
            );
            return snapshot;
        }

        private static bool AuxiliaryContentEquals(
            AuxiliaryGeneratedSnapshot expected,
            AuxiliaryGeneratedSnapshot actual)
        {
            if (expected == null || actual == null) return false;
            return actual.PackageRootIdentityDigest == expected.PackageRootIdentityDigest
                && actual.PackageManifestDigest == expected.PackageManifestDigest
                && actual.PackageManifestIdentityDigest == expected.PackageManifestIdentityDigest
                && actual.Tree.Exists == expected.Tree.Exists
                && actual.Tree.ContentDigest == expected.Tree.ContentDigest
                && actual.Tree.EntryCount == expected.Tree.EntryCount
                && actual.Tree.TotalBytes == expected.Tree.TotalBytes
                && (expected.Tree.Exists || actual.Tree.Digest == expected.Tree.Digest);
        }

        private static RootIdentitySnapshot CaptureRootIdentities(TreeSnapshot generatedBaseline)
        {
            Require(generatedBaseline != null, "The generated cache root baseline is unavailable.");
            var entries = new SortedDictionary<string, FileIdentity>(StringComparer.Ordinal);
            foreach (var root in RequiredRootPaths())
            {
                Require(Directory.Exists(root.Value), "A required project root is missing.");
                var identity = CaptureIdentity(root.Value, true);
                Require(!identity.IsReparsePoint && identity.NumberOfLinks == 1, "A required project root is linked or reparsed.");
                entries.Add(root.Key, identity);
            }
            if (generatedBaseline.Exists)
            {
                var generatedRoot = AbsoluteProjectPath(GeneratedRoot);
                Require(Directory.Exists(generatedRoot), "The generated cache root is missing.");
                var generatedIdentity = CaptureIdentity(generatedRoot, true);
                Require(!generatedIdentity.IsReparsePoint && generatedIdentity.NumberOfLinks == 1,
                    "The generated cache root is linked or reparsed.");
                entries.Add(GeneratedRoot, generatedIdentity);
            }
            else
            {
                entries.Add(GeneratedRoot, new FileIdentity
                {
                    Digest = Sha256Utf8(RootIdentitySchema + ".absent\n" + Frame(GeneratedRoot))
                });
            }
            return RootIdentitySnapshot.FromEntries(entries);
        }

        private static IReadOnlyList<KeyValuePair<string, string>> RequiredRootPaths()
        {
            var project = CurrentProjectPath();
            return new[]
            {
                new KeyValuePair<string, string>("Project", project),
                new KeyValuePair<string, string>("Assets", Path.Combine(project, "Assets")),
                new KeyValuePair<string, string>("Packages", Path.Combine(project, "Packages")),
                new KeyValuePair<string, string>("ProjectSettings", Path.Combine(project, "ProjectSettings")),
                new KeyValuePair<string, string>("Packages/com.vrcfury.temp", Path.Combine(project, "Packages", "com.vrcfury.temp")),
                new KeyValuePair<string, string>(AuxiliaryPackageRoot, AbsoluteProjectPath(AuxiliaryPackageRoot))
            };
        }

        private static void CaptureProtectedRoot(string root, string relativeRoot, SortedDictionary<string, TreeEntry> entries)
        {
            Require(Directory.Exists(root), "A protected project root is missing.");
            var rootIdentity = CaptureIdentity(root, true);
            Require(!rootIdentity.IsReparsePoint && rootIdentity.NumberOfLinks == 1, "A protected project root is linked or reparsed.");
            CaptureProtectedDirectory(root, root, relativeRoot, entries);
        }

        private static void CaptureProtectedDirectory(
            string root,
            string current,
            string relativeRoot,
            SortedDictionary<string, TreeEntry> entries)
        {
            foreach (var path in Directory.EnumerateFileSystemEntries(current, "*", SearchOption.TopDirectoryOnly).OrderBy(value => value, StringComparer.OrdinalIgnoreCase))
            {
                var relative = relativeRoot + "/" + RelativePath(root, path);
                if (relative.Equals(GeneratedRoot, StringComparison.OrdinalIgnoreCase) || relative.StartsWith(GeneratedRoot + "/", StringComparison.OrdinalIgnoreCase)) continue;
                if (relative.Equals(AuxiliaryGeneratedRoot, StringComparison.OrdinalIgnoreCase)
                    || relative.Equals(AuxiliaryGeneratedRoot + ".meta", StringComparison.OrdinalIgnoreCase)
                    || relative.StartsWith(AuxiliaryGeneratedRoot + "/", StringComparison.OrdinalIgnoreCase)) continue;
                if (relative.Equals(OutputRoot, StringComparison.OrdinalIgnoreCase)
                    || relative.Equals(OutputRoot + ".meta", StringComparison.OrdinalIgnoreCase)
                    || relative.StartsWith(OutputRoot + "/", StringComparison.OrdinalIgnoreCase)) continue;
                var attributes = File.GetAttributes(path);
                var isDirectory = (attributes & FileAttributes.Directory) != 0;
                var identity = CaptureIdentity(path, isDirectory);
                Require(!identity.IsReparsePoint && identity.NumberOfLinks == 1, "A protected project path is linked or reparsed.");
                entries.Add(
                    relative,
                    new TreeEntry
                    {
                        RelativePath = relative,
                        Kind = isDirectory ? "D" : "F",
                        Length = isDirectory ? 0 : new FileInfo(path).Length,
                        Digest = isDirectory ? identity.Digest : Sha256File(path),
                        IdentityDigest = identity.Digest
                    }
                );
                if (isDirectory) CaptureProtectedDirectory(root, path, relativeRoot, entries);
            }
        }

        private static TreeSnapshot CaptureTree(string assetPath, string schema, bool requireExists)
        {
            var absolute = AbsoluteProjectPath(assetPath);
            if (!Directory.Exists(absolute))
            {
                Require(!File.Exists(absolute), "The package generated build root collides with a file.");
                Require(!File.Exists(absolute + ".meta"), "The package generated build root metadata exists without its directory.");
                Require(!requireExists, "The package generated build root is missing.");
                return new TreeSnapshot
                {
                    Digest = Sha256Utf8(schema + "\n" + Frame(false)),
                    ContentDigest = Sha256Utf8(schema + ".content\n" + Frame(false)),
                    EntryCount = 0,
                    TotalBytes = 0,
                    Entries = new SortedDictionary<string, TreeEntry>(StringComparer.Ordinal),
                    Exists = false
                };
            }
            return CaptureTreeAbsolute(absolute, schema);
        }

        private static TreeSnapshot CaptureManagedTree(string assetPath, string schema)
        {
            var absolute = AbsoluteProjectPath(assetPath);
            return CaptureManagedTreeAbsolute(absolute, absolute + ".meta", schema);
        }

        private static TreeSnapshot CaptureManagedTreeAbsolute(string absolute, string meta, string schema)
        {
            if (!Directory.Exists(absolute))
            {
                Require(!File.Exists(absolute), "A managed tree root collides with a file.");
                Require(!File.Exists(meta), "A managed output root metadata file exists without its directory.");
                return new TreeSnapshot
                {
                    Digest = Sha256Utf8(schema + "\n" + Frame(false)),
                    ContentDigest = Sha256Utf8(schema + ".content\n" + Frame(false)),
                    EntryCount = 0,
                    TotalBytes = 0,
                    Entries = new SortedDictionary<string, TreeEntry>(StringComparer.Ordinal),
                    Exists = false
                };
            }
            RequireStableRegularFile(meta);
            var rootIdentity = CaptureIdentity(absolute, true);
            Require(!rootIdentity.IsReparsePoint && rootIdentity.NumberOfLinks == 1, "The managed output root is linked or reparsed.");
            var inner = CaptureTreeAbsolute(absolute, schema + ".inner");
            var entries = new SortedDictionary<string, TreeEntry>(inner.Entries, StringComparer.Ordinal)
            {
                ["$root"] = new TreeEntry
                {
                    RelativePath = "$root",
                    Kind = "D",
                    Length = 0,
                    Digest = rootIdentity.Digest,
                    IdentityDigest = rootIdentity.Digest
                },
                ["$root.meta"] = new TreeEntry
                {
                    RelativePath = "$root.meta",
                    Kind = "F",
                    Length = new FileInfo(meta).Length,
                    Digest = Sha256File(meta),
                    IdentityDigest = CaptureIdentity(meta, false).Digest
                }
            };
            return TreeSnapshot.FromEntries(schema, entries);
        }

        private static TreeSnapshot CapturePackageTree(string root)
        {
            Require(Directory.Exists(root), "The package source root is missing.");
            var rootIdentity = CaptureIdentity(root, true);
            Require(!rootIdentity.IsReparsePoint && rootIdentity.NumberOfLinks == 1, "The package source root is linked or reparsed.");
            var entries = new SortedDictionary<string, TreeEntry>(StringComparer.Ordinal);
            var pending = new Stack<string>();
            pending.Push(root);
            while (pending.Count > 0)
            {
                var current = pending.Pop();
                foreach (var path in Directory.EnumerateFileSystemEntries(current, "*", SearchOption.TopDirectoryOnly).OrderByDescending(value => value, StringComparer.OrdinalIgnoreCase))
                {
                    var attributes = File.GetAttributes(path);
                    var isDirectory = (attributes & FileAttributes.Directory) != 0;
                    var identity = CaptureIdentity(path, isDirectory);
                    Require(!identity.IsReparsePoint && identity.NumberOfLinks == 1, "A package source path is linked or reparsed.");
                    if (isDirectory)
                    {
                        pending.Push(path);
                        continue;
                    }
                    var relative = RelativePath(root, path);
                    var length = new FileInfo(path).Length;
                    var digest = Sha256File(path);
                    entries.Add(
                        relative,
                        new TreeEntry
                        {
                            RelativePath = relative,
                            Kind = "F",
                            Length = length,
                            Digest = digest,
                            IdentityDigest = identity.Digest
                        }
                    );
                }
            }
            var canonical = PackageTreeSchema + "\n"
                + string.Concat(entries.Values.Select(entry =>
                    Encoding.UTF8.GetByteCount(entry.RelativePath).ToString(CultureInfo.InvariantCulture)
                    + ":" + entry.RelativePath
                    + ":" + entry.Length.ToString(CultureInfo.InvariantCulture)
                    + ":" + entry.Digest + "\n"));
            return new TreeSnapshot
            {
                Digest = Sha256Utf8(canonical),
                EntryCount = entries.Count,
                Entries = entries
            };
        }

        private static TreeSnapshot CaptureTreeAbsolute(string root, string schema)
        {
            var entries = new SortedDictionary<string, TreeEntry>(StringComparer.Ordinal);
            Require(Directory.Exists(root), "A required tree root is missing.");
            var rootIdentity = CaptureIdentity(root, true);
            Require(!rootIdentity.IsReparsePoint && rootIdentity.NumberOfLinks == 1, "A required tree root is linked or reparsed.");
            var pending = new Stack<string>();
            pending.Push(root);
            while (pending.Count > 0)
            {
                var current = pending.Pop();
                foreach (var path in Directory.EnumerateFileSystemEntries(current, "*", SearchOption.TopDirectoryOnly).OrderByDescending(value => value, StringComparer.OrdinalIgnoreCase))
                {
                    var relative = RelativePath(root, path);
                    var attributes = File.GetAttributes(path);
                    var isDirectory = (attributes & FileAttributes.Directory) != 0;
                    var identity = CaptureIdentity(path, isDirectory);
                    Require(!identity.IsReparsePoint && identity.NumberOfLinks == 1, "A required tree path is linked or reparsed.");
                    entries.Add(
                        relative,
                        new TreeEntry
                        {
                            RelativePath = relative,
                            Kind = isDirectory ? "D" : "F",
                            Length = isDirectory ? 0 : new FileInfo(path).Length,
                            Digest = isDirectory ? identity.Digest : Sha256File(path),
                            IdentityDigest = identity.Digest
                        }
                    );
                    if (isDirectory) pending.Push(path);
                }
            }
            return TreeSnapshot.FromEntries(schema, entries);
        }

        private static GeneratedDelta CompareGeneratedTrees(TreeSnapshot before, TreeSnapshot after)
        {
            var added = after.Entries.Keys.Except(before.Entries.Keys, StringComparer.Ordinal).Select(key => after.Entries[key]).ToList();
            var removed = before.Entries.Keys.Except(after.Entries.Keys, StringComparer.Ordinal).Select(key => before.Entries[key]).ToList();
            var modified = before.Entries.Keys.Intersect(after.Entries.Keys, StringComparer.Ordinal).Where(key => before.Entries[key].Receipt != after.Entries[key].Receipt).Select(key => after.Entries[key]).ToList();
            return new GeneratedDelta { Added = added, Removed = removed, Modified = modified };
        }

        private static void RequireGeneratedSubtree(List<TreeEntry> added, string expectedRoot)
        {
            var roots = new HashSet<string>(StringComparer.Ordinal);
            foreach (var entry in added)
            {
                var first = entry.RelativePath.Split('/')[0];
                if (first.EndsWith(".meta", StringComparison.Ordinal)) first = first.Substring(0, first.Length - 5);
                Require(!string.IsNullOrWhiteSpace(first), "A generated output path is invalid.");
                roots.Add(first);
            }
            Require(!roots.Contains(StagingFolderName), "The generated input staging subtree was not consumed.");
            Require(roots.Count == 1, "The public preprocess pipeline wrote an unexpected generated scope.");
            Require(roots.SetEquals(new[] { expectedRoot }), "The generated output subtree does not match the approved clone.");
        }

        private static void RequireManagedOutputSubtree(List<TreeEntry> added, string cloneName)
        {
            var target = "ParameterBitPacking/" + cloneName;
            var prefab = target + "/" + cloneName + ".prefab";
            foreach (var entry in added)
            {
                var path = entry.RelativePath;
                var allowed = path == "$root"
                    || path == "$root.meta"
                    || path == "ParameterBitPacking"
                    || path == "ParameterBitPacking.meta"
                    || path == target
                    || path == target + ".meta"
                    || path.StartsWith(target + "/", StringComparison.Ordinal);
                Require(allowed, "The parameter build wrote outside its managed output subtree.");
            }
            Require(added.Any(entry => entry.RelativePath == target), "The managed output subtree is missing.");
            Require(added.Any(entry => entry.RelativePath == prefab), "The managed output prefab is missing from the output tree.");
        }

        private static OutputArtifactReceipt CaptureOutputPrefab(
            string prefabPath,
            string expectedName,
            IReadOnlyDictionary<string, HashSet<string>> menuUsage,
            ParameterBehaviorEvidence expectedOutputEvidence,
            ParameterBehaviorProof expectedBehaviorProof,
            ParameterBehaviorEvidence sourceEvidence,
            IReadOnlyCollection<string> compressedNames,
            IReadOnlyCollection<string> excludedNames,
            Action<string> setStage = null)
        {
            setStage?.Invoke("path");
            var temporaryPath = GeneratedRoot + "/" + expectedName + "/" + expectedName + ".prefab";
            var durablePath = OutputKindRoot + "/" + expectedName + "/" + expectedName + ".prefab";
            Require(prefabPath == temporaryPath || prefabPath == durablePath, "The persisted output prefab path is invalid.");
            var absolute = AbsoluteProjectPath(prefabPath);
            var meta = absolute + ".meta";
            setStage?.Invoke("files");
            RequireStableRegularFile(absolute);
            RequireStableRegularFile(meta);
            setStage?.Invoke("main_asset");
            var prefab = AssetDatabase.LoadAssetAtPath<GameObject>(prefabPath);
            Require(prefab != null && EditorUtility.IsPersistent(prefab), "The persisted output prefab is unavailable.");
            setStage?.Invoke("identity");
            Require(AssetDatabase.GetAssetPath(prefab) == prefabPath, "The persisted output prefab resolved to another path.");
            Require(prefab.name == expectedName, "The persisted output prefab root changed name.");
            Require(PrefabUtility.GetPrefabAssetType(prefab) != PrefabAssetType.NotAPrefab, "The persisted output asset is not a prefab.");
            setStage?.Invoke("descriptor");
            var descriptor = prefab.GetComponent<VRCAvatarDescriptor>();
            Require(descriptor != null, "The persisted output prefab has no avatar descriptor.");
            setStage?.Invoke("parameter_state");
            var parameterState = CaptureParameterState(RequireOutputParameters(descriptor), menuUsage);
            setStage?.Invoke("behavior_evidence");
            var evidence = ParameterBitPackingEvidence.Capture(prefab);
            setStage?.Invoke("portable_avatar");
            if (evidence.PortableAvatarDigest != expectedOutputEvidence.PortableAvatarDigest)
            {
                setStage?.Invoke(evidence.PortableObjectDigest != expectedOutputEvidence.PortableObjectDigest
                    ? "portable_avatar_objects"
                    : evidence.PortableComponentDigest != expectedOutputEvidence.PortableComponentDigest
                        ? "portable_avatar_components"
                        : evidence.PortablePropertyDigest != expectedOutputEvidence.PortablePropertyDigest
                            ? evidence.PortableTransformEditorPropertyDigest != expectedOutputEvidence.PortableTransformEditorPropertyDigest
                                ? "portable_avatar_properties_transform_editor"
                                : evidence.PortableTransformRuntimePropertyDigest != expectedOutputEvidence.PortableTransformRuntimePropertyDigest
                                    ? evidence.PortableTransformSpatialPropertyDigest != expectedOutputEvidence.PortableTransformSpatialPropertyDigest
                                        ? "portable_avatar_properties_transform_spatial"
                                        : evidence.PortableTransformHierarchyPropertyDigest != expectedOutputEvidence.PortableTransformHierarchyPropertyDigest
                                            ? "portable_avatar_properties_transform_hierarchy"
                                            : evidence.PortableTransformOtherPropertyDigest != expectedOutputEvidence.PortableTransformOtherPropertyDigest
                                                ? "portable_avatar_properties_transform_other"
                                                : "portable_avatar_properties_transform_unclassified"
                                    : evidence.PortableDescriptorPropertyDigest != expectedOutputEvidence.PortableDescriptorPropertyDigest
                                        ? "portable_avatar_properties_descriptor_"
                                            + FirstMismatchedDescriptorPropertyGroup(
                                                expectedOutputEvidence.PortableDescriptorPropertyGroupDigests,
                                                evidence.PortableDescriptorPropertyGroupDigests)
                                        : evidence.PortableOtherPropertyDigest != expectedOutputEvidence.PortableOtherPropertyDigest
                                            ? "portable_avatar_properties_other"
                                            : "portable_avatar_properties_unclassified"
                            : "portable_avatar_unclassified");
            }
            Require(evidence.PortableAvatarDigest == expectedOutputEvidence.PortableAvatarDigest, "The persisted output portable avatar projection changed.");
            setStage?.Invoke("ordered_parameters");
            Require(evidence.OrderedParameterDigest == expectedOutputEvidence.OrderedParameterDigest, "The persisted output parameter order changed.");
            setStage?.Invoke("menu_graph");
            Require(evidence.MenuGraphDigest == expectedOutputEvidence.MenuGraphDigest, "The persisted output menu graph changed.");
            setStage?.Invoke("animator_behavior");
            Require(evidence.AnimatorBehaviorDigest == expectedOutputEvidence.AnimatorBehaviorDigest, "The persisted output animator behavior changed.");
            setStage?.Invoke("evidence_receipt");
            Require(evidence.ReceiptDigest == expectedOutputEvidence.ReceiptDigest, "The persisted output semantic evidence changed.");
            setStage?.Invoke("behavior_proof");
            var readbackProof = ParameterBitPackingEvidence.VerifyBehavior(
                sourceEvidence,
                evidence,
                compressedNames,
                excludedNames);
            Require(readbackProof.ReceiptDigest == expectedBehaviorProof.ReceiptDigest, "The persisted output behavior proof changed.");
            setStage?.Invoke("guid");
            var guid = AssetDatabase.AssetPathToGUID(prefabPath).ToLowerInvariant();
            Require(IsGuid(guid), "The persisted output prefab GUID is invalid.");
            setStage?.Invoke("global_object_id");
            var globalObjectId = GlobalObjectId.GetGlobalObjectIdSlow(prefab).ToString();
            Require(!string.IsNullOrWhiteSpace(globalObjectId), "The persisted output prefab identity is unavailable.");
            setStage?.Invoke("complete");
            return new OutputArtifactReceipt
            {
                PrefabPath = prefabPath,
                Guid = guid,
                FileDigest = Sha256File(absolute),
                MetaDigest = Sha256File(meta),
                RootGlobalObjectId = globalObjectId,
                PortableAvatarDigest = evidence.PortableAvatarDigest,
                OrderedParameterDigest = evidence.OrderedParameterDigest,
                MenuGraphDigest = evidence.MenuGraphDigest,
                AnimatorBehaviorDigest = evidence.AnimatorBehaviorDigest,
                EvidenceReceiptDigest = evidence.ReceiptDigest,
                BehaviorProofDigest = readbackProof.ReceiptDigest,
                ParameterStateDigest = parameterState.StateDigest
            };
        }

        private static string FirstMismatchedDescriptorPropertyGroup(
            IReadOnlyDictionary<string, string> expected,
            IReadOnlyDictionary<string, string> actual)
        {
            foreach (var group in expected.Keys
                         .Union(actual.Keys, StringComparer.Ordinal)
                         .OrderBy(value => value, StringComparer.Ordinal))
            {
                if (!expected.TryGetValue(group, out var expectedDigest)
                    || !actual.TryGetValue(group, out var actualDigest)
                    || expectedDigest != actualDigest)
                {
                    return group;
                }
            }
            return "unclassified";
        }

        private static AssetTreeManifest CaptureAssetTreeManifest(
            string assetRoot,
            string prefabPath,
            bool requireNoTemporaryReferences)
        {
            var absoluteRoot = AbsoluteProjectPath(assetRoot);
            Require(Directory.Exists(absoluteRoot), "The output asset tree is missing.");
            var firstEnumeration = EnumerateManifestPaths(absoluteRoot);
            var contentRows = new List<string>();
            var handleRows = new List<string>();
            var guidRows = new List<string>();
            var totalBytes = 0L;
            var reparseFree = true;
            var singleLink = true;
            foreach (var item in firstEnumeration)
            {
                var relative = item.Key;
                var path = item.Value;
                var isDirectory = Directory.Exists(path);
                var identity = CaptureIdentity(path, isDirectory);
                reparseFree &= !identity.IsReparsePoint;
                singleLink &= identity.NumberOfLinks == 1;
                Require(!identity.IsReparsePoint && identity.NumberOfLinks == 1, "An output asset tree path is linked or reparsed.");
                if (isDirectory)
                {
                    contentRows.Add(Frame(relative) + Frame("D") + Frame(0) + Frame(string.Empty));
                }
                else
                {
                    var bytes = ReadStableFileBytes(path);
                    var digest = Sha256Bytes(bytes);
                    totalBytes += bytes.LongLength;
                    contentRows.Add(Frame(relative) + Frame("F") + Frame(bytes.LongLength) + Frame(digest));
                }
                handleRows.Add(
                    Frame(relative) + Frame(identity.Digest) + Frame(identity.NumberOfLinks)
                    + Frame(identity.IsReparsePoint));
            }

            foreach (var assetPath in EnumerateManifestAssetPaths(assetRoot, absoluteRoot))
            {
                var metaPath = AbsoluteProjectPath(assetPath) + ".meta";
                Require(File.Exists(metaPath), "An output asset metadata file is missing.");
                var metaGuid = ParseMetaGuid(ReadStableFileBytes(metaPath));
                var databaseGuid = AssetDatabase.AssetPathToGUID(assetPath).ToLowerInvariant();
                Require(IsGuid(metaGuid) && metaGuid == databaseGuid, "An output asset GUID is inconsistent.");
                var localIds = new List<long>();
                foreach (var asset in AssetDatabase.LoadAllAssetsAtPath(assetPath).Where(asset => asset != null))
                {
                    if (AssetDatabase.TryGetGUIDAndLocalFileIdentifier(asset, out string guid, out long localId))
                    {
                        Require(guid.ToLowerInvariant() == databaseGuid, "An output subasset GUID is inconsistent.");
                        localIds.Add(localId);
                    }
                }
                localIds.Sort();
                guidRows.Add(
                    Frame(RelativeAssetPath(assetRoot, assetPath)) + Frame(databaseGuid)
                    + Frame(string.Join(",", localIds.Select(value => value.ToString(CultureInfo.InvariantCulture)))));
            }

            var dependencies = AssetDatabase.GetDependencies(prefabPath, true)
                .Select(path => path.Replace('\\', '/'))
                .Distinct(StringComparer.Ordinal)
                .OrderBy(path => path, StringComparer.Ordinal)
                .ToArray();
            var dependencyGuidRows = new List<string>();
            foreach (var dependency in dependencies)
            {
                if (dependency.StartsWith(GeneratedRoot + "/", StringComparison.Ordinal))
                {
                    Require(
                        !requireNoTemporaryReferences
                            && (dependency == assetRoot || dependency.StartsWith(assetRoot + "/", StringComparison.Ordinal)),
                        "The persisted output retains an unowned temporary dependency.");
                }
                var guid = AssetDatabase.AssetPathToGUID(dependency).ToLowerInvariant();
                Require(IsGuid(guid), "An output dependency GUID is invalid.");
                var localIds = AssetDatabase.LoadAllAssetsAtPath(dependency)
                    .Where(asset => asset != null)
                    .Select(asset => AssetDatabase.TryGetGUIDAndLocalFileIdentifier(asset, out string _, out long localId)
                        ? localId
                        : long.MinValue)
                    .Where(localId => localId != long.MinValue)
                    .OrderBy(localId => localId)
                    .ToArray();
                dependencyGuidRows.Add(
                    Frame(guid) + Frame(string.Join(",", localIds.Select(value => value.ToString(CultureInfo.InvariantCulture)))));
            }
            var secondEnumeration = EnumerateManifestPaths(absoluteRoot);
            Require(
                firstEnumeration.Select(item => item.Key).SequenceEqual(secondEnumeration.Select(item => item.Key), StringComparer.Ordinal),
                "The output asset tree changed during manifest capture.");

            var contentDigest = Sha256Utf8(OutputManifestSchema + ".content\n" + string.Concat(contentRows));
            var handleEvidenceDigest = Sha256Utf8(OutputManifestSchema + ".handles\n" + string.Concat(handleRows));
            var guidMapDigest = Sha256Utf8(OutputManifestSchema + ".guids\n" + string.Concat(guidRows.OrderBy(row => row, StringComparer.Ordinal)));
            var dependencyGuidDigest = Sha256Utf8(OutputManifestSchema + ".dependencies\n" + string.Concat(dependencyGuidRows));
            var referenceClosureDigest = Sha256Utf8(
                OutputManifestSchema + ".references\n"
                + string.Concat(dependencies.Select(dependency => Frame(dependency) + Frame(AssetDatabase.AssetPathToGUID(dependency).ToLowerInvariant()))));
            return new AssetTreeManifest
            {
                RootPath = assetRoot,
                PrefabPath = prefabPath,
                EntryCount = firstEnumeration.Count,
                TotalBytes = totalBytes,
                ContentDigest = contentDigest,
                HandleEvidenceDigest = handleEvidenceDigest,
                GuidMapDigest = guidMapDigest,
                DependencyGuidDigest = dependencyGuidDigest,
                ReferenceClosureDigest = referenceClosureDigest,
                NoTemporaryReferences = requireNoTemporaryReferences,
                ReparseFree = reparseFree,
                SingleLink = singleLink,
                HandleHashed = true,
                FinalEnumerationVerified = true
            };
        }

        private static void VerifyGuidPreservingMove(
            AssetTreeManifest staged,
            AssetTreeManifest final,
            bool requireNoTemporaryReferences = true)
        {
            Require(staged != null && final != null, "The output migration manifest is incomplete.");
            Require(staged.EntryCount == final.EntryCount, "The output migration changed the asset tree count.");
            Require(staged.TotalBytes == final.TotalBytes, "The output migration changed the asset tree size.");
            Require(staged.ContentDigest == final.ContentDigest, "The output migration changed asset bytes.");
            Require(staged.HandleEvidenceDigest == final.HandleEvidenceDigest, "The output migration changed file identities.");
            Require(staged.GuidMapDigest == final.GuidMapDigest, "The output migration changed asset GUIDs or local identifiers.");
            Require(staged.DependencyGuidDigest == final.DependencyGuidDigest, "The output migration changed the dependency GUID closure.");
            Require((!requireNoTemporaryReferences || final.NoTemporaryReferences)
                    && final.ReparseFree && final.SingleLink
                && final.HandleHashed && final.FinalEnumerationVerified,
                "The durable output migration evidence is incomplete.");
        }

        private static List<KeyValuePair<string, string>> EnumerateManifestPaths(string absoluteRoot)
        {
            var result = new List<KeyValuePair<string, string>>
            {
                new KeyValuePair<string, string>("$root", absoluteRoot),
                new KeyValuePair<string, string>("$root.meta", absoluteRoot + ".meta")
            };
            var rootIdentity = CaptureIdentity(absoluteRoot, true);
            Require(
                !rootIdentity.IsReparsePoint && rootIdentity.NumberOfLinks == 1,
                "The output asset root is linked or reparsed."
            );
            RequireStableRegularFile(absoluteRoot + ".meta");

            var pending = new Stack<string>();
            pending.Push(absoluteRoot);
            while (pending.Count > 0)
            {
                var directory = pending.Pop();
                var children = Directory.EnumerateFileSystemEntries(
                        directory,
                        "*",
                        SearchOption.TopDirectoryOnly
                    )
                    .OrderBy(path => path, StringComparer.OrdinalIgnoreCase)
                    .ToArray();
                foreach (var path in children)
                {
                    var isDirectory = Directory.Exists(path);
                    Require(isDirectory || File.Exists(path), "An output asset tree path disappeared during enumeration.");
                    var identity = CaptureIdentity(path, isDirectory);
                    Require(
                        !identity.IsReparsePoint && identity.NumberOfLinks == 1,
                        "An output asset tree path is linked or reparsed."
                    );
                    result.Add(new KeyValuePair<string, string>(RelativePath(absoluteRoot, path), path));
                    if (isDirectory) pending.Push(path);
                }
            }
            return result.OrderBy(item => item.Key, StringComparer.Ordinal).ToList();
        }

        private static IEnumerable<string> EnumerateManifestAssetPaths(string assetRoot, string absoluteRoot)
        {
            yield return assetRoot;
            foreach (var item in EnumerateManifestPaths(absoluteRoot))
            {
                if (item.Key == "$root" || item.Key == "$root.meta"
                    || item.Key.EndsWith(".meta", StringComparison.OrdinalIgnoreCase)) continue;
                yield return assetRoot + "/" + item.Key.Replace('\\', '/');
            }
        }

        private static string RelativeAssetPath(string root, string assetPath)
        {
            if (assetPath == root) return "$root";
            Require(assetPath.StartsWith(root + "/", StringComparison.Ordinal), "An output asset escaped its manifest root.");
            return assetPath.Substring(root.Length + 1);
        }

        private static byte[] ReadStableFileBytes(string path)
        {
            RequireStableRegularFile(path);
            using (var stream = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.Read))
            using (var memory = new MemoryStream())
            {
                stream.CopyTo(memory);
                return memory.ToArray();
            }
        }

        private static string ParseMetaGuid(byte[] bytes)
        {
            foreach (var line in Encoding.UTF8.GetString(bytes).Split(new[] { "\r\n", "\n" }, StringSplitOptions.None))
            {
                if (!line.StartsWith("guid: ", StringComparison.Ordinal)) continue;
                return line.Substring(6).Trim().ToLowerInvariant();
            }
            throw new ParameterBitPackingException("An output asset metadata GUID is missing.");
        }

        private static string Sha256Bytes(byte[] bytes)
        {
            using (var sha = SHA256.Create())
            {
                return string.Concat(sha.ComputeHash(bytes).Select(value => value.ToString("x2")));
            }
        }

        private static string ComputeAddedEntriesDigest(List<TreeEntry> added)
        {
            return Sha256Utf8(
                "vrcforge.parameter_generated_delta.v1\n"
                + string.Concat(added.OrderBy(entry => entry.RelativePath, StringComparer.Ordinal).Select(entry => entry.Receipt))
            );
        }

        private static string ComputeTreeDeltaDigest(GeneratedDelta delta, string schema)
        {
            return Sha256Utf8(
                schema + "\n"
                + Frame(delta.Added.Count)
                + string.Concat(delta.Added.OrderBy(entry => entry.RelativePath, StringComparer.Ordinal).Select(entry => entry.Receipt))
                + Frame(delta.Modified.Count)
                + string.Concat(delta.Modified.OrderBy(entry => entry.RelativePath, StringComparer.Ordinal).Select(entry => entry.Receipt))
                + Frame(delta.Removed.Count)
                + string.Concat(delta.Removed.OrderBy(entry => entry.RelativePath, StringComparer.Ordinal).Select(entry => entry.Receipt))
            );
        }

        private static StableInputLeases HoldStableTree(string assetRoot, TreeSnapshot tree)
        {
            Require(tree != null && tree.Exists, "The stable output tree is unavailable.");
            var paths = new List<KeyValuePair<string, bool>>
            {
                new KeyValuePair<string, bool>(AbsoluteProjectPath(assetRoot), true),
                new KeyValuePair<string, bool>(AbsoluteProjectPath(assetRoot) + ".meta", false)
            };
            paths.AddRange(
                tree.Entries.Values
                    .Where(entry => entry.RelativePath != "$root" && entry.RelativePath != "$root.meta")
                    .Select(entry => new KeyValuePair<string, bool>(
                        AbsoluteProjectPath(assetRoot + "/" + entry.RelativePath),
                        entry.Kind == "D"))
            );
            var leases = new List<IDisposable>();
            try
            {
                foreach (var path in paths.OrderBy(item => item.Key, StringComparer.OrdinalIgnoreCase))
                {
                    if (path.Value)
                    {
                        leases.Add(OpenStableDirectoryLease(path.Key));
                    }
                    else
                    {
                        RequireStableRegularFile(path.Key);
                        leases.Add(new FileStream(path.Key, FileMode.Open, FileAccess.Read, FileShare.Read));
                    }
                }
                return new StableInputLeases(paths.Select(item => item.Key).ToArray(), leases);
            }
            catch
            {
                foreach (var lease in leases) lease.Dispose();
                throw;
            }
        }

        private static StableInputLeases HoldStableInputs(
            SourceSnapshot source,
            CapabilitySnapshot capability,
            TreeSnapshot generatedTree,
            AuxiliaryGeneratedSnapshot auxiliary,
            TreeSnapshot outputTree,
            TreeSnapshot protectedTree,
            RootIdentitySnapshot roots)
        {
            Require(auxiliary != null && auxiliary.Tree != null,
                "The auxiliary generated baseline is unavailable.");
            var pathKinds = new Dictionary<string, bool>(StringComparer.OrdinalIgnoreCase);
            void Add(string path, bool isDirectory)
            {
                if (pathKinds.TryGetValue(path, out var existing))
                {
                    Require(existing == isDirectory, "A stable lease path changed kind.");
                    return;
                }
                pathKinds.Add(path, isDirectory);
            }
            foreach (var path in RequiredRootPaths()) Add(path.Value, true);
            if (generatedTree.Exists) Add(AbsoluteProjectPath(GeneratedRoot), true);
            foreach (var entry in protectedTree.Entries.Values)
            {
                Add(AbsoluteProjectPath(entry.RelativePath), entry.Kind == "D");
            }
            if (outputTree.Exists)
            {
                Add(AbsoluteProjectPath(OutputRoot), true);
                Add(AbsoluteProjectPath(OutputRoot) + ".meta", false);
                foreach (var entry in outputTree.Entries.Values)
                {
                    if (entry.RelativePath == "$root" || entry.RelativePath == "$root.meta") continue;
                    Add(AbsoluteProjectPath(OutputRoot + "/" + entry.RelativePath), entry.Kind == "D");
                }
            }
            Require(
                capability.CallbackAssemblyPaths != null
                    && capability.CallbackAssemblyPaths.Count == capability.CallbackAssemblySetCount,
                "The callback assembly lease set is incomplete."
            );
            foreach (var path in source.SourceAssetFilePaths
                .Concat(new[] { source.SceneFilePath, source.SceneMetaPath })
                .Concat(capability.CallbackAssemblyPaths)) Add(path, false);
            var paths = pathKinds.OrderBy(item => item.Key, StringComparer.OrdinalIgnoreCase).ToArray();
            var leases = new List<IDisposable>();
            try
            {
                foreach (var path in paths)
                {
                    if (path.Value)
                    {
                        leases.Add(OpenStableDirectoryLease(path.Key));
                    }
                    else
                    {
                        RequireStableRegularFile(path.Key);
                        leases.Add(new FileStream(path.Key, FileMode.Open, FileAccess.Read, FileShare.Read));
                    }
                }
                var stablePaths = paths.Select(item => item.Key).ToArray();
                Require(roots.EntryCount == RequiredRootPaths().Count + 1, "The stable root lease set is incomplete.");
                return new StableInputLeases(stablePaths, leases);
            }
            catch
            {
                foreach (var lease in leases) lease.Dispose();
                throw;
            }
        }

        private static void VerifyStableInputs(
            SourceSnapshot source,
            CapabilitySnapshot capability,
            TreeSnapshot generatedTree,
            AuxiliaryGeneratedSnapshot auxiliary,
            TreeSnapshot outputTree,
            TreeSnapshot protectedTree,
            RootIdentitySnapshot roots,
            StableInputLeases leases,
            bool verifyOutputTree = true,
            bool verifyAuxiliaryIdentity = true,
            bool verifyAuxiliaryTree = true)
        {
            Require(leases.Paths.Count > PackageFileCount, "The stable input lease set is incomplete.");
            Require(Sha256File(source.SceneFilePath) == source.SceneFileDigest && Sha256File(source.SceneMetaPath) == source.SceneMetaDigest, "The source scene changed while leases were acquired.");
            var refreshedCapability = CaptureCapability();
            Require(refreshedCapability.CapabilityDigest == capability.CapabilityDigest, "The package capability changed while leases were acquired.");
            var refreshedRoots = CaptureRootIdentities(generatedTree);
            Require(refreshedRoots.Digest == roots.Digest && refreshedRoots.EntryCount == roots.EntryCount, "A project root identity changed while leases were acquired.");
            if (verifyAuxiliaryTree)
            {
                var refreshedAuxiliary = CaptureAuxiliaryGenerated();
                Require(
                    verifyAuxiliaryIdentity
                        ? refreshedAuxiliary.ReceiptDigest == auxiliary.ReceiptDigest
                        : AuxiliaryContentEquals(auxiliary, refreshedAuxiliary),
                    "The auxiliary generated baseline changed while leases were acquired.");
            }
            if (verifyOutputTree)
            {
                var refreshedOutput = CaptureManagedTree(OutputRoot, OutputTreeSchema);
                Require(refreshedOutput.Exists == outputTree.Exists && refreshedOutput.Digest == outputTree.Digest && refreshedOutput.EntryCount == outputTree.EntryCount, "The managed output tree changed while leases were acquired.");
            }
            var refreshedProtected = CaptureProtectedTree();
            Require(refreshedProtected.Digest == protectedTree.Digest && refreshedProtected.EntryCount == protectedTree.EntryCount, "The protected project tree changed while leases were acquired.");
        }

        private static SafeFileHandle OpenStableDirectoryLease(string path)
        {
            Require(Directory.Exists(path), "A stable directory lease path is missing.");
            var identity = CaptureIdentity(path, true);
            Require(!identity.IsReparsePoint && identity.NumberOfLinks == 1, "A stable directory lease path is linked or reparsed.");
            var handle = CreateFile(
                path,
                0,
                NativeFileShareRead | NativeFileShareWrite,
                IntPtr.Zero,
                NativeOpenExisting,
                NativeFileFlagBackupSemantics | NativeFileFlagOpenReparsePoint,
                IntPtr.Zero
            );
            Require(handle != null && !handle.IsInvalid, "A stable directory lease could not be acquired.");
            return handle;
        }

        private static bool TryCleanupFailure(
            Scene outputScene,
            TreeSnapshot beforeGenerated,
            AuxiliaryGeneratedSnapshot beforeAuxiliary,
            TreeSnapshot beforeOutput,
            TreeSnapshot beforeProtected,
            RootIdentitySnapshot beforeRoots,
            SourceSnapshot beforeSource,
            string cloneName,
            CacheTransaction cacheTransaction,
            AuxiliaryGeneratedTransaction auxiliaryTransaction,
            AssetTreeManifest stagedOutputManifest,
            AssetTreeManifest outputManifest,
            IReadOnlyCollection<CreatedAssetFolder> createdOutputFolders)
        {
            var restored = true;
            void RunCleanupStep(Action action)
            {
                try { action(); }
                catch { restored = false; }
            }

            RunCleanupStep(() =>
            {
                if (outputScene.IsValid() && outputScene.isLoaded)
                {
                    foreach (var root in outputScene.GetRootGameObjects()) Object.DestroyImmediate(root);
                    Require(EditorSceneManagerClose(outputScene), "Failed to close the temporary output scene.");
                }
            });

            RunCleanupStep(() =>
            {
                RestoreManagedOutputAfterFailure(
                    beforeOutput,
                    cloneName,
                    stagedOutputManifest,
                    outputManifest,
                    createdOutputFolders,
                    cacheTransaction != null && cacheTransaction.Prepared
                        && !cacheTransaction.Restored && !cacheTransaction.Completed,
                    auxiliaryTransaction != null && auxiliaryTransaction.Prepared
                        && !auxiliaryTransaction.Restored && !auxiliaryTransaction.Completed);
            });

            RunCleanupStep(() =>
            {
                if (auxiliaryTransaction != null && auxiliaryTransaction.Completed)
                {
                    Require(auxiliaryTransaction.VerifyClosedTerminal(),
                        "The completed auxiliary transaction has an invalid terminal state.");
                }
                else if (auxiliaryTransaction != null && !auxiliaryTransaction.Prepared)
                {
                    Require(auxiliaryTransaction.Completed || auxiliaryTransaction.AbortPreparation(),
                        "The incomplete auxiliary transaction could not be removed.");
                }
                else if (beforeAuxiliary != null && auxiliaryTransaction != null
                    && auxiliaryTransaction.Restored)
                {
                    Require(auxiliaryTransaction.VerifyRestoredBaseline(),
                        "The restored auxiliary transaction differs from its baseline.");
                }
                else if (beforeAuxiliary != null && auxiliaryTransaction != null)
                {
                    Require(auxiliaryTransaction.Restore(
                            allowGeneratedRootDirty: cacheTransaction != null
                                && cacheTransaction.Prepared && !cacheTransaction.Restored
                                && !cacheTransaction.Completed),
                        "The auxiliary generated root could not be restored.");
                }
                else if (beforeAuxiliary != null)
                {
                    Require(AuxiliaryContentEquals(beforeAuxiliary, CaptureAuxiliaryGenerated()),
                        "The auxiliary generated root changed without an owning transaction.");
                }
            });

            RunCleanupStep(() =>
            {
                if (cacheTransaction != null && cacheTransaction.Completed)
                {
                    Require(cacheTransaction.VerifyClosedTerminal(),
                        "The completed cache transaction has an invalid terminal state.");
                }
                else if (cacheTransaction != null && !cacheTransaction.Prepared)
                {
                    Require(cacheTransaction.Completed || cacheTransaction.AbortPreparation(),
                        "The incomplete cache transaction could not be removed.");
                }
                else if (beforeGenerated != null && cacheTransaction != null
                    && cacheTransaction.Restored)
                {
                    Require(cacheTransaction.VerifyRestoredBaseline(),
                        "The restored generated cache differs from its baseline.");
                }
                else if (beforeGenerated != null && cacheTransaction != null)
                {
                    Require(cacheTransaction.Restore(
                            allowAuxiliaryRootDirty: auxiliaryTransaction != null
                                && auxiliaryTransaction.Prepared && !auxiliaryTransaction.Restored
                                && !auxiliaryTransaction.Completed),
                        "The generated cache could not be restored.");
                }
                else if (beforeGenerated != null)
                {
                    if (!beforeGenerated.Exists)
                    {
                        Require(!CaptureTree(GeneratedRoot, GeneratedTreeSchema, requireExists: false).Exists,
                            "The absent generated cache changed without an owning transaction.");
                    }
                    else
                    {
                        var current = CaptureTree(GeneratedRoot, GeneratedTreeSchema, requireExists: true);
                        var delta = CompareGeneratedTrees(beforeGenerated, current);
                        Require(delta.Modified.Count == 0 && delta.Removed.Count == 0,
                            "The generated cache changed without an owning transaction.");
                        foreach (var first in delta.Added.Select(entry => entry.RelativePath.Split('/')[0].Replace(".meta", string.Empty)).Distinct(StringComparer.Ordinal))
                        {
                            Require(!string.IsNullOrWhiteSpace(first),
                                "The generated cache delta is not safely removable.");
                            AssetDatabase.DeleteAsset(GeneratedRoot + "/" + first);
                        }
                        RequireNoDirtyProjectAssets(GeneratedRoot);
                        AssetDatabase.SaveAssets();
                        AssetDatabase.Refresh(ImportAssetOptions.ForceSynchronousImport);
                        var restoredGenerated = CaptureTree(GeneratedRoot, GeneratedTreeSchema, requireExists: true);
                        Require(restoredGenerated.Digest == beforeGenerated.Digest
                                && restoredGenerated.EntryCount == beforeGenerated.EntryCount,
                            "The generated cache did not return to its baseline.");
                    }
                }
            });

            RunCleanupStep(() =>
            {
                RequireNoDirtyProjectAssets();
                if (beforeAuxiliary != null)
                {
                    Require(AuxiliaryContentEquals(beforeAuxiliary, CaptureAuxiliaryGenerated()),
                        "The auxiliary generated root differs after cleanup.");
                }
                if (beforeProtected != null)
                {
                    var protectedAfter = CaptureProtectedTree();
                    Require(protectedAfter.Digest == beforeProtected.Digest
                            && protectedAfter.EntryCount == beforeProtected.EntryCount,
                        "The protected project tree differs after cleanup.");
                }
                if (beforeRoots != null)
                {
                    var rootsAfter = CaptureRootIdentities(beforeGenerated);
                    Require(rootsAfter.Digest == beforeRoots.Digest
                            && rootsAfter.EntryCount == beforeRoots.EntryCount,
                        "A project root identity differs after cleanup.");
                }
                if (beforeSource != null)
                {
                    var sourceAfter = CaptureSource(beforeSource.ScenePath, beforeSource.ObjectPath);
                    Require(sourceAfter.SourceStateDigest == beforeSource.SourceStateDigest
                            && sourceAfter.SourceAssetSetDigest == beforeSource.SourceAssetSetDigest,
                        "The source avatar differs after cleanup.");
                }
            });

            if (restored)
            {
                RunCleanupStep(() =>
                {
                    if (auxiliaryTransaction != null && !auxiliaryTransaction.Completed)
                    {
                        auxiliaryTransaction.Complete();
                    }
                    if (auxiliaryTransaction != null)
                    {
                        Require(auxiliaryTransaction.VerifyClosedTerminal(),
                            "The auxiliary transaction did not close cleanly.");
                    }
                });
                RunCleanupStep(() =>
                {
                    if (cacheTransaction != null && !cacheTransaction.Completed)
                    {
                        cacheTransaction.Complete();
                    }
                    if (cacheTransaction != null)
                    {
                        Require(cacheTransaction.VerifyClosedTerminal(),
                            "The cache transaction did not close cleanly.");
                    }
                });
            }
            return restored;
        }

        private static void RestoreManagedOutputAfterFailure(
            TreeSnapshot beforeOutput,
            string cloneName,
            AssetTreeManifest stagedOutputManifest,
            AssetTreeManifest outputManifest,
            IReadOnlyCollection<CreatedAssetFolder> createdOutputFolders,
            bool allowGeneratedRootDirty,
            bool allowAuxiliaryRootDirty)
        {
            Require(beforeOutput != null && !string.IsNullOrWhiteSpace(cloneName),
                "The managed output cleanup baseline is unavailable.");
            Require(createdOutputFolders != null
                    && createdOutputFolders.All(folder => folder != null)
                    && createdOutputFolders.Select(folder => folder.AssetPath)
                        .Distinct(StringComparer.Ordinal).Count() == createdOutputFolders.Count,
                "The managed output folder ownership ledger is invalid.");
            var durableTarget = OutputKindRoot + "/" + cloneName;
            var outputMutated = false;
            if (AssetDatabase.IsValidFolder(durableTarget))
            {
                if (outputManifest != null)
                {
                    var currentManifest = CaptureAssetTreeManifest(
                        durableTarget,
                        durableTarget + "/" + cloneName + ".prefab",
                        requireNoTemporaryReferences: true);
                    Require(currentManifest.ReceiptDigest == outputManifest.ReceiptDigest,
                        "The owned durable output changed before failure cleanup.");
                }
                else
                {
                    Require(stagedOutputManifest != null,
                        "An unverified durable output target requires checkpoint restore.");
                    var movedFailureManifest = CaptureAssetTreeManifest(
                        durableTarget,
                        durableTarget + "/" + cloneName + ".prefab",
                        requireNoTemporaryReferences: false);
                    VerifyGuidPreservingMove(
                        stagedOutputManifest,
                        movedFailureManifest,
                        requireNoTemporaryReferences: false);
                }
                Require(AssetDatabase.DeleteAsset(durableTarget),
                    "The verified durable output target could not be removed.");
                outputMutated = true;
            }
            else
            {
                Require(outputManifest == null,
                    "The verified durable output target disappeared before failure cleanup.");
            }

            foreach (var folder in createdOutputFolders.Reverse())
            {
                Require(folder.AssetPath == OutputKindRoot || folder.AssetPath == OutputRoot,
                    "The managed output folder ownership path is invalid.");
                Require(AssetDatabase.IsValidFolder(folder.AssetPath),
                    "An operation-created managed output folder disappeared before cleanup.");
                VerifyCreatedAssetFolder(folder);
                var absolute = AbsoluteProjectPath(folder.AssetPath);
                Require(!Directory.EnumerateFileSystemEntries(
                        absolute,
                        "*",
                        SearchOption.TopDirectoryOnly).Any(),
                    "An operation-created managed output folder is not empty.");
                Require(AssetDatabase.DeleteAsset(folder.AssetPath),
                    "An operation-created managed output folder could not be removed.");
                Require(!Directory.Exists(absolute)
                        && !File.Exists(absolute)
                        && !File.Exists(absolute + ".meta"),
                    "An operation-created managed output folder remains after cleanup.");
                outputMutated = true;
            }

            if (outputMutated)
            {
                var allowedDirtyRoots = new List<string>();
                if (allowGeneratedRootDirty) allowedDirtyRoots.Add(GeneratedRoot);
                if (allowAuxiliaryRootDirty) allowedDirtyRoots.Add(AuxiliaryGeneratedRoot);
                RequireNoDirtyProjectAssets(allowedDirtyRoots.ToArray());
                AssetDatabase.SaveAssets();
                AssetDatabase.Refresh(ImportAssetOptions.ForceSynchronousImport);
            }
            var restoredOutput = CaptureManagedTree(OutputRoot, OutputTreeSchema);
            Require(restoredOutput.Exists == beforeOutput.Exists
                    && restoredOutput.Digest == beforeOutput.Digest
                    && restoredOutput.ContentDigest == beforeOutput.ContentDigest
                    && restoredOutput.EntryCount == beforeOutput.EntryCount
                    && restoredOutput.TotalBytes == beforeOutput.TotalBytes,
                "The managed output root did not return to its baseline.");
        }

        private static bool EditorSceneManagerClose(Scene scene)
        {
            return UnityEditor.SceneManagement.EditorSceneManager.CloseScene(scene, true);
        }

        private static void RequireNoDirtyProjectAssets(params string[] allowedDirtyRoots)
        {
            RequireNoDirtyProjectAssets(default(Scene), allowedDirtyRoots);
        }

        private static void RequireNoDirtyProjectAssets(Scene allowedTransientScene, params string[] allowedDirtyRoots)
        {
            var allowedRoots = (allowedDirtyRoots ?? Array.Empty<string>())
                .Select(root => (root ?? string.Empty).Replace('\\', '/').TrimEnd('/'))
                .ToArray();
            Require(
                allowedRoots.Distinct(StringComparer.Ordinal).Count() == allowedRoots.Length
                    && allowedRoots.All(root =>
                        root == GeneratedRoot
                        || root.StartsWith(GeneratedRoot + "/", StringComparison.Ordinal)
                        || root == OutputRoot
                        || root.StartsWith(OutputRoot + "/", StringComparison.Ordinal)
                        || root == AuxiliaryGeneratedRoot),
                "The dirty-asset save scope is invalid."
            );
            Require(
                SceneManager.sceneCount <= OpenProjectSceneScanLimit,
                "The open project scene set exceeds the bounded cleanliness scan."
            );
            var hasAllowedTransientScene = allowedTransientScene.IsValid();
            var allowedTransientSceneMatches = 0;
            for (var index = 0; index < SceneManager.sceneCount; index++)
            {
                var scene = SceneManager.GetSceneAt(index);
                Require(scene.IsValid() && scene.isLoaded, "An open project scene is incomplete.");
                if (hasAllowedTransientScene && scene.handle == allowedTransientScene.handle)
                {
                    Require(
                        string.IsNullOrWhiteSpace(scene.path) && !scene.isSubScene,
                        "The allowed transient scene acquired persistent or subscene state."
                    );
                    allowedTransientSceneMatches++;
                    continue;
                }
                Require(!string.IsNullOrWhiteSpace(scene.path), "An open project scene has no persistent project asset.");
                var scenePath = scene.path.Replace('\\', '/');
                var pathPartsAreCanonical = !scenePath.Split('/')
                    .Any(part => string.IsNullOrWhiteSpace(part) || part == "." || part == "..");
                var isProjectScene = !scene.isSubScene
                    && scenePath.StartsWith("Assets/", StringComparison.Ordinal);
                var isReadOnlyPackageSubScene = scene.isSubScene
                    && scenePath.StartsWith("Packages/", StringComparison.Ordinal);
                Require(
                    scenePath == scene.path
                        && scenePath.EndsWith(".unity", StringComparison.Ordinal)
                        && pathPartsAreCanonical
                        && (isProjectScene || isReadOnlyPackageSubScene),
                    "An open project scene has an invalid persistent path."
                );
                var sceneAsset = AssetDatabase.LoadAssetAtPath<SceneAsset>(scenePath);
                Require(
                    sceneAsset != null
                        && EditorUtility.IsPersistent(sceneAsset)
                        && AssetDatabase.GetAssetPath(sceneAsset) == scenePath,
                    "An open project scene has incomplete persistent state."
                );
                Require(!scene.isDirty, "All open project scenes must be saved before parameter bit-packing.");
            }
            Require(
                !hasAllowedTransientScene || allowedTransientSceneMatches == 1,
                "The allowed transient scene is not uniquely loaded."
            );

            var registeredPaths = AssetDatabase.GetAllAssetPaths();
            Require(
                registeredPaths != null && registeredPaths.Length <= RegisteredAssetPathScanLimit,
                "The registered asset set exceeds the bounded cleanliness scan."
            );
            var projectPaths = registeredPaths
                .Where(path => !string.IsNullOrWhiteSpace(path) && IsProjectOwnedAssetPath(path))
                .OrderBy(path => path, StringComparer.Ordinal)
                .ToArray();
            Require(
                projectPaths.Length <= RegisteredAssetPathScanLimit
                    && projectPaths.Distinct(StringComparer.Ordinal).Count() == projectPaths.Length,
                "The registered project asset set is incomplete."
            );
            var projectPathSet = new HashSet<string>(projectPaths, StringComparer.Ordinal);

            var objectCount = 0;
            foreach (var path in projectPaths)
            {
                Require(
                    path == path.Replace('\\', '/')
                        && !path.Split('/').Any(part => string.IsNullOrWhiteSpace(part) || part == "." || part == ".."),
                    "A registered project asset path is invalid."
                );
                var absolute = AbsoluteProjectPath(path);
                var isDirectory = Directory.Exists(absolute);
                Require(isDirectory || File.Exists(absolute), "A registered project asset is missing from disk.");

                var importer = AssetImporter.GetAtPath(path);
                Require(
                    importer != null
                        && !string.IsNullOrWhiteSpace(importer.assetPath)
                        && importer.assetPath.Replace('\\', '/') == path,
                    "A registered project asset importer is incomplete."
                );
                objectCount++;
                Require(objectCount <= RegisteredAssetObjectScanLimit, "The registered asset object set exceeds the bounded cleanliness scan.");
                Require(
                    !EditorUtility.IsDirty(importer) || IsAllowedDirtyAssetPath(path, allowedRoots),
                    "An unrelated project asset importer is dirty: " + path
                );
            }

            var loadedObjects = Resources.FindObjectsOfTypeAll<Object>();
            Require(
                loadedObjects != null && loadedObjects.Length <= RegisteredAssetObjectScanLimit,
                "The loaded asset object set exceeds the bounded cleanliness scan."
            );
            foreach (var asset in loadedObjects)
            {
                if (asset == null) continue;
                var path = AssetDatabase.GetAssetPath(asset);
                if (string.IsNullOrWhiteSpace(path)) continue;
                path = path.Replace('\\', '/');
                if (!IsProjectOwnedAssetPath(path)) continue;
                Require(
                    projectPathSet.Contains(path)
                        && AssetDatabase.Contains(asset)
                        && EditorUtility.IsPersistent(asset),
                    "A loaded project asset has incomplete persistent registration."
                );
                if (!AssetDatabase.IsNativeAsset(asset)) continue;
                Require(
                    !EditorUtility.IsDirty(asset) || IsAllowedDirtyAssetPath(path, allowedRoots),
                    "An unrelated project asset is dirty: " + path
                );
            }
        }

        private static bool IsAllowedDirtyAssetPath(string path, IReadOnlyCollection<string> allowedRoots)
        {
            return allowedRoots.Any(root =>
                path == root || path.StartsWith(root + "/", StringComparison.Ordinal));
        }

        private static bool IsProjectOwnedAssetPath(string path)
        {
            if (path.StartsWith("Assets/", StringComparison.Ordinal)) return true;
            if (!path.StartsWith("Packages/", StringComparison.Ordinal)) return false;
            var packageInfo = UnityEditor.PackageManager.PackageInfo.FindForAssetPath(path);
            if (packageInfo == null || string.IsNullOrWhiteSpace(packageInfo.resolvedPath)) return false;
            var projectPackages = Path.GetFullPath(Path.Combine(CurrentProjectPath(), "Packages"));
            var resolved = Path.GetFullPath(packageInfo.resolvedPath).TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar);
            return resolved.StartsWith(projectPackages + Path.DirectorySeparatorChar, StringComparison.OrdinalIgnoreCase);
        }

        private static bool IsAssetDirty(Object asset)
        {
            if (asset == null) return false;
            var path = AssetDatabase.GetAssetPath(asset);
            if (string.IsNullOrWhiteSpace(path)) return true;
            return AssetDatabase.LoadAllAssetsAtPath(path).Any(EditorUtility.IsDirty);
        }

        private static string ComputePreviewDigest(
            SourceSnapshot source,
            CapabilitySnapshot capability,
            TreeSnapshot generated,
            AuxiliaryGeneratedSnapshot auxiliary,
            TreeSnapshot outputTree,
            TreeSnapshot protectedTree,
            RootIdentitySnapshot roots,
            OutputPreview output,
            PreferenceSnapshot preferences)
        {
            return Sha256Utf8(
                string.Concat(
                    Frame(ResultSchema),
                    Frame(true),
                    Frame(true),
                    Frame(true),
                    Frame(false),
                    Frame(false),
                    Frame(false),
                    Frame(false),
                    Frame(0),
                    Frame(CurrentProjectPath()),
                    Frame(source.ScenePath),
                    Frame(source.SceneGuid),
                    Frame(source.SceneFileDigest),
                    Frame(source.SceneMetaDigest),
                    Frame(source.ObjectPath),
                    Frame(source.GlobalObjectId),
                    Frame(source.HierarchyDigest),
                    Frame(source.SourceStateDigest),
                    Frame(source.SourceAssetSetDigest),
                    Frame(source.SourceAssetCount),
                    Frame(source.ParameterState.StateDigest),
                    Frame(source.ControllerStateDigest),
                    Frame(source.MenuStateDigest),
                    Frame(source.BehaviorEvidence.ReceiptDigest),
                    Frame(source.ParameterState.CostBits),
                    Frame(source.ParameterState.Parameters.Count),
                    Frame(source.ParameterState.SafeCandidateDigest),
                    Frame(source.ParameterState.ExcludedDigest),
                    Frame(capability.CapabilityDigest),
                    Frame(GeneratedRoot),
                    Frame(generated.Digest),
                    Frame(generated.ContentDigest),
                    Frame(generated.EntryCount),
                    Frame(generated.TotalBytes),
                    Frame(CacheBackupMaxEntries),
                    Frame(CacheBackupMaxBytes),
                    Frame(CacheJournalSchema),
                    Frame(protectedTree.Digest),
                    Frame(protectedTree.EntryCount),
                    Frame(roots.Digest),
                    Frame(roots.EntryCount),
                    Frame(AuxiliaryGeneratedRoot),
                    Frame(AuxiliaryPackageRoot),
                    Frame(auxiliary.PackageRootIdentityDigest),
                    Frame(auxiliary.PackageManifestDigest),
                    Frame(auxiliary.PackageManifestIdentityDigest),
                    Frame(auxiliary.Tree.Exists),
                    Frame(auxiliary.Tree.Digest),
                    Frame(auxiliary.Tree.ContentDigest),
                    Frame(auxiliary.Tree.EntryCount),
                    Frame(auxiliary.Tree.TotalBytes),
                    Frame(CacheBackupMaxEntries),
                    Frame(CacheBackupMaxBytes),
                    Frame(AuxiliaryJournalSchema),
                    Frame(true),
                    Frame(preferences.ReceiptDigest),
                    Frame(EditorUserBuildSettings.activeBuildTarget.ToString()),
                    Frame("current-target-only"),
                    Frame(false),
                    Frame(false),
                    Frame(output.CloneName),
                    Frame(output.SceneName),
                    Frame(output.TemporaryPrefabPath),
                    Frame(output.PrefabPath),
                    Frame(OutputRoot),
                    Frame(OutputKindRoot),
                    Frame(outputTree.Digest),
                    Frame(outputTree.EntryCount),
                    Frame(outputTree.Exists),
                    Frame(false),
                    Frame(false),
                    Frame(false),
                    Frame(false)
                )
            );
        }

        private static string ComputeApplyReceiptDigest(
            string projectPath,
            string previewDigest,
            string capabilityDigest,
            int costBeforeBits,
            int costAfterBits,
            List<string> compressedNames,
            List<string> safeNames,
            List<ExcludedParameter> excluded,
            SourceSnapshot sourceBefore,
            SourceSnapshot sourceAfter,
            string cloneName,
            string sceneName,
            string clonePortableAvatarDigest,
            string cloneEvidenceDigest,
            string cloneParameterStateDigest,
            OutputArtifactReceipt output,
            ParameterBehaviorProof behaviorProof,
            AssetTreeManifest stagedManifest,
            AssetTreeManifest manifest,
            PreferenceSnapshot preferences,
            CacheTransaction cacheTransaction,
            AuxiliaryGeneratedTransaction auxiliaryTransaction,
            bool sceneLoadedAfter,
            bool temporaryObjectResidue,
            TreeSnapshot generatedBefore,
            TreeSnapshot generatedAfter,
            GeneratedDelta generatedDelta,
            string temporaryDeltaDigest,
            AuxiliaryGeneratedSnapshot auxiliaryBefore,
            AuxiliaryGeneratedSnapshot auxiliaryAfter,
            TreeSnapshot outputBefore,
            TreeSnapshot outputAfter,
            GeneratedDelta outputDelta,
            string outputAddedEntriesDigest,
            RootIdentitySnapshot rootsBefore,
            RootIdentitySnapshot rootsAfter,
            TreeSnapshot protectedBefore,
            TreeSnapshot protectedAfter,
            bool cleanupVerified,
            bool restored,
            bool cleanupRequired,
            bool checkpointRestoreRequired,
            string operationState)
        {
            var compressedDigest = Sha256Utf8(CompressedNamesSchema + "\n" + string.Concat(compressedNames.Select(Frame)));
            var safeDigest = Sha256Utf8(SafeNamesSchema + "\n" + string.Concat(safeNames.Select(Frame)));
            var excludedDigest = ComputeExcludedDigest(excluded);
            return Sha256Framed(
                ApplyReceiptSchema,
                projectPath,
                previewDigest,
                capabilityDigest,
                costBeforeBits,
                costAfterBits,
                compressedDigest,
                compressedNames.Count,
                safeDigest,
                safeNames.Count,
                excludedDigest,
                excluded.Count,
                sourceBefore.ScenePath,
                sourceBefore.SceneGuid,
                sourceBefore.SceneFileDigest,
                sourceBefore.SceneMetaDigest,
                sourceBefore.ObjectPath,
                sourceBefore.GlobalObjectId,
                sourceBefore.HierarchyDigest,
                sourceBefore.SourceStateDigest,
                sourceAfter.SourceStateDigest,
                sourceBefore.SourceAssetSetDigest,
                sourceAfter.SourceAssetSetDigest,
                sourceBefore.SourceAssetCount,
                sourceBefore.ParameterState.StateDigest,
                sourceBefore.ParameterState.Parameters.Count,
                sourceBefore.ControllerStateDigest,
                sourceBefore.MenuStateDigest,
                sourceBefore.BehaviorEvidence.ReceiptDigest,
                true,
                false,
                cloneName,
                sceneName,
                string.Empty,
                false,
                clonePortableAvatarDigest,
                cloneEvidenceDigest,
                cloneParameterStateDigest,
                output.PrefabPath,
                output.Guid,
                output.FileDigest,
                output.MetaDigest,
                output.RootGlobalObjectId,
                output.PortableAvatarDigest,
                output.OrderedParameterDigest,
                output.MenuGraphDigest,
                output.AnimatorBehaviorDigest,
                output.EvidenceReceiptDigest,
                output.BehaviorProofDigest,
                output.ParameterStateDigest,
                true,
                true,
                sceneLoadedAfter,
                temporaryObjectResidue,
                preferences.ReceiptDigest,
                EditorUserBuildSettings.activeBuildTarget.ToString(),
                "current-target-only",
                false,
                false,
                behaviorProof.ReceiptDigest,
                GeneratedRoot,
                StagingRoot,
                true,
                generatedBefore.Digest,
                generatedBefore.ContentDigest,
                generatedBefore.EntryCount,
                generatedBefore.TotalBytes,
                generatedAfter.Digest,
                generatedAfter.ContentDigest,
                generatedAfter.EntryCount,
                generatedAfter.TotalBytes,
                generatedDelta.Added.Count,
                generatedDelta.Modified.Count,
                generatedDelta.Removed.Count,
                false,
                temporaryDeltaDigest,
                true,
                true,
                CacheBackupMaxEntries,
                CacheBackupMaxBytes,
                CacheJournalSchema,
                cacheTransaction.JournalId,
                true,
                AuxiliaryGeneratedRoot,
                AuxiliaryPackageRoot,
                auxiliaryBefore.PackageRootIdentityDigest,
                auxiliaryAfter.PackageRootIdentityDigest,
                auxiliaryBefore.PackageManifestDigest,
                auxiliaryAfter.PackageManifestDigest,
                auxiliaryBefore.PackageManifestIdentityDigest,
                auxiliaryAfter.PackageManifestIdentityDigest,
                auxiliaryBefore.Tree.Exists,
                auxiliaryAfter.Tree.Exists,
                auxiliaryBefore.Tree.Digest,
                auxiliaryAfter.Tree.Digest,
                auxiliaryBefore.Tree.ContentDigest,
                auxiliaryAfter.Tree.ContentDigest,
                auxiliaryBefore.Tree.EntryCount,
                auxiliaryAfter.Tree.EntryCount,
                auxiliaryBefore.Tree.TotalBytes,
                auxiliaryAfter.Tree.TotalBytes,
                auxiliaryTransaction.Observed.Tree.Exists,
                auxiliaryTransaction.Observed.Tree.Digest,
                auxiliaryTransaction.Observed.Tree.ContentDigest,
                auxiliaryTransaction.Observed.Tree.EntryCount,
                auxiliaryTransaction.Observed.Tree.TotalBytes,
                auxiliaryTransaction.OwnedRootIdentityDigest,
                auxiliaryTransaction.CreatedByOperation,
                auxiliaryTransaction.RestorationMode,
                true,
                true,
                CacheBackupMaxEntries,
                CacheBackupMaxBytes,
                AuxiliaryJournalSchema,
                auxiliaryTransaction.JournalId,
                true,
                OutputRoot,
                OutputKindRoot,
                OutputKindRoot + "/" + cloneName,
                outputBefore.Exists,
                outputAfter.Exists,
                outputBefore.Digest,
                outputBefore.EntryCount,
                outputAfter.Digest,
                outputAfter.EntryCount,
                outputDelta.Added.Count,
                1,
                outputDelta.Modified.Count,
                outputDelta.Removed.Count,
                outputAddedEntriesDigest,
                true,
                true,
                true,
                true,
                true,
                stagedManifest.ReceiptDigest,
                manifest.ReceiptDigest,
                OutputManifestSchema,
                manifest.ReceiptDigest,
                manifest.EntryCount,
                manifest.TotalBytes,
                manifest.GuidMapDigest,
                manifest.DependencyGuidDigest,
                manifest.ReferenceClosureDigest,
                manifest.NoTemporaryReferences,
                manifest.ReparseFree,
                manifest.SingleLink,
                manifest.HandleHashed,
                manifest.FinalEnumerationVerified,
                rootsBefore.Digest,
                rootsAfter.Digest,
                rootsBefore.EntryCount,
                rootsAfter.EntryCount,
                protectedBefore.Digest,
                protectedAfter.Digest,
                protectedBefore.EntryCount,
                protectedAfter.EntryCount,
                cleanupVerified,
                sceneLoadedAfter,
                temporaryObjectResidue,
                restored,
                cleanupRequired,
                checkpointRestoreRequired,
                operationState
            );
        }

        private static string ComputeExcludedDigest(List<ExcludedParameter> excluded)
        {
            return Sha256Utf8(
                ExcludedSchema + "\n"
                + string.Concat(excluded.Select(item =>
                    Frame(item.Name)
                    + Frame(item.Type)
                    + Frame(item.NetworkSynced)
                    + Frame(string.Join(",", item.Reasons))
                    + Frame(item.StateDigest)))
            );
        }

        private static bool IsPackageCompressibleMenuType(string value)
        {
            return value == "Toggle" || value == "RadialPuppet" || value == "TwoAxisPuppet" || value == "FourAxisPuppet";
        }

        private static bool IsFaceTrackingName(string value)
        {
            return value.StartsWith("FT/", StringComparison.OrdinalIgnoreCase)
                || value.StartsWith("FT_", StringComparison.OrdinalIgnoreCase)
                || value.StartsWith("FaceTracking/", StringComparison.OrdinalIgnoreCase)
                || value.StartsWith("EyeTracking/", StringComparison.OrdinalIgnoreCase);
        }

        private static void ValidateRequestKeys(JObject request, HashSet<string> allowed)
        {
            foreach (var property in request.Properties())
            {
                Require(allowed.Contains(property.Name), "Parameter bit-packing received an unknown argument.");
            }
        }

        private static string NormalizeSceneAssetPath(string value)
        {
            var path = value.Trim().Replace('\\', '/');
            Require(path.StartsWith("Assets/", StringComparison.Ordinal) && path.EndsWith(".unity", StringComparison.Ordinal), "The source scene path is invalid.");
            Require(!path.Split('/').Any(part => part.Length == 0 || part == "." || part == ".."), "The source scene path is invalid.");
            return path;
        }

        private static string NormalizeObjectPath(string value)
        {
            var path = value.Trim().Replace('\\', '/');
            Require(path.Length > 0 && path.Length <= 512, "The source avatar path is invalid.");
            Require(!path.Split('/').Any(part => string.IsNullOrWhiteSpace(part) || part == "." || part == ".."), "The source avatar path is invalid.");
            return path;
        }

        private static string NormalizeObjectName(string value)
        {
            var name = value.Trim();
            Require(name.Length > 0 && name.Length <= 80 && name != "." && name != "..", "The output clone name is invalid.");
            Require(name.IndexOfAny(Path.GetInvalidFileNameChars().Concat(new[] { '/', '\\' }).Distinct().ToArray()) < 0, "The output clone name is invalid.");
            Require(!name.EndsWith(".", StringComparison.Ordinal) && !name.EndsWith(" ", StringComparison.Ordinal), "The output clone name is invalid.");
            var stem = name.Split('.')[0];
            Require(!WindowsReservedFileStems.Contains(stem), "The output clone name is invalid.");
            return name;
        }

        private static bool IsSceneNameLoaded(string name)
        {
            for (var index = 0; index < SceneManager.sceneCount; index++)
            {
                if (SceneManager.GetSceneAt(index).name == name) return true;
            }
            return false;
        }

        private static string CurrentProjectPath()
        {
            return Path.GetFullPath(Path.Combine(Application.dataPath, "..")).TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar);
        }

        private static string AbsoluteProjectPath(string assetPath)
        {
            var full = Path.GetFullPath(Path.Combine(CurrentProjectPath(), assetPath.Replace('/', Path.DirectorySeparatorChar)));
            var project = CurrentProjectPath() + Path.DirectorySeparatorChar;
            Require(full.StartsWith(project, StringComparison.OrdinalIgnoreCase), "A project asset path escaped the selected project.");
            return full;
        }

        private static bool ProjectPathsEqual(string left, string right)
        {
            return string.Equals(
                Path.GetFullPath(left).TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar),
                Path.GetFullPath(right).TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar),
                StringComparison.OrdinalIgnoreCase
            );
        }

        private static string RelativePath(string root, string path)
        {
            var rootUri = new Uri(Path.GetFullPath(root).TrimEnd(Path.DirectorySeparatorChar) + Path.DirectorySeparatorChar);
            var pathUri = new Uri(Path.GetFullPath(path));
            return Uri.UnescapeDataString(rootUri.MakeRelativeUri(pathUri).ToString()).Replace('\\', '/');
        }

        private static void RequireStableRegularFile(string path)
        {
            Require(File.Exists(path), "A required file is missing.");
            var identity = CaptureIdentity(path, false);
            Require(!identity.IsReparsePoint && identity.NumberOfLinks == 1, "A required file is linked or reparsed.");
        }

        private static void PublishTransactionJournal(string journalPath, byte[] bytes)
        {
            Require(!string.IsNullOrWhiteSpace(journalPath)
                    && bytes != null && bytes.Length > 0,
                "The transaction journal publication is invalid.");
            var nextPath = journalPath + ".next";
            for (var attempt = 0; attempt < TransactionIoRetryAttempts; attempt++)
            {
                try
                {
                    if (File.Exists(nextPath))
                    {
                        RequireStableRegularFile(nextPath);
                        File.Delete(nextPath);
                    }
                    if (!File.Exists(nextPath)) break;
                }
                catch (Exception exception) when (
                    (exception is IOException || exception is UnauthorizedAccessException)
                    && attempt + 1 < TransactionIoRetryAttempts)
                {
                    System.Threading.Thread.Sleep(
                        TransactionIoRetryBaseDelayMilliseconds * (attempt + 1));
                    continue;
                }
                if (attempt + 1 < TransactionIoRetryAttempts)
                {
                    System.Threading.Thread.Sleep(
                        TransactionIoRetryBaseDelayMilliseconds * (attempt + 1));
                }
            }
            Require(!File.Exists(nextPath),
                "The prior transaction journal staging file could not be removed.");
            using (var stream = new FileStream(
                nextPath,
                FileMode.CreateNew,
                FileAccess.Write,
                FileShare.Read))
            {
                stream.Write(bytes, 0, bytes.Length);
                stream.Flush(true);
            }
            RequireStableRegularFile(nextPath);

            for (var attempt = 0; attempt < TransactionIoRetryAttempts; attempt++)
            {
                try
                {
                    if (File.Exists(nextPath))
                    {
                        RequireStableRegularFile(nextPath);
                        if (File.Exists(journalPath))
                        {
                            RequireStableRegularFile(journalPath);
                            File.Replace(nextPath, journalPath, null, true);
                        }
                        else File.Move(nextPath, journalPath);
                    }
                    Require(!File.Exists(nextPath),
                        "The transaction journal staging file remained after publication.");
                    RequireStableRegularFile(journalPath);
                    Require(File.ReadAllBytes(journalPath).SequenceEqual(bytes),
                        "The published transaction journal differs from its durable payload.");
                    return;
                }
                catch (Exception exception) when (
                    (exception is IOException || exception is UnauthorizedAccessException)
                    && attempt + 1 < TransactionIoRetryAttempts)
                {
                    System.Threading.Thread.Sleep(
                        TransactionIoRetryBaseDelayMilliseconds * (attempt + 1));
                }
            }
            throw new ParameterBitPackingException(
                "The transaction journal could not be published.");
        }

        private static void DeleteOwnedTransactionTreeWithRetry(string transactionRoot)
        {
            Require(!string.IsNullOrWhiteSpace(transactionRoot),
                "The transaction cleanup root is invalid.");
            for (var attempt = 0; attempt < TransactionIoRetryAttempts; attempt++)
            {
                try
                {
                    if (!Directory.Exists(transactionRoot))
                    {
                        Require(!File.Exists(transactionRoot),
                            "The transaction cleanup root changed type.");
                        return;
                    }
                    CacheTransaction.RequireSafeOwnedTreeForDeletion(transactionRoot);
                    Directory.Delete(transactionRoot, true);
                    if (!Directory.Exists(transactionRoot)
                        && !File.Exists(transactionRoot)) return;
                }
                catch (Exception exception) when (
                    (exception is IOException || exception is UnauthorizedAccessException)
                    && attempt + 1 < TransactionIoRetryAttempts)
                {
                    System.Threading.Thread.Sleep(
                        TransactionIoRetryBaseDelayMilliseconds * (attempt + 1));
                    continue;
                }
                if (attempt + 1 < TransactionIoRetryAttempts)
                {
                    System.Threading.Thread.Sleep(
                        TransactionIoRetryBaseDelayMilliseconds * (attempt + 1));
                }
            }
            Require(!Directory.Exists(transactionRoot) && !File.Exists(transactionRoot),
                "The transaction journal tree could not be removed.");
        }

        private static FileIdentity CaptureIdentity(string path, bool isDirectory)
        {
            var flags = isDirectory
                ? NativeFileFlagBackupSemantics | NativeFileFlagOpenReparsePoint
                : NativeFileAttributeNormal | NativeFileFlagOpenReparsePoint;
            using (
                var handle = CreateFile(
                    path,
                    0,
                    NativeFileShareRead,
                    IntPtr.Zero,
                    NativeOpenExisting,
                    flags,
                    IntPtr.Zero
                )
            )
            {
                Require(handle != null && !handle.IsInvalid, "A required filesystem identity is unavailable.");
                Require(GetFileInformationByHandle(handle, out var info), "A required filesystem identity is unavailable.");
                var attributes = (FileAttributes)info.fileAttributes;
                var identity = new FileIdentity
                {
                    VolumeSerial = info.volumeSerialNumber,
                    FileIndexHigh = info.fileIndexHigh,
                    FileIndexLow = info.fileIndexLow,
                    NumberOfLinks = info.numberOfLinks,
                    IsReparsePoint = (attributes & FileAttributes.ReparsePoint) != 0
                };
                identity.Digest = Sha256Framed(
                    "vrcforge.parameter_file_identity.v1",
                    identity.VolumeSerial,
                    identity.FileIndexHigh,
                    identity.FileIndexLow,
                    identity.NumberOfLinks,
                    identity.IsReparsePoint,
                    isDirectory
                );
                return identity;
            }
        }

        private static string PublicKeyToken(AssemblyName name)
        {
            var token = name.GetPublicKeyToken();
            return token == null || token.Length == 0
                ? string.Empty
                : BitConverter.ToString(token).Replace("-", string.Empty).ToLowerInvariant();
        }

        private static string Sha256File(string path)
        {
            using (var stream = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.Read))
            using (var sha = SHA256.Create())
            {
                return Hex(sha.ComputeHash(stream));
            }
        }

        private static string Sha256Utf8(string value)
        {
            using (var sha = SHA256.Create())
            {
                return Hex(sha.ComputeHash(Encoding.UTF8.GetBytes(value)));
            }
        }

        private static string Sha256Framed(string schema, params object[] values)
        {
            return Sha256Utf8(schema + "\n" + string.Concat(values.Select(Frame)));
        }

        private static string Frame(object value)
        {
            string text;
            if (value == null) text = "null";
            else if (value is bool boolean) text = boolean ? "true" : "false";
            else if (value is IFormattable formattable) text = formattable.ToString(null, CultureInfo.InvariantCulture);
            else text = value.ToString();
            return Encoding.UTF8.GetByteCount(text).ToString(CultureInfo.InvariantCulture) + ":" + text;
        }

        private static string FloatText(float value)
        {
            return value.ToString("R", CultureInfo.InvariantCulture);
        }

        private static string VectorText(Vector3 value)
        {
            return FloatText(value.x) + "," + FloatText(value.y) + "," + FloatText(value.z);
        }

        private static string QuaternionText(Quaternion value)
        {
            return FloatText(value.x) + "," + FloatText(value.y) + "," + FloatText(value.z) + "," + FloatText(value.w);
        }

        private static string Hex(byte[] bytes)
        {
            return BitConverter.ToString(bytes).Replace("-", string.Empty).ToLowerInvariant();
        }

        private static bool IsGuid(string value)
        {
            return value != null && value.Length == 32 && value.All(character => (character >= '0' && character <= '9') || (character >= 'a' && character <= 'f'));
        }

        private static string ReadRequiredString(JObject request, string key)
        {
            var token = request[key];
            Require(token != null && token.Type == JTokenType.String, "A required parameter bit-packing argument is missing.");
            var value = token.Value<string>().Trim();
            Require(value.Length > 0 && value.Length <= 1024, "A parameter bit-packing argument is invalid.");
            return value;
        }

        private static string ReadExpectedString(JObject request, string key)
        {
            return ReadRequiredString(request, key);
        }

        private static int ReadExpectedInt(JObject request, string key)
        {
            var token = request[key];
            Require(token != null && token.Type == JTokenType.Integer, "A required numeric precondition is missing.");
            var value = token.Value<long>();
            Require(value >= 0 && value <= 100000, "A numeric precondition is invalid.");
            return checked((int)value);
        }

        private static long ReadExpectedLong(JObject request, string key)
        {
            var token = request[key];
            Require(token != null && token.Type == JTokenType.Integer, "A required numeric precondition is missing.");
            var value = token.Value<long>();
            Require(value >= 0 && value <= CacheBackupMaxBytes, "A numeric precondition is invalid.");
            return value;
        }

        private static bool ReadExpectedBool(JObject request, string key)
        {
            var token = request[key];
            Require(token != null && token.Type == JTokenType.Boolean, "A required boolean precondition is missing.");
            return token.Value<bool>();
        }

        private static bool ReadOptionalBool(JObject request, string key, bool fallback)
        {
            var token = request[key];
            if (token == null) return fallback;
            Require(token.Type == JTokenType.Boolean, "A parameter bit-packing boolean argument is invalid.");
            return token.Value<bool>();
        }

        private static void Require(bool condition, string message)
        {
            if (!condition) throw new ParameterBitPackingException(message);
        }

        [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
        private static extern SafeFileHandle CreateFile(
            string fileName,
            uint desiredAccess,
            uint shareMode,
            IntPtr securityAttributes,
            uint creationDisposition,
            uint flagsAndAttributes,
            IntPtr templateFile);

        [DllImport("kernel32.dll", SetLastError = true)]
        [return: MarshalAs(UnmanagedType.Bool)]
        private static extern bool GetFileInformationByHandle(
            SafeFileHandle file,
            out ByHandleFileInformation information);

        [StructLayout(LayoutKind.Sequential)]
        private struct ByHandleFileInformation
        {
            internal uint fileAttributes;
            internal System.Runtime.InteropServices.ComTypes.FILETIME creationTime;
            internal System.Runtime.InteropServices.ComTypes.FILETIME lastAccessTime;
            internal System.Runtime.InteropServices.ComTypes.FILETIME lastWriteTime;
            internal uint volumeSerialNumber;
            internal uint fileSizeHigh;
            internal uint fileSizeLow;
            internal uint numberOfLinks;
            internal uint fileIndexHigh;
            internal uint fileIndexLow;
        }

        private sealed class ParameterBitPackingException : Exception
        {
            internal ParameterBitPackingException(string message) : base(message) { }
        }

        private sealed class SourceSnapshot
        {
            internal GameObject Avatar;
            internal string ScenePath;
            internal string SceneGuid;
            internal string SceneFilePath;
            internal string SceneMetaPath;
            internal string SceneFileDigest;
            internal string SceneMetaDigest;
            internal string ObjectPath;
            internal string GlobalObjectId;
            internal string HierarchyDigest;
            internal string SourceStateDigest;
            internal string SourceAssetSetDigest;
            internal int SourceAssetCount;
            internal List<string> SourceAssetFilePaths;
            internal ParameterState ParameterState;
            internal string ControllerStateDigest;
            internal string MenuStateDigest;
            internal ParameterBehaviorEvidence BehaviorEvidence;
            internal bool SourceDirty;
            internal bool ReferencedAssetsDirty;
            internal IReadOnlyDictionary<string, HashSet<string>> MenuUsage;

            internal object ToPayload()
            {
                return new
                {
                    scenePath = ScenePath,
                    sceneGuid = SceneGuid,
                    sceneFileDigest = SceneFileDigest,
                    sceneMetaDigest = SceneMetaDigest,
                    objectPath = ObjectPath,
                    globalObjectId = GlobalObjectId,
                    hierarchyDigest = HierarchyDigest,
                    sourceStateDigest = SourceStateDigest,
                    sourceAssetSetDigest = SourceAssetSetDigest,
                    sourceAssetCount = SourceAssetCount,
                    parameterStateDigest = ParameterState.StateDigest,
                    controllerStateDigest = ControllerStateDigest,
                    menuStateDigest = MenuStateDigest,
                    behaviorEvidence = BehaviorEvidence.ToPayload(),
                    sourceCostBits = ParameterState.CostBits,
                    parameterCount = ParameterState.Parameters.Count,
                    safeCandidateNames = ParameterState.SafeCandidateNames,
                    safeCandidateDigest = ParameterState.SafeCandidateDigest,
                    excludedParameters = ParameterState.Excluded.Select(item => item.ToPayload()).ToArray(),
                    excludedDigest = ParameterState.ExcludedDigest,
                    sourceDirty = SourceDirty,
                    referencedAssetsDirty = ReferencedAssetsDirty
                };
            }
        }

        private sealed class ParameterState
        {
            internal List<ParameterRow> Parameters;
            internal int CostBits;
            internal List<string> SafeCandidateNames;
            internal string SafeCandidateDigest;
            internal List<ExcludedParameter> Excluded;
            internal string ExcludedDigest;
            internal string StateDigest;
            internal bool HasUnsafeCompressibleCandidate;
        }

        private sealed class ParameterRow
        {
            internal string Name;
            internal string Type;
            internal float DefaultValue;
            internal bool Saved;
            internal bool NetworkSynced;
            internal string StateDigest => Sha256Framed("vrcforge.parameter_row.v1", Name, Type, FloatText(DefaultValue), Saved, NetworkSynced);
        }

        private sealed class ExcludedParameter
        {
            internal string Name;
            internal string Type;
            internal bool NetworkSynced;
            internal List<string> Reasons;
            internal string StateDigest;
            internal object ToPayload() => new { name = Name, type = Type, networkSynced = NetworkSynced, reasons = Reasons, stateDigest = StateDigest };
        }

        private sealed class AssetReceipt
        {
            internal string AssetPath;
            internal string Guid;
            internal string FileDigest;
            internal string MetaDigest;
            internal List<string> AbsoluteFilePaths;
        }

        private sealed class CapabilityProfile
        {
            internal string Id;
            internal string CallbackAssemblySha256;
            internal int CallbackRosterCount;
            internal string CallbackRosterDigest;
            internal int CallbackAssemblySetCount;
            internal string CallbackAssemblySetDigest;
        }

        private sealed class CapabilitySnapshot
        {
            internal string PackageRootPath;
            internal string PackageRootIdentityDigest;
            internal string ProfileId;
            internal string CallbackAssemblySha256;
            internal string SdkCallbackAssemblySha256;
            internal int CallbackRosterCount;
            internal string CallbackRosterDigest;
            internal int CallbackAssemblySetCount;
            internal string CallbackAssemblySetDigest;
            internal List<string> CallbackAssemblyPaths;
            internal string CapabilityDigest;

            internal object ToPayload()
            {
                return new
                {
                    packageId = PackageId,
                    packageVersion = PackageVersion,
                    packageAuthor = PackageAuthor,
                    packageArchiveSha256 = PackageArchiveSha256,
                    packageTreeSha256 = PackageTreeSha256,
                    packageFileCount = PackageFileCount,
                    packageRootIdentityDigest = PackageRootIdentityDigest,
                    profileId = ProfileId,
                    callbackAssemblyName = CallbackAssemblyName,
                    callbackAssemblyVersion = CallbackAssemblyVersion,
                    callbackAssemblyPublicKeyToken = CallbackAssemblyPublicKeyToken,
                    callbackAssemblySha256 = CallbackAssemblySha256,
                    sdkCallbackAssemblyName = SdkCallbackAssemblyName,
                    sdkCallbackAssemblyVersion = SdkCallbackAssemblyVersion,
                    sdkCallbackAssemblyPublicKeyToken = SdkCallbackAssemblyPublicKeyToken,
                    sdkCallbackAssemblySha256 = SdkCallbackAssemblySha256,
                    callbackType = CallbackTypeName,
                    callbackSignature = CallbackSignature,
                    registeredHookType = RegisteredHookType,
                    registeredHookCount = 1,
                    callbackRosterCount = CallbackRosterCount,
                    callbackRosterDigest = CallbackRosterDigest,
                    callbackAssemblySetCount = CallbackAssemblySetCount,
                    callbackAssemblySetDigest = CallbackAssemblySetDigest,
                    capabilityDigest = CapabilityDigest
                };
            }
        }

        private sealed class PreferenceSnapshot
        {
            internal bool CompressorPresent;
            internal int CompressorValue;
            internal bool AlignMobilePresent;
            internal bool AlignMobileValue;
            internal string BuildTarget;
            internal string ReceiptDigest => Sha256Framed(
                PreferenceSchema,
                CompressorPresent,
                CompressorValue,
                AlignMobilePresent,
                AlignMobileValue,
                BuildTarget,
                "current-target-only",
                false,
                false);

            internal object ToPayload() => new
            {
                schema = PreferenceSchema,
                compressorPresent = CompressorPresent,
                compressorValue = CompressorValue,
                compressorMode = CompressorPresent ? "explicit-automatic" : "missing-default-automatic",
                alignMobilePresent = AlignMobilePresent,
                alignMobileValue = AlignMobileValue,
                readOnly = true,
                buildTarget = BuildTarget,
                platformScope = "current-target-only",
                crossPlatformEquivalent = false,
                localAppDataAccessed = false,
                receiptDigest = ReceiptDigest
            };
        }

        private sealed class OutputPreview
        {
            internal OutputPreview(string cloneName, string sceneName, TreeSnapshot outputTree)
            {
                CloneName = cloneName;
                SceneName = sceneName;
                TemporaryPrefabPath = GeneratedRoot + "/" + cloneName + "/" + cloneName + ".prefab";
                PrefabPath = OutputKindRoot + "/" + cloneName + "/" + cloneName + ".prefab";
                TreeDigestBefore = outputTree.Digest;
                EntryCountBefore = outputTree.EntryCount;
                RootExistsBefore = outputTree.Exists;
            }
            internal string CloneName { get; }
            internal string SceneName { get; }
            internal string TemporaryPrefabPath { get; }
            internal string PrefabPath { get; }
            internal string TreeDigestBefore { get; }
            internal int EntryCountBefore { get; }
            internal bool RootExistsBefore { get; }
            internal object ToPayload() => new
            {
                root = OutputRoot,
                kindRoot = OutputKindRoot,
                cloneName = CloneName,
                sceneName = SceneName,
                temporaryPrefabPath = TemporaryPrefabPath,
                prefabPath = PrefabPath,
                treeDigestBefore = TreeDigestBefore,
                entryCountBefore = EntryCountBefore,
                rootExistsBefore = RootExistsBefore,
                targetExistsBefore = false,
                cloneExists = false,
                sceneCreated = false,
                prefabExists = false
            };
        }

        private sealed class OutputArtifactReceipt
        {
            internal string PrefabPath;
            internal string Guid;
            internal string FileDigest;
            internal string MetaDigest;
            internal string RootGlobalObjectId;
            internal string PortableAvatarDigest;
            internal string OrderedParameterDigest;
            internal string MenuGraphDigest;
            internal string AnimatorBehaviorDigest;
            internal string EvidenceReceiptDigest;
            internal string BehaviorProofDigest;
            internal string ParameterStateDigest;
            internal string ReceiptDigest => Sha256Framed(
                "vrcforge.parameter_output_prefab.v1",
                PrefabPath,
                Guid,
                FileDigest,
                MetaDigest,
                RootGlobalObjectId,
                PortableAvatarDigest,
                OrderedParameterDigest,
                MenuGraphDigest,
                AnimatorBehaviorDigest,
                EvidenceReceiptDigest,
                BehaviorProofDigest,
                ParameterStateDigest
            );
        }

        private sealed class AssetTreeManifest
        {
            internal string RootPath;
            internal string PrefabPath;
            internal int EntryCount;
            internal long TotalBytes;
            internal string ContentDigest;
            internal string HandleEvidenceDigest;
            internal string GuidMapDigest;
            internal string DependencyGuidDigest;
            internal string ReferenceClosureDigest;
            internal bool NoTemporaryReferences;
            internal bool ReparseFree;
            internal bool SingleLink;
            internal bool HandleHashed;
            internal bool FinalEnumerationVerified;
            internal string ReceiptDigest => Sha256Framed(
                OutputManifestSchema,
                RootPath,
                PrefabPath,
                EntryCount,
                TotalBytes,
                ContentDigest,
                HandleEvidenceDigest,
                GuidMapDigest,
                DependencyGuidDigest,
                ReferenceClosureDigest,
                NoTemporaryReferences,
                ReparseFree,
                SingleLink,
                HandleHashed,
                FinalEnumerationVerified);

            internal object ToPayload() => new
            {
                schema = OutputManifestSchema,
                rootPath = RootPath,
                prefabPath = PrefabPath,
                entryCount = EntryCount,
                byteCount = TotalBytes,
                contentDigest = ContentDigest,
                handleEvidenceDigest = HandleEvidenceDigest,
                guidMapDigest = GuidMapDigest,
                dependencyGuidDigest = DependencyGuidDigest,
                referenceClosureDigest = ReferenceClosureDigest,
                noTemporaryReferences = NoTemporaryReferences,
                reparseFree = ReparseFree,
                singleLink = SingleLink,
                handleHashed = HandleHashed,
                finalEnumerationVerified = FinalEnumerationVerified,
                receiptDigest = ReceiptDigest
            };
        }

        private sealed class CreatedAssetFolder
        {
            internal string AssetPath;
            internal string Guid;
            internal string DirectoryIdentityDigest;
            internal string MetaIdentityDigest;
            internal string MetaDigest;
        }

        private sealed class TreeSnapshot
        {
            internal string Digest;
            internal string ContentDigest;
            internal int EntryCount;
            internal long TotalBytes;
            internal SortedDictionary<string, TreeEntry> Entries;
            internal bool Exists = true;

            internal static TreeSnapshot FromEntries(string schema, SortedDictionary<string, TreeEntry> entries)
            {
                var digest = Sha256Utf8(schema + "\n" + string.Concat(entries.Values.Select(entry => entry.Receipt)));
                var contentDigest = Sha256Utf8(
                    schema + ".content\n"
                    + string.Concat(entries.Values.Select(entry => entry.ContentReceipt)));
                return new TreeSnapshot
                {
                    Digest = digest,
                    ContentDigest = contentDigest,
                    EntryCount = entries.Count,
                    TotalBytes = entries.Values.Where(entry => entry.Kind == "F").Sum(entry => entry.Length),
                    Entries = entries,
                    Exists = true
                };
            }
        }

        private sealed class AuxiliaryGeneratedSnapshot
        {
            internal TreeSnapshot Tree;
            internal string PackageRootIdentityDigest;
            internal string PackageManifestDigest;
            internal string PackageManifestIdentityDigest;
            internal string ReceiptDigest;
        }

        private sealed class TreeEntry
        {
            internal string RelativePath;
            internal string Kind;
            internal long Length;
            internal string Digest;
            internal string IdentityDigest;
            internal string Receipt => Frame(RelativePath) + Frame(Kind) + Frame(Length) + Frame(Digest) + Frame(IdentityDigest);
            internal string ContentReceipt => Frame(RelativePath) + Frame(Kind) + Frame(Length) + Frame(Kind == "F" ? Digest : string.Empty);
        }

        private sealed class RootIdentitySnapshot
        {
            internal string Digest;
            internal int EntryCount;
            internal SortedDictionary<string, FileIdentity> Entries;

            internal static RootIdentitySnapshot FromEntries(SortedDictionary<string, FileIdentity> entries)
            {
                var digest = Sha256Utf8(
                    RootIdentitySchema + "\n"
                    + string.Concat(entries.Select(entry => Frame(entry.Key) + Frame(entry.Value.Digest)))
                );
                return new RootIdentitySnapshot
                {
                    Digest = digest,
                    EntryCount = entries.Count,
                    Entries = entries
                };
            }
        }

        private sealed class GeneratedDelta
        {
            internal List<TreeEntry> Added;
            internal List<TreeEntry> Modified;
            internal List<TreeEntry> Removed;
        }

        private sealed class FileIdentity
        {
            internal uint VolumeSerial;
            internal uint FileIndexHigh;
            internal uint FileIndexLow;
            internal uint NumberOfLinks;
            internal bool IsReparsePoint;
            internal string Digest;
        }

        private sealed class AuxiliaryGeneratedTransaction
        {
            private readonly string privateRoot;
            private readonly string transactionsRoot;
            private readonly string transactionRoot;
            private readonly string backupRoot;
            private readonly string backupMetaPath;
            private readonly string journalPath;
            private readonly string lockPath;
            private readonly AuxiliaryGeneratedSnapshot baseline;
            private FileStream transactionLock;
            private bool prepared;
            private bool restored;
            private bool closingStarted;
            private bool completed;
            private AuxiliaryGeneratedSnapshot observed;
            private string ownedRootIdentityDigest = string.Empty;
            private bool createdByOperation;
            private string restorationMode = string.Empty;

            private AuxiliaryGeneratedTransaction(
                string privateRoot,
                string transactionsRoot,
                string transactionRoot,
                string backupRoot,
                string backupMetaPath,
                string journalPath,
                AuxiliaryGeneratedSnapshot baseline)
            {
                this.privateRoot = privateRoot;
                this.transactionsRoot = transactionsRoot;
                this.transactionRoot = transactionRoot;
                this.backupRoot = backupRoot;
                this.backupMetaPath = backupMetaPath;
                this.journalPath = journalPath;
                lockPath = Path.Combine(transactionsRoot, "parameter-auxiliary-generated.lock");
                this.baseline = baseline;
            }

            internal string JournalId => Path.GetFileName(transactionRoot);
            internal bool Prepared => prepared;
            internal bool Restored => restored;
            internal bool Completed => completed;
            internal AuxiliaryGeneratedSnapshot Observed => observed;
            internal string OwnedRootIdentityDigest => ownedRootIdentityDigest;
            internal bool CreatedByOperation => createdByOperation;
            internal string RestorationMode => restorationMode;

            internal static AuxiliaryGeneratedTransaction Plan(AuxiliaryGeneratedSnapshot baseline)
            {
                Require(baseline != null && baseline.Tree != null,
                    "The auxiliary generated baseline is unavailable.");
                Require(baseline.Tree.EntryCount <= CacheBackupMaxEntries,
                    "The auxiliary generated baseline exceeds the backup entry limit.");
                Require(baseline.Tree.TotalBytes <= CacheBackupMaxBytes,
                    "The auxiliary generated baseline exceeds the backup byte limit.");
                var project = CurrentProjectPath();
                var library = Path.Combine(project, "Library");
                Require(Directory.Exists(library), "The project Library root is unavailable.");
                CacheTransaction.RequireSafeDirectory(library,
                    "The project Library root is linked or reparsed.");
                var privateRoot = Path.Combine(library, "VRCForge");
                Require(!File.Exists(privateRoot), "The private transaction root collides with a file.");
                if (Directory.Exists(privateRoot))
                {
                    CacheTransaction.RequireSafeDirectory(privateRoot,
                        "The private transaction root is linked or reparsed.");
                }
                var transactions = Path.Combine(privateRoot, "transactions");
                Require(!File.Exists(transactions), "The transaction journal root collides with a file.");
                if (Directory.Exists(transactions))
                {
                    CacheTransaction.RequireSafeDirectory(transactions,
                        "The transaction journal root is linked or reparsed.");
                    Require(!File.Exists(Path.Combine(transactions, "parameter-auxiliary-generated.lock")),
                        "Another auxiliary generated transaction is active.");
                    var unfinished = Directory.EnumerateFileSystemEntries(
                            transactions,
                            "parameter-auxiliary-generated-*",
                            SearchOption.TopDirectoryOnly)
                        .Take(2)
                        .ToArray();
                    Require(unfinished.Length == 0,
                        "An unfinished auxiliary generated transaction requires checkpoint restore.");
                }
                var transactionRoot = Path.Combine(
                    transactions,
                    "parameter-auxiliary-generated-" + Guid.NewGuid().ToString("N"));
                Require(!Directory.Exists(transactionRoot) && !File.Exists(transactionRoot),
                    "The planned auxiliary transaction path already exists.");
                return new AuxiliaryGeneratedTransaction(
                    privateRoot,
                    transactions,
                    transactionRoot,
                    Path.Combine(transactionRoot, "tree"),
                    Path.Combine(transactionRoot, "root.meta"),
                    Path.Combine(transactionRoot, "journal.json"),
                    baseline);
            }

            internal void Prepare()
            {
                Require(!prepared && !restored && !completed,
                    "The auxiliary transaction cannot be prepared in its current state.");
                Directory.CreateDirectory(privateRoot);
                CacheTransaction.RequireSafeDirectory(privateRoot,
                    "The private transaction root is linked or reparsed.");
                Directory.CreateDirectory(transactionsRoot);
                CacheTransaction.RequireSafeDirectory(transactionsRoot,
                    "The transaction journal root is linked or reparsed.");
                try
                {
                    transactionLock = new FileStream(
                        lockPath,
                        FileMode.CreateNew,
                        FileAccess.ReadWrite,
                        FileShare.Read,
                        4096,
                        FileOptions.DeleteOnClose | FileOptions.WriteThrough);
                    var lockBytes = Encoding.UTF8.GetBytes(JournalId);
                    transactionLock.Write(lockBytes, 0, lockBytes.Length);
                    transactionLock.Flush(true);
                }
                catch (IOException)
                {
                    throw new ParameterBitPackingException(
                        "Another auxiliary generated transaction is active.");
                }
                Require(
                    !Directory.EnumerateFileSystemEntries(
                        transactionsRoot,
                        "parameter-auxiliary-generated-*",
                        SearchOption.TopDirectoryOnly).Any(),
                    "An unfinished auxiliary generated transaction requires checkpoint restore.");
                Directory.CreateDirectory(transactionRoot);
                CacheTransaction.RequireSafeDirectory(transactionRoot,
                    "The auxiliary transaction root is linked or reparsed.");
                WriteJournal("preparing", false);
                if (baseline.Tree.Exists)
                {
                    Directory.CreateDirectory(backupRoot);
                    CacheTransaction.RequireSafeDirectory(backupRoot,
                        "The auxiliary backup root is linked or reparsed.");
                    CacheTransaction.CopyTree(AbsoluteProjectPath(AuxiliaryGeneratedRoot), backupRoot);
                    var auxiliaryMeta = AbsoluteProjectPath(AuxiliaryGeneratedRoot) + ".meta";
                    RequireStableRegularFile(auxiliaryMeta);
                    File.Copy(auxiliaryMeta, backupMetaPath, false);
                    RequireStableRegularFile(backupMetaPath);
                    var backup = CaptureManagedTreeAbsolute(
                        backupRoot,
                        backupMetaPath,
                        AuxiliaryGeneratedTreeSchema + ".backup");
                    Require(ContentEquivalent(baseline.Tree, backup)
                            && backup.EntryCount == baseline.Tree.EntryCount
                            && backup.TotalBytes == baseline.Tree.TotalBytes,
                        "The auxiliary generated backup does not match its baseline.");
                }
                var stableBaseline = CaptureAuxiliaryGenerated();
                Require(stableBaseline.ReceiptDigest == baseline.ReceiptDigest,
                    "The auxiliary generated baseline changed during backup.");
                WriteJournal("prepared", false);
                prepared = true;
            }

            internal void ObserveMutation()
            {
                Require(prepared && !restored && !completed,
                    "The auxiliary transaction cannot observe in its current state.");
                var current = CaptureAuxiliaryGenerated();
                RequirePackageIdentity(current);
                if (baseline.Tree.Exists)
                {
                    Require(current.Tree.Exists,
                        "The pre-existing auxiliary generated root disappeared during apply.");
                    var baselineRootIdentity = RootIdentityDigest(baseline.Tree);
                    Require(RootIdentityDigest(current.Tree) == baselineRootIdentity,
                        "The pre-existing auxiliary generated root identity changed during apply.");
                    ownedRootIdentityDigest = baselineRootIdentity;
                }
                else if (current.Tree.Exists)
                {
                    var currentRootIdentity = RootIdentityDigest(current.Tree);
                    if (string.IsNullOrEmpty(ownedRootIdentityDigest))
                    {
                        ownedRootIdentityDigest = currentRootIdentity;
                        createdByOperation = true;
                    }
                    else
                    {
                        Require(currentRootIdentity == ownedRootIdentityDigest,
                            "The operation-created auxiliary generated root identity changed.");
                    }
                }
                else
                {
                    Require(!createdByOperation,
                        "The operation-created auxiliary generated root disappeared before restore.");
                    ownedRootIdentityDigest = string.Empty;
                }
                observed = current;
                WriteJournal("observed", false);
            }

            internal bool Restore(bool allowGeneratedRootDirty)
            {
                if (!prepared || completed) return false;
                if (restored)
                {
                    try
                    {
                        return AuxiliaryContentEquals(baseline, CaptureAuxiliaryGenerated());
                    }
                    catch
                    {
                        return false;
                    }
                }
                try
                {
                    if (allowGeneratedRootDirty)
                    {
                        RequireNoDirtyProjectAssets(AuxiliaryGeneratedRoot, GeneratedRoot);
                    }
                    else RequireNoDirtyProjectAssets(AuxiliaryGeneratedRoot);
                    if (observed == null) ObserveMutation();
                    WriteJournal("restoring", false);
                    var current = CaptureAuxiliaryGenerated();
                    RequirePackageIdentity(current);
                    Require(current.Tree.Digest == observed.Tree.Digest
                            && current.Tree.ContentDigest == observed.Tree.ContentDigest
                            && current.Tree.EntryCount == observed.Tree.EntryCount
                            && current.Tree.TotalBytes == observed.Tree.TotalBytes
                            && current.Tree.Exists == observed.Tree.Exists,
                        "The auxiliary generated root changed after its owned observation.");
                    if (baseline.Tree.Exists)
                    {
                        RestorePresentBaseline(current.Tree);
                        restorationMode = "restored_baseline";
                    }
                    else if (current.Tree.Exists)
                    {
                        DeleteCreatedRoot(current.Tree);
                        restorationMode = "removed_created_root";
                    }
                    else
                    {
                        Require(!createdByOperation && string.IsNullOrEmpty(ownedRootIdentityDigest),
                            "The absent auxiliary baseline has inconsistent ownership evidence.");
                        restorationMode = "no_auxiliary_root";
                    }
                    if (allowGeneratedRootDirty)
                    {
                        RequireNoDirtyProjectAssets(AuxiliaryGeneratedRoot, GeneratedRoot);
                    }
                    else RequireNoDirtyProjectAssets(AuxiliaryGeneratedRoot);
                    AssetDatabase.SaveAssets();
                    AssetDatabase.Refresh(ImportAssetOptions.ForceSynchronousImport);
                    var final = CaptureAuxiliaryGenerated();
                    Require(AuxiliaryContentEquals(baseline, final)
                            && final.Tree.ContentDigest == baseline.Tree.ContentDigest
                            && final.Tree.EntryCount == baseline.Tree.EntryCount
                            && final.Tree.TotalBytes == baseline.Tree.TotalBytes,
                        "The auxiliary generated root was not restored exactly.");
                    WriteJournal("restored", true);
                    restored = true;
                    return true;
                }
                catch
                {
                    try { WriteJournal("restore_failed", false); } catch { }
                    return false;
                }
            }

            internal bool AbortPreparation()
            {
                if (prepared) return false;
                if (completed) return true;
                try
                {
                    if (Directory.Exists(transactionRoot))
                    {
                        CacheTransaction.RequireSafeOwnedTreeForDeletion(transactionRoot);
                        DeleteOwnedTransactionTreeWithRetry(transactionRoot);
                    }
                    Require(!Directory.Exists(transactionRoot) && !File.Exists(transactionRoot),
                        "The incomplete auxiliary transaction could not be removed.");
                    if (transactionLock != null) ReleaseLock();
                    completed = true;
                    return true;
                }
                catch
                {
                    return false;
                }
            }

            internal void Complete()
            {
                if (completed)
                {
                    Require(VerifyClosedTerminal(),
                        "The auxiliary transaction closed state is inconsistent.");
                    return;
                }
                Require(prepared && restored,
                    "The auxiliary transaction cannot be closed in its current state.");
                if (!closingStarted)
                {
                    Require(Directory.Exists(transactionRoot)
                            && transactionLock != null
                            && File.Exists(lockPath),
                        "The auxiliary transaction cannot begin closing from an incomplete state.");
                    CacheTransaction.RequireSafeOwnedTreeForDeletion(transactionRoot);
                    RequireStableRegularFile(journalPath);
                    WriteJournal("closing", true);
                    closingStarted = true;
                }
                else if (Directory.Exists(transactionRoot))
                {
                    CacheTransaction.RequireSafeOwnedTreeForDeletion(transactionRoot);
                    RequireStableRegularFile(journalPath);
                    WriteJournal("closing", true);
                }
                if (transactionLock != null) ReleaseLock();
                else Require(!File.Exists(lockPath),
                    "The auxiliary transaction lock state is inconsistent.");
                if (Directory.Exists(transactionRoot))
                {
                    CacheTransaction.RequireSafeOwnedTreeForDeletion(transactionRoot);
                    DeleteOwnedTransactionTreeWithRetry(transactionRoot);
                }
                Require(!Directory.Exists(transactionRoot)
                        && !File.Exists(transactionRoot)
                        && !File.Exists(lockPath),
                    "The auxiliary transaction journal could not be closed.");
                completed = true;
            }

            internal bool VerifyRestoredBaseline()
            {
                try
                {
                    return AuxiliaryContentEquals(baseline, CaptureAuxiliaryGenerated());
                }
                catch
                {
                    return false;
                }
            }

            internal bool VerifyClosedTerminal()
            {
                return completed
                    && (!prepared || closingStarted)
                    && transactionLock == null
                    && !Directory.Exists(transactionRoot)
                    && !File.Exists(transactionRoot)
                    && !File.Exists(lockPath)
                    && VerifyRestoredBaseline();
            }

            private void DeleteCreatedRoot(TreeSnapshot current)
            {
                Require(createdByOperation && current.Exists && observed != null,
                    "The auxiliary generated root is not owned by this operation.");
                Require(current.Digest == observed.Tree.Digest
                        && current.ContentDigest == observed.Tree.ContentDigest
                        && current.EntryCount == observed.Tree.EntryCount
                        && current.TotalBytes == observed.Tree.TotalBytes,
                    "The operation-created auxiliary generated root changed before cleanup.");
                Require(RootIdentityDigest(current) == ownedRootIdentityDigest,
                    "The operation-created auxiliary generated root identity changed before cleanup.");
                var auxiliaryRoot = AbsoluteProjectPath(AuxiliaryGeneratedRoot);
                var auxiliaryMeta = auxiliaryRoot + ".meta";
                var expectedMeta = current.Entries["$root.meta"];
                AssetDatabase.DeleteAsset(AuxiliaryGeneratedRoot);
                if (Directory.Exists(auxiliaryRoot))
                {
                    Require(RootIdentityDigest(CaptureManagedTree(
                            AuxiliaryGeneratedRoot,
                            AuxiliaryGeneratedTreeSchema + ".delete_check")) == ownedRootIdentityDigest,
                        "The operation-created auxiliary root identity changed during cleanup.");
                    CacheTransaction.RequireSafeOwnedTreeForDeletion(auxiliaryRoot);
                    Directory.Delete(auxiliaryRoot, true);
                }
                if (File.Exists(auxiliaryMeta))
                {
                    RequireStableRegularFile(auxiliaryMeta);
                    var metaIdentity = CaptureIdentity(auxiliaryMeta, false);
                    Require(metaIdentity.Digest == expectedMeta.IdentityDigest
                            && Sha256File(auxiliaryMeta) == expectedMeta.Digest,
                        "The operation-created auxiliary metadata changed during cleanup.");
                    File.Delete(auxiliaryMeta);
                }
                Require(!Directory.Exists(auxiliaryRoot)
                        && !File.Exists(auxiliaryRoot)
                        && !File.Exists(auxiliaryMeta),
                    "The operation-created auxiliary generated root remains after cleanup.");
            }

            private void RestorePresentBaseline(TreeSnapshot current)
            {
                Require(current.Exists && observed != null,
                    "The pre-existing auxiliary generated root is unavailable for restore.");
                Require(current.Digest == observed.Tree.Digest
                        && current.ContentDigest == observed.Tree.ContentDigest
                        && current.EntryCount == observed.Tree.EntryCount
                        && current.TotalBytes == observed.Tree.TotalBytes,
                    "The pre-existing auxiliary generated root changed before restore.");
                Require(RootIdentityDigest(current) == RootIdentityDigest(baseline.Tree),
                    "The pre-existing auxiliary generated root identity changed before restore.");
                var backup = CaptureManagedTreeAbsolute(
                    backupRoot,
                    backupMetaPath,
                    AuxiliaryGeneratedTreeSchema + ".backup_readback");
                Require(ContentEquivalent(baseline.Tree, backup)
                        && backup.EntryCount == baseline.Tree.EntryCount
                        && backup.TotalBytes == baseline.Tree.TotalBytes,
                    "The auxiliary generated backup changed before restore.");
                var auxiliaryRoot = AbsoluteProjectPath(AuxiliaryGeneratedRoot);
                var auxiliaryMeta = auxiliaryRoot + ".meta";
                var assetNames = Directory.EnumerateFileSystemEntries(
                        auxiliaryRoot,
                        "*",
                        SearchOption.TopDirectoryOnly)
                    .Select(Path.GetFileName)
                    .Where(name => !string.IsNullOrWhiteSpace(name))
                    .Select(name => name.EndsWith(".meta", StringComparison.OrdinalIgnoreCase)
                        ? name.Substring(0, name.Length - 5)
                        : name)
                    .Distinct(StringComparer.OrdinalIgnoreCase)
                    .ToArray();
                foreach (var name in assetNames)
                {
                    AssetDatabase.DeleteAsset(AuxiliaryGeneratedRoot + "/" + name);
                }
                foreach (var residue in Directory.EnumerateFileSystemEntries(
                    auxiliaryRoot,
                    "*",
                    SearchOption.TopDirectoryOnly).ToArray())
                {
                    var attributes = File.GetAttributes(residue);
                    var isDirectory = (attributes & FileAttributes.Directory) != 0;
                    var identity = CaptureIdentity(residue, isDirectory);
                    Require(!identity.IsReparsePoint && identity.NumberOfLinks == 1,
                        "The auxiliary restore target contains linked or reparsed residue.");
                    if (isDirectory)
                    {
                        CacheTransaction.RequireSafeOwnedTreeForDeletion(residue);
                        Directory.Delete(residue, true);
                    }
                    else File.Delete(residue);
                }
                Require(!Directory.EnumerateFileSystemEntries(
                        auxiliaryRoot,
                        "*",
                        SearchOption.TopDirectoryOnly).Any(),
                    "The auxiliary restore target could not be emptied.");
                CacheTransaction.CopyTree(backupRoot, auxiliaryRoot);
                RequireStableRegularFile(auxiliaryMeta);
                File.Copy(backupMetaPath, auxiliaryMeta, true);
                RequireStableRegularFile(auxiliaryMeta);
            }

            private void RequirePackageIdentity(AuxiliaryGeneratedSnapshot current)
            {
                Require(current.PackageRootIdentityDigest == baseline.PackageRootIdentityDigest,
                    "The auxiliary package root identity changed during apply.");
                Require(current.PackageManifestIdentityDigest == baseline.PackageManifestIdentityDigest,
                    "The auxiliary package manifest identity changed during apply.");
                Require(current.PackageManifestDigest == baseline.PackageManifestDigest,
                    "The auxiliary package manifest changed during apply.");
            }

            private void ReleaseLock()
            {
                Require(transactionLock != null, "The auxiliary transaction lock is unavailable.");
                transactionLock.Dispose();
                transactionLock = null;
                Require(!File.Exists(lockPath), "The auxiliary transaction lock was not released.");
            }

            private void WriteJournal(string state, bool restoreVerified)
            {
                var payload = new JObject
                {
                    ["schema"] = AuxiliaryJournalSchema,
                    ["journalId"] = JournalId,
                    ["state"] = state,
                    ["packageRoot"] = AuxiliaryPackageRoot,
                    ["packageRootIdentityDigest"] = baseline.PackageRootIdentityDigest,
                    ["packageManifest"] = AuxiliaryPackageManifest,
                    ["packageManifestDigest"] = baseline.PackageManifestDigest,
                    ["packageManifestIdentityDigest"] = baseline.PackageManifestIdentityDigest,
                    ["auxiliaryRoot"] = AuxiliaryGeneratedRoot,
                    ["baselineExists"] = baseline.Tree.Exists,
                    ["baselineTreeDigest"] = baseline.Tree.Digest,
                    ["baselineContentDigest"] = baseline.Tree.ContentDigest,
                    ["baselineEntryCount"] = baseline.Tree.EntryCount,
                    ["baselineByteCount"] = baseline.Tree.TotalBytes,
                    ["observedTreeDigest"] = observed == null ? string.Empty : observed.Tree.Digest,
                    ["ownedRootIdentityDigest"] = ownedRootIdentityDigest,
                    ["createdByOperation"] = createdByOperation,
                    ["restorationMode"] = restorationMode,
                    ["restoreVerified"] = restoreVerified
                };
                var bytes = Encoding.UTF8.GetBytes(payload.ToString(Newtonsoft.Json.Formatting.None));
                PublishTransactionJournal(journalPath, bytes);
            }

            private static string RootIdentityDigest(TreeSnapshot tree)
            {
                Require(tree != null && tree.Exists && tree.Entries.ContainsKey("$root"),
                    "The auxiliary generated root identity is unavailable.");
                return tree.Entries["$root"].IdentityDigest;
            }

            private static bool ContentEquivalent(TreeSnapshot left, TreeSnapshot right)
            {
                if (left == null || right == null || left.Exists != right.Exists) return false;
                const string schema = "vrcforge.parameter_auxiliary_backup_compare.v1";
                return CacheTransaction.ReframeContentDigest(left, schema)
                    == CacheTransaction.ReframeContentDigest(right, schema);
            }
        }

        private sealed class CacheTransaction
        {
            private readonly string privateRoot;
            private readonly string transactionsRoot;
            private readonly string transactionRoot;
            private readonly string backupRoot;
            private readonly string journalPath;
            private readonly string lockPath;
            private readonly TreeSnapshot baseline;
            private readonly string baselineContentDigest;
            private readonly int baselineEntryCount;
            private readonly long baselineByteCount;
            private CreatedAssetFolder createdRoot;
            private TreeSnapshot observed;
            private bool prepared;
            private bool restored;
            private bool closingStarted;
            private bool completed;
            private FileStream transactionLock;

            private CacheTransaction(
                string privateRoot,
                string transactionsRoot,
                string transactionRoot,
                string backupRoot,
                string journalPath,
                TreeSnapshot baseline)
            {
                this.privateRoot = privateRoot;
                this.transactionsRoot = transactionsRoot;
                this.transactionRoot = transactionRoot;
                this.backupRoot = backupRoot;
                this.journalPath = journalPath;
                lockPath = Path.Combine(transactionsRoot, "parameter-bit-packing.lock");
                this.baseline = baseline;
                baselineContentDigest = baseline.ContentDigest;
                baselineEntryCount = baseline.EntryCount;
                baselineByteCount = baseline.TotalBytes;
            }

            internal string JournalId => Path.GetFileName(transactionRoot);
            internal string BaselineContentDigest => baselineContentDigest;
            internal int BaselineEntryCount => baselineEntryCount;
            internal long BaselineByteCount => baselineByteCount;
            internal bool Prepared => prepared;
            internal bool Restored => restored;
            internal bool Completed => completed;

            internal void ObserveMutation(TreeSnapshot expected)
            {
                Require(prepared && !restored && !completed && expected != null && expected.Exists,
                    "The generated cache transaction cannot observe in its current state.");
                var current = CaptureTree(GeneratedRoot, GeneratedTreeSchema, requireExists: true);
                Require(current.Digest == expected.Digest
                        && current.ContentDigest == expected.ContentDigest
                        && current.EntryCount == expected.EntryCount
                        && current.TotalBytes == expected.TotalBytes,
                    "The generated cache changed before its owned observation.");
                if (!baseline.Exists) RequireCreatedCacheRootIdentity(AbsoluteProjectPath(GeneratedRoot));
                observed = current;
                WriteJournal("observed", false);
            }

            internal static CacheTransaction Plan(TreeSnapshot baseline)
            {
                Require(baseline != null, "The generated cache baseline is unavailable.");
                Require(baseline.EntryCount <= CacheBackupMaxEntries, "The generated cache exceeds the backup entry limit.");
                Require(baseline.TotalBytes <= CacheBackupMaxBytes, "The generated cache exceeds the backup byte limit.");
                var project = CurrentProjectPath();
                var library = Path.Combine(project, "Library");
                Require(Directory.Exists(library), "The project Library root is unavailable.");
                RequireSafeDirectory(library, "The project Library root is linked or reparsed.");
                var privateRoot = Path.Combine(library, "VRCForge");
                Require(!File.Exists(privateRoot), "The private transaction root collides with a file.");
                if (Directory.Exists(privateRoot))
                {
                    RequireSafeDirectory(privateRoot, "The private transaction root is linked or reparsed.");
                }
                var transactions = Path.Combine(privateRoot, "transactions");
                Require(!File.Exists(transactions), "The transaction journal root collides with a file.");
                if (Directory.Exists(transactions))
                {
                    RequireSafeDirectory(transactions, "The transaction journal root is linked or reparsed.");
                    Require(!File.Exists(Path.Combine(transactions, "parameter-bit-packing.lock")),
                        "Another parameter cache transaction is active.");
                    Require(!File.Exists(Path.Combine(transactions, "parameter-auxiliary-generated.lock")),
                        "Another auxiliary generated transaction is active.");
                    var unfinished = Directory.EnumerateFileSystemEntries(
                            transactions,
                            "parameter-bit-packing-*",
                            SearchOption.TopDirectoryOnly
                        )
                        .Take(2)
                        .ToArray();
                    Require(unfinished.Length == 0, "An unfinished parameter cache transaction requires checkpoint restore.");
                    var unfinishedAuxiliary = Directory.EnumerateFileSystemEntries(
                            transactions,
                            "parameter-auxiliary-generated-*",
                            SearchOption.TopDirectoryOnly)
                        .Take(2)
                        .ToArray();
                    Require(unfinishedAuxiliary.Length == 0,
                        "An unfinished auxiliary generated transaction requires checkpoint restore.");
                }
                var transactionRoot = Path.Combine(
                    transactions,
                    "parameter-bit-packing-" + Guid.NewGuid().ToString("N"));
                var backupRoot = Path.Combine(transactionRoot, "cache");
                var journalPath = Path.Combine(transactionRoot, "journal.json");
                Require(!Directory.Exists(transactionRoot) && !File.Exists(transactionRoot),
                    "The planned cache transaction path already exists.");
                return new CacheTransaction(
                    privateRoot,
                    transactions,
                    transactionRoot,
                    backupRoot,
                    journalPath,
                    baseline
                );
            }

            internal void Prepare()
            {
                Require(!prepared && !completed, "The cache transaction cannot be prepared in its current state.");
                Directory.CreateDirectory(privateRoot);
                RequireSafeDirectory(privateRoot, "The private transaction root is linked or reparsed.");
                Directory.CreateDirectory(transactionsRoot);
                RequireSafeDirectory(transactionsRoot, "The transaction journal root is linked or reparsed.");
                try
                {
                    transactionLock = new FileStream(
                        lockPath,
                        FileMode.CreateNew,
                        FileAccess.ReadWrite,
                        FileShare.Read,
                        4096,
                        FileOptions.DeleteOnClose | FileOptions.WriteThrough
                    );
                    var lockBytes = Encoding.UTF8.GetBytes(JournalId);
                    transactionLock.Write(lockBytes, 0, lockBytes.Length);
                    transactionLock.Flush(true);
                }
                catch (IOException)
                {
                    throw new ParameterBitPackingException("Another parameter cache transaction is active.");
                }
                Require(
                    !Directory.EnumerateFileSystemEntries(
                        transactionsRoot,
                        "parameter-bit-packing-*",
                        SearchOption.TopDirectoryOnly
                    ).Any(),
                    "An unfinished parameter cache transaction requires checkpoint restore."
                );
                Require(!Directory.Exists(transactionRoot) && !File.Exists(transactionRoot),
                    "The planned cache transaction path already exists.");
                Directory.CreateDirectory(transactionRoot);
                RequireSafeDirectory(transactionRoot, "The cache transaction root is linked or reparsed.");
                Directory.CreateDirectory(backupRoot);
                RequireSafeDirectory(backupRoot, "The cache backup root is linked or reparsed.");
                WriteJournal("preparing", false);
                if (!baseline.Exists)
                {
                    var cacheRoot = AbsoluteProjectPath(GeneratedRoot);
                    Require(!Directory.Exists(cacheRoot) && !File.Exists(cacheRoot) && !File.Exists(cacheRoot + ".meta"),
                        "The absent generated cache baseline changed before apply.");
                    Require(AssetDatabase.IsValidFolder("Packages/com.vrcfury.temp"),
                        "The generated cache parent package is unavailable.");
                    var createdGuid = AssetDatabase.CreateFolder("Packages/com.vrcfury.temp", "Builds");
                    Require(IsGuid(createdGuid), "The generated cache root could not be created.");
                    createdRoot = CaptureCreatedCacheRoot(createdGuid.ToLowerInvariant());
                }
                CopyTree(AbsoluteProjectPath(GeneratedRoot), backupRoot);
                var backup = CaptureTreeAbsolute(backupRoot, CacheContentSchema + ".backup");
                Require(
                    backup.ContentDigest == ReframeContentDigest(baseline, CacheContentSchema + ".backup")
                        && backup.EntryCount == baseline.EntryCount
                        && backup.TotalBytes == baseline.TotalBytes,
                    "The generated cache backup does not match its baseline.");
                WriteJournal("prepared", false);
                prepared = true;
            }

            internal bool Restore(bool allowAuxiliaryRootDirty)
            {
                if (!prepared || completed) return false;
                if (restored) return VerifyRestoredBaseline();
                try
                {
                    if (allowAuxiliaryRootDirty)
                    {
                        RequireNoDirtyProjectAssets(GeneratedRoot, AuxiliaryGeneratedRoot);
                    }
                    else RequireNoDirtyProjectAssets(GeneratedRoot);
                    WriteJournal("restoring", false);
                    var cacheRoot = AbsoluteProjectPath(GeneratedRoot);
                    Require(Directory.Exists(cacheRoot), "The generated cache root is missing during restore.");
                    var restoreTarget = CaptureTreeAbsolute(cacheRoot, CacheContentSchema + ".restore_target");
                    if (!baseline.Exists)
                    {
                        Require(observed != null
                                && restoreTarget.ContentDigest == ReframeContentDigest(observed, CacheContentSchema + ".restore_target")
                                && restoreTarget.EntryCount == observed.EntryCount
                                && restoreTarget.TotalBytes == observed.TotalBytes,
                            "The operation-created generated cache changed after its owned observation.");
                        RequireCreatedCacheRootIdentity(cacheRoot);
                    }
                    Require(restoreTarget.EntryCount <= CacheBackupMaxEntries,
                        "The generated restore target exceeds the bounded entry limit.");
                    Require(restoreTarget.TotalBytes <= CacheBackupMaxBytes,
                        "The generated restore target exceeds the bounded byte limit.");
                    foreach (var entry in Directory.EnumerateFileSystemEntries(cacheRoot, "*", SearchOption.TopDirectoryOnly).ToArray())
                    {
                        var attributes = File.GetAttributes(entry);
                        var isDirectory = (attributes & FileAttributes.Directory) != 0;
                        var identity = CaptureIdentity(entry, isDirectory);
                        Require(!identity.IsReparsePoint && identity.NumberOfLinks == 1, "The generated cache contains a linked or reparsed restore target.");
                    }
                    var assetNames = Directory.EnumerateFileSystemEntries(cacheRoot, "*", SearchOption.TopDirectoryOnly)
                        .Select(Path.GetFileName)
                        .Where(name => !string.IsNullOrWhiteSpace(name))
                        .Select(name => name.EndsWith(".meta", StringComparison.OrdinalIgnoreCase)
                            ? name.Substring(0, name.Length - 5)
                            : name)
                        .Distinct(StringComparer.OrdinalIgnoreCase)
                        .ToArray();
                    foreach (var name in assetNames)
                    {
                        AssetDatabase.DeleteAsset(GeneratedRoot + "/" + name);
                    }
                    foreach (var residue in Directory.EnumerateFileSystemEntries(cacheRoot, "*", SearchOption.TopDirectoryOnly).ToArray())
                    {
                        if (Directory.Exists(residue)) Directory.Delete(residue, true);
                        else File.Delete(residue);
                    }
                    Require(!Directory.EnumerateFileSystemEntries(cacheRoot, "*", SearchOption.TopDirectoryOnly).Any(),
                        "The generated cache could not be emptied for exact restore.");
                    CopyTree(backupRoot, cacheRoot);
                    if (!baseline.Exists) DeleteCreatedCacheRoot(cacheRoot);
                    if (allowAuxiliaryRootDirty)
                    {
                        RequireNoDirtyProjectAssets(GeneratedRoot, AuxiliaryGeneratedRoot);
                    }
                    else RequireNoDirtyProjectAssets(GeneratedRoot);
                    AssetDatabase.SaveAssets();
                    AssetDatabase.Refresh(ImportAssetOptions.ForceSynchronousImport);
                    var finalSnapshot = CaptureTree(GeneratedRoot, GeneratedTreeSchema, requireExists: baseline.Exists);
                    if (finalSnapshot.Exists != baseline.Exists
                        || finalSnapshot.ContentDigest != baselineContentDigest
                        || finalSnapshot.EntryCount != baselineEntryCount
                        || finalSnapshot.TotalBytes != baselineByteCount)
                    {
                        WriteJournal("restore_mismatch", false);
                        return false;
                    }
                    WriteJournal("restored", true);
                    restored = true;
                    return true;
                }
                catch
                {
                    try { WriteJournal("restore_failed", false); } catch { }
                    return false;
                }
            }

            internal bool AbortPreparation()
            {
                if (prepared) return false;
                if (completed) return true;
                try
                {
                    if (!baseline.Exists && (Directory.Exists(AbsoluteProjectPath(GeneratedRoot))
                        || File.Exists(AbsoluteProjectPath(GeneratedRoot) + ".meta")))
                    {
                        var incompleteRoot = CaptureTree(GeneratedRoot, GeneratedTreeSchema, requireExists: true);
                        Require(incompleteRoot.EntryCount == 0 && incompleteRoot.TotalBytes == 0,
                            "The incomplete operation-created generated cache is not empty.");
                        DeleteCreatedCacheRoot(AbsoluteProjectPath(GeneratedRoot));
                    }
                    if (Directory.Exists(transactionRoot))
                    {
                        RequireSafeDirectory(transactionRoot, "The incomplete cache transaction root is linked or reparsed.");
                        RequireSafeOwnedTreeForDeletion(transactionRoot);
                        DeleteOwnedTransactionTreeWithRetry(transactionRoot);
                    }
                    Require(!Directory.Exists(transactionRoot) && !File.Exists(transactionRoot),
                        "The incomplete cache transaction could not be removed.");
                    if (transactionLock != null) ReleaseLock();
                    completed = true;
                    return true;
                }
                catch
                {
                    return false;
                }
            }

            internal void Complete()
            {
                if (completed)
                {
                    Require(VerifyClosedTerminal(),
                        "The cache transaction closed state is inconsistent.");
                    return;
                }
                Require(prepared && restored, "The cache transaction cannot be closed in its current state.");
                if (!closingStarted)
                {
                    Require(Directory.Exists(transactionRoot)
                            && transactionLock != null
                            && File.Exists(lockPath),
                        "The cache transaction cannot begin closing from an incomplete state.");
                    RequireSafeOwnedTreeForDeletion(transactionRoot);
                    RequireStableRegularFile(journalPath);
                    WriteJournal("closing", true);
                    closingStarted = true;
                }
                else if (Directory.Exists(transactionRoot))
                {
                    RequireSafeOwnedTreeForDeletion(transactionRoot);
                    RequireStableRegularFile(journalPath);
                    WriteJournal("closing", true);
                }
                if (transactionLock != null) ReleaseLock();
                else Require(!File.Exists(lockPath),
                    "The cache transaction lock state is inconsistent.");
                if (Directory.Exists(transactionRoot))
                {
                    RequireSafeOwnedTreeForDeletion(transactionRoot);
                    DeleteOwnedTransactionTreeWithRetry(transactionRoot);
                }
                Require(!Directory.Exists(transactionRoot)
                        && !File.Exists(transactionRoot)
                        && !File.Exists(lockPath),
                    "The cache transaction journal could not be closed.");
                completed = true;
            }

            internal bool VerifyRestoredBaseline()
            {
                try
                {
                    var current = CaptureTree(GeneratedRoot, GeneratedTreeSchema, requireExists: baseline.Exists);
                    return current.Exists == baseline.Exists
                        && current.ContentDigest == baselineContentDigest
                        && current.EntryCount == baselineEntryCount
                        && current.TotalBytes == baselineByteCount;
                }
                catch
                {
                    return false;
                }
            }

            internal bool VerifyClosedTerminal()
            {
                return completed
                    && (!prepared || closingStarted)
                    && transactionLock == null
                    && !Directory.Exists(transactionRoot)
                    && !File.Exists(transactionRoot)
                    && !File.Exists(lockPath)
                    && VerifyRestoredBaseline();
            }

            private void ReleaseLock()
            {
                Require(transactionLock != null, "The cache transaction lock is unavailable.");
                transactionLock.Dispose();
                transactionLock = null;
                Require(!File.Exists(lockPath), "The cache transaction lock was not released.");
            }

            internal static void RequireSafeOwnedTreeForDeletion(string root)
            {
                var rootIdentity = CaptureIdentity(root, true);
                Require(!rootIdentity.IsReparsePoint && rootIdentity.NumberOfLinks == 1,
                    "The incomplete cache transaction root is linked or reparsed.");
                var entryCount = 0;
                var pending = new Stack<string>();
                pending.Push(root);
                while (pending.Count > 0)
                {
                    var current = pending.Pop();
                    foreach (var entry in Directory.EnumerateFileSystemEntries(current, "*", SearchOption.TopDirectoryOnly))
                    {
                        entryCount++;
                        Require(entryCount <= CacheBackupMaxEntries + 16,
                            "The incomplete cache transaction exceeds the bounded cleanup limit.");
                        var attributes = File.GetAttributes(entry);
                        var isDirectory = (attributes & FileAttributes.Directory) != 0;
                        var identity = CaptureIdentity(entry, isDirectory);
                        Require(!identity.IsReparsePoint && identity.NumberOfLinks == 1,
                            "The incomplete cache transaction contains a linked or reparsed path.");
                        if (isDirectory) pending.Push(entry);
                    }
                }
                var finalRootIdentity = CaptureIdentity(root, true);
                Require(
                    finalRootIdentity.VolumeSerial == rootIdentity.VolumeSerial
                        && finalRootIdentity.FileIndexHigh == rootIdentity.FileIndexHigh
                        && finalRootIdentity.FileIndexLow == rootIdentity.FileIndexLow,
                    "The incomplete cache transaction root identity changed before cleanup."
                );
            }

            private void WriteJournal(string state, bool cacheRestored)
            {
                var payload = new JObject
                {
                    ["schema"] = CacheJournalSchema,
                    ["journalId"] = JournalId,
                    ["state"] = state,
                    ["cacheRoot"] = GeneratedRoot,
                    ["baselineRootExists"] = baseline.Exists,
                    ["createdRootGuid"] = createdRoot == null ? "" : createdRoot.Guid,
                    ["createdRootIdentityDigest"] = createdRoot == null ? "" : createdRoot.DirectoryIdentityDigest,
                    ["createdRootMetaIdentityDigest"] = createdRoot == null ? "" : createdRoot.MetaIdentityDigest,
                    ["createdRootMetaDigest"] = createdRoot == null ? "" : createdRoot.MetaDigest,
                    ["observedTreeDigest"] = observed == null ? "" : observed.Digest,
                    ["observedContentDigest"] = observed == null ? "" : observed.ContentDigest,
                    ["observedEntryCount"] = observed == null ? 0 : observed.EntryCount,
                    ["observedByteCount"] = observed == null ? 0 : observed.TotalBytes,
                    ["baselineContentDigest"] = baselineContentDigest,
                    ["baselineEntryCount"] = baselineEntryCount,
                    ["baselineByteCount"] = baselineByteCount,
                    ["cacheRestored"] = cacheRestored
                };
                var bytes = Encoding.UTF8.GetBytes(payload.ToString(Newtonsoft.Json.Formatting.None));
                PublishTransactionJournal(journalPath, bytes);
            }

            private static CreatedAssetFolder CaptureCreatedCacheRoot(string expectedGuid)
            {
                var absolute = AbsoluteProjectPath(GeneratedRoot);
                Require(Directory.Exists(absolute), "The operation-created generated cache root is missing.");
                var rootIdentity = CaptureIdentity(absolute, true);
                Require(!rootIdentity.IsReparsePoint && rootIdentity.NumberOfLinks == 1,
                    "The operation-created generated cache root is linked or reparsed.");
                var metaPath = absolute + ".meta";
                RequireStableRegularFile(metaPath);
                var metaIdentity = CaptureIdentity(metaPath, false);
                Require(AssetDatabase.AssetPathToGUID(GeneratedRoot).ToLowerInvariant() == expectedGuid
                        && ParseMetaGuid(ReadStableFileBytes(metaPath)) == expectedGuid,
                    "The operation-created generated cache root GUID is inconsistent.");
                return new CreatedAssetFolder
                {
                    AssetPath = GeneratedRoot,
                    Guid = expectedGuid,
                    DirectoryIdentityDigest = rootIdentity.Digest,
                    MetaIdentityDigest = metaIdentity.Digest,
                    MetaDigest = Sha256File(metaPath)
                };
            }

            private void DeleteCreatedCacheRoot(string cacheRoot)
            {
                RequireCreatedCacheRootIdentity(cacheRoot);
                var metaPath = cacheRoot + ".meta";
                Require(AssetDatabase.DeleteAsset(GeneratedRoot),
                    "The operation-created generated cache root could not be removed.");
                Require(!Directory.Exists(cacheRoot) && !File.Exists(cacheRoot) && !File.Exists(metaPath),
                    "The operation-created generated cache root remains after cleanup.");
            }

            private void RequireCreatedCacheRootIdentity(string cacheRoot)
            {
                Require(createdRoot != null && createdRoot.AssetPath == GeneratedRoot,
                    "The generated cache root ownership evidence is unavailable.");
                Require(CaptureIdentity(cacheRoot, true).Digest == createdRoot.DirectoryIdentityDigest,
                    "The operation-created generated cache root identity changed before cleanup.");
                var metaPath = cacheRoot + ".meta";
                RequireStableRegularFile(metaPath);
                Require(CaptureIdentity(metaPath, false).Digest == createdRoot.MetaIdentityDigest
                        && Sha256File(metaPath) == createdRoot.MetaDigest
                        && AssetDatabase.AssetPathToGUID(GeneratedRoot).ToLowerInvariant() == createdRoot.Guid,
                    "The operation-created generated cache metadata changed before cleanup.");
            }

            internal static void CopyTree(string sourceRoot, string destinationRoot)
            {
                Require(Directory.Exists(sourceRoot), "A cache copy source is missing.");
                Directory.CreateDirectory(destinationRoot);
                RequireSafeDirectory(sourceRoot, "A cache copy source root is linked or reparsed.");
                RequireSafeDirectory(destinationRoot, "A cache copy destination root is linked or reparsed.");
                Require(!Directory.EnumerateFileSystemEntries(destinationRoot, "*", SearchOption.TopDirectoryOnly).Any(),
                    "A cache copy destination is not empty.");
                var entryCount = 0;
                var byteCount = 0L;
                var pending = new Stack<string>();
                pending.Push(sourceRoot);
                while (pending.Count > 0)
                {
                    var current = pending.Pop();
                    foreach (var source in Directory.EnumerateFileSystemEntries(current, "*", SearchOption.TopDirectoryOnly)
                        .OrderByDescending(path => path, StringComparer.OrdinalIgnoreCase))
                    {
                        var attributes = File.GetAttributes(source);
                        var isDirectory = (attributes & FileAttributes.Directory) != 0;
                        var identity = CaptureIdentity(source, isDirectory);
                        Require(!identity.IsReparsePoint && identity.NumberOfLinks == 1,
                            "A cache copy source path is linked or reparsed.");
                        entryCount++;
                        Require(entryCount <= CacheBackupMaxEntries, "A cache copy exceeds the bounded entry limit.");
                        var destination = Path.Combine(destinationRoot, RelativePath(sourceRoot, source));
                        if (isDirectory)
                        {
                            Directory.CreateDirectory(destination);
                            RequireSafeDirectory(destination, "A cache copy destination directory is linked or reparsed.");
                            pending.Push(source);
                            continue;
                        }
                        var length = new FileInfo(source).Length;
                        Require(length >= 0 && byteCount <= CacheBackupMaxBytes - length,
                            "A cache copy exceeds the bounded byte limit.");
                        byteCount += length;
                        Directory.CreateDirectory(Path.GetDirectoryName(destination));
                        using (var input = new FileStream(source, FileMode.Open, FileAccess.Read, FileShare.Read))
                        using (var output = new FileStream(destination, FileMode.CreateNew, FileAccess.Write, FileShare.None))
                        {
                            Require(input.Length == length, "A cache copy source changed before it was read.");
                            input.CopyTo(output);
                            Require(output.Position == length, "A cache copy was truncated.");
                            output.Flush(true);
                        }
                        RequireStableRegularFile(destination);
                    }
                }
            }

            internal static void RequireSafeDirectory(string path, string message)
            {
                Require(Directory.Exists(path), message);
                var identity = CaptureIdentity(path, true);
                Require(!identity.IsReparsePoint && identity.NumberOfLinks == 1, message);
            }

            internal static string ReframeContentDigest(TreeSnapshot snapshot, string schema)
            {
                return Sha256Utf8(
                    schema + ".content\n"
                    + string.Concat(snapshot.Entries.Values.Select(entry => entry.ContentReceipt)));
            }
        }

        private sealed class StableInputLeases : IDisposable
        {
            internal StableInputLeases(IReadOnlyList<string> paths, IReadOnlyList<IDisposable> leases)
            {
                Paths = paths;
                Leases = leases;
            }
            internal IReadOnlyList<string> Paths { get; }
            private IReadOnlyList<IDisposable> Leases { get; }
            public void Dispose()
            {
                foreach (var lease in Leases.Reverse()) lease.Dispose();
            }
        }
    }
}
