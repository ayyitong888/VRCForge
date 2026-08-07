from __future__ import annotations

import ast
import copy
import hashlib
from pathlib import Path

import dashboard_server
from provider_configuration_service import ProviderConfigurationService


ROOT = Path(__file__).parents[1]
METHODS = {
    "load_initial_dashboard_api_config",
    "load_initial_dashboard_vision_config",
    "load_config_document",
    "normalize_api_config_request",
    "normalize_vision_config_request",
    "save_dashboard_config_document",
    "save_dashboard_api_config",
    "save_dashboard_vision_config",
    "serialize_api_config",
    "serialize_vision_config",
    "serialize_app_api_config",
    "serialize_app_vision_config",
    "build_effective_model_summary",
    "mask_secret",
}
PRE_EXTRACTION_AST_SHA256 = {
    "load_initial_dashboard_api_config": "f6e131d4dadb096fd87bb5990ec2f9bc0c25b26ac53fe4ca02389ed9fe5354f5",
    "load_initial_dashboard_vision_config": "9d3d372eb379b9c62b54f72ea1147ce90ccfbfdf246e3b6b2aef63777c0179e1",
    "load_config_document": "14bf679d8abb476fe3336d1f8a7273ea43a19f27d8095f8af34af17ae75cfd45",
    "normalize_api_config_request": "5a6a020f52aa832c90eb10ee969fe37354d1eb34a1dc8e1f78ad3c30df910951",
    "normalize_vision_config_request": "1c760b070de61397fd3306b8fa17c8cd08b62aa80893dae8cd066549824f4855",
    "serialize_api_config": "0c51565068c91acbfecc8f7e39cab0a09e4404c5be4a08c19abd9179883edcbd",
    "serialize_vision_config": "0eb99abb87f8ab5c84c4aa86d53daa8c872161d21ee2bc8a08f7b7798161c798",
    "serialize_app_api_config": "c0faa218c67514fa3dc997b936d41437905256089ee468ca19dc9a6d136352f3",
    "serialize_app_vision_config": "6eb78d109585bd50b5c6eaa0afa5b821b60197055898919494048f9359a31efe",
    "build_effective_model_summary": "4ae09c625b0170730bc72aad22f9ee4365d55d900547a5a48d814a4cf9f97a88",
    "mask_secret": "a30761c9781ef606e6527842b131ac7383d55d3cde8eeade35ef7fd0c54218a5",
}


def _class(path: Path, name: str) -> ast.ClassDef:
    return next(
        node
        for node in ast.parse(path.read_text(encoding="utf-8")).body
        if isinstance(node, ast.ClassDef) and node.name == name
    )


class _HostFacadeUnwrapper(ast.NodeTransformer):
    """Normalize the transitional Dashboard host proxy back to the old body."""

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


def test_provider_configuration_keeps_exact_dashboard_facades_static_import_and_narrow_host() -> None:
    dashboard_path = ROOT / "dashboard_server.py"
    service_path = ROOT / "provider_configuration_service.py"
    dashboard_source = dashboard_path.read_text(encoding="utf-8")
    service_source = service_path.read_text(encoding="utf-8")
    dashboard_functions = {
        node.name: node
        for node in ast.parse(dashboard_source).body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    service = _class(service_path, "ProviderConfigurationService")
    implementations = {
        node.name.removeprefix("_impl_"): node
        for node in service.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("_impl_")
    }

    assert set(implementations) == METHODS
    assert "from provider_configuration_service import ProviderConfigurationService" in dashboard_source
    assert "_PROVIDER_CONFIGURATION = ProviderConfigurationService" in dashboard_source
    assert ProviderConfigurationService.__slots__ == ("_host",)
    assert len(dashboard_source.encode("utf-8")) < 1_175_000
    assert len(service_source.encode("utf-8")) > 10_000
    for forbidden in (
        "ProviderModelCatalogService",
        "ProviderTestIntegrationService",
        "fetch_provider_models",
        "run_provider_test_sync",
        "_run_provider_text_probe",
        "_run_provider_vision_analysis",
        "build_vision_analysis_prompt",
        "DASHBOARD_RUNTIME",
        "FastAPI",
        "@app.",
        "Doctor",
        "MCP",
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


def test_provider_configuration_preserves_pre_extraction_method_ast() -> None:
    service = _class(ROOT / "provider_configuration_service.py", "ProviderConfigurationService")
    implementations = {
        node.name.removeprefix("_impl_"): node
        for node in service.body
        if isinstance(node, ast.FunctionDef) and node.name.startswith("_impl_")
    }

    assert set(implementations) == METHODS
    for name, expected_hash in PRE_EXTRACTION_AST_SHA256.items():
        implementation = implementations[name]
        actual = _normalized_pre_extraction_ast(implementation, name)
        assert hashlib.sha256(actual.encode("utf-8")).hexdigest() == expected_hash


def test_provider_configuration_late_binds_config_projection_and_secret_masking(monkeypatch) -> None:
    service = dashboard_server._PROVIDER_CONFIGURATION
    assert service.serialize_api_config is dashboard_server.serialize_api_config
    assert service.serialize_vision_config is dashboard_server.serialize_vision_config
    assert service.save_dashboard_config_document is dashboard_server.save_dashboard_config_document

    config = dashboard_server.DashboardApiConfig(
        provider="openai",
        api_key="provider-secret-key",
        base_url="https://provider.example/v1",
        model="gpt-4.1-mini",
    )
    monkeypatch.setattr(dashboard_server, "DASHBOARD_API_CONFIG", config)
    monkeypatch.setattr(
        dashboard_server,
        "provider_config_descriptor",
        lambda _config: {"api_type": "late", "apiType": "late", "resolvedApiType": "late"},
    )
    monkeypatch.setattr(dashboard_server, "mask_secret", lambda _value: "masked-by-root")

    serialized = dashboard_server.serialize_api_config(include_secret=False)
    effective = dashboard_server.build_effective_model_summary()

    assert serialized["api_key"] == "masked-by-root"
    assert serialized["api_type"] == "late"
    assert effective["apiType"] == "late"
    assert service._impl_mask_secret("12345678") == "********"
    assert service._impl_mask_secret("123456789") == "1234****6789"


def test_provider_configuration_app_projection_never_exposes_secret(monkeypatch) -> None:
    monkeypatch.setattr(
        dashboard_server,
        "serialize_api_config",
        lambda *, include_secret: {"api_key": "must-not-leak", "includeSecret": include_secret},
    )
    monkeypatch.setattr(
        dashboard_server,
        "serialize_vision_config",
        lambda *, include_secret: {"api_key": "must-not-leak", "includeSecret": include_secret},
    )

    assert dashboard_server.serialize_app_api_config() == {"includeSecret": False}
    assert dashboard_server.serialize_app_vision_config() == {"includeSecret": False}
