using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Security.Cryptography;
using System.Text;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Tools;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEngine;

namespace VRCForge.Editor
{
    [McpForUnityTool(
        name: "vrc_set_material_shader",
        Description = "Preview or assign one persistent project material to a named shader through the supervised project-write lane."
    )]
    public static class MaterialShaderTool
    {
        private const string ResultSchema = "vrcforge.material_shader_assignment.v1";
        private const int MaxDependencyCandidates = 4096;
        private const int MaxImpactItems = 128;

        public static object HandleCommand(JObject @params)
        {
            try
            {
                var shaderName = (@params?["shaderName"]?.ToString() ?? @params?["targetShader"]?.ToString() ?? string.Empty).Trim();
                var shaderAssetPath = NormalizeOptionalAssetPath(@params?["shaderAssetPath"]?.ToString(), allowPackages: true);
                var rendererPath = (@params?["rendererPath"]?.ToString() ?? string.Empty).Trim();
                var rendererComponentId = NormalizeHex(@params?["rendererComponentId"]?.ToString(), 64, allowEmpty: true);
                var materialAssetPath = NormalizeOptionalAssetPath(@params?["materialAssetPath"]?.ToString(), allowPackages: false);
                var expectedBeforeShader = (@params?["expectedBeforeShader"]?.ToString() ?? string.Empty).Trim();
                var expectedBeforeShaderAssetPath = NormalizeOptionalAssetPath(@params?["expectedBeforeShaderAssetPath"]?.ToString(), allowPackages: true);
                var expectedBeforeShaderAssetGuid = NormalizeHex(@params?["expectedBeforeShaderAssetGuid"]?.ToString(), 32, allowEmpty: true);
                var expectedMaterialAssetPath = NormalizeOptionalAssetPath(@params?["expectedMaterialAssetPath"]?.ToString(), allowPackages: false);
                var expectedMaterialAssetGuid = NormalizeHex(@params?["expectedMaterialAssetGuid"]?.ToString(), 32, allowEmpty: true);
                var expectedMaterialFileDigest = NormalizeHex(@params?["expectedMaterialFileDigest"]?.ToString(), 64, allowEmpty: true);
                var expectedSharedImpactDigest = NormalizeHex(@params?["expectedSharedImpactDigest"]?.ToString(), 64, allowEmpty: true);
                var expectedRendererScenePath = NormalizeOptionalAssetPath(@params?["expectedRendererScenePath"]?.ToString(), allowPackages: false);
                var expectedRendererSceneGuid = NormalizeHex(@params?["expectedRendererSceneGuid"]?.ToString(), 32, allowEmpty: true);
                var expectedRendererSceneHandle = @params?["expectedRendererSceneHandle"]?.Value<int?>() ?? -1;
                var expectedRendererComponentId = NormalizeHex(@params?["expectedRendererComponentId"]?.ToString(), 64, allowEmpty: true);
                var expectedRendererComponentType = (@params?["expectedRendererComponentType"]?.ToString() ?? string.Empty).Trim();
                var expectedRendererComponentIndex = @params?["expectedRendererComponentIndex"]?.Value<int?>() ?? -1;
                var expectedShaderAssetPath = NormalizeOptionalAssetPath(@params?["expectedShaderAssetPath"]?.ToString(), allowPackages: true);
                var expectedShaderAssetGuid = NormalizeHex(@params?["expectedShaderAssetGuid"]?.ToString(), 32, allowEmpty: true);
                var expectedProjectPath = (@params?["expectedProjectPath"]?.ToString() ?? string.Empty).Trim();
                var slotIndex = @params?["slotIndex"]?.Value<int?>() ?? 0;
                var preview = @params?["preview"]?.Value<bool?>() ?? false;
                var saveAssets = @params?["saveAssets"]?.Value<bool?>() ?? true;

                if (string.IsNullOrWhiteSpace(shaderName))
                {
                    return new ErrorResponse("shaderName is required.");
                }
                if (!MatchesCurrentProject(expectedProjectPath))
                {
                    return new ErrorResponse("The selected Unity project does not match the active editor instance.");
                }

                if (!string.IsNullOrWhiteSpace(rendererPath) && !string.IsNullOrWhiteSpace(materialAssetPath))
                {
                    return new ErrorResponse("rendererPath and materialAssetPath cannot be combined.");
                }
                if (!string.IsNullOrWhiteSpace(materialAssetPath) && !string.IsNullOrWhiteSpace(rendererComponentId))
                {
                    return new ErrorResponse("rendererComponentId cannot be combined with materialAssetPath.");
                }

                if (!preview
                    && (string.IsNullOrWhiteSpace(expectedBeforeShader)
                        || string.IsNullOrWhiteSpace(expectedMaterialAssetPath)
                        || string.IsNullOrWhiteSpace(expectedMaterialAssetGuid)
                        || string.IsNullOrWhiteSpace(expectedMaterialFileDigest)
                        || string.IsNullOrWhiteSpace(expectedSharedImpactDigest)
                        || @params?["expectedBeforeShaderAssetPath"] == null
                        || @params?["expectedBeforeShaderAssetGuid"] == null
                        || @params?["expectedShaderAssetPath"] == null
                        || @params?["expectedShaderAssetGuid"] == null
                        || (!string.IsNullOrWhiteSpace(rendererPath)
                            && (@params?["expectedRendererScenePath"] == null
                                || @params?["expectedRendererSceneGuid"] == null
                                || expectedRendererSceneHandle <= 0
                                || string.IsNullOrWhiteSpace(expectedRendererComponentId)
                                || string.IsNullOrWhiteSpace(expectedRendererComponentType)
                                || expectedRendererComponentIndex < 0))))
                {
                    return new ErrorResponse("Verified material and shader preconditions are required for apply.");
                }

                if (!preview && !saveAssets)
                {
                    return new ErrorResponse("saveAssets must be true for apply.");
                }

                var shader = ResolveShader(shaderName, shaderAssetPath);
                if (shader == null)
                {
                    return new ErrorResponse("The requested shader could not be resolved.");
                }
                var resolvedShaderAssetPath = NormalizeResolvedShaderAssetPath(AssetDatabase.GetAssetPath(shader));
                var resolvedShaderAssetGuid = string.IsNullOrWhiteSpace(resolvedShaderAssetPath)
                    ? string.Empty
                    : NormalizeHex(AssetDatabase.AssetPathToGUID(resolvedShaderAssetPath), 32, allowEmpty: false);
                if (!preview
                    && (!string.Equals(resolvedShaderAssetPath, expectedShaderAssetPath, StringComparison.Ordinal)
                        || !string.Equals(resolvedShaderAssetGuid, expectedShaderAssetGuid, StringComparison.OrdinalIgnoreCase)))
                {
                    return new ErrorResponse("The resolved shader asset no longer matches the verified preview.");
                }

                var target = ResolveMaterialTarget(rendererPath, rendererComponentId, materialAssetPath, slotIndex);
                if (target.material == null)
                {
                    return new ErrorResponse("Material target could not be resolved.");
                }

                var materialEvidence = InspectWritableMaterialAsset(target.material);
                var persistentMaterialPath = materialEvidence.assetPath;
                var beforeShaderObject = target.material.shader;
                var beforeShader = beforeShaderObject != null ? beforeShaderObject.name : string.Empty;
                var beforeShaderAssetPath = beforeShaderObject != null
                    ? NormalizeResolvedShaderAssetPath(AssetDatabase.GetAssetPath(beforeShaderObject))
                    : string.Empty;
                var beforeShaderAssetGuid = string.IsNullOrWhiteSpace(beforeShaderAssetPath)
                    ? string.Empty
                    : NormalizeHex(AssetDatabase.AssetPathToGUID(beforeShaderAssetPath), 32, allowEmpty: false);
                if (!string.IsNullOrWhiteSpace(expectedBeforeShader)
                    && (!string.Equals(beforeShader, expectedBeforeShader, StringComparison.Ordinal)
                        || !string.Equals(beforeShaderAssetPath, expectedBeforeShaderAssetPath, StringComparison.Ordinal)
                        || !string.Equals(beforeShaderAssetGuid, expectedBeforeShaderAssetGuid, StringComparison.OrdinalIgnoreCase)))
                {
                    return new ErrorResponse("The material shader no longer matches the verified preview.");
                }
                if (!preview
                    && (!string.Equals(persistentMaterialPath, expectedMaterialAssetPath, StringComparison.Ordinal)
                        || !string.Equals(materialEvidence.assetGuid, expectedMaterialAssetGuid, StringComparison.OrdinalIgnoreCase)
                        || !string.Equals(materialEvidence.fileDigest, expectedMaterialFileDigest, StringComparison.OrdinalIgnoreCase)))
                {
                    return new ErrorResponse("The material asset no longer matches the verified preview.");
                }
                if (!preview
                    && !string.IsNullOrWhiteSpace(rendererPath)
                    && (!string.Equals(target.rendererScenePath, expectedRendererScenePath, StringComparison.Ordinal)
                        || !string.Equals(target.rendererSceneGuid, expectedRendererSceneGuid, StringComparison.OrdinalIgnoreCase)
                        || target.rendererSceneHandle != expectedRendererSceneHandle
                        || !string.Equals(target.rendererComponentId, expectedRendererComponentId, StringComparison.OrdinalIgnoreCase)
                        || !string.Equals(target.rendererComponentType, expectedRendererComponentType, StringComparison.Ordinal)
                        || target.rendererComponentIndex != expectedRendererComponentIndex))
                {
                    return new ErrorResponse("The renderer component no longer matches the verified preview.");
                }

                var sharedImpactResult = BuildSharedMaterialImpact(target.material, persistentMaterialPath);
                var sharedImpact = sharedImpactResult.impact;
                var sharedImpactDigest = sharedImpactResult.digest;
                var sharedImpactDisplayDigest = sharedImpactResult.displayDigest;
                var sharedImpactTailDigest = sharedImpactResult.tailDigest;
                if (!preview && !string.Equals(sharedImpactDigest, expectedSharedImpactDigest, StringComparison.OrdinalIgnoreCase))
                {
                    return new ErrorResponse("Shared material impact changed after the verified preview.");
                }
                var wouldChange = beforeShaderObject != shader;
                var changed = false;
                var materialFileDigestAfter = materialEvidence.fileDigest;
                if (!preview && wouldChange)
                {
                    if (!string.Equals(ComputeFileSha256(materialEvidence.filePath), materialEvidence.fileDigest, StringComparison.OrdinalIgnoreCase))
                    {
                        return new ErrorResponse("The material file changed after the verified preview.");
                    }
                    Undo.RecordObject(target.material, "Set VRCForge material shader");
                    target.material.shader = shader;
                    EditorUtility.SetDirty(target.material);
                    AssetDatabase.SaveAssetIfDirty(target.material);
                    if (EditorUtility.IsDirty(target.material))
                    {
                        throw new InvalidOperationException("Material asset remained dirty after save.");
                    }
                    materialFileDigestAfter = ComputeFileSha256(materialEvidence.filePath);
                    if (string.Equals(materialFileDigestAfter, materialEvidence.fileDigest, StringComparison.OrdinalIgnoreCase))
                    {
                        throw new InvalidOperationException("Material file did not change after save.");
                    }
                    changed = true;
                }

                var readback = AssetDatabase.LoadAssetAtPath<Material>(persistentMaterialPath);
                var readbackShader = readback != null && readback.shader != null
                    ? readback.shader.name
                    : string.Empty;
                var verified = preview
                    || (readback != null
                        && readback.shader == shader
                        && string.Equals(readbackShader, shader.name, StringComparison.Ordinal)
                        && !EditorUtility.IsDirty(readback)
                        && string.Equals(ComputeFileSha256(materialEvidence.filePath), materialFileDigestAfter, StringComparison.OrdinalIgnoreCase));
                if (!verified)
                {
                    throw new InvalidOperationException("Material shader readback did not match the requested shader.");
                }

                return new SuccessResponse(
                    preview ? "Material shader preview completed." : "Material shader assignment applied.",
                    new
                    {
                        schema = ResultSchema,
                        ok = true,
                        preview,
                        changed,
                        wouldChange,
                        saved = !preview && changed,
                        verified,
                        rendererPath = target.rendererPath,
                        rendererScenePath = target.rendererScenePath,
                        rendererSceneGuid = target.rendererSceneGuid,
                        rendererSceneHandle = target.rendererSceneHandle,
                        rendererComponentId = target.rendererComponentId,
                        rendererComponentType = target.rendererComponentType,
                        rendererComponentIndex = target.rendererComponentIndex,
                        materialAssetPath = persistentMaterialPath,
                        materialAssetGuid = materialEvidence.assetGuid,
                        materialFileDigestBefore = materialEvidence.fileDigest,
                        materialFileDigestAfter,
                        slotIndex = target.slotIndex,
                        materialName = target.material.name,
                        expectedBeforeShader,
                        beforeShader,
                        beforeShaderAssetPath,
                        beforeShaderAssetGuid,
                        requestedShader = shader.name,
                        afterShader = preview ? shader.name : readbackShader,
                        shaderAssetPath = resolvedShaderAssetPath,
                        shaderAssetGuid = resolvedShaderAssetGuid,
                        sharedImpact,
                        sharedImpactDigestSchema = "vrcforge.material_shader_impact.v2",
                        sharedImpactDigest,
                        sharedImpactDisplayDigest,
                        sharedImpactTailDigest
                    });
            }
            catch (Exception)
            {
                return new ErrorResponse("Material shader assignment failed.");
            }
        }

        private static Shader ResolveShader(string shaderName, string shaderAssetPath)
        {
            if (!string.IsNullOrWhiteSpace(shaderAssetPath))
            {
                return LoadShaderAtAssetPath(shaderAssetPath, shaderName);
            }

            var shader = Shader.Find(shaderName);
            if (shader != null)
            {
                return shader;
            }

            var leafName = shaderName.Split('/').LastOrDefault() ?? shaderName;
            var guids = AssetDatabase.FindAssets($"{leafName} t:Shader", new[] { "Assets", "Packages" })
                .Concat(AssetDatabase.FindAssets("t:Shader", new[] { "Assets", "Packages" }))
                .Distinct();
            foreach (var guid in guids)
            {
                var path = AssetDatabase.GUIDToAssetPath(guid);
                var candidate = LoadShaderAtAssetPath(path, shaderName);
                if (candidate != null)
                {
                    return candidate;
                }
            }

            return null;
        }

        private static Shader LoadShaderAtAssetPath(string shaderAssetPath, string shaderName)
        {
            if (string.IsNullOrWhiteSpace(shaderAssetPath))
            {
                return null;
            }

            var candidate = AssetDatabase.LoadAssetAtPath<Shader>(shaderAssetPath);
            if (candidate == null)
            {
                return null;
            }

            if (string.Equals(candidate.name, shaderName, StringComparison.Ordinal))
            {
                return candidate;
            }

            return null;
        }

        private static MaterialTarget ResolveMaterialTarget(
            string rendererPath,
            string rendererComponentId,
            string materialAssetPath,
            int slotIndex)
        {
            if (!string.IsNullOrWhiteSpace(materialAssetPath))
            {
                var materialAsset = AssetDatabase.LoadAssetAtPath<Material>(materialAssetPath);
                if (materialAsset == null)
                {
                    throw new InvalidOperationException("Material asset was not found.");
                }

                return new MaterialTarget
                {
                    material = materialAsset,
                    rendererPath = string.Empty,
                    rendererScenePath = string.Empty,
                    rendererSceneGuid = string.Empty,
                    rendererSceneHandle = -1,
                    rendererComponentId = string.Empty,
                    rendererComponentType = string.Empty,
                    rendererComponentIndex = -1,
                    slotIndex = -1
                };
            }

            if (string.IsNullOrWhiteSpace(rendererPath))
            {
                throw new InvalidOperationException("rendererPath or materialAssetPath is required.");
            }

            if (slotIndex < 0)
            {
                throw new InvalidOperationException("slotIndex must be zero or greater.");
            }

            var normalizedRendererPath = NormalizeScenePath(rendererPath);
            var matchingRenderers = Resources.FindObjectsOfTypeAll<Renderer>()
                .Where(IsSceneObject)
                .Where(item =>
                {
                    var path = NormalizeScenePath(GetTransformPath(item.transform));
                    return string.Equals(path, normalizedRendererPath, StringComparison.OrdinalIgnoreCase)
                        || path.EndsWith("/" + normalizedRendererPath, StringComparison.OrdinalIgnoreCase);
                })
                .Select(RendererComponentIdentity.Create)
                .Where(item => string.IsNullOrWhiteSpace(rendererComponentId)
                    || string.Equals(item.componentId, rendererComponentId, StringComparison.OrdinalIgnoreCase))
                .Take(2)
                .ToArray();
            if (matchingRenderers.Length == 0)
            {
                throw new InvalidOperationException("Renderer was not found.");
            }

            if (matchingRenderers.Length > 1)
            {
                throw new InvalidOperationException("Renderer path is ambiguous; provide a renderer component identifier from the material inventory.");
            }

            var rendererIdentity = matchingRenderers[0];
            var renderer = rendererIdentity.renderer;

            var materials = renderer.sharedMaterials ?? Array.Empty<Material>();
            if (slotIndex >= materials.Length)
            {
                throw new InvalidOperationException("Material slot is out of range.");
            }

            var material = materials[slotIndex];
            if (material == null)
            {
                throw new InvalidOperationException("Material slot is empty.");
            }

            return new MaterialTarget
            {
                material = material,
                rendererPath = rendererIdentity.rendererPath,
                rendererScenePath = rendererIdentity.scenePath,
                rendererSceneGuid = rendererIdentity.sceneGuid,
                rendererSceneHandle = rendererIdentity.sceneHandle,
                rendererComponentId = rendererIdentity.componentId,
                rendererComponentType = rendererIdentity.componentType,
                rendererComponentIndex = rendererIdentity.componentIndex,
                slotIndex = slotIndex
            };
        }

        private static MaterialAssetEvidence InspectWritableMaterialAsset(Material material)
        {
            var normalizedPath = (AssetDatabase.GetAssetPath(material) ?? string.Empty).Replace("\\", "/").Trim();
            if (!AssetDatabase.Contains(material)
                || !AssetDatabase.IsMainAsset(material)
                || !normalizedPath.StartsWith("Assets/", StringComparison.OrdinalIgnoreCase)
                || !normalizedPath.EndsWith(".mat", StringComparison.OrdinalIgnoreCase))
            {
                throw new InvalidOperationException("Material target must be a persistent project .mat asset under Assets/.");
            }

            if (!AssetDatabase.IsOpenForEdit(material, StatusQueryOptions.UseCachedIfPossible))
            {
                throw new InvalidOperationException("Material target is not writable.");
            }
            if (EditorUtility.IsDirty(material))
            {
                throw new InvalidOperationException("Material target has unsaved changes and cannot be safely previewed.");
            }

            var projectRoot = Path.GetFullPath(Path.Combine(Application.dataPath, ".."));
            var assetsRoot = Path.GetFullPath(Application.dataPath);
            var filePath = Path.GetFullPath(
                Path.Combine(projectRoot, normalizedPath.Replace('/', Path.DirectorySeparatorChar))
            );
            var assetsPrefix = assetsRoot.TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar)
                + Path.DirectorySeparatorChar;
            if (!filePath.StartsWith(assetsPrefix, StringComparison.OrdinalIgnoreCase))
            {
                throw new InvalidOperationException("Material file resolved outside the project Assets directory.");
            }

            EnsureNoReparseBoundary(assetsRoot, filePath);
            if (!File.Exists(filePath))
            {
                throw new InvalidOperationException("Material file does not exist on disk.");
            }
            var fileAttributes = File.GetAttributes(filePath);
            if ((fileAttributes & (FileAttributes.ReadOnly | FileAttributes.ReparsePoint)) != 0)
            {
                throw new InvalidOperationException("Material file is read-only or crosses a reparse boundary.");
            }

            var assetGuid = NormalizeHex(AssetDatabase.AssetPathToGUID(normalizedPath), 32, allowEmpty: false);
            return new MaterialAssetEvidence
            {
                assetPath = normalizedPath,
                assetGuid = assetGuid,
                filePath = filePath,
                fileDigest = ComputeFileSha256(filePath)
            };
        }

        private static void EnsureNoReparseBoundary(string assetsRoot, string filePath)
        {
            var rootAttributes = File.GetAttributes(assetsRoot);
            if ((rootAttributes & FileAttributes.ReparsePoint) != 0)
            {
                throw new InvalidOperationException("Project Assets directory cannot be a reparse point.");
            }

            var current = new DirectoryInfo(Path.GetDirectoryName(filePath) ?? string.Empty);
            var root = new DirectoryInfo(assetsRoot);
            while (current != null)
            {
                if ((current.Attributes & FileAttributes.ReparsePoint) != 0)
                {
                    throw new InvalidOperationException("Material path crosses a reparse boundary.");
                }
                if (string.Equals(current.FullName, root.FullName, StringComparison.OrdinalIgnoreCase))
                {
                    return;
                }
                current = current.Parent;
            }

            throw new InvalidOperationException("Material path did not resolve below the project Assets directory.");
        }

        private static string ComputeFileSha256(string filePath)
        {
            using (var sha256 = SHA256.Create())
            using (var stream = new FileStream(filePath, FileMode.Open, FileAccess.Read, FileShare.Read))
            {
                return BitConverter.ToString(sha256.ComputeHash(stream)).Replace("-", string.Empty).ToLowerInvariant();
            }
        }

        private static SharedMaterialImpactResult BuildSharedMaterialImpact(Material material, string materialAssetPath)
        {
            var loadedRendererSlots = new List<RendererSlotImpact>();
            foreach (var renderer in Resources.FindObjectsOfTypeAll<Renderer>().Where(IsSceneObject))
            {
                var rendererIdentity = RendererComponentIdentity.Create(renderer);
                var materials = renderer.sharedMaterials ?? Array.Empty<Material>();
                for (var index = 0; index < materials.Length; index++)
                {
                    if (materials[index] != material)
                    {
                        continue;
                    }

                    loadedRendererSlots.Add(new RendererSlotImpact
                    {
                        scenePath = rendererIdentity.scenePath,
                        sceneGuid = rendererIdentity.sceneGuid,
                        sceneHandle = rendererIdentity.sceneHandle,
                        rendererPath = rendererIdentity.rendererPath,
                        rendererComponentId = rendererIdentity.componentId,
                        rendererComponentType = rendererIdentity.componentType,
                        rendererComponentIndex = rendererIdentity.componentIndex,
                        slotIndex = index
                    });
                    if (loadedRendererSlots.Count > MaxDependencyCandidates)
                    {
                        throw new InvalidOperationException("Shared material impact scan exceeded its bounded loaded-renderer limit.");
                    }
                }
            }

            loadedRendererSlots = loadedRendererSlots
                .OrderBy(item => item.scenePath, StringComparer.Ordinal)
                .ThenBy(item => item.sceneHandle)
                .ThenBy(item => item.rendererPath, StringComparer.Ordinal)
                .ThenBy(item => item.rendererComponentType, StringComparer.Ordinal)
                .ThenBy(item => item.rendererComponentIndex)
                .ThenBy(item => item.rendererComponentId, StringComparer.Ordinal)
                .ThenBy(item => item.slotIndex)
                .ToList();

            var dependencyCandidates = AssetDatabase.FindAssets("t:Prefab", new[] { "Assets" })
                .Concat(AssetDatabase.FindAssets("t:Scene", new[] { "Assets" }))
                .Select(AssetDatabase.GUIDToAssetPath)
                .Where(path => !string.IsNullOrWhiteSpace(path))
                .Distinct(StringComparer.Ordinal)
                .OrderBy(path => path, StringComparer.Ordinal)
                .ToArray();
            if (dependencyCandidates.Length > MaxDependencyCandidates)
            {
                throw new InvalidOperationException("Shared material impact scan exceeded its bounded project-asset limit.");
            }

            var dependentAssets = new List<string>();
            foreach (var candidatePath in dependencyCandidates)
            {
                var dependencies = AssetDatabase.GetDependencies(candidatePath, true);
                if (dependencies.Any(path => string.Equals(path, materialAssetPath, StringComparison.OrdinalIgnoreCase)))
                {
                    dependentAssets.Add(candidatePath);
                }
            }

            var listsTruncated = loadedRendererSlots.Count > MaxImpactItems || dependentAssets.Count > MaxImpactItems;
            var impact = new SharedMaterialImpact
            {
                scope = "loaded_scene_renderers_and_project_scene_prefab_dependencies",
                dependencyCandidateCount = dependencyCandidates.Length,
                loadedRendererSlotCount = loadedRendererSlots.Count,
                loadedRendererSlots = loadedRendererSlots.Take(MaxImpactItems).ToArray(),
                dependentAssetCount = dependentAssets.Count,
                dependentAssets = dependentAssets.Take(MaxImpactItems).ToArray(),
                listsTruncated = listsTruncated
            };
            var displayDigest = ComputeImpactPartitionDigest(
                "display",
                impact.scope,
                impact.dependencyCandidateCount,
                impact.loadedRendererSlotCount,
                impact.loadedRendererSlots,
                impact.dependentAssetCount,
                impact.dependentAssets,
                listsTruncated
            );
            var tailDigest = ComputeImpactPartitionDigest(
                "tail",
                impact.scope,
                impact.dependencyCandidateCount,
                impact.loadedRendererSlotCount,
                loadedRendererSlots.Skip(MaxImpactItems).ToArray(),
                impact.dependentAssetCount,
                dependentAssets.Skip(MaxImpactItems).ToArray(),
                listsTruncated
            );
            return new SharedMaterialImpactResult
            {
                impact = impact,
                digest = ComputeImpactCommitment(
                    impact.scope,
                    impact.dependencyCandidateCount,
                    impact.loadedRendererSlotCount,
                    impact.dependentAssetCount,
                    listsTruncated,
                    displayDigest,
                    tailDigest
                ),
                displayDigest = displayDigest,
                tailDigest = tailDigest
            };
        }

        private static string ComputeImpactPartitionDigest(
            string partition,
            string scope,
            int dependencyCandidateCount,
            int loadedRendererSlotCount,
            IReadOnlyList<RendererSlotImpact> loadedRendererSlots,
            int dependentAssetCount,
            IReadOnlyList<string> dependentAssets,
            bool listsTruncated)
        {
            var value = new StringBuilder();
            AppendDigestField(value, "vrcforge.material_shader_impact.v2");
            AppendDigestField(value, partition);
            AppendDigestField(value, scope);
            AppendDigestField(value, dependencyCandidateCount.ToString(CultureInfo.InvariantCulture));
            AppendDigestField(value, loadedRendererSlotCount.ToString(CultureInfo.InvariantCulture));
            foreach (var slot in loadedRendererSlots)
            {
                AppendDigestField(value, slot.scenePath);
                AppendDigestField(value, slot.sceneGuid);
                AppendDigestField(value, slot.sceneHandle.ToString(CultureInfo.InvariantCulture));
                AppendDigestField(value, slot.rendererPath);
                AppendDigestField(value, slot.rendererComponentId);
                AppendDigestField(value, slot.rendererComponentType);
                AppendDigestField(value, slot.rendererComponentIndex.ToString(CultureInfo.InvariantCulture));
                AppendDigestField(value, slot.slotIndex.ToString(CultureInfo.InvariantCulture));
            }
            AppendDigestField(value, dependentAssetCount.ToString(CultureInfo.InvariantCulture));
            foreach (var path in dependentAssets)
            {
                AppendDigestField(value, path);
            }
            AppendDigestField(value, listsTruncated ? "true" : "false");

            using (var sha256 = SHA256.Create())
            {
                var digest = sha256.ComputeHash(Encoding.UTF8.GetBytes(value.ToString()));
                return BitConverter.ToString(digest).Replace("-", string.Empty).ToLowerInvariant();
            }
        }

        private static string ComputeImpactCommitment(
            string scope,
            int dependencyCandidateCount,
            int loadedRendererSlotCount,
            int dependentAssetCount,
            bool listsTruncated,
            string displayDigest,
            string tailDigest)
        {
            var value = new StringBuilder();
            AppendDigestField(value, "vrcforge.material_shader_impact.v2");
            AppendDigestField(value, "full");
            AppendDigestField(value, scope);
            AppendDigestField(value, dependencyCandidateCount.ToString(CultureInfo.InvariantCulture));
            AppendDigestField(value, loadedRendererSlotCount.ToString(CultureInfo.InvariantCulture));
            AppendDigestField(value, dependentAssetCount.ToString(CultureInfo.InvariantCulture));
            AppendDigestField(value, listsTruncated ? "true" : "false");
            AppendDigestField(value, displayDigest);
            AppendDigestField(value, tailDigest);
            using (var sha256 = SHA256.Create())
            {
                var digest = sha256.ComputeHash(Encoding.UTF8.GetBytes(value.ToString()));
                return BitConverter.ToString(digest).Replace("-", string.Empty).ToLowerInvariant();
            }
        }

        private static void AppendDigestField(StringBuilder target, string value)
        {
            var safeValue = value ?? string.Empty;
            target.Append(safeValue.Length).Append(':').Append(safeValue);
        }

        private static string NormalizeHex(string value, int expectedLength, bool allowEmpty)
        {
            var normalized = (value ?? string.Empty).Trim().ToLowerInvariant();
            if (allowEmpty && string.IsNullOrWhiteSpace(normalized))
            {
                return string.Empty;
            }
            if (normalized.Length != expectedLength || normalized.Any(character => !Uri.IsHexDigit(character)))
            {
                throw new InvalidOperationException("Verification identifier is invalid.");
            }
            return normalized;
        }

        private static string NormalizeOptionalAssetPath(string value, bool allowPackages)
        {
            var normalized = (value ?? string.Empty).Replace("\\", "/").Trim();
            if (string.IsNullOrWhiteSpace(normalized))
            {
                return string.Empty;
            }

            var allowedRoot = normalized.StartsWith("Assets/", StringComparison.OrdinalIgnoreCase)
                || (allowPackages && normalized.StartsWith("Packages/", StringComparison.OrdinalIgnoreCase));
            if (normalized.StartsWith("/", StringComparison.Ordinal)
                || normalized.EndsWith("/", StringComparison.Ordinal)
                || !allowedRoot
                || normalized.Split('/').Any(part => part == "." || part == ".." || string.IsNullOrWhiteSpace(part)))
            {
                throw new InvalidOperationException("Asset path is outside the allowed project roots.");
            }

            return normalized;
        }

        private static string NormalizeResolvedShaderAssetPath(string value)
        {
            var normalized = (value ?? string.Empty).Replace("\\", "/").Trim();
            if (string.IsNullOrWhiteSpace(normalized)
                || string.Equals(normalized, "Resources/unity_builtin_extra", StringComparison.Ordinal)
                || string.Equals(normalized, "Library/unity default resources", StringComparison.Ordinal))
            {
                return string.Empty;
            }
            return NormalizeOptionalAssetPath(normalized, allowPackages: true);
        }

        private static bool MatchesCurrentProject(string expectedProjectPath)
        {
            if (string.IsNullOrWhiteSpace(expectedProjectPath) || !Path.IsPathRooted(expectedProjectPath))
            {
                return false;
            }
            var expected = Path.GetFullPath(expectedProjectPath)
                .TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar);
            var current = Path.GetFullPath(Path.Combine(Application.dataPath, ".."))
                .TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar);
            var comparison = Application.platform == RuntimePlatform.WindowsEditor
                ? StringComparison.OrdinalIgnoreCase
                : StringComparison.Ordinal;
            return string.Equals(expected, current, comparison);
        }

        private static bool IsSceneObject(Component component)
        {
            return component != null
                && component.gameObject.scene.IsValid()
                && component.gameObject.scene.isLoaded
                && !EditorUtility.IsPersistent(component);
        }

        private static string GetTransformPath(Transform transform)
        {
            if (transform == null)
            {
                return string.Empty;
            }

            var current = transform;
            var path = current.name;
            while (current.parent != null)
            {
                current = current.parent;
                path = current.name + "/" + path;
            }

            return path;
        }

        private static string NormalizeScenePath(string value)
        {
            return (value ?? string.Empty).Replace("\\", "/").Trim().Trim('/');
        }

        private sealed class MaterialTarget
        {
            public Material material;
            public string rendererPath = string.Empty;
            public string rendererScenePath = string.Empty;
            public string rendererSceneGuid = string.Empty;
            public int rendererSceneHandle = -1;
            public string rendererComponentId = string.Empty;
            public string rendererComponentType = string.Empty;
            public int rendererComponentIndex = -1;
            public int slotIndex;
        }

        private sealed class MaterialAssetEvidence
        {
            public string assetPath = string.Empty;
            public string assetGuid = string.Empty;
            public string filePath = string.Empty;
            public string fileDigest = string.Empty;
        }

        private sealed class RendererSlotImpact
        {
            public string scenePath = string.Empty;
            public string sceneGuid = string.Empty;
            public int sceneHandle;
            public string rendererPath = string.Empty;
            public string rendererComponentId = string.Empty;
            public string rendererComponentType = string.Empty;
            public int rendererComponentIndex;
            public int slotIndex;
        }

        private sealed class SharedMaterialImpact
        {
            public string scope = string.Empty;
            public int dependencyCandidateCount;
            public int loadedRendererSlotCount;
            public RendererSlotImpact[] loadedRendererSlots = Array.Empty<RendererSlotImpact>();
            public int dependentAssetCount;
            public string[] dependentAssets = Array.Empty<string>();
            public bool listsTruncated;
        }

        private sealed class SharedMaterialImpactResult
        {
            public SharedMaterialImpact impact = new SharedMaterialImpact();
            public string digest = string.Empty;
            public string displayDigest = string.Empty;
            public string tailDigest = string.Empty;
        }
    }
}
