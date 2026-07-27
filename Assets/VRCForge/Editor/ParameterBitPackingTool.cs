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
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Tools;
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

namespace VRCForge.Editor
{
    [McpForUnityTool(
        name: "vrc_build_parameter_bit_packed_clone",
        Description = "Preview or build one verified parameter-packed avatar clone through the public avatar preprocess pipeline."
    )]
    public static class ParameterBitPackingTool
    {
        private const string ResultSchema = "vrcforge.parameter_bit_packing.v2";
        private const string PackageTreeSchema = "vrcforge.package_tree.v1";
        private const string GeneratedTreeSchema = "vrcforge.generated_tree.v1";
        private const string OutputTreeSchema = "vrcforge.parameter_output_tree.v1";
        private const string ProtectedTreeSchema = "vrcforge.protected_project_tree.v1";
        private const string RootIdentitySchema = "vrcforge.parameter_project_roots.v1";
        private const string ApplyReceiptSchema = "vrcforge.parameter_bit_packing_apply_receipt.v2";
        private const string SafeNamesSchema = "vrcforge.safe_parameter_names.v1";
        private const string CompressedNamesSchema = "vrcforge.compressed_parameter_names.v1";
        private const string ExcludedSchema = "vrcforge.excluded_parameters.v1";
        private const string CacheJournalSchema = "vrcforge.parameter_cache_journal.v1";
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
        private const string CallbackAssemblySha256 = "e568293abe29428b7fb35d805cb3053cc8437621a19ae714d5fc76931d9fe10f";
        private const string SdkCallbackAssemblyName = "VRCSDKBase-Editor";
        private const string SdkCallbackAssemblyVersion = "1.0.0.0";
        private const string SdkCallbackAssemblyPublicKeyToken = "";
        private const string SdkCallbackAssemblySha256 = "952abdd2e9f696acba1fa773402d824fac4f0c6dd0b1b3488df8e4a3d870eba9";
        private const string CallbackTypeName = "VRC.SDKBase.Editor.BuildPipeline.VRCBuildPipelineCallbacks";
        private const string CallbackSignature = "public static System.Boolean OnPreprocessAvatar(UnityEngine.GameObject)";
        private const string RegisteredHookType = "VF.Hooks.ParameterCompressorHook";
        private const int CallbackRosterCount = 16;
        private const string CallbackRosterDigest = "305bc43e713cc76fe13f16d99e6e1d7137d87c066d6a46a6917196b909de10ba";
        private const string GeneratedRoot = "Packages/com.vrcfury.temp/Builds";
        private const string StagingFolderName = "VRCForge Input";
        private const string StagingRoot = GeneratedRoot + "/" + StagingFolderName;
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
            "expectedGeneratedTreeDigestBefore",
            "expectedGeneratedEntryCountBefore",
            "expectedGeneratedContentDigestBefore",
            "expectedGeneratedByteCountBefore",
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
            TreeSnapshot beforeOutput = null;
            TreeSnapshot beforeProtected = null;
            RootIdentitySnapshot beforeRoots = null;
            StableInputLeases stableInputLeases = null;
            StableInputLeases stableOutputLeases = null;
            CacheTransaction cacheTransaction = null;
            AssetTreeManifest outputManifest = null;
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
                beforeGenerated = CaptureTree(GeneratedRoot, GeneratedTreeSchema, requireExists: true);
                operationStage = "output_tree_capture";
                beforeOutput = CaptureManagedTree(OutputRoot, OutputTreeSchema);
                operationStage = "root_identity_capture";
                beforeRoots = CaptureRootIdentities();
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
                    beforeOutput,
                    beforeProtected,
                    beforeRoots,
                    outputPreview,
                    preferences
                );

                if (preview)
                {
                    operationStage = "preview_response";
                    return new SuccessResponse(
                        "Parameter bit-packing preview completed.",
                        BuildPreviewPayload(
                            beforeSource,
                            capability,
                            beforeGenerated,
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
                    beforeOutput,
                    beforeProtected,
                    beforeRoots,
                    outputPreview,
                    preferences,
                    previewDigest
                );

                stableInputLeases = HoldStableInputs(beforeSource, capability, beforeOutput, beforeProtected, beforeRoots);
                {
                    var leases = stableInputLeases;
                    operationStage = "stable_input_verification";
                    VerifyStableInputs(beforeSource, capability, beforeOutput, beforeProtected, beforeRoots, leases);
                    Require(CapturePreferences().ReceiptDigest == preferences.ReceiptDigest, "A parameter build preference changed after preview.");
                    operationStage = "project_cleanliness_recheck";
                    RequireNoDirtyProjectAssets();
                    operationStage = "cache_transaction_prepare";
                    cacheTransaction = CacheTransaction.Create(beforeGenerated);
                    mutationStarted = true;
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
                    PrepareCloneAssets(clone);
                    VerifyStableInputs(beforeSource, capability, beforeOutput, beforeProtected, beforeRoots, leases);

                    operationStage = "public_preprocess";
                    var callbacksOk = VRCBuildPipelineCallbacks.OnPreprocessAvatar(clone);
                    Require(callbacksOk, "The public avatar preprocess pipeline rejected the clone.");
                    RequireNoDirtyProjectAssets(GeneratedRoot);
                    AssetDatabase.SaveAssets();
                    AssetDatabase.Refresh(ImportAssetOptions.ForceSynchronousImport);

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
                    var callbackRoots = CaptureRootIdentities();
                    Require(callbackRoots.Digest == beforeRoots.Digest && callbackRoots.EntryCount == beforeRoots.EntryCount, "A project root identity changed during clone preprocessing.");
                    operationStage = "generated_scope_verification";
                    var callbackGenerated = CaptureTree(GeneratedRoot, GeneratedTreeSchema, requireExists: true);
                    var callbackDelta = CompareGeneratedTrees(beforeGenerated, callbackGenerated);
                    Require(callbackDelta.Added.Count > 0, "The public preprocess pipeline produced no generated assets.");
                    RequireGeneratedSubtree(callbackDelta.Added, outputCloneName);

                    operationStage = "output_verification";
                    var cloneDescriptor = clone.GetComponent<VRCAvatarDescriptor>();
                    Require(cloneDescriptor != null, "The output clone has no avatar descriptor.");
                    var outputParameters = RequireOutputParameters(cloneDescriptor);
                    var outputState = CaptureParameterState(outputParameters, beforeSource.MenuUsage);
                    var compressedNames = VerifyOutputParameters(beforeSource.ParameterState, outputState);
                    Require(outputState.CostBits <= 256, "The output clone remains above the synchronized parameter budget.");
                    Require(outputState.CostBits < beforeSource.ParameterState.CostBits, "The output clone did not reduce synchronized parameter cost.");
                    Require(compressedNames.Count > 0, "The output clone did not compress an approved parameter.");
                    var cloneEvidence = ParameterBitPackingEvidence.Capture(clone);
                    var behaviorProof = ParameterBitPackingEvidence.VerifyBehavior(
                        beforeSource.BehaviorEvidence,
                        cloneEvidence,
                        compressedNames,
                        beforeSource.ParameterState.Excluded.Select(item => item.Name).ToArray());
                    Require(behaviorProof.PlatformScope == "current-target-only", "The behavior proof claimed unsupported cross-platform equivalence.");
                    var cloneParameterStateDigest = outputState.StateDigest;
                    var temporaryOutputRoot = GeneratedRoot + "/" + outputCloneName;
                    var durableOutputRoot = OutputKindRoot + "/" + outputCloneName;
                    var temporaryPrefabPath = temporaryOutputRoot + "/" + outputCloneName + ".prefab";
                    Require(AssetDatabase.IsValidFolder(temporaryOutputRoot), "The processed temporary output subtree is missing.");
                    operationStage = "temporary_output_prefab_save";
                    Require(AssetDatabase.LoadAssetAtPath<GameObject>(temporaryPrefabPath) == null, "The temporary output prefab already exists.");
                    var stagedPrefab = PrefabUtility.SaveAsPrefabAsset(clone, temporaryPrefabPath, out var stagedPrefabSaved);
                    Require(stagedPrefabSaved && stagedPrefab != null, "The processed clone could not be saved inside its temporary output subtree.");
                    RequireNoDirtyProjectAssets(GeneratedRoot);
                    AssetDatabase.SaveAssets();
                    AssetDatabase.Refresh(ImportAssetOptions.ForceSynchronousImport);
                    var stagedReceipt = CaptureOutputPrefab(
                        temporaryPrefabPath,
                        outputCloneName,
                        beforeSource.MenuUsage,
                        cloneEvidence,
                        behaviorProof,
                        beforeSource.BehaviorEvidence,
                        compressedNames,
                        beforeSource.ParameterState.Excluded.Select(item => item.Name).ToArray());
                    var stagedManifest = CaptureAssetTreeManifest(temporaryOutputRoot, temporaryPrefabPath, requireNoTemporaryReferences: false);

                    operationStage = "persistent_output_move";
                    EnsureAssetFolder(OutputKindRoot);
                    Require(!AssetDatabase.IsValidFolder(durableOutputRoot), "The approved durable output subtree already exists.");
                    var moveError = AssetDatabase.MoveAsset(temporaryOutputRoot, durableOutputRoot);
                    Require(string.IsNullOrWhiteSpace(moveError), "The processed output subtree could not be moved into managed project assets.");
                    RequireNoDirtyProjectAssets(GeneratedRoot, durableOutputRoot);
                    AssetDatabase.SaveAssets();
                    AssetDatabase.Refresh(ImportAssetOptions.ForceSynchronousImport);
                    Require(!AssetDatabase.IsValidFolder(temporaryOutputRoot), "The temporary processed output subtree remained after migration.");
                    Require(AssetDatabase.IsValidFolder(durableOutputRoot), "The durable processed output subtree is missing after migration.");
                    var movedManifest = CaptureAssetTreeManifest(durableOutputRoot, outputPreview.PrefabPath, requireNoTemporaryReferences: true);
                    VerifyGuidPreservingMove(stagedManifest, movedManifest);
                    outputManifest = movedManifest;
                    operationStage = "persistent_output_scope_verification";
                    var afterGenerated = CaptureTree(GeneratedRoot, GeneratedTreeSchema, requireExists: true);
                    var generatedDelta = CompareGeneratedTrees(beforeGenerated, afterGenerated);
                    Require(!AssetDatabase.IsValidFolder(StagingRoot) && !AssetDatabase.IsValidFolder(temporaryOutputRoot), "The package temporary build root contains operation residue.");
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

                    operationStage = "cache_restore";
                    Require(cacheTransaction.Restore(), "The dependency cache could not be restored exactly.");
                    var restoredGenerated = CaptureTree(GeneratedRoot, GeneratedTreeSchema, requireExists: true);
                    Require(restoredGenerated.ContentDigest == beforeGenerated.ContentDigest
                        && restoredGenerated.EntryCount == beforeGenerated.EntryCount
                        && restoredGenerated.TotalBytes == beforeGenerated.TotalBytes,
                        "The dependency cache differs from its approved baseline.");
                    afterGenerated = restoredGenerated;
                    generatedDelta = CompareGeneratedTrees(beforeGenerated, afterGenerated);
                    temporaryDeltaDigest = ComputeTreeDeltaDigest(generatedDelta, "vrcforge.parameter_temporary_delta.v1");

                    operationStage = "final_input_verification";
                    VerifyStableInputs(beforeSource, capability, beforeOutput, beforeProtected, beforeRoots, leases, verifyOutputTree: false);
                    afterSource = CaptureSource(sourceScenePath, sourceAvatarPath);
                    Require(afterSource.SourceStateDigest == beforeSource.SourceStateDigest, "The source avatar changed before final readback.");
                    var afterCapability = CaptureCapability();
                    Require(afterCapability.CapabilityDigest == capability.CapabilityDigest, "The package capability changed before final readback.");
                    var afterPreferences = CapturePreferences();
                    Require(afterPreferences.ReceiptDigest == preferences.ReceiptDigest, "A parameter build preference changed during apply.");
                    var afterProtected = CaptureProtectedTree();
                    Require(afterProtected.Digest == beforeProtected.Digest && afterProtected.EntryCount == beforeProtected.EntryCount, "The persistent output escaped the generated build root.");
                    var afterRoots = CaptureRootIdentities();
                    Require(afterRoots.Digest == beforeRoots.Digest && afterRoots.EntryCount == beforeRoots.EntryCount, "A project root identity changed before final readback.");

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
                        stagedManifest,
                        finalManifest,
                        preferences,
                        cacheTransaction,
                        sceneLoadedAfter,
                        temporaryObjectResidue,
                        beforeGenerated,
                        afterGenerated,
                        generatedDelta,
                        temporaryDeltaDigest,
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
                    cacheTransaction.Complete();
                    return new SuccessResponse(
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
                                stagedManifest = stagedManifest.ToPayload(),
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
                var restored = TryCleanupFailure(outputScene, beforeGenerated, beforeOutput, beforeProtected, beforeRoots, beforeSource, outputCloneName, cacheTransaction, outputManifest);
                var reason = exception is ParameterBitPackingException
                    ? " " + exception.Message
                    : string.Empty;
                return new ErrorResponse(
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
                return new ErrorResponse(exception.Message);
            }
            catch (Exception exception)
            {
                return new ErrorResponse(
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
                    exists = true,
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

        private static void ValidateApplyPreconditions(
            JObject request,
            SourceSnapshot source,
            CapabilitySnapshot capability,
            TreeSnapshot generated,
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
            Require(ReadExpectedString(request, "expectedGeneratedTreeDigestBefore") == generated.Digest, "The generated build root changed after preview.");
            Require(ReadExpectedInt(request, "expectedGeneratedEntryCountBefore") == generated.EntryCount, "The generated build root count changed after preview.");
            Require(ReadExpectedString(request, "expectedGeneratedContentDigestBefore") == generated.ContentDigest, "The generated build root content changed after preview.");
            Require(ReadExpectedLong(request, "expectedGeneratedByteCountBefore") == generated.TotalBytes, "The generated build root byte count changed after preview.");
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
            Require(callbackHash == CallbackAssemblySha256, "The package callback assembly bytes are not allowlisted.");

            var sdkAssembly = typeof(VRCBuildPipelineCallbacks).Assembly;
            var sdkName = sdkAssembly.GetName();
            Require(sdkName.Name == SdkCallbackAssemblyName && sdkName.Version.ToString() == SdkCallbackAssemblyVersion, "The public callback assembly identity is not allowlisted.");
            Require(PublicKeyToken(sdkName) == SdkCallbackAssemblyPublicKeyToken, "The public callback assembly signature state is not allowlisted.");
            Require(Sha256File(sdkAssembly.Location) == SdkCallbackAssemblySha256, "The public callback assembly bytes are not allowlisted.");
            var callbackMethod = typeof(VRCBuildPipelineCallbacks).GetMethod(
                "OnPreprocessAvatar",
                BindingFlags.Public | BindingFlags.Static,
                null,
                new[] { typeof(GameObject) },
                null
            );
            Require(callbackMethod != null && callbackMethod.ReturnType == typeof(bool), "The public avatar callback signature is unavailable.");
            var registered = TypeCache.GetTypesDerivedFrom<IVRCSDKPreprocessAvatarCallback>()
                .Where(type => type.FullName == RegisteredHookType && type.Assembly.GetName().Name == CallbackAssemblyName)
                .ToArray();
            Require(registered.Length == 1 && !registered[0].IsAbstract, "The package compressor hook registration is not allowlisted.");
            var callbackRoster = TypeCache.GetTypesDerivedFrom<IVRCSDKPreprocessAvatarCallback>()
                .Where(type => !type.IsAbstract)
                .Select(type => type.Assembly.GetName().Name + ":" + type.FullName)
                .OrderBy(value => value, StringComparer.Ordinal)
                .ToArray();
            var callbackRosterDigest = Sha256Framed(
                "vrcforge.avatar_callback_roster.v1",
                callbackRoster.Cast<object>().ToArray()
            );
            Require(
                callbackRoster.Length == CallbackRosterCount && callbackRosterDigest == CallbackRosterDigest,
                "The avatar preprocess callback roster is not allowlisted."
            );

            var snapshot = new CapabilitySnapshot
            {
                PackageRootPath = packageRoot,
                PackageRootIdentityDigest = rootIdentity.Digest,
                CallbackAssemblyPath = callbackAssembly.Location,
                CallbackAssemblySha256 = callbackHash,
                SdkCallbackAssemblyPath = sdkAssembly.Location
            };
            snapshot.CapabilityDigest = Sha256Framed(
                "vrcforge.parameter_capability.v1",
                PackageId,
                PackageVersion,
                PackageAuthor,
                PackageArchiveSha256,
                PackageTreeSha256,
                PackageFileCount,
                snapshot.PackageRootIdentityDigest,
                CallbackAssemblyName,
                CallbackAssemblyVersion,
                CallbackAssemblyPublicKeyToken,
                CallbackAssemblySha256,
                SdkCallbackAssemblyName,
                SdkCallbackAssemblyVersion,
                SdkCallbackAssemblyPublicKeyToken,
                SdkCallbackAssemblySha256,
                CallbackTypeName,
                CallbackSignature,
                RegisteredHookType,
                1,
                CallbackRosterCount,
                CallbackRosterDigest
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

        private static void PrepareCloneAssets(GameObject clone)
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
            RequireNoDirtyProjectAssets(StagingRoot);
            AssetDatabase.SaveAssets();
            AssetDatabase.Refresh(ImportAssetOptions.ForceSynchronousImport);
            Require(IsStagedAsset(descriptor.expressionParameters), "The clone expression parameters escaped the generated input staging root.");
            Require(IsStagedAsset(descriptor.expressionsMenu), "The clone expression menu escaped the generated input staging root.");
        }

        private static void EnsureAssetFolder(string assetPath)
        {
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
                    Require(!string.IsNullOrWhiteSpace(AssetDatabase.CreateFolder(current, parts[index])), "A managed output folder could not be created.");
                }
                var identity = CaptureIdentity(AbsoluteProjectPath(next), true);
                Require(!identity.IsReparsePoint && identity.NumberOfLinks == 1, "A managed output folder is linked or reparsed.");
                current = next;
            }
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

        private static RootIdentitySnapshot CaptureRootIdentities()
        {
            var entries = new SortedDictionary<string, FileIdentity>(StringComparer.Ordinal);
            foreach (var root in RequiredRootPaths())
            {
                Require(Directory.Exists(root.Value), "A required project root is missing.");
                var identity = CaptureIdentity(root.Value, true);
                Require(!identity.IsReparsePoint && identity.NumberOfLinks == 1, "A required project root is linked or reparsed.");
                entries.Add(root.Key, identity);
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
                new KeyValuePair<string, string>(GeneratedRoot, AbsoluteProjectPath(GeneratedRoot))
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
            if (requireExists) Require(Directory.Exists(absolute), "The package generated build root is missing.");
            return CaptureTreeAbsolute(absolute, schema);
        }

        private static TreeSnapshot CaptureManagedTree(string assetPath, string schema)
        {
            var absolute = AbsoluteProjectPath(assetPath);
            var meta = absolute + ".meta";
            if (!Directory.Exists(absolute))
            {
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
            IReadOnlyCollection<string> excludedNames)
        {
            var temporaryPath = GeneratedRoot + "/" + expectedName + "/" + expectedName + ".prefab";
            var durablePath = OutputKindRoot + "/" + expectedName + "/" + expectedName + ".prefab";
            Require(prefabPath == temporaryPath || prefabPath == durablePath, "The persisted output prefab path is invalid.");
            var absolute = AbsoluteProjectPath(prefabPath);
            var meta = absolute + ".meta";
            RequireStableRegularFile(absolute);
            RequireStableRegularFile(meta);
            var prefab = AssetDatabase.LoadAssetAtPath<GameObject>(prefabPath);
            Require(prefab != null && EditorUtility.IsPersistent(prefab), "The persisted output prefab is unavailable.");
            Require(AssetDatabase.GetAssetPath(prefab) == prefabPath, "The persisted output prefab resolved to another path.");
            Require(prefab.name == expectedName, "The persisted output prefab root changed name.");
            Require(PrefabUtility.GetPrefabAssetType(prefab) != PrefabAssetType.NotAPrefab, "The persisted output asset is not a prefab.");
            var descriptor = prefab.GetComponent<VRCAvatarDescriptor>();
            Require(descriptor != null, "The persisted output prefab has no avatar descriptor.");
            var parameterState = CaptureParameterState(RequireOutputParameters(descriptor), menuUsage);
            var evidence = ParameterBitPackingEvidence.Capture(prefab);
            Require(evidence.PortableAvatarDigest == expectedOutputEvidence.PortableAvatarDigest, "The persisted output portable avatar projection changed.");
            Require(evidence.OrderedParameterDigest == expectedOutputEvidence.OrderedParameterDigest, "The persisted output parameter order changed.");
            Require(evidence.MenuGraphDigest == expectedOutputEvidence.MenuGraphDigest, "The persisted output menu graph changed.");
            Require(evidence.AnimatorBehaviorDigest == expectedOutputEvidence.AnimatorBehaviorDigest, "The persisted output animator behavior changed.");
            Require(evidence.ReceiptDigest == expectedOutputEvidence.ReceiptDigest, "The persisted output semantic evidence changed.");
            var readbackProof = ParameterBitPackingEvidence.VerifyBehavior(
                sourceEvidence,
                evidence,
                compressedNames,
                excludedNames);
            Require(readbackProof.ReceiptDigest == expectedBehaviorProof.ReceiptDigest, "The persisted output behavior proof changed.");
            var guid = AssetDatabase.AssetPathToGUID(prefabPath).ToLowerInvariant();
            Require(IsGuid(guid), "The persisted output prefab GUID is invalid.");
            var globalObjectId = GlobalObjectId.GetGlobalObjectIdSlow(prefab).ToString();
            Require(!string.IsNullOrWhiteSpace(globalObjectId), "The persisted output prefab identity is unavailable.");
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

        private static void VerifyGuidPreservingMove(AssetTreeManifest staged, AssetTreeManifest final)
        {
            Require(staged != null && final != null, "The output migration manifest is incomplete.");
            Require(staged.EntryCount == final.EntryCount, "The output migration changed the asset tree count.");
            Require(staged.TotalBytes == final.TotalBytes, "The output migration changed the asset tree size.");
            Require(staged.ContentDigest == final.ContentDigest, "The output migration changed asset bytes.");
            Require(staged.HandleEvidenceDigest == final.HandleEvidenceDigest, "The output migration changed file identities.");
            Require(staged.GuidMapDigest == final.GuidMapDigest, "The output migration changed asset GUIDs or local identifiers.");
            Require(staged.DependencyGuidDigest == final.DependencyGuidDigest, "The output migration changed the dependency GUID closure.");
            Require(final.NoTemporaryReferences && final.ReparseFree && final.SingleLink
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
            TreeSnapshot outputTree,
            TreeSnapshot protectedTree,
            RootIdentitySnapshot roots)
        {
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
            foreach (var path in source.SourceAssetFilePaths.Concat(new[]
            {
                source.SceneFilePath,
                source.SceneMetaPath,
                capability.CallbackAssemblyPath,
                capability.SdkCallbackAssemblyPath
            })) Add(path, false);
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
                Require(roots.EntryCount == RequiredRootPaths().Count, "The stable root lease set is incomplete.");
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
            TreeSnapshot outputTree,
            TreeSnapshot protectedTree,
            RootIdentitySnapshot roots,
            StableInputLeases leases,
            bool verifyOutputTree = true)
        {
            Require(leases.Paths.Count > PackageFileCount, "The stable input lease set is incomplete.");
            Require(Sha256File(source.SceneFilePath) == source.SceneFileDigest && Sha256File(source.SceneMetaPath) == source.SceneMetaDigest, "The source scene changed while leases were acquired.");
            var refreshedCapability = CaptureCapability();
            Require(refreshedCapability.CapabilityDigest == capability.CapabilityDigest, "The package capability changed while leases were acquired.");
            var refreshedRoots = CaptureRootIdentities();
            Require(refreshedRoots.Digest == roots.Digest && refreshedRoots.EntryCount == roots.EntryCount, "A project root identity changed while leases were acquired.");
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
            TreeSnapshot beforeOutput,
            TreeSnapshot beforeProtected,
            RootIdentitySnapshot beforeRoots,
            SourceSnapshot beforeSource,
            string cloneName,
            CacheTransaction cacheTransaction,
            AssetTreeManifest outputManifest)
        {
            try
            {
                var restored = true;
                if (outputScene.IsValid() && outputScene.isLoaded)
                {
                    foreach (var root in outputScene.GetRootGameObjects()) Object.DestroyImmediate(root);
                    Require(EditorSceneManagerClose(outputScene), "Failed to close the temporary output scene.");
                }
                if (beforeGenerated != null && cacheTransaction != null)
                {
                    if (!cacheTransaction.Restore()) restored = false;
                }
                else if (beforeGenerated != null)
                {
                    var current = CaptureTree(GeneratedRoot, GeneratedTreeSchema, requireExists: true);
                    var delta = CompareGeneratedTrees(beforeGenerated, current);
                    if (delta.Modified.Count > 0 || delta.Removed.Count > 0)
                    {
                        restored = false;
                    }
                    else foreach (var first in delta.Added.Select(entry => entry.RelativePath.Split('/')[0].Replace(".meta", string.Empty)).Distinct(StringComparer.Ordinal))
                    {
                        if (string.IsNullOrWhiteSpace(first))
                        {
                            restored = false;
                            continue;
                        }
                        AssetDatabase.DeleteAsset(GeneratedRoot + "/" + first);
                    }
                    RequireNoDirtyProjectAssets(GeneratedRoot);
                    AssetDatabase.SaveAssets();
                    AssetDatabase.Refresh(ImportAssetOptions.ForceSynchronousImport);
                    var restoredGenerated = CaptureTree(GeneratedRoot, GeneratedTreeSchema, requireExists: true);
                    if (restoredGenerated.Digest != beforeGenerated.Digest || restoredGenerated.EntryCount != beforeGenerated.EntryCount) restored = false;
                }
                if (beforeOutput != null && !string.IsNullOrWhiteSpace(cloneName))
                {
                    var durableTarget = OutputKindRoot + "/" + cloneName;
                    var ownedTargetRemoved = true;
                    if (AssetDatabase.IsValidFolder(durableTarget))
                    {
                        if (outputManifest == null)
                        {
                            restored = false;
                            ownedTargetRemoved = false;
                        }
                        else
                        {
                            var currentManifest = CaptureAssetTreeManifest(
                                durableTarget,
                                durableTarget + "/" + cloneName + ".prefab",
                                requireNoTemporaryReferences: true);
                            if (currentManifest.ReceiptDigest != outputManifest.ReceiptDigest)
                            {
                                restored = false;
                                ownedTargetRemoved = false;
                            }
                            else AssetDatabase.DeleteAsset(durableTarget);
                        }
                    }
                    if (ownedTargetRemoved && beforeOutput.Exists)
                    {
                        if (!beforeOutput.Entries.ContainsKey("ParameterBitPacking") && AssetDatabase.IsValidFolder(OutputKindRoot))
                        {
                            AssetDatabase.DeleteAsset(OutputKindRoot);
                        }
                    }
                    else if (ownedTargetRemoved && !beforeOutput.Exists && AssetDatabase.IsValidFolder(OutputRoot))
                    {
                        AssetDatabase.DeleteAsset(OutputRoot);
                    }
                    RequireNoDirtyProjectAssets(OutputRoot);
                    AssetDatabase.SaveAssets();
                    AssetDatabase.Refresh(ImportAssetOptions.ForceSynchronousImport);
                    var restoredOutput = CaptureManagedTree(OutputRoot, OutputTreeSchema);
                    if (restoredOutput.Exists != beforeOutput.Exists
                        || restoredOutput.Digest != beforeOutput.Digest
                        || restoredOutput.EntryCount != beforeOutput.EntryCount) restored = false;
                }
                if (beforeProtected != null)
                {
                    var protectedAfter = CaptureProtectedTree();
                    if (protectedAfter.Digest != beforeProtected.Digest || protectedAfter.EntryCount != beforeProtected.EntryCount) restored = false;
                }
                if (beforeRoots != null)
                {
                    var rootsAfter = CaptureRootIdentities();
                    if (rootsAfter.Digest != beforeRoots.Digest || rootsAfter.EntryCount != beforeRoots.EntryCount) restored = false;
                }
                if (beforeSource != null)
                {
                    var sourceAfter = CaptureSource(beforeSource.ScenePath, beforeSource.ObjectPath);
                    if (sourceAfter.SourceStateDigest != beforeSource.SourceStateDigest
                        || sourceAfter.SourceAssetSetDigest != beforeSource.SourceAssetSetDigest) restored = false;
                }
                if (restored && cacheTransaction != null && !cacheTransaction.Completed)
                {
                    cacheTransaction.Complete();
                }
                return restored;
            }
            catch
            {
                return false;
            }
        }

        private static bool EditorSceneManagerClose(Scene scene)
        {
            return UnityEditor.SceneManagement.EditorSceneManager.CloseScene(scene, true);
        }

        private static void RequireNoDirtyProjectAssets(params string[] allowedDirtyRoots)
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
                        || root.StartsWith(OutputRoot + "/", StringComparison.Ordinal)),
                "The dirty-asset save scope is invalid."
            );
            Require(
                SceneManager.sceneCount <= OpenProjectSceneScanLimit,
                "The open project scene set exceeds the bounded cleanliness scan."
            );
            for (var index = 0; index < SceneManager.sceneCount; index++)
            {
                var scene = SceneManager.GetSceneAt(index);
                Require(scene.IsValid() && scene.isLoaded, "An open project scene is incomplete.");
                Require(!string.IsNullOrWhiteSpace(scene.path), "An open project scene has no persistent project asset.");
                var scenePath = scene.path.Replace('\\', '/');
                Require(
                    scenePath == scene.path
                        && scenePath.StartsWith("Assets/", StringComparison.Ordinal)
                        && scenePath.EndsWith(".unity", StringComparison.Ordinal)
                        && !scenePath.Split('/').Any(part => string.IsNullOrWhiteSpace(part) || part == "." || part == ".."),
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
                    "An unrelated project asset importer is dirty."
                );
                if (isDirectory) continue;

                var assets = AssetDatabase.LoadAllAssetsAtPath(path);
                Require(assets != null && assets.Length > 0, "A registered project asset has no persistent object.");
                Require(
                    objectCount <= RegisteredAssetObjectScanLimit - assets.Length,
                    "The registered asset object set exceeds the bounded cleanliness scan."
                );
                objectCount += assets.Length;
                foreach (var asset in assets)
                {
                    Require(
                        asset != null
                            && EditorUtility.IsPersistent(asset)
                            && AssetDatabase.GetAssetPath(asset) == path,
                        "A registered project asset contains an incomplete persistent object."
                    );
                    Require(
                        !EditorUtility.IsDirty(asset) || IsAllowedDirtyAssetPath(path, allowedRoots),
                        "An unrelated project asset is dirty."
                    );
                }
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
            bool sceneLoadedAfter,
            bool temporaryObjectResidue,
            TreeSnapshot generatedBefore,
            TreeSnapshot generatedAfter,
            GeneratedDelta generatedDelta,
            string temporaryDeltaDigest,
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

        private sealed class CapabilitySnapshot
        {
            internal string PackageRootPath;
            internal string PackageRootIdentityDigest;
            internal string CallbackAssemblyPath;
            internal string CallbackAssemblySha256;
            internal string SdkCallbackAssemblyPath;
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

        private sealed class CacheTransaction
        {
            private readonly string transactionRoot;
            private readonly string backupRoot;
            private readonly string journalPath;
            private readonly string baselineContentDigest;
            private readonly int baselineEntryCount;
            private readonly long baselineByteCount;
            private bool completed;

            private CacheTransaction(
                string transactionRoot,
                string backupRoot,
                string journalPath,
                TreeSnapshot baseline)
            {
                this.transactionRoot = transactionRoot;
                this.backupRoot = backupRoot;
                this.journalPath = journalPath;
                baselineContentDigest = baseline.ContentDigest;
                baselineEntryCount = baseline.EntryCount;
                baselineByteCount = baseline.TotalBytes;
            }

            internal string JournalId => Path.GetFileName(transactionRoot);
            internal string BaselineContentDigest => baselineContentDigest;
            internal int BaselineEntryCount => baselineEntryCount;
            internal long BaselineByteCount => baselineByteCount;
            internal bool Completed => completed;

            internal static CacheTransaction Create(TreeSnapshot baseline)
            {
                Require(baseline != null, "The generated cache baseline is unavailable.");
                Require(baseline.EntryCount <= CacheBackupMaxEntries, "The generated cache exceeds the backup entry limit.");
                Require(baseline.TotalBytes <= CacheBackupMaxBytes, "The generated cache exceeds the backup byte limit.");
                var project = CurrentProjectPath();
                var library = Path.Combine(project, "Library");
                Require(Directory.Exists(library), "The project Library root is unavailable.");
                RequireSafeDirectory(library, "The project Library root is linked or reparsed.");
                var privateRoot = Path.Combine(library, "VRCForge");
                Directory.CreateDirectory(privateRoot);
                RequireSafeDirectory(privateRoot, "The private transaction root is linked or reparsed.");
                var transactions = Path.Combine(privateRoot, "transactions");
                Directory.CreateDirectory(transactions);
                RequireSafeDirectory(transactions, "The transaction journal root is linked or reparsed.");
                var transactionRoot = Path.Combine(
                    transactions,
                    "parameter-bit-packing-" + Guid.NewGuid().ToString("N"));
                Directory.CreateDirectory(transactionRoot);
                RequireSafeDirectory(transactionRoot, "The cache transaction root is linked or reparsed.");
                var backupRoot = Path.Combine(transactionRoot, "cache");
                Directory.CreateDirectory(backupRoot);
                RequireSafeDirectory(backupRoot, "The cache backup root is linked or reparsed.");
                var journalPath = Path.Combine(transactionRoot, "journal.json");
                var transaction = new CacheTransaction(transactionRoot, backupRoot, journalPath, baseline);
                transaction.WriteJournal("preparing", false);
                CopyTree(AbsoluteProjectPath(GeneratedRoot), backupRoot);
                var backup = CaptureTreeAbsolute(backupRoot, CacheContentSchema + ".backup");
                Require(
                    backup.ContentDigest == ReframeContentDigest(baseline, CacheContentSchema + ".backup")
                        && backup.EntryCount == baseline.EntryCount
                        && backup.TotalBytes == baseline.TotalBytes,
                    "The generated cache backup does not match its baseline.");
                transaction.WriteJournal("prepared", false);
                return transaction;
            }

            internal bool Restore()
            {
                try
                {
                    WriteJournal("restoring", false);
                    var cacheRoot = AbsoluteProjectPath(GeneratedRoot);
                    Require(Directory.Exists(cacheRoot), "The generated cache root is missing during restore.");
                    var restoreTarget = CaptureTreeAbsolute(cacheRoot, CacheContentSchema + ".restore_target");
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
                    RequireNoDirtyProjectAssets(GeneratedRoot);
                    AssetDatabase.SaveAssets();
                    AssetDatabase.Refresh(ImportAssetOptions.ForceSynchronousImport);
                    var restored = CaptureTree(GeneratedRoot, GeneratedTreeSchema, requireExists: true);
                    if (restored.ContentDigest != baselineContentDigest
                        || restored.EntryCount != baselineEntryCount
                        || restored.TotalBytes != baselineByteCount)
                    {
                        WriteJournal("restore_mismatch", false);
                        return false;
                    }
                    WriteJournal("restored", true);
                    return true;
                }
                catch
                {
                    try { WriteJournal("restore_failed", false); } catch { }
                    return false;
                }
            }

            internal void Complete()
            {
                WriteJournal("closing", true);
                Directory.Delete(transactionRoot, true);
                Require(!Directory.Exists(transactionRoot), "The cache transaction journal could not be closed.");
                completed = true;
            }

            private void WriteJournal(string state, bool cacheRestored)
            {
                var payload = new JObject
                {
                    ["schema"] = CacheJournalSchema,
                    ["journalId"] = JournalId,
                    ["state"] = state,
                    ["cacheRoot"] = GeneratedRoot,
                    ["baselineContentDigest"] = baselineContentDigest,
                    ["baselineEntryCount"] = baselineEntryCount,
                    ["baselineByteCount"] = baselineByteCount,
                    ["cacheRestored"] = cacheRestored
                };
                var bytes = Encoding.UTF8.GetBytes(payload.ToString(Newtonsoft.Json.Formatting.None));
                var nextPath = journalPath + ".next";
                if (File.Exists(nextPath))
                {
                    RequireStableRegularFile(nextPath);
                    File.Delete(nextPath);
                }
                using (var stream = new FileStream(nextPath, FileMode.CreateNew, FileAccess.Write, FileShare.Read))
                {
                    stream.Write(bytes, 0, bytes.Length);
                    stream.Flush(true);
                }
                RequireStableRegularFile(nextPath);
                if (File.Exists(journalPath))
                {
                    RequireStableRegularFile(journalPath);
                    File.Replace(nextPath, journalPath, null, true);
                }
                else
                {
                    File.Move(nextPath, journalPath);
                }
                RequireStableRegularFile(journalPath);
            }

            private static void CopyTree(string sourceRoot, string destinationRoot)
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

            private static void RequireSafeDirectory(string path, string message)
            {
                Require(Directory.Exists(path), message);
                var identity = CaptureIdentity(path, true);
                Require(!identity.IsReparsePoint && identity.NumberOfLinks == 1, message);
            }

            private static string ReframeContentDigest(TreeSnapshot snapshot, string schema)
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
