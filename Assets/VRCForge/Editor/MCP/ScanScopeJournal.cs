using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Security.Cryptography;
using System.Text;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEngine;
using VRCForge.Core.MCP;
using Object = UnityEngine.Object;

namespace VRCForge.Editor
{
    [InitializeOnLoad]
    internal static class ScanScopeJournal
    {
        private const string StateKey = "VRCForge.ScanScopeJournal.v1";
        private const int MaxScopes = 64;
        private const int MaxObjects = 2048;
        private const int MaxStateBytes = 4 * 1024 * 1024;
        private static Journal state;

        private sealed class Journal
        {
            public string session = Guid.NewGuid().ToString("D");
            public long epoch;
            public long sceneEpoch;
            public bool sealedForReload;
            public Dictionary<string, Watch> watches = new Dictionary<string, Watch>();
            public Dictionary<string, long> paths = new Dictionary<string, long>();
            public Dictionary<string, Scope> scopes = new Dictionary<string, Scope>();
        }

        private sealed class Watch
        {
            public string hash;
            public int dirty;
            public long revision;
        }

        private sealed class Scope
        {
            public long epoch;
            public long sceneEpoch;
            public bool scene;
            public string capturedAt;
            public string[] roots;
            public JObject selector;
            public Dictionary<string, string> objects;
            public Dictionary<string, string> files;
        }

        static ScanScopeJournal()
        {
            try
            {
                var saved = SessionState.GetString(StateKey, "");
                state = string.IsNullOrEmpty(saved) ? new Journal() : JsonConvert.DeserializeObject<Journal>(saved);
                if (state == null || state.watches == null || state.paths == null || state.scopes == null)
                    throw new InvalidDataException();
                if (!string.IsNullOrEmpty(saved))
                {
                    // SessionState retains the record, not evidence of events during
                    // the detached-observer interval. Real reloads therefore expire it.
                    state.epoch++;
                    state.sealedForReload = false;
                }
            }
            catch { state = new Journal(); }
            AssemblyReloadEvents.beforeAssemblyReload += BeforeReload;
            ObjectChangeEvents.changesPublished += ChangesPublished;
            Undo.postprocessModifications += Postprocess;
            Undo.undoRedoPerformed += Observe;
            EditorApplication.playModeStateChanged += PlayChanged;
            Persist();
        }

        internal static JObject Capabilities()
        {
            return new JObject
            {
                ["schema"] = "vrcforge.scan_scope.v1",
                ["implementation"] = "scan-scope-v1",
                ["tools"] = new JArray("vrc_capture_scan_scope", "vrc_validate_scan_scope"),
                ["reloadPolicy"] = "unprovable_observer_gap_expires",
            };
        }

        private static void Persist()
        {
            var text = JsonConvert.SerializeObject(state);
            if (Encoding.UTF8.GetByteCount(text) > MaxStateBytes)
            {
                state.epoch++;
                state.scopes.Clear();
                state.watches.Clear();
                state.paths.Clear();
                text = JsonConvert.SerializeObject(state);
            }
            // Editor-session-owned, bounded state; no files, socket or asset writes.
            SessionState.SetString(StateKey, text);
        }

        private static void BeforeReload()
        {
            Observe();
            state.sealedForReload = true;
            Persist();
        }

        private static void PlayChanged(PlayModeStateChange change)
        {
            Observe();
            if (change == PlayModeStateChange.ExitingEditMode || change == PlayModeStateChange.ExitingPlayMode)
                state.sceneEpoch++;
            Persist();
        }

        private static UndoPropertyModification[] Postprocess(UndoPropertyModification[] modifications)
        {
            foreach (var modification in modifications)
            {
                var target = modification.currentValue?.target ?? modification.previousValue?.target;
                if (target != null && state.watches.TryGetValue(Identity(target), out var watch))
                    watch.revision++;
            }
            Observe();
            return modifications;
        }

        private static void ChangesPublished(ref ObjectChangeEventStream stream) { Observe(); }

        private static void Observe()
        {
            foreach (var pair in state.watches)
            {
                try
                {
                    var target = Resolve(pair.Key);
                    var hash = target == null ? "missing" : MemoryHash(target);
                    var dirty = target == null ? -1 : EditorUtility.GetDirtyCount(target);
                    if (hash != pair.Value.hash || dirty != pair.Value.dirty)
                    {
                        pair.Value.revision++;
                        pair.Value.hash = hash;
                        pair.Value.dirty = dirty;
                    }
                }
                catch { state.epoch++; }
            }
            Persist();
        }

        internal static void Imported(string[] imported, string[] deleted, string[] moved, string[] movedFrom)
        {
            foreach (var path in imported.Concat(deleted).Concat(moved).Concat(movedFrom).Distinct())
                if (state.paths.ContainsKey(path)) state.paths[path]++;
            Observe();
        }

        private static string Identity(Object target)
        {
            var id = GlobalObjectId.GetGlobalObjectIdSlow(target);
            return id.targetObjectId != 0 && id.assetGUID.ToString() != "00000000000000000000000000000000"
                ? "global:" + id : "instance:" + target.GetInstanceID();
        }

        private static Object Resolve(string id)
        {
            if (id.StartsWith("global:", StringComparison.Ordinal) && GlobalObjectId.TryParse(id.Substring(7), out var global))
                return GlobalObjectId.GlobalObjectIdentifierToObjectSlow(global);
            if (id.StartsWith("instance:", StringComparison.Ordinal) && int.TryParse(id.Substring(9), out var instance))
                return EditorUtility.InstanceIDToObject(instance);
            return null;
        }

        private static string Hash(string text)
        {
            using (var sha = SHA256.Create())
                return BitConverter.ToString(sha.ComputeHash(Encoding.UTF8.GetBytes(text))).Replace("-", "").ToLowerInvariant();
        }

        private static string MemoryHash(Object target)
        {
            // Material inventory reports texture metadata; disk fingerprints
            // cover asset contents. Serializing pixel buffers is unbounded and
            // adds data which that reader does not observe.
            var text = target is Texture texture
                ? TextureObservation(texture).ToString(Formatting.None) : EditorJsonUtility.ToJson(target);
            // Material inventory observes mesh names and assignments, not mesh
            // vertex buffers. Keep name changes visible on their owning slot.
            var mesh = target is SkinnedMeshRenderer skinned ? skinned.sharedMesh
                : target is MeshFilter filter ? filter.sharedMesh : null;
            if (mesh != null) text += "\nmeshName:" + mesh.name;
            if (target is GameObject hierarchyObject) text += AvatarPrimitiveCrudCore.GetTransformPath(hierarchyObject.transform);
            if (target is Component hierarchyComponent) text += AvatarPrimitiveCrudCore.GetTransformPath(hierarchyComponent.transform);
            if (target is AnimationClip clip)
            {
                var curves = new JArray();
                foreach (var binding in AnimationUtility.GetCurveBindings(clip))
                    curves.Add(new JObject { ["path"] = binding.path, ["type"] = binding.type.FullName,
                        ["property"] = binding.propertyName, ["curve"] = JToken.FromObject(AnimationUtility.GetEditorCurve(clip, binding)) });
                foreach (var binding in AnimationUtility.GetObjectReferenceCurveBindings(clip))
                    curves.Add(new JObject { ["path"] = binding.path, ["type"] = binding.type.FullName,
                        ["property"] = binding.propertyName, ["keys"] = new JArray(AnimationUtility.GetObjectReferenceCurve(clip, binding)
                            .Select(key => new JObject { ["time"] = key.time, ["object"] = key.value == null ? "" : Identity(key.value) })) });
                text += curves.ToString(Formatting.None);
            }
            if (Encoding.UTF8.GetByteCount(text) > MaxStateBytes)
            {
                var failure = new InvalidDataException("object_too_large");
                failure.Data["objectType"] = target.GetType().FullName;
                failure.Data["propertyPath"] = "serialized_object";
                throw failure;
            }
            return Hash(text);
        }

        internal static JObject TextureObservation(Texture texture)
        {
            var path = AssetDatabase.GetAssetPath(texture) ?? "";
            var importer = string.IsNullOrWhiteSpace(path) ? null : AssetImporter.GetAtPath(path) as TextureImporter;
            return new JObject { ["name"] = texture.name, ["width"] = texture.width, ["height"] = texture.height,
                ["bytes"] = UnityEngine.Profiling.Profiler.GetRuntimeMemorySizeLong(texture),
                ["assetPath"] = path, ["importerType"] = importer == null ? "" : importer.textureType.ToString(),
                ["importerMaxTextureSize"] = importer == null ? 0 : importer.maxTextureSize };
        }

        private static bool AssetPath(string path)
        {
            return !string.IsNullOrEmpty(path) && (path.StartsWith("Assets/", StringComparison.Ordinal)
                || path.StartsWith("Packages/", StringComparison.Ordinal))
                && !path.Contains("\\") && !path.Split('/').Any(part => part == ".." || part == "." || part == "");
        }

        private static string FileHash(string path, ref long budget)
        {
            if (!AssetPath(path)) throw new InvalidDataException("invalid_asset_path");
            var file = new FileInfo(path);
            if (!file.Exists || file.Length > budget) throw new InvalidDataException("disk_scope_unavailable");
            budget -= file.Length;
            var before = file.Length + ":" + file.LastWriteTimeUtc.Ticks;
            string digest;
            // Project-scoped, read-only handle, closed before returning.
            using (var input = file.OpenRead())
            using (var sha = SHA256.Create()) digest = BitConverter.ToString(sha.ComputeHash(input));
            file.Refresh();
            if (before != file.Length + ":" + file.LastWriteTimeUtc.Ticks) throw new InvalidDataException("disk_changed_during_read");
            return before + ":" + digest;
        }

        // Material inventory reads hierarchy membership, renderer slots, meshes,
        // materials and shader semantics; expression menus and animator graphs
        // are not dependencies of that observation.
        internal static bool MaterialReferenceRelevant(Object owner, string propertyPath, Object referenced, bool includeTextures)
        {
            if (owner is Renderer) return propertyPath.StartsWith("m_Materials", StringComparison.Ordinal) || propertyPath == "m_Mesh";
            if (owner is MeshFilter) return propertyPath == "m_Mesh";
            if (owner is Material) return propertyPath == "m_Shader" || propertyPath == "m_Parent";
            return false;
        }

        internal static IEnumerable<Texture> MaterialTextureDependencies(Material material, bool includeTextures)
        {
            if (material == null || !includeTextures) return Enumerable.Empty<Texture>();
            // Match the inventory reader. Serialized TexEnv slots may retain
            // missing references which GetTexture does not report as textures.
            return material.GetTexturePropertyNames().Select(material.GetTexture).Where(texture => texture != null);
        }

        private static Scope Measure(string[] roots, JObject selector)
        {
            var materialScope = (string)selector?["scanTool"] == "vrcforge_scan_avatar_materials";
            var includeTextures = (bool?)selector?["arguments"]?["includeTextures"] ?? true;
            var scope = new Scope { epoch = state.epoch, sceneEpoch = state.sceneEpoch, roots = roots,
                capturedAt = DateTime.UtcNow.ToString("o"), objects = new Dictionary<string, string>(), files = new Dictionary<string, string>() };
            var pending = new Queue<Object>(roots.Select(Resolve));
            var paths = new HashSet<string>();
            while (pending.Count > 0)
            {
                var target = pending.Dequeue();
                if (target == null) throw new InvalidDataException("scope_object_missing");
                var id = Identity(target);
                if (scope.objects.ContainsKey(id)) continue;
                if (scope.objects.Count >= MaxObjects) throw new InvalidDataException("scope_too_large");
                var path = AssetDatabase.GetAssetPath(target);
                var hash = MemoryHash(target);
                var dirty = EditorUtility.GetDirtyCount(target);
                if (!state.watches.TryGetValue(id, out var watch))
                    state.watches[id] = watch = new Watch { hash = hash, dirty = dirty };
                if (watch.hash != hash || watch.dirty != dirty)
                {
                    watch.revision++;
                    watch.hash = hash;
                    watch.dirty = dirty;
                }
                scope.objects[id] = watch.revision + ":" + dirty + ":" + hash;
                scope.scene |= !EditorUtility.IsPersistent(target);
                if (AssetPath(path) && paths.Add(path))
                {
                    foreach (var dependency in materialScope && !(target is Shader) ? Array.Empty<string>() : AssetDatabase.GetDependencies(path, true))
                    {
                        if (!AssetPath(dependency)) continue;
                        paths.Add(dependency);
                        foreach (var asset in materialScope ? Array.Empty<Object>() : AssetDatabase.LoadAllAssetsAtPath(dependency))
                            if (asset != null && !(asset is MonoScript)) pending.Enqueue(asset);
                    }
                }
                if (target is GameObject gameObject)
                {
                    foreach (var component in gameObject.GetComponents<Component>())
                    {
                        if (materialScope && !(component is Transform) && !(component is Renderer) && !(component is MeshFilter)) continue;
                        if (component == null) throw new InvalidDataException("missing_component");
                        pending.Enqueue(component);
                    }
                    foreach (Transform child in gameObject.transform) pending.Enqueue(child.gameObject);
                }
                if (materialScope && target is Material material)
                    foreach (var texture in MaterialTextureDependencies(material, includeTextures)) pending.Enqueue(texture);
                using (var serialized = new SerializedObject(target))
                {
                    var property = serialized.GetIterator();
                    while (property.Next(true))
                    {
                        if (materialScope && property.propertyType == SerializedPropertyType.ObjectReference
                            && !MaterialReferenceRelevant(target, property.propertyPath, property.objectReferenceValue, includeTextures)) continue;
                        if (property.propertyType == SerializedPropertyType.ObjectReference && property.objectReferenceValue == null
                            && property.objectReferenceInstanceIDValue != 0)
                        {
                            var failure = new InvalidDataException("unresolved_scope_reference");
                            failure.Data["objectType"] = target.GetType().FullName;
                            failure.Data["propertyPath"] = property.propertyPath;
                            throw failure;
                        }
                        if (materialScope && property.propertyType == SerializedPropertyType.ObjectReference
                            && property.objectReferenceValue is Mesh) continue;
                        if (property.propertyType == SerializedPropertyType.ObjectReference && property.objectReferenceValue != null
                            && !(target is Transform && property.name == "m_Father")
                            && !(property.objectReferenceValue is MonoScript)) pending.Enqueue(property.objectReferenceValue);
                    }
                }
            }
            long budget = 128 * 1024 * 1024;
            foreach (var path in paths.OrderBy(value => value, StringComparer.Ordinal))
            {
                if (!state.paths.ContainsKey(path)) state.paths[path] = 0;
                scope.files[path] = state.paths[path] + ":" + FileHash(path, ref budget) + ":" + FileHash(path + ".meta", ref budget);
            }
            return scope;
        }

        private static string[] Roots(JObject parameters)
        {
            var roots = new HashSet<string>();
            var arguments = parameters["arguments"] as JObject ?? new JObject();
            var tool = (string)parameters["scanTool"] ?? "";
            if ((bool?)arguments["includeAllProjectClips"] == true)
                throw new InvalidDataException("project_inventory_scope_unavailable");
            if (tool.Length > 0 && !new[] { "vrcforge_scan_fx_animator", "vrcforge_scan_animation_bindings", "vrcforge_scan_wardrobe",
                "vrcforge_scan_avatar_controls", "vrcforge_scan_avatar_parameters", "vrcforge_scan_avatar_materials",
                "vrcforge_scan_avatar_items", "vrcforge_read_avatar_descriptor" }.Contains(tool))
                throw new InvalidDataException("scan_scope_undeclared");
            var explicitPaths = parameters["assetPaths"] as JArray;
            var objectIds = parameters["objectIds"] as JArray;
            if (objectIds != null) foreach (var id in objectIds) roots.Add(id.Value<string>());
            var paths = new List<string>();
            if (explicitPaths != null) paths.AddRange(explicitPaths.Values<string>());
            foreach (var name in new[] { "assetPath", "controllerPath", "clipPath" })
                if (!string.IsNullOrEmpty((string)arguments[name])) paths.Add((string)arguments[name]);
            if (arguments["clipPaths"] is JArray clips) paths.AddRange(clips.Values<string>());
            foreach (var path in paths)
            {
                if (!AssetPath(path)) throw new InvalidDataException("invalid_asset_path");
                var asset = AssetDatabase.LoadMainAssetAtPath(path);
                if (asset == null) throw new InvalidDataException("scope_asset_missing");
                roots.Add(Identity(asset));
            }
            var avatarPath = (string)arguments["avatarPath"] ?? "";
            if (roots.Count == 0 || (tool == "vrcforge_scan_animation_bindings" && avatarPath.Length > 0))
            {
                if (!tool.StartsWith("vrcforge_scan_", StringComparison.Ordinal) && tool != "vrcforge_read_avatar_descriptor")
                    throw new InvalidDataException("scan_scope_undeclared");
                var path = avatarPath;
                var avatars = Resources.FindObjectsOfTypeAll<VRC.SDK3.Avatars.Components.VRCAvatarDescriptor>()
                    .Where(item => !EditorUtility.IsPersistent(item) && item.gameObject.scene.IsValid())
                    .Where(item => path.Length == 0 || AvatarPrimitiveCrudCore.GetTransformPath(item.transform) == path).ToArray();
                if (avatars.Length == 0 || (path.Length > 0 && avatars.Length != 1)) throw new InvalidDataException("avatar_scope_unavailable");
                foreach (var avatar in avatars) roots.Add(Identity(avatar.gameObject));
            }
            if (roots.Count == 0 || roots.Count > MaxObjects) throw new InvalidDataException("scope_roots_unavailable");
            return roots.OrderBy(value => value, StringComparer.Ordinal).ToArray();
        }

        private static JObject Receipt(bool valid, string reason)
        {
            return new JObject { ["schema"] = "vrcforge.scan_scope.v1", ["valid"] = valid,
                ["verified"] = valid, ["reason"] = reason, ["editorSession"] = state.session,
                ["changedAssetPaths"] = new JArray() };
        }

        private static JObject DiagnosticObjectState(string value)
        {
            if (value == null) return null;
            var parts = value.Split(':');
            if (parts.Length != 3 || !long.TryParse(parts[0], out var revision) || !int.TryParse(parts[1], out var dirty))
                throw new InvalidDataException("diagnostic_object_state_unavailable");
            return new JObject { ["contentHash"] = parts[2], ["dirtyCount"] = dirty, ["revision"] = revision };
        }

        private static JArray ObjectDiagnostics(Scope saved, Scope current)
        {
            var rows = new JArray();
            foreach (var id in saved.objects.Keys.Union(current.objects.Keys).OrderBy(value => value, StringComparer.Ordinal).Take(MaxObjects * 2))
            {
                saved.objects.TryGetValue(id, out var before);
                current.objects.TryGetValue(id, out var after);
                var oldState = DiagnosticObjectState(before);
                var newState = DiagnosticObjectState(after);
                var target = Resolve(id);
                var classification = oldState == null ? "added" : newState == null ? "missing"
                    : !JToken.DeepEquals(oldState["contentHash"], newState["contentHash"]) ? "content_changed"
                    : !JToken.DeepEquals(oldState["dirtyCount"], newState["dirtyCount"]) ? "dirty_count_only"
                    : !JToken.DeepEquals(oldState["revision"], newState["revision"]) ? "revision_only" : "unchanged";
                rows.Add(new JObject { ["objectId"] = id, ["assetPath"] = target == null ? "" : AssetDatabase.GetAssetPath(target),
                    ["objectType"] = target == null ? "" : target.GetType().FullName,
                    ["old"] = oldState, ["new"] = newState, ["classification"] = classification });
            }
            return rows;
        }

        internal static JObject Capture(JObject parameters)
        {
            try
            {
                Observe();
                var scope = Measure(Roots(parameters ?? new JObject()), parameters);
                scope.selector = (JObject)(parameters ?? new JObject()).DeepClone();
                if (state.scopes.Count >= MaxScopes) throw new InvalidDataException("scope_journal_full");
                var token = "scope_" + Guid.NewGuid().ToString("N");
                state.scopes.Add(token, scope);
                Persist();
                var result = Receipt(true, "");
                result["token"] = token;
                result["capturedAt"] = scope.capturedAt;
                result["dependencyCoverage"] = "complete";
                result["assetPaths"] = new JArray(scope.files.Keys.Where(path => path.StartsWith("Assets/", StringComparison.Ordinal)));
                return result;
            }
            catch (Exception exception)
            {
                var receipt = Receipt(false, exception.Message);
                if (exception.Data["objectType"] is string objectType && exception.Data["propertyPath"] is string propertyPath)
                    receipt["failureContext"] = new JObject { ["objectType"] = objectType, ["propertyPath"] = propertyPath };
                return receipt;
            }
        }

        internal static JObject Validate(JObject parameters)
        {
            try
            {
                Observe();
                var token = (string)parameters?["token"];
                if (token == null || !state.scopes.TryGetValue(token, out var saved)) return Receipt(false, "scope_token_unavailable");
                if (saved.epoch != state.epoch) return Receipt(false, "domain_reload_observation_gap");
                if (saved.scene && saved.sceneEpoch != state.sceneEpoch) return Receipt(false, "play_mode_scene_scope_changed");
                if (saved.selector == null || !saved.roots.SequenceEqual(Roots(saved.selector)))
                    return Receipt(false, "scope_membership_changed");
                var current = Measure(saved.roots, saved.selector);
                var changed = saved.files.Keys.Union(current.files.Keys)
                    .Where(path => !saved.files.TryGetValue(path, out var old) || !current.files.TryGetValue(path, out var now) || old != now).ToArray();
                var same = changed.Length == 0 && saved.objects.Count == current.objects.Count
                    && saved.objects.All(pair => current.objects.TryGetValue(pair.Key, out var value) && value == pair.Value);
                var memoryPaths = saved.objects.Keys.Union(current.objects.Keys)
                    .Where(id => !saved.objects.TryGetValue(id, out var old) || !current.objects.TryGetValue(id, out var now) || old != now)
                    .Select(Resolve).Where(target => target != null).Select(AssetDatabase.GetAssetPath)
                    .Where(path => path.StartsWith("Assets/", StringComparison.Ordinal));
                var result = Receipt(same, same ? "" : "scope_content_or_revision_changed");
                result["changedAssetPaths"] = new JArray(changed.Where(path => path.StartsWith("Assets/", StringComparison.Ordinal)).Union(memoryPaths));
                if ((bool?)parameters?["includeDiagnostics"] == true)
                {
                    // Diagnostics project the existing comparison only; failure cannot alter validity.
                    try { result["objectDiagnostics"] = ObjectDiagnostics(saved, current); }
                    catch (Exception exception) { result["diagnosticsError"] = exception.GetType().Name; }
                }
                return result;
            }
            catch (Exception exception) { return Receipt(false, exception.Message); }
        }
    }

    internal sealed class ScanScopeAssetChanges : AssetPostprocessor
    {
        private static void OnPostprocessAllAssets(string[] imported, string[] deleted, string[] moved, string[] movedFrom)
        {
            ScanScopeJournal.Imported(imported, deleted, moved, movedFrom);
        }
    }

    [VRCForgeCommand(toolId: "vrc_capture_scan_scope", Access = VRCForgeCommandAccess.ReadOnly,
        Summary = "When to use: capture a bounded scan scope before a read. When NOT to use: save, refresh, approve or execute a mutation. Negative example: a captured scope does not authorize writes.")]
    public static class CaptureScanScopeTool
    {
        public class Parameters
        {
            [VRCForgeInput("Owner scan tool name.", IsRequired = false)] public string scanTool { get; set; }
            [VRCForgeInput("Exact scan arguments.", IsRequired = false)] public object arguments { get; set; }
            [VRCForgeInput("Exact asset roots.", IsRequired = false)] public string[] assetPaths { get; set; }
            [VRCForgeInput("Exact journal object identities.", IsRequired = false)] public string[] objectIds { get; set; }
        }
        public static object HandleCommand(JObject parameters)
        {
            return VRCForgeToolResult.Completed("Captured read-only scan scope.", ScanScopeJournal.Capture(parameters));
        }
    }

    [VRCForgeCommand(toolId: "vrc_validate_scan_scope", Access = VRCForgeCommandAccess.ReadOnly,
        Summary = "When to use: verify an exact captured scope before reuse. When NOT to use: approve writes, save or reload assets. Negative example: unavailable continuity is never valid.")]
    public static class ValidateScanScopeTool
    {
        public class Parameters
        {
            [VRCForgeInput("Exact opaque scope token.")] public string token { get; set; }
            [VRCForgeInput("Include bounded read-only per-object comparison details.", IsRequired = false)] public bool includeDiagnostics { get; set; }
        }
        public static object HandleCommand(JObject parameters)
        {
            return VRCForgeToolResult.Completed("Validated read-only scan scope.", ScanScopeJournal.Validate(parameters));
        }
    }
}
