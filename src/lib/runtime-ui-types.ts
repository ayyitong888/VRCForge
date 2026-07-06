export type RuntimeScheduleItem = {
  id: string;
  status: "running" | "queued" | "cancelling";
  title: string;
  meta: string;
};

export type RuntimeFileReference = {
  path: string;
  source: string;
};

export type RuntimeReviewEvidence = {
  id: string;
  kind: "approval" | "checkpoint" | "diff" | "run";
  title: string;
  meta: string;
  status?: string;
  action?: () => void;
};
