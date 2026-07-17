export type OnboardingChecklistCompletionState = "done" | "pending";
export type OnboardingChecklistPositionState = "current" | "other";

export type OnboardingChecklistItemState = {
  completion: OnboardingChecklistCompletionState;
  position: OnboardingChecklistPositionState;
};

export function onboardingChecklistItemState(done: boolean, current: boolean): OnboardingChecklistItemState {
  return {
    completion: done ? "done" : "pending",
    position: current ? "current" : "other",
  };
}

export const onboardingChecklistVisualClasses = {
  item: {
    current: "border-primary/40 bg-primary/5 text-foreground",
    other: "border-border bg-muted/20 text-muted-foreground",
  },
  icon: {
    done: "text-primary",
    pending: "text-muted-foreground",
  },
} as const;
