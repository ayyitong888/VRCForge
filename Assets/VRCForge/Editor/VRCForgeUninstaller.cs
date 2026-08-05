using UnityEditor;
using UnityEngine;

namespace VRCForge.Editor
{
    internal static class VRCForgeUninstaller
    {
        private const string ProductRoot = "Assets/VRCForge";

        [MenuItem("VRCForge/Uninstall VRCForge...")]
        private static void Uninstall()
        {
            if (!EditorUtility.DisplayDialog(
                "Uninstall VRCForge",
                "Stop the bundled MCP Core, clear its auto-connect preference, and remove Assets/VRCForge?",
                "Uninstall",
                "Cancel"))
            {
                return;
            }

            McpBridgeBootstrap.PrepareForUninstall();
            if (!AssetDatabase.DeleteAsset(ProductRoot))
            {
                McpBridgeBootstrap.ResumeAfterFailedUninstall();
                Debug.LogError("[VRCForge] Uninstall could not remove Assets/VRCForge. Files and the auto-connect preference were preserved; the bundled Core will reconnect when auto-connect is enabled.");
                return;
            }
            McpBridgeBootstrap.CompleteUninstall();
            AssetDatabase.Refresh();
            Debug.Log("[VRCForge] Uninstall complete. Assets, menus, MCP Core, and the auto-connect preference were removed.");
        }
    }
}
