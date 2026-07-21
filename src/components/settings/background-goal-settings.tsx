import { Bell, BellOff, Loader2 } from "lucide-react";
import { useTranslation } from "react-i18next";
import { Badge } from "../ui/badge";
import { Button } from "../ui/button";

export function BackgroundGoalSettings({
  enabled,
  saving,
  runtimeConnected,
  onChange,
}: {
  enabled: boolean;
  saving: boolean;
  runtimeConnected: boolean;
  onChange: (enabled: boolean) => void;
}) {
  const { t } = useTranslation();
  return (
    <div>
      <div className="flex min-w-0 items-center gap-2">
        <h2 className="truncate text-base font-semibold">
          {enabled ? (
            <Bell className="mr-1.5 inline-block h-4 w-4 align-text-bottom" />
          ) : (
            <BellOff className="mr-1.5 inline-block h-4 w-4 align-text-bottom" />
          )}
          {t("settings.backgroundGoalNotifications")}
        </h2>
        <Badge tone={enabled ? "ok" : "muted"} className="shrink-0">
          {enabled ? t("settings.enabled") : t("connector.off")}
        </Badge>
      </div>
      <p className="mt-1 text-sm text-muted-foreground">
        {t("settings.backgroundGoalNotificationsDesc")}
      </p>
      <div className="mt-4">
        <Button
          type="button"
          variant={enabled ? "outline" : "primary"}
          disabled={saving || !runtimeConnected}
          onClick={() => onChange(!enabled)}
        >
          {saving ? <Loader2 className="mr-1 h-4 w-4 animate-spin" /> : null}
          {enabled ? t("settings.turnOff") : t("settings.turnOn")}
        </Button>
      </div>
    </div>
  );
}
