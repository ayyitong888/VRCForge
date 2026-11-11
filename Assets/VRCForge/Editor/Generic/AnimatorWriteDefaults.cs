using System;
using System.Collections.Generic;
using System.Linq;
using UnityEditor;
using UnityEditor.Animations;
using UnityEngine;
using VRC.SDK3.Avatars.Components;

namespace VRCForge.Editor
{
    internal static class AnimatorWriteDefaults
    {
        private const string TimingPath = "__VRCForgeWriteDefaultsTiming";
        private const string TimingProperty = "VRCForgeWriteDefaultsTiming";

        internal static bool Resolve(AnimatorController controller, AnimatorControllerLayer targetLayer, bool? requested,
            AnimatorState existing = null, VRCAvatarDescriptor avatar = null)
        {
            if (requested.HasValue) return requested.Value;
            if (existing != null) return existing.writeDefaultValues;

            return ResolveNew(targetLayer?.name, targetLayer?.blendingMode == AnimatorLayerBlendingMode.Additive,
                OrdinaryValues(targetLayer), (controller?.layers ?? Array.Empty<AnimatorControllerLayer>()).SelectMany(OrdinaryValues),
                GetAvatarOrdinaryValues(avatar));
        }

        internal static bool? GetLayerConvention(AnimatorControllerLayer layer)
        {
            var values = OrdinaryValues(layer).Distinct().ToArray();
            return values.Length == 1 ? (bool?)values[0] : null;
        }

        internal static bool ResolveNew(string layerName, bool additive, IEnumerable<bool> layerValues,
            IEnumerable<bool> controllerValues, IEnumerable<bool> avatarValues = null)
        {
            if (additive) return true;

            var values = layerValues.Distinct().ToArray();
            if (values.Length > 1)
                throw new InvalidOperationException($"FX layer '{layerName}' mixes Write Defaults. Specify writeDefaults explicitly for the new state.");
            if (values.Length == 1) return values[0];

            values = controllerValues.Distinct().ToArray();
            if (values.Length > 1)
                throw new InvalidOperationException("The FX controller mixes Write Defaults and the target layer has no ordinary states. Specify writeDefaults explicitly for the new state.");
            if (values.Length == 1) return values[0];

            values = (avatarValues ?? Enumerable.Empty<bool>()).Distinct().ToArray();
            if (values.Length > 1)
                throw new InvalidOperationException("The avatar's other playable controllers mix Write Defaults. Specify writeDefaults explicitly for the new state.");
            return values.Length == 0 || values[0];
        }

        internal static bool IsAvatarFxController(VRCAvatarDescriptor avatar, AnimatorController controller) =>
            avatar != null && controller != null && (avatar.baseAnimationLayers ?? Array.Empty<VRCAvatarDescriptor.CustomAnimLayer>())
                .Any(slot => slot.type == VRCAvatarDescriptor.AnimLayerType.FX && !slot.isDefault && slot.animatorController == controller);

        internal static IEnumerable<bool> GetAvatarOrdinaryValues(VRCAvatarDescriptor avatar)
        {
            if (avatar == null || !avatar.customizeAnimationLayers) yield break;
            var slots = (avatar.baseAnimationLayers ?? Array.Empty<VRCAvatarDescriptor.CustomAnimLayer>())
                .Concat(avatar.specialAnimationLayers ?? Array.Empty<VRCAvatarDescriptor.CustomAnimLayer>());
            foreach (var slot in slots)
            {
                if (slot.isDefault || slot.type == VRCAvatarDescriptor.AnimLayerType.FX
                    || slot.type == VRCAvatarDescriptor.AnimLayerType.Additive) continue;
                var runtimeController = slot.animatorController;
                while (runtimeController is AnimatorOverrideController wrapper) runtimeController = wrapper.runtimeAnimatorController;
                if (!(runtimeController is AnimatorController controller)) continue;
                foreach (var value in controller.layers.SelectMany(OrdinaryValues)) yield return value;
            }
        }

        internal static bool HasEmptyMotion(Motion motion) => motion == null
            || (motion is AnimationClip clip && clip.empty)
            || (motion is BlendTree tree && tree.children.Any(child => HasEmptyMotion(child.motion)));

        internal static void RequireUsableMotion(string stateName, bool writeDefaults, bool emptyMotion)
        {
            if (!writeDefaults && emptyMotion)
                throw new InvalidOperationException($"Write Defaults is Off on state '{stateName}'. Use a non-empty motion clip (motionClipPath) before writing this state.");
        }

        internal static void AddTimingCurve(AnimationClip clip)
        {
            // A reserved, nonexistent binding keeps a generated placeholder non-empty without animating the avatar.
            var binding = EditorCurveBinding.FloatCurve(TimingPath, typeof(GameObject), TimingProperty);
            AnimationUtility.SetEditorCurve(clip, binding, AnimationCurve.Constant(0f, 1f / 60f, 0f));
        }

        internal static bool IsTimingCurve(EditorCurveBinding binding) => !binding.isPPtrCurve
            && binding.type == typeof(GameObject) && binding.path == TimingPath && binding.propertyName == TimingProperty;

        internal static bool IsDirectBlendTree(AnimatorState state) =>
            state.motion is BlendTree tree && tree.blendType == BlendTreeType.Direct;

        private static IEnumerable<bool> OrdinaryValues(AnimatorControllerLayer layer) =>
            layer == null || layer.blendingMode == AnimatorLayerBlendingMode.Additive
                ? Enumerable.Empty<bool>() : OrdinaryValues(layer.stateMachine);

        private static IEnumerable<bool> OrdinaryValues(AnimatorStateMachine machine)
        {
            if (machine == null) yield break;
            foreach (var child in machine.states)
                if (child.state != null && !IsDirectBlendTree(child.state))
                    yield return child.state.writeDefaultValues;
            foreach (var child in machine.stateMachines)
                foreach (var value in OrdinaryValues(child.stateMachine))
                    yield return value;
        }
    }
}
