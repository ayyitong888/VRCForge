using System;
using System.Collections.Generic;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEngine;
using VRCForge.Core.MCP;

namespace VRCForge.Editor
{
    [VRCForgeCommand(
        toolId: "vrc_scan_avatar_reference_diagnostics",
        Summary = "when-to-use: read missing script components and serialized missing object-reference signals under one exact scene avatar hierarchy, alongside menu parameter declaration facts. when-NOT-to-use: do not use this as a general null-reference validator, scan project assets, or infer that undeclared menu parameters are invalid because VRChat SDK built-ins may be implicit. Negative example: a null serialized field with instance ID zero is not reported as a broken reference.",
        Access = VRCForgeCommandAccess.ReadOnly
    )]
    public static class UnityAvatarReferenceDiagnostics
    {
        public const string ToolName = "vrc_scan_avatar_reference_diagnostics";

        public sealed class Parameters
        {
            [VRCForgeInput("Exact scene hierarchy path to the avatar root.", IsRequired = true)]
            public string avatarPath { get; set; } = "";

        }

        private sealed class Finding
        {
            public string kind { get; set; }
            public string gameObjectPath { get; set; }
            public string componentType { get; set; }
            public int? componentIndex { get; set; }
            public string serializedPropertyPath { get; set; }
            public string message { get; set; }
        }

        public static object HandleCommand(JObject @params)
        {
            var p = (@params ?? new JObject()).ToObject<Parameters>() ?? new Parameters();
            try
            {
                var avatar = ComponentCrudCore.ResolveGameObject(p.avatarPath);
                if (!avatar.scene.IsValid() || !avatar.scene.isLoaded)
                    throw new InvalidOperationException("avatarPath must resolve to a GameObject in a loaded scene.");

                var findings = new List<Finding>();
                var unresolvedScans = new List<object>();
                var missingScriptCount = 0;
                var missingReferenceCount = 0;
                var scannedGameObjectCount = 0;
                var scannedComponentCount = 0;

                foreach (var transform in avatar.GetComponentsInChildren<Transform>(true))
                {
                    scannedGameObjectCount++;
                    var gameObject = transform.gameObject;
                    var objectPath = ComponentCrudCore.GetHierarchyPath(transform);
                    var missingScripts = GameObjectUtility.GetMonoBehavioursWithMissingScriptCount(gameObject);
                    missingScriptCount += missingScripts;
                    if (missingScripts > 0)
                    {
                        findings.Add(new Finding
                        {
                            kind = "missing_script_component",
                            gameObjectPath = objectPath,
                            message = "Unity reports " + missingScripts + " missing MonoBehaviour script component(s) on this GameObject."
                        });
                    }

                    var components = gameObject.GetComponents<Component>();
                    for (var componentIndex = 0; componentIndex < components.Length; componentIndex++)
                    {
                        var component = components[componentIndex];
                        if (component == null) continue;
                        scannedComponentCount++;
                        try
                        {
                            using (var serialized = new SerializedObject(component))
                            {
                                var property = serialized.GetIterator();
                                while (property.Next(true))
                                {
                                    if (property.propertyType != SerializedPropertyType.ObjectReference
                                        || property.objectReferenceValue != null
                                        || property.objectReferenceInstanceIDValue == 0)
                                        continue;

                                    missingReferenceCount++;
                                    findings.Add(new Finding
                                    {
                                        kind = "serialized_missing_object_reference",
                                        gameObjectPath = objectPath,
                                        componentType = component.GetType().FullName ?? component.GetType().Name,
                                        componentIndex = componentIndex,
                                        serializedPropertyPath = property.propertyPath,
                                        message = "Unity reports a null object reference with a non-zero serialized instance ID."
                                    });
                                }
                            }
                        }
                        catch (Exception exception)
                        {
                            unresolvedScans.Add(new
                            {
                                gameObjectPath = objectPath,
                                componentType = component.GetType().FullName ?? component.GetType().Name,
                                componentIndex,
                                error = ComponentCrudCore.SafeExceptionMessage(exception)
                            });
                        }
                    }
                }

                var menuParameterEvidence = ReadMenuParameterEvidence(avatar);
                return VRCForgeToolResult.Completed(
                    "Scanned one avatar hierarchy for Unity-reported missing scripts and serialized missing object-reference signals.",
                    new
                    {
                        schema = "vrcforge.avatar_reference_diagnostics.v1",
                        avatarPath = ComponentCrudCore.GetHierarchyPath(avatar.transform),
                        readOnly = true,
                        mutationStarted = false,
                        proofStatus = unresolvedScans.Count == 0 ? "complete" : "indeterminate",
                        scannedGameObjectCount,
                        scannedComponentCount,
                        missingScriptCount,
                        missingObjectReferenceCount = missingReferenceCount,
                        findingCount = findings.Count,
                        problemCount = missingScriptCount + missingReferenceCount,
                        findings,
                        unresolvedScans,
                        menuParameterEvidence,
                        notes = new[]
                        {
                            "Only object-reference properties with a null resolved value and non-zero serialized instance ID are reported; an ordinary unassigned null reference is not treated as an error.",
                            "Menu parameter declaration facts compare menu controls with the avatar expression-parameters asset. A parameter absent from that asset is not classified as invalid because VRChat SDK built-in parameters may be implicit.",
                            "This scan covers the selected scene hierarchy only; it does not scan prefabs, project assets, SDK Builder rules, or runtime behavior."
                        }
                    });
            }
            catch (Exception exception)
            {
                return VRCForgeToolResult.FailedWithException(
                    "avatar_reference_diagnostics_failed",
                    "Avatar reference diagnostics could not scan the requested hierarchy: " + ComponentCrudCore.SafeExceptionMessage(exception),
                    exception,
                    new { avatarPath = p.avatarPath, readOnly = true, mutationStarted = false });
            }
        }

        private static object ReadMenuParameterEvidence(GameObject avatar)
        {
            var scanResult = AvatarControlScanner.HandleCommand(new JObject
            {
                ["avatarPath"] = ComponentCrudCore.GetHierarchyPath(avatar.transform),
                ["outputPath"] = ""
            }) as VRCForgeToolResult;
            if (scanResult == null || !scanResult.IsSuccessful || scanResult.Payload == null)
            {
                return new
                {
                    status = "unavailable",
                    reason = scanResult == null ? "control_scan_result_unavailable" : scanResult.Message,
                    controls = new object[0],
                    unmatchedCount = (int?)null
                };
            }

            var payload = JObject.FromObject(scanResult.Payload);
            var declaredParameters = new HashSet<string>(StringComparer.Ordinal);
            foreach (var item in payload["expressionParameterNames"] as JArray ?? new JArray())
            {
                var name = item.Value<string>();
                if (!string.IsNullOrWhiteSpace(name)) declaredParameters.Add(name);
            }

            var controls = new List<object>();
            var unmatchedCount = 0;
            foreach (var item in payload["items"] as JArray ?? new JArray())
            {
                if (!string.Equals((string)item["source"], "menu_control", StringComparison.Ordinal)) continue;
                var parameterName = ((string)item["parameterName"] ?? "").Trim();
                if (string.IsNullOrEmpty(parameterName)) continue;
                var explicitlyDeclared = declaredParameters.Contains(parameterName);
                if (!explicitlyDeclared) unmatchedCount++;
                controls.Add(new
                {
                    menuPath = (string)item["menuPath"] ?? "",
                    parameterName,
                    explicitlyDeclaredInExpressionParameters = explicitlyDeclared,
                    interpretation = explicitlyDeclared ? "declared" : "not_declared; may be an SDK built-in"
                });
            }

            var configuration = payload["descriptorConfiguration"] as JObject;
            return new
            {
                status = "available",
                descriptorPresent = configuration?["descriptorPresent"]?.Value<bool?>(),
                expressionsMenuAssigned = configuration?["expressionsMenuAssigned"]?.Value<bool?>(),
                expressionParametersAssigned = configuration?["expressionParametersAssigned"]?.Value<bool?>(),
                menuTraversalComplete = payload["menuTraversalComplete"]?.Value<bool?>(),
                controls,
                unmatchedCount,
                unmatchedInterpretation = "These names are not in the declared expression-parameters asset; this is evidence only, not a validation error, because SDK built-in parameters may be implicit."
            };
        }

    }
}
