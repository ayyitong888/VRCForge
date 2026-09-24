using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Security.Cryptography;
using System.Text;
using System.Text.RegularExpressions;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using UnityEngine;
using VRCForge.Core.MCP;

namespace VRCForge.Editor
{
    internal static class VRCForgeUserToolCatalog
    {
        internal const string RecordSchema = "vrcforge.user_unity_tool_install.v1";
        private const int MaxRecordBytes = 1024 * 1024;
        private const string StampPackageIdField = "PackageId";
        private const string StampPackageDigestField = "PackageDigest";

        internal static JObject List()
        {
            var result = new JArray();
            foreach (var recordPath in RecordPaths())
            {
                var record = ReadRecord(recordPath, out var packageId, out var reasons);
                if (record == null)
                {
                    result.Add(new JObject
                    {
                        ["packageId"] = packageId,
                        ["status"] = "unavailable",
                        ["available"] = false,
                        ["reasons"] = new JArray(reasons),
                    });
                    continue;
                }
                var package = DescribePackage(recordPath, record, reasons);
                result.Add(package);
            }
            return new JObject
            {
                ["schema"] = "vrcforge.user-tools.v1",
                ["tools"] = result,
                ["count"] = result.Count,
            };
        }

        internal static JObject Invoke(JObject arguments)
        {
            var packageId = ReadRequired(arguments, "packageId");
            var packageDigest = ReadRequired(arguments, "packageDigest");
            var toolId = ReadRequired(arguments, "toolId");
            var toolArguments = arguments["arguments"] as JObject;
            if (toolArguments == null)
            {
                return Unavailable("invalid_arguments", "arguments must be a JSON object.");
            }
            var recordPath = RecordPaths().FirstOrDefault(path =>
            {
                var directory = new FileInfo(path).Directory;
                return directory != null && directory.Parent != null && string.Equals(directory.Parent.Name, packageId, StringComparison.Ordinal);
            });
            if (recordPath == null)
            {
                return Unavailable("package_missing", "The approved user-tool package record is unavailable.");
            }
            var record = ReadRecord(recordPath, out _, out var reasons);
            if (record == null)
            {
                return Unavailable(reasons.FirstOrDefault() ?? "package_invalid", "The user-tool package record is unavailable.");
            }
            var description = DescribePackage(recordPath, record, reasons);
            if (description.Value<bool?>("available") != true)
            {
                return Unavailable("package_unavailable", "The approved user-tool package is unavailable: " + string.Join(", ", reasons));
            }
            if (!string.Equals((string)record["packageDigest"], packageDigest, StringComparison.Ordinal))
            {
                return Unavailable("package_digest_mismatch", "The approved user-tool package digest does not match.");
            }
            var tool = ((JArray)record["tools"]).OfType<JObject>().FirstOrDefault(item => string.Equals((string)item["toolId"], toolId, StringComparison.Ordinal));
            if (tool == null)
            {
                return Unavailable("tool_missing", "The approved user-tool record does not contain the requested tool.");
            }
            var typeName = (string)tool["typeName"];
            var type = FindLoadedType(typeName);
            if (type == null)
            {
                return Unavailable("pending_compile", "The approved user-tool type is not loaded by the current Core assembly set.");
            }
            var identity = ValidateLoadedType(type, record, tool, recordPath);
            if (identity != null)
            {
                return Unavailable(identity, "The loaded user-tool identity no longer matches its approved record.");
            }
            try
            {
                var handler = type.GetMethod("HandleCommand", BindingFlags.Public | BindingFlags.Static, null, new[] { typeof(JObject) }, null);
                var value = handler.Invoke(null, new object[] { toolArguments });
                if (value is VRCForgeToolResult toolResult) return toolResult.ToStructuredContent();
                if (value is JObject returned && ((returned["success"] != null && returned.Value<bool>("success") == false) || (returned["status"] != null && !string.Equals((string)returned["status"], "complete", StringComparison.Ordinal)))) return returned;
                return new JObject
                {
                    ["schema"] = "vrcforge.user-tool-result.v1",
                    ["status"] = "complete",
                    ["toolId"] = toolId,
                    ["result"] = value is JToken token ? token : JToken.FromObject(value ?? new JObject()),
                };
            }
            catch (Exception exception)
            {
                var actual = exception is TargetInvocationException target && target.InnerException != null
                    ? target.InnerException : exception;
                return new JObject
                {
                    ["schema"] = "vrcforge.user-tool-result.v1",
                    ["status"] = "unknown",
                    ["outcome"] = "unknown",
                    ["retryable"] = false,
                    ["errorCode"] = "user_tool_exception",
                    ["errorType"] = actual.GetType().FullName,
                    ["message"] = actual.Message,
                };
            }
        }

        private static JObject DescribePackage(string recordPathValue, JObject record, List<string> reasons)
        {
            var recordPath = new FileInfo(recordPathValue);
            var packageId = (string)record["packageId"] ?? (recordPath.Directory != null && recordPath.Directory.Parent != null ? recordPath.Directory.Parent.Name : string.Empty);
            var tools = new JArray();
            var available = ValidateRecordBaseline(record, reasons) && record.Value<bool?>("enabled") == true;
            if (record["enabled"] == null) reasons.Add("disabled");
            var entries = record["tools"] as JArray;
            if (entries == null || entries.Count == 0)
            {
                reasons.Add("tools_missing");
                available = false;
                entries = entries ?? new JArray();
            }
            foreach (var entry in entries.OfType<JObject>())
            {
                var tool = new JObject
                {
                    ["toolId"] = (string)entry["toolId"] ?? string.Empty,
                    ["typeName"] = (string)entry["typeName"] ?? string.Empty,
                    ["description"] = (string)entry["description"] ?? string.Empty,
                    ["inputSchema"] = entry["inputSchema"] ?? new JObject(),
                    ["available"] = false,
                };
                var type = FindLoadedType((string)entry["typeName"]);
                if (type == null) tool["reason"] = "pending_compile";
                else
                {
                    var identity = ValidateLoadedType(type, record, entry, recordPathValue);
                    if (identity == null) { tool["available"] = true; tool["reason"] = "ready"; }
                    else tool["reason"] = identity;
                }
                available = available && tool.Value<bool>("available");
                tools.Add(tool);
            }
            return new JObject
            {
                ["packageId"] = packageId,
                ["packageDigest"] = (string)record["packageDigest"] ?? string.Empty,
                ["status"] = available ? "ready" : "unavailable",
                ["available"] = available,
                ["enabled"] = record.Value<bool?>("enabled") == true,
                ["reasons"] = new JArray(reasons.Distinct(StringComparer.Ordinal)),
                ["tools"] = tools,
            };
        }

        private static string ValidateLoadedType(Type type, JObject record, JObject tool, string recordPath)
        {
            var attribute = (VRCForgeCommandAttribute)Attribute.GetCustomAttribute(type, typeof(VRCForgeCommandAttribute), false);
            if (attribute == null || !string.Equals(attribute.ToolId, (string)tool["toolId"], StringComparison.Ordinal)) return "tool_identity_mismatch";
            VRCForgeToolDescriptor descriptor;
            try { descriptor = VRCForgeToolRegistry.Describe(type); }
            catch { return "handler_invalid"; }
            var handler = descriptor.Handler;
            if (handler == null || handler.ReturnType != typeof(object)) return "handler_invalid";
            var expectedSchema = tool["inputSchema"] as JObject;
            if (expectedSchema == null || !JToken.DeepEquals(descriptor.CreateInputSchema(), expectedSchema)) return "schema_hash_mismatch";
            var sourceDigest = (string)tool["sourceSha256"];
            if (!Regex.IsMatch(sourceDigest ?? string.Empty, "^[0-9a-f]{64}$", RegexOptions.CultureInvariant)) return "source_hash_invalid";
            var sourceName = ((string)tool["source"] ?? string.Empty).Replace('\\', '/');
            var projectRoot = Directory.GetParent(Application.dataPath);
            var expectedPrefix = "Assets/VRCForgeUserTools/" + (string)record["packageId"] + "/Editor/";
            if (projectRoot == null || !sourceName.StartsWith(expectedPrefix, StringComparison.Ordinal)) return "source_path_invalid";
            var sourcePath = Path.Combine(projectRoot.FullName, sourceName.Replace('/', Path.DirectorySeparatorChar));
            if (string.IsNullOrWhiteSpace(sourceName) || !File.Exists(sourcePath) || !string.Equals(Sha256(File.ReadAllBytes(sourcePath)), sourceDigest, StringComparison.OrdinalIgnoreCase)) return "source_hash_mismatch";
            var stampType = FindLoadedType((string)record["stampType"]);
            if (stampType == null) return "stale_generated_stamp";
            if (!string.Equals(ReadConst(stampType, StampPackageIdField), (string)record["packageId"], StringComparison.Ordinal)) return "package_identity_mismatch";
            if (!string.Equals(ReadConst(stampType, StampPackageDigestField), (string)record["packageDigest"], StringComparison.Ordinal)) return "package_digest_mismatch";
            var repairs = record["repairs"] as JArray;
            if (repairs != null)
            {
                foreach (var repair in repairs.OfType<JObject>())
                {
                    if (!VRCForgeMcpToolContract.IsExpectedToolName((string)repair["toolId"])) return "repair_tool_unknown";
                    if (!string.Equals((string)repair["coreVersion"], VRCForgeMcpToolContract.ProductVersion, StringComparison.Ordinal)
                        || !string.Equals((string)repair["toolContractVersion"], VRCForgeMcpToolContract.ToolContractVersion, StringComparison.Ordinal)) return "core_baseline_mismatch";
                }
            }
            return null;
        }


        private static bool ValidateRecordBaseline(JObject record, List<string> reasons)
        {
            if (record.Value<bool?>("enabled") != true) reasons.Add("disabled");
            if (string.IsNullOrWhiteSpace((string)record["stampType"])) reasons.Add("stamp_missing");
            return reasons.Count == 0;
        }

        private static JObject ReadRecord(string pathValue, out string packageId, out List<string> reasons)
        {
            var path = new FileInfo(pathValue);
            packageId = path.Directory != null && path.Directory.Parent != null ? path.Directory.Parent.Name : string.Empty;
            reasons = new List<string>();
            try
            {
                if (!path.Exists || path.Length > MaxRecordBytes) { reasons.Add("record_unsafe"); return null; }
                var record = JObject.Parse(File.ReadAllText(path.FullName, Encoding.UTF8));
                if ((string)record["schema"] != RecordSchema || !string.Equals((string)record["packageId"], packageId, StringComparison.Ordinal) || string.IsNullOrWhiteSpace((string)record["stampType"])) { reasons.Add("record_schema_invalid"); return null; }
                if (!Regex.IsMatch((string)record["packageDigest"] ?? string.Empty, "^[0-9a-f]{64}$", RegexOptions.CultureInvariant)) reasons.Add("package_digest_invalid");
                return reasons.Count == 0 ? record : null;
            }
            catch { reasons.Add("record_unreadable"); return null; }
        }

        private static IEnumerable<string> RecordPaths()
        {
            var root = Path.Combine(Directory.GetParent(Application.dataPath).FullName, "Assets", "VRCForgeUserTools");
            if (!Directory.Exists(root)) return Enumerable.Empty<string>();
            return Directory.GetDirectories(root).OrderBy(path => path, StringComparer.Ordinal).Select(path => Path.Combine(path, "Editor", "tool-package.json"));
        }

        private static Type FindLoadedType(string name)
        {
            if (string.IsNullOrWhiteSpace(name)) return null;
            var matches = AppDomain.CurrentDomain.GetAssemblies().Where(assembly => !assembly.IsDynamic).Select(assembly => assembly.GetType(name, false, false) ?? assembly.GetType(ToNestedTypeName(name), false, false)).Where(type => type != null).Distinct().ToArray();
            return matches.Length == 1 ? matches[0] : null;
        }

        private static string ToNestedTypeName(string name)
        {
            var index = name == null ? -1 : name.LastIndexOf('.');
            return index < 0 ? name : name.Substring(0, index) + "+" + name.Substring(index + 1);
        }

        private static string ReadConst(Type type, string name)
        {
            var field = type.GetField(name, BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Static);
            if (field == null || !field.IsLiteral || field.FieldType != typeof(string)) return string.Empty;
            return field.GetRawConstantValue() as string ?? string.Empty;
        }

        private static string ReadRequired(JObject arguments, string name)
        {
            return arguments == null || arguments[name] == null || arguments[name].Type != JTokenType.String ? string.Empty : (string)arguments[name];
        }

        private static JObject Unavailable(string code, string message)
        {
            return new JObject { ["schema"] = "vrcforge.user-tool-result.v1", ["status"] = "unavailable", ["available"] = false, ["errorCode"] = code, ["message"] = message, ["retryable"] = false };
        }

        internal static VRCForgeToolResult ToToolResult(JObject value)
        {
            var status = (string)value["status"];
            if (string.Equals((string)value["_mcp_status"], "pending", StringComparison.Ordinal)
                || string.Equals(status, "pending", StringComparison.Ordinal))
            {
                var delay = value.Value<double?>("_mcp_poll_interval") ?? 1.0;
                return VRCForgeToolResult.Waiting((string)value["message"] ?? "User tool is still running.", delay, value["data"] ?? value["result"] ?? value);
            }
            if (value["success"] != null)
            {
                if (value.Value<bool>("success")) return VRCForgeToolResult.Completed((string)value["message"] ?? "User tool completed.", value["data"] ?? value["result"] ?? value);
                return VRCForgeToolResult.FailedWithCode((string)value["code"] ?? (string)value["errorCode"] ?? "user_tool_failed", (string)value["error"] ?? (string)value["message"] ?? "User tool failed.", value["data"] ?? value);
            }
            var message = (string)value["message"] ?? status ?? "User tool request failed.";
            if (string.Equals(status, "complete", StringComparison.Ordinal)) return VRCForgeToolResult.Completed(message, value["result"] ?? value);
            return VRCForgeToolResult.FailedWithCode((string)value["errorCode"] ?? "user_tool_unavailable", message, value);
        }

        private static string Sha256(string value)
        {
            using (var sha = SHA256.Create()) return BitConverter.ToString(sha.ComputeHash(Encoding.UTF8.GetBytes(value ?? string.Empty))).Replace("-", string.Empty).ToLowerInvariant();
        }

        private static string Sha256(byte[] value)
        {
            using (var sha = SHA256.Create()) return BitConverter.ToString(sha.ComputeHash(value ?? new byte[0])).Replace("-", string.Empty).ToLowerInvariant();
        }
    }

    [VRCForgeCommand(toolId: "vrc_list_user_tools", Summary = "when-to-use: inspect approved user-tool packages and their current availability. when-NOT-to-use: do not use to execute a user tool or infer that an unavailable tool was installed.", Access = VRCForgeCommandAccess.ReadOnly)]
    public static class ListUserToolsTool
    {
        public static object HandleCommand(JObject parameters) { return VRCForgeToolResult.Completed("User tool catalog listed.", VRCForgeUserToolCatalog.List()); }
    }

    [VRCForgeCommand(toolId: "vrc_invoke_user_tool", Summary = "when-to-use: invoke one exact approved user tool through the managed write lane after explicit approval. when-NOT-to-use: do not use for unapproved, missing, stale, disabled, or arbitrary assemblies.", Access = VRCForgeCommandAccess.RequiresApproval)]
    public static class InvokeUserToolTool
    {
        private sealed class Parameters
        {
            [VRCForgeInput("Approved package identifier.")] public string packageId { get; set; }
            [VRCForgeInput("Approved package SHA-256 digest.")] public string packageDigest { get; set; }
            [VRCForgeInput("Exact approved user tool identifier.")] public string toolId { get; set; }
            [VRCForgeInput("JSON object passed to the approved user tool.")] public JObject arguments { get; set; }
        }
        public static object HandleCommand(JObject parameters) { return VRCForgeUserToolCatalog.ToToolResult(VRCForgeUserToolCatalog.Invoke(parameters)); }
    }
}
