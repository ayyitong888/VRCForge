export function projectKey(project: { path?: string; name?: string }): string {
  return project.path || project.name || "";
}

export function shortPath(path: string): string {
  const normalized = path.replace(/\\/g, "/");
  return normalized.split("/").filter(Boolean).slice(-1)[0] || path;
}
