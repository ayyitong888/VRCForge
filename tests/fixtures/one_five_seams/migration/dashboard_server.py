# STOPGAP(1.5): fixture migration owner.
from legacy_owner import LegacyOwner

_OWNER = LegacyOwner(object())


def old_facade(value: str) -> str:
    return _OWNER._impl_old_facade(value)
