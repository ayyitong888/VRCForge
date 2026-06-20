using System;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;
using UnityEngine;

namespace VRCForge.Editor
{
    public interface IShaderMaterialAdapter
    {
        string ShaderFamily { get; }
        bool Supports(Material material);
        Dictionary<string, MaterialPropertyValue> ReadSupportedProperties(Material material);
        bool CanWrite(string semanticProperty);
        bool TryValidateChange(Material material, string semanticProperty, object value, out object clampedValue, out string warning);
        bool TryApplyChange(Material material, string semanticProperty, object value, out object previousValue, out object appliedValue, out string warning);
    }

    public static class ShaderAdapterRegistry
    {
        private static readonly List<IShaderMaterialAdapter> Adapters = new List<IShaderMaterialAdapter>
        {
            new LilToonShaderAdapter(),
            new PoiyomiShaderAdapter(),
            new GenericShaderAdapter()
        };

        public static IShaderMaterialAdapter GetAdapter(Material material)
        {
            return Adapters.FirstOrDefault(adapter => adapter.Supports(material));
        }

        public static string GetShaderFamily(Material material)
        {
            return GetAdapter(material)?.ShaderFamily ?? "Unsupported";
        }

        public static bool IsSupportedSemanticProperty(string semanticProperty)
        {
            return ShaderMaterialAdapterBase.KnownSemanticProperties.Contains(semanticProperty ?? "");
        }
    }

    public abstract class ShaderMaterialAdapterBase : IShaderMaterialAdapter
    {
        public static readonly HashSet<string> KnownSemanticProperties = new HashSet<string>(StringComparer.Ordinal)
        {
            "base_color",
            "shade_color",
            "shadow_strength",
            "shadow_softness",
            "smoothness",
            "specular_strength",
            "rim_color",
            "rim_strength",
            "emission_color",
            "emission_strength",
            "matcap_strength",
            "outline_color",
            "outline_width",
            "normal_strength"
        };

        private readonly Dictionary<string, SemanticPropertyMapping> mappings;

        protected ShaderMaterialAdapterBase(string shaderFamily, Dictionary<string, SemanticPropertyMapping> mappings)
        {
            ShaderFamily = shaderFamily;
            this.mappings = mappings;
        }

        public string ShaderFamily { get; }

        public abstract bool Supports(Material material);

        public Dictionary<string, MaterialPropertyValue> ReadSupportedProperties(Material material)
        {
            var values = new Dictionary<string, MaterialPropertyValue>();
            if (material == null)
            {
                return values;
            }

            foreach (var pair in mappings)
            {
                if (!TryResolveProperty(material, pair.Value, out var propertyName))
                {
                    continue;
                }

                values[pair.Key] = new MaterialPropertyValue
                {
                    type = pair.Value.kind == SemanticPropertyKind.Color ? "color" : "float",
                    value = pair.Value.kind == SemanticPropertyKind.Color
                        ? ColorToHex(material.GetColor(propertyName))
                        : (object)material.GetFloat(propertyName),
                    writable = true
                };
            }

            return values;
        }

        public bool CanWrite(string semanticProperty)
        {
            return mappings.ContainsKey(semanticProperty ?? "");
        }

        public bool TryValidateChange(Material material, string semanticProperty, object value, out object clampedValue, out string warning)
        {
            clampedValue = null;
            warning = "";

            if (material == null)
            {
                warning = "Material is missing.";
                return false;
            }

            if (!mappings.TryGetValue(semanticProperty ?? "", out var mapping))
            {
                warning = $"Semantic property is not supported by {ShaderFamily}: {semanticProperty}";
                return false;
            }

            if (!TryResolveProperty(material, mapping, out _))
            {
                warning = $"Material does not expose a writable property for semantic property: {semanticProperty}";
                return false;
            }

            if (!TryNormalizeValue(mapping, value, out clampedValue))
            {
                warning = $"Invalid value for semantic property: {semanticProperty}";
                return false;
            }

            return true;
        }

        public bool TryApplyChange(Material material, string semanticProperty, object value, out object previousValue, out object appliedValue, out string warning)
        {
            previousValue = null;
            appliedValue = null;
            warning = "";

            if (!mappings.TryGetValue(semanticProperty ?? "", out var mapping))
            {
                warning = $"Semantic property is not supported by {ShaderFamily}: {semanticProperty}";
                return false;
            }

            if (!TryResolveProperty(material, mapping, out var propertyName))
            {
                warning = $"Material does not expose a writable property for semantic property: {semanticProperty}";
                return false;
            }

            if (!TryNormalizeValue(mapping, value, out var clampedValue))
            {
                warning = $"Invalid value for semantic property: {semanticProperty}";
                return false;
            }

            if (mapping.kind == SemanticPropertyKind.Color)
            {
                previousValue = ColorToHex(material.GetColor(propertyName));
                var color = (Color)clampedValue;
                material.SetColor(propertyName, color);
                appliedValue = ColorToHex(color);
            }
            else
            {
                previousValue = material.GetFloat(propertyName);
                var number = (float)clampedValue;
                material.SetFloat(propertyName, number);
                appliedValue = number;
            }

            return true;
        }

        private static bool TryResolveProperty(Material material, SemanticPropertyMapping mapping, out string propertyName)
        {
            foreach (var alias in mapping.aliases)
            {
                if (material.HasProperty(alias))
                {
                    propertyName = alias;
                    return true;
                }
            }

            propertyName = "";
            return false;
        }

        private static bool TryNormalizeValue(SemanticPropertyMapping mapping, object value, out object normalized)
        {
            normalized = null;
            if (mapping.kind == SemanticPropertyKind.Color)
            {
                if (value is Color color)
                {
                    normalized = color;
                    return true;
                }

                var text = Convert.ToString(value, CultureInfo.InvariantCulture);
                if (string.IsNullOrWhiteSpace(text))
                {
                    return false;
                }

                if (!text.StartsWith("#", StringComparison.Ordinal))
                {
                    text = "#" + text;
                }

                if (!ColorUtility.TryParseHtmlString(text, out color))
                {
                    return false;
                }

                normalized = color;
                return true;
            }

            float number;
            if (value is float floatValue)
            {
                number = floatValue;
            }
            else if (value is double doubleValue)
            {
                number = (float)doubleValue;
            }
            else if (value is int intValue)
            {
                number = intValue;
            }
            else if (!float.TryParse(Convert.ToString(value, CultureInfo.InvariantCulture), NumberStyles.Float, CultureInfo.InvariantCulture, out number))
            {
                return false;
            }

            if (float.IsNaN(number) || float.IsInfinity(number))
            {
                return false;
            }

            normalized = Mathf.Clamp(number, mapping.min, mapping.max);
            return true;
        }

        private static string ColorToHex(Color color)
        {
            return "#" + ColorUtility.ToHtmlStringRGBA(color);
        }
    }

    public sealed class LilToonShaderAdapter : ShaderMaterialAdapterBase
    {
        public LilToonShaderAdapter()
            : base("lilToon", new Dictionary<string, SemanticPropertyMapping>
            {
                ["base_color"] = SemanticPropertyMapping.Color("_Color", "_MainColor"),
                ["shade_color"] = SemanticPropertyMapping.Color("_ShadeColor", "_ShadowColor"),
                ["shadow_strength"] = SemanticPropertyMapping.Float(0f, 1f, "_ShadowStrength", "_Shadow2ndColorTex_Power"),
                ["shadow_softness"] = SemanticPropertyMapping.Float(0f, 1f, "_ShadowBlur", "_ShadowBorder"),
                ["smoothness"] = SemanticPropertyMapping.Float(0f, 1f, "_Smoothness", "_Glossiness"),
                ["specular_strength"] = SemanticPropertyMapping.Float(0f, 1f, "_SpecularStrength", "_SpecularPower"),
                ["rim_color"] = SemanticPropertyMapping.Color("_RimColor"),
                ["rim_strength"] = SemanticPropertyMapping.Float(0f, 1f, "_RimStrength", "_RimPower"),
                ["emission_color"] = SemanticPropertyMapping.Color("_EmissionColor", "_EmissionColor1"),
                ["emission_strength"] = SemanticPropertyMapping.Float(0f, 2f, "_EmissionStrength", "_EmissionBlend"),
                ["matcap_strength"] = SemanticPropertyMapping.Float(0f, 1f, "_MatCapBlend", "_MatCapEnableLighting"),
                ["outline_color"] = SemanticPropertyMapping.Color("_OutlineColor"),
                ["outline_width"] = SemanticPropertyMapping.Float(0f, 0.25f, "_OutlineWidth"),
                ["normal_strength"] = SemanticPropertyMapping.Float(0f, 2f, "_BumpScale")
            })
        {
        }

        public override bool Supports(Material material)
        {
            var shaderName = material != null && material.shader != null ? material.shader.name.ToLowerInvariant() : "";
            return shaderName.Contains("liltoon") || shaderName.Contains("lil/toon");
        }
    }

    public sealed class PoiyomiShaderAdapter : ShaderMaterialAdapterBase
    {
        public PoiyomiShaderAdapter()
            : base("Poiyomi", new Dictionary<string, SemanticPropertyMapping>
            {
                ["base_color"] = SemanticPropertyMapping.Color("_Color", "_MainColor", "_MainTexColor"),
                ["shade_color"] = SemanticPropertyMapping.Color("_ShadeColor", "_ShadowColor", "_ShadowTint"),
                ["shadow_strength"] = SemanticPropertyMapping.Float(0f, 1f, "_ShadowStrength", "_ShadowIntensity"),
                ["shadow_softness"] = SemanticPropertyMapping.Float(0f, 1f, "_ShadowSoftness", "_ShadowBlur"),
                ["smoothness"] = SemanticPropertyMapping.Float(0f, 1f, "_Smoothness", "_Glossiness"),
                ["specular_strength"] = SemanticPropertyMapping.Float(0f, 1f, "_SpecularStrength", "_SpecularIntensity"),
                ["rim_color"] = SemanticPropertyMapping.Color("_RimColor"),
                ["rim_strength"] = SemanticPropertyMapping.Float(0f, 1f, "_RimStrength", "_RimIntensity"),
                ["emission_color"] = SemanticPropertyMapping.Color("_EmissionColor", "_EmissionColor0"),
                ["emission_strength"] = SemanticPropertyMapping.Float(0f, 2f, "_EmissionStrength", "_EmissionIntensity"),
                ["matcap_strength"] = SemanticPropertyMapping.Float(0f, 1f, "_MatcapIntensity", "_MatcapStrength"),
                ["outline_color"] = SemanticPropertyMapping.Color("_OutlineColor"),
                ["outline_width"] = SemanticPropertyMapping.Float(0f, 0.25f, "_OutlineWidth"),
                ["normal_strength"] = SemanticPropertyMapping.Float(0f, 2f, "_BumpScale", "_NormalStrength")
            })
        {
        }

        public override bool Supports(Material material)
        {
            var shaderName = material != null && material.shader != null ? material.shader.name.ToLowerInvariant() : "";
            return shaderName.Contains("poiyomi");
        }
    }

    public sealed class GenericShaderAdapter : ShaderMaterialAdapterBase
    {
        public GenericShaderAdapter()
            : base("Generic", new Dictionary<string, SemanticPropertyMapping>
            {
                ["base_color"] = SemanticPropertyMapping.Color("_Color", "_BaseColor", "_MainColor", "_MainTexColor"),
                ["shade_color"] = SemanticPropertyMapping.Color("_ShadeColor", "_ShadowColor", "_ShadowTint"),
                ["shadow_strength"] = SemanticPropertyMapping.Float(0f, 1f, "_ShadowStrength", "_ShadowIntensity"),
                ["shadow_softness"] = SemanticPropertyMapping.Float(0f, 1f, "_ShadowSoftness", "_ShadowBlur", "_ShadowBorder"),
                ["smoothness"] = SemanticPropertyMapping.Float(0f, 1f, "_Smoothness", "_Glossiness"),
                ["specular_strength"] = SemanticPropertyMapping.Float(0f, 1f, "_SpecularStrength", "_SpecularIntensity"),
                ["rim_color"] = SemanticPropertyMapping.Color("_RimColor"),
                ["rim_strength"] = SemanticPropertyMapping.Float(0f, 1f, "_RimStrength", "_RimIntensity", "_RimPower"),
                ["emission_color"] = SemanticPropertyMapping.Color("_EmissionColor", "_EmissionColor0", "_EmissionColor1"),
                ["emission_strength"] = SemanticPropertyMapping.Float(0f, 2f, "_EmissionStrength", "_EmissionIntensity"),
                ["matcap_strength"] = SemanticPropertyMapping.Float(0f, 1f, "_MatcapIntensity", "_MatcapStrength", "_MatCapBlend"),
                ["outline_color"] = SemanticPropertyMapping.Color("_OutlineColor"),
                ["outline_width"] = SemanticPropertyMapping.Float(0f, 0.25f, "_OutlineWidth", "_OutlineWidthMask"),
                ["normal_strength"] = SemanticPropertyMapping.Float(0f, 2f, "_BumpScale", "_NormalStrength")
            })
        {
        }

        public override bool Supports(Material material)
        {
            if (material == null || material.shader == null)
            {
                return false;
            }

            var shaderName = material.shader.name.ToLowerInvariant();
            if (shaderName.Contains("liltoon") || shaderName.Contains("lil/toon") || shaderName.Contains("poiyomi"))
            {
                return false;
            }

            return ReadSupportedProperties(material).Count > 0;
        }
    }

    public enum SemanticPropertyKind
    {
        Float,
        Color
    }

    public sealed class SemanticPropertyMapping
    {
        public SemanticPropertyKind kind;
        public float min;
        public float max;
        public string[] aliases;

        public static SemanticPropertyMapping Float(float min, float max, params string[] aliases)
        {
            return new SemanticPropertyMapping
            {
                kind = SemanticPropertyKind.Float,
                min = min,
                max = max,
                aliases = aliases
            };
        }

        public static SemanticPropertyMapping Color(params string[] aliases)
        {
            return new SemanticPropertyMapping
            {
                kind = SemanticPropertyKind.Color,
                min = 0f,
                max = 1f,
                aliases = aliases
            };
        }
    }
}
