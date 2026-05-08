using System;
using System.Collections.Concurrent;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Threading;
using System.Threading.Tasks;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Tools;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEditor.Animations;
using UnityEditor.SceneManagement;
using UnityEngine;

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

            if (!TryResolveRoslynRuntime(out var runtimeError))
            {
                return new ErrorResponse(runtimeError);
            }

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
            catch (Exception ex) when (IsCompilationErrorException(ex))
            {
                var diagnostics = FormatCompilationDiagnostics(ex);
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
        }

        private static (object result, int writeDefaultsUpdated) ExecuteSnippet(
            string code,
            bool enforceWriteDefaultsOn,
            IEnumerable<string> targetAvatarPaths)
        {
            var options = BuildScriptOptions();
            var globals = new ScriptGlobals();
            var evaluation = EvaluateScriptAsync(code, options, globals);
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

        private static object BuildScriptOptions()
        {
            var scriptOptionsType = ResolveRoslynType(
                "Microsoft.CodeAnalysis.Scripting.ScriptOptions",
                "Microsoft.CodeAnalysis.Scripting");
            var options = scriptOptionsType.GetProperty("Default", BindingFlags.Public | BindingFlags.Static)
                ?.GetValue(null);
            if (options == null)
            {
                throw new InvalidOperationException("Roslyn ScriptOptions.Default was not available.");
            }

            var assemblies = AppDomain.CurrentDomain.GetAssemblies()
                .Where(assembly => !assembly.IsDynamic && !string.IsNullOrWhiteSpace(assembly.Location))
                .GroupBy(assembly => assembly.Location, StringComparer.OrdinalIgnoreCase)
                .Select(group => group.First())
                .ToArray();

            options = InvokeScriptOptionsMethod(
                scriptOptionsType,
                options,
                "AddReferences",
                typeof(Assembly[]),
                assemblies);

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

            return InvokeScriptOptionsMethod(
                scriptOptionsType,
                options,
                "AddImports",
                typeof(string[]),
                imports.ToArray());
        }

        private static object InvokeScriptOptionsMethod(
            Type scriptOptionsType,
            object options,
            string methodName,
            Type parameterType,
            object argument)
        {
            var method = scriptOptionsType.GetMethods(BindingFlags.Public | BindingFlags.Instance)
                .FirstOrDefault(candidate =>
                {
                    if (candidate.Name != methodName)
                    {
                        return false;
                    }

                    var parameters = candidate.GetParameters();
                    return parameters.Length == 1 && parameters[0].ParameterType == parameterType;
                });

            if (method == null)
            {
                throw new InvalidOperationException($"Roslyn ScriptOptions.{methodName} overload was not available.");
            }

            return method.Invoke(options, new[] { argument });
        }

        private static Task<object> EvaluateScriptAsync(string code, object options, ScriptGlobals globals)
        {
            var csharpScriptType = ResolveRoslynType(
                "Microsoft.CodeAnalysis.CSharp.Scripting.CSharpScript",
                "Microsoft.CodeAnalysis.CSharp.Scripting");
            var scriptOptionsType = ResolveRoslynType(
                "Microsoft.CodeAnalysis.Scripting.ScriptOptions",
                "Microsoft.CodeAnalysis.Scripting");
            var method = csharpScriptType.GetMethods(BindingFlags.Public | BindingFlags.Static)
                .Where(candidate => candidate.Name == "EvaluateAsync" && candidate.IsGenericMethodDefinition)
                .FirstOrDefault(candidate =>
                {
                    var parameters = candidate.GetParameters();
                    return parameters.Length == 5
                        && parameters[0].ParameterType == typeof(string)
                        && parameters[1].ParameterType == scriptOptionsType
                        && parameters[2].ParameterType == typeof(object)
                        && parameters[3].ParameterType == typeof(Type)
                        && parameters[4].ParameterType == typeof(CancellationToken);
                });

            if (method == null)
            {
                throw new InvalidOperationException("Roslyn CSharpScript.EvaluateAsync overload was not available.");
            }

            var task = method.MakeGenericMethod(typeof(object)).Invoke(
                null,
                new object[] { code, options, globals, typeof(ScriptGlobals), CancellationToken.None });
            return (Task<object>)task;
        }

        private static bool TryResolveRoslynRuntime(out string error)
        {
            error = string.Empty;
            try
            {
                ResolveRoslynType(
                    "Microsoft.CodeAnalysis.CSharp.Scripting.CSharpScript",
                    "Microsoft.CodeAnalysis.CSharp.Scripting");
                ResolveRoslynType(
                    "Microsoft.CodeAnalysis.Scripting.ScriptOptions",
                    "Microsoft.CodeAnalysis.Scripting");
                return true;
            }
            catch (Exception ex)
            {
                error =
                    "Roslyn runtime is unavailable. Install Roslyn DLLs under Assets/Plugins/Roslyn or run tools/install-roslyn-support.ps1. "
                    + ex.Message;
                return false;
            }
        }

        private static Type ResolveRoslynType(string fullName, string assemblyName)
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

            try
            {
                var loadedType = Type.GetType($"{fullName}, {assemblyName}", false);
                if (loadedType != null)
                {
                    return loadedType;
                }
            }
            catch
            {
                // Try loading from the conventional project plugin folder below.
            }

            var dllPath = Path.Combine(Application.dataPath, "Plugins", "Roslyn", $"{assemblyName}.dll");
            if (File.Exists(dllPath))
            {
                var assembly = Assembly.LoadFrom(dllPath);
                var type = assembly.GetType(fullName, false);
                if (type != null)
                {
                    return type;
                }
            }

            throw new InvalidOperationException($"Could not load Roslyn type '{fullName}'.");
        }

        private static bool IsCompilationErrorException(Exception ex)
        {
            return ex.GetType().FullName == "Microsoft.CodeAnalysis.Scripting.CompilationErrorException";
        }

        private static string FormatCompilationDiagnostics(Exception ex)
        {
            try
            {
                var diagnostics = ex.GetType().GetProperty("Diagnostics")?.GetValue(ex) as System.Collections.IEnumerable;
                if (diagnostics == null)
                {
                    return ex.Message;
                }

                var lines = diagnostics.Cast<object>().Select(FormatDiagnosticObject).ToArray();
                return lines.Length > 0 ? string.Join(Environment.NewLine, lines) : ex.Message;
            }
            catch
            {
                return ex.Message;
            }
        }

        private static string FormatDiagnosticObject(object diagnostic)
        {
            try
            {
                var diagnosticType = diagnostic.GetType();
                var severity = diagnosticType.GetProperty("Severity")?.GetValue(diagnostic)?.ToString() ?? "Diagnostic";
                var message = diagnosticType.GetMethod("GetMessage", Type.EmptyTypes)?.Invoke(diagnostic, null)?.ToString()
                    ?? diagnostic.ToString();
                var location = diagnosticType.GetProperty("Location")?.GetValue(diagnostic);
                var span = location?.GetType().GetMethod("GetLineSpan", Type.EmptyTypes)?.Invoke(location, null);
                var isValid = (bool?)span?.GetType().GetProperty("IsValid")?.GetValue(span) ?? false;
                if (!isValid)
                {
                    return diagnostic.ToString();
                }

                var start = span.GetType().GetProperty("StartLinePosition")?.GetValue(span);
                if (start == null)
                {
                    return diagnostic.ToString();
                }

                var line = (int)start.GetType().GetProperty("Line").GetValue(start) + 1;
                var character = (int)start.GetType().GetProperty("Character").GetValue(start) + 1;
                return $"{severity} L{line}:C{character} {message}";
            }
            catch
            {
                return diagnostic.ToString();
            }
        }

        public static void Log(string message)
        {
            UnityEngine.Debug.Log($"[{ToolName}] {message}");
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
