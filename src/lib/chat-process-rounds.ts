import type { ConversationItem } from "./chat-types";

type Segment = Extract<ConversationItem, { type: "agent" | "streaming" }>;
export type ChatProcessRound = {
  first: Segment;
  last: Segment;
  failures: number;
  elapsedSeconds?: number;
  liveStartedAt?: number;
};

// Older archived dispatches stored their parent CID in the continuation key.
// Question answers can also start fresh work, so they require explicit lineage.
const archivedMarkers: Record<string, string> = {
  shell_process_finished: ":shell:", sub_agent_finished: ":subagent:",
  approval_finished: ":approval:", approval_revision_requested: ":revision:",
};

function runOf(item: Segment) {
  return item.type === "agent" ? item.response : item;
}

export function conversationItemStart(item: ConversationItem): number {
  if (item.type === "agent") {
    const firstEventAt = item.timeline?.[0]?.timestamp || item.response.timeline?.[0]?.timestamp;
    return Date.parse(item.response.startedAt || firstEventAt || item.createdAt || "");
  }
  if (item.type === "streaming") return Date.parse(item.createdAt || item.timeline?.[0]?.timestamp || "");
  if (item.type === "question_answer") return Date.parse(item.updatedAt || "");
  if (item.type === "approval_revision") return Date.parse(item.requestedAt || item.createdAt || "");
  if (item.type === "timeline_event") return Date.parse(item.event.timestamp || item.createdAt || "");
  if (item.type === "subagent") return Date.parse(item.task.createdAt || item.task.startedAt || "");
  if ((item.type === "result" || item.type === "error") && !item.createdAt) {
    // The desktop writer stamps these receipts with Date.now() in the last ID component.
    const stamp = /-(\d{13})$/.exec(item.id);
    if (stamp) return Number(stamp[1]);
  }
  return Date.parse(("createdAt" in item && item.createdAt) || "");
}

/** Relate dispatches without losing their tool targets or moving intervening user messages. */
export function projectProcessRounds(items: ConversationItem[], sessionId = "") {
  const orderedItems = [...items];
  const dated = items.map((item, index) => ({ item, index, time: conversationItemStart(item) }))
    .filter(entry => Number.isFinite(entry.time));
  // A receipt can be written after the next run starts. Keep undated history in its saved slots.
  const chronological = [...dated].sort((a, b) => a.time - b.time);
  dated.forEach(({ index }, i) => {
    orderedItems[index] = chronological[i].item;
  });
  const segments = orderedItems.filter((item): item is Segment => item.type === "agent" || item.type === "streaming");
  const clients = new Map<string, Segment | null>();
  const turns = new Map<string, Segment | null>();
  const sessionOf = (item: Segment) => runOf(item).sessionId || (item.type === "agent" && item.response.session_id) || sessionId;
  const key = (item: Segment, id: string) => JSON.stringify([sessionOf(item), id]);
  const index = (map: Map<string, Segment | null>, item: Segment, id?: string) => {
    if (id) {
      const scoped = key(item, id);
      map.set(scoped, map.has(scoped) ? null : item);
    }
  };
  for (const item of segments) {
    const run = runOf(item);
    index(clients, item, run.clientTurnId);
    index(turns, item, run.turnId || (item.type === "agent" ? item.response.turn_id : ""));
  }

  const parents = new Map<Segment, Segment>();
  for (const item of segments) {
    const run = runOf(item);
    let parent: Segment | null | undefined;
    if (run.parentTurnId) {
      parent = turns.get(key(item, run.parentTurnId));
      if (parent && run.parentClientTurnId && runOf(parent).clientTurnId !== run.parentClientTurnId) continue;
    } else if (run.parentClientTurnId) {
      parent = clients.get(key(item, run.parentClientTurnId));
    } else if (run.continuationSource && run.clientTurnId) {
      const marker = archivedMarkers[run.continuationSource];
      const offset = marker ? run.clientTurnId.lastIndexOf(marker) : -1;
      if (offset > 0) parent = clients.get(key(item, run.clientTurnId.slice(0, offset)));
    }
    if (parent && parent !== item) parents.set(item, parent);
  }

  const membersByRoot = new Map<Segment, Set<Segment>>();
  const roots = new Map<Segment, Segment>();
  for (const child of parents.keys()) {
    if (roots.has(child)) continue;
    const chain = new Set<Segment>([child]);
    let ancestor = parents.get(child);
    while (ancestor && !chain.has(ancestor)) {
      chain.add(ancestor);
      const parent = parents.get(ancestor);
      if (!parent || roots.has(ancestor)) {
        const root = roots.get(ancestor) || ancestor;
        const members = membersByRoot.get(root) || new Set<Segment>();
        for (const segment of [...chain].reverse()) {
          members.add(segment);
          roots.set(segment, root);
        }
        membersByRoot.set(root, members);
        break;
      }
      ancestor = parent;
    }
  }

  const rounds = new Map<ConversationItem, ChatProcessRound>();
  const positions = new Map(orderedItems.map((item, i) => [item, i]));
  for (const [first, members] of membersByRoot) {
    const ordered = [...members];
    const last = ordered[ordered.length - 1];
    const round: ChatProcessRound = { first, last, failures: 0 };
    // Recovery can fetch a child before its parent. Only reorder that round's own slots.
    const slots = ordered.map(segment => positions.get(segment)!).sort((a, b) => a - b);
    ordered.forEach((segment, i) => {
      orderedItems[slots[i]] = segment;
      rounds.set(segment, round);
      const events = segment.timeline || (segment.type === "agent" ? segment.response.timeline : undefined);
      round.failures += events?.filter(event => event.kind === "tool_result"
        && ["error", "failed"].includes(event.payload?.status?.toLowerCase() || "")).length || 0;
      const start = Date.parse((segment.type === "agent" && segment.response.startedAt) || segment.createdAt || events?.[0]?.timestamp || "");
      if (segment.type === "agent") {
        const end = Date.parse(segment.completedAt || segment.response.completedAt || events?.[events.length - 1]?.timestamp || "");
        const duration = segment.elapsedSeconds ?? (end - start) / 1000;
        if (Number.isFinite(duration) && duration >= 0) round.elapsedSeconds = (round.elapsedSeconds || 0) + duration;
      } else if (segment === last && Number.isFinite(start)) round.liveStartedAt = start;
    });
  }
  return { items: orderedItems, rounds };
}
