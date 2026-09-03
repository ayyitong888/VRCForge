using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Security.Cryptography;
using System.Text;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEngine;
using UnityEngine.SceneManagement;
using VRCForge.Core.MCP;

namespace VRCForge.Editor
{
    [VRCForgeCommand(toolId: "vrc_get_execution_targets", Summary = "Read-only: discover current Unity project, Core, scene, Avatar, object, and component identities for a fail-closed ExecutionTarget.", Access = VRCForgeCommandAccess.ReadOnly)]
    public static class ExecutionTargetInspector
    {
        public const string ToolName = "vrc_get_execution_targets";
        private const string Schema = "vrcforge.execution_target.v1";
        private const string AvatarDescriptorTypeName = "VRC.SDK3.Avatars.Components.VRCAvatarDescriptor";

        public sealed class Parameters
        {
            [VRCForgeInput("Identity scope: project, scene, avatar, object, or component.", IsRequired = false, DefaultLiteral = "project")] public string scope { get; set; } = "project";
            [VRCForgeInput("Exact Avatar GlobalObjectId, when already known.", IsRequired = false)] public string avatarGlobalObjectId { get; set; }
            [VRCForgeInput("Exact object GlobalObjectId, when already known.", IsRequired = false)] public string objectGlobalObjectId { get; set; }
            [VRCForgeInput("Exact component GlobalObjectId, when already known.", IsRequired = false)] public string componentGlobalObjectId { get; set; }
            [VRCForgeInput("Expected fully qualified component type, when already known.", IsRequired = false)] public string componentType { get; set; }
        }

        public static object HandleCommand(JObject @params)
        {
            try
            {
                var parameters = (@params ?? new JObject()).ToObject<Parameters>() ?? new Parameters();
                var scope = NormalizeScope(parameters.scope);
                var root = GetProjectRoot();
                var project = new JObject { ["root"] = root, ["projectId"] = ComputeProjectId(root) };
                var editor = new JObject { ["unityPid"] = System.Diagnostics.Process.GetCurrentProcess().Id, ["processStartTime"] = CurrentProcessStartTime(), ["coreInstanceId"] = GetCoreInstanceId() };
                if (scope == "project") return VRCForgeToolResult.Completed("Execution target discovered.", BuildTarget(scope, project, editor, null, null, null, null));
                var scene = BuildScene(SceneManager.GetActiveScene());
                if (scene == null) return VRCForgeToolResult.FailedWithCode("execution_target_scene_unavailable", "The current Unity scene has no saved scene identity.", BuildDiscovery(project, editor, scope, null));
                if (scope == "scene") return VRCForgeToolResult.Completed("Execution target discovered.", BuildTarget(scope, project, editor, scene, null, null, null));
                var avatars = DiscoverAvatars(scene.Value<string>("assetPath"));
                var avatar = ResolveAvatar(avatars, parameters.avatarGlobalObjectId, parameters.objectGlobalObjectId, parameters.componentGlobalObjectId);
                if (avatar == null)
                {
                    if (!string.IsNullOrWhiteSpace(parameters.avatarGlobalObjectId))
                        return VRCForgeToolResult.FailedWithCode("execution_target_avatar_unavailable", "The exact Avatar GlobalObjectId is not present in the current scene.", BuildDiscovery(project, editor, scope, null));
                    return VRCForgeToolResult.Completed("Avatar candidates discovered.", BuildDiscovery(project, editor, scope, avatars.Select(item => BuildTarget(scope, project, editor, scene, item, null, null)).ToList()));
                }
                if (scope == "avatar") return VRCForgeToolResult.Completed("Execution target discovered.", BuildTarget(scope, project, editor, scene, avatar, null, null));
                var targetObject = ResolveObject(parameters.objectGlobalObjectId, avatar);
                if (targetObject == null) return VRCForgeToolResult.FailedWithCode("execution_target_object_unavailable", "An exact object GlobalObjectId under the Avatar is required.", BuildDiscovery(project, editor, scope, null));
                if (scope == "object") return VRCForgeToolResult.Completed("Execution target discovered.", BuildTarget(scope, project, editor, scene, avatar, DescribeGameObject(targetObject), null));
                var component = ResolveComponent(parameters.componentGlobalObjectId, parameters.componentType, targetObject);
                if (component == null) return VRCForgeToolResult.FailedWithCode("execution_target_component_unavailable", "An exact component GlobalObjectId on the object is required.", BuildDiscovery(project, editor, scope, null));
                return VRCForgeToolResult.Completed("Execution target discovered.", BuildTarget(scope, project, editor, scene, avatar, DescribeGameObject(targetObject), DescribeComponent(component)));
            }
            catch (Exception exception) { return VRCForgeToolResult.FailedWithCode("execution_target_discovery_failed", "Execution target discovery failed: " + exception.Message); }
        }

        private static JObject BuildDiscovery(JObject project, JObject editor, string scope, IList<JObject> candidates)
        {
            return new JObject { ["schema"] = Schema, ["scope"] = scope, ["project"] = project, ["editor"] = editor, ["ambiguous"] = candidates != null && candidates.Count != 1, ["resolutionCandidateCount"] = candidates == null ? 0 : candidates.Count, ["targets"] = candidates == null ? new JArray() : new JArray(candidates) };
        }

        private static JObject BuildTarget(string scope, JObject project, JObject editor, JObject scene, JObject avatar, JObject targetObject, JObject component)
        {
            var result = BuildDiscovery(project, editor, scope, new List<JObject>()) as JObject;
            result["ambiguous"] = false; result["resolutionCandidateCount"] = 1; result["scene"] = scene == null ? JValue.CreateNull() : scene;
            if (avatar != null) result["avatar"] = avatar; if (targetObject != null) result["object"] = targetObject; if (component != null) result["component"] = component;
            result["namespace"] = BuildNamespace(result); result.Remove("targets"); return result;
        }

        private static string BuildNamespace(JObject target)
        {
            var value = "vrcforge://projects/" + target["project"]["projectId"];
            var scene = target["scene"] as JObject; var avatar = target["avatar"] as JObject; var targetObject = target["object"] as JObject; var component = target["component"] as JObject;
            if (scene != null) value += "/scenes/" + scene["guid"]; if (avatar != null) value += "/avatars/" + avatar["globalObjectId"]; if (targetObject != null) value += "/objects/" + targetObject["globalObjectId"]; if (component != null) value += "/components/" + component["globalObjectId"]; return value;
        }

        private static JObject BuildScene(Scene scene)
        {
            if (!scene.IsValid() || !scene.isLoaded || string.IsNullOrEmpty(scene.path)) return null;
            var assetPath = scene.path.Replace('\\', '/'); var absolute = Path.GetFullPath(Path.Combine(GetProjectRoot(), assetPath.Replace('/', Path.DirectorySeparatorChar)));
            if (!File.Exists(absolute)) return null;
            return new JObject { ["assetPath"] = assetPath, ["absolutePath"] = absolute, ["guid"] = ReadUnityMetaGuid(absolute + ".meta"), ["revision"] = File.GetLastWriteTimeUtc(absolute).Ticks.ToString(CultureInfo.InvariantCulture), ["digest"] = ComputeFileSha256(absolute) };
        }

        private static List<JObject> DiscoverAvatars(string assetPath)
        {
            var result = new List<JObject>(); var descriptorType = FindType(AvatarDescriptorTypeName); if (descriptorType == null) return result;
            foreach (var descriptor in Resources.FindObjectsOfTypeAll(descriptorType).OfType<Component>())
            {
                if (descriptor == null || descriptor.gameObject == null || !descriptor.gameObject.scene.IsValid() || !descriptor.gameObject.scene.isLoaded || EditorUtility.IsPersistent(descriptor) || descriptor.gameObject.scene.path.Replace('\\', '/') != assetPath) continue;
                var id = GlobalObjectId.GetGlobalObjectIdSlow(descriptor.gameObject).ToString(); if (string.IsNullOrEmpty(id) || result.Any(item => item.Value<string>("globalObjectId") == id)) continue; result.Add(DescribeGameObject(descriptor.gameObject));
            }
            result.Sort((left, right) => string.CompareOrdinal(left.Value<string>("exactHierarchyPath"), right.Value<string>("exactHierarchyPath"))); return result;
        }

        private static JObject ResolveAvatar(List<JObject> candidates, string requestedId, string objectId, string componentId)
        {
            if (!string.IsNullOrWhiteSpace(requestedId)) return candidates.FirstOrDefault(item => item.Value<string>("globalObjectId") == requestedId);
            var exactObject = !string.IsNullOrWhiteSpace(objectId) ? ResolveGameObject(objectId) : null;
            if (exactObject == null && !string.IsNullOrWhiteSpace(componentId))
            {
                GlobalObjectId parsed;
                if (GlobalObjectId.TryParse(componentId, out parsed)) exactObject = (GlobalObjectIdentifierToObject(parsed) as Component)?.gameObject;
            }
            var exactRoot = exactObject == null ? null : FindAvatarRoot(exactObject.transform);
            if (exactRoot != null) return candidates.FirstOrDefault(item => item.Value<string>("globalObjectId") == GlobalObjectId.GetGlobalObjectIdSlow(exactRoot).ToString());
            return candidates.Count == 1 ? candidates[0] : null;
        }

        private static GameObject FindAvatarRoot(Transform value)
        {
            var type = FindType(AvatarDescriptorTypeName); for (var current = value; current != null; current = current.parent) if (type != null && current.GetComponent(type) != null) return current.gameObject; return null;
        }

        private static GameObject ResolveObject(string id, JObject avatar)
        {
            var value = string.IsNullOrWhiteSpace(id) ? null : ResolveGameObject(id); var root = ResolveGameObject(avatar.Value<string>("globalObjectId")); return value != null && root != null && (value == root || value.transform.IsChildOf(root.transform)) ? value : null;
        }

        private static Component ResolveComponent(string id, string expectedType, GameObject targetObject)
        {
            GlobalObjectId parsed; if (string.IsNullOrWhiteSpace(id) || !GlobalObjectId.TryParse(id, out parsed)) return null; var value = GlobalObjectId.GlobalObjectIdentifierToObjectSlow(parsed) as Component; return value != null && value.gameObject == targetObject && (string.IsNullOrWhiteSpace(expectedType) || string.Equals(value.GetType().FullName, expectedType, StringComparison.Ordinal)) ? value : null;
        }

        private static JObject DescribeGameObject(GameObject value) { return new JObject { ["globalObjectId"] = GlobalObjectId.GetGlobalObjectIdSlow(value).ToString(), ["exactHierarchyPath"] = HierarchyPath(value.transform), ["name"] = value.name }; }
        private static JObject DescribeComponent(Component value) { return new JObject { ["globalObjectId"] = GlobalObjectId.GetGlobalObjectIdSlow(value).ToString(), ["type"] = value.GetType().FullName, ["exactHierarchyPath"] = HierarchyPath(value.transform) }; }
        private static GameObject ResolveGameObject(string id) { GlobalObjectId parsed; return GlobalObjectId.TryParse(id, out parsed) ? GlobalObjectId.GlobalObjectIdentifierToObjectSlow(parsed) as GameObject : null; }
        private static UnityEngine.Object GlobalObjectIdentifierToObject(GlobalObjectId id) { return GlobalObjectId.GlobalObjectIdentifierToObjectSlow(id); }
        private static string HierarchyPath(Transform value) { var parts = new List<string>(); for (var current = value; current != null; current = current.parent) parts.Add(current.name); parts.Reverse(); return string.Join("/", parts.ToArray()); }
        private static Type FindType(string name) { return AppDomain.CurrentDomain.GetAssemblies().Select(item => item.GetType(name, false)).FirstOrDefault(item => item != null); }
        private static string GetProjectRoot() { return Path.GetFullPath(Directory.GetParent(Application.dataPath).FullName).TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar); }
        private static string GetCoreInstanceId() { return VRCForgeMcpCoreServer.CurrentInstanceId; }
        private static string CurrentProcessStartTime() { const long epoch = 621355968000000000L; var ticks = System.Diagnostics.Process.GetCurrentProcess().StartTime.ToUniversalTime().Ticks - epoch; return ((double)ticks / TimeSpan.TicksPerSecond).ToString("F6", CultureInfo.InvariantCulture); }
        private static string ComputeProjectId(string root) { using (var sha = SHA256.Create()) return Hex(sha.ComputeHash(Encoding.UTF8.GetBytes(root))); }
        private static string ComputeFileSha256(string path) { using (var sha = SHA256.Create()) using (var stream = File.OpenRead(path)) return Hex(sha.ComputeHash(stream)); }
        private static string Hex(byte[] bytes) { var builder = new StringBuilder(bytes.Length * 2); foreach (var value in bytes) builder.Append(value.ToString("x2", CultureInfo.InvariantCulture)); return builder.ToString(); }
        private static string ReadUnityMetaGuid(string path) { if (!File.Exists(path)) return string.Empty; foreach (var line in File.ReadLines(path)) if (line.StartsWith("guid:", StringComparison.Ordinal)) return line.Substring(5).Trim(); return string.Empty; }
        private static string NormalizeScope(string value) { var scope = (value ?? "project").Trim().ToLowerInvariant(); if (scope != "project" && scope != "scene" && scope != "avatar" && scope != "object" && scope != "component") throw new ArgumentException("scope must be project, scene, avatar, object, or component."); return scope; }
    }
}
