using System;
using System.Collections.Concurrent;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Threading;
using System.Threading.Tasks;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Tools;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEditor.Animations;
using UnityEditor.SceneManagement;
using UnityEngine;

#if USE_ROSLYN
using Microsoft.CodeAnalysis;
using Microsoft.CodeAnalysis.CSharp.Scripting;
using Microsoft.CodeAnalysis.Scripting;
#endif

namespace VRCAutoRig.Editor
{
    [InitializeOnLoad]
    internal static class RoslynMainThreadDispatcher
    {
        private static readonly ConcurrentQueue<Action> Queue = new ConcurrentQueue<Action>();
        private static readonly int MainThreadId;

        static RoslynMainThreadDispatcher()
        {
            MainThreadId = Thread.CurrentThread.ManagedThreadId;
            EditorApplication.update += Flush;
        }

        public static bool IsMainThread => Thread.CurrentThread.ManagedThreadId == MainThreadId;

        public static T Run<T>(Func<T> work, TimeSpan timeout)
        {
            if (IsMainThread)
            {
                return work();
            }

            Exception capturedException = null;
            T result = default;
            using var completed = new ManualResetEventSlim(false);

            Queue.Enqueue(() =>
            {
                try
                {
                    result = work();
                }
                catch (Exception ex)
                {
                    capturedException = ex;
                }
                finally
                {
                    completed.Set();
                }
            });

            if (!completed.Wait(timeout))
            {
                throw new TimeoutException($"Timed out waiting {timeout.TotalSeconds:F1}s for Unity main-thread execution.");
            }

            if (capturedException != null)
            {
                throw capturedException;
            }

            return result;
        }

        private static void Flush()
        {
            while (Queue.TryDequeue(out var work))
            {
                work();
            }
        }
    }

    [McpForUnityTool(
        name: "vrc_execute_roslyn",
        Description = "Execute Roslyn C# snippets against the active Unity scene and VRChat avatar setup."
    )]
    public static class RoslynExecutor
    {
        public const string ToolName = "vrc_execute_roslyn";
        private static readonly TimeSpan ExecutionTimeout = TimeSpan.FromSeconds(2);

        public class Parameters
        {
            [ToolParameter("C# snippet to execute. Use RoslynExecutor.SetBlendshapeWeight for generated assignments.")]
            public string code { get; set; }

            [ToolParameter("If true, animator states found on the avatar graph will have Write Defaults forced ON.", Required = false)]
            public bool? enforceWriteDefaultsOn { get; set; } = true;

            [ToolParameter("Optional avatar root paths whose AnimatorControllers should have Write Defaults forced ON.", Required = false)]
            public string[] targetAvatarPaths { get; set; }
        }

        public sealed class ScriptGlobals
        {
            public string ProjectPath => Directory.GetParent(Application.dataPath)?.FullName ?? string.Empty;
        }

        public static object HandleCommand(JObject @params)
        {
            var parameters = (@params ?? new JObject()).ToObject<Parameters>() ?? new Parameters();
            if (string.IsNullOrWhiteSpace(parameters.code))
            {
                return new ErrorResponse("Missing required parameter: code");
            }

#if !USE_ROSLYN
            return new ErrorResponse(
                "Roslyn runtime is disabled. Install the Roslyn DLLs in MCP for Unity and add USE_ROSLYN to Player Settings > Scripting Define Symbols.");
#else
            try
            {
                var startedAt = Stopwatch.StartNew();
                var executionResult = RoslynMainThreadDispatcher.Run(
                    () => ExecuteSnippet(
                        parameters.code,
                        parameters.enforceWriteDefaultsOn ?? true,
                        parameters.targetAvatarPaths),
                    ExecutionTimeout);

                startedAt.Stop();
                if (startedAt.Elapsed > ExecutionTimeout)
                {
                    return new ErrorResponse(
                        $"Roslyn snippet exceeded the 2 second safety budget ({startedAt.Elapsed.TotalSeconds:F2}s).");
                }

                return new SuccessResponse(
                    $"Roslyn snippet executed in {startedAt.Elapsed.TotalMilliseconds:F0} ms.",
                    new
                    {
                        result = executionResult.result,
                        writeDefaultsUpdated = executionResult.writeDefaultsUpdated,
                        durationMs = startedAt.Elapsed.TotalMilliseconds
                    });
            }
            catch (CompilationErrorException ex)
            {
                var diagnostics = string.Join(
                    Environment.NewLine,
                    ex.Diagnostics.Select(FormatDiagnostic));
                return new ErrorResponse($"Roslyn compilation failed:{Environment.NewLine}{diagnostics}");
            }
            catch (TimeoutException ex)
            {
                return new ErrorResponse($"Roslyn execution timed out: {ex.Message}");
            }
            catch (Exception ex)
            {
                return new ErrorResponse($"Roslyn execution failed: {ex.Message}{Environment.NewLine}{ex.StackTrace}");
            }
#endif
        }

#if USE_ROSLYN
        private static (object result, int writeDefaultsUpdated) ExecuteSnippet(
            string code,
            bool enforceWriteDefaultsOn,
            IEnumerable<string> targetAvatarPaths)
        {
            var options = BuildScriptOptions();
            var globals = new ScriptGlobals();
            var evaluation = CSharpScript.EvaluateAsync<object>(code, options, globals, typeof(ScriptGlobals));
            var completed = Task.WhenAny(evaluation, Task.Delay(ExecutionTimeout)).GetAwaiter().GetResult();

            if (completed != evaluation)
            {
                throw new TimeoutException("Snippet did not finish within the 2 second safety budget.");
            }

            var result = evaluation.GetAwaiter().GetResult();
            var writeDefaultsUpdated = enforceWriteDefaultsOn ? EnsureWriteDefaultsOn(targetAvatarPaths) : 0;

            AssetDatabase.SaveAssets();
            EditorSceneManager.SaveOpenScenes();
            return (result, writeDefaultsUpdated);
        }

        private static ScriptOptions BuildScriptOptions()
        {
            var references = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
            var metadata = new List<MetadataReference>();

            foreach (var assembly in AppDomain.CurrentDomain.GetAssemblies())
            {
                if (assembly.IsDynamic || string.IsNullOrWhiteSpace(assembly.Location))
                {
                    continue;
                }

                if (!references.Add(assembly.Location))
                {
                    continue;
                }

                metadata.Add(MetadataReference.CreateFromFile(assembly.Location));
            }

            var imports = new List<string>
            {
                "System",
                "System.IO",
                "System.Linq",
                "System.Collections.Generic",
                "UnityEngine",
                "UnityEditor",
                "UnityEditor.Animations",
                "VRCAutoRig.Editor"
            };

            if (AppDomain.CurrentDomain.GetAssemblies().Any(assembly => assembly.GetName().Name.StartsWith("VRC", StringComparison.OrdinalIgnoreCase)))
            {
                imports.Add("VRC.SDKBase");
                imports.Add("VRC.SDK3.Avatars.Components");
                imports.Add("VRC.SDK3.Avatars.ScriptableObjects");
            }

            return ScriptOptions.Default
                .AddReferences(metadata)
                .AddImports(imports);
        }

        private static string FormatDiagnostic(Diagnostic diagnostic)
        {
            var span = diagnostic.Location.GetLineSpan();
            if (!span.IsValid)
            {
                return diagnostic.ToString();
            }

            var line = span.StartLinePosition.Line + 1;
            var character = span.StartLinePosition.Character + 1;
            return $"{diagnostic.Severity} L{line}:C{character} {diagnostic.GetMessage()}";
        }
#endif

        public static void Log(string message)
        {
            Debug.Log($"[{ToolName}] {message}");
        }

        public static void SetBlendshapeWeight(string avatarPath, string rendererPath, string blendshapeName, float targetWeight)
        {
            var renderer = ResolveRenderer(avatarPath, rendererPath);
            if (renderer.sharedMesh == null)
            {
                throw new InvalidOperationException($"Renderer '{rendererPath}' has no shared mesh.");
            }

            var blendshapeIndex = renderer.sharedMesh.GetBlendShapeIndex(blendshapeName);
            if (blendshapeIndex < 0)
            {
                throw new InvalidOperationException(
                    $"Blendshape '{blendshapeName}' was not found on renderer '{rendererPath}'.");
            }

            renderer.SetBlendShapeWeight(blendshapeIndex, Mathf.Clamp(targetWeight, 0f, 100f));
            EditorUtility.SetDirty(renderer);
            EditorUtility.SetDirty(renderer.gameObject);
            EditorSceneManager.MarkSceneDirty(renderer.gameObject.scene);
        }

        public static void SaveProjectAssets()
        {
            AssetDatabase.SaveAssets();
            EditorSceneManager.SaveOpenScenes();
        }

        public static int EnsureWriteDefaultsOn(IEnumerable<string> targetAvatarPaths = null)
        {
            var updatedStates = 0;
            var scopedAvatarPaths = BuildNormalizedPathSet(targetAvatarPaths);

            foreach (var animator in Resources.FindObjectsOfTypeAll<Animator>().Where(IsSceneObject))
            {
                if (animator == null || animator.runtimeAnimatorController == null)
                {
                    continue;
                }

                if (scopedAvatarPaths.Count > 0)
                {
                    var animatorAvatarPath = NormalizePath(GetTransformPath(FindAvatarRoot(animator.transform)));
                    if (!scopedAvatarPaths.Contains(animatorAvatarPath))
                    {
                        continue;
                    }
                }

                var controller = animator.runtimeAnimatorController as AnimatorController;
                if (controller == null)
                {
                    continue;
                }

                foreach (var layer in controller.layers)
                {
                    updatedStates += ForceWriteDefaults(layer.stateMachine);
                }

                if (updatedStates > 0)
                {
                    EditorUtility.SetDirty(controller);
                }
            }

            return updatedStates;
        }

        private static int ForceWriteDefaults(AnimatorStateMachine stateMachine)
        {
            var updated = 0;

            foreach (var childState in stateMachine.states)
            {
                if (!childState.state.writeDefaultValues)
                {
                    childState.state.writeDefaultValues = true;
                    updated++;
                }
            }

            foreach (var childMachine in stateMachine.stateMachines)
            {
                updated += ForceWriteDefaults(childMachine.stateMachine);
            }

            return updated;
        }

        private static SkinnedMeshRenderer ResolveRenderer(string avatarPath, string rendererPath)
        {
            var renderers = Resources.FindObjectsOfTypeAll<SkinnedMeshRenderer>().Where(IsSceneObject);

            var normalizedAvatarPath = NormalizePath(avatarPath);
            var normalizedRendererPath = NormalizePath(rendererPath);

            var match = renderers.FirstOrDefault(renderer =>
                NormalizePath(GetTransformPath(renderer.transform)) == normalizedRendererPath
                && (string.IsNullOrEmpty(normalizedAvatarPath)
                    || NormalizePath(GetTransformPath(FindAvatarRoot(renderer.transform))) == normalizedAvatarPath));

            if (match == null)
            {
                throw new InvalidOperationException(
                    $"Could not locate renderer '{rendererPath}' under avatar '{avatarPath}'.");
            }

            return match;
        }

        private static Transform FindAvatarRoot(Transform source)
        {
            var current = source;
            Transform fallback = source.root;
            var avatarDescriptorType = FindType("VRC.SDK3.Avatars.Components.VRCAvatarDescriptor");

            while (current != null)
            {
                if (avatarDescriptorType != null && current.GetComponent(avatarDescriptorType) != null)
                {
                    return current;
                }

                if (current.GetComponent<Animator>() != null)
                {
                    fallback = current;
                }

                current = current.parent;
            }

            return fallback;
        }

        private static Type FindType(string fullName)
        {
            foreach (var assembly in AppDomain.CurrentDomain.GetAssemblies())
            {
                try
                {
                    var type = assembly.GetType(fullName, false);
                    if (type != null)
                    {
                        return type;
                    }
                }
                catch
                {
                    // Ignore editor reload races.
                }
            }

            return null;
        }

        private static string GetTransformPath(Transform transform)
        {
            var segments = new Stack<string>();
            var current = transform;

            while (current != null)
            {
                segments.Push(current.name);
                current = current.parent;
            }

            return string.Join("/", segments);
        }

        private static string NormalizePath(string value)
        {
            return (value ?? string.Empty).Trim().Replace("\\", "/");
        }

        private static HashSet<string> BuildNormalizedPathSet(IEnumerable<string> paths)
        {
            if (paths == null)
            {
                return new HashSet<string>(StringComparer.OrdinalIgnoreCase);
            }

            return new HashSet<string>(
                paths
                    .Where(path => !string.IsNullOrWhiteSpace(path))
                    .Select(NormalizePath),
                StringComparer.OrdinalIgnoreCase);
        }

        private static bool IsSceneObject(Component component)
        {
            return component != null
                && component.gameObject.scene.IsValid()
                && component.gameObject.scene.isLoaded
                && !EditorUtility.IsPersistent(component);
        }
    }
}
