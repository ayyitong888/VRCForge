import { HelpCircle, ShieldCheck } from "lucide-react";
import { useTranslation } from "react-i18next";

const pendingKinds = {
  question: { icon: HelpCircle, label: "questionCard.label" },
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
  if (!items.length) return null;
  return (
    <section className="mb-3 overflow-hidden rounded-xl border border-border bg-card" aria-labelledby="runtime-pending-title">
      <header className="flex items-center justify-between border-b border-border px-3 py-2 text-xs">
        <h2 id="runtime-pending-title" className="font-semibold">{t("workspace.runtimePendingItems")}</h2>
        <span className="tabular-nums text-muted-foreground">{items.length}</span>
      </header>
      <ul className="app-scrollbar m-0 max-h-36 list-none overflow-y-auto overscroll-contain p-0" data-runtime-pending-list>
        {items.map((item) => {
          const { icon: Icon, label } = pendingKinds[item.kind];
          return (
            <li key={item.id}>
              <button type="button" onClick={item.onOpen}
                data-vrcforge-status={item.kind === "question" ? "runtime-question" : undefined}
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
