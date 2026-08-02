using System;
using System.Collections.Concurrent;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Net;
using System.Net.Sockets;
using System.Security.Cryptography;
using System.Text;
using System.Threading;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEngine;
using VRCForge.Core.MCP;

namespace VRCForge.Editor
{
    /// <summary>
    /// Project-scoped, loopback-only MCP service. Direct calls may execute only
    /// explicitly read-only tools. Preview, checkpoint-control, and write calls
    /// require the release-paired managed VRCForge App lane; writes additionally
    /// require a one-use approval/checkpoint execution context.
    /// </summary>
    public static class VRCForgeMcpCoreServer
    {
        private const string TransportSchema = "vrcforge.mcp.transport.v2";
        private const string ModernProtocolVersion = "2026-07-28";
        private const string ApprovedExecutionMetaKey = "io.vrcforge/approvedExecution";
        private const int MaxFrameBytes = 1024 * 1024;
        private const int MaxClients = 4;
        private const int SocketTimeoutMilliseconds = 15000;
        private const int ThreadJoinMilliseconds = 2000;
        private const int InvocationQueueTimeoutMilliseconds = 120000;
        private const int ApprovedExecutionMaxLifetimeMilliseconds = 120000;
        private const int ApprovedExecutionClockSkewMilliseconds = 5000;
        private const int MaxConsumedExecutionIds = 4096;
        private static readonly object LifecycleGate = new object();
        private static readonly object Gate = new object();
        private static readonly HashSet<TcpClient> ActiveClients = new HashSet<TcpClient>();
        private static readonly HashSet<Thread> ActiveWorkers = new HashSet<Thread>();
        private static readonly ConcurrentQueue<PendingInvocation> PendingInvocations = new ConcurrentQueue<PendingInvocation>();
        private static readonly Dictionary<string, long> ConsumedExecutionExpirations =
            new Dictionary<string, long>(StringComparer.Ordinal);
        private static readonly Queue<KeyValuePair<string, long>> ConsumedExecutionOrder =
            new Queue<KeyValuePair<string, long>>();
        private static readonly HashSet<string> PreviewTools = new HashSet<string>(StringComparer.Ordinal)
        {
            "vrc_set_material_shader",
            "vrc_duplicate_scene_object",
            "vrc_save_scene_object_as_prefab",
            "vrc_set_texture_import_settings",
            "vrc_set_constraint_sources",
            "vrc_create_component_feature",
            "vrc_build_parameter_bit_packed_clone",
            "vrc_atomic_reference_rename",
        };
        private static readonly HashSet<string> SafetyControlTools = new HashSet<string>(StringComparer.Ordinal)
        {
            "vrc_prepare_checkpoint",
            "vrc_reload_after_checkpoint_restore",
        };
        // This must use Core tool names, never AgentGateway handler names. It
        // is derived only from the startup-verified immutable 64-tool snapshot.
        private static ISet<string> ApprovedAppCoreTools = new HashSet<string>(StringComparer.Ordinal);

        private enum InvocationLane
        {
            DirectRead = 0,
            AppPreview = 1,
            AppSafetyControl = 2,
            AppSetupOutfitPoll = 3,
            ApprovedWrite = 4,
        }

        private sealed class VRCForgeMcpProtocolException : Exception { }

        private sealed class VRCForgeMcpMetadataError
        {
            internal int Code;
            internal string Message;
            internal JObject Data;
        }

        private sealed class PendingInvocation
        {
            public string ToolName;
            public JObject Arguments;
            public JObject ExecutionContext;
            public InvocationLane Lane;
            public TcpClient Client;
            public VRCForgeMcpPeerProcessEvidence PeerEvidence;
            public bool Modern;
            public readonly ManualResetEventSlim Completion = new ManualResetEventSlim(false);
            public JObject Response;
            public int State;
        }

        private static TcpListener listener;
        private static Thread acceptThread;
        private static volatile bool stopping;
        private static byte[] token;
        private static string descriptorPath;
        private static string descriptorInstanceId;
        private static VRCForgeToolDescriptor[] tools = new VRCForgeToolDescriptor[0];

        [InitializeOnLoadMethod]
        private static void RegisterEditorDomainInvocationPump()
        {
            // The main-thread pump belongs to the Unity editor domain, not to
            // any listener instance. Register it before Bootstrap can start a
            // listener from an update callback, so a successful Start never
            // depends on mutating the update delegate during its dispatch.
            EditorApplication.update -= DrainInvocations;
            EditorApplication.update += DrainInvocations;
        }

        public static void Start()
        {
            lock (LifecycleGate)
            {
                StartExclusive();
            }
        }

        public static bool IsReady
        {
            get
            {
                lock (Gate)
                {
                    return listener != null;
                }
            }
        }

        private static void StartExclusive()
        {
            Thread priorAcceptThread = null;
            Thread[] priorWorkers = null;
            string priorDescriptorPath = null;
            string priorDescriptorInstanceId = null;
            var failed = false;
            lock (Gate)
            {
                if (listener != null)
                {
                    Debug.Log("[VRCForge MCP] Core already ready.");
                    return;
                }

                try
                {
                    tools = SnapshotTools();
                    ApprovedAppCoreTools = SnapshotApprovedWriteTools(tools);
                    token = CreateToken();
                    descriptorPath = GetDescriptorPath();
                    descriptorInstanceId = Guid.NewGuid().ToString("N");
                    listener = new TcpListener(IPAddress.Loopback, 0);
                    listener.Start(MaxClients);
                    WriteDescriptor((IPEndPoint)listener.LocalEndpoint);
                    stopping = false;
                    acceptThread = new Thread(AcceptLoop);
                    acceptThread.IsBackground = true;
                    acceptThread.Name = "VRCForgeMcpCoreAccept";
                    acceptThread.Start();
                    Debug.Log("[VRCForge MCP] Core Ready (loopback project service).");
                }
                catch (Exception)
                {
                    StopLocked(out priorAcceptThread, out priorWorkers, out priorDescriptorPath,
                        out priorDescriptorInstanceId);
                    failed = true;
                }
            }
            if (failed)
            {
                JoinThreads(priorAcceptThread, priorWorkers);
                DeleteOwnedDescriptor(priorDescriptorPath, priorDescriptorInstanceId);
                Debug.LogWarning("[VRCForge MCP] Core failed to start.");
            }
        }

        public static void Stop()
        {
            lock (LifecycleGate)
            {
                StopExclusive();
            }
        }

        private static void StopExclusive()
        {
            Thread priorAcceptThread;
            Thread[] priorWorkers;
            string priorDescriptorPath;
            string priorDescriptorInstanceId;
            lock (Gate)
            {
                StopLocked(out priorAcceptThread, out priorWorkers, out priorDescriptorPath,
                    out priorDescriptorInstanceId);
            }
            JoinThreads(priorAcceptThread, priorWorkers);
            DeleteOwnedDescriptor(priorDescriptorPath, priorDescriptorInstanceId);
        }

        private static void StopLocked(
            out Thread priorAcceptThread,
            out Thread[] priorWorkers,
            out string priorDescriptorPath,
            out string priorDescriptorInstanceId)
        {
            stopping = true;
            if (listener != null)
            {
                try { listener.Stop(); } catch (SocketException) { }
                listener = null;
            }

            foreach (var client in ActiveClients)
            {
                try { client.Close(); } catch (SocketException) { }
            }
            ActiveClients.Clear();
            priorAcceptThread = acceptThread;
            priorWorkers = new List<Thread>(ActiveWorkers).ToArray();
            ActiveWorkers.Clear();
            priorDescriptorPath = descriptorPath;
            priorDescriptorInstanceId = descriptorInstanceId;
            acceptThread = null;
            tools = new VRCForgeToolDescriptor[0];
            ApprovedAppCoreTools = new HashSet<string>(StringComparer.Ordinal);
            ConsumedExecutionExpirations.Clear();
            ConsumedExecutionOrder.Clear();
            ClearSecret(ref token);
            descriptorPath = null;
            descriptorInstanceId = null;
            CancelPendingInvocations();
        }

        private static void JoinThreads(Thread priorAcceptThread, IEnumerable<Thread> priorWorkers)
        {
            JoinThread(priorAcceptThread);
            if (priorWorkers == null)
            {
                return;
            }
            foreach (var worker in priorWorkers)
            {
                JoinThread(worker);
            }
        }

        private static void JoinThread(Thread thread)
        {
            if (thread == null || thread == Thread.CurrentThread || !thread.IsAlive)
            {
                return;
            }
            try { thread.Join(ThreadJoinMilliseconds); }
            catch (ThreadStateException) { }
        }

        private static void DeleteOwnedDescriptor(string path, string instanceId)
        {
            if (string.IsNullOrEmpty(path) || string.IsNullOrEmpty(instanceId))
            {
                return;
            }
            try
            {
                if (!File.Exists(path))
                {
                    return;
                }
                var document = JObject.Parse(File.ReadAllText(path, Encoding.UTF8));
                if (string.Equals((string)document["instanceId"], instanceId, StringComparison.Ordinal)
                    && (int?)document["processId"] == System.Diagnostics.Process.GetCurrentProcess().Id)
                {
                    File.Delete(path);
                }
            }
            catch (IOException) { }
            catch (UnauthorizedAccessException) { }
            catch (JsonException) { }
            catch (FormatException) { }
            catch (InvalidCastException) { }
            catch (OverflowException) { }
        }

        private static VRCForgeToolDescriptor[] SnapshotTools()
        {
            return VRCForgeMcpToolContract.SnapshotExact(
                VRCForgeToolRegistry.DiscoverLoadedAssemblies().Tools);
        }

        private static ISet<string> SnapshotApprovedWriteTools(IEnumerable<VRCForgeToolDescriptor> snapshot)
        {
            var all = new HashSet<string>(StringComparer.Ordinal);
            var readOnly = new HashSet<string>(StringComparer.Ordinal);
            foreach (var descriptor in snapshot ?? Enumerable.Empty<VRCForgeToolDescriptor>())
            {
                if (descriptor == null || !VRCForgeMcpToolContract.IsExpectedDescriptor(descriptor)
                    || !all.Add(descriptor.Name))
                {
                    throw new InvalidOperationException("The packaged VRCForge MCP tool contract is invalid.");
                }
                if (descriptor.Permission == VRCForgeToolPermission.ReadOnly)
                {
                    readOnly.Add(descriptor.Name);
                }
            }
            var preview = new HashSet<string>(PreviewTools, StringComparer.Ordinal);
            var safety = new HashSet<string>(SafetyControlTools, StringComparer.Ordinal);
            if (all.Count != VRCForgeMcpToolContract.ToolCount
                || readOnly.Count != 8 || preview.Count != 8 || safety.Count != 2
                || !all.SetEquals(VRCForgeMcpToolContract.ExpectedToolNames)
                || !readOnly.SetEquals(VRCForgeMcpToolContract.ExpectedReadOnlyToolNames)
                || !all.IsSupersetOf(preview) || !all.IsSupersetOf(safety)
                || readOnly.Overlaps(preview) || readOnly.Overlaps(safety) || preview.Overlaps(safety))
            {
                throw new InvalidOperationException("The VRCForge MCP tool lanes do not match the packaged contract.");
            }
            var approved = new HashSet<string>(all, StringComparer.Ordinal);
            approved.ExceptWith(readOnly);
            approved.ExceptWith(safety);
            if (approved.Count != 54 || !approved.IsSupersetOf(preview))
            {
                throw new InvalidOperationException("The VRCForge approved-write tool contract is invalid.");
            }
            return approved;
        }

        private static byte[] CreateToken()
        {
            var value = new byte[32];
            using (var rng = new RNGCryptoServiceProvider())
            {
                rng.GetBytes(value);
            }
            return value;
        }

        private static void ClearSecret(ref byte[] value)
        {
            if (value != null)
            {
                Array.Clear(value, 0, value.Length);
                value = null;
            }
        }

        private static void AcceptLoop()
        {
            while (true)
            {
                TcpListener currentListener;
                lock (Gate)
                {
                    if (stopping || listener == null)
                    {
                        return;
                    }
                    currentListener = listener;
                }

                TcpClient client = null;
                try
                {
                    client = currentListener.AcceptTcpClient();
                    lock (Gate)
                    {
                        if (stopping || ActiveClients.Count >= MaxClients)
                        {
                            client.Close();
                            continue;
                        }
                        ActiveClients.Add(client);
                    }
                    var worker = new Thread(HandleClient)
                    {
                        IsBackground = true,
                        Name = "VRCForgeMcpCoreClient"
                    };
                    lock (Gate)
                    {
                        if (stopping)
                        {
                            ActiveClients.Remove(client);
                            client.Close();
                            continue;
                        }
                        ActiveWorkers.Add(worker);
                        try { worker.Start(client); }
                        catch (Exception)
                        {
                            ActiveWorkers.Remove(worker);
                            ActiveClients.Remove(client);
                            client.Close();
                            return;
                        }
                    }
                }
                catch (SocketException)
                {
                    lock (Gate)
                    {
                        if (stopping || listener == null)
                        {
                            return;
                        }
                    }
                    if (client != null) { try { client.Close(); } catch (SocketException) { } }
                }
                catch (ObjectDisposedException)
                {
                    if (client != null) { try { client.Close(); } catch (SocketException) { } }
                    return;
                }
                catch (InvalidOperationException)
                {
                    if (client != null) { try { client.Close(); } catch (SocketException) { } }
                    return;
                }
            }
        }

        private static void HandleClient(object state)
        {
            var client = (TcpClient)state;
            try
            {
                client.ReceiveTimeout = SocketTimeoutMilliseconds;
                client.SendTimeout = SocketTimeoutMilliseconds;
                using (client)
                using (var stream = client.GetStream())
                {
                    while (!stopping)
                    {
                        var envelope = ReadEnvelope(stream);
                        var message = envelope["message"] as JObject;
                        AuthenticateEnvelope(envelope, true);
                        var response = HandleMessage(message, client);
                        if (response != null)
                        {
                            WriteEnvelope(stream, response);
                        }
                    }
                }
            }
            catch (IOException) { }
            catch (SocketException) { }
            catch (VRCForgeMcpProtocolException) { }
            catch (JsonException) { }
            catch (Exception) { }
            finally
            {
                lock (Gate)
                {
                    ActiveClients.Remove(client);
                    ActiveWorkers.Remove(Thread.CurrentThread);
                }
            }
        }

        private static JObject ReadEnvelope(NetworkStream stream)
        {
            var first = stream.ReadByte();
            if (first < 0)
            {
                throw new IOException();
            }
            if (first != (byte)'{')
            {
                throw new VRCForgeMcpProtocolException();
            }
            return ParseEnvelope(ReadNewlineFrame(stream, (byte)first));
        }

        private static byte[] ReadNewlineFrame(NetworkStream stream, byte? first)
        {
            using (var buffer = new MemoryStream())
            {
                if (first.HasValue)
                {
                    buffer.WriteByte(first.Value);
                }
                while (true)
                {
                    var value = stream.ReadByte();
                    if (value < 0)
                    {
                        throw new IOException();
                    }
                    if (value == (byte)'\n')
                    {
                        break;
                    }
                    if (buffer.Length >= MaxFrameBytes)
                    {
                        throw new VRCForgeMcpProtocolException();
                    }
                    buffer.WriteByte((byte)value);
                }
                var payload = buffer.ToArray();
                if (payload.Length > 0 && payload[payload.Length - 1] == (byte)'\r')
                {
                    Array.Resize(ref payload, payload.Length - 1);
                }
                if (payload.Length == 0)
                {
                    throw new VRCForgeMcpProtocolException();
                }
                return payload;
            }
        }

        private static JObject ParseEnvelope(byte[] payload)
        {
            var envelope = JObject.Parse(new UTF8Encoding(false, true).GetString(payload));
            if (!string.Equals((string)envelope["schema"], TransportSchema, StringComparison.Ordinal)
                || !(envelope["message"] is JObject))
            {
                throw new VRCForgeMcpProtocolException();
            }
            return envelope;
        }

        private static void AuthenticateEnvelope(JObject envelope, bool required)
        {
            var authorizationToken = envelope["authorization"];
            if (authorizationToken == null)
            {
                if (required)
                {
                    throw new VRCForgeMcpProtocolException();
                }
                return;
            }
            if (authorizationToken.Type != JTokenType.String)
            {
                throw new VRCForgeMcpProtocolException();
            }
            var supplied = (string)authorizationToken;
            const string prefix = "Bearer ";
            if (string.IsNullOrEmpty(supplied) || !supplied.StartsWith(prefix, StringComparison.Ordinal)
                || !ConstantTimeTokenEquals(supplied.Substring(prefix.Length), token))
            {
                throw new VRCForgeMcpProtocolException();
            }
        }

        private static bool ConstantTimeTokenEquals(string presented, byte[] expected)
        {
            if (expected == null || string.IsNullOrEmpty(presented))
            {
                return false;
            }
            var prefix = "Bearer ";
            if (presented.StartsWith(prefix, StringComparison.Ordinal))
            {
                presented = presented.Substring(prefix.Length);
            }
            byte[] supplied;
            try { supplied = Convert.FromBase64String(presented); }
            catch (FormatException) { return false; }
            var difference = supplied.Length ^ expected.Length;
            var length = Math.Max(supplied.Length, expected.Length);
            for (var index = 0; index < length; index++)
            {
                var left = index < supplied.Length ? supplied[index] : (byte)0;
                var right = index < expected.Length ? expected[index] : (byte)0;
                difference |= left ^ right;
            }
            return difference == 0;
        }

        private static JObject HandleMessage(JObject message, TcpClient client)
        {
            if (!string.Equals((string)message["jsonrpc"], "2.0", StringComparison.Ordinal)
                || message["method"] == null || message["method"].Type != JTokenType.String)
            {
                throw new VRCForgeMcpProtocolException();
            }
            var method = (string)message["method"];
            var idProperty = message.Property("id");
            var hasId = idProperty != null;
            var id = idProperty == null ? null : idProperty.Value;
            if (hasId && (id == null || (id.Type != JTokenType.String && id.Type != JTokenType.Integer)))
            {
                throw new VRCForgeMcpProtocolException();
            }
            if (message["params"] != null && message["params"].Type != JTokenType.Object)
            {
                throw new VRCForgeMcpProtocolException();
            }
            var parameters = message["params"] as JObject;
            var metadataError = ValidateModernMetadata(parameters);
            if (metadataError != null)
            {
                return hasId ? Error(id, metadataError.Code, metadataError.Message, metadataError.Data) : null;
            }
            if (string.Equals(method, "server/discover", StringComparison.Ordinal))
            {
                return hasId ? Result(id, DiscoverResult(), true) : null;
            }

            if (string.Equals(method, "tools/list", StringComparison.Ordinal))
            {
                return hasId ? Result(id, ToolsListResult(true), true) : null;
            }
            if (string.Equals(method, "tools/call", StringComparison.Ordinal))
            {
                return hasId ? Result(id, InvokeTool(parameters, client, true), true) : null;
            }
            if (string.Equals(method, "resources/list", StringComparison.Ordinal))
            {
                var resources = new JObject { ["resources"] = new JArray() };
                resources["ttlMs"] = 3000;
                resources["cacheScope"] = "private";
                return hasId ? Result(id, resources, true) : null;
            }
            if (string.Equals(method, "prompts/list", StringComparison.Ordinal))
            {
                var prompts = new JObject { ["prompts"] = new JArray() };
                prompts["ttlMs"] = 3000;
                prompts["cacheScope"] = "private";
                return hasId ? Result(id, prompts, true) : null;
            }
            return hasId ? Error(id, -32601, "Method not found.") : null;
        }

        private static VRCForgeMcpMetadataError ValidateModernMetadata(JObject parameters)
        {
            var metadata = parameters == null ? null : parameters["_meta"] as JObject;
            if (metadata == null)
            {
                return new VRCForgeMcpMetadataError
                {
                    Code = -32021,
                    Message = "Required client capabilities are missing.",
                    Data = new JObject { ["requiredCapabilities"] = new JObject() },
                };
            }
            var requestedVersion = metadata["io.modelcontextprotocol/protocolVersion"];
            if (!string.Equals(
                    requestedVersion != null && requestedVersion.Type == JTokenType.String
                        ? (string)requestedVersion
                        : null,
                    ModernProtocolVersion,
                    StringComparison.Ordinal))
            {
                return new VRCForgeMcpMetadataError
                {
                    Code = -32022,
                    Message = "Unsupported protocol version.",
                    Data = new JObject
                    {
                        ["supported"] = new JArray(ModernProtocolVersion),
                        ["requested"] = requestedVersion != null && requestedVersion.Type == JTokenType.String
                            ? (string)requestedVersion
                            : string.Empty,
                    },
                };
            }
            if (!(metadata["io.modelcontextprotocol/clientCapabilities"] is JObject))
            {
                return new VRCForgeMcpMetadataError
                {
                    Code = -32021,
                    Message = "Required client capabilities are missing.",
                    Data = new JObject { ["requiredCapabilities"] = new JObject() },
                };
            }
            var clientInfoToken = metadata["io.modelcontextprotocol/clientInfo"];
            var clientInfo = clientInfoToken as JObject;
            if (clientInfoToken != null && (clientInfo == null
                || clientInfo["name"] == null || clientInfo["name"].Type != JTokenType.String
                || clientInfo["version"] == null || clientInfo["version"].Type != JTokenType.String))
            {
                return new VRCForgeMcpMetadataError { Code = -32602, Message = "Client identity is invalid." };
            }
            return null;
        }

        private static JObject InvokeTool(JObject parameters, TcpClient client, bool modern)
        {
            if (parameters == null || parameters["name"] == null || parameters["name"].Type != JTokenType.String
                || (parameters["arguments"] != null && !(parameters["arguments"] is JObject)))
            {
                throw new VRCForgeMcpProtocolException();
            }
            var toolName = (string)parameters["name"];
            var arguments = parameters["arguments"] as JObject ?? new JObject();
            VRCForgeToolDescriptor descriptor;
            try
            {
                descriptor = FindTool(toolName);
            }
            catch (KeyNotFoundException)
            {
                return ToolError("Unknown VRCForge tool.", modern);
            }

            if (descriptor.Permission == VRCForgeToolPermission.ReadOnly
                || IsStrictBlendshapePayloadRead(toolName, arguments))
            {
                return QueueInvocation(toolName, arguments, null, InvocationLane.DirectRead, client, null, modern);
            }

            var metadata = parameters["_meta"] as JObject;
            var executionContext = metadata == null ? null : metadata[ApprovedExecutionMetaKey] as JObject;
            if (executionContext == null)
            {
                return ToolError("This tool requires the VRCForge App approval and checkpoint lane.", modern);
            }
            VRCForgeMcpPeerProcessEvidence peerEvidence;
            if (!VRCForgeMcpPeerProcessVerifier.TryScreenManagedBackendPeer(client, out peerEvidence))
            {
                return ToolError("The VRCForge managed peer eligibility check failed.", modern);
            }
            int claimedProcessId;
            try
            {
                claimedProcessId = executionContext["clientProcessId"] != null
                    && executionContext["clientProcessId"].Type == JTokenType.Integer
                    ? executionContext["clientProcessId"].Value<int>()
                    : 0;
            }
            catch (Exception)
            {
                claimedProcessId = 0;
            }
            if (claimedProcessId != peerEvidence.ProcessId)
            {
                return ToolError("The VRCForge App process binding is invalid.", modern);
            }

            var laneName = (string)executionContext["lane"];
            InvocationLane lane;
            if (string.Equals(laneName, "app_preview", StringComparison.Ordinal))
            {
                lane = InvocationLane.AppPreview;
                if (!PreviewTools.Contains(toolName) || !HasExplicitPreviewRequest(arguments))
                {
                    return ToolError("The App preview request is not allowed.", modern);
                }
            }
            else if (string.Equals(laneName, "app_safety_control", StringComparison.Ordinal))
            {
                lane = InvocationLane.AppSafetyControl;
                if (!SafetyControlTools.Contains(toolName))
                {
                    return ToolError("The App safety-control tool is not allowed.", modern);
                }
            }
            else if (string.Equals(laneName, "app_setup_outfit_poll", StringComparison.Ordinal))
            {
                lane = InvocationLane.AppSetupOutfitPoll;
                if (!IsStrictSetupOutfitJobPoll(toolName, arguments)
                    || !ValidateManagedAppInstanceContext(executionContext))
                {
                    return ToolError("The App Setup Outfit job poll is not allowed.", modern);
                }
            }
            else if (string.Equals(laneName, "approved_write", StringComparison.Ordinal))
            {
                lane = InvocationLane.ApprovedWrite;
                if (!ValidateApprovedExecutionContext(executionContext, toolName, arguments))
                {
                    return ToolError("The approved execution context is invalid or expired.", modern);
                }
            }
            else
            {
                return ToolError("The VRCForge App execution lane is invalid.", modern);
            }

            return QueueInvocation(toolName, arguments, executionContext, lane, client, peerEvidence, modern);
        }

        private static bool IsStrictBlendshapePayloadRead(string toolName, JObject arguments)
        {
            if (!string.Equals(toolName, "vrc_export_blendshapes", StringComparison.Ordinal)
                || arguments == null || arguments.Count != 3)
            {
                return false;
            }
            var outputPath = arguments["outputPath"];
            var refreshAssets = arguments["refreshAssets"];
            var returnPayloadOnly = arguments["returnPayloadOnly"];
            return outputPath != null && outputPath.Type == JTokenType.String
                && string.IsNullOrEmpty((string)outputPath)
                && refreshAssets != null && refreshAssets.Type == JTokenType.Boolean
                && !refreshAssets.Value<bool>()
                && returnPayloadOnly != null && returnPayloadOnly.Type == JTokenType.Boolean
                && returnPayloadOnly.Value<bool>();
        }

        private static JObject QueueInvocation(
            string toolName,
            JObject arguments,
            JObject executionContext,
            InvocationLane lane,
            TcpClient client,
            VRCForgeMcpPeerProcessEvidence peerEvidence,
            bool modern)
        {

            var pending = new PendingInvocation
            {
                ToolName = toolName,
                Arguments = (JObject)arguments.DeepClone(),
                ExecutionContext = executionContext == null ? null : (JObject)executionContext.DeepClone(),
                Lane = lane,
                Client = client,
                PeerEvidence = peerEvidence,
                Modern = modern,
            };
            PendingInvocations.Enqueue(pending);
            var deadline = DateTime.UtcNow.AddMilliseconds(InvocationQueueTimeoutMilliseconds);
            while (!pending.Completion.Wait(250))
            {
                if (stopping || DateTime.UtcNow >= deadline)
                {
                    if (Interlocked.CompareExchange(ref pending.State, 3, 0) == 0)
                    {
                        throw new VRCForgeMcpProtocolException();
                    }
                }
            }
            if (Volatile.Read(ref pending.State) == 3 || pending.Response == null)
            {
                throw new VRCForgeMcpProtocolException();
            }
            return pending.Response;
        }

        private static bool HasExplicitPreviewRequest(JObject arguments)
        {
            return arguments != null
                && arguments["preview"] != null
                && arguments["preview"].Type == JTokenType.Boolean
                && arguments["preview"].Value<bool>();
        }

        private static VRCForgeToolDescriptor FindTool(string name)
        {
            foreach (var descriptor in tools)
            {
                if (string.Equals(descriptor.Name, name, StringComparison.Ordinal))
                {
                    return descriptor;
                }
            }
            throw new KeyNotFoundException();
        }

        private static void DrainInvocations()
        {
            PendingInvocation pending;
            if (!PendingInvocations.TryDequeue(out pending))
            {
                return;
            }
            if (Interlocked.CompareExchange(ref pending.State, 1, 0) != 0)
            {
                pending.Completion.Set();
                return;
            }
            try
            {
                var descriptor = FindTool(pending.ToolName);
                if (!VRCForgeMcpToolContract.IsExpectedDescriptor(descriptor)
                    || !IsInvocationStillAuthorized(pending, descriptor))
                {
                    throw new VRCForgeMcpProtocolException();
                }
                var result = descriptor.Handler.Invoke(null, new object[] { pending.Arguments });
                pending.Response = ToolResult(result, pending.Modern);
            }
            catch (Exception)
            {
                pending.Response = ToolError("VRCForge tool execution failed.", pending.Modern);
            }
            finally
            {
                Volatile.Write(ref pending.State, 2);
                pending.Completion.Set();
            }
        }

        private static bool IsInvocationStillAuthorized(
            PendingInvocation pending,
            VRCForgeToolDescriptor descriptor)
        {
            if (pending == null || descriptor == null)
            {
                return false;
            }
            if (pending.Lane == InvocationLane.DirectRead)
            {
                return descriptor.Permission == VRCForgeToolPermission.ReadOnly
                    || IsStrictBlendshapePayloadRead(pending.ToolName, pending.Arguments);
            }
            if (descriptor.Permission == VRCForgeToolPermission.ReadOnly
                || !ReverifyManagedPeer(pending))
            {
                return false;
            }
            if (pending.Lane == InvocationLane.AppPreview)
            {
                return PreviewTools.Contains(pending.ToolName);
            }
            if (pending.Lane == InvocationLane.AppSafetyControl)
            {
                return SafetyControlTools.Contains(pending.ToolName);
            }
            if (pending.Lane == InvocationLane.AppSetupOutfitPoll)
            {
                return IsStrictSetupOutfitJobPoll(pending.ToolName, pending.Arguments)
                    && ValidateManagedAppInstanceContext(pending.ExecutionContext);
            }
            return pending.Lane == InvocationLane.ApprovedWrite
                && ValidateApprovedExecutionContext(
                    pending.ExecutionContext,
                    pending.ToolName,
                    pending.Arguments)
                && ConsumeApprovedExecutionId(
                    (string)pending.ExecutionContext["executionId"],
                    pending.ExecutionContext["expiresAtUnixMs"].Value<long>());
        }

        private static bool IsStrictSetupOutfitJobPoll(string toolName, JObject arguments)
        {
            if (!string.Equals(toolName, "vrc_setup_outfit", StringComparison.Ordinal)
                || arguments == null
                || arguments.Properties().Count() != 1)
            {
                return false;
            }
            var jobId = arguments["jobId"];
            Guid parsed;
            return jobId != null
                && jobId.Type == JTokenType.String
                && Guid.TryParseExact((string)jobId, "N", out parsed);
        }

        private static bool ValidateManagedAppInstanceContext(JObject context)
        {
            if (context == null)
            {
                return false;
            }
            try
            {
                var projectHash = RequiredBoundedString(context, "projectHash", 64);
                var instanceId = RequiredBoundedString(context, "instanceId", 128);
                return IsLowerHex(projectHash, 64)
                    && string.Equals(projectHash, ComputeProjectHash(GetProjectRoot()), StringComparison.Ordinal)
                    && string.Equals(instanceId, descriptorInstanceId, StringComparison.Ordinal);
            }
            catch (Exception)
            {
                return false;
            }
        }

        private static bool ReverifyManagedPeer(PendingInvocation pending)
        {
            VRCForgeMcpPeerProcessEvidence evidence;
            if (pending == null || pending.ExecutionContext == null || pending.PeerEvidence == null
                || !VRCForgeMcpPeerProcessVerifier.TryScreenManagedBackendPeer(pending.Client, out evidence)
                || !pending.PeerEvidence.Matches(evidence))
            {
                return false;
            }
            try
            {
                return pending.ExecutionContext["clientProcessId"] != null
                    && pending.ExecutionContext["clientProcessId"].Type == JTokenType.Integer
                    && pending.ExecutionContext["clientProcessId"].Value<int>() == evidence.ProcessId;
            }
            catch (Exception)
            {
                return false;
            }
        }

        private static bool ValidateApprovedExecutionContext(
            JObject context,
            string toolName,
            JObject arguments)
        {
            if (context == null || arguments == null)
            {
                return false;
            }
            try
            {
                var executionId = RequiredBoundedString(context, "executionId", 128);
                var approvalId = RequiredBoundedString(context, "approvalId", 256);
                var checkpointId = RequiredBoundedString(context, "checkpointId", 256);
                var targetTool = RequiredBoundedString(context, "targetTool", 128);
                var boundUnityTool = RequiredBoundedString(context, "unityToolName", 128);
                var argumentsSha256 = RequiredBoundedString(context, "argumentsSha256", 64);
                var projectHash = RequiredBoundedString(context, "projectHash", 64);
                var instanceId = RequiredBoundedString(context, "instanceId", 128);
                var issuedAt = context["issuedAtUnixMs"].Value<long>();
                var expiresAt = context["expiresAtUnixMs"].Value<long>();
                var now = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds();
                if (string.IsNullOrEmpty(executionId)
                    || string.IsNullOrEmpty(approvalId)
                    || string.IsNullOrEmpty(checkpointId)
                    || !ApprovedAppCoreTools.Contains(targetTool)
                    || !string.Equals(targetTool, toolName, StringComparison.Ordinal)
                    || !string.Equals(boundUnityTool, toolName, StringComparison.Ordinal)
                    || !IsLowerHex(argumentsSha256, 64)
                    || !string.Equals(argumentsSha256, ComputeCanonicalJsonHash(arguments), StringComparison.Ordinal)
                    || !string.Equals(projectHash, ComputeProjectHash(GetProjectRoot()), StringComparison.Ordinal)
                    || !string.Equals(instanceId, descriptorInstanceId, StringComparison.Ordinal)
                    || expiresAt <= issuedAt
                    || expiresAt - issuedAt > ApprovedExecutionMaxLifetimeMilliseconds
                    || now < issuedAt - ApprovedExecutionClockSkewMilliseconds
                    || now >= expiresAt)
                {
                    return false;
                }
                lock (Gate)
                {
                    PurgeExpiredExecutionIds(now);
                    return !ConsumedExecutionExpirations.ContainsKey(executionId);
                }
            }
            catch (Exception)
            {
                return false;
            }
        }

        private static string RequiredBoundedString(JObject value, string name, int maxLength)
        {
            var tokenValue = value[name];
            if (tokenValue == null || tokenValue.Type != JTokenType.String)
            {
                return null;
            }
            var text = (string)tokenValue;
            return string.IsNullOrWhiteSpace(text) || text.Length > maxLength ? null : text;
        }

        private static bool IsLowerHex(string value, int length)
        {
            if (string.IsNullOrEmpty(value) || value.Length != length)
            {
                return false;
            }
            foreach (var character in value)
            {
                if (!((character >= '0' && character <= '9') || (character >= 'a' && character <= 'f')))
                {
                    return false;
                }
            }
            return true;
        }

        private static bool ConsumeApprovedExecutionId(string executionId, long expiresAtUnixMs)
        {
            var now = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds();
            if (string.IsNullOrEmpty(executionId) || expiresAtUnixMs <= now)
            {
                return false;
            }
            lock (Gate)
            {
                PurgeExpiredExecutionIds(now);
                if (ConsumedExecutionExpirations.ContainsKey(executionId)
                    || ConsumedExecutionExpirations.Count >= MaxConsumedExecutionIds)
                {
                    return false;
                }
                ConsumedExecutionExpirations.Add(executionId, expiresAtUnixMs);
                ConsumedExecutionOrder.Enqueue(
                    new KeyValuePair<string, long>(executionId, expiresAtUnixMs));
                return true;
            }
        }

        private static void PurgeExpiredExecutionIds(long nowUnixMs)
        {
            while (ConsumedExecutionOrder.Count > 0
                && ConsumedExecutionOrder.Peek().Value <= nowUnixMs)
            {
                var expired = ConsumedExecutionOrder.Dequeue();
                long recordedExpiry;
                if (ConsumedExecutionExpirations.TryGetValue(expired.Key, out recordedExpiry)
                    && recordedExpiry == expired.Value)
                {
                    ConsumedExecutionExpirations.Remove(expired.Key);
                }
            }
        }

        private static string ComputeCanonicalJsonHash(JToken value)
        {
            var canonical = CanonicalizeJson(value).ToString(Formatting.None);
            using (var sha = SHA256.Create())
            {
                var digest = sha.ComputeHash(new UTF8Encoding(false, true).GetBytes(canonical));
                var builder = new StringBuilder(digest.Length * 2);
                foreach (var item in digest)
                {
                    builder.Append(item.ToString("x2"));
                }
                return builder.ToString();
            }
        }

        private static JToken CanonicalizeJson(JToken value)
        {
            var objectValue = value as JObject;
            if (objectValue != null)
            {
                var result = new JObject();
                var properties = new List<JProperty>(objectValue.Properties());
                properties.Sort((left, right) => string.CompareOrdinal(left.Name, right.Name));
                foreach (var property in properties)
                {
                    result[property.Name] = CanonicalizeJson(property.Value);
                }
                return result;
            }
            var arrayValue = value as JArray;
            if (arrayValue != null)
            {
                var result = new JArray();
                foreach (var item in arrayValue)
                {
                    result.Add(CanonicalizeJson(item));
                }
                return result;
            }
            return value == null ? JValue.CreateNull() : value.DeepClone();
        }

        private static void CancelPendingInvocations()
        {
            PendingInvocation pending;
            while (PendingInvocations.TryDequeue(out pending))
            {
                Interlocked.CompareExchange(ref pending.State, 3, 0);
                pending.Completion.Set();
            }
        }

        private static JObject ToolResult(object value, bool modern)
        {
            var tokenValue = value == null ? JValue.CreateNull() : JToken.FromObject(value);
            var isError = value is IMcpResponse response && !response.Success;
            var structured = tokenValue as JObject ?? new JObject { ["result"] = tokenValue.DeepClone() };
            var result = new JObject
            {
                ["content"] = new JArray
                {
                    new JObject
                    {
                        ["type"] = "text",
                        ["text"] = tokenValue.ToString(Formatting.None),
                    },
                },
                ["structuredContent"] = structured,
                ["isError"] = isError,
            };
            if (modern)
            {
                result["resultType"] = "complete";
            }
            return result;
        }

        private static JObject ToolError(string message, bool modern)
        {
            var result = new JObject
            {
                ["content"] = new JArray
                {
                    new JObject { ["type"] = "text", ["text"] = message },
                },
                ["isError"] = true,
            };
            if (modern)
            {
                result["resultType"] = "complete";
            }
            return result;
        }

        private static JObject DiscoverResult()
        {
            return new JObject
            {
                ["supportedVersions"] = new JArray(ModernProtocolVersion),
                ["capabilities"] = new JObject
                {
                    ["tools"] = new JObject { ["listChanged"] = false },
                    ["resources"] = new JObject { ["listChanged"] = false },
                    ["prompts"] = new JObject { ["listChanged"] = false },
                },
                ["instructions"] = "Read tools are direct. Other tools require the release-paired managed VRCForge App lane.",
                ["ttlMs"] = 3000,
                ["cacheScope"] = "private",
            };
        }

        private static JObject ToolsListResult(bool modern)
        {
            var result = new JArray();
            var orderedTools = new List<VRCForgeToolDescriptor>(tools);
            orderedTools.Sort((left, right) => string.CompareOrdinal(left.Name, right.Name));
            foreach (var descriptor in orderedTools)
            {
                var annotations = new JObject();
                if (descriptor.Permission == VRCForgeToolPermission.ReadOnly)
                {
                    annotations["readOnlyHint"] = true;
                }
                else
                {
                    annotations["destructiveHint"] = true;
                }
                var metadata = new JObject
                {
                    ["permission"] = descriptor.Permission.ToString(),
                    ["group"] = descriptor.Group,
                    ["poll"] = descriptor.RequiresPolling
                        ? new JObject { ["action"] = descriptor.PollAction, ["maxSeconds"] = descriptor.MaxPollSeconds }
                        : JValue.CreateNull()
                };
                result.Add(new JObject
                {
                    ["name"] = descriptor.Name,
                    ["description"] = descriptor.Description,
                    ["inputSchema"] = descriptor.CreateInputSchema(),
                    ["annotations"] = annotations,
                    ["_meta"] = metadata
                });
            }
            var response = new JObject { ["tools"] = result };
            if (modern)
            {
                response["ttlMs"] = 3000;
                response["cacheScope"] = "private";
            }
            return response;
        }

        private static JObject Result(JToken id, JToken result, bool modern)
        {
            var responseResult = result == null ? new JObject() : result.DeepClone();
            if (modern)
            {
                var responseObject = responseResult as JObject;
                if (responseObject == null)
                {
                    responseObject = new JObject { ["value"] = responseResult };
                    responseResult = responseObject;
                }
                responseObject["resultType"] = "complete";
                var metadata = responseObject["_meta"] as JObject ?? new JObject();
                metadata["io.modelcontextprotocol/serverInfo"] = new JObject
                {
                    ["name"] = "VRCForge MCP Core",
                    ["version"] = "2",
                };
                responseObject["_meta"] = metadata;
            }
            return new JObject { ["jsonrpc"] = "2.0", ["id"] = id.DeepClone(), ["result"] = responseResult };
        }

        private static JObject Error(JToken id, int code, string message, JToken data = null)
        {
            var error = new JObject { ["code"] = code, ["message"] = message };
            if (data != null)
            {
                error["data"] = data.DeepClone();
            }
            return new JObject
            {
                ["jsonrpc"] = "2.0",
                ["id"] = id == null ? JValue.CreateNull() : id.DeepClone(),
                ["error"] = error
            };
        }

        private static void WriteEnvelope(NetworkStream stream, JObject message)
        {
            var payload = Encoding.UTF8.GetBytes(new JObject
            {
                ["schema"] = TransportSchema,
                ["message"] = message
            }.ToString(Formatting.None));
            if (payload.Length > MaxFrameBytes)
            {
                throw new VRCForgeMcpProtocolException();
            }
            stream.Write(payload, 0, payload.Length);
            stream.WriteByte((byte)'\n');
            stream.Flush();
        }

        private static string GetDescriptorPath()
        {
            return Path.Combine(GetProjectRoot(), "Library", "VRCForge", "mcp-core.json");
        }

        private static string GetProjectRoot()
        {
            return Directory.GetParent(Application.dataPath).FullName;
        }

        private static void WriteDescriptor(IPEndPoint endpoint)
        {
            var root = GetProjectRoot();
            var document = new JObject
            {
                ["schema"] = TransportSchema,
                ["transport"] = "tcp-newline-jsonrpc",
                ["protocolVersion"] = ModernProtocolVersion,
                ["supportedProtocolVersions"] = new JArray(ModernProtocolVersion),
                ["host"] = IPAddress.Loopback.ToString(),
                ["port"] = endpoint.Port,
                ["authMode"] = "bearer-per-request",
                ["authToken"] = Convert.ToBase64String(token),
                ["instanceId"] = descriptorInstanceId,
                ["processId"] = System.Diagnostics.Process.GetCurrentProcess().Id,
                ["projectPath"] = root,
                ["projectHash"] = ComputeProjectHash(root),
                ["startedAt"] = DateTime.UtcNow.ToString("o"),
                ["toolCount"] = tools.Length,
                ["lifecycle"] = "unity-editor-domain",
                ["executionPolicy"] = "read-direct-app-process-approved-writes"
            };
            WriteJsonAtomically(descriptorPath, document);
        }

        private static void WriteJsonAtomically(string path, JObject document)
        {
            var directory = Path.GetDirectoryName(path);
            Directory.CreateDirectory(directory);
            var temporary = path + "." + Guid.NewGuid().ToString("N") + ".tmp";
            try
            {
                File.WriteAllText(temporary, document.ToString(Formatting.None), new UTF8Encoding(false));
                if (File.Exists(path))
                {
                    File.Replace(temporary, path, null);
                }
                else
                {
                    File.Move(temporary, path);
                }
            }
            finally
            {
                if (File.Exists(temporary))
                {
                    try { File.Delete(temporary); } catch (IOException) { }
                }
            }
        }

        private static string ComputeProjectHash(string root)
        {
            using (var sha = SHA256.Create())
            {
                var digest = sha.ComputeHash(Encoding.UTF8.GetBytes(root));
                var builder = new StringBuilder(digest.Length * 2);
                foreach (var value in digest)
                {
                    builder.Append(value.ToString("x2"));
                }
                return builder.ToString();
            }
        }
    }
}
