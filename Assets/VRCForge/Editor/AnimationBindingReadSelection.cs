using System;
using System.Collections.Generic;
using System.Linq;
using System.Security.Cryptography;
using System.Text;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEngine;

namespace VRCForge.Editor
{
    // Stateless pages belong to one authenticated read call. No cache, jobs or files.
    public static class AnimationBindingReadSelection
    {
        internal static readonly string[] Fields = { "bindingView", "bindingSelectors", "bindingOffset", "bindingLimit", "clipOffset", "keyOffset", "maxTotalKeys", "expectedSnapshotDigest" };
        internal static bool Requested(JObject args) => args != null && Fields.Any(name => args[name] != null);
        internal static bool ValidReadArguments(JObject args)
        {
            try { Parse(args); return true; } catch { return false; }
        }
        private sealed class Options
        {
            internal string View;
            internal JArray Selectors;
            internal int Offset, Limit, ClipOffset, KeyOffset, TotalKeys, MaxKeys, MaxClips;
        }
        private static int Integer(JObject args, string name, int fallback, int min, int max)
        {
            var token = args[name];
            if (token == null) return fallback;
            if (token.Type != JTokenType.Integer || token.Value<long>() < min || token.Value<long>() > max)
                throw new ArgumentException(name + " is outside its integer bounds.");
            return token.Value<int>();
        }
        private static Options Parse(JObject args)
        {
            var view = args["bindingView"] == null ? "details" : args["bindingView"].Type == JTokenType.String ? args["bindingView"].ToString() : "";
            if (view != "summary" && view != "index" && view != "details") throw new ArgumentException("bindingView must be summary, index or details.");
            var selectors = args["bindingSelectors"] as JArray ?? new JArray();
            if (args["bindingSelectors"] != null && !(args["bindingSelectors"] is JArray) || selectors.Count > 32)
                throw new ArgumentException("bindingSelectors requires at most 32 exact selectors.");
            foreach (var item in selectors)
            {
                var row = item as JObject;
                if (row == null || row.Count == 0 || row.Properties().Any(p => !new[] { "path", "propertyName", "componentType" }.Contains(p.Name))
                    || row["path"] == null && row["propertyName"] == null)
                    throw new ArgumentException("Each selector requires path or propertyName; optional componentType narrows it.");
                foreach (var field in row.Properties())
                    if (field.Value.Type != JTokenType.String || field.Value.ToString().Length > 1024 || field.Name != "path" && field.Value.ToString().Length == 0)
                        throw new ArgumentException("Selector fields must be exact strings; only root path may be empty.");
            }
            var digest = args["expectedSnapshotDigest"];
            if (digest != null && (digest.Type != JTokenType.String || digest.ToString().Length != 64 || digest.ToString().Any(c => !Uri.IsHexDigit(c))))
                throw new ArgumentException("expectedSnapshotDigest must be SHA256.");
            return new Options { View = view, Selectors = selectors, Offset = Integer(args,"bindingOffset",0,0,int.MaxValue),
                Limit = Integer(args,"bindingLimit",16,1,256), ClipOffset = Integer(args,"clipOffset",0,0,int.MaxValue),
                KeyOffset = Integer(args,"keyOffset",0,0,int.MaxValue), TotalKeys = Integer(args,"maxTotalKeys",4096,1,4096),
                MaxKeys = Integer(args,"maxKeysPerBinding",256,1,2000), MaxClips = Integer(args,"maxClips",300,1,2000) };
        }
        private sealed class Entry
        {
            internal AnimationClip Clip;
            internal string ClipPath, Kind;
            internal long ClipLocalId;
            internal EditorCurveBinding Binding;
        }
        private static bool Matches(EditorCurveBinding binding, JArray selectors)
        {
            return selectors.Count == 0 || selectors.OfType<JObject>().Any(s =>
                (s["path"] == null || s["path"].ToString() == binding.path) &&
                (s["propertyName"] == null || s["propertyName"].ToString() == binding.propertyName) &&
                (s["componentType"] == null || s["componentType"].ToString() == binding.type?.Name || s["componentType"].ToString() == binding.type?.FullName));
        }
        private static long LocalId(AnimationClip clip)
        {
            return AssetDatabase.TryGetGUIDAndLocalFileIdentifier(clip, out string _, out long id) ? id : 0;
        }
        internal static JObject Build(JObject args, Func<List<AnimationClip>> resolve)
        {
            var options = Parse(args);
            if (args["outputPath"] != null && args["outputPath"].ToString() != "") throw new ArgumentException("Selected animation reads do not write output files.");
            var discovered = resolve().Where(c => c != null).Distinct().OrderBy(AssetDatabase.GetAssetPath, StringComparer.Ordinal).ThenBy(LocalId).ToList();
            var clips = discovered.Skip(options.ClipOffset).Take(options.MaxClips).ToList();
            var entries = new List<Entry>();
            var index = new JArray();
            foreach (var clip in clips)
            {
                var path = AssetDatabase.GetAssetPath(clip);
                var all = AnimationUtility.GetCurveBindings(clip).Select(b => new Entry { Clip = clip, ClipPath = path, ClipLocalId = LocalId(clip), Binding = b, Kind = "float_curve" })
                    .Concat(AnimationUtility.GetObjectReferenceCurveBindings(clip).Select(b => new Entry { Clip = clip, ClipPath = path, ClipLocalId = LocalId(clip), Binding = b, Kind = "object_reference_curve" })).ToList();
                var matched = all.Where(e => Matches(e.Binding, options.Selectors)).ToList();
                entries.AddRange(matched);
                index.Add(new JObject { ["asset_path"] = path, ["local_id"] = LocalId(clip), ["name"] = clip.name, ["binding_count"] = all.Count, ["matched_binding_count"] = matched.Count });
            }
            entries = entries.OrderBy(e => e.ClipPath,StringComparer.Ordinal).ThenBy(e => e.ClipLocalId).ThenBy(e => e.Binding.path,StringComparer.Ordinal)
                .ThenBy(e => e.Binding.type?.FullName,StringComparer.Ordinal).ThenBy(e => e.Binding.propertyName,StringComparer.Ordinal).ThenBy(e => e.Kind,StringComparer.Ordinal).ToList();
            string snapshot;
            using (var hash = SHA256.Create())
                snapshot = BitConverter.ToString(hash.ComputeHash(Encoding.UTF8.GetBytes(new JArray(discovered.Select(c => new JObject {
                    ["path"] = AssetDatabase.GetAssetPath(c), ["localId"] = LocalId(c), ["dependencyHash"] = AssetDatabase.GetAssetDependencyHash(AssetDatabase.GetAssetPath(c)).ToString() })).ToString(Formatting.None)))).Replace("-", "").ToLowerInvariant();
            if (args["expectedSnapshotDigest"] != null && args["expectedSnapshotDigest"].ToString() != snapshot)
                throw new InvalidOperationException("Animation snapshot changed; restart the summary/index before continuing.");
            var rows = new JArray();
            var keyCount = 0;
            var incompleteKeys = false;
            if (options.View != "summary")
            foreach (var entry in entries.Skip(options.Offset).Take(options.Limit))
            {
                if (options.View == "details" && keyCount >= options.TotalKeys) break;
                var b = entry.Binding;
                var row = new JObject { ["clip_path"] = entry.ClipPath, ["clip_local_id"] = entry.ClipLocalId, ["path"] = b.path, ["type_name"] = b.type?.Name ?? "", ["property_name"] = b.propertyName, ["binding_kind"] = entry.Kind };
                var limit = Math.Min(options.MaxKeys, options.TotalKeys-keyCount);
                int total, returned = 0;
                if (entry.Kind == "float_curve")
                {
                    var curve = AnimationUtility.GetEditorCurve(entry.Clip,b);
                    total = curve?.length ?? 0;
                    if (options.View == "details")
                    {
                        var keys = new JArray();
                        for (var i = options.KeyOffset; i < total && returned < limit; i++,returned++)
                        {
                            var k = curve[i];
                            keys.Add(JObject.FromObject(new { time=k.time,value=k.value,inTangent=k.inTangent,outTangent=k.outTangent,inWeight=k.inWeight,outWeight=k.outWeight,weightedMode=k.weightedMode.ToString() }));
                        }
                        row["keys"] = keys; row["curve_pre_wrap_mode"] = curve?.preWrapMode.ToString(); row["curve_post_wrap_mode"] = curve?.postWrapMode.ToString();
                    }
                    row["keyframe_count"] = total;
                }
                else
                {
                    var keys = AnimationUtility.GetObjectReferenceCurve(entry.Clip,b) ?? Array.Empty<ObjectReferenceKeyframe>();
                    total = keys.Length;
                    if (options.View == "details")
                    {
                        var result = new JArray();
                        for (var i = options.KeyOffset; i < total && returned < limit; i++,returned++)
                        {
                            var k = keys[i];
                            result.Add(new JObject { ["time"] = k.time, ["asset_path"] = k.value != null ? AssetDatabase.GetAssetPath(k.value) : "", ["name"] = k.value != null ? k.value.name : "", ["type_name"] = k.value != null ? k.value.GetType().Name : "" });
                        }
                        row["object_reference_keys"] = result;
                    }
                    row["object_reference_key_count"] = total;
                }
                if (options.View == "details")
                {
                    var end = Math.Min(total,(long)options.KeyOffset+returned);
                    var omitted = options.KeyOffset > 0 || end < total;
                    row["keys_truncated"] = omitted; row["keyOffset"] = options.KeyOffset; row["returnedKeyCount"] = returned;
                    row["nextKeyOffset"] = end < total ? new JValue(end) : JValue.CreateNull();
                    row["omittedKeyCount"] = total-returned;
                    if (end < total)
                    {
                        var keyRequest = new JObject();
                        keyRequest["bindingView"] = "details"; keyRequest["clipOffset"] = options.ClipOffset; keyRequest["bindingOffset"] = options.Offset + rows.Count;
                        keyRequest["bindingLimit"] = 1; keyRequest["keyOffset"] = end;
                        keyRequest["expectedSnapshotDigest"] = snapshot; row["nextKeyRequest"] = keyRequest;
                    }
                    incompleteKeys |= omitted; keyCount += returned;
                }
                rows.Add(row);
            }
            var bindingEnd = (long)options.Offset+rows.Count;
            var nextBinding = options.View != "summary" && bindingEnd < entries.Count ? (int?)bindingEnd : null;
            var nextClip = options.ClipOffset+clips.Count < discovered.Count ? (int?)(options.ClipOffset+clips.Count) : null;
            var nextRequest = (JObject)args.DeepClone(); nextRequest["expectedSnapshotDigest"] = snapshot; nextRequest["keyOffset"] = 0;
            if (nextBinding.HasValue) nextRequest["bindingOffset"] = nextBinding.Value;
            else if (nextClip.HasValue) { nextRequest["clipOffset"] = nextClip.Value; nextRequest["bindingOffset"] = 0; }
            return new JObject { ["type"] = "animation_bindings_selection", ["schema"] = "vrcforge.animation_binding_selection.v1", ["version"] = "1", ["snapshotDigest"] = snapshot,
                ["selection"] = new JObject { ["explicit"] = true, ["view"] = options.View, ["selectors"] = options.Selectors.DeepClone(), ["matchStatus"] = entries.Count == 0 ? "no_matches" : "matched" },
                ["clips"] = options.View == "summary" ? index : new JArray(index.Where(c => rows.Any(r => r["clip_path"].ToString() == c["asset_path"].ToString() && r["clip_local_id"].Value<long>() == c["local_id"].Value<long>()))), ["bindings"] = rows,
                ["summary"] = new JObject { ["totalDiscoveredClips"] = discovered.Count, ["scannedClipCount"] = clips.Count, ["totalBindingCount"] = index.Sum(c => c["binding_count"].Value<int>()), ["matchedBindingCount"] = entries.Count, ["returnedBindingCount"] = rows.Count, ["returnedKeyCount"] = keyCount },
                ["paging"] = new JObject { ["clipOffset"] = options.ClipOffset, ["bindingOffset"] = options.Offset, ["bindingLimit"] = options.Limit, ["nextBindingOffset"] = nextBinding.HasValue ? new JValue(nextBinding.Value) : JValue.CreateNull(), ["nextClipOffset"] = nextClip.HasValue ? new JValue(nextClip.Value) : JValue.CreateNull(), ["maxTotalKeys"] = options.TotalKeys, ["keyDetailsOmitted"] = options.View != "details" || incompleteKeys, ["allSelectedDetailsReturned"] = options.View == "details" && options.Offset == 0 && options.ClipOffset == 0 && !nextBinding.HasValue && !nextClip.HasValue && !incompleteKeys, ["nextRequest"] = nextBinding.HasValue || nextClip.HasValue ? nextRequest : null },
                ["readHints"] = new JObject { ["tool"] = "vrcforge_scan_animation_bindings", ["index"] = "Use bindingView=index for exact binding identities, then bindingSelectors with path/propertyName/componentType and bindingView=details. Empty path selects the animator root; omitted field is unrestricted.", ["continuation"] = "Use paging.nextRequest for binding/clip pages; merge each nextKeyRequest into the original request for incomplete keys. Preserve clip selectors and project/target context. A no_matches result means no binding matched, not a successful find.", ["legacy"] = "Omit selection fields to retain the original includeBindingDetails behavior." } };
        }
    }
}
