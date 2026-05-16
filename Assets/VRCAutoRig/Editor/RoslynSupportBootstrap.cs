#if VRCFORGE_ENABLE_ROSLYN
using System;
using System.IO;
using System.Linq;
using UnityEditor;
using UnityEngine;

namespace VRCAutoRig.Editor
{
    [InitializeOnLoad]
    internal static class RoslynSupportBootstrap
    {
        private static readonly string[] RequiredDlls =
        {
            "Microsoft.CodeAnalysis.dll",
            "Microsoft.CodeAnalysis.CSharp.dll",
            "Microsoft.CodeAnalysis.Scripting.dll",
            "Microsoft.CodeAnalysis.CSharp.Scripting.dll",
            "System.Collections.Immutable.dll",
            "System.Reflection.Metadata.dll"
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
                        "[VRCAutoRig] Roslyn fallback DLLs were not found. vrc_execute_roslyn will report an install hint if called.");
                    return;
                }

                UnityEngine.Debug.Log("[VRCAutoRig] Roslyn fallback DLLs found. vrc_execute_roslyn will load them at runtime.");
            }
            catch (Exception ex)
            {
                UnityEngine.Debug.LogWarning($"[VRCAutoRig] Could not inspect Roslyn fallback DLLs: {ex.Message}");
            }
        }

        private static bool RoslynDllsInstalled()
        {
            var folder = Path.Combine(Application.dataPath, "Plugins", "Roslyn");
            return RequiredDlls.All(file => File.Exists(Path.Combine(folder, file)));
        }
    }
}
#endif
