using System;
using System.Collections.Concurrent;
using System.Collections.Generic;
using System.IO;
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
    /// Project-scoped, loopback-only MCP service. Explicitly read-only tools
    /// may execute directly; every write-capable tool fails closed.
    /// </summary>
    public static class VRCForgeMcpCoreServer
    {
        private const string TransportSchema = "vrcforge.mcp.transport.v1";
        private const string ProtocolVersion = "2025-11-25";
        private const int MaxFrameBytes = 1024 * 1024;
        private const int MaxClients = 4;
        private const int SocketTimeoutMilliseconds = 15000;
        private const int ThreadJoinMilliseconds = 2000;
        private const int InvocationQueueTimeoutMilliseconds = 120000;
        private static readonly object LifecycleGate = new object();
        private static readonly object Gate = new object();
        private static readonly HashSet<TcpClient> ActiveClients = new HashSet<TcpClient>();
        private static readonly HashSet<Thread> ActiveWorkers = new HashSet<Thread>();
        private static readonly ConcurrentQueue<PendingInvocation> PendingInvocations = new ConcurrentQueue<PendingInvocation>();

        private sealed class VRCForgeMcpProtocolException : Exception { }

        private sealed class PendingInvocation
        {
            public string ToolName;
            public JObject Arguments;
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

        public static void Start()
        {
            lock (LifecycleGate)
            {
                StartExclusive();
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
                    token = CreateToken();
                    descriptorPath = GetDescriptorPath();
                    descriptorInstanceId = Guid.NewGuid().ToString("N");
                    listener = new TcpListener(IPAddress.Loopback, 0);
                    listener.Start(MaxClients);
                    WriteDescriptor((IPEndPoint)listener.LocalEndpoint);
                    stopping = false;
                    EditorApplication.update += DrainInvocations;
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
            EditorApplication.update -= DrainInvocations;
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
            return new List<VRCForgeToolDescriptor>(VRCForgeToolRegistry.DiscoverLoadedAssemblies().Tools).ToArray();
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
                    var authenticated = false;
                    var initialized = false;
                    var receivedInitializedNotification = false;
                    while (!stopping)
                    {
                        var envelope = ReadEnvelope(stream);
                        if (!authenticated)
                        {
                            AuthenticateFirstEnvelope(envelope);
                            authenticated = true;
                        }
                        else
                        {
                            ValidateSubsequentEnvelope(envelope);
                        }

                        var message = envelope["message"] as JObject;
                        var response = HandleMessage(message, ref initialized, ref receivedInitializedNotification);
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
            var lengthBytes = ReadExactly(stream, 4);
            var length = (lengthBytes[0] << 24) | (lengthBytes[1] << 16) | (lengthBytes[2] << 8) | lengthBytes[3];
            if (length <= 0 || length > MaxFrameBytes)
            {
                throw new VRCForgeMcpProtocolException();
            }
            var payload = Encoding.UTF8.GetString(ReadExactly(stream, length));
            var envelope = JObject.Parse(payload);
            if (!string.Equals((string)envelope["schema"], TransportSchema, StringComparison.Ordinal)
                || !(envelope["message"] is JObject))
            {
                throw new VRCForgeMcpProtocolException();
            }
            return envelope;
        }

        private static byte[] ReadExactly(NetworkStream stream, int length)
        {
            var result = new byte[length];
            var offset = 0;
            while (offset < length)
            {
                var count = stream.Read(result, offset, length - offset);
                if (count <= 0)
                {
                    throw new IOException();
                }
                offset += count;
            }
            return result;
        }

        private static void AuthenticateFirstEnvelope(JObject envelope)
        {
            var authorizationToken = envelope["authorization"];
            if (authorizationToken == null || authorizationToken.Type != JTokenType.String)
            {
                throw new VRCForgeMcpProtocolException();
            }
            var authorization = (string)authorizationToken;
            const string prefix = "Bearer ";
            if (string.IsNullOrEmpty(authorization) || !authorization.StartsWith(prefix, StringComparison.Ordinal)
                || !ConstantTimeTokenEquals(authorization.Substring(prefix.Length), token))
            {
                throw new VRCForgeMcpProtocolException();
            }
        }

        private static void ValidateSubsequentEnvelope(JObject envelope)
        {
            var authorization = envelope["authorization"];
            if (authorization != null && authorization.Type != JTokenType.String)
            {
                throw new VRCForgeMcpProtocolException();
            }
            var supplied = authorization == null ? null : (string)authorization;
            if (authorization != null && (string.IsNullOrEmpty(supplied)
                || !supplied.StartsWith("Bearer ", StringComparison.Ordinal)
                || !ConstantTimeTokenEquals(supplied.Substring("Bearer ".Length), token)))
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

        private static JObject HandleMessage(JObject message, ref bool initialized, ref bool initializedNotification)
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

            if (string.Equals(method, "ping", StringComparison.Ordinal))
            {
                return hasId ? Result(id, new JObject()) : null;
            }
            if (!initialized)
            {
                if (!string.Equals(method, "initialize", StringComparison.Ordinal) || !hasId)
                {
                    return hasId ? Error(id, -32600, "Initialize first.") : null;
                }
                var clientInfo = parameters == null ? null : parameters["clientInfo"] as JObject;
                if (parameters == null
                    || !string.Equals((string)parameters["protocolVersion"], ProtocolVersion, StringComparison.Ordinal)
                    || !(parameters["capabilities"] is JObject)
                    || clientInfo == null
                    || clientInfo["name"] == null || clientInfo["name"].Type != JTokenType.String
                    || clientInfo["version"] == null || clientInfo["version"].Type != JTokenType.String)
                {
                    return Error(id, -32602, "Invalid initialize parameters.");
                }
                initialized = true;
                return Result(id, InitializeResult());
            }

            if (string.Equals(method, "notifications/initialized", StringComparison.Ordinal))
            {
                if (hasId)
                {
                    return Error(id, -32600, "Invalid notification.");
                }
                initializedNotification = true;
                return null;
            }
            if (!initializedNotification)
            {
                return hasId ? Error(id, -32600, "Initialized notification required.") : null;
            }

            if (string.Equals(method, "tools/list", StringComparison.Ordinal))
            {
                return hasId ? Result(id, ToolsListResult()) : null;
            }
            if (string.Equals(method, "tools/call", StringComparison.Ordinal))
            {
                return hasId ? Result(id, InvokeTool(parameters)) : null;
            }
            if (string.Equals(method, "resources/list", StringComparison.Ordinal))
            {
                return hasId ? Result(id, new JObject { ["resources"] = new JArray() }) : null;
            }
            if (string.Equals(method, "prompts/list", StringComparison.Ordinal))
            {
                return hasId ? Result(id, new JObject { ["prompts"] = new JArray() }) : null;
            }
            return hasId ? Error(id, -32601, "Method not found.") : null;
        }

        private static JObject InvokeTool(JObject parameters)
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
                return ToolError("Unknown VRCForge tool.");
            }
            if (descriptor.Permission != VRCForgeToolPermission.ReadOnly)
            {
                return ToolError("This tool requires the VRCForge FastAPI approval and checkpoint lane.");
            }

            return QueueInvocation(toolName, arguments);
        }

        private static JObject QueueInvocation(string toolName, JObject arguments)
        {

            var pending = new PendingInvocation
            {
                ToolName = toolName,
                Arguments = (JObject)arguments.DeepClone(),
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
                var registry = VRCForgeToolRegistry.DiscoverLoadedAssemblies();
                var descriptor = registry.GetRequired(pending.ToolName);
                if (descriptor.Permission != VRCForgeToolPermission.ReadOnly)
                {
                    throw new VRCForgeMcpProtocolException();
                }
                var result = registry.Invoke(pending.ToolName, pending.Arguments);
                pending.Response = ToolResult(result);
            }
            catch (Exception)
            {
                pending.Response = ToolError("VRCForge tool execution failed.");
            }
            finally
            {
                Volatile.Write(ref pending.State, 2);
                pending.Completion.Set();
            }
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

        private static JObject ToolResult(object value)
        {
            var tokenValue = value == null ? JValue.CreateNull() : JToken.FromObject(value);
            var isError = value is IMcpResponse response && !response.Success;
            var structured = tokenValue as JObject ?? new JObject { ["result"] = tokenValue.DeepClone() };
            return new JObject
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
        }

        private static JObject ToolError(string message)
        {
            return new JObject
            {
                ["content"] = new JArray
                {
                    new JObject { ["type"] = "text", ["text"] = message },
                },
                ["isError"] = true,
            };
        }

        private static JObject InitializeResult()
        {
            return new JObject
            {
                ["protocolVersion"] = ProtocolVersion,
                ["capabilities"] = new JObject
                {
                    ["tools"] = new JObject { ["listChanged"] = false },
                    ["resources"] = new JObject { ["listChanged"] = false },
                    ["prompts"] = new JObject { ["listChanged"] = false }
                },
                ["serverInfo"] = new JObject { ["name"] = "VRCForge MCP Core", ["version"] = "1" }
            };
        }

        private static JObject ToolsListResult()
        {
            var result = new JArray();
            foreach (var descriptor in tools)
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
            return new JObject { ["tools"] = result };
        }

        private static JObject Result(JToken id, JToken result)
        {
            return new JObject { ["jsonrpc"] = "2.0", ["id"] = id.DeepClone(), ["result"] = result };
        }

        private static JObject Error(JToken id, int code, string message)
        {
            return new JObject
            {
                ["jsonrpc"] = "2.0",
                ["id"] = id == null ? JValue.CreateNull() : id.DeepClone(),
                ["error"] = new JObject { ["code"] = code, ["message"] = message }
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
            var header = new[]
            {
                (byte)(payload.Length >> 24), (byte)(payload.Length >> 16),
                (byte)(payload.Length >> 8), (byte)payload.Length
            };
            stream.Write(header, 0, header.Length);
            stream.Write(payload, 0, payload.Length);
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
                ["transport"] = "tcp-length-prefixed-jsonrpc",
                ["protocolVersion"] = ProtocolVersion,
                ["host"] = IPAddress.Loopback.ToString(),
                ["port"] = endpoint.Port,
                ["authMode"] = "bearer",
                ["authToken"] = Convert.ToBase64String(token),
                ["instanceId"] = descriptorInstanceId,
                ["processId"] = System.Diagnostics.Process.GetCurrentProcess().Id,
                ["projectPath"] = root,
                ["projectHash"] = ComputeProjectHash(root),
                ["startedAt"] = DateTime.UtcNow.ToString("o"),
                ["toolCount"] = tools.Length,
                ["lifecycle"] = "unity-editor-domain",
                ["executionPolicy"] = "read-only-direct-writes-rejected"
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
