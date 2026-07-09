export type RuntimeScheduleItem = {
  id: string;
  status: "running" | "queued" | "cancelling";
  title: string;
  meta: string;
};

export type RuntimePlanChoice = {
  id: string;
  label: string;
  description?: string;
  value?: string;
};

export type RuntimePlanItem = {
  id: string;
  title: string;
  meta?: string;
  status?: "completed" | "running" | "queued" | "question" | "blocked" | string;
  choices?: RuntimePlanChoice[];
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
