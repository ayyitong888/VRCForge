class LegacyOwner:
    def __init__(self, host: object) -> None:
        self._host = host

    def __getattr__(self, name: str) -> object:
        return getattr(self._host, name)

    def _impl_old_facade(self, value: str) -> str:
        return value
