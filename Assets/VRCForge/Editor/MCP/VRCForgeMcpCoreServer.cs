using System;
using System.Collections.Concurrent;
using System.Collections.Generic;
using System.IO;
using System.Globalization;
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
    /// require the protocol-negotiated managed VRCForge App lane; writes additionally
    /// require a one-use exact execution context. Internal Agent writes bind an
    /// approval/checkpoint; external MCP writes bind only their operation.
    /// </summary>
    public static class VRCForgeMcpCoreServer
    {
        private const string TransportSchema = "vrcforge.mcp.transport.v2";
        private const string ModernProtocolVersion = "2026-07-28";
        private const string MinimumProtocolVersion = "2026-07-28";
        private const string MaximumProtocolVersion = "2026-07-28";
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
            "vrc_set_material_texture",
            "vrc_duplicate_scene_object",
            "vrc_duplicate_project_asset",
            "vrc_save_scene_object_as_prefab",
            "vrc_save_current_scene",
            "vrc_save_new_scene",
            "vrc_set_texture_import_settings",
            "vrc_set_constraint_sources",
            "vrc_create_component_feature",
            "vrc_build_parameter_bit_packed_clone",
            "vrc_atomic_reference_rename",
            "vrc_restore_safe_backup",
            "vrc_ensure_expression_parameter",
            "vrc_ensure_expression_menu_control",
            "vrc_ensure_animator_state",
            "vrc_write_avatar_descriptor",
            "vrc_write_animation_curve",
            "vrc_manage_expression_parameters",
            "vrc_manage_expression_menu",
            "vrc_manage_fx_animator",
            "vrc_convert_unity_constraint",
            "vrc_add_wardrobe_outfit",
            "vrc_add_outfit_part",
            "vrc_add_modular_avatar_component",
            "vrc_manage_wardrobe",
        };
        private static readonly HashSet<string> SafetyControlTools = new HashSet<string>(StringComparer.Ordinal)
        {
            "vrc_prepare_checkpoint",
            "vrc_reload_after_checkpoint_restore",
        };
        // This must use Core tool names, never AgentGateway handler names. It
        // is derived only from the startup-verified immutable 76-tool snapshot.
        private static ISet<string> ApprovedAppCoreTools = new HashSet<string>(StringComparer.Ordinal);

        private enum InvocationLane
        {
            DirectRead = 0,
            AppPreview = 1,
            AppSafetyControl = 2,
            AppSetupOutfitPoll = 3,
            AppUnityPackageImportPoll = 4,
            ApprovedWrite = 5,
            ExternalMcpWrite = 6,
            AppBuildTestPoll = 7,
            AppAvatarUploadPoll = 8,
        }

        private sealed class VRCForgeMcpProtocolException : Exception { }

        private sealed class VRCForgeMcpMetadataError
        {
            internal int Code;
            internal string Message;
            internal JObject Data;
        }

        private sealed class VRCForgeMcpConnectionSession
        {
            internal bool HandshakeComplete;
            internal string ProtocolVersion;
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
        private static SynchronizationContext editorSynchronizationContext;

        [InitializeOnLoadMethod]
        private static void RegisterEditorDomainInvocationPump()
        {
            EnsureInvocationPumpRegistered();
            EditorApplication.playModeStateChanged -= HandlePlayModeStateChanged;
            EditorApplication.playModeStateChanged += HandlePlayModeStateChanged;
        }

        private static void HandlePlayModeStateChanged(PlayModeStateChange state)
        {
            // Enter/exit Play Mode can replace Editor callbacks without a C#
            // domain reload. Rebind the Core pump on the next editor turn so
            // the listener cannot remain alive while Unity calls stop draining.
            ScheduleInvocationPumpRegistration();
        }

        internal static void EnsureInvocationPumpRegistered()
        {
            if (SynchronizationContext.Current != null)
            {
                editorSynchronizationContext = SynchronizationContext.Current;
            }
            // The main-thread pump belongs to the Unity editor domain, not to
            // any listener instance. Register it before Bootstrap can start a
            // listener from an update callback, so a successful Start never
            // depends on mutating the update delegate during its dispatch.
            EditorApplication.update -= DrainInvocations;
            EditorApplication.update += DrainInvocations;
        }

        internal static void ScheduleInvocationPumpRegistration()
        {
            EditorApplication.delayCall -= EnsureInvocationPumpRegistered;
            EditorApplication.delayCall += EnsureInvocationPumpRegistered;
        }

        private static void RequestInvocationDrain()
        {
            var context = editorSynchronizationContext;
            if (context == null)
            {
                return;
            }
            try
            {
                context.Post(_ => DrainInvocations(), null);
            }
            catch (InvalidOperationException)
            {
                // The editor update registration remains the bounded fallback
                // if Unity is replacing its synchronization context.
            }
        }

        public static void Start()
        {
            // Startup may be entered from another EditorApplication.update
            // callback before InitializeOnLoadMethod ordering has rebound the
            // invocation pump. Queue a next-turn registration on every start,
            // including an idempotent Start against an existing listener.
            ScheduleInvocationPumpRegistration();
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
            Exception startupFailure = null;
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
                catch (Exception exception)
                {
                    StopLocked(out priorAcceptThread, out priorWorkers, out priorDescriptorPath,
                        out priorDescriptorInstanceId);
                    startupFailure = exception;
                    failed = true;
                }
            }
            if (failed)
            {
                JoinThreads(priorAcceptThread, priorWorkers);
                DeleteOwnedDescriptor(priorDescriptorPath, priorDescriptorInstanceId);
                Debug.LogWarning("[VRCForge MCP] Core failed to start: "
                    + startupFailure.GetType().Name + ": " + startupFailure.Message);
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
                if (descriptor.Permission == VRCForgeCommandAccess.ReadOnly)
                {
                    readOnly.Add(descriptor.Name);
                }
            }
            var preview = new HashSet<string>(PreviewTools, StringComparer.Ordinal);
            var safety = new HashSet<string>(SafetyControlTools, StringComparer.Ordinal);
            var expectedAll = VRCForgeMcpToolContract.ExpectedToolNames;
            var expectedReadOnly = VRCForgeMcpToolContract.ExpectedReadOnlyToolNames;
            var laneProblems = new List<string>();
            if (!all.SetEquals(expectedAll))
            {
                laneProblems.Add("tool registry missing=[" + string.Join(",", expectedAll.Except(all).OrderBy(name => name, StringComparer.Ordinal).ToArray())
                    + "] unexpected=[" + string.Join(",", all.Except(expectedAll).OrderBy(name => name, StringComparer.Ordinal).ToArray()) + "]");
            }
            if (!readOnly.SetEquals(expectedReadOnly))
            {
                laneProblems.Add("read-only missing=[" + string.Join(",", expectedReadOnly.Except(readOnly).OrderBy(name => name, StringComparer.Ordinal).ToArray())
                    + "] unexpected=[" + string.Join(",", readOnly.Except(expectedReadOnly).OrderBy(name => name, StringComparer.Ordinal).ToArray()) + "]");
            }
            if (!all.IsSupersetOf(preview))
            {
                laneProblems.Add("preview tools absent from registry=[" + string.Join(",", preview.Except(all).OrderBy(name => name, StringComparer.Ordinal).ToArray()) + "]");
            }
            if (!all.IsSupersetOf(safety))
            {
                laneProblems.Add("safety tools absent from registry=[" + string.Join(",", safety.Except(all).OrderBy(name => name, StringComparer.Ordinal).ToArray()) + "]");
            }
            if (readOnly.Overlaps(preview) || readOnly.Overlaps(safety) || preview.Overlaps(safety))
            {
                laneProblems.Add("read-only, preview, and safety lanes overlap");
            }
            if (laneProblems.Count != 0)
            {
                throw new InvalidOperationException(
                    "The VRCForge MCP tool lanes do not match the packaged contract: "
                    + string.Join("; ", laneProblems.ToArray()));
            }
            var approved = new HashSet<string>(all, StringComparer.Ordinal);
            approved.ExceptWith(readOnly);
            approved.ExceptWith(safety);
            var missingApprovedPreview = preview.Except(approved).OrderBy(name => name, StringComparer.Ordinal).ToArray();
            if (missingApprovedPreview.Length != 0)
            {
                throw new InvalidOperationException(
                    "The VRCForge approved-write tool contract is invalid: preview tools missing from approved-write lane=["
                    + string.Join(",", missingApprovedPreview) + "].");
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
                    var session = new VRCForgeMcpConnectionSession();
                    while (!stopping)
                    {
                        var envelope = ReadEnvelope(stream);
                        var message = envelope["message"] as JObject;
                        AuthenticateEnvelope(envelope, true);
                        JObject response;
                        try
                        {
                            response = HandleMessage(message, client, session);
                        }
                        catch (VRCForgeMcpProtocolException)
                        {
                            throw;
                        }
                        catch (Exception exception)
                        {
                            response = BuildUnhandledMessageResponse(message, session, exception);
                        }
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

        private static JObject BuildUnhandledMessageResponse(
            JObject message,
            VRCForgeMcpConnectionSession session,
            Exception exception)
        {
            var method = message == null ? string.Empty : (string)message["method"] ?? string.Empty;
            var parameters = message == null ? null : message["params"] as JObject;
            var toolName = parameters == null ? string.Empty : (string)parameters["name"] ?? string.Empty;
            var id = message == null ? null : message["id"];
            var exceptionType = exception == null ? "Exception" : exception.GetType().FullName;
            var exceptionMessage = exception == null ? "Unknown Unity Core exception." : exception.Message;
            var exceptionStack = exception == null ? string.Empty : exception.StackTrace ?? string.Empty;
            var humanMessage = $"Unity Core request dispatch failed: {exceptionType}: {exceptionMessage}";
            Debug.LogError($"[VRCForge MCP] {humanMessage}\n{exceptionStack}");

            var details = new JObject
            {
                ["method"] = method,
                ["toolName"] = toolName,
                ["exceptionType"] = exceptionType,
                ["exceptionMessage"] = exceptionMessage,
                ["exceptionStack"] = exceptionStack,
            };
            if (string.Equals(method, "tools/call", StringComparison.Ordinal) && id != null)
            {
                var toolError = ToolError("unity_core_unhandled_exception", humanMessage, true);
                var structured = toolError["structuredContent"] as JObject;
                if (structured != null)
                {
                    structured["failureLayer"] = "unity_core_dispatch";
                    structured["failurePhase"] = "request_dispatch_exception";
                    structured["toolRoutingStarted"] = JValue.CreateNull();
                    structured["details"] = details;
                }
                return Result(id, toolError, true);
            }

            return id == null
                ? null
                : Error(id, -32603, humanMessage, new JObject
                {
                    ["schema"] = "vrcforge.unity_core_error.v1",
                    ["errorCode"] = "unity_core_unhandled_exception",
                    ["failureLayer"] = "unity_core_dispatch",
                    ["failurePhase"] = "request_dispatch_exception",
                    ["toolRoutingStarted"] = JValue.CreateNull(),
                    ["mutationStarted"] = JValue.CreateNull(),
                    ["committed"] = JValue.CreateNull(),
                    ["commitState"] = "unknown",
                    ["details"] = details,
                });
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

        private static JObject HandleMessage(
            JObject message,
            TcpClient client,
            VRCForgeMcpConnectionSession session)
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
            if (string.Equals(method, "server/core-info", StringComparison.Ordinal))
            {
                return hasId ? Result(id, CoreInfoResult(), true) : null;
            }
            var metadataError = ValidateModernMetadata(parameters);
            if (metadataError != null)
            {
                var data = metadataError.Data as JObject ?? new JObject();
                data["coreInfo"] = CoreInfoResult();
                metadataError.Data = data;
                return hasId ? Error(id, metadataError.Code, metadataError.Message, metadataError.Data) : null;
            }
            if (string.Equals(method, "server/discover", StringComparison.Ordinal))
            {
                session.HandshakeComplete = true;
                session.ProtocolVersion = (string)((parameters["_meta"] as JObject)["io.modelcontextprotocol/protocolVersion"]);
                return hasId ? Result(id, DiscoverResult(), true) : null;
            }
            if (!session.HandshakeComplete
                || !IsProtocolVersion(session.ProtocolVersion))
            {
                return hasId
                    ? Error(
                        id,
                        -32023,
                        "MCP handshake is required before using Core methods.",
                        new JObject
                        {
                            ["requiredMethod"] = "server/discover",
                            ["protocolRange"] = ProtocolRangeResult(),
                            ["coreInfo"] = CoreInfoResult(),
                        })
                    : null;
            }

            if (string.Equals(method, "tools/list", StringComparison.Ordinal))
            {
                var exposureLayerToken = parameters == null ? null : parameters["exposureLayer"];
                if (exposureLayerToken != null && exposureLayerToken.Type != JTokenType.String)
                {
                    return hasId ? Error(id, -32602, "exposureLayer must be planning or execution.") : null;
                }
                var exposureLayer = exposureLayerToken == null ? null : (string)exposureLayerToken;
                exposureLayer = string.IsNullOrEmpty(exposureLayer) ? "planning" : exposureLayer;
                if (!string.Equals(exposureLayer, "planning", StringComparison.Ordinal)
                    && !string.Equals(exposureLayer, "execution", StringComparison.Ordinal))
                {
                    return hasId ? Error(id, -32602, "exposureLayer must be planning or execution.") : null;
                }
                return hasId ? Result(id, ToolsListResult(true, exposureLayer), true) : null;
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

        private static VRCForgeMcpMetadataError ValidateProtocolVersion(JObject metadata)
        {
            var token = metadata["io.modelcontextprotocol/protocolVersion"];
            var requested = token != null && token.Type == JTokenType.String ? (string)token : string.Empty;
            var clientRange = metadata["io.vrcforge/protocolRange"] as JObject;
            var clientMinimum = clientRange == null ? string.Empty : (string)clientRange["minimum"] ?? string.Empty;
            var clientMaximum = clientRange == null ? string.Empty : (string)clientRange["maximum"] ?? string.Empty;
            if (IsProtocolVersion(requested)
                && IsProtocolVersion(clientMinimum)
                && IsProtocolVersion(clientMaximum)
                && string.CompareOrdinal(clientMinimum, requested) <= 0
                && string.CompareOrdinal(requested, clientMaximum) <= 0
                && string.CompareOrdinal(MinimumProtocolVersion, requested) <= 0
                && string.CompareOrdinal(requested, MaximumProtocolVersion) <= 0)
            {
                return null;
            }
            return new VRCForgeMcpMetadataError
            {
                Code = -32022,
                Message = "No compatible MCP protocol version was negotiated.",
                Data = new JObject
                {
                    ["requested"] = requested,
                    ["clientRange"] = clientRange == null ? JValue.CreateNull() : clientRange.DeepClone(),
                    ["coreRange"] = ProtocolRangeResult(),
                },
            };
        }

        private static bool IsProtocolVersion(string value)
        {
            DateTime parsed;
            return !string.IsNullOrEmpty(value)
                && DateTime.TryParseExact(
                    value,
                    "yyyy-MM-dd",
                    CultureInfo.InvariantCulture,
                    DateTimeStyles.None,
                    out parsed);
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
            var protocolError = ValidateProtocolVersion(metadata);
            if (protocolError != null) return protocolError;
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
            if (clientInfo == null
                || clientInfo["name"] == null || clientInfo["name"].Type != JTokenType.String
                || clientInfo["version"] == null || clientInfo["version"].Type != JTokenType.String)
            {
                return new VRCForgeMcpMetadataError { Code = -32602, Message = "Client identity is invalid." };
            }
            if (!string.Equals((string)clientInfo["name"], "VRCForge FastAPI", StringComparison.Ordinal)
                || string.IsNullOrWhiteSpace((string)clientInfo["version"]))
            {
                return new VRCForgeMcpMetadataError
                {
                    Code = -32602,
                    Message = "Client identity is invalid.",
                };
            }
            var projectBinding = metadata["io.vrcforge/projectBinding"] as JObject;
            if (projectBinding == null
                || !string.Equals((string)projectBinding["projectId"], ComputeProjectId(GetProjectRoot()), StringComparison.Ordinal)
                || !string.Equals((string)projectBinding["instanceId"], descriptorInstanceId, StringComparison.Ordinal))
            {
                return new VRCForgeMcpMetadataError
                {
                    Code = -32025,
                    Message = "Unity project instance handshake failed.",
                };
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
                return ToolError("unknown_tool", "Unknown VRCForge tool.", modern, true);
            }

            if (descriptor.Permission == VRCForgeCommandAccess.ReadOnly
                || IsStrictNoWritePayloadRead(toolName, arguments))
            {
                return QueueInvocation(toolName, arguments, null, InvocationLane.DirectRead, client, null, modern);
            }

            var metadata = parameters["_meta"] as JObject;
            var executionContext = metadata == null ? null : metadata[ApprovedExecutionMetaKey] as JObject;
            if (executionContext == null)
            {
                return ToolError("managed_write_required", "This tool requires a one-use managed write authorization.", modern, true);
            }
            VRCForgeMcpPeerProcessEvidence peerEvidence;
            if (!VRCForgeMcpPeerProcessVerifier.TryScreenManagedBackendPeer(client, out peerEvidence))
            {
                return ToolError("managed_peer_ineligible", "The authenticated loopback backend peer was rejected by the running Core before the requested Unity tool was routed.", modern, true);
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
                return ToolError("app_process_binding_invalid", "The VRCForge App process binding is invalid.", modern, true);
            }

            var laneName = (string)executionContext["lane"];
            InvocationLane lane;
            if (string.Equals(laneName, "app_preview", StringComparison.Ordinal))
            {
                lane = InvocationLane.AppPreview;
                if (!HasAllowedPreviewRequest(toolName, arguments))
                {
                    return ToolError("preview_not_allowed", "The App preview request is not allowed.", modern, true);
                }
            }
            else if (string.Equals(laneName, "app_safety_control", StringComparison.Ordinal))
            {
                lane = InvocationLane.AppSafetyControl;
                if (!IsStrictSafetyControlRequest(toolName, arguments))
                {
                    return ToolError("safety_control_not_allowed", "The App safety-control tool is not allowed.", modern, true);
                }
            }
            else if (string.Equals(laneName, "app_setup_outfit_poll", StringComparison.Ordinal))
            {
                lane = InvocationLane.AppSetupOutfitPoll;
                if (!IsStrictSetupOutfitJobPoll(toolName, arguments)
                    || !ValidateManagedAppInstanceContext(executionContext))
                {
                    return ToolError("setup_outfit_poll_not_allowed", "The App Setup Outfit job poll is not allowed.", modern, true);
                }
            }
            else if (string.Equals(laneName, "app_unitypackage_import_poll", StringComparison.Ordinal))
            {
                lane = InvocationLane.AppUnityPackageImportPoll;
                if (!IsStrictUnityPackageImportJobPoll(toolName, arguments)
                    || !ValidateManagedAppInstanceContext(executionContext))
                {
                    return ToolError("unitypackage_import_poll_not_allowed", "The App UnityPackage import job poll is not allowed.", modern, true);
                }
            }
            else if (string.Equals(laneName, "app_build_test_poll", StringComparison.Ordinal))
            {
                lane = InvocationLane.AppBuildTestPoll;
                if (!IsStrictBuildTestJobPoll(toolName, arguments)
                    || !ValidateManagedAppInstanceContext(executionContext))
                {
                    return ToolError("build_test_poll_not_allowed", "The App VRChat Build & Test job poll is not allowed.", modern, true);
                }
            }
            else if (string.Equals(laneName, "app_avatar_upload_poll", StringComparison.Ordinal))
            {
                lane = InvocationLane.AppAvatarUploadPoll;
                if (!IsStrictAvatarUploadJobPoll(toolName, arguments)
                    || !ValidateManagedAppInstanceContext(executionContext))
                {
                    return ToolError("avatar_upload_poll_not_allowed", "The App VRChat avatar upload job poll is not allowed.", modern, true);
                }
            }
            else if (string.Equals(laneName, "approved_write", StringComparison.Ordinal))
            {
                lane = InvocationLane.ApprovedWrite;
                if (!ValidateApprovedExecutionContext(executionContext, toolName, arguments))
                {
                    return ToolError("approved_execution_invalid", "The approved execution context is invalid or expired.", modern, true);
                }
            }
            else if (string.Equals(laneName, "external_mcp_write", StringComparison.Ordinal))
            {
                lane = InvocationLane.ExternalMcpWrite;
                string externalExecutionFailure;
                if (!ValidateExternalMcpExecutionContext(
                    executionContext,
                    toolName,
                    arguments,
                    out externalExecutionFailure))
                {
                    return ToolError(
                        "external_mcp_execution_" + externalExecutionFailure,
                        "The external MCP execution context was rejected before the Unity tool started.",
                        modern,
                        true);
                }
            }
            else
            {
                return ToolError("app_lane_invalid", "The VRCForge App execution lane is invalid.", modern, true);
            }

            return QueueInvocation(toolName, arguments, executionContext, lane, client, peerEvidence, modern);
        }

        private static bool IsStrictNoWritePayloadRead(string toolName, JObject arguments)
        {
            if (arguments == null)
            {
                return false;
            }
            if (string.Equals(toolName, "vrc_export_blendshapes", StringComparison.Ordinal))
            {
                return HasExactKeys(arguments, "outputPath", "refreshAssets", "returnPayloadOnly")
                    && HasEmptyOutputPath(arguments)
                    && HasFalseBoolean(arguments, "refreshAssets")
                    && HasTrueBoolean(arguments, "returnPayloadOnly");
            }
            if (string.Equals(toolName, "vrc_scan_avatar_controls", StringComparison.Ordinal)
                || string.Equals(toolName, "vrc_scan_avatar_parameters", StringComparison.Ordinal)
                || string.Equals(toolName, "vrc_scan_wardrobe", StringComparison.Ordinal)
                || string.Equals(toolName, "vrc_scan_thry_avatar_performance", StringComparison.Ordinal))
            {
                return HasExactKeys(arguments, "avatarPath", "outputPath")
                    && HasString(arguments, "avatarPath") && HasEmptyOutputPath(arguments);
            }
            if (string.Equals(toolName, "vrc_scan_avatar_materials", StringComparison.Ordinal))
            {
                return HasExactKeys(arguments, "avatarPath", "outputPath", "refreshAssets")
                    && HasString(arguments, "avatarPath") && HasEmptyOutputPath(arguments)
                    && HasFalseBoolean(arguments, "refreshAssets");
            }
            if (string.Equals(toolName, "vrc_scan_avatar_items", StringComparison.Ordinal))
            {
                return HasExactKeys(arguments, "avatarPath", "outputPath", "maxItems", "refreshAssets")
                    && HasString(arguments, "avatarPath") && HasEmptyOutputPath(arguments)
                    && HasBoundedInteger(arguments, "maxItems", 1, 2000)
                    && HasFalseBoolean(arguments, "refreshAssets");
            }
            if (string.Equals(toolName, "vrc_scan_fx_animator", StringComparison.Ordinal))
            {
                return HasExactKeys(arguments, "avatarPath", "outputPath", "controllerPath", "refreshAssets")
                    && HasString(arguments, "avatarPath") && HasEmptyOutputPath(arguments)
                    && HasString(arguments, "controllerPath") && HasFalseBoolean(arguments, "refreshAssets");
            }
            if (string.Equals(toolName, "vrc_scan_animation_bindings", StringComparison.Ordinal))
            {
                return HasExactKeys(arguments, "avatarPath", "outputPath", "controllerPath", "clipPaths", "includeAllProjectClips", "includeBindingDetails", "maxClips", "refreshAssets")
                    && HasString(arguments, "avatarPath") && HasEmptyOutputPath(arguments)
                    && HasString(arguments, "controllerPath") && HasStringArray(arguments, "clipPaths")
                    && HasBoolean(arguments, "includeAllProjectClips")
                    && HasBoolean(arguments, "includeBindingDetails")
                    && HasBoundedInteger(arguments, "maxClips", 1, 2000)
                    && HasFalseBoolean(arguments, "refreshAssets");
            }
            if (string.Equals(toolName, "vrc_scan_avatar_performance", StringComparison.Ordinal))
            {
                return HasExactKeys(arguments, "avatarPath", "outputPath", "isMobile")
                    && HasString(arguments, "avatarPath") && HasEmptyOutputPath(arguments)
                    && HasBoolean(arguments, "isMobile");
            }
            return string.Equals(toolName, "vrc_capture_scene_view", StringComparison.Ordinal)
                && HasTrueBoolean(arguments, "statusOnly")
                && HasBoolean(arguments, "requirePlayMode")
                && (HasExactKeys(arguments, "statusOnly", "requirePlayMode")
                    || (HasExactKeys(arguments, "statusOnly", "requirePlayMode", "captureMode")
                        && HasCaptureMode(arguments))
                    || (HasExactKeys(
                            arguments,
                            "statusOnly",
                            "requirePlayMode",
                            "avatarPath",
                            "includeGestureManagerParameters",
                            "gestureManagerParameterNames",
                            "gestureManagerParameterPrefix")
                        && HasString(arguments, "avatarPath")
                        && HasBoolean(arguments, "includeGestureManagerParameters")
                        && HasStringArray(arguments, "gestureManagerParameterNames")
                        && ((JArray)arguments["gestureManagerParameterNames"]).Count <= 128
                        && HasString(arguments, "gestureManagerParameterPrefix")
                        && ((string)arguments["gestureManagerParameterPrefix"] ?? string.Empty).Length <= 256));
        }

        private static bool HasExactKeys(JObject arguments, params string[] names)
        {
            return arguments.Count == names.Length
                && names.All(name => arguments[name] != null);
        }

        private static bool HasString(JObject arguments, string name)
        {
            return arguments[name].Type == JTokenType.String;
        }

        private static bool HasCaptureMode(JObject arguments)
        {
            if (!HasString(arguments, "captureMode"))
            {
                return false;
            }
            var value = ((string)arguments["captureMode"] ?? string.Empty).Trim().ToLowerInvariant();
            return value == "auto" || value == "scene_view" || value == "game_view";
        }

        private static bool HasNonEmptyString(JObject arguments, string name)
        {
            return HasString(arguments, name) && !string.IsNullOrWhiteSpace((string)arguments[name]);
        }

        private static bool HasEmptyOutputPath(JObject arguments)
        {
            return HasString(arguments, "outputPath") && string.IsNullOrEmpty((string)arguments["outputPath"]);
        }

        private static bool HasBoolean(JObject arguments, string name)
        {
            var value = arguments == null ? null : arguments[name];
            return value != null && value.Type == JTokenType.Boolean;
        }

        private static bool HasTrueBoolean(JObject arguments, string name)
        {
            return HasBoolean(arguments, name) && arguments[name].Value<bool>();
        }

        private static bool HasFalseBoolean(JObject arguments, string name)
        {
            return HasBoolean(arguments, name) && !arguments[name].Value<bool>();
        }

        private static bool HasBoundedInteger(JObject arguments, string name, int minimum, int maximum)
        {
            var value = arguments[name];
            if (value.Type != JTokenType.Integer)
            {
                return false;
            }
            var integer = value.Value<long>();
            return integer >= minimum && integer <= maximum;
        }

        private static bool HasStringArray(JObject arguments, string name)
        {
            var values = arguments[name] as JArray;
            return values != null && values.Count <= 2000
                && values.All(item => item != null && item.Type == JTokenType.String);
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
            RequestInvocationDrain();
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

        private static bool HasAllowedPreviewRequest(string toolName, JObject arguments)
        {
            if (string.Equals(toolName, "vrc_restore_safe_backup", StringComparison.Ordinal))
            {
                return HasStrictRestoreBackupPreviewRequest(arguments);
            }
            if (string.Equals(toolName, "vrc_setup_outfit", StringComparison.Ordinal))
            {
                return HasStrictSetupOutfitPreviewRequest(arguments);
            }
            return PreviewTools.Contains(toolName) && HasExplicitPreviewRequest(arguments);
        }

        private static bool IsStrictSafetyControlRequest(string toolName, JObject arguments)
        {
            var isPrepareCheckpoint = string.Equals(toolName, "vrc_prepare_checkpoint", StringComparison.Ordinal);
            if (!(isPrepareCheckpoint
                    || string.Equals(toolName, "vrc_reload_after_checkpoint_restore", StringComparison.Ordinal))
                || arguments == null)
            {
                return false;
            }
            if (isPrepareCheckpoint && HasExactKeys(arguments, "projectPath"))
            {
                return HasNonEmptyString(arguments, "projectPath");
            }
            if (isPrepareCheckpoint && HasExactKeys(arguments,
                    "projectPath", "expectedRunIdDigest", "expectedProjectPathDigest",
                    "expectedUnityProcessId", "expectedUnityProcessStartedAtUtc", "expectedUnityExecutableDigest")
                && HasCompleteSafetyControlLiveBinding(arguments))
            {
                return true;
            }
            if (!string.Equals(toolName, "vrc_reload_after_checkpoint_restore", StringComparison.Ordinal)
                || !HasNonEmptyString(arguments, "projectPath")
                || !HasNonEmptyString(arguments, "phase"))
            {
                return false;
            }
            var phase = (string)arguments["phase"];
            if (string.Equals(phase, "prepare_restore", StringComparison.Ordinal))
            {
                return HasExactKeys(arguments, "projectPath", "phase")
                    || (HasExactKeys(arguments,
                            "projectPath", "phase", "expectedRunIdDigest", "expectedProjectPathDigest",
                            "expectedUnityProcessId", "expectedUnityProcessStartedAtUtc", "expectedUnityExecutableDigest")
                        && HasCompleteSafetyControlLiveBinding(arguments));
            }
            if (!string.Equals(phase, "reload", StringComparison.Ordinal)
                || !HasStringArray(arguments, "scenePaths")
                || !HasString(arguments, "activeScenePath")
                || !HasBoolean(arguments, "refreshAssets"))
            {
                return false;
            }
            return HasExactKeys(arguments, "projectPath", "phase", "scenePaths", "activeScenePath", "refreshAssets")
                || (HasExactKeys(arguments,
                        "projectPath", "phase", "scenePaths", "activeScenePath", "refreshAssets",
                        "expectedRunIdDigest", "expectedProjectPathDigest", "expectedUnityProcessId",
                        "expectedUnityProcessStartedAtUtc", "expectedUnityExecutableDigest")
                    && HasCompleteSafetyControlLiveBinding(arguments));
        }

        private static bool HasCompleteSafetyControlLiveBinding(JObject arguments)
        {
            return HasNonEmptyString(arguments, "projectPath")
                && HasNonEmptyString(arguments, "expectedRunIdDigest")
                && HasNonEmptyString(arguments, "expectedProjectPathDigest")
                && HasBoundedInteger(arguments, "expectedUnityProcessId", 1, int.MaxValue)
                && HasNonEmptyString(arguments, "expectedUnityProcessStartedAtUtc")
                && HasNonEmptyString(arguments, "expectedUnityExecutableDigest");
        }

        private static bool HasStrictRestoreBackupPreviewRequest(JObject arguments)
        {
            if (arguments == null
                || !(HasExactKeys(arguments,
                        "backupPath", "backupId", "assetPaths", "confirmRestore",
                        "allowProjectMismatch", "allowOverwriteChanged", "refreshAssets")
                    || HasExactKeys(arguments,
                        "backupPath", "backupId", "assetPaths", "confirmRestore",
                        "allowProjectMismatch", "allowOverwriteChanged", "refreshAssets", "backupRoot")))
            {
                return false;
            }
            return HasString(arguments, "backupPath")
                && HasString(arguments, "backupId")
                && HasStringArray(arguments, "assetPaths")
                && ((JArray)arguments["assetPaths"]).Count <= 2000
                && HasFalseBoolean(arguments, "confirmRestore")
                && HasFalseBoolean(arguments, "allowProjectMismatch")
                && HasFalseBoolean(arguments, "allowOverwriteChanged")
                && HasFalseBoolean(arguments, "refreshAssets")
                && (arguments["backupRoot"] == null || HasString(arguments, "backupRoot"));
        }

        private static bool HasStrictSetupOutfitPreviewRequest(JObject arguments)
        {
            return arguments != null
                && HasExactKeys(arguments, "avatarPath", "outfitPath", "confirmSetup", "saveScene")
                && HasString(arguments, "avatarPath")
                && HasNonEmptyString(arguments, "outfitPath")
                && HasFalseBoolean(arguments, "confirmSetup")
                && arguments["saveScene"] != null
                && arguments["saveScene"].Type == JTokenType.Boolean;
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
                    pending.Response = ToolError(
                        "invocation_revalidation_failed",
                        "VRCForge tool authorization changed before execution.",
                        pending.Modern,
                        true);
                    return;
                }
                var result = descriptor.Handler.Invoke(null, new object[] { pending.Arguments });
                pending.Response = ToolResult(result, pending.Modern);
            }
            catch (Exception exception)
            {
                pending.Response = ToolError(
                    "tool_handler_exception",
                    "VRCForge tool execution failed in the Unity tool handler.",
                    pending.Modern,
                    false,
                    exception);
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
                return descriptor.Permission == VRCForgeCommandAccess.ReadOnly
                    || IsStrictNoWritePayloadRead(pending.ToolName, pending.Arguments);
            }
            if (descriptor.Permission == VRCForgeCommandAccess.ReadOnly
                || !ReverifyManagedPeer(pending))
            {
                return false;
            }
            if (pending.Lane == InvocationLane.AppPreview)
            {
                return HasAllowedPreviewRequest(pending.ToolName, pending.Arguments);
            }
            if (pending.Lane == InvocationLane.AppSafetyControl)
            {
                return IsStrictSafetyControlRequest(pending.ToolName, pending.Arguments);
            }
            if (pending.Lane == InvocationLane.AppSetupOutfitPoll)
            {
                return IsStrictSetupOutfitJobPoll(pending.ToolName, pending.Arguments)
                    && ValidateManagedAppInstanceContext(pending.ExecutionContext);
            }
            if (pending.Lane == InvocationLane.AppUnityPackageImportPoll)
            {
                return IsStrictUnityPackageImportJobPoll(pending.ToolName, pending.Arguments)
                    && ValidateManagedAppInstanceContext(pending.ExecutionContext);
            }
            if (pending.Lane == InvocationLane.AppBuildTestPoll)
            {
                return IsStrictBuildTestJobPoll(pending.ToolName, pending.Arguments)
                    && ValidateManagedAppInstanceContext(pending.ExecutionContext);
            }
            if (pending.Lane == InvocationLane.AppAvatarUploadPoll)
            {
                return IsStrictAvatarUploadJobPoll(pending.ToolName, pending.Arguments)
                    && ValidateManagedAppInstanceContext(pending.ExecutionContext);
            }
            var validManagedWrite = pending.Lane == InvocationLane.ApprovedWrite
                ? ValidateApprovedExecutionContext(
                    pending.ExecutionContext,
                    pending.ToolName,
                    pending.Arguments)
                : pending.Lane == InvocationLane.ExternalMcpWrite
                    && ValidateExternalMcpExecutionContext(
                        pending.ExecutionContext,
                        pending.ToolName,
                        pending.Arguments);
            return validManagedWrite
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

        private static bool IsStrictUnityPackageImportJobPoll(string toolName, JObject arguments)
        {
            if (!string.Equals(toolName, "vrc_import_unitypackage", StringComparison.Ordinal)
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

        private static bool IsStrictBuildTestJobPoll(string toolName, JObject arguments)
        {
            if (!string.Equals(toolName, "vrc_build_test_avatar", StringComparison.Ordinal)
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

        private static bool IsStrictAvatarUploadJobPoll(string toolName, JObject arguments)
        {
            if (!string.Equals(toolName, "vrc_build_and_upload_avatar", StringComparison.Ordinal)
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
                var projectId = RequiredBoundedString(context, "projectId", 64);
                var instanceId = RequiredBoundedString(context, "instanceId", 128);
                return IsLowerHex(projectId, 64)
                    && string.Equals(projectId, ComputeProjectId(GetProjectRoot()), StringComparison.Ordinal)
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
            string ignoredFailureCode;
            return ValidateManagedWriteExecutionContext(
                context,
                toolName,
                arguments,
                externalMcp: false,
                failureCode: out ignoredFailureCode);
        }

        private static bool ValidateExternalMcpExecutionContext(
            JObject context,
            string toolName,
            JObject arguments)
        {
            string ignoredFailureCode;
            return ValidateManagedWriteExecutionContext(
                context,
                toolName,
                arguments,
                externalMcp: true,
                failureCode: out ignoredFailureCode);
        }

        private static bool ValidateExternalMcpExecutionContext(
            JObject context,
            string toolName,
            JObject arguments,
            out string failureCode)
        {
            return ValidateManagedWriteExecutionContext(
                context,
                toolName,
                arguments,
                externalMcp: true,
                failureCode: out failureCode);
        }

        private static bool ValidateManagedWriteExecutionContext(
            JObject context,
            string toolName,
            JObject arguments,
            bool externalMcp,
            out string failureCode)
        {
            failureCode = "context_invalid";
            if (context == null || arguments == null)
            {
                failureCode = "context_missing";
                return false;
            }
            try
            {
                var executionId = RequiredBoundedString(context, "executionId", 128);
                var operationId = externalMcp
                    ? RequiredBoundedString(context, "operationId", 256)
                    : null;
                var approvalId = externalMcp
                    ? null
                    : RequiredBoundedString(context, "approvalId", 256);
                var checkpointId = externalMcp
                    ? null
                    : RequiredBoundedString(context, "checkpointId", 256);
                var targetTool = RequiredBoundedString(context, "targetTool", 128);
                var boundUnityTool = RequiredBoundedString(context, "unityToolName", 128);
                var argumentsSha256 = RequiredBoundedString(context, "argumentsSha256", 64);
                var projectId = RequiredBoundedString(context, "projectId", 64);
                var instanceId = RequiredBoundedString(context, "instanceId", 128);
                var issuedAt = context["issuedAtUnixMs"].Value<long>();
                var expiresAt = context["expiresAtUnixMs"].Value<long>();
                var now = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds();
                if (string.IsNullOrEmpty(executionId)
                    || (externalMcp && string.IsNullOrEmpty(operationId))
                    || (!externalMcp && string.IsNullOrEmpty(approvalId))
                    || (!externalMcp && string.IsNullOrEmpty(checkpointId)))
                {
                    failureCode = "identity_invalid";
                    return false;
                }
                if (externalMcp && (context["approvalId"] != null || context["checkpointId"] != null))
                {
                    failureCode = "internal_fields_present";
                    return false;
                }
                if (!ApprovedAppCoreTools.Contains(targetTool))
                {
                    failureCode = "tool_not_approved";
                    return false;
                }
                if (!string.Equals(targetTool, toolName, StringComparison.Ordinal)
                    || !string.Equals(boundUnityTool, toolName, StringComparison.Ordinal))
                {
                    failureCode = "tool_mismatch";
                    return false;
                }
                if (!IsLowerHex(argumentsSha256, 64))
                {
                    failureCode = "arguments_hash_invalid";
                    return false;
                }
                if (!string.Equals(argumentsSha256, ComputeCanonicalJsonHash(arguments), StringComparison.Ordinal))
                {
                    failureCode = "arguments_mismatch";
                    return false;
                }
                if (!string.Equals(projectId, ComputeProjectId(GetProjectRoot()), StringComparison.Ordinal))
                {
                    failureCode = "project_mismatch";
                    return false;
                }
                if (!string.Equals(instanceId, descriptorInstanceId, StringComparison.Ordinal))
                {
                    failureCode = "instance_mismatch";
                    return false;
                }
                if (expiresAt <= issuedAt
                    || expiresAt - issuedAt > ApprovedExecutionMaxLifetimeMilliseconds)
                {
                    failureCode = "lifetime_invalid";
                    return false;
                }
                if (now < issuedAt - ApprovedExecutionClockSkewMilliseconds)
                {
                    failureCode = "not_yet_valid";
                    return false;
                }
                if (now >= expiresAt)
                {
                    failureCode = "expired";
                    return false;
                }
                lock (Gate)
                {
                    PurgeExpiredExecutionIds(now);
                    if (ConsumedExecutionExpirations.ContainsKey(executionId))
                    {
                        failureCode = "replayed";
                        return false;
                    }
                }
                failureCode = string.Empty;
                return true;
            }
            catch (Exception)
            {
                failureCode = "context_invalid";
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
            var canonical = new StringBuilder();
            AppendCanonicalArgumentToken(canonical, value);
            using (var sha = SHA256.Create())
            {
                var digest = sha.ComputeHash(new UTF8Encoding(false, true).GetBytes(canonical.ToString()));
                var builder = new StringBuilder(digest.Length * 2);
                foreach (var item in digest)
                {
                    builder.Append(item.ToString("x2"));
                }
                return builder.ToString();
            }
        }

        private static void AppendCanonicalArgumentToken(StringBuilder builder, JToken value)
        {
            if (value == null || value.Type == JTokenType.Null)
            {
                builder.Append("n;");
                return;
            }
            if (value.Type == JTokenType.Boolean)
            {
                builder.Append(value.Value<bool>() ? "b1;" : "b0;");
                return;
            }
            if (value.Type == JTokenType.Integer)
            {
                builder.Append('i');
                builder.Append(Convert.ToString(((JValue)value).Value, CultureInfo.InvariantCulture));
                builder.Append(';');
                return;
            }
            if (value.Type == JTokenType.Float)
            {
                var number = value.Value<double>();
                if (double.IsNaN(number) || double.IsInfinity(number))
                {
                    throw new InvalidOperationException("Managed execution arguments contain a non-finite number.");
                }
                var bits = unchecked((ulong)BitConverter.DoubleToInt64Bits(number));
                builder.Append('f');
                builder.Append(bits.ToString("x16", CultureInfo.InvariantCulture));
                builder.Append(';');
                return;
            }
            if (value.Type == JTokenType.String)
            {
                builder.Append('s');
                builder.Append(Convert.ToBase64String(new UTF8Encoding(false, true).GetBytes(value.Value<string>())));
                builder.Append(';');
                return;
            }
            var arrayValue = value as JArray;
            if (arrayValue != null)
            {
                builder.Append("a[");
                foreach (var item in arrayValue)
                {
                    AppendCanonicalArgumentToken(builder, item);
                }
                builder.Append("];");
                return;
            }
            var objectValue = value as JObject;
            if (objectValue != null)
            {
                builder.Append("o{");
                var properties = new List<JProperty>(objectValue.Properties());
                properties.Sort((left, right) => string.CompareOrdinal(
                    Convert.ToBase64String(new UTF8Encoding(false, true).GetBytes(left.Name)),
                    Convert.ToBase64String(new UTF8Encoding(false, true).GetBytes(right.Name))));
                foreach (var property in properties)
                {
                    AppendCanonicalArgumentToken(builder, new JValue(property.Name));
                    AppendCanonicalArgumentToken(builder, property.Value);
                }
                builder.Append("};");
                return;
            }
            throw new InvalidOperationException("Managed execution arguments contain an unsupported JSON token.");
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
            var commandResult = value as VRCForgeToolResult;
            var tokenValue = commandResult != null
                ? commandResult.ToStructuredContent()
                : value == null ? JValue.CreateNull() : JToken.FromObject(value);
            var isError = commandResult != null && !commandResult.IsSuccessful;
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

        private static JObject ToolError(
            string code,
            string message,
            bool modern,
            bool noWriteProven = false,
            Exception exception = null)
        {
            var structured = new JObject
            {
                ["success"] = false,
                ["code"] = code,
                ["errorCode"] = code,
                ["error"] = message,
                ["failureLayer"] = noWriteProven ? "unity_core_pre_route" : "unity_tool_handler",
                ["failurePhase"] = noWriteProven ? "before_tool_routing" : "tool_handler_exception",
                ["toolRoutingStarted"] = !noWriteProven,
                ["mutationStarted"] = noWriteProven ? new JValue(false) : JValue.CreateNull(),
                ["committed"] = noWriteProven ? new JValue(false) : JValue.CreateNull(),
                ["commitState"] = noWriteProven ? "not_started" : "unknown",
                ["requestMayHaveCommitted"] = !noWriteProven,
                ["checkpointRecoveryRequired"] = false,
                ["temporaryCleanupRequired"] = false,
            };
            if (exception != null)
            {
                // Keep the handler's exact failure facts available to both
                // internal and external agents.  The bounded chain is enough
                // to diagnose reflection/TargetInvocationException wrappers
                // without returning an unbounded Unity stack trace.
                var chain = new JArray();
                var current = exception;
                var depth = 0;
                while (current != null && depth++ < 6)
                {
                    chain.Add(new JObject
                    {
                        ["type"] = current.GetType().FullName ?? current.GetType().Name,
                        ["message"] = (current.Message ?? string.Empty).Substring(0, Math.Min(800, (current.Message ?? string.Empty).Length)),
                    });
                    current = current.InnerException;
                }
                structured["handlerException"] = new JObject
                {
                    ["type"] = exception.GetType().FullName ?? exception.GetType().Name,
                    ["message"] = (exception.Message ?? string.Empty).Substring(0, Math.Min(800, (exception.Message ?? string.Empty).Length)),
                    ["innerChain"] = chain,
                };
                structured["diagnostics"] = new JObject
                {
                    ["schema"] = "vrcforge.unity_tool_handler_diagnostics.v1",
                    ["handlerException"] = structured["handlerException"].DeepClone(),
                };
                structured["failureCause"] = new JObject
                {
                    ["code"] = code,
                    ["message"] = (exception.Message ?? message).Substring(0, Math.Min(800, (exception.Message ?? message).Length)),
                    ["failureLayer"] = "unity_tool_handler",
                    ["failurePhase"] = "tool_handler_exception",
                };
                structured["rootCause"] = structured["failureCause"].DeepClone();
                structured["failedStep"] = "unity_tool_handler";
            }
            var result = new JObject
            {
                ["content"] = new JArray
                {
                    new JObject { ["type"] = "text", ["text"] = message },
                },
                ["structuredContent"] = structured,
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
                ["protocolRange"] = ProtocolRangeResult(),
                ["coreIdentity"] = VRCForgeMcpToolContract.CoreIdentity,
                ["handshakeProtocol"] = VRCForgeMcpToolContract.HandshakeProtocol,
                ["productVersion"] = VRCForgeMcpToolContract.ProductVersion,
                ["toolContractVersion"] = VRCForgeMcpToolContract.ToolContractVersion,
                ["instanceId"] = descriptorInstanceId,
                ["projectId"] = ComputeProjectId(GetProjectRoot()),
                ["capabilities"] = new JObject
                {
                    ["tools"] = new JObject { ["listChanged"] = false },
                    ["resources"] = new JObject { ["listChanged"] = false },
                    ["prompts"] = new JObject { ["listChanged"] = false },
                },
                ["instructions"] = "Read tools are direct. Other tools require a version-negotiated managed VRCForge App lane.",
                ["ttlMs"] = 3000,
                ["cacheScope"] = "private",
            };
        }

        private static JObject ProtocolRangeResult()
        {
            return new JObject
            {
                ["minimum"] = MinimumProtocolVersion,
                ["maximum"] = MaximumProtocolVersion,
            };
        }

        private static JObject CoreInfoResult()
        {
            return new JObject
            {
                ["schema"] = "vrcforge.core_info.v1",
                ["coreIdentity"] = VRCForgeMcpToolContract.CoreIdentity,
                ["coreVersion"] = VRCForgeMcpToolContract.ProductVersion,
                ["versionSource"] = "compiled_constant",
                ["protocolRange"] = ProtocolRangeResult(),
                ["toolContractVersion"] = VRCForgeMcpToolContract.ToolContractVersion,
                ["toolCount"] = VRCForgeMcpToolContract.ToolCount,
                ["instanceId"] = descriptorInstanceId,
                ["projectId"] = ComputeProjectId(GetProjectRoot()),
                ["projectIdSource"] = "normalized_project_path_sha256",
                ["compileSnapshot"] = CompileErrorMonitor.ReadCoreInfoSnapshot(30),
            };
        }

        private static JObject ToolsListResult(bool modern, string exposureLayer)
        {
            var result = new JArray();
            var orderedTools = new List<VRCForgeToolDescriptor>(tools);
            orderedTools.Sort((left, right) => string.CompareOrdinal(left.Name, right.Name));
            foreach (var descriptor in orderedTools)
            {
                if (string.Equals(exposureLayer, "planning", StringComparison.Ordinal)
                    && !VRCForgeMcpToolContract.ExpectedPlanningToolNames.Contains(descriptor.Name))
                {
                    continue;
                }
                var annotations = new JObject();
                if (descriptor.Permission == VRCForgeCommandAccess.ReadOnly)
                {
                    annotations["readOnlyHint"] = true;
                }
                else
                {
                    annotations["destructiveHint"] = true;
                }
                var writeTool = descriptor.Permission != VRCForgeCommandAccess.ReadOnly;
                var planningCapable = VRCForgeMcpToolContract.ExpectedPlanningToolNames.Contains(descriptor.Name);
                var whenToUse = descriptor.Description;
                var whenNotToUse = writeTool && planningCapable
                    ? "During planning, do not request an output path or any project mutation; output-producing variants require a managed execution lane."
                    : writeTool
                    ? "Do not use while planning, for hypothetical or quoted requests, or without an explicit project change request and a managed execution lane."
                    : "Do not use for general questions, quoted examples, hypothetical requests, or when the user explicitly forbids project inspection.";
                var negativeExample = writeTool && planningCapable
                    ? "Negative example: Run " + descriptor.Name + " during planning and save its report into Assets."
                    : writeTool
                    ? "Negative example: Explain " + descriptor.Name + " conceptually, but do not modify the Unity project."
                    : "Negative example: Mention " + descriptor.Name + " without inspecting the current Unity project.";
                var description = "When to use: " + whenToUse + "\nWhen NOT to use: "
                    + whenNotToUse + "\n" + negativeExample;
                var metadata = new JObject
                {
                    ["permission"] = descriptor.Permission.ToString(),
                    ["group"] = descriptor.Group,
                    ["whenToUse"] = whenToUse,
                    ["doNotUse"] = whenNotToUse + " " + negativeExample,
                    ["negativeExample"] = negativeExample.Substring("Negative example: ".Length),
                    ["exposureLayer"] = planningCapable
                        ? "planning" : "execution",
                    ["poll"] = descriptor.RequiresPolling
                        ? new JObject { ["action"] = descriptor.PollAction, ["maxSeconds"] = descriptor.MaxPollSeconds }
                        : JValue.CreateNull()
                };
                result.Add(new JObject
                {
                    ["name"] = descriptor.Name,
                    ["description"] = description,
                    ["inputSchema"] = descriptor.CreateInputSchema(),
                    ["annotations"] = annotations,
                    ["_meta"] = metadata
                });
            }
            var response = new JObject { ["tools"] = result, ["exposureLayer"] = exposureLayer };
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
                    ["version"] = VRCForgeMcpToolContract.ProductVersion,
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
                ["minimumProtocolVersion"] = MinimumProtocolVersion,
                ["maximumProtocolVersion"] = MaximumProtocolVersion,
                ["coreIdentity"] = VRCForgeMcpToolContract.CoreIdentity,
                ["handshakeProtocol"] = VRCForgeMcpToolContract.HandshakeProtocol,
                ["productVersion"] = VRCForgeMcpToolContract.ProductVersion,
                ["toolContractVersion"] = VRCForgeMcpToolContract.ToolContractVersion,
                ["host"] = IPAddress.Loopback.ToString(),
                ["port"] = endpoint.Port,
                ["authMode"] = "bearer-per-request",
                ["authToken"] = Convert.ToBase64String(token),
                ["instanceId"] = descriptorInstanceId,
                ["processId"] = System.Diagnostics.Process.GetCurrentProcess().Id,
                ["projectPath"] = root,
                ["projectId"] = ComputeProjectId(root),
                ["projectIdSource"] = "normalized_project_path_sha256",
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

        private static string ComputeProjectId(string root)
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
