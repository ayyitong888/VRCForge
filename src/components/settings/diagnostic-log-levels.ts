import type { DiagnosticLogLevel } from "../../lib/api";

export const STANDARD_DIAGNOSTIC_LOG_LEVELS: readonly DiagnosticLogLevel[] = ["error", "warn", "info", "debug"];
export const DEVELOPER_DIAGNOSTIC_LOG_LEVELS: readonly DiagnosticLogLevel[] = [
  ...STANDARD_DIAGNOSTIC_LOG_LEVELS,
  "trace",
];

export function availableDiagnosticLogLevels(developerOptionsEnabled: boolean): readonly DiagnosticLogLevel[] {
  return developerOptionsEnabled ? DEVELOPER_DIAGNOSTIC_LOG_LEVELS : STANDARD_DIAGNOSTIC_LOG_LEVELS;
}

export function normalizeDiagnosticLogLevel(
  level: DiagnosticLogLevel | null | undefined,
  developerOptionsEnabled: boolean,
): DiagnosticLogLevel {
  const normalized = level && DEVELOPER_DIAGNOSTIC_LOG_LEVELS.includes(level) ? level : "info";
  return normalized === "trace" && !developerOptionsEnabled ? "debug" : normalized;
}
