from __future__ import annotations

from pathlib import Path

import pytest

import external_agent_connector_installer as installer


class _FakeKey:
    def __init__(self, values: dict[str, object] | None = None, children: dict[str, "_FakeKey"] | None = None):
        self.values = values or {}
        self.children = children or {}

    def __enter__(self) -> "_FakeKey":
        return self

    def __exit__(self, *_args: object) -> None:
        return None


class _FakeWinreg:
    HKEY_CURRENT_USER = object()
    KEY_READ = 0x20019

    def __init__(self, packages: dict[str, _FakeKey]):
        self.root = _FakeKey(children=packages)

    def OpenKey(self, parent: object, subkey: str, *_args: object) -> _FakeKey:
        if parent is self.HKEY_CURRENT_USER:
            if "Packages" not in subkey:
                raise FileNotFoundError(subkey)
            return self.root
        if isinstance(parent, _FakeKey) and subkey in parent.children:
            return parent.children[subkey]
        raise FileNotFoundError(subkey)

    @staticmethod
    def QueryInfoKey(key: _FakeKey) -> tuple[int, int, int]:
        return len(key.children), len(key.values), 0

    @staticmethod
    def EnumKey(key: _FakeKey, index: int) -> str:
        return list(key.children)[index]

    @staticmethod
    def QueryValueEx(key: _FakeKey, name: str) -> tuple[object, int]:
        if name not in key.values:
            raise FileNotFoundError(name)
        return key.values[name], 1


def _patch_fake_registry(monkeypatch: pytest.MonkeyPatch, fake: _FakeWinreg) -> None:
    monkeypatch.setattr(installer, "winreg", fake)
    monkeypatch.setattr(installer.os, "name", "nt")


def test_appx_probe_returns_registered_package_root(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    root = tmp_path / "WindowsApps" / "OpenAI.Codex_1.2.3.0_x64__test"
    root.mkdir(parents=True)
    package_name = root.name
    _patch_fake_registry(
        monkeypatch,
        _FakeWinreg(
            {
                package_name: _FakeKey(
                    {
                        "PackageRootFolder": str(root),
                        "PackageID": package_name,
                    }
                )
            }
        ),
    )

    probe = installer._probe_appx_package("OpenAI.Codex")

    assert probe["found"] is True
    assert probe["ok"] is True
    assert probe["matches"] == [str(root)]
    assert probe["packageIds"] == [package_name]


def test_appx_probe_reports_unverifiable_registered_path(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    missing = tmp_path / "WindowsApps" / "OpenAI.Codex_missing"
    _patch_fake_registry(
        monkeypatch,
        _FakeWinreg(
            {
                missing.name: _FakeKey(
                    {
                        "PackageRootFolder": str(missing),
                        "PackageID": missing.name,
                    }
                )
            }
        ),
    )

    probe = installer._probe_appx_package("OpenAI.Codex")

    assert probe["found"] is False
    assert probe["ok"] is False
    assert probe["matches"] == []
    assert "No registered AppX package" in probe["error"]


@pytest.mark.skipif(installer.os.name != "nt", reason="AppX registry is Windows-only")
def test_live_codex_appx_registration_is_detected() -> None:
    probe = installer._probe_appx_package("OpenAI.Codex")
    if not probe["found"]:
        pytest.skip("OpenAI Codex AppX package is not installed for this user")

    assert probe["ok"] is True
    assert any(Path(path).name.startswith("OpenAI.Codex_") for path in probe["matches"])
