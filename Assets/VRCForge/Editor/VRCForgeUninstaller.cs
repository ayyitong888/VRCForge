using UnityEditor;
using UnityEngine;
using VRCForge.Setup;

namespace VRCForge.Editor
{
    internal static class VRCForgeUninstaller
    {
        private const string ProductRoot = "Assets/VRCForge";

        [MenuItem("VRCForge/Uninstall VRCForge...")]
        private static void Uninstall()
        {
            if (!EditorUtility.DisplayDialog(
                VRCForgeLocalization.Text("Uninstall VRCForge", "卸载 VRCForge"),
                VRCForgeLocalization.Text("Stop the bundled MCP Core, clear its auto-connect preference, and remove Assets/VRCForge?",
                    "停止内置 MCP Core、清除自动连接偏好，并删除 Assets/VRCForge？"),
                VRCForgeLocalization.Text("Uninstall", "卸载"),
                VRCForgeLocalization.Text("Cancel", "取消")))
            {
                return;
            }

            McpBridgeBootstrap.PrepareForUninstall();
            if (!AssetDatabase.DeleteAsset(ProductRoot))
            {
                McpBridgeBootstrap.ResumeAfterFailedUninstall();
                Debug.LogError("[VRCForge] " + VRCForgeLocalization.Text(
                    "Uninstall could not remove Assets/VRCForge. Files and the auto-connect preference were preserved; the bundled Core will reconnect when auto-connect is enabled.",
                    "无法删除 Assets/VRCForge。文件和自动连接偏好已保留；启用自动连接后，内置 Core 将重新连接。"));
                return;
            }
            McpBridgeBootstrap.CompleteUninstall();
            AssetDatabase.Refresh();
            Debug.Log("[VRCForge] " + VRCForgeLocalization.Text(
                "Uninstall complete. Assets, menus, MCP Core, and the auto-connect preference were removed.",
                "卸载完成。Assets、菜单、MCP Core 和自动连接偏好已移除。"));
        }
    }
}
