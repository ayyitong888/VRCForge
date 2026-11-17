import type { SubAgentTask, SubAgentTaskList } from "./api";

const VRCHAT_AVATAR_AGENT_NAMES = [
  "Manuka",
  "Shinano",
  "Kikyo",
  "Moe",
  "Selestia",
  "Milltina",
  "Kipfel",
  "Rurune",
  "Mamehinata",
  "Usasaki",
  "Airi",
  "Maya",
  "Rindo",
  "Karin",
  "Lasyusha",
  "Lime",
  "Chiffon",
  "Chocolat",
  "Mizuki",
  "Sio",
  "Milfy",
  "Mao",
  "Lumina",
  "Leefa",
  "Lunalitt",
  "Rusk",
  "Clonka",
  "Uzuruha",
  "Mitsumame",
  "Ulthara",
  "IsanaiNuku",
  "Yilnel",
  "NoraFirika",
  "IODragonewt",
  "Ortwa",
  "Ricorine",
  "Siska",
  "NoraMiaree",
  "Clara",
  "Korone",
  "Azuki",
  "Miminoko",
  "Nemesis",
  "Elusion",
];

export function pickSubAgentName(): string {
  const index = Math.floor(Math.random() * VRCHAT_AVATAR_AGENT_NAMES.length);
  return VRCHAT_AVATAR_AGENT_NAMES[index] || "Manuka";
}

export function updateSubAgentList(current: SubAgentTaskList | null, task: SubAgentTask): SubAgentTaskList {
  const existing = current?.tasks || [];
  task = latestSubAgentTask(existing.find((item) => item.id === task.id), task);
  const tasks = [task, ...existing.filter((item) => item.id !== task.id)];
  return {
    ok: true,
    schema: current?.schema || "vrcforge.sub_agent_tasks.v2",
    tasks,
    count: tasks.length,
    roles: current?.roles,
    maxConcurrent: current?.maxConcurrent,
    runningCount: tasks.filter((item) => ["queued", "running", "cancelling"].includes(item.status)).length,
  };
}

export function latestSubAgentTask(current: SubAgentTask | null | undefined, incoming: SubAgentTask): SubAgentTask {
  if (!current || current.id !== incoming.id) return incoming;
  if ((current.revision ?? 0) > (incoming.revision ?? 0)) return current;
  return { ...current, ...incoming };
}

export function reconcileSubAgentList(
  current: SubAgentTaskList | null,
  incoming: SubAgentTaskList,
): SubAgentTaskList {
  const existing = new Map(current?.tasks.map((task) => [task.id, task]));
  return { ...incoming, tasks: incoming.tasks.map((task) => latestSubAgentTask(existing.get(task.id), task)) };
}

export function combineSubAgentLists(global: SubAgentTaskList, scoped: SubAgentTaskList): SubAgentTaskList {
  const tasks = new Map(global.tasks.map((task) => [task.id, task]));
  for (const task of scoped.tasks) tasks.set(task.id, latestSubAgentTask(tasks.get(task.id), task));
  return { ...global, tasks: [...tasks.values()], count: tasks.size };
}

export function reconcileSelectedSubAgent(
  selected: SubAgentTask | null,
  tasks: SubAgentTask[],
): SubAgentTask | null {
  if (!selected) {
    return null;
  }
  const updated = tasks.find((task) => task.id === selected.id);
  return updated ? latestSubAgentTask(selected, updated) : selected;
}
