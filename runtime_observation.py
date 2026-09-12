"""Supervised, bounded GM observation arguments and artifact readback."""
from pathlib import Path
import hashlib
import math
import re
import uuid
import struct
from PIL import Image

PUBLIC_KEYS = ("avatarPath", "parameterName", "value", "durationSeconds", "frameCount", "width", "height", "cameraPosition", "targetPosition", "upVector", "fieldOfView")
OPTIONAL_KEYS = ("parameterSteps", "rendererProbes")


def state_selection(arguments):
    """Validate the optional post-readback layer selector.

    This is Gateway projection metadata only.  It must never be forwarded to
    Unity/Core, where the complete observation receipt is validated first.
    """
    value = arguments.get("stateSelection")
    if value is None:
        return None
    if not isinstance(value, dict) or set(value) != {"layerName"}:
        raise ValueError("stateSelection requires exactly layerName.")
    layer_name = value.get("layerName")
    if not isinstance(layer_name, str) or not layer_name.strip() or len(layer_name) > 128:
        raise ValueError("stateSelection.layerName must be a non-empty string of at most 128 characters.")
    return {"layerName": layer_name}


def validate_renderer_probes(probes, avatar_path):
    if not isinstance(probes, list) or len(probes) > 8:
        raise ValueError("rendererProbes must be an array of at most 8 probes.")
    for probe in probes:
        required = {"rendererPath", "materialIndex", "propertyNames"}
        if not isinstance(probe, dict) or not required <= set(probe) or set(probe) - required - {"rendererComponentIndex", "blendShapeNames"}:
            raise ValueError("rendererProbes require rendererPath, materialIndex, propertyNames and optional rendererComponentIndex.")
        path = probe["rendererPath"]
        if not isinstance(path, str) or len(path) > 2048 or not (path == avatar_path or path.startswith(avatar_path + "/")) or any(part in ("", ".", "..") for part in path.split("/")):
            raise ValueError("rendererProbes require an exact Avatar descendant rendererPath.")
        for key in ("materialIndex", "rendererComponentIndex"):
            value = probe.get(key, 0)
            if type(value) is not int or value < 0:
                raise ValueError("rendererProbes indices must be nonnegative integers.")
        names = probe["propertyNames"]
        if not isinstance(names, list) or not 1 <= len(names) <= 8 or any(not isinstance(name, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,127}", name) for name in names) or len(set(names)) != len(names):
            raise ValueError("rendererProbes require 1–8 distinct shader property names.")
        blend_names = probe.get("blendShapeNames", [])
        if "blendShapeNames" in probe and (not isinstance(blend_names, list) or not 1 <= len(blend_names) <= 8 or any(not isinstance(name, str) or not name.strip() or len(name) > 128 for name in blend_names) or len(set(blend_names)) != len(blend_names)):
            raise ValueError("rendererProbes blendShapeNames must contain at most 8 distinct non-empty names.")
    return probes


def _validate_probe_frames(payload, request, require_frames=False):
    probes = request.get("rendererProbes", [])
    if payload.get("rendererProbes", []) != probes:
        raise ValueError("Observation renderer probe request mismatch.")
    if not probes:
        return
    validate_renderer_probes(probes, request["avatarPath"])
    frames = payload.get("frames", [])
    if require_frames and not frames:
        raise ValueError("Observation renderer probe frames missing.")
    for frame in frames:
        rows = frame.get("rendererProbes")
        if not isinstance(rows, list) or len(rows) != len(probes):
            raise ValueError("Observation renderer probe coverage missing.")
        for probe, row in zip(probes, rows):
            if not isinstance(row, dict) or any(row.get(key) != probe.get(key, 0) for key in ("rendererPath", "materialIndex", "rendererComponentIndex")):
                raise ValueError("Observation renderer probe identity mismatch.")
            properties = row.get("properties")
            if not isinstance(properties, list) or any(not isinstance(value, dict) for value in properties) or [value.get("name") for value in properties] != probe["propertyNames"] or any(value.get("valueSource") not in ("material", "material_texture_scale_offset", "renderer_property_block", "material_property_block") or "value" not in value for value in properties):
                raise ValueError("Observation renderer probe property evidence missing.")
            blend_names = probe.get("blendShapeNames", [])
            blend_shapes = row.get("blendShapes")
            if blend_names:
                if not isinstance(blend_shapes, list) or len(blend_shapes) != len(blend_names):
                    raise ValueError("Observation renderer probe blendshape evidence missing or invalid.")
                for expected_name, value in zip(blend_names, blend_shapes):
                    if (not isinstance(value, dict) or value.get("name") != expected_name
                            or type(value.get("index")) is not int or value["index"] < 0
                            or type(value.get("weight")) not in (int, float)
                            or not math.isfinite(value["weight"])):
                        raise ValueError("Observation renderer probe blendshape evidence missing or invalid.")

def _steps_match(actual, expected):
    if not isinstance(actual, list) or len(actual) != len(expected):
        return False
    for left, right in zip(actual, expected):
        if not isinstance(left, dict) or left.get("timeSeconds") != right["timeSeconds"] or left.get("parameterName") != right["parameterName"]:
            return False
        try:
            if struct.pack("!f", left.get("value")) != struct.pack("!f", right["value"]):
                return False
        except (TypeError, struct.error):
            return False
    return True


def validate_start_result(payload, request):
    """Validate the Core start acknowledgement before exposing a pending job."""
    if not isinstance(payload, dict) or str(payload.get("status") or "").casefold() != "pending":
        return payload
    if payload.get("schema") != "vrcforge.runtime_observation.v1":
        raise ValueError("Runtime observation start schema mismatch.")
    if payload.get("jobId") != request["jobId"] or payload.get("avatarPath") != request["avatarPath"]:
        raise ValueError("Runtime observation start identity mismatch.")
    if payload.get("parameterName") != request["parameterName"]:
        raise ValueError("Runtime observation start parameter mismatch.")
    if not _steps_match(payload.get("parameterSteps", []), request.get("parameterSteps", [])):
        raise ValueError("Runtime observation start parameter steps mismatch.")
    _validate_probe_frames(payload, request)
    actual = payload.get("requestedValue")
    if type(actual) not in (int, float) or not math.isfinite(actual) or struct.pack("!f", actual) != struct.pack("!f", request["value"]):
        raise ValueError("Runtime observation start requested value mismatch.")
    for key in ("durationSeconds", "width", "height", "requestedFrameCount"):
        expected = request["durationSeconds"] if key == "durationSeconds" else request[key.replace("requestedFrameCount", "frameCount")]
        actual = payload.get(key)
        if actual != expected:
            raise ValueError("Runtime observation start " + key + " mismatch.")
    if payload.get("ok") is False or payload.get("success") is False or payload.get("isError") is True:
        raise ValueError("Runtime observation start acknowledgement reports failure.")
    if payload.get("mutationStarted") is not True or payload.get("verified") is not False or payload.get("commitState") != "pending":
        raise ValueError("Runtime observation start acknowledgement is not pending.")
    if payload.get("error") or payload.get("failureLayer") or payload.get("coreIdentity") in (None, ""):
        raise ValueError("Runtime observation start acknowledgement contains failure or missing Core identity.")
    if type(payload.get("avatarInstanceId")) is not int or payload["avatarInstanceId"] == 0 or type(payload.get("animatorInstanceId")) is not int or payload["animatorInstanceId"] == 0:
        raise ValueError("Runtime observation start Core object identity is missing.")
    return payload

def state_detail(arguments):
    value = arguments.get("stateDetail", "resource")
    if value not in ("inline", "resource"):
        raise ValueError("stateDetail must be inline or resource.")
    return value


def prepare_request(arguments, artifact_root):
    if any(key in arguments for key in ("jobId", "outputDirectory")):
        raise ValueError("Observation job identity and artifacts are App-owned.")
    detail = state_detail(arguments)
    values = {key: arguments[key] for key in PUBLIC_KEYS if key in arguments}
    values["parameterSteps"] = arguments.get("parameterSteps", [])
    for key in PUBLIC_KEYS:
        if key not in values:
            raise ValueError(key + " is required.")
    if not all(isinstance(values[key], str) and values[key].strip() for key in ("avatarPath", "parameterName")):
        raise ValueError("Exact Avatar and parameter names are required.")
    for key in ("value", "durationSeconds", "fieldOfView"):
        if type(values[key]) not in (int, float) or not math.isfinite(values[key]):
            raise ValueError(key + " must be finite.")
    if not .1 <= values["durationSeconds"] <= 10 or not 1 <= values["fieldOfView"] <= 179:
        raise ValueError("Duration/FOV outside bounded range.")
    steps = values.get("parameterSteps", [])
    if steps is None:
        steps = []
    if not isinstance(steps, list) or len(steps) > 64:
        raise ValueError("parameterSteps must contain at most 64 steps.")
    previous_time = -1.0
    normalized_steps = []
    for step in steps:
        if not isinstance(step, dict) or set(step) != {"timeSeconds", "parameterName", "value"}:
            raise ValueError("Each parameter step requires exactly timeSeconds, parameterName, and value.")
        time_seconds, name, step_value = step["timeSeconds"], step["parameterName"], step["value"]
        if type(time_seconds) not in (int, float) or not math.isfinite(time_seconds) or not 0 <= time_seconds <= values["durationSeconds"] or time_seconds <= previous_time:
            raise ValueError("parameterSteps must be finite, in range, and strictly increasing.")
        if not isinstance(name, str) or not name.strip():
            raise ValueError("Each parameter step requires an exact parameter name.")
        if type(step_value) not in (int, float) or not math.isfinite(step_value):
            raise ValueError("Each parameter step value must be finite.")
        normalized_steps.append({"timeSeconds": time_seconds, "parameterName": name, "value": step_value})
        previous_time = time_seconds
    values["parameterSteps"] = normalized_steps
    if "rendererProbes" in arguments:
        values["rendererProbes"] = validate_renderer_probes(arguments["rendererProbes"], values["avatarPath"])
    for key, low, high in (("frameCount", 2, 32), ("width", 128, 512), ("height", 128, 512)):
        if type(values[key]) is not int or not low <= values[key] <= high:
            raise ValueError(key + " outside bounded range.")
    if (values["frameCount"] + 1) * values["width"] * values["height"] * 3 > 24 * 1024 * 1024:
        raise ValueError("Raw pixels including baseline exceed 24 MiB.")
    for key in ("cameraPosition", "targetPosition", "upVector"):
        vector = values[key]
        if not isinstance(vector, dict) or set(vector) != {"x", "y", "z"} or any(type(v) not in (int, float) or not math.isfinite(v) for v in vector.values()):
            raise ValueError("Finite exact x/y/z vectors are required.")
    job = uuid.uuid4().hex
    values.update(stateDetail=detail, jobId=job, outputDirectory=str(Path(artifact_root).resolve() / "runtime-observations" / job))
    return values


def validate_result(payload, request):
    if payload.get("schema") != "vrcforge.runtime_observation.v1" or payload.get("jobId") != request["jobId"] or payload.get("avatarPath") != request["avatarPath"]:
        raise ValueError("Observation receipt identity mismatch.")
    requested_steps = request.get("parameterSteps", [])
    receipts = payload.get("stepReceipts", [])
    if not isinstance(receipts, list) or len(receipts) > len(requested_steps):
        raise ValueError("Observation parameter step receipt coverage is invalid.")
    last_actual, last_frame = -1.0, -1
    for index, receipt in enumerate(receipts):
        if not isinstance(receipt, dict) or receipt.get("parameterName") != requested_steps[index]["parameterName"] or receipt.get("timeSeconds") != requested_steps[index]["timeSeconds"]:
            raise ValueError("Observation parameter step identity differs from the request.")
        if receipt.get("status") == "not_applied":
            continue
        actual_time = receipt.get("actualElapsedSeconds")
        unity_frame = receipt.get("unityFrame")
        if type(actual_time) not in (int, float) or not math.isfinite(actual_time) or actual_time < requested_steps[index]["timeSeconds"] or actual_time < last_actual:
            raise ValueError("Observation parameter step timing is invalid.")
        if type(unity_frame) is not int or unity_frame < last_frame:
            raise ValueError("Observation parameter step Unity frame ordering is invalid.")
        last_actual, last_frame = actual_time, unity_frame
        if receipt.get("status") == "applied":
            actual = receipt.get("requestedValue")
            after = receipt.get("afterValue")
            before = receipt.get("beforeValue")
            if type(actual) not in (int, float) or type(before) not in (int, float) or type(after) not in (int, float) or struct.pack("!f", actual) != struct.pack("!f", requested_steps[index]["value"]) or struct.pack("!f", after) != struct.pack("!f", actual):
                raise ValueError("Observation parameter step readback differs from the request.")
        elif receipt.get("status") not in ("failed", "not_applied"):
            raise ValueError("Observation parameter step status is invalid.")
    if payload.get("status") != "completed" or payload.get("verified") is not True:
        return {**payload, "ok": False, "verified": False}
    _validate_probe_frames(payload, request, require_frames=True)
    if "parameterName" in request and payload.get("parameterName") != request["parameterName"]:
        raise ValueError("Observation parameter identity mismatch.")
    if "value" in request:
        actual = payload.get("requestedValue")
        if type(actual) not in (int, float) or not math.isfinite(actual) or struct.pack("!f", actual) != struct.pack("!f", request["value"]):
            raise ValueError("Observation requested parameter value mismatch.")
    if payload.get("requestedFrameCount") != request["frameCount"]:
        raise ValueError("Observation requested frame count mismatch.")
    if len(receipts) != len(requested_steps):
        raise ValueError("Observation parameter step receipt coverage differs from the request.")
    for expected, receipt in zip(requested_steps, receipts):
        if not isinstance(receipt, dict) or receipt.get("parameterName") != expected["parameterName"] or receipt.get("status") != "applied":
            raise ValueError("Observation parameter step identity or status differs from the request.")
        actual = receipt.get("requestedValue")
        if type(actual) not in (int, float) or struct.pack("!f", actual) != struct.pack("!f", expected["value"]):
            raise ValueError("Observation parameter step value differs from the request.")
    frames = payload.get("frames")
    if not isinstance(frames, list) or not 2 <= len(frames) <= request["frameCount"] + 1:
        raise ValueError("Observation frame evidence missing or unbounded.")
    total = 0
    last_time = -1.
    last_index = -2
    last_frame = -1
    for index, frame in enumerate(frames):
        if not isinstance(frame, dict) or not frame.get("states"):
            raise ValueError("Observation Animator state evidence missing.")
        sample = frame.get("sampleIndex")
        unity_frame = frame.get("unityFrame")
        if type(sample) is not int or (index == 0 and sample != -1) or sample <= last_index or sample >= request["frameCount"]:
            raise ValueError("Missing baseline or duplicate/unordered sample index.")
        if type(unity_frame) is not int or unity_frame < last_frame or (index > 1 and unity_frame == last_frame):
            raise ValueError("Observation frames are not distinct advancing Unity frames.")
        last_index, last_frame = sample, unity_frame
        elapsed = frame.get("actualElapsedSeconds")
        if type(elapsed) not in (int, float) or not math.isfinite(elapsed) or elapsed < last_time:
            raise ValueError("Invalid actual frame cadence.")
        last_time = elapsed
        expected = Path(request["outputDirectory"]) / f"{index:03d}.png"
        if Path(frame.get("imagePath", "")).resolve() != expected.resolve() or not re.fullmatch("[0-9a-f]{64}", str(frame.get("sha256", ""))):
            raise ValueError("Observation artifact identity mismatch.")
        data = expected.read_bytes(); total += len(data)
        if total > 32 * 1024 * 1024 or hashlib.sha256(data).hexdigest() != frame["sha256"]:
            raise ValueError("Observation artifact changed or exceeds bound.")
        with Image.open(expected) as image:
            if image.format != "PNG" or image.size != (request["width"], request["height"]):
                raise ValueError("Observation artifact dimensions differ.")
            image.verify()
    if last_index != request["frameCount"] - 1 or payload.get("sampledFrameCount") != len(frames) - 1 or payload.get("undersampled") is not (len(frames) - 1 < request["frameCount"]):
        raise ValueError("Observation coverage receipt differs from actual frames.")
    final_unity_frame = frames[-1]["unityFrame"]
    for receipt in receipts:
        if receipt.get("status") == "applied" and (receipt["actualElapsedSeconds"] > last_time or receipt["unityFrame"] > final_unity_frame):
            raise ValueError("Observation parameter step occurs after the final observed frame.")
    return {**payload, "ok": True, "readback": {"jobId": request["jobId"], "avatarPath": request["avatarPath"], "frameCount": len(frames), "artifactHashes": [frame["sha256"] for frame in frames]}}
