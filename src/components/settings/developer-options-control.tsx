import { Loader2 } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import {
  beginDeveloperOptionsChallenge,
  cancelDeveloperOptionsChallenge,
  type DeveloperOptionsChallenge,
} from "../../lib/api";
import { Badge } from "../ui/badge";
import { Button } from "../ui/button";
import { DeveloperOptionsWarningDialog } from "./developer-options-warning-dialog";

export function DeveloperOptionsControl({
  endpoint,
  enabled,
  everEnabled,
  saving,
  onChange,
}: {
  endpoint: string;
  enabled: boolean;
  everEnabled: boolean;
  saving: boolean;
  onChange: (enabled: boolean, developerChallengeId?: string) => Promise<void> | void;
}) {
  const { t } = useTranslation();
  const [challenge, setChallenge] = useState<DeveloperOptionsChallenge | null>(null);
  const [startingChallenge, setStartingChallenge] = useState(false);
  const [challengeError, setChallengeError] = useState("");
  const challengeRef = useRef<DeveloperOptionsChallenge | null>(null);
  const requestGenerationRef = useRef(0);
  const endpointRef = useRef(endpoint);

  useEffect(() => {
    challengeRef.current = challenge;
  }, [challenge]);

  useEffect(() => {
    const previousEndpoint = endpointRef.current;
    endpointRef.current = endpoint;
    if (previousEndpoint === endpoint) {
      return;
    }
    requestGenerationRef.current += 1;
    const active = challengeRef.current;
    challengeRef.current = null;
    setChallenge(null);
    setStartingChallenge(false);
    if (active?.challengeId) {
      void cancelDeveloperOptionsChallenge(previousEndpoint, active.challengeId).catch(() => undefined);
    }
  }, [endpoint]);

  useEffect(
    () => () => {
      requestGenerationRef.current += 1;
      const active = challengeRef.current;
      challengeRef.current = null;
      if (active?.challengeId) {
        void cancelDeveloperOptionsChallenge(endpointRef.current, active.challengeId).catch(() => undefined);
      }
    },
    [],
  );

  useEffect(() => {
    if (!enabled) {
      return;
    }
    requestGenerationRef.current += 1;
    const active = challengeRef.current;
    challengeRef.current = null;
    setChallenge(null);
    setStartingChallenge(false);
    if (active?.challengeId) {
      void cancelDeveloperOptionsChallenge(endpointRef.current, active.challengeId).catch(() => undefined);
    }
  }, [enabled]);

  const closeChallenge = (reportFailure: boolean) => {
    requestGenerationRef.current += 1;
    const active = challengeRef.current;
    challengeRef.current = null;
    setChallenge(null);
    if (active?.challengeId) {
      void cancelDeveloperOptionsChallenge(endpoint, active.challengeId).catch(() => {
        if (reportFailure) {
          setChallengeError(t("settings.developerChallengeCancelFailed"));
        }
      });
    }
  };

  const beginChallenge = async () => {
    if (saving || startingChallenge || challengeRef.current) {
      return;
    }
    const generation = ++requestGenerationRef.current;
    setStartingChallenge(true);
    setChallengeError("");
    try {
      const next = await beginDeveloperOptionsChallenge(endpoint);
      if (generation !== requestGenerationRef.current) {
        if (next.challengeId) {
          void cancelDeveloperOptionsChallenge(endpoint, next.challengeId).catch(() => undefined);
        }
        return;
      }
      if (!next.challengeId || !Number.isFinite(next.waitMs)) {
        if (next.challengeId) {
          void cancelDeveloperOptionsChallenge(endpoint, next.challengeId).catch(() => undefined);
        }
        setChallengeError(t("settings.developerChallengeStartFailed"));
        return;
      }
      challengeRef.current = next;
      setChallenge(next);
    } catch {
      if (generation === requestGenerationRef.current) {
        setChallengeError(t("settings.developerChallengeStartFailed"));
      }
    } finally {
      if (generation === requestGenerationRef.current) {
        setStartingChallenge(false);
      }
    }
  };

  const confirmChallenge = () => {
    const active = challengeRef.current;
    if (!active?.challengeId) {
      return;
    }
    requestGenerationRef.current += 1;
    challengeRef.current = null;
    setChallenge(null);
    void onChange(true, active.challengeId);
  };

  const toggle = () => {
    setChallengeError("");
    if (enabled) {
      closeChallenge(false);
      void onChange(false);
      return;
    }
    void beginChallenge();
  };

  return (
    <>
      <div className="rounded-xl border border-border bg-card p-4">
        <div className="flex min-w-0 flex-wrap items-center gap-3">
          <div className="min-w-0 flex-1">
            <div className="truncate text-sm font-medium">{t("settings.developerOptions")}</div>
            <div className="mt-1 text-xs text-muted-foreground">{t("settings.developerOptionsDesc")}</div>
          </div>
          <Badge tone={enabled ? "warn" : "muted"} className="shrink-0">
            {enabled ? t("settings.enabled") : t("connector.off")}
          </Badge>
          {!enabled && everEnabled ? (
            <Badge tone="muted" className="shrink-0">
              {t("settings.everEnabled")}
            </Badge>
          ) : null}
          <Button
            type="button"
            variant={enabled ? "outline" : "primary"}
            disabled={saving || startingChallenge}
            data-vrcforge-developer-toggle
            onClick={toggle}
          >
            {saving || startingChallenge ? <Loader2 className="h-4 w-4 animate-spin" /> : null}
            {enabled ? t("settings.turnOffDeveloperOptions") : t("settings.turnOnDeveloperOptions")}
          </Button>
        </div>
        {challengeError ? <p className="mt-3 text-xs text-destructive">{challengeError}</p> : null}
      </div>

      {challenge ? (
        <DeveloperOptionsWarningDialog
          key={challenge.challengeId}
          waitMs={challenge.waitMs}
          onCancel={() => closeChallenge(true)}
          onConfirm={confirmChallenge}
        />
      ) : null}
    </>
  );
}
