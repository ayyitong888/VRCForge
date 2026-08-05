using System;

namespace VRCForge.Core.MCP
{
    /// <summary>
    /// Marks one static editor command for discovery by the project-owned
    /// VRCForge catalogue. This metadata is descriptive and grants no access.
    /// </summary>
    [AttributeUsage(AttributeTargets.Class, AllowMultiple = false, Inherited = false)]
    public sealed class VRCForgeCommandAttribute : Attribute
    {
        public VRCForgeCommandAttribute(string toolId)
        {
            ToolId = toolId;
        }

        public string ToolId { get; private set; }
        public string Summary { get; set; }
        public VRCForgeCommandAccess Access { get; set; } = VRCForgeCommandAccess.RequiresApproval;
        public VRCForgeCommandOutput Output { get; set; } = VRCForgeCommandOutput.Structured;
        public bool IsDiscoverable { get; set; } = true;
        public string Category { get; set; } = "core";
        public bool UsesContinuation { get; set; }
        public string ContinuationAction { get; set; } = "status";
        public int ContinuationTimeoutSeconds { get; set; }
    }

    public enum VRCForgeCommandAccess
    {
        RequiresApproval = 0,
        ReadOnly = 1,
        ProjectWrite = 2,
        ExternalProcess = 3,
    }

    public enum VRCForgeCommandOutput
    {
        Structured = 0,
        Text = 1,
    }
}
