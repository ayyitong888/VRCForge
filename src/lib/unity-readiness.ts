import { asRecord } from "./runtime-parsing";

type HealthComponentLike = {
  status?: string;
  detail?: unknown;
} | null | undefined;

function getBoolean(record: Record<string, unknown> | null, key: string): boolean {
  return record?.[key] === true;
}

export function isVrcForgeUnityToolsReady(
  runtimeConnected: boolean,
  bridge: HealthComponentLike,
  instance: HealthComponentLike,
  tools: HealthComponentLike,
): boolean {
  if (!runtimeConnected || bridge?.status !== "ok" || instance?.status !== "ok" || tools?.status !== "ok") {
    return false;
  }
  const bridgeDetail = asRecord(bridge.detail) ?? {};
  const instanceDetail = asRecord(instance.detail) ?? {};
  const toolsDetail = asRecord(tools.detail) ?? {};
  const missingFromBridge = bridgeDetail.missingRequiredVrcForgeTools;
  const missingFromTools = toolsDetail.missingRequiredVrcForgeTools;
  if ((Array.isArray(missingFromBridge) && missingFromBridge.length > 0)
    || (Array.isArray(missingFromTools) && missingFromTools.length > 0)) {
    return false;
  }

  const criticalKeys = [
    "mcpServerReachable",
    "executionReady",
    "unityInstanceRegistered",
    "selectedInstanceMatched",
    "coreVersionMatched",
    "vrcForgeToolsRegistered",
  ];
  const hasExplicitFalse = [bridgeDetail, instanceDetail, toolsDetail].some((detail) =>
    criticalKeys.some((key) => key in detail && detail[key] === false),
  );
  if (hasExplicitFalse) {
    return false;
  }

  // Full health responses expose all of these fields. Bootstrap responses can
  // defer tool inspection, so accept the smaller version/instance contract
  // when the backend has not projected the optional fields yet.
  const fullReadiness = [
    "mcpServerReachable",
    "executionReady",
    "unityInstanceRegistered",
    "selectedInstanceMatched",
    "coreVersionMatched",
    "vrcForgeToolsRegistered",
  ].every((key) => key in bridgeDetail && getBoolean(bridgeDetail, key));
  const deferredInspection = toolsDetail.inspectionSkipped === true
    || toolsDetail.inspectionMode === "core_version_only";
  const deferredReadiness = deferredInspection
    && getBoolean(bridgeDetail, "coreVersionMatched")
    && (getBoolean(bridgeDetail, "selectedInstanceMatched") || getBoolean(instanceDetail, "selectedInstanceMatched"))
    && (getBoolean(toolsDetail, "coreVersionMatched") || !Object.keys(toolsDetail).length);
  return fullReadiness || deferredReadiness;
}
