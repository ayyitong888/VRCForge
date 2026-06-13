using System;
using System.Collections.Generic;
using System.Linq;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Tools;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEngine;

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

    [McpForUnityTool(
        name: "vrc_get_gameobject",
        Description = "Describe a scene GameObject: path, active state, tag/layer, parent, children, and components (read-only)."
    )]
    public static class GetGameObjectTool
    {
        public const string ToolName = "vrc_get_gameobject";

        public class GetGameObjectParameters
        {
            [ToolParameter("Full hierarchy path (e.g. 'Avatar/Body') or unique name of the GameObject.", Required = true)]
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
                    name = go.name,
                    activeSelf = go.activeSelf,
                    activeInHierarchy = go.activeInHierarchy,
                    tag = go.tag,
                    layer = go.layer,
                    layerName = LayerMask.LayerToName(go.layer),
                    isStatic = go.isStatic,
                    sceneName = go.scene.IsValid() ? go.scene.name : null,
                    parentPath = t.parent != null ? ComponentCrudCore.GetHierarchyPath(t.parent) : null,
                    siblingIndex = t.GetSiblingIndex(),
                    childCount = t.childCount,
                    componentCount = components.Length,
                    components = components,
                    children = children
                };

                return new SuccessResponse(
                    $"GameObject '{go.name}' at '{payload.gameObjectPath}' ({components.Length} component(s), {t.childCount} child(ren)).",
                    payload);
            }
            catch (Exception ex)
            {
                return new ErrorResponse($"Get GameObject failed: {ex.Message}");
            }
        }
    }

    [McpForUnityTool(
        name: "vrc_create_gameobject",
        Description = "Create a new empty GameObject, optionally parented under another scene object (Undo-registered). Supports preview mode."
    )]
    public static class CreateGameObjectTool
    {
        public const string ToolName = "vrc_create_gameobject";

        public class CreateGameObjectParameters
        {
            [ToolParameter("Name for the new GameObject (default 'GameObject').", Required = false)]
            public string name { get; set; } = "";

            [ToolParameter("Full hierarchy path or unique name of the parent GameObject. Empty creates at the active scene root.", Required = false)]
            public string parentPath { get; set; } = "";

            [ToolParameter("If true, only report what would happen without mutating the scene (default false).", Required = false)]
            public bool? preview { get; set; } = false;
        }

        public static object HandleCommand(JObject @params)
        {
            var p = (@params ?? new JObject()).ToObject<CreateGameObjectParameters>() ?? new CreateGameObjectParameters();
            try
            {
                var name = string.IsNullOrWhiteSpace(p.name) ? "GameObject" : p.name.Trim();
                var parentPath = ComponentCrudCore.NormalizePath(p.parentPath);
                GameObject parent = null;
                if (!string.IsNullOrEmpty(parentPath))
                {
                    parent = ComponentCrudCore.ResolveGameObject(parentPath);
                }
                var resolvedParentPath = parent != null ? ComponentCrudCore.GetHierarchyPath(parent.transform) : null;

                if (p.preview ?? false)
                {
                    var previewPayload = new
                    {
                        action = "create_gameobject",
                        preview = true,
                        name,
                        parentPath = resolvedParentPath
                    };
                    return new SuccessResponse(
                        parent != null
                            ? $"Preview: would create '{name}' under '{resolvedParentPath}'."
                            : $"Preview: would create '{name}' at the active scene root.",
                        previewPayload);
                }

                var go = new GameObject(name);
                Undo.RegisterCreatedObjectUndo(go, $"Create {name}");
                if (parent != null)
                {
                    Undo.SetTransformParent(go.transform, parent.transform, $"Create {name} under parent");
                }
                EditorUtility.SetDirty(go);

                var goPath = ComponentCrudCore.GetHierarchyPath(go.transform);
                var payload = new
                {
                    action = "create_gameobject",
                    preview = false,
                    name = go.name,
                    gameObjectPath = goPath,
                    parentPath = resolvedParentPath,
                    instanceId = go.GetInstanceID()
                };
                return new SuccessResponse($"Created GameObject '{goPath}'.", payload);
            }
            catch (Exception ex)
            {
                return new ErrorResponse($"Create GameObject failed: {ex.Message}");
            }
        }
    }

    [McpForUnityTool(
        name: "vrc_rename_gameobject",
        Description = "Rename a scene GameObject (Undo-registered). Supports preview mode."
    )]
    public static class RenameGameObjectTool
    {
        public const string ToolName = "vrc_rename_gameobject";

        public class RenameGameObjectParameters
        {
            [ToolParameter("Full hierarchy path or unique name of the target GameObject.", Required = true)]
            public string gameObjectPath { get; set; } = "";

            [ToolParameter("New name for the GameObject.", Required = true)]
            public string newName { get; set; } = "";

            [ToolParameter("If true, only report what would happen without mutating the scene (default false).", Required = false)]
            public bool? preview { get; set; } = false;
        }

        public static object HandleCommand(JObject @params)
        {
            var p = (@params ?? new JObject()).ToObject<RenameGameObjectParameters>() ?? new RenameGameObjectParameters();
            try
            {
                if (string.IsNullOrWhiteSpace(p.newName))
                {
                    return new ErrorResponse("Rename requires a non-empty 'newName' argument.");
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
                    return new SuccessResponse(
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
                return new SuccessResponse($"Renamed '{oldName}' to '{go.name}'.", payload);
            }
            catch (Exception ex)
            {
                return new ErrorResponse($"Rename GameObject failed: {ex.Message}");
            }
        }
    }

    [McpForUnityTool(
        name: "vrc_reparent_gameobject",
        Description = "Move a scene GameObject under a new parent (or to the scene root) preserving world transform by default (Undo-registered). Supports preview mode."
    )]
    public static class ReparentGameObjectTool
    {
        public const string ToolName = "vrc_reparent_gameobject";

        public class ReparentGameObjectParameters
        {
            [ToolParameter("Full hierarchy path or unique name of the GameObject to move.", Required = true)]
            public string gameObjectPath { get; set; } = "";

            [ToolParameter("Full hierarchy path or unique name of the new parent. Empty moves the object to the scene root.", Required = false)]
            public string newParentPath { get; set; } = "";

            [ToolParameter("Keep the object's world position/rotation/scale (default true).", Required = false)]
            public bool? worldPositionStays { get; set; } = true;

            [ToolParameter("If true, only report what would happen without mutating the scene (default false).", Required = false)]
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
                        return new ErrorResponse("Cannot parent a GameObject to itself.");
                    }
                    if (newParent.transform.IsChildOf(go.transform))
                    {
                        return new ErrorResponse(
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
                    return new SuccessResponse(
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
                return new SuccessResponse(
                    toRoot
                        ? $"Moved '{go.name}' to the scene root."
                        : $"Moved '{go.name}' under '{resolvedNewParentPath}'.",
                    payload);
            }
            catch (Exception ex)
            {
                return new ErrorResponse($"Reparent GameObject failed: {ex.Message}");
            }
        }
    }

    [McpForUnityTool(
        name: "vrc_delete_gameobject",
        Description = "Delete a scene GameObject and its children (Undo-registered). Supports preview mode."
    )]
    public static class DeleteGameObjectTool
    {
        public const string ToolName = "vrc_delete_gameobject";

        public class DeleteGameObjectParameters
        {
            [ToolParameter("Full hierarchy path or unique name of the GameObject to delete.", Required = true)]
            public string gameObjectPath { get; set; } = "";

            [ToolParameter("If true, only report what would happen without mutating the scene (default false).", Required = false)]
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
                    return new SuccessResponse(
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
                return new SuccessResponse($"Deleted '{goPath}'.", payload);
            }
            catch (Exception ex)
            {
                return new ErrorResponse($"Delete GameObject failed: {ex.Message}");
            }
        }
    }

    [McpForUnityTool(
        name: "vrc_set_gameobject_active",
        Description = "Set a scene GameObject's active-self state (Undo-registered). Supports preview mode."
    )]
    public static class SetGameObjectActiveTool
    {
        public const string ToolName = "vrc_set_gameobject_active";

        public class SetGameObjectActiveParameters
        {
            [ToolParameter("Full hierarchy path or unique name of the target GameObject.", Required = true)]
            public string gameObjectPath { get; set; } = "";

            [ToolParameter("Desired active-self state (true/false).", Required = true)]
            public bool? active { get; set; }

            [ToolParameter("If true, only report what would happen without mutating the scene (default false).", Required = false)]
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
                    return new ErrorResponse("Set active requires an 'active' boolean argument.");
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
                    return new SuccessResponse(
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
                return new SuccessResponse($"Set '{goPath}' active-self to {go.activeSelf}.", payload);
            }
            catch (Exception ex)
            {
                return new ErrorResponse($"Set GameObject active failed: {ex.Message}");
            }
        }
    }
}
