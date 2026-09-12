using System;
using System.Linq;
using System.Reflection;
using System.Text.RegularExpressions;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEngine;
using VRCForge.Core.MCP;

namespace VRCForge.Editor
{
    // Owns no persistent resources: all reads complete synchronously on the Core
    // main-thread call. Gateway verifies the namespace; Core rechecks exact IDs.
    internal static class UnityComponentPropertyBatch
    {
        internal static JObject CreateInputSchema(JObject scalarSchema)
        {
            var schema = (JObject)scalarSchema.DeepClone();
            var required = (JArray)schema["required"]?.DeepClone() ?? new JArray("gameObjectPath", "componentType", "propertyPath");
            schema.Remove("required");
            var scalarProperties = ((JObject)schema["properties"]).Properties().Where(p => p.Name != "queries").Select(p => p.Name).ToArray();
            var fields = new JObject();
            foreach (var name in new[] { "gameObjectPath", "componentType", "objectGlobalObjectId", "componentGlobalObjectId", "scenePath", "sceneGuid", "avatarGlobalObjectId", "avatarPath" })
                fields[name] = new JObject { ["type"] = "string", ["minLength"] = 1 };
            fields["componentIndex"] = new JObject { ["type"] = "integer", ["minimum"] = 0 };
            fields["propertyNames"] = new JObject
            {
                ["type"] = "array", ["minItems"] = 1, ["maxItems"] = 16, ["uniqueItems"] = true,
                ["items"] = new JObject { ["type"] = "string", ["pattern"] = "^[A-Za-z_][A-Za-z0-9_]*$" }
            };
            schema["properties"]["queries"] = new JObject
            {
                ["type"] = "array", ["minItems"] = 1, ["maxItems"] = 128,
                ["description"] = "Gateway-verified bound component identities, same project/editor/scene/avatar; at most 512 total simple properties. Every identity is rechecked before its values are read.",
                ["items"] = new JObject
                {
                    ["type"] = "object", ["additionalProperties"] = false, ["properties"] = fields,
                    ["required"] = new JArray("gameObjectPath", "componentType", "componentIndex", "propertyNames", "objectGlobalObjectId", "componentGlobalObjectId", "scenePath", "sceneGuid"),
                    ["dependentRequired"] = new JObject { ["avatarGlobalObjectId"] = new JArray("avatarPath"), ["avatarPath"] = new JArray("avatarGlobalObjectId") }
                }
            };
            schema["oneOf"] = new JArray(
                new JObject { ["required"] = required, ["not"] = new JObject { ["required"] = new JArray("queries") } },
                new JObject { ["required"] = new JArray("queries"), ["not"] = new JObject
                {
                    ["anyOf"] = new JArray(scalarProperties.Select(name => new JObject { ["required"] = new JArray(name) }))
                } });
            return schema;
        }

        internal static object Handle(JObject args)
        {
            try
            {
                if (args.Properties().Any(property => property.Name != "queries"))
                    throw new ArgumentException("queries and scalar arguments are mutually exclusive.");
                var queries = args["queries"] as JArray;
                ValidateQueries(queries);
                var frame = Time.frameCount;
                var sampleId = Guid.NewGuid().ToString("N");
                var sampledAtUtc = DateTime.UtcNow.ToString("O");
                var rows = new JArray();
                var successCount = 0;
                var failureCount = 0;
                for (var index = 0; index < queries.Count; index++)
                {
                    var query = (JObject)queries[index];
                    Component component = null;
                    string identityError = null;
                    try { component = ResolveExact(query); }
                    catch (Exception error) { identityError = error.Message; }
                    var values = new JArray();
                    foreach (var name in (JArray)query["propertyNames"])
                    {
                        var value = new JObject { ["propertyPath"] = (string)name };
                        try
                        {
                            if (identityError != null) throw new InvalidOperationException(identityError);
                            var member = ComponentCrudCore.ResolveMember(component.GetType(), (string)name);
                            var property = member as PropertyInfo;
                            if (property != null && (!property.CanRead || property.GetIndexParameters().Length != 0))
                                throw new InvalidOperationException("The property must have a non-indexed getter.");
                            var raw = ComponentCrudCore.GetReadOnlyMemberValue(component, member);
                            if (!IsSimple(raw)) throw new InvalidOperationException("Batch reads support simple values and object references; use a bounded scalar read for collections.");
                            value["valueType"] = ComponentCrudCore.GetMemberType(member).FullName;
                            value["propertyValue"] = raw == null ? JValue.CreateNull() : JToken.FromObject(ComponentCrudCore.DescribeValue(raw));
                            value["ok"] = true;
                            successCount++;
                        }
                        catch (Exception error)
                        {
                            value["ok"] = false;
                            value["error"] = new JObject
                            {
                                ["code"] = identityError == null ? "property_read_failed" : "component_identity_mismatch",
                                ["message"] = error.GetBaseException().Message
                            };
                            failureCount++;
                        }
                        values.Add(value);
                    }
                    rows.Add(new JObject
                    {
                        ["queryIndex"] = index,
                        ["gameObjectPath"] = (string)query["gameObjectPath"],
                        ["objectGlobalObjectId"] = (string)query["objectGlobalObjectId"],
                        ["componentGlobalObjectId"] = (string)query["componentGlobalObjectId"],
                        ["componentType"] = (string)query["componentType"],
                        ["componentIndex"] = (int)query["componentIndex"],
                        ["identityVerified"] = identityError == null,
                        ["properties"] = values
                    });
                }
                return VRCForgeToolResult.Completed(
                    $"Component property batch completed: {successCount} succeeded, {failureCount} failed.",
                    new
                    {
                        schema = "vrcforge.component_property_batch.v1", sampleId, sampledAtUtc,
                        unityFrame = frame, completedUnityFrame = Time.frameCount,
                        sampleScope = "single_synchronous_editor_call",
                        successCount, failureCount, allSucceeded = failureCount == 0,
                        queryCount = queries.Count, propertyCount = successCount + failureCount,
                        results = rows
                    });
            }
            catch (Exception error)
            {
                return VRCForgeToolResult.FailedWithCode("component_property_batch_invalid", error.Message);
            }
        }

        private static bool IsSimple(object value)
        {
            return value == null || value.GetType().IsPrimitive || value is string || value is Enum
                || value is Vector2 || value is Vector3 || value is Vector4 || value is Quaternion
                || value is Color || value is Bounds || value is Matrix4x4 || value is UnityEngine.Object;
        }

        private static void ValidateQueries(JArray queries)
        {
            if (queries == null || queries.Count < 1 || queries.Count > 128)
                throw new ArgumentException("queries must contain 1–128 components.");
            var count = 0;
            var ids = new System.Collections.Generic.HashSet<string>(StringComparer.Ordinal);
            foreach (var token in queries)
            {
                var query = token as JObject;
                var required = new[] { "gameObjectPath", "componentType", "objectGlobalObjectId", "componentGlobalObjectId", "scenePath", "sceneGuid" };
                if (query == null || required.Any(key => query[key]?.Type != JTokenType.String || string.IsNullOrWhiteSpace((string)query[key]))
                    || query["componentIndex"]?.Type != JTokenType.Integer || (int)query["componentIndex"] < 0
                    || query.Properties().Any(property => !required.Concat(new[] { "componentIndex", "propertyNames", "avatarGlobalObjectId", "avatarPath" }).Contains(property.Name)))
                    throw new ArgumentException("Invalid component property query identity or fields.");
                var names = query["propertyNames"] as JArray;
                if (names == null || names.Count < 1 || names.Count > 16
                    || names.Any(name => name.Type != JTokenType.String || !Regex.IsMatch((string)name, "^[A-Za-z_][A-Za-z0-9_]*$"))
                    || names.Select(name => (string)name).Distinct(StringComparer.Ordinal).Count() != names.Count)
                    throw new ArgumentException("propertyNames must contain 1–16 distinct simple names.");
                if (!ids.Add((string)query["componentGlobalObjectId"])) throw new ArgumentException("Duplicate component query.");
                count += names.Count;
                if (count > 512) throw new ArgumentException("A batch may read at most 512 properties.");
                if (query.ContainsKey("avatarGlobalObjectId") != query.ContainsKey("avatarPath"))
                    throw new ArgumentException("Avatar identity must include both ID and exact path.");
                if (new[] { "sceneGuid", "scenePath", "avatarGlobalObjectId", "avatarPath" }.Any(key => (string)query[key] != (string)queries[0][key]))
                    throw new ArgumentException("All components must belong to the same bound scene and Avatar namespace.");
            }
        }

        private static Component ResolveExact(JObject query)
        {
            GlobalObjectId objectId, componentId;
            if (!GlobalObjectId.TryParse((string)query["objectGlobalObjectId"], out objectId)
                || !GlobalObjectId.TryParse((string)query["componentGlobalObjectId"], out componentId))
                throw new InvalidOperationException("Invalid object/component GlobalObjectId.");
            var go = GlobalObjectId.GlobalObjectIdentifierToObjectSlow(objectId) as GameObject;
            var component = GlobalObjectId.GlobalObjectIdentifierToObjectSlow(componentId) as Component;
            if (go == null || component == null || component.gameObject != go || EditorUtility.IsPersistent(go)
                || !go.scene.IsValid() || !go.scene.isLoaded || go.scene.path != (string)query["scenePath"]
                || AssetDatabase.AssetPathToGUID(go.scene.path) != (string)query["sceneGuid"]
                || ComponentCrudCore.GetHierarchyPath(go.transform) != (string)query["gameObjectPath"]
                || component.GetType().FullName != (string)query["componentType"]
                || ComponentCrudCore.ResolveComponent(go, component.GetType(), (int)query["componentIndex"]) != component)
                throw new InvalidOperationException("The scene object or component no longer matches its exact bound identity.");
            if (query["avatarGlobalObjectId"] != null)
            {
                GlobalObjectId avatarId;
                if (!GlobalObjectId.TryParse((string)query["avatarGlobalObjectId"], out avatarId))
                    throw new InvalidOperationException("Invalid Avatar GlobalObjectId.");
                var avatar = GlobalObjectId.GlobalObjectIdentifierToObjectSlow(avatarId) as GameObject;
                if (avatar == null || avatar.scene != go.scene || ComponentCrudCore.GetHierarchyPath(avatar.transform) != (string)query["avatarPath"]
                    || (go != avatar && !go.transform.IsChildOf(avatar.transform)))
                    throw new InvalidOperationException("The component is outside the bound Avatar.");
            }
            return component;
        }
    }
}
