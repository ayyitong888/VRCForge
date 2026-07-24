using System;
using System.Collections;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Security.Cryptography;
using System.Text;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEngine;

namespace VRCForge.Editor
{
    internal enum StructuredValueKind
    {
        ObjectReference,
        BoundedSingle
    }

    internal sealed class StructuredFieldSchema
    {
        internal string RequestKey = string.Empty;
        internal string MemberName = string.Empty;
        internal StructuredValueKind Kind;
        internal string ObjectTypeName = string.Empty;
        internal bool AllowNull;
        internal float Minimum;
        internal float Maximum;
    }

    internal sealed class StructuredListSchema
    {
        internal string Id = string.Empty;
        internal HashSet<string> ComponentTypeNames = new HashSet<string>(StringComparer.Ordinal);
        internal string ComponentAssemblyName = string.Empty;
        internal string ComponentAssemblyVersion = string.Empty;
        internal string ComponentAssemblyPublicKeyToken = string.Empty;
        internal string ComponentAssemblySha256 = string.Empty;
        internal string ListMemberName = string.Empty;
        internal string ListTypeName = string.Empty;
        internal string ElementTypeName = string.Empty;
        internal string AssemblyName = string.Empty;
        internal string AssemblyVersion = string.Empty;
        internal string AssemblyPublicKeyToken = string.Empty;
        internal string AssemblySha256 = string.Empty;
        internal int MaximumItems;
        internal bool RequireUniqueObjectReferences;
        internal List<StructuredFieldSchema> Fields = new List<StructuredFieldSchema>();
        internal Func<Type, object> ManagedElementFactory;
        internal Func<Type, Type, IReadOnlyList<object>, object> CollectionFactory;
        internal int FixedSlotCount;
        internal string TotalLengthRelativePath = string.Empty;
        internal string FixedSlotPrefix = string.Empty;
        internal string OverflowListRelativePath = string.Empty;
    }

    internal sealed class StructuredListElementValue
    {
        internal Dictionary<string, object> Values = new Dictionary<string, object>(StringComparer.Ordinal);
    }

    internal sealed class StructuredListPlan
    {
        internal Component Component;
        internal StructuredListSchema Schema;
        internal MemberInfo ListMember;
        internal Type ListType;
        internal Type ElementType;
        internal List<StructuredListElementValue> Before = new List<StructuredListElementValue>();
        internal List<StructuredListElementValue> Target = new List<StructuredListElementValue>();
        internal object OriginalCollection;
        internal object TargetCollection;
    }

    internal enum StructuredManagedValueKind
    {
        Boolean,
        Int32,
        EnumInt32,
        BoundedSingle,
        String,
        ObjectReference
    }

    internal sealed class StructuredManagedFieldSchema
    {
        internal string CanonicalKey = string.Empty;
        internal string RelativePath = string.Empty;
        internal StructuredManagedValueKind Kind;
        internal float Minimum;
        internal float Maximum;
        internal int MaximumStringLength;
        internal string ObjectTypeName = string.Empty;
        internal bool AllowNull;
    }

    internal sealed class StructuredManagedConcreteTypeSchema
    {
        internal string RuntimeTypeName = string.Empty;
        internal string SerializedFullTypeName = string.Empty;
        internal string AssemblyName = string.Empty;
        internal string AssemblyVersion = string.Empty;
        internal string AssemblyPublicKeyToken = string.Empty;
        internal string AssemblySha256 = string.Empty;
        internal bool RequireExactDirectFieldLayout = true;
        internal List<StructuredManagedFieldSchema> Fields = new List<StructuredManagedFieldSchema>();
        internal List<StructuredManagedCollectionSchema> Collections =
            new List<StructuredManagedCollectionSchema>();
    }

    internal enum StructuredManagedCollectionKind
    {
        TypedList,
        ManagedReferenceList
    }

    internal sealed class StructuredManagedCollectionSchema
    {
        internal string CanonicalKey = string.Empty;
        internal string RelativePath = string.Empty;
        internal StructuredManagedCollectionKind Kind;
        internal int MaximumItems;
        internal string DeclaredElementTypeName = string.Empty;
        internal string TypedElementRuntimeTypeName = string.Empty;
        internal string TypedElementAssemblyName = string.Empty;
        internal string TypedElementAssemblyVersion = string.Empty;
        internal string TypedElementAssemblyPublicKeyToken = string.Empty;
        internal string TypedElementAssemblySha256 = string.Empty;
        internal bool RequireExactElementFieldLayout = true;
        internal List<StructuredManagedFieldSchema> TypedElementFields =
            new List<StructuredManagedFieldSchema>();
        internal List<StructuredManagedConcreteTypeSchema> AllowedManagedElementTypes =
            new List<StructuredManagedConcreteTypeSchema>();
    }

    internal sealed class StructuredManagedReferenceSchema
    {
        internal string Id = string.Empty;
        internal string DigestSchema = string.Empty;
        internal string RootComponentTypeName = string.Empty;
        internal string RootAssemblyName = string.Empty;
        internal string RootAssemblyVersion = string.Empty;
        internal string RootAssemblyPublicKeyToken = string.Empty;
        internal string RootAssemblySha256 = string.Empty;
        internal string MemberName = string.Empty;
        internal bool AllowNull;
        internal List<StructuredManagedConcreteTypeSchema> AllowedConcreteTypes =
            new List<StructuredManagedConcreteTypeSchema>();
    }

    internal sealed class StructuredManagedFieldValue
    {
        internal string CanonicalKey = string.Empty;
        internal StructuredManagedValueKind Kind;
        internal object RawValue;
        internal string CanonicalValue = string.Empty;
    }

    internal sealed class StructuredManagedReferenceReadPlan
    {
        internal Component Component;
        internal StructuredManagedReferenceSchema Schema;
        internal StructuredManagedConcreteTypeSchema ConcreteSchema;
        internal object ManagedValue;
        internal string ComponentGlobalId = string.Empty;
        internal string ManagedReferenceFullTypeName = string.Empty;
        internal List<StructuredManagedFieldValue> Fields = new List<StructuredManagedFieldValue>();
        internal List<StructuredManagedCollectionValue> Collections =
            new List<StructuredManagedCollectionValue>();
        internal string CanonicalDigest = string.Empty;

        internal StructuredManagedFieldValue RequireField(string canonicalKey)
        {
            var matches = Fields.Where(field => field.CanonicalKey == canonicalKey).ToList();
            if (matches.Count != 1)
            {
                throw new InvalidOperationException("Managed-reference canonical field is unavailable.");
            }
            return matches[0];
        }

        internal StructuredManagedCollectionValue RequireCollection(string canonicalKey)
        {
            var matches = Collections.Where(value => value.CanonicalKey == canonicalKey).ToList();
            if (matches.Count != 1)
            {
                throw new InvalidOperationException("Managed-reference canonical collection is unavailable.");
            }
            return matches[0];
        }

        internal JObject ToCanonicalJObject()
        {
            var fields = new JObject();
            foreach (var field in Fields)
            {
                fields[field.CanonicalKey] = field.CanonicalValue;
            }
            var collections = new JObject();
            foreach (var collection in Collections)
            {
                collections[collection.CanonicalKey] = collection.ToCanonicalJArray();
            }
            return new JObject
            {
                ["schema"] = Schema.DigestSchema,
                ["componentGlobalId"] = ComponentGlobalId,
                ["concreteType"] = ManagedReferenceFullTypeName,
                ["fields"] = fields,
                ["collections"] = collections,
                ["digest"] = CanonicalDigest
            };
        }
    }

    internal sealed class StructuredManagedCollectionValue
    {
        internal string CanonicalKey = string.Empty;
        internal StructuredManagedCollectionKind Kind;
        internal List<StructuredManagedElementValue> Elements =
            new List<StructuredManagedElementValue>();

        internal JArray ToCanonicalJArray()
        {
            return new JArray(Elements.Select(element => element.ToCanonicalJObject()));
        }
    }

    internal sealed class StructuredManagedElementValue
    {
        internal string RuntimeTypeName = string.Empty;
        internal string SerializedFullTypeName = string.Empty;
        internal List<StructuredManagedFieldValue> Fields = new List<StructuredManagedFieldValue>();

        internal StructuredManagedFieldValue RequireField(string canonicalKey)
        {
            var matches = Fields.Where(field => field.CanonicalKey == canonicalKey).ToList();
            if (matches.Count != 1)
            {
                throw new InvalidOperationException("Managed-reference collection field is unavailable.");
            }
            return matches[0];
        }

        internal JObject ToCanonicalJObject()
        {
            var fields = new JObject();
            foreach (var field in Fields)
            {
                fields[field.CanonicalKey] = field.CanonicalValue;
            }
            return new JObject
            {
                ["runtimeType"] = RuntimeTypeName,
                ["serializedType"] = SerializedFullTypeName,
                ["fields"] = fields
            };
        }
    }

    internal static class TypedStructuredListCore
    {
        private static readonly Dictionary<string, StructuredListSchema> Schemas =
            new Dictionary<string, StructuredListSchema>(StringComparer.Ordinal);
        private static readonly Dictionary<string, StructuredManagedReferenceSchema> ManagedSchemas =
            new Dictionary<string, StructuredManagedReferenceSchema>(StringComparer.Ordinal);
        private static readonly Dictionary<string, string> ManagedSchemaFingerprints =
            new Dictionary<string, string>(StringComparer.Ordinal);

        internal static void Register(StructuredListSchema schema)
        {
            if (schema == null
                || string.IsNullOrWhiteSpace(schema.Id)
                || schema.ComponentTypeNames == null
                || schema.ComponentTypeNames.Count == 0
                || string.IsNullOrWhiteSpace(schema.ComponentAssemblyName)
                || string.IsNullOrWhiteSpace(schema.ComponentAssemblyVersion)
                || schema.ComponentAssemblyPublicKeyToken == null
                || string.IsNullOrWhiteSpace(schema.ComponentAssemblySha256)
                || string.IsNullOrWhiteSpace(schema.ListMemberName)
                || string.IsNullOrWhiteSpace(schema.ListTypeName)
                || string.IsNullOrWhiteSpace(schema.ElementTypeName)
                || string.IsNullOrWhiteSpace(schema.AssemblyName)
                || string.IsNullOrWhiteSpace(schema.AssemblyVersion)
                || schema.AssemblyPublicKeyToken == null
                || string.IsNullOrWhiteSpace(schema.AssemblySha256)
                || schema.MaximumItems < 0
                || schema.Fields == null
                || schema.Fields.Count == 0
                || schema.ManagedElementFactory == null)
            {
                throw new InvalidOperationException("Structured-list schema registration is incomplete.");
            }
            if (schema.Fields.Any(field => field == null
                    || string.IsNullOrWhiteSpace(field.RequestKey)
                    || string.IsNullOrWhiteSpace(field.MemberName))
                || schema.Fields.Select(field => field.RequestKey).Distinct(StringComparer.Ordinal).Count()
                    != schema.Fields.Count
                || schema.Fields.Select(field => field.MemberName).Distinct(StringComparer.Ordinal).Count()
                    != schema.Fields.Count)
            {
                throw new InvalidOperationException("Structured-list schema fields are invalid.");
            }
            if (schema.Fields.Any(field => field.Kind == StructuredValueKind.BoundedSingle
                    && (!IsFinite(field.Minimum) || !IsFinite(field.Maximum) || field.Minimum > field.Maximum)))
            {
                throw new InvalidOperationException("Structured-list scalar bounds are invalid.");
            }
            if (Schemas.ContainsKey(schema.Id))
            {
                throw new InvalidOperationException("Structured-list schema is already registered.");
            }
            Schemas.Add(schema.Id, schema);
        }

        internal static StructuredManagedReferenceSchema RegisterManagedReferenceSchema(
            StructuredManagedReferenceSchema schema)
        {
            if (schema == null
                || string.IsNullOrWhiteSpace(schema.Id)
                || string.IsNullOrWhiteSpace(schema.DigestSchema)
                || string.IsNullOrWhiteSpace(schema.RootComponentTypeName)
                || string.IsNullOrWhiteSpace(schema.RootAssemblyName)
                || string.IsNullOrWhiteSpace(schema.RootAssemblyVersion)
                || schema.RootAssemblyPublicKeyToken == null
                || string.IsNullOrWhiteSpace(schema.RootAssemblySha256)
                || string.IsNullOrWhiteSpace(schema.MemberName)
                || schema.AllowedConcreteTypes == null
                || schema.AllowedConcreteTypes.Count == 0
                || ManagedSchemas.ContainsKey(schema.Id))
            {
                throw new InvalidOperationException("Managed-reference schema registration is incomplete.");
            }
            if (schema.AllowedConcreteTypes.Any(concrete => !ManagedConcreteSchemaIsValid(concrete, true))
                || schema.AllowedConcreteTypes.Select(concrete => concrete.RuntimeTypeName)
                    .Distinct(StringComparer.Ordinal).Count() != schema.AllowedConcreteTypes.Count
                || schema.AllowedConcreteTypes.Select(concrete => concrete.SerializedFullTypeName)
                    .Distinct(StringComparer.Ordinal).Count() != schema.AllowedConcreteTypes.Count)
            {
                throw new InvalidOperationException("Managed-reference concrete-type registration is invalid.");
            }
            ManagedSchemas.Add(schema.Id, schema);
            ManagedSchemaFingerprints.Add(schema.Id, ComputeManagedSchemaFingerprint(schema));
            return schema;
        }

        internal static StructuredManagedReferenceReadPlan BuildManagedReferenceReadPlan(
            Component component,
            StructuredManagedReferenceSchema schema)
        {
            StructuredManagedReferenceSchema registered;
            string registeredFingerprint;
            if (schema == null
                || !ManagedSchemas.TryGetValue(schema.Id, out registered)
                || !ManagedSchemaFingerprints.TryGetValue(schema.Id, out registeredFingerprint)
                || !ReferenceEquals(schema, registered)
                || ComputeManagedSchemaFingerprint(schema) != registeredFingerprint
                || component == null
                || component.GetType().FullName != schema.RootComponentTypeName)
            {
                throw new InvalidOperationException("Managed-reference schema or component is not registered.");
            }
            ValidateAssembly(
                component.GetType().Assembly,
                schema.RootAssemblyName,
                schema.RootAssemblyVersion,
                schema.RootAssemblyPublicKeyToken,
                schema.RootAssemblySha256
            );
            var componentGlobalId = StableGlobalObjectId(component, "managed-reference component");
            using (var serialized = new SerializedObject(component))
            {
                serialized.UpdateIfRequiredOrScript();
                var root = serialized.FindProperty(schema.MemberName);
                if (root == null || root.propertyType != SerializedPropertyType.ManagedReference)
                {
                    throw new InvalidOperationException("Registered managed-reference layout is unsupported.");
                }
                var managedValue = root.managedReferenceValue;
                var serializedType = root.managedReferenceFullTypename ?? string.Empty;
                if (managedValue == null)
                {
                    if (!schema.AllowNull || !string.IsNullOrEmpty(serializedType))
                    {
                        throw new InvalidOperationException("Registered managed-reference value is unavailable.");
                    }
                    var emptyPlan = new StructuredManagedReferenceReadPlan
                    {
                        Component = component,
                        Schema = schema,
                        ManagedValue = null,
                        ComponentGlobalId = componentGlobalId,
                        ManagedReferenceFullTypeName = string.Empty
                    };
                    emptyPlan.CanonicalDigest = ComputeManagedReferenceDigest(emptyPlan);
                    return emptyPlan;
                }

                var runtimeType = managedValue.GetType();
                var concreteMatches = schema.AllowedConcreteTypes.Where(concrete =>
                    concrete.RuntimeTypeName == (runtimeType.FullName ?? string.Empty)
                    && concrete.SerializedFullTypeName == serializedType).ToList();
                if (concreteMatches.Count != 1)
                {
                    throw new InvalidOperationException("Managed-reference concrete type is not allowlisted.");
                }
                var concreteSchema = concreteMatches[0];
                ValidateAssembly(
                    runtimeType.Assembly,
                    concreteSchema.AssemblyName,
                    concreteSchema.AssemblyVersion,
                    concreteSchema.AssemblyPublicKeyToken,
                    concreteSchema.AssemblySha256
                );
                if (concreteSchema.RequireExactDirectFieldLayout)
                {
                    ValidateExactManagedReferenceLayout(root, concreteSchema);
                }
                var values = concreteSchema.Fields
                    .Select(field => ReadManagedReferenceField(root, field))
                    .ToList();
                var collections = concreteSchema.Collections
                    .Select(collection => ReadManagedReferenceCollection(
                        root,
                        runtimeType,
                        collection
                    ))
                    .ToList();
                var plan = new StructuredManagedReferenceReadPlan
                {
                    Component = component,
                    Schema = schema,
                    ConcreteSchema = concreteSchema,
                    ManagedValue = managedValue,
                    ComponentGlobalId = componentGlobalId,
                    ManagedReferenceFullTypeName = serializedType,
                    Fields = values,
                    Collections = collections
                };
                plan.CanonicalDigest = ComputeManagedReferenceDigest(plan);
                return plan;
            }
        }

        internal static StructuredManagedReferenceReadPlan ReadManagedReference(
            Component component,
            StructuredManagedReferenceSchema schema)
        {
            return BuildManagedReferenceReadPlan(component, schema);
        }

        internal static StructuredListPlan BuildPlan(
            Component component,
            string schemaId,
            JArray requestedItems,
            Func<string, Type, UnityEngine.Object> objectResolver)
        {
            var schema = ResolveSchema(schemaId);
            var binding = ResolveBinding(component, schema);
            var originalCollection = GetMemberValue(component, binding.ListMember);
            var before = ReadCollection(schema, binding.ElementType, originalCollection);
            ValidateSerializedShape(component, schema, before);
            var target = ParseTarget(schema, binding.ElementType, requestedItems, objectResolver);
            return new StructuredListPlan
            {
                Component = component,
                Schema = schema,
                ListMember = binding.ListMember,
                ListType = binding.ListType,
                ElementType = binding.ElementType,
                Before = before,
                Target = target,
                OriginalCollection = originalCollection,
                TargetCollection = BuildCollection(schema, binding.ListType, binding.ElementType, target)
            };
        }

        internal static List<StructuredListElementValue> ReadElements(Component component, string schemaId)
        {
            var schema = ResolveSchema(schemaId);
            var binding = ResolveBinding(component, schema);
            var collection = GetMemberValue(component, binding.ListMember);
            var elements = ReadCollection(schema, binding.ElementType, collection);
            ValidateSerializedShape(component, schema, elements);
            return elements;
        }

        internal static void Apply(Component component, StructuredListPlan plan)
        {
            if (plan == null || !ReferenceEquals(component, plan.Component))
            {
                throw new InvalidOperationException("Structured-list plan target changed.");
            }
            SetMemberValue(component, plan.ListMember, plan.TargetCollection);
            var readback = ReadElements(component, plan.Schema.Id);
            if (!ElementsEqual(plan.Schema, readback, plan.Target))
            {
                throw new InvalidOperationException("Structured-list in-memory readback did not match the plan.");
            }
        }

        internal static void Restore(
            Component component,
            string schemaId,
            IReadOnlyList<StructuredListElementValue> elements)
        {
            var schema = ResolveSchema(schemaId);
            var binding = ResolveBinding(component, schema);
            var restored = elements == null
                ? new List<StructuredListElementValue>()
                : elements.Select(CloneElement).ToList();
            var collection = BuildCollection(schema, binding.ListType, binding.ElementType, restored);
            SetMemberValue(component, binding.ListMember, collection);
            var readback = ReadElements(component, schemaId);
            if (!ElementsEqual(schema, readback, restored))
            {
                throw new InvalidOperationException("Structured-list restore readback did not match.");
            }
        }

        internal static void RestoreOriginal(Component component, StructuredListPlan plan)
        {
            if (plan == null || !ReferenceEquals(component, plan.Component) || plan.OriginalCollection == null)
            {
                throw new InvalidOperationException("Structured-list original state is unavailable.");
            }
            SetMemberValue(component, plan.ListMember, plan.OriginalCollection);
            var readback = ReadElements(component, plan.Schema.Id);
            if (!ElementsEqual(plan.Schema, readback, plan.Before))
            {
                throw new InvalidOperationException("Structured-list original-state restore did not match.");
            }
        }

        internal static bool ElementsEqual(
            StructuredListSchema schema,
            IReadOnlyList<StructuredListElementValue> left,
            IReadOnlyList<StructuredListElementValue> right)
        {
            if (schema == null || left == null || right == null || left.Count != right.Count)
            {
                return false;
            }
            for (var index = 0; index < left.Count; index++)
            {
                foreach (var field in schema.Fields)
                {
                    object leftValue;
                    object rightValue;
                    if (!left[index].Values.TryGetValue(field.RequestKey, out leftValue)
                        || !right[index].Values.TryGetValue(field.RequestKey, out rightValue))
                    {
                        return false;
                    }
                    if (field.Kind == StructuredValueKind.ObjectReference)
                    {
                        if (!ReferenceEquals(leftValue as UnityEngine.Object, rightValue as UnityEngine.Object))
                        {
                            return false;
                        }
                    }
                    else if (field.Kind == StructuredValueKind.BoundedSingle
                        && BitConverter.SingleToInt32Bits(Convert.ToSingle(leftValue, CultureInfo.InvariantCulture))
                            != BitConverter.SingleToInt32Bits(Convert.ToSingle(rightValue, CultureInfo.InvariantCulture)))
                    {
                        return false;
                    }
                }
            }
            return true;
        }

        internal static void ValidateSerializedShape(
            Component component,
            StructuredListSchema schema,
            IReadOnlyList<StructuredListElementValue> logicalElements)
        {
            if (schema.FixedSlotCount <= 0
                || string.IsNullOrWhiteSpace(schema.TotalLengthRelativePath)
                || string.IsNullOrWhiteSpace(schema.FixedSlotPrefix)
                || string.IsNullOrWhiteSpace(schema.OverflowListRelativePath))
            {
                throw new InvalidOperationException("Structured-list serialized layout is not registered.");
            }
            using (var serialized = new SerializedObject(component))
            {
                serialized.UpdateIfRequiredOrScript();
                var listProperty = serialized.FindProperty(schema.ListMemberName);
                if (listProperty == null || listProperty.propertyType != SerializedPropertyType.Generic)
                {
                    throw new InvalidOperationException("Structured-list serialized root layout is unsupported.");
                }
                var totalLength = listProperty.FindPropertyRelative(schema.TotalLengthRelativePath);
                var overflow = listProperty.FindPropertyRelative(schema.OverflowListRelativePath);
                if (totalLength == null
                    || totalLength.propertyType != SerializedPropertyType.Integer
                    || totalLength.intValue != logicalElements.Count
                    || overflow == null
                    || !overflow.isArray
                    || overflow.arraySize != Math.Max(0, logicalElements.Count - schema.FixedSlotCount))
                {
                    throw new InvalidOperationException("Structured-list serialized length layout is unsupported.");
                }

                var fixedCount = Math.Min(logicalElements.Count, schema.FixedSlotCount);
                for (var index = 0; index < fixedCount; index++)
                {
                    var element = listProperty.FindPropertyRelative(schema.FixedSlotPrefix + index);
                    ValidateSerializedElement(schema, element, logicalElements[index]);
                }
                for (var index = schema.FixedSlotCount; index < logicalElements.Count; index++)
                {
                    var element = overflow.GetArrayElementAtIndex(index - schema.FixedSlotCount);
                    ValidateSerializedElement(schema, element, logicalElements[index]);
                }
            }
        }

        private static void ValidateSerializedElement(
            StructuredListSchema schema,
            SerializedProperty element,
            StructuredListElementValue logical)
        {
            if (element == null || logical == null)
            {
                throw new InvalidOperationException("Structured-list serialized element is unavailable.");
            }
            foreach (var field in schema.Fields)
            {
                var property = element.FindPropertyRelative(field.MemberName);
                object logicalValue;
                if (property == null || !logical.Values.TryGetValue(field.RequestKey, out logicalValue))
                {
                    throw new InvalidOperationException("Structured-list serialized field layout is unsupported.");
                }
                if (field.Kind == StructuredValueKind.ObjectReference)
                {
                    if (property.propertyType != SerializedPropertyType.ObjectReference
                        || !ReferenceEquals(property.objectReferenceValue, logicalValue as UnityEngine.Object))
                    {
                        throw new InvalidOperationException("Structured-list object-reference order is inconsistent.");
                    }
                }
                else if (field.Kind == StructuredValueKind.BoundedSingle)
                {
                    if (property.propertyType != SerializedPropertyType.Float
                        || BitConverter.SingleToInt32Bits(property.floatValue)
                            != BitConverter.SingleToInt32Bits(Convert.ToSingle(logicalValue, CultureInfo.InvariantCulture)))
                    {
                        throw new InvalidOperationException("Structured-list scalar order is inconsistent.");
                    }
                }
            }
        }

        private static List<StructuredListElementValue> ParseTarget(
            StructuredListSchema schema,
            Type elementType,
            JArray requested,
            Func<string, Type, UnityEngine.Object> objectResolver)
        {
            if (requested == null || requested.Count > schema.MaximumItems)
            {
                throw new InvalidOperationException("Structured-list request exceeds its fixed schema bound.");
            }
            var allowedKeys = new HashSet<string>(schema.Fields.Select(field => field.RequestKey), StringComparer.Ordinal);
            var elements = new List<StructuredListElementValue>();
            var seenObjectIds = new HashSet<int>();
            foreach (var token in requested)
            {
                var item = token as JObject;
                if (item == null
                    || !new HashSet<string>(
                        item.Properties().Select(property => property.Name),
                        StringComparer.Ordinal
                    ).SetEquals(allowedKeys))
                {
                    throw new InvalidOperationException("Structured-list item does not match its registered schema.");
                }
                var parsed = new StructuredListElementValue();
                foreach (var field in schema.Fields)
                {
                    var valueToken = item[field.RequestKey];
                    if (field.Kind == StructuredValueKind.ObjectReference)
                    {
                        if (valueToken == null || valueToken.Type != JTokenType.String)
                        {
                            throw new InvalidOperationException("Structured-list object reference must be a path string.");
                        }
                        var raw = valueToken.ToString().Trim();
                        if (string.IsNullOrWhiteSpace(raw))
                        {
                            if (!field.AllowNull)
                            {
                                throw new InvalidOperationException("Structured-list null object reference is not supported.");
                            }
                            parsed.Values[field.RequestKey] = null;
                            continue;
                        }
                        if (objectResolver == null)
                        {
                            throw new InvalidOperationException("Structured-list object resolver is unavailable.");
                        }
                        var objectType = ResolveExactType(field.ObjectTypeName);
                        var resolved = objectResolver(raw, objectType);
                        if (resolved == null || !objectType.IsInstanceOfType(resolved))
                        {
                            throw new InvalidOperationException("Structured-list object reference did not resolve to its registered type.");
                        }
                        if (schema.RequireUniqueObjectReferences && !seenObjectIds.Add(resolved.GetInstanceID()))
                        {
                            throw new InvalidOperationException("Structured-list object references must be unique.");
                        }
                        parsed.Values[field.RequestKey] = resolved;
                    }
                    else if (field.Kind == StructuredValueKind.BoundedSingle)
                    {
                        if (valueToken == null
                            || (valueToken.Type != JTokenType.Integer && valueToken.Type != JTokenType.Float))
                        {
                            throw new InvalidOperationException("Structured-list scalar must be numeric.");
                        }
                        var numeric = valueToken.Value<double>();
                        var value = (float)numeric;
                        if (double.IsNaN(numeric)
                            || double.IsInfinity(numeric)
                            || !IsFinite(value)
                            || value < field.Minimum
                            || value > field.Maximum)
                        {
                            throw new InvalidOperationException("Structured-list scalar is out of range.");
                        }
                        parsed.Values[field.RequestKey] = value;
                    }
                    else
                    {
                        throw new InvalidOperationException("Structured-list field kind is not registered.");
                    }
                }
                elements.Add(parsed);
            }
            return elements;
        }

        private static List<StructuredListElementValue> ReadCollection(
            StructuredListSchema schema,
            Type elementType,
            object collection)
        {
            var enumerable = collection as IEnumerable;
            if (enumerable == null)
            {
                throw new InvalidOperationException("Structured-list member is not enumerable.");
            }
            var elements = new List<StructuredListElementValue>();
            foreach (var rawElement in enumerable)
            {
                if (rawElement == null || !elementType.IsInstanceOfType(rawElement))
                {
                    throw new InvalidOperationException("Structured-list element type is unsupported.");
                }
                if (elements.Count >= schema.MaximumItems)
                {
                    throw new InvalidOperationException("Structured-list current value exceeds its schema bound.");
                }
                var element = new StructuredListElementValue();
                foreach (var field in schema.Fields)
                {
                    var member = ResolveMember(elementType, field.MemberName);
                    ValidateFieldMember(field, member);
                    var value = GetMemberValue(rawElement, member);
                    if (field.Kind == StructuredValueKind.ObjectReference)
                    {
                        var objectValue = value as UnityEngine.Object;
                        if (objectValue == null && !field.AllowNull)
                        {
                            throw new InvalidOperationException("Structured-list contains an unsupported null reference.");
                        }
                        element.Values[field.RequestKey] = objectValue;
                    }
                    else
                    {
                        var single = Convert.ToSingle(value, CultureInfo.InvariantCulture);
                        if (!IsFinite(single) || single < field.Minimum || single > field.Maximum)
                        {
                            throw new InvalidOperationException("Structured-list contains an out-of-range scalar.");
                        }
                        element.Values[field.RequestKey] = single;
                    }
                }
                elements.Add(element);
            }
            return elements;
        }

        private static object BuildCollection(
            StructuredListSchema schema,
            Type listType,
            Type elementType,
            IReadOnlyList<StructuredListElementValue> elements)
        {
            var rawElements = new List<object>();
            foreach (var logical in elements)
            {
                var element = schema.ManagedElementFactory(elementType);
                if (element == null || !elementType.IsInstanceOfType(element))
                {
                    throw new InvalidOperationException("Structured-list managed-reference factory returned the wrong type.");
                }
                foreach (var field in schema.Fields)
                {
                    var member = ResolveMember(elementType, field.MemberName);
                    ValidateFieldMember(field, member);
                    object value;
                    if (!logical.Values.TryGetValue(field.RequestKey, out value))
                    {
                        throw new InvalidOperationException("Structured-list logical field is missing.");
                    }
                    SetMemberValue(element, member, value);
                }
                rawElements.Add(element);
            }
            if (schema.CollectionFactory != null)
            {
                var custom = schema.CollectionFactory(listType, elementType, rawElements);
                if (custom == null || !listType.IsInstanceOfType(custom))
                {
                    throw new InvalidOperationException("Structured-list collection factory returned the wrong type.");
                }
                return custom;
            }
            if (listType.IsArray)
            {
                var array = Array.CreateInstance(elementType, rawElements.Count);
                for (var index = 0; index < rawElements.Count; index++)
                {
                    array.SetValue(rawElements[index], index);
                }
                return array;
            }
            var list = Activator.CreateInstance(listType) as IList;
            if (list == null)
            {
                throw new InvalidOperationException("Structured-list collection type requires a registered factory.");
            }
            foreach (var raw in rawElements)
            {
                list.Add(raw);
            }
            return list;
        }

        private static void ValidateFieldMember(StructuredFieldSchema field, MemberInfo member)
        {
            var type = GetMemberType(member);
            if (field.Kind == StructuredValueKind.ObjectReference)
            {
                if (type != ResolveExactType(field.ObjectTypeName))
                {
                    throw new InvalidOperationException("Structured-list object-reference field type changed.");
                }
            }
            else if (field.Kind == StructuredValueKind.BoundedSingle && type != typeof(float))
            {
                throw new InvalidOperationException("Structured-list scalar field type changed.");
            }
        }

        private static bool ManagedConcreteSchemaIsValid(
            StructuredManagedConcreteTypeSchema concrete,
            bool allowCollections)
        {
            if (concrete == null
                || string.IsNullOrWhiteSpace(concrete.RuntimeTypeName)
                || string.IsNullOrWhiteSpace(concrete.SerializedFullTypeName)
                || string.IsNullOrWhiteSpace(concrete.AssemblyName)
                || string.IsNullOrWhiteSpace(concrete.AssemblyVersion)
                || concrete.AssemblyPublicKeyToken == null
                || string.IsNullOrWhiteSpace(concrete.AssemblySha256)
                || concrete.Fields == null
                || concrete.Collections == null
                || (!allowCollections && concrete.Collections.Count != 0)
                || !ManagedFieldsAreValid(concrete.Fields)
                || concrete.Fields.Select(field => field.CanonicalKey)
                    .Concat(concrete.Collections.Select(collection => collection.CanonicalKey))
                    .Distinct(StringComparer.Ordinal).Count()
                    != concrete.Fields.Count + concrete.Collections.Count
                || concrete.Collections.Any(collection => !ManagedCollectionSchemaIsValid(collection)))
            {
                return false;
            }
            return true;
        }

        private static bool ManagedFieldsAreValid(
            IReadOnlyList<StructuredManagedFieldSchema> fields)
        {
            return fields != null
                && !fields.Any(field => field == null
                    || string.IsNullOrWhiteSpace(field.CanonicalKey)
                    || !IsSafeDirectPropertyName(field.RelativePath)
                    || (field.Kind == StructuredManagedValueKind.BoundedSingle
                        && (!IsFinite(field.Minimum)
                            || !IsFinite(field.Maximum)
                            || field.Minimum > field.Maximum))
                    || (field.Kind == StructuredManagedValueKind.String
                        && field.MaximumStringLength <= 0)
                    || (field.Kind == StructuredManagedValueKind.ObjectReference
                        && string.IsNullOrWhiteSpace(field.ObjectTypeName)))
                && fields.Select(field => field.CanonicalKey)
                    .Distinct(StringComparer.Ordinal).Count() == fields.Count
                && fields.Select(field => field.RelativePath)
                    .Distinct(StringComparer.Ordinal).Count() == fields.Count;
        }

        private static bool ManagedCollectionSchemaIsValid(
            StructuredManagedCollectionSchema collection)
        {
            if (collection == null
                || string.IsNullOrWhiteSpace(collection.CanonicalKey)
                || !IsSafeRelativePropertyPath(collection.RelativePath)
                || collection.MaximumItems < 0
                || collection.MaximumItems > 4096
                || string.IsNullOrWhiteSpace(collection.DeclaredElementTypeName)
                || collection.TypedElementFields == null
                || collection.AllowedManagedElementTypes == null)
            {
                return false;
            }
            if (collection.Kind == StructuredManagedCollectionKind.TypedList)
            {
                return !string.IsNullOrWhiteSpace(collection.TypedElementRuntimeTypeName)
                    && !string.IsNullOrWhiteSpace(collection.TypedElementAssemblyName)
                    && !string.IsNullOrWhiteSpace(collection.TypedElementAssemblyVersion)
                    && collection.TypedElementAssemblyPublicKeyToken != null
                    && !string.IsNullOrWhiteSpace(collection.TypedElementAssemblySha256)
                    && ManagedFieldsAreValid(collection.TypedElementFields)
                    && collection.AllowedManagedElementTypes.Count == 0;
            }
            if (collection.Kind == StructuredManagedCollectionKind.ManagedReferenceList)
            {
                return string.IsNullOrEmpty(collection.TypedElementRuntimeTypeName)
                    && collection.TypedElementFields.Count == 0
                    && collection.AllowedManagedElementTypes.Count > 0
                    && collection.AllowedManagedElementTypes.All(concrete =>
                        ManagedConcreteSchemaIsValid(concrete, false))
                    && collection.AllowedManagedElementTypes.Select(concrete => concrete.RuntimeTypeName)
                        .Distinct(StringComparer.Ordinal).Count()
                        == collection.AllowedManagedElementTypes.Count
                    && collection.AllowedManagedElementTypes
                        .Select(concrete => concrete.SerializedFullTypeName)
                        .Distinct(StringComparer.Ordinal).Count()
                        == collection.AllowedManagedElementTypes.Count;
            }
            return false;
        }

        private static StructuredManagedCollectionValue ReadManagedReferenceCollection(
            SerializedProperty root,
            Type rootRuntimeType,
            StructuredManagedCollectionSchema collection)
        {
            var property = root.FindPropertyRelative(collection.RelativePath);
            if (property == null || !property.isArray || property.arraySize > collection.MaximumItems)
            {
                throw new InvalidOperationException("Managed-reference collection layout or bound changed.");
            }
            var declaredElementType = ResolveCollectionElementType(
                rootRuntimeType,
                collection.RelativePath
            );
            if ((declaredElementType.FullName ?? string.Empty) != collection.DeclaredElementTypeName)
            {
                throw new InvalidOperationException("Managed-reference collection declared element type changed.");
            }

            var result = new StructuredManagedCollectionValue
            {
                CanonicalKey = collection.CanonicalKey,
                Kind = collection.Kind
            };
            if (collection.Kind == StructuredManagedCollectionKind.TypedList)
            {
                if ((declaredElementType.FullName ?? string.Empty)
                        != collection.TypedElementRuntimeTypeName)
                {
                    throw new InvalidOperationException("Managed-reference typed-list element type changed.");
                }
                ValidateAssembly(
                    declaredElementType.Assembly,
                    collection.TypedElementAssemblyName,
                    collection.TypedElementAssemblyVersion,
                    collection.TypedElementAssemblyPublicKeyToken,
                    collection.TypedElementAssemblySha256
                );
                for (var index = 0; index < property.arraySize; index++)
                {
                    var element = property.GetArrayElementAtIndex(index);
                    if (element == null || element.propertyType != SerializedPropertyType.Generic)
                    {
                        throw new InvalidOperationException("Managed-reference typed-list element layout changed.");
                    }
                    if (collection.RequireExactElementFieldLayout)
                    {
                        ValidateExactElementFieldLayout(element, collection.TypedElementFields);
                    }
                    result.Elements.Add(new StructuredManagedElementValue
                    {
                        RuntimeTypeName = collection.TypedElementRuntimeTypeName,
                        SerializedFullTypeName = string.Empty,
                        Fields = collection.TypedElementFields
                            .Select(field => ReadManagedReferenceField(element, field))
                            .ToList()
                    });
                }
                return result;
            }

            for (var index = 0; index < property.arraySize; index++)
            {
                var element = property.GetArrayElementAtIndex(index);
                if (element == null || element.propertyType != SerializedPropertyType.ManagedReference)
                {
                    throw new InvalidOperationException("Managed-reference list element layout changed.");
                }
                var raw = element.managedReferenceValue;
                var serializedType = element.managedReferenceFullTypename ?? string.Empty;
                if (raw == null)
                {
                    throw new InvalidOperationException("Managed-reference list contains a null element.");
                }
                var runtimeType = raw.GetType();
                var matches = collection.AllowedManagedElementTypes.Where(concrete =>
                    concrete.RuntimeTypeName == (runtimeType.FullName ?? string.Empty)
                    && concrete.SerializedFullTypeName == serializedType).ToList();
                if (matches.Count != 1)
                {
                    throw new InvalidOperationException("Managed-reference list concrete type is not allowlisted.");
                }
                var concrete = matches[0];
                ValidateAssembly(
                    runtimeType.Assembly,
                    concrete.AssemblyName,
                    concrete.AssemblyVersion,
                    concrete.AssemblyPublicKeyToken,
                    concrete.AssemblySha256
                );
                if (concrete.RequireExactDirectFieldLayout)
                {
                    ValidateExactElementFieldLayout(element, concrete.Fields);
                }
                result.Elements.Add(new StructuredManagedElementValue
                {
                    RuntimeTypeName = concrete.RuntimeTypeName,
                    SerializedFullTypeName = serializedType,
                    Fields = concrete.Fields
                        .Select(field => ReadManagedReferenceField(element, field))
                        .ToList()
                });
            }
            return result;
        }

        private static void ValidateExactElementFieldLayout(
            SerializedProperty element,
            IReadOnlyList<StructuredManagedFieldSchema> fields)
        {
            var observed = new HashSet<string>(StringComparer.Ordinal);
            var iterator = element.Copy();
            var end = iterator.GetEndProperty();
            var enterChildren = true;
            while (iterator.NextVisible(enterChildren)
                && !SerializedProperty.EqualContents(iterator, end))
            {
                enterChildren = false;
                if (iterator.depth == element.depth + 1)
                {
                    observed.Add(iterator.name);
                }
            }
            var expected = new HashSet<string>(
                fields.Select(field => field.RelativePath),
                StringComparer.Ordinal
            );
            if (!observed.SetEquals(expected))
            {
                throw new InvalidOperationException("Managed-reference collection element field layout changed.");
            }
        }

        private static Type ResolveCollectionElementType(Type rootType, string relativePath)
        {
            var current = rootType;
            foreach (var segment in relativePath.Split('.'))
            {
                current = GetMemberType(ResolveManagedMember(current, segment));
            }
            var candidates = new List<Type>();
            if (current.IsArray)
            {
                candidates.Add(current.GetElementType());
            }
            if (current.IsGenericType && current.GetGenericTypeDefinition() == typeof(List<>))
            {
                candidates.Add(current.GetGenericArguments()[0]);
            }
            candidates.AddRange(current.GetInterfaces()
                .Where(item => item.IsGenericType
                    && item.GetGenericTypeDefinition() == typeof(IList<>))
                .Select(item => item.GetGenericArguments()[0]));
            candidates = candidates.Where(item => item != null).Distinct().ToList();
            if (candidates.Count != 1)
            {
                throw new InvalidOperationException("Managed-reference collection type is unsupported.");
            }
            return candidates[0];
        }

        private static MemberInfo ResolveManagedMember(Type type, string name)
        {
            const BindingFlags flags = BindingFlags.Instance
                | BindingFlags.Public
                | BindingFlags.NonPublic
                | BindingFlags.DeclaredOnly;
            for (var current = type; current != null; current = current.BaseType)
            {
                var field = current.GetField(name, flags);
                if (field != null)
                {
                    return field;
                }
                var property = current.GetProperty(name, flags);
                if (property != null && property.GetIndexParameters().Length == 0)
                {
                    return property;
                }
            }
            throw new InvalidOperationException("Managed-reference registered member is unavailable.");
        }

        private static bool IsSafeDirectPropertyName(string value)
        {
            return !string.IsNullOrWhiteSpace(value)
                && value.IndexOf('.') < 0
                && value.IndexOf('[') < 0
                && value.IndexOf(']') < 0
                && value.IndexOf('/') < 0;
        }

        private static bool IsSafeRelativePropertyPath(string value)
        {
            return !string.IsNullOrWhiteSpace(value)
                && value.Split('.').All(IsSafeDirectPropertyName);
        }

        private static string TopPropertySegment(string value)
        {
            return value.Split('.')[0];
        }

        private static void ValidateExactManagedReferenceLayout(
            SerializedProperty root,
            StructuredManagedConcreteTypeSchema concrete)
        {
            var observed = new HashSet<string>(StringComparer.Ordinal);
            var iterator = root.Copy();
            var end = iterator.GetEndProperty();
            var enterChildren = true;
            while (iterator.NextVisible(enterChildren)
                && !SerializedProperty.EqualContents(iterator, end))
            {
                enterChildren = false;
                if (iterator.depth == root.depth + 1)
                {
                    observed.Add(iterator.name);
                }
            }
            var expected = new HashSet<string>(
                concrete.Fields.Select(field => TopPropertySegment(field.RelativePath))
                    .Concat(concrete.Collections.Select(collection =>
                        TopPropertySegment(collection.RelativePath))),
                StringComparer.Ordinal
            );
            if (!observed.SetEquals(expected))
            {
                throw new InvalidOperationException("Managed-reference direct field layout changed.");
            }
        }

        private static StructuredManagedFieldValue ReadManagedReferenceField(
            SerializedProperty root,
            StructuredManagedFieldSchema field)
        {
            var property = root.FindPropertyRelative(field.RelativePath);
            if (property == null || property.depth != root.depth + 1)
            {
                throw new InvalidOperationException("Managed-reference registered field is unavailable.");
            }
            object raw;
            string canonical;
            switch (field.Kind)
            {
                case StructuredManagedValueKind.Boolean:
                    if (property.propertyType != SerializedPropertyType.Boolean)
                    {
                        throw new InvalidOperationException("Managed-reference boolean layout changed.");
                    }
                    raw = property.boolValue;
                    canonical = property.boolValue ? "true" : "false";
                    break;
                case StructuredManagedValueKind.Int32:
                    if (property.propertyType != SerializedPropertyType.Integer)
                    {
                        throw new InvalidOperationException("Managed-reference integer layout changed.");
                    }
                    raw = property.intValue;
                    canonical = property.intValue.ToString(CultureInfo.InvariantCulture);
                    break;
                case StructuredManagedValueKind.EnumInt32:
                    if (property.propertyType != SerializedPropertyType.Enum)
                    {
                        throw new InvalidOperationException("Managed-reference enum layout changed.");
                    }
                    raw = property.intValue;
                    canonical = property.intValue.ToString(CultureInfo.InvariantCulture);
                    break;
                case StructuredManagedValueKind.BoundedSingle:
                    if (property.propertyType != SerializedPropertyType.Float
                        || !IsFinite(property.floatValue)
                        || property.floatValue < field.Minimum
                        || property.floatValue > field.Maximum)
                    {
                        throw new InvalidOperationException("Managed-reference scalar layout or value changed.");
                    }
                    raw = property.floatValue;
                    canonical = CanonicalSingleBits(property.floatValue);
                    break;
                case StructuredManagedValueKind.String:
                    if (property.propertyType != SerializedPropertyType.String
                        || property.stringValue == null
                        || property.stringValue.Length > field.MaximumStringLength)
                    {
                        throw new InvalidOperationException("Managed-reference string layout or value changed.");
                    }
                    raw = property.stringValue;
                    canonical = property.stringValue;
                    break;
                case StructuredManagedValueKind.ObjectReference:
                    if (property.propertyType != SerializedPropertyType.ObjectReference)
                    {
                        throw new InvalidOperationException("Managed-reference object layout changed.");
                    }
                    var objectValue = property.objectReferenceValue;
                    if (objectValue == null)
                    {
                        if (!field.AllowNull)
                        {
                            throw new InvalidOperationException("Managed-reference required object is null.");
                        }
                        raw = null;
                        canonical = "null";
                        break;
                    }
                    var requiredType = ResolveExactType(field.ObjectTypeName);
                    if (!requiredType.IsInstanceOfType(objectValue))
                    {
                        throw new InvalidOperationException("Managed-reference object type changed.");
                    }
                    raw = objectValue;
                    canonical = StableGlobalObjectId(objectValue, "managed-reference object");
                    break;
                default:
                    throw new InvalidOperationException("Managed-reference field kind is unsupported.");
            }
            return new StructuredManagedFieldValue
            {
                CanonicalKey = field.CanonicalKey,
                Kind = field.Kind,
                RawValue = raw,
                CanonicalValue = canonical
            };
        }

        private static string ComputeManagedReferenceDigest(StructuredManagedReferenceReadPlan plan)
        {
            var value = new StringBuilder();
            AppendCanonicalField(value, plan.Schema.DigestSchema);
            AppendCanonicalField(value, plan.Schema.Id);
            AppendCanonicalField(value, plan.ComponentGlobalId);
            AppendCanonicalField(value, plan.ManagedReferenceFullTypeName);
            AppendCanonicalField(value, plan.Fields.Count.ToString(CultureInfo.InvariantCulture));
            foreach (var field in plan.Fields)
            {
                AppendCanonicalField(value, field.CanonicalKey);
                AppendCanonicalField(value, field.Kind.ToString());
                AppendCanonicalField(value, field.CanonicalValue);
            }
            AppendCanonicalField(value, plan.Collections.Count.ToString(CultureInfo.InvariantCulture));
            foreach (var collection in plan.Collections)
            {
                AppendCanonicalField(value, collection.CanonicalKey);
                AppendCanonicalField(value, collection.Kind.ToString());
                AppendCanonicalField(
                    value,
                    collection.Elements.Count.ToString(CultureInfo.InvariantCulture)
                );
                foreach (var element in collection.Elements)
                {
                    AppendCanonicalField(value, element.RuntimeTypeName);
                    AppendCanonicalField(value, element.SerializedFullTypeName);
                    AppendCanonicalField(
                        value,
                        element.Fields.Count.ToString(CultureInfo.InvariantCulture)
                    );
                    foreach (var field in element.Fields)
                    {
                        AppendCanonicalField(value, field.CanonicalKey);
                        AppendCanonicalField(value, field.Kind.ToString());
                        AppendCanonicalField(value, field.CanonicalValue);
                    }
                }
            }
            using (var sha256 = SHA256.Create())
            {
                return BitConverter.ToString(sha256.ComputeHash(Encoding.UTF8.GetBytes(value.ToString())))
                    .Replace("-", string.Empty)
                    .ToLowerInvariant();
            }
        }

        private static string ComputeManagedSchemaFingerprint(
            StructuredManagedReferenceSchema schema)
        {
            var value = new StringBuilder();
            AppendCanonicalField(value, "vrcforge.structured_managed_schema.v1");
            AppendCanonicalField(value, schema.Id);
            AppendCanonicalField(value, schema.DigestSchema);
            AppendCanonicalField(value, schema.RootComponentTypeName);
            AppendCanonicalField(value, schema.RootAssemblyName);
            AppendCanonicalField(value, schema.RootAssemblyVersion);
            AppendCanonicalField(value, schema.RootAssemblyPublicKeyToken);
            AppendCanonicalField(value, schema.RootAssemblySha256);
            AppendCanonicalField(value, schema.MemberName);
            AppendCanonicalField(value, schema.AllowNull ? "true" : "false");
            AppendCanonicalField(
                value,
                schema.AllowedConcreteTypes.Count.ToString(CultureInfo.InvariantCulture)
            );
            foreach (var concrete in schema.AllowedConcreteTypes)
            {
                AppendManagedConcreteFingerprint(value, concrete);
            }
            using (var sha256 = SHA256.Create())
            {
                return BitConverter.ToString(sha256.ComputeHash(Encoding.UTF8.GetBytes(value.ToString())))
                    .Replace("-", string.Empty)
                    .ToLowerInvariant();
            }
        }

        private static void AppendManagedConcreteFingerprint(
            StringBuilder value,
            StructuredManagedConcreteTypeSchema concrete)
        {
            AppendCanonicalField(value, concrete.RuntimeTypeName);
            AppendCanonicalField(value, concrete.SerializedFullTypeName);
            AppendCanonicalField(value, concrete.AssemblyName);
            AppendCanonicalField(value, concrete.AssemblyVersion);
            AppendCanonicalField(value, concrete.AssemblyPublicKeyToken);
            AppendCanonicalField(value, concrete.AssemblySha256);
            AppendCanonicalField(value, concrete.RequireExactDirectFieldLayout ? "true" : "false");
            AppendManagedFieldsFingerprint(value, concrete.Fields);
            AppendCanonicalField(
                value,
                concrete.Collections.Count.ToString(CultureInfo.InvariantCulture)
            );
            foreach (var collection in concrete.Collections)
            {
                AppendCanonicalField(value, collection.CanonicalKey);
                AppendCanonicalField(value, collection.RelativePath);
                AppendCanonicalField(value, collection.Kind.ToString());
                AppendCanonicalField(
                    value,
                    collection.MaximumItems.ToString(CultureInfo.InvariantCulture)
                );
                AppendCanonicalField(value, collection.DeclaredElementTypeName);
                AppendCanonicalField(value, collection.TypedElementRuntimeTypeName);
                AppendCanonicalField(value, collection.TypedElementAssemblyName);
                AppendCanonicalField(value, collection.TypedElementAssemblyVersion);
                AppendCanonicalField(value, collection.TypedElementAssemblyPublicKeyToken);
                AppendCanonicalField(value, collection.TypedElementAssemblySha256);
                AppendCanonicalField(
                    value,
                    collection.RequireExactElementFieldLayout ? "true" : "false"
                );
                AppendManagedFieldsFingerprint(value, collection.TypedElementFields);
                AppendCanonicalField(
                    value,
                    collection.AllowedManagedElementTypes.Count.ToString(
                        CultureInfo.InvariantCulture
                    )
                );
                foreach (var managedElement in collection.AllowedManagedElementTypes)
                {
                    AppendManagedConcreteFingerprint(value, managedElement);
                }
            }
        }

        private static void AppendManagedFieldsFingerprint(
            StringBuilder value,
            IReadOnlyList<StructuredManagedFieldSchema> fields)
        {
            AppendCanonicalField(value, fields.Count.ToString(CultureInfo.InvariantCulture));
            foreach (var field in fields)
            {
                AppendCanonicalField(value, field.CanonicalKey);
                AppendCanonicalField(value, field.RelativePath);
                AppendCanonicalField(value, field.Kind.ToString());
                AppendCanonicalField(value, CanonicalSingleBits(field.Minimum));
                AppendCanonicalField(value, CanonicalSingleBits(field.Maximum));
                AppendCanonicalField(
                    value,
                    field.MaximumStringLength.ToString(CultureInfo.InvariantCulture)
                );
                AppendCanonicalField(value, field.ObjectTypeName);
                AppendCanonicalField(value, field.AllowNull ? "true" : "false");
            }
        }

        private static string StableGlobalObjectId(UnityEngine.Object target, string label)
        {
            var globalId = GlobalObjectId.GetGlobalObjectIdSlow(target);
            if (globalId.identifierType == 0)
            {
                throw new InvalidOperationException("Stable " + label + " identity is unavailable.");
            }
            return globalId.ToString();
        }

        private static string CanonicalSingleBits(float value)
        {
            var bytes = BitConverter.GetBytes(value);
            if (BitConverter.IsLittleEndian)
            {
                Array.Reverse(bytes);
            }
            return BitConverter.ToString(bytes).Replace("-", string.Empty).ToLowerInvariant();
        }

        private static void AppendCanonicalField(StringBuilder target, string value)
        {
            var text = value ?? string.Empty;
            target.Append(text.Length.ToString(CultureInfo.InvariantCulture));
            target.Append(':');
            target.Append(text);
        }

        private static StructuredListBinding ResolveBinding(Component component, StructuredListSchema schema)
        {
            if (component == null || !schema.ComponentTypeNames.Contains(component.GetType().FullName ?? string.Empty))
            {
                throw new InvalidOperationException("Component type is not registered for this structured-list schema.");
            }
            ValidateAssembly(
                component.GetType().Assembly,
                schema.ComponentAssemblyName,
                schema.ComponentAssemblyVersion,
                schema.ComponentAssemblyPublicKeyToken,
                schema.ComponentAssemblySha256
            );
            var listType = ResolveExactType(schema.ListTypeName);
            var elementType = ResolveExactType(schema.ElementTypeName);
            ValidateAssembly(
                listType.Assembly,
                schema.AssemblyName,
                schema.AssemblyVersion,
                schema.AssemblyPublicKeyToken,
                schema.AssemblySha256
            );
            if (!ReferenceEquals(listType.Assembly, elementType.Assembly))
            {
                throw new InvalidOperationException("Structured-list types came from different assemblies.");
            }
            var member = ResolveMember(component.GetType(), schema.ListMemberName);
            if (GetMemberType(member) != listType)
            {
                throw new InvalidOperationException("Structured-list member type changed.");
            }
            return new StructuredListBinding
            {
                ListMember = member,
                ListType = listType,
                ElementType = elementType
            };
        }

        private static void ValidateAssembly(
            Assembly assembly,
            string expectedName,
            string expectedVersion,
            string expectedPublicKeyToken,
            string expectedSha256)
        {
            var name = assembly.GetName();
            var tokenBytes = name.GetPublicKeyToken() ?? Array.Empty<byte>();
            var token = string.Concat(tokenBytes.Select(value => value.ToString("x2", CultureInfo.InvariantCulture)));
            if (name.Name != expectedName
                || (name.Version?.ToString() ?? string.Empty) != expectedVersion
                || token != expectedPublicKeyToken)
            {
                throw new InvalidOperationException("Structured-list compatibility assembly identity is unsupported.");
            }
            var location = assembly.Location;
            if (string.IsNullOrWhiteSpace(location) || !File.Exists(location))
            {
                throw new InvalidOperationException("Structured-list compatibility assembly file is unavailable.");
            }
            string digest;
            using (var sha256 = SHA256.Create())
            using (var stream = new FileStream(location, FileMode.Open, FileAccess.Read, FileShare.Read))
            {
                digest = BitConverter.ToString(sha256.ComputeHash(stream)).Replace("-", string.Empty)
                    .ToLowerInvariant();
            }
            if (digest != expectedSha256)
            {
                throw new InvalidOperationException("Structured-list compatibility assembly hash is unsupported.");
            }
        }

        private static StructuredListSchema ResolveSchema(string schemaId)
        {
            StructuredListSchema schema;
            if (string.IsNullOrWhiteSpace(schemaId) || !Schemas.TryGetValue(schemaId, out schema))
            {
                throw new InvalidOperationException("Structured-list schema is not registered.");
            }
            return schema;
        }

        private static Type ResolveExactType(string fullName)
        {
            var matches = AppDomain.CurrentDomain.GetAssemblies()
                .Select(assembly => assembly.GetType(fullName, false, false))
                .Where(type => type != null)
                .Distinct()
                .ToList();
            if (matches.Count != 1)
            {
                throw new InvalidOperationException("Structured-list compatibility type is missing or ambiguous.");
            }
            return matches[0];
        }

        private static MemberInfo ResolveMember(Type type, string name)
        {
            const BindingFlags flags = BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic;
            var field = type.GetField(name, flags);
            if (field != null)
            {
                return field;
            }
            var property = type.GetProperty(name, flags);
            if (property == null || property.GetIndexParameters().Length != 0)
            {
                throw new InvalidOperationException("Structured-list registered member is unavailable.");
            }
            return property;
        }

        private static Type GetMemberType(MemberInfo member)
        {
            return member is FieldInfo field ? field.FieldType : ((PropertyInfo)member).PropertyType;
        }

        private static object GetMemberValue(object target, MemberInfo member)
        {
            return member is FieldInfo field
                ? field.GetValue(target)
                : ((PropertyInfo)member).GetValue(target, null);
        }

        private static void SetMemberValue(object target, MemberInfo member, object value)
        {
            if (member is FieldInfo field)
            {
                field.SetValue(target, value);
                return;
            }
            var property = (PropertyInfo)member;
            if (!property.CanWrite)
            {
                throw new InvalidOperationException("Structured-list registered member is read-only.");
            }
            property.SetValue(target, value, null);
        }

        private static StructuredListElementValue CloneElement(StructuredListElementValue source)
        {
            return new StructuredListElementValue
            {
                Values = new Dictionary<string, object>(source.Values, StringComparer.Ordinal)
            };
        }

        private static bool IsFinite(float value)
        {
            return !float.IsNaN(value) && !float.IsInfinity(value);
        }

        private sealed class StructuredListBinding
        {
            internal MemberInfo ListMember;
            internal Type ListType;
            internal Type ElementType;
        }
    }
}
