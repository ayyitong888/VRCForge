"""Exercise the production JSON-RPC error paths without routing any Unity tools."""

import re
from pathlib import Path

from test_curve_fx_authoring_runtime_contract import method
from test_fx_authoritative_state_preview import compile_probe


def test_core_protocol_errors_keep_rpc_and_private_failures_distinct(tmp_path):
    source = (Path(__file__).resolve().parents[1] /
              "Assets/VRCForge/Editor/MCP/VRCForgeMcpCoreServer.cs").read_text(encoding="utf-8")
    members = "\n".join(re.findall(r'^        private const (?:int|string) .*?;', source, re.M))
    members += "\n" + "\n".join(method(source, name) for name in (
        "private sealed class VRCForgeMcpMetadataError",
        "private sealed class VRCForgeMcpConnectionSession",
        "private static JObject HandleMessage(",
        "private static VRCForgeMcpMetadataError ValidateProtocolVersion(",
        "private static bool IsProtocolVersion(",
        "private static VRCForgeMcpMetadataError ValidateModernMetadata(",
        "private static JObject Error(",
    ))
    compile_probe(tmp_path, r'''
using System;
using System.Globalization;
using System.Net.Sockets;
using Newtonsoft.Json.Linq;

class Probe {
    private sealed class VRCForgeMcpProtocolException : Exception { }
    const string descriptorProjectId = "project", descriptorInstanceId = "instance";
    static JObject ProtocolRangeResult() => new JObject();
    static JObject CoreInfoResult() => new JObject();
    static JObject DiscoverResult() => new JObject();
    static JObject ToolsListResult(bool modern, string layer) => new JObject();
    static JObject InvokeTool(JObject p, TcpClient c, bool modern) => throw new Exception("Unexpected dispatch");
    static JObject ReadMcpWriteOperation(JObject p) => throw new Exception("Unexpected dispatch");
    static JObject Result(JToken id, JToken result, bool modern) =>
        new JObject { ["id"] = id, ["result"] = result };

    MEMBERS

    const string Version = "io.modelcontextprotocol/protocolVersion";
    const string Capabilities = "io.modelcontextprotocol/clientCapabilities";
    const string Binding = "io.vrcforge/projectBinding";
    const string Range = "io.vrcforge/protocolRange";
    static JObject Metadata() => new JObject {
        [Version] = ModernProtocolVersion,
        [Capabilities] = new JObject(),
        ["io.modelcontextprotocol/clientInfo"] = new JObject {
            ["name"] = "VRCForge FastAPI", ["version"] = "test"
        },
        [Binding] = new JObject { ["projectId"] = "project", ["instanceId"] = "instance" },
        [Range] = new JObject { ["minimum"] = ModernProtocolVersion, ["maximum"] = ModernProtocolVersion }
    };

    static JObject Request(JObject metadata, string method = "tools/list", VRCForgeMcpConnectionSession session = null) =>
        HandleMessage(new JObject {
            ["id"] = "core-check", ["jsonrpc"] = "2.0", ["method"] = method,
            ["params"] = metadata == null ? new JObject() : new JObject { ["_meta"] = metadata }
        }, null, session ?? new VRCForgeMcpConnectionSession());

    static int failures;
    static void Check(bool passed, string name) {
        Console.WriteLine((passed ? "PASS " : "FAIL ") + name);
        if (!passed) failures++;
    }
    static bool IsError(JObject result, int code) =>
        (string)result["id"] == "core-check" && (int)result["error"]["code"] == code;
    static bool IsApplicationError(JObject result, string reason) {
        int code = (int)result["error"]["code"];
        return (string)result["id"] == "core-check"
            && (code < -32768 || code > -32000)
            && (string)result["error"]["data"]?["reason"] == reason;
    }
    static int Main() {
        Check(IsError(Request(null), -32602), "missing _meta");
        var metadata = Metadata(); metadata.Remove(Version);
        Check(IsError(Request(metadata), -32602), "missing protocolVersion");
        metadata = Metadata(); metadata[Version] = "not-a-version";
        Check(IsError(Request(metadata), -32602), "malformed protocolVersion");
        metadata = Metadata(); metadata.Remove(Capabilities);
        Check(IsError(Request(metadata), -32602), "missing clientCapabilities");
        metadata = Metadata(); metadata[Capabilities] = new JArray();
        Check(IsError(Request(metadata), -32602), "malformed clientCapabilities");
        metadata = Metadata(); metadata[Version] = "2025-11-25"; metadata.Remove(Capabilities);
        Check(IsError(Request(metadata), -32602), "required fields checked before supported versions");
        metadata[Capabilities] = new JObject();
        var unsupported = Request(metadata);
        var data = unsupported["error"]?["data"];
        Check(IsError(unsupported, -32022) && (string)data?["requested"] == "2025-11-25"
              && JToken.DeepEquals(data?["supported"], new JArray(ModernProtocolVersion)), "unsupported version includes supported list");
        metadata = Metadata(); metadata.Remove(Range);
        Check(IsError(Request(metadata), -32602), "missing private range");
        metadata = Metadata(); metadata[Range]["maximum"] = "2027-01-01"; metadata[Range]["minimum"] = "2027-01-01";
        Check(IsApplicationError(Request(metadata), "core_protocol_range_mismatch"), "private range mismatch");
        Check(IsApplicationError(Request(Metadata()), "core_discovery_required"), "discovery still required");
        metadata = Metadata(); metadata[Binding]["instanceId"] = "other";
        Check(IsApplicationError(Request(metadata, "server/discover"), "core_project_binding_mismatch"), "instance binding still enforced");
        var session = new VRCForgeMcpConnectionSession();
        Check(Request(Metadata(), "server/discover", session)["result"] != null
              && Request(Metadata(), "tools/list", session)["result"] != null, "discovery admits normal requests");
        return failures == 0 ? 0 : 1;
    }
}
'''.replace("MEMBERS", members), 12)
