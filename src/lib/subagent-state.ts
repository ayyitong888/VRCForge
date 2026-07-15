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

export function reconcileSelectedSubAgent(
  selected: SubAgentTask | null,
  tasks: SubAgentTask[],
): SubAgentTask | null {
  if (!selected) {
    return null;
  }
  const updated = tasks.find((task) => task.id === selected.id);
  return updated ? { ...selected, ...updated } : null;
}
