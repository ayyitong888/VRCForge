using System;
using System.IO;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Tools;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEngine;

namespace VRCAutoRig.Editor
{
    [McpForUnityTool(
        name: "vrc_capture_scene_view",
        Description = "Capture the active Unity SceneView camera to a PNG without requiring Roslyn."
    )]
    public static class SceneViewCaptureTool
    {
        public static object HandleCommand(JObject @params)
        {
            try
            {
                var outputPath = (@params?["outputPath"]?.ToString() ?? string.Empty).Trim();
                if (string.IsNullOrWhiteSpace(outputPath))
                {
                    return new ErrorResponse("Missing required parameter: outputPath");
                }

                var width = Mathf.Clamp(@params?["width"]?.Value<int?>() ?? 960, 256, 2048);
                var height = Mathf.Clamp(@params?["height"]?.Value<int?>() ?? 960, 256, 2048);
                var setRotation = @params?["setRotation"]?.Value<bool?>() ?? false;
                var restoreView = @params?["restoreView"]?.Value<bool?>() ?? true;
                var pitch = @params?["pitch"]?.Value<float?>() ?? 0f;
                var yaw = @params?["yaw"]?.Value<float?>() ?? 0f;
                var roll = @params?["roll"]?.Value<float?>() ?? 0f;

                var sceneView = SceneView.lastActiveSceneView ?? EditorWindow.GetWindow<SceneView>();
                if (sceneView == null)
                {
                    return new ErrorResponse("No SceneView is available for screenshot capture.");
                }

                sceneView.Show();
                var previousRotation = sceneView.rotation;
                if (setRotation)
                {
                    sceneView.rotation = Quaternion.Euler(pitch, yaw, roll);
                }

                sceneView.Repaint();
                var camera = sceneView.camera;
                if (camera == null)
                {
                    return new ErrorResponse("SceneView camera is not available for screenshot capture.");
                }

                var absolutePath = ResolveToAbsolutePath(outputPath);
                var directory = Path.GetDirectoryName(absolutePath);
                if (string.IsNullOrEmpty(directory))
                {
                    return new ErrorResponse($"Cannot resolve parent folder for screenshot path: {outputPath}");
                }

                Directory.CreateDirectory(directory);
                var renderTexture = new RenderTexture(width, height, 24);
                var texture = new Texture2D(width, height, TextureFormat.RGB24, false);
                var previousTarget = camera.targetTexture;
                var previousActive = RenderTexture.active;

                try
                {
                    camera.targetTexture = renderTexture;
                    RenderTexture.active = renderTexture;
                    camera.Render();
                    texture.ReadPixels(new Rect(0, 0, width, height), 0, 0);
                    texture.Apply();
                    File.WriteAllBytes(absolutePath, texture.EncodeToPNG());
                }
                finally
                {
                    camera.targetTexture = previousTarget;
                    RenderTexture.active = previousActive;
                    UnityEngine.Object.DestroyImmediate(renderTexture);
                    UnityEngine.Object.DestroyImmediate(texture);
                    if (setRotation && restoreView)
                    {
                        sceneView.rotation = previousRotation;
                        sceneView.Repaint();
                    }
                }

                return new SuccessResponse(
                    $"Captured SceneView screenshot: {absolutePath}",
                    new
                    {
                        imagePath = absolutePath.Replace("\\", "/"),
                        width,
                        height,
                        pitch,
                        yaw,
                        roll,
                        setRotation
                    });
            }
            catch (Exception ex)
            {
                return new ErrorResponse($"SceneView capture failed: {ex.Message}\n{ex.StackTrace}");
            }
        }

        private static string ResolveToAbsolutePath(string requestedPath)
        {
            if (Path.IsPathRooted(requestedPath))
            {
                return requestedPath.Replace("\\", "/");
            }

            var projectRoot = Directory.GetParent(Application.dataPath)?.FullName
                ?? throw new InvalidOperationException("Cannot determine Unity project root.");
            return Path.Combine(projectRoot, requestedPath).Replace("\\", "/");
        }
    }
}
