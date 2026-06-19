using System;
using System.Collections.Generic;
using System.Linq;
using System.Reflection;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Tools;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEngine;

namespace VRCForge.Editor
{
    // Writes the commonly-used Modular Avatar components onto a scene object via
    // reflection, so VRCForge never takes a hard compile-time dependency on the MA
    // package. Handles the parts the generic component CRUD cannot:
    //   - resolves MA's AvatarObjectReference fields (e.g. MergeArmature.mergeTarget,
    //     BoneProxy.target) from a scene-object path by invoking AvatarObjectReference.Set,
    //   - loads UnityEngine.Object asset/scene references by member type (e.g.
    //     MenuInstaller.menuToAppend = a VRCExpressionsMenu asset),
    //   - sets scalar/enum/string fields by name.
    // Friendly type aliases (MergeArmature, BoneProxy, MenuInstaller, MergeAnimator,
    // Parameters) map to nadena.dev.modular_avatar.core.* ; a fully-qualified type
    // name is also accepted. Add-only by default (won't duplicate an existing MA
    // component of the same type unless allowDuplicate=true). Undo-registered.
    // Supports preview.
    [McpForUnityTool(
        name: "vrc_add_modular_avatar_component",
        Description = "Add a common Modular Avatar component (MergeArmature, BoneProxy, MenuInstaller, MergeAnimator, Parameters, or a fully-qualified nadena.dev type) to a scene object. Resolves AvatarObjectReference fields from object paths, loads asset/scene object references by member type, and sets scalar/enum fields. Add-only by default, Undo-registered. Supports preview."
    )]
    public static class MAComponentWriter
    {
        public const string ToolName = "vrc_add_modular_avatar_component";

        private const string MaNamespace = "nadena.dev.modular_avatar.core.";

        private static readonly Dictionary<string, string> Aliases = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase)
        {
            { "MergeArmature", MaNamespace + "ModularAvatarMergeArmature" },
            { "ModularAvatarMergeArmature", MaNamespace + "ModularAvatarMergeArmature" },
            { "BoneProxy", MaNamespace + "ModularAvatarBoneProxy" },
            { "ModularAvatarBoneProxy", MaNamespace + "ModularAvatarBoneProxy" },
            { "MenuInstaller", MaNamespace + "ModularAvatarMenuInstaller" },
            { "ModularAvatarMenuInstaller", MaNamespace + "ModularAvatarMenuInstaller" },
            { "MergeAnimator", MaNamespace + "ModularAvatarMergeAnimator" },
            { "ModularAvatarMergeAnimator", MaNamespace + "ModularAvatarMergeAnimator" },
            { "Parameters", MaNamespace + "ModularAvatarParameters" },
            { "ModularAvatarParameters", MaNamespace + "ModularAvatarParameters" },
            { "MenuItem", MaNamespace + "ModularAvatarMenuItem" },
            { "ModularAvatarMenuItem", MaNamespace + "ModularAvatarMenuItem" },
        };

        public static object HandleCommand(JObject @params)
        {
            try
            {
                @params = @params ?? new JObject();
                var gameObjectPath = (@params["gameObjectPath"]?.ToString() ?? @params["targetPath"]?.ToString() ?? string.Empty).Trim();
                var componentTypeInput = (@params["componentType"]?.ToString() ?? string.Empty).Trim();
                var avatarPath = (@params["avatarPath"]?.ToString() ?? string.Empty).Trim();
                var preview = @params["preview"] != null && @params["preview"].ToObject<bool>();
                var allowDuplicate = @params["allowDuplicate"] != null && @params["allowDuplicate"].ToObject<bool>();

                if (string.IsNullOrWhiteSpace(gameObjectPath))
                {
                    return new ErrorResponse("Missing required parameter: gameObjectPath (the scene object to add the Modular Avatar component to).");
                }
                if (string.IsNullOrWhiteSpace(componentTypeInput))
                {
                    return new ErrorResponse(
                        "Missing required parameter: componentType. Use one of: "
                        + string.Join(", ", Aliases.Keys.Where(k => k.StartsWith("M", StringComparison.Ordinal) == false || !k.StartsWith("Modular", StringComparison.Ordinal)))
                        + " (or a fully-qualified nadena.dev type).");
                }

                // Resolve MA presence + the target component type.
                if (FindType(MaNamespace + "ModularAvatarMergeArmature") == null)
                {
                    return new ErrorResponse("Modular Avatar runtime types were not found. Install the Modular Avatar package first.");
                }
                var resolvedTypeName = Aliases.TryGetValue(componentTypeInput, out var mapped) ? mapped : componentTypeInput;
                var componentType = FindType(resolvedTypeName) ?? FindType(MaNamespace + componentTypeInput);
                if (componentType == null || !typeof(Component).IsAssignableFrom(componentType))
                {
                    return new ErrorResponse(
                        $"Modular Avatar component type not found: '{componentTypeInput}'. Supported aliases: MergeArmature, BoneProxy, MenuInstaller, MergeAnimator, Parameters, MenuItem (or a fully-qualified nadena.dev type).");
                }

                // Resolve target object (and the avatar root used for reference resolution).
                var avatarRoot = ResolveAvatarRoot(avatarPath);
                var target = ResolveSceneObject(gameObjectPath, avatarRoot);
                if (target == null)
                {
                    return new ErrorResponse($"Target GameObject not found in the loaded scene(s): '{gameObjectPath}'.");
                }
                var targetPath = GetTransformPath(target.transform);
                if (avatarRoot == null)
                {
                    avatarRoot = FindAvatarRootFor(target.transform);
                }

                var existing = target.GetComponents(componentType).Length;
                var warnings = new List<string>();
                if (existing > 0 && !allowDuplicate)
                {
                    return new SuccessResponse(
                        $"'{target.name}' already has {existing} {componentType.Name} component(s); skipped (pass allowDuplicate=true to add another).",
                        new
                        {
                            ok = true,
                            preview,
                            action = "add_modular_avatar_component",
                            skipped = true,
                            reason = "component_exists",
                            gameObjectPath = targetPath,
                            componentType = componentType.FullName,
                            existingCount = existing
                        });
                }

                // Plan field + reference assignments (resolve everything before mutating).
                var references = @params["references"] as JObject ?? new JObject();
                var fields = @params["fields"] as JObject ?? new JObject();
                var refPlan = new List<AssignmentPlan>();
                var fieldPlan = new List<AssignmentPlan>();

                foreach (var pair in references)
                {
                    var member = ResolveMember(componentType, pair.Key);
                    if (member == null)
                    {
                        return new ErrorResponse($"Reference field '{pair.Key}' not found on {componentType.Name}.");
                    }
                    var memberType = GetMemberType(member);
                    var pathValue = pair.Value?.ToString() ?? string.Empty;
                    var kind = ClassifyReference(memberType);
                    if (kind == ReferenceKind.Unsupported)
                    {
                        return new ErrorResponse($"Field '{pair.Key}' on {componentType.Name} is type '{memberType.Name}', which is not a supported reference type.");
                    }
                    var assignment = new AssignmentPlan { member = member, memberType = memberType, raw = pathValue, kind = kind };
                    if (!TryResolveReference(assignment, avatarRoot, out var referenceError))
                    {
                        return new ErrorResponse(referenceError);
                    }
                    refPlan.Add(assignment);
                }

                foreach (var pair in fields)
                {
                    var member = ResolveMember(componentType, pair.Key);
                    if (member == null)
                    {
                        return new ErrorResponse($"Field '{pair.Key}' not found on {componentType.Name}.");
                    }
                    if (!CanWriteMember(member))
                    {
                        return new ErrorResponse($"Field '{pair.Key}' on {componentType.Name} is read-only.");
                    }
                    var assignment = new AssignmentPlan { member = member, memberType = GetMemberType(member), token = pair.Value };
                    try
                    {
                        assignment.convertedValue = ConvertScalar(pair.Value, assignment.memberType);
                    }
                    catch (Exception ex)
                    {
                        return new ErrorResponse($"Could not convert field '{pair.Key}' to {assignment.memberType.Name}: {ex.Message}");
                    }
                    fieldPlan.Add(assignment);
                }

                if (preview)
                {
                    var refDesc = refPlan.Select(p => new { field = p.member.Name, type = p.memberType.Name, path = p.raw, resolved = p.resolvedDisplay, kind = p.kind.ToString() }).ToList();
                    var fieldDesc = fieldPlan.Select(p => new { field = p.member.Name, type = p.memberType.Name, value = p.token?.ToString() }).ToList();
                    return new SuccessResponse(
                        $"Preview: would add {componentType.Name} to '{target.name}'.",
                        new
                        {
                            ok = true,
                            preview = true,
                            action = "add_modular_avatar_component",
                            gameObjectPath = targetPath,
                            avatarPath = avatarRoot != null ? GetTransformPath(avatarRoot) : null,
                            componentType = componentType.FullName,
                            existingCount = existing,
                            references = refDesc,
                            fields = fieldDesc,
                            warnings
                        });
                }

                // ---- APPLY -------------------------------------------------------
                Undo.IncrementCurrentGroup();
                var undoGroup = Undo.GetCurrentGroup();
                Undo.SetCurrentGroupName($"Add {componentType.Name}");
                var component = Undo.AddComponent(target, componentType);
                if (component == null)
                {
                    return new ErrorResponse($"Failed to add {componentType.Name} to '{target.name}'.");
                }
                Undo.RegisterCompleteObjectUndo(component, $"Configure {componentType.Name}");

                var appliedRefs = new List<object>();
                var appliedFields = new List<object>();
                try
                {
                    foreach (var plan in refPlan)
                    {
                        ApplyReference(component, plan);
                        appliedRefs.Add(new { field = plan.member.Name, resolved = plan.resolvedDisplay });
                    }

                    foreach (var plan in fieldPlan)
                    {
                        SetMemberValue(component, plan.member, plan.convertedValue);
                        appliedFields.Add(new { field = plan.member.Name, set = true, value = plan.token?.ToString() });
                    }
                }
                catch (Exception ex)
                {
                    Undo.RevertAllDownToGroup(undoGroup);
                    return new ErrorResponse($"Could not configure {componentType.Name}; the added component was rolled back: {ex.Message}");
                }

                EditorUtility.SetDirty(component);
                if (target.scene.IsValid())
                {
                    UnityEditor.SceneManagement.EditorSceneManager.MarkSceneDirty(target.scene);
                }
                Undo.CollapseUndoOperations(undoGroup);

                return new SuccessResponse(
                    $"Added {componentType.Name} to '{target.name}'.",
                    new
                    {
                        ok = true,
                        preview = false,
                        action = "add_modular_avatar_component",
                        gameObjectPath = targetPath,
                        avatarPath = avatarRoot != null ? GetTransformPath(avatarRoot) : null,
                        componentType = componentType.FullName,
                        addedComponent = true,
                        references = appliedRefs,
                        fields = appliedFields,
                        warnings
                    });
            }
            catch (Exception ex)
            {
                return new ErrorResponse($"Add Modular Avatar component failed: {ex.Message}\n{ex.StackTrace}");
            }
        }

        // --- reference / field application -------------------------------------------

        private enum ReferenceKind { AvatarObjectReference, SceneObject, Asset, Unsupported }

        private static ReferenceKind ClassifyReference(Type memberType)
        {
            if (memberType == null) { return ReferenceKind.Unsupported; }
            if (memberType.Name == "AvatarObjectReference") { return ReferenceKind.AvatarObjectReference; }
            if (typeof(GameObject).IsAssignableFrom(memberType) || typeof(Component).IsAssignableFrom(memberType))
            {
                return ReferenceKind.SceneObject;
            }
            if (typeof(UnityEngine.Object).IsAssignableFrom(memberType))
            {
                return ReferenceKind.Asset;
            }
            return ReferenceKind.Unsupported;
        }

        private static bool TryResolveReference(AssignmentPlan plan, Transform avatarRoot, out string error)
        {
            error = null;
            switch (plan.kind)
            {
                case ReferenceKind.AvatarObjectReference:
                {
                    var go = ResolveSceneObject(plan.raw, avatarRoot);
                    if (go == null)
                    {
                        error = $"Could not resolve object '{plan.raw}' for AvatarObjectReference field '{plan.member.Name}'.";
                        return false;
                    }
                    plan.resolvedValue = go;
                    plan.resolvedDisplay = GetTransformPath(go.transform);
                    plan.avatarRelativePath = avatarRoot != null
                        ? RelativePath(avatarRoot, go.transform)
                        : plan.resolvedDisplay;
                    return true;
                }
                case ReferenceKind.SceneObject:
                {
                    var go = ResolveSceneObject(plan.raw, avatarRoot);
                    if (go == null)
                    {
                        error = $"Could not resolve scene object '{plan.raw}' for field '{plan.member.Name}'.";
                        return false;
                    }
                    if (typeof(GameObject).IsAssignableFrom(plan.memberType))
                    {
                        plan.resolvedValue = go;
                    }
                    else
                    {
                        var resolvedComponent = go.GetComponent(plan.memberType);
                        if (resolvedComponent == null)
                        {
                            error = $"Scene object '{plan.raw}' has no component of type '{plan.memberType.Name}' for field '{plan.member.Name}'.";
                            return false;
                        }
                        plan.resolvedValue = resolvedComponent;
                    }
                    plan.resolvedDisplay = GetTransformPath(go.transform);
                    return true;
                }
                case ReferenceKind.Asset:
                {
                    var asset = AssetDatabase.LoadAssetAtPath(plan.raw, plan.memberType);
                    if (asset == null)
                    {
                        error = $"Could not load asset '{plan.raw}' as {plan.memberType.Name} for field '{plan.member.Name}'.";
                        return false;
                    }
                    plan.resolvedValue = asset;
                    plan.resolvedDisplay = plan.raw;
                    return true;
                }
                default:
                    error = $"Field '{plan.member.Name}' is not a supported reference type.";
                    return false;
            }
        }

        private static void ApplyReference(Component component, AssignmentPlan plan)
        {
            switch (plan.kind)
            {
                case ReferenceKind.AvatarObjectReference:
                {
                    var go = (GameObject)plan.resolvedValue;
                    var refInstance = GetMemberValue(component, plan.member);
                    if (refInstance == null)
                    {
                        refInstance = Activator.CreateInstance(plan.memberType);
                    }
                    var setMethod = plan.memberType.GetMethods(BindingFlags.Public | BindingFlags.Instance)
                        .FirstOrDefault(m => m.Name == "Set"
                            && m.GetParameters().Length == 1
                            && m.GetParameters()[0].ParameterType == typeof(GameObject));
                    if (setMethod != null)
                    {
                        setMethod.Invoke(refInstance, new object[] { go });
                    }
                    else
                    {
                        // Fallback: set the public referencePath field directly.
                        var refPathField = plan.memberType.GetField("referencePath", BindingFlags.Public | BindingFlags.Instance);
                        if (refPathField != null)
                        {
                            refPathField.SetValue(refInstance, plan.avatarRelativePath);
                        }
                        else
                        {
                            throw new InvalidOperationException(
                                $"AvatarObjectReference field '{plan.member.Name}' has no usable Set method or referencePath field.");
                        }
                    }
                    SetMemberValue(component, plan.member, refInstance);
                    return;
                }
                case ReferenceKind.SceneObject:
                case ReferenceKind.Asset:
                    SetMemberValue(component, plan.member, plan.resolvedValue);
                    return;
                default:
                    throw new InvalidOperationException($"Unsupported reference kind for '{plan.member.Name}'.");
            }
        }

        private static object ConvertScalar(JToken token, Type targetType)
        {
            if (token == null)
            {
                return targetType.IsValueType ? Activator.CreateInstance(targetType) : null;
            }
            if (targetType.IsEnum)
            {
                if (token.Type == JTokenType.Integer) { return Enum.ToObject(targetType, token.ToObject<int>()); }
                return Enum.Parse(targetType, token.ToString(), true);
            }
            if (targetType == typeof(string)) { return token.ToString(); }
            if (targetType == typeof(bool)) { return token.ToObject<bool>(); }
            if (targetType == typeof(int)) { return token.ToObject<int>(); }
            if (targetType == typeof(float)) { return token.ToObject<float>(); }
            if (targetType == typeof(double)) { return token.ToObject<double>(); }
            // Last resort: let Json.NET try.
            return token.ToObject(targetType);
        }

        // --- reflection helpers ------------------------------------------------------

        private static MemberInfo ResolveMember(Type type, string memberName)
        {
            const BindingFlags flags = BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic;
            var field = type.GetField(memberName, flags);
            if (field != null) { return field; }
            return type.GetProperty(memberName, flags);
        }

        private static Type GetMemberType(MemberInfo member)
        {
            return member is FieldInfo field ? field.FieldType : ((PropertyInfo)member).PropertyType;
        }

        private static bool CanWriteMember(MemberInfo member)
        {
            if (member is FieldInfo field) { return !field.IsInitOnly && !field.IsLiteral; }
            return member is PropertyInfo property && property.CanWrite;
        }

        private static object GetMemberValue(object source, MemberInfo member)
        {
            return member is FieldInfo field ? field.GetValue(source) : ((PropertyInfo)member).GetValue(source);
        }

        private static void SetMemberValue(object target, MemberInfo member, object value)
        {
            if (member is FieldInfo field) { field.SetValue(target, value); return; }
            var property = (PropertyInfo)member;
            if (property.CanWrite) { property.SetValue(target, value); }
        }

        // --- scene / type resolution -------------------------------------------------

        private static Transform ResolveAvatarRoot(string avatarPath)
        {
            if (string.IsNullOrWhiteSpace(avatarPath)) { return null; }
            var descriptorType = FindType("VRC.SDK3.Avatars.Components.VRCAvatarDescriptor");
            if (descriptorType == null) { return null; }
            var normalized = NormalizePath(avatarPath);
            var descriptors = Resources.FindObjectsOfTypeAll(descriptorType)
                .OfType<Component>()
                .Where(IsSceneComponent)
                .ToList();
            var match = descriptors.FirstOrDefault(d => NormalizePath(GetTransformPath(d.transform)) == normalized)
                ?? descriptors.FirstOrDefault(d => d.name.Equals(avatarPath, StringComparison.OrdinalIgnoreCase));
            return match != null ? match.transform : null;
        }

        private static Transform FindAvatarRootFor(Transform t)
        {
            var descriptorType = FindType("VRC.SDK3.Avatars.Components.VRCAvatarDescriptor");
            if (descriptorType == null) { return null; }
            var current = t;
            while (current != null)
            {
                if (current.GetComponent(descriptorType) != null) { return current; }
                current = current.parent;
            }
            return null;
        }

        private static GameObject ResolveSceneObject(string rawPath, Transform avatarRoot)
        {
            var path = NormalizePath(rawPath);
            if (string.IsNullOrEmpty(path)) { return null; }

            // Relative to the avatar root first.
            if (avatarRoot != null)
            {
                var rel = avatarRoot.Find(path);
                if (rel != null) { return rel.gameObject; }
                if (path.Equals(avatarRoot.name, StringComparison.Ordinal)) { return avatarRoot.gameObject; }
                if (path.StartsWith(avatarRoot.name + "/", StringComparison.Ordinal))
                {
                    var sub = avatarRoot.Find(path.Substring(avatarRoot.name.Length + 1));
                    if (sub != null) { return sub.gameObject; }
                }
            }

            // Full-scene scan: exact hierarchy path, then unique leaf name.
            var leaf = path.Contains("/") ? path.Substring(path.LastIndexOf('/') + 1) : path;
            GameObject byLeaf = null;
            var leafMatches = 0;
            foreach (var t in Resources.FindObjectsOfTypeAll<Transform>())
            {
                if (!IsSceneComponent(t)) { continue; }
                var full = NormalizePath(GetTransformPath(t));
                if (full == path) { return t.gameObject; }
                if (t.name.Equals(leaf, StringComparison.Ordinal))
                {
                    byLeaf = t.gameObject;
                    leafMatches++;
                }
            }
            return leafMatches == 1 ? byLeaf : null;
        }

        private static bool IsSceneComponent(Component component)
        {
            return component != null
                && component.gameObject != null
                && component.gameObject.scene.IsValid()
                && component.gameObject.scene.isLoaded
                && !EditorUtility.IsPersistent(component.gameObject);
        }

        private static string GetTransformPath(Transform transform)
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

        private static string RelativePath(Transform root, Transform target)
        {
            var segments = new Stack<string>();
            var current = target;
            while (current != null && current != root)
            {
                segments.Push(current.name);
                current = current.parent;
            }
            return string.Join("/", segments);
        }

        private static string NormalizePath(string value)
        {
            return (value ?? string.Empty).Replace("\\", "/").Trim().Trim('/');
        }

        private static Type FindType(string fullName)
        {
            return AppDomain.CurrentDomain.GetAssemblies()
                .Select(assembly =>
                {
                    try { return assembly.GetType(fullName, false); }
                    catch { return null; }
                })
                .FirstOrDefault(type => type != null);
        }

        private class AssignmentPlan
        {
            public MemberInfo member;
            public Type memberType;
            public string raw;       // for references
            public JToken token;     // for scalar fields
            public ReferenceKind kind;
            public object resolvedValue;
            public object convertedValue;
            public string resolvedDisplay;
            public string avatarRelativePath;
        }
    }
}
