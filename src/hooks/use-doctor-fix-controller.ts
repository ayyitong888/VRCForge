import { useCallback, useState } from "react";
import { useTranslation } from "react-i18next";
import { fixDoctorCheck, type DoctorFixMode, type DoctorFixResult } from "../lib/api";

type UseDoctorFixControllerParams = {
  endpoint: string;
  projectPath: string;
  onRefresh: (result: DoctorFixResult) => Promise<void>;
  onMessage: (message: string, tone: "ok" | "warn" | "danger") => void;
  onError: (message: string) => void;
};

export function useDoctorFixController({
  endpoint,
  projectPath,
  onRefresh,
  onMessage,
  onError,
}: UseDoctorFixControllerParams) {
  const { t } = useTranslation();
  const [fixingCheckId, setFixingCheckId] = useState("");
  const [lastFixResult, setLastFixResult] = useState<DoctorFixResult | null>(null);

  const fixCheck = useCallback(async (checkId: string, mode: DoctorFixMode = "safe") => {
    if (fixingCheckId) {
      return;
    }
    if (mode === "force" && !window.confirm(t("doctor.forceConfirmBody"))) {
      return;
    }
    setFixingCheckId(checkId);
    setLastFixResult(null);
    onMessage("", "ok");
    onError("");
    try {
      const result = await fixDoctorCheck(endpoint, checkId, {
        mode,
        projectPath: projectPath || undefined,
      });
      setLastFixResult(result);
      await onRefresh(result);
      const tone = doctorFixTone(result.status);
      onMessage(t(`doctor.fixStatus.${result.status}`, { defaultValue: result.status }), tone);
    } catch (cause) {
      onError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setFixingCheckId("");
    }
  }, [endpoint, fixingCheckId, onError, onMessage, onRefresh, projectPath, t]);

  return { fixingCheckId, lastFixResult, fixCheck };
}

function doctorFixTone(status: string): "ok" | "warn" | "danger" {
  if (status === "healthy" || status === "repaired") {
    return "ok";
  }
  return status === "failed" ? "danger" : "warn";
}
