using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Security.Cryptography;
using System.Text;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEngine;
using VRCForge.Core.MCP;

namespace VRCForge.Editor
{
    internal static class UnityMaterialShaderBatch
    {
        private const string Schema = "vrcforge.material_shader_assignment.v1";
        private const int MaxBytes = 512 * 1024;
        internal static JArray Validate(JObject request)
        {
            var allowed = new[] { "assignments", "preview", "saveAssets", "expectedProjectPath", "expectedPreviewDigest", "expectedAssignments" };
            if (request.Properties().Any(p => !allowed.Contains(p.Name)) || request["saveAssets"]?.Value<bool>() == false)
                throw new InvalidOperationException("Batch shader assignment accepts only pure asset rows and requires saveAssets.");
            CheckSize(request);
            var rows = request["assignments"] as JArray;
            if (rows == null || rows.Count < 1 || rows.Count > 128) throw new InvalidOperationException("assignments requires 1..128 rows.");
            var targets = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
            foreach (var token in rows)
            {
                var row = token as JObject;
                if (row == null || row.Properties().Any(p => p.Name != "materialAssetPath" && p.Name != "shaderName" && p.Name != "shaderAssetPath")
                    || row["materialAssetPath"]?.Type != JTokenType.String || row["shaderName"]?.Type != JTokenType.String || string.IsNullOrWhiteSpace(row["shaderName"].Value<string>()))
                    throw new InvalidOperationException("Each assignment requires exact materialAssetPath/shaderName and optional shaderAssetPath.");
                var path = MaterialShaderTool.NormalizeOptionalAssetPath(row["materialAssetPath"].Value<string>(), false);
                if (!path.EndsWith(".mat", StringComparison.OrdinalIgnoreCase) || !targets.Add(path)) throw new InvalidOperationException("Material targets must be unique .mat assets.");
            }
            return (JArray)rows.DeepClone();
        }

        private static void CheckSize(JToken value)
        {
            if (Encoding.UTF8.GetByteCount(value.ToString(Formatting.None)) > MaxBytes) throw new InvalidOperationException("Sealed shader batch exceeds 512 KiB.");
        }

        internal static string Digest(JArray previews)
        {
            var fields = new[] { "materialAssetPath", "materialAssetGuid", "materialFileDigestBefore", "beforeShader", "beforeShaderAssetPath", "beforeShaderAssetGuid", "requestedShader", "shaderAssetPath", "shaderAssetGuid", "sharedImpactDigest" };
            var text = new StringBuilder("vrcforge.material_shader_batch.v1:");
            foreach (var row in previews) foreach (var field in fields)
            {
                var value = row[field]?.Value<string>() ?? "";
                text.Append(Encoding.UTF8.GetByteCount(value)).Append(':').Append(value);
            }
            using (var sha = SHA256.Create()) return string.Concat(sha.ComputeHash(Encoding.UTF8.GetBytes(text.ToString())).Select(b => b.ToString("x2")));
        }

        private static JObject Preview(JObject row, string project, MaterialShaderTool.BatchSharedMaterialImpact context)
        {
            var request = (JObject)row.DeepClone(); request["preview"] = true; request["saveAssets"] = true; request["expectedProjectPath"] = project;
            var result = MaterialShaderTool.HandleSingleCommand(request, context) as VRCForgeToolResult;
            if (result == null || !result.IsSuccessful || result.Payload == null) throw new InvalidOperationException(result?.Message ?? "Shader preview failed.");
            var payload = JObject.FromObject(result.Payload);
            if (payload["verified"]?.Value<bool>() != true || payload["preview"]?.Value<bool>() != true) throw new InvalidOperationException("Shader preview is not verified.");
            return payload;
        }

        private sealed class Edit
        {
            internal string Path, BeforeJson, ExpectedJson;
            internal JObject BeforeRenderState;
            internal int RowIndex;
            internal byte[] Bytes;
            internal Material Material;
            internal Shader Shader;
            internal StableAssetEvidence Before, Owned;
            internal bool Mutated;
        }

        // Never restore a path just because it appeared in the request.
        private static bool Restore(Edit edit)
        {
            if (!edit.Mutated) return true;
            try
            {
                if (edit.Material == null || EditorJsonUtility.ToJson(edit.Material) != edit.ExpectedJson) return false;
                var current = SceneObjectCopyCore.ReadStableAssetEvidence(edit.Path, "shader batch recovery");
                var expected = edit.Owned ?? edit.Before;
                if (!SceneObjectCopyCore.StableAssetEvidenceMatches(expected, current, true)) return false;
                if (!SceneObjectCopyCore.StableAssetEvidenceMatches(edit.Before, current, true))
                {
                    var absolute = SceneObjectCopyCore.ToAbsoluteAssetPath(edit.Path);
                    var temp = absolute + ".vrcforge-restore-" + Guid.NewGuid().ToString("N");
                    try { File.WriteAllBytes(temp, edit.Bytes); File.Replace(temp, absolute, null); }
                    finally { if (File.Exists(temp)) File.Delete(temp); }
                }
                AssetDatabase.ImportAsset(edit.Path, ImportAssetOptions.ForceSynchronousImport | ImportAssetOptions.ForceUpdate);
                var restored = AssetDatabase.LoadAssetAtPath<Material>(edit.Path);
                var restoredEvidence = SceneObjectCopyCore.ReadStableAssetEvidence(edit.Path, "restored shader batch material");
                return restored != null && EditorJsonUtility.ToJson(restored) == edit.BeforeJson
                    && restoredEvidence.Guid == edit.Before.Guid && restoredEvidence.Meta.Digest == edit.Before.Meta.Digest
                    && restoredEvidence.File.Digest == edit.Before.File.Digest;
            }
            catch { return false; }
        }

        internal static JObject SavedMemoryStateFailure(int rowIndex, string path, bool dirty,
            string beforeJson, string expectedJson, string actualJson)
        {
            var failed = new JArray();
            if (dirty) failed.Add("materialDirty");
            if (actualJson != expectedJson) failed.Add("serializedMaterial");
            if (failed.Count == 0) return null;
            return new JObject { ["rowIndex"] = rowIndex, ["materialAssetPath"] = path,
                ["failurePhase"] = "post_save_memory", ["failedPredicates"] = failed,
                ["before"] = JsonEvidence(beforeJson),
                ["expected"] = new JObject { ["json"] = JsonEvidence(expectedJson) },
                ["actual"] = new JObject { ["dirty"] = dirty, ["json"] = JsonEvidence(actualJson) },
                ["jsonDifference"] = JsonDifference(expectedJson, actualJson) };
        }

        internal static JObject SavedStateFailure(int rowIndex, string path, string expectedGuid, string actualGuid,
            string expectedMeta, string actualMeta, bool materialPresent, bool shaderMatches,
            string beforeJson, string expectedJson, string actualJson)
        {
            var failed = new JArray();
            if (actualGuid != expectedGuid) failed.Add("materialGuid");
            if (actualMeta != expectedMeta) failed.Add("metaDigest");
            if (!materialPresent) failed.Add("materialPresent");
            if (!shaderMatches) failed.Add("shaderIdentity");
            if (actualJson != expectedJson) failed.Add("serializedMaterial");
            if (failed.Count == 0) return null;
            return new JObject { ["rowIndex"] = rowIndex, ["materialAssetPath"] = path,
                ["failurePhase"] = "post_import_ownership", ["failedPredicates"] = failed,
                ["before"] = JsonEvidence(beforeJson),
                ["expected"] = new JObject { ["materialGuid"] = expectedGuid, ["metaDigest"] = expectedMeta, ["json"] = JsonEvidence(expectedJson) },
                ["actual"] = new JObject { ["materialGuid"] = actualGuid, ["metaDigest"] = actualMeta, ["materialPresent"] = materialPresent,
                    ["shaderMatches"] = shaderMatches, ["json"] = JsonEvidence(actualJson) },
                ["jsonDifference"] = JsonDifference(expectedJson, actualJson) };
        }

        private static string Sha256(string value)
        {
            if (value == null) return null;
            using (var sha = SHA256.Create()) return string.Concat(sha.ComputeHash(Encoding.UTF8.GetBytes(value)).Select(b => b.ToString("x2")));
        }

        private static JObject JsonEvidence(string value)
        {
            return new JObject { ["present"] = value != null, ["utf8Bytes"] = value == null ? 0 : Encoding.UTF8.GetByteCount(value),
                ["sha256"] = Sha256(value), ["preview"] = value == null ? null : value.Substring(0, Math.Min(256, value.Length)),
                ["previewTruncated"] = value != null && value.Length > 256 };
        }

        private static JObject JsonDifference(string expected, string actual)
        {
            var fields = new JArray();
            var result = new JObject { ["fields"] = fields, ["truncated"] = false, ["representationOnly"] = false };
            if (expected == actual) return result;
            if (expected == null || actual == null || Encoding.UTF8.GetByteCount(expected) > 256 * 1024 || Encoding.UTF8.GetByteCount(actual) > 256 * 1024)
            { result["truncated"] = true; result["reason"] = "missing_or_exceeds_256KiB_json_limit"; return result; }
            try
            {
                var left = JToken.Parse(expected); var right = JToken.Parse(actual);
                if (JToken.DeepEquals(left, right)) { result["representationOnly"] = true; return result; }
                var pending = new Stack<Tuple<string, JToken, JToken, int>>();
                pending.Push(Tuple.Create("", left, right, 0));
                var visited = 0;
                while (pending.Count > 0 && fields.Count < 32 && visited++ < 4096)
                {
                    var item = pending.Pop(); var a = item.Item2; var b = item.Item3;
                    if (JToken.DeepEquals(a, b)) continue;
                    if (item.Item4 < 32 && a is JObject ao && b is JObject bo)
                    {
                        foreach (var key in ao.Properties().Select(v => v.Name).Union(bo.Properties().Select(v => v.Name)).OrderByDescending(v => v, StringComparer.Ordinal))
                            pending.Push(Tuple.Create(item.Item1 + "/" + key.Replace("~", "~0").Replace("/", "~1"), ao[key], bo[key], item.Item4 + 1));
                    }
                    else if (item.Item4 < 32 && a is JArray aa && b is JArray ba)
                    {
                        for (var i = Math.Max(aa.Count, ba.Count) - 1; i >= 0; i--)
                            pending.Push(Tuple.Create(item.Item1 + "/" + i, i < aa.Count ? aa[i] : null, i < ba.Count ? ba[i] : null, item.Item4 + 1));
                    }
                    else fields.Add(new JObject { ["path"] = item.Item1.Substring(0, Math.Min(1024, item.Item1.Length)), ["pathTruncated"] = item.Item1.Length > 1024, ["pathSha256"] = Sha256(item.Item1), ["expected"] = JsonEvidence(a?.ToString(Formatting.None)), ["actual"] = JsonEvidence(b?.ToString(Formatting.None)) });
                }
                result["truncated"] = pending.Count > 0;
            }
            catch (JsonException) { result["truncated"] = true; result["reason"] = "json_parse_failed"; }
            return result;
        }

        private static JObject PreflightAssignments(JArray rows)
        {
            var materials = new List<Material>();
            var shaders = new List<Shader>();
            var assignments = new Dictionary<Material, Shader>();
            foreach (var row in rows)
            {
                var path = MaterialShaderTool.NormalizeOptionalAssetPath(row["materialAssetPath"].Value<string>(), false);
                var material = AssetDatabase.LoadAssetAtPath<Material>(path);
                MaterialShaderTool.InspectWritableMaterialAsset(material);
                var shader = MaterialShaderTool.ResolveShader(row["shaderName"].Value<string>(), row["shaderAssetPath"]?.Value<string>() ?? "");
                if (material == null || shader == null) throw new InvalidOperationException("Shader capability preflight target could not be resolved.");
                materials.Add(material); shaders.Add(shader); assignments.Add(material, shader);
            }
            var blocked = new JArray();
            for (var i = 0; i < materials.Count; i++)
            {
                var failure = MaterialShaderTool.PreflightShaderAssignment(materials[i], shaders[i], assignments);
                if (failure == null) continue;
                failure["rowIndex"] = i;
                blocked.Add(failure);
            }
            return blocked.Count == 0 ? null : new JObject { ["failurePhase"] = "shader_capability_preflight", ["blockedAssignments"] = blocked };
        }

        internal static object HandleCommand(JObject request)
        {
            var edits = new List<Edit>();
            JObject failureDetails = null;
            var activeRow = -1;
            var activePath = "";
            var failurePhase = "preflight";
            try
            {
                var rows = Validate(request);
                var project = request["expectedProjectPath"]?.Value<string>() ?? "";
                if (!MaterialShaderTool.MatchesCurrentProject(project)) throw new InvalidOperationException("Shader batch project mismatch.");
                failurePhase = "shader_capability_preflight";
                failureDetails = PreflightAssignments(rows);
                if (failureDetails != null) throw new InvalidOperationException("One or more materials cannot independently accept the requested shader; no materials were changed.");
                var sharedImpact = MaterialShaderTool.BuildBatchSharedMaterialImpact(rows);
                var previews = new JArray(rows.Select(row => Preview((JObject)row, project, sharedImpact)));
                var digest = Digest(previews);
                var envelope = new JObject { ["schema"] = Schema, ["batch"] = true, ["ok"] = true, ["preview"] = true,
                    ["verified"] = true, ["saved"] = false, ["changed"] = false, ["assignments"] = previews, ["previewDigest"] = digest };
                CheckSize(envelope);
                if (request["preview"]?.Value<bool>() == true) return VRCForgeToolResult.Completed("Shader asset batch preview.", envelope);
                if (request["expectedPreviewDigest"]?.Value<string>() != digest || !JToken.DeepEquals(request["expectedAssignments"], previews))
                    throw new InvalidOperationException("Shader batch preview changed before mutation.");
                long totalBytes = 0;
                foreach (JObject preview in previews)
                {
                    var path = preview["materialAssetPath"].Value<string>();
                    var material = AssetDatabase.LoadAssetAtPath<Material>(path);
                    MaterialShaderTool.InspectWritableMaterialAsset(material);
                    var evidence = SceneObjectCopyCore.ReadStableAssetEvidence(path, "shader batch pre-state");
                    if (evidence.Guid != preview["materialAssetGuid"].Value<string>() || evidence.File.Digest != preview["materialFileDigestBefore"].Value<string>())
                        throw new InvalidOperationException("Shader batch material identity drifted.");
                    totalBytes += new FileInfo(SceneObjectCopyCore.ToAbsoluteAssetPath(path)).Length;
                    if (totalBytes > 64L * 1024 * 1024) throw new InvalidOperationException("Shader batch rollback snapshots exceed 64 MiB.");
                    edits.Add(new Edit { RowIndex = edits.Count, Path = path, Material = material, Shader = MaterialShaderTool.ResolveShader(preview["requestedShader"].Value<string>(), preview["shaderAssetPath"].Value<string>()),
                        Before = evidence, Bytes = File.ReadAllBytes(SceneObjectCopyCore.ToAbsoluteAssetPath(path)), BeforeJson = EditorJsonUtility.ToJson(material),
                        BeforeRenderState = MaterialShaderTool.CaptureMaterialRenderState(material) });
                }
                foreach (var edit in edits)
                    if (!SceneObjectCopyCore.StableAssetEvidenceMatches(edit.Before, SceneObjectCopyCore.ReadStableAssetEvidence(edit.Path, "shader batch final preflight"), true)
                        || EditorJsonUtility.ToJson(edit.Material) != edit.BeforeJson || edit.Shader == null) throw new InvalidOperationException("Shader batch changed before mutation.");
                failurePhase = "shader_capability_final_preflight";
                failureDetails = PreflightAssignments(rows);
                if (failureDetails != null) throw new InvalidOperationException("Material shader capability changed before mutation.");
                foreach (var edit in edits)
                {
                    activeRow = edit.RowIndex; activePath = edit.Path; failurePhase = "set_shader";
                    if (edit.Material.shader == edit.Shader) continue;
                    edit.Mutated = true; edit.ExpectedJson = edit.BeforeJson;
                    edit.Material.shader = edit.Shader;
                    MaterialShaderTool.RestoreMaterialRenderState(edit.Material, edit.BeforeRenderState);
                    edit.ExpectedJson = EditorJsonUtility.ToJson(edit.Material);
                    EditorUtility.SetDirty(edit.Material);
                }
                activeRow = -1; activePath = ""; failurePhase = "save_assets";
                AssetDatabase.SaveAssets();
                Exception savedValidationException = null;
                JObject savedValidationFailure = null;
                foreach (var edit in edits.Where(e => e.Mutated))
                {
                    try
                    {
                        activeRow = edit.RowIndex; activePath = edit.Path; failurePhase = "post_save_memory";
                        failureDetails = SavedMemoryStateFailure(edit.RowIndex, edit.Path, EditorUtility.IsDirty(edit.Material),
                            edit.BeforeJson, edit.ExpectedJson, EditorJsonUtility.ToJson(edit.Material));
                        if (failureDetails != null) throw new InvalidOperationException("Material changed during batch save.");
                        AssetDatabase.ImportAsset(edit.Path, ImportAssetOptions.ForceSynchronousImport | ImportAssetOptions.ForceUpdate);
                        var persisted = AssetDatabase.LoadAssetAtPath<Material>(edit.Path);
                        var owned = SceneObjectCopyCore.ReadStableAssetEvidence(edit.Path, "saved shader batch ownership");
                        MaterialShaderTool.VerifyMaterialRenderState(persisted, edit.BeforeRenderState);
                        failurePhase = "post_import_ownership";
                        failureDetails = SavedStateFailure(edit.RowIndex, edit.Path, edit.Before.Guid, owned.Guid,
                            edit.Before.Meta.Digest, owned.Meta.Digest, persisted != null, persisted != null && persisted.shader == edit.Shader,
                            edit.BeforeJson, edit.ExpectedJson, persisted == null ? null : EditorJsonUtility.ToJson(persisted));
                        if (failureDetails != null)
                        {
                            failureDetails["expected"]["shaderName"] = edit.Shader == null ? null : edit.Shader.name;
                            failureDetails["expected"]["shaderAssetPath"] = AssetDatabase.GetAssetPath(edit.Shader);
                            failureDetails["actual"]["shaderName"] = persisted == null || persisted.shader == null ? null : persisted.shader.name;
                            failureDetails["actual"]["shaderAssetPath"] = persisted == null ? null : AssetDatabase.GetAssetPath(persisted.shader);
                            failureDetails["beforeFileDigest"] = edit.Before.File.Digest;
                            failureDetails["actualFileDigest"] = owned.File.Digest;
                            throw new InvalidOperationException("Saved material state changed.");
                        }
                        edit.Owned = owned;
                    }
                    catch (Exception exception)
                    {
                        // A shared save already touched later rows. Verify them too so
                        // recovery can own valid rows without claiming the failed row.
                        if (savedValidationException == null)
                        {
                            savedValidationException = exception;
                            savedValidationFailure = failureDetails ?? new JObject
                            {
                                ["rowIndex"] = activeRow, ["materialAssetPath"] = activePath,
                                ["failurePhase"] = failurePhase
                            };
                        }
                    }
                }
                if (savedValidationException != null)
                {
                    failureDetails = savedValidationFailure;
                    throw savedValidationException;
                }
                var results = new JArray();
                for (var i = 0; i < edits.Count; i++)
                {
                    var edit = edits[i];
                    activeRow = i; activePath = edit.Path; failurePhase = "persisted_readback";
                    AssetDatabase.ImportAsset(edit.Path, ImportAssetOptions.ForceSynchronousImport | ImportAssetOptions.ForceUpdate);
                    var actual = AssetDatabase.LoadAssetAtPath<Material>(edit.Path);
                    var evidence = SceneObjectCopyCore.ReadStableAssetEvidence(edit.Path, "shader batch persisted readback");
                    if (actual == null || actual.shader != edit.Shader || EditorUtility.IsDirty(actual)
                        || EditorJsonUtility.ToJson(actual) != (edit.Mutated ? edit.ExpectedJson : edit.BeforeJson)
                        || !SceneObjectCopyCore.StableAssetEvidenceMatches(edit.Owned ?? edit.Before, evidence, true)) throw new InvalidOperationException("Shader batch persisted readback mismatch.");
                    var result = (JObject)previews[i].DeepClone();
                    result["preview"] = false; result["changed"] = edit.Mutated; result["saved"] = edit.Mutated;
                    result["mutationStarted"] = edit.Mutated; result["committed"] = true;
                    result["commitState"] = edit.Mutated ? "committed" : "no_change";
                    result["materialFileDigestAfter"] = evidence.File.Digest;
                    result["afterShader"] = actual.shader.name;
                    var shaderPath = MaterialShaderTool.NormalizeResolvedShaderAssetPath(AssetDatabase.GetAssetPath(actual.shader));
                    result["after"] = new JObject { ["shader"] = actual.shader.name, ["shaderAssetPath"] = shaderPath,
                        ["shaderAssetGuid"] = string.IsNullOrEmpty(shaderPath) ? "" : AssetDatabase.AssetPathToGUID(shaderPath), ["materialFileDigest"] = evidence.File.Digest };
                    result["persistedReadback"] = true; result["readback"] = new JObject { ["materialAssetPath"] = edit.Path, ["materialAssetGuid"] = evidence.Guid,
                        ["materialFileDigest"] = evidence.File.Digest, ["shaderName"] = actual.shader.name, ["shaderAssetPath"] = shaderPath, ["shaderAssetGuid"] = string.IsNullOrEmpty(shaderPath) ? "" : AssetDatabase.AssetPathToGUID(shaderPath) };
                    results.Add(result);
                }
                foreach (var edit in edits)
                    if (!SceneObjectCopyCore.StableAssetEvidenceMatches(edit.Owned ?? edit.Before, SceneObjectCopyCore.ReadStableAssetEvidence(edit.Path, "shader batch final readback"), true))
                        throw new InvalidOperationException("A material changed during final batch readback.");
                envelope["preview"] = false; envelope["changed"] = edits.Any(e => e.Mutated); envelope["saved"] = true;
                envelope["persistedReadback"] = true; envelope["committed"] = true; envelope["commitState"] = "committed";
                envelope["assignments"] = results; CheckSize(envelope);
                return VRCForgeToolResult.Completed("Shader asset batch saved and independently verified.", envelope);
            }
            catch (Exception exception)
            {
                failureDetails = failureDetails ?? new JObject { ["rowIndex"] = activeRow, ["materialAssetPath"] = activePath, ["failurePhase"] = failurePhase };
                var restored = true;
                var recoveryResults = new JArray();
                foreach (var edit in edits.AsEnumerable().Reverse())
                {
                    var rowRestored = Restore(edit);
                    if (!rowRestored) restored = false;
                    recoveryResults.Add(new JObject { ["rowIndex"] = edit.RowIndex, ["materialAssetPath"] = edit.Path,
                        ["mutationStarted"] = edit.Mutated, ["restored"] = rowRestored, ["hadSavedOwnership"] = edit.Owned != null });
                }
                var started = edits.Any(e => e.Mutated);
                return VRCForgeToolResult.FailedWithCode("material_shader_batch_failed", exception.Message,
                    new { schema = Schema, batch = true, verified = false, mutationStarted = started, committed = false, restored, failureDetails, recoveryResults,
                        checkpointRecoveryRequired = !restored, commitState = !started ? "not_started" : restored ? "rolled_back" : "unknown", commitStateKnown = restored });
            }
        }
    }
}
