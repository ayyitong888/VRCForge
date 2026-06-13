using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Text;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Tools;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEditor.Animations;
using UnityEngine;
using VRC.SDK3.Avatars.Components;
using VRC.SDK3.Avatars.ScriptableObjects;

namespace VRCForge.Editor
{
    // Detects VRChat "int-exclusive wardrobe(s)" by reconciling the native triangle:
    //   1) an Int parameter in VRCExpressionParameters (the source of truth),
    //   2) menu Toggle controls bound to that Int with distinct values (recurses SubMenus),
    //   3) an FX AnimatorController layer whose Any State -> state transitions are gated on
    //      "<intParam> Equals N", each state pointing at one clip.
    // For every outfit it also reads the clip's m_IsActive curves (which objects it turns
    // on vs off) and the state's Write Defaults flag, because exclusivity in this style
    // relies on WD + scene-default-off alternates. This is read-only.
    [McpForUnityTool(
        name: "vrc_scan_wardrobe",
        Description = "Detect VRChat int-exclusive wardrobe(s): reconcile an expression Int parameter, menu toggle values (recursing SubMenus), FX layer Any-State Equals transitions, per-clip object on/off toggles, and Write Defaults flags. Read-only."
    )]
    public static class WardrobeScanner
    {
        public static object HandleCommand(JObject @params)
        {
            try
            {
                var avatarPath = (@params?["avatarPath"]?.ToString() ?? string.Empty).Trim();
                var outputPath = (@params?["outputPath"]?.ToString() ?? string.Empty).Trim();

                var descriptor = ResolveAvatarDescriptor(avatarPath);
                var avatarRootPath = GetTransformPath(descriptor.transform);

                var intParameters = ReadIntParameters(descriptor);

                var menuToggles = new List<MenuToggle>();
                ReadMenuToggles(descriptor.expressionsMenu, "", menuToggles, new HashSet<int>(), 0);

                var fxController = GetFxController(descriptor);
                var fxControllerPath = fxController != null ? AssetDatabase.GetAssetPath(fxController) : "";
                var fxLayers = fxController != null ? ReadFxLayers(fxController) : new List<FxLayerInfo>();

                var wardrobes = BuildWardrobes(intParameters, menuToggles, fxLayers);

                var payload = new
                {
                    ok = true,
                    avatarPath = avatarRootPath,
                    avatarName = descriptor.name,
                    fxControllerPath,
                    intParameterCount = intParameters.Count,
                    menuToggleCount = menuToggles.Count,
                    fxLayerCount = fxLayers.Count,
                    wardrobeCount = wardrobes.Count,
                    wardrobes
                };

                var jsonPath = WriteJsonIfRequested(outputPath, payload);

                return new SuccessResponse(
                    $"Detected {wardrobes.Count} wardrobe(s) on '{descriptor.name}'.",
                    new
                    {
                        ok = true,
                        jsonPath,
                        avatarPath = avatarRootPath,
                        avatarName = descriptor.name,
                        fxControllerPath,
                        wardrobeCount = wardrobes.Count,
                        wardrobes
                    });
            }
            catch (Exception ex)
            {
                return new ErrorResponse($"Wardrobe scan failed: {ex.Message}\n{ex.StackTrace}");
            }
        }

        // --- 1. Expression parameters (Int only) -------------------------------------

        private static List<ParamInfo> ReadIntParameters(VRCAvatarDescriptor descriptor)
        {
            var result = new List<ParamInfo>();
            var asset = descriptor.expressionParameters;
            if (asset == null || asset.parameters == null)
            {
                return result;
            }

            foreach (var parameter in asset.parameters)
            {
                if (parameter == null)
                {
                    continue;
                }
                if (parameter.valueType != VRCExpressionParameters.ValueType.Int)
                {
                    continue;
                }
                result.Add(new ParamInfo
                {
                    name = parameter.name ?? "",
                    defaultValue = Mathf.RoundToInt(parameter.defaultValue),
                    saved = parameter.saved,
                    networkSynced = parameter.networkSynced
                });
            }

            return result;
        }

        // --- 2. Menu toggles (recursive, capture value) ------------------------------

        private static void ReadMenuToggles(
            VRCExpressionsMenu menu,
            string parentPath,
            List<MenuToggle> sink,
            HashSet<int> visited,
            int depth)
        {
            if (menu == null || depth > 8)
            {
                return;
            }
            if (!visited.Add(menu.GetInstanceID()))
            {
                return;
            }
            if (menu.controls == null)
            {
                return;
            }

            foreach (var control in menu.controls)
            {
                if (control == null)
                {
                    continue;
                }

                var name = control.name ?? "";
                var menuPath = string.IsNullOrWhiteSpace(parentPath) ? name : $"{parentPath}/{name}";
                var parameterName = control.parameter != null ? (control.parameter.name ?? "") : "";

                if (!string.IsNullOrWhiteSpace(parameterName))
                {
                    sink.Add(new MenuToggle
                    {
                        menuName = name,
                        menuPath = menuPath,
                        parameterName = parameterName,
                        value = Mathf.RoundToInt(control.value),
                        controlType = control.type.ToString()
                    });
                }

                if (control.type == VRCExpressionsMenu.Control.ControlType.SubMenu && control.subMenu != null)
                {
                    ReadMenuToggles(control.subMenu, menuPath, sink, visited, depth + 1);
                }
            }
        }

        // --- 3. FX layers / states / Any-State transitions ---------------------------

        private static AnimatorController GetFxController(VRCAvatarDescriptor descriptor)
        {
            if (descriptor.baseAnimationLayers == null)
            {
                return null;
            }

            foreach (var layer in descriptor.baseAnimationLayers)
            {
                if (layer.type == VRCAvatarDescriptor.AnimLayerType.FX && !layer.isDefault)
                {
                    if (layer.animatorController is AnimatorController controller)
                    {
                        return controller;
                    }
                }
            }

            // Fallback: any FX layer with a controller.
            foreach (var layer in descriptor.baseAnimationLayers)
            {
                if (layer.type == VRCAvatarDescriptor.AnimLayerType.FX
                    && layer.animatorController is AnimatorController controller)
                {
                    return controller;
                }
            }

            return null;
        }

        private static List<FxLayerInfo> ReadFxLayers(AnimatorController controller)
        {
            var layers = new List<FxLayerInfo>();
            foreach (var layer in controller.layers)
            {
                if (layer == null || layer.stateMachine == null)
                {
                    continue;
                }

                var info = new FxLayerInfo
                {
                    name = layer.name ?? "",
                    defaultStateName = layer.stateMachine.defaultState != null ? layer.stateMachine.defaultState.name : "",
                    states = new List<FxState>(),
                    anyStateEquals = new List<AnyStateEquals>()
                };

                CollectStates(layer.stateMachine, info.states);
                CollectAnyStateEquals(layer.stateMachine, info.anyStateEquals);
                layers.Add(info);
            }

            return layers;
        }

        private static void CollectStates(AnimatorStateMachine machine, List<FxState> sink)
        {
            if (machine == null)
            {
                return;
            }

            foreach (var child in machine.states)
            {
                var state = child.state;
                if (state == null)
                {
                    continue;
                }

                var clip = state.motion as AnimationClip;
                var clipPath = clip != null ? AssetDatabase.GetAssetPath(clip) : "";
                var on = new List<string>();
                var off = new List<string>();
                ReadClipToggles(clip, on, off);

                sink.Add(new FxState
                {
                    name = state.name ?? "",
                    motionName = state.motion != null ? state.motion.name : "",
                    clipPath = clipPath,
                    writeDefaults = state.writeDefaultValues,
                    onObjects = on,
                    offObjects = off
                });
            }

            foreach (var sub in machine.stateMachines)
            {
                CollectStates(sub.stateMachine, sink);
            }
        }

        private static void CollectAnyStateEquals(AnimatorStateMachine machine, List<AnyStateEquals> sink)
        {
            if (machine == null)
            {
                return;
            }

            foreach (var transition in machine.anyStateTransitions ?? Array.Empty<AnimatorStateTransition>())
            {
                if (transition == null || transition.destinationState == null || transition.conditions == null)
                {
                    continue;
                }

                foreach (var condition in transition.conditions)
                {
                    if (condition.mode != AnimatorConditionMode.Equals)
                    {
                        continue;
                    }
                    sink.Add(new AnyStateEquals
                    {
                        stateName = transition.destinationState.name ?? "",
                        parameter = condition.parameter ?? "",
                        value = Mathf.RoundToInt(condition.threshold)
                    });
                }
            }

            foreach (var sub in machine.stateMachines)
            {
                CollectAnyStateEquals(sub.stateMachine, sink);
            }
        }

        private static void ReadClipToggles(AnimationClip clip, List<string> onObjects, List<string> offObjects)
        {
            if (clip == null)
            {
                return;
            }

            foreach (var binding in AnimationUtility.GetCurveBindings(clip))
            {
                if (binding.type != typeof(GameObject) || binding.propertyName != "m_IsActive")
                {
                    continue;
                }

                var curve = AnimationUtility.GetEditorCurve(clip, binding);
                if (curve == null || curve.length == 0)
                {
                    continue;
                }

                var lastValue = curve.keys[curve.length - 1].value;
                if (lastValue >= 0.5f)
                {
                    onObjects.Add(binding.path);
                }
                else
                {
                    offObjects.Add(binding.path);
                }
            }
        }

        // --- 4. Reconcile the triangle -----------------------------------------------

        private static List<object> BuildWardrobes(
            List<ParamInfo> intParameters,
            List<MenuToggle> menuToggles,
            List<FxLayerInfo> fxLayers)
        {
            var ranked = new List<KeyValuePair<float, object>>();

            foreach (var param in intParameters)
            {
                var togglesForParam = menuToggles
                    .Where(toggle => string.Equals(toggle.parameterName, param.name, StringComparison.Ordinal))
                    .ToList();

                var layer = fxLayers.FirstOrDefault(item =>
                    item.anyStateEquals.Any(equals => string.Equals(equals.parameter, param.name, StringComparison.Ordinal)));

                var hasToggles = togglesForParam.Count > 0;
                var hasFxLayer = layer != null;
                if (!hasToggles && !hasFxLayer)
                {
                    continue; // this int param is not a wardrobe.
                }

                var equalsForParam = layer != null
                    ? layer.anyStateEquals.Where(equals => string.Equals(equals.parameter, param.name, StringComparison.Ordinal)).ToList()
                    : new List<AnyStateEquals>();

                var stateByName = layer != null
                    ? layer.states.GroupBy(state => state.name).ToDictionary(group => group.Key, group => group.First())
                    : new Dictionary<string, FxState>();

                var values = new SortedSet<int>();
                foreach (var toggle in togglesForParam)
                {
                    values.Add(toggle.value);
                }
                foreach (var equals in equalsForParam)
                {
                    values.Add(equals.value);
                }

                var outfits = new List<object>();
                foreach (var value in values)
                {
                    var toggle = togglesForParam.FirstOrDefault(item => item.value == value);
                    var equals = equalsForParam.FirstOrDefault(item => item.value == value);
                    FxState state = null;
                    if (equals != null && stateByName.ContainsKey(equals.stateName))
                    {
                        state = stateByName[equals.stateName];
                    }

                    var onObjects = state != null ? state.onObjects : new List<string>();
                    var offObjects = state != null ? state.offObjects : new List<string>();

                    outfits.Add(new
                    {
                        value,
                        menuName = toggle?.menuName ?? "",
                        menuPath = toggle?.menuPath ?? "",
                        inMenu = toggle != null,
                        fxStateName = state?.name ?? (equals?.stateName ?? ""),
                        inFx = equals != null,
                        clipPath = state?.clipPath ?? "",
                        writeDefaults = state?.writeDefaults ?? false,
                        onObjects,
                        offObjects,
                        isStripOrDefaultCandidate = state != null && onObjects.Count == 0
                    });
                }

                var wdStates = (layer?.states ?? new List<FxState>())
                    .Where(state => equalsForParam.Any(equals => equals.stateName == state.name))
                    .ToList();
                var writeDefaultsAllOn = wdStates.Count > 0 && wdStates.All(state => state.writeDefaults);
                var writeDefaultsConsistent = wdStates.Count == 0 || wdStates.All(state => state.writeDefaults == wdStates[0].writeDefaults);

                var nameHasKeyword = HasWardrobeKeyword(param.name)
                    || (layer != null && HasWardrobeKeyword(layer.name))
                    || togglesForParam.Any(toggle => HasWardrobeKeyword(toggle.menuName) || HasWardrobeKeyword(toggle.menuPath));

                var signals = new List<string>();
                var confidence = 0f;
                if (values.Count >= 2 && togglesForParam.Count >= 2)
                {
                    confidence += 0.4f;
                    signals.Add("multiple menu toggles share this int with distinct values");
                }
                if (equalsForParam.Count >= 2)
                {
                    confidence += 0.4f;
                    signals.Add("FX layer has multiple Any-State Equals transitions on this int");
                }
                if (hasToggles && hasFxLayer)
                {
                    confidence += 0.1f;
                    signals.Add("menu and FX agree on the same int parameter");
                }
                if (nameHasKeyword)
                {
                    confidence += 0.1f;
                    signals.Add("name/path contains a wardrobe keyword");
                }
                confidence = Mathf.Clamp01(confidence);

                ranked.Add(new KeyValuePair<float, object>(confidence, new
                {
                    parameterName = param.name,
                    parameterDefault = param.defaultValue,
                    parameterSaved = param.saved,
                    parameterNetworkSynced = param.networkSynced,
                    fxLayerName = layer?.name ?? "",
                    fxDefaultStateName = layer?.defaultStateName ?? "",
                    outfitCount = outfits.Count,
                    writeDefaultsAllOn,
                    writeDefaultsConsistent,
                    confidence,
                    signals,
                    outfits
                }));
            }

            return ranked
                .OrderByDescending(item => item.Key)
                .Select(item => item.Value)
                .ToList();
        }

        private static readonly string[] WardrobeKeywords =
        {
            "wardrobe", "outfit", "cloth", "clothes", "clothing", "dress", "costume", "wear",
            "衣柜", "衣装", "衣服", "服装", "服", "换装", "外套", "全脱", "脱"
        };

        private static bool HasWardrobeKeyword(string value)
        {
            if (string.IsNullOrWhiteSpace(value))
            {
                return false;
            }
            foreach (var keyword in WardrobeKeywords)
            {
                if (value.IndexOf(keyword, StringComparison.OrdinalIgnoreCase) >= 0)
                {
                    return true;
                }
            }
            return false;
        }

        // --- Shared helpers ----------------------------------------------------------

        private static VRCAvatarDescriptor ResolveAvatarDescriptor(string avatarPath)
        {
            var descriptors = Resources.FindObjectsOfTypeAll<VRCAvatarDescriptor>()
                .Where(item => item != null && item.gameObject.scene.IsValid() && item.gameObject.scene.isLoaded && !EditorUtility.IsPersistent(item))
                .OrderBy(item => item.name)
                .ToList();
            if (descriptors.Count == 0)
            {
                throw new InvalidOperationException("No scene VRChat avatar descriptor was found.");
            }

            var normalizedAvatarPath = NormalizePath(avatarPath);
            if (string.IsNullOrEmpty(normalizedAvatarPath))
            {
                return descriptors[0];
            }

            return descriptors.FirstOrDefault(item => NormalizePath(GetTransformPath(item.transform)) == normalizedAvatarPath)
                ?? descriptors.FirstOrDefault(item => item.name.Equals(avatarPath, StringComparison.OrdinalIgnoreCase))
                ?? throw new InvalidOperationException($"Avatar descriptor not found: {avatarPath}");
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

        private static string NormalizePath(string value)
        {
            return (value ?? string.Empty).Replace("\\", "/").Trim().Trim('/');
        }

        private static string WriteJsonIfRequested(string outputPath, object payload)
        {
            if (string.IsNullOrWhiteSpace(outputPath))
            {
                return "";
            }

            var absolutePath = ResolveToAbsolutePath(outputPath);
            var directory = Path.GetDirectoryName(absolutePath);
            if (!string.IsNullOrEmpty(directory))
            {
                Directory.CreateDirectory(directory);
            }

            File.WriteAllText(
                absolutePath,
                JsonConvert.SerializeObject(payload, Formatting.Indented),
                Encoding.UTF8);
            AssetDatabase.Refresh();
            return absolutePath.Replace("\\", "/");
        }

        private static string ResolveToAbsolutePath(string requestedPath)
        {
            if (Path.IsPathRooted(requestedPath))
            {
                return requestedPath.Replace("\\", "/");
            }

            var projectRoot = Directory.GetParent(Application.dataPath)?.FullName
                ?? throw new InvalidOperationException("Cannot determine Unity project root.");
            return Path.Combine(projectRoot, requestedPath).Replace("\\", "/");
        }

        private class ParamInfo
        {
            public string name;
            public int defaultValue;
            public bool saved;
            public bool networkSynced;
        }

        private class MenuToggle
        {
            public string menuName;
            public string menuPath;
            public string parameterName;
            public int value;
            public string controlType;
        }

        private class FxLayerInfo
        {
            public string name;
            public string defaultStateName;
            public List<FxState> states;
            public List<AnyStateEquals> anyStateEquals;
        }

        private class FxState
        {
            public string name;
            public string motionName;
            public string clipPath;
            public bool writeDefaults;
            public List<string> onObjects;
            public List<string> offObjects;
        }

        private class AnyStateEquals
        {
            public string stateName;
            public string parameter;
            public int value;
        }
    }
}
