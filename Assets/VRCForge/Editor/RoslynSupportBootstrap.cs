using System;
using System.IO;
using System.Linq;
using UnityEditor;
using UnityEngine;

namespace VRCForge.Editor
{
    [InitializeOnLoad]
    internal static class RoslynSupportBootstrap
    {
        private const string RoslynPluginPathHint = "Assets/Plugins/Roslyn";
        private static readonly string[] RequiredDlls =
        {
            "Microsoft.CodeAnalysis.dll",
            "Microsoft.CodeAnalysis.CSharp.dll",
            "Microsoft.CodeAnalysis.Scripting.dll",
            "Microsoft.CodeAnalysis.CSharp.Scripting.dll",
            "System.Collections.Immutable.dll",
            "System.Reflection.Metadata.dll",
            "System.Memory.dll",
            "System.Runtime.CompilerServices.Unsafe.dll",
            "System.Buffers.dll",
            "System.Threading.Tasks.Extensions.dll"
        };

        static RoslynSupportBootstrap()
        {
            EditorApplication.delayCall += ReportRoslynRuntimeState;
        }

        private static void ReportRoslynRuntimeState()
        {
            try
            {
                if (!RoslynDllsInstalled())
                {
                    UnityEngine.Debug.LogWarning(
                        $"[VRCForge] Roslyn Advanced Power Mode DLLs were not found under {RoslynPluginPathHint}. vrc_execute_roslyn will report an install hint if called.");
                    return;
                }

                UnityEngine.Debug.Log("[VRCForge] Roslyn Advanced Power Mode DLLs found. vrc_execute_roslyn will load them at runtime.");
            }
            catch (Exception ex)
            {
                UnityEngine.Debug.LogWarning($"[VRCForge] Could not inspect Roslyn Advanced Power Mode DLLs: {ex.Message}");
            }
        }

        private static bool RoslynDllsInstalled()
        {
            var folder = Path.Combine(Application.dataPath, "Plugins", "Roslyn");
            return RequiredDlls.All(file => File.Exists(Path.Combine(folder, file)));
        }
    }
}
