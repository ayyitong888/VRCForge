using System;
using System.Collections;
using System.Collections.Generic;
using System.Linq;
using System.Reflection;
using Newtonsoft.Json.Linq;

namespace VRCForge.Core.MCP
{
    /// <summary>
    /// Immutable, transport-neutral projection of an explicitly declared tool.
    /// Metadata describes a requested ceiling only; it never grants access.
    /// </summary>
    public sealed class VRCForgeToolDescriptor
    {
        private readonly VRCForgeParameterSchema[] parameters;

        internal VRCForgeToolDescriptor(Type toolType, MethodInfo handler, VRCForgeCommandAttribute attribute,
            IEnumerable<VRCForgeParameterSchema> parameterSchemas)
        {
            ToolType = toolType;
            Handler = handler;
            Name = attribute.ToolId;
            Description = attribute.Summary ?? string.Empty;
            Permission = attribute.Access;
            Group = attribute.Category ?? string.Empty;
            StructuredOutput = attribute.Output == VRCForgeCommandOutput.Structured;
            RequiresPolling = attribute.UsesContinuation;
            PollAction = attribute.ContinuationAction ?? string.Empty;
            MaxPollSeconds = attribute.ContinuationTimeoutSeconds;
            parameters = (parameterSchemas ?? Enumerable.Empty<VRCForgeParameterSchema>())
                .OrderBy(item => item.Name, StringComparer.Ordinal)
                .ToArray();
        }

        public Type ToolType { get; private set; }
        public MethodInfo Handler { get; private set; }
        public string Name { get; private set; }
        public string Description { get; private set; }
        public VRCForgeCommandAccess Permission { get; private set; }
        public string Group { get; private set; }
        public bool StructuredOutput { get; private set; }
        public bool RequiresPolling { get; private set; }
        public string PollAction { get; private set; }
        public int MaxPollSeconds { get; private set; }
        public IList<VRCForgeParameterSchema> Parameters { get { return Array.AsReadOnly(parameters); } }

        /// <summary>Returns a stable JSON Schema suitable for a narrow adapter boundary.</summary>
        public JObject CreateInputSchema()
        {
            var properties = new JObject();
            var required = new JArray();
            foreach (var parameter in parameters)
            {
                var property = new JObject();
                property["type"] = parameter.SchemaType;
                if (!string.IsNullOrEmpty(parameter.Description))
                {
                    property["description"] = parameter.Description;
                }
                if (parameter.EnumValues.Count > 0)
                {
                    var enumValues = new JArray();
                    foreach (var enumValue in parameter.EnumValues)
                    {
                        enumValues.Add(enumValue);
                    }
                    property["enum"] = enumValues;
                }
                if (parameter.DefaultValue != null)
                {
                    property["default"] = parameter.DefaultValue;
                }
                properties[parameter.Name] = property;
                if (parameter.Required)
                {
                    required.Add(parameter.Name);
                }
            }

            var schema = new JObject();
            schema["type"] = "object";
            schema["properties"] = properties;
            schema["additionalProperties"] = false;
            if (required.Count > 0)
            {
                schema["required"] = required;
            }
            return schema;
        }
    }

    /// <summary>
    /// Local-only discovery and invocation catalogue. Remote identity validation,
    /// authorization, approval, checkpointing, and write execution belong to the
    /// adapter/FastAPI boundary; this class grants none of them.
    /// </summary>
    public sealed class VRCForgeToolRegistry
    {
        private readonly Dictionary<string, VRCForgeToolDescriptor> descriptors;

        private VRCForgeToolRegistry(Dictionary<string, VRCForgeToolDescriptor> descriptors)
        {
            this.descriptors = descriptors;
        }

        public IEnumerable<VRCForgeToolDescriptor> Tools
        {
            get { return descriptors.Values.OrderBy(item => item.Name, StringComparer.Ordinal).ToArray(); }
        }

        public static VRCForgeToolRegistry DiscoverLoadedAssemblies()
        {
            return Discover(AppDomain.CurrentDomain.GetAssemblies());
        }

        public static VRCForgeToolRegistry Discover(IEnumerable<Assembly> assemblies)
        {
            if (assemblies == null)
            {
                throw new ArgumentNullException("assemblies");
            }

            var result = new Dictionary<string, VRCForgeToolDescriptor>(StringComparer.Ordinal);
            foreach (var assembly in assemblies.Where(item => item != null && !item.IsDynamic)
                .OrderBy(item => item.FullName ?? string.Empty, StringComparer.Ordinal))
            {
                foreach (var type in GetLoadableTypes(assembly)
                    .OrderBy(item => item.FullName ?? item.Name, StringComparer.Ordinal))
                {
                    var attribute = (VRCForgeCommandAttribute)Attribute.GetCustomAttribute(type, typeof(VRCForgeCommandAttribute), false);
                    if (attribute == null || !attribute.IsDiscoverable)
                    {
                        continue;
                    }
                    if (!IsValidToolName(attribute.ToolId))
                    {
                        throw new InvalidOperationException("A VRCForge tool has no valid name: " + (type.FullName ?? type.Name));
                    }
                    var handler = FindHandler(type);
                    var parameters = DiscoverParameters(type);
                    var descriptor = new VRCForgeToolDescriptor(type, handler, attribute, parameters);
                    if (result.ContainsKey(descriptor.Name))
                    {
                        throw new InvalidOperationException("Duplicate VRCForge tool name: " + descriptor.Name);
                    }
                    result.Add(descriptor.Name, descriptor);
                }
            }
            return new VRCForgeToolRegistry(result);
        }

        public VRCForgeToolDescriptor GetRequired(string name)
        {
            VRCForgeToolDescriptor descriptor;
            if (string.IsNullOrWhiteSpace(name) || !descriptors.TryGetValue(name, out descriptor))
            {
                throw new KeyNotFoundException("Unknown VRCForge tool: " + (name ?? string.Empty));
            }
            return descriptor;
        }

        public object Invoke(string name, JObject parameters = null)
        {
            var descriptor = GetRequired(name);
            try
            {
                return descriptor.Handler.Invoke(null, new object[] { parameters ?? new JObject() });
            }
            catch (TargetInvocationException exception)
            {
                throw exception.InnerException ?? exception;
            }
        }

        private static IEnumerable<Type> GetLoadableTypes(Assembly assembly)
        {
            try
            {
                return assembly.GetTypes().Where(item => item != null).ToArray();
            }
            catch (ReflectionTypeLoadException exception)
            {
                return exception.Types.Where(item => item != null).ToArray();
            }
        }

        private static MethodInfo FindHandler(Type toolType)
        {
            var handler = toolType.GetMethod("HandleCommand", BindingFlags.Public | BindingFlags.Static,
                null, new[] { typeof(JObject) }, null);
            if (handler == null || handler.ReturnType != typeof(object))
            {
                throw new InvalidOperationException("VRCForge tool requires public static object HandleCommand(JObject): "
                    + (toolType.FullName ?? toolType.Name));
            }
            return handler;
        }

        private static bool IsValidToolName(string name)
        {
            if (string.IsNullOrEmpty(name) || name.Length > 128)
            {
                return false;
            }
            foreach (var character in name)
            {
                var allowed = (character >= 'A' && character <= 'Z')
                    || (character >= 'a' && character <= 'z')
                    || (character >= '0' && character <= '9')
                    || character == '_'
                    || character == '-'
                    || character == '.';
                if (!allowed)
                {
                    return false;
                }
            }
            return true;
        }

        private static IEnumerable<VRCForgeParameterSchema> DiscoverParameters(Type toolType)
        {
            var candidates = toolType.GetNestedTypes(BindingFlags.Public | BindingFlags.NonPublic)
                .Select(CreateParameterCandidate)
                .Where(item => item != null)
                .OrderBy(item => item.Type.FullName ?? item.Type.Name, StringComparer.Ordinal)
                .ToArray();

            if (candidates.Length == 0)
            {
                return Enumerable.Empty<VRCForgeParameterSchema>();
            }
            ParameterCandidate selected;
            if (candidates.Length == 1)
            {
                selected = candidates[0];
            }
            else
            {
                var explicitCandidates = candidates.Where(item => item.Type.Name == "Parameters"
                    || item.Type.Name == toolType.Name + "Parameters").ToArray();
                if (explicitCandidates.Length != 1)
                {
                    throw new InvalidOperationException("Ambiguous VRCForge parameter schema for tool: "
                        + (toolType.FullName ?? toolType.Name));
                }
                selected = explicitCandidates[0];
            }

            var seenNames = new HashSet<string>(StringComparer.Ordinal);
            var schemas = new List<VRCForgeParameterSchema>();
            foreach (var member in selected.Members.OrderBy(item => item.Name, StringComparer.Ordinal))
            {
                var attribute = (VRCForgeInputAttribute)Attribute.GetCustomAttribute(member, typeof(VRCForgeInputAttribute), true);
                if (attribute == null)
                {
                    continue;
                }
                var name = string.IsNullOrWhiteSpace(attribute.Key) ? member.Name : attribute.Key;
                if (!seenNames.Add(name))
                {
                    throw new InvalidOperationException("Duplicate VRCForge parameter name: " + name);
                }
                schemas.Add(new VRCForgeParameterSchema(name, attribute.HelpText, GetSchemaType(GetMemberType(member)),
                    attribute.IsRequired, attribute.DefaultLiteral, GetEnumValues(GetMemberType(member))));
            }
            return schemas;
        }

        private static ParameterCandidate CreateParameterCandidate(Type type)
        {
            var members = type.GetMembers(BindingFlags.Public | BindingFlags.Instance)
                .Where(item => (item.MemberType == MemberTypes.Property || item.MemberType == MemberTypes.Field)
                    && Attribute.IsDefined(item, typeof(VRCForgeInputAttribute), true))
                .ToArray();
            return members.Length == 0 ? null : new ParameterCandidate(type, members);
        }

        private static Type GetMemberType(MemberInfo member)
        {
            var property = member as PropertyInfo;
            if (property != null)
            {
                return property.PropertyType;
            }
            var field = member as FieldInfo;
            if (field != null)
            {
                return field.FieldType;
            }
            throw new InvalidOperationException("Unsupported VRCForge parameter member: " + member.Name);
        }

        private static VRCForgeSchemaValueType GetSchemaType(Type sourceType)
        {
            var type = Nullable.GetUnderlyingType(sourceType) ?? sourceType;
            if (type.IsEnum || type == typeof(string) || type == typeof(char) || type == typeof(Guid) || type == typeof(DateTime)) return VRCForgeSchemaValueType.String;
            if (type == typeof(bool)) return VRCForgeSchemaValueType.Boolean;
            if (type == typeof(byte) || type == typeof(sbyte) || type == typeof(short) || type == typeof(ushort)
                || type == typeof(int) || type == typeof(uint) || type == typeof(long) || type == typeof(ulong)) return VRCForgeSchemaValueType.Integer;
            if (type == typeof(float) || type == typeof(double) || type == typeof(decimal)) return VRCForgeSchemaValueType.Number;
            if (type.IsArray || (type != typeof(string) && typeof(IEnumerable).IsAssignableFrom(type))) return VRCForgeSchemaValueType.Array;
            return VRCForgeSchemaValueType.Object;
        }

        private static IEnumerable<string> GetEnumValues(Type sourceType)
        {
            var type = Nullable.GetUnderlyingType(sourceType) ?? sourceType;
            return type.IsEnum ? Enum.GetNames(type).OrderBy(item => item, StringComparer.Ordinal).ToArray() : new string[0];
        }

        private sealed class ParameterCandidate
        {
            public ParameterCandidate(Type type, MemberInfo[] members)
            {
                Type = type;
                Members = members;
            }
            public Type Type { get; private set; }
            public MemberInfo[] Members { get; private set; }
        }
    }
}
