using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Security.Cryptography;
using System.Text;
using VRCForge.Core.MCP;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEngine;
using UnityEngine.SceneManagement;

namespace VRCForge.Editor
{
    internal sealed class ComponentFeatureWriteException : InvalidOperationException
    {
        internal ComponentFeatureWriteException(string message) : base(message)
        {
        }
    }

    internal sealed class ComponentFeatureCompatibility
    {
        internal string PackageName = string.Empty;
        internal string PackageVersion = string.Empty;
        internal int PackageFileCount;
        internal long PackageTotalBytes;
        internal string PackageTreeDigest = string.Empty;
        internal string ApiAssemblyName = string.Empty;
        internal string ApiAssemblyVersion = string.Empty;
        internal string ApiAssemblyPublicKeyToken = string.Empty;
        internal string ApiAssemblySignatureState = string.Empty;
        internal string ApiAssemblyDigest = string.Empty;
        internal string RuntimeAssemblyName = string.Empty;
        internal string RuntimeAssemblyVersion = string.Empty;
        internal string RuntimeAssemblyPublicKeyToken = string.Empty;
        internal string RuntimeAssemblySignatureState = string.Empty;
        internal string RuntimeAssemblyDigest = string.Empty;
        internal string ApiSignatureDigest = string.Empty;
        internal string Digest = string.Empty;
        internal Assembly ApiAssembly;
        internal Assembly RuntimeAssembly;
        internal Type RootComponentType;
        internal Type ToggleWrapperType;
        internal Type ArmatureWrapperType;
        internal Type ActionSetType;

        internal JObject ToPayload()
        {
            return new JObject
            {
                ["packageName"] = PackageName,
                ["packageVersion"] = PackageVersion,
                ["packageFileCount"] = PackageFileCount,
                ["packageTotalBytes"] = PackageTotalBytes,
                ["packageTreeDigest"] = PackageTreeDigest,
                ["apiAssemblyName"] = ApiAssemblyName,
                ["apiAssemblyVersion"] = ApiAssemblyVersion,
                ["apiAssemblyPublicKeyToken"] = ApiAssemblyPublicKeyToken,
                ["apiAssemblySignatureState"] = ApiAssemblySignatureState,
                ["apiAssemblyDigest"] = ApiAssemblyDigest,
                ["runtimeAssemblyName"] = RuntimeAssemblyName,
                ["runtimeAssemblyVersion"] = RuntimeAssemblyVersion,
                ["runtimeAssemblyPublicKeyToken"] = RuntimeAssemblyPublicKeyToken,
                ["runtimeAssemblySignatureState"] = RuntimeAssemblySignatureState,
                ["runtimeAssemblyDigest"] = RuntimeAssemblyDigest,
                ["apiSignatureDigest"] = ApiSignatureDigest
            };
        }
    }

    internal sealed class ComponentFeatureSceneEvidence
    {
        internal Scene Scene;
        internal string Path = string.Empty;
        internal string Guid = string.Empty;
        internal int Handle;
        internal string FileDigest = string.Empty;
        internal string FileIdentity = string.Empty;
        internal string MetaDigest = string.Empty;
        internal string MetaIdentity = string.Empty;
        internal bool Dirty;

        internal JObject ToPreviewPayload(ComponentFeatureSceneEvidence after)
        {
            return new JObject
            {
                ["path"] = Path,
                ["guid"] = Guid,
                ["handle"] = Handle,
                ["fileDigestBefore"] = FileDigest,
                ["fileDigestAfter"] = after.FileDigest,
                ["fileIdentity"] = FileIdentity,
                ["metaDigestBefore"] = MetaDigest,
                ["metaDigestAfter"] = after.MetaDigest,
                ["metaIdentity"] = MetaIdentity,
                ["dirtyBefore"] = Dirty,
                ["dirtyAfter"] = after.Dirty
            };
        }
    }

    internal sealed class ComponentFeatureHostEvidence
    {
        internal GameObject Host;
        internal string ObjectPath = string.Empty;
        internal string ObjectId = string.Empty;
        internal string ComponentType = string.Empty;
        internal int ComponentIndex;
        internal string ComponentIdentitySeed = string.Empty;
        internal int ExistingFeatureCount;

        internal JObject ToPayload()
        {
            return new JObject
            {
                ["objectPath"] = ObjectPath,
                ["objectId"] = ObjectId,
                ["componentType"] = ComponentType,
                ["componentIndex"] = ComponentIndex,
                ["componentIdentitySeed"] = ComponentIdentitySeed,
                ["existingFeatureCount"] = ExistingFeatureCount
            };
        }
    }

    internal sealed class ComponentFeatureLinkRequest
    {
        internal string TargetKind = string.Empty;
        internal string Target = string.Empty;
        internal string Offset = string.Empty;
        internal GameObject ObjectTarget;
        internal HumanBodyBones Bone;
        internal string ObjectId = string.Empty;
    }

    internal sealed class ComponentFeatureRequest
    {
        internal string ScenePath = string.Empty;
        internal string GameObjectPath = string.Empty;
        internal string FeatureKind = string.Empty;
        internal string MenuPath = string.Empty;
        internal List<string> TargetObjectPaths = new List<string>();
        internal List<GameObject> TargetObjects = new List<GameObject>();
        internal bool Slider;
        internal bool DefaultOn;
        internal bool Saved;
        internal string GlobalParameter = string.Empty;
        internal string LinkFromPath = string.Empty;
        internal GameObject LinkFrom;
        internal string LinkFromId = string.Empty;
        internal List<ComponentFeatureLinkRequest> LinkTargets = new List<ComponentFeatureLinkRequest>();
        internal bool Recursive;
        internal bool Align;
    }

    internal sealed class ComponentFeaturePreviewSnapshot
    {
        internal string ProjectPath = string.Empty;
        internal ComponentFeatureCompatibility Compatibility;
        internal ComponentFeatureSceneEvidence Scene;
        internal ComponentFeatureHostEvidence Host;
        internal ComponentFeatureRequest Request;
        internal JObject Before;
        internal JObject Target;
        internal string BeforeDigest = string.Empty;
        internal string TargetDigest = string.Empty;
        internal string PreviewDigest = string.Empty;

        internal JObject ToPayload(ComponentFeatureSceneEvidence after)
        {
            var payload = new JObject
            {
                ["schema"] = ComponentFeatureWriteCore.ResultSchema,
                ["ok"] = true,
                ["preview"] = true,
                ["verified"] = true,
                ["changed"] = false,
                ["saved"] = false,
                ["mutationCount"] = 0,
                ["projectPath"] = ProjectPath,
                ["compatibility"] = Compatibility.ToPayload(),
                ["compatibilityDigestSchema"] = ComponentFeatureWriteCore.CompatibilityDigestSchema,
                ["compatibilityDigest"] = Compatibility.Digest,
                ["scene"] = Scene.ToPreviewPayload(after),
                ["host"] = Host.ToPayload(),
                ["before"] = Before.DeepClone(),
                ["target"] = Target.DeepClone(),
                ["featureDigestSchema"] = ComponentFeatureWriteCore.FeatureDigestSchema,
                ["beforeFeatureDigest"] = BeforeDigest,
                ["targetFeatureDigest"] = TargetDigest,
                ["wouldChange"] = true
            };
            payload["previewDigest"] = ComponentFeatureWriteCore.ComputePreviewDigest(payload);
            return payload;
        }
    }

    internal static class ComponentFeatureWriteCore
    {
        internal const string ResultSchema = "vrcforge.component_feature_write.v1";
        internal const string FeatureDigestSchema = "vrcforge.component_feature_state.v1";
        internal const string PreviewDigestSchema = "vrcforge.component_feature_preview.v1";
        internal const string CompatibilityDigestSchema = "vrcforge.component_feature_compatibility.v1";
        internal const string PackageTreeDigestSchema = "vrcforge.component_feature.package_tree.v1";
        internal const string ApiSignatureDigestSchema = "vrcforge.component_feature.api_signature.v1";
        internal const string ToggleKind = "toggle";
        internal const string ArmatureLinkKind = "armature_link";
        internal const string RootComponentTypeName = "VF.Model.VRCFury";
        internal const string RuntimeAssemblyName = "VRCFury";
        internal const string RuntimeAssemblyVersion = "0.0.0.0";
        internal const string RuntimeAssemblyToken = "";
        internal const string ApiAssemblyName = "com.vrcfury.api";
        internal const string ApiAssemblyVersion = "0.0.0.0";
        internal const string ApiAssemblyToken = "";
        internal const string ExpectedPackageName = "com.vrcfury.vrcfury";
        internal const string ExpectedPackageVersion = "1.1334.0";
        internal const int ExpectedPackageFileCount = 1255;
        internal const long ExpectedPackageTotalBytes = 1999565;
        internal const string ExpectedPackageTreeDigest = "d58d5db6083852bb0f5b495248794026b753b494dd88c8f6523e0019ff1a0f59";
        internal const string ExpectedApiSignatureDigest = "71dc4faf929c8da61b8969e2b23a00636ac0aa5a53e9a67f73274213d4a417b1";
        internal const string ToggleSerializedType = "VRCFury VF.Model.Feature.Toggle";
        internal const string ArmatureSerializedType = "VRCFury VF.Model.Feature.ArmatureLink";

        private static readonly object ReadbackSchemaLock = new object();
        private static StructuredManagedReferenceSchema ToggleSchema;
        private static StructuredManagedReferenceSchema ArmatureSchema;
        private static string RegisteredRuntimeAssemblyDigest = string.Empty;

        private static readonly string[] RequiredApiSignatures =
        {
            "com.vrcfury.api.Actions.FuryActionSet::AddTurnOn(UnityEngine.GameObject)->System.Void;public;instance",
            "com.vrcfury.api.Components.FuryArmatureLink::LinkFrom(UnityEngine.GameObject)->System.Void;public;instance",
            "com.vrcfury.api.Components.FuryArmatureLink::LinkTo(System.String)->System.Void;public;instance",
            "com.vrcfury.api.Components.FuryArmatureLink::LinkTo(UnityEngine.GameObject,System.String)->System.Void;public;instance",
            "com.vrcfury.api.Components.FuryArmatureLink::LinkTo(UnityEngine.HumanBodyBones,System.String)->System.Void;public;instance",
            "com.vrcfury.api.Components.FuryArmatureLink::SetAlign(System.Boolean)->System.Void;public;instance",
            "com.vrcfury.api.Components.FuryArmatureLink::SetRecursive(System.Boolean)->System.Void;public;instance",
            "com.vrcfury.api.Components.FuryToggle::GetActions()->com.vrcfury.api.Actions.FuryActionSet;public;instance",
            "com.vrcfury.api.Components.FuryToggle::SetDefaultOn()->System.Void;public;instance",
            "com.vrcfury.api.Components.FuryToggle::SetGlobalParameter(System.String)->System.Void;public;instance",
            "com.vrcfury.api.Components.FuryToggle::SetMenuPath(System.String)->System.Void;public;instance",
            "com.vrcfury.api.Components.FuryToggle::SetSaved()->System.Void;public;instance",
            "com.vrcfury.api.Components.FuryToggle::SetSlider(System.Boolean)->System.Void;public;instance",
            "com.vrcfury.api.FuryComponents::CreateArmatureLink(UnityEngine.GameObject)->com.vrcfury.api.Components.FuryArmatureLink;public;static",
            "com.vrcfury.api.FuryComponents::CreateToggle(UnityEngine.GameObject)->com.vrcfury.api.Components.FuryToggle;public;static"
        };

        internal static object Success(object payload)
        {
            return VRCForgeToolResult.Completed("Component feature operation completed.", payload);
        }

        internal static object Failure(Exception exception)
        {
            if (exception is ComponentFeatureWriteException controlled)
            {
                return VRCForgeToolResult.Failed(controlled.Message);
            }
            return VRCForgeToolResult.Failed("Component feature operation failed closed.");
        }

        internal static object BuildMutationFailure(bool restored)
        {
            return VRCForgeToolResult.Failed(
                restored
                    ? "Component feature apply failed after restoring the verified pre-state."
                    : "Component feature apply failed; checkpoint restore is required.",
                new
                {
                    schema = ResultSchema,
                    mutationStarted = true,
                    restored,
                    cleanupVerified = restored,
                    cleanupRequired = !restored,
                    checkpointRestoreRequired = !restored,
                    operationState = restored ? "restored" : "checkpoint_restore_required"
                });
        }

        internal static ComponentFeatureCompatibility ValidateCompatibility()
        {
            var apiAssembly = ResolveSingleAssembly(ApiAssemblyName);
            var runtimeAssembly = ResolveSingleAssembly(RuntimeAssemblyName);
            var apiAssemblyDigest = ValidateAssemblyIdentity(
                apiAssembly,
                ApiAssemblyName,
                ApiAssemblyVersion,
                ApiAssemblyToken);
            var runtimeAssemblyDigest = ValidateAssemblyIdentity(
                runtimeAssembly,
                RuntimeAssemblyName,
                RuntimeAssemblyVersion,
                RuntimeAssemblyToken);

            var package = UnityEditor.PackageManager.PackageInfo.FindForAssembly(apiAssembly);
            var runtimePackage = UnityEditor.PackageManager.PackageInfo.FindForAssembly(runtimeAssembly);
            if (package == null
                || runtimePackage == null
                || package.name != ExpectedPackageName
                || package.version != ExpectedPackageVersion
                || runtimePackage.name != package.name
                || runtimePackage.version != package.version
                || string.IsNullOrWhiteSpace(package.resolvedPath)
                || string.IsNullOrWhiteSpace(runtimePackage.resolvedPath)
                || !Path.GetFullPath(runtimePackage.resolvedPath).TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar)
                    .Equals(
                        Path.GetFullPath(package.resolvedPath).TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar),
                        StringComparison.OrdinalIgnoreCase))
            {
                throw new ComponentFeatureWriteException("The required component feature package version is unavailable.");
            }
            var packageRoot = Path.GetFullPath(package.resolvedPath);
            var packageEvidence = ComputePackageTreeEvidence(packageRoot);
            if (packageEvidence.FileCount != ExpectedPackageFileCount
                || packageEvidence.TotalBytes != ExpectedPackageTotalBytes
                || packageEvidence.Digest != ExpectedPackageTreeDigest)
            {
                throw new ComponentFeatureWriteException("The component feature package tree is unsupported.");
            }

            var componentsType = RequireType(apiAssembly, "com.vrcfury.api.FuryComponents");
            var toggleType = RequireType(apiAssembly, "com.vrcfury.api.Components.FuryToggle");
            var armatureType = RequireType(apiAssembly, "com.vrcfury.api.Components.FuryArmatureLink");
            var actionSetType = RequireType(apiAssembly, "com.vrcfury.api.Actions.FuryActionSet");
            ValidatePublicApiSurface(componentsType, toggleType, armatureType, actionSetType);
            var signatureDigest = ComputeFramedDigest(
                new[] { ApiSignatureDigestSchema, RequiredApiSignatures.Length.ToString(CultureInfo.InvariantCulture) }
                    .Concat(RequiredApiSignatures.OrderBy(value => value, StringComparer.Ordinal)));
            if (signatureDigest != ExpectedApiSignatureDigest)
            {
                throw new ComponentFeatureWriteException("The component feature public API signature is unsupported.");
            }
            var rootType = RequireType(runtimeAssembly, RootComponentTypeName);
            if (!typeof(Component).IsAssignableFrom(rootType) || rootType.IsPublic || rootType.IsNestedPublic)
            {
                throw new ComponentFeatureWriteException("The component feature runtime root type is unsupported.");
            }
            EnsureReadbackSchemas(runtimeAssemblyDigest);

            var evidence = new ComponentFeatureCompatibility
            {
                PackageName = package.name,
                PackageVersion = package.version,
                PackageFileCount = packageEvidence.FileCount,
                PackageTotalBytes = packageEvidence.TotalBytes,
                PackageTreeDigest = packageEvidence.Digest,
                ApiAssemblyName = ApiAssemblyName,
                ApiAssemblyVersion = ApiAssemblyVersion,
                ApiAssemblyPublicKeyToken = ApiAssemblyToken,
                ApiAssemblySignatureState = "unsigned",
                ApiAssemblyDigest = apiAssemblyDigest,
                RuntimeAssemblyName = RuntimeAssemblyName,
                RuntimeAssemblyVersion = RuntimeAssemblyVersion,
                RuntimeAssemblyPublicKeyToken = RuntimeAssemblyToken,
                RuntimeAssemblySignatureState = "unsigned",
                RuntimeAssemblyDigest = runtimeAssemblyDigest,
                ApiSignatureDigest = signatureDigest,
                ApiAssembly = apiAssembly,
                RuntimeAssembly = runtimeAssembly,
                RootComponentType = rootType,
                ToggleWrapperType = toggleType,
                ArmatureWrapperType = armatureType,
                ActionSetType = actionSetType
            };
            evidence.Digest = ComputeCompatibilityDigest(evidence.ToPayload());
            return evidence;
        }

        internal static void RunMethodSignatureDriftProbe()
        {
            var apiAssembly = ResolveSingleAssembly(ApiAssemblyName);
            var componentsType = RequireType(apiAssembly, "com.vrcfury.api.FuryComponents");
            RequireExactPublicMethod(
                componentsType,
                "CreateToggle",
                RequireType(apiAssembly, "com.vrcfury.api.Components.FuryToggle"),
                false,
                typeof(string));
        }

        internal static ComponentFeaturePreviewSnapshot BuildPreview(ComponentFeatureRequest request)
        {
            if (request == null)
            {
                throw new ComponentFeatureWriteException("Component feature request is unavailable.");
            }
            var compatibility = ValidateCompatibility();
            var scene = ResolveSavedScene(request.ScenePath);
            if (scene.Dirty)
            {
                throw new ComponentFeatureWriteException("Component feature writes require a clean saved scene.");
            }
            var host = ResolveUniqueGameObject(scene.Scene, request.GameObjectPath, "feature host");
            ResolveRequestObjects(request, scene.Scene);
            var target = BuildTargetPayload(request);
            var before = new JObject
            {
                ["present"] = false,
                ["featureKind"] = request.FeatureKind
            };
            var targetDigest = ComputeFeatureDigest(target);
            var rootComponents = GetRootComponents(host, compatibility.RootComponentType);
            var expectedSerializedType = ExpectedSerializedType(request.FeatureKind);
            var existingCount = rootComponents.Count(component => ReadManagedReferenceType(component) == expectedSerializedType);
            if (existingCount != 0)
            {
                throw new ComponentFeatureWriteException("The component feature CreateNew target already exists.");
            }
            var hostId = StableGlobalObjectId(host, "feature host");
            var componentIndex = rootComponents.Count;
            var identitySeed = ComputeFramedDigest(new[]
            {
                "vrcforge.component_feature_component_seed.v1",
                scene.Guid,
                hostId,
                RootComponentTypeName,
                componentIndex.ToString(CultureInfo.InvariantCulture),
                targetDigest,
                compatibility.Digest
            });
            var hostEvidence = new ComponentFeatureHostEvidence
            {
                Host = host,
                ObjectPath = request.GameObjectPath,
                ObjectId = hostId,
                ComponentType = RootComponentTypeName,
                ComponentIndex = componentIndex,
                ComponentIdentitySeed = identitySeed,
                ExistingFeatureCount = existingCount
            };
            var snapshot = new ComponentFeaturePreviewSnapshot
            {
                ProjectPath = CurrentProjectPath(),
                Compatibility = compatibility,
                Scene = scene,
                Host = hostEvidence,
                Request = request,
                Before = before,
                Target = target,
                BeforeDigest = ComputeFeatureDigest(before),
                TargetDigest = targetDigest
            };
            var after = ResolveSavedScene(request.ScenePath);
            if (!SceneEvidenceMatches(scene, after)
                || after.Dirty
                || GetRootComponents(host, compatibility.RootComponentType).Count != rootComponents.Count)
            {
                throw new ComponentFeatureWriteException("Component feature preview changed project state.");
            }
            var payload = snapshot.ToPayload(after);
            snapshot.PreviewDigest = payload.Value<string>("previewDigest") ?? string.Empty;
            return snapshot;
        }

        internal static object InvokePublicCreate(
            ComponentFeaturePreviewSnapshot snapshot,
            out Component createdComponent)
        {
            if (snapshot == null)
            {
                throw new ComponentFeatureWriteException("Component feature preview is unavailable.");
            }
            var before = GetRootComponents(snapshot.Host.Host, snapshot.Compatibility.RootComponentType);
            var componentsType = RequireType(snapshot.Compatibility.ApiAssembly, "com.vrcfury.api.FuryComponents");
            var isToggle = snapshot.Request.FeatureKind == ToggleKind;
            var returnType = isToggle
                ? snapshot.Compatibility.ToggleWrapperType
                : snapshot.Compatibility.ArmatureWrapperType;
            var method = RequireExactPublicMethod(
                componentsType,
                isToggle ? "CreateToggle" : "CreateArmatureLink",
                returnType,
                true,
                typeof(GameObject));
            var wrapper = InvokeExact(method, null, snapshot.Host.Host);
            if (wrapper == null || wrapper.GetType() != returnType)
            {
                throw new ComponentFeatureWriteException("The component feature public API returned an unexpected wrapper.");
            }
            var after = GetRootComponents(snapshot.Host.Host, snapshot.Compatibility.RootComponentType);
            var created = after.Where(component => !before.Contains(component)).ToList();
            if (created.Count != 1
                || after.Count != before.Count + 1
                || after.IndexOf(created[0]) != snapshot.Host.ComponentIndex)
            {
                throw new ComponentFeatureWriteException("The component feature public API did not CreateNew exactly one component.");
            }
            createdComponent = created[0];
            if (isToggle)
            {
                ConfigureToggle(wrapper, snapshot.Request, snapshot.Compatibility);
            }
            else
            {
                ConfigureArmature(wrapper, snapshot.Request, snapshot.Compatibility);
            }
            return wrapper;
        }

        internal static StructuredManagedReferenceReadPlan ReadExactFeature(
            Component component,
            string featureKind)
        {
            var schema = featureKind == ToggleKind ? ToggleSchema : ArmatureSchema;
            return TypedStructuredListCore.ReadManagedReference(component, schema);
        }

        internal static void VerifyReadback(
            ComponentFeaturePreviewSnapshot snapshot,
            Component component,
            StructuredManagedReferenceReadPlan plan)
        {
            if (snapshot == null || component == null || plan == null)
            {
                throw new ComponentFeatureWriteException("Component feature readback is unavailable.");
            }
            var request = snapshot.Request;
            if (request.FeatureKind == ToggleKind)
            {
                if (plan.ManagedReferenceFullTypeName != ToggleSerializedType
                    || !FieldString(plan, "menuPath").Equals(request.MenuPath, StringComparison.Ordinal)
                    || FieldBoolean(plan, "slider") != request.Slider
                    || FieldBoolean(plan, "defaultOn") != request.DefaultOn
                    || FieldBoolean(plan, "saved") != request.Saved
                    || FieldBoolean(plan, "useGlobalParameter") != !string.IsNullOrEmpty(request.GlobalParameter)
                    || !FieldString(plan, "globalParameter").Equals(request.GlobalParameter, StringComparison.Ordinal))
                {
                    throw new ComponentFeatureWriteException("Toggle readback did not match the approved target.");
                }
                var targets = plan.RequireCollection("targets").Elements;
                if (targets.Count != request.TargetObjects.Count)
                {
                    throw new ComponentFeatureWriteException("Toggle target count readback changed.");
                }
                for (var index = 0; index < targets.Count; index++)
                {
                    if (!ReferenceEquals(targets[index].RequireField("object").RawValue, request.TargetObjects[index])
                        || targets[index].RequireField("mode").CanonicalValue != "0")
                    {
                        throw new ComponentFeatureWriteException("Toggle action readback did not match the approved target.");
                    }
                }
            }
            else
            {
                if (plan.ManagedReferenceFullTypeName != ArmatureSerializedType
                    || !ReferenceEquals(plan.RequireField("linkFrom").RawValue, request.LinkFrom)
                    || FieldBoolean(plan, "recursive") != request.Recursive
                    || FieldBoolean(plan, "alignPosition") != request.Align
                    || FieldBoolean(plan, "alignRotation") != request.Align
                    || FieldBoolean(plan, "alignScale") != request.Align)
                {
                    throw new ComponentFeatureWriteException("Armature-link readback did not match the approved target.");
                }
                var links = plan.RequireCollection("links").Elements;
                if (links.Count != request.LinkTargets.Count)
                {
                    throw new ComponentFeatureWriteException("Armature-link target count readback changed.");
                }
                for (var index = 0; index < links.Count; index++)
                {
                    var expected = request.LinkTargets[index];
                    var actual = links[index];
                    var expectedUseBone = expected.TargetKind == "humanoid_bone";
                    var expectedUseObject = expected.TargetKind == "game_object";
                    var expectedBone = expectedUseBone ? (int)expected.Bone : (int)HumanBodyBones.Hips;
                    var expectedObject = expectedUseObject ? expected.ObjectTarget : null;
                    var expectedOffset = expected.TargetKind == "relative_path" ? expected.Target : expected.Offset;
                    if ((actual.RequireField("useBone").CanonicalValue == "true") != expectedUseBone
                        || actual.RequireField("bone").CanonicalValue
                            != expectedBone.ToString(CultureInfo.InvariantCulture)
                        || (actual.RequireField("useObject").CanonicalValue == "true") != expectedUseObject
                        || !ReferenceEquals(actual.RequireField("object").RawValue, expectedObject)
                        || actual.RequireField("offset").CanonicalValue != expectedOffset)
                    {
                        throw new ComponentFeatureWriteException("Armature-link item readback did not match the approved target.");
                    }
                }
            }
            var actualTarget = BuildTargetPayload(request);
            if (ComputeFeatureDigest(actualTarget) != snapshot.TargetDigest)
            {
                throw new ComponentFeatureWriteException("Component feature product readback digest changed.");
            }
        }

        internal static JObject BuildApplyPayload(
            ComponentFeaturePreviewSnapshot snapshot,
            Component component,
            StructuredManagedReferenceReadPlan readback,
            ComponentFeatureSceneEvidence after)
        {
            var globalId = StableGlobalObjectId(component, "component feature");
            var componentId = ComputeFramedDigest(new[]
            {
                "vrcforge.component_feature_component.v1",
                after.Guid,
                globalId,
                RootComponentTypeName,
                snapshot.Host.ComponentIndex.ToString(CultureInfo.InvariantCulture),
                snapshot.TargetDigest
            });
            return new JObject
            {
                ["schema"] = ResultSchema,
                ["ok"] = true,
                ["preview"] = false,
                ["verified"] = true,
                ["changed"] = true,
                ["saved"] = true,
                ["mutationCount"] = 1,
                ["projectPath"] = snapshot.ProjectPath,
                ["compatibility"] = snapshot.Compatibility.ToPayload(),
                ["compatibilityDigestSchema"] = CompatibilityDigestSchema,
                ["compatibilityDigest"] = snapshot.Compatibility.Digest,
                ["scene"] = new JObject
                {
                    ["path"] = after.Path,
                    ["guid"] = after.Guid,
                    ["handle"] = after.Handle,
                    ["fileDigestBefore"] = snapshot.Scene.FileDigest,
                    ["fileDigestAfter"] = after.FileDigest,
                    ["fileIdentityBefore"] = snapshot.Scene.FileIdentity,
                    ["fileIdentityAfter"] = after.FileIdentity,
                    ["metaDigestBefore"] = snapshot.Scene.MetaDigest,
                    ["metaDigestAfter"] = after.MetaDigest,
                    ["metaIdentityBefore"] = snapshot.Scene.MetaIdentity,
                    ["metaIdentityAfter"] = after.MetaIdentity,
                    ["dirtyAfter"] = after.Dirty
                },
                ["host"] = snapshot.Host.ToPayload(),
                ["componentGlobalId"] = globalId,
                ["componentId"] = componentId,
                ["before"] = snapshot.Before.DeepClone(),
                ["target"] = snapshot.Target.DeepClone(),
                ["featureDigestSchema"] = FeatureDigestSchema,
                ["beforeFeatureDigest"] = snapshot.BeforeDigest,
                ["targetFeatureDigest"] = snapshot.TargetDigest,
                ["managedReadback"] = readback.ToCanonicalJObject(),
                ["managedReadbackDigest"] = readback.CanonicalDigest,
                ["previewDigest"] = snapshot.PreviewDigest,
                ["cleanupRequired"] = false,
                ["CreateNew"] = true
            };
        }

        internal static ComponentFeatureSceneEvidence ResolveSavedScene(string assetPath)
        {
            var normalized = NormalizeScenePath(assetPath);
            var matches = Enumerable.Range(0, SceneManager.sceneCount)
                .Select(SceneManager.GetSceneAt)
                .Where(scene => scene.IsValid()
                    && scene.isLoaded
                    && string.Equals(
                        (scene.path ?? string.Empty).Replace('\\', '/'),
                        normalized,
                        StringComparison.Ordinal))
                .ToList();
            if (matches.Count != 1)
            {
                throw new ComponentFeatureWriteException("The selected saved scene is not loaded exactly once.");
            }
            var scene = matches[0];
            var guid = (AssetDatabase.AssetPathToGUID(normalized, AssetPathToGUIDOptions.OnlyExistingAssets)
                ?? string.Empty).Trim().ToLowerInvariant();
            if (guid.Length != 32 || guid.Any(character => !Uri.IsHexDigit(character)))
            {
                throw new ComponentFeatureWriteException("The selected scene GUID is unavailable.");
            }
            StableAssetEvidence stable;
            try
            {
                stable = SceneObjectCopyCore.ReadStableAssetEvidence(
                    normalized,
                    "component feature scene");
            }
            catch (SceneObjectCopyException)
            {
                throw new ComponentFeatureWriteException(
                    "The selected scene could not be bound to stable file evidence.");
            }
            if (stable == null
                || stable.File == null
                || stable.Meta == null
                || stable.Guid != guid
                || stable.File.LinkCount != 1
                || stable.Meta.LinkCount != 1)
            {
                throw new ComponentFeatureWriteException(
                    "The selected scene stable file evidence is invalid.");
            }
            return new ComponentFeatureSceneEvidence
            {
                Scene = scene,
                Path = normalized,
                Guid = guid,
                Handle = scene.handle,
                FileDigest = stable.File.Digest,
                FileIdentity = stable.File.Identity,
                MetaDigest = stable.Meta.Digest,
                MetaIdentity = stable.Meta.Identity,
                Dirty = scene.isDirty
            };
        }

        internal static List<Component> GetRootComponents(GameObject host, Type rootType)
        {
            if (host == null || rootType == null || !typeof(Component).IsAssignableFrom(rootType))
            {
                throw new ComponentFeatureWriteException("Component feature root binding is invalid.");
            }
            return host.GetComponents<Component>()
                .Where(component => component != null && component.GetType() == rootType)
                .ToList();
        }

        internal static string ReadManagedReferenceType(Component component)
        {
            using (var serialized = new SerializedObject(component))
            {
                serialized.UpdateIfRequiredOrScript();
                var content = serialized.FindProperty("content");
                if (content == null || content.propertyType != SerializedPropertyType.ManagedReference)
                {
                    throw new ComponentFeatureWriteException("Component feature serialized root is unsupported.");
                }
                return content.managedReferenceFullTypename ?? string.Empty;
            }
        }

        internal static string ComputeFeatureDigest(JObject state)
        {
            if (state == null)
            {
                throw new ComponentFeatureWriteException("Component feature state is unavailable.");
            }
            var kind = state.Value<string>("featureKind") ?? string.Empty;
            var values = new List<string> { FeatureDigestSchema };
            if (state.Value<bool?>("present") == false)
            {
                values.Add("absent");
                values.Add(kind);
                return ComputeFramedDigest(values);
            }
            if (state.Value<bool?>("present") != true)
            {
                throw new ComponentFeatureWriteException("Component feature state presence is invalid.");
            }
            values.Add("present");
            values.Add(kind);
            if (kind == ToggleKind)
            {
                var targets = state["targets"] as JArray
                    ?? throw new ComponentFeatureWriteException("Toggle digest targets are unavailable.");
                values.Add(state.Value<string>("menuPath") ?? string.Empty);
                values.Add(BooleanToken(state.Value<bool>("slider")));
                values.Add(BooleanToken(state.Value<bool>("defaultOn")));
                values.Add(BooleanToken(state.Value<bool>("saved")));
                values.Add(state.Value<string>("globalParameter") ?? string.Empty);
                values.Add(targets.Count.ToString(CultureInfo.InvariantCulture));
                foreach (var target in targets.OfType<JObject>())
                {
                    values.Add(target.Value<string>("objectPath") ?? string.Empty);
                    values.Add(target.Value<string>("objectId") ?? string.Empty);
                }
                return ComputeFramedDigest(values);
            }
            if (kind != ArmatureLinkKind)
            {
                throw new ComponentFeatureWriteException("Component feature digest kind is unsupported.");
            }
            var linkFrom = state["linkFrom"] as JObject
                ?? throw new ComponentFeatureWriteException("Armature-link source digest is unavailable.");
            var links = state["links"] as JArray
                ?? throw new ComponentFeatureWriteException("Armature-link digest targets are unavailable.");
            values.Add(linkFrom.Value<string>("objectPath") ?? string.Empty);
            values.Add(linkFrom.Value<string>("objectId") ?? string.Empty);
            values.Add(BooleanToken(state.Value<bool>("recursive")));
            values.Add(BooleanToken(state.Value<bool>("align")));
            values.Add(links.Count.ToString(CultureInfo.InvariantCulture));
            foreach (var link in links.OfType<JObject>())
            {
                values.Add(link.Value<string>("targetKind") ?? string.Empty);
                values.Add(link.Value<string>("target") ?? string.Empty);
                values.Add(link.Value<string>("objectId") ?? string.Empty);
                values.Add(link.Value<string>("offset") ?? string.Empty);
            }
            return ComputeFramedDigest(values);
        }

        internal static string ComputeCompatibilityDigest(JObject payload)
        {
            var keys = new[]
            {
                "packageName", "packageVersion", "packageFileCount", "packageTotalBytes",
                "packageTreeDigest", "apiAssemblyName", "apiAssemblyVersion",
                "apiAssemblyPublicKeyToken", "apiAssemblySignatureState", "apiAssemblyDigest",
                "runtimeAssemblyName", "runtimeAssemblyVersion", "runtimeAssemblyPublicKeyToken",
                "runtimeAssemblySignatureState", "runtimeAssemblyDigest", "apiSignatureDigest"
            };
            var values = new List<string> { CompatibilityDigestSchema };
            foreach (var key in keys)
            {
                values.Add(key);
                var token = payload[key];
                values.Add(token != null && token.Type == JTokenType.Integer
                    ? token.Value<long>().ToString(CultureInfo.InvariantCulture)
                    : token?.Value<string>() ?? string.Empty);
            }
            return ComputeFramedDigest(values);
        }

        internal static string ComputePreviewDigest(JObject payload)
        {
            var committed = new JObject
            {
                ["schema"] = payload["schema"]?.DeepClone(),
                ["projectPath"] = payload["projectPath"]?.DeepClone(),
                ["compatibility"] = payload["compatibility"]?.DeepClone(),
                ["compatibilityDigestSchema"] = payload["compatibilityDigestSchema"]?.DeepClone(),
                ["compatibilityDigest"] = payload["compatibilityDigest"]?.DeepClone(),
                ["scene"] = payload["scene"]?.DeepClone(),
                ["host"] = payload["host"]?.DeepClone(),
                ["before"] = payload["before"]?.DeepClone(),
                ["target"] = payload["target"]?.DeepClone(),
                ["featureDigestSchema"] = payload["featureDigestSchema"]?.DeepClone(),
                ["beforeFeatureDigest"] = payload["beforeFeatureDigest"]?.DeepClone(),
                ["targetFeatureDigest"] = payload["targetFeatureDigest"]?.DeepClone(),
                ["wouldChange"] = payload["wouldChange"]?.DeepClone()
            };
            return ComputeFramedDigest(new[] { PreviewDigestSchema, CanonicalJson(committed) });
        }

        internal static string CurrentProjectPath()
        {
            return Path.GetFullPath(Path.Combine(Application.dataPath, ".."))
                .TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar);
        }

        internal static bool ProjectPathMatches(string expected)
        {
            if (string.IsNullOrWhiteSpace(expected) || !Path.IsPathRooted(expected))
            {
                return false;
            }
            var comparison = Application.platform == RuntimePlatform.WindowsEditor
                ? StringComparison.OrdinalIgnoreCase
                : StringComparison.Ordinal;
            return string.Equals(
                Path.GetFullPath(expected).TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar),
                CurrentProjectPath(),
                comparison);
        }

        internal static bool SceneEvidenceMatches(
            ComponentFeatureSceneEvidence left,
            ComponentFeatureSceneEvidence right)
        {
            return left != null
                && right != null
                && left.Path == right.Path
                && left.Guid == right.Guid
                && left.Handle == right.Handle
                && left.FileDigest == right.FileDigest
                && left.FileIdentity == right.FileIdentity
                && left.MetaDigest == right.MetaDigest
                && left.MetaIdentity == right.MetaIdentity
                && left.Dirty == right.Dirty;
        }

        internal static GameObject ResolveUniqueGameObject(Scene scene, string rawPath, string label)
        {
            var path = NormalizeHierarchyPath(rawPath, label);
            var matches = new List<GameObject>();
            foreach (var root in scene.GetRootGameObjects().Where(root => root.name == path.Split('/')[0]))
            {
                var current = root.transform;
                var segments = path.Split('/');
                var valid = true;
                for (var index = 1; index < segments.Length; index++)
                {
                    var children = current.Cast<Transform>()
                        .Where(child => child.name == segments[index])
                        .ToList();
                    if (children.Count != 1)
                    {
                        valid = false;
                        break;
                    }
                    current = children[0];
                }
                if (valid)
                {
                    matches.Add(current.gameObject);
                }
            }
            if (matches.Count != 1)
            {
                throw new ComponentFeatureWriteException("The selected " + label + " is missing or ambiguous.");
            }
            return matches[0];
        }

        internal static string StableGlobalObjectId(UnityEngine.Object target, string label)
        {
            var id = GlobalObjectId.GetGlobalObjectIdSlow(target);
            if (id.identifierType == 0)
            {
                throw new ComponentFeatureWriteException("The stable " + label + " identity is unavailable.");
            }
            return id.ToString();
        }

        private static void ResolveRequestObjects(ComponentFeatureRequest request, Scene scene)
        {
            if (request.FeatureKind == ToggleKind)
            {
                request.TargetObjects = request.TargetObjectPaths
                    .Select(path => ResolveUniqueGameObject(scene, path, "toggle target"))
                    .ToList();
                if (request.TargetObjects.Select(target => target.GetInstanceID()).Distinct().Count()
                    != request.TargetObjects.Count)
                {
                    throw new ComponentFeatureWriteException("Toggle target identities must be unique.");
                }
                return;
            }
            request.LinkFrom = ResolveUniqueGameObject(scene, request.LinkFromPath, "armature source");
            request.LinkFromId = StableGlobalObjectId(request.LinkFrom, "armature source");
            foreach (var link in request.LinkTargets)
            {
                if (link.TargetKind == "humanoid_bone")
                {
                    if (!Enum.TryParse(link.Target, false, out HumanBodyBones bone)
                        || bone == HumanBodyBones.LastBone)
                    {
                        throw new ComponentFeatureWriteException("The humanoid bone target is unsupported.");
                    }
                    link.Bone = bone;
                }
                else if (link.TargetKind == "game_object")
                {
                    link.ObjectTarget = ResolveUniqueGameObject(scene, link.Target, "armature target");
                    link.ObjectId = StableGlobalObjectId(link.ObjectTarget, "armature target");
                }
            }
        }

        private static JObject BuildTargetPayload(ComponentFeatureRequest request)
        {
            if (request.FeatureKind == ToggleKind)
            {
                return new JObject
                {
                    ["present"] = true,
                    ["featureKind"] = ToggleKind,
                    ["menuPath"] = request.MenuPath,
                    ["slider"] = request.Slider,
                    ["defaultOn"] = request.DefaultOn,
                    ["saved"] = request.Saved,
                    ["globalParameter"] = request.GlobalParameter,
                    ["targets"] = new JArray(request.TargetObjects.Select((target, index) => new JObject
                    {
                        ["objectPath"] = request.TargetObjectPaths[index],
                        ["objectId"] = StableGlobalObjectId(target, "toggle target")
                    }))
                };
            }
            return new JObject
            {
                ["present"] = true,
                ["featureKind"] = ArmatureLinkKind,
                ["linkFrom"] = new JObject
                {
                    ["objectPath"] = request.LinkFromPath,
                    ["objectId"] = request.LinkFromId
                },
                ["links"] = new JArray(request.LinkTargets.Select(link => new JObject
                {
                    ["targetKind"] = link.TargetKind,
                    ["target"] = link.Target,
                    ["objectId"] = link.ObjectId,
                    ["offset"] = link.Offset
                })),
                ["recursive"] = request.Recursive,
                ["align"] = request.Align
            };
        }

        private static void ConfigureToggle(
            object wrapper,
            ComponentFeatureRequest request,
            ComponentFeatureCompatibility compatibility)
        {
            var type = compatibility.ToggleWrapperType;
            InvokeExact(RequireExactPublicMethod(type, "SetMenuPath", typeof(void), false, typeof(string)), wrapper, request.MenuPath);
            InvokeExact(RequireExactPublicMethod(type, "SetSlider", typeof(void), false, typeof(bool)), wrapper, request.Slider);
            if (request.DefaultOn)
            {
                InvokeExact(RequireExactPublicMethod(type, "SetDefaultOn", typeof(void), false), wrapper);
            }
            if (request.Saved)
            {
                InvokeExact(RequireExactPublicMethod(type, "SetSaved", typeof(void), false), wrapper);
            }
            if (!string.IsNullOrEmpty(request.GlobalParameter))
            {
                InvokeExact(RequireExactPublicMethod(type, "SetGlobalParameter", typeof(void), false, typeof(string)), wrapper, request.GlobalParameter);
            }
            var actions = InvokeExact(
                RequireExactPublicMethod(type, "GetActions", compatibility.ActionSetType, false),
                wrapper);
            if (actions == null || actions.GetType() != compatibility.ActionSetType)
            {
                throw new ComponentFeatureWriteException("Toggle public actions wrapper is unavailable.");
            }
            var addTurnOn = RequireExactPublicMethod(
                compatibility.ActionSetType,
                "AddTurnOn",
                typeof(void),
                false,
                typeof(GameObject));
            foreach (var target in request.TargetObjects)
            {
                InvokeExact(addTurnOn, actions, target);
            }
        }

        private static void ConfigureArmature(
            object wrapper,
            ComponentFeatureRequest request,
            ComponentFeatureCompatibility compatibility)
        {
            var type = compatibility.ArmatureWrapperType;
            InvokeExact(
                RequireExactPublicMethod(type, "LinkFrom", typeof(void), false, typeof(GameObject)),
                wrapper,
                request.LinkFrom);
            foreach (var link in request.LinkTargets)
            {
                if (link.TargetKind == "humanoid_bone")
                {
                    InvokeExact(
                        RequireExactPublicMethod(
                            type,
                            "LinkTo",
                            typeof(void),
                            false,
                            typeof(HumanBodyBones),
                            typeof(string)),
                        wrapper,
                        link.Bone,
                        link.Offset);
                }
                else if (link.TargetKind == "game_object")
                {
                    InvokeExact(
                        RequireExactPublicMethod(
                            type,
                            "LinkTo",
                            typeof(void),
                            false,
                            typeof(GameObject),
                            typeof(string)),
                        wrapper,
                        link.ObjectTarget,
                        link.Offset);
                }
                else
                {
                    InvokeExact(
                        RequireExactPublicMethod(type, "LinkTo", typeof(void), false, typeof(string)),
                        wrapper,
                        link.Target);
                }
            }
            InvokeExact(
                RequireExactPublicMethod(type, "SetRecursive", typeof(void), false, typeof(bool)),
                wrapper,
                request.Recursive);
            InvokeExact(
                RequireExactPublicMethod(type, "SetAlign", typeof(void), false, typeof(bool)),
                wrapper,
                request.Align);
        }

        private static void ValidatePublicApiSurface(
            Type components,
            Type toggle,
            Type armature,
            Type actions)
        {
            RequireExactPublicMethod(components, "CreateToggle", toggle, true, typeof(GameObject));
            RequireExactPublicMethod(components, "CreateArmatureLink", armature, true, typeof(GameObject));
            RequireExactPublicMethod(toggle, "SetMenuPath", typeof(void), false, typeof(string));
            RequireExactPublicMethod(toggle, "SetSlider", typeof(void), false, typeof(bool));
            RequireExactPublicMethod(toggle, "SetDefaultOn", typeof(void), false);
            RequireExactPublicMethod(toggle, "SetSaved", typeof(void), false);
            RequireExactPublicMethod(toggle, "SetGlobalParameter", typeof(void), false, typeof(string));
            RequireExactPublicMethod(toggle, "GetActions", actions, false);
            RequireExactPublicMethod(actions, "AddTurnOn", typeof(void), false, typeof(GameObject));
            RequireExactPublicMethod(armature, "LinkFrom", typeof(void), false, typeof(GameObject));
            RequireExactPublicMethod(armature, "LinkTo", typeof(void), false, typeof(HumanBodyBones), typeof(string));
            RequireExactPublicMethod(armature, "LinkTo", typeof(void), false, typeof(GameObject), typeof(string));
            RequireExactPublicMethod(armature, "LinkTo", typeof(void), false, typeof(string));
            RequireExactPublicMethod(armature, "SetRecursive", typeof(void), false, typeof(bool));
            RequireExactPublicMethod(armature, "SetAlign", typeof(void), false, typeof(bool));
        }

        private static MethodInfo RequireExactPublicMethod(
            Type type,
            string name,
            Type returnType,
            bool requireStatic,
            params Type[] parameters)
        {
            const BindingFlags flags = BindingFlags.Public | BindingFlags.Instance | BindingFlags.Static;
            var matches = type.GetMethods(flags).Where(method =>
                method.Name == name
                && method.IsStatic == requireStatic
                && method.ReturnType == returnType
                && !method.IsGenericMethod
                && method.GetParameters().Select(parameter => parameter.ParameterType)
                    .SequenceEqual(parameters)).ToList();
            if (matches.Count != 1)
            {
                throw new ComponentFeatureWriteException("The component feature public API method signature changed.");
            }
            return matches[0];
        }

        private static object InvokeExact(MethodInfo method, object target, params object[] values)
        {
            try
            {
                return method.Invoke(target, values);
            }
            catch (TargetInvocationException exception)
            {
                throw new ComponentFeatureWriteException(
                    exception.InnerException == null
                        ? "The component feature public API invocation failed."
                        : "The component feature public API rejected the fixed request.");
            }
        }

        private static void EnsureReadbackSchemas(string runtimeAssemblyDigest)
        {
            if (string.IsNullOrWhiteSpace(runtimeAssemblyDigest))
            {
                throw new ComponentFeatureWriteException("The component feature runtime assembly digest is unavailable.");
            }
            lock (ReadbackSchemaLock)
            {
                if (ToggleSchema != null || ArmatureSchema != null)
                {
                    if (ToggleSchema == null
                        || ArmatureSchema == null
                        || RegisteredRuntimeAssemblyDigest != runtimeAssemblyDigest)
                    {
                        throw new ComponentFeatureWriteException(
                            "The component feature readback schema identity changed during this editor session.");
                    }
                    return;
                }
                var toggle = BuildToggleSchema(runtimeAssemblyDigest);
                var armature = BuildArmatureSchema(runtimeAssemblyDigest);
                ToggleSchema = TypedStructuredListCore.RegisterManagedReferenceSchema(toggle);
                ArmatureSchema = TypedStructuredListCore.RegisterManagedReferenceSchema(armature);
                RegisteredRuntimeAssemblyDigest = runtimeAssemblyDigest;
            }
        }

        private static StructuredManagedReferenceSchema BuildToggleSchema(string runtimeAssemblyDigest)
        {
            return new StructuredManagedReferenceSchema
            {
                Id = "vrcforge.component_feature.toggle.readback.v1",
                DigestSchema = "vrcforge.component_feature_managed_readback.v1",
                RootComponentTypeName = RootComponentTypeName,
                RootAssemblyName = RuntimeAssemblyName,
                RootAssemblyVersion = RuntimeAssemblyVersion,
                RootAssemblyPublicKeyToken = RuntimeAssemblyToken,
                RootAssemblySha256 = runtimeAssemblyDigest,
                MemberName = "content",
                AllowNull = false,
                AllowedConcreteTypes = new List<StructuredManagedConcreteTypeSchema>
                {
                    new StructuredManagedConcreteTypeSchema
                    {
                        RuntimeTypeName = "VF.Model.Feature.Toggle",
                        SerializedFullTypeName = ToggleSerializedType,
                        AssemblyName = RuntimeAssemblyName,
                        AssemblyVersion = RuntimeAssemblyVersion,
                        AssemblyPublicKeyToken = RuntimeAssemblyToken,
                        AssemblySha256 = runtimeAssemblyDigest,
                        RequireExactDirectFieldLayout = false,
                        Fields = new List<StructuredManagedFieldSchema>
                        {
                            StringField("menuPath", "name", 2048),
                            BooleanField("slider", "slider"),
                            BooleanField("defaultOn", "defaultOn"),
                            BooleanField("saved", "saved"),
                            BooleanField("useGlobalParameter", "useGlobalParam"),
                            StringField("globalParameter", "globalParam", 128)
                        },
                        Collections = new List<StructuredManagedCollectionSchema>
                        {
                            new StructuredManagedCollectionSchema
                            {
                                CanonicalKey = "targets",
                                RelativePath = "state.actions",
                                Kind = StructuredManagedCollectionKind.ManagedReferenceList,
                                MaximumItems = 32,
                                DeclaredElementTypeName = "VF.Model.StateAction.Action",
                                AllowedManagedElementTypes = new List<StructuredManagedConcreteTypeSchema>
                                {
                                    new StructuredManagedConcreteTypeSchema
                                    {
                                        RuntimeTypeName = "VF.Model.StateAction.ObjectToggleAction",
                                        SerializedFullTypeName = "VRCFury VF.Model.StateAction.ObjectToggleAction",
                                        AssemblyName = RuntimeAssemblyName,
                                        AssemblyVersion = RuntimeAssemblyVersion,
                                        AssemblyPublicKeyToken = RuntimeAssemblyToken,
                                        AssemblySha256 = runtimeAssemblyDigest,
                                        RequireExactDirectFieldLayout = false,
                                        Fields = new List<StructuredManagedFieldSchema>
                                        {
                                            ObjectField("object", "obj", "UnityEngine.GameObject", false),
                                            EnumField("mode", "mode")
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
            };
        }

        private static StructuredManagedReferenceSchema BuildArmatureSchema(string runtimeAssemblyDigest)
        {
            return new StructuredManagedReferenceSchema
            {
                Id = "vrcforge.component_feature.armature_link.readback.v1",
                DigestSchema = "vrcforge.component_feature_managed_readback.v1",
                RootComponentTypeName = RootComponentTypeName,
                RootAssemblyName = RuntimeAssemblyName,
                RootAssemblyVersion = RuntimeAssemblyVersion,
                RootAssemblyPublicKeyToken = RuntimeAssemblyToken,
                RootAssemblySha256 = runtimeAssemblyDigest,
                MemberName = "content",
                AllowNull = false,
                AllowedConcreteTypes = new List<StructuredManagedConcreteTypeSchema>
                {
                    new StructuredManagedConcreteTypeSchema
                    {
                        RuntimeTypeName = "VF.Model.Feature.ArmatureLink",
                        SerializedFullTypeName = ArmatureSerializedType,
                        AssemblyName = RuntimeAssemblyName,
                        AssemblyVersion = RuntimeAssemblyVersion,
                        AssemblyPublicKeyToken = RuntimeAssemblyToken,
                        AssemblySha256 = runtimeAssemblyDigest,
                        RequireExactDirectFieldLayout = false,
                        Fields = new List<StructuredManagedFieldSchema>
                        {
                            ObjectField("linkFrom", "propBone", "UnityEngine.GameObject", false),
                            BooleanField("recursive", "recursive"),
                            BooleanField("alignPosition", "alignPosition"),
                            BooleanField("alignRotation", "alignRotation"),
                            BooleanField("alignScale", "alignScale")
                        },
                        Collections = new List<StructuredManagedCollectionSchema>
                        {
                            new StructuredManagedCollectionSchema
                            {
                                CanonicalKey = "links",
                                RelativePath = "linkTo",
                                Kind = StructuredManagedCollectionKind.TypedList,
                                MaximumItems = 8,
                                DeclaredElementTypeName = "VF.Model.Feature.ArmatureLink+LinkTo",
                                TypedElementRuntimeTypeName = "VF.Model.Feature.ArmatureLink+LinkTo",
                                TypedElementAssemblyName = RuntimeAssemblyName,
                                TypedElementAssemblyVersion = RuntimeAssemblyVersion,
                                TypedElementAssemblyPublicKeyToken = RuntimeAssemblyToken,
                                TypedElementAssemblySha256 = runtimeAssemblyDigest,
                                RequireExactElementFieldLayout = true,
                                TypedElementFields = new List<StructuredManagedFieldSchema>
                                {
                                    BooleanField("useBone", "useBone"),
                                    EnumField("bone", "bone"),
                                    BooleanField("useObject", "useObj"),
                                    ObjectField("object", "obj", "UnityEngine.GameObject", true),
                                    StringField("offset", "offset", 512)
                                }
                            }
                        }
                    }
                }
            };
        }

        private static StructuredManagedFieldSchema BooleanField(string key, string path)
        {
            return new StructuredManagedFieldSchema
            {
                CanonicalKey = key,
                RelativePath = path,
                Kind = StructuredManagedValueKind.Boolean
            };
        }

        private static StructuredManagedFieldSchema EnumField(string key, string path)
        {
            return new StructuredManagedFieldSchema
            {
                CanonicalKey = key,
                RelativePath = path,
                Kind = StructuredManagedValueKind.EnumInt32
            };
        }

        private static StructuredManagedFieldSchema StringField(string key, string path, int maximum)
        {
            return new StructuredManagedFieldSchema
            {
                CanonicalKey = key,
                RelativePath = path,
                Kind = StructuredManagedValueKind.String,
                MaximumStringLength = maximum
            };
        }

        private static StructuredManagedFieldSchema ObjectField(
            string key,
            string path,
            string type,
            bool allowNull)
        {
            return new StructuredManagedFieldSchema
            {
                CanonicalKey = key,
                RelativePath = path,
                Kind = StructuredManagedValueKind.ObjectReference,
                ObjectTypeName = type,
                AllowNull = allowNull
            };
        }

        private static string FieldString(StructuredManagedReferenceReadPlan plan, string key)
        {
            return plan.RequireField(key).CanonicalValue;
        }

        private static bool FieldBoolean(StructuredManagedReferenceReadPlan plan, string key)
        {
            var value = plan.RequireField(key).CanonicalValue;
            if (value == "true")
            {
                return true;
            }
            if (value == "false")
            {
                return false;
            }
            throw new ComponentFeatureWriteException("Component feature boolean readback is invalid.");
        }

        private static string ExpectedSerializedType(string kind)
        {
            return kind == ToggleKind ? ToggleSerializedType : ArmatureSerializedType;
        }

        private static Assembly ResolveSingleAssembly(string name)
        {
            var matches = AppDomain.CurrentDomain.GetAssemblies()
                .Where(assembly => assembly.GetName().Name == name)
                .ToList();
            if (matches.Count != 1)
            {
                throw new ComponentFeatureWriteException("The component feature compatibility assembly is missing or ambiguous.");
            }
            return matches[0];
        }

        private static Type RequireType(Assembly assembly, string fullName)
        {
            var type = assembly?.GetType(fullName, false, false);
            if (type == null)
            {
                throw new ComponentFeatureWriteException("The component feature compatibility type is unavailable.");
            }
            return type;
        }

        private static string ValidateAssemblyIdentity(
            Assembly assembly,
            string expectedName,
            string expectedVersion,
            string expectedToken)
        {
            var name = assembly.GetName();
            var token = string.Concat((name.GetPublicKeyToken() ?? Array.Empty<byte>())
                .Select(value => value.ToString("x2", CultureInfo.InvariantCulture)));
            var signatureState = token.Length == 0 ? "unsigned" : "strong_name_signed";
            var projectRoot = Path.GetFullPath(Path.GetDirectoryName(Application.dataPath) ?? string.Empty);
            var scriptAssemblyRoot = Path.GetFullPath(Path.Combine(projectRoot, "Library", "ScriptAssemblies"));
            var assemblyPath = string.IsNullOrWhiteSpace(assembly.Location)
                ? string.Empty
                : Path.GetFullPath(assembly.Location);
            if (name.Name != expectedName
                || (name.Version?.ToString() ?? string.Empty) != expectedVersion
                || token != expectedToken
                || signatureState != "unsigned"
                || string.IsNullOrWhiteSpace(assemblyPath)
                || !string.Equals(
                    Path.GetDirectoryName(assemblyPath),
                    scriptAssemblyRoot,
                    StringComparison.OrdinalIgnoreCase)
                || !Path.GetFileName(assemblyPath).Equals(
                    expectedName + ".dll",
                    StringComparison.Ordinal)
                || !File.Exists(assemblyPath))
            {
                throw new ComponentFeatureWriteException("The component feature assembly identity is unsupported.");
            }
            EnsureOrdinaryFile(assemblyPath, "component feature assembly");
            return ComputeFileSha256(assemblyPath);
        }

        private static PackageTreeEvidence ComputePackageTreeEvidence(string root)
        {
            if (!Directory.Exists(root)
                || (File.GetAttributes(root) & FileAttributes.ReparsePoint) != 0)
            {
                throw new ComponentFeatureWriteException("The component feature package root is unavailable.");
            }
            foreach (var directory in Directory.EnumerateDirectories(root, "*", SearchOption.AllDirectories))
            {
                if ((File.GetAttributes(directory) & FileAttributes.ReparsePoint) != 0)
                {
                    throw new ComponentFeatureWriteException("The component feature package contains a linked directory.");
                }
            }
            var files = Directory.EnumerateFiles(root, "*", SearchOption.AllDirectories)
                .Select(path => new
                {
                    FullPath = path,
                    Relative = path.Substring(root.Length).TrimStart(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar)
                        .Replace('\\', '/')
                })
                .OrderBy(item => item.Relative, StringComparer.Ordinal)
                .ToList();
            var value = new StringBuilder();
            AppendDigestField(value, PackageTreeDigestSchema);
            AppendDigestField(value, files.Count.ToString(CultureInfo.InvariantCulture));
            long totalBytes = 0;
            foreach (var file in files)
            {
                EnsureOrdinaryFile(file.FullPath, "package file");
                var length = new FileInfo(file.FullPath).Length;
                totalBytes = checked(totalBytes + length);
                AppendDigestField(value, file.Relative);
                AppendDigestField(value, length.ToString(CultureInfo.InvariantCulture));
                AppendDigestField(value, ComputeFileSha256(file.FullPath));
            }
            return new PackageTreeEvidence
            {
                FileCount = files.Count,
                TotalBytes = totalBytes,
                Digest = Sha256Text(value.ToString())
            };
        }

        private static string NormalizeScenePath(string value)
        {
            var normalized = (value ?? string.Empty).Trim().Replace('\\', '/');
            var parts = normalized.Split('/');
            if (!normalized.StartsWith("Assets/", StringComparison.Ordinal)
                || !normalized.EndsWith(".unity", StringComparison.OrdinalIgnoreCase)
                || normalized.StartsWith("/", StringComparison.Ordinal)
                || normalized.EndsWith("/", StringComparison.Ordinal)
                || parts.Any(part => string.IsNullOrWhiteSpace(part) || part == "." || part == ".."))
            {
                throw new ComponentFeatureWriteException("scenePath must select a saved scene under Assets/.");
            }
            return normalized;
        }

        private static string NormalizeHierarchyPath(string value, string label)
        {
            var normalized = (value ?? string.Empty).Trim();
            var parts = normalized.Split('/');
            if (normalized.Length == 0
                || normalized.Length > 2048
                || normalized.IndexOf('\\') >= 0
                || normalized.StartsWith("/", StringComparison.Ordinal)
                || normalized.EndsWith("/", StringComparison.Ordinal)
                || parts.Any(part => string.IsNullOrWhiteSpace(part) || part == "." || part == ".."))
            {
                throw new ComponentFeatureWriteException(label + " path is invalid.");
            }
            return normalized;
        }

        private static void EnsureOrdinaryFile(string path, string label)
        {
            if (!File.Exists(path) || (File.GetAttributes(path) & FileAttributes.ReparsePoint) != 0)
            {
                throw new ComponentFeatureWriteException("The " + label + " is missing or linked.");
            }
        }

        private static string ComputeFileSha256(string path)
        {
            using (var sha256 = SHA256.Create())
            using (var stream = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.Read))
            {
                return BitConverter.ToString(sha256.ComputeHash(stream))
                    .Replace("-", string.Empty)
                    .ToLowerInvariant();
            }
        }

        private static string ComputeFramedDigest(IEnumerable<string> fields)
        {
            var value = new StringBuilder();
            foreach (var field in fields)
            {
                AppendDigestField(value, field);
            }
            return Sha256Text(value.ToString());
        }

        private static void AppendDigestField(StringBuilder target, string value)
        {
            var safe = value ?? string.Empty;
            target.Append(safe.Length.ToString(CultureInfo.InvariantCulture))
                .Append(':')
                .Append(safe);
        }

        private static string Sha256Text(string value)
        {
            using (var sha256 = SHA256.Create())
            {
                return BitConverter.ToString(sha256.ComputeHash(Encoding.UTF8.GetBytes(value)))
                    .Replace("-", string.Empty)
                    .ToLowerInvariant();
            }
        }

        private static string CanonicalJson(JToken token)
        {
            if (token == null || token.Type == JTokenType.Null)
            {
                return "null";
            }
            if (token is JObject obj)
            {
                return "{" + string.Join(",", obj.Properties()
                    .OrderBy(property => property.Name, StringComparer.Ordinal)
                    .Select(property => JsonConvert.ToString(property.Name) + ":" + CanonicalJson(property.Value))) + "}";
            }
            if (token is JArray array)
            {
                return "[" + string.Join(",", array.Select(CanonicalJson)) + "]";
            }
            if (token.Type == JTokenType.String)
            {
                return JsonConvert.ToString(token.Value<string>() ?? string.Empty);
            }
            if (token.Type == JTokenType.Boolean)
            {
                return token.Value<bool>() ? "true" : "false";
            }
            if (token.Type == JTokenType.Integer)
            {
                return Convert.ToString(((JValue)token).Value, CultureInfo.InvariantCulture);
            }
            throw new ComponentFeatureWriteException("Component feature preview contains a non-canonical value.");
        }

        private static string BooleanToken(bool value)
        {
            return value ? "true" : "false";
        }

        private sealed class PackageTreeEvidence
        {
            internal int FileCount;
            internal long TotalBytes;
            internal string Digest = string.Empty;
        }
    }
}
