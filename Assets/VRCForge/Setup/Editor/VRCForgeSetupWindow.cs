using System;
using System.Collections.Generic;
using UnityEditor;
using UnityEngine;
using UnityEditor.PackageManager;
using PackageInfo = UnityEditor.PackageManager.PackageInfo;

namespace VRCForge.Setup
{
    internal sealed class VRCForgeSetupWindow : EditorWindow
    {
        private const string VccDownloadUrl = "https://vrchat.com/home/download";
        private const string AvatarBasePackage = "com.vrchat.base";
        private const string AvatarPackage = "com.vrchat.avatars";
        private const string NewtonsoftPackage = "com.unity.nuget.newtonsoft-json";
        private DependencyState state;

        [MenuItem("VRCForge/Setup/Check Dependencies")]
        public static void Open()
        {
            var window = GetWindow<VRCForgeSetupWindow>(Text("VRCForge Setup", "VRCForge 设置"));
            window.minSize = new Vector2(420f, 300f);
            window.RefreshDependencies();
        }

        internal static bool DependenciesReady()
        {
            return ReadDependencies().Ready;
        }

        private static string Text(string english, string chinese)
        {
            var language = Application.systemLanguage;
            return language == SystemLanguage.Chinese
                || language == SystemLanguage.ChineseSimplified
                || language == SystemLanguage.ChineseTraditional
                ? chinese
                : english;
        }

        private void OnEnable()
        {
            RefreshDependencies();
        }

        private void RefreshDependencies()
        {
            state = ReadDependencies();
            Repaint();
        }

        private static DependencyState ReadDependencies()
        {
            var packages = new Dictionary<string, PackageInfo>(StringComparer.Ordinal);
            try
            {
                foreach (var package in PackageInfo.GetAllRegisteredPackages() ?? Array.Empty<PackageInfo>())
                {
                    if (package != null && !packages.ContainsKey(package.name))
                        packages.Add(package.name, package);
                }
            }
            catch (Exception exception)
            {
                return new DependencyState(false, false, false, false, exception.Message);
            }

            return new DependencyState(
                packages.ContainsKey(AvatarBasePackage),
                packages.ContainsKey(AvatarPackage),
                packages.ContainsKey(NewtonsoftPackage),
                true,
                string.Empty);
        }

        private void OnGUI()
        {
            if (state == null)
                RefreshDependencies();

            EditorGUILayout.LabelField(Text("VRCForge setup", "VRCForge 设置"), EditorStyles.boldLabel);
            EditorGUILayout.Space(4f);

            if (!state.ReadSucceeded)
            {
                EditorGUILayout.HelpBox(Text(
                    "Dependency status is unavailable. Refresh after Unity finishes package registration.",
                    "暂时无法读取依赖状态。请等待 Unity 完成包注册后刷新。"), MessageType.Warning);
            }
            else if (state.Ready)
            {
                EditorGUILayout.HelpBox(Text(
                    "All required packages are registered. VRCForge Core can compile and start after Unity finishes reloading.",
                    "所需依赖已注册。Unity 完成重新加载后，VRCForge Core 可以编译并启动。"), MessageType.Info);
            }
            else
            {
                EditorGUILayout.HelpBox(Text(
                    "Install the missing official VRChat SDK Base and Avatars packages with VCC or ALCOM, then return here and refresh. VRCForge does not install packages automatically.",
                    "请使用 VCC 或 ALCOM 安装缺失的官方 VRChat SDK Base 和 Avatars 包，然后返回此处刷新。VRCForge 不会自动安装依赖。"), MessageType.Warning);
            }

            DrawPackageStatus(Text("VRChat SDK Base", "VRChat SDK Base"), state.HasBase, state.ReadSucceeded);
            DrawPackageStatus(Text("VRChat SDK Avatars", "VRChat SDK Avatars"), state.HasAvatars, state.ReadSucceeded);
            DrawPackageStatus(Text("Unity Newtonsoft JSON", "Unity Newtonsoft JSON"), state.HasNewtonsoft, state.ReadSucceeded);

            if (!state.ReadSucceeded && !string.IsNullOrEmpty(state.Error))
                EditorGUILayout.HelpBox(state.Error, MessageType.Warning);

            EditorGUILayout.Space(8f);
            using (new EditorGUILayout.HorizontalScope())
            {
                if (GUILayout.Button(Text("Refresh", "刷新")))
                    RefreshDependencies();
                if (GUILayout.Button(Text("Open official installation page", "打开官方安装页面")))
                    Application.OpenURL(VccDownloadUrl);
            }

            EditorGUILayout.Space(6f);
            EditorGUILayout.LabelField(Text(
                "Install the official VRChat SDK Base and Avatars packages through VCC or ALCOM. The Newtonsoft JSON package is part of the supported avatar project baseline.",
                "请通过 VCC 或 ALCOM 安装官方 VRChat SDK Base 和 Avatars。Newtonsoft JSON 包属于受支持的 Avatar 工程基线依赖。"), EditorStyles.wordWrappedLabel);
        }

        private static void DrawPackageStatus(string label, bool installed, bool readSucceeded)
        {
            var status = !readSucceeded
                ? Text("Unknown", "未知")
                : installed ? Text("Ready", "已就绪") : Text("Missing", "缺失");
            EditorGUILayout.LabelField(label, status);
        }

        private sealed class DependencyState
        {
            internal readonly bool HasBase;
            internal readonly bool HasAvatars;
            internal readonly bool HasNewtonsoft;
            internal readonly bool ReadSucceeded;
            internal readonly string Error;

            internal bool Ready
            {
                get { return ReadSucceeded && HasBase && HasAvatars && HasNewtonsoft; }
            }

            internal DependencyState(bool hasBase, bool hasAvatars, bool hasNewtonsoft, bool readSucceeded, string error)
            {
                HasBase = hasBase;
                HasAvatars = hasAvatars;
                HasNewtonsoft = hasNewtonsoft;
                ReadSucceeded = readSucceeded;
                Error = error ?? string.Empty;
            }
        }
    }

    [InitializeOnLoad]
    internal static class VRCForgeSetupAutoOpen
    {
        private const string AutoOpenedKey = "VRCForge.Setup.AutoOpened.2026-10-12";

        static VRCForgeSetupAutoOpen()
        {
            EditorApplication.delayCall += TryOpen;
        }

        private static void TryOpen()
        {
            EditorApplication.delayCall -= TryOpen;
            if (Application.isBatchMode || SessionState.GetBool(AutoOpenedKey, false))
                return;
            if (VRCForgeSetupWindow.DependenciesReady())
                return;

            SessionState.SetBool(AutoOpenedKey, true);
            VRCForgeSetupWindow.Open();
        }
    }
}
