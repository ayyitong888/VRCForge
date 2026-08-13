export type QueueSettlement = { acceptedSteer: boolean; stopRequested: boolean; cancelled?: boolean };
export type QueueDecision = "steered" | "enqueue" | "start" | "drop";

export type QueueReservation = {
  decision: Promise<QueueDecision>;
};

/** Dequeue only work that is explicitly runnable. */
export function takeNextRunnableQueuedTurn<T extends { queueStatus?: string }>(queue: T[]): T | undefined {
  const head = queue[0];
  if (!head || (head.queueStatus !== undefined && head.queueStatus !== "queued")) return undefined;
  return queue.shift();
}

type PendingReservation = {
  settlement?: QueueSettlement;
  resolve: (decision: QueueDecision) => void;
};

/** Single-owner FIFO reservation state; no React or network dependencies. */
export class RuntimeQueueArbitrator {
  private readonly pending = new Map<string, PendingReservation>();
  private readonly order: string[] = [];
  private startReserved = false;
  constructor(_max?: number) { /* legacy argument retained for callers; queue is unbounded */ }

  reserve(id: string, _queuedCount: number): QueueReservation | null {
    if (this.pending.has(id)) return null;
    let resolveDecision!: (decision: QueueDecision) => void;
    const decision = new Promise<QueueDecision>((resolve) => { resolveDecision = resolve; });
    this.pending.set(id, { resolve: resolveDecision });
    this.order.push(id);
    return { decision };
  }

  settle(id: string, result: QueueSettlement, runActive: boolean): void {
    const reservation = this.pending.get(id);
    if (!reservation) return;
    reservation.settlement = result.stopRequested ? { ...result, acceptedSteer: false } : result;
    if (runActive) this.startReserved = false;
    let active = runActive || this.startReserved;
    while (this.order.length > 0) {
      const headId = this.order[0];
      const head = this.pending.get(headId);
      if (!head?.settlement) break;
      this.order.shift();
      this.pending.delete(headId);
      if (head.settlement.cancelled) {
        head.resolve("drop");
      } else if (head.settlement.acceptedSteer) {
        head.resolve("steered");
      } else if (!active) {
        active = true;
        this.startReserved = true;
        head.resolve("start");
      } else {
        head.resolve("enqueue");
      }
    }
  }

  stop(): void {
    for (const reservation of this.pending.values()) reservation.resolve("drop");
    this.pending.clear();
    this.order.length = 0;
    this.startReserved = false;
  }

  runnerStarted(): void {
    this.startReserved = false;
  }

  get pendingCount(): number { return this.pending.size; }
  get orderIds(): string[] { return [...this.order]; }
}
