using System;
using System.CodeDom.Compiler;
using System.Collections.Concurrent;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Text;
using System.Threading;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Tools;
using Microsoft.CSharp;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEditor.Animations;
using UnityEditor.SceneManagement;
using UnityEngine;

namespace VRCForge.Editor
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

    internal sealed class SnippetCompilationException : Exception
    {
        public SnippetCompilationException(string backend, IReadOnlyList<string> errors)
            : base(string.Join(Environment.NewLine, errors ?? Array.Empty<string>()))
        {
            Backend = backend;
            Errors = errors ?? Array.Empty<string>();
        }

        public string Backend { get; }
        public IReadOnlyList<string> Errors { get; }
    }

    /// <summary>
    /// Full-compilation snippet backend. Primary path uses Roslyn
    /// (Microsoft.CodeAnalysis via reflection, no compile-time dependency,
    /// only 4 DLLs required under Assets/Plugins/Roslyn). Fallback path uses
    /// Unity's built-in CodeDom CSharpCodeProvider which needs zero install.
    /// </summary>
    internal static class VRCForgeSnippetCompiler
    {
        private const string WrapperClassName = "VRCForgeDynamicSnippet";
        private const string WrapperMethodName = "Execute";

        private static readonly string RoslynPluginFolder = Path.Combine(Application.dataPath, "Plugins", "Roslyn");

        private static bool? _roslynAvailable;
        private static bool? _codeDomAvailable;
        private static string[] _cachedAssemblyPaths;

        private static Type _syntaxTreeType;
        private static Type _compilationType;
        private static Type _compilationOptionsType;
        private static Type _parseOptionsType;
        private static Type _metadataReferenceType;
        private static Type _outputKindEnum;
        private static Type _languageVersionEnum;
        private static MethodInfo _parseText;
        private static MethodInfo _createCompilation;
        private static MethodInfo _createFromFile;
        private static MethodInfo _emit;
        private static object _parseOptions;
        private static object _compilationOptions;

        public static string LastInitError { get; private set; } = string.Empty;

        public static bool RoslynAvailable
        {
            get
            {
                if (_roslynAvailable == null)
                {
                    _roslynAvailable = InitializeRoslyn();
                }

                return _roslynAvailable.Value;
            }
        }

        public static bool CodeDomAvailable
        {
            get
            {
                if (_codeDomAvailable == null)
                {
                    try
                    {
                        using (new CSharpCodeProvider())
                        {
                            _codeDomAvailable = true;
                        }
                    }
                    catch (Exception ex)
                    {
                        LastInitError = $"CodeDom unavailable: {ex.Message}";
                        _codeDomAvailable = false;
                    }
                }

                return _codeDomAvailable.Value;
            }
        }

        public static string ActiveBackend
        {
            get
            {
                if (RoslynAvailable)
                {
                    return "roslyn";
                }

                return CodeDomAvailable ? "codedom" : string.Empty;
            }
        }

        public static (object result, string backend) CompileAndInvoke(string code)
        {
            var wrappedSource = WrapUserCode(code, out var lineOffset);
            var assemblyPaths = GetAssemblyPaths();

            Assembly compiled;
            string backend;
            if (RoslynAvailable)
            {
                backend = "roslyn";
                compiled = CompileWithRoslyn(wrappedSource, assemblyPaths, lineOffset, out var errors);
                if (compiled == null)
                {
                    throw new SnippetCompilationException(backend, errors);
                }
            }
            else if (CodeDomAvailable)
            {
                backend = "codedom";
                compiled = CompileWithCodeDom(wrappedSource, assemblyPaths, lineOffset, out var errors);
                if (compiled == null)
                {
                    throw new SnippetCompilationException(backend, errors);
                }
            }
            else
            {
                throw new InvalidOperationException(
                    "No C# compiler backend is available. Run tools/install-roslyn-support.ps1 to install Roslyn DLLs. " + LastInitError);
            }

            return (Invoke(compiled), backend);
        }

        private static object Invoke(Assembly assembly)
        {
            var type = assembly.GetType(WrapperClassName);
            var method = type?.GetMethod(WrapperMethodName, BindingFlags.Public | BindingFlags.Static);
            if (method == null)
            {
                throw new InvalidOperationException("Internal error: compiled snippet wrapper was not found.");
            }

            try
            {
                return SerializeResult(method.Invoke(null, null));
            }
            catch (TargetInvocationException tie)
            {
                throw tie.InnerException ?? tie;
            }
        }

        private static object SerializeResult(object result)
        {
            if (result == null)
            {
                return null;
            }

            var type = result.GetType();
            if (type.IsPrimitive || result is string || result is decimal)
            {
                return result;
            }

            try
            {
                return JToken.FromObject(result);
            }
            catch
            {
                return result.ToString();
            }
        }

        internal static string WrapUserCode(string code, out int lineOffset)
        {
            var sb = new StringBuilder();
            sb.AppendLine("using System;");
            sb.AppendLine("using System.Collections.Generic;");
            sb.AppendLine("using System.IO;");
            sb.AppendLine("using System.Linq;");
            sb.AppendLine("using System.Reflection;");
            sb.AppendLine("using UnityEngine;");
            sb.AppendLine("using UnityEditor;");
            sb.AppendLine("using UnityEditor.Animations;");
            sb.AppendLine("using VRCForge.Editor;");

            if (AppDomain.CurrentDomain.GetAssemblies().Any(assembly =>
                    assembly.GetName().Name.StartsWith("VRC", StringComparison.OrdinalIgnoreCase)))
            {
                sb.AppendLine("using VRC.SDKBase;");
                sb.AppendLine("using VRC.SDK3.Avatars.Components;");
                sb.AppendLine("using VRC.SDK3.Avatars.ScriptableObjects;");
            }

            sb.AppendLine($"public static class {WrapperClassName}");
            sb.AppendLine("{");
            sb.AppendLine($"    public static object {WrapperMethodName}()");
            sb.AppendLine("    {");

            // Number of wrapper lines before the first user-code line.
            lineOffset = CountLines(sb.ToString());

            var trimmed = (code ?? string.Empty).Trim();
            var looksLikeStatement = trimmed.Contains(";")
                || trimmed.StartsWith("return", StringComparison.Ordinal)
                || trimmed.StartsWith("throw", StringComparison.Ordinal)
                || trimmed.StartsWith("{", StringComparison.Ordinal);
            if (!looksLikeStatement)
            {
                // Bare expression: auto-return its value.
                sb.AppendLine($"        return ({trimmed});");
            }
            else
            {
                sb.AppendLine(trimmed);
                // Unreachable when the snippet already returns; warning only.
                sb.AppendLine("        return null;");
            }

            sb.AppendLine("    }");
            sb.AppendLine("}");
            return sb.ToString();
        }

        private static int CountLines(string text)
        {
            var count = 0;
            foreach (var ch in text)
            {
                if (ch == '\n')
                {
                    count++;
                }
            }

            return count;
        }

        private static bool InitializeRoslyn()
        {
            try
            {
                _syntaxTreeType = ResolveType("Microsoft.CodeAnalysis.CSharp.CSharpSyntaxTree", "Microsoft.CodeAnalysis.CSharp");
                _compilationType = ResolveType("Microsoft.CodeAnalysis.CSharp.CSharpCompilation", "Microsoft.CodeAnalysis.CSharp");
                _compilationOptionsType = ResolveType("Microsoft.CodeAnalysis.CSharp.CSharpCompilationOptions", "Microsoft.CodeAnalysis.CSharp");
                _parseOptionsType = ResolveType("Microsoft.CodeAnalysis.CSharp.CSharpParseOptions", "Microsoft.CodeAnalysis.CSharp");
                _metadataReferenceType = ResolveType("Microsoft.CodeAnalysis.MetadataReference", "Microsoft.CodeAnalysis");
                _outputKindEnum = ResolveType("Microsoft.CodeAnalysis.OutputKind", "Microsoft.CodeAnalysis");
                _languageVersionEnum = ResolveType("Microsoft.CodeAnalysis.CSharp.LanguageVersion", "Microsoft.CodeAnalysis.CSharp");

                if (_syntaxTreeType == null || _compilationType == null || _compilationOptionsType == null
                    || _parseOptionsType == null || _metadataReferenceType == null || _outputKindEnum == null
                    || _languageVersionEnum == null)
                {
                    LastInitError = "Roslyn types could not be resolved (Microsoft.CodeAnalysis DLLs missing or not loadable).";
                    return false;
                }

                var syntaxTreeBase = ResolveType("Microsoft.CodeAnalysis.SyntaxTree", "Microsoft.CodeAnalysis");
                _parseText = _syntaxTreeType.GetMethod(
                    "ParseText",
                    new[] { typeof(string), _parseOptionsType, typeof(string), typeof(Encoding), typeof(CancellationToken) });

                var syntaxTreeEnumerable = typeof(IEnumerable<>).MakeGenericType(syntaxTreeBase);
                var metadataRefEnumerable = typeof(IEnumerable<>).MakeGenericType(_metadataReferenceType);
                _createCompilation = _compilationType.GetMethod(
                    "Create",
                    new[] { typeof(string), syntaxTreeEnumerable, metadataRefEnumerable, _compilationOptionsType });

                _createFromFile = _metadataReferenceType
                    .GetMethods(BindingFlags.Public | BindingFlags.Static)
                    .FirstOrDefault(m => m.Name == "CreateFromFile");

                var compilationBase = ResolveType("Microsoft.CodeAnalysis.Compilation", "Microsoft.CodeAnalysis");
                _emit = compilationBase?
                    .GetMethods(BindingFlags.Public | BindingFlags.Instance)
                    .Where(m => m.Name == "Emit")
                    .OrderBy(m => m.GetParameters().Length)
                    .FirstOrDefault();

                if (_parseText == null || _createCompilation == null || _createFromFile == null || _emit == null)
                {
                    LastInitError = "Roslyn compile entry points could not be resolved (version mismatch?).";
                    return false;
                }

                var latestValue = Enum.Parse(_languageVersionEnum, "Latest");
                var parseOptionsCtor = _parseOptionsType.GetConstructors(BindingFlags.Public | BindingFlags.Instance)[0];
                _parseOptions = parseOptionsCtor.Invoke(BuildCtorArgs(parseOptionsCtor, "languageVersion", latestValue));

                var dllKind = Enum.Parse(_outputKindEnum, "DynamicallyLinkedLibrary");
                var compOptionsCtor = _compilationOptionsType.GetConstructors(BindingFlags.Public | BindingFlags.Instance)[0];
                _compilationOptions = compOptionsCtor.Invoke(BuildCtorArgs(compOptionsCtor, "outputKind", dllKind));

                return true;
            }
            catch (Exception ex)
            {
                LastInitError = $"Roslyn initialization failed: {ex.Message}";
                return false;
            }
        }

        private static object[] BuildCtorArgs(ConstructorInfo ctor, string overrideName, object overrideValue)
        {
            var ctorParams = ctor.GetParameters();
            var args = new object[ctorParams.Length];
            for (var i = 0; i < ctorParams.Length; i++)
            {
                if (ctorParams[i].Name == overrideName)
                {
                    args[i] = overrideValue;
                }
                else if (ctorParams[i].HasDefaultValue)
                {
                    args[i] = ctorParams[i].DefaultValue;
                }
                else
                {
                    args[i] = null;
                }
            }

            return args;
        }

        private static Assembly CompileWithRoslyn(string source, string[] assemblyPaths, int lineOffset, out List<string> errors)
        {
            errors = new List<string>();

            try
            {
                var syntaxTree = _parseText.Invoke(
                    null,
                    new object[] { source, _parseOptions, string.Empty, null, default(CancellationToken) });

                var listType = typeof(List<>).MakeGenericType(_metadataReferenceType);
                var refs = (System.Collections.IList)Activator.CreateInstance(listType);
                var createFromFileParams = _createFromFile.GetParameters();
                foreach (var path in assemblyPaths)
                {
                    try
                    {
                        var cfArgs = new object[createFromFileParams.Length];
                        cfArgs[0] = path;
                        for (var i = 1; i < createFromFileParams.Length; i++)
                        {
                            cfArgs[i] = createFromFileParams[i].HasDefaultValue ? createFromFileParams[i].DefaultValue : null;
                        }

                        refs.Add(_createFromFile.Invoke(null, cfArgs));
                    }
                    catch
                    {
                        // Skip assemblies that cannot be loaded as metadata references.
                    }
                }

                var syntaxTreeBase = ResolveType("Microsoft.CodeAnalysis.SyntaxTree", "Microsoft.CodeAnalysis");
                var treeArray = Array.CreateInstance(syntaxTreeBase, 1);
                treeArray.SetValue(syntaxTree, 0);

                var compilation = _createCompilation.Invoke(
                    null,
                    new object[] { "VRCForgeDynamic", treeArray, refs, _compilationOptions });

                using (var ms = new MemoryStream())
                {
                    var emitParams = _emit.GetParameters();
                    var emitArgs = new object[emitParams.Length];
                    emitArgs[0] = ms;
                    for (var i = 1; i < emitParams.Length; i++)
                    {
                        emitArgs[i] = emitParams[i].HasDefaultValue ? emitParams[i].DefaultValue : null;
                    }

                    var emitResult = _emit.Invoke(compilation, emitArgs);
                    var success = (bool)emitResult.GetType().GetProperty("Success").GetValue(emitResult);

                    if (!success)
                    {
                        CollectErrorDiagnostics(emitResult, lineOffset, errors);
                        return null;
                    }

                    ms.Seek(0, SeekOrigin.Begin);
                    return Assembly.Load(ms.ToArray());
                }
            }
            catch (Exception ex)
            {
                errors.Add($"Roslyn compilation error: {ex.Message}");
                return null;
            }
        }

        private static void CollectErrorDiagnostics(object emitResult, int lineOffset, List<string> errors)
        {
            var diagnostics = (System.Collections.IEnumerable)emitResult.GetType()
                .GetProperty("Diagnostics")
                .GetValue(emitResult);
            var severityEnum = ResolveType("Microsoft.CodeAnalysis.DiagnosticSeverity", "Microsoft.CodeAnalysis");
            var severityError = Enum.Parse(severityEnum, "Error");

            foreach (var diag in diagnostics)
            {
                var severity = diag.GetType().GetProperty("Severity")?.GetValue(diag);
                if (severity == null || !severity.Equals(severityError))
                {
                    continue;
                }

                var id = diag.GetType().GetProperty("Id")?.GetValue(diag)?.ToString() ?? string.Empty;
                string message;
                try
                {
                    var msgMethod = diag.GetType().GetMethod("GetMessage", new[] { typeof(System.Globalization.CultureInfo) });
                    message = (string)msgMethod.Invoke(diag, new object[] { null });
                }
                catch
                {
                    message = diag.ToString();
                }

                var userLine = 0;
                try
                {
                    var location = diag.GetType().GetProperty("Location")?.GetValue(diag);
                    var lineSpan = location?.GetType().GetMethod("GetLineSpan", Type.EmptyTypes)?.Invoke(location, null);
                    var startPos = lineSpan?.GetType().GetProperty("StartLinePosition")?.GetValue(lineSpan);
                    if (startPos != null)
                    {
                        var line = (int)startPos.GetType().GetProperty("Line").GetValue(startPos);
                        userLine = Math.Max(1, line + 1 - lineOffset);
                    }
                }
                catch
                {
                    // Line info is best-effort only.
                }

                errors.Add(userLine > 0 ? $"Line {userLine}: {id} {message}" : $"{id} {message}");
            }

            if (errors.Count == 0)
            {
                errors.Add("Compilation failed without error diagnostics.");
            }
        }

        // CSharpCodeProvider cannot resolve type forwarding; when netstandard.dll is
        // referenced alongside these, common types appear twice and compilation fails.
        private static readonly HashSet<string> CodeDomDuplicateAssemblies = new HashSet<string>(StringComparer.OrdinalIgnoreCase)
        {
            "mscorlib",
            "System.Runtime",
            "System.Private.CoreLib",
            "System.Collections",
        };

        private static Assembly CompileWithCodeDom(string source, string[] assemblyPaths, int lineOffset, out List<string> errors)
        {
            errors = new List<string>();

            var hasNetstandard = assemblyPaths.Any(p =>
                string.Equals(Path.GetFileNameWithoutExtension(p), "netstandard", StringComparison.OrdinalIgnoreCase));
            var filtered = hasNetstandard
                ? assemblyPaths.Where(p => !CodeDomDuplicateAssemblies.Contains(Path.GetFileNameWithoutExtension(p))).ToArray()
                : assemblyPaths;

            using (var provider = new CSharpCodeProvider())
            {
                var parameters = new CompilerParameters
                {
                    GenerateInMemory = true,
                    GenerateExecutable = false,
                    TreatWarningsAsErrors = false,
                };

                foreach (var path in filtered)
                {
                    parameters.ReferencedAssemblies.Add(path);
                }

                var results = provider.CompileAssemblyFromSource(parameters, source);
                if (results.Errors.HasErrors)
                {
                    foreach (CompilerError error in results.Errors)
                    {
                        if (!error.IsWarning)
                        {
                            var userLine = Math.Max(1, error.Line - lineOffset);
                            errors.Add($"Line {userLine}: {error.ErrorNumber} {error.ErrorText}");
                        }
                    }

                    return null;
                }

                return results.CompiledAssembly;
            }
        }

        private static string[] GetAssemblyPaths()
        {
            if (_cachedAssemblyPaths == null)
            {
                var paths = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
                foreach (var assembly in AppDomain.CurrentDomain.GetAssemblies())
                {
                    try
                    {
                        if (assembly.IsDynamic)
                        {
                            continue;
                        }

                        var location = assembly.Location;
                        if (string.IsNullOrEmpty(location) || !File.Exists(location))
                        {
                            continue;
                        }

                        paths.Add(location);
                    }
                    catch (NotSupportedException)
                    {
                        // Some assemblies do not support the Location property.
                    }
                }

                _cachedAssemblyPaths = paths.ToArray();
            }

            return _cachedAssemblyPaths;
        }

        private static Type ResolveType(string fullName, string assemblyName)
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
                // Try the conventional plugin folder below.
            }

            try
            {
                var dllPath = Path.Combine(RoslynPluginFolder, $"{assemblyName}.dll");
                if (File.Exists(dllPath))
                {
                    return Assembly.LoadFrom(dllPath).GetType(fullName, false);
                }
            }
            catch
            {
                // Leave unresolved; caller treats null as unavailable.
            }

            return null;
        }
    }

    [McpForUnityTool(
        name: "vrc_execute_roslyn",
        Description = "Advanced Power Mode only: execute C# snippets (compiled in-memory) after explicit risk confirmation."
    )]
    public static class RoslynExecutor
    {
        public const string ToolName = "vrc_execute_roslyn";
        private const int DefaultExecutionTimeoutSeconds = 10;
        private const int MaxExecutionTimeoutSeconds = 30;
        private static readonly string[] RequiredRoslynDlls =
        {
            "Microsoft.CodeAnalysis.dll",
            "Microsoft.CodeAnalysis.CSharp.dll",
            "System.Collections.Immutable.dll",
            "System.Reflection.Metadata.dll"
        };
        private static readonly string RoslynPluginFolder = Path.Combine(Application.dataPath, "Plugins", "Roslyn");

        static RoslynExecutor()
        {
            AppDomain.CurrentDomain.AssemblyResolve += ResolveRoslynDependency;
        }

        public class Parameters
        {
            [ToolParameter("C# snippet to execute. A bare expression (no semicolons) is auto-returned; multi-statement snippets should end with 'return <value>;' (a trailing 'return null;' is appended automatically). Helpers like RoslynExecutor.SetBlendshapeWeight are in scope.")]
            public string code { get; set; }

            [ToolParameter("Must be true to acknowledge Advanced Power Mode risk before any snippet can run.", Required = false)]
            public bool? confirmAdvancedPowerMode { get; set; } = false;

            [ToolParameter("If true, animator states found on the avatar graph will have Write Defaults forced ON.", Required = false)]
            public bool? enforceWriteDefaultsOn { get; set; } = true;

            [ToolParameter("Optional avatar root paths whose AnimatorControllers should have Write Defaults forced ON.", Required = false)]
            public string[] targetAvatarPaths { get; set; }

            [ToolParameter("Optional execution timeout in seconds. Clamped to 1-30 seconds.", Required = false)]
            public int? timeoutSeconds { get; set; } = DefaultExecutionTimeoutSeconds;

            [ToolParameter("If true, save dirty assets after execution. Defaults to false because Roslyn is an advanced repair path.", Required = false)]
            public bool? saveAssets { get; set; } = false;

            [ToolParameter("If true, save open scenes after execution. Defaults to false because Roslyn is an advanced repair path.", Required = false)]
            public bool? saveScenes { get; set; } = false;
        }

        public static object HandleCommand(JObject @params)
        {
            var parameters = (@params ?? new JObject()).ToObject<Parameters>() ?? new Parameters();
            if (string.IsNullOrWhiteSpace(parameters.code))
            {
                return new ErrorResponse("Missing required parameter: code");
            }

            if (parameters.confirmAdvancedPowerMode != true)
            {
                return new ErrorResponse(
                    "Roslyn Advanced Power Mode is disabled for this call. "
                    + "Set confirmAdvancedPowerMode=true and approve the Unity warning dialog before executing arbitrary C#.");
            }

            if (!TryResolveRoslynRuntime(out var runtimeError))
            {
                return new ErrorResponse(runtimeError);
            }

            try
            {
                if (!ConfirmAdvancedPowerModeDialog())
                {
                    return new ErrorResponse("Roslyn Advanced Power Mode execution was cancelled by the user.");
                }

                var executionTimeout = BuildExecutionTimeout(parameters.timeoutSeconds);
                var startedAt = Stopwatch.StartNew();
                var executionResult = RoslynMainThreadDispatcher.Run(
                    () => ExecuteSnippet(
                        parameters.code,
                        parameters.enforceWriteDefaultsOn ?? true,
                        parameters.targetAvatarPaths,
                        parameters.saveAssets == true,
                        parameters.saveScenes == true),
                    executionTimeout + TimeSpan.FromSeconds(2));

                startedAt.Stop();
                if (startedAt.Elapsed > executionTimeout)
                {
                    return new ErrorResponse(
                        $"Roslyn snippet exceeded the {executionTimeout.TotalSeconds:F0} second safety budget ({startedAt.Elapsed.TotalSeconds:F2}s).");
                }

                return new SuccessResponse(
                    $"Snippet executed in {startedAt.Elapsed.TotalMilliseconds:F0} ms via {executionResult.backend}.",
                    new
                    {
                        result = executionResult.result,
                        writeDefaultsUpdated = executionResult.writeDefaultsUpdated,
                        durationMs = startedAt.Elapsed.TotalMilliseconds,
                        compilerBackend = executionResult.backend
                    });
            }
            catch (SnippetCompilationException ex)
            {
                return new ErrorResponse(
                    $"Snippet compilation failed ({ex.Backend}):{Environment.NewLine}{string.Join(Environment.NewLine, ex.Errors)}");
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

        private static bool ConfirmAdvancedPowerModeDialog()
        {
            return RoslynMainThreadDispatcher.Run(
                () => EditorUtility.DisplayDialog(
                    "VRCForge Advanced Power Mode",
                    "Roslyn will execute arbitrary C# inside the current Unity project. "
                    + "This can modify scenes, assets, avatar settings, and generated files. "
                    + "Use only after creating a backup and reviewing the snippet.",
                    "I understand, execute",
                    "Cancel"),
                TimeSpan.FromSeconds(30));
        }

        private static (object result, int writeDefaultsUpdated, string backend) ExecuteSnippet(
            string code,
            bool enforceWriteDefaultsOn,
            IEnumerable<string> targetAvatarPaths,
            bool saveAssets,
            bool saveScenes)
        {
            var (result, backend) = VRCForgeSnippetCompiler.CompileAndInvoke(code);
            var writeDefaultsUpdated = enforceWriteDefaultsOn ? EnsureWriteDefaultsOn(targetAvatarPaths) : 0;

            if (saveAssets)
            {
                AssetDatabase.SaveAssets();
            }
            if (saveScenes)
            {
                EditorSceneManager.SaveOpenScenes();
            }
            return (result, writeDefaultsUpdated, backend);
        }

        private static TimeSpan BuildExecutionTimeout(int? requestedSeconds)
        {
            var seconds = requestedSeconds ?? DefaultExecutionTimeoutSeconds;
            seconds = Mathf.Clamp(seconds, 1, MaxExecutionTimeoutSeconds);
            return TimeSpan.FromSeconds(seconds);
        }

        internal static bool TryResolveRoslynRuntime(out string error)
        {
            error = string.Empty;
            if (VRCForgeSnippetCompiler.RoslynAvailable || VRCForgeSnippetCompiler.CodeDomAvailable)
            {
                return true;
            }

            error =
                "No C# compiler backend is available. Install Roslyn DLLs under Assets/Plugins/Roslyn "
                + "(run tools/install-roslyn-support.ps1) or ensure the Unity CodeDom fallback works. "
                + VRCForgeSnippetCompiler.LastInitError;
            return false;
        }

        internal static object BuildStatusPayload()
        {
            var dlls = RequiredRoslynDlls
                .Select(name =>
                {
                    var path = Path.Combine(RoslynPluginFolder, name);
                    return new
                    {
                        name,
                        installed = File.Exists(path),
                        path = path.Replace("\\", "/")
                    };
                })
                .ToArray();
            var missing = dlls
                .Where(item => !item.installed)
                .Select(item => item.name)
                .ToArray();
            var runtimeResolved = TryResolveRoslynRuntime(out var runtimeError);
            var defineEnabled = HasRoslynDefine();

            return new
            {
                ok = runtimeResolved,
                installed = missing.Length == 0,
                runtimeResolved,
                roslynAvailable = VRCForgeSnippetCompiler.RoslynAvailable,
                codeDomAvailable = VRCForgeSnippetCompiler.CodeDomAvailable,
                activeBackend = VRCForgeSnippetCompiler.ActiveBackend,
                defineEnabled,
                pluginFolder = RoslynPluginFolder.Replace("\\", "/"),
                requiredDllCount = RequiredRoslynDlls.Length,
                missingDlls = missing,
                dlls,
                runtimeError
            };
        }

        internal static object BuildExecutionSmokePayload()
        {
            if (!TryResolveRoslynRuntime(out var runtimeError))
            {
                return new
                {
                    ok = false,
                    compiled = false,
                    executed = false,
                    expected = 42.0f,
                    result = (object)null,
                    compilerBackend = "",
                    runtimeError,
                    code = "<fixed-smoke-snippet>"
                };
            }

            try
            {
                var startedAt = Stopwatch.StartNew();
                var (result, backend) = VRCForgeSnippetCompiler.CompileAndInvoke(
                    "new UnityEngine.Vector3(1f, 2f, 3f).x + 41f");
                startedAt.Stop();
                var numericResult = Convert.ToSingle(result);
                var ok = Math.Abs(numericResult - 42.0f) < 0.001f;

                return new
                {
                    ok,
                    compiled = true,
                    executed = true,
                    expected = 42.0f,
                    result = numericResult,
                    resultType = result?.GetType().FullName ?? "",
                    compilerBackend = backend,
                    durationMs = startedAt.Elapsed.TotalMilliseconds,
                    runtimeError = "",
                    code = "<fixed-smoke-snippet>"
                };
            }
            catch (SnippetCompilationException ex)
            {
                return new
                {
                    ok = false,
                    compiled = false,
                    executed = false,
                    expected = 42.0f,
                    result = (object)null,
                    compilerBackend = ex.Backend,
                    runtimeError = ex.Message,
                    code = "<fixed-smoke-snippet>"
                };
            }
        }

        private static bool HasRoslynDefine()
        {
            try
            {
                var cscRspPath = Path.Combine(Application.dataPath, "csc.rsp");
                if (File.Exists(cscRspPath)
                    && File.ReadAllText(cscRspPath).Contains("VRCFORGE_ENABLE_ROSLYN"))
                {
                    return true;
                }

                var symbols = PlayerSettings.GetScriptingDefineSymbolsForGroup(
                    EditorUserBuildSettings.selectedBuildTargetGroup);
                return symbols
                    .Split(new[] { ';' }, StringSplitOptions.RemoveEmptyEntries)
                    .Any(symbol => symbol.Trim() == "VRCFORGE_ENABLE_ROSLYN");
            }
            catch
            {
                return false;
            }
        }

        private static Assembly ResolveRoslynDependency(object sender, ResolveEventArgs args)
        {
            try
            {
                var requestedName = new AssemblyName(args.Name).Name;
                if (string.IsNullOrWhiteSpace(requestedName))
                {
                    return null;
                }

                foreach (var loaded in AppDomain.CurrentDomain.GetAssemblies())
                {
                    if (string.Equals(loaded.GetName().Name, requestedName, StringComparison.OrdinalIgnoreCase))
                    {
                        return loaded;
                    }
                }

                var dllPath = Path.Combine(RoslynPluginFolder, $"{requestedName}.dll");
                return File.Exists(dllPath) ? Assembly.LoadFrom(dllPath) : null;
            }
            catch
            {
                return null;
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

    [McpForUnityTool(
        name: "vrc_check_roslyn_status",
        Description = "Read-only Roslyn Advanced Power Mode diagnostics for installed DLLs, compiler backends, define state, and runtime loadability."
    )]
    public static class RoslynStatusTool
    {
        public const string ToolName = "vrc_check_roslyn_status";

        public static object HandleCommand(JObject @params)
        {
            try
            {
                return new SuccessResponse(
                    "Roslyn Advanced Power Mode status checked.",
                    RoslynExecutor.BuildStatusPayload());
            }
            catch (Exception ex)
            {
                return new ErrorResponse($"Roslyn status check failed: {ex.Message}\n{ex.StackTrace}");
            }
        }

        public static void BatchStatusSmoke()
        {
            var status = RoslynExecutor.BuildStatusPayload();
            var ok = (bool)(status.GetType().GetProperty("ok")?.GetValue(status) ?? false);
            UnityEngine.Debug.Log("[VRCForge Roslyn Status Smoke] "
                + Newtonsoft.Json.JsonConvert.SerializeObject(status));
            if (!ok)
            {
                EditorApplication.Exit(1);
            }
        }

        public static void BatchExecutionSmoke()
        {
            var execution = RoslynExecutor.BuildExecutionSmokePayload();
            var ok = (bool)(execution.GetType().GetProperty("ok")?.GetValue(execution) ?? false);
            UnityEngine.Debug.Log("[VRCForge Roslyn Execution Smoke] "
                + Newtonsoft.Json.JsonConvert.SerializeObject(execution));
            if (!ok)
            {
                EditorApplication.Exit(1);
            }
        }
    }
}
