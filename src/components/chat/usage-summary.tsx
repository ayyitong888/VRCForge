import { useTranslation } from "react-i18next";
import type { AgentContextUsage } from "../../lib/api";
import { formatCount } from "../../lib/utils";

export function UsageSummary({ usage, compactionUsage }: { usage?: AgentContextUsage; compactionUsage?: AgentContextUsage }) {
  const { t } = useTranslation();
  const taskTotal = usage?.taskTotalAvailable === true || usage?.scope === "task_total_context_usage";
  const tokenValue = (value: unknown): string =>
    typeof value === "number" && Number.isFinite(value) && value >= 0 ? formatCount(value) : t("chat.usageSummaryUnknown");
  const input = taskTotal ? usage?.cumulativeInputTokens ?? usage?.inputTokens : usage?.inputTokens;
  const output = taskTotal ? usage?.cumulativeOutputTokens ?? usage?.outputTokens : usage?.outputTokens;
  const cacheRead = typeof usage?.cacheReadTokens === "number" && Number.isFinite(usage.cacheReadTokens) && usage.cacheReadTokens >= 0
    ? usage.cacheReadTokens : undefined;
  const cacheInput = typeof usage?.inputTokens === "number" && Number.isFinite(usage.inputTokens) && usage.inputTokens > 0
    ? usage.inputTokens : undefined;
  const cacheComplete = usage?.cacheUsageComplete === true && cacheInput !== undefined && cacheRead !== undefined && cacheRead <= cacheInput;
  const cacheStatus = cacheComplete
    ? `${tokenValue(cacheRead)} (${Math.round((cacheRead / cacheInput) * 100)}%)`
    : usage?.cacheUsageComplete === false || cacheRead !== undefined
      ? t("chat.usageSummaryCacheIncomplete")
      : t("chat.usageSummaryUnknown");
  const compactionPartial = compactionUsage?.exact !== true || compactionUsage?.partial === true;
  const compactionValue = (value: unknown): string => tokenValue(value);
  const compactionCacheValue = (value: unknown, complete: boolean | undefined): string => {
    const rendered = tokenValue(value);
    return complete === true ? rendered : `${rendered} (${t("chat.usageSummaryPartial", "Partial")})`;
  };
  return (
    <details className="mt-1 rounded-lg border border-border/60 bg-muted/20 px-2.5 py-1.5 text-[11px] text-muted-foreground" data-usage-summary>
      <summary className="cursor-pointer select-none font-medium text-foreground">
        {t("chat.usageSummaryTitle")} · {t(taskTotal ? "chat.usageSummaryTask" : "chat.usageSummaryRecorded")}
      </summary>
      <div className="mt-2 grid grid-cols-2 gap-x-4 gap-y-1" data-usage-summary-values>
        <span>{t("chat.usageSummaryInput")}</span><span>{tokenValue(input)}</span>
        <span>{t("chat.usageSummaryOutput")}</span><span>{tokenValue(output)}</span>
        <span>{t("chat.usageSummaryLastRequest")}</span><span>{tokenValue(usage?.lastInputTokens)}</span>
        <span>{t("chat.usageSummaryCache")}</span><span>{cacheStatus} <span className="text-[10px]">({t("chat.usageSummaryIncluded")})</span></span>
        <span>{t("chat.usageSummaryCost")}</span><span>{t("chat.usageSummaryPricingNotConfigured")}</span>
      </div>
      {compactionUsage ? (
        <div className="mt-2 border-t border-border/50 pt-2" data-compaction-usage-summary>
          <div className="mb-1 font-medium text-foreground">
            {t("chat.usageSummaryCompaction", "Compaction usage total")}
            {compactionPartial ? ` · ${t("chat.usageSummaryPartial", "Partial")}` : ""}
          </div>
          <div className="grid grid-cols-2 gap-x-4 gap-y-1">
            <span>{t("chat.usageSummaryInput")}</span><span>{compactionValue(compactionUsage.inputTokens)}</span>
            <span>{t("chat.usageSummaryOutput")}</span><span>{compactionValue(compactionUsage.outputTokens)}</span>
            <span>{t("chat.usageSummaryTotal", "Total")}</span><span>{compactionValue(compactionUsage.totalTokens)}</span>
            <span>{t("chat.usageSummaryCache")}</span><span>{compactionCacheValue(compactionUsage.cacheReadTokens, compactionUsage.cacheCoverage?.read?.complete)}</span>
            <span>{t("chat.usageSummaryCacheWrite", "Cache write")}</span><span>{compactionCacheValue(compactionUsage.cacheWriteTokens, compactionUsage.cacheCoverage?.write?.complete)}</span>
            <span>{t("chat.usageSummaryRequests", "Recorded requests")}</span><span>{compactionValue(compactionUsage.requestCount)}</span>
          </div>
        </div>
      ) : null}
    </details>
  );
}
