const PREPARE_APP_QUIT_EXPRESSION = `(async () => {
  const invoke = window.__TAURI_INTERNALS__?.invoke;
  if (typeof invoke !== "function") {
    return { accepted: false, error: "Tauri invoke is unavailable." };
  }
  try {
    const receipt = await invoke("prepare_app_quit");
    return receipt?.accepted === true
      ? { accepted: true }
      : { accepted: false, error: "The packaged app rejected the Quit request." };
  } catch (error) {
    return { accepted: false, error: String(error?.message || error) };
  }
})()`;

const CONFIRM_APP_QUIT_EXPRESSION = `(() => {
  const invoke = window.__TAURI_INTERNALS__?.invoke;
  if (typeof invoke !== "function") {
    return { dispatched: false, error: "Tauri invoke is unavailable." };
  }
  void invoke("confirm_app_quit").catch(() => {});
  return { dispatched: true };
})()`;

const QUIT_IPC_TIMEOUT_MS = 10_000;

async function boundedRuntimeEvaluate(cdp, params, phase) {
  let timer;
  try {
    return await Promise.race([
      Promise.resolve().then(() => cdp.send("Runtime.evaluate", params)),
      new Promise((_, reject) => {
        timer = setTimeout(
          () => reject(new Error(`${phase} timed out after ${QUIT_IPC_TIMEOUT_MS} ms.`)),
          QUIT_IPC_TIMEOUT_MS,
        );
      }),
    ]);
  } finally {
    if (timer) clearTimeout(timer);
  }
}

export async function requestPackagedAppQuit(cdp) {
  if (!cdp || typeof cdp.send !== "function") {
    return { accepted: false, error: "CDP Runtime transport is unavailable." };
  }
  try {
    const response = await boundedRuntimeEvaluate(cdp, {
      expression: PREPARE_APP_QUIT_EXPRESSION,
      returnByValue: true,
      awaitPromise: true,
    }, "prepare_app_quit");
    const result = response?.result?.value;
    if (result?.accepted === true) {
      try {
        const confirmation = await boundedRuntimeEvaluate(cdp, {
          expression: CONFIRM_APP_QUIT_EXPRESSION,
          returnByValue: true,
          awaitPromise: false,
        }, "confirm_app_quit");
        const confirmationResult = confirmation?.result?.value;
        if (confirmationResult?.dispatched !== true) {
          return {
            accepted: false,
            confirmAttempted: true,
            confirmDispatched: false,
            error: String(confirmationResult?.error || "confirm_app_quit was not dispatched."),
          };
        }
        return {
          accepted: true,
          confirmAttempted: true,
          confirmDispatched: true,
        };
      } catch (error) {
        // confirm_app_quit may close the CDP target before its evaluation
        // response is delivered. Callers must still require their exact app
        // process tree and backend/CDP ports to clear before treating Quit as
        // successful.
        return {
          accepted: true,
          confirmAttempted: true,
          confirmDispatched: false,
          confirmResponseLost: true,
          error: String(error?.message || error),
        };
      }
    }
    return {
      accepted: false,
      error: String(result?.error || "The packaged app did not accept the Quit request."),
    };
  } catch (error) {
    return {
      accepted: false,
      error: String(error?.message || error || "The packaged app Quit request failed."),
    };
  }
}
