export type AppQuitPersistenceResult = "persisted" | "persistence_failed";

/** Flush the current chat snapshot before handing lifetime ownership to Rust. */
export async function flushChatsBeforeQuit(
  persistChatsNow: () => Promise<void>,
  confirmQuit: () => Promise<void>,
): Promise<AppQuitPersistenceResult> {
  try {
    await persistChatsNow();
  } catch {
    return "persistence_failed";
  }
  await confirmQuit();
  return "persisted";
}
