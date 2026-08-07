from __future__ import annotations

import ast
import copy
import hashlib
import sys
import types
from pathlib import Path

import dashboard_server
from provider_test_integration_service import ProviderTestIntegrationService


ROOT = Path(__file__).parents[1]
METHODS = {
    "run_provider_test_sync",
    "_run_provider_text_probe",
    "_provider_probe_settings",
}
PRE_EXTRACTION_AST_SHA256 = {
    "run_provider_test_sync": "7c50694ccf57683d83350d2aa2d17df32d1989d17f50a6cd136860efb82bc291",
    "_run_provider_text_probe": "0d7b87d46c24dbd6fe4cbe22262c941270fc00273e3aa55a2a84e7170244d834",
    "_provider_probe_settings": "edc5fe9f60380075a4d0e98d18c0c8cbdd5f9af595fa130ef3ccb351d2c4c6ee",
}


def _class(path: Path, name: str) -> ast.ClassDef:
    return next(
        node
        for node in ast.parse(path.read_text(encoding="utf-8")).body
        if isinstance(node, ast.ClassDef) and node.name == name
    )


class _HostFacadeUnwrapper(ast.NodeTransformer):
    """Normalize the transitional host proxy back to the pre-extraction AST."""

    def visit_Attribute(self, node: ast.Attribute) -> ast.AST:
        node = self.generic_visit(node)
        if (
            isinstance(node.value, ast.Attribute)
            and isinstance(node.value.value, ast.Name)
            and node.value.value.id == "self"
            and node.value.attr == "_host"
        ):
            return ast.copy_location(ast.Name(id=node.attr, ctx=node.ctx), node)
        return node


def _normalized_pre_extraction_ast(node: ast.FunctionDef, name: str) -> str:
    normalized = copy.deepcopy(node)
    normalized = ast.fix_missing_locations(_HostFacadeUnwrapper().visit(normalized))
    assert isinstance(normalized, ast.FunctionDef)
    normalized.name = name
    assert normalized.args.args[0].arg == "self"
    normalized.args.args = normalized.args.args[1:]
    return ast.dump(normalized, include_attributes=False)


def test_provider_test_service_keeps_exact_dashboard_facades_static_import_and_narrow_host() -> None:
    dashboard_path = ROOT / "dashboard_server.py"
    service_path = ROOT / "provider_test_integration_service.py"
    dashboard_source = dashboard_path.read_text(encoding="utf-8")
    service_source = service_path.read_text(encoding="utf-8")
    dashboard_functions = {
        node.name: node
        for node in ast.parse(dashboard_source).body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    service = _class(service_path, "ProviderTestIntegrationService")
    implementations = {
        node.name.removeprefix("_impl_"): node
        for node in service.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("_impl_")
    }

    assert set(implementations) == METHODS
    assert "from provider_test_integration_service import ProviderTestIntegrationService" in dashboard_source
    assert "_PROVIDER_TEST_INTEGRATION = ProviderTestIntegrationService" in dashboard_source
    assert ProviderTestIntegrationService.__slots__ == ("_host",)
    assert len(dashboard_source.encode("utf-8")) < 1_175_000
    assert len(service_source.encode("utf-8")) > 7_000
    for forbidden in (
        "CONFIG_DOCUMENT_LOCK",
        "save_dashboard_config_document",
        "serialize_dashboard_api_config",
        "ProviderModelCatalogService",
        "DashboardVisionConfig",
        "Doctor",
        "DASHBOARD_RUNTIME",
        "FastAPI",
        "@app.",
        "dashboard_server import",
    ):
        assert forbidden not in service_source

    for name, implementation in implementations.items():
        facade = dashboard_functions[name]
        implementation_args = copy.deepcopy(implementation.args)
        assert implementation_args.args[0].arg == "self"
        implementation_args.args = implementation_args.args[1:]
        assert ast.dump(facade.args, include_attributes=False) == ast.dump(
            implementation_args,
            include_attributes=False,
        )
        assert len(facade.body) == 1
        statement = facade.body[0]
        assert isinstance(statement, ast.Return)
        assert f"_impl_{name}" in ast.unparse(statement)


def test_provider_test_service_preserves_pre_extraction_method_ast() -> None:
    service = _class(ROOT / "provider_test_integration_service.py", "ProviderTestIntegrationService")
    implementations = {
        node.name.removeprefix("_impl_"): node
        for node in service.body
        if isinstance(node, ast.FunctionDef) and node.name.startswith("_impl_")
    }

    assert set(implementations) == METHODS
    for name, implementation in implementations.items():
        actual = _normalized_pre_extraction_ast(implementation, name)
        assert hashlib.sha256(actual.encode("utf-8")).hexdigest() == PRE_EXTRACTION_AST_SHA256[name]


def test_provider_test_service_late_binds_probe_and_probe_settings(monkeypatch) -> None:
    service = dashboard_server._PROVIDER_TEST_INTEGRATION
    assert service._run_provider_text_probe is dashboard_server._run_provider_text_probe
    assert service._provider_probe_settings is dashboard_server._provider_probe_settings

    config = dashboard_server.DashboardApiConfig(
        provider="openai",
        api_key="safe-key",
        base_url="https://provider.example/v1",
        model="gpt-4.1-mini",
    )
    observed: dict[str, object] = {}

    class FakeOpenAI:
        def __init__(self, **kwargs: object) -> None:
            observed["client"] = kwargs

            def create(**request_kwargs: object) -> object:
                observed["request"] = request_kwargs
                return types.SimpleNamespace(choices=[])

            self.chat = types.SimpleNamespace(
                completions=types.SimpleNamespace(
                    create=create
                )
            )

    sentinel_settings = object()
    monkeypatch.setitem(sys.modules, "openai", types.SimpleNamespace(OpenAI=FakeOpenAI))

    def probe_settings(actual: object) -> object:
        observed["settings_config"] = actual
        return sentinel_settings

    def build_payload(settings: object, prompt: str) -> dict[str, object]:
        observed["payload"] = (settings, prompt)
        return {}

    monkeypatch.setattr(dashboard_server, "_provider_probe_settings", probe_settings)
    monkeypatch.setattr(dashboard_server, "build_openai_compatible_request_payload", build_payload)
    monkeypatch.setattr(dashboard_server, "model_rejects_fixed_temperature", lambda _model: False)

    assert dashboard_server._run_provider_text_probe(config, "late-bound probe") == ""
    assert observed["settings_config"] is config
    assert observed["payload"] == (sentinel_settings, "late-bound probe")
    assert observed["request"] == {"max_tokens": 64}


def test_provider_test_service_late_binds_dashboard_probe_facade(monkeypatch) -> None:
    request = dashboard_server.ProviderTestRequest(
        provider="openai",
        api_key="safe-key",
        model="gpt-4.1-mini",
        capability="structured",
    )
    calls: list[tuple[object, str, bool]] = []

    def fake_probe(config: object, prompt: str, *, structured: bool = False) -> str:
        calls.append((config, prompt, structured))
        return '{"ok": true}'

    monkeypatch.setattr(dashboard_server, "_run_provider_text_probe", fake_probe)
    result = dashboard_server.run_provider_test_sync(request)

    assert result["ok"] is True
    assert len(calls) == 1
    assert calls[0][2] is True
