using System;

namespace VRCForge.Core.MCP
{
    /// <summary>
    /// Supplies the schema metadata for one field or property in a command's
    /// request object. Validation remains in the catalogue and command handler.
    /// </summary>
    [AttributeUsage(
        AttributeTargets.Parameter | AttributeTargets.Property | AttributeTargets.Field,
        AllowMultiple = false,
        Inherited = true)]
    public sealed class VRCForgeInputAttribute : Attribute
    {
        public VRCForgeInputAttribute(string helpText)
        {
            HelpText = helpText;
        }

        public VRCForgeInputAttribute(string key, string helpText)
        {
            Key = key;
            HelpText = helpText;
        }

        public string Key { get; set; }
        public string HelpText { get; private set; }
        public bool IsRequired { get; set; } = true;
        public string DefaultLiteral { get; set; }
    }
}
