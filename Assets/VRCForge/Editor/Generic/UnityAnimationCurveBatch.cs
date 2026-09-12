using System;
using System.Collections.Generic;
using System.Linq;
using System.IO;
using System.Text;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEngine;
using VRCForge.Core.MCP;

namespace VRCForge.Editor
{
    // One existing public curve call owns this bounded single-clip transaction.
    internal static class UnityAnimationCurveBatch
    {
        private sealed class CurveEdit
        {
            internal EditorCurveBinding binding;
            internal AnimationCurve curve;
        }

        // Multi-clip writes accept existing assets only: no destination/folder cleanup ownership is inferred.
        internal static JArray ValidateClips(JObject arguments)
        {
            if (Encoding.UTF8.GetByteCount(arguments.ToString(Formatting.None)) > 512 * 1024
                || arguments.Properties().Any(p => !new[] { "clips", "preview", "action" }.Contains(p.Name))
                || (arguments["action"]?.ToString() ?? "set_curve") != "set_curve")
                throw new InvalidOperationException("clips accepts only set_curve, preview and a bounded clips array.");
            var clips = arguments["clips"] as JArray;
            if (clips == null || clips.Count < 1 || clips.Count > 32) throw new InvalidOperationException("clips requires 1..32 existing clips.");
            var paths = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
            var keys = 0;
            foreach (var token in clips)
            {
                var row = token as JObject ?? throw new InvalidOperationException("Each clip must be an object.");
                if (row.Properties().Any(p => p.Name != "clipPath" && p.Name != "curves")) throw new InvalidOperationException("Unknown clip row field.");
                var path = row["clipPath"]?.Value<string>() ?? "";
                if (!path.StartsWith("Assets/", StringComparison.Ordinal) || !path.EndsWith(".anim", StringComparison.OrdinalIgnoreCase)
                    || path.Contains("\\") || path.Split('/').Any(part => part == ".." || part == "." || part.Length == 0)
                    || !paths.Add(path)) throw new InvalidOperationException("Clip paths must be exact, unique existing .anim assets.");
                foreach (JObject curve in ValidateEnvelope(row)) keys += (curve["keys"] as JArray)?.Count ?? 1;
                if (keys > 4096) throw new InvalidOperationException("Multi-clip batch exceeds 4096 total keys.");
            }
            return clips;
        }

        private sealed class PreparedClip
        {
            internal string Path;
            internal AnimationClip Clip, Staged;
            internal StableAssetEvidence Before, Owned;
            internal byte[] Bytes;
            internal CurveEdit[] Edits;
            internal bool Started;
        }

        // The expected clip is independently constructed during preflight, before any real asset is touched.
        private static void VerifyPreparedClip(PreparedClip edit, AnimationClip actual)
        {
            if (actual == null) throw new InvalidOperationException("Persisted clip is missing.");
            var expected = FloatCurves(edit.Staged); var read = FloatCurves(actual);
            if (expected.Count != read.Count || expected.Any(p => !read.TryGetValue(p.Key, out var curve) || !WriteAnimationCurveTool.CurvesEqual(p.Value, curve))
                || !JToken.DeepEquals(ObjectCurves(edit.Staged), ObjectCurves(actual)) || !JToken.DeepEquals(Events(edit.Staged), Events(actual))
                || actual.frameRate != edit.Staged.frameRate || actual.wrapMode != edit.Staged.wrapMode || actual.legacy != edit.Staged.legacy
                || JsonUtility.ToJson(AnimationUtility.GetAnimationClipSettings(actual)) != JsonUtility.ToJson(AnimationUtility.GetAnimationClipSettings(edit.Staged)))
                throw new InvalidOperationException("Requested/untouched curves, references, events or clip settings differ from preflight.");
        }

        private static void RequireEvidence(PreparedClip edit, StableAssetEvidence expected)
        {
            if (!SceneObjectCopyCore.StableAssetEvidenceMatches(expected,
                SceneObjectCopyCore.ReadStableAssetEvidence(edit.Path, "multi-clip identity check"), true))
                throw new InvalidOperationException("Clip file/meta identity changed: " + edit.Path);
        }

        // Deliberately no global Undo rollback: it could revert unrelated concurrent edits.
        private static bool RestorePreparedClips(IList<PreparedClip> edits)
        {
            var restored = true;
            foreach (var edit in edits.Reverse())
            {
                try
                {
                    if (!edit.Started) { RequireEvidence(edit, edit.Before); continue; }
                    RequireEvidence(edit, edit.Owned ?? edit.Before);
                    var current = AssetDatabase.LoadAssetAtPath<AnimationClip>(edit.Path);
                    if (current == null) throw new InvalidOperationException("Clip disappeared before restore.");
                    if (EditorUtility.IsDirty(current)) VerifyPreparedClip(edit, current);
                    if (edit.Owned != null)
                    {
                        var absolute = SceneObjectCopyCore.ToAbsoluteAssetPath(edit.Path);
                        var temp = absolute + ".vrcforge-restore-" + Guid.NewGuid().ToString("N");
                        try
                        {
                            using (var stream = new FileStream(temp, FileMode.CreateNew, FileAccess.Write, FileShare.None))
                                stream.Write(edit.Bytes, 0, edit.Bytes.Length);
                            RequireEvidence(edit, edit.Owned);
                            File.Replace(temp, absolute, null);
                        }
                        finally { if (File.Exists(temp)) File.Delete(temp); }
                    }
                    AssetDatabase.ImportAsset(edit.Path, ImportAssetOptions.ForceSynchronousImport | ImportAssetOptions.ForceUpdate);
                    var after = SceneObjectCopyCore.ReadStableAssetEvidence(edit.Path, "multi-clip restored readback");
                    if (after.Guid != edit.Before.Guid || after.Meta.Digest != edit.Before.Meta.Digest || after.File.Digest != edit.Before.File.Digest
                        || EditorUtility.IsDirty(AssetDatabase.LoadAssetAtPath<AnimationClip>(edit.Path))) restored = false;
                }
                catch { restored = false; } // Preserve unknown content and require supervised recovery.
            }
            return restored;
        }

        internal static object HandleClips(JObject arguments)
        {
            var prepared = new List<PreparedClip>();
            var phase = "pre_mutation_validation"; var started = false;
            try
            {
                var rows = ValidateClips(arguments); long bytes = 0;
                var guids = new HashSet<string>(StringComparer.Ordinal);
                foreach (JObject row in rows)
                {
                    var path = row["clipPath"].Value<string>();
                    var clip = AssetDatabase.LoadAssetAtPath<AnimationClip>(path);
                    if (clip == null || AssetDatabase.GetAssetPath(clip) != path || EditorUtility.IsDirty(clip))
                        throw new InvalidOperationException("An exact saved existing clip is required: " + path);
                    var before = SceneObjectCopyCore.ReadStableAssetEvidence(path, "multi-clip preflight");
                    if (!guids.Add(before.Guid)) throw new InvalidOperationException("Duplicate clip asset identity.");
                    var absolute = SceneObjectCopyCore.ToAbsoluteAssetPath(path);
                    bytes += new FileInfo(absolute).Length;
                    if (bytes > 64L * 1024 * 1024) throw new InvalidOperationException("Batch snapshots exceed 64 MiB.");
                    var edit = new PreparedClip { Path = path, Clip = clip, Before = before, Bytes = File.ReadAllBytes(absolute) };
                    prepared.Add(edit); RequireEvidence(edit, before);
                    edit.Staged = UnityEngine.Object.Instantiate(clip);
                    var existing = FloatCurves(clip); var curves = new List<CurveEdit>();
                    foreach (JObject curveRow in (JArray)row["curves"])
                    {
                        var property = (curveRow["propertyName"]?.ToString() ?? "").Trim();
                        if (property.Length == 0) throw new InvalidOperationException("Every curve requires propertyName.");
                        var type = AvatarPrimitiveCrudCore.FindType(curveRow["componentType"]?.ToString() ?? "GameObject")
                            ?? throw new InvalidOperationException("Binding component type not found.");
                        var binding = new EditorCurveBinding { path = AvatarPrimitiveCrudCore.NormalizePath(curveRow["bindingPath"]?.ToString() ?? curveRow["objectPath"]?.ToString() ?? ""), type = type, propertyName = property };
                        ValidateBinding(binding, curves.Select(c => c.binding), existing.Keys, curveRow["overwriteExisting"]?.Value<bool?>() ?? false);
                        curves.Add(new CurveEdit { binding = binding, curve = WriteAnimationCurveTool.BuildCurveForBatch(curveRow) });
                    }
                    edit.Edits = curves.ToArray();
                    AnimationUtility.SetEditorCurves(edit.Staged, curves.Select(c => c.binding).ToArray(), curves.Select(c => c.curve).ToArray());
                }
                // Whole-batch recheck before first mutation, including untouched target files and metadata.
                foreach (var edit in prepared) RequireEvidence(edit, edit.Before);
                if (arguments["preview"]?.Value<bool>() == true)
                    return VRCForgeToolResult.Completed("Preview: existing multi-clip curve batch.", new { ok = true, preview = true, batch = true,
                        clips = prepared.Select(e => new { clipPath = e.Path, assetGuid = e.Before.Guid, fileDigest = e.Before.File.Digest, curveCount = e.Edits.Length }).ToArray() });
                foreach (var edit in prepared)
                {
                    RequireEvidence(edit, edit.Before);
                    if (EditorUtility.IsDirty(edit.Clip)) throw new InvalidOperationException("Clip became dirty before mutation.");
                    phase = "asset_mutation"; started = edit.Started = true;
                    AnimationUtility.SetEditorCurves(edit.Clip, edit.Edits.Select(c => c.binding).ToArray(), edit.Edits.Select(c => c.curve).ToArray());
                    VerifyPreparedClip(edit, edit.Clip);
                    EditorUtility.SetDirty(edit.Clip); phase = "asset_save";
                    AssetDatabase.SaveAssetIfDirty(edit.Clip);
                    if (EditorUtility.IsDirty(edit.Clip)) throw new InvalidOperationException("Clip remained dirty after save.");
                    // A failed/ambiguous save is never assumed owned; restore then preserves unknown disk changes.
                    var owned = SceneObjectCopyCore.ReadStableAssetEvidence(edit.Path, "multi-clip saved ownership");
                    if (owned.Guid != edit.Before.Guid || owned.Meta.Digest != edit.Before.Meta.Digest) throw new InvalidOperationException("Saved clip metadata changed.");
                    phase = "persisted_readback";
                    AssetDatabase.ImportAsset(edit.Path, ImportAssetOptions.ForceSynchronousImport | ImportAssetOptions.ForceUpdate);
                    RequireEvidence(edit, owned);
                    VerifyPreparedClip(edit, AssetDatabase.LoadAssetAtPath<AnimationClip>(edit.Path));
                    edit.Owned = owned;
                }
                // Check every target again after the final write, never validate only the last clip.
                foreach (var edit in prepared) { RequireEvidence(edit, edit.Owned); var finalClip = AssetDatabase.LoadAssetAtPath<AnimationClip>(edit.Path);
                    if (finalClip == null || EditorUtility.IsDirty(finalClip)) throw new InvalidOperationException("Final clip readback is missing or dirty.");
                    VerifyPreparedClip(edit, finalClip); }
                return VRCForgeToolResult.Completed("Multi-clip curves saved and verified.", new {
                    schema = "vrcforge.animation_curve_write.v1", ok = true, preview = false, batch = true, verified = true, persistedReadback = true,
                    mutationStarted = true, mutationApplied = true, committed = true, commitState = "committed", commitStateKnown = true,
                    checkpointRecoveryRequired = false, temporaryCleanupRequired = false,
                    readback = new { persisted = true, clips = prepared.Select(e => new { clipPath = e.Path, assetGuid = e.Owned.Guid,
                        fileDigest = e.Owned.File.Digest, curves = e.Edits.Select(c => Describe(c.binding, c.curve)).ToArray(),
                        totalFloatBindings = FloatCurves(e.Staged).Count, untouchedFloatCurvesVerified = true, objectReferenceCurvesVerified = true,
                        animationEventsVerified = true, playbackSettingsVerified = true }).ToArray() } });
            }
            catch (Exception exception)
            {
                if (!started) return VRCForgeToolResult.RejectedBeforeMutation("animation_clips_batch_failed", exception.Message, "unity_core_tool", phase);
                var restored = RestorePreparedClips(prepared);
                return VRCForgeToolResult.FailedWithCode("animation_clips_batch_failed", exception.Message, new {
                    schema = "vrcforge.authoring_failure.v1", ok = false, failureLayer = "unity_core_tool", failurePhase = phase,
                    mutationStarted = true, committed = false, commitState = restored ? "rolled_back" : "unknown", commitStateKnown = restored,
                    restored, cleanupRequired = !restored, checkpointRecoveryRequired = !restored, temporaryCleanupRequired = !restored, retryable = false });
            }
            finally { foreach (var edit in prepared) if (edit.Staged != null) UnityEngine.Object.DestroyImmediate(edit.Staged); }
        }

        internal static JArray ValidateEnvelope(JObject arguments)
        {
            if (Encoding.UTF8.GetByteCount(arguments.ToString(Formatting.None)) > 512 * 1024)
                throw new InvalidOperationException("Curve batch exceeds 512 KiB.");
            if (arguments.Properties().Any(property => !new[] { "clipPath", "preview", "action", "curves" }.Contains(property.Name)))
                throw new InvalidOperationException("curves is mutually exclusive with single-curve fields.");
            if ((arguments["action"]?.ToString() ?? "set_curve") != "set_curve")
                throw new InvalidOperationException("Curve batches only support set_curve.");
            var curves = arguments["curves"] as JArray;
            if (curves == null || curves.Count < 1 || curves.Count > 256)
                throw new InvalidOperationException("curves requires 1 to 256 entries.");
            var keyCount = 0;
            foreach (var token in curves)
            {
                var row = token as JObject ?? throw new InvalidOperationException("Every curve must be an object.");
                if (row.Properties().Any(property => !new[] { "bindingPath", "objectPath", "componentType", "propertyName", "keys", "constantFloat", "overwriteExisting" }.Contains(property.Name)))
                    throw new InvalidOperationException("Unknown batch curve field.");
                if ((row["keys"] != null) == (row["constantFloat"] != null))
                    throw new InvalidOperationException("Every curve requires exactly one of keys or constantFloat.");
                if (row["keys"] != null && !(row["keys"] is JArray))
                    throw new InvalidOperationException("keys must be an array.");
                keyCount += (row["keys"] as JArray)?.Count ?? 1;
                if (keyCount > 4096) throw new InvalidOperationException("Curve batch exceeds 4096 keys.");
            }
            return curves;
        }

        internal static object HandleCommand(JObject arguments)
        {
            var recovery = new WriteAnimationCurveTool.AssetEditRecovery();
            var mutationStarted = false;
            var phase = "pre_mutation_validation";
            try
            {
                var rows = ValidateEnvelope(arguments);
                var path = AvatarPrimitiveCrudCore.NormalizeAssetPath(arguments["clipPath"]?.ToString() ?? "");
                if (string.IsNullOrWhiteSpace(path) || !path.StartsWith("Assets/", StringComparison.Ordinal))
                    throw new InvalidOperationException("clipPath must be an Assets AnimationClip path.");
                var preview = arguments["preview"]?.Value<bool?>() ?? false;
                var clip = AssetDatabase.LoadAssetAtPath<AnimationClip>(path);
                if (clip == null)
                {
                    path = GeneratedAssetPaths.ValidateNewAssetPath(path);
                    if (SceneObjectCopyCore.AssetOrMetaExists(path))
                        throw new InvalidOperationException("The clip destination or metadata is occupied.");
                }
                else if (EditorUtility.IsDirty(clip))
                    throw new InvalidOperationException("Save or discard existing clip edits before a batch write.");
                var expected = FloatCurves(clip);
                var originalObjects = ObjectCurves(clip);
                var originalEvents = Events(clip);
                var originalFrameRate = clip != null ? clip.frameRate : 0f;
                var originalWrap = clip != null ? clip.wrapMode : WrapMode.Default;
                var originalLegacy = clip != null && clip.legacy;
                var settings = clip != null ? AnimationUtility.GetAnimationClipSettings(clip) : null;
                var originalGuid = clip != null ? AssetDatabase.AssetPathToGUID(path) : "";
                var edits = new List<CurveEdit>();
                foreach (JObject row in rows)
                {
                    var property = (row["propertyName"]?.ToString() ?? "").Trim();
                    if (property.Length == 0) throw new InvalidOperationException("Every curve requires propertyName.");
                    var type = AvatarPrimitiveCrudCore.FindType(row["componentType"]?.ToString() ?? "GameObject")
                        ?? throw new InvalidOperationException("Binding component type not found.");
                    var binding = new EditorCurveBinding {
                        path = AvatarPrimitiveCrudCore.NormalizePath(row["bindingPath"]?.ToString() ?? row["objectPath"]?.ToString() ?? ""),
                        type = type, propertyName = property };
                    ValidateBinding(binding, edits.Select(edit => edit.binding), expected.Keys,
                        row["overwriteExisting"]?.Value<bool?>() ?? false);
                    var curve = WriteAnimationCurveTool.BuildCurveForBatch(row);
                    edits.Add(new CurveEdit { binding = binding, curve = curve });
                    expected[binding] = curve;
                }
                // No asset, folder or Undo mutation occurred before the last row passed.
                if (preview)
                    return VRCForgeToolResult.Completed("Preview: would write one clip curve batch.", new {
                        ok = true, preview = true, action = "set_curve", clipPath = path, batch = true,
                        curveCount = edits.Count, plan = edits.Select(edit => Describe(edit.binding, edit.curve)).ToArray() });
                recovery.Capture(path);
                recovery.Begin();
                phase = "asset_mutation";
                mutationStarted = true;
                clip = WriteAnimationCurveTool.LoadOrCreateClipForBatch(path);
                Undo.RegisterCompleteObjectUndo(clip, "Write animation curve batch");
                AnimationUtility.SetEditorCurves(clip, edits.Select(edit => edit.binding).ToArray(), edits.Select(edit => edit.curve).ToArray());
                EditorUtility.SetDirty(clip);
                phase = "asset_save";
                AssetDatabase.SaveAssetIfDirty(clip);
                if (EditorUtility.IsDirty(clip)) throw new InvalidOperationException("Batch clip remained dirty after save.");
                phase = "persisted_readback";
                AssetDatabase.ImportAsset(path, ImportAssetOptions.ForceSynchronousImport | ImportAssetOptions.ForceUpdate);
                var persisted = AssetDatabase.LoadAssetAtPath<AnimationClip>(path)
                    ?? throw new InvalidOperationException("Batch clip persisted readback failed.");
                var actual = FloatCurves(persisted);
                if (actual.Count != expected.Count || expected.Any(pair => !actual.TryGetValue(pair.Key, out var curve)
                    || !WriteAnimationCurveTool.CurvesEqual(pair.Value, curve)))
                    throw new InvalidOperationException("Batch requested or untouched float curves differ after reload.");
                if (!JToken.DeepEquals(originalObjects, ObjectCurves(persisted)) || !JToken.DeepEquals(originalEvents, Events(persisted)))
                    throw new InvalidOperationException("Untouched object-reference curves or animation events changed.");
                if (!string.IsNullOrEmpty(originalGuid))
                {
                    var savedSettings = AnimationUtility.GetAnimationClipSettings(persisted);
                    if (AssetDatabase.AssetPathToGUID(path) != originalGuid || persisted.frameRate != originalFrameRate
                        || persisted.wrapMode != originalWrap || persisted.legacy != originalLegacy
                        || savedSettings.loopTime != settings.loopTime || savedSettings.loopBlend != settings.loopBlend)
                        throw new InvalidOperationException("Batch changed the existing clip identity or playback settings.");
                }
                var evidence = SceneObjectCopyCore.ReadStableAssetEvidence(path, "animation batch readback");
                recovery.Complete();
                return VRCForgeToolResult.Completed("Animation curve batch saved and verified.", new {
                    schema = "vrcforge.animation_curve_write.v1", ok = true, preview = false, batch = true,
                    action = "set_curve", clipPath = path, curveCount = edits.Count, verified = true, persistedReadback = true,
                    mutationStarted = true, mutationApplied = true, committed = true, commitState = "committed", commitStateKnown = true,
                    checkpointRecoveryRequired = false, temporaryCleanupRequired = false,
                    readback = new { persisted = true, clipPath = path, assetGuid = evidence.Guid, fileDigest = evidence.File.Digest,
                        curves = edits.Select(edit => Describe(edit.binding, actual[edit.binding])).ToArray(),
                        totalFloatBindings = actual.Count, untouchedFloatCurvesVerified = true, objectReferenceCurvesVerified = true, animationEventsVerified = true } });
            }
            catch (Exception exception)
            {
                return WriteAnimationCurveTool.EditFailure("animation_curve_batch_failed", exception, mutationStarted, phase, recovery);
            }
        }

        internal static void ValidateBinding(EditorCurveBinding binding, IEnumerable<EditorCurveBinding> planned,
            IEnumerable<EditorCurveBinding> existing, bool overwrite)
        {
            if (planned.Any(item => SameBinding(item, binding)))
                throw new InvalidOperationException("Duplicate batch curve binding.");
            if (!overwrite && existing.Any(item => SameBinding(item, binding)))
                throw new InvalidOperationException("Destination already has a curve and overwriteExisting is false.");
        }

        private static bool SameBinding(EditorCurveBinding left, EditorCurveBinding right)
        {
            return left.path == right.path && left.type == right.type && left.propertyName == right.propertyName;
        }

        private static Dictionary<EditorCurveBinding, AnimationCurve> FloatCurves(AnimationClip clip) => clip == null
            ? new Dictionary<EditorCurveBinding, AnimationCurve>()
            : AnimationUtility.GetCurveBindings(clip).ToDictionary(binding => binding, binding => AnimationUtility.GetEditorCurve(clip, binding));

        private static object Describe(EditorCurveBinding binding, AnimationCurve curve) => new {
            bindingPath = binding.path, componentType = binding.type.FullName, propertyName = binding.propertyName,
            curve = WriteAnimationCurveTool.DescribeCurveForBatch(curve) };

        private static string Reference(UnityEngine.Object value)
        {
            if (value == null) return "";
            if (!AssetDatabase.TryGetGUIDAndLocalFileIdentifier(value, out string guid, out long localId))
                throw new InvalidOperationException("Object reference has no persistent asset identity.");
            return guid + ":" + localId;
        }

        private static JToken ObjectCurves(AnimationClip clip) => clip == null ? new JArray() : JToken.FromObject(
            AnimationUtility.GetObjectReferenceCurveBindings(clip)
                .OrderBy(binding => binding.path, StringComparer.Ordinal).ThenBy(binding => binding.type.FullName, StringComparer.Ordinal)
                .ThenBy(binding => binding.propertyName, StringComparer.Ordinal)
                .Select(binding => new { binding.path, type = binding.type.FullName, binding.propertyName,
                    keys = AnimationUtility.GetObjectReferenceCurve(clip, binding).Select(key => new { key.time, value = Reference(key.value) }).ToArray() }).ToArray());

        private static JToken Events(AnimationClip clip) => clip == null ? new JArray() : JToken.FromObject(
            AnimationUtility.GetAnimationEvents(clip).Select(item => new { item.time, item.functionName, item.floatParameter,
                item.intParameter, item.stringParameter, item.messageOptions, value = Reference(item.objectReferenceParameter) }).ToArray());
    }
}
