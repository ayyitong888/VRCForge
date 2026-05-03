using System;
using System.Threading.Tasks;
using MCPForUnity.Editor.Services;
using UnityEditor;
using UnityEngine;

namespace VRCAutoRig.Editor
{
    [InitializeOnLoad]
    public static class McpBridgeBootstrap
    {
        private const string AutoConnectKey = "VRCAutoRig.McpBridgeBootstrap.AutoConnect";
        private const string HttpBaseUrl = "http://127.0.0.1:8080";

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

        [MenuItem("VRCAutoRig/MCP/Start Bridge Now")]
        public static void StartBridgeNow()
        {
            _ = StartBridgeWithRetryAsync("menu");
        }

        [MenuItem("VRCAutoRig/MCP/Auto Connect Enabled")]
        public static void ToggleAutoConnect()
        {
            var enabled = !EditorPrefs.GetBool(AutoConnectKey, true);
            EditorPrefs.SetBool(AutoConnectKey, enabled);
            Menu.SetChecked("VRCAutoRig/MCP/Auto Connect Enabled", enabled);
            Debug.Log($"[VRCAutoRig MCP] Auto connect {(enabled ? "enabled" : "disabled")}.");
        }

        [MenuItem("VRCAutoRig/MCP/Auto Connect Enabled", true)]
        public static bool ToggleAutoConnectValidate()
        {
            Menu.SetChecked("VRCAutoRig/MCP/Auto Connect Enabled", EditorPrefs.GetBool(AutoConnectKey, true));
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
                for (var attempt = 1; attempt <= 10; attempt++)
                {
                    if (await TryStartBridgeAsync(source, attempt))
                    {
                        return;
                    }

                    await Task.Delay(TimeSpan.FromSeconds(3));
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
                        Debug.LogWarning($"[VRCAutoRig MCP] Bridge start attempt {attempt} from {source} did not connect yet.");
                        return false;
                    }
                }

                var verification = await MCPServiceLocator.Bridge.VerifyAsync();
                if (!verification.Success)
                {
                    Debug.LogWarning($"[VRCAutoRig MCP] Bridge verification attempt {attempt} failed: {verification.Message}");
                    return false;
                }

                Debug.Log($"[VRCAutoRig MCP] Bridge connected to {HttpBaseUrl} from {source}.");
                return true;
            }
            catch (Exception ex)
            {
                Debug.LogWarning($"[VRCAutoRig MCP] Bridge start attempt {attempt} failed: {ex.Message}");
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
                Debug.LogWarning($"[VRCAutoRig MCP] Could not refresh MCP preference cache: {ex.Message}");
            }
        }
    }
}
