using System;
using System.IO;
using System.Linq;
using System.Net;
using System.Net.Sockets;
using System.Text;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.SceneManagement;
using VRCForge.Core.MCP;
using VRCForge.Editor;
using VRCForge.Editor.Generic;

namespace VRCForge.LocalReview
{
    public static class GenericPluginControlFixture
    {
        private const string MenuRoot = "Local Review/Generic Plugin Control/";
        private static readonly string RunId = DateTime.UtcNow.ToString("yyyyMMddHHmmss");
        private static string MaterialPath { get { return "Assets/LocalReview/FixtureMaterial_" + RunId + ".mat"; } }
        private static GameObject expectedTarget;
        private static GameObject createdBySuccess;
        private static bool successSawTarget;
        private static bool emptySelectionSawEmpty;
        private static bool multiAttributeMenuInvoked;
        private static GameObject exceptionSideEffect;
        private static string callbackExceptionLog = string.Empty;

        [MenuItem(MenuRoot + "Create Result")]
        private static void CreateResult()
        {
            successSawTarget = Selection.activeObject == expectedTarget;
            createdBySuccess = new GameObject("LocalReview_MenuCreatedResult");
            Selection.activeObject = createdBySuccess;
        }

        [MenuItem(MenuRoot + "Run Without Selection")]
        private static void RunWithoutSelection()
        {
            emptySelectionSawEmpty = Selection.objects == null || Selection.objects.Length == 0;
        }

        [MenuItem(MenuRoot + "Multiple Attribute/First")]
        [MenuItem(MenuRoot + "Multiple Attribute/Second")]
        private static void MultipleAttributeMenu()
        {
            multiAttributeMenuInvoked = true;
        }

        [MenuItem(MenuRoot + "Throw After Side Effect")]
        private static void ThrowAfterSideEffect()
        {
            exceptionSideEffect = new GameObject("LocalReview_ExceptionSideEffect");
            throw new InvalidOperationException("local-review-menu-callback-exception");
        }

        public static void RunAll()
        {
            try
            {
                var discovery = AssertSuccess(UnityEditorCapabilityDiscovery.HandleCommand(new JObject
                {
                    ["kind"] = "menus", ["search"] = "Local Review/Generic Plugin Control/Create Result", ["limit"] = 10,
                }), "menu discovery");
                var discoveredItems = (JArray)((JObject)discovery.Payload)["items"];
                Require(discoveredItems.Any(item => (string)item["menuItemPath"] == MenuRoot + "Create Result" && (bool)item["validate"] == false),
                    "menu discovery did not return the executable fixture item");

                var multiAttributeDiscovery = AssertSuccess(UnityEditorCapabilityDiscovery.HandleCommand(new JObject
                {
                    ["kind"] = "menus", ["search"] = MenuRoot + "Multiple Attribute/", ["limit"] = 10,
                }), "multiple menu attribute discovery");
                var multiAttributeItems = (JArray)((JObject)multiAttributeDiscovery.Payload)["items"];
                Require(multiAttributeItems.Count(item => (string)item["menuItemPath"] == MenuRoot + "Multiple Attribute/First") == 1
                    && multiAttributeItems.Count(item => (string)item["menuItemPath"] == MenuRoot + "Multiple Attribute/Second") == 1,
                    "multiple MenuItem attributes were not exposed as separate menu capabilities");

                var windows = AssertSuccess(UnityEditorCapabilityDiscovery.HandleCommand(new JObject
                {
                    ["kind"] = "windows", ["limit"] = 10,
                }), "window discovery");
                Require((string)((JObject)windows.Payload)["schema"] == "vrcforge.unity_editor_capabilities.v1",
                    "window discovery returned an unexpected schema");

                var localReviewFolder = "Assets/LocalReview";
                if (!AssetDatabase.IsValidFolder(localReviewFolder)) AssetDatabase.CreateFolder("Assets", "LocalReview");
                var scene = EditorSceneManager.NewScene(NewSceneSetup.EmptyScene, NewSceneMode.Single);
                expectedTarget = new GameObject("LocalReview_ExplicitTarget");
                var decoy = new GameObject("LocalReview_PreexistingSelection");
                var scenePath = "Assets/LocalReview/GenericPluginControlFixture_" + RunId + ".unity";
                Require(EditorSceneManager.SaveScene(scene, scenePath), "fixture scene could not be saved");
                var targetId = GlobalObjectId.GetGlobalObjectIdSlow(expectedTarget).ToString();
                Selection.activeObject = decoy;

                var success = UnityEditorMenuExecuteTool.HandleCommand(new JObject
                {
                    ["menuItemPath"] = MenuRoot + "Create Result",
                    ["selectionGlobalObjectIds"] = new JArray(targetId),
                });
                var successPayload = AssertDispatch(success, "menu success dispatch", expectedTriggered: true);
                Require(successSawTarget, "menu callback did not receive the explicit target selection");
                Require(createdBySuccess != null && Selection.activeObject == createdBySuccess,
                    "menu executor did not preserve the callback's resulting selection");
                Require((string)successPayload["resultVerification"] == "pending",
                    "menu dispatch claimed a verified operation result");

                var noSelection = UnityEditorMenuExecuteTool.HandleCommand(new JObject
                {
                    ["menuItemPath"] = MenuRoot + "Run Without Selection",
                    ["selectionGlobalObjectIds"] = new JArray(),
                });
                AssertDispatch(noSelection, "empty selection dispatch", expectedTriggered: true);
                Require(emptySelectionSawEmpty, "an empty explicit selection was not applied before invocation");

                var multiAttributeDispatch = UnityEditorMenuExecuteTool.HandleCommand(new JObject
                {
                    ["menuItemPath"] = MenuRoot + "Multiple Attribute/Second",
                    ["selectionGlobalObjectIds"] = new JArray(),
                });
                AssertDispatch(multiAttributeDispatch, "multiple menu attribute dispatch", expectedTriggered: true);
                Require(multiAttributeMenuInvoked, "the second MenuItem attribute path did not dispatch to its method");

                Application.logMessageReceived += CaptureExceptionLog;
                var exceptionResult = UnityEditorMenuExecuteTool.HandleCommand(new JObject
                {
                    ["menuItemPath"] = MenuRoot + "Throw After Side Effect",
                    ["selectionGlobalObjectIds"] = new JArray(targetId),
                });
                Application.logMessageReceived -= CaptureExceptionLog;
                var exceptionPayload = exceptionResult as VRCForgeToolResult;
                Require(exceptionPayload != null, "exception dispatch did not return a structured command result");
                if (exceptionPayload.Kind == VRCForgeToolResultKind.Failed)
                {
                    var data = exceptionPayload.ToStructuredContent()["data"] as JObject;
                    Require(data != null && (bool?)data["checkpointRecoveryRequired"] == true,
                        "raised invocation exception did not report checkpoint recovery");
                    Require((string)data["state"] == "invocation_exception" && (string)data["dispatchState"] == "unknown",
                        "raised invocation exception was misreported as a definite dispatch result");
                }
                else
                {
                    var data = exceptionPayload.ToStructuredContent()["data"] as JObject;
                    Require(data != null && (bool?)data["triggered"] == true && (string)data["resultVerification"] == "pending",
                        "Unity callback exception was incorrectly reported as completed work");
                }
                Require(exceptionSideEffect != null,
                    "the fixture callback did not leave its pre-throw side effect in the isolated scene");
                Require(callbackExceptionLog.Contains("local-review-menu-callback-exception", StringComparison.Ordinal),
                    "Unity did not emit the intentional fixture callback exception to the captured log");

                var shader = Shader.Find("Standard");
                Require(shader != null, "Unity Standard shader is unavailable in the isolated project");
                var material = new Material(shader);
                AssetDatabase.CreateAsset(material, MaterialPath);
                AssetDatabase.SaveAssets();
                var materialResult = AssertSuccess(UnityMaterialPropertyInspect.HandleCommand(new JObject
                {
                    ["materialAssetPath"] = MaterialPath, ["offset"] = 0, ["limit"] = 256,
                }), "material property read");
                var materialData = (JObject)materialResult.Payload;
                Require((string)materialData["shaderName"] == shader.name && (bool)materialData["dirty"] == false,
                    "material read did not return the saved shader identity and clean state");
                Require(((JArray)materialData["properties"]).Count == shader.GetPropertyCount(),
                    "material read did not enumerate the shader's declared properties");

                var serializedReport = SerializedPluginFixture.Run();
                EditorSceneManager.SaveScene(SceneManager.GetActiveScene(), scenePath);
                var coreReport = ReadCoreServerInfo();
                var report = new JObject
                {
                    ["status"] = "passed",
                    ["editorVersion"] = Application.unityVersion,
                    ["checks"] = new JArray("menu discovery", "multiple MenuItem attribute discovery and dispatch", "window discovery", "exact selection", "callback selection preserved",
                        "empty selection", "exception leaves side effect and is reported unverified", "material shader property read",
                        "SerializedObject nested fields, array element, object reference and legacy path read/write",
                        "authenticated Core server/core-info, server/discover and tools/list registration"),
                    ["menuTriggered"] = (bool)successPayload["triggered"],
                    ["resultVerification"] = (string)successPayload["resultVerification"],
                    ["materialPropertyCount"] = ((JArray)materialData["properties"]).Count,
                    ["callbackExceptionLogCaptured"] = true,
                    ["serializedPropertyFixture"] = serializedReport,
                    ["coreInfo"] = coreReport.CoreInfo,
                    ["registeredTools"] = new JArray(coreReport.ToolNames),
                };
                var outputPath = Path.GetFullPath(Path.Combine(Application.dataPath, "..", "generic-plugin-control-result.json"));
                File.WriteAllText(outputPath, report.ToString());
                Debug.Log("GENERIC_PLUGIN_CONTROL_FIXTURE_RESULT " + report.ToString(Newtonsoft.Json.Formatting.None));
                EditorApplication.Exit(0);
            }
            catch (Exception exception)
            {
                var report = new JObject
                {
                    ["status"] = "failed",
                    ["editorVersion"] = Application.unityVersion,
                    ["errorType"] = exception.GetType().FullName,
                    ["error"] = exception.ToString(),
                };
                var outputPath = Path.GetFullPath(Path.Combine(Application.dataPath, "..", "generic-plugin-control-result.json"));
                File.WriteAllText(outputPath, report.ToString());
                Debug.LogError("GENERIC_PLUGIN_CONTROL_FIXTURE_RESULT " + report.ToString(Newtonsoft.Json.Formatting.None));
                EditorApplication.Exit(1);
            }
        }

        private static void CaptureExceptionLog(string condition, string stackTrace, LogType type)
        {
            if (condition != null && condition.IndexOf("local-review-menu-callback-exception", StringComparison.Ordinal) >= 0)
                callbackExceptionLog = condition + "\n" + stackTrace;
        }

        private static (JObject CoreInfo, string[] ToolNames) ReadCoreServerInfo()
        {
            VRCForgeMcpCoreServer.Start();
            if (!VRCForgeMcpCoreServer.IsReady)
                throw new InvalidOperationException("Isolated VRCForge Core server did not become ready.");

            try
            {
                var descriptorPath = Path.Combine(Directory.GetParent(Application.dataPath).FullName,
                    "Library", "VRCForge", "mcp-core.json");
                var descriptor = JObject.Parse(File.ReadAllText(descriptorPath));
                var host = IPAddress.Parse((string)descriptor["host"]);
                var port = (int)descriptor["port"];
                var token = (string)descriptor["authToken"];
                using (var client = new TcpClient())
                {
                    client.ReceiveTimeout = 10000;
                    client.SendTimeout = 10000;
                    client.Connect(host, port);
                    var stream = client.GetStream();
                    var infoMessage = SendCoreRequest(stream, token, "core-info", "server/core-info", new JObject());
                    var info = infoMessage["result"] as JObject;
                    Require(info != null && (string)info["schema"] == "vrcforge.core_info.v1",
                        "server/core-info returned an invalid Core identity payload");
                    Require((string)info["coreVersion"] == "1.10.0"
                        && (string)info["toolContractVersion"] == "190"
                        && (int?)info["toolCount"] == 102,
                        "server/core-info did not report the expected product, contract, and tool count");
                    var compileSnapshot = info["compileSnapshot"] as JObject;
                    Require(compileSnapshot != null && (bool?)compileSnapshot["isCompiling"] == false
                        && (bool?)compileSnapshot["hasErrors"] == false && (int?)compileSnapshot["errorCount"] == 0,
                        "server/core-info reported a Core script compilation error or incomplete state");

                    var discoverParameters = new JObject
                    {
                        ["_meta"] = new JObject
                        {
                            ["io.modelcontextprotocol/protocolVersion"] = "2026-07-28",
                            ["io.vrcforge/protocolRange"] = new JObject { ["minimum"] = "2026-07-28", ["maximum"] = "2026-07-28" },
                            ["io.vrcforge/projectBinding"] = new JObject
                            {
                                ["projectId"] = (string)info["projectId"],
                                ["instanceId"] = (string)info["instanceId"],
                            },
                            ["io.modelcontextprotocol/clientCapabilities"] = new JObject(),
                            ["io.modelcontextprotocol/clientInfo"] = new JObject { ["name"] = "VRCForge FastAPI", ["version"] = "local-review" },
                        }
                    };
                    var discoverMessage = SendCoreRequest(stream, token, "discover", "server/discover", discoverParameters);
                    Require(discoverMessage["result"] != null, "server/discover handshake failed");
                    var toolsMessage = SendCoreRequest(stream, token, "tools-list", "tools/list", new JObject
                    {
                        ["exposureLayer"] = "execution",
                        ["_meta"] = discoverParameters["_meta"].DeepClone(),
                    });
                    var tools = toolsMessage["result"]?["tools"] as JArray;
                    Require(tools != null, "tools/list did not return an executable Core tool list");
                    var names = tools.Select(item => (string)item["name"]).Where(name => !string.IsNullOrEmpty(name)).ToArray();
                    Require(names.Contains("vrc_execute_unity_editor_menu", StringComparer.Ordinal),
                        "live isolated Core did not expose the generic menu execution command");
                    Require(names.Contains("vrc_inspect_material_properties", StringComparer.Ordinal),
                        "live isolated Core did not expose the read-only material property command");
                    Require(names.Length == (int)info["toolCount"],
                        "server/core-info tool count differs from the executable tools/list result");
                    return (info, names);
                }
            }
            finally
            {
                VRCForgeMcpCoreServer.Stop();
            }
        }

        private static JObject SendCoreRequest(NetworkStream stream, string token, string id, string method, JObject parameters)
        {
            var envelope = new JObject
            {
                ["schema"] = "vrcforge.mcp.transport.v2",
                ["authorization"] = "Bearer " + token,
                ["message"] = new JObject
                {
                    ["jsonrpc"] = "2.0", ["id"] = id, ["method"] = method, ["params"] = parameters,
                }
            };
            var requestBytes = Encoding.UTF8.GetBytes(envelope.ToString(Newtonsoft.Json.Formatting.None) + "\n");
            stream.Write(requestBytes, 0, requestBytes.Length);
            stream.Flush();
            using (var responseBuffer = new MemoryStream())
            {
                while (true)
                {
                    var value = stream.ReadByte();
                    if (value < 0) throw new IOException("Isolated Core closed the connection before replying.");
                    if (value == (byte)'\n') break;
                    if (responseBuffer.Length >= 8 * 1024 * 1024)
                        throw new IOException("Isolated Core response exceeded the fixture's bounded 8 MiB frame.");
                    responseBuffer.WriteByte((byte)value);
                }
                var response = JObject.Parse(Encoding.UTF8.GetString(responseBuffer.ToArray()));
                var message = response["message"] as JObject;
                if (message == null || message["error"] != null)
                    throw new InvalidOperationException("Isolated Core request failed: " + (message?["error"]?.ToString() ?? "malformed response"));
                return message;
            }
        }

        private static VRCForgeToolResult AssertSuccess(object result, string label)
        {
            var typed = result as VRCForgeToolResult;
            if (typed == null || typed.Kind != VRCForgeToolResultKind.Completed)
                throw new InvalidOperationException(label + " failed: " + (typed == null ? "missing command result" : typed.Message));
            return typed;
        }

        private static JObject AssertDispatch(object result, string label, bool expectedTriggered)
        {
            var typed = AssertSuccess(result, label);
            var data = typed.ToStructuredContent()["data"] as JObject;
            Require(data != null && (bool?)data["triggered"] == expectedTriggered, label + " returned an unexpected trigger state");
            Require((string)data["resultVerification"] == "pending", label + " did not leave result verification pending");
            return data;
        }

        private static void Require(bool condition, string message)
        {
            if (!condition) throw new InvalidOperationException(message);
        }
    }
}
