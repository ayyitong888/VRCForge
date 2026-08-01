using System;

namespace VRCForge.Core.MCP
{
    /// <summary>
    /// Declares a VRCForge Editor tool. Discovery and execution are intentionally
    /// separate so this declaration never grants a write by itself.
    /// </summary>
    [AttributeUsage(AttributeTargets.Class, AllowMultiple = false, Inherited = false)]
    public sealed class VRCForgeToolAttribute : Attribute
    {
        public string Name { get; set; }
        public string Description { get; set; }
        public bool StructuredOutput { get; set; } = true;
        public bool AutoRegister { get; set; } = true;
        public string Group { get; set; } = "core";
        public bool RequiresPolling { get; set; }
        public string PollAction { get; set; } = "status";
        public int MaxPollSeconds { get; set; }

        /// <summary>
        /// The declared permission ceiling. The default requires the normal
        /// VRCForge supervision and approval path; it is never an implicit write.
        /// </summary>
        public VRCForgeToolPermission Permission { get; set; } = VRCForgeToolPermission.RequiresApproval;

        /// <summary>
        /// Compatibility alias for callers that refer to the routing name.
        /// </summary>
        public string CommandName
        {
            get { return Name; }
            set { Name = value; }
        }

        public VRCForgeToolAttribute()
        {
        }

        public VRCForgeToolAttribute(string name)
        {
            Name = name;
        }
    }

    /// <summary>
    /// Declares the most permissive operation a tool may request. Enforcement is
    /// performed by the owning registry and approval lane, not by this metadata.
    /// </summary>
    public enum VRCForgeToolPermission
    {
        RequiresApproval = 0,
        ReadOnly = 1,
        ProjectWrite = 2,
        ExternalProcess = 3,
    }
}
