using System;
using Newtonsoft.Json.Linq;

namespace VRCForge.Core.MCP
{
    /// <summary>
    /// Transport-neutral outcome returned by a VRCForge editor command. The
    /// project-owned MCP Core performs the explicit wire projection.
    /// </summary>
    public sealed class VRCForgeToolResult
    {
        private VRCForgeToolResult(
            VRCForgeToolResultKind kind,
            string message,
            string errorCode,
            object payload,
            double continuationDelaySeconds)
        {
            Kind = kind;
            Message = message ?? string.Empty;
            ErrorCode = errorCode ?? string.Empty;
            Payload = payload;
            ContinuationDelaySeconds = continuationDelaySeconds;
        }

        public VRCForgeToolResultKind Kind { get; private set; }
        public bool IsSuccessful { get { return Kind != VRCForgeToolResultKind.Failed; } }
        public string Message { get; private set; }
        public string ErrorCode { get; private set; }
        public object Payload { get; private set; }
        public double ContinuationDelaySeconds { get; private set; }

        public static VRCForgeToolResult Completed(string message, object payload = null)
        {
            return new VRCForgeToolResult(VRCForgeToolResultKind.Completed, message, string.Empty, payload, 0.0);
        }

        public static VRCForgeToolResult Failed(string messageOrCode, object payload = null)
        {
            return new VRCForgeToolResult(
                VRCForgeToolResultKind.Failed,
                messageOrCode,
                messageOrCode,
                payload,
                0.0);
        }

        public static VRCForgeToolResult FailedWithCode(
            string errorCode,
            string message,
            object payload = null)
        {
            if (string.IsNullOrWhiteSpace(errorCode))
            {
                throw new ArgumentException("errorCode is required.", "errorCode");
            }
            return new VRCForgeToolResult(
                VRCForgeToolResultKind.Failed,
                message,
                errorCode,
                payload,
                0.0);
        }

        /// <summary>Keep caught handler failures diagnosable without changing mutation facts.</summary>
        public static VRCForgeToolResult FailedWithException(
            string messageOrCode, Exception exception, object payload = null)
        {
            return FailedWithException(messageOrCode, messageOrCode, exception, payload);
        }

        public static VRCForgeToolResult FailedWithException(
            string errorCode, string message, Exception exception, object payload = null)
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
            var failure = payload == null ? new JObject() : JObject.FromObject(payload);
            var diagnostics = failure["diagnostics"] as JObject ?? new JObject();
            var existingSchema = diagnostics["schema"]?.DeepClone();
            diagnostics.Merge(ExceptionDiagnostics(exception), new JsonMergeSettings
            {
                MergeArrayHandling = MergeArrayHandling.Union,
            });
            if (existingSchema != null) diagnostics["schema"] = existingSchema;
            failure["diagnostics"] = diagnostics;
            if (failure["rootCause"] == null && exception != null)
            {
                var root = exception.GetBaseException();
                var rootMessage = root.Message ?? string.Empty;
                failure["rootCause"] = new JObject
                {
                    ["type"] = root.GetType().FullName ?? root.GetType().Name,
                    ["message"] = rootMessage.Substring(0, Math.Min(800, rootMessage.Length)),
                };
            }
            return FailedWithCode(errorCode, message, failure);
        }

        public static JObject ExceptionDiagnostics(Exception exception)
        {
            var chain = new JArray();
            var current = exception;
            var depth = 0;
            var stackTrace = string.Empty;
            while (current != null && depth++ < 6)
            {
                var message = current.Message ?? string.Empty;
                chain.Add(new JObject
                {
                    ["type"] = current.GetType().FullName ?? current.GetType().Name,
                    ["message"] = message.Substring(0, Math.Min(800, message.Length)),
                });
                // The deepest available stack points to the original cause,
                // rather than only the reflection wrapper that reported it.
                if (!string.IsNullOrWhiteSpace(current.StackTrace))
                    stackTrace = current.StackTrace;
                current = current.InnerException;
            }
            var handler = new JObject
            {
                ["type"] = exception == null ? "Exception" : exception.GetType().FullName ?? exception.GetType().Name,
                ["message"] = exception == null ? string.Empty
                    : (exception.Message ?? string.Empty).Substring(0, Math.Min(800, (exception.Message ?? string.Empty).Length)),
                ["innerChain"] = chain,
            };
            if (!string.IsNullOrEmpty(stackTrace))
                handler["stackTrace"] = stackTrace.Substring(0, Math.Min(4000, stackTrace.Length));
            return new JObject
            {
                ["schema"] = "vrcforge.unity_tool_handler_diagnostics.v1",
                ["handlerException"] = handler,
            };
        }

        public static VRCForgeToolResult RejectedBeforeMutation(
            string errorCode,
            string message,
            string failureLayer,
            string failurePhase,
            bool retryable = false,
            object details = null,
            Exception exception = null)
        {
            var payload = new JObject
            {
                ["failureLayer"] = failureLayer ?? string.Empty,
                ["failurePhase"] = failurePhase ?? string.Empty,
                ["toolRoutingStarted"] = true,
                ["mutationStarted"] = false,
                ["committed"] = false,
                ["commitState"] = "not_started",
                ["commitStateKnown"] = true,
                ["retryable"] = retryable,
                ["checkpointRecoveryRequired"] = false,
                ["temporaryCleanupRequired"] = false,
            };
            if (details != null)
            {
                payload["details"] = JToken.FromObject(details);
            }
            if (exception != null)
                return FailedWithException(errorCode, message, exception, payload);
            return FailedWithCode(errorCode, message, payload);
        }

        public static VRCForgeToolResult Waiting(
            string message = "",
            double continuationDelaySeconds = 1.0,
            object payload = null)
        {
            if (double.IsNaN(continuationDelaySeconds)
                || double.IsInfinity(continuationDelaySeconds)
                || continuationDelaySeconds <= 0.0)
            {
                throw new ArgumentOutOfRangeException("continuationDelaySeconds");
            }
            return new VRCForgeToolResult(
                VRCForgeToolResultKind.Waiting,
                message,
                string.Empty,
                payload,
                continuationDelaySeconds);
        }

        public JObject ToStructuredContent()
        {
            var result = new JObject { ["success"] = IsSuccessful };
            if (Kind == VRCForgeToolResultKind.Failed)
            {
                result["code"] = ErrorCode;
                result["error"] = Message;
            }
            else if (Kind == VRCForgeToolResultKind.Waiting)
            {
                result["_mcp_status"] = "pending";
                result["_mcp_poll_interval"] = ContinuationDelaySeconds;
                if (!string.IsNullOrEmpty(Message))
                {
                    result["message"] = Message;
                }
            }
            else
            {
                result["message"] = Message;
            }

            if (Payload != null)
            {
                result["data"] = JToken.FromObject(Payload);
            }
            return result;
        }
    }

    public enum VRCForgeToolResultKind
    {
        Completed = 0,
        Failed = 1,
        Waiting = 2,
    }
}
