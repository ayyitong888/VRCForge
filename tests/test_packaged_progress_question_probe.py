from __future__ import annotations

import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "diagnose_packaged_progress_questions.mjs"


def test_question_probe_has_release_binding_isolation_and_owned_cleanup() -> None:
    source = SCRIPT.read_text(encoding="utf-8")

    assert 'const allowUnpushed = process.argv.includes("--allow-unpushed");' in source
    assert '"--self-test"' in source
    assert "isLocalAcceptanceBuildPolicy" in source
    assert "releaseEligible === false" in source
    assert "policy.allowDirty === false" in source
    assert "!worktreeClean || !localAcceptanceBuildPolicy" in source
    assert "manifestCommit !== headCommit" in source
    assert "portableSha256" in source
    assert "innerExeSha256" in source
    assert "extractedExeSha256: binding.exeSha256" in source
    assert "if ($entries.Count -ne 1)" in source
    assert "Select-Object -First 1" not in source

    assert '!key.toUpperCase().startsWith("VRCFORGE_")' in source
    assert 'VRCFORGE_DESKTOP_EXECUTOR: "0"' in source
    assert "APPDATA: hostProfileRoot" in source
    assert "LOCALAPPDATA: hostProfileRoot" in source
    assert "USERPROFILE: hostProfileRoot" not in source
    assert "HOME: hostProfileRoot" not in source
    assert 'process.env.USERPROFILE = "host-user-profile-preserved"' in source
    assert 'process.env.HOME = "host-home-preserved"' in source
    assert "WEBVIEW2_USER_DATA_FOLDER: webviewDataRoot" in source
    assert '"OPENAI_API_KEY", "ANTHROPIC_API_KEY"' in source
    assert '"XAI_API_KEY", "OLLAMA_API_KEY"' in source
    assert '"GOOGLE_APPLICATION_CREDENTIALS", "LLM_API_KEY"' in source
    assert "isolatedCredentialKeys.some((key) => Object.hasOwn(env, key))" in source
    assert "Preflight found an existing VRCForge process or occupied backend/CDP port; nothing was terminated" in source
    assert "Launch preflight found an existing VRCForge process or occupied backend/CDP port; nothing was terminated" in source
    assert "closeExistingVrcforgeProcesses" not in source
    assert "closePackagedProcesses" not in source
    assert "async function forceCloseLaunch(launch)" in source
    assert "Packaged app exited before launch completed" in source
    assert "wrapped.launchDiagnostics" in source
    assert "report.phases.launchFailure" in source
    assert "report.closures.launchFailure" in source
    assert "$ids.Contains([int]$candidate.ParentProcessId)" in source
    assert "$path.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase)" in source
    assert "$path.Equals($exe, [StringComparison]::OrdinalIgnoreCase)" in source


def test_question_probe_contract_covers_auth_scope_redaction_and_restart() -> None:
    source = SCRIPT.read_text(encoding="utf-8")

    assert 'rawAppRequest("/api/app/agent/questions", { token: undefined })' in source
    assert 'rawAppRequest("/api/app/agent/questions", { token: wrongAuthToken })' in source
    assert "missingAuth.status !== 401" in source
    assert "wrongAuth.status !== 401" in source
    assert "runPackagedProgressQuestionUiGate(report, app.cdp)" in source
    assert 'appApi("/api/app/agent/progress/replace", {' in source
    assert "Array.from({ length: 8 }" in source
    assert "optionScroller.scrollHeight > optionScroller.clientHeight" in source
    assert "progressInRightRail" in source
    assert "questionInRightRail" in source
    assert "recommendedVisible" in source
    assert "phase.clickOption = await evalValue" in source
    assert "phase.cardDismissed = await waitForEval" in source
    assert 'appApi("/api/app/agent/questions", {' in source
    assert "querySessionBProjectA" in source
    assert "querySessionAProjectB" in source
    assert "foreignSessionList" in source
    assert "foreignProjectList" in source
    assert "wrongSession.status !== 404" in source
    assert "pendingQuestionIsUntouched(afterWrongSession, questionId)" in source
    assert "wrongProject.status !== 404" in source
    assert "pendingQuestionIsUntouched(afterWrongProject, questionId)" in source
    assert 'includeAnswered: "true"' in source
    assert "answerIsRedactedNonEmpty(answeredResponseQuestion)" in source
    assert "const restartList = await appApi" in source
    assert "answerIsRedactedNonEmpty(restartQuestion)" in source
    assert "durableAnswerRedactedNonEmpty" in source
    assert "Question JSONL did not contain exactly one redacted non-empty durable answer event" in source
    assert "containsSensitiveText(answeredPayload)" in source
    assert "containsSensitiveText(answeredList)" in source
    assert "containsSensitiveText(restartList)" in source
    assert "containsSensitiveText(questionLog)" in source
    assert "privateProfileName" in source
    assert "probeSecrets = new Set([sensitiveToken, privateProfileName" in source
    assert "const redactedReport = redactProbeSecrets(report);" in source
    assert 'status: "not-exercised"' in source
    assert "No private Goal storage was seeded." in source


def test_question_probe_self_test_and_unknown_option_are_side_effect_free() -> None:
    self_test = subprocess.run(
        ["node", str(SCRIPT), "--self-test"],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert self_test.returncode == 0, self_test.stderr
    assert "Question lifecycle probe self-test passed" in self_test.stdout

    unknown = subprocess.run(
        ["node", str(SCRIPT), "--unexpected-option"],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert unknown.returncode == 2
    assert "Unknown packaged Question lifecycle probe option." in unknown.stderr
