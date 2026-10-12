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
        private const string SdkDownloadUrl = "https://vrchat.com/home/download";
        private const string AvatarBasePackage = "com.vrchat.base";
        private const string AvatarPackage = "com.vrchat.avatars";
        private const string NewtonsoftPackage = "com.unity.nuget.newtonsoft-json";
        private const string TestFrameworkPackage = "com.unity.test-framework";
        private DependencyState state;
        private double nextDependencyRefresh;
        private string copiedPackageName;
        private Vector2 scrollPosition;

        [MenuItem("VRCForge/Setup/Check Dependencies")]
        public static void Open()
        {
            var window = GetWindow<VRCForgeSetupWindow>(Text("VRCForge Setup", "VRCForge 设置"));
            window.minSize = new Vector2(520f, 420f);
            window.RefreshDependencies();
        }

        internal static bool DependenciesReady()
        {
            return ReadDependencies().Ready;
        }

        private static string Text(string english, string chinese)
        {
            return VRCForgeLocalization.Text(english, chinese);
        }

        private void OnEnable()
        {
            RefreshDependencies();
        }

        private void RefreshDependencies()
        {
            state = ReadDependencies();
            nextDependencyRefresh = EditorApplication.timeSinceStartup + 1d;
            Repaint();
        }

        private void OnInspectorUpdate()
        {
            if (EditorApplication.timeSinceStartup >= nextDependencyRefresh)
                RefreshDependencies();
            else
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
                return new DependencyState(false, false, false, false, false, false, exception.Message);
            }

            return new DependencyState(
                packages.ContainsKey(AvatarBasePackage),
                packages.ContainsKey(AvatarPackage),
                packages.ContainsKey(NewtonsoftPackage),
                packages.ContainsKey(TestFrameworkPackage),
                !packages.ContainsKey(TestFrameworkPackage) &&
                    ((packages.TryGetValue(AvatarPackage, out var avatars) && avatars.version == "3.5.0") ||
                     (packages.TryGetValue(AvatarBasePackage, out var avatarBase) && avatarBase.version == "3.5.0")),
                true,
                string.Empty);
        }

        private void OnGUI()
        {
            if (state == null)
                RefreshDependencies();

            scrollPosition = EditorGUILayout.BeginScrollView(scrollPosition);
            VRCForgeLocalization.DrawLanguageSelector();
            titleContent = new GUIContent(Text("VRCForge Setup", "VRCForge 设置"));
            EditorGUILayout.LabelField(Text("VRCForge setup", "VRCForge 设置"), EditorStyles.boldLabel);
            EditorGUILayout.Space(4f);

            var compiling = EditorApplication.isCompiling || EditorApplication.isUpdating;
            var compilationFailed = EditorUtility.scriptCompilationFailed;
            var needsSdk = state.ReadSucceeded && (!state.HasBase || !state.HasAvatars);
            var needsTestFramework = state.ReadSucceeded && !state.HasTestFramework;
            if (compiling)
            {
                EditorGUILayout.HelpBox(Text(
                    "Unity is installing or compiling. Wait for it to finish before checking SDK readiness.",
                    "Unity 正在安装或编译，请等待完成后检查 SDK 状态。"), MessageType.Info);
            }
            else if (compilationFailed)
            {
                EditorGUILayout.HelpBox(Text(
                    "Unity compilation failed. Check the Console errors and add any missing packages using the instructions below, then wait for compilation to finish.",
                    "Unity 编译失败。请检查 Console 错误，按下方指引补齐缺失的包，再等待编译完成。"), MessageType.Error);
            }
            else if (!state.ReadSucceeded)
            {
                EditorGUILayout.HelpBox(Text(
                    "Dependency status is unavailable. Refresh after Unity finishes package registration.",
                    "暂时无法读取依赖状态。请等待 Unity 完成包注册后刷新。"), MessageType.Warning);
            }
            else if (state.Ready)
            {
                EditorGUILayout.HelpBox(Text(
                    "VRChat Avatar SDK is registered and Unity reports no compilation errors.",
                    "VRChat Avatar SDK 已注册，Unity 当前未报告编译错误。"), MessageType.Info);
            }
            else
            {
                EditorGUILayout.HelpBox(Text(
                    "VRChat Avatar SDK or a required package is missing. Follow the manual SDK installation and missing-package instructions below.",
                    "VRChat Avatar SDK 或所需包尚未齐全，请按下方手动安装及缺项指引补齐。"), MessageType.Warning);
            }

            DrawPackageStatus("VRChat Avatar SDK", state.Ready, state.ReadSucceeded, compiling, compilationFailed);
            EditorGUILayout.LabelField("Unity Test Framework", !state.ReadSucceeded
                ? Text("Unknown", "未知")
                : state.HasTestFramework ? Text("Installed", "已安装") : Text("Not installed", "未安装"));

            if (!state.ReadSucceeded && !string.IsNullOrEmpty(state.Error))
                EditorGUILayout.HelpBox(state.Error, MessageType.Warning);

            if (state.ReadSucceeded && (state.HasBase || state.HasAvatars) && !state.HasNewtonsoft)
                EditorGUILayout.HelpBox(Text(
                    "Missing JSON package: open Window > Package Manager, click + > Add package by name, enter com.unity.nuget.newtonsoft-json, and click Add.",
                    "缺少 JSON 包：打开 Window > Package Manager，点击 + > Add package by name，输入 com.unity.nuget.newtonsoft-json 并点击 Add。"), MessageType.Warning);
            if (state.ReadSucceeded && state.MissingLegacyTestFramework)
                EditorGUILayout.HelpBox(Text(
                    "SDK 3.5.0 needs Unity Test Framework for its NUnit references. Open Window > Package Manager > + > Add package by name, enter com.unity.test-framework (version 1.1.33), and click Add.",
                    "SDK 3.5.0 的 NUnit 引用需要 Unity Test Framework。打开 Window > Package Manager > + > Add package by name，输入 com.unity.test-framework（版本 1.1.33），点击 Add。"), MessageType.Warning);
            else if (needsTestFramework)
                EditorGUILayout.HelpBox(Text(
                    "Installing the older manual Avatar SDK 3.5.0 may also require Unity Test Framework for NUnit references. Unity provides it through Package Manager, not an SDK unitypackage. Use the button below if this package is needed; newer SDK versions do not all require it.",
                    "手动安装旧版 Avatar SDK 3.5.0 时，NUnit 引用可能还需要 Unity Test Framework。它由 Unity Package Manager 提供，不是 SDK unitypackage。需要时可用下方按钮补齐，并非所有新版 SDK 都需要它。"), MessageType.Info);

            if (needsTestFramework ||
                (state.ReadSucceeded && (state.HasBase || state.HasAvatars) && !state.HasNewtonsoft))
                EditorGUILayout.LabelField(Text(
                    "These buttons copy the package name to your clipboard and open Package Manager; you choose whether to install.",
                    "以下按钮会将包名复制到剪贴板并打开 Package Manager，由你自行决定是否安装。"), EditorStyles.wordWrappedLabel);
            if (needsTestFramework && GUILayout.Button(Text(
                "Add Test Framework in Unity", "在 Unity 中补齐 Test Framework")))
            {
                EditorGUIUtility.systemCopyBuffer = TestFrameworkPackage;
                copiedPackageName = TestFrameworkPackage;
                EditorApplication.ExecuteMenuItem("Window/Package Manager");
            }
            if (state.ReadSucceeded && (state.HasBase || state.HasAvatars) && !state.HasNewtonsoft && GUILayout.Button(Text(
                "Add missing JSON package in Unity", "在 Unity 中补齐 JSON 包")))
            {
                EditorGUIUtility.systemCopyBuffer = NewtonsoftPackage;
                copiedPackageName = NewtonsoftPackage;
                EditorApplication.ExecuteMenuItem("Window/Package Manager");
            }
            if (!string.IsNullOrEmpty(copiedPackageName))
                EditorGUILayout.HelpBox(Text(
                    "Package name copied: ", "包名已复制：") + copiedPackageName + Text(
                    ". In Package Manager, choose + > Add package by name, paste it, and click Add yourself.",
                    "。请在 Package Manager 中选择 + > Add package by name，粘贴后自行点击 Add 安装。") +
                    (copiedPackageName == TestFrameworkPackage ? Text(" Use version 1.1.33 for SDK 3.5.0.", " SDK 3.5.0 请填写版本 1.1.33。") : string.Empty), MessageType.Info);

            EditorGUILayout.Space(8f);
            using (new EditorGUILayout.HorizontalScope())
            {
                if (GUILayout.Button(Text("Refresh", "刷新")))
                    RefreshDependencies();
                if (needsSdk && GUILayout.Button(Text("Download Avatar SDK", "下载 Avatar SDK")))
                    Application.OpenURL(SdkDownloadUrl);
            }

            if (needsSdk)
            {
                EditorGUILayout.Space(6f);
                EditorGUILayout.LabelField(Text(
                "1. Click Download Avatar SDK. On the official page, find Manual SDK Installation and select Download Avatars SDK.\n2. In this Unity project, use Assets > Import Package > Custom Package to import the downloaded SDK package.\n3. Add any missing packages listed above in Package Manager, then wait for Unity compilation. Check Console errors if compilation fails.",
                "1. 点击下载 Avatar SDK，在官方页面找到 Manual SDK Installation，选择 Download Avatars SDK。\n2. 在当前 Unity 工程中，通过 Assets > Import Package > Custom Package 导入下载的 SDK 包。\n3. 在 Package Manager 中补齐上方提示的缺失包，然后等待 Unity 编译完成；失败时查看 Console 错误。"), EditorStyles.wordWrappedLabel);
            }
            EditorGUILayout.EndScrollView();
        }

        private static void DrawPackageStatus(string label, bool installed, bool readSucceeded, bool compiling, bool compilationFailed)
        {
            var status = compiling
                ? Text("Installing / compiling", "安装或编译中")
                : compilationFailed
                ? Text("Compilation failed", "编译失败")
                : !readSucceeded
                ? Text("Unknown", "未知")
                : installed ? Text("Ready", "已就绪") : Text("Missing", "缺失");
            EditorGUILayout.LabelField(label, status);
        }

        private sealed class DependencyState
        {
            internal readonly bool HasBase;
            internal readonly bool HasAvatars;
            internal readonly bool HasNewtonsoft;
            internal readonly bool HasTestFramework;
            internal readonly bool MissingLegacyTestFramework;
            internal readonly bool ReadSucceeded;
            internal readonly string Error;

            internal bool Ready
            {
                get { return ReadSucceeded && HasBase && HasAvatars && HasNewtonsoft && !MissingLegacyTestFramework; }
            }

            internal DependencyState(bool hasBase, bool hasAvatars, bool hasNewtonsoft, bool hasTestFramework, bool missingLegacyTestFramework, bool readSucceeded, string error)
            {
                HasBase = hasBase;
                HasAvatars = hasAvatars;
                HasNewtonsoft = hasNewtonsoft;
                HasTestFramework = hasTestFramework;
                MissingLegacyTestFramework = missingLegacyTestFramework;
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
