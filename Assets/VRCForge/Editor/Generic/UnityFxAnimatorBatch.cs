using System;
using System.Collections.Generic;
using System.Linq;
using System.Text;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEditor.Animations;
using UnityEngine;
using VRCForge.Core.MCP;

namespace VRCForge.Editor
{
    internal static class UnityFxAnimatorBatch
    {
        internal static JArray ValidateEnvelope(JObject arguments)
        {
            if (Encoding.UTF8.GetByteCount(arguments.ToString(Formatting.None)) > 512 * 1024)
                throw new InvalidOperationException("FX batch exceeds 512 KiB.");
            if (arguments.Properties().Any(property => !new[] { "controllerPath", "preview", "edits" }.Contains(property.Name)))
                throw new InvalidOperationException("edits is mutually exclusive with single FX fields.");
            var edits = arguments["edits"] as JArray;
            if (edits == null || edits.Count < 1 || edits.Count > 128)
                throw new InvalidOperationException("edits requires 1 to 128 entries.");
            return (JArray)edits.DeepClone();
        }

        // Pure JSON planning: no Unity object or asset is mutated here.
        internal static JObject BuildPlan(JObject before, JArray edits, ISet<string> existingMotions)
        {
            var result = (JObject)before.DeepClone();
            foreach (var token in edits)
            {
                var edit = token as JObject ?? throw new InvalidOperationException("Every FX edit must be an object.");
                var action = Required(edit, "action");
                var layerName = Required(edit, "layerName");
                var layers = ((JArray)result["layers"]).OfType<JObject>().Where(layer => (string)layer["name"] == layerName).ToArray();
                if (layers.Length != 1) throw new InvalidOperationException("Batch requires one unambiguous existing layer.");
                var machine = (JObject)layers[0]["machine"];
                var allStates = States(machine).ToArray();
                if (allStates.GroupBy(state => (string)state["name"]).Any(group => group.Count() > 1))
                    throw new InvalidOperationException("Layer contains ambiguous duplicate state names.");
                var stateEdit = action == "ensure_state" || action == "update_state";
                var edgeEdit = action == "ensure_transition" || action == "delete_transition";
                if (!stateEdit && !edgeEdit) throw new InvalidOperationException("Unsupported batch FX action.");
                var allowed = stateEdit
                    ? new[] { "action", "layerName", "stateName", "motionClipPath", "writeDefaults", "speed" }
                    : new[] { "action", "layerName", "sourceStateName", "destinationStateName", "transitionIndex", "hasExitTime", "exitTime", "duration", "canTransitionToSelf", "interruptionSource", "orderedInterruption", "conditions" };
                if (edit.Properties().Any(property => !allowed.Contains(property.Name)))
                    throw new InvalidOperationException("Unsupported field for batch FX action.");
                edit["action"] = action; edit["layerName"] = layerName;
                if (stateEdit)
                {
                    var name = Required(edit, "stateName"); edit["stateName"] = name;
                    var state = allStates.SingleOrDefault(item => (string)item["name"] == name);
                    if (state == null)
                    {
                        if (action == "update_state") throw new InvalidOperationException("State not found for update.");
                        Required(edit, "motionClipPath");
                        state = new JObject { ["name"] = name, ["speed"] = 1f, ["writeDefaultValues"] = true,
                            ["motionPath"] = "", ["transitions"] = new JArray() };
                        if (((JArray)machine["states"]).Count == 0 && (string)machine["defaultState"] == "") machine["defaultState"] = name;
                        ((JArray)machine["states"]).Add(state);
                        if (edit["speed"] == null) edit["speed"] = 1f;
                        if (edit["writeDefaults"] == null) edit["writeDefaults"] = true;
                    }
                    if (edit["motionClipPath"] != null)
                    {
                        var path = Required(edit, "motionClipPath");
                        if (!existingMotions.Contains(path)) throw new InvalidOperationException("Batch motion must be an existing clip.");
                        edit["motionClipPath"] = path; state["motionPath"] = path;
                    }
                    if (edit["speed"] != null) state["speed"] = Number(edit, "speed", 1f, false);
                    if (edit["writeDefaults"] != null) state["writeDefaultValues"] = Boolean(edit, "writeDefaults", true);
                    continue;
                }
                var destination = Required(edit, "destinationStateName"); edit["destinationStateName"] = destination;
                if (!allStates.Any(state => (string)state["name"] == destination)) throw new InvalidOperationException("Destination state not found.");
                var sourceName = ((string)edit["sourceStateName"] ?? "").Trim();
                JArray transitions;
                if (sourceName.Length == 0) transitions = (JArray)machine["anyStateTransitions"];
                else
                {
                    edit["sourceStateName"] = sourceName;
                    var source = allStates.SingleOrDefault(state => (string)state["name"] == sourceName)
                        ?? throw new InvalidOperationException("Source state not found.");
                    transitions = (JArray)source["transitions"];
                }
                if (action == "delete_transition")
                {
                    if (edit.Properties().Any(property => !new[] { "action", "layerName", "sourceStateName", "destinationStateName", "transitionIndex" }.Contains(property.Name))
                        || edit["transitionIndex"]?.Type != JTokenType.Integer)
                        throw new InvalidOperationException("Deletion requires an exact integer transitionIndex and destination only.");
                    var index = edit["transitionIndex"].Value<int>();
                    if (index < 0 || index >= transitions.Count || (string)transitions[index]["destination"] != destination)
                        throw new InvalidOperationException("Transition index/destination differs at this step.");
                    transitions.RemoveAt(index);
                    continue;
                }
                if (edit["transitionIndex"] != null) throw new InvalidOperationException("Append transition does not accept transitionIndex.");
                var conditions = new JArray();
                if (edit["conditions"] != null)
                {
                    if (!(edit["conditions"] is JArray requested) || requested.Count > 64)
                        throw new InvalidOperationException("conditions must contain at most 64 entries.");
                    foreach (var value in requested)
                    {
                        var condition = value as JObject ?? throw new InvalidOperationException("Every condition must be an object.");
                        if (condition.Properties().Any(property => !new[] { "parameterName", "mode", "threshold" }.Contains(property.Name)))
                            throw new InvalidOperationException("Unknown condition field.");
                        var parameter = Required(condition, "parameterName");
                        var mode = Required(condition, "mode");
                        var definition = ((JArray)result["parameters"]).OfType<JObject>().SingleOrDefault(item => (string)item["name"] == parameter)
                            ?? throw new InvalidOperationException("Condition parameter not found.");
                        var type = (string)definition["type"];
                        var valid = type == "Bool" || type == "Trigger" ? new[] { "If", "IfNot" }
                            : type == "Int" ? new[] { "Greater", "Less", "Equals", "NotEqual" } : new[] { "Greater", "Less" };
                        if (!valid.Contains(mode)) throw new InvalidOperationException("Condition mode is incompatible with parameter type.");
                        if (condition["threshold"] == null) throw new InvalidOperationException("Condition threshold is required.");
                        var threshold = Number(condition, "threshold", 0f, false);
                        condition["parameterName"] = parameter; condition["mode"] = mode; condition["threshold"] = threshold;
                        conditions.Add(new JObject { ["parameter"] = parameter, ["mode"] = mode, ["threshold"] = threshold });
                    }
                }
                transitions.Add(new JObject { ["destination"] = destination, ["isExit"] = false,
                    ["hasExitTime"] = Boolean(edit, "hasExitTime", false), ["exitTime"] = Number(edit, "exitTime", 0f, true),
                    ["duration"] = Number(edit, "duration", 0f, true), ["hasFixedDuration"] = true,
                    ["canTransitionToSelf"] = Boolean(edit, "canTransitionToSelf", false),
                    ["interruptionSource"] = InterruptionSource(edit), ["orderedInterruption"] = Boolean(edit, "orderedInterruption", true), ["conditions"] = conditions });
            }
            return result;
        }

        private static IEnumerable<JObject> States(JObject machine)
        {
            foreach (var state in ((JArray)machine["states"]).OfType<JObject>()) yield return state;
            foreach (var child in ((JArray)machine["machines"]).OfType<JObject>())
                foreach (var state in States(child)) yield return state;
        }
        private static string Required(JObject value, string name)
        {
            var text = (value[name]?.ToString() ?? "").Trim();
            if (text.Length == 0) throw new InvalidOperationException(name + " is required.");
            return text;
        }
        private static float Number(JObject value, string name, float fallback, bool nonnegative)
        {
            if (value[name] == null) return fallback;
            if (value[name].Type != JTokenType.Float && value[name].Type != JTokenType.Integer)
                throw new InvalidOperationException(name + " must be numeric.");
            var number = value[name].Value<float>();
            if (float.IsNaN(number) || float.IsInfinity(number) || (nonnegative && number < 0))
                throw new InvalidOperationException(name + " is outside its finite range.");
            return number;
        }
        private static bool Boolean(JObject value, string name, bool fallback)
        {
            if (value[name] == null) return fallback;
            if (value[name].Type != JTokenType.Boolean) throw new InvalidOperationException(name + " must be boolean.");
            return value[name].Value<bool>();
        }
        private static string InterruptionSource(JObject value)
        {
            var source = value["interruptionSource"]?.ToString() ?? "None";
            if (!new[] { "None", "Source", "Destination", "SourceThenDestination", "DestinationThenSource" }.Contains(source))
                throw new InvalidOperationException("interruptionSource must be None, Source, Destination, SourceThenDestination, or DestinationThenSource.");
            return source;
        }

        internal static object HandleCommand(JObject arguments)
        {
            var recovery = new WriteAnimationCurveTool.AssetEditRecovery();
            var mutated = false;
            var phase = "pre_mutation_validation";
            try
            {
                var edits = ValidateEnvelope(arguments);
                var path = AvatarPrimitiveCrudCore.NormalizeAssetPath(Required(arguments, "controllerPath"));
                if (!path.StartsWith("Assets/", StringComparison.Ordinal)) throw new InvalidOperationException("Controller must be an existing Assets controller.");
                var controller = AssetDatabase.LoadAssetAtPath<AnimatorController>(path)
                    ?? throw new InvalidOperationException("Controller not found.");
                if (AssetDatabase.LoadAllAssetsAtPath(path).Any(EditorUtility.IsDirty))
                    throw new InvalidOperationException("Save or discard existing controller edits before a batch.");
                var guid = AssetDatabase.AssetPathToGUID(path);
                var motions = new HashSet<string>(StringComparer.Ordinal);
                foreach (var edit in edits.OfType<JObject>())
                    if (edit["motionClipPath"] != null)
                    {
                        var motion = Required(edit, "motionClipPath");
                        if (!motion.StartsWith("Assets/", StringComparison.Ordinal) || AssetDatabase.LoadAssetAtPath<AnimationClip>(motion) == null)
                            throw new InvalidOperationException("Batch references a missing motion clip.");
                        motions.Add(motion);
                    }
                var before = Snapshot(controller);
                var expected = BuildPlan(before, edits, motions);
                if (arguments["preview"]?.Value<bool?>() ?? false)
                    return VRCForgeToolResult.Completed("Preview: would apply one controller batch.", new {
                        ok = true, preview = true, batch = true, controllerPath = path, editCount = edits.Count,
                        plan = Projection(expected) });
                recovery.Capture(path); recovery.Begin();
                Undo.RegisterCompleteObjectUndo(AssetDatabase.LoadAllAssetsAtPath(path), "FX animator batch");
                phase = "asset_mutation"; mutated = true;
                foreach (JObject edit in edits)
                {
                    ManageFxAnimatorTool.ApplyForBatch((string)edit["action"], controller, edit);
                    if ((string)edit["action"] == "ensure_transition")
                    {
                        var layer = controller.layers.Single(item => item.name == (string)edit["layerName"]);
                        var source = ((string)edit["sourceStateName"] ?? "").Trim();
                        var transitions = source.Length == 0 ? layer.stateMachine.anyStateTransitions
                            : AvatarPrimitiveCrudCore.FindState(layer.stateMachine, source).transitions;
                        transitions.Last().hasFixedDuration = true;
                    }
                }
                Verify(expected, Snapshot(controller));
                phase = "asset_save"; EditorUtility.SetDirty(controller); AssetDatabase.SaveAssetIfDirty(controller);
                phase = "persisted_readback";
                AssetDatabase.ImportAsset(path, ImportAssetOptions.ForceSynchronousImport | ImportAssetOptions.ForceUpdate);
                var actual = AssetDatabase.LoadAssetAtPath<AnimatorController>(path)
                    ?? throw new InvalidOperationException("Controller reload failed.");
                if (AssetDatabase.AssetPathToGUID(path) != guid || AssetDatabase.LoadAllAssetsAtPath(path).Any(EditorUtility.IsDirty))
                    throw new InvalidOperationException("Controller identity or saved state changed.");
                var readback = Snapshot(actual); Verify(expected, readback);
                var evidence = SceneObjectCopyCore.ReadStableAssetEvidence(path, "FX batch readback");
                recovery.Complete();
                return VRCForgeToolResult.Completed("FX batch saved and verified.", new {
                    schema = "vrcforge.fx_animator_write.v1", ok = true, preview = false, batch = true,
                    controllerPath = path, editCount = edits.Count, verified = true, persistedReadback = true,
                    mutationStarted = true, mutationApplied = true, committed = true, commitState = "committed", commitStateKnown = true,
                    checkpointRecoveryRequired = false, temporaryCleanupRequired = false,
                    readback = new { persisted = true, controllerPath = path, assetGuid = evidence.Guid, fileDigest = evidence.File.Digest,
                        state = Projection(readback), existingObjectGuardsVerified = true } });
            }
            catch (Exception exception)
            {
                return WriteAnimationCurveTool.EditFailure("fx_animator_batch_failed", exception, mutated, phase, recovery);
            }
        }

        private static JObject Snapshot(AnimatorController controller)
        {
            var snapshot = (JObject)ManageFxAnimatorTool.DescribeForBatch(controller);
            var layers = (JArray)snapshot["layers"];
            for (var index = 0; index < controller.layers.Length; index++)
            {
                var layer = controller.layers[index];
                layers[index]["syncedLayerIndex"] = layer.syncedLayerIndex;
                layers[index]["syncedLayerAffectsTiming"] = layer.syncedLayerAffectsTiming;
                layers[index]["iKPass"] = layer.iKPass;
                GuardMachine((JObject)layers[index]["machine"], layer.stateMachine);
            }
            return snapshot;
        }
        private static string Identity(UnityEngine.Object value)
        {
            if (value == null) return "";
            return AssetDatabase.TryGetGUIDAndLocalFileIdentifier(value, out string guid, out long local)
                ? guid + ":" + local : "memory:" + value.GetInstanceID();
        }
        private static void GuardMachine(JObject snapshot, AnimatorStateMachine machine)
        {
            snapshot["_id"] = Identity(machine);
            snapshot["_guard"] = JToken.FromObject(new {
                behaviours = machine.behaviours.Select(Identity).ToArray(),
                entryTransitions = machine.entryTransitions.Select(DescribeEntry).ToArray(),
                childTransitions = machine.stateMachines.Select(child => new {
                    machine = Identity(child.stateMachine), transitions = machine.GetStateMachineTransitions(child.stateMachine).Select(DescribeEntry).ToArray() }).ToArray() });
            for (var index = 0; index < machine.states.Length; index++)
            {
                var state = machine.states[index].state;
                var row = snapshot["states"][index]; row["_id"] = Identity(state);
                row["_guard"] = JToken.FromObject(new { state.tag, state.mirror, state.cycleOffset, state.iKOnFeet,
                    state.speedParameter, state.speedParameterActive, state.mirrorParameter, state.mirrorParameterActive,
                    state.cycleOffsetParameter, state.cycleOffsetParameterActive, state.timeParameter, state.timeParameterActive,
                    behaviours = state.behaviours.Select(Identity).ToArray() });
                GuardTransitions((JArray)row["transitions"], state.transitions);
            }
            GuardTransitions((JArray)snapshot["anyStateTransitions"], machine.anyStateTransitions);
            for (var index = 0; index < machine.stateMachines.Length; index++)
                GuardMachine((JObject)snapshot["machines"][index], machine.stateMachines[index].stateMachine);
        }
        private static object DescribeEntry(AnimatorTransition transition) => new {
            transition.name, transition.isExit, transition.mute, transition.solo,
            destination = Identity(transition.destinationState), destinationMachine = Identity(transition.destinationStateMachine),
            conditions = transition.conditions.Select(condition => new { condition.parameter, mode = condition.mode.ToString(), condition.threshold }).ToArray() };

        private static void GuardTransitions(JArray rows, AnimatorStateTransition[] transitions)
        {
            for (var index = 0; index < transitions.Length; index++)
            {
                var transition = transitions[index]; if (transition == null) continue;
                rows[index]["_id"] = Identity(transition);
                rows[index]["_guard"] = JToken.FromObject(new { transition.name, transition.mute, transition.solo, transition.offset,
                    interruptionSource = transition.interruptionSource.ToString(), transition.orderedInterruption });
            }
        }
        internal static JToken Projection(JToken source)
        {
            var result = source.DeepClone();
            foreach (var property in ((JContainer)result).Descendants().OfType<JProperty>().Where(item => item.Name.StartsWith("_", StringComparison.Ordinal)).ToArray()) property.Remove();
            return result;
        }
        internal static void Verify(JObject expected, JObject actual)
        {
            if (!JToken.DeepEquals(Projection(expected), Projection(actual)))
                throw new InvalidOperationException("FX batch differs from the preflight final plan.");
            var expectedIds = new HashSet<string>(expected.Descendants().OfType<JObject>().Where(item => item["_id"] != null).Select(item => (string)item["_id"]));
            var actualNodes = actual.Descendants().OfType<JObject>().Where(item => item["_id"] != null && expectedIds.Contains((string)item["_id"]))
                .ToDictionary(item => (string)item["_id"], StringComparer.Ordinal);
            foreach (var node in expected.Descendants().OfType<JObject>().Where(item => item["_id"] != null))
                if (!actualNodes.TryGetValue((string)node["_id"], out var persisted) || !JToken.DeepEquals(node["_guard"], persisted["_guard"]))
                    throw new InvalidOperationException("An existing FX object's unchanged settings or behaviour references changed.");
        }
    }
}
