using System;
using UnityEditor;
using UnityEngine;

namespace VRCForge.Setup
{
    public static class VRCForgeLocalization
    {
        private const string PreferenceKey = "VRCForge.Language";
        private static readonly string[] Languages = { "system", "zh-CN", "en" };

        public static event Action LanguageChanged;

        public static string Text(string english, string chinese)
        {
            var preference = EditorPrefs.GetString(PreferenceKey, "system");
            if (preference == "zh-CN")
                return chinese;
            if (preference == "en")
                return english;
            var language = Application.systemLanguage;
            return language == SystemLanguage.Chinese
                || language == SystemLanguage.ChineseSimplified
                || language == SystemLanguage.ChineseTraditional
                ? chinese
                : english;
        }

        public static void DrawLanguageSelector()
        {
            var preference = EditorPrefs.GetString(PreferenceKey, "system");
            var selected = Array.IndexOf(Languages, preference);
            if (selected < 0)
                selected = 0;
            var next = EditorGUILayout.Popup(Text("Language", "语言"), selected,
                new[] { Text("System default", "跟随系统"), "简体中文", "English" });
            if (next == selected || next < 0 || next >= Languages.Length)
                return;
            EditorPrefs.SetString(PreferenceKey, Languages[next]);
            LanguageChanged?.Invoke();
        }
    }
}
