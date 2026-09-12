using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using VRCForge.Core.MCP;

namespace VRCForge.Editor
{
    // ------------------------------------------------------------------
    // Generic Unity Asset / Prefab layer (v0.5, third cut).
    //
    // Four MCP tools. Everything sits on stable UnityEditor APIs
    // (AssetDatabase / PrefabUtility) and reuses ComponentCrudCore's
    // hierarchy/path helpers, so this stays reflection-friendly and never
    // hard-references Modular Avatar / VRChat SDK assemblies:
    //   vrc_find_assets       (read)
    //   vrc_get_asset_info    (read)
    //   vrc_instantiate_prefab(write, Undo-registered)
    //   vrc_unpack_prefab     (write, Undo-registered)
    //
    // This is the bridge toward the "add an outfit to the avatar" workflow:
    // find the outfit prefab in the project, instantiate it into the scene
    // under the avatar (prefab link preserved), and optionally unpack it so
    // its contents become plain GameObjects ready for Modular Avatar merges.
    //
    // Both write tools register a Unity Undo entry so the checkpoint timeline
    // (bound to Undo) can roll them back, and support a preview mode that
    // reports what *would* change without mutating, feeding the per-action
    // approval card. Payload keys deliberately avoid data/result/payload/value
    // so the gateway's auto-unwrap never swallows them.
    // ------------------------------------------------------------------

    internal static class AssetPrefabCore
    {
        internal static string NormalizeAssetPath(string value)
        {
            return (value ?? string.Empty).Replace("\\", "/").Trim();
        }

        // Resolve an asset path from either an explicit asset path or a GUID.
        // Throws InvalidOperationException with a helpful message when neither
        // is provided or the asset cannot be located.
        internal static string ResolveAssetPath(string assetPath, string guid)
        {
            var normalizedPath = NormalizeAssetPath(assetPath);
            if (!string.IsNullOrEmpty(normalizedPath))
            {
                return normalizedPath;
            }

            var normalizedGuid = (guid ?? string.Empty).Trim();
            if (!string.IsNullOrEmpty(normalizedGuid))
            {
                var fromGuid = AssetDatabase.GUIDToAssetPath(normalizedGuid);
                if (string.IsNullOrEmpty(fromGuid))
                {
                    throw new InvalidOperationException($"No asset found for GUID '{normalizedGuid}'.");
                }
                return fromGuid;
            }

            throw new InvalidOperationException("assetPath (or guid) is required.");
        }

        internal static string AssetDisplayName(string assetPath)
        {
            return Path.GetFileNameWithoutExtension(assetPath ?? string.Empty);
        }

        internal static int CountHierarchyPath(string hierarchyPath, int sceneHandle)
        {
            var normalized = ComponentCrudCore.NormalizePath(hierarchyPath);
            return Resources.FindObjectsOfTypeAll<GameObject>().Count(go =>
                go != null
                && go.scene.IsValid()
                && go.scene.handle == sceneHandle
                && string.Equals(ComponentCrudCore.GetHierarchyPath(go.transform), normalized, StringComparison.Ordinal));
        }
    }

    [VRCForgeCommand(
        toolId: "vrc_find_assets",
        Summary = "Search the project for assets by query/type/folder via AssetDatabase (read-only).",
        Access = VRCForgeCommandAccess.ReadOnly
    )]
    public static class FindAssetsTool
    {
        public const string ToolName = "vrc_find_assets";

        public class FindAssetsParameters
        {
            [VRCForgeInput("Unity search filter (e.g. 'outfit' or 'l:wardrobe'). Combined with 'typeName' when both are given.", IsRequired = false)]
            public string query { get; set; } = "";

            [VRCForgeInput("Restrict to an asset type by name (e.g. 'Prefab', 'Material', 'AnimationClip'); applied as a 't:' filter.", IsRequired = false)]
            public string typeName { get; set; } = "";

            [VRCForgeInput("Limit the search to a project folder (e.g. 'Assets/Outfits'). Empty searches the whole project.", IsRequired = false)]
            public string folder { get; set; } = "";

            [VRCForgeInput("Maximum number of results to return (default 50).", IsRequired = false)]
            public int? limit { get; set; } = 50;
        }

        public static object HandleCommand(JObject @params)
        {
            var p = (@params ?? new JObject()).ToObject<FindAssetsParameters>() ?? new FindAssetsParameters();
            try
            {
                var limit = p.limit ?? 50;
                if (limit <= 0)
                {
                    limit = 50;
                }

                var filterParts = new List<string>();
                if (!string.IsNullOrWhiteSpace(p.typeName))
                {
                    filterParts.Add("t:" + p.typeName.Trim());
                }
                if (!string.IsNullOrWhiteSpace(p.query))
                {
                    filterParts.Add(p.query.Trim());
                }
                var filter = string.Join(" ", filterParts);

                string[] searchFolders = null;
                var folder = AssetPrefabCore.NormalizeAssetPath(p.folder).TrimEnd('/');
                if (!string.IsNullOrEmpty(folder))
                {
                    if (!AssetDatabase.IsValidFolder(folder))
                    {
                        return VRCForgeToolResult.Failed($"Search folder not found: '{folder}'.");
                    }
                    searchFolders = new[] { folder };
                }

                var guids = searchFolders != null
                    ? AssetDatabase.FindAssets(filter, searchFolders)
                    : AssetDatabase.FindAssets(filter);

                var assets = new List<object>();
                foreach (var guid in guids)
                {
                    if (assets.Count >= limit)
                    {
                        break;
                    }
                    var path = AssetDatabase.GUIDToAssetPath(guid);
                    if (string.IsNullOrEmpty(path))
                    {
                        continue;
                    }
                    var type = AssetDatabase.GetMainAssetTypeAtPath(path);
                    assets.Add(new
                    {
                        name = AssetPrefabCore.AssetDisplayName(path),
                        assetPath = path,
                        guid,
                        assetType = type != null ? type.FullName : null
                    });
                }

                var payload = new
                {
                    filter,
                    folder = string.IsNullOrEmpty(folder) ? null : folder,
                    totalFound = guids.Length,
                    count = assets.Count,
                    assets
                };
                return VRCForgeToolResult.Completed(
                    $"Found {guids.Length} asset(s) for filter '{filter}' (returning {assets.Count}).",
                    payload);
            }
            catch (Exception ex)
            {
                return VRCForgeToolResult.Failed($"Find assets failed: {ex.Message}");
            }
        }
    }

    [VRCForgeCommand(
        toolId: "vrc_get_asset_info",
        Summary = "Describe a project asset: path, GUID, type, importer, and prefab details when applicable (read-only).",
        Access = VRCForgeCommandAccess.ReadOnly
    )]
    public static class GetAssetInfoTool
    {
        public const string ToolName = "vrc_get_asset_info";

        public class GetAssetInfoParameters
        {
            [VRCForgeInput("Project-relative asset path (e.g. 'Assets/Outfits/Dress.prefab').", IsRequired = false)]
            public string assetPath { get; set; } = "";

            [VRCForgeInput("Asset GUID (used when assetPath is omitted).", IsRequired = false)]
            public string guid { get; set; } = "";

            [VRCForgeInput("Include a bounded page of loaded asset objects and their exact copy-layout digest inputs (read-only).", IsRequired = false)]
            public bool includeObjects { get; set; } = false;

            [VRCForgeInput("Zero-based object page offset, 0..4096.", IsRequired = false)]
            public int objectOffset { get; set; } = 0;

            [VRCForgeInput("Object page size, 1..128; default 64.", IsRequired = false)]
            public int objectLimit { get; set; } = 64;

            [VRCForgeInput("When-to-use: inspect prefab asset descendant renderer shared-material slots without instantiation. When-NOT-to-use: scene objects or non-prefab assets.", IsRequired = false)]
            public bool includeRendererMaterials { get; set; } = false;

            [VRCForgeInput("Zero-based renderer page offset, 0..4096.", IsRequired = false)]
            public int rendererOffset { get; set; } = 0;

            [VRCForgeInput("Renderer page size, 1..128; default 64. Slots are returned completely or the response fails its size bound.", IsRequired = false)]
            public int rendererLimit { get; set; } = 64;

            [VRCForgeInput("Exact signed decimal Unity local file ID of a persistent imported TextAsset child.", IsRequired = false)]
            public string localFileId { get; set; } = "";

            [VRCForgeInput("Optional bounded ordinal literal search over the exact imported TextAsset child identified by localFileId.", IsRequired = false)]
            public GetAssetInfoParameters.ImportedTextSearchParameters importedTextSearch { get; set; }

            public class ImportedTextSearchParameters
            {
                public string literal { get; set; } = "";
                public int maxMatches { get; set; } = 8;
                public int contextCharacters { get; set; } = 128;
            }
        }

        public static object HandleCommand(JObject @params)
        {
            try
            {
                var request = @params ?? new JObject();
                if ((request["includeObjects"] != null && request["includeObjects"].Type != JTokenType.Boolean)
                    || (request["includeRendererMaterials"] != null && request["includeRendererMaterials"].Type != JTokenType.Boolean)
                    || (request["rendererOffset"] != null && request["rendererOffset"].Type != JTokenType.Integer)
                    || (request["rendererLimit"] != null && request["rendererLimit"].Type != JTokenType.Integer)
                    || (request["objectOffset"] != null && request["objectOffset"].Type != JTokenType.Integer)
                    || (request["objectLimit"] != null && request["objectLimit"].Type != JTokenType.Integer))
                    throw new InvalidOperationException("Listing include flags must be boolean; page offsets and limits must be integers.");
                if (request["localFileId"] != null && request["localFileId"].Type != JTokenType.String)
                    throw new InvalidOperationException("localFileId must be a decimal string.");
                var importedSearchToken = request["importedTextSearch"];
                if (importedSearchToken != null && importedSearchToken.Type != JTokenType.Object)
                    throw new InvalidOperationException("importedTextSearch must be an object.");
                if (importedSearchToken != null)
                {
                    var searchObject = (JObject)importedSearchToken;
                    var allowedSearchFields = new[] { "literal", "maxMatches", "contextCharacters" };
                    if (searchObject.Properties().Any(item => !allowedSearchFields.Contains(item.Name, StringComparer.Ordinal)))
                        throw new InvalidOperationException("importedTextSearch received an unknown field.");
                    if (searchObject["literal"] != null && searchObject["literal"].Type != JTokenType.String)
                        throw new InvalidOperationException("importedTextSearch.literal must be a string.");
                    if (searchObject["maxMatches"] != null && searchObject["maxMatches"].Type != JTokenType.Integer)
                        throw new InvalidOperationException("importedTextSearch.maxMatches must be an integer.");
                    if (searchObject["contextCharacters"] != null && searchObject["contextCharacters"].Type != JTokenType.Integer)
                        throw new InvalidOperationException("importedTextSearch.contextCharacters must be an integer.");
                }
                var p = request.ToObject<GetAssetInfoParameters>() ?? new GetAssetInfoParameters();
                if (p.objectOffset < 0 || p.objectOffset > 4096 || p.objectLimit < 1 || p.objectLimit > 128)
                    throw new InvalidOperationException("Object page requires offset 0..4096 and limit 1..128.");
                if (p.rendererOffset < 0 || p.rendererOffset > 4096 || p.rendererLimit < 1 || p.rendererLimit > 128)
                    throw new InvalidOperationException("Renderer page requires offset 0..4096 and limit 1..128.");
                var path = AssetPrefabCore.ResolveAssetPath(p.assetPath, p.guid);
                var asset = AssetDatabase.LoadMainAssetAtPath(path);
                if (asset == null)
                {
                    return VRCForgeToolResult.Failed($"No asset found at '{path}'.");
                }
                var type = AssetDatabase.GetMainAssetTypeAtPath(path);
                var resolvedGuid = AssetDatabase.AssetPathToGUID(path);
                var dependencyHash = AssetDatabase.GetAssetDependencyHash(path).ToString();
                var importer = AssetImporter.GetAtPath(path);
                string monoScriptClass = null;
                string monoScriptAssembly = null;
                JObject monoScriptCompilation = null;
                var monoScript = asset as MonoScript;
                if (monoScript != null)
                {
                    var loadedClass = monoScript.GetClass();
                    if (loadedClass != null)
                    {
                        monoScriptClass = loadedClass.FullName;
                        monoScriptAssembly = loadedClass.Assembly.GetName().Name;
                    }
                    monoScriptCompilation = ReadScriptCompilation(path);
                }

                var prefabAssetType = PrefabUtility.GetPrefabAssetType(asset);
                var isPrefab = prefabAssetType != PrefabAssetType.NotAPrefab && asset is GameObject;
                string prefabRootName = null;
                int prefabChildCount = 0;
                int prefabComponentCount = 0;
                if (isPrefab)
                {
                    var root = (GameObject)asset;
                    prefabRootName = root.name;
                    prefabChildCount = root.transform.childCount;
                    prefabComponentCount = root.GetComponents<Component>().Count(c => c != null);
                }

                var payload = new
                {
                    assetPath = path,
                    guid = resolvedGuid,
                    dependencyHash,
                    name = asset.name,
                    assetType = type != null ? type.FullName : null,
                    importerType = importer != null ? importer.GetType().FullName : null,
                    defaultImporterType = AssetDatabase.GetDefaultImporter(path)?.FullName,
                    overrideImporterType = AssetDatabase.GetImporterOverride(path)?.FullName,
                    monoScriptClass,
                    monoScriptAssembly,
                    monoScriptCompilation,
                    isPrefab,
                    prefabAssetType = prefabAssetType.ToString(),
                    prefabRootName,
                    prefabChildCount,
                    prefabComponentCount
                };
                if (p.importedTextSearch != null && string.IsNullOrWhiteSpace(p.localFileId))
                    throw new InvalidOperationException("importedTextSearch requires an exact localFileId.");
                if (p.importedTextSearch == null && !string.IsNullOrWhiteSpace(p.localFileId))
                    throw new InvalidOperationException("localFileId requires importedTextSearch.");
                if (p.includeObjects || p.includeRendererMaterials || p.importedTextSearch != null)
                {
                    var expanded = JObject.FromObject(payload);
                    if (p.includeObjects)
                        expanded["objectListing"] = ReadObjectListing(path, p.objectOffset, p.objectLimit);
                    if (p.includeRendererMaterials)
                    {
                        if (!isPrefab) throw new InvalidOperationException("Renderer material inspection requires a prefab GameObject asset.");
                        expanded["rendererMaterialListing"] = ReadRendererMaterialListing((GameObject)asset, p.rendererOffset, p.rendererLimit);
                    }
                    if (p.importedTextSearch != null)
                        expanded["importedTextSearch"] = ReadImportedTextSearch(path, resolvedGuid, p.localFileId, p.importedTextSearch);
                    if (System.Text.Encoding.UTF8.GetByteCount(expanded.ToString(Newtonsoft.Json.Formatting.None)) > 256 * 1024)
                        throw new InvalidOperationException("Asset inspection response exceeds 256 KiB; request a smaller objectLimit or rendererLimit.");
                    return VRCForgeToolResult.Completed("Read a bounded asset object page without importing or saving.", expanded);
                }
                return VRCForgeToolResult.Completed(
                    $"Asset '{asset.name}' ({(type != null ? type.Name : "unknown")}) at '{path}'.",
                    payload);
            }
            catch (Exception ex)
            {
                return VRCForgeToolResult.Failed($"Get asset info failed: {ex.Message}");
            }
        }

        private static JObject ReadScriptCompilation(string path)
        {
            var name = UnityEditor.Compilation.CompilationPipeline.GetAssemblyNameFromScriptPath(path);
            var assemblyName = name != null && name.EndsWith(".dll", StringComparison.OrdinalIgnoreCase)
                ? name.Substring(0, name.Length - 4) : name;
            var assemblies = string.IsNullOrEmpty(name) ? null
                : UnityEditor.Compilation.CompilationPipeline.GetAssemblies(UnityEditor.Compilation.AssembliesType.Editor);
            var matches = assemblies == null ? null : assemblies.Where(item => item != null
                && string.Equals(item.name, assemblyName, StringComparison.Ordinal)).ToArray();
            var assembly = matches != null && matches.Length == 1 ? matches[0] : null;
            var defines = assembly?.defines;
            if (defines != null && (defines.Length > 2048 || defines.Any(value => value == null || value.Length > 1024)))
                throw new InvalidOperationException("Script compilation defines exceed the diagnostic bound.");
            var loadedAssemblies = string.IsNullOrEmpty(assemblyName) ? new System.Reflection.Assembly[0]
                : AppDomain.CurrentDomain.GetAssemblies().Where(item =>
                    string.Equals(item.GetName().Name, assemblyName, StringComparison.Ordinal)).ToArray();
            JArray fileNameTypeMatches = null;
            string loadedTypeInspectionError = null;
            if (loadedAssemblies.Length == 1)
            {
                try
                {
                    var fileName = System.IO.Path.GetFileNameWithoutExtension(path);
                    var types = loadedAssemblies[0].GetTypes();
                    if (types.Length > 32768)
                        throw new InvalidOperationException("Loaded assembly type count exceeds the diagnostic bound.");
                    fileNameTypeMatches = new JArray(types.Where(item =>
                        string.Equals(item.Name, fileName, StringComparison.Ordinal)).Select(item => new JObject
                    {
                        ["fullName"] = item.FullName,
                        ["baseType"] = item.BaseType?.FullName,
                        ["isAssetImporter"] = typeof(AssetImporter).IsAssignableFrom(item),
                    }));
                }
                catch (Exception ex)
                {
                    // A failed type enumeration must remain unknown, not an empty successful match.
                    loadedTypeInspectionError = ex.GetType().FullName;
                }
            }
            var result = new JObject
            {
                ["source"] = "Unity CompilationPipeline Editor assembly metadata; not a loaded-class assertion",
                ["assemblyName"] = name == null ? JValue.CreateNull() : new JValue(name),
                ["assemblyFound"] = assembly != null,
                ["isCompiling"] = EditorApplication.isCompiling,
                ["defines"] = defines == null ? JValue.CreateNull() : (JToken)new JArray(defines),
                ["definesComplete"] = defines != null,
                ["loadedAssemblyCount"] = loadedAssemblies.Length,
                ["fileNameTypeMatches"] = fileNameTypeMatches == null ? JValue.CreateNull() : (JToken)fileNameTypeMatches,
                ["loadedTypesComplete"] = fileNameTypeMatches != null,
                ["loadedTypeInspectionError"] = loadedTypeInspectionError,
                ["loadedTypeMatchScope"] = "Exact file-name type candidates in the uniquely named loaded assembly; not MonoScript binding or importer registration proof",
            };
            if (System.Text.Encoding.UTF8.GetByteCount(result.ToString(Newtonsoft.Json.Formatting.None)) > 64 * 1024)
                throw new InvalidOperationException("Script compilation diagnostic response exceeds 64 KiB.");
            return result;
        }

        private static JObject ReadImportedTextSearch(
            string path, string assetGuid, string localFileId, GetAssetInfoParameters.ImportedTextSearchParameters search)
        {
            long requestedId;
            var digitsStart = localFileId != null && localFileId.StartsWith("-", StringComparison.Ordinal) ? 1 : 0;
            if (string.IsNullOrEmpty(localFileId) || digitsStart == localFileId.Length
                || localFileId.Skip(digitsStart).Any(value => value < '0' || value > '9')
                || !long.TryParse(localFileId, System.Globalization.NumberStyles.AllowLeadingSign, System.Globalization.CultureInfo.InvariantCulture, out requestedId))
                throw new InvalidOperationException("localFileId must be a signed decimal string in the Int64 range.");
            if (search == null || string.IsNullOrEmpty(search.literal) || search.literal.Length > 512
                || search.maxMatches < 1 || search.maxMatches > 32
                || search.contextCharacters < 0 || search.contextCharacters > 2048)
                throw new InvalidOperationException("importedTextSearch requires literal 1..512, maxMatches 1..32, contextCharacters 0..2048.");
            var matches = AssetDatabase.LoadAllAssetsAtPath(path)
                .Where(item => item != null)
                .Where(item => {
                    string guid; long id;
                    return AssetDatabase.TryGetGUIDAndLocalFileIdentifier(item, out guid, out id)
                        && string.Equals(guid, assetGuid, StringComparison.OrdinalIgnoreCase) && id == requestedId;
                }).ToArray();
            if (matches.Length != 1)
                throw new InvalidOperationException(matches.Length == 0
                    ? "No uniquely identified imported child matched localFileId."
                    : "localFileId matched multiple loaded objects.");
            var textAsset = matches[0] as TextAsset;
            if (textAsset == null)
                throw new InvalidOperationException("The exact localFileId is not an imported TextAsset.");
            if (!EditorUtility.IsPersistent(matches[0]))
                throw new InvalidOperationException("The exact localFileId is not a persistent imported TextAsset.");
            if (AssetDatabase.IsMainAsset(matches[0]) || !string.Equals(AssetDatabase.GetAssetPath(matches[0]), path, StringComparison.Ordinal))
                throw new InvalidOperationException("The exact localFileId is not a child TextAsset at the requested asset path.");
            var text = textAsset.text ?? string.Empty;
            var textByteCount = System.Text.Encoding.UTF8.GetByteCount(text);
            if (textByteCount > 4 * 1024 * 1024)
                throw new InvalidOperationException("Imported TextAsset text exceeds the 4 MiB diagnostic bound.");
            var textBytes = System.Text.Encoding.UTF8.GetBytes(text);
            var literal = search.literal;
            var rows = new JArray();
            var totalMatches = 0;
            var start = 0;
            while (start <= text.Length - literal.Length)
            {
                var index = text.IndexOf(literal, start, StringComparison.Ordinal);
                if (index < 0) break;
                totalMatches++;
                if (rows.Count < search.maxMatches)
                {
                    var contextStart = Math.Max(0, index - search.contextCharacters);
                    var contextEnd = Math.Min(text.Length, index + literal.Length + search.contextCharacters);
                    rows.Add(new JObject
                    {
                        ["matchStartCharacter"] = index,
                        ["matchEndCharacter"] = index + literal.Length,
                        ["contextStartCharacter"] = contextStart,
                        ["contextEndCharacter"] = contextEnd,
                        ["context"] = text.Substring(contextStart, contextEnd - contextStart),
                    });
                }
                start = index + Math.Max(1, literal.Length);
            }
            using (var sha = System.Security.Cryptography.SHA256.Create())
            {
                var digest = string.Concat(sha.ComputeHash(textBytes)
                    .Select(value => value.ToString("x2", System.Globalization.CultureInfo.InvariantCulture)));
                var result = new JObject
                {
                    ["schema"] = "vrcforge.imported_text_search.v1",
                    ["assetPath"] = path,
                    ["assetGuid"] = assetGuid,
                    ["localFileId"] = localFileId,
                    ["textKind"] = "Unity imported TextAsset.text UTF-8; not source-file or GPU evidence",
                    ["importedTextSha256"] = digest,
                    ["textLengthCharacters"] = text.Length,
                    ["textLengthUtf8Bytes"] = textByteCount,
                    ["offsetUnit"] = "UTF-16 code units",
                    ["literal"] = literal,
                    ["totalMatches"] = totalMatches,
                    ["returnedMatches"] = rows.Count,
                    ["truncated"] = totalMatches > rows.Count,
                    ["contextCharacters"] = search.contextCharacters,
                    ["matches"] = rows,
                };
                if (System.Text.Encoding.UTF8.GetByteCount(result.ToString(Newtonsoft.Json.Formatting.None)) > 256 * 1024)
                    throw new InvalidOperationException("Imported TextAsset search response exceeds 256 KiB.");
                return result;
            }
        }

        internal static JObject ReadRendererMaterialListing(GameObject root, int offset, int limit)
        {
            // Read the persistent prefab itself: no instantiated materials or scene objects.
            var renderers = root.GetComponentsInChildren<Renderer>(true);
            if (renderers.Length > 4096)
                throw new InvalidOperationException("Prefab renderer enumeration exceeds 4096 renderers.");
            var rows = new JArray();
            foreach (var renderer in renderers.Skip(offset).Take(limit))
            {
                var names = new Stack<string>();
                var indices = new Stack<string>();
                for (var current = renderer.transform; current != root.transform; current = current.parent)
                {
                    if (current == null) throw new InvalidOperationException("Renderer is outside the inspected prefab root.");
                    names.Push(current.name);
                    indices.Push(current.GetSiblingIndex().ToString(System.Globalization.CultureInfo.InvariantCulture));
                }
                var materialSlots = new JArray();
                var materials = renderer.sharedMaterials;
                for (var slot = 0; slot < materials.Length; slot++)
                {
                    var material = materials[slot];
                    materialSlots.Add(new JObject
                    {
                        ["slotIndex"] = slot,
                        ["material"] = ReadAssetReference(material),
                        ["shader"] = ReadAssetReference(material != null ? material.shader : null),
                    });
                }
                var mesh = renderer is SkinnedMeshRenderer skinned ? skinned.sharedMesh
                    : renderer is MeshRenderer ? renderer.GetComponent<MeshFilter>()?.sharedMesh : null;
                rows.Add(new JObject
                {
                    ["relativePath"] = string.Join("/", names),
                    // Index paths distinguish repeated sibling names and names containing '/'.
                    ["siblingIndexPath"] = string.Join("/", indices),
                    ["rendererComponentType"] = renderer.GetType().FullName,
                    ["rendererComponentIndex"] = Array.IndexOf(renderer.GetComponents<Renderer>()
                        .Where(item => item.GetType() == renderer.GetType()).ToArray(), renderer),
                    ["renderer"] = ReadAssetReference(renderer),
                    ["mesh"] = ReadAssetReference(mesh),
                    ["materialSlotCount"] = materials.Length,
                    ["materials"] = materialSlots,
                });
            }
            var hasMore = offset + rows.Count < renderers.Length;
            return new JObject
            {
                ["scope"] = "prefab_asset_descendants_including_inactive",
                ["pathRoot"] = root.name, ["rootRelativePath"] = "",
                ["componentIndexScope"] = "same_renderer_type_on_same_gameobject",
                ["offset"] = offset, ["count"] = rows.Count, ["total"] = renderers.Length,
                ["nextOffset"] = hasMore ? new JValue(offset + rows.Count) : JValue.CreateNull(),
                ["truncated"] = hasMore, ["renderers"] = rows,
            };
        }

        private static JToken ReadAssetReference(UnityEngine.Object item)
        {
            if (item == null) return JValue.CreateNull();
            string guid;
            long localId;
            var identified = AssetDatabase.TryGetGUIDAndLocalFileIdentifier(item, out guid, out localId);
            return new JObject
            {
                ["name"] = item.name, ["assetPath"] = AssetDatabase.GetAssetPath(item),
                ["guid"] = identified ? guid : null,
                ["localFileId"] = identified ? localId.ToString(System.Globalization.CultureInfo.InvariantCulture) : null,
            };
        }

        internal static JObject ReadObjectListing(string path, int offset, int limit)
        {
            var loaded = AssetDatabase.LoadAllAssetsAtPath(path);
            if (loaded == null || loaded.Length > 4096)
                throw new InvalidOperationException("Asset object enumeration unavailable or exceeds 4096 objects.");
            // Match ComputeObjectLayoutDigest byte-for-byte, from this one enumeration.
            // Do not filter transient objects or deduplicate: those are the evidence being inspected.
            var entries = loaded.Where(item => item != null).Select(item => new
            {
                Item = item,
                IncludedInCopyLayout = DuplicateProjectAssetTool.IsCopyLayoutObject(item),
                Entry = string.Join("\n", new[]
                {
                    item.GetType().AssemblyQualifiedName ?? item.GetType().FullName ?? item.GetType().Name,
                    AssetDatabase.IsMainAsset(item) ? "<main>" : (item.name ?? string.Empty),
                    ((int)item.hideFlags).ToString(System.Globalization.CultureInfo.InvariantCulture),
                }),
            }).OrderBy(row => row.Entry, StringComparer.Ordinal).ThenBy(row => row.Item.GetInstanceID()).ToArray();
            if (entries.Length == 0) throw new InvalidOperationException("The Unity authoring asset has no loadable objects.");
            if (entries.Any(row => row.Entry.Length > 8192))
                throw new InvalidOperationException("An asset layout entry exceeds 8192 characters; exact evidence was not truncated.");
            var encoded = new System.Text.StringBuilder();
            var allEncoded = new System.Text.StringBuilder();
            foreach (var row in entries)
            {
                var prefix = row.Entry.Length.ToString(System.Globalization.CultureInfo.InvariantCulture) + ":";
                allEncoded.Append(prefix); allEncoded.Append(row.Entry);
                if (row.IncludedInCopyLayout) { encoded.Append(prefix); encoded.Append(row.Entry); }
            }
            string digest = null, allDigest;
            using (var sha = System.Security.Cryptography.SHA256.Create())
            {
                if (encoded.Length > 0)
                    digest = string.Concat(sha.ComputeHash(System.Text.Encoding.UTF8.GetBytes(encoded.ToString()))
                        .Select(value => value.ToString("x2", System.Globalization.CultureInfo.InvariantCulture)));
                allDigest = string.Concat(sha.ComputeHash(System.Text.Encoding.UTF8.GetBytes(allEncoded.ToString()))
                    .Select(value => value.ToString("x2", System.Globalization.CultureInfo.InvariantCulture)));
            }
            var objects = new JArray();
            foreach (var row in entries.Skip(offset).Take(limit))
            {
                var item = row.Item;
                string guid;
                long localId;
                var identified = AssetDatabase.TryGetGUIDAndLocalFileIdentifier(item, out guid, out localId);
                objects.Add(new JObject
                {
                    ["type"] = item.GetType().AssemblyQualifiedName ?? item.GetType().FullName ?? item.GetType().Name,
                    ["name"] = item.name, ["isMain"] = AssetDatabase.IsMainAsset(item),
                    ["hideFlags"] = (int)item.hideFlags, ["persistent"] = EditorUtility.IsPersistent(item),
                    ["assetPath"] = AssetDatabase.GetAssetPath(item), ["guid"] = identified ? guid : null,
                    // Unity local IDs are 64-bit. A decimal string preserves them through JSON/JavaScript clients.
                    ["localFileId"] = identified ? localId.ToString(System.Globalization.CultureInfo.InvariantCulture) : null,
                    ["instanceId"] = item.GetInstanceID(), ["layoutEntry"] = row.Entry,
                    ["includedInCopyLayout"] = row.IncludedInCopyLayout,
                    ["exclusionReason"] = row.IncludedInCopyLayout ? null : "unity_import_diagnostic",
                });
            }
            var hasMore = offset + objects.Count < entries.Length;
            return new JObject
            {
                ["offset"] = offset, ["count"] = objects.Count, ["total"] = entries.Length,
                ["nextOffset"] = hasMore ? new JValue(offset + objects.Count) : JValue.CreateNull(),
                ["truncated"] = hasMore, ["objectLayoutDigest"] = digest, ["objects"] = objects,
                ["objectLayoutScope"] = "authoring_objects_excluding_unity_import_logs",
                ["allObjectLayoutDigest"] = allDigest,
            };
        }
    }

    [VRCForgeCommand(
        toolId: "vrc_instantiate_prefab",
        Summary = "Instantiate a prefab asset into the active scene, optionally under a parent, keeping the prefab link (Undo-registered). Supports preview mode."
    )]
    public static class InstantiatePrefabTool
    {
        public const string ToolName = "vrc_instantiate_prefab";

        public class InstantiatePrefabParameters
        {
            [VRCForgeInput("Project-relative path to the prefab asset (e.g. 'Assets/Outfits/Dress.prefab').", IsRequired = false)]
            public string assetPath { get; set; } = "";

            [VRCForgeInput("Prefab asset GUID (used when assetPath is omitted).", IsRequired = false)]
            public string guid { get; set; } = "";

            [VRCForgeInput("Full hierarchy path or unique name of the parent GameObject. Empty instantiates at the active scene root.", IsRequired = false)]
            public string parentPath { get; set; } = "";

            [VRCForgeInput("Optional name override for the new instance.", IsRequired = false)]
            public string name { get; set; } = "";

            [VRCForgeInput("Keep the instance's world position/rotation/scale when parenting (default true).", IsRequired = false)]
            public bool? worldPositionStays { get; set; } = true;

            [VRCForgeInput("Optional exact prefab GUID expected by the approved plan; mismatch fails closed.", IsRequired = false)]
            public string expectedPrefabGuid { get; set; } = "";

            [VRCForgeInput("Optional exact AssetDatabase dependency hash expected by the approved plan; mismatch fails closed.", IsRequired = false)]
            public string expectedAssetDependencyHash { get; set; } = "";

            [VRCForgeInput("Optional exact active/parent scene path expected for the new instance.", IsRequired = false)]
            public string expectedScenePath { get; set; } = "";

            [VRCForgeInput("Optional exact parent GlobalObjectId expected by the approved plan.", IsRequired = false)]
            public string expectedParentGlobalObjectId { get; set; } = "";

            [VRCForgeInput("Optional exact hierarchy path expected for the new instance; it must be absent before mutation.", IsRequired = false)]
            public string expectedResultPath { get; set; } = "";

            [VRCForgeInput("Approval-generated 64-hex nonce that binds Unity's new GlobalObjectId to the exact ordered continuation tools.", IsRequired = false)]
            public string approvedObjectReceiptNonce { get; set; } = "";

            [VRCForgeInput("Exact ordered continuation tools approved for the newly instantiated object.", IsRequired = false)]
            public string[] approvedContinuationTools { get; set; } = Array.Empty<string>();

            [VRCForgeInput("If true, only report what would happen without mutating the scene (default false).", IsRequired = false)]
            public bool? preview { get; set; } = false;
        }

        public static object HandleCommand(JObject @params)
        {
            var p = (@params ?? new JObject()).ToObject<InstantiatePrefabParameters>() ?? new InstantiatePrefabParameters();
            var mutationStarted = false;
            var mutatedPath = "";
            var mutatedGlobalObjectId = "";
            SavedSceneSnapshot beforeScene = null;
            var undoGroup = -1;
            var continuationNonce = (p.approvedObjectReceiptNonce ?? "").Trim();
            var continuationCount = 0;
            var continuationReserved = false;
            var mutationSceneHandle = -1;
            try
            {
                var path = AssetPrefabCore.ResolveAssetPath(p.assetPath, p.guid);
                var asset = AssetDatabase.LoadMainAssetAtPath(path);
                if (asset == null)
                {
                    return VRCForgeToolResult.Failed($"No asset found at '{path}'.");
                }
                if (!(asset is GameObject) || PrefabUtility.GetPrefabAssetType(asset) == PrefabAssetType.NotAPrefab)
                {
                    return VRCForgeToolResult.Failed($"Asset at '{path}' is not a prefab (type '{asset.GetType().Name}').");
                }
                var prefabGuid = AssetDatabase.AssetPathToGUID(path);
                if (!string.IsNullOrWhiteSpace(p.expectedPrefabGuid)
                    && !string.Equals(prefabGuid, p.expectedPrefabGuid.Trim(), StringComparison.OrdinalIgnoreCase))
                {
                    return VRCForgeToolResult.Failed("Prefab GUID drifted from the approved expectation.");
                }
                var dependencyHash = AssetDatabase.GetAssetDependencyHash(path).ToString();
                if (!string.IsNullOrWhiteSpace(p.expectedAssetDependencyHash)
                    && !string.Equals(dependencyHash, p.expectedAssetDependencyHash.Trim(), StringComparison.Ordinal))
                {
                    return VRCForgeToolResult.Failed("Prefab dependency hash drifted from the approved expectation.");
                }

                GameObject parent = null;
                var parentPath = ComponentCrudCore.NormalizePath(p.parentPath);
                if (!string.IsNullOrEmpty(parentPath))
                {
                    parent = ComponentCrudCore.ResolveGameObject(parentPath);
                }
                var resolvedParentPath = parent != null ? ComponentCrudCore.GetHierarchyPath(parent.transform) : null;
                var scene = parent != null ? parent.scene : UnityEngine.SceneManagement.SceneManager.GetActiveScene();
                mutationSceneHandle = scene.handle;
                var scenePath = scene.path;
                if (!string.IsNullOrWhiteSpace(p.expectedScenePath) && !string.Equals(scenePath, p.expectedScenePath.Trim(), StringComparison.Ordinal))
                {
                    return VRCForgeToolResult.Failed("Target scene drifted from the approved expectation.");
                }
                var parentGlobalObjectId = parent != null ? GlobalObjectId.GetGlobalObjectIdSlow(parent).ToString() : "";
                if (!string.IsNullOrWhiteSpace(p.expectedParentGlobalObjectId)
                    && !string.Equals(parentGlobalObjectId, p.expectedParentGlobalObjectId.Trim(), StringComparison.Ordinal))
                {
                    return VRCForgeToolResult.Failed("Prefab parent GlobalObjectId drifted from the approved expectation.");
                }
                var instanceName = string.IsNullOrWhiteSpace(p.name) ? asset.name : p.name.Trim();
                if (instanceName.Contains("/"))
                {
                    return VRCForgeToolResult.Failed("Prefab instance name cannot contain '/'.");
                }
                var expectedResultPath = string.IsNullOrEmpty(resolvedParentPath)
                    ? instanceName
                    : resolvedParentPath + "/" + instanceName;
                if (!string.IsNullOrWhiteSpace(p.expectedResultPath)
                    && !string.Equals(expectedResultPath, ComponentCrudCore.NormalizePath(p.expectedResultPath), StringComparison.Ordinal))
                {
                    return VRCForgeToolResult.Failed("Prefab result path differs from the approved expectation.");
                }
                if (AssetPrefabCore.CountHierarchyPath(expectedResultPath, scene.handle) != 0)
                {
                    return VRCForgeToolResult.Failed($"Prefab result path '{expectedResultPath}' already exists; refusing to create an ambiguous duplicate.");
                }
                var before = new
                {
                    gameObjectPath = expectedResultPath,
                    exists = false,
                    scenePath
                };
                var worldPositionStays = p.worldPositionStays ?? true;

                if (p.preview ?? false)
                {
                    var previewPayload = new
                    {
                        action = "instantiate_prefab",
                        preview = true,
                        assetPath = path,
                        prefabGuid,
                        dependencyHash,
                        scenePath,
                        name = instanceName,
                        parentPath = resolvedParentPath,
                        parentGlobalObjectId,
                        expectedResultPath,
                        mutationStarted = false,
                        committed = false,
                        commitState = "not_started",
                        persistenceState = "not_applicable",
                        readbackState = "not_required"
                    };
                    return VRCForgeToolResult.Completed(
                        parent != null
                            ? $"Preview: would instantiate '{path}' as '{instanceName}' under '{resolvedParentPath}'."
                            : $"Preview: would instantiate '{path}' as '{instanceName}' at the active scene root.",
                        previewPayload);
                }

                if (!string.IsNullOrWhiteSpace(continuationNonce) || (p.approvedContinuationTools?.Length ?? 0) > 0)
                {
                    continuationCount = VRCForgeApprovedObjectReceipt.Reserve(
                        continuationNonce,
                        p.approvedContinuationTools ?? Array.Empty<string>());
                    continuationReserved = true;
                }

                // Require a clean saved scene and isolate this operation from prior Undo work.
                beforeScene = SceneObjectCopyCore.ResolveSavedScene(scenePath, "prefab target scene");
                Undo.IncrementCurrentGroup();
                undoGroup = Undo.GetCurrentGroup();
                Undo.SetCurrentGroupName($"Instantiate {instanceName}");
                var instance = PrefabUtility.InstantiatePrefab(asset) as GameObject;
                if (instance == null)
                {
                    VRCForgeApprovedObjectReceipt.CancelReservation(continuationNonce);
                    continuationReserved = false;
                    return VRCForgeToolResult.Failed($"Unity refused to instantiate the prefab at '{path}'.");
                }
                mutationStarted = true;
                Undo.RegisterCreatedObjectUndo(instance, $"Instantiate {instanceName}");
                if (parent != null)
                {
                    Undo.SetTransformParent(
                        instance.transform,
                        parent.transform,
                        worldPositionStays,
                        $"Parent {instanceName}");
                }
                if (!string.IsNullOrWhiteSpace(p.name))
                {
                    instance.name = instanceName;
                }
                EditorUtility.SetDirty(instance);

                var goPath = ComponentCrudCore.GetHierarchyPath(instance.transform);
                mutatedPath = goPath;
                if (!string.Equals(goPath, expectedResultPath, StringComparison.Ordinal)
                    || AssetPrefabCore.CountHierarchyPath(goPath, scene.handle) != 1)
                {
                    VRCForgeApprovedObjectReceipt.CancelReservation(continuationNonce);
                    continuationReserved = false;
                    return CommittedFailure("Instantiated prefab hierarchy readback did not match the approved target.", mutatedPath, mutatedGlobalObjectId, beforeScene, undoGroup, scene.handle, expectedResultPath);
                }
                var globalObjectId = GlobalObjectId.GetGlobalObjectIdSlow(instance).ToString();
                mutatedGlobalObjectId = globalObjectId;
                var readbackPrefabPath = PrefabUtility.GetPrefabAssetPathOfNearestInstanceRoot(instance);
                var readbackPrefabGuid = string.IsNullOrEmpty(readbackPrefabPath) ? "" : AssetDatabase.AssetPathToGUID(readbackPrefabPath);
                if (!string.Equals(readbackPrefabGuid, prefabGuid, StringComparison.OrdinalIgnoreCase))
                {
                    VRCForgeApprovedObjectReceipt.CancelReservation(continuationNonce);
                    continuationReserved = false;
                    return CommittedFailure("Instantiated prefab identity readback did not match the approved asset.", mutatedPath, mutatedGlobalObjectId, beforeScene, undoGroup, scene.handle, expectedResultPath);
                }
                var readbackInstance = ComponentCrudCore.ResolveGameObject(goPath);
                var readbackGlobalObjectId = GlobalObjectId.GetGlobalObjectIdSlow(readbackInstance).ToString();
                var finalPrefabPath = PrefabUtility.GetPrefabAssetPathOfNearestInstanceRoot(readbackInstance);
                var finalPrefabGuid = string.IsNullOrEmpty(finalPrefabPath) ? "" : AssetDatabase.AssetPathToGUID(finalPrefabPath);
                var after = new
                {
                    gameObjectPath = ComponentCrudCore.GetHierarchyPath(readbackInstance.transform),
                    exists = true,
                    name = readbackInstance.name,
                    globalObjectId = readbackGlobalObjectId,
                    prefabPath = finalPrefabPath,
                    prefabGuid = finalPrefabGuid,
                    activeSelf = readbackInstance.activeSelf
                };
                // Save synchronously, verify saved scene evidence, then resolve the object
                // again. This does not reopen the scene or rebuild the Avatar.
                var afterScene = ComponentCrudCore.SaveAndResolveScene(beforeScene);
                var persistedInstance = SceneObjectCopyCore.ResolveUniqueGameObject(
                    afterScene.Scene, goPath, "instantiated prefab persisted readback");
                var persistedGlobalObjectId = GlobalObjectId.GetGlobalObjectIdSlow(persistedInstance).ToString();
                var persistedPrefabPath = PrefabUtility.GetPrefabAssetPathOfNearestInstanceRoot(persistedInstance);
                var persistedPrefabGuid = string.IsNullOrEmpty(persistedPrefabPath)
                    ? ""
                    : AssetDatabase.AssetPathToGUID(persistedPrefabPath);
                if (!string.Equals(persistedGlobalObjectId, readbackGlobalObjectId, StringComparison.Ordinal)
                    || !string.Equals(persistedPrefabGuid, prefabGuid, StringComparison.OrdinalIgnoreCase)
                    || !string.Equals(ComponentCrudCore.GetHierarchyPath(persistedInstance.transform), goPath, StringComparison.Ordinal))
                {
                    VRCForgeApprovedObjectReceipt.CancelReservation(continuationNonce);
                    continuationReserved = false;
                    return CommittedFailure("Instantiated prefab persisted readback did not match the committed target.", mutatedPath, mutatedGlobalObjectId, beforeScene, undoGroup, scene.handle, expectedResultPath);
                }
                if (continuationReserved)
                {
                    var boundGlobalObjectId = VRCForgeApprovedObjectReceipt.Bind(continuationNonce, persistedInstance);
                    continuationReserved = false;
                    if (!string.Equals(boundGlobalObjectId, persistedGlobalObjectId, StringComparison.Ordinal))
                    {
                        return CommittedFailure("Instantiated prefab continuation identity did not match persisted readback.", mutatedPath, mutatedGlobalObjectId, beforeScene, undoGroup, scene.handle, expectedResultPath);
                    }
                }
                Undo.CollapseUndoOperations(undoGroup);
                var payload = new
                {
                    schema = "vrcforge.instantiate_prefab_receipt.v1",
                    action = "instantiate_prefab",
                    preview = false,
                    assetPath = path,
                    gameObjectPath = goPath,
                    name = readbackInstance.name,
                    parentPath = resolvedParentPath,
                    instanceId = readbackInstance.GetInstanceID(),
                    prefabGuid,
                    dependencyHash,
                    scenePath,
                    parentGlobalObjectId,
                    globalObjectId = readbackGlobalObjectId,
                    continuationRegistered = continuationCount > 0,
                    continuationCount,
                    before,
                    after,
                    readback = new { persisted = true, data = after },
                    sceneSaved = true,
                    persistedReadback = true,
                    readbackVerified = true,
                    verified = true,
                    saved = true,
                    changed = true,
                    verification = new { state = "passed", checks = new[] { "scene_saved", "persisted_readback", "prefab_identity" } },
                    mutationStarted = true,
                    committed = true,
                    commitState = "committed",
                    persistenceState = "persisted",
                    readbackState = "verified",
                    pending = false,
                    note = "已修改并落盘"
                };
                return VRCForgeToolResult.Completed($"Instantiated '{path}' as '{goPath}'.", payload);
            }
            catch (Exception ex)
            {
                if (continuationReserved)
                {
                    VRCForgeApprovedObjectReceipt.CancelReservation(continuationNonce);
                }
                if (mutationStarted)
                {
                    return CommittedFailure($"Instantiate prefab failed after mutation: {ex.Message}", mutatedPath, mutatedGlobalObjectId, beforeScene, undoGroup, mutationSceneHandle, string.IsNullOrEmpty(mutatedPath) ? "" : mutatedPath);
                }
                return VRCForgeToolResult.Failed($"Instantiate prefab failed: {ex.Message}");
            }
        }

        private static VRCForgeToolResult CommittedFailure(string message, string gameObjectPath, string globalObjectId)
        {
            return VRCForgeToolResult.Failed(message, new
            {
                ok = false,
                committed = true,
                commitState = "unknown",
                checkpointRecoveryRequired = true,
                gameObjectPath = gameObjectPath ?? "",
                globalObjectId = globalObjectId ?? "",
            });
        }

        private static VRCForgeToolResult CommittedFailure(
            string message,
            string gameObjectPath,
            string globalObjectId,
            SavedSceneSnapshot beforeScene,
            int undoGroup,
            int sceneHandle,
            string expectedAbsentPath)
        {
            var restored = false;
            var cleanupDetail = "Scoped Undo cleanup was not available.";
            try
            {
                if (beforeScene != null && undoGroup >= 0)
                {
                    Undo.FlushUndoRecordObjects();
                    Undo.RevertAllDownToGroup(undoGroup);
                    EditorSceneManager.MarkSceneDirty(beforeScene.Scene);
                    if (!EditorSceneManager.SaveScene(beforeScene.Scene))
                        throw new InvalidOperationException("Unity did not save the restored scene.");
                    var restoredScene = SceneObjectCopyCore.ResolveSavedScene(beforeScene.Path, "prefab mutation cleanup");
                    restored = restoredScene.Guid == beforeScene.Guid
                        && restoredScene.Handle == beforeScene.Handle
                        && restoredScene.FileDigest == beforeScene.FileDigest
                        && restoredScene.MetaDigest == beforeScene.MetaDigest
                        && restoredScene.MetaIdentity == beforeScene.MetaIdentity
                        && AssetPrefabCore.CountHierarchyPath(expectedAbsentPath ?? string.Empty, sceneHandle) == 0;
                    cleanupDetail = restored ? "The scoped pre-state was restored and verified." : "The restored scene or target residue did not match the pre-state.";
                }
            }
            catch (Exception cleanupException)
            {
                cleanupDetail = cleanupException.Message;
            }
            return VRCForgeToolResult.Failed(message, new
            {
                ok = false,
                mutationStarted = true,
                mutationApplied = true,
                committed = restored ? (bool?)false : null,
                commitState = restored ? "rolled_back" : "unknown",
                checkpointRecoveryRequired = !restored,
                restored,
                cleanupVerified = restored,
                cleanupDetail,
                gameObjectPath = gameObjectPath ?? "",
                globalObjectId = globalObjectId ?? ""
            });
        }
    }

    [VRCForgeCommand(
        toolId: "vrc_unpack_prefab",
        Summary = "Unpack a prefab instance in the scene so its contents become plain GameObjects (Undo-registered). Supports preview mode."
    )]
    public static class UnpackPrefabTool
    {
        public const string ToolName = "vrc_unpack_prefab";

        public class UnpackPrefabParameters
        {
            [VRCForgeInput("Full hierarchy path or unique name of the prefab instance root to unpack.", IsRequired = true)]
            public string gameObjectPath { get; set; } = "";

            [VRCForgeInput("Optional exact GlobalObjectId expected for the prefab instance root.", IsRequired = false)]
            public string expectedGlobalObjectId { get; set; } = "";

            [VRCForgeInput("Optional exact prefab asset GUID expected before unpacking.", IsRequired = false)]
            public string expectedPrefabGuid { get; set; } = "";

            [VRCForgeInput("Optional exact prefab dependency hash expected before unpacking.", IsRequired = false)]
            public string expectedAssetDependencyHash { get; set; } = "";

            [VRCForgeInput("Optional exact scene path expected before unpacking.", IsRequired = false)]
            public string expectedScenePath { get; set; } = "";

            [VRCForgeInput("Approval-generated continuation nonce registered by vrc_instantiate_prefab.", IsRequired = false)]
            public string approvedObjectReceiptNonce { get; set; } = "";

            [VRCForgeInput("Unpack mode: 'outermost' (default, only this prefab layer) or 'completely' (all nested prefabs).", IsRequired = false)]
            public string mode { get; set; } = "outermost";

            [VRCForgeInput("If true, only report what would happen without mutating the scene (default false).", IsRequired = false)]
            public bool? preview { get; set; } = false;
        }

        public static object HandleCommand(JObject @params)
        {
            var p = (@params ?? new JObject()).ToObject<UnpackPrefabParameters>() ?? new UnpackPrefabParameters();
            var mutationStarted = false;
            var mutatedPath = "";
            var mutatedGlobalObjectId = "";
            try
            {
                var go = ComponentCrudCore.ResolveGameObject(p.gameObjectPath);
                var goPath = ComponentCrudCore.GetHierarchyPath(go.transform);
                mutatedPath = goPath;
                var globalObjectId = GlobalObjectId.GetGlobalObjectIdSlow(go).ToString();
                mutatedGlobalObjectId = globalObjectId;
                if (!string.IsNullOrWhiteSpace(p.expectedGlobalObjectId) && !string.Equals(globalObjectId, p.expectedGlobalObjectId.Trim(), StringComparison.Ordinal))
                    return VRCForgeToolResult.Failed("Prefab instance GlobalObjectId drifted from the approved expectation.");
                var prefabPath = PrefabUtility.GetPrefabAssetPathOfNearestInstanceRoot(go);
                var prefabGuid = string.IsNullOrEmpty(prefabPath) ? "" : AssetDatabase.AssetPathToGUID(prefabPath);
                if (!string.IsNullOrWhiteSpace(p.expectedPrefabGuid) && !string.Equals(prefabGuid, p.expectedPrefabGuid.Trim(), StringComparison.OrdinalIgnoreCase))
                    return VRCForgeToolResult.Failed("Prefab asset GUID drifted from the approved expectation.");
                var dependencyHash = string.IsNullOrEmpty(prefabPath) ? "" : AssetDatabase.GetAssetDependencyHash(prefabPath).ToString();
                if (!string.IsNullOrWhiteSpace(p.expectedAssetDependencyHash)
                    && !string.Equals(dependencyHash, p.expectedAssetDependencyHash.Trim(), StringComparison.Ordinal))
                    return VRCForgeToolResult.Failed("Prefab dependency hash drifted from the approved expectation.");
                var scenePath = go.scene.path;
                if (!string.IsNullOrWhiteSpace(p.expectedScenePath)
                    && !string.Equals(scenePath, p.expectedScenePath.Trim(), StringComparison.Ordinal))
                    return VRCForgeToolResult.Failed("Prefab instance scene drifted from the approved expectation.");

                if (!PrefabUtility.IsOutermostPrefabInstanceRoot(go))
                {
                    return VRCForgeToolResult.Failed(
                        $"'{goPath}' is not the outermost root of a prefab instance; nothing to unpack.");
                }

                var completely = string.Equals(
                    (p.mode ?? string.Empty).Trim(),
                    "completely",
                    StringComparison.OrdinalIgnoreCase);
                var unpackMode = completely ? PrefabUnpackMode.Completely : PrefabUnpackMode.OutermostRoot;
                var modeLabel = completely ? "completely" : "outermost";

                if (p.preview ?? false)
                {
                    var previewPayload = new
                    {
                        action = "unpack_prefab",
                        preview = true,
                        gameObjectPath = goPath,
                        unpackMode = modeLabel
                        , globalObjectId
                        , prefabGuid
                        , dependencyHash
                        , scenePath
                    };
                    return VRCForgeToolResult.Completed(
                        $"Preview: would unpack prefab instance '{goPath}' ({modeLabel}).",
                        previewPayload);
                }

                var continuationConsumed = false;
                if (!string.IsNullOrWhiteSpace(p.approvedObjectReceiptNonce))
                {
                    VRCForgeApprovedObjectReceipt.Consume(
                        p.approvedObjectReceiptNonce.Trim(),
                        ToolName,
                        go);
                    continuationConsumed = true;
                }

                var before = new
                {
                    gameObjectPath = goPath,
                    globalObjectId,
                    prefabPath,
                    prefabGuid,
                    isPartOfAnyPrefab = PrefabUtility.IsPartOfAnyPrefab(go)
                };
                PrefabUtility.UnpackPrefabInstance(go, unpackMode, InteractionMode.UserAction);
                mutationStarted = true;
                EditorUtility.SetDirty(go);
                var readbackObject = ComponentCrudCore.ResolveGameObject(goPath);
                var readbackGlobalObjectId = GlobalObjectId.GetGlobalObjectIdSlow(readbackObject).ToString();
                var readbackPrefabPath = PrefabUtility.GetPrefabAssetPathOfNearestInstanceRoot(readbackObject);
                var readbackIsPartOfAnyPrefab = PrefabUtility.IsPartOfAnyPrefab(readbackObject);
                var unpacked = string.IsNullOrEmpty(readbackPrefabPath) && !readbackIsPartOfAnyPrefab;
                if (!unpacked)
                {
                    return CommittedFailure("Prefab unpack readback still reports a prefab instance.", mutatedPath, mutatedGlobalObjectId);
                }
                var after = new
                {
                    gameObjectPath = ComponentCrudCore.GetHierarchyPath(readbackObject.transform),
                    globalObjectId = readbackGlobalObjectId,
                    prefabPath = readbackPrefabPath,
                    isPartOfAnyPrefab = readbackIsPartOfAnyPrefab,
                    unpacked
                };

                var payload = new
                {
                    action = "unpack_prefab",
                    preview = false,
                    gameObjectPath = goPath,
                    unpackMode = modeLabel
                    , previousGlobalObjectId = globalObjectId
                    , globalObjectId = readbackGlobalObjectId
                    , prefabGuid
                    , dependencyHash
                    , scenePath
                    , unpacked
                    , continuationConsumed
                    , before
                    , after
                    , pending = true
                    , note = "已修改，尚未落盘"
                };
                return VRCForgeToolResult.Completed($"Unpacked prefab instance '{goPath}' ({modeLabel}).", payload);
            }
            catch (Exception ex)
            {
                if (mutationStarted)
                {
                    return CommittedFailure($"Unpack prefab failed after mutation: {ex.Message}", mutatedPath, mutatedGlobalObjectId);
                }
                return VRCForgeToolResult.Failed($"Unpack prefab failed: {ex.Message}");
            }
        }

        private static VRCForgeToolResult CommittedFailure(string message, string gameObjectPath, string globalObjectId)
        {
            return VRCForgeToolResult.Failed(message, new
            {
                ok = false,
                committed = true,
                commitState = "unknown",
                checkpointRecoveryRequired = true,
                gameObjectPath = gameObjectPath ?? "",
                globalObjectId = globalObjectId ?? "",
            });
        }
    }
}
