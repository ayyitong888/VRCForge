import type { ProjectSnapshot } from "./api";

export function projectDiscoveryState(snapshot?: ProjectSnapshot) {
  const scan = snapshot?.scan;
  const sources = snapshot?.catalogueScan?.sources || {};
  const sourceErrors = Object.entries(sources)
    .filter(([, value]) => value.status === "error" || Number(value.errorCount || 0) > 0)
    .map(([name]) => name);
  return {
    scanning: scan?.refreshing === true || scan?.status === "refreshing",
    notChecked: !scan?.status || scan.status === "pending",
    error: scan?.error || (scan?.status === "error" ? "Project discovery failed" : ""),
    sourceErrors,
  };
}
