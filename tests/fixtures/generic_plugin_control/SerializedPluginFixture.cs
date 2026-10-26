using System;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.SceneManagement;
using VRCForge.Core.MCP;
using VRCForge.Editor;
using VRCForge.Editor.Generic;

namespace VRCForge.LocalReview
{
    public static class SerializedPluginFixture
    {
        private const string TargetName = "LocalReview_SerializedTarget";
        private const string ReferenceName = "LocalReview_SerializedReferenceTarget";

        public static JObject Run()
        {
            var scene = SceneManager.GetActiveScene();
            if (!scene.IsValid() || string.IsNullOrWhiteSpace(scene.path))
                throw new InvalidOperationException("Serialized property fixture requires the saved main fixture scene.");

            var target = new GameObject(TargetName);
            new GameObject(ReferenceName);
            var component = target.AddComponent<SerializedPluginFixtureData>();
            SaveFixtureScene(scene);

            var preview = Set(TargetName, "settings.count", 19, preview: true);
            Require((bool?)preview["preview"] == true && (int)preview["newValue"] == 19,
                "nested integer preview did not return the requested value");
            Require(component.settings.count == 3, "preview changed the nested integer in memory");

            var nestedWrite = Set(TargetName, "settings.count", 17);
            Require((bool?)nestedWrite["persistedReadback"] == true && (int)nestedWrite["after"] == 17,
                "nested integer write did not report persisted readback");

            var arrayWrite = Set(TargetName, "entries.Array.data[1].weight", 4.25f);
            Require((bool?)arrayWrite["persistedReadback"] == true
                && Mathf.Abs((float)arrayWrite["after"] - 4.25f) < 0.0001f,
                "array element write did not report persisted readback");

            var referenceWrite = Set(TargetName, "linkedObject", ReferenceName);
            Require((bool?)referenceWrite["persistedReadback"] == true,
                "object reference write did not report persisted readback");

            SaveFixtureScene(SceneManager.GetActiveScene());
            var scenePath = scene.path;
            EditorSceneManager.OpenScene(scenePath, OpenSceneMode.Single);

            var nestedRead = Get(TargetName, serializedPath: "settings.count");
            Require((int)nestedRead["propertyValue"] == 17, "nested integer did not survive scene reopen");
            var arrayRead = Get(TargetName, serializedPath: "entries.Array.data[1].weight");
            Require(Mathf.Abs((float)arrayRead["propertyValue"] - 4.25f) < 0.0001f,
                "array element did not survive scene reopen");
            var referenceRead = Get(TargetName, serializedPath: "linkedObject");
            Require((string)referenceRead["propertyValue"]["name"] == ReferenceName
                && !string.IsNullOrWhiteSpace((string)referenceRead["propertyValue"]["globalObjectId"]),
                "object reference did not survive scene reopen with its scene identity");

            var legacyWrite = Set(TargetName, "legacyValue", 23, useSerializedPath: false);
            Require((bool?)legacyWrite["persistedReadback"] == true && (int)legacyWrite["after"] == 23,
                "legacy reflection property path did not persist");
            var legacyRead = Get(TargetName, directPath: "legacyValue");
            Require((int)legacyRead["propertyValue"] == 23, "legacy reflection property readback failed");

            return new JObject
            {
                ["status"] = "passed",
                ["checks"] = new JArray(
                    "nested serialized integer preview without mutation",
                    "nested serialized integer save and reopened readback",
                    "Array.data element save and reopened readback",
                    "scene GameObject object-reference save and reopened identity readback",
                    "legacy reflection field save and readback"),
                ["scenePath"] = scenePath,
                ["nestedInteger"] = (int)nestedRead["propertyValue"],
                ["arrayElementWeight"] = (float)arrayRead["propertyValue"],
                ["objectReferenceName"] = (string)referenceRead["propertyValue"]["name"],
                ["legacyValue"] = (int)legacyRead["propertyValue"],
            };
        }

        private static JObject Get(string gameObjectPath, string serializedPath = null, string directPath = null)
        {
            var result = GetPropertyTool.HandleCommand(new JObject
            {
                ["gameObjectPath"] = gameObjectPath,
                ["componentType"] = typeof(SerializedPluginFixtureData).FullName,
                ["serializedPropertyPath"] = serializedPath,
                ["propertyPath"] = directPath,
            }) as VRCForgeToolResult;
            return Payload(result, "get " + (serializedPath ?? directPath));
        }

        private static JObject Set(string gameObjectPath, string path, JToken value, bool preview = false, bool useSerializedPath = true)
        {
            var request = new JObject
            {
                ["gameObjectPath"] = gameObjectPath,
                ["componentType"] = typeof(SerializedPluginFixtureData).FullName,
                ["value"] = value,
                ["preview"] = preview,
            };
            if (useSerializedPath)
                request["serializedPropertyPath"] = path;
            else
                request["propertyPath"] = path;
            return Payload(SetPropertyTool.HandleCommand(request) as VRCForgeToolResult, "set " + path);
        }

        private static JObject Payload(VRCForgeToolResult result, string label)
        {
            if (result == null || result.Kind != VRCForgeToolResultKind.Completed)
                throw new InvalidOperationException(label + " failed: " + (result == null ? "missing result" : result.ToStructuredContent().ToString()));
            var structured = result.ToStructuredContent();
            var payload = structured["data"] as JObject;
            if (payload == null)
                throw new InvalidOperationException(label + " returned no structured data payload: " + structured);
            return payload;
        }

        private static void SaveFixtureScene(Scene scene)
        {
            if (!EditorSceneManager.SaveScene(scene))
                throw new InvalidOperationException("Could not save the isolated serialized property fixture scene.");
        }

        private static void Require(bool condition, string message)
        {
            if (!condition) throw new InvalidOperationException(message);
        }
    }
}
