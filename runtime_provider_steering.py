"""Interrupt only an owned model request; leave turn and tool authority intact."""


class ProviderSteeredError(RuntimeError):
    code = "provider_steered"

    def __init__(self, owner):
        super().__init__("Provider request interrupted for pending user steering.")
        self.provider_usage = (
            dict(owner.response.usage or {}) if owner.response is not None
            else getattr(owner.error, "provider_usage", None)
        )


def interrupt_for_steer(owner, state, context, *, enabled, join_seconds):
    if not enabled or state.cancel_requested(
        session_id=context.get("sessionId", ""), turn_id=context.get("turnId", ""),
        client_turn_id=context.get("clientTurnId", ""),
    ):
        return
    if not state.steer_pending(session_id=context.get("sessionId", ""),
                               client_turn_id=context.get("clientTurnId", "")):
        return
    owner.cancellation.set()
    # Do not release the owner or admit a replacement while its worker is alive.
    # Existing provider deadlines and user Stop remain authoritative.
    if owner.done.wait(join_seconds):
        raise ProviderSteeredError(owner)
