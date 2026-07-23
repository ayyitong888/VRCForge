using System;
using System.Globalization;
using System.Security.Cryptography;
using System.Text;
using UnityEditor;
using UnityEngine;

namespace VRCForge.Editor
{
    internal static class RendererComponentIdentity
    {
        internal static RendererComponentIdentityEvidence Create(Renderer renderer)
        {
            if (renderer == null
                || !renderer.gameObject.scene.IsValid()
                || !renderer.gameObject.scene.isLoaded
                || EditorUtility.IsPersistent(renderer))
            {
                throw new InvalidOperationException("Renderer component identity is unavailable.");
            }

            var scenePath = (renderer.gameObject.scene.path ?? string.Empty).Replace("\\", "/");
            var sceneGuid = string.IsNullOrWhiteSpace(scenePath)
                ? string.Empty
                : NormalizeGuid(AssetDatabase.AssetPathToGUID(scenePath));
            var componentType = renderer.GetType().FullName ?? renderer.GetType().Name;
            var sameTypeComponents = renderer.gameObject.GetComponents(renderer.GetType());
            var componentIndex = Array.FindIndex(sameTypeComponents, item => ReferenceEquals(item, renderer));
            if (componentIndex < 0)
            {
                throw new InvalidOperationException("Renderer component index is unavailable.");
            }

            var rendererPath = GetTransformPath(renderer.transform);
            var identityInput = new StringBuilder();
            AppendField(identityInput, "vrcforge.renderer_component.v1");
            AppendField(identityInput, string.IsNullOrWhiteSpace(sceneGuid)
                ? "handle:" + renderer.gameObject.scene.handle.ToString(CultureInfo.InvariantCulture)
                : "guid:" + sceneGuid);
            var globalObjectId = GlobalObjectId.GetGlobalObjectIdSlow(renderer);
            var objectIdentity = globalObjectId.identifierType == 0
                ? "instance:" + renderer.GetInstanceID().ToString(CultureInfo.InvariantCulture)
                : "global:" + globalObjectId.ToString();
            AppendField(identityInput, objectIdentity);
            AppendField(identityInput, rendererPath);
            AppendField(identityInput, componentType);
            AppendField(identityInput, componentIndex.ToString(CultureInfo.InvariantCulture));
            string componentId;
            using (var sha256 = SHA256.Create())
            {
                componentId = BitConverter.ToString(sha256.ComputeHash(Encoding.UTF8.GetBytes(identityInput.ToString())))
                    .Replace("-", string.Empty)
                    .ToLowerInvariant();
            }

            return new RendererComponentIdentityEvidence
            {
                renderer = renderer,
                scenePath = scenePath,
                sceneGuid = sceneGuid,
                sceneHandle = renderer.gameObject.scene.handle,
                rendererPath = rendererPath,
                componentId = componentId,
                componentType = componentType,
                componentIndex = componentIndex
            };
        }

        private static string NormalizeGuid(string value)
        {
            var normalized = (value ?? string.Empty).Trim().ToLowerInvariant();
            if (normalized.Length != 32 || Array.Exists(normalized.ToCharArray(), character => !Uri.IsHexDigit(character)))
            {
                throw new InvalidOperationException("Renderer scene identity is unavailable.");
            }
            return normalized;
        }

        private static string GetTransformPath(Transform transform)
        {
            var current = transform;
            var path = current != null ? current.name : string.Empty;
            while (current != null && current.parent != null)
            {
                current = current.parent;
                path = current.name + "/" + path;
            }
            return path;
        }

        private static void AppendField(StringBuilder target, string value)
        {
            var safeValue = value ?? string.Empty;
            target.Append(safeValue.Length).Append(':').Append(safeValue);
        }
    }

    internal sealed class RendererComponentIdentityEvidence
    {
        internal Renderer renderer;
        internal string scenePath = string.Empty;
        internal string sceneGuid = string.Empty;
        internal int sceneHandle;
        internal string rendererPath = string.Empty;
        internal string componentId = string.Empty;
        internal string componentType = string.Empty;
        internal int componentIndex;
    }

    internal static class MaterialInventoryIdentity
    {
        internal static string CreateRendererId(string rendererPath)
        {
            return StableId("renderer", rendererPath);
        }

        internal static string CreateMaterialId(
            string rendererPath,
            int slotIndex,
            string materialName,
            string shaderName)
        {
            return StableId(
                "mat",
                $"{rendererPath}|{slotIndex}|{materialName ?? string.Empty}|{shaderName ?? string.Empty}"
            );
        }

        private static string StableId(string prefix, string value)
        {
            var normalized = (value ?? string.Empty).Replace("\\", "/").Trim().Trim('/');
            using (var sha1 = SHA1.Create())
            {
                var bytes = sha1.ComputeHash(Encoding.UTF8.GetBytes(normalized));
                var hex = BitConverter.ToString(bytes).Replace("-", string.Empty).ToLowerInvariant();
                return $"{prefix}_{hex.Substring(0, 16)}";
            }
        }
    }
}
