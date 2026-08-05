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
