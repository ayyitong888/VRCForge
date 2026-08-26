using System;
using System.Collections.Generic;
using System.Linq;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.Animations;
using VRC.Dynamics;
using VRC.SDK3.Avatars;
using VRC.SDK3.Avatars.Components;
using VRC.SDK3.Dynamics.Constraint.Components;
using VRCForge.Core.MCP;

namespace VRCForge.Editor
{
    [VRCForgeCommand(
        toolId: "vrc_convert_unity_constraint",
        Summary = "when-to-use: preview or convert one exact Unity IConstraint on one saved-scene avatar to the SDK-equivalent VRChat constraint, including SDK animation rebinding. when-NOT-to-use: do not bulk-convert an avatar, infer a component selector, convert an already-VRChat constraint, or run while animation preview is active. Negative example: do not call it merely because the SDK panel shows an unrelated warning."
    )]
    public static class VrchatConstraintConversionTool
    {
        public sealed class Parameters
        {
            [VRCForgeInput("Saved scene asset path.", IsRequired = true)] public string scenePath { get; set; } = string.Empty;
            [VRCForgeInput("Exact active Unity project root.", IsRequired = true)] public string projectPath { get; set; } = string.Empty;
            [VRCForgeInput("Exact avatar hierarchy path.", IsRequired = true)] public string avatarPath { get; set; } = string.Empty;
            [VRCForgeInput("Exact constraint host hierarchy path.", IsRequired = true)] public string gameObjectPath { get; set; } = string.Empty;
            [VRCForgeInput("Concrete Unity constraint component type.", IsRequired = true)] public string componentType { get; set; } = string.Empty;
            [VRCForgeInput("Zero-based matching constraint component index.", IsRequired = true)] public int componentIndex { get; set; }
            [VRCForgeInput("Return a verified non-mutating preview.", IsRequired = true)] public bool preview { get; set; }
            [VRCForgeInput("Save the scene during conversion; required for apply.", IsRequired = true)] public bool saveScene { get; set; }
            [VRCForgeInput("Expected avatar identity from preview.", IsRequired = false)] public string expectedAvatarGlobalObjectId { get; set; } = string.Empty;
            [VRCForgeInput("Expected component identity from preview.", IsRequired = false)] public string expectedComponentGlobalObjectId { get; set; } = string.Empty;
            [VRCForgeInput("Expected scene GUID from preview.", IsRequired = false)] public string expectedSceneGuid { get; set; } = string.Empty;
            [VRCForgeInput("Expected scene file digest from preview.", IsRequired = false)] public string expectedSceneFileDigest { get; set; } = string.Empty;
            [VRCForgeInput("Expected pre-conversion digest from preview.", IsRequired = false)] public string expectedBeforeDigest { get; set; } = string.Empty;
        }

        public const string ToolName = "vrc_convert_unity_constraint";
        private const string Schema = "vrcforge.vrchat_constraint_conversion.v1";
        private const int ConsoleEntryLimit = 120;

        private static readonly Dictionary<string, Type> UnityTypes =
            new Dictionary<string, Type>(StringComparer.Ordinal)
            {
                { typeof(PositionConstraint).FullName, typeof(PositionConstraint) },
                { typeof(RotationConstraint).FullName, typeof(RotationConstraint) },
                { typeof(ScaleConstraint).FullName, typeof(ScaleConstraint) },
                { typeof(ParentConstraint).FullName, typeof(ParentConstraint) },
                { typeof(AimConstraint).FullName, typeof(AimConstraint) },
                { typeof(LookAtConstraint).FullName, typeof(LookAtConstraint) },
            };

        private static readonly Dictionary<Type, Type> VrcTypes =
            new Dictionary<Type, Type>
            {
                { typeof(PositionConstraint), typeof(VRCPositionConstraint) },
                { typeof(RotationConstraint), typeof(VRCRotationConstraint) },
                { typeof(ScaleConstraint), typeof(VRCScaleConstraint) },
                { typeof(ParentConstraint), typeof(VRCParentConstraint) },
                { typeof(AimConstraint), typeof(VRCAimConstraint) },
                { typeof(LookAtConstraint), typeof(VRCLookAtConstraint) },
            };

        public static object HandleCommand(JObject @params)
        {
            var raw = @params ?? new JObject();
            var consoleBefore = UnityConsoleSnapshotReader.Capture(ConsoleEntryLimit);
            try
            {
                CheckpointPrepareTool.ValidateProject(raw);
                var snapshot = Inspect(raw);
                if (ReadBool(raw, "preview", false))
                {
                    return VRCForgeToolResult.Completed(
                        "Inspected one exact Unity constraint conversion without changing the project.",
                        Payload(snapshot, null, true, false, false, false, consoleBefore));
                }

                if (!ReadBool(raw, "saveScene", false))
                {
                    throw new InvalidOperationException("saveScene must be true for constraint conversion.");
                }
                RequireExact(raw, "expectedSceneGuid", snapshot.SceneGuid);
                RequireExact(raw, "expectedSceneFileDigest", snapshot.SceneFileDigest);
                RequireExact(raw, "expectedAvatarGlobalObjectId", snapshot.AvatarGlobalObjectId);
                RequireExact(raw, "expectedComponentGlobalObjectId", snapshot.ComponentGlobalObjectId);
                RequireExact(raw, "expectedBeforeDigest", snapshot.BeforeDigest);

                var immediate = Inspect(raw);
                if (!string.Equals(immediate.BeforeDigest, snapshot.BeforeDigest, StringComparison.Ordinal))
                {
                    throw new InvalidOperationException("The Unity constraint changed after preview.");
                }

                var mutationStarted = false;
                try
                {
                    mutationStarted = true;
                    var issuesGenerated = AvatarDynamicsSetup.DoConvertUnityConstraints(
                        new[] { immediate.Constraint },
                        immediate.Avatar,
                        true);
                    EditorSceneManager.MarkSceneDirty(immediate.Scene.Scene);
                    if (!EditorSceneManager.SaveScene(immediate.Scene.Scene))
                    {
                        throw new InvalidOperationException("The converted constraint scene could not be saved.");
                    }
                    var readback = Readback(immediate);
                    var payload = Payload(
                        immediate,
                        readback,
                        false,
                        true,
                        true,
                        issuesGenerated,
                        consoleBefore);
                    if (issuesGenerated)
                    {
                        payload["ok"] = false;
                        payload["errorCode"] = "vrchat_constraint_conversion_completed_with_issues";
                        payload["error"] = "The VRChat SDK converted the constraint but reported one or more issues; inspect consoleDelta before continuing.";
                        payload["commitState"] = "committed_with_issues";
                        return VRCForgeToolResult.FailedWithCode(
                            "vrchat_constraint_conversion_completed_with_issues",
                            (string)payload["error"],
                            payload);
                    }
                    return VRCForgeToolResult.Completed(
                        "Converted one exact Unity constraint to the equivalent VRChat constraint and saved the scene.",
                        payload);
                }
                catch (Exception exception) when (mutationStarted)
                {
                    var after = UnityConsoleSnapshotReader.Capture(ConsoleEntryLimit);
                    return VRCForgeToolResult.FailedWithCode(
                        "vrchat_constraint_conversion_failed_after_mutation",
                        exception.Message ?? "VRChat constraint conversion failed after mutation started.",
                        new JObject
                        {
                            ["ok"] = false,
                            ["schema"] = Schema,
                            ["operation"] = "convert_unity_constraint",
                            ["failureLayer"] = "vrchat_sdk_constraint_converter",
                            ["failurePhase"] = "convert_or_save",
                            ["mutationStarted"] = true,
                            ["writeOccurred"] = true,
                            ["committed"] = false,
                            ["commitState"] = "unknown",
                            ["requestMayHaveCommitted"] = true,
                            ["checkpointRecoveryRequired"] = true,
                            ["automaticRollbackAttempted"] = false,
                            ["consoleBefore"] = consoleBefore,
                            ["consoleAfter"] = after,
                            ["consoleDelta"] = VrchatAvatarUploadShared.ConsoleDelta(consoleBefore, after),
                            ["exceptionType"] = exception.GetType().FullName ?? exception.GetType().Name,
                            ["exceptionMessage"] = exception.Message ?? string.Empty,
                        });
                }
            }
            catch (Exception exception)
            {
                var after = UnityConsoleSnapshotReader.Capture(ConsoleEntryLimit);
                return VRCForgeToolResult.FailedWithCode(
                    "vrchat_constraint_conversion_preflight_failed",
                    exception.Message ?? "VRChat constraint conversion preflight failed.",
                    new JObject
                    {
                        ["ok"] = false,
                        ["schema"] = Schema,
                        ["operation"] = "convert_unity_constraint",
                        ["failureLayer"] = "vrchat_constraint_conversion_preflight",
                        ["failurePhase"] = "inspect",
                        ["mutationStarted"] = false,
                        ["writeOccurred"] = false,
                        ["committed"] = false,
                        ["commitState"] = "not_started",
                        ["requestMayHaveCommitted"] = false,
                        ["checkpointRecoveryRequired"] = false,
                        ["automaticRollbackAttempted"] = false,
                        ["consoleBefore"] = consoleBefore,
                        ["consoleAfter"] = after,
                        ["consoleDelta"] = VrchatAvatarUploadShared.ConsoleDelta(consoleBefore, after),
                        ["exceptionType"] = exception.GetType().FullName ?? exception.GetType().Name,
                        ["exceptionMessage"] = exception.Message ?? string.Empty,
                    });
            }
        }

        private static Snapshot Inspect(JObject raw)
        {
            var scenePath = Required(raw, "scenePath", 1024);
            var avatarPath = Required(raw, "avatarPath", 2048);
            var gameObjectPath = Required(raw, "gameObjectPath", 4096);
            var componentTypeName = Required(raw, "componentType", 256);
            var componentIndex = ReadInt(raw, "componentIndex", 0, 31);
            Type componentType;
            if (!UnityTypes.TryGetValue(componentTypeName, out componentType))
            {
                throw new InvalidOperationException("componentType must be one supported UnityEngine.Animations constraint type.");
            }

            var scene = SceneObjectCopyCore.ResolveSavedScene(scenePath, "constraint conversion scene");
            var stable = SceneObjectCopyCore.ReadStableAssetEvidence(scene.Path, "constraint conversion scene");
            if (stable.File.LinkCount != 1 || stable.Meta.LinkCount != 1 || scene.Scene.isDirty)
            {
                throw new InvalidOperationException("The constraint scene must be uniquely linked, saved, and clean before conversion.");
            }
            var avatar = VrchatAvatarUploadShared.ResolveExactAvatar(avatarPath);
            if (avatar.gameObject.scene.handle != scene.Scene.handle)
            {
                throw new InvalidOperationException("avatarPath does not belong to scenePath.");
            }
            var host = SceneObjectCopyCore.ResolveUniqueGameObject(scene.Scene, gameObjectPath, "constraint host");
            var components = host.GetComponents(componentType);
            if (componentIndex >= components.Length || components[componentIndex] == null)
            {
                throw new InvalidOperationException("The exact Unity constraint selector did not resolve.");
            }
            var constraint = components[componentIndex] as IConstraint;
            if (constraint == null)
            {
                throw new InvalidOperationException("The selected component no longer implements Unity IConstraint.");
            }
            var sources = new JArray();
            for (var index = 0; index < constraint.sourceCount; index++)
            {
                var source = constraint.GetSource(index);
                sources.Add(new JObject
                {
                    ["index"] = index,
                    ["sourcePath"] = source.sourceTransform == null
                        ? string.Empty
                        : AvatarAuthoringCrudCore.GetTransformPath(source.sourceTransform),
                    ["weight"] = source.weight,
                });
            }
            var component = (Component)constraint;
            var componentGlobalId = GlobalObjectId.GetGlobalObjectIdSlow(component).ToString();
            var avatarGlobalId = GlobalObjectId.GetGlobalObjectIdSlow(avatar.gameObject).ToString();
            var vrcType = VrcTypes[componentType];
            var digestMaterial = new JObject
            {
                ["sceneGuid"] = stable.Guid,
                ["sceneFileDigest"] = stable.File.Digest,
                ["avatarGlobalObjectId"] = avatarGlobalId,
                ["gameObjectPath"] = gameObjectPath,
                ["componentType"] = componentType.FullName,
                ["componentIndex"] = componentIndex,
                ["componentGlobalObjectId"] = componentGlobalId,
                ["weight"] = constraint.weight,
                ["constraintActive"] = constraint.constraintActive,
                ["locked"] = constraint.locked,
                ["sources"] = sources,
            };
            return new Snapshot
            {
                Scene = scene,
                SceneGuid = stable.Guid,
                SceneFileDigest = stable.File.Digest,
                SceneMetaDigest = stable.Meta.Digest,
                Avatar = avatar,
                AvatarPath = AvatarAuthoringCrudCore.GetTransformPath(avatar.transform),
                AvatarGlobalObjectId = avatarGlobalId,
                GameObjectPath = gameObjectPath,
                Host = host,
                ComponentType = componentType,
                ComponentIndex = componentIndex,
                ComponentGlobalObjectId = componentGlobalId,
                VrcComponentType = vrcType,
                UnityComponentCountBefore = components.Length,
                VrcComponentCountBefore = host.GetComponents(vrcType).Length,
                Constraint = constraint,
                Sources = sources,
                Weight = constraint.weight,
                ConstraintActive = constraint.constraintActive,
                Locked = constraint.locked,
                BeforeDigest = StableHash(digestMaterial.ToString(Newtonsoft.Json.Formatting.None)),
            };
        }

        private static JObject Readback(Snapshot before)
        {
            var unityCount = before.Host.GetComponents(before.ComponentType).Length;
            var vrcComponents = before.Host.GetComponents(before.VrcComponentType);
            if (unityCount != before.UnityComponentCountBefore - 1
                || vrcComponents.Length != before.VrcComponentCountBefore + 1)
            {
                throw new InvalidOperationException("Constraint conversion readback did not find exactly one replacement.");
            }
            var replacement = vrcComponents[vrcComponents.Length - 1];
            if (replacement == null)
            {
                throw new InvalidOperationException("The replacement VRChat constraint is missing.");
            }
            var saved = SceneObjectCopyCore.ReadStableAssetEvidence(before.Scene.Path, "converted constraint scene");
            if (saved.Guid != before.SceneGuid
                || saved.Meta.Digest != before.SceneMetaDigest
                || saved.File.Digest == before.SceneFileDigest
                || before.Scene.Scene.isDirty)
            {
                throw new InvalidOperationException("Converted constraint scene identity or save readback is invalid.");
            }
            return new JObject
            {
                ["unityComponentCount"] = unityCount,
                ["vrcComponentCount"] = vrcComponents.Length,
                ["vrcComponentType"] = before.VrcComponentType.FullName,
                ["vrcComponentGlobalObjectId"] = GlobalObjectId.GetGlobalObjectIdSlow(replacement).ToString(),
                ["sceneFileDigest"] = saved.File.Digest,
                ["sceneDirty"] = before.Scene.Scene.isDirty,
            };
        }

        private static JObject Payload(
            Snapshot before,
            JObject readback,
            bool preview,
            bool mutationStarted,
            bool committed,
            bool issuesGenerated,
            JObject consoleBefore)
        {
            var after = UnityConsoleSnapshotReader.Capture(ConsoleEntryLimit);
            return new JObject
            {
                ["ok"] = true,
                ["schema"] = Schema,
                ["operation"] = "convert_unity_constraint",
                ["preview"] = preview,
                ["scenePath"] = before.Scene.Path,
                ["sceneGuid"] = before.SceneGuid,
                ["sceneFileDigestBefore"] = before.SceneFileDigest,
                ["avatarPath"] = before.AvatarPath,
                ["avatarGlobalObjectId"] = before.AvatarGlobalObjectId,
                ["gameObjectPath"] = before.GameObjectPath,
                ["componentTypeBefore"] = before.ComponentType.FullName,
                ["componentIndex"] = before.ComponentIndex,
                ["componentGlobalObjectIdBefore"] = before.ComponentGlobalObjectId,
                ["componentTypeAfter"] = before.VrcComponentType.FullName,
                ["weight"] = before.Weight,
                ["constraintActive"] = before.ConstraintActive,
                ["locked"] = before.Locked,
                ["sources"] = before.Sources.DeepClone(),
                ["beforeDigest"] = before.BeforeDigest,
                ["convertReferencedAnimationClips"] = true,
                ["animationBindingCoverage"] = "vrchat_sdk_converter",
                ["sdkIssuesGenerated"] = issuesGenerated,
                ["readback"] = readback == null ? JValue.CreateNull() : readback.DeepClone(),
                ["toolRoutingStarted"] = true,
                ["mutationStarted"] = mutationStarted,
                ["writeOccurred"] = mutationStarted,
                ["committed"] = committed,
                ["commitState"] = committed ? "committed" : "not_started",
                ["checkpointRecoveryRequired"] = false,
                ["automaticRollbackAttempted"] = false,
                ["consoleBefore"] = consoleBefore,
                ["consoleAfter"] = after,
                ["consoleDelta"] = VrchatAvatarUploadShared.ConsoleDelta(consoleBefore, after),
            };
        }

        private static string Required(JObject raw, string name, int maximum)
        {
            var token = raw[name];
            if (token == null || token.Type != JTokenType.String)
            {
                throw new InvalidOperationException(name + " is required and must be a string.");
            }
            var value = ((string)token ?? string.Empty).Trim();
            if (value.Length == 0 || value.Length > maximum)
            {
                throw new InvalidOperationException(name + " is empty or too long.");
            }
            return value;
        }

        private static int ReadInt(JObject raw, string name, int minimum, int maximum)
        {
            var token = raw[name];
            if (token == null || token.Type != JTokenType.Integer)
            {
                throw new InvalidOperationException(name + " must be an integer.");
            }
            var value = (int)token;
            if (value < minimum || value > maximum)
            {
                throw new InvalidOperationException(name + " is outside the supported range.");
            }
            return value;
        }

        private static bool ReadBool(JObject raw, string name, bool fallback)
        {
            var token = raw[name];
            if (token == null || token.Type == JTokenType.Null) return fallback;
            if (token.Type != JTokenType.Boolean)
            {
                throw new InvalidOperationException(name + " must be a boolean.");
            }
            return (bool)token;
        }

        private static void RequireExact(JObject raw, string name, string actual)
        {
            var expected = Required(raw, name, 4096);
            if (!string.Equals(expected, actual ?? string.Empty, StringComparison.Ordinal))
            {
                throw new InvalidOperationException(name + " no longer matches the current constraint state.");
            }
        }

        private static string StableHash(string value)
        {
            using (var sha = System.Security.Cryptography.SHA256.Create())
            {
                return BitConverter.ToString(
                    sha.ComputeHash(System.Text.Encoding.UTF8.GetBytes(value ?? string.Empty)))
                    .Replace("-", string.Empty)
                    .ToLowerInvariant();
            }
        }

        private sealed class Snapshot
        {
            internal SavedSceneSnapshot Scene;
            internal string SceneGuid = string.Empty;
            internal string SceneFileDigest = string.Empty;
            internal string SceneMetaDigest = string.Empty;
            internal VRCAvatarDescriptor Avatar;
            internal string AvatarPath = string.Empty;
            internal string AvatarGlobalObjectId = string.Empty;
            internal string GameObjectPath = string.Empty;
            internal GameObject Host;
            internal Type ComponentType;
            internal int ComponentIndex;
            internal string ComponentGlobalObjectId = string.Empty;
            internal Type VrcComponentType;
            internal int UnityComponentCountBefore;
            internal int VrcComponentCountBefore;
            internal IConstraint Constraint;
            internal JArray Sources = new JArray();
            internal float Weight;
            internal bool ConstraintActive;
            internal bool Locked;
            internal string BeforeDigest = string.Empty;
        }
    }
}
