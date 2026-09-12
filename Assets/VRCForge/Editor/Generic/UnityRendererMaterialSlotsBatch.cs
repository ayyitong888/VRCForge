using System;
using System.Collections.Generic;
using System.Linq;
using System.Text;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using VRCForge.Core.MCP;

namespace VRCForge.Editor
{
    internal static class UnityRendererMaterialSlotsBatch
    {
        private const string Schema = "vrcforge.renderer_material_slot.v1";
        private const int MaxBytes = 512 * 1024;

        internal static JArray ValidateEnvelope(JObject request)
        {
            var allowed = new HashSet<string>(new[] { "assignments", "expectedProjectPath", "preview", "expectedBatchPlan" }, StringComparer.Ordinal);
            if (request.Properties().Any(p => !allowed.Contains(p.Name))) throw new InvalidOperationException("assignments cannot be combined with single-slot or unknown fields.");
            if (Encoding.UTF8.GetByteCount(request.ToString(Formatting.None)) > MaxBytes) throw new InvalidOperationException("Renderer assignments exceed 512 KiB.");
            var rows = request["assignments"] as JArray;
            if (rows == null || rows.Count < 1 || rows.Count > 256) throw new InvalidOperationException("assignments must contain 1..256 entries.");
            foreach (var token in rows)
            {
                var row = token as JObject;
                if (row == null || row.Properties().Any(p => !new[] { "rendererPath", "rendererComponentId", "slotIndex", "newMaterialAssetPath" }.Contains(p.Name))) throw new InvalidOperationException("Each assignment must contain exact renderer, slot and material fields.");
                Text(row, "rendererPath"); Text(row, "newMaterialAssetPath");
                var id = Text(row, "rendererComponentId");
                if (id.Length != 64 || id.Any(c => !Uri.IsHexDigit(c)) || id != id.ToLowerInvariant()) throw new InvalidOperationException("Each assignment requires an exact rendererComponentId.");
                if (row["slotIndex"]?.Type != JTokenType.Integer || row["slotIndex"].Value<int>() < 0 || row["slotIndex"].Value<int>() > 1024) throw new InvalidOperationException("slotIndex is invalid.");
            }
            return (JArray)rows.DeepClone();
        }

        private static string Text(JObject value, string key)
        {
            if (value[key]?.Type != JTokenType.String || string.IsNullOrWhiteSpace(value[key].Value<string>())) throw new InvalidOperationException(key + " is required.");
            return value[key].Value<string>();
        }

        // Pure final-state planning; does not mutate the observed arrays.
        internal static JArray BuildExpected(JArray beforeRenderers, JArray assignments)
        {
            var after = (JArray)beforeRenderers.DeepClone();
            var seen = new HashSet<string>(StringComparer.Ordinal);
            foreach (JObject row in assignments)
            {
                var id = Text(row, "rendererComponentId");
                var slot = row["slotIndex"].Value<int>();
                if (!seen.Add(id + ":" + slot)) throw new InvalidOperationException("Duplicate renderer/slot assignment.");
                var matches = after.OfType<JObject>().Where(r => r["rendererComponentId"].Value<string>() == id).ToArray();
                if (matches.Length != 1 || matches[0]["rendererPath"].Value<string>() != Text(row, "rendererPath")) throw new InvalidOperationException("Assignment renderer identity is missing or ambiguous.");
                var slots = (JArray)matches[0]["slots"];
                if (slot < 0 || slot >= slots.Count) throw new InvalidOperationException("slotIndex is outside sharedMaterials.");
                var next = row["newMaterial"] as JObject;
                if (next == null) throw new InvalidOperationException("New persistent material evidence is missing.");
                var identity = Text(next, "assetPath") + "|" + Text(next, "assetGuid");
                if (slots[slot].Value<string>() == identity) throw new InvalidOperationException("A requested slot already uses its target material.");
                slots[slot] = identity;
            }
            return after;
        }

        internal static void Verify(JToken expected, JToken actual)
        {
            if (!JToken.DeepEquals(expected, actual)) throw new InvalidOperationException("Renderer batch state differs from the sealed plan.");
        }

        private sealed class Plan
        {
            internal SavedSceneSnapshot Scene;
            internal readonly List<RendererMaterialSlotTool.Target> Targets = new List<RendererMaterialSlotTool.Target>();
            internal readonly Dictionary<string, RendererMaterialSlotTool.MaterialEvidence> Materials = new Dictionary<string, RendererMaterialSlotTool.MaterialEvidence>(StringComparer.Ordinal);
            internal JObject Evidence;
        }

        private static JObject SceneEvidence(SavedSceneSnapshot scene)
        {
            return JObject.FromObject(new { scenePath = scene.Path, sceneGuid = scene.Guid, sceneHandle = scene.Handle,
                sceneFileDigest = scene.FileDigest, sceneMetaDigest = scene.MetaDigest, sceneMetaIdentity = scene.MetaIdentity });
        }

        private static JObject RendererEvidence(RendererMaterialSlotTool.Target target)
        {
            return JObject.FromObject(new { rendererPath = target.Identity.rendererPath,
                rendererComponentId = target.Identity.componentId, rendererComponentType = target.Identity.componentType,
                rendererComponentIndex = target.Identity.componentIndex,
                slots = target.Renderer.sharedMaterials.Select(RendererMaterialSlotTool.MaterialIdentity).ToArray() });
        }

        private static Plan Build(JArray requests)
        {
            var plan = new Plan();
            var before = new JArray();
            var rows = new JArray();
            foreach (JObject request in requests)
            {
                RendererMaterialSlotTool.Target target;
                var scene = RendererMaterialSlotTool.ResolveTarget(Text(request, "rendererPath"), Text(request, "rendererComponentId"), out target);
                if (plan.Scene == null) plan.Scene = scene;
                else Verify(SceneEvidence(plan.Scene), SceneEvidence(scene));
                if (!plan.Targets.Any(t => t.Identity.componentId == target.Identity.componentId)) { plan.Targets.Add(target); before.Add(RendererEvidence(target)); }
                var path = RendererMaterialSlotTool.NormalizeMaterialPath(Text(request, "newMaterialAssetPath"));
                RendererMaterialSlotTool.MaterialEvidence next;
                if (!plan.Materials.TryGetValue(path, out next)) { next = RendererMaterialSlotTool.ReadMaterial(path, "new batch material"); plan.Materials.Add(path, next); }
                if (EditorUtility.IsDirty(next.Material)) throw new InvalidOperationException("New batch material has unsaved changes.");
                var slot = request["slotIndex"].Value<int>();
                var slots = target.Renderer.sharedMaterials;
                if (slot >= slots.Length) throw new InvalidOperationException("slotIndex is outside sharedMaterials.");
                var old = slots[slot] == null ? null : RendererMaterialSlotTool.ReadMaterialAsset(slots[slot], "before batch material");
                if (old != null && EditorUtility.IsDirty(old.Material)) throw new InvalidOperationException("Before batch material has unsaved changes.");
                rows.Add(JObject.FromObject(new { rendererPath = target.Identity.rendererPath, rendererComponentId = target.Identity.componentId,
                    slotIndex = slot, newMaterialAssetPath = path, beforeMaterial = RendererMaterialSlotTool.MaterialPayload(old), newMaterial = RendererMaterialSlotTool.MaterialPayload(next) }));
            }
            plan.Evidence = new JObject { ["scene"] = SceneEvidence(plan.Scene), ["beforeRenderers"] = before, ["assignments"] = rows, ["afterRenderers"] = BuildExpected(before, rows) };
            return plan;
        }

        internal static object HandleCommand(JObject request)
        {
            Plan plan = null;
            var undoGroup = -1;
            var started = false;
            try
            {
                var rows = ValidateEnvelope(request);
                RendererMaterialSlotTool.RequireProject(request);
                plan = Build(rows);
                var sealedRequest = (JObject)request.DeepClone(); sealedRequest["expectedBatchPlan"] = plan.Evidence;
                if (Encoding.UTF8.GetByteCount(sealedRequest.ToString(Formatting.None)) > MaxBytes) throw new InvalidOperationException("Sealed renderer batch exceeds 512 KiB; split the batch.");
                if (request["preview"]?.Value<bool>() == true)
                    return VRCForgeToolResult.Completed("Renderer assignments fully preflighted; no mutation.", new { schema = Schema, batch = true, preview = true, verified = true,
                        mutationStarted = false, applied = false, committed = false, sceneSaved = false, persistedReadback = false, plan = plan.Evidence });
                if (!(request["expectedBatchPlan"] is JObject)) throw new InvalidOperationException("A sealed expectedBatchPlan from preview is required.");
                Verify(request["expectedBatchPlan"], plan.Evidence);
                // Re-read the full plan immediately before any mutation.
                Verify(plan.Evidence, Build(rows).Evidence);
                Undo.IncrementCurrentGroup(); undoGroup = Undo.GetCurrentGroup(); Undo.SetCurrentGroupName("Set VRCForge renderer material slots");
                foreach (var target in plan.Targets) Undo.RegisterCompleteObjectUndo(target.Renderer, "Set VRCForge renderer material slots");
                started = true;
                foreach (var target in plan.Targets)
                {
                    var slots = target.Renderer.sharedMaterials;
                    foreach (JObject row in plan.Evidence["assignments"])
                        if (row["rendererComponentId"].Value<string>() == target.Identity.componentId)
                            slots[row["slotIndex"].Value<int>()] = plan.Materials[row["newMaterialAssetPath"].Value<string>()].Material;
                    target.Renderer.sharedMaterials = slots;
                }
                Undo.FlushUndoRecordObjects();
                EditorSceneManager.MarkSceneDirty(plan.Scene.Scene);
                if (!EditorSceneManager.SaveScene(plan.Scene.Scene)) throw new InvalidOperationException("The renderer batch scene could not be saved.");
                AssetDatabase.Refresh(ImportAssetOptions.ForceSynchronousImport);
                var saved = SceneObjectCopyCore.ResolveSavedScene(plan.Scene.Path, "renderer batch persisted scene");
                var sceneAfter = SceneEvidence(saved);
                var sceneWithoutDigest = (JObject)sceneAfter.DeepClone(); sceneWithoutDigest["sceneFileDigest"] = plan.Scene.FileDigest;
                Verify(plan.Evidence["scene"], sceneWithoutDigest);
                if (saved.FileDigest == plan.Scene.FileDigest) throw new InvalidOperationException("Renderer batch scene bytes did not change.");
                var actual = ReadRenderers(plan);
                Verify(plan.Evidence["afterRenderers"], actual);
                foreach (JObject row in plan.Evidence["assignments"])
                {
                    var next = RendererMaterialSlotTool.ReadMaterial(row["newMaterialAssetPath"].Value<string>(), "persisted new material");
                    Verify(row["newMaterial"], JToken.FromObject(RendererMaterialSlotTool.MaterialPayload(next)));
                    if (row["beforeMaterial"].Type != JTokenType.Null)
                    {
                        var old = RendererMaterialSlotTool.ReadMaterial(row["beforeMaterial"]["assetPath"].Value<string>(), "unchanged before material");
                        Verify(row["beforeMaterial"], JToken.FromObject(RendererMaterialSlotTool.MaterialPayload(old)));
                    }
                }
                Undo.CollapseUndoOperations(undoGroup);
                return VRCForgeToolResult.Completed("Renderer assignments saved and independently read back.", new { schema = Schema, batch = true, preview = false, verified = true,
                    mutationStarted = true, applied = true, committed = true, commitState = "committed", commitStateKnown = true, sceneSaved = true, persistedReadback = true,
                    assignmentCount = rows.Count, rendererCount = plan.Targets.Count, plan = plan.Evidence,
                    readback = new { scene = sceneAfter, renderers = actual } });
            }
            catch (Exception exception)
            {
                var restored = false;
                if (started && plan != null)
                {
                    try
                    {
                        Undo.RevertAllDownToGroup(undoGroup);
                        if (!EditorSceneManager.SaveScene(plan.Scene.Scene)) throw new InvalidOperationException("Batch restoration save failed.");
                        var restoredScene = SceneObjectCopyCore.ResolveSavedScene(plan.Scene.Path, "restored renderer batch scene");
                        Verify(plan.Evidence["scene"], SceneEvidence(restoredScene));
                        Verify(plan.Evidence["beforeRenderers"], ReadRenderers(plan));
                        restored = true;
                    }
                    catch { }
                }
                return VRCForgeToolResult.FailedWithCode("renderer_material_slots_batch_failed", exception.Message,
                    new { schema = Schema, batch = true, verified = false, mutationStarted = started, applied = false, committed = false,
                        sceneSaved = false, persistedReadback = false, restored, commitState = started ? (restored ? "rolled_back" : "unknown") : "not_started",
                        commitStateKnown = !started || restored, checkpointRecoveryRequired = started && !restored });
            }
        }

        private static JArray ReadRenderers(Plan plan)
        {
            var actual = new JArray();
            foreach (var before in plan.Targets)
            {
                RendererMaterialSlotTool.Target target;
                RendererMaterialSlotTool.ResolveTarget(before.Identity.rendererPath, before.Identity.componentId, out target);
                actual.Add(RendererEvidence(target));
            }
            return actual;
        }
    }
}
