using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEngine;
using VRCForge.Core.MCP;

namespace VRCForge.Editor
{
    [VRCForgeCommand(
        toolId: "vrc_capture_scene_view",
        Summary = "when-to-use: capture a verified Unity Scene/Game View image for visual review, including fixed front/side/back/bottom avatar angles. when-NOT-to-use: do not use for arbitrary image editing or to replace explicit free-camera positioning. Named-angle negatives: do not combine angle with pitch, yaw, roll, or cameraMode=free."
    )]
    public static class SceneViewCaptureTool
    {
        private const string PlayModeRecommendedMessage = "建议进入 Play Mode 并启动 Gesture Manager 后再截图；当前将使用 Scene View 截图 / Play Mode with Gesture Manager is recommended; current capture will use Scene View.";
        private const string PlayModeRequiredMessage = "请进入 Play Mode 并启动 Gesture Manager 后再截图 / Please enter Play Mode with Gesture Manager before capturing";
        private const string GestureManagerRecommendedMessage = "建议安装 Gesture Manager 以获得准确效果 / Gesture Manager recommended for accurate preview";
        private const string GameCameraRenderMessage = "Play Mode capture renders the active Game camera directly to avoid Gesture Manager menu overlays.";
        private const string ScreenCaptureFallbackMessage = "No active non-overlay Game camera was found; falling back to Game View screen capture. Close Gesture Manager menus if they cover the avatar.";

        public class Parameters
        {
            [VRCForgeInput("Camera mode: framed (avatar/scene framing) or free (explicit basis).", IsRequired = false)] public string cameraMode { get; set; } = "framed";
            [VRCForgeInput("Explicit free-camera world position {x,y,z}.", IsRequired = false)] public object cameraPosition { get; set; }
            [VRCForgeInput("Explicit free-camera world target {x,y,z}.", IsRequired = false)] public object targetPosition { get; set; }
            [VRCForgeInput("Explicit free-camera world up vector {x,y,z}.", IsRequired = false)] public object upVector { get; set; }
            [VRCForgeInput("Free-camera projection: orthographic or perspective.", IsRequired = false)] public string projection { get; set; } = "perspective";
            [VRCForgeInput("Free-camera orthographic size (>0).", IsRequired = false)] public float? orthographicSize { get; set; }
            [VRCForgeInput("Free-camera field of view in degrees (0,180).", IsRequired = false)] public float? fieldOfView { get; set; }
            [VRCForgeInput("Return capture readiness without writing an image.", IsRequired = false)] public bool? statusOnly { get; set; } = false;
            [VRCForgeInput("Require Play Mode before capture.", IsRequired = false)] public bool? requirePlayMode { get; set; } = false;
            [VRCForgeInput("Capture mode: auto, scene_view, or game_view.", IsRequired = false)] public string captureMode { get; set; } = "auto";
            [VRCForgeInput("Approved image output path.", IsRequired = false)] public string outputPath { get; set; } = "";
            [VRCForgeInput("Capture width, from 256 through 2048 pixels.", IsRequired = false)] public int? width { get; set; } = 960;
            [VRCForgeInput("Capture height, from 256 through 2048 pixels.", IsRequired = false)] public int? height { get; set; } = 960;
            [VRCForgeInput("Set the Scene view rotation before capture.", IsRequired = false)] public bool? setRotation { get; set; } = false;
            [VRCForgeInput("Restore the prior Scene view after capture.", IsRequired = false)] public bool? restoreView { get; set; } = true;
            [VRCForgeInput("Scene view pitch in degrees.", IsRequired = false)] public float? pitch { get; set; } = 0f;
            [VRCForgeInput("Scene view yaw in degrees.", IsRequired = false)] public float? yaw { get; set; } = 0f;
            [VRCForgeInput("Scene view roll in degrees.", IsRequired = false)] public float? roll { get; set; } = 0f;
            [VRCForgeInput("Named deterministic framed angle: front, side_left, side_right, back, or bottom (true underneath view). Do not combine with pitch/yaw/roll or free camera.", IsRequired = false)] public string angle { get; set; } = "";
            [VRCForgeInput("Optional avatar hierarchy path used for avatar-scoped capture.", IsRequired = false)] public string avatarPath { get; set; } = "";
            [VRCForgeInput("Capture scope: avatar or scene.", IsRequired = false)] public string captureScope { get; set; } = "avatar";
            [VRCForgeInput("Include all Gesture Manager runtime parameters in capture status.", IsRequired = false)] public bool? includeGestureManagerParameters { get; set; } = false;
            [VRCForgeInput("Exact Gesture Manager parameter names to include without returning the full parameter list.", IsRequired = false)] public string[] gestureManagerParameterNames { get; set; } = Array.Empty<string>();
            [VRCForgeInput("Gesture Manager parameter-name prefix to include without returning the full parameter list.", IsRequired = false)] public string gestureManagerParameterPrefix { get; set; } = "";
        }

        public static object HandleCommand(JObject @params)
        {
            try
            {
                var statusOnly = @params?["statusOnly"]?.Value<bool?>() ?? false;
                var requirePlayMode = @params?["requirePlayMode"]?.Value<bool?>() ?? false;
                var outputPath = (@params?["outputPath"]?.ToString() ?? string.Empty).Trim();
                if (!statusOnly && string.IsNullOrWhiteSpace(outputPath))
                {
                    return VRCForgeToolResult.Failed("Missing required parameter: outputPath");
                }

                var width = Mathf.Clamp(@params?["width"]?.Value<int?>() ?? 960, 256, 2048);
                var height = Mathf.Clamp(@params?["height"]?.Value<int?>() ?? 960, 256, 2048);
                var setRotation = @params?["setRotation"]?.Value<bool?>() ?? false;
                var restoreView = @params?["restoreView"]?.Value<bool?>() ?? true;
                var pitch = @params?["pitch"]?.Value<float?>() ?? 0f;
                var yaw = @params?["yaw"]?.Value<float?>() ?? 0f;
                var roll = @params?["roll"]?.Value<float?>() ?? 0f;
                var requestedAngle = (@params?["angle"]?.ToString() ?? string.Empty).Trim().ToLowerInvariant();
                var hasExplicitEuler = @params?["pitch"] != null || @params?["yaw"] != null || @params?["roll"] != null;
                if (!string.IsNullOrEmpty(requestedAngle)
                    && requestedAngle != "front" && requestedAngle != "side_left" && requestedAngle != "side_right"
                    && requestedAngle != "back" && requestedAngle != "bottom")
                {
                    return VRCForgeToolResult.RejectedBeforeMutation("capture_angle_invalid", "angle must be front, side_left, side_right, back, or bottom.", "unity_capture", "argument_validation");
                }
                if (!string.IsNullOrEmpty(requestedAngle) && hasExplicitEuler)
                {
                    return VRCForgeToolResult.RejectedBeforeMutation("capture_angle_conflict", "Named angle is mutually exclusive with explicit pitch, yaw, and roll.", "unity_capture", "argument_validation");
                }
                var avatarPath = (@params?["avatarPath"]?.ToString() ?? string.Empty).Trim();
                var captureScope = (@params?["captureScope"]?.ToString() ?? "avatar").Trim().ToLowerInvariant();
                if (captureScope != "avatar" && captureScope != "face" && captureScope != "scene")
                {
                    return VRCForgeToolResult.RejectedBeforeMutation("capture_scope_invalid", "captureScope must be avatar, face, or scene.", "unity_capture", "argument_validation");
                }
                var includeGestureManagerParameters = @params?["includeGestureManagerParameters"]?.Value<bool?>() ?? false;
                var gestureManagerParameterNames = (@params?["gestureManagerParameterNames"] as JArray)?
                    .Values<string>()
                    .Where(item => !string.IsNullOrWhiteSpace(item))
                    .Select(item => item.Trim())
                    .Distinct(StringComparer.Ordinal)
                    .Take(128)
                    .ToArray() ?? Array.Empty<string>();
                var gestureManagerParameterPrefix = (@params?["gestureManagerParameterPrefix"]?.ToString() ?? string.Empty).Trim();
                var requestedCaptureMode = (@params?["captureMode"]?.ToString() ?? "auto").Trim().ToLowerInvariant();
                var cameraMode = (@params?["cameraMode"]?.ToString() ?? "framed").Trim().ToLowerInvariant();
                if (cameraMode != "framed" && cameraMode != "free")
                {
                    return VRCForgeToolResult.RejectedBeforeMutation("camera_mode_invalid", "cameraMode must be framed or free.", "unity_capture", "argument_validation");
                }
                var freeCamera = default(FreeCameraSpec);
                if (cameraMode == "free")
                {
                    if (!string.IsNullOrEmpty(requestedAngle))
                    {
                        return VRCForgeToolResult.RejectedBeforeMutation("free_camera_angle_conflict", "Named angles require cameraMode=framed; free camera accepts explicit position/target/up only.", "unity_capture", "argument_validation");
                    }
                    var freeError = TryParseFreeCamera(@params, out freeCamera);
                    if (!string.IsNullOrEmpty(freeError))
                    {
                        return VRCForgeToolResult.RejectedBeforeMutation("free_camera_invalid", freeError, "unity_capture", "argument_validation");
                    }
                    if (setRotation || captureScope != "avatar")
                    {
                        return VRCForgeToolResult.RejectedBeforeMutation("free_camera_parameters_conflict", "cameraMode=free is mutually exclusive with setRotation and non-avatar captureScope.", "unity_capture", "argument_validation");
                    }
                }
                else if (@params?["cameraPosition"] != null || @params?["targetPosition"] != null || @params?["upVector"] != null
                    || @params?["projection"] != null || @params?["orthographicSize"] != null || @params?["fieldOfView"] != null)
                {
                    return VRCForgeToolResult.RejectedBeforeMutation("framed_camera_parameters_conflict", "Explicit free-camera parameters require cameraMode=free.", "unity_capture", "argument_validation");
                }
                if (cameraMode == "framed" && !string.IsNullOrEmpty(avatarPath)
                    && !TryResolveCaptureTarget(avatarPath, captureScope, out _, out _, out _))
                {
                    return VRCForgeToolResult.RejectedBeforeMutation("capture_target_not_found", "The requested avatarPath could not be resolved; capture refused without fallback.", "unity_capture", "capture_precondition");
                }
                if (!string.IsNullOrEmpty(requestedAngle))
                {
                    if (captureScope == "scene")
                    {
                        return VRCForgeToolResult.RejectedBeforeMutation("capture_angle_scope_conflict", "Named angles require avatar or face captureScope.", "unity_capture", "argument_validation");
                    }
                    ApplyNamedAngle(requestedAngle, ref setRotation, ref pitch, ref yaw, ref roll);
                    if (!TryResolveCaptureTarget(avatarPath, captureScope, out _, out _, out _))
                    {
                        return VRCForgeToolResult.RejectedBeforeMutation("capture_target_not_found", "Named-angle capture requires an unambiguous avatarPath target; capture refused without fallback.", "unity_capture", "capture_precondition");
                    }
                }
                if (requestedCaptureMode != "auto"
                    && requestedCaptureMode != "scene_view"
                    && requestedCaptureMode != "game_view")
                {
                    return VRCForgeToolResult.RejectedBeforeMutation(
                        "capture_mode_invalid",
                        "captureMode must be auto, scene_view, or game_view.",
                        "unity_capture",
                        "argument_validation");
                }

                var isPlayMode = EditorApplication.isPlaying;
                var captureMode = requestedCaptureMode == "auto"
                    ? (isPlayMode ? "game_view" : "scene_view")
                    : requestedCaptureMode;
                var playModeNeeded = requirePlayMode || captureMode == "game_view";
                var warnings = new List<string>();
                var gestureManagerStatus = GestureManagerRuntimeBridge.ReadStatus(
                    avatarPath,
                    includeGestureManagerParameters,
                    gestureManagerParameterNames,
                    gestureManagerParameterPrefix);
                var gestureManagerDetected = isPlayMode && gestureManagerStatus.detected;
                var activeGameCamera = isPlayMode && captureMode == "game_view" ? ResolveActiveGameCamera() : null;
                var gameViewCaptureMethod = string.Empty;

                if (!isPlayMode)
                {
                    warnings.Add(PlayModeRecommendedMessage);
                }
                else if (!gestureManagerDetected)
                {
                    warnings.Add(GestureManagerRecommendedMessage);
                    Debug.LogWarning($"[VRCForge Capture] {GestureManagerRecommendedMessage}");
                }

                if (statusOnly)
                {
                    return VRCForgeToolResult.Completed(
                        "Capture status checked.",
                        new
                        {
                            isPlayMode,
                            requestedCaptureMode,
                            cameraMode,
                            angle = requestedAngle,
                            captureMode,
                            requirePlayMode,
                            canCapture = !playModeNeeded || isPlayMode,
                            gestureManagerDetected,
                            gestureManager = gestureManagerStatus,
                            activeGameCameraDetected = activeGameCamera != null,
                            activeGameCameraName = activeGameCamera != null ? activeGameCamera.name : string.Empty,
                            warnings = warnings.ToArray(),
                            error = playModeNeeded && !isPlayMode ? PlayModeRequiredMessage : string.Empty
                        });
                }

                if (playModeNeeded && !isPlayMode)
                {
                    return VRCForgeToolResult.RejectedBeforeMutation(
                        "capture_play_mode_required",
                        PlayModeRequiredMessage,
                        "unity_capture",
                        "capture_precondition");
                }
                if (!string.IsNullOrEmpty(requestedAngle) && captureMode == "game_view" && activeGameCamera == null)
                {
                    return VRCForgeToolResult.RejectedBeforeMutation("capture_camera_unavailable", "Named-angle Game View capture requires an active Game camera; capture refused without screen fallback.", "unity_capture", "capture_precondition");
                }

                var absolutePath = ResolveToAbsolutePath(outputPath);
                var directory = Path.GetDirectoryName(absolutePath);
                if (string.IsNullOrEmpty(directory))
                {
                    return VRCForgeToolResult.Failed($"Cannot resolve parent folder for screenshot path: {outputPath}");
                }

                Directory.CreateDirectory(directory);

                if (captureMode == "game_view")
                {
                    var playUsedOrbitCamera = false;
                    var playResolvedAvatarPath = string.Empty;
                    var playTargetCenter = Vector3.zero;
                    var playCameraPosition = Vector3.zero;
                    var playOrthographicSize = 0f;
                    CameraObservation playCameraObservation = null;

                    TryShowGameView();
                    if (cameraMode == "free")
                    {
                        var freeObservation = CaptureFreeCamera(activeGameCamera, absolutePath, width, height, freeCamera);
                        return VRCForgeToolResult.Completed($"Captured Game View screenshot: {absolutePath}", new
                        {
                            imagePath = absolutePath.Replace("\\", "/"), width, height, cameraMode, projection = freeCamera.ProjectionName,
                            cameraPosition = ToObject(freeCamera.Position), targetPosition = ToObject(freeCamera.Target), upVector = ToObject(freeCamera.Up),
                            cameraBasis = freeObservation.Basis, cameraQuaternion = ToObject(freeObservation.Rotation), projectionMatrix = FlattenRowMajor(freeObservation.Projection),
                            viewMatrix = FlattenRowMajor(freeObservation.View), cameraEvidence = BuildCameraEvidence(freeObservation),
                            warnings = warnings.ToArray(), captureMode, isPlayMode
                        });
                    }
                    if (setRotation
                        && activeGameCamera != null
                        && TryResolveCaptureTarget(
                            avatarPath,
                            captureScope,
                            out var playBounds,
                            out var playBaseRotation,
                            out playResolvedAvatarPath))
                    {
                        playCameraObservation = CaptureOrbitCamera(
                            sceneCamera: activeGameCamera,
                            absolutePath: absolutePath,
                            width: width,
                            height: height,
                            pitch: pitch,
                            yaw: yaw,
                            roll: roll,
                            bounds: playBounds,
                            baseRotation: playBaseRotation,
                            out playTargetCenter,
                            out playCameraPosition,
                            out playOrthographicSize);
                        playUsedOrbitCamera = true;
                        gameViewCaptureMethod = "play_mode_orbit_camera";
                        warnings.Add("Play Mode avatar capture used a temporary orbit camera; the active Game camera and scene were not modified.");
                    }
                    else
                    {
                        if (setRotation)
                        {
                            warnings.Add("Play Mode avatar target could not be resolved for temporary framing; falling back to the active Game camera without modifying it.");
                        }
                        gameViewCaptureMethod = CaptureGameViewToPng(absolutePath, width, height, warnings);
                    }
                    return VRCForgeToolResult.Completed(
                        $"Captured Game View screenshot: {absolutePath}",
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
                            requestedCaptureMode,
                            cameraMode,
                            angle = requestedAngle,
                            resolvedAvatarPath = playResolvedAvatarPath,
                            usedOrbitCamera = playUsedOrbitCamera,
                            captureMode,
                            isPlayMode,
                            gestureManagerDetected,
                            gestureManager = gestureManagerStatus,
                            activeGameCameraDetected = activeGameCamera != null,
                            activeGameCameraName = activeGameCamera != null ? activeGameCamera.name : string.Empty,
                            gameViewCaptureMethod,
                            warnings = warnings.ToArray(),
                            targetCenter = new { x = playTargetCenter.x, y = playTargetCenter.y, z = playTargetCenter.z },
                            cameraPosition = new { x = playCameraPosition.x, y = playCameraPosition.y, z = playCameraPosition.z },
                            targetPosition = new { x = playTargetCenter.x, y = playTargetCenter.y, z = playTargetCenter.z },
                            upVector = playCameraObservation != null ? ToObject(playCameraObservation.Up) : null,
                            cameraBasis = playCameraObservation != null ? playCameraObservation.Basis : null,
                            projection = playCameraObservation != null ? playCameraObservation.ProjectionName : string.Empty,
                            cameraQuaternion = playCameraObservation != null ? ToObject(playCameraObservation.Rotation) : null,
                            projectionMatrix = playCameraObservation != null ? FlattenRowMajor(playCameraObservation.Projection) : null,
                            viewMatrix = playCameraObservation != null ? FlattenRowMajor(playCameraObservation.View) : null,
                            orthographicSize = playOrthographicSize,
                            cameraEvidence = playCameraObservation != null ? BuildCameraEvidence(playCameraObservation) : null
                        });
                }

                var sceneView = SceneView.lastActiveSceneView ?? EditorWindow.GetWindow<SceneView>();
                if (sceneView == null)
                {
                    return VRCForgeToolResult.Failed("No SceneView is available for screenshot capture.");
                }

                var camera = sceneView.camera;
                if (camera == null)
                {
                    return VRCForgeToolResult.Failed("SceneView camera is not available for screenshot capture.");
                }

                sceneView.Show();

                if (cameraMode == "free")
                {
                    var freeObservation = CaptureFreeCamera(camera, absolutePath, width, height, freeCamera);
                    return VRCForgeToolResult.Completed($"Captured SceneView screenshot: {absolutePath}", new
                    {
                        imagePath = absolutePath.Replace("\\", "/"), width, height, cameraMode, projection = freeCamera.ProjectionName,
                        cameraPosition = ToObject(freeCamera.Position), targetPosition = ToObject(freeCamera.Target), upVector = ToObject(freeCamera.Up),
                        cameraBasis = freeObservation.Basis, cameraQuaternion = ToObject(freeObservation.Rotation), projectionMatrix = FlattenRowMajor(freeObservation.Projection),
                        viewMatrix = FlattenRowMajor(freeObservation.View), cameraEvidence = BuildCameraEvidence(freeObservation),
                        warnings = warnings.ToArray(), captureMode, isPlayMode
                    });
                }

                var usedOrbitCamera = false;
                var resolvedAvatarPath = string.Empty;
                var targetCenter = Vector3.zero;
                var cameraPosition = Vector3.zero;
                var orthographicSize = 0f;
                CameraObservation cameraObservation = null;

                if (setRotation && TryResolveCaptureTarget(avatarPath, captureScope, out var bounds, out var baseRotation, out resolvedAvatarPath))
                {
                    cameraObservation = CaptureOrbitCamera(
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

                return VRCForgeToolResult.Completed(
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
                        requestedCaptureMode,
                        cameraMode,
                        angle = requestedAngle,
                        resolvedAvatarPath,
                        usedOrbitCamera,
                        captureMode,
                        isPlayMode,
                        gestureManagerDetected,
                        warnings = warnings.ToArray(),
                        targetCenter = new { x = targetCenter.x, y = targetCenter.y, z = targetCenter.z },
                        cameraPosition = new { x = cameraPosition.x, y = cameraPosition.y, z = cameraPosition.z },
                        targetPosition = new { x = targetCenter.x, y = targetCenter.y, z = targetCenter.z },
                        upVector = cameraObservation != null ? ToObject(cameraObservation.Up) : null,
                        cameraBasis = cameraObservation != null ? cameraObservation.Basis : null,
                        projection = cameraObservation != null ? cameraObservation.ProjectionName : string.Empty,
                        cameraQuaternion = cameraObservation != null ? ToObject(cameraObservation.Rotation) : null,
                        projectionMatrix = cameraObservation != null ? FlattenRowMajor(cameraObservation.Projection) : null,
                        viewMatrix = cameraObservation != null ? FlattenRowMajor(cameraObservation.View) : null,
                        orthographicSize,
                        cameraEvidence = cameraObservation != null ? BuildCameraEvidence(cameraObservation) : null
                    });
            }
            catch (Exception ex)
            {
                return VRCForgeToolResult.Failed($"SceneView capture failed: {ex.Message}\n{ex.StackTrace}");
            }
        }

        private static string CaptureGameViewToPng(string absolutePath, int width, int height, List<string> warnings)
        {
            EditorApplication.QueuePlayerLoopUpdate();
            var camera = ResolveActiveGameCamera();
            if (camera != null)
            {
                warnings.Add(GameCameraRenderMessage);
                CaptureCameraToPng(camera, absolutePath, width, height);
                return "active_game_camera";
            }

            warnings.Add(ScreenCaptureFallbackMessage);
            var source = ScreenCapture.CaptureScreenshotAsTexture();
            if (source != null)
            {
                try
                {
                    var finalTexture = CenterCropAndResizeTexture(source, width, height);
                    try
                    {
                        File.WriteAllBytes(absolutePath, finalTexture.EncodeToPNG());
                    }
                    finally
                    {
                        UnityEngine.Object.DestroyImmediate(finalTexture);
                    }
                    return "screen_capture";
                }
                finally
                {
                    UnityEngine.Object.DestroyImmediate(source);
                }
            }

            throw new InvalidOperationException("Game View screen capture texture was not available, and no active Game camera was found.");
        }

        private static Texture2D CenterCropAndResizeTexture(Texture2D source, int width, int height)
        {
            var targetAspect = width / (float)height;
            var sourceAspect = source.width / (float)source.height;
            var cropWidth = source.width;
            var cropHeight = source.height;

            if (sourceAspect > targetAspect)
            {
                cropWidth = Mathf.Max(1, Mathf.RoundToInt(source.height * targetAspect));
            }
            else if (sourceAspect < targetAspect)
            {
                cropHeight = Mathf.Max(1, Mathf.RoundToInt(source.width / targetAspect));
            }

            var cropX = Mathf.Max(0, (source.width - cropWidth) / 2);
            var cropY = Mathf.Max(0, (source.height - cropHeight) / 2);
            var cropped = new Texture2D(cropWidth, cropHeight, TextureFormat.RGB24, false);
            Texture2D result = null;

            try
            {
                cropped.SetPixels(source.GetPixels(cropX, cropY, cropWidth, cropHeight));
                cropped.Apply();

                if (cropWidth == width && cropHeight == height)
                {
                    result = cropped;
                    cropped = null;
                    return result;
                }

                result = ResizeTexture(cropped, width, height);
                return result;
            }
            finally
            {
                if (cropped != null)
                {
                    UnityEngine.Object.DestroyImmediate(cropped);
                }
            }
        }

        private static Texture2D ResizeTexture(Texture2D source, int width, int height)
        {
            var renderTexture = RenderTexture.GetTemporary(width, height, 0, RenderTextureFormat.ARGB32);
            var previousActive = RenderTexture.active;
            try
            {
                Graphics.Blit(source, renderTexture);
                RenderTexture.active = renderTexture;
                var result = new Texture2D(width, height, TextureFormat.RGB24, false);
                result.ReadPixels(new Rect(0, 0, width, height), 0, 0);
                result.Apply();
                return result;
            }
            finally
            {
                RenderTexture.active = previousActive;
                RenderTexture.ReleaseTemporary(renderTexture);
            }
        }

        private static Camera ResolveActiveGameCamera()
        {
            if (Camera.main != null && Camera.main.isActiveAndEnabled && !IsLikelyOverlayCamera(Camera.main))
            {
                return Camera.main;
            }

            Camera bestCamera = null;
            Camera fallbackCamera = null;
            foreach (var camera in Camera.allCameras)
            {
                if (camera == null || !camera.isActiveAndEnabled || !camera.gameObject.activeInHierarchy)
                {
                    continue;
                }

                if (fallbackCamera == null || camera.depth >= fallbackCamera.depth)
                {
                    fallbackCamera = camera;
                }

                if (IsLikelyOverlayCamera(camera))
                {
                    continue;
                }

                if (bestCamera == null || camera.depth >= bestCamera.depth)
                {
                    bestCamera = camera;
                }
            }

            return bestCamera ?? fallbackCamera;
        }

        private static bool IsLikelyOverlayCamera(Camera camera)
        {
            if (camera == null || camera.gameObject == null)
            {
                return true;
            }

            var uiLayer = LayerMask.NameToLayer("UI");
            if (uiLayer >= 0)
            {
                var uiMask = 1 << uiLayer;
                if ((camera.cullingMask & ~uiMask) == 0)
                {
                    return true;
                }
            }

            var text = $"{camera.name} {camera.gameObject.name} {LayerMask.LayerToName(camera.gameObject.layer)}".ToLowerInvariant();
            return ContainsAny(text, "gesture", "menu", "overlay", "canvas", "ui");
        }

        private static void TryShowGameView()
        {
            try
            {
                var gameViewType = Type.GetType("UnityEditor.GameView,UnityEditor");
                if (gameViewType == null)
                {
                    return;
                }

                var gameView = EditorWindow.GetWindow(gameViewType);
                gameView?.Show();
                gameView?.Repaint();
            }
            catch (Exception ex)
            {
                Debug.LogWarning($"[VRCForge Capture] Could not focus Game View before capture: {ex.Message}");
            }
        }

        private static bool IsGestureManagerRunning()
        {
            if (GestureManagerRuntimeBridge.IsRunning())
            {
                return true;
            }
            foreach (var behaviour in Resources.FindObjectsOfTypeAll<MonoBehaviour>())
            {
                if (behaviour == null || behaviour.gameObject == null || !IsSceneObject(behaviour.gameObject))
                {
                    continue;
                }

                if (!behaviour.isActiveAndEnabled)
                {
                    continue;
                }

                var type = behaviour.GetType();
                var text = $"{type.FullName} {type.Name} {behaviour.gameObject.name}";
                if (ContainsIgnoreCase(text, "GestureManager") || ContainsIgnoreCase(text, "Gesture Manager"))
                {
                    return true;
                }
            }

            foreach (var transform in Resources.FindObjectsOfTypeAll<Transform>())
            {
                if (transform == null || transform.gameObject == null || !IsSceneObject(transform.gameObject))
                {
                    continue;
                }

                if (transform.gameObject.activeInHierarchy && ContainsIgnoreCase(transform.name, "GestureManager"))
                {
                    return true;
                }
            }

            return false;
        }

        private static bool IsSceneObject(GameObject gameObject)
        {
            return gameObject.scene.IsValid() && !EditorUtility.IsPersistent(gameObject);
        }

        private static bool ContainsIgnoreCase(string text, string term)
        {
            return !string.IsNullOrEmpty(text)
                && !string.IsNullOrEmpty(term)
                && text.IndexOf(term, StringComparison.OrdinalIgnoreCase) >= 0;
        }

        private sealed class FreeCameraSpec
        {
            public Vector3 Position;
            public Vector3 Target;
            public Vector3 Up;
            public string ProjectionName;
            public float OrthographicSize;
            public float FieldOfView;
        }

        private sealed class CameraObservation
        {
            public Vector3 Position;
            public Vector3 Target;
            public Quaternion Rotation;
            public string ProjectionName;
            public float OrthographicSize;
            public float FieldOfView;
            public float Aspect;
            public float NearClip;
            public float FarClip;
            public Matrix4x4 CameraToWorld;
            public Matrix4x4 View;
            public Matrix4x4 Projection;
            public Matrix4x4 GpuProjection;
            public Matrix4x4 ViewProjection;
            public object Basis;
            public Vector3 Up;
        }

        private static string TryParseFreeCamera(JObject parameters, out FreeCameraSpec spec)
        {
            spec = null;
            Vector3 position, target, up;
            string error;
            if (!TryReadVector3(parameters?["cameraPosition"], "cameraPosition", out position, out error)) return error;
            if (!TryReadVector3(parameters?["targetPosition"], "targetPosition", out target, out error)) return error;
            if (!TryReadVector3(parameters?["upVector"], "upVector", out up, out error)) return error;
            var direction = target - position;
            if (direction.sqrMagnitude < 1e-8f) return "cameraPosition and targetPosition must differ.";
            if (up.sqrMagnitude < 1e-8f) return "upVector must be non-zero.";
            if (Vector3.Cross(direction, up).sqrMagnitude < 1e-8f) return "upVector must not be collinear with cameraPosition-targetPosition.";
            var projection = (parameters?["projection"]?.ToString() ?? "perspective").Trim().ToLowerInvariant();
            if (projection != "orthographic" && projection != "perspective") return "projection must be orthographic or perspective.";
            var hasOrtho = parameters?["orthographicSize"] != null;
            var hasFov = parameters?["fieldOfView"] != null;
            if (hasOrtho == hasFov) return "Free camera requires exactly one of orthographicSize or fieldOfView.";
            var ortho = parameters?["orthographicSize"]?.Value<float>() ?? 0f;
            var fov = parameters?["fieldOfView"]?.Value<float>() ?? 0f;
            if (projection == "orthographic" && !hasOrtho) return "orthographic projection requires orthographicSize.";
            if (projection == "perspective" && !hasFov) return "perspective projection requires fieldOfView.";
            if (projection == "orthographic" && (hasFov || !IsFinitePositive(ortho))) return "orthographicSize must be finite and > 0, and fieldOfView must be omitted.";
            if (projection == "perspective" && (hasOrtho || !IsFinite(fov) || fov <= 0f || fov >= 180f)) return "fieldOfView must be finite and in (0,180), and orthographicSize must be omitted.";
            spec = new FreeCameraSpec { Position = position, Target = target, Up = up, ProjectionName = projection, OrthographicSize = ortho, FieldOfView = fov };
            return string.Empty;
        }

        private static bool TryReadVector3(JToken token, string name, out Vector3 value, out string error)
        {
            value = Vector3.zero;
            error = string.Empty;
            if (token == null) { error = $"{name} is required for cameraMode=free."; return false; }
            try
            {
                float x, y, z;
                if (token.Type == JTokenType.Array && token.Count() == 3)
                {
                    x = token[0].Value<float>(); y = token[1].Value<float>(); z = token[2].Value<float>();
                }
                else if (token.Type == JTokenType.Object)
                {
                    x = token["x"].Value<float>(); y = token["y"].Value<float>(); z = token["z"].Value<float>();
                }
                else { error = $"{name} must be an object with finite x,y,z or a 3-item array."; return false; }
                if (!IsFinite(x) || !IsFinite(y) || !IsFinite(z)) { error = $"{name} must contain finite x,y,z values."; return false; }
                value = new Vector3(x, y, z); return true;
            }
            catch { error = $"{name} must be an object with finite x,y,z or a 3-item array."; return false; }
        }

        private static bool IsFinite(float value) { return !float.IsNaN(value) && !float.IsInfinity(value); }
        private static bool IsFinitePositive(float value) { return IsFinite(value) && value > 0f; }
        private static object ToObject(Vector3 v) { return new { x = v.x, y = v.y, z = v.z }; }
        private static object ToObject(Quaternion q) { return new { x = q.x, y = q.y, z = q.z, w = q.w }; }
        private static float[] FlattenRowMajor(Matrix4x4 m)
        {
            var result = new float[16];
            for (var row = 0; row < 4; row++) for (var col = 0; col < 4; col++) result[row * 4 + col] = m[row, col];
            return result;
        }

        private static Dictionary<string, object> BuildCameraEvidence(CameraObservation observation)
        {
            var evidence = new Dictionary<string, object>(StringComparer.Ordinal)
            {
                ["position"] = ToObject(observation.Position),
                ["target"] = ToObject(observation.Target),
                ["basis"] = observation.Basis,
                ["quaternion"] = ToObject(observation.Rotation),
                ["projection"] = observation.ProjectionName,
                ["aspect"] = observation.Aspect,
                ["nearClip"] = observation.NearClip,
                ["farClip"] = observation.FarClip,
                ["matrix"] = new Dictionary<string, object>(StringComparer.Ordinal)
                {
                    ["cameraToWorld"] = FlattenRowMajor(observation.CameraToWorld),
                    ["worldToCamera"] = FlattenRowMajor(observation.View),
                    ["projection"] = FlattenRowMajor(observation.Projection),
                    ["gpuProjection"] = FlattenRowMajor(observation.GpuProjection),
                    ["viewProjection"] = FlattenRowMajor(observation.ViewProjection),
                },
                ["matrixOrder"] = "row_major",
                ["coordinateSpace"] = "unity_world",
            };
            if (observation.ProjectionName == "orthographic")
            {
                evidence["orthographicSize"] = observation.OrthographicSize;
            }
            else
            {
                evidence["fieldOfView"] = observation.FieldOfView;
            }
            return evidence;
        }

        private static CameraObservation CaptureFreeCamera(Camera source, string absolutePath, int width, int height, FreeCameraSpec spec)
        {
            var go = new GameObject("VRCForge_FreeCaptureCamera") { hideFlags = HideFlags.HideAndDontSave };
            var camera = go.AddComponent<Camera>();
            try
            {
                if (source != null)
                {
                    camera.CopyFrom(source);
                }
                var rotation = Quaternion.LookRotation((spec.Target - spec.Position).normalized, spec.Up.normalized);
                camera.transform.SetPositionAndRotation(spec.Position, rotation);
                camera.aspect = width / (float)height;
                camera.orthographic = spec.ProjectionName == "orthographic";
                if (camera.orthographic) camera.orthographicSize = spec.OrthographicSize; else camera.fieldOfView = spec.FieldOfView;
                camera.nearClipPlane = 0.01f;
                camera.farClipPlane = Mathf.Max(camera.farClipPlane, 1000f);
                CaptureCameraToPng(camera, absolutePath, width, height);
                var gpuProjection = GL.GetGPUProjectionMatrix(camera.projectionMatrix, true);
                return new CameraObservation
                {
                    Position = spec.Position,
                    Target = spec.Target,
                    Rotation = rotation,
                    ProjectionName = spec.ProjectionName,
                    OrthographicSize = camera.orthographicSize,
                    FieldOfView = camera.fieldOfView,
                    Aspect = camera.aspect,
                    NearClip = camera.nearClipPlane,
                    FarClip = camera.farClipPlane,
                    CameraToWorld = camera.cameraToWorldMatrix,
                    View = camera.worldToCameraMatrix,
                    Projection = camera.projectionMatrix,
                    GpuProjection = gpuProjection,
                    ViewProjection = gpuProjection * camera.worldToCameraMatrix,
                    Basis = new { right = ToObject(camera.transform.right), up = ToObject(camera.transform.up), forward = ToObject(camera.transform.forward) },
                    Up = camera.transform.up
                };
            }
            finally { UnityEngine.Object.DestroyImmediate(go); }
        }

        private static CameraObservation CaptureOrbitCamera(
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
                captureCamera.aspect = width / (float)height;
                captureCamera.orthographic = true;
                captureCamera.orthographicSize = orthographicSize;
                captureCamera.nearClipPlane = 0.01f;
                captureCamera.farClipPlane = Mathf.Max(distance + bounds.size.magnitude * 2.0f, 10.0f);
                captureCamera.targetTexture = null;
                CaptureCameraToPng(captureCamera, absolutePath, width, height);
                var gpuProjection = GL.GetGPUProjectionMatrix(captureCamera.projectionMatrix, true);
                return new CameraObservation
                {
                    Position = cameraPosition,
                    Target = targetCenter,
                    Rotation = rotation,
                    ProjectionName = "orthographic",
                    OrthographicSize = captureCamera.orthographicSize,
                    FieldOfView = captureCamera.fieldOfView,
                    Aspect = captureCamera.aspect,
                    NearClip = captureCamera.nearClipPlane,
                    FarClip = captureCamera.farClipPlane,
                    CameraToWorld = captureCamera.cameraToWorldMatrix,
                    View = captureCamera.worldToCameraMatrix,
                    Projection = captureCamera.projectionMatrix,
                    GpuProjection = gpuProjection,
                    ViewProjection = gpuProjection * captureCamera.worldToCameraMatrix,
                    Basis = new { right = ToObject(captureCamera.transform.right), up = ToObject(captureCamera.transform.up), forward = ToObject(captureCamera.transform.forward) },
                    Up = captureCamera.transform.up
                };
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

        private static void ApplyNamedAngle(string angle, ref bool setRotation, ref float pitch, ref float yaw, ref float roll)
        {
            setRotation = true;
            pitch = 0f;
            yaw = 0f;
            roll = 0f;
            switch (angle)
            {
                case "front": break;
                case "side_left": yaw = 90f; break;
                case "side_right": yaw = -90f; break;
                case "back": yaw = 180f; break;
                // Positive X looks downward in Unity; -90 is the true underneath view.
                case "bottom": pitch = -90f; break;
                default: throw new ArgumentOutOfRangeException(nameof(angle), angle, "Unsupported named capture angle.");
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

            // Named captures must be deterministic under Gesture Manager animation:
            // use the avatar root's horizontal heading, never an animated Neck/Head
            // transform that may be supplied as avatarPath.
            var orientationRoot = FindAvatarOrientationRoot(target);
            var forward = orientationRoot.forward;
            forward.y = 0f;
            if (forward.sqrMagnitude < 0.0001f)
            {
                forward = Vector3.forward;
            }
            baseRotation = Quaternion.LookRotation(-forward.normalized, Vector3.up);

            resolvedAvatarPath = GetTransformPath(target);
            return true;
        }

        private static Transform FindAvatarOrientationRoot(Transform target)
        {
            for (var current = target; current != null; current = current.parent)
            {
                foreach (var component in current.GetComponents<Component>())
                {
                    var typeName = component != null ? component.GetType().FullName : string.Empty;
                    if (string.Equals(typeName, "VRC.SDK3.Avatars.Components.VRCAvatarDescriptor", StringComparison.Ordinal))
                    {
                        return current;
                    }
                }
            }
            return target.root != null ? target.root : target;
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

            return ContainsAny(text, "face", "head", "body", "atama", "顔", "頭", "头");
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
            Transform firstSceneRendererRoot = null;
            var exactMatches = new List<Transform>();
            var fallbackMatches = new List<Transform>();

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
                if (fullPath == requested)
                {
                    exactMatches.Add(transform);
                }
                else if (name == requested || fullPath.EndsWith("/" + requested, StringComparison.Ordinal))
                {
                    fallbackMatches.Add(transform);
                }
            }

            if (!string.IsNullOrEmpty(requested))
            {
                if (exactMatches.Count == 1)
                {
                    return exactMatches[0];
                }
                if (exactMatches.Count == 0 && fallbackMatches.Count == 1)
                {
                    return fallbackMatches[0];
                }
                return null;
            }
            return firstSceneRendererRoot;
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
