using Microsoft.CodeAnalysis;
using Microsoft.CodeAnalysis.CSharp;

if (args.Length != 1)
{
    Console.Error.WriteLine("Usage: VRCForge.CSharpSyntaxGate <Unity asset source root>");
    return 2;
}

var sourceRoot = Path.GetFullPath(args[0]);
if (!Directory.Exists(sourceRoot))
{
    Console.Error.WriteLine($"Unity C# source root does not exist: {sourceRoot}");
    return 2;
}

var sourceFiles = Directory
    .EnumerateFiles(sourceRoot, "*.cs", SearchOption.AllDirectories)
    .OrderBy(path => path, StringComparer.OrdinalIgnoreCase)
    .ToArray();
if (sourceFiles.Length == 0)
{
    Console.Error.WriteLine($"Unity C# source root contains no .cs files: {sourceRoot}");
    return 2;
}

var parseOptions = new CSharpParseOptions(
    LanguageVersion.CSharp9,
    DocumentationMode.Parse,
    SourceCodeKind.Regular,
    new[] { "UNITY_EDITOR", "UNITY_2022_3_OR_NEWER", "VRC_SDK_VRCSDK3" });
var errorCount = 0;
foreach (var sourceFile in sourceFiles)
{
    var source = File.ReadAllText(sourceFile);
    var tree = CSharpSyntaxTree.ParseText(source, parseOptions, sourceFile);
    foreach (var diagnostic in tree.GetDiagnostics().Where(item => item.Severity == DiagnosticSeverity.Error))
    {
        var span = diagnostic.Location.GetLineSpan();
        var line = span.StartLinePosition.Line + 1;
        var column = span.StartLinePosition.Character + 1;
        Console.Error.WriteLine($"{sourceFile}({line},{column}): error {diagnostic.Id}: {diagnostic.GetMessage()}");
        errorCount++;
    }
}

if (errorCount > 0)
{
    Console.Error.WriteLine($"Unity C# syntax gate failed: {errorCount} error(s) across {sourceFiles.Length} source file(s).");
    return 1;
}

Console.WriteLine($"Unity C# syntax gate passed: {sourceFiles.Length} source file(s), 0 syntax errors.");
return 0;
