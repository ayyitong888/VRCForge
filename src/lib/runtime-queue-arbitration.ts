export type QueueSettlement = { acceptedSteer: boolean; stopRequested: boolean };
export type QueueDecision = "steered" | "enqueue" | "start" | "drop" | "queue_full";

export type QueueReservation = {
  decision: Promise<QueueDecision>;
};

type PendingReservation = {
  settlement?: QueueSettlement;
  resolve: (decision: QueueDecision) => void;
};

/** Single-owner FIFO reservation state; no React or network dependencies. */
export class RuntimeQueueArbitrator {
  private readonly pending = new Map<string, PendingReservation>();
  private readonly order: string[] = [];
  private readonly max: number;
  private startReserved = false;
  constructor(max = 8) { this.max = max; }

  reserve(id: string, queuedCount: number): QueueReservation | null {
    if (this.pending.has(id) || this.pending.size + queuedCount >= this.max) return null;
    let resolveDecision!: (decision: QueueDecision) => void;
    const decision = new Promise<QueueDecision>((resolve) => { resolveDecision = resolve; });
    this.pending.set(id, { resolve: resolveDecision });
    this.order.push(id);
    return { decision };
  }

  settle(id: string, result: QueueSettlement, runActive: boolean): void {
    const reservation = this.pending.get(id);
    if (!reservation) return;
    if (result.stopRequested) {
      this.stop();
      return;
    }
    reservation.settlement = result;
    if (runActive) this.startReserved = false;
    let active = runActive || this.startReserved;
    while (this.order.length > 0) {
      const headId = this.order[0];
      const head = this.pending.get(headId);
      if (!head?.settlement) break;
      this.order.shift();
      this.pending.delete(headId);
      if (head.settlement.acceptedSteer) {
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
