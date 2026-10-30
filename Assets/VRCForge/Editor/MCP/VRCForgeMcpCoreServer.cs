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
        private const int MaxResponseFrameBytes = 8 * 1024 * 1024;
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
        private static readonly ConcurrentQueue<PendingInvocation> PendingMcpWriteExecutions = new ConcurrentQueue<PendingInvocation>();
        private static readonly ConcurrentDictionary<string, PendingInvocation> McpWriteSubmissions = new ConcurrentDictionary<string, PendingInvocation>(StringComparer.Ordinal);
        private static readonly Dictionary<string, long> ConsumedExecutionExpirations =
            new Dictionary<string, long>(StringComparer.Ordinal);
        private static readonly Queue<KeyValuePair<string, long>> ConsumedExecutionOrder =
            new Queue<KeyValuePair<string, long>>();
        private static readonly HashSet<string> PreviewTools = new HashSet<string>(StringComparer.Ordinal)
        {
            "vrc_set_material_shader",
            "vrc_flatten_material_variant",
            "vrc_set_material_texture",
            "vrc_set_renderer_material_slot",
            "vrc_duplicate_scene_object",
            "vrc_duplicate_project_asset",
            "vrc_relocate_generated_assets",
            "vrc_duplicate_scene_asset",
            "vrc_save_scene_object_as_prefab",
            "vrc_save_current_scene",
            "vrc_save_new_scene",
            "vrc_scene_save",
            "vrc_save_asset",
            "vrc_scene_transition",
            "vrc_texture_patch",
            "vrc_user_adjustment_handoff",
            "vrc_set_texture_import_settings",
            "vrc_set_constraint_sources",
            "vrc_create_component_feature",
            "vrc_configure_aao_merge_physbone",
            "vrc_revert_removed_component",
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
            "vrc_unpack_prefab",
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
            public bool AsyncWrite;
            public string ExecutionId;
            public string JobId;
            public long QueueEditorTick;
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
        private static string descriptorProjectId;
        private static VRCForgeToolDescriptor[] tools = new VRCForgeToolDescriptor[0];
        private static VRCForgeExcludedToolDescriptor[] excludedExternalTools = new VRCForgeExcludedToolDescriptor[0];
        private static SynchronizationContext editorSynchronizationContext;
        private static long editorUpdateTick;

        internal static string CurrentInstanceId
        {
            get { return descriptorInstanceId ?? string.Empty; }
        }

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
            EditorApplication.update -= OnEditorUpdate;
            EditorApplication.update += OnEditorUpdate;
        }

        private static void OnEditorUpdate()
        {
            editorUpdateTick++;
            DrainInvocations();
            DrainMcpWriteExecutions();
        }

        internal static void ScheduleInvocationPumpRegistration()
        {
            EditorApplication.delayCall -= EnsureInvocationPumpRegistered;
            EditorApplication.delayCall += EnsureInvocationPumpRegistered;
        }

        internal static bool TryPostToEditor(Action callback)
        {
            if (callback == null)
            {
                return false;
            }
            var context = editorSynchronizationContext;
            if (context == null)
            {
                return false;
            }
            try
            {
                context.Post(_ => callback(), null);
                return true;
            }
            catch (InvalidOperationException)
            {
                return false;
            }
        }

        /// <summary>
        /// Execute a Unity-API-dependent function on the editor main thread and
        /// block the calling (TCP service) thread for the bounded result.
        /// Unity scene APIs throw off the main thread, so external-write
        /// identity validation routes its live assertions through this bridge.
        /// </summary>
        private static T RunOnMainThread<T>(Func<T> func)
        {
            // Re-entrancy guard: the drain itself runs on the main thread, so
            // validations invoked from inside a drained invocation must execute
            // inline instead of posting to the queue they are blocking.
            if (SynchronizationContext.Current == editorSynchronizationContext
                && editorSynchronizationContext != null)
            {
                return func();
            }
            var completion = new System.Threading.Tasks.TaskCompletionSource<T>(System.Threading.Tasks.TaskCreationOptions.RunContinuationsAsynchronously);
            if (!TryPostToEditor(() =>
            {
                try
                {
                    completion.SetResult(func());
                }
                catch (System.Exception ex)
                {
                    completion.SetException(ex);
                }
            }))
            {
                throw new UnityException("The editor main thread is unavailable for Unity API validation.");
            }
            if (!completion.Task.Wait(TimeSpan.FromSeconds(15)))
            {
                throw new UnityException("The editor main thread did not complete Unity API validation in time.");
            }
            return completion.Task.Result;
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
            descriptorProjectId = null;
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
            var registry = VRCForgeToolRegistry.DiscoverOwnedLoadedAssemblies(
                VRCForgeMcpToolContract.IsExpectedDeclaration);
            excludedExternalTools = registry.ExcludedTools.ToArray();
            return VRCForgeMcpToolContract.SnapshotExact(registry.Tools);
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
            if (string.Equals(method, "server/write-operation", StringComparison.Ordinal))
            {
                return hasId ? Result(id, ReadMcpWriteOperation(parameters), true) : null;
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
                || !string.Equals((string)projectBinding["projectId"], descriptorProjectId, StringComparison.Ordinal)
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

            var asyncWrite = modern
                && metadata != null
                && metadata["io.vrcforge/asyncWrite"] != null
                && metadata["io.vrcforge/asyncWrite"].Type == JTokenType.Boolean
                && metadata["io.vrcforge/asyncWrite"].Value<bool>()
                && (lane == InvocationLane.ApprovedWrite || lane == InvocationLane.ExternalMcpWrite);
            return QueueInvocation(toolName, arguments, executionContext, lane, client, peerEvidence, modern, asyncWrite);
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
                var basicRead = HasExactKeys(arguments, "avatarPath", "outputPath", "refreshAssets")
                    || (HasExactKeys(arguments, "avatarPath", "outputPath", "refreshAssets", "includeTextures")
                        && HasBoolean(arguments, "includeTextures"));
                var boundedRead = HasExactKeys(
                    arguments,
                    "avatarPath",
                    "outputPath",
                    "refreshAssets",
                    "materialIds",
                    "includeTextures")
                    && HasStringArray(arguments, "materialIds")
                    && HasBoolean(arguments, "includeTextures");
                return (basicRead || boundedRead)
                    && HasString(arguments, "avatarPath") && HasEmptyOutputPath(arguments)
                    && HasFalseBoolean(arguments, "refreshAssets");
            }
            if (string.Equals(toolName, "vrc_scan_avatar_items", StringComparison.Ordinal))
            {
                var baseRead = HasExactKeys(arguments, "avatarPath", "outputPath", "maxItems", "refreshAssets")
                    || HasExactKeys(arguments, "avatarPath", "outputPath", "maxItems", "refreshAssets", "offset")
                    || HasExactKeys(arguments, "avatarPath", "outputPath", "maxItems", "refreshAssets", "expectedSnapshotDigest")
                    || HasExactKeys(arguments, "avatarPath", "outputPath", "maxItems", "refreshAssets", "offset", "expectedSnapshotDigest");
                return baseRead
                    && HasString(arguments, "avatarPath") && HasEmptyOutputPath(arguments)
                    && HasBoundedInteger(arguments, "maxItems", 1, 2000)
                    && (arguments["offset"] == null || HasBoundedInteger(arguments, "offset", 0, int.MaxValue))
                    && (arguments["expectedSnapshotDigest"] == null
                        || (HasString(arguments, "expectedSnapshotDigest")
                            && arguments["expectedSnapshotDigest"].Value<string>().Length == 64
                            && arguments["expectedSnapshotDigest"].Value<string>().All(Uri.IsHexDigit)))
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
                if (VRCForge.Editor.AnimationBindingReadSelection.Requested(arguments))
                {
                    if (!VRCForge.Editor.AnimationBindingReadSelection.ValidReadArguments(arguments)) return false;
                    arguments = (JObject)arguments.DeepClone();
                    foreach (var field in VRCForge.Editor.AnimationBindingReadSelection.Fields) arguments.Remove(field);
                }
                return (HasExactKeys(arguments, "avatarPath", "outputPath", "controllerPath", "clipPaths", "includeAllProjectClips", "includeBindingDetails", "maxClips", "refreshAssets")
                    || (HasExactKeys(arguments, "avatarPath", "outputPath", "controllerPath", "clipPaths", "includeAllProjectClips", "includeBindingDetails", "maxClips", "maxKeysPerBinding", "refreshAssets")
                        && HasBoundedInteger(arguments, "maxKeysPerBinding", 1, 2000)))
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
            bool modern,
            bool asyncWrite = false)
        {
            if (asyncWrite)
            {
                var executionId = (string)executionContext["executionId"];
                if (string.IsNullOrWhiteSpace(executionId))
                    return ToolError("async_execution_id_missing", "An async managed write requires its exact executionId.", modern, true);
                var cached = UnityAsyncJobRegistry.ReadMcpWriteSnapshot(executionId);
                if (cached != null) return AsyncWriteSubmissionResponse(cached);
                var candidate = new PendingInvocation
                {
                    ToolName = toolName,
                    Arguments = (JObject)arguments.DeepClone(),
                    ExecutionContext = (JObject)executionContext.DeepClone(),
                    Lane = lane,
                    Client = client,
                    PeerEvidence = peerEvidence,
                    Modern = modern,
                    AsyncWrite = true,
                    ExecutionId = executionId,
                };
                var pendingAsync = McpWriteSubmissions.GetOrAdd(executionId, candidate);
                if (ReferenceEquals(candidate, pendingAsync))
                {
                    PendingInvocations.Enqueue(pendingAsync);
                    RequestInvocationDrain();
                }
                var asyncDeadline = DateTime.UtcNow.AddMilliseconds(InvocationQueueTimeoutMilliseconds);
                while (!pendingAsync.Completion.Wait(100))
                {
                    if (stopping || DateTime.UtcNow >= asyncDeadline)
                        return AsyncWriteUnknownResponse(executionId, pendingAsync.JobId);
                }
                if (pendingAsync.Response?["schema"] == null)
                    McpWriteSubmissions.TryRemove(executionId, out _);
                return pendingAsync.Response ?? AsyncWriteUnknownResponse(executionId, pendingAsync.JobId);
            }

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
            var hasRecoveryEvidenceOnly = isPrepareCheckpoint && arguments.Property("recoveryEvidenceOnly") != null;
            var recoveryEvidenceOnly = false;
            if (hasRecoveryEvidenceOnly)
            {
                if (!HasBoolean(arguments, "recoveryEvidenceOnly")) return false;
                recoveryEvidenceOnly = arguments["recoveryEvidenceOnly"].Value<bool>();
            }
            var phaseName = arguments["phase"]?.Type == JTokenType.String ? arguments["phase"].Value<string>() : null;
            var scopeField = isPrepareCheckpoint || phaseName == "prepare_restore" ? "checkpointFiles"
                : phaseName == "reload" ? "restoredAssetPaths" : null;
            if (scopeField != null && arguments.Property(scopeField) != null)
            {
                if (recoveryEvidenceOnly && (scopeField != "checkpointFiles" || arguments[scopeField] == null))
                {
                    return false;
                }
                try
                {
                    CheckpointPrepareTool.ExactFileScope(arguments[scopeField], allowEmpty: scopeField == "restoredAssetPaths");
                }
                catch (InvalidOperationException)
                {
                    return false;
                }
                arguments = (JObject)arguments.DeepClone();
                arguments.Remove(scopeField);
            }
            else if (recoveryEvidenceOnly)
            {
                return false;
            }
            if (hasRecoveryEvidenceOnly)
            {
                arguments = (JObject)arguments.DeepClone();
                arguments.Remove("recoveryEvidenceOnly");
            }
            var materialBaselineOnly = isPrepareCheckpoint && arguments.Property("materialBaselineOnly") != null;
            if (materialBaselineOnly)
            {
                if (!HasTrueBoolean(arguments, "materialBaselineOnly")
                    || !HasStringArray(arguments, "checkpointAssetPaths")
                    || ((JArray)arguments["checkpointAssetPaths"]).Count == 0
                    || ((JArray)arguments["checkpointAssetPaths"]).Any(path =>
                        !path.Value<string>().EndsWith(".mat", StringComparison.OrdinalIgnoreCase))) return false;
                arguments = (JObject)arguments.DeepClone();
                arguments.Remove("materialBaselineOnly");
            }
            if (isPrepareCheckpoint && arguments.Property("checkpointAssetPaths") != null)
            {
                if (!HasStringArray(arguments, "checkpointAssetPaths")
                    || ((JArray)arguments["checkpointAssetPaths"]).Count > (materialBaselineOnly ? 128 : 32)) return false;
                arguments = (JObject)arguments.DeepClone();
                arguments.Remove("checkpointAssetPaths");
            }
            if (!isPrepareCheckpoint && arguments.Property("assetBaseline") != null)
            {
                if (!(arguments["assetBaseline"] is JArray baseline) || baseline.Count > 128
                    || arguments["phase"]?.Value<string>() != "reload") return false;
                foreach (var item in baseline)
                {
                    if (!(item is JObject row)
                        || !HasNonEmptyString(row, "assetPath") || !HasNonEmptyString(row, "assetGuid")) return false;
                    if (row["assetPath"].Value<string>().EndsWith(".mat", StringComparison.OrdinalIgnoreCase))
                    {
                        if (!HasExactKeys(row, "assetPath", "assetGuid", "materialStateDigest")
                            || !HasNonEmptyString(row, "materialStateDigest")) return false;
                        var digest = row["materialStateDigest"].Value<string>();
                        if (digest.Length != 64 || digest.Any(c => !Uri.IsHexDigit(c))) return false;
                    }
                    else if (baseline.Count > 32
                        || !HasExactKeys(row, "assetPath", "assetGuid", "serializedState")
                        || !(row["serializedState"] is JObject)) return false;
                }
                arguments = (JObject)arguments.DeepClone();
                arguments.Remove("assetBaseline");
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
                        "VRCForge tool authorization changed before execution. reason=" + LastPeerReverifyFailure,
                        pending.Modern,
                        true);
                    return;
                }
                if (pending.AsyncWrite)
                {
                    QueueMcpWriteExecution(pending);
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

        private static void QueueMcpWriteExecution(PendingInvocation pending)
        {
            var operation = UnityAsyncJobRegistry.CreateMcpWrite(
                pending.ToolName,
                pending.ExecutionId,
                GetProjectRoot());
            pending.JobId = (string)operation["jobId"];
            pending.Response = AsyncWriteSubmissionResponse(operation);
            pending.QueueEditorTick = editorUpdateTick;
            PendingMcpWriteExecutions.Enqueue(pending);
        }

        private static void DrainMcpWriteExecutions()
        {
            if (!PendingMcpWriteExecutions.TryPeek(out var next) || next.QueueEditorTick >= editorUpdateTick) return;
            if (!PendingMcpWriteExecutions.TryDequeue(out var pending)) return;
            JObject result;
            var handlerStarted = false;
            try
            {
                var descriptor = FindTool(pending.ToolName);
                if (!IsMcpWriteAuthorizedAtExecution(pending, descriptor))
                {
                    result = ToolError("async_execution_revalidation_failed", "The queued Unity write no longer matches its approved execution context; the tool handler did not start.", true, true);
                    UnityAsyncJobRegistry.CompleteMcpWrite(pending.JobId, result);
                    McpWriteSubmissions.TryRemove(pending.ExecutionId, out _);
                    return;
                }
                UnityAsyncJobRegistry.MarkRunning(pending.JobId);
                handlerStarted = true;
                var value = descriptor.Handler.Invoke(null, new object[] { pending.Arguments });
                result = ToolResult(value, true);
            }
            catch (Exception exception)
            {
                result = handlerStarted
                    ? ToolError("tool_handler_exception", "VRCForge tool execution failed in the Unity tool handler.", true, false, exception)
                    : ToolError("async_job_start_failed", "The queued Unity tool handler did not start.", true, true, exception);
            }
            try
            {
                UnityAsyncJobRegistry.CompleteMcpWrite(pending.JobId, result);
            }
            catch (Exception persistenceFailure)
            {
                UnityAsyncJobRegistry.MarkMcpWriteUnknown(
                    pending.ExecutionId,
                    "The Unity call returned but its result could not be persisted (" + persistenceFailure.GetType().Name + "); execution state is unknown.");
            }
            finally
            {
                McpWriteSubmissions.TryRemove(pending.ExecutionId, out _);
            }
        }

        private static bool IsMcpWriteAuthorizedAtExecution(PendingInvocation pending, VRCForgeToolDescriptor descriptor)
        {
            if (pending == null || descriptor == null || !VRCForgeMcpToolContract.IsExpectedDescriptor(descriptor)
                || descriptor.Permission == VRCForgeCommandAccess.ReadOnly) return false;
            bool valid;
            if (pending.Lane == InvocationLane.ApprovedWrite)
                valid = ValidateApprovedExecutionContext(pending.ExecutionContext, pending.ToolName, pending.Arguments);
            else if (pending.Lane == InvocationLane.ExternalMcpWrite)
            {
                string ignoredFailureCode;
                valid = ValidateManagedWriteExecutionContext(
                    pending.ExecutionContext,
                    pending.ToolName,
                    pending.Arguments,
                    externalMcp: true,
                    validateUnityObjectIdentities: true,
                    failureCode: out ignoredFailureCode);
            }
            else return false;
            // Consume once at handler dispatch, after the final queued-state validation.
            return valid && ConsumeApprovedExecutionId(
                (string)pending.ExecutionContext["executionId"],
                pending.ExecutionContext["expiresAtUnixMs"].Value<long>());
        }

        private static JObject ReadMcpWriteOperation(JObject parameters)
        {
            if (parameters == null
                || parameters.Properties().Any(property => property.Name != "executionId" && property.Name != "jobId" && property.Name != "_meta")
                || !HasNonEmptyString(parameters, "executionId")
                || ((string)parameters["executionId"]).Length > 128
                || (parameters["jobId"] != null
                    && (parameters["jobId"].Type != JTokenType.String
                        || !UnityAsyncJobRegistry.IsValidJobId((string)parameters["jobId"]))))
                return AsyncWriteUnknownResponse(string.Empty, null);
            var snapshot = UnityAsyncJobRegistry.ReadMcpWriteSnapshot((string)parameters["executionId"]);
            if (snapshot == null || (parameters["jobId"] != null
                && !string.Equals((string)parameters["jobId"], (string)snapshot["jobId"], StringComparison.Ordinal)))
                return AsyncWriteUnknownResponse((string)parameters["executionId"], (string)parameters["jobId"]);
            if (string.Equals((string)snapshot["status"], "queued", StringComparison.Ordinal)) snapshot["retryAfterMs"] = 100;
            return snapshot;
        }

        private static JObject AsyncWriteSubmissionResponse(JObject snapshot)
        {
            var response = new JObject
            {
                ["schema"] = "vrcforge.write-operation.v1",
                ["jobId"] = snapshot["jobId"]?.DeepClone() ?? JValue.CreateNull(),
                ["executionId"] = snapshot["executionId"]?.DeepClone() ?? JValue.CreateNull(),
                ["status"] = snapshot["status"]?.DeepClone() ?? new JValue("unknown"),
            };
            if (string.Equals((string)snapshot["status"], "queued", StringComparison.Ordinal)) response["retryAfterMs"] = 100;
            return response;
        }

        private static JObject AsyncWriteUnknownResponse(string executionId, string jobId)
        {
            return new JObject
            {
                ["schema"] = "vrcforge.write-operation.v1",
                ["jobId"] = jobId == null ? JValue.CreateNull() : new JValue(jobId),
                ["executionId"] = executionId ?? string.Empty,
                ["status"] = "unknown",
            };
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
            if (pending.Lane == InvocationLane.ApprovedWrite)
            {
                var validApproved = ValidateApprovedExecutionContext(
                    pending.ExecutionContext,
                    pending.ToolName,
                    pending.Arguments);
                if (!validApproved)
                {
                    LastPeerReverifyFailure = "approved-context-invalid";
                }
                return validApproved
                    && (pending.AsyncWrite || ConsumeApprovedExecutionId(
                        (string)pending.ExecutionContext["executionId"],
                        pending.ExecutionContext["expiresAtUnixMs"].Value<long>()));
            }
            if (pending.Lane == InvocationLane.ExternalMcpWrite)
            {
                string drainFailureCode;
                var validExternal = ValidateExternalMcpExecutionContext(
                    pending.ExecutionContext,
                    pending.ToolName,
                    pending.Arguments,
                    out drainFailureCode);
                if (!validExternal)
                {
                    LastPeerReverifyFailure = "external-context-invalid:" + drainFailureCode;
                }
                if (!validExternal)
                {
                    return false;
                }
            }
            var consumed = pending.AsyncWrite || ConsumeApprovedExecutionId(
                (string)pending.ExecutionContext["executionId"],
                pending.ExecutionContext["expiresAtUnixMs"].Value<long>());
            if (!consumed)
            {
                LastPeerReverifyFailure = "execution-id-consume-failed";
            }
            return consumed;
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
                    && string.Equals(projectId, descriptorProjectId, StringComparison.Ordinal)
                    && string.Equals(instanceId, descriptorInstanceId, StringComparison.Ordinal);
            }
            catch (Exception)
            {
                return false;
            }
        }

        internal static string LastPeerReverifyFailure = "never-run";

        private static bool ReverifyManagedPeer(PendingInvocation pending)
        {
            if (pending == null || pending.ExecutionContext == null || pending.PeerEvidence == null)
            {
                LastPeerReverifyFailure = "pending-null";
                return false;
            }
            // Primary: re-screen the live connection while the socket is open.
            VRCForgeMcpPeerProcessEvidence evidence;
            if (VRCForgeMcpPeerProcessVerifier.TryScreenManagedBackendPeer(pending.Client, out evidence)
                && pending.PeerEvidence.Matches(evidence))
            {
                LastPeerReverifyFailure = "live-ok";
                return ClientProcessIdMatches(pending, evidence.ProcessId);
            }
            LastPeerReverifyFailure = evidence != null
                ? "live-screen-mismatch:" + (evidence.LastMismatch ?? "?")
                : "live-screen-failed";
            // The editor main thread drains the queue; while it is busy the
            // caller's TCP socket can time out and close, which makes the
            // connection-table screen impossible. Fall back to re-reading the
            // snapshotted process identity: same backend executable, same
            // parent shell, still running with the same start times. This is
            // equally fail-closed - a recycled or replaced process cannot
            // reproduce both start-time ticks.
            long processStartTicks;
            var processPath = VRCForgeMcpPeerProcessVerifier.ReadExpectedProcessPath(
                pending.PeerEvidence.ProcessId, "vrcforge_backend", out processStartTicks);
            if (string.IsNullOrEmpty(processPath)
                || processStartTicks != pending.PeerEvidence.ProcessStartTimeUtcTicks
                || !string.Equals(processPath, pending.PeerEvidence.ProcessPath, StringComparison.OrdinalIgnoreCase))
            {
                LastPeerReverifyFailure = "snapshot-process-gone-or-replaced"; return false;
            }
            return ClientProcessIdMatches(pending, pending.PeerEvidence.ProcessId);
        }

        private static bool ClientProcessIdMatches(PendingInvocation pending, int processId)
        {
            try
            {
                return pending.ExecutionContext != null
                    && pending.ExecutionContext["clientProcessId"] != null
                    && pending.ExecutionContext["clientProcessId"].Type == JTokenType.Integer
                    && pending.ExecutionContext["clientProcessId"].Value<int>() == processId;
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
                validateUnityObjectIdentities: true,
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
                validateUnityObjectIdentities: true,
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
                validateUnityObjectIdentities: false,
                failureCode: out failureCode);
        }

        private static bool ValidateManagedWriteExecutionContext(
            JObject context,
            string toolName,
            JObject arguments,
            bool externalMcp,
            bool validateUnityObjectIdentities,
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
                var checkpointRequired = true;
                if (!externalMcp && context["checkpointRequired"] != null)
                {
                    if (context["checkpointRequired"].Type != JTokenType.Boolean)
                    {
                        failureCode = "checkpoint_required_invalid";
                        return false;
                    }
                    checkpointRequired = context["checkpointRequired"].Value<bool>();
                }
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
                    || (!externalMcp && checkpointRequired && string.IsNullOrEmpty(checkpointId)))
                {
                    failureCode = "identity_invalid";
                    return false;
                }
                if (externalMcp && (context["approvalId"] != null || context["checkpointId"] != null))
                {
                    failureCode = "internal_fields_present";
                    return false;
                }
                if (externalMcp && !ValidateExecutionTargetContext(
                    context,
                    validateUnityObjectIdentities,
                    out failureCode))
                {
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
                if (!string.Equals(projectId, descriptorProjectId, StringComparison.Ordinal))
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

        private static bool ValidateExecutionTargetContext(
            JObject context,
            bool validateUnityObjectIdentities,
            out string failureCode)
        {
            failureCode = "execution_target_invalid";
            try
            {
                var target = context["executionTarget"] as JObject;
                var project = target == null ? null : target["project"] as JObject;
                var editor = target == null ? null : target["editor"] as JObject;
                var scope = target == null ? null : RequiredBoundedString(target, "scope", 32);
                if (target == null || project == null || editor == null
                    || !string.Equals((string)target["schema"], "vrcforge.execution_target.v1", StringComparison.Ordinal)
                    || !(scope == "project" || scope == "scene" || scope == "avatar" || scope == "object" || scope == "component"))
                {
                    return false;
                }

                var projectRoot = Path.GetFullPath(RequiredBoundedString(project, "root", 4096)).TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar);
                var currentRoot = Path.GetFullPath(GetProjectRoot()).TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar);
                var projectId = RequiredBoundedString(project, "projectId", 64);
                var unityPid = editor["unityPid"] != null && editor["unityPid"].Type == JTokenType.Integer
                    ? editor["unityPid"].Value<int>() : 0;
                if (!string.Equals(projectRoot, currentRoot, StringComparison.OrdinalIgnoreCase)
                    || !string.Equals(projectId, descriptorProjectId, StringComparison.Ordinal)
                    || unityPid != System.Diagnostics.Process.GetCurrentProcess().Id
                    || !string.Equals(RequiredBoundedString(editor, "coreInstanceId", 128), descriptorInstanceId, StringComparison.Ordinal)
                    || !string.Equals(RequiredBoundedString(editor, "processStartTime", 64), CurrentProcessStartTime(), StringComparison.Ordinal))
                {
                    failureCode = "execution_target_editor_or_project_drifted";
                    return false;
                }

                var scene = target["scene"] as JObject;
                GameObject avatarObject = null;
                GameObject targetObject = null;
                if (scope != "project")
                {
                    if (!ValidateExecutionTargetScene(scene, currentRoot))
                    {
                        failureCode = "execution_target_scene_drifted";
                        return false;
                    }
                }
                if (validateUnityObjectIdentities
                    && (scope == "avatar" || (scope == "object" || scope == "component") && target["avatar"] != null))
                {
                    avatarObject = ResolveExecutionTargetGameObject(target["avatar"] as JObject);
                    if (avatarObject == null || !string.Equals(avatarObject.scene.path.Replace('\\', '/'), (string)scene["assetPath"], StringComparison.Ordinal))
                    {
                        failureCode = "execution_target_avatar_drifted";
                        return false;
                    }
                }
                if (validateUnityObjectIdentities
                    && (scope == "object" || scope == "component"))
                {
                    targetObject = ResolveExecutionTargetGameObject(target["object"] as JObject);
                    if (targetObject == null
                        || !string.Equals(targetObject.scene.path.Replace('\\', '/'), (string)scene["assetPath"], StringComparison.Ordinal)
                        || (avatarObject != null && targetObject != avatarObject && !targetObject.transform.IsChildOf(avatarObject.transform)))
                    {
                        failureCode = "execution_target_object_drifted";
                        return false;
                    }
                }
                if (validateUnityObjectIdentities && scope == "component")
                {
                    var componentIdentity = target["component"] as JObject;
                    GlobalObjectId parsed;
                    var componentId = componentIdentity == null ? null : RequiredBoundedString(componentIdentity, "globalObjectId", 512);
                    var componentType = componentIdentity == null ? null : RequiredBoundedString(componentIdentity, "type", 512);
                    var component = !string.IsNullOrEmpty(componentId) && GlobalObjectId.TryParse(componentId, out parsed)
                        ? GlobalObjectId.GlobalObjectIdentifierToObjectSlow(parsed) as Component : null;
                    if (component == null || component.gameObject != targetObject
                        || !string.Equals(component.GetType().FullName, componentType, StringComparison.Ordinal))
                    {
                        failureCode = "execution_target_component_drifted";
                        return false;
                    }
                }

                var expectedNamespace = "vrcforge://projects/" + projectId;
                if (scene != null) expectedNamespace += "/scenes/" + RequiredBoundedString(scene, "guid", 64);
                var avatar = target["avatar"] as JObject;
                var objectIdentity = target["object"] as JObject;
                var componentSection = target["component"] as JObject;
                if (avatar != null) expectedNamespace += "/avatars/" + RequiredBoundedString(avatar, "globalObjectId", 512);
                if (objectIdentity != null) expectedNamespace += "/objects/" + RequiredBoundedString(objectIdentity, "globalObjectId", 512);
                if (componentSection != null) expectedNamespace += "/components/" + RequiredBoundedString(componentSection, "globalObjectId", 512);
                if (!string.Equals(RequiredBoundedString(target, "namespace", 4096), expectedNamespace, StringComparison.Ordinal))
                {
                    failureCode = "execution_target_namespace_mismatch";
                    return false;
                }
                failureCode = string.Empty;
                return true;
            }
            catch (Exception ex)
            {
                // Surface the exact failing field through the rejection code so
                // external agents can self-correct instead of guessing among
                // scene/editor/namespace drift possibilities.
                failureCode = "execution_target_invalid:" + ex.GetType().Name + ":" + ex.Message;
                return false;
            }
        }

        private static bool ValidateExecutionTargetScene(JObject scene, string projectRoot)
        {
            if (scene == null) return false;
            var assetPath = RequiredBoundedString(scene, "assetPath", 2048);
            var absolutePath = RequiredBoundedString(scene, "absolutePath", 4096);
            if (string.IsNullOrEmpty(assetPath) || !assetPath.StartsWith("Assets/", StringComparison.Ordinal)
                || !assetPath.EndsWith(".unity", StringComparison.OrdinalIgnoreCase)) return false;
            var expectedAbsolute = Path.GetFullPath(Path.Combine(projectRoot, assetPath.Replace('/', Path.DirectorySeparatorChar)));
            if (!string.Equals(Path.GetFullPath(absolutePath), expectedAbsolute, StringComparison.OrdinalIgnoreCase)
                || !File.Exists(expectedAbsolute)) return false;
            // The whole Scene-struct assertion (IsValid/isLoaded/path) is
            // main-thread-only Unity API; evaluate it as one bridged block.
            if (!RunOnMainThread(() =>
            {
                var activeScene = UnityEngine.SceneManagement.SceneManager.GetActiveScene();
                return activeScene.IsValid() && activeScene.isLoaded
                    && string.Equals(activeScene.path.Replace('\\', '/'), assetPath, StringComparison.Ordinal);
            })) return false;
            var guid = ReadUnityMetaGuid(expectedAbsolute + ".meta");
            // Content revisions belong to write/checkpoint state, not scene identity.
            return string.Equals(RequiredBoundedString(scene, "guid", 64), guid, StringComparison.Ordinal);
        }

        private static string ReadUnityMetaGuid(string metaPath)
        {
            if (!File.Exists(metaPath)) return string.Empty;
            foreach (var line in File.ReadLines(metaPath))
            {
                if (!line.StartsWith("guid:", StringComparison.Ordinal)) continue;
                var guid = line.Substring("guid:".Length).Trim();
                return IsLowerHex(guid, 32) ? guid : string.Empty;
            }
            return string.Empty;
        }

        private static GameObject ResolveExecutionTargetGameObject(JObject identity)
        {
            GlobalObjectId parsed;
            var value = identity == null ? null : RequiredBoundedString(identity, "globalObjectId", 512);
            if (string.IsNullOrEmpty(value) || !GlobalObjectId.TryParse(value, out parsed))
            {
                return null;
            }
            // GlobalObjectIdentifierToObjectSlow is a main-thread Unity API;
            // external write identity validation calls this from the TCP
            // service thread and must bridge before touching scene objects.
            return RunOnMainThread(() => GlobalObjectId.GlobalObjectIdentifierToObjectSlow(parsed) as GameObject);
        }

        private static string CurrentProcessStartTime()
        {
            const long unixEpochTicks = 621355968000000000L;
            var ticks = System.Diagnostics.Process.GetCurrentProcess().StartTime.ToUniversalTime().Ticks - unixEpochTicks;
            return ((double)ticks / TimeSpan.TicksPerSecond).ToString("F6", CultureInfo.InvariantCulture);
        }

        private static string ComputeFileSha256(string path)
        {
            using (var stream = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.ReadWrite | FileShare.Delete))
            using (var sha = SHA256.Create())
            {
                var digest = sha.ComputeHash(stream);
                return string.Concat(digest.Select(item => item.ToString("x2", CultureInfo.InvariantCulture)));
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
            if (exception != null && exception.InnerException != null)
            {
                var root = exception.GetBaseException();
                var rootMessage = root.Message ?? string.Empty;
                var context = message ?? string.Empty;
                message = root.GetType().Name + ": "
                    + rootMessage.Substring(0, Math.Min(800, rootMessage.Length))
                    + " Context: " + context.Substring(0, Math.Min(1600, context.Length));
            }
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
                var rootException = exception.GetBaseException();
                structured["rootCause"]["type"] = rootException.GetType().FullName ?? rootException.GetType().Name;
                structured["rootCause"]["message"] = (rootException.Message ?? message).Substring(
                    0, Math.Min(800, (rootException.Message ?? message).Length));
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
                ["supportsWriteOperationPolling"] = true,
                ["excludedExternalTools"] = new JArray(excludedExternalTools
                    .OrderBy(item => item.Name, StringComparer.Ordinal)
                    .ThenBy(item => item.TypeName, StringComparer.Ordinal)
                    .Select(item => new JObject
                    {
                        ["name"] = item.Name,
                        ["type"] = item.TypeName,
                        ["callable"] = false,
                        ["reason"] = "outside_owned_core_contract",
                    })),
                ["instanceId"] = descriptorInstanceId,
                ["processId"] = System.Diagnostics.Process.GetCurrentProcess().Id,
                ["processStartTime"] = CurrentProcessStartTime(),
                ["projectId"] = ComputeProjectId(GetProjectRoot()),
                ["projectIdSource"] = "normalized_project_path_sha256",
                ["compileSnapshot"] = CompileErrorMonitor.ReadCoreInfoSnapshot(30),
                ["scanScope"] = ScanScopeJournal.Capabilities(),
                ["assetDatabaseRefreshSavesDirtyAssets"] = VRCForge.Editor.AssetDatabaseRefreshTool.SavesDirtyAssets,
                ["checkpointPrepareSavesDirtyAssets"] = VRCForge.Editor.CheckpointPrepareTool.SavesDirtyAssets,
                ["checkpointRecoveryEvidenceOnly"] = true,
                ["checkpointRecoveryEvidenceSchema"] = "vrcforge.recovery_witness.v1",
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
                if (writeTool && !SafetyControlTools.Contains(descriptor.Name) && !descriptor.RequiresPolling)
                    metadata["io.vrcforge/asyncWrite"] = true;
                result.Add(new JObject
                {
                    ["name"] = descriptor.Name,
                    ["description"] = description,
                    ["inputSchema"] = descriptor.Name == GetPropertyTool.ToolName
                        ? UnityComponentPropertyBatch.CreateInputSchema(descriptor.CreateInputSchema())
                        : descriptor.CreateInputSchema(),
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
            var envelope = new JObject
            {
                ["schema"] = TransportSchema,
                ["message"] = message
            };
            var payload = Encoding.UTF8.GetBytes(envelope.ToString(Formatting.None));
            if (payload.Length > MaxResponseFrameBytes)
            {
                envelope["message"] = Error(message["id"], -32000,
                    "VRCForge Core response exceeds the bounded transport limit.",
                    new JObject
                    {
                        ["errorCode"] = "unity_core_response_too_large",
                        ["maxResponseBytes"] = MaxResponseFrameBytes,
                        ["actualResponseBytes"] = payload.Length,
                        ["retryable"] = false,
                    });
                payload = Encoding.UTF8.GetBytes(envelope.ToString(Formatting.None));
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
            descriptorProjectId = ComputeProjectId(root);
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
                ["processStartTime"] = CurrentProcessStartTime(),
                ["projectPath"] = root,
                ["projectId"] = descriptorProjectId,
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
