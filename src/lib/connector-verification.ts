import type { ExternalAgentConnectorStatus } from "./api/connectors";

// A recent installation self-test proves the bridge worked at that time. It
// does not establish that the external client is currently connected.
export function hasRecentConnectorSelfTest(
  status: ExternalAgentConnectorStatus | null,
  runtimeConnected: boolean,
  projectPath: string,
  now = Date.now(),
): boolean {
  if (!runtimeConnected || !status?.gateway?.enabled) return false;
  if (status.contextProjectPath !== projectPath) return false;
  return Object.entries(status.clients || {}).some(([client, state]) => {
    const action = status.connectorActions?.[client];
    return Boolean(
      state.installed
        && action?.ok
        && action.action === "install"
        && action.verificationScope === "installation_self_test"
        && (action.verificationExpiresAt || 0) * 1000 > now
        && action.handshake?.ready
        && action.handshake?.preflightOk
        && action.handshake?.preflightRuntimeOnline,
    );
  });
}
