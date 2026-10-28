import type { SubAgentTask } from "./api/sub-agents";

export type SubAgentThreadGroup = {
  id: string;
  task: SubAgentTask;
  turns: SubAgentTask[];
};

function creationTime(task: SubAgentTask): number | null {
  for (const value of [task.createdAt, task.startedAt]) {
    if (!value) continue;
    const parsed = Date.parse(value);
    if (Number.isFinite(parsed)) return parsed;
  }
  return null;
}

function taskTime(task: SubAgentTask): number | null {
  const created = creationTime(task);
  if (created !== null) return created;
  if (!task.updatedAt) return null;
  const updated = Date.parse(task.updatedAt);
  return Number.isFinite(updated) ? updated : null;
}

function orderTurns(turns: SubAgentTask[]): SubAgentTask[] {
  if (turns.every((task) => creationTime(task) !== null)) {
    const inputOrder = new Map(turns.map((task, index) => [task.id, index]));
    return [...turns].sort((left, right) => {
      const timeDifference = (creationTime(left) || 0) - (creationTime(right) || 0);
      return timeDifference || (inputOrder.get(left.id) || 0) - (inputOrder.get(right.id) || 0);
    });
  }

  const byId = new Map(turns.map((task) => [task.id, task]));
  const inputOrder = new Map(turns.map((task, index) => [task.id, index]));

  const resumeDepth = (task: SubAgentTask): number => {
    let depth = 0;
    let parentId = task.resumeOf;
    const visited = new Set([task.id]);
    while (parentId && byId.has(parentId) && !visited.has(parentId)) {
      visited.add(parentId);
      depth += 1;
      parentId = byId.get(parentId)?.resumeOf;
    }
    return depth;
  };

  const layers = new Map<number, SubAgentTask[]>();
  for (const task of turns) {
    const depth = resumeDepth(task);
    const layer = layers.get(depth);
    if (layer) layer.push(task);
    else layers.set(depth, [task]);
  }

  return [...layers.keys()].sort((left, right) => left - right).flatMap((depth) =>
    (layers.get(depth) || []).sort((left, right) => (inputOrder.get(left.id) || 0) - (inputOrder.get(right.id) || 0)),
  );
}

function latestTurn(turns: SubAgentTask[]): SubAgentTask {
  const resumedIds = new Set(turns.map((task) => task.resumeOf).filter((id): id is string => Boolean(id)));
  const tips = turns.filter((task) => !resumedIds.has(task.id));
  const candidates = tips.length ? tips : turns;
  return candidates.reduce((latest, task) => {
    const latestTime = taskTime(latest);
    const currentTime = taskTime(task);
    return currentTime !== null && (latestTime === null || currentTime > latestTime) ? task : latest;
  });
}

/** Group task records into logical child conversations, preserving resume order. */
export function groupSubAgentThreads(tasks: SubAgentTask[]): SubAgentThreadGroup[] {
  const grouped = new Map<string, SubAgentTask[]>();
  for (const task of tasks) {
    const id = task.childSessionId?.trim() || task.id;
    const turns = grouped.get(id);
    if (turns) turns.push(task);
    else grouped.set(id, [task]);
  }

  return [...grouped].map(([id, groupTurns]) => {
    const turns = orderTurns(groupTurns);
    return { id, task: latestTurn(turns), turns };
  });
}
