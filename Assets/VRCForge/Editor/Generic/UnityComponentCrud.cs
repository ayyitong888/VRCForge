using System;
using System.Collections;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;
using System.Reflection;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.SceneManagement;
using VRCForge.Core.MCP;

namespace VRCForge.Editor
{
    // ------------------------------------------------------------------
    // Generic Unity component CRUD layer (v0.5 "first cut").
    //
    // Five MCP tools, all reflection-based so VRCForge never hard-references
    // Modular Avatar / VRChat SDK assemblies:
    //   vrc_get_property    (read)
    //   vrc_inspect_skinned_mesh_bone_usage (read)
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
        internal sealed class GameObjectNotFoundException : InvalidOperationException
        {
            internal GameObjectNotFoundException(string message) : base(message) { }
        }

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

            throw new GameObjectNotFoundException($"GameObject not found in loaded scenes: '{pathOrName}'.");
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

            if (targetType.IsArray)
            {
                var convertedArray = Array.CreateInstance(elementType, array.Count);
                for (var i = 0; i < array.Count; i++)
                {
                    convertedArray.SetValue(ConvertValue(array[i], elementType), i);
                }
                return convertedArray;
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

        internal static object DescribeValue(
            object value,
            int maxItems,
            out int returnedItemCount,
            out int valueItemCount,
            out bool valueTruncated)
        {
            returnedItemCount = -1;
            valueItemCount = -1;
            valueTruncated = false;
            // UnityEngine.Object references such as Transform also implement
            // IEnumerable.  They are scalar object references for MCP
            // property reads and must not be expanded into child transforms.
            if (value is UnityEngine.Object || !(value is IEnumerable enumerable) || value is string)
            {
                return DescribeValue(value);
            }

            var items = new List<object>();
            var collection = value as ICollection;
            var totalKnown = collection != null;
            if (collection != null)
            {
                valueItemCount = collection.Count;
            }
            foreach (var item in enumerable)
            {
                if (items.Count >= maxItems)
                {
                    valueTruncated = true;
                    break;
                }
                items.Add(DescribeValue(item));
            }
            returnedItemCount = items.Count;
            if (!totalKnown && !valueTruncated)
            {
                valueItemCount = returnedItemCount;
            }
            else if (totalKnown)
            {
                valueTruncated = valueItemCount > returnedItemCount;
            }
            return items;
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

        internal static SavedSceneSnapshot ResolveSavedSceneFor(GameObject gameObject)
        {
            if (gameObject == null
                || !gameObject.scene.IsValid()
                || !gameObject.scene.isLoaded
                || string.IsNullOrWhiteSpace(gameObject.scene.path))
            {
                throw new InvalidOperationException("Component writes require one loaded saved scene object.");
            }
            return SceneObjectCopyCore.ResolveSavedScene(gameObject.scene.path, "component target scene");
        }

        internal static SavedSceneSnapshot SaveAndResolveScene(SavedSceneSnapshot beforeScene)
        {
            // Undo.RecordObject normally finalizes its diff at the end of the
            // editor frame. These commands save in the same frame, so flush
            // first or Unity can mark the scene dirty again after success.
            Undo.FlushUndoRecordObjects();
            EditorSceneManager.MarkSceneDirty(beforeScene.Scene);
            if (!EditorSceneManager.SaveScene(beforeScene.Scene))
            {
                throw new InvalidOperationException("The component target scene could not be saved.");
            }
            var afterScene = SceneObjectCopyCore.ResolveSavedScene(
                beforeScene.Path,
                "component target scene readback");
            if (afterScene.Guid != beforeScene.Guid
                || afterScene.Handle != beforeScene.Handle
                || afterScene.MetaDigest != beforeScene.MetaDigest
                || afterScene.MetaIdentity != beforeScene.MetaIdentity)
            {
                throw new InvalidOperationException("The component target scene identity changed during the write.");
            }
            return afterScene;
        }

        internal static bool ValuesEqual(object left, object right)
        {
            if (ReferenceEquals(left, right))
            {
                return true;
            }
            if (left == null || right == null)
            {
                return false;
            }
            if (left is UnityEngine.Object leftObject && right is UnityEngine.Object rightObject)
            {
                return leftObject == rightObject;
            }
            if (left is float leftFloat && right is float rightFloat)
            {
                return FloatApproximately(leftFloat, rightFloat);
            }
            if (left is double leftDouble && right is double rightDouble)
            {
                var scale = Math.Max(1d, Math.Max(Math.Abs(leftDouble), Math.Abs(rightDouble)));
                return Math.Abs(leftDouble - rightDouble) <= 1e-9d * scale;
            }
            if (left is Vector2 leftVector2 && right is Vector2 rightVector2)
            {
                return FloatApproximately(leftVector2.x, rightVector2.x)
                    && FloatApproximately(leftVector2.y, rightVector2.y);
            }
            if (left is Vector3 leftVector3 && right is Vector3 rightVector3)
            {
                return FloatApproximately(leftVector3.x, rightVector3.x)
                    && FloatApproximately(leftVector3.y, rightVector3.y)
                    && FloatApproximately(leftVector3.z, rightVector3.z);
            }
            if (left is Vector4 leftVector4 && right is Vector4 rightVector4)
            {
                return FloatApproximately(leftVector4.x, rightVector4.x)
                    && FloatApproximately(leftVector4.y, rightVector4.y)
                    && FloatApproximately(leftVector4.z, rightVector4.z)
                    && FloatApproximately(leftVector4.w, rightVector4.w);
            }
            if (left is Quaternion leftQuaternion && right is Quaternion rightQuaternion)
            {
                return QuaternionComponentsEqual(leftQuaternion, rightQuaternion)
                    || QuaternionComponentsEqual(
                        leftQuaternion,
                        new Quaternion(
                            -rightQuaternion.x,
                            -rightQuaternion.y,
                            -rightQuaternion.z,
                            -rightQuaternion.w));
            }
            if (left is Color leftColor && right is Color rightColor)
            {
                return FloatApproximately(leftColor.r, rightColor.r)
                    && FloatApproximately(leftColor.g, rightColor.g)
                    && FloatApproximately(leftColor.b, rightColor.b)
                    && FloatApproximately(leftColor.a, rightColor.a);
            }
            if (left is IList leftList && right is IList rightList)
            {
                if (leftList.Count != rightList.Count)
                {
                    return false;
                }
                for (var index = 0; index < leftList.Count; index++)
                {
                    if (!ValuesEqual(leftList[index], rightList[index]))
                    {
                        return false;
                    }
                }
                return true;
            }
            var describedLeft = DescribeValue(left);
            var describedRight = DescribeValue(right);
            return JToken.DeepEquals(
                describedLeft == null ? JValue.CreateNull() : JToken.FromObject(describedLeft),
                describedRight == null ? JValue.CreateNull() : JToken.FromObject(describedRight));
        }

        internal static bool ValuesExactlyEqual(object left, object right)
        {
            if (ReferenceEquals(left, right))
            {
                return true;
            }
            if (left == null || right == null)
            {
                return false;
            }
            if (left is UnityEngine.Object leftObject && right is UnityEngine.Object rightObject)
            {
                return leftObject == rightObject;
            }
            if (left is IList leftList && right is IList rightList)
            {
                if (leftList.Count != rightList.Count)
                {
                    return false;
                }
                for (var index = 0; index < leftList.Count; index++)
                {
                    if (!ValuesExactlyEqual(leftList[index], rightList[index]))
                    {
                        return false;
                    }
                }
                return true;
            }
            var describedLeft = DescribeValue(left);
            var describedRight = DescribeValue(right);
            return JToken.DeepEquals(
                describedLeft == null ? JValue.CreateNull() : JToken.FromObject(describedLeft),
                describedRight == null ? JValue.CreateNull() : JToken.FromObject(describedRight));
        }

        private static bool QuaternionComponentsEqual(Quaternion left, Quaternion right)
        {
            return FloatApproximately(left.x, right.x)
                && FloatApproximately(left.y, right.y)
                && FloatApproximately(left.z, right.z)
                && FloatApproximately(left.w, right.w);
        }

        private static bool FloatApproximately(float left, float right)
        {
            // Parent-space/world-space conversion commonly introduces tiny
            // absolute drift around zero where Mathf.Approximately is purely
            // relative and therefore too strict for persisted Transform reads.
            return Mathf.Approximately(left, right) || Mathf.Abs(left - right) <= 0.000001f;
        }

        internal sealed class MutationCleanupResult
        {
            private MutationCleanupResult(bool restored, string stage, string detail)
            {
                Restored = restored;
                Stage = stage ?? string.Empty;
                Detail = detail ?? string.Empty;
            }

            internal bool Restored { get; private set; }
            internal string Stage { get; private set; }
            internal string Detail { get; private set; }

            internal static MutationCleanupResult Completed()
            {
                return new MutationCleanupResult(true, "verified", "The pre-state was restored and verified.");
            }

            internal static MutationCleanupResult Failed(string stage, string detail)
            {
                return new MutationCleanupResult(false, stage, detail);
            }
        }

        internal static MutationCleanupResult RestoreFailedMutation(
            int undoGroup,
            SavedSceneSnapshot beforeScene,
            string gameObjectPath,
            Func<GameObject, bool> verifyObject)
        {
            var cleanupStage = "undo_flush";
            try
            {
                Undo.FlushUndoRecordObjects();
                cleanupStage = "undo_revert";
                Undo.RevertAllDownToGroup(undoGroup);
                cleanupStage = "scene_save";
                EditorSceneManager.MarkSceneDirty(beforeScene.Scene);
                if (!EditorSceneManager.SaveScene(beforeScene.Scene))
                {
                    return MutationCleanupResult.Failed("scene_save", "Unity did not save the restored scene.");
                }
                cleanupStage = "scene_readback";
                var cleanup = SceneObjectCopyCore.ResolveSavedScene(
                    beforeScene.Path,
                    "component mutation cleanup");
                if (cleanup.Guid != beforeScene.Guid
                    || cleanup.Handle != beforeScene.Handle)
                {
                    return MutationCleanupResult.Failed("scene_identity", "The restored scene identity did not match the pre-state.");
                }
                if (cleanup.FileDigest != beforeScene.FileDigest
                    || cleanup.FileIdentity != beforeScene.FileIdentity
                    || cleanup.MetaDigest != beforeScene.MetaDigest
                    || cleanup.MetaIdentity != beforeScene.MetaIdentity)
                {
                    return MutationCleanupResult.Failed("scene_content", "The restored scene or metadata did not match the pre-state bytes.");
                }
                cleanupStage = "object_readback";
                var restoredObject = SceneObjectCopyCore.ResolveUniqueGameObject(
                    cleanup.Scene,
                    gameObjectPath,
                    "component target cleanup");
                cleanupStage = "pre_state_verification";
                if (verifyObject != null && !verifyObject(restoredObject))
                {
                    return MutationCleanupResult.Failed("pre_state_verification", "The restored object did not match the captured pre-state value.");
                }
                return MutationCleanupResult.Completed();
            }
            catch (Exception cleanupException)
            {
                return MutationCleanupResult.Failed(cleanupStage, SafeExceptionMessage(cleanupException));
            }
        }

        internal static VRCForgeToolResult FailedMutationResult(
            string errorCode,
            string action,
            string failureStage,
            Exception failure,
            SavedSceneSnapshot beforeScene,
            MutationCleanupResult cleanup,
            bool? mutationApplied)
        {
            var restored = cleanup != null && cleanup.Restored;
            var failureDetail = SafeExceptionMessage(failure);
            var rootFailure = UnwrapException(failure);
            var message = restored
                ? $"{action} failed at {failureStage}: {failureDetail} The verified pre-state was restored."
                : $"{action} failed at {failureStage}: {failureDetail} Atomic cleanup could not be verified; checkpoint recovery requires user approval.";
            return VRCForgeToolResult.FailedWithCode(
                errorCode,
                message,
                new
                {
                    action,
                    failureLayer = "unity_editor_tool",
                    failureStage,
                    failureType = rootFailure == null ? string.Empty : rootFailure.GetType().FullName,
                    failureDetail,
                    scenePath = beforeScene == null ? string.Empty : beforeScene.Path,
                    mutationStarted = true,
                    mutationApplied,
                    writeState = mutationApplied == true
                        ? "applied_in_memory_before_failure"
                        : "application_unknown_before_failure",
                    restored,
                    cleanupVerified = restored,
                    cleanupStage = cleanup == null ? "not_run" : cleanup.Stage,
                    cleanupDetail = cleanup == null ? "Atomic cleanup did not return a result." : cleanup.Detail,
                    committed = restored ? (bool?)false : null,
                    commitState = restored ? "rolled_back" : "unknown",
                    requestMayHaveCommitted = !restored,
                    checkpointRecoveryRequired = !restored
                });
        }

        internal static string SafeExceptionMessage(Exception exception)
        {
            var root = UnwrapException(exception);
            var message = root == null || string.IsNullOrWhiteSpace(root.Message)
                ? "Unity component operation failed without an exception message."
                : root.Message.Trim();
            return message.Length <= 1024 ? message : message.Substring(0, 1024);
        }

        private static Exception UnwrapException(Exception exception)
        {
            var current = exception;
            while (current is TargetInvocationException invocation && invocation.InnerException != null)
            {
                current = invocation.InnerException;
            }
            return current;
        }
    }

    [VRCForgeCommand(
        toolId: "vrc_get_property",
        Summary = "Read a single serialized field/property value from a component on a scene GameObject (read-only).",
        Access = VRCForgeCommandAccess.ReadOnly
    )]
    public static class GetPropertyTool
    {
        public const string ToolName = "vrc_get_property";

        public class GetPropertyParameters
        {
            [VRCForgeInput("Full hierarchy path (e.g. 'Avatar/Body') or unique name of the GameObject.", IsRequired = true)]
            public string gameObjectPath { get; set; } = "";

            [VRCForgeInput("Component type. Fully-qualified (e.g. 'UnityEngine.SkinnedMeshRenderer') or unique short name.", IsRequired = true)]
            public string componentType { get; set; } = "";

            [VRCForgeInput("Field or property name to read (e.g. 'enabled', 'sharedMesh').", IsRequired = true)]
            public string propertyPath { get; set; } = "";

            [VRCForgeInput("Which component instance to read when several of the same type exist (default 0).", IsRequired = false)]
            public int? componentIndex { get; set; } = 0;

            [VRCForgeInput("Maximum collection items to return (1-2000, default 50). The result reports total/returned counts and truncation.", IsRequired = false)]
            public int? maxItems { get; set; } = 50;
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
                var maxItems = p.maxItems ?? 50;
                if (maxItems < 1 || maxItems > 2000)
                {
                    return VRCForgeToolResult.Failed("maxItems must be between 1 and 2000.");
                }
                var propertyValue = ComponentCrudCore.DescribeValue(
                    value,
                    maxItems,
                    out var returnedItemCount,
                    out var valueItemCount,
                    out var valueTruncated);

                var payload = new
                {
                    gameObjectPath = ComponentCrudCore.GetHierarchyPath(go.transform),
                    componentType = component.GetType().FullName,
                    componentIndex = p.componentIndex ?? 0,
                    propertyPath = p.propertyPath,
                    valueType = ComponentCrudCore.GetMemberType(member).FullName,
                    propertyValue,
                    returnedItemCount = returnedItemCount >= 0 ? (int?)returnedItemCount : null,
                    valueItemCount = valueItemCount >= 0 ? (int?)valueItemCount : null,
                    valueTruncated
                };

                return VRCForgeToolResult.Completed(
                    $"{component.GetType().Name}.{p.propertyPath} = {payload.propertyValue ?? "null"}",
                    payload);
            }
            catch (Exception ex)
            {
                return VRCForgeToolResult.Failed($"Get property failed: {ex.Message}");
            }
        }
    }

    [VRCForgeCommand(
        toolId: "vrc_inspect_skinned_mesh_deformation",
        Summary = "Bake a SkinnedMeshRenderer in memory and report finite deformation/AABB/outlier metrics (read-only).",
        Access = VRCForgeCommandAccess.ReadOnly
    )]
    public static class InspectSkinnedMeshDeformationTool
    {
        public const string ToolName = "vrc_inspect_skinned_mesh_deformation";

        public class InspectSkinnedMeshDeformationParameters
        {
            [VRCForgeInput("Full hierarchy path or unique name of the GameObject with a SkinnedMeshRenderer.", IsRequired = true)]
            public string gameObjectPath { get; set; } = "";

            [VRCForgeInput("Which SkinnedMeshRenderer instance to inspect when several exist (default 0).", IsRequired = false)]
            public int? componentIndex { get; set; } = 0;
        }

        private static bool Finite(Vector3 value)
        {
            return IsFinite(value.x) && IsFinite(value.y) && IsFinite(value.z);
        }

        private static bool IsFinite(float value)
        {
            return !float.IsNaN(value) && !float.IsInfinity(value);
        }

        private static object VectorPayload(Vector3 value)
        {
            return new { x = value.x, y = value.y, z = value.z };
        }

        private static object Aabb(Vector3[] vertices, Matrix4x4 transform, out int finiteCount, out float[] distances)
        {
            var finite = new List<Vector3>();
            for (var i = 0; i < (vertices ?? new Vector3[0]).Length; i++)
            {
                if (Finite(vertices[i])) finite.Add(vertices[i]);
            }
            finiteCount = finite.Count;
            if (finite.Count == 0)
            {
                distances = new float[0];
                return new { min = new { x = 0f, y = 0f, z = 0f }, max = new { x = 0f, y = 0f, z = 0f }, center = new { x = 0f, y = 0f, z = 0f }, size = new { x = 0f, y = 0f, z = 0f } };
            }
            var min = finite[0];
            var max = finite[0];
            var centroid = Vector3.zero;
            foreach (var vertex in finite)
            {
                min = Vector3.Min(min, vertex);
                max = Vector3.Max(max, vertex);
                centroid += vertex;
            }
            centroid /= finite.Count;
            distances = finite.Select(vertex => Vector3.Distance(vertex, centroid)).OrderBy(value => value).ToArray();
            return new
            {
                min = VectorPayload(min),
                max = VectorPayload(max),
                center = VectorPayload((min + max) * 0.5f),
                size = VectorPayload(max - min),
                centroid = VectorPayload(centroid)
            };
        }

        private static object DistanceSummary(float[] distances)
        {
            if (distances == null || distances.Length == 0)
            {
                return new { p50 = 0f, p95 = 0f, p99 = 0f, max = 0f };
            }
            float At(double percentile)
            {
                var index = Mathf.Clamp((int)Math.Ceiling(percentile * distances.Length) - 1, 0, distances.Length - 1);
                return distances[index];
            }
            return new { p50 = At(0.50), p95 = At(0.95), p99 = At(0.99), max = distances[distances.Length - 1] };
        }

        private static float[] RowMajor(Matrix4x4 matrix)
        {
            var values = new float[16];
            for (var row = 0; row < 4; row++)
                for (var column = 0; column < 4; column++)
                    values[row * 4 + column] = matrix[row, column];
            return values;
        }

        private static float MaxAbsDeviationFromIdentity(Matrix4x4 matrix)
        {
            var max = 0f;
            for (var row = 0; row < 4; row++)
                for (var column = 0; column < 4; column++)
                    max = Mathf.Max(max, Mathf.Abs(matrix[row, column] - (row == column ? 1f : 0f)));
            return max;
        }

        public static object HandleCommand(JObject @params)
        {
            var p = (@params ?? new JObject()).ToObject<InspectSkinnedMeshDeformationParameters>()
                ?? new InspectSkinnedMeshDeformationParameters();
            Mesh baked = null;
            try
            {
                var go = ComponentCrudCore.ResolveGameObject(p.gameObjectPath);
                var componentIndex = p.componentIndex ?? 0;
                var renderer = ComponentCrudCore.ResolveComponent(go, typeof(SkinnedMeshRenderer), componentIndex) as SkinnedMeshRenderer;
                if (renderer == null) throw new InvalidOperationException("The requested component is not a SkinnedMeshRenderer.");
                if (renderer.sharedMesh == null) throw new InvalidOperationException("The SkinnedMeshRenderer has no shared mesh.");
                baked = new Mesh { name = "VRCForge_DeformationReadback_Temporary" };
                renderer.BakeMesh(baked, false);
                var restVertices = renderer.sharedMesh.vertices ?? new Vector3[0];
                var playVertices = baked.vertices ?? new Vector3[0];
                int restFiniteCount, playFiniteCount, worldFiniteCount;
                float[] restDistances, playDistances, worldDistances;
                var restAabb = Aabb(restVertices, Matrix4x4.identity, out restFiniteCount, out restDistances);
                var playAabb = Aabb(playVertices, Matrix4x4.identity, out playFiniteCount, out playDistances);
                var worldVertices = playVertices.Select(renderer.transform.localToWorldMatrix.MultiplyPoint3x4).ToArray();
                var worldAabb = Aabb(worldVertices, Matrix4x4.identity, out worldFiniteCount, out worldDistances);
                var rendererBones = renderer.bones ?? new Transform[0];
                var bindposes = renderer.sharedMesh.bindposes ?? new Matrix4x4[0];
                var translations = new List<float>();
                var deviations = new List<float>();
                var determinants = new List<float>();
                for (var i = 0; i < rendererBones.Length && i < bindposes.Length; i++)
                {
                    var bone = rendererBones[i];
                    if (bone == null) continue;
                    var reconstructed = renderer.transform.worldToLocalMatrix * bone.localToWorldMatrix * bindposes[i];
                    translations.Add(new Vector3(reconstructed.m03, reconstructed.m13, reconstructed.m23).magnitude);
                    deviations.Add(MaxAbsDeviationFromIdentity(reconstructed));
                    determinants.Add(reconstructed.determinant);
                }
                var metricCount = translations.Count;
                return VRCForgeToolResult.Completed(
                    $"Baked deformation metrics for '{renderer.sharedMesh.name}' without modifying assets.",
                    new
                    {
                        action = "inspect_skinned_mesh_deformation",
                        gameObjectPath = ComponentCrudCore.GetHierarchyPath(go.transform),
                        componentIndex,
                        meshName = renderer.sharedMesh.name,
                        currentPlayMode = Application.isPlaying,
                        rest = new { vertexCount = restVertices.Length, finiteVertexCount = restFiniteCount, aabb = restAabb, distanceSummary = DistanceSummary(restDistances) },
                        play = new { vertexCount = playVertices.Length, finiteVertexCount = playFiniteCount, aabb = playAabb, distanceSummary = DistanceSummary(playDistances) },
                        world = new { finiteVertexCount = worldFiniteCount, aabb = worldAabb, distanceSummary = DistanceSummary(worldDistances) },
                        usedBoneReconstructedSkinMatrix = new
                        {
                            sampleCount = metricCount,
                            maxTranslationMagnitude = translations.Count == 0 ? 0f : translations.Max(),
                            maxAbsDeviation = deviations.Count == 0 ? 0f : deviations.Max(),
                            determinantMin = determinants.Count == 0 ? 1f : determinants.Min(),
                            determinantMax = determinants.Count == 0 ? 1f : determinants.Max()
                        }
                    });
            }
            catch (Exception ex)
            {
                return VRCForgeToolResult.FailedWithCode("skinned_mesh_deformation_inspection_failed", $"Inspect skinned mesh deformation failed: {ex.Message}");
            }
            finally
            {
                if (baked != null) UnityEngine.Object.DestroyImmediate(baked);
            }
        }
    }

    [VRCForgeCommand(
        toolId: "vrc_remap_skinned_mesh_bone",
        Summary = "Replace one explicitly weighted SkinnedMeshRenderer bone slot with an exact target Transform; preserves mesh, bindposes, and rootBone.")]
    public static class RemapSkinnedMeshBoneTool
    {
        public const string ToolName = "vrc_remap_skinned_mesh_bone";

        public class Parameters
        {
            [VRCForgeInput("Full hierarchy path of the GameObject with the SkinnedMeshRenderer.", IsRequired = true)] public string gameObjectPath { get; set; } = "";
            [VRCForgeInput("Renderer component index (default 0).", IsRequired = false)] public int? componentIndex { get; set; } = 0;
            [VRCForgeInput("Exact zero-based renderer bones[] index to replace.", IsRequired = true)] public int? boneIndex { get; set; }
            [VRCForgeInput("Exact sharedMesh.name expected on the renderer before replacement.", IsRequired = true)] public string expectedMeshName { get; set; } = "";
            [VRCForgeInput("Exact current bone hierarchy path required before replacement.", IsRequired = true)] public string expectedCurrentBonePath { get; set; } = "";
            [VRCForgeInput("Exact target Transform hierarchy path.", IsRequired = true)] public string targetBonePath { get; set; } = "";
            [VRCForgeInput("Only validate and return the planned replacement.", IsRequired = false)] public bool? preview { get; set; } = false;
        }

        private static bool HasPositiveWeightAt(Mesh mesh, int boneIndex)
        {
            var bonesPerVertex = mesh.GetBonesPerVertex();
            var weights = mesh.GetAllBoneWeights();
            try
            {
                var cursor = 0;
                for (var vertex = 0; vertex < bonesPerVertex.Length; vertex++)
                {
                    for (var influence = 0; influence < bonesPerVertex[vertex]; influence++)
                    {
                        var weight = weights[cursor++];
                        if (weight.boneIndex == boneIndex && weight.weight > 0f) return true;
                    }
                }
                return false;
            }
            finally
            {
                if (bonesPerVertex.IsCreated) bonesPerVertex.Dispose();
                if (weights.IsCreated) weights.Dispose();
            }
        }

        private static bool MatricesExactlyEqual(Matrix4x4[] left, Matrix4x4[] right)
        {
            if (left == null || right == null || left.Length != right.Length) return left == right;
            for (var index = 0; index < left.Length; index++)
            {
                for (var row = 0; row < 4; row++)
                {
                    for (var column = 0; column < 4; column++)
                    {
                        if (left[index][row, column] != right[index][row, column]) return false;
                    }
                }
            }
            return true;
        }

        private static string ObjectId(UnityEngine.Object value)
        {
            return value == null ? string.Empty : GlobalObjectId.GetGlobalObjectIdSlow(value).ToString();
        }

        private static float[] RowMajor(Matrix4x4 matrix)
        {
            var values = new float[16];
            for (var row = 0; row < 4; row++)
            {
                for (var column = 0; column < 4; column++)
                {
                    values[row * 4 + column] = matrix[row, column];
                }
            }
            return values;
        }

        private static float MaxAbsDeviationFromIdentity(Matrix4x4 matrix)
        {
            var max = 0f;
            for (var row = 0; row < 4; row++)
            {
                for (var column = 0; column < 4; column++)
                {
                    max = Mathf.Max(max, Mathf.Abs(matrix[row, column] - (row == column ? 1f : 0f)));
                }
            }
            return max;
        }

        private static object SkinningMetrics(SkinnedMeshRenderer renderer, Transform bone, Matrix4x4 bindpose)
        {
            var reconstructed = renderer.transform.worldToLocalMatrix * bone.localToWorldMatrix * bindpose;
            var maxAbsDeviation = MaxAbsDeviationFromIdentity(reconstructed);
            return new
            {
                rendererWorldToLocal = RowMajor(renderer.transform.worldToLocalMatrix),
                boneLocalToWorld = RowMajor(bone.localToWorldMatrix),
                bindpose = RowMajor(bindpose),
                reconstructedSkinMatrix = RowMajor(reconstructed),
                translationMagnitude = new Vector3(reconstructed.m03, reconstructed.m13, reconstructed.m23).magnitude,
                maxAbsDeviation,
                determinant = reconstructed.determinant,
                nearIdentity = maxAbsDeviation <= 0.001f
            };
        }

        public static object HandleCommand(JObject @params)
        {
            var p = (@params ?? new JObject()).ToObject<Parameters>() ?? new Parameters();
            SavedSceneSnapshot beforeScene = null;
            var undoGroup = -1;
            var mutationStarted = false;
            var mutationApplied = false;
            var failureStage = "validation";
            var rendererPath = string.Empty;
            SkinnedMeshRenderer renderer = null;
            Transform oldBone = null;
            Transform targetBone = null;
            Matrix4x4[] originalBindposes = null;
            Transform originalRootBone = null;
            Mesh originalMesh = null;
            try
            {
                rendererPath = ComponentCrudCore.NormalizePath(p.gameObjectPath);
                if (string.IsNullOrWhiteSpace(rendererPath) || string.IsNullOrWhiteSpace(p.expectedMeshName) || string.IsNullOrWhiteSpace(p.expectedCurrentBonePath) || string.IsNullOrWhiteSpace(p.targetBonePath))
                {
                    return VRCForgeToolResult.RejectedBeforeMutation("skinned_bone_remap_argument_missing", "gameObjectPath, expectedMeshName, expectedCurrentBonePath, and targetBonePath are required.", "unity_editor_tool", "validation");
                }
                var index = p.boneIndex ?? -1;
                var componentIndex = p.componentIndex ?? 0;
                if (index < 0 || componentIndex < 0) return VRCForgeToolResult.RejectedBeforeMutation("skinned_bone_remap_index_invalid", "boneIndex and componentIndex must be non-negative.", "unity_editor_tool", "validation");
                var go = ComponentCrudCore.ResolveGameObject(rendererPath);
                if (!string.Equals(ComponentCrudCore.GetHierarchyPath(go.transform), rendererPath, StringComparison.Ordinal))
                {
                    throw new InvalidOperationException("gameObjectPath must be the exact full hierarchy path; leaf-name fallback is not allowed.");
                }
                renderer = ComponentCrudCore.ResolveComponent(go, typeof(SkinnedMeshRenderer), componentIndex) as SkinnedMeshRenderer;
                if (renderer == null) throw new InvalidOperationException("The requested component is not a SkinnedMeshRenderer.");
                var mesh = renderer.sharedMesh;
                if (mesh == null) throw new InvalidOperationException("The SkinnedMeshRenderer has no shared mesh.");
                if (!string.Equals(mesh.name, p.expectedMeshName.Trim(), StringComparison.Ordinal))
                {
                    throw new InvalidOperationException($"expectedMeshName did not match the live sharedMesh.name ('{mesh.name}').");
                }
                var bones = renderer.bones ?? Array.Empty<Transform>();
                var bindposes = mesh.bindposes ?? Array.Empty<Matrix4x4>();
                if (index >= bones.Length) throw new InvalidOperationException($"boneIndex {index} is outside renderer bones[] length {bones.Length}.");
                if (index >= bindposes.Length) throw new InvalidOperationException($"boneIndex {index} has no corresponding bindpose.");
                if (!HasPositiveWeightAt(mesh, index)) throw new InvalidOperationException($"boneIndex {index} has no positive mesh weight; refusing an unused-slot remap.");
                oldBone = bones[index];
                var expectedPath = ComponentCrudCore.NormalizePath(p.expectedCurrentBonePath);
                if (oldBone == null || !string.Equals(ComponentCrudCore.GetHierarchyPath(oldBone), expectedPath, StringComparison.Ordinal))
                {
                    throw new InvalidOperationException($"expectedCurrentBonePath did not match the live bone at index {index}.");
                }
                var targetPath = ComponentCrudCore.NormalizePath(p.targetBonePath);
                targetBone = ComponentCrudCore.ResolveGameObject(targetPath).transform;
                if (!string.Equals(ComponentCrudCore.GetHierarchyPath(targetBone), targetPath, StringComparison.Ordinal))
                {
                    throw new InvalidOperationException("targetBonePath must be the exact full hierarchy path; leaf-name fallback is not allowed.");
                }
                if (targetBone.gameObject.scene != go.scene)
                {
                    throw new InvalidOperationException("targetBonePath must resolve inside the renderer's loaded scene.");
                }
                if (targetBone == null || targetBone == oldBone) throw new InvalidOperationException("targetBonePath must resolve to a different Transform.");
                beforeScene = ComponentCrudCore.ResolveSavedSceneFor(go);
                originalBindposes = bindposes.ToArray();
                originalRootBone = renderer.rootBone;
                originalMesh = mesh;
                if (p.preview ?? false)
                {
                    return VRCForgeToolResult.Completed("Preview: validated one positively weighted SkinnedMeshRenderer bone remap.", new
                    {
                        action = "remap_skinned_mesh_bone", preview = true, gameObjectPath = rendererPath, componentIndex,
                        boneIndex = index, expectedMeshName = p.expectedMeshName.Trim(), expectedCurrentBonePath = expectedPath, targetBonePath = ComponentCrudCore.GetHierarchyPath(targetBone),
                        meshName = mesh.name, bindposeCount = bindposes.Length, rendererBoneCount = bones.Length,
                        currentSkinningMetrics = SkinningMetrics(renderer, oldBone, bindposes[index]),
                        targetSkinningMetrics = SkinningMetrics(renderer, targetBone, bindposes[index]),
                        preservesMesh = true, preservesBindposes = true, preservesRootBone = true
                    });
                }

                Undo.IncrementCurrentGroup();
                undoGroup = Undo.GetCurrentGroup();
                Undo.SetCurrentGroupName("Remap VRCForge SkinnedMeshRenderer bone");
                Undo.RecordObject(renderer, "Remap VRCForge SkinnedMeshRenderer bone");
                mutationStarted = true;
                failureStage = "unity_mutation";
                var replacement = bones.ToArray();
                replacement[index] = targetBone;
                renderer.bones = replacement;
                mutationApplied = true;
                EditorUtility.SetDirty(renderer);
                EditorUtility.SetDirty(go);
                failureStage = "scene_save";
                var afterScene = ComponentCrudCore.SaveAndResolveScene(beforeScene);
                failureStage = "persisted_readback";
                var readbackObject = SceneObjectCopyCore.ResolveUniqueGameObject(afterScene.Scene, rendererPath, "skinned mesh bone remap target");
                var readbackRenderer = ComponentCrudCore.ResolveComponent(readbackObject, typeof(SkinnedMeshRenderer), componentIndex) as SkinnedMeshRenderer;
                var readbackBones = readbackRenderer.bones ?? Array.Empty<Transform>();
                if (readbackBones.Length != bones.Length
                    || ObjectId(readbackBones[index]) != ObjectId(targetBone)
                    || ObjectId(readbackRenderer.rootBone) != ObjectId(originalRootBone)
                    || ObjectId(readbackRenderer.sharedMesh) != ObjectId(originalMesh)
                    || !MatricesExactlyEqual(readbackRenderer.sharedMesh.bindposes, originalBindposes)
                    || afterScene.FileDigest == beforeScene.FileDigest)
                {
                    throw new InvalidOperationException("The remapped bone did not pass exact persisted readback or an invariant changed.");
                }
                Undo.CollapseUndoOperations(undoGroup);
                return VRCForgeToolResult.Completed("Remapped one positively weighted SkinnedMeshRenderer bone slot.", new
                {
                    action = "remap_skinned_mesh_bone", preview = false, changed = true, gameObjectPath = rendererPath, componentIndex,
                     boneIndex = index, expectedMeshName = p.expectedMeshName.Trim(), expectedCurrentBonePath = expectedPath, targetBonePath = ComponentCrudCore.GetHierarchyPath(targetBone),
                     meshName = originalMesh.name, bindposeCount = originalBindposes.Length, rendererBoneCount = readbackBones.Length,
                     currentSkinningMetrics = SkinningMetrics(renderer, oldBone, originalBindposes[index]),
                     targetSkinningMetrics = SkinningMetrics(readbackRenderer, readbackBones[index], originalBindposes[index]),
                     preservesMesh = true, preservesBindposes = true, preservesRootBone = true, scenePath = afterScene.Path,
                    sceneSaved = true, persistedReadback = true, mutationStarted = true, committed = true, commitState = "committed",
                    checkpointRecoveryRequired = false
                });
            }
            catch (Exception ex)
            {
                if (mutationStarted && beforeScene != null && undoGroup >= 0)
                {
                    var cleanup = ComponentCrudCore.RestoreFailedMutation(
                        undoGroup, beforeScene, rendererPath,
                        restoredObject =>
                        {
                            var restored = ComponentCrudCore.ResolveComponent(restoredObject, typeof(SkinnedMeshRenderer), p.componentIndex ?? 0) as SkinnedMeshRenderer;
                            var restoredBones = restored.bones ?? Array.Empty<Transform>();
                            return restoredBones.Length > (p.boneIndex ?? -1)
                                && ObjectId(restoredBones[p.boneIndex ?? -1]) == ObjectId(oldBone)
                                && ObjectId(restored.rootBone) == ObjectId(originalRootBone)
                                && ObjectId(restored.sharedMesh) == ObjectId(originalMesh)
                                && MatricesExactlyEqual(restored.sharedMesh.bindposes, originalBindposes);
                        });
                    return ComponentCrudCore.FailedMutationResult("skinned_bone_remap_failed_after_mutation", "remap_skinned_mesh_bone", failureStage, ex, beforeScene, cleanup, mutationApplied);
                }
                return VRCForgeToolResult.RejectedBeforeMutation("skinned_bone_remap_rejected_before_mutation", $"Skinned mesh bone remap failed: {ex.Message}", "unity_editor_tool", failureStage);
            }
        }
    }

    [VRCForgeCommand(
        toolId: "vrc_inspect_skinned_mesh_bone_usage",
        Summary = "Inspect which SkinnedMeshRenderer bone-array indices are referenced by non-zero mesh weights (read-only).",
        Access = VRCForgeCommandAccess.ReadOnly
    )]
    public static class InspectSkinnedMeshBoneUsageTool
    {
        public const string ToolName = "vrc_inspect_skinned_mesh_bone_usage";

        public class InspectSkinnedMeshBoneUsageParameters
        {
            [VRCForgeInput("Full hierarchy path or unique name of the GameObject with a SkinnedMeshRenderer.", IsRequired = true)]
            public string gameObjectPath { get; set; } = "";

            [VRCForgeInput("Which SkinnedMeshRenderer instance to inspect when several exist (default 0).", IsRequired = false)]
            public int? componentIndex { get; set; } = 0;

            [VRCForgeInput("Minimum positive bone weight to count as used (0 to 1, default 0.000001).", IsRequired = false)]
            public float? minimumWeight { get; set; } = 0.000001f;
        }

        private sealed class BoneUsageAccumulator
        {
            public int InfluenceCount;
            public double TotalWeight;
            public float MaxWeight;
        }

        private static float[] RowMajor(Matrix4x4 matrix)
        {
            var values = new float[16];
            for (var row = 0; row < 4; row++)
            {
                for (var column = 0; column < 4; column++)
                {
                    values[row * 4 + column] = matrix[row, column];
                }
            }
            return values;
        }

        private static float MaxAbsDeviationFromIdentity(Matrix4x4 matrix)
        {
            var max = 0f;
            for (var row = 0; row < 4; row++)
            {
                for (var column = 0; column < 4; column++)
                {
                    max = Mathf.Max(max, Mathf.Abs(matrix[row, column] - (row == column ? 1f : 0f)));
                }
            }
            return max;
        }

        public static object HandleCommand(JObject @params)
        {
            var p = (@params ?? new JObject()).ToObject<InspectSkinnedMeshBoneUsageParameters>()
                ?? new InspectSkinnedMeshBoneUsageParameters();
            try
            {
                var go = ComponentCrudCore.ResolveGameObject(p.gameObjectPath);
                var componentIndex = p.componentIndex ?? 0;
                var renderer = ComponentCrudCore.ResolveComponent(
                    go,
                    typeof(SkinnedMeshRenderer),
                    componentIndex) as SkinnedMeshRenderer;
                if (renderer == null)
                {
                    throw new InvalidOperationException("The requested component is not a SkinnedMeshRenderer.");
                }
                var mesh = renderer.sharedMesh;
                if (mesh == null)
                {
                    throw new InvalidOperationException("The SkinnedMeshRenderer has no shared mesh.");
                }
                var minimumWeight = p.minimumWeight ?? 0.000001f;
                if (float.IsNaN(minimumWeight)
                    || float.IsInfinity(minimumWeight)
                    || minimumWeight < 0.0f
                    || minimumWeight > 1.0f)
                {
                    throw new InvalidOperationException("minimumWeight must be between 0 and 1.");
                }

                var rendererBones = renderer.bones ?? new Transform[0];
                var usage = new Dictionary<int, BoneUsageAccumulator>();
                var outOfRangeWeightCount = 0;
                var bonesPerVertex = mesh.GetBonesPerVertex();
                var allWeights = mesh.GetAllBoneWeights();
                try
                {
                    var weightIndex = 0;
                    for (var vertexIndex = 0; vertexIndex < bonesPerVertex.Length; vertexIndex++)
                    {
                        var influenceCount = bonesPerVertex[vertexIndex];
                        for (var influenceIndex = 0; influenceIndex < influenceCount; influenceIndex++)
                        {
                            var weight = allWeights[weightIndex++];
                            if (weight.weight <= minimumWeight)
                            {
                                continue;
                            }
                            if (weight.boneIndex < 0 || weight.boneIndex >= rendererBones.Length)
                            {
                                outOfRangeWeightCount++;
                                continue;
                            }
                            BoneUsageAccumulator accumulator;
                            if (!usage.TryGetValue(weight.boneIndex, out accumulator))
                            {
                                accumulator = new BoneUsageAccumulator();
                                usage.Add(weight.boneIndex, accumulator);
                            }
                            accumulator.InfluenceCount++;
                            accumulator.TotalWeight += weight.weight;
                            accumulator.MaxWeight = Mathf.Max(accumulator.MaxWeight, weight.weight);
                        }
                    }
                    if (weightIndex != allWeights.Length)
                    {
                        throw new InvalidOperationException(
                            $"Mesh bone-weight traversal consumed {weightIndex} of {allWeights.Length} entries.");
                    }
                }
                finally
                {
                    if (bonesPerVertex.IsCreated)
                    {
                        bonesPerVertex.Dispose();
                    }
                    if (allWeights.IsCreated)
                    {
                        allWeights.Dispose();
                    }
                }

                var usedBones = usage
                    .OrderBy(item => item.Key)
                    .Select(item =>
                    {
                        var bone = rendererBones[item.Key];
                        var bindposes = mesh.bindposes ?? new Matrix4x4[0];
                        var bindposeExists = item.Key < bindposes.Length;
                        var bindpose = bindposeExists ? bindposes[item.Key] : Matrix4x4.identity;
                        var rendererWorldToLocal = renderer.transform.worldToLocalMatrix;
                        var boneLocalToWorld = bone != null ? bone.localToWorldMatrix : Matrix4x4.identity;
                        var reconstructed = rendererWorldToLocal * boneLocalToWorld * bindpose;
                        return new
                        {
                            index = item.Key,
                            name = bone != null ? bone.name : null,
                            gameObjectPath = bone != null ? ComponentCrudCore.GetHierarchyPath(bone) : null,
                            instanceId = bone != null ? bone.GetInstanceID() : 0,
                            missingTransform = bone == null,
                            influenceCount = item.Value.InfluenceCount,
                            totalWeight = item.Value.TotalWeight,
                            maxWeight = item.Value.MaxWeight
                            ,bindposeExists
                            ,bindpose = RowMajor(bindpose)
                            ,rendererLocalToWorld = RowMajor(renderer.transform.localToWorldMatrix)
                            ,rendererWorldToLocal = RowMajor(rendererWorldToLocal)
                            ,boneLocalToWorld = RowMajor(boneLocalToWorld)
                            ,reconstructedSkinMatrix = RowMajor(reconstructed)
                            ,translationMagnitude = new Vector3(reconstructed.m03, reconstructed.m13, reconstructed.m23).magnitude
                            ,maxAbsDeviation = MaxAbsDeviationFromIdentity(reconstructed)
                            ,determinant = reconstructed.determinant
                            ,nearIdentity = bindposeExists && bone != null && MaxAbsDeviationFromIdentity(reconstructed) <= 0.001f
                        };
                    })
                    .ToArray();
                var usedIndices = new HashSet<int>(usage.Keys);
                var unusedBoundBoneCount = rendererBones
                    .Select((bone, index) => new { bone, index })
                    .Count(item => item.bone != null && !usedIndices.Contains(item.index));
                var nullBoneCount = rendererBones.Count(bone => bone == null);
                var rootBone = renderer.rootBone;
                var bindposeCount = mesh.bindposes != null ? mesh.bindposes.Length : 0;
                var usedNullBoneCount = usedBones.Count(item => item.missingTransform);
                var allUsedBonesResolved = usedNullBoneCount == 0;
                var allUsedBindposesResolved = usage.Keys.All(index => index >= 0 && index < bindposeCount);
                var payload = new
                {
                    action = "inspect_skinned_mesh_bone_usage",
                    gameObjectPath = ComponentCrudCore.GetHierarchyPath(go.transform),
                    componentIndex,
                    meshName = mesh.name,
                    vertexCount = mesh.vertexCount,
                    rendererBoneCount = rendererBones.Length,
                    bindposeCount,
                    rendererLocalToWorld = RowMajor(renderer.transform.localToWorldMatrix),
                    rendererWorldToLocal = RowMajor(renderer.transform.worldToLocalMatrix),
                    capability = new
                    {
                        complete = rendererBones.Length == bindposeCount
                            && rendererBones.All(bone => bone != null)
                            && outOfRangeWeightCount == 0,
                        bonesPresent = rendererBones.Length > 0,
                        bindposesPresent = bindposeCount > 0,
                        boneBindposeCountParity = rendererBones.Length == bindposeCount,
                        allRendererBonesResolved = rendererBones.All(bone => bone != null),
                        noOutOfRangeWeights = outOfRangeWeightCount == 0,
                        usedNullBoneCount,
                        allUsedBonesResolved,
                        allUsedBindposesResolved,
                        mixedChainClosure = rootBone != null
                            && usedBones.All(item => !item.missingTransform
                                && rendererBones[item.index] != null
                                && (rendererBones[item.index] == rootBone || rendererBones[item.index].IsChildOf(rootBone)))
                        ,safeForWeightedRemap = rendererBones.Length == bindposeCount
                            && bindposeCount > 0
                            && allUsedBonesResolved
                            && allUsedBindposesResolved
                            && outOfRangeWeightCount == 0
                            && rootBone != null
                            && usedBones.All(item => !item.missingTransform
                                && (rendererBones[item.index] == rootBone || rendererBones[item.index].IsChildOf(rootBone)))
                    },
                    usedBoneCount = usedBones.Length,
                    usedNullBoneCount,
                    allUsedBonesResolved,
                    allUsedBindposesResolved,
                    unusedBoundBoneCount,
                    nullBoneCount,
                    outOfRangeWeightCount,
                    minimumWeight,
                    rootBonePath = rootBone != null ? ComponentCrudCore.GetHierarchyPath(rootBone) : null,
                    usedBones
                };
                return VRCForgeToolResult.Completed(
                    $"Skinned mesh '{mesh.name}' uses {usedBones.Length} of {rendererBones.Length} renderer bone slots.",
                    payload);
            }
            catch (Exception ex)
            {
                return VRCForgeToolResult.FailedWithCode(
                    "skinned_mesh_bone_usage_inspection_failed",
                    $"Inspect skinned mesh bone usage failed: {ex.Message}");
            }
        }
    }

    [VRCForgeCommand(
        toolId: "vrc_add_component",
        Summary = "Add a component of a given type to a scene GameObject (Undo-registered). Supports preview mode."
    )]
    public static class AddComponentTool
    {
        public const string ToolName = "vrc_add_component";

        public class AddComponentParameters
        {
            [VRCForgeInput("Full hierarchy path or unique name of the target GameObject.", IsRequired = true)]
            public string gameObjectPath { get; set; } = "";

            [VRCForgeInput("Component type to add. Fully-qualified or unique short name.", IsRequired = true)]
            public string componentType { get; set; } = "";

            [VRCForgeInput("If true, only report what would happen without mutating the scene (default false).", IsRequired = false)]
            public bool? preview { get; set; } = false;
        }

        public static object HandleCommand(JObject @params)
        {
            var p = (@params ?? new JObject()).ToObject<AddComponentParameters>() ?? new AddComponentParameters();
            SavedSceneSnapshot beforeScene = null;
            var undoGroup = -1;
            var mutationStarted = false;
            var goPath = string.Empty;
            Type type = null;
            var existing = 0;
            var failureStage = "validation";
            bool? mutationApplied = null;
            try
            {
                var go = ComponentCrudCore.ResolveGameObject(p.gameObjectPath);
                type = ComponentCrudCore.ResolveComponentType(p.componentType);
                goPath = ComponentCrudCore.GetHierarchyPath(go.transform);
                existing = go.GetComponents(type).Length;
                beforeScene = ComponentCrudCore.ResolveSavedSceneFor(go);
                var objectId = GlobalObjectId.GetGlobalObjectIdSlow(go).ToString();

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
                    return VRCForgeToolResult.Completed(
                        $"Preview: would add '{type.Name}' to '{goPath}' (currently {existing} of this type).",
                        previewPayload);
                }

                Undo.IncrementCurrentGroup();
                undoGroup = Undo.GetCurrentGroup();
                Undo.SetCurrentGroupName("Add VRCForge component");
                mutationStarted = true;
                failureStage = "unity_mutation";
                var added = Undo.AddComponent(go, type);
                if (added == null)
                {
                    throw new InvalidOperationException(
                        $"Unity refused to add '{type.Name}' (missing dependency or disallowed type).");
                }
                mutationApplied = true;
                EditorUtility.SetDirty(go);
                EditorUtility.SetDirty(added);
                failureStage = "scene_save";
                var afterScene = ComponentCrudCore.SaveAndResolveScene(beforeScene);
                failureStage = "persisted_readback";
                var readback = SceneObjectCopyCore.ResolveUniqueGameObject(
                    afterScene.Scene,
                    goPath,
                    "added component target");
                var readbackComponents = readback.GetComponents(type);
                if (GlobalObjectId.GetGlobalObjectIdSlow(readback).ToString() != objectId
                    || readbackComponents.Length != existing + 1
                    || afterScene.FileDigest == beforeScene.FileDigest)
                {
                    throw new InvalidOperationException("The added component persisted readback was not exact.");
                }
                Undo.CollapseUndoOperations(undoGroup);

                var payload = new
                {
                    action = "add_component",
                    preview = false,
                    gameObjectPath = goPath,
                    componentType = type.FullName,
                    componentIndex = readbackComponents.Length - 1,
                    instanceId = added.GetInstanceID(),
                    scenePath = afterScene.Path,
                    sceneSaved = true,
                    persistedReadback = true,
                    mutationStarted = true,
                    committed = true,
                    commitState = "committed",
                    checkpointRecoveryRequired = false
                };
                return VRCForgeToolResult.Completed($"Added '{type.Name}' to '{goPath}'.", payload);
            }
            catch (Exception ex)
            {
                if (mutationStarted && beforeScene != null && undoGroup >= 0)
                {
                    var cleanup = ComponentCrudCore.RestoreFailedMutation(
                        undoGroup,
                        beforeScene,
                        goPath,
                        restoredObject => restoredObject.GetComponents(type).Length == existing);
                    return ComponentCrudCore.FailedMutationResult(
                        "component_add_failed_after_mutation",
                        "add_component",
                        failureStage,
                        ex,
                        beforeScene,
                        cleanup,
                        mutationApplied);
                }
                return VRCForgeToolResult.Failed($"Add component failed: {ex.Message}");
            }
        }
    }

    [VRCForgeCommand(
        toolId: "vrc_remove_component",
        Summary = "Remove a component of a given type from a scene GameObject (Undo-registered). Supports preview mode."
    )]
    public static class RemoveComponentTool
    {
        public const string ToolName = "vrc_remove_component";

        public class RemoveComponentParameters
        {
            [VRCForgeInput("Full hierarchy path or unique name of the target GameObject.", IsRequired = true)]
            public string gameObjectPath { get; set; } = "";

            [VRCForgeInput("Component type to remove. Fully-qualified or unique short name.", IsRequired = true)]
            public string componentType { get; set; } = "";

            [VRCForgeInput("Which component instance to remove when several of the same type exist (default 0).", IsRequired = false)]
            public int? componentIndex { get; set; } = 0;

            [VRCForgeInput("If true, only report what would happen without mutating the scene (default false).", IsRequired = false)]
            public bool? preview { get; set; } = false;
        }

        public static object HandleCommand(JObject @params)
        {
            var p = (@params ?? new JObject()).ToObject<RemoveComponentParameters>() ?? new RemoveComponentParameters();
            SavedSceneSnapshot beforeScene = null;
            var undoGroup = -1;
            var mutationStarted = false;
            var goPath = string.Empty;
            Type type = null;
            var existing = 0;
            var failureStage = "validation";
            bool? mutationApplied = null;
            try
            {
                var go = ComponentCrudCore.ResolveGameObject(p.gameObjectPath);
                type = ComponentCrudCore.ResolveComponentType(p.componentType);
                goPath = ComponentCrudCore.GetHierarchyPath(go.transform);
                var index = p.componentIndex ?? 0;
                var component = ComponentCrudCore.ResolveComponent(go, type, index);
                existing = go.GetComponents(type).Length;
                beforeScene = ComponentCrudCore.ResolveSavedSceneFor(go);
                var objectId = GlobalObjectId.GetGlobalObjectIdSlow(go).ToString();

                if (component is Transform)
                {
                    return VRCForgeToolResult.Failed("Refusing to remove a Transform component; every GameObject requires one.");
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
                    return VRCForgeToolResult.Completed(
                        $"Preview: would remove '{component.GetType().Name}' (index {index}) from '{goPath}'.",
                        previewPayload);
                }

                Undo.IncrementCurrentGroup();
                undoGroup = Undo.GetCurrentGroup();
                Undo.SetCurrentGroupName("Remove VRCForge component");
                var removedType = component.GetType().FullName;
                mutationStarted = true;
                failureStage = "unity_mutation";
                Undo.DestroyObjectImmediate(component);
                mutationApplied = true;
                EditorUtility.SetDirty(go);
                failureStage = "scene_save";
                var afterScene = ComponentCrudCore.SaveAndResolveScene(beforeScene);
                failureStage = "persisted_readback";
                var readback = SceneObjectCopyCore.ResolveUniqueGameObject(
                    afterScene.Scene,
                    goPath,
                    "removed component target");
                if (GlobalObjectId.GetGlobalObjectIdSlow(readback).ToString() != objectId
                    || readback.GetComponents(type).Length != existing - 1
                    || afterScene.FileDigest == beforeScene.FileDigest)
                {
                    throw new InvalidOperationException("The removed component persisted readback was not exact.");
                }
                Undo.CollapseUndoOperations(undoGroup);

                var payload = new
                {
                    action = "remove_component",
                    preview = false,
                    gameObjectPath = goPath,
                    componentType = removedType,
                    componentIndex = index,
                    scenePath = afterScene.Path,
                    sceneSaved = true,
                    persistedReadback = true,
                    mutationStarted = true,
                    committed = true,
                    commitState = "committed",
                    checkpointRecoveryRequired = false
                };
                return VRCForgeToolResult.Completed($"Removed '{type.Name}' (index {index}) from '{goPath}'.", payload);
            }
            catch (Exception ex)
            {
                if (mutationStarted && beforeScene != null && undoGroup >= 0)
                {
                    var cleanup = ComponentCrudCore.RestoreFailedMutation(
                        undoGroup,
                        beforeScene,
                        goPath,
                        restoredObject => restoredObject.GetComponents(type).Length == existing);
                    return ComponentCrudCore.FailedMutationResult(
                        "component_remove_failed_after_mutation",
                        "remove_component",
                        failureStage,
                        ex,
                        beforeScene,
                        cleanup,
                        mutationApplied);
                }
                return VRCForgeToolResult.Failed($"Remove component failed: {ex.Message}");
            }
        }
    }

    [VRCForgeCommand(
        toolId: "vrc_set_property",
        Summary = "Set a single field/property on a component of a scene GameObject (Undo-registered). Supports preview mode."
    )]
    public static class SetPropertyTool
    {
        public const string ToolName = "vrc_set_property";

        public class SetPropertyParameters
        {
            [VRCForgeInput("Full hierarchy path or unique name of the target GameObject.", IsRequired = true)]
            public string gameObjectPath { get; set; } = "";

            [VRCForgeInput("Component type. Fully-qualified or unique short name.", IsRequired = true)]
            public string componentType { get; set; } = "";

            [VRCForgeInput("Field or property name to set (e.g. 'enabled', 'm_Weight').", IsRequired = true)]
            public string propertyPath { get; set; } = "";

            [VRCForgeInput("JSON value to assign to the field or property.", IsRequired = true)]
            public object value { get; set; }

            [VRCForgeInput("Which component instance to target when several of the same type exist (default 0).", IsRequired = false)]
            public int? componentIndex { get; set; } = 0;

            [VRCForgeInput("If true, only report what would happen without mutating the scene (default false).", IsRequired = false)]
            public bool? preview { get; set; } = false;
        }

        public static object HandleCommand(JObject @params)
        {
            var p = (@params ?? new JObject()).ToObject<SetPropertyParameters>() ?? new SetPropertyParameters();
            SavedSceneSnapshot beforeScene = null;
            var undoGroup = -1;
            var mutationStarted = false;
            var goPath = string.Empty;
            Type type = null;
            MemberInfo member = null;
            object oldValue = null;
            var componentIndex = p.componentIndex ?? 0;
            var failureStage = "validation";
            bool? mutationApplied = null;
            try
            {
                var rawParams = @params ?? new JObject();
                if (rawParams["value"] == null)
                {
                    return VRCForgeToolResult.RejectedBeforeMutation(
                        "component_property_value_missing",
                        "Set property requires a 'value' argument.",
                        "unity_editor_tool",
                        "validation",
                        details: new
                        {
                            action = "set_property",
                            requiredArgument = "value"
                        });
                }
                var valueToken = rawParams["value"];

                var go = ComponentCrudCore.ResolveGameObject(p.gameObjectPath);
                type = ComponentCrudCore.ResolveComponentType(p.componentType);
                var component = ComponentCrudCore.ResolveComponent(go, type, componentIndex);
                member = ComponentCrudCore.ResolveMember(component.GetType(), p.propertyPath);
                var memberType = ComponentCrudCore.GetMemberType(member);
                goPath = ComponentCrudCore.GetHierarchyPath(go.transform);
                beforeScene = ComponentCrudCore.ResolveSavedSceneFor(go);
                var objectId = GlobalObjectId.GetGlobalObjectIdSlow(go).ToString();

                oldValue = ComponentCrudCore.GetMemberValue(component, member);
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
                    return VRCForgeToolResult.Completed(
                        $"Preview: would set {component.GetType().Name}.{p.propertyPath} to {previewPayload.newValue ?? "null"}.",
                        previewPayload);
                }

                if (ComponentCrudCore.ValuesExactlyEqual(oldValue, newValue))
                {
                    return VRCForgeToolResult.Completed(
                        $"{component.GetType().Name}.{p.propertyPath} already has the requested value on '{goPath}'.",
                        new
                        {
                            action = "set_property",
                            preview = false,
                            changed = false,
                            gameObjectPath = goPath,
                            componentType = component.GetType().FullName,
                            componentIndex,
                            propertyPath = p.propertyPath,
                            valueType = memberType.FullName,
                            oldValue = ComponentCrudCore.DescribeValue(oldValue),
                            newValue = ComponentCrudCore.DescribeValue(oldValue),
                            scenePath = beforeScene.Path,
                            sceneSaved = true,
                            persistedReadback = true,
                            mutationStarted = false,
                            committed = true,
                            commitState = "committed",
                            checkpointRecoveryRequired = false
                        });
                }

                Undo.IncrementCurrentGroup();
                undoGroup = Undo.GetCurrentGroup();
                Undo.SetCurrentGroupName("Set VRCForge component property");
                Undo.RecordObject(component, $"Set {component.GetType().Name}.{p.propertyPath}");
                mutationStarted = true;
                failureStage = "unity_mutation";
                ComponentCrudCore.SetMemberValue(component, member, newValue);
                mutationApplied = true;
                EditorUtility.SetDirty(component);
                EditorUtility.SetDirty(go);
                failureStage = "scene_save";
                var afterScene = ComponentCrudCore.SaveAndResolveScene(beforeScene);
                failureStage = "persisted_readback";
                var readbackObject = SceneObjectCopyCore.ResolveUniqueGameObject(
                    afterScene.Scene,
                    goPath,
                    "component property target");
                var readbackComponent = ComponentCrudCore.ResolveComponent(
                    readbackObject,
                    type,
                    componentIndex);
                var readbackMember = ComponentCrudCore.ResolveMember(
                    readbackComponent.GetType(),
                    p.propertyPath);
                var readbackValue = ComponentCrudCore.GetMemberValue(readbackComponent, readbackMember);
                if (GlobalObjectId.GetGlobalObjectIdSlow(readbackObject).ToString() != objectId
                    || !ComponentCrudCore.ValuesEqual(readbackValue, newValue)
                    || afterScene.FileDigest == beforeScene.FileDigest)
                {
                    throw new InvalidOperationException("The component property persisted readback was not exact.");
                }
                Undo.CollapseUndoOperations(undoGroup);

                var payload = new
                {
                    action = "set_property",
                    preview = false,
                    changed = true,
                    gameObjectPath = goPath,
                    componentType = component.GetType().FullName,
                    componentIndex,
                    propertyPath = p.propertyPath,
                    valueType = memberType.FullName,
                    oldValue = ComponentCrudCore.DescribeValue(oldValue),
                    newValue = ComponentCrudCore.DescribeValue(readbackValue),
                    scenePath = afterScene.Path,
                    sceneSaved = true,
                    persistedReadback = true,
                    mutationStarted = true,
                    committed = true,
                    commitState = "committed",
                    checkpointRecoveryRequired = false
                };
                return VRCForgeToolResult.Completed(
                    $"Set {component.GetType().Name}.{p.propertyPath} on '{goPath}'.",
                    payload);
            }
            catch (Exception ex)
            {
                if (mutationStarted && beforeScene != null && undoGroup >= 0)
                {
                    var cleanup = ComponentCrudCore.RestoreFailedMutation(
                        undoGroup,
                        beforeScene,
                        goPath,
                        restoredObject =>
                        {
                            var restoredComponent = ComponentCrudCore.ResolveComponent(
                                restoredObject,
                                type,
                                componentIndex);
                            var restoredMember = ComponentCrudCore.ResolveMember(
                                restoredComponent.GetType(),
                                p.propertyPath);
                            return ComponentCrudCore.ValuesEqual(
                                ComponentCrudCore.GetMemberValue(restoredComponent, restoredMember),
                                oldValue);
                        });
                    return ComponentCrudCore.FailedMutationResult(
                        "component_property_failed_after_mutation",
                        "set_property",
                        failureStage,
                        ex,
                        beforeScene,
                        cleanup,
                        mutationApplied);
                }
                var selectedSceneIsDirty = ex.Message.IndexOf(
                    "unsaved changes",
                    StringComparison.OrdinalIgnoreCase) >= 0;
                return VRCForgeToolResult.RejectedBeforeMutation(
                    selectedSceneIsDirty
                        ? "scene_unsaved_changes"
                        : "component_property_rejected_before_mutation",
                    $"Set property failed: {ex.Message}",
                    "unity_editor_tool",
                    selectedSceneIsDirty ? "scene_precondition" : failureStage,
                    retryable: selectedSceneIsDirty,
                    details: new
                    {
                        action = "set_property",
                        gameObjectPath = p.gameObjectPath,
                        componentType = p.componentType,
                        componentIndex,
                        propertyPath = p.propertyPath,
                        exceptionType = ex.GetType().FullName,
                        reason = ex.Message,
                        recommendedAction = selectedSceneIsDirty
                            ? "Save or revert the selected scene, then retry the exact same tool call."
                            : "Correct the reported validation or precondition failure before retrying."
                    });
            }
        }
    }
}
