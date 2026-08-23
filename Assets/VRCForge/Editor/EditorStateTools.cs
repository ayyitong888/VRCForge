using System;
using System.Linq;
using System.Reflection;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEngine;
using VRCForge.Core.MCP;

namespace VRCForge.Editor
{
    [VRCForgeCommand(
        toolId: "vrc_select_scene_object",
        Summary = "Select and ping one exact loaded-scene GameObject in the Unity Editor without changing scene data."
    )]
    public static class SelectSceneObjectTool
    {
        public class Parameters
        {
            [VRCForgeInput("Exact loaded-scene hierarchy path.")] public string gameObjectPath { get; set; } = "";
        }

        public static object HandleCommand(JObject @params)
        {
            var requestedPath = (@params?["gameObjectPath"]?.ToString() ?? string.Empty).Trim();
            if (string.IsNullOrEmpty(requestedPath))
            {
                return VRCForgeToolResult.FailedWithCode(
                    "gameobject_path_required",
                    "gameObjectPath is required.",
                    NoMutation());
            }

            GameObject target;
            try
            {
                target = ComponentCrudCore.ResolveGameObject(requestedPath);
            }
            catch (ComponentCrudCore.GameObjectNotFoundException exception)
            {
                return VRCForgeToolResult.FailedWithCode(
                    "gameobject_not_found",
                    exception.Message,
                    NoMutation(new { gameObjectPath = requestedPath }));
            }
            catch (Exception exception)
            {
                return VRCForgeToolResult.FailedWithCode(
                    "scene_object_selection_rejected",
                    exception.Message,
                    NoMutation(new { gameObjectPath = requestedPath }));
            }

            var canonicalPath = ComponentCrudCore.GetHierarchyPath(target.transform);
            var before = Selection.activeGameObject
                ? ComponentCrudCore.GetHierarchyPath(Selection.activeGameObject.transform)
                : string.Empty;
            var hierarchyReveal = ClearHierarchySearchFilters();
            Selection.activeGameObject = target;
            EditorGUIUtility.PingObject(target);
            var after = Selection.activeGameObject
                ? ComponentCrudCore.GetHierarchyPath(Selection.activeGameObject.transform)
                : string.Empty;
            if (!string.Equals(after, canonicalPath, StringComparison.Ordinal))
            {
                return VRCForgeToolResult.FailedWithCode(
                    "scene_object_selection_readback_failed",
                    $"Unity did not retain the requested editor selection: {canonicalPath}",
                    new
                    {
                        gameObjectPath = canonicalPath,
                        selectedBefore = before,
                        selectedAfter = after,
                        mutationStarted = true,
                        committed = false,
                        commitState = "unknown"
                    });
            }

            if (hierarchyReveal.FilterWasPresent && !hierarchyReveal.FilterCleared)
            {
                return VRCForgeToolResult.FailedWithCode(
                    "scene_object_hierarchy_reveal_failed",
                    "Unity selected the requested object, but an existing Hierarchy search filter could not be cleared; the object may remain hidden in the editor UI.",
                    new
                    {
                        gameObjectPath = canonicalPath,
                        selectedBefore = before,
                        selectedAfter = after,
                        hierarchyReveal = hierarchyReveal.ToResult(),
                        persistent = false,
                        sceneDirty = false,
                        mutationStarted = true,
                        committed = false,
                        commitState = "editor_state_partial"
                    });
            }

            return VRCForgeToolResult.Completed(
                hierarchyReveal.FilterCleared
                    ? $"Selected and revealed scene object: {canonicalPath}"
                    : $"Selected scene object: {canonicalPath}",
                new
                {
                    gameObjectPath = canonicalPath,
                    selectedBefore = before,
                    selectedAfter = after,
                    hierarchyReveal = hierarchyReveal.ToResult(),
                    persistent = false,
                    sceneDirty = false,
                    mutationStarted = true,
                    committed = true,
                    commitState = "editor_state_applied"
                });
        }

        private static HierarchyRevealResult ClearHierarchySearchFilters()
        {
            var result = new HierarchyRevealResult();
            try
            {
                var editorAssembly = typeof(EditorWindow).Assembly;
                var hierarchyWindowType = editorAssembly.GetType("UnityEditor.SceneHierarchyWindow", throwOnError: false);
                var searchableWindowType = editorAssembly.GetType("UnityEditor.SearchableEditorWindow", throwOnError: false);
                if (hierarchyWindowType == null || searchableWindowType == null)
                {
                    result.Error = "Unity Hierarchy search types are unavailable in this Editor version.";
                    return result;
                }

                var flags = BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic;
                var searchFilterProperty = searchableWindowType.GetProperty("searchFilter", flags);
                var searchFilterField = searchableWindowType.GetField("m_SearchFilter", flags);
                var setSearchFilter = searchableWindowType
                    .GetMethods(flags)
                    .Where(method => method.Name == "SetSearchFilter")
                    .Where(method =>
                    {
                        var parameters = method.GetParameters();
                        return parameters.Length > 0 && parameters[0].ParameterType == typeof(string);
                    })
                    .OrderBy(method => method.GetParameters().Length)
                    .FirstOrDefault();

                if (setSearchFilter == null)
                {
                    result.Error = "Unity Hierarchy search setter is unavailable in this Editor version.";
                    return result;
                }

                foreach (var candidate in Resources.FindObjectsOfTypeAll(hierarchyWindowType))
                {
                    if (!(candidate is EditorWindow window))
                    {
                        continue;
                    }

                    result.WindowCount++;
                    var before = ReadSearchFilter(window, searchFilterProperty, searchFilterField);
                    if (string.IsNullOrEmpty(before))
                    {
                        continue;
                    }

                    result.FilterWasPresent = true;
                    result.FilterBefore = before;
                    var parameters = setSearchFilter.GetParameters();
                    var arguments = new object[parameters.Length];
                    arguments[0] = string.Empty;
                    for (var index = 1; index < parameters.Length; index++)
                    {
                        arguments[index] = DefaultArgument(parameters[index]);
                    }

                    setSearchFilter.Invoke(window, arguments);
                    window.Repaint();
                    var after = ReadSearchFilter(window, searchFilterProperty, searchFilterField);
                    result.FilterAfter = after;
                    result.FilterCleared = string.IsNullOrEmpty(after);
                    if (!result.FilterCleared)
                    {
                        result.Error = $"Hierarchy search filter remained active after clear request: {after}";
                        return result;
                    }
                }

                return result;
            }
            catch (Exception exception)
            {
                result.Error = exception.GetBaseException().Message;
                return result;
            }
        }

        private static string ReadSearchFilter(EditorWindow window, PropertyInfo property, FieldInfo field)
        {
            if (property != null && property.PropertyType == typeof(string))
            {
                return property.GetValue(window, null) as string ?? string.Empty;
            }

            if (field != null && field.FieldType == typeof(string))
            {
                return field.GetValue(window) as string ?? string.Empty;
            }

            return string.Empty;
        }

        private static object DefaultArgument(ParameterInfo parameter)
        {
            if (parameter.HasDefaultValue)
            {
                return parameter.DefaultValue;
            }

            if (parameter.ParameterType == typeof(bool))
            {
                return false;
            }

            if (parameter.ParameterType.IsEnum)
            {
                var names = Enum.GetNames(parameter.ParameterType);
                var all = names.FirstOrDefault(name => string.Equals(name, "All", StringComparison.OrdinalIgnoreCase));
                return Enum.Parse(parameter.ParameterType, all ?? names[0]);
            }

            return parameter.ParameterType.IsValueType
                ? Activator.CreateInstance(parameter.ParameterType)
                : null;
        }

        private sealed class HierarchyRevealResult
        {
            internal int WindowCount;
            internal bool FilterWasPresent;
            internal bool FilterCleared;
            internal string FilterBefore = string.Empty;
            internal string FilterAfter = string.Empty;
            internal string Error = string.Empty;

            internal object ToResult()
            {
                return new
                {
                    hierarchyWindowCount = WindowCount,
                    filterWasPresent = FilterWasPresent,
                    filterCleared = FilterCleared,
                    filterBefore = FilterBefore,
                    filterAfter = FilterAfter,
                    error = Error
                };
            }
        }

        private static object NoMutation(object details = null)
        {
            return new
            {
                details,
                mutationStarted = false,
                committed = false,
                commitState = "not_started"
            };
        }
    }

    [VRCForgeCommand(
        toolId: "vrc_set_play_mode",
        Summary = "Request one explicit Unity Editor Play Mode state and report whether a transition was scheduled."
    )]
    public static class SetPlayModeTool
    {
        public class Parameters
        {
            [VRCForgeInput("True to enter Play Mode; false to exit Play Mode.")] public bool? isPlaying { get; set; }
        }

        public static object HandleCommand(JObject @params)
        {
            var targetToken = @params?["isPlaying"];
            if (targetToken == null || targetToken.Type == JTokenType.Null)
            {
                return VRCForgeToolResult.RejectedBeforeMutation(
                    "play_mode_target_required",
                    "isPlaying is required.",
                    "unity_editor_state",
                    "argument_validation");
            }

            bool requested;
            try
            {
                requested = targetToken.Value<bool>();
            }
            catch (Exception)
            {
                return VRCForgeToolResult.RejectedBeforeMutation(
                    "play_mode_target_invalid",
                    "isPlaying must be a boolean.",
                    "unity_editor_state",
                    "argument_validation");
            }

            var before = EditorApplication.isPlaying;
            var isTransitioning = EditorApplication.isPlayingOrWillChangePlaymode != before;
            var entryBlockedByEditorWork = requested && (EditorApplication.isCompiling || EditorApplication.isUpdating);
            if (entryBlockedByEditorWork || isTransitioning)
            {
                return VRCForgeToolResult.RejectedBeforeMutation(
                    "editor_play_mode_busy",
                    requested
                        ? "Unity is compiling, updating, or already changing Play Mode; wait and read status before retrying."
                        : "Unity is already changing Play Mode; wait and read status before retrying.",
                    "unity_editor_state",
                    "play_mode_precondition",
                    true,
                    new
                    {
                        requested,
                        isPlaying = before,
                        isPlayingOrWillChangePlaymode = EditorApplication.isPlayingOrWillChangePlaymode,
                        isCompiling = EditorApplication.isCompiling,
                        isUpdating = EditorApplication.isUpdating
                    });
            }

            if (before == requested)
            {
                return VRCForgeToolResult.Completed(
                    requested ? "Unity is already in Play Mode." : "Unity is already outside Play Mode.",
                    new
                    {
                        requested,
                        before,
                        transitionScheduled = false,
                        verificationRequired = false,
                        persistent = false,
                        sceneDirty = false,
                        mutationStarted = false,
                        committed = true,
                        commitState = "no_change"
                    });
            }

            if (requested)
            {
                EditorApplication.EnterPlaymode();
            }
            else
            {
                EditorApplication.ExitPlaymode();
            }

            return VRCForgeToolResult.Completed(
                requested ? "Unity Play Mode entry was scheduled." : "Unity Play Mode exit was scheduled.",
                new
                {
                    requested,
                    before,
                    transitionScheduled = true,
                    verificationRequired = true,
                    persistent = false,
                    sceneDirty = false,
                    mutationStarted = true,
                    committed = false,
                    commitState = "transition_scheduled"
                });
        }

        private static object NoMutation(object details = null)
        {
            return new
            {
                details,
                mutationStarted = false,
                committed = false,
                commitState = "not_started"
            };
        }
    }
}
