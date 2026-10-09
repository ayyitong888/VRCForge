using System;
using System.Text.RegularExpressions;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEngine;
using UnityEngine.Networking;

namespace VRCForge.Editor
{
    public sealed class CoreUpdateWindow : EditorWindow
    {
        private const string Endpoint = "https://api.github.com/repos/ayyitong888/VRCForge/releases/latest";
        private const string ReleasePrefix = "https://github.com/ayyitong888/VRCForge/releases/tag/v";
        private const ulong MaxBytes = 65536;
        private UnityWebRequest request;
        private double deadline;
        private string message = "";
        private string releaseVersion = "";

        [MenuItem("VRCForge/Check Core Updates")]
        public static void Open()
        {
            var window = GetWindow<CoreUpdateWindow>("VRCForge Core");
            window.minSize = new Vector2(360, 240);
            window.Check();
        }

        private static string Text(string english, string chinese)
        {
            var language = Application.systemLanguage;
            return language == SystemLanguage.Chinese || language == SystemLanguage.ChineseSimplified
                || language == SystemLanguage.ChineseTraditional ? chinese : english;
        }

        private void OnEnable()
        {
            AssemblyReloadEvents.beforeAssemblyReload += Cancel;
            EditorApplication.quitting += Cancel;
        }

        private void OnDisable()
        {
            AssemblyReloadEvents.beforeAssemblyReload -= Cancel;
            EditorApplication.quitting -= Cancel;
            Cancel();
        }

        private void Cancel()
        {
            EditorApplication.update -= Poll;
            if (request == null) return;
            request.Abort();
            request.Dispose();
            request = null;
        }

        private void Check()
        {
            if (request != null) return;
            releaseVersion = "";
            message = Text("Checking the official release...", "正在检查官方发布版本……");
            // One anonymous, TLS-validated, window-owned GET. Never download/import assets.
            request = UnityWebRequest.Get(Endpoint);
            request.timeout = 4;
            request.redirectLimit = 0;
            request.SetRequestHeader("Accept", "application/vnd.github+json");
            request.SetRequestHeader("User-Agent", "VRCForge-Core-Update");
            deadline = EditorApplication.timeSinceStartup + 5;
            try
            {
                request.SendWebRequest();
                EditorApplication.update += Poll;
            }
            catch (Exception)
            {
                Failed();
            }
        }

        private void Failed()
        {
            message = Text("Could not check for updates. No files were changed. Please try again later.",
                "暂时无法检查更新。未修改任何文件，请稍后重试。");
            releaseVersion = "";
            Cancel();
            Repaint();
        }

        internal static bool TryStableVersion(string text, out Version version)
        {
            version = null;
            return Regex.IsMatch(text ?? "", @"\A(0|[1-9][0-9]{0,8})\.(0|[1-9][0-9]{0,8})\.(0|[1-9][0-9]{0,8})\z")
                && Version.TryParse(text, out version);
        }

        private void Poll()
        {
            if (request == null) return;
            if (request.downloadedBytes > MaxBytes || EditorApplication.timeSinceStartup > deadline)
            {
                Failed();
                return;
            }
            if (!request.isDone) return;
            try
            {
                if (request.result != UnityWebRequest.Result.Success || request.responseCode != 200)
                    throw new InvalidOperationException();
                var release = JObject.Parse(request.downloadHandler.text);
                var tag = (string)release["tag_name"] ?? "";
                if (release["draft"]?.Type != JTokenType.Boolean || (bool)release["draft"]
                    || release["prerelease"]?.Type != JTokenType.Boolean || (bool)release["prerelease"]
                    || !tag.StartsWith("v", StringComparison.Ordinal)
                    || !TryStableVersion(tag.Substring(1), out var latest)
                    || !TryStableVersion(VRCForgeMcpToolContract.ProductVersion, out var current))
                    throw new InvalidOperationException();
                releaseVersion = latest.ToString();
                message = latest > current
                    ? Text("A newer Core is available. Update App and Unity Core from the same release.",
                        "有新版 Core。请将 App 和 Unity Core 更新至同一发布版本。")
                    : latest == current
                        ? Text("Core matches the latest official release.", "Core 已是最新官方发布版本。")
                        : Text("This Core is newer than the latest public release. Do not downgrade automatically; use its matching App.",
                            "当前 Core 比最新公开版本更高。请使用与它配套的 App，不要自动降级。");
                Cancel();
                Repaint();
            }
            catch (Exception)
            {
                Failed();
            }
        }

        private void OnGUI()
        {
            EditorGUILayout.LabelField("VRCForge Core", EditorStyles.boldLabel);
            EditorGUILayout.LabelField(Text("Installed version", "当前安装版本"), VRCForgeMcpToolContract.ProductVersion);
            if (releaseVersion.Length > 0)
                EditorGUILayout.LabelField(Text("Latest public release", "最新公开版本"), releaseVersion);
            EditorGUILayout.Space();
            EditorGUILayout.HelpBox(message.Length > 0 ? message : Text("Check the official release before updating.", "更新前请检查官方发布版本。"), MessageType.Info);
            using (new EditorGUI.DisabledScope(request != null))
                if (GUILayout.Button(Text("Check for Updates", "检查更新"))) Check();
            if (releaseVersion.Length > 0 && GUILayout.Button(Text("Open Official Release", "打开官方发布页面")))
                Application.OpenURL(ReleasePrefix + releaseVersion);
            EditorGUILayout.Space();
            EditorGUILayout.LabelField(Text(
                "Download VRCForge.unitypackage from the matching release, import it into this project, then refresh the App connection after compilation. This window never installs updates automatically.",
                "下载配套版本的 VRCForge.unitypackage，在当前项目中导入，编译完成后刷新 App 连接。本窗口不会自动安装更新。"), EditorStyles.wordWrappedLabel);
        }
    }
}
