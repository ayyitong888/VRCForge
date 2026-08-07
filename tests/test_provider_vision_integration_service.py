from __future__ import annotations

import ast
import copy
import hashlib
import sys
import types
from pathlib import Path

import dashboard_server
from provider_vision_integration_service import ProviderVisionIntegrationService


ROOT = Path(__file__).parents[1]
METHODS = {
    "_agent_gateway_vision_analyze",
    "provider_model_supports_vision",
    "split_image_data_url",
    "build_vision_analysis_prompt",
    "_extract_openai_usage",
    "_run_provider_vision_analysis",
}
PRE_EXTRACTION_AST_SHA256 = {
    "_agent_gateway_vision_analyze": "f35aaff9308e680013f9364394be175fc1e749ba5a914352d4947a13b62c92d6",
    "provider_model_supports_vision": "9d2b70efb61230edb67f84a1d09e265b749752b4f2efbf34e6a52db3e637f918",
    "split_image_data_url": "5d9ea288f270838f7c18d86f2f6084e32360cbc6acbf36f7f38be7e1438764ff",
    "build_vision_analysis_prompt": "8a032f736d4268d91ea887f4573ccced584d74a52bd1c0d2c732b5ae3fc31398",
    "_extract_openai_usage": "4b5039b1e55e81602120b71d2e96701d77ccbde73420d79191e5b1da408cf43e",
    "_run_provider_vision_analysis": "4cd248c3bf5cbe4fea7cf3da11ebbec4036b89f7da1d326e942ad8586602908e",
}


def _class(path: Path, name: str) -> ast.ClassDef:
    return next(node for node in ast.parse(path.read_text(encoding="utf-8")).body if isinstance(node, ast.ClassDef) and node.name == name)


class _HostFacadeUnwrapper(ast.NodeTransformer):
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


def test_provider_vision_service_keeps_exact_dashboard_facades_static_import_and_narrow_host() -> None:
    dashboard_path = ROOT / "dashboard_server.py"
    service_path = ROOT / "provider_vision_integration_service.py"
    dashboard_source = dashboard_path.read_text(encoding="utf-8")
    service_source = service_path.read_text(encoding="utf-8")
    dashboard_functions = {
        node.name: node
        for node in ast.parse(dashboard_source).body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    service = _class(service_path, "ProviderVisionIntegrationService")
    implementations = {
        node.name.removeprefix("_impl_"): node
        for node in service.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("_impl_")
    }

    assert set(implementations) == METHODS
    assert "from provider_vision_integration_service import ProviderVisionIntegrationService" in dashboard_source
    assert "_PROVIDER_VISION_INTEGRATION = ProviderVisionIntegrationService" in dashboard_source
    assert ProviderVisionIntegrationService.__slots__ == ("_host",)
    assert len(dashboard_source.encode("utf-8")) < 1_175_000
    assert len(service_source.encode("utf-8")) > 9_000
    for forbidden in (
        "CONFIG_DOCUMENT_LOCK",
        "save_dashboard_config_document",
        "serialize_dashboard_api_config",
        "run_gemini_vision_audit",
        "capture_",
        "shader_",
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
        assert ast.dump(facade.args, include_attributes=False) == ast.dump(implementation_args, include_attributes=False)
        assert len(facade.body) == 1
        statement = facade.body[0]
        assert isinstance(statement, ast.Return)
        assert f"_impl_{name}" in ast.unparse(statement)


def test_provider_vision_service_preserves_pre_extraction_method_ast() -> None:
    service = _class(ROOT / "provider_vision_integration_service.py", "ProviderVisionIntegrationService")
    implementations = {
        node.name.removeprefix("_impl_"): node
        for node in service.body
        if isinstance(node, ast.FunctionDef) and node.name.startswith("_impl_")
    }
    assert set(implementations) == METHODS
    for name, implementation in implementations.items():
        actual = _normalized_pre_extraction_ast(implementation, name)
        assert hashlib.sha256(actual.encode("utf-8")).hexdigest() == PRE_EXTRACTION_AST_SHA256[name]


def test_provider_vision_service_keeps_pure_contracts() -> None:
    assert dashboard_server.provider_model_supports_vision("gemini", "anything") is True
    assert dashboard_server.provider_model_supports_vision("deepseek", "deepseek-vl") is False
    assert dashboard_server.provider_model_supports_vision("openai", "gpt-4o") is True
    assert dashboard_server.split_image_data_url("data:image/png;base64,YQ==") == ("image/png", "YQ==")
    prompt = dashboard_server.build_vision_analysis_prompt("请检查截图", [{"name": "avatar.png"}])
    assert "avatar.png" in prompt
    assert "请检查截图" in prompt
    assert dashboard_server._extract_openai_usage(types.SimpleNamespace(usage=types.SimpleNamespace(prompt_tokens=3, completion_tokens=5, total_tokens=8))) == {
        "exact": True,
        "inputTokens": 3,
        "outputTokens": 5,
        "totalTokens": 8,
    }


def test_provider_vision_service_late_binds_gateway_and_openai_helpers(monkeypatch) -> None:
    config = dashboard_server.DashboardApiConfig(
        provider="openai", api_key="safe-key", base_url="https://provider.example/v1", model="gpt-4o"
    )
    calls: dict[str, object] = {}
    monkeypatch.setattr(dashboard_server, "DASHBOARD_API_CONFIG", config)

    def build_prompt(message: str, images: list[dict[str, object]]) -> str:
        calls["prompt"] = (message, images)
        return "prompt"

    def run_vision(actual: object, prompt: str, images: list[dict[str, object]]) -> tuple[str, dict[str, bool]]:
        calls["run"] = (actual, prompt, images)
        return "analysis", {"exact": False}

    monkeypatch.setattr(dashboard_server, "build_vision_analysis_prompt", build_prompt)
    monkeypatch.setattr(dashboard_server, "_run_provider_vision_analysis", run_vision)

    result = dashboard_server._agent_gateway_vision_analyze("look", [{"dataUrl": "data:image/png;base64,YQ=="}])
    assert result["status"] == "analyzed"
    assert calls["prompt"] == ("look", [{"dataUrl": "data:image/png;base64,YQ=="}])
    assert calls["run"] == (config, "prompt", [{"dataUrl": "data:image/png;base64,YQ=="}])

    class FakeOpenAI:
        def __init__(self, **kwargs: object) -> None:
            calls["client"] = kwargs

            def create(**request: object) -> object:
                calls["request"] = request
                return types.SimpleNamespace(
                    choices=[types.SimpleNamespace(message=types.SimpleNamespace(content="fake vision"))],
                    usage=types.SimpleNamespace(prompt_tokens=4, completion_tokens=6, total_tokens=10),
                )

            self.chat = types.SimpleNamespace(completions=types.SimpleNamespace(create=create))

    monkeypatch.setitem(sys.modules, "openai", types.SimpleNamespace(OpenAI=FakeOpenAI))
    monkeypatch.setattr(dashboard_server, "model_rejects_fixed_temperature", lambda _model: False)
    text, usage = dashboard_server._PROVIDER_VISION_INTEGRATION._impl__run_provider_vision_analysis(
        config, "late prompt", [{"dataUrl": "data:image/png;base64,YQ=="}]
    )
    assert text == "fake vision"
    assert usage == {"exact": True, "inputTokens": 4, "outputTokens": 6, "totalTokens": 10}
    assert calls["client"] == {"api_key": "safe-key", "base_url": "https://provider.example/v1", "timeout": 60.0}
    assert calls["request"] == {
        "model": "gpt-4o",
        "messages": [{"role": "user", "content": [{"type": "image_url", "image_url": {"url": "data:image/png;base64,YQ=="}}, {"type": "text", "text": "late prompt"}]}],
        "temperature": 0,
        "max_tokens": 1024,
    }
