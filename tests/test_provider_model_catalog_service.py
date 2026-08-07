from __future__ import annotations

import ast
import copy
import inspect
from pathlib import Path

import dashboard_server
from provider_model_catalog_service import ProviderModelCatalogService


ROOT = Path(__file__).parents[1]
METHODS = {
    "provider_config_descriptor",
    "enrich_provider_model_item",
    "fetch_provider_models",
    "fetch_openai_compatible_models",
    "fetch_google_ai_studio_models",
    "fetch_vertex_ai_models",
    "fetch_anthropic_models",
    "normalize_provider_model_list",
    "read_model_attr",
    "coerce_positive_int",
    "build_provider_model_info",
}


def _class(path: Path, name: str) -> ast.ClassDef:
    return next(
        node
        for node in ast.parse(path.read_text(encoding="utf-8")).body
        if isinstance(node, ast.ClassDef) and node.name == name
    )


def test_provider_catalog_keeps_exact_dashboard_facades_static_import_and_narrow_host() -> None:
    dashboard_path = ROOT / "dashboard_server.py"
    service_path = ROOT / "provider_model_catalog_service.py"
    dashboard_source = dashboard_path.read_text(encoding="utf-8")
    service_source = service_path.read_text(encoding="utf-8")
    dashboard_functions = {
        node.name: node
        for node in ast.parse(dashboard_source).body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    service = _class(service_path, "ProviderModelCatalogService")
    implementations = {
        node.name.removeprefix("_impl_"): node
        for node in service.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("_impl_")
    }

    assert set(implementations) == METHODS
    assert "from provider_model_catalog_service import ProviderModelCatalogService" in dashboard_source
    assert "_PROVIDER_MODEL_CATALOG = ProviderModelCatalogService" in dashboard_source
    assert ProviderModelCatalogService.__slots__ == ("_host",)
    assert len(dashboard_source.encode("utf-8")) < 1_175_000
    assert len(service_source.encode("utf-8")) > 8_000
    for forbidden in (
        "CONFIG_DOCUMENT_LOCK",
        "save_dashboard_config_document",
        "serialize_dashboard_api_config",
        "run_provider_test_sync",
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


def test_provider_catalog_late_binds_descriptor_normalization_and_vertex_resolution(monkeypatch) -> None:
    service = dashboard_server._PROVIDER_MODEL_CATALOG
    assert service.provider_model_descriptor is dashboard_server.provider_model_descriptor

    config = dashboard_server.DashboardApiConfig(
        provider="vertexai", api_key="", base_url="projects/late/locations/test", model="vertex-model"
    )
    monkeypatch.setattr(
        dashboard_server,
        "provider_model_descriptor",
        lambda *_args: {
            "apiType": "late",
            "resolvedApiType": "late",
            "supportedApiTypes": ["late"],
            "capabilities": ["late"],
            "capabilitySource": "late",
        },
    )
    monkeypatch.setattr(dashboard_server, "build_provider_model_info", lambda _item, model_id: {"id": model_id, "late": True})

    descriptor = dashboard_server.provider_config_descriptor(config)
    enriched = dashboard_server.enrich_provider_model_item(config, {"id": "late-model"})
    models = dashboard_server.normalize_provider_model_list({"models": [{"id": "late-model"}]}, "Late")

    assert descriptor["apiType"] == "late"
    assert enriched["capabilitySource"] == "late"
    assert models == [{"id": "late-model", "late": True}]

    calls: list[str] = []
    monkeypatch.setattr(dashboard_server, "fetch_vertex_ai_models", lambda _config: calls.append("vertex") or [])
    assert dashboard_server.fetch_provider_models(config) == []
    assert calls == ["vertex"]

    source = inspect.getsource(ProviderModelCatalogService._impl_fetch_vertex_ai_models)
    assert "self._host.resolve_vertex_project_location" in source


def test_provider_catalog_keeps_vertex_resolution_at_dashboard_root() -> None:
    dashboard_functions = {
        node.name: node
        for node in ast.parse((ROOT / "dashboard_server.py").read_text(encoding="utf-8")).body
        if isinstance(node, ast.FunctionDef)
    }
    assert "resolve_vertex_project_location" in dashboard_functions
    assert "resolve_vertex_project_location" not in {
        node.name.removeprefix("_impl_")
        for node in _class(ROOT / "provider_model_catalog_service.py", "ProviderModelCatalogService").body
        if isinstance(node, ast.FunctionDef) and node.name.startswith("_impl_")
    }
