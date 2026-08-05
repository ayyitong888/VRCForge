using System;
using System.IO;
using UnityEditor;
using UnityEngine;

namespace VRCForge.Editor
{
    [InitializeOnLoad]
    public static class McpBridgeBootstrap
    {
        // Version the preference so a disabled legacy bridge cannot suppress
        // first-run startup of the packaged 2026-07-28 Core after import.
        private const string AutoConnectKey = "VRCForge.McpBridgeBootstrap.2026-07-28.AutoConnect";
        private const double AutoConnectRetrySeconds = 5.0;
        private static readonly string[] ThirdPartyMcpPackageIds = { "com.coplaydev.unity-mcp", "com.gamelovers.unity-mcp" };
        private static double nextAutoConnectAttempt;

        static McpBridgeBootstrap()
        {
            WarnThirdPartyMcpPackages();
            AssemblyReloadEvents.beforeAssemblyReload += StopBridge;
            EditorApplication.quitting += StopBridge;
            QueueAutoConnect();
        }

        [InitializeOnLoadMethod]
        private static void QueueAutoConnectAfterReload()
        {
            QueueAutoConnect();
        }

        private static void WarnThirdPartyMcpPackages()
        {
            var packages = Path.Combine(Directory.GetParent(Application.dataPath).FullName, "Packages");
            var manifestPath = Path.Combine(packages, "manifest.json");
            var manifest = string.Empty;
            try { if (File.Exists(manifestPath)) manifest = File.ReadAllText(manifestPath); } catch (Exception) { }
            foreach (var packageId in ThirdPartyMcpPackageIds)
                if (manifest.IndexOf("\"" + packageId + "\"", StringComparison.OrdinalIgnoreCase) >= 0 || Directory.Exists(Path.Combine(packages, packageId)))
                    Debug.LogWarning("[VRCForge MCP] Third-party MCP package '" + packageId + "' may conflict with the bundled Core; remove it from Packages if it is unused.");
        }

        private static void QueueAutoConnect()
        {
            EditorApplication.update -= EnsureAutoConnected;
            if (Application.isBatchMode || !EditorPrefs.GetBool(AutoConnectKey, true))
            {
                return;
            }
            nextAutoConnectAttempt = 0.0;
            EditorApplication.update += EnsureAutoConnected;
        }

        private static void EnsureAutoConnected()
        {
            if (Application.isBatchMode || !EditorPrefs.GetBool(AutoConnectKey, true))
            {
                EditorApplication.update -= EnsureAutoConnected;
                return;
            }
            if (VRCForgeMcpCoreServer.IsReady)
            {
                EditorApplication.update -= EnsureAutoConnected;
                return;
            }
            if (EditorApplication.isCompiling || EditorApplication.isUpdating
                || EditorApplication.timeSinceStartup < nextAutoConnectAttempt)
            {
                return;
            }
            nextAutoConnectAttempt = EditorApplication.timeSinceStartup + AutoConnectRetrySeconds;
            StartBridgeNow();
            if (VRCForgeMcpCoreServer.IsReady)
            {
                EditorApplication.update -= EnsureAutoConnected;
            }
            else
            {
                nextAutoConnectAttempt = EditorApplication.timeSinceStartup + AutoConnectRetrySeconds;
            }
        }

        [MenuItem("VRCForge/MCP/Start Bridge Now")]
        public static void StartBridgeNow()
        {
            VRCForgeMcpCoreServer.Start();
        }

        [MenuItem("VRCForge/MCP/Auto Connect Enabled")]
        public static void ToggleAutoConnect()
        {
            var enabled = !EditorPrefs.GetBool(AutoConnectKey, true);
            EditorPrefs.SetBool(AutoConnectKey, enabled);
            if (enabled)
            {
                QueueAutoConnect();
            }
            else
            {
                EditorApplication.update -= EnsureAutoConnected;
            }
            Menu.SetChecked("VRCForge/MCP/Auto Connect Enabled", enabled);
            Debug.Log("[VRCForge MCP] Auto connect " + (enabled ? "enabled" : "disabled") + ".");
        }

        [MenuItem("VRCForge/MCP/Auto Connect Enabled", true)]
        public static bool ToggleAutoConnectValidate()
        {
            Menu.SetChecked("VRCForge/MCP/Auto Connect Enabled", EditorPrefs.GetBool(AutoConnectKey, true));
            return true;
        }

        internal static void PrepareForUninstall()
        {
            EditorApplication.update -= EnsureAutoConnected;
            VRCForgeMcpCoreServer.Stop();
        }

        internal static void ResumeAfterFailedUninstall()
        {
            QueueAutoConnect();
        }

        internal static void CompleteUninstall()
        {
            EditorPrefs.DeleteKey(AutoConnectKey);
        }

        private static void StopBridge()
        {
            EditorApplication.update -= EnsureAutoConnected;
            VRCForgeMcpCoreServer.Stop();
        }
    }
}
