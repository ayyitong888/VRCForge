using System;
using System.Threading.Tasks;
using MCPForUnity.Editor.Services;
using UnityEditor;
using UnityEngine;

namespace VRCForge.Editor
{
    [InitializeOnLoad]
    public static class McpBridgeBootstrap
    {
        private const string AutoConnectKey = "VRCForge.McpBridgeBootstrap.AutoConnect";
        private const string HttpBaseUrl = "http://127.0.0.1:8080";
        private const int MaxStartAttempts = 60;
        private static readonly TimeSpan RetryDelay = TimeSpan.FromSeconds(5);

        private static bool startInProgress;

        static McpBridgeBootstrap()
        {
            if (Application.isBatchMode)
            {
                return;
            }

            EnsureMcpHttpPrefs();

            if (EditorPrefs.GetBool(AutoConnectKey, true))
            {
                EditorApplication.delayCall += () => _ = StartBridgeWithRetryAsync("auto");
            }
        }

        [MenuItem("VRCForge/MCP/Start Bridge Now")]
        public static void StartBridgeNow()
        {
            _ = StartBridgeWithRetryAsync("menu");
        }

        [MenuItem("VRCForge/MCP/Auto Connect Enabled")]
        public static void ToggleAutoConnect()
        {
            var enabled = !EditorPrefs.GetBool(AutoConnectKey, true);
            EditorPrefs.SetBool(AutoConnectKey, enabled);
            Menu.SetChecked("VRCForge/MCP/Auto Connect Enabled", enabled);
            Debug.Log($"[VRCForge MCP] Auto connect {(enabled ? "enabled" : "disabled")}.");
        }

        [MenuItem("VRCForge/MCP/Auto Connect Enabled", true)]
        public static bool ToggleAutoConnectValidate()
        {
            Menu.SetChecked("VRCForge/MCP/Auto Connect Enabled", EditorPrefs.GetBool(AutoConnectKey, true));
            return true;
        }

        private static async Task StartBridgeWithRetryAsync(string source)
        {
            if (startInProgress)
            {
                return;
            }

            startInProgress = true;
            try
            {
                for (var attempt = 1; attempt <= MaxStartAttempts; attempt++)
                {
                    if (await TryStartBridgeAsync(source, attempt))
                    {
                        return;
                    }

                    await Task.Delay(RetryDelay);
                }
            }
            finally
            {
                startInProgress = false;
            }
        }

        private static async Task<bool> TryStartBridgeAsync(string source, int attempt)
        {
            try
            {
                EnsureMcpHttpPrefs();

                if (!MCPServiceLocator.Bridge.IsRunning)
                {
                    var started = await MCPServiceLocator.Bridge.StartAsync();
                    if (!started)
                    {
                        Debug.LogWarning($"[VRCForge MCP] Bridge start attempt {attempt} from {source} did not connect yet.");
                        return false;
                    }
                }

                var verification = await MCPServiceLocator.Bridge.VerifyAsync();
                if (!verification.Success)
                {
                    Debug.LogWarning($"[VRCForge MCP] Bridge verification attempt {attempt} failed: {verification.Message}");
                    return false;
                }

                Debug.Log($"[VRCForge MCP] Bridge connected to {HttpBaseUrl} from {source}.");
                return true;
            }
            catch (Exception ex)
            {
                Debug.LogWarning($"[VRCForge MCP] Bridge start attempt {attempt} failed: {ex.Message}");
                return false;
            }
        }

        private static void EnsureMcpHttpPrefs()
        {
            EditorPrefs.SetBool("MCPForUnity.UseHttpTransport", true);
            EditorPrefs.SetString("MCPForUnity.HttpTransportScope", "local");
            EditorPrefs.SetString("MCPForUnity.HttpUrl", HttpBaseUrl);
            EditorPrefs.SetBool("MCPForUnity.ProjectScopedTools.LocalHttp", true);
            EditorPrefs.SetBool("MCPForUnity.AutoStartOnLoad", true);

            try
            {
                EditorConfigurationCache.Instance.Refresh();
            }
            catch (Exception ex)
            {
                Debug.LogWarning($"[VRCForge MCP] Could not refresh MCP preference cache: {ex.Message}");
            }
        }
    }
}
