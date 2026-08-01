using Newtonsoft.Json;

namespace VRCForge.Core.MCP
{
    public interface IMcpResponse
    {
        [JsonProperty("success")]
        bool Success { get; }
    }

    public sealed class SuccessResponse : IMcpResponse
    {
        [JsonProperty("success")]
        public bool Success { get { return true; } }

        [JsonIgnore]
        public bool success { get { return Success; } }

        [JsonProperty("message")]
        public string Message { get; private set; }

        [JsonProperty("data", NullValueHandling = NullValueHandling.Ignore)]
        public object Data { get; private set; }

        [JsonIgnore]
        public object data { get { return Data; } }

        public SuccessResponse(string message, object data = null)
        {
            Message = message;
            Data = data;
        }
    }

    public sealed class ErrorResponse : IMcpResponse
    {
        [JsonProperty("success")]
        public bool Success { get { return false; } }

        [JsonIgnore]
        public bool success { get { return Success; } }

        [JsonProperty("code", NullValueHandling = NullValueHandling.Ignore)]
        public string Code { get; private set; }

        [JsonIgnore]
        public string code { get { return Code; } }

        [JsonProperty("error")]
        public string Error { get; private set; }

        [JsonIgnore]
        public string error { get { return Error; } }

        [JsonProperty("data", NullValueHandling = NullValueHandling.Ignore)]
        public object Data { get; private set; }

        [JsonIgnore]
        public object data { get { return Data; } }

        public ErrorResponse(string messageOrCode, object data = null)
        {
            Code = messageOrCode;
            Error = messageOrCode;
            Data = data;
        }
    }

    public sealed class PendingResponse : IMcpResponse
    {
        [JsonProperty("success")]
        public bool Success { get { return true; } }

        [JsonIgnore]
        public bool success { get { return Success; } }

        [JsonProperty("_mcp_status")]
        public string Status { get { return "pending"; } }

        [JsonIgnore]
        public string _mcp_status { get { return Status; } }

        [JsonProperty("_mcp_poll_interval")]
        public double PollIntervalSeconds { get; private set; }

        [JsonIgnore]
        public double _mcp_poll_interval { get { return PollIntervalSeconds; } }

        [JsonProperty("message", NullValueHandling = NullValueHandling.Ignore)]
        public string Message { get; private set; }

        [JsonIgnore]
        public string message { get { return Message; } }

        [JsonProperty("data", NullValueHandling = NullValueHandling.Ignore)]
        public object Data { get; private set; }

        [JsonIgnore]
        public object data { get { return Data; } }

        public PendingResponse(string message = "", double pollIntervalSeconds = 1.0, object data = null)
        {
            Message = string.IsNullOrEmpty(message) ? null : message;
            PollIntervalSeconds = pollIntervalSeconds;
            Data = data;
        }
    }
}
