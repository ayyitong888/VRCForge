using System;
using System.Collections.Generic;
using System.Linq;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.SceneManagement;
using VRCForge.Core.MCP;

namespace VRCForge.Editor
{
    // ------------------------------------------------------------------
    // Generic Unity GameObject CRUD layer (v0.5, second cut).
    //
    // Six MCP tools. Hierarchy resolution / path helpers are reused from
    // ComponentCrudCore (same assembly), so this stays reflection-based and
    // never hard-references Modular Avatar / VRChat SDK assemblies:
    //   vrc_get_gameobject      (read)
    //   vrc_create_gameobject   (write, Undo-registered)
    //   vrc_rename_gameobject   (write, Undo-registered)
    //   vrc_reparent_gameobject (write, Undo-registered)
    //   vrc_delete_gameobject   (write, Undo-registered)
    //   vrc_set_gameobject_active (write, Undo-registered)
    //
    // Every write tool registers a Unity Undo entry so the checkpoint timeline
    // (bound to Undo) can roll it back, and supports a preview mode that reports
    // what *would* change without mutating, feeding the per-action approval card.
    // ------------------------------------------------------------------

    [VRCForgeCommand(
        toolId: "vrc_get_gameobject",
        Summary = "Describe a scene GameObject: path, active state, tag/layer, parent, children, and components (read-only).",
        Access = VRCForgeCommandAccess.ReadOnly
    )]
    public static class GetGameObjectTool
    {
        public const string ToolName = "vrc_get_gameobject";

        public class GetGameObjectParameters
        {
            [VRCForgeInput("Full hierarchy path (e.g. 'Avatar/Body') or unique name of the GameObject.", IsRequired = true)]
            public string gameObjectPath { get; set; } = "";
        }

        public static object HandleCommand(JObject @params)
        {
            var p = (@params ?? new JObject()).ToObject<GetGameObjectParameters>() ?? new GetGameObjectParameters();
            try
            {
                var go = ComponentCrudCore.ResolveGameObject(p.gameObjectPath);
                var t = go.transform;

                var children = new List<object>();
                for (var i = 0; i < t.childCount; i++)
                {
                    var child = t.GetChild(i);
                    children.Add(new
                    {
                        name = child.name,
                        gameObjectPath = ComponentCrudCore.GetHierarchyPath(child),
                        activeSelf = child.gameObject.activeSelf
                    });
                }

                var components = go.GetComponents<Component>()
                    .Where(c => c != null)
                    .Select(c => c.GetType().FullName)
                    .ToArray();

                var payload = new
                {
                    gameObjectPath = ComponentCrudCore.GetHierarchyPath(t),
                    globalObjectId = GlobalObjectId.GetGlobalObjectIdSlow(go).ToString(),
                    name = go.name,
                    activeSelf = go.activeSelf,
                    activeInHierarchy = go.activeInHierarchy,
                    tag = go.tag,
                    layer = go.layer,
                    layerName = LayerMask.LayerToName(go.layer),
                    isStatic = go.isStatic,
                    sceneName = go.scene.IsValid() ? go.scene.name : null,
                    scenePath = go.scene.IsValid() ? go.scene.path : null,
                    hierarchyPathCount = go.scene.IsValid()
                        ? AssetPrefabCore.CountHierarchyPath(ComponentCrudCore.GetHierarchyPath(t), go.scene.handle)
                        : 0,
                    parentPath = t.parent != null ? ComponentCrudCore.GetHierarchyPath(t.parent) : null,
                    siblingIndex = t.GetSiblingIndex(),
                    childCount = t.childCount,
                    componentCount = components.Length,
                    components = components,
                    children = children
                };

                return VRCForgeToolResult.Completed(
                    $"GameObject '{go.name}' at '{payload.gameObjectPath}' ({components.Length} component(s), {t.childCount} child(ren)).",
                    payload);
            }
            catch (Exception ex)
            {
                return VRCForgeToolResult.Failed($"Get GameObject failed: {ex.Message}");
            }
        }
    }

    [VRCForgeCommand(
        toolId: "vrc_create_gameobject",
        Summary = "Create and save a new empty GameObject, optionally parented under another scene object (Undo-registered). Supports preview mode."
    )]
    public static class CreateGameObjectTool
    {
        public const string ToolName = "vrc_create_gameobject";

        public class CreateGameObjectParameters
        {
            [VRCForgeInput("Name for the new GameObject (default 'GameObject').", IsRequired = false)]
            public string name { get; set; } = "";

            [VRCForgeInput("Full hierarchy path or unique name of the parent GameObject. Empty creates at the active scene root.", IsRequired = false)]
            public string parentPath { get; set; } = "";

            [VRCForgeInput("If true, only report what would happen without mutating the scene (default false).", IsRequired = false)]
            public bool? preview { get; set; } = false;
        }

        public static object HandleCommand(JObject @params)
        {
            var p = (@params ?? new JObject()).ToObject<CreateGameObjectParameters>() ?? new CreateGameObjectParameters();
            var mutationStarted = false;
            SavedSceneSnapshot beforeScene = null;
            GameObject created = null;
            var createdPath = string.Empty;
            try
            {
                var name = SceneObjectCopyCore.NormalizeObjectName(
                    string.IsNullOrWhiteSpace(p.name) ? "GameObject" : p.name.Trim(),
                    "name");
                var parentPath = ComponentCrudCore.NormalizePath(p.parentPath);
                GameObject parent = null;
                if (!string.IsNullOrEmpty(parentPath))
                {
                    parent = ComponentCrudCore.ResolveGameObject(parentPath);
                }
                var resolvedParentPath = parent != null ? ComponentCrudCore.GetHierarchyPath(parent.transform) : null;
                var targetScene = SceneManager.GetActiveScene();

                if (p.preview ?? false)
                {
                    var previewPayload = new
                    {
                        action = "create_gameobject",
                        preview = true,
                        name,
                        parentPath = resolvedParentPath
                    };
                    return VRCForgeToolResult.Completed(
                        parent != null
                            ? $"Preview: would create '{name}' under '{resolvedParentPath}'."
                            : $"Preview: would create '{name}' at the active scene root.",
                        previewPayload);
                }

                if (parent != null && parent.scene.handle != targetScene.handle)
                {
                    return VRCForgeToolResult.Failed("Create GameObject requires the parent to belong to the active scene.");
                }
                beforeScene = SceneObjectCopyCore.ResolveSavedScene(targetScene.path, "target scene");
                if (beforeScene.Handle != targetScene.handle)
                {
                    throw new InvalidOperationException("The active saved scene changed before creation.");
                }
                createdPath = string.IsNullOrEmpty(resolvedParentPath)
                    ? name
                    : resolvedParentPath + "/" + name;
                if (AssetPrefabCore.CountHierarchyPath(createdPath, targetScene.handle) != 0)
                {
                    return VRCForgeToolResult.Failed("Create GameObject requires a unique destination hierarchy path.");
                }

                created = new GameObject(name);
                mutationStarted = true;
                Undo.RegisterCreatedObjectUndo(created, $"Create {name}");
                if (parent != null)
                {
                    Undo.SetTransformParent(created.transform, parent.transform, $"Create {name} under parent");
                }
                EditorUtility.SetDirty(created);
                EditorSceneManager.MarkSceneDirty(targetScene);

                if (!EditorSceneManager.SaveScene(targetScene))
                {
                    throw new InvalidOperationException("The target scene could not be saved.");
                }
                var afterScene = SceneObjectCopyCore.ResolveSavedScene(beforeScene.Path, "saved target scene");
                var readback = SceneObjectCopyCore.ResolveUniqueGameObject(
                    afterScene.Scene,
                    createdPath,
                    "created object");
                if (!ReferenceEquals(readback, created)
                    || AssetPrefabCore.CountHierarchyPath(createdPath, afterScene.Handle) != 1
                    || afterScene.Guid != beforeScene.Guid
                    || afterScene.Handle != beforeScene.Handle
                    || afterScene.FileDigest == beforeScene.FileDigest
                    || afterScene.MetaDigest != beforeScene.MetaDigest
                    || afterScene.MetaIdentity != beforeScene.MetaIdentity)
                {
                    throw new InvalidOperationException("The created GameObject persisted readback was not exact.");
                }
                var globalObjectId = GlobalObjectId.GetGlobalObjectIdSlow(readback);
                var payload = new
                {
                    action = "create_gameobject",
                    preview = false,
                    name = readback.name,
                    gameObjectPath = createdPath,
                    parentPath = resolvedParentPath,
                    instanceId = readback.GetInstanceID(),
                    globalObjectId = globalObjectId.ToString(),
                    scenePath = afterScene.Path,
                    sceneSaved = true,
                    persistedReadback = true,
                    sceneFileDigestBefore = beforeScene.FileDigest,
                    sceneFileDigestAfter = afterScene.FileDigest,
                    sceneFileIdentityBefore = beforeScene.FileIdentity,
                    sceneFileIdentityAfter = afterScene.FileIdentity
                };
                return VRCForgeToolResult.Completed($"Created and saved GameObject '{createdPath}'.", payload);
            }
            catch (Exception ex)
            {
                var restored = !mutationStarted;
                try
                {
                    if (mutationStarted && beforeScene != null)
                    {
                        if (created != null)
                        {
                            UnityEngine.Object.DestroyImmediate(created);
                        }
                        EditorSceneManager.MarkSceneDirty(beforeScene.Scene);
                        if (EditorSceneManager.SaveScene(beforeScene.Scene))
                        {
                            var cleanup = SceneObjectCopyCore.ResolveSavedScene(
                                beforeScene.Path,
                                "restored target scene");
                            restored = cleanup.Guid == beforeScene.Guid
                                && cleanup.Handle == beforeScene.Handle
                                && cleanup.FileDigest == beforeScene.FileDigest
                                && cleanup.FileIdentity == beforeScene.FileIdentity
                                && cleanup.MetaDigest == beforeScene.MetaDigest
                                && cleanup.MetaIdentity == beforeScene.MetaIdentity
                                && AssetPrefabCore.CountHierarchyPath(createdPath, cleanup.Handle) == 0;
                        }
                    }
                }
                catch
                {
                    restored = false;
                }
                return VRCForgeToolResult.Failed(
                    $"Create GameObject failed: {ex.Message}",
                    new
                    {
                        mutationStarted,
                        restored,
                        cleanupVerified = restored,
                        cleanupRequired = !restored,
                        checkpointRecoveryRequired = !restored,
                        operationState = restored ? "restored" : "checkpoint_restore_required"
                    });
            }
        }
    }

    [VRCForgeCommand(
        toolId: "vrc_rename_gameobject",
        Summary = "Rename a scene GameObject (Undo-registered). Supports preview mode."
    )]
    public static class RenameGameObjectTool
    {
        public const string ToolName = "vrc_rename_gameobject";

        public class RenameGameObjectParameters
        {
            [VRCForgeInput("Full hierarchy path or unique name of the target GameObject.", IsRequired = true)]
            public string gameObjectPath { get; set; } = "";

            [VRCForgeInput("New name for the GameObject.", IsRequired = true)]
            public string newName { get; set; } = "";

            [VRCForgeInput("If true, only report what would happen without mutating the scene (default false).", IsRequired = false)]
            public bool? preview { get; set; } = false;
        }

        public static object HandleCommand(JObject @params)
        {
            var p = (@params ?? new JObject()).ToObject<RenameGameObjectParameters>() ?? new RenameGameObjectParameters();
            try
            {
                if (string.IsNullOrWhiteSpace(p.newName))
                {
                    return VRCForgeToolResult.Failed("Rename requires a non-empty 'newName' argument.");
                }
                var newName = p.newName.Trim();

                var go = ComponentCrudCore.ResolveGameObject(p.gameObjectPath);
                var oldName = go.name;
                var oldPath = ComponentCrudCore.GetHierarchyPath(go.transform);

                if (p.preview ?? false)
                {
                    var previewPayload = new
                    {
                        action = "rename_gameobject",
                        preview = true,
                        oldName,
                        newName,
                        gameObjectPath = oldPath
                    };
                    return VRCForgeToolResult.Completed(
                        $"Preview: would rename '{oldPath}' to '{newName}'.",
                        previewPayload);
                }

                Undo.RecordObject(go, $"Rename {oldName}");
                go.name = newName;
                EditorUtility.SetDirty(go);

                var newPath = ComponentCrudCore.GetHierarchyPath(go.transform);
                var payload = new
                {
                    action = "rename_gameobject",
                    preview = false,
                    oldName,
                    newName = go.name,
                    oldPath,
                    gameObjectPath = newPath
                };
                return VRCForgeToolResult.Completed($"Renamed '{oldName}' to '{go.name}'.", payload);
            }
            catch (Exception ex)
            {
                return VRCForgeToolResult.Failed($"Rename GameObject failed: {ex.Message}");
            }
        }
    }

    [VRCForgeCommand(
        toolId: "vrc_reparent_gameobject",
        Summary = "Move a scene GameObject under a new parent (or to the scene root) preserving world transform by default (Undo-registered). Supports preview mode."
    )]
    public static class ReparentGameObjectTool
    {
        public const string ToolName = "vrc_reparent_gameobject";

        public class ReparentGameObjectParameters
        {
            [VRCForgeInput("Full hierarchy path or unique name of the GameObject to move.", IsRequired = true)]
            public string gameObjectPath { get; set; } = "";

            [VRCForgeInput("Full hierarchy path or unique name of the new parent. Empty moves the object to the scene root.", IsRequired = false)]
            public string newParentPath { get; set; } = "";

            [VRCForgeInput("Keep the object's world position/rotation/scale (default true).", IsRequired = false)]
            public bool? worldPositionStays { get; set; } = true;

            [VRCForgeInput("If true, only report what would happen without mutating the scene (default false).", IsRequired = false)]
            public bool? preview { get; set; } = false;
        }

        public static object HandleCommand(JObject @params)
        {
            var p = (@params ?? new JObject()).ToObject<ReparentGameObjectParameters>() ?? new ReparentGameObjectParameters();
            try
            {
                var go = ComponentCrudCore.ResolveGameObject(p.gameObjectPath);
                var oldParent = go.transform.parent;
                var oldParentPath = oldParent != null ? ComponentCrudCore.GetHierarchyPath(oldParent) : null;

                var newParentPath = ComponentCrudCore.NormalizePath(p.newParentPath);
                var toRoot = string.IsNullOrEmpty(newParentPath);
                GameObject newParent = null;
                if (!toRoot)
                {
                    newParent = ComponentCrudCore.ResolveGameObject(newParentPath);
                    if (newParent == go)
                    {
                        return VRCForgeToolResult.Failed("Cannot parent a GameObject to itself.");
                    }
                    if (newParent.transform.IsChildOf(go.transform))
                    {
                        return VRCForgeToolResult.Failed(
                            $"Cannot reparent '{go.name}' under its own descendant '{newParent.name}' (would create a cycle).");
                    }
                }

                var worldPositionStays = p.worldPositionStays ?? true;
                var resolvedNewParentPath = newParent != null ? ComponentCrudCore.GetHierarchyPath(newParent.transform) : null;

                if (p.preview ?? false)
                {
                    var previewPayload = new
                    {
                        action = "reparent_gameobject",
                        preview = true,
                        gameObjectPath = ComponentCrudCore.GetHierarchyPath(go.transform),
                        oldParentPath,
                        newParentPath = resolvedNewParentPath,
                        worldPositionStays
                    };
                    return VRCForgeToolResult.Completed(
                        toRoot
                            ? $"Preview: would move '{go.name}' to the scene root."
                            : $"Preview: would move '{go.name}' under '{resolvedNewParentPath}'.",
                        previewPayload);
                }

                Undo.SetTransformParent(
                    go.transform,
                    toRoot ? null : newParent.transform,
                    worldPositionStays,
                    $"Reparent {go.name}");
                EditorUtility.SetDirty(go);

                var payload = new
                {
                    action = "reparent_gameobject",
                    preview = false,
                    gameObjectPath = ComponentCrudCore.GetHierarchyPath(go.transform),
                    oldParentPath,
                    newParentPath = resolvedNewParentPath,
                    worldPositionStays
                };
                return VRCForgeToolResult.Completed(
                    toRoot
                        ? $"Moved '{go.name}' to the scene root."
                        : $"Moved '{go.name}' under '{resolvedNewParentPath}'.",
                    payload);
            }
            catch (Exception ex)
            {
                return VRCForgeToolResult.Failed($"Reparent GameObject failed: {ex.Message}");
            }
        }
    }

    [VRCForgeCommand(
        toolId: "vrc_delete_gameobject",
        Summary = "Delete a scene GameObject and its children (Undo-registered). Supports preview mode."
    )]
    public static class DeleteGameObjectTool
    {
        public const string ToolName = "vrc_delete_gameobject";

        public class DeleteGameObjectParameters
        {
            [VRCForgeInput("Full hierarchy path or unique name of the GameObject to delete.", IsRequired = true)]
            public string gameObjectPath { get; set; } = "";

            [VRCForgeInput("If true, only report what would happen without mutating the scene (default false).", IsRequired = false)]
            public bool? preview { get; set; } = false;
        }

        public static object HandleCommand(JObject @params)
        {
            var p = (@params ?? new JObject()).ToObject<DeleteGameObjectParameters>() ?? new DeleteGameObjectParameters();
            try
            {
                var go = ComponentCrudCore.ResolveGameObject(p.gameObjectPath);
                var goPath = ComponentCrudCore.GetHierarchyPath(go.transform);
                var childCount = go.transform.childCount;
                var componentCount = go.GetComponents<Component>().Count(c => c != null);

                if (p.preview ?? false)
                {
                    var previewPayload = new
                    {
                        action = "delete_gameobject",
                        preview = true,
                        gameObjectPath = goPath,
                        childCount,
                        componentCount
                    };
                    return VRCForgeToolResult.Completed(
                        $"Preview: would delete '{goPath}' ({childCount} child(ren), {componentCount} component(s)).",
                        previewPayload);
                }

                Undo.DestroyObjectImmediate(go);

                var payload = new
                {
                    action = "delete_gameobject",
                    preview = false,
                    gameObjectPath = goPath,
                    childCount,
                    componentCount
                };
                return VRCForgeToolResult.Completed($"Deleted '{goPath}'.", payload);
            }
            catch (Exception ex)
            {
                return VRCForgeToolResult.Failed($"Delete GameObject failed: {ex.Message}");
            }
        }
    }

    [VRCForgeCommand(
        toolId: "vrc_set_gameobject_active",
        Summary = "Set a scene GameObject's active-self state (Undo-registered). Supports preview mode."
    )]
    public static class SetGameObjectActiveTool
    {
        public const string ToolName = "vrc_set_gameobject_active";

        public class SetGameObjectActiveParameters
        {
            [VRCForgeInput("Full hierarchy path or unique name of the target GameObject.", IsRequired = true)]
            public string gameObjectPath { get; set; } = "";

            [VRCForgeInput("Desired active-self state (true/false).", IsRequired = true)]
            public bool? active { get; set; }

            [VRCForgeInput("If true, only report what would happen without mutating the scene (default false).", IsRequired = false)]
            public bool? preview { get; set; } = false;
        }

        public static object HandleCommand(JObject @params)
        {
            var p = (@params ?? new JObject()).ToObject<SetGameObjectActiveParameters>() ?? new SetGameObjectActiveParameters();
            try
            {
                var rawParams = @params ?? new JObject();
                if (rawParams["active"] == null)
                {
                    return VRCForgeToolResult.Failed("Set active requires an 'active' boolean argument.");
                }
                var active = rawParams["active"].ToObject<bool>();

                var go = ComponentCrudCore.ResolveGameObject(p.gameObjectPath);
                var goPath = ComponentCrudCore.GetHierarchyPath(go.transform);
                var oldActive = go.activeSelf;

                if (p.preview ?? false)
                {
                    var previewPayload = new
                    {
                        action = "set_gameobject_active",
                        preview = true,
                        gameObjectPath = goPath,
                        oldActive,
                        newActive = active
                    };
                    return VRCForgeToolResult.Completed(
                        $"Preview: would set '{goPath}' active-self {oldActive} -> {active}.",
                        previewPayload);
                }

                Undo.RecordObject(go, $"Set Active {go.name}");
                go.SetActive(active);
                EditorUtility.SetDirty(go);

                var payload = new
                {
                    action = "set_gameobject_active",
                    preview = false,
                    gameObjectPath = goPath,
                    oldActive,
                    newActive = go.activeSelf,
                    activeInHierarchy = go.activeInHierarchy
                };
                return VRCForgeToolResult.Completed($"Set '{goPath}' active-self to {go.activeSelf}.", payload);
            }
            catch (Exception ex)
            {
                return VRCForgeToolResult.Failed($"Set GameObject active failed: {ex.Message}");
            }
        }
    }
}
