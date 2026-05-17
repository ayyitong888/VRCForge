using System;
using System.IO;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Tools;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEngine;

namespace VRCForge.Editor
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
                var avatarPath = (@params?["avatarPath"]?.ToString() ?? string.Empty).Trim();
                var captureScope = (@params?["captureScope"]?.ToString() ?? "avatar").Trim().ToLowerInvariant();

                var sceneView = SceneView.lastActiveSceneView ?? EditorWindow.GetWindow<SceneView>();
                if (sceneView == null)
                {
                    return new ErrorResponse("No SceneView is available for screenshot capture.");
                }

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
                sceneView.Show();

                var usedOrbitCamera = false;
                var resolvedAvatarPath = string.Empty;
                var targetCenter = Vector3.zero;
                var cameraPosition = Vector3.zero;
                var orthographicSize = 0f;

                if (setRotation && TryResolveCaptureTarget(avatarPath, captureScope, out var bounds, out var baseRotation, out resolvedAvatarPath))
                {
                    CaptureOrbitCamera(
                        sceneCamera: camera,
                        absolutePath: absolutePath,
                        width: width,
                        height: height,
                        pitch: pitch,
                        yaw: yaw,
                        roll: roll,
                        bounds: bounds,
                        baseRotation: baseRotation,
                        out targetCenter,
                        out cameraPosition,
                        out orthographicSize);
                    usedOrbitCamera = true;
                }
                else
                {
                    var previousRotation = sceneView.rotation;
                    if (setRotation)
                    {
                        sceneView.rotation = Quaternion.Euler(pitch, yaw, roll);
                    }

                    sceneView.Repaint();
                    try
                    {
                        CaptureCameraToPng(camera, absolutePath, width, height);
                    }
                    finally
                    {
                        if (setRotation && restoreView)
                        {
                            sceneView.rotation = previousRotation;
                            sceneView.Repaint();
                        }
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
                        captureScope,
                        setRotation,
                        avatarPath,
                        resolvedAvatarPath,
                        usedOrbitCamera,
                        targetCenter = new { x = targetCenter.x, y = targetCenter.y, z = targetCenter.z },
                        cameraPosition = new { x = cameraPosition.x, y = cameraPosition.y, z = cameraPosition.z },
                        orthographicSize
                    });
            }
            catch (Exception ex)
            {
                return new ErrorResponse($"SceneView capture failed: {ex.Message}\n{ex.StackTrace}");
            }
        }

        private static void CaptureOrbitCamera(
            Camera sceneCamera,
            string absolutePath,
            int width,
            int height,
            float pitch,
            float yaw,
            float roll,
            Bounds bounds,
            Quaternion baseRotation,
            out Vector3 targetCenter,
            out Vector3 cameraPosition,
            out float orthographicSize)
        {
            var rotation = baseRotation * Quaternion.Euler(pitch, yaw, roll);
            targetCenter = bounds.center;
            var maxHorizontal = Mathf.Max(bounds.extents.x, bounds.extents.z);
            orthographicSize = Mathf.Clamp(
                Mathf.Max(bounds.extents.y * 1.05f, maxHorizontal * 1.25f),
                0.18f,
                4.0f);
            var distance = Mathf.Clamp(bounds.size.magnitude * 2.5f, 2.0f, 24.0f);
            cameraPosition = targetCenter - (rotation * Vector3.forward * distance);

            var cameraObject = new GameObject("VRCForge_OrbitCaptureCamera")
            {
                hideFlags = HideFlags.HideAndDontSave
            };
            var captureCamera = cameraObject.AddComponent<Camera>();
            try
            {
                captureCamera.CopyFrom(sceneCamera);
                captureCamera.transform.position = cameraPosition;
                captureCamera.transform.rotation = rotation;
                captureCamera.orthographic = true;
                captureCamera.orthographicSize = orthographicSize;
                captureCamera.nearClipPlane = 0.01f;
                captureCamera.farClipPlane = Mathf.Max(distance + bounds.size.magnitude * 2.0f, 10.0f);
                captureCamera.targetTexture = null;
                CaptureCameraToPng(captureCamera, absolutePath, width, height);
            }
            finally
            {
                UnityEngine.Object.DestroyImmediate(cameraObject);
            }
        }

        private static void CaptureCameraToPng(Camera camera, string absolutePath, int width, int height)
        {
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
            }
        }

        private static bool TryResolveCaptureTarget(
            string avatarPath,
            string captureScope,
            out Bounds bounds,
            out Quaternion baseRotation,
            out string resolvedAvatarPath)
        {
            bounds = new Bounds(Vector3.zero, Vector3.one);
            baseRotation = Quaternion.identity;
            resolvedAvatarPath = string.Empty;

            var target = ResolveTransform(avatarPath);
            if (target == null)
            {
                return false;
            }

            var renderers = target.GetComponentsInChildren<Renderer>(true);
            Bounds avatarBounds;
            var hasBounds = false;
            foreach (var renderer in renderers)
            {
                if (renderer == null)
                {
                    continue;
                }

                if (!hasBounds)
                {
                    bounds = renderer.bounds;
                    hasBounds = true;
                }
                else
                {
                    bounds.Encapsulate(renderer.bounds);
                }
            }

            if (!hasBounds)
            {
                bounds = new Bounds(target.position, Vector3.one * 0.5f);
            }
            avatarBounds = bounds;

            if (captureScope == "face")
            {
                bounds = BuildFaceFocusBounds(target, avatarBounds, renderers);
            }

            var forward = target.forward;
            if (forward.sqrMagnitude < 0.0001f)
            {
                forward = Vector3.forward;
            }
            baseRotation = Quaternion.LookRotation(-forward.normalized, Vector3.up);

            resolvedAvatarPath = GetTransformPath(target);
            return true;
        }

        private static Bounds BuildFaceFocusBounds(Transform avatarRoot, Bounds avatarBounds, Renderer[] renderers)
        {
            var hasFaceRendererBounds = false;
            var faceRendererBounds = avatarBounds;
            foreach (var renderer in renderers)
            {
                if (renderer == null || !IsFaceRendererCandidate(avatarRoot, renderer))
                {
                    continue;
                }

                if (!hasFaceRendererBounds)
                {
                    faceRendererBounds = renderer.bounds;
                    hasFaceRendererBounds = true;
                }
                else
                {
                    faceRendererBounds.Encapsulate(renderer.bounds);
                }
            }

            if (hasFaceRendererBounds && faceRendererBounds.size.y < avatarBounds.size.y * 0.58f)
            {
                return PadBounds(faceRendererBounds, 1.18f, 0.08f);
            }

            var height = Mathf.Max(avatarBounds.size.y, 0.5f);
            var faceHeight = Mathf.Clamp(height * 0.32f, 0.32f, 1.25f);
            var faceWidth = Mathf.Clamp(height * 0.24f, 0.28f, 1.05f);
            var faceDepth = Mathf.Clamp(height * 0.20f, 0.24f, 0.95f);
            var center = new Vector3(
                avatarBounds.center.x,
                avatarBounds.min.y + height * 0.78f,
                avatarBounds.center.z);
            return new Bounds(center, new Vector3(faceWidth, faceHeight, faceDepth));
        }

        private static Bounds PadBounds(Bounds source, float scale, float minimumPadding)
        {
            var size = source.size * Mathf.Max(scale, 1.0f);
            size.x = Mathf.Max(size.x, minimumPadding);
            size.y = Mathf.Max(size.y, minimumPadding);
            size.z = Mathf.Max(size.z, minimumPadding);
            return new Bounds(source.center, size);
        }

        private static bool IsFaceRendererCandidate(Transform avatarRoot, Renderer renderer)
        {
            var rendererPath = GetTransformPath(renderer.transform).ToLowerInvariant();
            var rootPath = GetTransformPath(avatarRoot).ToLowerInvariant();
            if (rendererPath.StartsWith(rootPath, StringComparison.Ordinal))
            {
                rendererPath = rendererPath.Substring(rootPath.Length).Trim('/');
            }

            var meshName = string.Empty;
            if (renderer is SkinnedMeshRenderer skinned && skinned.sharedMesh != null)
            {
                meshName = skinned.sharedMesh.name.ToLowerInvariant();
                for (var i = 0; i < skinned.sharedMesh.blendShapeCount; i++)
                {
                    if (ContainsAny(skinned.sharedMesh.GetBlendShapeName(i).ToLowerInvariant(), "eye", "brow", "mouth", "lip", "jaw", "cheek", "face", "nose", "tare", "tsuri", "smile"))
                    {
                        return true;
                    }
                }
            }

            var text = $"{rendererPath} {renderer.name.ToLowerInvariant()} {meshName}";
            if (ContainsAny(text, "costume", "cloth", "clothes", "hair", "tail", "wing", "accessory", "bracelet", "ribbon", "shoe", "skirt"))
            {
                return false;
            }

            return ContainsAny(text, "face", "head", "body", "atama", "顔");
        }

        private static bool ContainsAny(string text, params string[] terms)
        {
            foreach (var term in terms)
            {
                if (!string.IsNullOrEmpty(term) && text.Contains(term))
                {
                    return true;
                }
            }
            return false;
        }

        private static Transform ResolveTransform(string avatarPath)
        {
            var requested = NormalizeTransformPath(avatarPath);
            Transform nameFallback = null;
            Transform firstSceneRendererRoot = null;

            foreach (var transform in Resources.FindObjectsOfTypeAll<Transform>())
            {
                if (transform == null || transform.gameObject == null)
                {
                    continue;
                }

                if (!transform.gameObject.scene.IsValid() || EditorUtility.IsPersistent(transform.gameObject))
                {
                    continue;
                }

                if (firstSceneRendererRoot == null && transform.GetComponentInChildren<Renderer>(true) != null)
                {
                    firstSceneRendererRoot = transform;
                }

                if (string.IsNullOrEmpty(requested))
                {
                    continue;
                }

                var fullPath = NormalizeTransformPath(GetTransformPath(transform));
                var name = NormalizeTransformPath(transform.name);
                if (fullPath == requested || name == requested || fullPath.EndsWith("/" + requested, StringComparison.Ordinal))
                {
                    return transform;
                }

                if (nameFallback == null && name == requested)
                {
                    nameFallback = transform;
                }
            }

            return nameFallback ?? firstSceneRendererRoot;
        }

        private static string NormalizeTransformPath(string value)
        {
            return (value ?? string.Empty).Trim().Replace("\\", "/").Trim('/');
        }

        private static string GetTransformPath(Transform transform)
        {
            if (transform == null)
            {
                return string.Empty;
            }

            var path = transform.name;
            var parent = transform.parent;
            while (parent != null)
            {
                path = parent.name + "/" + path;
                parent = parent.parent;
            }

            return path;
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
