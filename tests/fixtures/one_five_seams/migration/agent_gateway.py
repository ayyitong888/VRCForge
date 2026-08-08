class AgentGateway:
    def __init__(self, legacy: object) -> None:
        self._legacy = legacy

    def gateway_facade(self, value: str) -> str:
        return self._legacy._impl_gateway_facade(value)
