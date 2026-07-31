using System;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;
using System.Security.Cryptography;
using System.Text;
using UnityEditor;
using UnityEditor.Animations;
using UnityEngine;
using VRC.SDK3.Avatars.Components;
using VRC.SDK3.Avatars.ScriptableObjects;
using Object = UnityEngine.Object;

namespace VRCForge.Editor
{
    /// <summary>
    /// Read-only semantic evidence for the fixed parameter packing writer.
    /// The projection deliberately avoids raw scene/prefab JSON and instance IDs.
    /// </summary>
    internal static class ParameterBitPackingEvidence
    {
        internal const string EvidenceSchema = "vrcforge.parameter_behavior_evidence.v1";
        internal const string ProofSchema = "vrcforge.parameter_behavior_proof.v1";
        private const string ParameterDriverType =
            "VRC.SDK3.Avatars.Components.VRCAvatarParameterDriver";

        internal static ParameterBehaviorEvidence Capture(
            GameObject avatar,
            Action<string> setStage = null)
        {
            setStage?.Invoke("root");
            Require(avatar != null, "The avatar evidence root is missing.");
            var descriptor = avatar.GetComponent<VRCAvatarDescriptor>();
            Require(descriptor != null, "The avatar evidence root has no descriptor.");
            Require(
                descriptor.expressionParameters != null
                    && descriptor.expressionParameters.parameters != null,
                "The avatar expression parameters are unavailable.");

            setStage?.Invoke("parameters");
            var parameters = CaptureParameters(descriptor.expressionParameters);
            setStage?.Invoke("menu");
            var menuRows = CaptureMenuRows(descriptor.expressionsMenu);
            setStage?.Invoke("animator");
            var animatorRows = CaptureAnimatorRows(descriptor, setStage);
            setStage?.Invoke("portable");
            var portablePropertyCategories = new Dictionary<string, List<string>>(StringComparer.Ordinal);
            var descriptorPropertyGroups = new Dictionary<string, List<string>>(StringComparer.Ordinal);
            var portableRows = CapturePortableRows(
                avatar,
                portablePropertyCategories,
                descriptorPropertyGroups);
            setStage?.Invoke("digests");
            return new ParameterBehaviorEvidence
            {
                Parameters = parameters,
                MenuRows = menuRows,
                AnimatorRows = animatorRows,
                PortableAvatarDigest = DigestRows(
                    "vrcforge.portable_avatar.v1",
                    portableRows),
                PortableObjectDigest = DigestRows(
                    "vrcforge.portable_avatar.objects.v1",
                    portableRows.Where(row => row.StartsWith(Frame("object"), StringComparison.Ordinal))),
                PortableComponentDigest = DigestRows(
                    "vrcforge.portable_avatar.components.v1",
                    portableRows.Where(row => row.StartsWith(Frame("component"), StringComparison.Ordinal))),
                PortablePropertyDigest = DigestRows(
                    "vrcforge.portable_avatar.properties.v1",
                    portableRows.Where(row => row.StartsWith(Frame("property"), StringComparison.Ordinal))),
                PortableTransformEditorPropertyDigest = DigestRows(
                    "vrcforge.portable_avatar.properties.transform_editor.v1",
                    portablePropertyCategories["transform_editor"]),
                PortableTransformRuntimePropertyDigest = DigestRows(
                    "vrcforge.portable_avatar.properties.transform_runtime.v1",
                    portablePropertyCategories["transform_spatial"]
                        .Concat(portablePropertyCategories["transform_hierarchy"])
                        .Concat(portablePropertyCategories["transform_other"])),
                PortableTransformSpatialPropertyDigest = DigestRows(
                    "vrcforge.portable_avatar.properties.transform_spatial.v1",
                    portablePropertyCategories["transform_spatial"]),
                PortableTransformHierarchyPropertyDigest = DigestRows(
                    "vrcforge.portable_avatar.properties.transform_hierarchy.v1",
                    portablePropertyCategories["transform_hierarchy"]),
                PortableTransformOtherPropertyDigest = DigestRows(
                    "vrcforge.portable_avatar.properties.transform_other.v1",
                    portablePropertyCategories["transform_other"]),
                PortableDescriptorPropertyDigest = DigestRows(
                    "vrcforge.portable_avatar.properties.descriptor.v1",
                    portablePropertyCategories["descriptor"]),
                PortableDescriptorPropertyGroupDigests = descriptorPropertyGroups.ToDictionary(
                    pair => pair.Key,
                    pair => DigestRows(
                        "vrcforge.portable_avatar.properties.descriptor_group.v1",
                        pair.Value),
                    StringComparer.Ordinal),
                PortableOtherPropertyDigest = DigestRows(
                    "vrcforge.portable_avatar.properties.other.v1",
                    portablePropertyCategories["other"]),
                OrderedParameterDigest = DigestRows(
                    "vrcforge.ordered_parameters.v1",
                    parameters.Select(row => row.Canonical)),
                MenuGraphDigest = DigestRows(
                    "vrcforge.menu_graph.v1",
                    menuRows.Select(row => row.Canonical)),
                AnimatorBehaviorDigest = DigestRows(
                    "vrcforge.animator_behavior.v1",
                    animatorRows.Select(row => row.Canonical))
            };
        }

        internal static ParameterBehaviorProof VerifyBehavior(
            ParameterBehaviorEvidence source,
            ParameterBehaviorEvidence output,
            IReadOnlyCollection<string> compressedNames,
            IReadOnlyCollection<string> excludedNames,
            Action<string> setStage = null)
        {
            setStage?.Invoke("evidence");
            Require(source != null && output != null, "Behavior evidence is incomplete.");
            var compressed = new HashSet<string>(compressedNames ?? Array.Empty<string>(), StringComparer.Ordinal);
            var excluded = new HashSet<string>(excludedNames ?? Array.Empty<string>(), StringComparer.Ordinal);
            setStage?.Invoke("parameter_sets");
            Require(compressed.Count > 0, "No compressed parameter was available for behavior proof.");
            Require(!compressed.Overlaps(excluded), "A compressed parameter is excluded from compression.");

            setStage?.Invoke("parameter_index");
            var outputByName = output.Parameters.ToDictionary(row => row.Name, StringComparer.Ordinal);
            var sourceNames = new HashSet<string>(source.Parameters.Select(row => row.Name), StringComparer.Ordinal);
            var lastOutputIndex = -1;
            foreach (var before in source.Parameters.OrderBy(row => row.Index))
            {
                Require(outputByName.TryGetValue(before.Name, out var after), "The output removed a source parameter.");
                Require(after.Index > lastOutputIndex, "The output changed source parameter order.");
                lastOutputIndex = after.Index;
                Require(
                    after.Type == before.Type
                        && after.DefaultValue == before.DefaultValue
                        && after.Saved == before.Saved,
                    "The output changed source parameter semantics.");
                if (compressed.Contains(before.Name))
                {
                    Require(before.NetworkSynced && !after.NetworkSynced, "A compressed parameter has invalid synchronization state.");
                }
                else
                {
                    Require(after.NetworkSynced == before.NetworkSynced, "An uncompressed parameter changed synchronization state.");
                }
            }

            setStage?.Invoke("menu_subset");
            RequireMultisetSubset(
                source.MenuRows.Select(row => row.Canonical),
                output.MenuRows.Select(row => row.Canonical),
                "The output changed a source menu control.");
            var sourceControllerParameters = source.AnimatorRows
                .Where(row => row.Kind == "controller_parameter")
                .ToArray();
            var outputControllerParameters = output.AnimatorRows
                .Where(row => row.Kind == "controller_parameter")
                .ToArray();
            setStage?.Invoke("animator_subset_controller_parameter_name");
            RequireMultisetSubset(
                sourceControllerParameters.Select(row => Frame(row.SemanticName)),
                outputControllerParameters.Select(row => Frame(row.SemanticName)),
                "The output changed an existing animator parameter name.");
            setStage?.Invoke("animator_subset_controller_parameter_scope");
            RequireMultisetSubset(
                sourceControllerParameters.Select(row => Frame(row.Scope) + Frame(row.SemanticName)),
                outputControllerParameters.Select(row => Frame(row.Scope) + Frame(row.SemanticName)),
                "The output moved an existing animator parameter to another controller role.");
            setStage?.Invoke("animator_subset_controller_parameter_type");
            RequireMultisetSubset(
                sourceControllerParameters.Select(row =>
                    Frame(row.Scope) + Frame(row.SemanticName) + Frame(row.SemanticType)),
                outputControllerParameters.Select(row =>
                    Frame(row.Scope) + Frame(row.SemanticName) + Frame(row.SemanticType)),
                "The output changed an existing animator parameter type.");
            setStage?.Invoke("animator_subset_controller_parameter_default");
            RequireMultisetSubset(
                sourceControllerParameters.Select(row =>
                    Frame(row.Scope) + Frame(row.SemanticName) + Frame(row.SemanticType)
                        + Frame(row.SemanticDefault)),
                outputControllerParameters.Select(row =>
                    Frame(row.Scope) + Frame(row.SemanticName) + Frame(row.SemanticType)
                        + Frame(row.SemanticDefault)),
                "The output changed an existing animator parameter default.");
            var sourceLayers = source.AnimatorRows.Where(row => row.Kind == "layer").ToArray();
            var outputLayers = output.AnimatorRows.Where(row => row.Kind == "layer").ToArray();
            setStage?.Invoke("animator_subset_layer_scope");
            RequireMultisetSubset(
                sourceLayers.Select(row => Frame(row.Scope)),
                outputLayers.Select(row => Frame(row.Scope)),
                "The output removed an existing animator layer role.");
            setStage?.Invoke("animator_subset_layer_name");
            RequireMultisetSubset(
                sourceLayers.Select(row => Frame(row.Scope) + Frame(row.SemanticName)),
                outputLayers.Select(row => Frame(row.Scope) + Frame(row.SemanticName)),
                "The output renamed an existing animator layer.");
            var layerFieldNames = new[] { "weight", "blending", "mask", "ik", "sync_target", "sync_timing" };
            for (var fieldIndex = 0; fieldIndex < layerFieldNames.Length; fieldIndex++)
            {
                var fieldCount = fieldIndex + 1;
                var fieldStage = "animator_subset_layer_" + layerFieldNames[fieldIndex];
                if (fieldIndex == 2)
                {
                    var mismatchCategory = FirstLayerMaskMismatchCategory(sourceLayers, outputLayers);
                    if (mismatchCategory != "none") fieldStage += "_" + mismatchCategory;
                }
                setStage?.Invoke(fieldStage);
                RequireMultisetSubset(
                    sourceLayers.Select(row =>
                        Frame(row.Scope) + Frame(row.SemanticName)
                            + string.Concat(row.SemanticFields.Take(fieldCount).Select(Frame))),
                    outputLayers.Select(row =>
                        Frame(row.Scope) + Frame(row.SemanticName)
                            + string.Concat(row.SemanticFields.Take(fieldCount).Select(Frame))),
                    "The output changed existing animator layer settings.");
            }
            var sourceTransitions = source.AnimatorRows.Where(row => row.Kind == "transition").ToArray();
            var outputTransitions = output.AnimatorRows.Where(row => row.Kind == "transition").ToArray();
            setStage?.Invoke("animator_subset_transition_identity");
            RequireMultisetSubset(
                sourceTransitions.Select(row => Frame(row.Scope) + Frame(row.SemanticName)),
                outputTransitions.Select(row => Frame(row.Scope) + Frame(row.SemanticName)),
                "The output removed or reordered an existing animator transition.");
            var transitionFieldNames = new[]
            {
                "destination", "exit", "mute", "solo", "conditions", "duration", "exit_time",
                "has_exit_time", "fixed_duration", "interruption", "offset", "ordered", "self"
            };
            for (var fieldIndex = 0; fieldIndex < transitionFieldNames.Length; fieldIndex++)
            {
                var fieldCount = fieldIndex + 1;
                setStage?.Invoke("animator_subset_transition_" + transitionFieldNames[fieldIndex]);
                RequireMultisetSubset(
                    sourceTransitions.Select(row =>
                        Frame(row.Scope) + Frame(row.SemanticName)
                            + string.Concat(row.SemanticFields.Take(fieldCount).Select(Frame))),
                    outputTransitions.Select(row =>
                        Frame(row.Scope) + Frame(row.SemanticName)
                            + string.Concat(row.SemanticFields.Take(fieldCount).Select(Frame))),
                    "The output changed an existing animator transition.");
            }
            foreach (var kind in new[]
            {
                "override_controller",
                "state_machine",
                "state_machine_position",
                "state",
                "behaviour",
                "driver",
                "motion",
                "blend_tree",
                "blend_child"
            })
            {
                setStage?.Invoke("animator_subset_" + kind);
                RequireMultisetSubset(
                    source.AnimatorRows.Where(row => row.Kind == kind).Select(row => row.Canonical),
                    output.AnimatorRows.Where(row => row.Kind == kind).Select(row => row.Canonical),
                    "The output changed existing animator behavior.");
            }
            setStage?.Invoke("animator_subset_complete");
            RequireMultisetSubset(
                source.AnimatorRows.Select(row => row.Canonical),
                output.AnimatorRows.Select(row => row.Canonical),
                "The output changed existing animator behavior.");

            var addedAnimatorRows = SubtractRows(source.AnimatorRows, output.AnimatorRows);
            setStage?.Invoke("excluded_codec_references");
            foreach (var name in excluded)
            {
                Require(
                    !addedAnimatorRows.Any(row => row.ParameterNames.Contains(name)),
                    "The generated codec references an excluded parameter.");
            }

            setStage?.Invoke("codec_edges");
            var edges = addedAnimatorRows
                .Where(row => row.Kind == "driver" && !string.IsNullOrWhiteSpace(row.SourceParameter)
                    && !string.IsNullOrWhiteSpace(row.DestinationParameter))
                .ToArray();
            var outputParameters = output.Parameters.ToDictionary(row => row.Name, StringComparer.Ordinal);
            var mappingRows = new List<string>();
            setStage?.Invoke("carrier_graph");
            foreach (var name in compressed.OrderBy(value => value, StringComparer.Ordinal))
            {
                var forward = Reachable(name, edges, reverse: false);
                var backward = Reachable(name, edges, reverse: true);
                var carriers = forward.Intersect(backward, StringComparer.Ordinal)
                    .Where(candidate => !sourceNames.Contains(candidate))
                    .Where(candidate => outputParameters.TryGetValue(candidate, out var row) && row.NetworkSynced)
                    .OrderBy(candidate => candidate, StringComparer.Ordinal)
                    .ToArray();
                Require(carriers.Length > 0, "A compressed parameter has no verified bidirectional synchronized carrier.");

                var relevantRows = addedAnimatorRows
                    .Where(row => row.ParameterNames.Contains(name)
                        || row.ParameterNames.Any(parameter => carriers.Contains(parameter, StringComparer.Ordinal)))
                    .Select(row => row.Canonical)
                    .OrderBy(row => row, StringComparer.Ordinal)
                    .ToArray();
                Require(relevantRows.Length > 0, "A compressed parameter has no generated controller proof.");
                mappingRows.Add(
                    Frame(name)
                    + Frame(string.Join("\n", carriers))
                    + Frame(DigestRows("vrcforge.codec_parameter_rows.v1", relevantRows)));
            }

            setStage?.Invoke("excluded_state");
            var excludedBefore = source.Parameters
                .Where(row => excluded.Contains(row.Name))
                .OrderBy(row => row.Name, StringComparer.Ordinal)
                .Select(row => Frame(row.Name) + Frame(row.Type) + Frame(row.DefaultValue)
                    + Frame(row.Saved) + Frame(row.NetworkSynced))
                .ToArray();
            var excludedAfter = output.Parameters
                .Where(row => excluded.Contains(row.Name))
                .OrderBy(row => row.Name, StringComparer.Ordinal)
                .Select(row => Frame(row.Name) + Frame(row.Type) + Frame(row.DefaultValue)
                    + Frame(row.Saved) + Frame(row.NetworkSynced))
                .ToArray();
            Require(excludedBefore.SequenceEqual(excludedAfter, StringComparer.Ordinal), "Excluded parameter state changed.");

            setStage?.Invoke("codec_graph");
            var codecRows = addedAnimatorRows
                .Where(row => row.ParameterNames.Any(parameter => compressed.Contains(parameter)
                    || !sourceNames.Contains(parameter)))
                .Select(row => row.Canonical)
                .OrderBy(row => row, StringComparer.Ordinal)
                .ToArray();
            Require(codecRows.Length > 0, "No generated codec graph was proven.");

            setStage?.Invoke("receipt");
            return new ParameterBehaviorProof
            {
                Status = "verified",
                PlatformScope = "current-target-only",
                SourceOrderedParameterDigest = source.OrderedParameterDigest,
                OutputOrderedParameterDigest = output.OrderedParameterDigest,
                SourceParameterCount = source.Parameters.Count,
                OutputParameterCount = output.Parameters.Count,
                SourceMenuGraphDigest = source.MenuGraphDigest,
                OutputMenuGraphDigest = output.MenuGraphDigest,
                SourceMenuRowCount = source.MenuRows.Count,
                OutputMenuRowCount = output.MenuRows.Count,
                SourceAnimatorBehaviorDigest = source.AnimatorBehaviorDigest,
                OutputAnimatorBehaviorDigest = output.AnimatorBehaviorDigest,
                SourceAnimatorRowCount = source.AnimatorRows.Count,
                OutputAnimatorRowCount = output.AnimatorRows.Count,
                PreservedBehaviorDigest = DigestRows(
                    "vrcforge.preserved_parameter_behavior.v1",
                    source.MenuRows.Select(row => row.Canonical)
                        .Concat(source.AnimatorRows.Select(row => row.Canonical))),
                CodecGraphDigest = DigestRows("vrcforge.parameter_codec_graph.v1", codecRows),
                CodecMappingDigest = DigestRows("vrcforge.parameter_codec_mapping.v1", mappingRows),
                CodecMappingCount = mappingRows.Count,
                ExcludedBeforeDigest = DigestRows("vrcforge.excluded_behavior.v1", excludedBefore),
                ExcludedAfterDigest = DigestRows("vrcforge.excluded_behavior.v1", excludedAfter)
            };
        }

        private static List<ParameterEvidenceRow> CaptureParameters(VRCExpressionParameters parameters)
        {
            var field = typeof(VRCExpressionParameters.Parameter).GetField(
                "networkSynced",
                System.Reflection.BindingFlags.Public
                    | System.Reflection.BindingFlags.NonPublic
                    | System.Reflection.BindingFlags.Instance);
            Require(field != null && field.FieldType == typeof(bool), "The parameter synchronization layout is unsupported.");
            var result = new List<ParameterEvidenceRow>();
            var names = new HashSet<string>(StringComparer.Ordinal);
            for (var index = 0; index < parameters.parameters.Length; index++)
            {
                var parameter = parameters.parameters[index];
                Require(parameter != null && !string.IsNullOrWhiteSpace(parameter.name), "An expression parameter is invalid.");
                Require(names.Add(parameter.name), "Expression parameter names are not unique.");
                result.Add(new ParameterEvidenceRow
                {
                    Index = index,
                    Name = parameter.name,
                    Type = parameter.valueType.ToString(),
                    DefaultValue = FloatText(parameter.defaultValue),
                    Saved = parameter.saved,
                    NetworkSynced = (bool)field.GetValue(parameter)
                });
            }
            return result;
        }

        private static List<MenuEvidenceRow> CaptureMenuRows(VRCExpressionsMenu root)
        {
            var rows = new List<MenuEvidenceRow>();
            var active = new HashSet<int>();
            void Walk(VRCExpressionsMenu menu, string path, int depth)
            {
                Require(depth <= 64, "The expression menu graph is too deep.");
                if (menu == null) return;
                Require(active.Add(menu.GetInstanceID()), "The expression menu graph contains a cycle.");
                var controls = menu.controls ?? new List<VRCExpressionsMenu.Control>();
                for (var index = 0; index < controls.Count; index++)
                {
                    var control = controls[index];
                    Require(control != null, "The expression menu contains an invalid control.");
                    var controlPath = path + "/" + index.ToString(CultureInfo.InvariantCulture);
                    var subParameters = (control.subParameters ?? Array.Empty<VRCExpressionsMenu.Control.Parameter>())
                        .Select(parameter => parameter == null ? string.Empty : parameter.name ?? string.Empty)
                        .ToArray();
                    rows.Add(new MenuEvidenceRow
                    {
                        Path = controlPath,
                        Name = control.name ?? string.Empty,
                        Type = control.type.ToString(),
                        Parameter = control.parameter == null ? string.Empty : control.parameter.name ?? string.Empty,
                        SubParameters = subParameters,
                        Value = FloatText(control.value),
                        Icon = ObjectToken(control.icon, null),
                        HasSubMenu = control.subMenu != null
                    });
                    Walk(control.subMenu, controlPath, depth + 1);
                }
                active.Remove(menu.GetInstanceID());
            }
            Walk(root, "menu", 0);
            return rows.OrderBy(row => row.Path, StringComparer.Ordinal).ToList();
        }

        private static List<AnimatorEvidenceRow> CaptureAnimatorRows(
            VRCAvatarDescriptor descriptor,
            Action<string> setStage)
        {
            var rows = new List<AnimatorEvidenceRow>();
            setStage?.Invoke("animator_base");
            CaptureLayers(descriptor.baseAnimationLayers, "base", rows, setStage);
            setStage?.Invoke("animator_special");
            CaptureLayers(descriptor.specialAnimationLayers, "special", rows, setStage);
            return rows.OrderBy(row => row.Canonical, StringComparer.Ordinal).ToList();
        }

        private static void CaptureLayers(
            VRCAvatarDescriptor.CustomAnimLayer[] layers,
            string group,
            ICollection<AnimatorEvidenceRow> rows,
            Action<string> setStage)
        {
            var values = layers ?? Array.Empty<VRCAvatarDescriptor.CustomAnimLayer>();
            var activeRoles = new HashSet<string>(StringComparer.Ordinal);
            for (var layerSlot = 0; layerSlot < values.Length; layerSlot++)
            {
                var value = values[layerSlot];
                if (value.animatorController == null) continue;
                var role = group + ":" + value.type;
                Require(activeRoles.Add(role), "Animator controller roles must be unique within a layer group.");
                setStage?.Invoke("animator_" + group + "_controller_type");
                AnimatorController controller;
                if (value.animatorController is AnimatorController directController)
                {
                    controller = directController;
                }
                else if (value.animatorController is AnimatorOverrideController overrideController)
                {
                    controller = overrideController.runtimeAnimatorController as AnimatorController;
                    Require(controller != null, "An animator override controller has no direct base controller.");
                    var overrides = new List<KeyValuePair<AnimationClip, AnimationClip>>();
                    overrideController.GetOverrides(overrides);
                    rows.Add(AnimatorEvidenceRow.Simple(
                        "override_controller",
                        Frame(role) + string.Concat(overrides
                            .Select(pair => Frame(ControllerObjectToken(pair.Key, controller)) + Frame(ControllerObjectToken(pair.Value, controller)))
                            .OrderBy(row => row, StringComparer.Ordinal))));
                }
                else
                {
                    throw new InvalidOperationException("The animator controller type is unsupported by behavior proof.");
                }
                setStage?.Invoke("animator_" + group + "_controller_parameters");
                var controllerParameters = controller.parameters ?? Array.Empty<AnimatorControllerParameter>();
                for (var parameterIndex = 0; parameterIndex < controllerParameters.Length; parameterIndex++)
                {
                    var parameter = controllerParameters[parameterIndex];
                    var semanticDefault = ControllerParameterDefault(parameter);
                    rows.Add(new AnimatorEvidenceRow
                    {
                        Kind = "controller_parameter",
                        Body = Frame(role) + Frame(parameter.name) + Frame(parameter.type)
                            + Frame(semanticDefault),
                        ParameterNames = new HashSet<string>(new[] { parameter.name }, StringComparer.Ordinal),
                        Scope = role,
                        SemanticName = parameter.name,
                        SemanticType = parameter.type.ToString(),
                        SemanticDefault = semanticDefault
                    });
                }
                setStage?.Invoke("animator_" + group + "_controller_layers");
                var controllerLayers = controller.layers ?? Array.Empty<AnimatorControllerLayer>();
                var layerNames = new HashSet<string>(StringComparer.Ordinal);
                for (var index = 0; index < controllerLayers.Length; index++)
                {
                    var layer = controllerLayers[index];
                    Require(layer.stateMachine != null, "An animator layer has no state machine.");
                    Require(layerNames.Add(layer.name ?? string.Empty), "Animator layer names must be unique within a controller.");
                    var layerPath = role + "/layer:" + layer.name;
                    var synchronizedLayer = string.Empty;
                    var synchronizedTiming = string.Empty;
                    if (layer.syncedLayerIndex >= 0)
                    {
                        Require(layer.syncedLayerIndex < controllerLayers.Length,
                            "An animator layer synchronization target is out of range.");
                        synchronizedLayer = controllerLayers[layer.syncedLayerIndex].name ?? string.Empty;
                        synchronizedTiming = layer.syncedLayerAffectsTiming ? "true" : "false";
                    }
                    var avatarMaskToken = AvatarMaskToken(layer.avatarMask, out var avatarMaskSummary);
                    var layerSettings = Frame(FloatText(layer.defaultWeight))
                        + Frame(layer.blendingMode) + Frame(avatarMaskToken)
                        + Frame(layer.iKPass) + Frame(synchronizedLayer)
                        + Frame(synchronizedTiming);
                    var layerFields = new[]
                    {
                        FloatText(layer.defaultWeight),
                        layer.blendingMode.ToString(),
                        avatarMaskToken,
                        layer.iKPass ? "true" : "false",
                        synchronizedLayer,
                        synchronizedTiming
                    };
                    rows.Add(new AnimatorEvidenceRow
                    {
                        Kind = "layer",
                        Body = Frame(layerPath) + layerSettings,
                        Scope = role,
                        SemanticName = layer.name ?? string.Empty,
                        SemanticType = layerSettings,
                        SemanticFields = layerFields,
                        SemanticMaskSummary = avatarMaskSummary
                    });
                    setStage?.Invoke("animator_" + group + "_state_machine");
                    var animatorPathIndex = BuildAnimatorPathIndex(layer.stateMachine, layerPath);
                    CaptureStateMachine(
                        layer.stateMachine,
                        layerPath,
                        controller,
                        animatorPathIndex,
                        rows,
                        new HashSet<int>(),
                        group,
                        setStage);
                }
            }
        }

        private static void CaptureStateMachine(
            AnimatorStateMachine machine,
            string path,
            AnimatorController controller,
            AnimatorPathIndex animatorPathIndex,
            ICollection<AnimatorEvidenceRow> rows,
            ISet<int> visited,
            string group,
            Action<string> setStage)
        {
            setStage?.Invoke("animator_" + group + "_state_machine_header");
            Require(visited.Add(machine.GetInstanceID()), "The animator state-machine graph contains a cycle.");
            var states = machine.states;
            var defaultStateToken = StateWithinMachineToken(machine.defaultState, states);
            rows.Add(AnimatorEvidenceRow.Simple(
                "state_machine",
                Frame(path) + Frame(machine.name)
                    + Frame(defaultStateToken)
                    + Frame(VectorText(machine.anyStatePosition))
                    + Frame(VectorText(machine.entryPosition))
                    + Frame(VectorText(machine.exitPosition))
                    + Frame(VectorText(machine.parentStateMachinePosition))));
            setStage?.Invoke("animator_" + group + "_machine_behaviours");
            CaptureBehaviours(machine.behaviours, path + "/machine_behaviour", controller, rows);
            setStage?.Invoke("animator_" + group + "_state_names");
            for (var stateIndex = 0; stateIndex < states.Length; stateIndex++)
            {
                var child = states[stateIndex];
                Require(child.state != null, "An animator state is unresolved.");
                var state = child.state;
                var statePath = path + "/state:" + stateIndex.ToString(CultureInfo.InvariantCulture) + ":" + state.name;
                setStage?.Invoke("animator_" + group + "_state_header");
                rows.Add(AnimatorEvidenceRow.Simple(
                    "state",
                    Frame(statePath) + Frame(FloatText(state.speed)) + Frame(state.speedParameter)
                        + Frame(state.speedParameterActive) + Frame(state.mirror) + Frame(state.mirrorParameter)
                        + Frame(state.mirrorParameterActive) + Frame(FloatText(state.cycleOffset))
                        + Frame(state.cycleOffsetParameter) + Frame(state.cycleOffsetParameterActive)
                        + Frame(state.iKOnFeet) + Frame(state.writeDefaultValues)
                        + Frame(state.timeParameter) + Frame(state.timeParameterActive)
                        + Frame(state.tag) + Frame(VectorText(child.position))
                        + Frame(ControllerObjectToken(state.motion, controller)),
                    state.speedParameter,
                    state.mirrorParameter,
                    state.cycleOffsetParameter,
                    state.timeParameter));
                setStage?.Invoke("animator_" + group + "_state_transitions");
                CaptureTransitions(state.transitions, statePath + "/transition", controller, animatorPathIndex, rows);
                setStage?.Invoke("animator_" + group + "_state_behaviours");
                CaptureBehaviours(state.behaviours, statePath, controller, rows);
                setStage?.Invoke("animator_" + group + "_state_motion");
                CaptureMotion(state.motion, statePath + "/motion", controller, rows, new HashSet<int>());
            }
            setStage?.Invoke("animator_" + group + "_any_transitions");
            CaptureTransitions(machine.anyStateTransitions, path + "/any", controller, animatorPathIndex, rows);
            setStage?.Invoke("animator_" + group + "_entry_transitions");
            CaptureTransitions(machine.entryTransitions, path + "/entry", controller, animatorPathIndex, rows);
            var stateMachines = machine.stateMachines;
            setStage?.Invoke("animator_" + group + "_child_state_machine_names");
            for (var machineIndex = 0; machineIndex < stateMachines.Length; machineIndex++)
            {
                var child = stateMachines[machineIndex];
                Require(child.stateMachine != null, "A child animator state machine is unresolved.");
                var childPath = path + "/machine:" + machineIndex.ToString(CultureInfo.InvariantCulture) + ":" + child.stateMachine.name;
                rows.Add(AnimatorEvidenceRow.Simple("state_machine_position", Frame(childPath) + Frame(VectorText(child.position))));
                setStage?.Invoke("animator_" + group + "_child_transitions");
                CaptureTransitions(
                    machine.GetStateMachineTransitions(child.stateMachine),
                    childPath + "/transition",
                    controller,
                    animatorPathIndex,
                    rows);
                CaptureStateMachine(child.stateMachine, childPath, controller, animatorPathIndex, rows, visited, group, setStage);
            }
            visited.Remove(machine.GetInstanceID());
        }

        private static void CaptureTransitions(
            AnimatorTransitionBase[] transitions,
            string path,
            AnimatorController controller,
            AnimatorPathIndex animatorPathIndex,
            ICollection<AnimatorEvidenceRow> rows)
        {
            var values = transitions ?? Array.Empty<AnimatorTransitionBase>();
            for (var index = 0; index < values.Length; index++)
            {
                var transition = values[index];
                Require(transition != null, "An animator transition is unresolved.");
                var names = new List<string>();
                var conditionRows = new List<string>();
                foreach (var condition in transition.conditions ?? Array.Empty<AnimatorCondition>())
                {
                    names.Add(condition.parameter ?? string.Empty);
                    conditionRows.Add(
                        Frame(condition.parameter) + Frame(condition.mode) + Frame(FloatText(condition.threshold)));
                }
                conditionRows.Sort(StringComparer.Ordinal);
                var destination = transition.destinationState != null
                    ? AnimatorStatePathToken(transition.destinationState, animatorPathIndex)
                    : transition.destinationStateMachine != null
                        ? AnimatorStateMachinePathToken(transition.destinationStateMachine, animatorPathIndex)
                        : "exit:" + transition.isExit;
                var stateTransition = transition as AnimatorStateTransition;
                var semanticFields = new[]
                {
                    destination,
                    transition.isExit ? "true" : "false",
                    transition.mute ? "true" : "false",
                    transition.solo ? "true" : "false",
                    string.Concat(conditionRows),
                    stateTransition == null ? string.Empty : FloatText(stateTransition.duration),
                    stateTransition == null ? string.Empty : FloatText(stateTransition.exitTime),
                    stateTransition != null && stateTransition.hasExitTime ? "true" : "false",
                    stateTransition != null && stateTransition.hasFixedDuration ? "true" : "false",
                    stateTransition == null ? string.Empty : stateTransition.interruptionSource.ToString(),
                    stateTransition == null ? string.Empty : FloatText(stateTransition.offset),
                    stateTransition != null && stateTransition.orderedInterruption ? "true" : "false",
                    stateTransition != null && stateTransition.canTransitionToSelf ? "true" : "false"
                };
                rows.Add(new AnimatorEvidenceRow
                {
                    Kind = "transition",
                    Scope = path,
                    SemanticName = index.ToString(CultureInfo.InvariantCulture),
                    SemanticFields = semanticFields,
                    ParameterNames = new HashSet<string>(names, StringComparer.Ordinal),
                    Body = Frame(path) + Frame(index) + string.Concat(semanticFields.Select(Frame))
                });
            }
        }

        private static void CaptureBehaviours(
            StateMachineBehaviour[] behaviours,
            string statePath,
            AnimatorController controller,
            ICollection<AnimatorEvidenceRow> rows)
        {
            var values = behaviours ?? Array.Empty<StateMachineBehaviour>();
            for (var behaviourIndex = 0; behaviourIndex < values.Length; behaviourIndex++)
            {
                var behaviour = values[behaviourIndex];
                Require(behaviour != null, "An animator state behaviour is unresolved.");
                if (behaviour.GetType().FullName != ParameterDriverType)
                {
                    var serializedBehaviour = new SerializedObject(behaviour);
                    var iterator = serializedBehaviour.GetIterator();
                    var propertyRows = new List<string>();
                    var parameterNames = new HashSet<string>(StringComparer.Ordinal);
                    var enterChildren = true;
                    while (iterator.Next(enterChildren))
                    {
                        enterChildren = true;
                        if (iterator.propertyType == SerializedPropertyType.Generic) continue;
                        var value = PropertyValue(iterator, null, controller);
                        propertyRows.Add(Frame(iterator.propertyPath) + Frame(iterator.propertyType) + Frame(value));
                        if (iterator.propertyType == SerializedPropertyType.String
                            && iterator.propertyPath.IndexOf("parameter", StringComparison.OrdinalIgnoreCase) >= 0
                            && !string.IsNullOrWhiteSpace(iterator.stringValue))
                        {
                            parameterNames.Add(iterator.stringValue);
                        }
                    }
                    rows.Add(new AnimatorEvidenceRow
                    {
                        Kind = "behaviour",
                        Body = Frame(statePath) + Frame(behaviourIndex)
                            + Frame(behaviour.GetType().AssemblyQualifiedName)
                            + Frame(string.Concat(propertyRows)),
                        ParameterNames = parameterNames
                    });
                    continue;
                }
                var serialized = new SerializedObject(behaviour);
                var parameters = serialized.FindProperty("parameters");
                var localOnlyProperty = serialized.FindProperty("localOnly");
                var debugStringProperty = serialized.FindProperty("debugString");
                Require(parameters != null && parameters.isArray, "The parameter-driver layout is unsupported.");
                Require(localOnlyProperty != null && localOnlyProperty.propertyType == SerializedPropertyType.Boolean,
                    "The parameter-driver local-only layout is unsupported.");
                Require(debugStringProperty != null && debugStringProperty.propertyType == SerializedPropertyType.String,
                    "The parameter-driver debug layout is unsupported.");
                for (var index = 0; index < parameters.arraySize; index++)
                {
                    var element = parameters.GetArrayElementAtIndex(index);
                    var source = ReadRelativeString(element, "source");
                    var destination = ReadRelativeString(element, "name");
                    var type = ReadRelativeEnum(element, "type", out var typeName);
                    var value = ReadRelativeFloat(element, "value");
                    var valueMin = ReadRelativeFloat(element, "valueMin");
                    var valueMax = ReadRelativeFloat(element, "valueMax");
                    var chance = ReadRelativeFloat(element, "chance");
                    var preventRepeats = ReadRelativeBool(element, "preventRepeats");
                    var convertRange = ReadRelativeBool(element, "convertRange");
                    var sourceMin = ReadRelativeFloat(element, "sourceMin");
                    var sourceMax = ReadRelativeFloat(element, "sourceMax");
                    var destMin = ReadRelativeFloat(element, "destMin");
                    var destMax = ReadRelativeFloat(element, "destMax");
                    rows.Add(new AnimatorEvidenceRow
                    {
                        Kind = "driver",
                        SourceParameter = typeName == "Copy" ? source : string.Empty,
                        DestinationParameter = typeName == "Copy" ? destination : string.Empty,
                        ParameterNames = new HashSet<string>(
                            new[] { source, destination }.Where(name => !string.IsNullOrWhiteSpace(name)),
                            StringComparer.Ordinal),
                        Body = Frame(statePath) + Frame(behaviourIndex)
                            + Frame(localOnlyProperty.boolValue) + Frame(debugStringProperty.stringValue ?? string.Empty)
                            + Frame(index)
                            + Frame(type) + Frame(typeName) + Frame(source) + Frame(destination)
                            + Frame(value) + Frame(valueMin) + Frame(valueMax)
                            + Frame(chance) + Frame(preventRepeats) + Frame(convertRange)
                            + Frame(sourceMin) + Frame(sourceMax)
                            + Frame(destMin) + Frame(destMax)
                    });
                }
            }
        }

        private static void CaptureMotion(
            Motion motion,
            string path,
            AnimatorController controller,
            ICollection<AnimatorEvidenceRow> rows,
            ISet<int> visited)
        {
            if (motion == null) return;
            if (!(motion is BlendTree tree))
            {
                rows.Add(AnimatorEvidenceRow.Simple("motion", Frame(path) + Frame(ControllerObjectToken(motion, controller))));
                return;
            }
            Require(visited.Add(tree.GetInstanceID()), "The blend-tree graph contains a cycle.");
            rows.Add(AnimatorEvidenceRow.Simple(
                "blend_tree",
                Frame(path) + Frame(tree.name) + Frame(tree.blendType)
                    + Frame(tree.blendParameter) + Frame(tree.blendParameterY)
                    + Frame(FloatText(tree.minThreshold)) + Frame(FloatText(tree.maxThreshold))
                    + Frame(tree.useAutomaticThresholds),
                tree.blendParameter,
                tree.blendParameterY));
            var children = tree.children;
            for (var index = 0; index < children.Length; index++)
            {
                var child = children[index];
                rows.Add(AnimatorEvidenceRow.Simple(
                    "blend_child",
                    Frame(path) + Frame(index) + Frame(child.directBlendParameter)
                        + Frame(FloatText(child.threshold)) + Frame(FloatText(child.timeScale))
                        + Frame(FloatText(child.position.x)) + Frame(FloatText(child.position.y))
                        + Frame(FloatText(child.cycleOffset)) + Frame(child.mirror),
                    child.directBlendParameter));
                CaptureMotion(child.motion, path + "/" + index.ToString(CultureInfo.InvariantCulture), controller, rows, visited);
            }
            visited.Remove(tree.GetInstanceID());
        }

        private static List<string> CapturePortableRows(
            GameObject root,
            IDictionary<string, List<string>> propertyCategories,
            IDictionary<string, List<string>> descriptorPropertyGroups)
        {
            foreach (var category in new[]
                     {
                         "transform_editor", "transform_spatial", "transform_hierarchy", "transform_other",
                         "descriptor", "other"
                     })
            {
                propertyCategories[category] = new List<string>();
            }
            var rows = new List<string>();
            void Walk(Transform transform, string path)
            {
                var obj = transform.gameObject;
                rows.Add(
                    Frame("object") + Frame(path) + Frame(obj.name) + Frame(obj.activeSelf)
                    + Frame(obj.layer) + Frame(obj.tag) + Frame((int)obj.hideFlags)
                    + Frame(VectorText(transform.localPosition))
                    + Frame(QuaternionText(transform.localRotation))
                    + Frame(VectorText(transform.localScale)));
                var components = obj.GetComponents<Component>();
                for (var index = 0; index < components.Length; index++)
                {
                    var component = components[index];
                    Require(component != null, "The avatar hierarchy contains a missing component.");
                    var componentPath = path + "/component:" + component.GetType().AssemblyQualifiedName
                        + ":" + index.ToString(CultureInfo.InvariantCulture);
                    rows.Add(Frame("component") + Frame(componentPath));
                    if (component is Transform) continue;
                    var serialized = new SerializedObject(component);
                    var iterator = serialized.GetIterator();
                    var enterChildren = true;
                    while (iterator.Next(enterChildren))
                    {
                        enterChildren = true;
                        if (iterator.propertyPath == "m_ObjectHideFlags"
                            || iterator.propertyPath == "m_GameObject"
                            || iterator.propertyPath.StartsWith("m_GameObject.", StringComparison.Ordinal)
                            || iterator.propertyPath.StartsWith("m_GameObject[", StringComparison.Ordinal)
                            || iterator.propertyPath == "m_CorrespondingSourceObject"
                            || iterator.propertyPath.StartsWith("m_CorrespondingSourceObject.", StringComparison.Ordinal)
                            || iterator.propertyPath.StartsWith("m_CorrespondingSourceObject[", StringComparison.Ordinal)
                            || iterator.propertyPath == "m_PrefabInstance"
                            || iterator.propertyPath.StartsWith("m_PrefabInstance.", StringComparison.Ordinal)
                            || iterator.propertyPath.StartsWith("m_PrefabInstance[", StringComparison.Ordinal)
                            || iterator.propertyPath == "m_PrefabAsset"
                            || iterator.propertyPath.StartsWith("m_PrefabAsset.", StringComparison.Ordinal)
                            || iterator.propertyPath.StartsWith("m_PrefabAsset[", StringComparison.Ordinal)
                            || iterator.propertyPath == "serializedVersion")
                        {
                            continue;
                        }
                        if (iterator.propertyType == SerializedPropertyType.Generic) continue;
                        var propertyRow =
                            Frame("property") + Frame(componentPath) + Frame(iterator.propertyPath)
                            + Frame(iterator.propertyType) + Frame(PropertyValue(iterator, root));
                        rows.Add(propertyRow);
                        var category = component is Transform
                            ? TransformPropertyCategory(iterator.propertyPath)
                            : component is VRCAvatarDescriptor
                                ? "descriptor"
                                : "other";
                        propertyCategories[category].Add(propertyRow);
                        if (category == "descriptor")
                        {
                            var group = DescriptorPropertyGroup(iterator.propertyPath);
                            if (!descriptorPropertyGroups.TryGetValue(group, out var groupRows))
                            {
                                groupRows = new List<string>();
                                descriptorPropertyGroups[group] = groupRows;
                            }
                            groupRows.Add(propertyRow);
                        }
                    }
                }
                for (var index = 0; index < transform.childCount; index++)
                {
                    Walk(transform.GetChild(index), path + "/" + index.ToString(CultureInfo.InvariantCulture));
                }
            }
            Walk(root.transform, "0");
            return rows;
        }

        private static string DescriptorPropertyGroup(string propertyPath)
        {
            var separator = propertyPath.IndexOf('.');
            var bracket = propertyPath.IndexOf('[');
            if (separator < 0 || bracket >= 0 && bracket < separator) separator = bracket;
            var root = separator < 0 ? propertyPath : propertyPath.Substring(0, separator);
            var token = new string(root.Select(character =>
                char.IsLetterOrDigit(character) || character == '_'
                    ? char.ToLowerInvariant(character)
                    : '_').ToArray());
            return string.IsNullOrWhiteSpace(token) ? "unclassified" : token;
        }

        private static bool IsTransformEditorHint(string propertyPath)
        {
            return propertyPath == "m_LocalEulerAnglesHint"
                || propertyPath.StartsWith("m_LocalEulerAnglesHint.", StringComparison.Ordinal)
                || propertyPath == "m_RootOrder"
                || propertyPath == "m_ConstrainProportionsScale"
                || propertyPath == "serializedVersion";
        }

        private static string TransformPropertyCategory(string propertyPath)
        {
            if (IsTransformEditorHint(propertyPath)) return "transform_editor";
            if (propertyPath.StartsWith("m_LocalPosition", StringComparison.Ordinal)
                || propertyPath.StartsWith("m_LocalRotation", StringComparison.Ordinal)
                || propertyPath.StartsWith("m_LocalScale", StringComparison.Ordinal))
            {
                return "transform_spatial";
            }
            if (propertyPath == "m_GameObject"
                || propertyPath == "m_Father"
                || propertyPath.StartsWith("m_Children", StringComparison.Ordinal))
            {
                return "transform_hierarchy";
            }
            return "transform_other";
        }

        private static string PropertyValue(
            SerializedProperty property,
            GameObject root,
            AnimatorController controller = null)
        {
            switch (property.propertyType)
            {
                case SerializedPropertyType.Integer: return property.longValue.ToString(CultureInfo.InvariantCulture);
                case SerializedPropertyType.Boolean: return property.boolValue ? "true" : "false";
                case SerializedPropertyType.Float: return property.doubleValue.ToString("R", CultureInfo.InvariantCulture);
                case SerializedPropertyType.String: return property.stringValue ?? string.Empty;
                case SerializedPropertyType.Color: return ColorText(property.colorValue);
                case SerializedPropertyType.ObjectReference:
                    return controller == null
                        ? ObjectToken(property.objectReferenceValue, root)
                        : ControllerObjectToken(property.objectReferenceValue, controller);
                case SerializedPropertyType.LayerMask: return property.intValue.ToString(CultureInfo.InvariantCulture);
                case SerializedPropertyType.Enum: return property.enumValueIndex.ToString(CultureInfo.InvariantCulture);
                case SerializedPropertyType.Vector2: return VectorText(property.vector2Value);
                case SerializedPropertyType.Vector3: return VectorText(property.vector3Value);
                case SerializedPropertyType.Vector4: return VectorText(property.vector4Value);
                case SerializedPropertyType.Rect: return RectText(property.rectValue);
                case SerializedPropertyType.ArraySize: return property.intValue.ToString(CultureInfo.InvariantCulture);
                case SerializedPropertyType.Character: return property.intValue.ToString(CultureInfo.InvariantCulture);
                case SerializedPropertyType.Bounds: return BoundsText(property.boundsValue);
                case SerializedPropertyType.Quaternion: return QuaternionText(property.quaternionValue);
                case SerializedPropertyType.ExposedReference:
                    return controller == null
                        ? ObjectToken(property.exposedReferenceValue, root)
                        : ControllerObjectToken(property.exposedReferenceValue, controller);
                case SerializedPropertyType.FixedBufferSize: return property.fixedBufferSize.ToString(CultureInfo.InvariantCulture);
                case SerializedPropertyType.Vector2Int: return property.vector2IntValue.ToString();
                case SerializedPropertyType.Vector3Int: return property.vector3IntValue.ToString();
                case SerializedPropertyType.RectInt: return property.rectIntValue.ToString();
                case SerializedPropertyType.BoundsInt: return property.boundsIntValue.ToString();
                case SerializedPropertyType.AnimationCurve:
                    var curve = property.animationCurveValue;
                    return Frame(curve.preWrapMode) + Frame(curve.postWrapMode)
                        + string.Concat(curve.keys.Select(key =>
                            Frame(FloatText(key.time)) + Frame(FloatText(key.value))
                            + Frame(FloatText(key.inTangent)) + Frame(FloatText(key.outTangent))
                            + Frame(FloatText(key.inWeight)) + Frame(FloatText(key.outWeight))
                            + Frame(key.weightedMode)));
                case SerializedPropertyType.Gradient:
                    var gradient = property.gradientValue;
                    return Frame(gradient.mode)
                        + string.Concat(gradient.colorKeys.Select(key => Frame(ColorText(key.color)) + Frame(FloatText(key.time))))
                        + string.Concat(gradient.alphaKeys.Select(key => Frame(FloatText(key.alpha)) + Frame(FloatText(key.time))));
                case SerializedPropertyType.ManagedReference: return property.managedReferenceFullTypename ?? string.Empty;
                default:
                    throw new InvalidOperationException("Unsupported serialized property type: " + property.propertyType);
            }
        }

        private static string ObjectToken(Object value, GameObject root)
        {
            if (value == null) return "null";
            if (root != null)
            {
                Transform transform = null;
                if (value is GameObject gameObject) transform = gameObject.transform;
                else if (value is Component component) transform = component.transform;
                if (transform != null && (transform == root.transform || transform.IsChildOf(root.transform)))
                {
                    var path = RelativeSiblingPath(root.transform, transform);
                    if (value is GameObject) return "avatar:" + path + ":gameObject";
                    var components = transform.GetComponents(value.GetType());
                    var ordinal = Array.FindIndex(components, component => component == value);
                    Require(ordinal >= 0, "An avatar component reference could not be normalized.");
                    return "avatar:" + path + ":" + value.GetType().AssemblyQualifiedName + ":" + ordinal;
                }
            }
            if (AssetDatabase.TryGetGUIDAndLocalFileIdentifier(value, out string guid, out long localId))
            {
                Require(!string.IsNullOrWhiteSpace(guid), "A persistent object reference has no GUID.");
                return "asset:" + guid.ToLowerInvariant() + ":" + localId.ToString(CultureInfo.InvariantCulture);
            }
            throw new InvalidOperationException("An external scene object reference cannot be normalized.");
        }

        private static string RelativeSiblingPath(Transform root, Transform value)
        {
            var indices = new List<int>();
            var current = value;
            while (current != root)
            {
                Require(current.parent != null, "An avatar reference escaped the evidence root.");
                indices.Add(current.GetSiblingIndex());
                current = current.parent;
            }
            indices.Reverse();
            return "0" + string.Concat(indices.Select(index => "/" + index.ToString(CultureInfo.InvariantCulture)));
        }

        private static List<AnimatorEvidenceRow> SubtractRows(
            IReadOnlyCollection<AnimatorEvidenceRow> before,
            IReadOnlyCollection<AnimatorEvidenceRow> after)
        {
            var counts = before.GroupBy(row => row.Canonical, StringComparer.Ordinal)
                .ToDictionary(group => group.Key, group => group.Count(), StringComparer.Ordinal);
            var result = new List<AnimatorEvidenceRow>();
            foreach (var row in after)
            {
                if (counts.TryGetValue(row.Canonical, out var count) && count > 0)
                {
                    counts[row.Canonical] = count - 1;
                }
                else
                {
                    result.Add(row);
                }
            }
            return result;
        }

        private static HashSet<string> Reachable(
            string start,
            IReadOnlyCollection<AnimatorEvidenceRow> edges,
            bool reverse)
        {
            var result = new HashSet<string>(StringComparer.Ordinal) { start };
            var queue = new Queue<string>();
            queue.Enqueue(start);
            while (queue.Count > 0)
            {
                var current = queue.Dequeue();
                foreach (var edge in edges)
                {
                    var source = reverse ? edge.DestinationParameter : edge.SourceParameter;
                    var destination = reverse ? edge.SourceParameter : edge.DestinationParameter;
                    if (source != current || string.IsNullOrWhiteSpace(destination) || !result.Add(destination)) continue;
                    queue.Enqueue(destination);
                }
            }
            return result;
        }

        private static void RequireMultisetSubset(
            IEnumerable<string> required,
            IEnumerable<string> actual,
            string message)
        {
            var counts = actual.GroupBy(row => row, StringComparer.Ordinal)
                .ToDictionary(group => group.Key, group => group.Count(), StringComparer.Ordinal);
            foreach (var row in required)
            {
                if (!counts.TryGetValue(row, out var count) || count == 0) throw new InvalidOperationException(message);
                counts[row] = count - 1;
            }
        }

        private static string ReadRelativeString(SerializedProperty element, string name)
        {
            var value = element.FindPropertyRelative(name);
            Require(value != null && value.propertyType == SerializedPropertyType.String, "The parameter-driver string layout is unsupported.");
            return value.stringValue ?? string.Empty;
        }

        private static long ReadRelativeInteger(SerializedProperty element, string name)
        {
            var value = element.FindPropertyRelative(name);
            Require(value != null && (value.propertyType == SerializedPropertyType.Integer
                || value.propertyType == SerializedPropertyType.Enum), "The parameter-driver integer layout is unsupported.");
            return value.propertyType == SerializedPropertyType.Enum ? value.enumValueIndex : value.longValue;
        }

        private static long ReadRelativeEnum(
            SerializedProperty element,
            string name,
            out string enumName)
        {
            var value = element.FindPropertyRelative(name);
            Require(value != null && value.propertyType == SerializedPropertyType.Enum,
                "The parameter-driver enum layout is unsupported.");
            Require(value.enumValueIndex >= 0 && value.enumValueIndex < value.enumNames.Length,
                "The parameter-driver enum value is unsupported.");
            enumName = value.enumNames[value.enumValueIndex];
            Require(!string.IsNullOrWhiteSpace(enumName), "The parameter-driver enum name is unavailable.");
            return value.enumValueIndex;
        }

        private static string ReadRelativeFloat(SerializedProperty element, string name)
        {
            var value = element.FindPropertyRelative(name);
            Require(value != null && value.propertyType == SerializedPropertyType.Float, "The parameter-driver float layout is unsupported.");
            return value.doubleValue.ToString("R", CultureInfo.InvariantCulture);
        }

        private static bool ReadRelativeBool(SerializedProperty element, string name)
        {
            var value = element.FindPropertyRelative(name);
            Require(value != null && value.propertyType == SerializedPropertyType.Boolean, "The parameter-driver boolean layout is unsupported.");
            return value.boolValue;
        }

        private static string ControllerObjectToken(Object value, AnimatorController controller)
        {
            if (value == null) return "null";
            Require(controller != null, "The animator evidence controller is unavailable.");
            var controllerPath = AssetDatabase.GetAssetPath(controller);
            var valuePath = AssetDatabase.GetAssetPath(value);
            if (!string.IsNullOrWhiteSpace(controllerPath) && valuePath == controllerPath)
            {
                Require(
                    AssetDatabase.TryGetGUIDAndLocalFileIdentifier(value, out string _, out long localId),
                    "An animator subasset identifier is unavailable.");
                return "controller:" + value.GetType().AssemblyQualifiedName + ":"
                    + localId.ToString(CultureInfo.InvariantCulture);
            }
            return ObjectToken(value, null);
        }

        private static string ControllerParameterDefault(AnimatorControllerParameter parameter)
        {
            switch (parameter.type)
            {
                case AnimatorControllerParameterType.Float:
                    return FloatText(parameter.defaultFloat);
                case AnimatorControllerParameterType.Int:
                    return parameter.defaultInt.ToString(CultureInfo.InvariantCulture);
                case AnimatorControllerParameterType.Bool:
                    return parameter.defaultBool ? "true" : "false";
                case AnimatorControllerParameterType.Trigger:
                    return string.Empty;
                default:
                    throw new InvalidOperationException("The animator controller parameter type is unsupported.");
            }
        }

        private static AnimatorPathIndex BuildAnimatorPathIndex(
            AnimatorStateMachine root,
            string rootPath)
        {
            Require(root != null && !string.IsNullOrWhiteSpace(rootPath), "Animator path-index input is incomplete.");
            var index = new AnimatorPathIndex();
            IndexAnimatorStateMachine(root, rootPath, index, new HashSet<int>());
            return index;
        }

        private static void IndexAnimatorStateMachine(
            AnimatorStateMachine machine,
            string path,
            AnimatorPathIndex index,
            ISet<int> active)
        {
            var machineId = machine.GetInstanceID();
            Require(active.Add(machineId), "The animator state-machine graph contains a cycle.");
            Require(!index.StateMachinePaths.ContainsKey(machineId),
                "An animator state machine has more than one structural path.");
            index.StateMachinePaths.Add(machineId, path);
            var states = machine.states;
            for (var stateIndex = 0; stateIndex < states.Length; stateIndex++)
            {
                var state = states[stateIndex].state;
                Require(state != null, "An animator state is unresolved while indexing paths.");
                var stateId = state.GetInstanceID();
                Require(!index.StatePaths.ContainsKey(stateId),
                    "An animator state has more than one structural path.");
                index.StatePaths.Add(
                    stateId,
                    path + "/state:" + stateIndex.ToString(CultureInfo.InvariantCulture) + ":" + state.name);
            }
            var stateMachines = machine.stateMachines;
            for (var machineIndex = 0; machineIndex < stateMachines.Length; machineIndex++)
            {
                var child = stateMachines[machineIndex].stateMachine;
                Require(child != null, "A child animator state machine is unresolved while indexing paths.");
                IndexAnimatorStateMachine(
                    child,
                    path + "/machine:" + machineIndex.ToString(CultureInfo.InvariantCulture) + ":" + child.name,
                    index,
                    active);
            }
            active.Remove(machineId);
        }

        private static string AnimatorStatePathToken(AnimatorState state, AnimatorPathIndex index)
        {
            Require(state != null && index != null, "An animator transition state target is unresolved.");
            Require(index.StatePaths.TryGetValue(state.GetInstanceID(), out var path),
                "An animator transition state target is outside its layer state machine.");
            return "state:" + path;
        }

        private static string AnimatorStateMachinePathToken(
            AnimatorStateMachine stateMachine,
            AnimatorPathIndex index)
        {
            Require(stateMachine != null && index != null, "An animator transition state-machine target is unresolved.");
            Require(index.StateMachinePaths.TryGetValue(stateMachine.GetInstanceID(), out var path),
                "An animator transition state-machine target is outside its layer state machine.");
            return "machine:" + path;
        }

        private static string StateWithinMachineToken(
            AnimatorState state,
            IReadOnlyList<ChildAnimatorState> states)
        {
            if (state == null) return "null";
            for (var index = 0; index < states.Count; index++)
            {
                if (states[index].state != state) continue;
                return "state:" + index.ToString(CultureInfo.InvariantCulture) + ":" + (state.name ?? string.Empty);
            }
            throw new InvalidOperationException("An animator default state is outside its state machine.");
        }

        private sealed class AnimatorPathIndex
        {
            internal readonly Dictionary<int, string> StatePaths = new Dictionary<int, string>();
            internal readonly Dictionary<int, string> StateMachinePaths = new Dictionary<int, string>();
        }

        private static string AvatarMaskToken(AvatarMask mask, out string summary)
        {
            if (mask == null)
            {
                summary = "null";
                return "null";
            }
            var rows = new List<string>();
            var activeBodyPartCount = 0;
            for (var index = 0; index < (int)AvatarMaskBodyPart.LastBodyPart; index++)
            {
                var part = (AvatarMaskBodyPart)index;
                var active = mask.GetHumanoidBodyPartActive(part);
                if (active) activeBodyPartCount++;
                rows.Add(Frame("body") + Frame(part) + Frame(active));
            }
            var activeTransformCount = 0;
            for (var index = 0; index < mask.transformCount; index++)
            {
                var active = mask.GetTransformActive(index);
                if (active) activeTransformCount++;
                rows.Add(
                    Frame("transform") + Frame(index) + Frame(mask.GetTransformPath(index))
                        + Frame(active));
            }
            summary = "mask_body_" + activeBodyPartCount.ToString(CultureInfo.InvariantCulture)
                + "_of_" + ((int)AvatarMaskBodyPart.LastBodyPart).ToString(CultureInfo.InvariantCulture)
                + "_transform_" + activeTransformCount.ToString(CultureInfo.InvariantCulture)
                + "_of_" + mask.transformCount.ToString(CultureInfo.InvariantCulture);
            return "mask:" + DigestRows("vrcforge.animator_avatar_mask.v1", rows);
        }

        private static string FirstLayerMaskMismatchCategory(
            IEnumerable<AnimatorEvidenceRow> sourceLayers,
            IEnumerable<AnimatorEvidenceRow> outputLayers)
        {
            var outputByIdentity = outputLayers.ToDictionary(
                row => Frame(row.Scope) + Frame(row.SemanticName),
                StringComparer.Ordinal);
            foreach (var sourceLayer in sourceLayers)
            {
                var identity = Frame(sourceLayer.Scope) + Frame(sourceLayer.SemanticName);
                if (!outputByIdentity.TryGetValue(identity, out var outputLayer)) return "missing";
                var sourceToken = sourceLayer.SemanticFields.ElementAtOrDefault(2) ?? string.Empty;
                var outputToken = outputLayer.SemanticFields.ElementAtOrDefault(2) ?? string.Empty;
                if (sourceToken == outputToken) continue;
                var sourceSummary = string.IsNullOrWhiteSpace(sourceLayer.SemanticMaskSummary)
                    ? AvatarMaskTokenCategory(sourceToken)
                    : sourceLayer.SemanticMaskSummary;
                var outputSummary = string.IsNullOrWhiteSpace(outputLayer.SemanticMaskSummary)
                    ? AvatarMaskTokenCategory(outputToken)
                    : outputLayer.SemanticMaskSummary;
                return sourceSummary + "_to_" + outputSummary;
            }
            return "none";
        }

        private static string AvatarMaskTokenCategory(string token)
        {
            if (token == "null") return "null";
            return token != null && token.StartsWith("mask:", StringComparison.Ordinal) ? "mask" : "invalid";
        }

        private static string DigestRows(string schema, IEnumerable<string> rows)
        {
            return Sha256(schema + "\n" + string.Concat(rows.Select(Frame)));
        }

        private static string Sha256(string value)
        {
            using (var sha = SHA256.Create())
            {
                return string.Concat(sha.ComputeHash(Encoding.UTF8.GetBytes(value)).Select(valueByte => valueByte.ToString("x2")));
            }
        }

        private static string Frame(object value)
        {
            string text;
            if (value == null) text = string.Empty;
            else if (value is bool boolean) text = boolean ? "true" : "false";
            else if (value is IFormattable formattable) text = formattable.ToString(null, CultureInfo.InvariantCulture);
            else text = value.ToString();
            return Encoding.UTF8.GetByteCount(text).ToString(CultureInfo.InvariantCulture) + ":" + text;
        }

        private static string FloatText(float value) => value.ToString("R", CultureInfo.InvariantCulture);
        private static string VectorText(Vector2 value) => FloatText(value.x) + "," + FloatText(value.y);
        private static string VectorText(Vector3 value) => FloatText(value.x) + "," + FloatText(value.y) + "," + FloatText(value.z);
        private static string VectorText(Vector4 value) => FloatText(value.x) + "," + FloatText(value.y) + "," + FloatText(value.z) + "," + FloatText(value.w);
        private static string QuaternionText(Quaternion value) => VectorText(new Vector4(value.x, value.y, value.z, value.w));
        private static string ColorText(Color value) => VectorText(new Vector4(value.r, value.g, value.b, value.a));
        private static string RectText(Rect value) => VectorText(new Vector4(value.x, value.y, value.width, value.height));
        private static string BoundsText(Bounds value) => VectorText(value.center) + ";" + VectorText(value.size);

        private static void Require(bool condition, string message)
        {
            if (!condition) throw new InvalidOperationException(message);
        }
    }

    internal sealed class ParameterBehaviorEvidence
    {
        internal List<ParameterEvidenceRow> Parameters;
        internal List<MenuEvidenceRow> MenuRows;
        internal List<AnimatorEvidenceRow> AnimatorRows;
        internal string PortableAvatarDigest;
        internal string PortableObjectDigest;
        internal string PortableComponentDigest;
        internal string PortablePropertyDigest;
        internal string PortableTransformEditorPropertyDigest;
        internal string PortableTransformRuntimePropertyDigest;
        internal string PortableTransformSpatialPropertyDigest;
        internal string PortableTransformHierarchyPropertyDigest;
        internal string PortableTransformOtherPropertyDigest;
        internal string PortableDescriptorPropertyDigest;
        internal Dictionary<string, string> PortableDescriptorPropertyGroupDigests;
        internal string PortableOtherPropertyDigest;
        internal string OrderedParameterDigest;
        internal string MenuGraphDigest;
        internal string AnimatorBehaviorDigest;
        internal string ReceiptDigest => Digest(
            ParameterBitPackingEvidence.EvidenceSchema,
            PortableAvatarDigest,
            OrderedParameterDigest,
            Parameters.Count,
            MenuGraphDigest,
            MenuRows.Count,
            AnimatorBehaviorDigest,
            AnimatorRows.Count);

        internal object ToPayload() => new
        {
            schema = ParameterBitPackingEvidence.EvidenceSchema,
            portableAvatarDigest = PortableAvatarDigest,
            orderedParameterDigest = OrderedParameterDigest,
            parameterCount = Parameters.Count,
            menuGraphDigest = MenuGraphDigest,
            menuRowCount = MenuRows.Count,
            animatorBehaviorDigest = AnimatorBehaviorDigest,
            animatorRowCount = AnimatorRows.Count,
            receiptDigest = ReceiptDigest
        };

        private static string Digest(string schema, params object[] values)
        {
            using (var sha = SHA256.Create())
            {
                var framed = schema + "\n" + string.Concat(values.Select(value =>
                {
                    var text = value is IFormattable formattable
                        ? formattable.ToString(null, CultureInfo.InvariantCulture)
                        : value == null ? string.Empty : value.ToString();
                    return Encoding.UTF8.GetByteCount(text).ToString(CultureInfo.InvariantCulture) + ":" + text;
                }));
                return string.Concat(sha.ComputeHash(Encoding.UTF8.GetBytes(framed)).Select(value => value.ToString("x2")));
            }
        }
    }

    internal sealed class ParameterEvidenceRow
    {
        internal int Index;
        internal string Name;
        internal string Type;
        internal string DefaultValue;
        internal bool Saved;
        internal bool NetworkSynced;
        internal string Canonical => string.Join("|", Index, Name, Type, DefaultValue, Saved ? "true" : "false", NetworkSynced ? "true" : "false");
    }

    internal sealed class MenuEvidenceRow
    {
        internal string Path;
        internal string Name;
        internal string Type;
        internal string Parameter;
        internal string[] SubParameters;
        internal string Value;
        internal string Icon;
        internal bool HasSubMenu;
        internal string Canonical => string.Join("|", Path, Name, Type, Parameter, string.Join("\n", SubParameters), Value, Icon, HasSubMenu ? "true" : "false");
    }

    internal sealed class AnimatorEvidenceRow
    {
        internal string Kind;
        internal string Body;
        internal HashSet<string> ParameterNames = new HashSet<string>(StringComparer.Ordinal);
        internal string SourceParameter;
        internal string DestinationParameter;
        internal string Scope;
        internal string SemanticName;
        internal string SemanticType;
        internal string SemanticDefault;
        internal string[] SemanticFields = Array.Empty<string>();
        internal string SemanticMaskSummary;
        internal string Canonical => Kind + "|" + Body;

        internal static AnimatorEvidenceRow Simple(string kind, string body, params string[] names)
        {
            return new AnimatorEvidenceRow
            {
                Kind = kind,
                Body = body,
                ParameterNames = new HashSet<string>(
                    (names ?? Array.Empty<string>()).Where(name => !string.IsNullOrWhiteSpace(name)),
                    StringComparer.Ordinal)
            };
        }
    }

    internal sealed class ParameterBehaviorProof
    {
        internal string Status;
        internal string PlatformScope;
        internal string SourceOrderedParameterDigest;
        internal string OutputOrderedParameterDigest;
        internal int SourceParameterCount;
        internal int OutputParameterCount;
        internal string SourceMenuGraphDigest;
        internal string OutputMenuGraphDigest;
        internal int SourceMenuRowCount;
        internal int OutputMenuRowCount;
        internal string SourceAnimatorBehaviorDigest;
        internal string OutputAnimatorBehaviorDigest;
        internal int SourceAnimatorRowCount;
        internal int OutputAnimatorRowCount;
        internal string PreservedBehaviorDigest;
        internal string CodecGraphDigest;
        internal string CodecMappingDigest;
        internal int CodecMappingCount;
        internal string ExcludedBeforeDigest;
        internal string ExcludedAfterDigest;
        internal string ReceiptDigest => ParameterBehaviorEvidenceDigest();

        internal object ToPayload() => new
        {
            schema = ParameterBitPackingEvidence.ProofSchema,
            status = Status,
            platformScope = PlatformScope,
            crossPlatformEquivalent = false,
            sourceOrderedParameterDigest = SourceOrderedParameterDigest,
            outputOrderedParameterDigest = OutputOrderedParameterDigest,
            sourceParameterCount = SourceParameterCount,
            outputParameterCount = OutputParameterCount,
            sourceMenuGraphDigest = SourceMenuGraphDigest,
            outputMenuGraphDigest = OutputMenuGraphDigest,
            sourceMenuRowCount = SourceMenuRowCount,
            outputMenuRowCount = OutputMenuRowCount,
            sourceAnimatorBehaviorDigest = SourceAnimatorBehaviorDigest,
            outputAnimatorBehaviorDigest = OutputAnimatorBehaviorDigest,
            sourceAnimatorRowCount = SourceAnimatorRowCount,
            outputAnimatorRowCount = OutputAnimatorRowCount,
            preservedBehaviorDigest = PreservedBehaviorDigest,
            codecGraphDigest = CodecGraphDigest,
            codecMappingDigest = CodecMappingDigest,
            codecMappingCount = CodecMappingCount,
            excludedBeforeDigest = ExcludedBeforeDigest,
            excludedAfterDigest = ExcludedAfterDigest,
            receiptDigest = ReceiptDigest
        };

        private string ParameterBehaviorEvidenceDigest()
        {
            using (var sha = SHA256.Create())
            {
                var values = new object[]
                {
                    Status, PlatformScope, false,
                    SourceOrderedParameterDigest, OutputOrderedParameterDigest,
                    SourceParameterCount, OutputParameterCount,
                    SourceMenuGraphDigest, OutputMenuGraphDigest,
                    SourceMenuRowCount, OutputMenuRowCount,
                    SourceAnimatorBehaviorDigest, OutputAnimatorBehaviorDigest,
                    SourceAnimatorRowCount, OutputAnimatorRowCount,
                    PreservedBehaviorDigest, CodecGraphDigest, CodecMappingDigest,
                    CodecMappingCount, ExcludedBeforeDigest, ExcludedAfterDigest
                };
                var framed = ParameterBitPackingEvidence.ProofSchema + "\n" + string.Concat(values.Select(value =>
                {
                    string text;
                    if (value is bool boolean) text = boolean ? "true" : "false";
                    else if (value is IFormattable formattable) text = formattable.ToString(null, CultureInfo.InvariantCulture);
                    else text = value == null ? string.Empty : value.ToString();
                    return Encoding.UTF8.GetByteCount(text).ToString(CultureInfo.InvariantCulture) + ":" + text;
                }));
                return string.Concat(sha.ComputeHash(Encoding.UTF8.GetBytes(framed)).Select(value => value.ToString("x2")));
            }
        }
    }
}
