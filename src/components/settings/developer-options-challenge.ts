export const DEVELOPER_OPTIONS_MINIMUM_WAIT_MS = 5_000;

export function developerChallengeRemainingMs(deadline: number, now: number) {
  return Math.max(0, deadline - now);
}

export function developerChallengeCountdown(deadline: number, now: number) {
  return Math.ceil(developerChallengeRemainingMs(deadline, now) / 1_000);
}

export function developerChallengeReady(deadline: number, now: number) {
  return developerChallengeRemainingMs(deadline, now) === 0;
}

export function createDeveloperChallengeSubmitGuard() {
  let submitted = false;
  return () => {
    if (submitted) {
      return false;
    }
    submitted = true;
    return true;
  };
}
