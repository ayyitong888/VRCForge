using System;
using System.Collections.Generic;

namespace VRCForge.Core.MCP
{
    /// <summary>
    /// Stable primitive kinds used by VRCForge's tool-schema projection.
    /// </summary>
    public enum VRCForgeSchemaValueType
    {
        String,
        Boolean,
        Integer,
        Number,
        Array,
        Object,
    }

    /// <summary>
    /// Pure data representation of one parameter. It deliberately has no
    /// transport, reflection, or third-party MCP dependency.
    /// </summary>
    public sealed class VRCForgeParameterSchema
    {
        private readonly string[] enumValues;

        public string Name { get; private set; }
        public string Description { get; private set; }
        public VRCForgeSchemaValueType ValueType { get; private set; }
        public bool Required { get; private set; }
        public string DefaultValue { get; private set; }
        public IList<string> EnumValues { get { return Array.AsReadOnly(enumValues); } }

        public VRCForgeParameterSchema(
            string name,
            string description,
            VRCForgeSchemaValueType valueType,
            bool required,
            string defaultValue = null,
            IEnumerable<string> enumValues = null)
        {
            if (string.IsNullOrWhiteSpace(name))
            {
                throw new ArgumentException("A parameter schema requires a name.", "name");
            }

            Name = name;
            Description = description ?? string.Empty;
            ValueType = valueType;
            Required = required;
            DefaultValue = defaultValue;
            this.enumValues = CopyAndSort(enumValues);
        }

        /// <summary>
        /// Returns the JSON-schema primitive spelling without allocating a
        /// transport-specific object.
        /// </summary>
        public string SchemaType
        {
            get
            {
                switch (ValueType)
                {
                    case VRCForgeSchemaValueType.Boolean:
                        return "boolean";
                    case VRCForgeSchemaValueType.Integer:
                        return "integer";
                    case VRCForgeSchemaValueType.Number:
                        return "number";
                    case VRCForgeSchemaValueType.Array:
                        return "array";
                    case VRCForgeSchemaValueType.Object:
                        return "object";
                    default:
                        return "string";
                }
            }
        }

        private static string[] CopyAndSort(IEnumerable<string> values)
        {
            if (values == null)
            {
                return new string[0];
            }

            var copied = new List<string>();
            foreach (var value in values)
            {
                if (value != null)
                {
                    copied.Add(value);
                }
            }

            copied.Sort(StringComparer.Ordinal);
            return copied.ToArray();
        }
    }
}
