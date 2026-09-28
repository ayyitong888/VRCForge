import type { SkillPackageEntry } from "./api";

/** Display only: never pass the shortened path to import or verification. */
export function displayVskPath(path: string): string {
  if (path.startsWith("\\\\?\\UNC\\")) return "\\\\" + path.slice(8);
  return /^\\\\\?\\[A-Za-z]:\\/.test(path) ? path.slice(4) : path;
}

export function vskPreviewIdentity(entry: SkillPackageEntry) {
  const manifest = entry.manifest || {};
  const text = (...values: unknown[]) => values.find((value): value is string => typeof value === "string" && value.trim().length > 0) || "—";
  return {
    name: text(manifest.title, manifest.name, manifest.id, entry.title, entry.name, entry.id),
    version: text(manifest.version, entry.version),
  };
}
