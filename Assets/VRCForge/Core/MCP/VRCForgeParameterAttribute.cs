using System;

namespace VRCForge.Core.MCP
{
    /// <summary>
    /// Describes a named input accepted by a VRCForge tool contract.
    /// </summary>
    [AttributeUsage(
        AttributeTargets.Parameter | AttributeTargets.Property | AttributeTargets.Field,
        AllowMultiple = false,
        Inherited = true)]
    public sealed class VRCForgeParameterAttribute : Attribute
    {
        public string Name { get; set; }
        public string Description { get; set; }
        public bool Required { get; set; } = true;
        public string DefaultValue { get; set; }

        public VRCForgeParameterAttribute(string description)
        {
            Description = description;
        }

        public VRCForgeParameterAttribute(string name, string description)
        {
            Name = name;
            Description = description;
        }
    }
}
