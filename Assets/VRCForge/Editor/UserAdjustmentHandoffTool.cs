using System;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Security.Cryptography;
using System.Text;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using VRCForge.Core.MCP;

namespace VRCForge.Editor
{
    [VRCForgeCommand(
        toolId: "vrc_user_adjustment_handoff",
        Summary = "when-to-use: prepare, finalize, or abort one exact supervised user-drag proxy. when-NOT-to-use: never resolve by hierarchy path or continue after Scene, Avatar, target, or proxy identity drift.")]
    public static class UserAdjustmentHandoffTool
    {
        private const string ResultSchema = "vrcforge.user_adjustment_handoff.v1";
        private const string StateRoot = "Library/VRCForge/adjustment-handoffs";

        public sealed class Parameters
        {
            [VRCForgeInput("prepare, finalize, or abort.", IsRequired = true)] public string action { get; set; } = "";
            [VRCForgeInput("transform, skinned_bone_proxy, constraint_bound, or physbone_chain.", IsRequired = true)] public string mode { get; set; } = "";
            [VRCForgeInput("Exact avatar root GlobalObjectId.", IsRequired = true)] public string avatarGlobalObjectId { get; set; } = "";
            [VRCForgeInput("Exact target GameObject GlobalObjectId.", IsRequired = true)] public string targetGlobalObjectId { get; set; } = "";
            [VRCForgeInput("Exact handoff id for finalize or abort.", IsRequired = false)] public string handoffId { get; set; } = "";
            [VRCForgeInput("Return a non-mutating preview.", IsRequired = false)] public bool? preview { get; set; } = false;
            [VRCForgeInput("Expected project root from preview.", IsRequired = false)] public string expectedProjectPath { get; set; } = "";
            [VRCForgeInput("Expected preview digest.", IsRequired = false)] public string expectedPreviewDigest { get; set; } = "";
            [VRCForgeInput("Expected Scene path.", IsRequired = false)] public string expectedScenePath { get; set; } = "";
            [VRCForgeInput("Expected Scene GUID.", IsRequired = false)] public string expectedSceneGuid { get; set; } = "";
            [VRCForgeInput("Expected hierarchy digest.", IsRequired = false)] public string expectedHierarchyDigest { get; set; } = "";
            [VRCForgeInput("Expected proxy GlobalObjectId.", IsRequired = false)] public string expectedProxyGlobalObjectId { get; set; } = "";
        }

        public static object HandleCommand(JObject raw)
        {
            var p = (raw ?? new JObject()).ToObject<Parameters>() ?? new Parameters();
            var action = (p.action ?? "").Trim();
            var mutationStarted = false;
            try
            {
                action = Choice(action, "action", "prepare", "finalize", "abort");
                var mode = Choice(p.mode, "mode", "transform", "skinned_bone_proxy", "constraint_bound", "physbone_chain");
                CheckpointPrepareTool.EnsureEditorReady();
                var snapshot = Capture(p, action, mode);
                if (p.preview ?? false)
                    return VRCForgeToolResult.Completed("User adjustment handoff preview completed without mutation.", Preview(snapshot));
                VerifyApply(p, snapshot);
                mutationStarted = true;
                return action == "prepare" ? Prepare(snapshot) : Complete(snapshot, action == "finalize");
            }
            catch (Exception exception)
            {
                if (mutationStarted)
                {
                    return VRCForgeToolResult.FailedWithCode(
                        "user_adjustment_handoff_failed_after_mutation",
                        exception.Message,
                        new
                        {
                            schema = ResultSchema,
                            operation = "user_adjustment_" + action,
                            mutationStarted = true,
                            commitState = "unknown",
                            checkpointRecoveryRequired = true,
                            failureLayer = "unity_core_tool",
                            failurePhase = "apply_mutation"
                        });
                }
                return VRCForgeToolResult.RejectedBeforeMutation(
                    "user_adjustment_handoff_rejected", exception.Message,
                    "unity_core_tool", "identity_or_preview_validation", false,
                    new { schema = ResultSchema, operation = "user_adjustment_" + action });
            }
        }

        private static Snapshot Capture(Parameters p, string action, string mode)
        {
            var project = ProjectPath();
            var avatar = Resolve(p.avatarGlobalObjectId, "avatar");
            var target = Resolve(p.targetGlobalObjectId, "target");
            if (avatar == target || !target.transform.IsChildOf(avatar.transform))
                throw new InvalidOperationException("The exact target is not inside the exact Avatar.");
            VerifyMode(avatar, target, mode);
            if (string.IsNullOrWhiteSpace(target.scene.path))
                throw new InvalidOperationException("The handoff requires an already-saved Scene.");

            JObject state = null;
            GameObject proxy = null;
            var handoffId = (p.handoffId ?? "").Trim();
            if (action != "prepare")
            {
                state = LoadState(handoffId);
                RequireEqual(state["mode"], mode, "Handoff mode drifted.");
                RequireEqual(state["avatarGlobalObjectId"], ObjectId(avatar), "Avatar identity drifted.");
                RequireEqual(state["targetGlobalObjectId"], ObjectId(target), "Target identity drifted.");
                proxy = Resolve(state["proxyGlobalObjectId"]?.ToString(), "owned proxy");
                if (proxy.name != state["proxyName"]?.ToString() || target.transform.parent != proxy.transform)
                    throw new InvalidOperationException("The owned proxy or target relationship drifted; user state was preserved.");
                if (proxy.transform.childCount != 1 || proxy.transform.GetChild(0) != target.transform
                    || proxy.GetComponents<Component>().Any(component => component == null || !(component is Transform)))
                    throw new InvalidOperationException("The handoff proxy contains additional user content; preserve it before completing the handoff.");
            }

            var scenePath = target.scene.path.Replace('\\', '/');
            var sceneGuid = AssetDatabase.AssetPathToGUID(scenePath) ?? "";
            if (sceneGuid.Length != 32) throw new InvalidOperationException("The Scene GUID is unavailable.");
            if (state != null)
            {
                RequireEqual(state["scenePath"], scenePath, "Scene path drifted.");
                RequireEqual(state["sceneGuid"], sceneGuid, "Scene GUID drifted.");
            }
            var hierarchy = SceneHierarchyDigest(target.scene);
            var proxyId = proxy == null ? "" : ObjectId(proxy);
            var digest = Digest(ResultSchema, action, mode, project, scenePath, sceneGuid,
                ObjectId(avatar), ObjectId(target), handoffId, proxyId, hierarchy,
                Vector(target.transform.position), QuaternionValue(target.transform.rotation), Vector(target.transform.lossyScale));
            return new Snapshot
            {
                Action = action, Mode = mode, ProjectPath = project, Avatar = avatar, Target = target,
                AvatarId = ObjectId(avatar), TargetId = ObjectId(target), ScenePath = scenePath,
                SceneGuid = sceneGuid, HierarchyDigest = hierarchy, PreviewDigest = digest,
                HandoffId = handoffId, Proxy = proxy, ProxyId = proxyId, State = state
            };
        }

        private static object Preview(Snapshot s)
        {
            return new
            {
                schema = ResultSchema, operation = "user_adjustment_" + s.Action, ok = true,
                preview = true, verified = true, changed = false, mutationStarted = false,
                commitState = "not_started", projectPath = s.ProjectPath, previewDigest = s.PreviewDigest,
                applyBinding = ApplyBinding(s),
                target = new
                {
                    avatarGlobalObjectId = s.AvatarId, targetGlobalObjectId = s.TargetId,
                    handoffId = s.HandoffId, proxyGlobalObjectId = s.ProxyId,
                    avatarPath = DisplayPath(s.Avatar.transform), targetPath = DisplayPath(s.Target.transform),
                    scenePath = s.ScenePath, sceneGuid = s.SceneGuid
                },
                effect = s.Action == "prepare" ? "create an adjustment proxy and pause for the user" : s.Action + " the exact handoff",
                readback = Readback(s)
            };
        }

        private static void VerifyApply(Parameters p, Snapshot s)
        {
            if (!SceneObjectCopyCore.MatchesCurrentProject(p.expectedProjectPath)
                || (p.expectedProjectPath ?? string.Empty).Replace('\\', '/').TrimEnd('/') != s.ProjectPath
                || p.expectedPreviewDigest != s.PreviewDigest || p.expectedScenePath != s.ScenePath
                || p.expectedSceneGuid != s.SceneGuid || p.expectedHierarchyDigest != s.HierarchyDigest
                || (s.Action != "prepare" && p.expectedProxyGlobalObjectId != s.ProxyId))
                throw new InvalidOperationException("The project, Scene, Avatar, target, proxy, or user pose drifted after preview.");
        }

        private static object Prepare(Snapshot s)
        {
            var id = Guid.NewGuid().ToString("N");
            var proxyName = "VRCForge_UserAdjustmentProxy_" + id;
            var target = s.Target.transform;
            var originalParent = target.parent;
            var state = new JObject
            {
                ["schema"] = ResultSchema, ["handoffId"] = id, ["mode"] = s.Mode,
                ["avatarGlobalObjectId"] = s.AvatarId, ["targetGlobalObjectId"] = s.TargetId,
                ["parentGlobalObjectId"] = originalParent == null ? "" : ObjectId(originalParent.gameObject),
                ["localPosition"] = VectorToken(target.localPosition), ["localRotation"] = QuaternionToken(target.localRotation),
                ["localScale"] = VectorToken(target.localScale), ["scenePath"] = s.ScenePath,
                ["sceneGuid"] = s.SceneGuid, ["proxyName"] = proxyName,
                ["preparePreviewDigest"] = s.PreviewDigest
            };

            var proxy = new GameObject(proxyName);
            try
            {
                Undo.RegisterCreatedObjectUndo(proxy, "Prepare VRCForge user adjustment handoff");
                UnityEngine.SceneManagement.SceneManager.MoveGameObjectToScene(proxy, s.Target.scene);
                proxy.transform.SetParent(originalParent, true);
                proxy.transform.position = target.position;
                proxy.transform.rotation = target.rotation;
                SetWorldScale(proxy.transform, target.lossyScale);
                target.SetParent(proxy.transform, true);
                state["proxyGlobalObjectId"] = ObjectId(proxy);
                Directory.CreateDirectory(StateRoot);
                File.WriteAllText(StatePath(id), state.ToString(Newtonsoft.Json.Formatting.None), new UTF8Encoding(false));
                EditorSceneManager.MarkSceneDirty(s.Target.scene);
                if (!EditorSceneManager.SaveScene(s.Target.scene))
                    throw new InvalidOperationException("Unity did not save the prepared handoff Scene.");
                var targetReadback = Resolve(s.TargetId, "target readback");
                var proxyReadback = Resolve(state["proxyGlobalObjectId"].ToString(), "proxy readback");
                if (targetReadback.transform.parent != proxyReadback.transform)
                    throw new InvalidOperationException("The prepared target/proxy readback relationship changed.");
                return VRCForgeToolResult.Completed("Adjustment proxy prepared; stop and let the user drag it.", new
                {
                    schema = ResultSchema, operation = "user_adjustment_prepare", ok = true,
                    status = "awaiting_user", operationStatus = "user_confirmation_required",
                    preview = false, verified = true, changed = true, mutationStarted = true,
                    commitState = "committed", projectPath = s.ProjectPath, previewDigest = s.PreviewDigest,
                    applyBinding = ApplyBinding(new Snapshot
                    {
                        Action = s.Action, Mode = s.Mode, ProjectPath = s.ProjectPath,
                        AvatarId = s.AvatarId, TargetId = s.TargetId, ScenePath = s.ScenePath,
                        SceneGuid = s.SceneGuid, HierarchyDigest = s.HierarchyDigest, PreviewDigest = s.PreviewDigest,
                        HandoffId = s.HandoffId, ProxyId = ObjectId(proxyReadback)
                    }),
                    handoffId = id, nextAction = "After the user finishes dragging, call action=finalize with the same exact identities.",
                    readback = new { avatarGlobalObjectId = s.AvatarId, targetGlobalObjectId = s.TargetId, proxyGlobalObjectId = ObjectId(proxyReadback), targetPath = DisplayPath(targetReadback.transform), proxyPath = DisplayPath(proxyReadback.transform), scenePath = s.ScenePath, sceneGuid = s.SceneGuid }
                });
            }
            catch
            {
                if (target == null || (target.parent != originalParent
                    && (proxy == null || target.parent != proxy.transform)))
                    throw new InvalidOperationException("The target hierarchy changed during prepare; proxy and recovery state were preserved.");
                target.SetParent(originalParent, false);
                target.localPosition = ReadVector(state["localPosition"], "localPosition");
                target.localRotation = ReadQuaternion(state["localRotation"], "localRotation");
                target.localScale = ReadVector(state["localScale"], "localScale");
                if (proxy != null) UnityEngine.Object.DestroyImmediate(proxy);
                EditorSceneManager.MarkSceneDirty(s.Target.scene);
                if (!EditorSceneManager.SaveScene(s.Target.scene))
                    throw new InvalidOperationException("The failed handoff rollback could not be saved; recovery state was retained.");
                var readback = Resolve(s.TargetId, "rollback target readback").transform;
                if (readback != target || readback.parent != originalParent
                    || readback.localPosition != ReadVector(state["localPosition"], "localPosition")
                    || readback.localRotation != ReadQuaternion(state["localRotation"], "localRotation")
                    || readback.localScale != ReadVector(state["localScale"], "localScale"))
                    throw new InvalidOperationException("The failed handoff rollback readback did not match; recovery state was retained.");
                if (File.Exists(StatePath(id))) File.Delete(StatePath(id));
                throw;
            }
        }

        private static object Complete(Snapshot s, bool finalize)
        {
            var target = s.Target.transform;
            var originalWorldPosition = target.position;
            var originalWorldRotation = target.rotation;
            var originalWorldScale = target.lossyScale;
            var parentId = s.State["parentGlobalObjectId"]?.ToString() ?? "";
            var parent = string.IsNullOrEmpty(parentId) ? null : Resolve(parentId, "original parent").transform;
            var originalLocalPosition = ReadVector(s.State["localPosition"], "localPosition");
            var originalLocalRotation = ReadQuaternion(s.State["localRotation"], "localRotation");
            var originalLocalScale = ReadVector(s.State["localScale"], "localScale");
            if (finalize)
            {
                target.SetParent(parent, true);
                target.position = originalWorldPosition;
                target.rotation = originalWorldRotation;
                SetWorldScale(target, originalWorldScale);
            }
            else
            {
                target.SetParent(parent, false);
                target.localPosition = originalLocalPosition;
                target.localRotation = originalLocalRotation;
                target.localScale = originalLocalScale;
            }
            UnityEngine.Object.DestroyImmediate(s.Proxy);
            EditorSceneManager.MarkSceneDirty(s.Target.scene);
            if (!EditorSceneManager.SaveScene(s.Target.scene))
                throw new InvalidOperationException("Unity did not save the completed handoff; the state record was retained.");
            var readback = Resolve(s.TargetId, "target readback");
            if (GlobalObjectId.TryParse(s.ProxyId, out var proxyId)
                && GlobalObjectId.GlobalObjectIdentifierToObjectSlow(proxyId) != null)
                throw new InvalidOperationException("The exact tool-created proxy still exists after cleanup.");
            File.Delete(StatePath(s.HandoffId));
            return VRCForgeToolResult.Completed(finalize ? "User adjustment finalized." : "User adjustment aborted and restored.", new
            {
                schema = ResultSchema, operation = finalize ? "user_adjustment_finalize" : "user_adjustment_abort",
                ok = true, status = finalize ? "finalized" : "aborted", preview = false, verified = true,
                changed = true, mutationStarted = true, commitState = "committed", projectPath = s.ProjectPath,
                applyBinding = ApplyBinding(s),
                previewDigest = s.PreviewDigest,
                readback = new { avatarGlobalObjectId = s.AvatarId, targetGlobalObjectId = ObjectId(readback), targetPath = DisplayPath(readback.transform), proxyDeleted = true, scenePath = s.ScenePath, sceneGuid = s.SceneGuid, hierarchyDigest = SceneHierarchyDigest(readback.scene) }
            });
        }

        private static object ApplyBinding(Snapshot s)
        {
            return new
            {
                expectedProjectPath = s.ProjectPath,
                expectedPreviewDigest = s.PreviewDigest,
                expectedScenePath = s.ScenePath,
                expectedSceneGuid = s.SceneGuid,
                expectedHierarchyDigest = s.HierarchyDigest,
                expectedProxyGlobalObjectId = s.ProxyId
            };
        }

        private static void VerifyMode(GameObject avatar, GameObject target, string mode)
        {
            if (mode == "transform") return;
            if (mode == "skinned_bone_proxy")
            {
                if (!avatar.GetComponentsInChildren<SkinnedMeshRenderer>(true).Any(r => r.bones != null && r.bones.Contains(target.transform)))
                    throw new InvalidOperationException("The target is not a bone used by a SkinnedMeshRenderer under this Avatar.");
                return;
            }
            var token = mode == "constraint_bound" ? "Constraint" : "PhysBone";
            if (!target.GetComponents<Component>().Any(c => c != null && c.GetType().FullName.IndexOf(token, StringComparison.OrdinalIgnoreCase) >= 0))
                throw new InvalidOperationException("The target does not carry the requested optional component family; no proxy was written.");
        }

        private static JObject LoadState(string id)
        {
            var path = StatePath(id);
            if (!File.Exists(path)) throw new InvalidOperationException("The exact handoff state does not exist.");
            return JObject.Parse(File.ReadAllText(path, Encoding.UTF8));
        }

        private static string StatePath(string id)
        {
            var value = (id ?? "").Trim();
            if (value.Length != 32 || value.Any(c => !((c >= '0' && c <= '9') || (c >= 'a' && c <= 'f'))))
                throw new InvalidOperationException("handoffId must be 32 lowercase hexadecimal characters.");
            return Path.Combine(StateRoot, "handoff-" + value + ".json");
        }

        private static GameObject Resolve(string id, string label)
        {
            if (!GlobalObjectId.TryParse((id ?? "").Trim(), out var parsed) || parsed.identifierType == 0)
                throw new InvalidOperationException("A valid exact " + label + " GlobalObjectId is required.");
            var result = GlobalObjectId.GlobalObjectIdentifierToObjectSlow(parsed) as GameObject;
            if (result == null || ObjectId(result) != id.Trim())
                throw new InvalidOperationException("The exact " + label + " was deleted, replaced, or recreated.");
            return result;
        }

        private static string ProjectPath() { return Path.GetFullPath(Path.Combine(Application.dataPath, "..")).TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar).Replace('\\', '/'); }
        private static string SceneHierarchyDigest(UnityEngine.SceneManagement.Scene scene) { return Digest(scene.path, string.Join("|", scene.GetRootGameObjects().Select(SceneObjectCopyCore.ComputeHierarchyDigest))); }
        private static object Readback(Snapshot s) { return new { avatarGlobalObjectId = s.AvatarId, targetGlobalObjectId = s.TargetId, proxyGlobalObjectId = s.ProxyId, scenePath = s.ScenePath, sceneGuid = s.SceneGuid, hierarchyDigest = s.HierarchyDigest, targetPath = DisplayPath(s.Target.transform) }; }
        private static string ObjectId(UnityEngine.Object value) { return GlobalObjectId.GetGlobalObjectIdSlow(value).ToString(); }
        private static string DisplayPath(Transform value) { return value.parent == null ? value.name : DisplayPath(value.parent) + "/" + value.name; }
        private static string Choice(string value, string label, params string[] allowed) { var text = (value ?? "").Trim(); if (!allowed.Contains(text, StringComparer.Ordinal)) throw new InvalidOperationException(label + " is invalid."); return text; }
        private static void RequireEqual(JToken actual, string expected, string message) { if (actual == null || actual.ToString() != expected) throw new InvalidOperationException(message); }
        private static string Vector(Vector3 value) { return value.x.ToString("R", CultureInfo.InvariantCulture) + "," + value.y.ToString("R", CultureInfo.InvariantCulture) + "," + value.z.ToString("R", CultureInfo.InvariantCulture); }
        private static string QuaternionValue(Quaternion value) { return Vector(new Vector3(value.x, value.y, value.z)) + "," + value.w.ToString("R", CultureInfo.InvariantCulture); }
        private static JObject VectorToken(Vector3 v) { return new JObject { ["x"] = v.x, ["y"] = v.y, ["z"] = v.z }; }
        private static JObject QuaternionToken(Quaternion q) { return new JObject { ["x"] = q.x, ["y"] = q.y, ["z"] = q.z, ["w"] = q.w }; }
        private static Vector3 ReadVector(JToken t, string label) { var o = t as JObject; if (o == null) throw new InvalidOperationException(label + " is missing."); return new Vector3((float)o["x"], (float)o["y"], (float)o["z"]); }
        private static Quaternion ReadQuaternion(JToken t, string label) { var o = t as JObject; if (o == null) throw new InvalidOperationException(label + " is missing."); return new Quaternion((float)o["x"], (float)o["y"], (float)o["z"], (float)o["w"]); }
        private static void SetWorldScale(Transform t, Vector3 world) { var p = t.parent == null ? Vector3.one : t.parent.lossyScale; if (Mathf.Abs(p.x) < 0.000001f || Mathf.Abs(p.y) < 0.000001f || Mathf.Abs(p.z) < 0.000001f) throw new InvalidOperationException("A zero-scale parent cannot preserve world scale."); t.localScale = new Vector3(world.x / p.x, world.y / p.y, world.z / p.z); }

        private static string Digest(params string[] fields)
        {
            var frame = new StringBuilder();
            foreach (var field in fields) { var value = field ?? ""; frame.Append(Encoding.UTF8.GetByteCount(value).ToString(CultureInfo.InvariantCulture)).Append(':').Append(value); }
            using (var sha = SHA256.Create()) return string.Concat(sha.ComputeHash(Encoding.UTF8.GetBytes(frame.ToString())).Select(b => b.ToString("x2", CultureInfo.InvariantCulture)));
        }

        private sealed class Snapshot
        {
            internal string Action, Mode, ProjectPath, AvatarId, TargetId, ScenePath, SceneGuid, HierarchyDigest, PreviewDigest, HandoffId, ProxyId;
            internal GameObject Avatar, Target, Proxy;
            internal JObject State;
        }
    }
}
