using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Security.Cryptography;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEngine;
using UnityEngine.Animations;
using UnityEngine.Playables;
using VRCForge.Core.MCP;

namespace VRCForge.Editor
{
    [InitializeOnLoad]
    internal static class RuntimeObservation
    {
        internal const int MaxFrames = 32;
        internal static readonly Dictionary<string, Job> Jobs = new Dictionary<string, Job>();
        private static Job active;
        static RuntimeObservation()
        {
            AssemblyReloadEvents.beforeAssemblyReload += () => active?.Finish("assembly_reload");
            EditorApplication.playModeStateChanged += state => { if (state == PlayModeStateChange.ExitingPlayMode) active?.Finish("play_mode_ended"); };
            EditorApplication.update += Watch;
        }
        private static void Watch()
        {
            if (active != null && EditorApplication.timeSinceStartup > active.Deadline) active.Finish("sampling_deadline_expired");
        }
        internal static object PublicMember(object value, string name)
        {
            if (value == null) return null;
            var type = value.GetType();
            return type.GetProperty(name, BindingFlags.Public | BindingFlags.Instance)?.GetValue(value)
                ?? type.GetField(name, BindingFlags.Public | BindingFlags.Instance)?.GetValue(value);
        }
        internal static void ValidateLimits(double duration, int frames, int width, int height)
        {
            if (double.IsNaN(duration) || double.IsInfinity(duration) || duration < .1 || duration > 10 || frames < 2 || frames > MaxFrames
                || width < 128 || height < 128 || width > 512 || height > 512 || (long)(frames + 1) * width * height * 3 > 24 * 1024 * 1024)
                throw new ArgumentException("Observation requires 0.1–10 seconds, 2–32 frames, 128–512 pixels, at most 24 MiB raw pixels.");
        }
        internal static int DueIndex(double elapsed, double duration, int frames)
        {
            return Math.Min(frames - 1, (int)Math.Floor(elapsed / (duration / (frames - 1))));
        }
        internal static string Digest(byte[] bytes)
        {
            using (var hash = SHA256.Create()) return BitConverter.ToString(hash.ComputeHash(bytes)).Replace("-", "").ToLowerInvariant();
        }
        private static string CoreIdentity()
        {
            var descriptor = JObject.Parse(File.ReadAllText(Path.Combine(Path.GetDirectoryName(Application.dataPath), "Library/VRCForge/mcp-core.json")));
            return string.Join("|", new[] { "projectId", "instanceId", "processId", "processStartTime" }.Select(key => descriptor[key]?.ToString() ?? ""));
        }
        internal static List<AnimatorControllerPlayable> Controllers(Animator animator)
        {
            var graphs = UnityEditor.Playables.Utility.GetAllGraphs();
            var found = new List<AnimatorControllerPlayable>();
            var visited = new HashSet<Playable>();
            var queue = new Queue<Playable>();
            foreach (var graph in graphs)
            {
                if (!graph.IsValid()) continue;
                for (int i = 0; i < graph.GetOutputCount(); i++)
                {
                    var output = graph.GetOutput(i);
                    if (output.GetPlayableOutputType() == typeof(AnimationPlayableOutput)
                        && ((AnimationPlayableOutput)output).GetTarget() == animator) queue.Enqueue(output.GetSourcePlayable());
                }
            }
            while (queue.Count > 0)
            {
                var value = queue.Dequeue();
                if (!value.IsValid() || !visited.Add(value)) continue;
                if (visited.Count > 512) throw new InvalidOperationException("Animator controller discovery exceeds bounded external graph nodes: actualCount=" + visited.Count + ", limit=512.");
                if (value.GetPlayableType() == typeof(AnimatorControllerPlayable))
                {
                    var controller = (AnimatorControllerPlayable)value;
                    if (controller.GetLayerCount() > 0) found.Add(controller);
                    // Its layers expose the required state evidence. Internal clip/mixer
                    // inputs do not represent additional controller branches; keep walking
                    // the queued sibling branches targeting this same Animator instead.
                    continue;
                }
                for (int i = 0; i < value.GetInputCount(); i++) queue.Enqueue(value.GetInput(i));
            }
            if (found.Count == 0)
                throw new InvalidOperationException("No running AnimatorControllerPlayable layers target the GM AvatarAnimator: controllerCount=0, visitedNodeCount=" + visited.Count + ".");
            var layerCount = found.Sum(item => item.GetLayerCount());
            if (layerCount > 256)
                throw new InvalidOperationException("Animator controller layers exceed bounded observation size: actualCount=" + layerCount + ", limit=256, controllerCount=" + found.Count + ".");
            return found;
        }
        internal static JObject ClipSnapshot(AnimatorClipInfo[] infos)
        {
            const int limit = 16;
            var clips = new JArray();
            for (int i = 0; i < Math.Min(infos.Length, limit); i++)
            {
                var clip = infos[i].clip;
                var path = clip ? AssetDatabase.GetAssetPath(clip) : "";
                clips.Add(new JObject { ["name"] = clip ? clip.name : "", ["weight"] = infos[i].weight,
                    ["assetPath"] = path, ["assetGuid"] = string.IsNullOrEmpty(path) ? "" : AssetDatabase.AssetPathToGUID(path),
                    ["assetIdentity"] = !clip ? "missing_clip" : string.IsNullOrEmpty(path) ? "runtime_no_asset" : "asset" });
            }
            return new JObject { ["clips"] = clips, ["totalCount"] = infos.Length, ["limit"] = limit, ["truncated"] = infos.Length > limit };
        }
        internal static object Start(JObject args)
        {
            Job job = null;
            try
            {
                if (active != null) throw new InvalidOperationException("An observation job is already running; query its status.");
                if (!EditorApplication.isPlaying) throw new InvalidOperationException("Play Mode is required.");
                var duration = args.Value<double>("durationSeconds");
                var count = args.Value<int>("frameCount");
                var width = args.Value<int>("width"); var height = args.Value<int>("height");
                ValidateLimits(duration, count, width, height);
                var path = args.Value<string>("avatarPath") ?? "";
                if (string.IsNullOrWhiteSpace(path)) throw new ArgumentException("An exact Avatar path is required.");
                var matches = GestureManagerRuntimeBridge.Discover(path).Where(item => item.Module != null && item.Behaviour && item.Behaviour.isActiveAndEnabled).ToArray();
                if (matches.Length != 1) throw new InvalidOperationException("Exactly one active connected GM module must match the Avatar.");
                var manager = matches[0];
                var avatar = PublicMember(manager.Module, "Avatar") as GameObject;
                var animator = PublicMember(manager.Module, "AvatarAnimator") as Animator;
                if (!avatar || !animator || animator.gameObject != avatar || !animator.isActiveAndEnabled)
                    throw new InvalidOperationException("GM does not expose an active AvatarAnimator on its actual Avatar instance.");
                var controllers = Controllers(animator);
                var probes = PrepareRendererProbes(args["rendererProbes"], avatar, path);
                var name = args.Value<string>("parameterName") ?? "";
                if (!GestureManagerRuntimeBridge.TryReadParameter(manager, name, out var parameter, out var before, out _)) throw new ArgumentException("The exact existing GM parameter was not found.");
                var requested = args.Value<float>("value");
                if (float.IsNaN(requested) || float.IsInfinity(requested)) throw new ArgumentException("Parameter value must be finite.");
                if (args["parameterSteps"] != null && !(args["parameterSteps"] is JArray)) throw new ArgumentException("parameterSteps must be an array.");
                var stepTokens = args["parameterSteps"] as JArray ?? new JArray();
                if (stepTokens.Count > 64) throw new ArgumentException("parameterSteps must contain at most 64 steps.");
                var steps = new List<ParameterStep>(); double previousStepTime = -1;
                foreach (var token in stepTokens)
                {
                    var step = token as JObject;
                    if (step == null || step.Properties().Select(property => property.Name).Except(new[] { "timeSeconds", "parameterName", "value" }).Any()
                        || new[] { "timeSeconds", "parameterName", "value" }.Any(key => step[key] == null)
                        || !new[] { JTokenType.Integer, JTokenType.Float }.Contains(step["timeSeconds"].Type)
                        || step["parameterName"].Type != JTokenType.String
                        || !new[] { JTokenType.Integer, JTokenType.Float }.Contains(step["value"].Type))
                        throw new ArgumentException("Each parameter step requires exactly timeSeconds, parameterName, and value.");
                    var time = step.Value<double>("timeSeconds"); var stepName = step.Value<string>("parameterName") ?? ""; var stepValue = step.Value<float>("value");
                    if (double.IsNaN(time) || double.IsInfinity(time) || time < 0 || time > duration || time <= previousStepTime || string.IsNullOrWhiteSpace(stepName)
                        || float.IsNaN(stepValue) || float.IsInfinity(stepValue)) throw new ArgumentException("parameterSteps must be finite, in range, and strictly increasing.");
                    if (!GestureManagerRuntimeBridge.TryReadParameter(manager, stepName, out var stepParameter, out _, out var stepType))
                        throw new ArgumentException("The exact existing GM parameter was not found: " + stepName);
                    if (!ValidStepValue(stepType, stepValue)) throw new ArgumentException("parameterSteps value does not match parameter type: " + stepName);
                    steps.Add(new ParameterStep { TimeSeconds = time, Name = stepName, Value = stepValue, Parameter = stepParameter, Type = stepType });
                    previousStepTime = time;
                }
                var id = args.Value<string>("jobId") ?? "";
                if (!Guid.TryParseExact(id, "N", out _) || Jobs.ContainsKey(id)) throw new ArgumentException("Observation job identity is invalid or already used.");
                var root = Path.GetFullPath(Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "VRCForge/agentic-app/artifacts/dashboard/latest/runtime-observations"));
                var output = Path.GetFullPath(args.Value<string>("outputDirectory") ?? "");
                if (!string.Equals(output, Path.Combine(root, id), StringComparison.OrdinalIgnoreCase)) throw new ArgumentException("Only the exact managed observation artifact directory is allowed.");
                for (var parent = new DirectoryInfo(root); parent != null; parent = parent.Parent)
                    if (parent.Exists && (parent.Attributes & FileAttributes.ReparsePoint) != 0) throw new ArgumentException("Managed output cannot traverse links.");
                if (Directory.Exists(output) || File.Exists(output)) throw new IOException("Observation artifact destination already exists.");
                var position = Vector(args["cameraPosition"]); var target = Vector(args["targetPosition"]); var up = Vector(args["upVector"]);
                if ((target - position).sqrMagnitude < .000001f || Vector3.Cross(target - position, up).sqrMagnitude < .000001f) throw new ArgumentException("Camera basis is degenerate.");
                var fov = args.Value<float>("fieldOfView");
                if (float.IsNaN(fov) || fov < 1 || fov > 179) throw new ArgumentException("fieldOfView must be 1–179.");
                job = new Job { Id = id, Manager = manager, Avatar = avatar, Animator = animator, Controllers = controllers,
                    Parameter = parameter, ParameterName = name, Requested = requested, Before = before, Steps = steps, Probes = probes,
                    Duration = duration, Count = count, Width = width, Height = height, Output = output, Identity = CoreIdentity() };
                job.CreateCamera(position, target, up, fov);
                job.ReadStates(); // Reject invalid/empty graph evidence before mutation.
                job.Capture(-1, 0); // Baseline before the one permitted trigger.
                if (Jobs.Count >= 8) { var old = Jobs.FirstOrDefault(pair => pair.Value != active); if (old.Key != null) Jobs.Remove(old.Key); }
                Jobs.Add(id, job); active = job;
                job.StartTime = EditorApplication.timeSinceStartup;
                job.Deadline = job.StartTime + duration + 2;
                job.Started = true;
                GestureManagerRuntimeBridge.SetParameter(manager, parameter, requested);
                if (!GestureManagerRuntimeBridge.TryReadParameter(manager, name, out _, out var triggerReadback, out _) || triggerReadback != requested)
                    throw new InvalidOperationException("GM parameter trigger readback differs from the request.");
                job.Pump.Tick = job.Tick;
                job.Pump.Lost = () => job.Finish("frame_pump_destroyed");
                return VRCForgeToolResult.Completed("Runtime observation started; query the bound job until terminal.", job.Result());
            }
            catch (Exception exception)
            {
                if (job != null) { job.Finish(exception.Message); return VRCForgeToolResult.Completed("Runtime observation stopped.", job.Result()); }
                return VRCForgeToolResult.RejectedBeforeMutation("runtime_observation_rejected", exception.Message, "runtime_observation", "preflight");
            }
        }
        private static Vector3 Vector(JToken token)
        {
            if (!(token is JObject)) throw new ArgumentException("Camera vectors require x/y/z.");
            var values = new[] { "x", "y", "z" }.Select(key => token[key]?.Value<float>() ?? float.NaN).ToArray();
            if (values.Any(value => float.IsNaN(value) || float.IsInfinity(value))) throw new ArgumentException("Camera vectors must be finite.");
            return new Vector3(values[0], values[1], values[2]);
        }
        // Owned by the existing authenticated Avatar observation job; no material instantiation,
        // writes, persistent handles, or independent sampling lifetime.
        internal sealed class RendererProbe
        {
            internal Renderer Renderer;
            internal string Path;
            internal int MaterialIndex, ComponentIndex;
            internal string[] Names;
            internal string[] BlendShapeNames = Array.Empty<string>();
            internal JObject Request;
            internal readonly MaterialPropertyBlock RendererBlock = new MaterialPropertyBlock();
            internal readonly MaterialPropertyBlock IndexedBlock = new MaterialPropertyBlock();
        }
        internal static List<RendererProbe> PrepareRendererProbes(JToken token, GameObject avatar, string avatarPath)
        {
            var result = new List<RendererProbe>();
            if (token == null) return result;
            if (!(token is JArray rows) || rows.Count > 8) throw new ArgumentException("rendererProbes must contain at most 8 probes.");
            foreach (var row in rows)
            {
                var probe = row as JObject;
                if (probe == null || probe.Properties().Any(p => !new[] { "rendererPath", "materialIndex", "rendererComponentIndex", "propertyNames", "blendShapeNames" }.Contains(p.Name))
                    || probe["rendererPath"]?.Type != JTokenType.String || probe["materialIndex"]?.Type != JTokenType.Integer
                    || (probe["rendererComponentIndex"] != null && probe["rendererComponentIndex"].Type != JTokenType.Integer)
                    || !(probe["propertyNames"] is JArray names) || names.Count < 1 || names.Count > 8
                    || names.Any(name => name.Type != JTokenType.String || !System.Text.RegularExpressions.Regex.IsMatch(name.Value<string>(), @"\A[A-Za-z_][A-Za-z0-9_]{0,127}\z"))
                    || names.Values<string>().Distinct(StringComparer.Ordinal).Count() != names.Count)
                    throw new ArgumentException("Invalid rendererProbes fields or property names.");
                var blendShapeNamesToken = probe["blendShapeNames"];
                if (blendShapeNamesToken != null && !(blendShapeNamesToken is JArray))
                    throw new ArgumentException("rendererProbes blendShapeNames must be an array.");
                var blendShapeNames = blendShapeNamesToken as JArray;
                if (blendShapeNames != null && (blendShapeNames.Count < 1 || blendShapeNames.Count > 8
                    || blendShapeNames.Any(name => name.Type != JTokenType.String || string.IsNullOrWhiteSpace(name.Value<string>()) || name.Value<string>().Length > 128)
                    || blendShapeNames.Values<string>().Distinct(StringComparer.Ordinal).Count() != blendShapeNames.Count))
                    throw new ArgumentException("rendererProbes blendShapeNames must be 1–8 distinct non-empty names.");
                var path = probe.Value<string>("rendererPath");
                if (path.Length > 2048 || (path != avatarPath && !path.StartsWith(avatarPath + "/", StringComparison.Ordinal))
                    || path.Split('/').Any(part => part == "" || part == "." || part == ".."))
                    throw new ArgumentException("rendererProbes require exact Avatar descendant paths.");
                var target = avatar.transform;
                if (path != avatarPath)
                    foreach (var part in path.Substring(avatarPath.Length + 1).Split('/'))
                    {
                        var matches = new List<Transform>();
                        for (var i = 0; i < target.childCount; i++) if (target.GetChild(i).name == part) matches.Add(target.GetChild(i));
                        if (matches.Count != 1) throw new ArgumentException("Renderer path is missing or ambiguous: " + path);
                        target = matches[0];
                    }
                var components = target.GetComponents<Renderer>();
                var componentIndex = probe.Value<int?>("rendererComponentIndex") ?? 0;
                if (componentIndex < 0 || componentIndex >= components.Length || (components.Length != 1 && probe["rendererComponentIndex"] == null))
                    throw new ArgumentException("Exact rendererComponentIndex required for missing/ambiguous Renderer: " + path);
                var item = new RendererProbe { Renderer = components[componentIndex], Path = path, ComponentIndex = componentIndex,
                    MaterialIndex = probe.Value<int>("materialIndex"), Names = names.Values<string>().ToArray(),
                    BlendShapeNames = blendShapeNames?.Values<string>().ToArray() ?? Array.Empty<string>(), Request = (JObject)probe.DeepClone() };
                if (item.BlendShapeNames.Length > 0 && !(item.Renderer is SkinnedMeshRenderer))
                    throw new ArgumentException("BlendShape probes require an exact SkinnedMeshRenderer: " + path);
                ReadRendererProbe(item, avatar); // Validate every slot/declaration before the initial parameter write.
                result.Add(item);
            }
            return result;
        }
        internal static string ProbePropertyType(Material material, string name, out string textureBase)
        {
            textureBase = null;
            var shader = material.shader;
            var index = shader.FindPropertyIndex(name);
            if (index >= 0)
            {
                var type = shader.GetPropertyType(index).ToString();
                if (new[] { "Float", "Range", "Vector", "Color", "Texture" }.Contains(type)) return type;
                throw new ArgumentException("Unsupported declared shader property type: " + name + " (" + type + ").");
            }
            // Unity generates texture scale/offset vectors even when not separately declared.
            if (name.EndsWith("_ST", StringComparison.Ordinal))
            {
                var candidate = name.Substring(0, name.Length - 3);
                var textureIndex = shader.FindPropertyIndex(candidate);
                if (textureIndex >= 0 && shader.GetPropertyType(textureIndex) == UnityEngine.Rendering.ShaderPropertyType.Texture)
                { textureBase = candidate; return "Vector"; }
            }
            throw new ArgumentException("Shader property is not declared: " + name);
        }
        internal static JObject ProbeObject(UnityEngine.Object value)
        {
            var path = value ? AssetDatabase.GetAssetPath(value) : "";
            return new JObject { ["name"] = value ? value.name : "", ["instanceId"] = value ? value.GetInstanceID() : 0,
                ["assetPath"] = path, ["assetGuid"] = string.IsNullOrEmpty(path) ? "" : AssetDatabase.AssetPathToGUID(path),
                ["identityKind"] = !value ? "null" : string.IsNullOrEmpty(path) ? "runtime_no_asset" : "asset" };
        }
        internal static JObject ReadProbeProperty(Material material, MaterialPropertyBlock block, string blockSource, string name)
        {
            var type = ProbePropertyType(material, name, out var textureBase);
            var id = Shader.PropertyToID(name);
            var overridden = type == "Float" || type == "Range" ? block.HasFloat(id)
                : type == "Texture" ? block.HasTexture(id) : block.HasVector(id);
            JToken value;
            if (type == "Float" || type == "Range") value = overridden ? block.GetFloat(id) : material.GetFloat(id);
            else if (type == "Texture") value = ProbeObject(overridden ? block.GetTexture(id) : material.GetTexture(id));
            else
            {
                Vector4 vector;
                if (overridden) vector = block.GetVector(id);
                else if (textureBase != null)
                {
                    var scale = material.GetTextureScale(textureBase); var offset = material.GetTextureOffset(textureBase);
                    vector = new Vector4(scale.x, scale.y, offset.x, offset.y);
                }
                else vector = type == "Color" ? (Vector4)material.GetColor(id) : material.GetVector(id);
                value = new JArray(vector.x, vector.y, vector.z, vector.w);
            }
            return new JObject { ["name"] = name, ["type"] = type, ["value"] = value,
                ["valueSource"] = overridden ? blockSource : textureBase != null ? "material_texture_scale_offset" : "material" };
        }
        internal static JObject ReadRendererProbe(RendererProbe probe, GameObject avatar)
        {
            var renderer = probe.Renderer;
            if (!renderer || !renderer.transform.IsChildOf(avatar.transform)) throw new InvalidOperationException("Observed Renderer left the bound Avatar.");
            var pathNames = new Stack<string>();
            for (var current = renderer.transform; current != null; current = current.parent) pathNames.Push(current.name);
            if (string.Join("/", pathNames) != probe.Path) throw new InvalidOperationException("Observed Renderer path changed.");
            var components = renderer.GetComponents<Renderer>();
            if (probe.ComponentIndex >= components.Length || components[probe.ComponentIndex] != renderer) throw new InvalidOperationException("Observed Renderer component identity changed.");
            var materials = renderer.sharedMaterials;
            if (probe.MaterialIndex < 0 || probe.MaterialIndex >= materials.Length || !materials[probe.MaterialIndex] || !materials[probe.MaterialIndex].shader)
                throw new InvalidOperationException("Observed material slot or shader missing: " + probe.Path);
            var material = materials[probe.MaterialIndex]; var shader = material.shader;
            renderer.GetPropertyBlock(probe.RendererBlock);
            renderer.GetPropertyBlock(probe.IndexedBlock, probe.MaterialIndex);
            var indexed = !probe.IndexedBlock.isEmpty;
            var properties = ReadProbeProperties(material, probe.RendererBlock, probe.IndexedBlock, probe.Names);
            var blendShapes = ReadRendererBlendShapes(renderer, probe.Path, probe.BlendShapeNames);
            var keywords = material.shaderKeywords;
            var passes = new JArray();
            for (int i = 0; i < Math.Min(material.passCount, 32); i++)
            {
                var name = material.GetPassName(i);
                passes.Add(new JObject { ["index"] = i, ["name"] = name, ["enabled"] = material.GetShaderPassEnabled(name) });
            }
            var messages = ShaderUtil.GetShaderMessages(shader);
            var shaderInfo = ProbeObject(shader);
            shaderInfo["isSupported"] = shader.isSupported;
            shaderInfo["messages"] = new JArray(messages.Take(8).Select(message => new JObject {
                ["severity"] = message.severity.ToString(), ["message"] = message.message.Length > 1024 ? message.message.Substring(0, 1024) : message.message,
                ["messageTruncated"] = message.message.Length > 1024, ["line"] = message.line }));
            shaderInfo["messageCount"] = messages.Length; shaderInfo["messagesTruncated"] = messages.Length > 8;
            var result = new JObject { ["rendererPath"] = probe.Path, ["rendererComponentIndex"] = probe.ComponentIndex, ["rendererInstanceId"] = renderer.GetInstanceID(),
                ["rendererEnabled"] = renderer.enabled, ["activeInHierarchy"] = renderer.gameObject.activeInHierarchy, ["materialIndex"] = probe.MaterialIndex,
                ["sharedMaterial"] = ProbeObject(material), ["shader"] = shaderInfo, ["keywords"] = new JArray(keywords.Take(128)),
                ["keywordCount"] = keywords.Length, ["keywordsTruncated"] = keywords.Length > 128,
                ["passes"] = passes, ["passCount"] = material.passCount, ["passesTruncated"] = material.passCount > 32,
                ["rendererBlockEmpty"] = probe.RendererBlock.isEmpty, ["materialBlockEmpty"] = probe.IndexedBlock.isEmpty,
                ["selectedBlock"] = indexed ? "material_property_block" : "renderer_property_block", ["properties"] = properties };
            if (blendShapes != null) result["blendShapes"] = blendShapes;
            return result;
        }
        internal static JArray ReadRendererBlendShapes(Renderer renderer, string path, string[] names)
        {
            if (names == null || names.Length == 0) return null;
            var skinned = renderer as SkinnedMeshRenderer;
            if (skinned == null) throw new ArgumentException("BlendShape probes require an exact SkinnedMeshRenderer: " + path);
            var mesh = skinned.sharedMesh;
            if (mesh == null) throw new InvalidOperationException("Observed SkinnedMeshRenderer has no shared mesh: " + path);
            var result = new JArray();
            foreach (var name in names)
            {
                var index = mesh.GetBlendShapeIndex(name);
                if (index < 0) throw new ArgumentException("BlendShape is missing on renderer: " + path + " / " + name);
                var weight = skinned.GetBlendShapeWeight(index);
                if (float.IsNaN(weight) || float.IsInfinity(weight)) throw new InvalidOperationException("Observed BlendShape weight is not finite: " + path + " / " + name);
                result.Add(new JObject { ["name"] = name, ["index"] = index, ["weight"] = weight });
            }
            return result;
        }
        internal static JArray ReadProbeProperties(Material material, MaterialPropertyBlock rendererBlock, MaterialPropertyBlock indexedBlock, string[] names)
        {
            // Unity 2022.3 Renderer.SetPropertyBlock: only the indexed block is used when both exist.
            // Whole-block precedence: an absent indexed property falls to material, not renderer block.
            var indexed = !indexedBlock.isEmpty;
            var block = indexed ? indexedBlock : rendererBlock;
            return new JArray(names.Select(name => ReadProbeProperty(material, block, indexed ? "material_property_block" : "renderer_property_block", name)));
        }
        private static bool ValidStepValue(string type, float value)
        {
            if (float.IsNaN(value) || float.IsInfinity(value)) return false;
            if (string.Equals(type, "Bool", StringComparison.OrdinalIgnoreCase) || string.Equals(type, "Boolean", StringComparison.OrdinalIgnoreCase)) return value == 0f || value == 1f;
            if (string.Equals(type, "Int", StringComparison.OrdinalIgnoreCase) || string.Equals(type, "Integer", StringComparison.OrdinalIgnoreCase)) return value == Mathf.Round(value);
            return string.Equals(type, "Float", StringComparison.OrdinalIgnoreCase) || string.Equals(type, "Single", StringComparison.OrdinalIgnoreCase);
        }
        internal sealed class Job
        {
            internal string Id, Output, Identity, ParameterName, Error = "";
            internal GestureManagerRuntimeBridge.ManagerBinding Manager;
            internal GameObject Avatar, CameraObject;
            internal Animator Animator;
            internal List<AnimatorControllerPlayable> Controllers;
            internal object Parameter;
            internal float Requested;
            internal object Before;
            internal List<ParameterStep> Steps = new List<ParameterStep>();
            internal List<RendererProbe> Probes = new List<RendererProbe>();
            internal int NextStep;
            internal readonly JArray StepReceipts = new JArray();
            internal double Duration, StartTime, Deadline;
            internal int Count, Width, Height, Next, LastFrame = -1;
            internal bool Started, Done;
            internal Camera Camera;
            internal RenderTexture Target;
            internal RuntimeObservationFramePump Pump;
            internal readonly List<Texture2D> Pixels = new List<Texture2D>();
            internal readonly JArray Frames = new JArray();
            internal void CreateCamera(Vector3 position, Vector3 target, Vector3 up, float fov)
            {
                CameraObject = new GameObject("VRCForge_RuntimeObservation") { hideFlags = HideFlags.HideAndDontSave };
                Camera = CameraObject.AddComponent<Camera>(); Camera.enabled = false;
                Camera.transform.SetPositionAndRotation(position, Quaternion.LookRotation(target - position, up));
                Camera.fieldOfView = fov; Camera.aspect = Width / (float)Height; Camera.nearClipPlane = .01f; Camera.farClipPlane = 1000;
                Camera.cullingMask = ~0;
                Target = new RenderTexture(Width, Height, 24) { hideFlags = HideFlags.HideAndDontSave };
                Target.Create(); Camera.targetTexture = Target;
                Pump = CameraObject.AddComponent<RuntimeObservationFramePump>();
            }
            internal JArray ReadStates()
            {
                var result = new JArray();
                for (int c = 0; c < Controllers.Count; c++)
                {
                    var controller = Controllers[c];
                    if (!controller.IsValid()) throw new InvalidOperationException("GM playable controller was destroyed.");
                    for (int i = 0; i < controller.GetLayerCount(); i++)
                    {
                        var state = controller.GetCurrentAnimatorStateInfo(i);
                        var next = controller.GetNextAnimatorStateInfo(i);
                        result.Add(new JObject { ["controllerIndex"] = c, ["layerIndex"] = i,
                            ["layerName"] = controller.GetLayerName(i), ["layerWeight"] = controller.GetLayerWeight(i),
                            ["currentClips"] = ClipSnapshot(controller.GetCurrentAnimatorClipInfo(i)),
                            ["nextClips"] = ClipSnapshot(controller.GetNextAnimatorClipInfo(i)), ["stateHash"] = state.fullPathHash,
                            ["normalizedTime"] = state.normalizedTime, ["inTransition"] = controller.IsInTransition(i), ["nextStateHash"] = next.fullPathHash });
                    }
                }
                if (result.Count == 0 || !result.OfType<JObject>().Any(row => row.Value<int>("stateHash") != 0)) throw new InvalidOperationException("Animator state evidence is empty or contains no active state.");
                return result;
            }
            internal void AssertIdentity()
            {
                if (!EditorApplication.isPlaying || !Avatar || !Animator || !Manager.Behaviour || !Manager.Behaviour.isActiveAndEnabled
                    || !ReferenceEquals(PublicMember(Manager.Behaviour, "Module"), Manager.Module)
                    || PublicMember(Manager.Module, "Avatar") as GameObject != Avatar
                    || PublicMember(Manager.Module, "AvatarAnimator") as Animator != Animator || CoreIdentity() != Identity)
                    throw new InvalidOperationException("Observation target, Core, or Play Mode changed.");
                var current = RuntimeObservation.Controllers(Animator);
                if (!current.SequenceEqual(Controllers)) throw new InvalidOperationException("The target playable graph changed.");
            }
            internal void Capture(int index, double elapsed)
            {
                AssertIdentity();
                if (!GestureManagerRuntimeBridge.TryReadParameter(Manager, ParameterName, out _, out var value, out var type)) throw new InvalidOperationException("GM parameter disappeared.");
                var states = ReadStates();
                var probes = Probes.Count == 0 ? null : new JArray(Probes.Select(probe => ReadRendererProbe(probe, Avatar)));
                var previous = RenderTexture.active;
                Texture2D texture = null;
                try
                {
                    Camera.Render(); RenderTexture.active = Target;
                    texture = new Texture2D(Width, Height, TextureFormat.RGB24, false);
                    texture.ReadPixels(new Rect(0, 0, Width, Height), 0, 0); texture.Apply();
                    Pixels.Add(texture); texture = null;
                    Frames.Add(new JObject { ["sampleIndex"] = index, ["requestedElapsedSeconds"] = index < 0 ? -1 : index * Duration / (Count - 1),
                        ["actualElapsedSeconds"] = elapsed, ["unityFrame"] = Time.frameCount, ["parameterValue"] = JToken.FromObject(value),
                        ["parameterType"] = type, ["states"] = states });
                    if (probes != null) Frames.Last["rendererProbes"] = probes;
                }
                finally { RenderTexture.active = previous; if (texture) UnityEngine.Object.DestroyImmediate(texture); }
            }
            internal void Tick()
            {
                if (Done || !Started || LastFrame == Time.frameCount) return;
                try
                {
                    AssertIdentity(); LastFrame = Time.frameCount;
                    var elapsed = EditorApplication.timeSinceStartup - StartTime;
                    while (NextStep < Steps.Count && elapsed >= Steps[NextStep].TimeSeconds)
                    {
                        var step = Steps[NextStep];
                        var observedBefore = false; var observedAfter = false; float beforeValue = 0f, afterValue = 0f; var observedType = "";
                        try
                        {
                            if (!GestureManagerRuntimeBridge.TryReadParameter(Manager, step.Name, out var currentParameter, out var before, out var type) || !ReferenceEquals(currentParameter, step.Parameter) || type != step.Type)
                                throw new InvalidOperationException("Runtime observation step parameter identity changed: " + step.Name);
                            beforeValue = before; observedBefore = true; observedType = type;
                            GestureManagerRuntimeBridge.SetParameter(Manager, step.Parameter, step.Value);
                            var readbackAvailable = GestureManagerRuntimeBridge.TryReadParameter(Manager, step.Name, out var afterParameter, out var after, out var afterType);
                            if (readbackAvailable) { afterValue = after; observedAfter = true; observedType = afterType; }
                            if (!readbackAvailable || !ReferenceEquals(afterParameter, step.Parameter) || afterType != step.Type || after != step.Value)
                                throw new InvalidOperationException("Runtime observation step readback differs: " + step.Name);
                            StepReceipts.Add(new JObject { ["timeSeconds"] = step.TimeSeconds, ["parameterName"] = step.Name,
                                ["requestedValue"] = step.Value, ["actualElapsedSeconds"] = elapsed, ["unityFrame"] = Time.frameCount,
                                ["beforeValue"] = before, ["afterValue"] = after, ["parameterType"] = afterType, ["status"] = "applied" });
                            NextStep++;
                        }
                        catch (Exception stepException)
                        {
                            var failedReceipt = new JObject { ["timeSeconds"] = step.TimeSeconds, ["parameterName"] = step.Name,
                                ["requestedValue"] = step.Value, ["actualElapsedSeconds"] = elapsed, ["unityFrame"] = Time.frameCount,
                                ["status"] = "failed", ["error"] = stepException.Message };
                            if (observedBefore) failedReceipt["beforeValue"] = beforeValue;
                            if (observedAfter) failedReceipt["afterValue"] = afterValue;
                            if (!string.IsNullOrEmpty(observedType)) failedReceipt["parameterType"] = observedType;
                            StepReceipts.Add(failedReceipt);
                            for (var remaining = NextStep + 1; remaining < Steps.Count; remaining++) StepReceipts.Add(new JObject { ["timeSeconds"] = Steps[remaining].TimeSeconds, ["parameterName"] = Steps[remaining].Name, ["requestedValue"] = Steps[remaining].Value, ["status"] = "not_applied" });
                            NextStep = Steps.Count;
                            throw;
                        }
                    }
                    var due = DueIndex(elapsed, Duration, Count);
                    if (due < Next) return;
                    Capture(due, elapsed); Next = due + 1;
                    if (elapsed >= Duration || Next >= Count) Finish("");
                }
                catch (Exception exception) { Finish(exception.Message); }
            }
            internal void Cleanup(Action action)
            {
                try { action(); }
                catch (Exception exception) { Error += "; cleanup: " + exception.Message; }
            }
            internal void Finish(string error)
            {
                if (Done) return;
                for (var remaining = NextStep; remaining < Steps.Count; remaining++)
                    StepReceipts.Add(new JObject { ["timeSeconds"] = Steps[remaining].TimeSeconds, ["parameterName"] = Steps[remaining].Name,
                        ["requestedValue"] = Steps[remaining].Value, ["status"] = "not_applied" });
                NextStep = Steps.Count;
                Done = true; Error = error ?? "";
                if (Pump) { Pump.Tick = null; Pump.Lost = null; }
                try
                {
                    if (Started)
                    {
                        if (Directory.Exists(Output) || File.Exists(Output)) throw new IOException("Artifact directory was created by another operation.");
                        Directory.CreateDirectory(Output);
                        long bytesWritten = 0;
                        for (int i = 0; i < Pixels.Count; i++)
                        {
                            var bytes = Pixels[i].EncodeToPNG(); bytesWritten += bytes.Length;
                            if (bytesWritten > 32 * 1024 * 1024) throw new IOException("Encoded observation artifacts exceed 32 MiB.");
                            var path = Path.Combine(Output, i.ToString("D3") + ".png");
                            using (var stream = new FileStream(path, FileMode.CreateNew, FileAccess.Write, FileShare.None)) { stream.Write(bytes, 0, bytes.Length); stream.Flush(true); }
                            var saved = File.ReadAllBytes(path);
                            if (Digest(saved) != Digest(bytes)) throw new IOException("Artifact persisted readback mismatch.");
                            Frames[i]["imagePath"] = path; Frames[i]["sha256"] = Digest(saved);
                        }
                    }
                }
                catch (Exception exception) { Error = string.IsNullOrEmpty(Error) ? exception.Message : Error + "; " + exception.Message; }
                finally
                {
                    foreach (var pixel in Pixels) if (pixel) Cleanup(() => UnityEngine.Object.DestroyImmediate(pixel));
                    Pixels.Clear();
                    if (Camera) Cleanup(() => Camera.targetTexture = null);
                    if (Target) { Cleanup(() => Target.Release()); Cleanup(() => UnityEngine.Object.DestroyImmediate(Target)); }
                    if (CameraObject) Cleanup(() => UnityEngine.Object.DestroyImmediate(CameraObject));
                    if (active == this) active = null;
                }
            }
            internal JObject Result()
            {
                var sampled = Frames.OfType<JObject>().Count(frame => frame.Value<int>("sampleIndex") >= 0);
                var complete = Done && string.IsNullOrEmpty(Error) && Started;
                var elapsed = Frames.OfType<JObject>().Where(frame => frame.Value<int>("sampleIndex") >= 0).Select(frame => frame.Value<double>("actualElapsedSeconds")).ToArray();
                var gaps = elapsed.Skip(1).Select((value, index) => value - elapsed[index]).ToArray();
                var result = new JObject { ["schema"] = "vrcforge.runtime_observation.v1", ["jobId"] = Id, ["status"] = !Done ? "pending" : complete ? "completed" : "partial",
                    ["avatarPath"] = Manager.AvatarPath, ["managerPath"] = Manager.ManagerPath, ["avatarInstanceId"] = Avatar ? Avatar.GetInstanceID() : 0,
                    ["animatorInstanceId"] = Animator ? Animator.GetInstanceID() : 0, ["coreIdentity"] = Identity,
                    ["parameterName"] = ParameterName, ["requestedValue"] = Requested, ["beforeValue"] = JToken.FromObject(Before),
                    ["durationSeconds"] = Duration, ["width"] = Width, ["height"] = Height, ["requestedFrameCount"] = Count, ["sampledFrameCount"] = sampled,
                    ["undersampled"] = sampled < Count,
                    ["coverageStatus"] = sampled < Count ? "undersampled" : "requested_samples_observed",
                    ["actualCadence"] = new JObject { ["maxGapSeconds"] = gaps.Length > 0 ? gaps.Max() : (JToken)JValue.CreateNull(),
                        ["actualSpanSeconds"] = elapsed.Length > 0 ? elapsed.Last() : (JToken)JValue.CreateNull() },
                    ["frames"] = Frames.DeepClone(), ["error"] = Error,
                    ["parameterSteps"] = new JArray(Steps.Select(step => new JObject { ["timeSeconds"] = step.TimeSeconds, ["parameterName"] = step.Name, ["value"] = step.Value })),
                    ["stepReceipts"] = StepReceipts.DeepClone(),
                    ["mutationStarted"] = Started, ["committed"] = Done ? (JToken)complete : JValue.CreateNull(),
                    ["verified"] = complete, ["persistent"] = false, ["commitState"] = !Started ? "not_started" : !Done ? "pending" : complete ? "runtime_observed" : "partial" };
                if (Probes.Count > 0) result["rendererProbes"] = new JArray(Probes.Select(probe => probe.Request.DeepClone()));
                return result;
            }
        }
        internal sealed class ParameterStep { internal double TimeSeconds; internal string Name, Type; internal float Value; internal object Parameter; }
    }
    [VRCForgeCommand(toolId: "vrc_start_runtime_observation", Summary = "Start one finite GM parameter observation with real frame artifacts.", UsesContinuation = true, ContinuationAction = "vrc_get_runtime_observation", ContinuationTimeoutSeconds = 12)]
    public static class RuntimeObservationStartTool
    {
        public class Parameters
        {
            [VRCForgeInput("Exact connected Avatar path.")] public string avatarPath { get; set; }
            [VRCForgeInput("Exact existing GM parameter.")] public string parameterName { get; set; }
            [VRCForgeInput("Finite requested value.")] public float value { get; set; }
            [VRCForgeInput("0.1–10 seconds.")] public double durationSeconds { get; set; }
            [VRCForgeInput("2–32 requested frames.")] public int frameCount { get; set; }
            [VRCForgeInput("128–512 pixels.")] public int width { get; set; }
            [VRCForgeInput("128–512 pixels.")] public int height { get; set; }
            [VRCForgeInput("Explicit camera position.")] public JObject cameraPosition { get; set; }
            [VRCForgeInput("Explicit look target.")] public JObject targetPosition { get; set; }
            [VRCForgeInput("Explicit camera up.")] public JObject upVector { get; set; }
            [VRCForgeInput("Perspective FOV 1–179.")] public float fieldOfView { get; set; }
            [VRCForgeInput("Optional ordered parameter steps; each has timeSeconds, parameterName, and value (maximum 64).", IsRequired = false)] public JArray parameterSteps { get; set; }
            [VRCForgeInput("Optional maximum 8 exact Avatar rendererPath/materialIndex probes, each with 1–8 propertyNames and optional rendererComponentIndex; captures shared material and effective property blocks without instantiation.", IsRequired = false)] public JArray rendererProbes { get; set; }
            [VRCForgeInput("App-owned unique job ID.")] public string jobId { get; set; }
            [VRCForgeInput("App-owned create-new artifact directory.")] public string outputDirectory { get; set; }
        }
        public static object HandleCommand(JObject args) { return RuntimeObservation.Start(args); }
    }
    [VRCForgeCommand(toolId: "vrc_get_runtime_observation", Summary = "Read one existing runtime observation job without triggering a parameter write.", Access = VRCForgeCommandAccess.ReadOnly)]
    public static class RuntimeObservationStatusTool
    {
        public class Parameters
        {
            [VRCForgeInput("Exact observation job ID.")] public string jobId { get; set; }
            [VRCForgeInput("Exact observation Avatar.")] public string avatarPath { get; set; }
        }
        public static object HandleCommand(JObject args)
        {
            var id = args.Value<string>("jobId") ?? "";
            if (!RuntimeObservation.Jobs.TryGetValue(id, out var job)) return VRCForgeToolResult.Failed("Observation job unavailable in this Editor lifetime.");
            if (args.Value<string>("avatarPath") != job.Manager.AvatarPath) return VRCForgeToolResult.Failed("Observation Avatar identity mismatch.");
            return VRCForgeToolResult.Completed("Observation status read.", job.Result());
        }
    }
}
