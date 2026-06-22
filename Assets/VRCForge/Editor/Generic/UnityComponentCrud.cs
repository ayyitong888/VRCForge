using System;
using System.Collections;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;
using System.Reflection;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Tools;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEngine;

namespace VRCForge.Editor
{
    // ------------------------------------------------------------------
    // Generic Unity component CRUD layer (v0.5 "first cut").
    //
    // Four MCP tools, all reflection-based so VRCForge never hard-references
    // Modular Avatar / VRChat SDK assemblies:
    //   vrc_get_property    (read)
    //   vrc_add_component   (write, Undo-registered)
    //   vrc_remove_component(write, Undo-registered)
    //   vrc_set_property    (write, Undo-registered)
    //
    // All write tools register a Unity Undo entry so the checkpoint timeline
    // (bound to Undo) can roll them back. Each write tool also supports a
    // preview mode that reports what *would* change without mutating, feeding
    // the per-action approval card (preview + risk summary).
    // ------------------------------------------------------------------

    internal static class ComponentCrudCore
    {
        internal static GameObject ResolveGameObject(string pathOrName)
        {
            var normalized = NormalizePath(pathOrName);
            if (string.IsNullOrEmpty(normalized))
            {
                throw new InvalidOperationException("gameObjectPath is required.");
            }

            var sceneObjects = EnumerateSceneGameObjects().ToList();

            // 1) Exact full-hierarchy-path match (handles inactive objects too).
            foreach (var go in sceneObjects)
            {
                if (string.Equals(GetHierarchyPath(go.transform), normalized, StringComparison.Ordinal))
                {
                    return go;
                }
            }

            // 2) Leaf-name match as a convenience fallback.
            var leaf = normalized.Contains('/')
                ? normalized.Substring(normalized.LastIndexOf('/') + 1)
                : normalized;
            var nameMatches = sceneObjects.Where(go => string.Equals(go.name, leaf, StringComparison.Ordinal)).ToList();
            if (nameMatches.Count == 1)
            {
                return nameMatches[0];
            }
            if (nameMatches.Count > 1)
            {
                throw new InvalidOperationException(
                    $"GameObject name '{leaf}' is ambiguous ({nameMatches.Count} matches). Pass a full hierarchy path.");
            }

            throw new InvalidOperationException($"GameObject not found in loaded scenes: '{pathOrName}'.");
        }

        internal static IEnumerable<GameObject> EnumerateSceneGameObjects()
        {
            var seen = new HashSet<int>();
            foreach (var transform in Resources.FindObjectsOfTypeAll<Transform>())
            {
                if (transform == null)
                {
                    continue;
                }
                var go = transform.gameObject;
                if (go == null || EditorUtility.IsPersistent(go))
                {
                    continue;
                }
                if (!go.scene.IsValid() || !go.scene.isLoaded)
                {
                    continue;
                }
                if (seen.Add(go.GetInstanceID()))
                {
                    yield return go;
                }
            }
        }

        internal static Type ResolveComponentType(string typeName)
        {
            if (string.IsNullOrWhiteSpace(typeName))
            {
                throw new InvalidOperationException("componentType is required.");
            }

            var trimmed = typeName.Trim();

            // 1) Direct full-name lookup.
            var direct = FindType(trimmed);
            if (direct != null && typeof(Component).IsAssignableFrom(direct))
            {
                return direct;
            }

            // 2) Common UnityEngine shorthand (e.g. "BoxCollider").
            var qualified = FindType("UnityEngine." + trimmed);
            if (qualified != null && typeof(Component).IsAssignableFrom(qualified))
            {
                return qualified;
            }

            // 3) Last-segment name scan across all component types.
            Type byShortName = null;
            var matchCount = 0;
            foreach (var assembly in AppDomain.CurrentDomain.GetAssemblies())
            {
                Type[] types;
                try
                {
                    types = assembly.GetTypes();
                }
                catch (ReflectionTypeLoadException ex)
                {
                    types = ex.Types.Where(t => t != null).ToArray();
                }
                catch
                {
                    continue;
                }

                foreach (var type in types)
                {
                    if (type == null || !typeof(Component).IsAssignableFrom(type))
                    {
                        continue;
                    }
                    if (string.Equals(type.Name, trimmed, StringComparison.Ordinal))
                    {
                        byShortName = type;
                        matchCount++;
                    }
                }
            }

            if (matchCount == 1)
            {
                return byShortName;
            }
            if (matchCount > 1)
            {
                throw new InvalidOperationException(
                    $"Component type '{typeName}' is ambiguous ({matchCount} matches). Pass a fully-qualified type name.");
            }

            throw new InvalidOperationException(
                $"Component type not found or not a UnityEngine.Component: '{typeName}'.");
        }

        internal static Component ResolveComponent(GameObject go, Type type, int index)
        {
            var components = go.GetComponents(type);
            if (components == null || components.Length == 0)
            {
                throw new InvalidOperationException(
                    $"GameObject '{go.name}' has no component of type '{type.FullName}'.");
            }
            if (index < 0 || index >= components.Length)
            {
                throw new InvalidOperationException(
                    $"componentIndex {index} out of range; '{go.name}' has {components.Length} component(s) of type '{type.Name}'.");
            }
            return components[index];
        }

        internal static MemberInfo ResolveMember(Type type, string memberName)
        {
            const BindingFlags flags = BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic;
            var field = type.GetField(memberName, flags);
            if (field != null)
            {
                return field;
            }
            var property = type.GetProperty(memberName, flags);
            if (property != null)
            {
                return property;
            }
            throw new InvalidOperationException($"Member '{memberName}' not found on type '{type.FullName}'.");
        }

        internal static Type GetMemberType(MemberInfo member)
        {
            return member is FieldInfo field ? field.FieldType : ((PropertyInfo)member).PropertyType;
        }

        internal static object GetMemberValue(object source, MemberInfo member)
        {
            return member is FieldInfo field ? field.GetValue(source) : ((PropertyInfo)member).GetValue(source);
        }

        internal static void SetMemberValue(object target, MemberInfo member, object value)
        {
            if (member is FieldInfo field)
            {
                field.SetValue(target, value);
                return;
            }
            var property = (PropertyInfo)member;
            if (!property.CanWrite)
            {
                throw new InvalidOperationException($"Property '{property.Name}' on '{property.DeclaringType?.FullName}' is read-only.");
            }
            property.SetValue(target, value);
        }

        // Convert an arbitrary JSON token into the target CLR/Unity type.
        internal static object ConvertValue(JToken token, Type targetType)
        {
            if (targetType == typeof(string))
            {
                return token?.Type == JTokenType.Null ? null : token?.ToString();
            }

            if (token == null || token.Type == JTokenType.Null)
            {
                return targetType.IsValueType ? Activator.CreateInstance(targetType) : null;
            }

            if (targetType.IsEnum)
            {
                if (token.Type == JTokenType.Integer)
                {
                    return Enum.ToObject(targetType, token.ToObject<long>());
                }
                return Enum.Parse(targetType, token.ToString(), true);
            }

            if (targetType == typeof(Vector2))
            {
                var v = ReadFloats(token, 2);
                return new Vector2(v[0], v[1]);
            }
            if (targetType == typeof(Vector3))
            {
                var v = ReadFloats(token, 3);
                return new Vector3(v[0], v[1], v[2]);
            }
            if (targetType == typeof(Vector4))
            {
                var v = ReadFloats(token, 4);
                return new Vector4(v[0], v[1], v[2], v[3]);
            }
            if (targetType == typeof(Quaternion))
            {
                var v = ReadFloats(token, 4);
                return new Quaternion(v[0], v[1], v[2], v[3]);
            }
            if (targetType == typeof(Color))
            {
                var v = ReadFloats(token, 4, defaultLast: 1f);
                return new Color(v[0], v[1], v[2], v[3]);
            }

            if (typeof(IList).IsAssignableFrom(targetType) && token.Type == JTokenType.Array)
            {
                return ConvertListValue((JArray)token, targetType);
            }

            if (typeof(UnityEngine.Object).IsAssignableFrom(targetType))
            {
                return ResolveObjectReference(token, targetType);
            }

            // Primitive / numeric / bool fall-through.
            try
            {
                return token.ToObject(targetType);
            }
            catch (Exception ex)
            {
                throw new InvalidOperationException(
                    $"Cannot convert value '{token}' to type '{targetType.FullName}': {ex.Message}");
            }
        }

        private static float[] ReadFloats(JToken token, int count, float defaultLast = 0f)
        {
            var result = new float[count];
            for (var i = 0; i < count; i++)
            {
                result[i] = (i == count - 1) ? defaultLast : 0f;
            }

            if (token.Type == JTokenType.Array)
            {
                var arr = (JArray)token;
                for (var i = 0; i < count && i < arr.Count; i++)
                {
                    result[i] = arr[i].ToObject<float>();
                }
                return result;
            }

            if (token.Type == JTokenType.Object)
            {
                string[] keys = { "x", "y", "z", "w" };
                string[] colorKeys = { "r", "g", "b", "a" };
                var obj = (JObject)token;
                for (var i = 0; i < count; i++)
                {
                    var t = obj[keys[i]] ?? obj[colorKeys[i]];
                    if (t != null)
                    {
                        result[i] = t.ToObject<float>();
                    }
                }
                return result;
            }

            throw new InvalidOperationException(
                $"Expected an array or object with {count} numeric components, got: {token}");
        }

        private static UnityEngine.Object ResolveObjectReference(JToken token, Type targetType)
        {
            // Accept null/empty as a cleared reference.
            var raw = token.ToString().Trim();
            if (string.IsNullOrEmpty(raw))
            {
                return null;
            }

            // Integer => instance ID lookup.
            if (token.Type == JTokenType.Integer)
            {
                var obj = EditorUtility.InstanceIDToObject(token.ToObject<int>());
                if (obj != null && targetType.IsInstanceOfType(obj))
                {
                    return obj;
                }
                throw new InvalidOperationException(
                    $"Instance ID {raw} did not resolve to a '{targetType.Name}'.");
            }

            // Asset-relative path => load from AssetDatabase.
            if (raw.Replace("\\", "/").StartsWith("Assets/", StringComparison.OrdinalIgnoreCase))
            {
                var asset = AssetDatabase.LoadAssetAtPath(raw.Replace("\\", "/"), targetType);
                if (asset != null)
                {
                    return asset;
                }
                throw new InvalidOperationException(
                    $"No '{targetType.Name}' asset found at '{raw}'.");
            }

            // Scene hierarchy path => resolve GameObject, then component if needed.
            var go = ResolveGameObject(raw);
            if (targetType == typeof(GameObject))
            {
                return go;
            }
            if (typeof(Component).IsAssignableFrom(targetType))
            {
                var comp = go.GetComponent(targetType);
                if (comp != null)
                {
                    return comp;
                }
                throw new InvalidOperationException(
                    $"GameObject '{raw}' has no '{targetType.Name}' component to reference.");
            }

            throw new InvalidOperationException(
                $"Cannot resolve an object reference of type '{targetType.Name}' from '{raw}'.");
        }

        private static object ConvertListValue(JArray array, Type targetType)
        {
            var elementType = ResolveListElementType(targetType);
            if (elementType == null)
            {
                throw new InvalidOperationException($"Cannot determine list element type for '{targetType.FullName}'.");
            }

            IList list;
            if (targetType.IsInterface || targetType.IsAbstract)
            {
                var concrete = typeof(List<>).MakeGenericType(elementType);
                list = (IList)Activator.CreateInstance(concrete);
            }
            else
            {
                list = (IList)Activator.CreateInstance(targetType);
            }

            foreach (var item in array)
            {
                list.Add(ConvertValue(item, elementType));
            }
            return list;
        }

        private static Type ResolveListElementType(Type targetType)
        {
            if (targetType.IsGenericType)
            {
                return targetType.GetGenericArguments()[0];
            }
            foreach (var iface in targetType.GetInterfaces())
            {
                if (iface.IsGenericType && iface.GetGenericTypeDefinition() == typeof(IList<>))
                {
                    return iface.GetGenericArguments()[0];
                }
            }
            return typeof(object);
        }

        internal static object DescribeValue(object value)
        {
            switch (value)
            {
                case null:
                    return null;
                case string s:
                    return s;
                case bool b:
                    return b;
                case Enum e:
                    return e.ToString();
                case Vector2 v2:
                    return new { x = v2.x, y = v2.y };
                case Vector3 v3:
                    return new { x = v3.x, y = v3.y, z = v3.z };
                case Vector4 v4:
                    return new { x = v4.x, y = v4.y, z = v4.z, w = v4.w };
                case Quaternion q:
                    return new { x = q.x, y = q.y, z = q.z, w = q.w };
                case Color c:
                    return new { r = c.r, g = c.g, b = c.b, a = c.a };
                case UnityEngine.Object uo:
                    return new
                    {
                        name = uo == null ? null : uo.name,
                        type = uo == null ? null : uo.GetType().FullName,
                        instanceId = uo == null ? 0 : uo.GetInstanceID()
                    };
            }

            if (value is IEnumerable enumerable && !(value is string))
            {
                var items = new List<object>();
                var count = 0;
                foreach (var item in enumerable)
                {
                    if (count >= 50)
                    {
                        break;
                    }
                    items.Add(DescribeValue(item));
                    count++;
                }
                return items;
            }

            if (value.GetType().IsPrimitive)
            {
                return value;
            }
            return value.ToString();
        }

        internal static Type FindType(string fullName)
        {
            foreach (var assembly in AppDomain.CurrentDomain.GetAssemblies())
            {
                try
                {
                    var type = assembly.GetType(fullName, false);
                    if (type != null)
                    {
                        return type;
                    }
                }
                catch
                {
                    // Ignore transient reflection failures during editor reloads.
                }
            }
            return null;
        }

        internal static string GetHierarchyPath(Transform transform)
        {
            var segments = new Stack<string>();
            var current = transform;
            while (current != null)
            {
                segments.Push(current.name);
                current = current.parent;
            }
            return string.Join("/", segments);
        }

        internal static string NormalizePath(string value)
        {
            return (value ?? string.Empty).Replace("\\", "/").Trim().Trim('/');
        }
    }

    [McpForUnityTool(
        name: "vrc_get_property",
        Description = "Read a single serialized field/property value from a component on a scene GameObject (read-only)."
    )]
    public static class GetPropertyTool
    {
        public const string ToolName = "vrc_get_property";

        public class GetPropertyParameters
        {
            [ToolParameter("Full hierarchy path (e.g. 'Avatar/Body') or unique name of the GameObject.", Required = true)]
            public string gameObjectPath { get; set; } = "";

            [ToolParameter("Component type. Fully-qualified (e.g. 'UnityEngine.SkinnedMeshRenderer') or unique short name.", Required = true)]
            public string componentType { get; set; } = "";

            [ToolParameter("Field or property name to read (e.g. 'enabled', 'sharedMesh').", Required = true)]
            public string propertyPath { get; set; } = "";

            [ToolParameter("Which component instance to read when several of the same type exist (default 0).", Required = false)]
            public int? componentIndex { get; set; } = 0;
        }

        public static object HandleCommand(JObject @params)
        {
            var p = (@params ?? new JObject()).ToObject<GetPropertyParameters>() ?? new GetPropertyParameters();
            try
            {
                var go = ComponentCrudCore.ResolveGameObject(p.gameObjectPath);
                var type = ComponentCrudCore.ResolveComponentType(p.componentType);
                var component = ComponentCrudCore.ResolveComponent(go, type, p.componentIndex ?? 0);
                var member = ComponentCrudCore.ResolveMember(component.GetType(), p.propertyPath);
                var value = ComponentCrudCore.GetMemberValue(component, member);

                var payload = new
                {
                    gameObjectPath = ComponentCrudCore.GetHierarchyPath(go.transform),
                    componentType = component.GetType().FullName,
                    componentIndex = p.componentIndex ?? 0,
                    propertyPath = p.propertyPath,
                    valueType = ComponentCrudCore.GetMemberType(member).FullName,
                    propertyValue = ComponentCrudCore.DescribeValue(value)
                };

                return new SuccessResponse(
                    $"{component.GetType().Name}.{p.propertyPath} = {payload.propertyValue ?? "null"}",
                    payload);
            }
            catch (Exception ex)
            {
                return new ErrorResponse($"Get property failed: {ex.Message}");
            }
        }
    }

    [McpForUnityTool(
        name: "vrc_add_component",
        Description = "Add a component of a given type to a scene GameObject (Undo-registered). Supports preview mode."
    )]
    public static class AddComponentTool
    {
        public const string ToolName = "vrc_add_component";

        public class AddComponentParameters
        {
            [ToolParameter("Full hierarchy path or unique name of the target GameObject.", Required = true)]
            public string gameObjectPath { get; set; } = "";

            [ToolParameter("Component type to add. Fully-qualified or unique short name.", Required = true)]
            public string componentType { get; set; } = "";

            [ToolParameter("If true, only report what would happen without mutating the scene (default false).", Required = false)]
            public bool? preview { get; set; } = false;
        }

        public static object HandleCommand(JObject @params)
        {
            var p = (@params ?? new JObject()).ToObject<AddComponentParameters>() ?? new AddComponentParameters();
            try
            {
                var go = ComponentCrudCore.ResolveGameObject(p.gameObjectPath);
                var type = ComponentCrudCore.ResolveComponentType(p.componentType);
                var goPath = ComponentCrudCore.GetHierarchyPath(go.transform);
                var existing = go.GetComponents(type).Length;

                if (p.preview ?? false)
                {
                    var previewPayload = new
                    {
                        action = "add_component",
                        preview = true,
                        gameObjectPath = goPath,
                        componentType = type.FullName,
                        existingCount = existing
                    };
                    return new SuccessResponse(
                        $"Preview: would add '{type.Name}' to '{goPath}' (currently {existing} of this type).",
                        previewPayload);
                }

                var added = Undo.AddComponent(go, type);
                if (added == null)
                {
                    return new ErrorResponse(
                        $"Unity refused to add '{type.Name}' to '{goPath}' (missing dependency or disallowed type).");
                }
                EditorUtility.SetDirty(go);

                var payload = new
                {
                    action = "add_component",
                    preview = false,
                    gameObjectPath = goPath,
                    componentType = type.FullName,
                    componentIndex = go.GetComponents(type).Length - 1,
                    instanceId = added.GetInstanceID()
                };
                return new SuccessResponse($"Added '{type.Name}' to '{goPath}'.", payload);
            }
            catch (Exception ex)
            {
                return new ErrorResponse($"Add component failed: {ex.Message}");
            }
        }
    }

    [McpForUnityTool(
        name: "vrc_remove_component",
        Description = "Remove a component of a given type from a scene GameObject (Undo-registered). Supports preview mode."
    )]
    public static class RemoveComponentTool
    {
        public const string ToolName = "vrc_remove_component";

        public class RemoveComponentParameters
        {
            [ToolParameter("Full hierarchy path or unique name of the target GameObject.", Required = true)]
            public string gameObjectPath { get; set; } = "";

            [ToolParameter("Component type to remove. Fully-qualified or unique short name.", Required = true)]
            public string componentType { get; set; } = "";

            [ToolParameter("Which component instance to remove when several of the same type exist (default 0).", Required = false)]
            public int? componentIndex { get; set; } = 0;

            [ToolParameter("If true, only report what would happen without mutating the scene (default false).", Required = false)]
            public bool? preview { get; set; } = false;
        }

        public static object HandleCommand(JObject @params)
        {
            var p = (@params ?? new JObject()).ToObject<RemoveComponentParameters>() ?? new RemoveComponentParameters();
            try
            {
                var go = ComponentCrudCore.ResolveGameObject(p.gameObjectPath);
                var type = ComponentCrudCore.ResolveComponentType(p.componentType);
                var goPath = ComponentCrudCore.GetHierarchyPath(go.transform);
                var index = p.componentIndex ?? 0;
                var component = ComponentCrudCore.ResolveComponent(go, type, index);

                if (component is Transform)
                {
                    return new ErrorResponse("Refusing to remove a Transform component; every GameObject requires one.");
                }

                if (p.preview ?? false)
                {
                    var previewPayload = new
                    {
                        action = "remove_component",
                        preview = true,
                        gameObjectPath = goPath,
                        componentType = component.GetType().FullName,
                        componentIndex = index
                    };
                    return new SuccessResponse(
                        $"Preview: would remove '{component.GetType().Name}' (index {index}) from '{goPath}'.",
                        previewPayload);
                }

                var removedType = component.GetType().FullName;
                Undo.DestroyObjectImmediate(component);
                EditorUtility.SetDirty(go);

                var payload = new
                {
                    action = "remove_component",
                    preview = false,
                    gameObjectPath = goPath,
                    componentType = removedType,
                    componentIndex = index
                };
                return new SuccessResponse($"Removed '{type.Name}' (index {index}) from '{goPath}'.", payload);
            }
            catch (Exception ex)
            {
                return new ErrorResponse($"Remove component failed: {ex.Message}");
            }
        }
    }

    [McpForUnityTool(
        name: "vrc_set_property",
        Description = "Set a single field/property on a component of a scene GameObject (Undo-registered). Supports preview mode."
    )]
    public static class SetPropertyTool
    {
        public const string ToolName = "vrc_set_property";

        public class SetPropertyParameters
        {
            [ToolParameter("Full hierarchy path or unique name of the target GameObject.", Required = true)]
            public string gameObjectPath { get; set; } = "";

            [ToolParameter("Component type. Fully-qualified or unique short name.", Required = true)]
            public string componentType { get; set; } = "";

            [ToolParameter("Field or property name to set (e.g. 'enabled', 'm_Weight').", Required = true)]
            public string propertyPath { get; set; } = "";

            [ToolParameter("Which component instance to target when several of the same type exist (default 0).", Required = false)]
            public int? componentIndex { get; set; } = 0;

            [ToolParameter("If true, only report what would happen without mutating the scene (default false).", Required = false)]
            public bool? preview { get; set; } = false;
        }

        public static object HandleCommand(JObject @params)
        {
            var p = (@params ?? new JObject()).ToObject<SetPropertyParameters>() ?? new SetPropertyParameters();
            try
            {
                var rawParams = @params ?? new JObject();
                if (rawParams["value"] == null)
                {
                    return new ErrorResponse("Set property requires a 'value' argument.");
                }
                var valueToken = rawParams["value"];

                var go = ComponentCrudCore.ResolveGameObject(p.gameObjectPath);
                var type = ComponentCrudCore.ResolveComponentType(p.componentType);
                var component = ComponentCrudCore.ResolveComponent(go, type, p.componentIndex ?? 0);
                var member = ComponentCrudCore.ResolveMember(component.GetType(), p.propertyPath);
                var memberType = ComponentCrudCore.GetMemberType(member);
                var goPath = ComponentCrudCore.GetHierarchyPath(go.transform);

                var oldValue = ComponentCrudCore.GetMemberValue(component, member);
                var newValue = ComponentCrudCore.ConvertValue(valueToken, memberType);

                if (p.preview ?? false)
                {
                    var previewPayload = new
                    {
                        action = "set_property",
                        preview = true,
                        gameObjectPath = goPath,
                        componentType = component.GetType().FullName,
                        componentIndex = p.componentIndex ?? 0,
                        propertyPath = p.propertyPath,
                        valueType = memberType.FullName,
                        oldValue = ComponentCrudCore.DescribeValue(oldValue),
                        newValue = ComponentCrudCore.DescribeValue(newValue)
                    };
                    return new SuccessResponse(
                        $"Preview: would set {component.GetType().Name}.{p.propertyPath} to {previewPayload.newValue ?? "null"}.",
                        previewPayload);
                }

                Undo.RecordObject(component, $"Set {component.GetType().Name}.{p.propertyPath}");
                ComponentCrudCore.SetMemberValue(component, member, newValue);
                EditorUtility.SetDirty(component);

                var payload = new
                {
                    action = "set_property",
                    preview = false,
                    gameObjectPath = goPath,
                    componentType = component.GetType().FullName,
                    componentIndex = p.componentIndex ?? 0,
                    propertyPath = p.propertyPath,
                    valueType = memberType.FullName,
                    oldValue = ComponentCrudCore.DescribeValue(oldValue),
                    newValue = ComponentCrudCore.DescribeValue(ComponentCrudCore.GetMemberValue(component, member))
                };
                return new SuccessResponse(
                    $"Set {component.GetType().Name}.{p.propertyPath} on '{goPath}'.",
                    payload);
            }
            catch (Exception ex)
            {
                return new ErrorResponse($"Set property failed: {ex.Message}");
            }
        }
    }
}
