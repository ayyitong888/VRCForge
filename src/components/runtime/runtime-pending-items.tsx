import { ChevronDown, ChevronRight, ClipboardCheck, Hand, HelpCircle, ShieldCheck } from "lucide-react";
import { useId, useState } from "react";
import { useTranslation } from "react-i18next";

const pendingKinds = {
  question: { icon: HelpCircle, label: "questionCard.label" },
  userAction: { icon: Hand, label: "questionCard.userAction" },
  acceptance: { icon: ClipboardCheck, label: "questionCard.visualCheckpoint.label" },
  approval: { icon: ShieldCheck, label: "workspace.pendingConfirmation" },
} as const;

export type RuntimePendingItem = {
  id: string;
  kind: keyof typeof pendingKinds;
  title: string;
  chatTitle: string;
  onOpen: () => void;
};

export function RuntimePendingItems({ items }: { items: RuntimePendingItem[] }) {
  const { t } = useTranslation();
  const titleId = useId();
  const listId = useId();
  const [open, setOpen] = useState(true);
  const Chevron = open ? ChevronDown : ChevronRight;
  if (!items.length) return null;
  return (
    <section className="mb-3 overflow-hidden rounded-xl border border-border bg-card" aria-labelledby={titleId}>
      <h2 className={open ? "border-b border-border" : undefined}>
        <button type="button" aria-expanded={open} aria-controls={listId} onClick={() => setOpen((current) => !current)}
          className="flex w-full items-center gap-2 px-3 py-2 text-left text-xs focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring">
          <Chevron className="h-3.5 w-3.5 shrink-0 text-muted-foreground" aria-hidden="true" />
          <span id={titleId} className="flex-1 font-semibold">{t("workspace.runtimePendingItems")}</span>
          <span className="tabular-nums text-muted-foreground">{items.length}</span>
        </button>
      </h2>
      <ul id={listId} hidden={!open} className="app-scrollbar m-0 max-h-36 list-none overflow-y-auto overscroll-contain p-0" data-runtime-pending-list>
        {items.map((item) => {
          const { icon: Icon, label } = pendingKinds[item.kind];
          return (
            <li key={item.id}>
              <button type="button" onClick={item.onOpen}
                data-vrcforge-status={item.kind !== "approval" ? "runtime-question" : undefined}
                data-runtime-pending-kind={item.kind}
                className="flex h-12 w-full items-center gap-2 px-3 text-left hover:bg-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring"
                data-app-tooltip={item.title}>
                <Icon className="h-4 w-4 shrink-0 text-primary" aria-hidden="true" />
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-sm font-medium">{item.title}</span>
                  <span className="block truncate text-xs text-muted-foreground">{t(label)} · {item.chatTitle}</span>
                </span>
              </button>
            </li>
          );
        })}
      </ul>
    </section>
  );
}
