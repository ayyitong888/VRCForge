using UnityEditor;
using UnityEngine;

namespace VRCForge.Editor
{
    [InitializeOnLoad]
    public static class McpBridgeBootstrap
    {
        private const string AutoConnectKey = "VRCForge.McpBridgeBootstrap.AutoConnect";

        static McpBridgeBootstrap()
        {
            AssemblyReloadEvents.beforeAssemblyReload += StopBridge;
            EditorApplication.quitting += StopBridge;
            if (!Application.isBatchMode && EditorPrefs.GetBool(AutoConnectKey, true))
            {
                EditorApplication.delayCall += StartBridgeNow;
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
            Menu.SetChecked("VRCForge/MCP/Auto Connect Enabled", enabled);
            Debug.Log("[VRCForge MCP] Auto connect " + (enabled ? "enabled" : "disabled") + ".");
        }

        [MenuItem("VRCForge/MCP/Auto Connect Enabled", true)]
        public static bool ToggleAutoConnectValidate()
        {
            Menu.SetChecked("VRCForge/MCP/Auto Connect Enabled", EditorPrefs.GetBool(AutoConnectKey, true));
            return true;
        }

        private static void StopBridge()
        {
            VRCForgeMcpCoreServer.Stop();
        }
    }
}
