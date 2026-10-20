from pathlib import Path

from vrchat_blendshape_agent import Settings, request_responses_plan_with_metadata


class _FakeResponses:
    def create(self, **_payload):
        return {"output_text": "ok", "output": [], "usage": {}}


class _FakeClient:
    def __init__(self):
        self.responses = _FakeResponses()

    def close(self):
        return None


def _settings(max_retries=None):
    return Settings(
        llm_provider="custom",
        llm_api_key="fixture-key",
        llm_base_url="https://provider.example/v1",
        llm_model="loop-model",
        llm_api_key_env="TEST_API_KEY",
        gemini_thinking_level="",
        unity_mcp_command=[],
        unity_mcp_host="127.0.0.1",
        unity_mcp_port=0,
        unity_mcp_instance="project-scoped",
        unity_mcp_retries=0,
        unity_mcp_retry_backoff_seconds=0.0,
        unity_mcp_timeout_seconds=10,
        export_tool_name="",
        execute_tool_name="",
        export_path=Path("export.json"),
        min_confidence=0.0,
        llm_api_type="responses",
        llm_sdk_max_retries=max_retries,
    )


def _capture_client_kwargs(settings):
    created = []

    def factory(**kwargs):
        created.append(kwargs)
        return _FakeClient()

    response = request_responses_plan_with_metadata(
        settings,
        "fixture prompt",
        client_factory=factory,
    )
    return response, created


def test_custom_responses_disables_implicit_sdk_retries_by_default():
    response, created = _capture_client_kwargs(_settings())
    assert response.text == "ok"
    assert created == [{
        "api_key": "fixture-key",
        "base_url": "https://provider.example/v1",
        "max_retries": 0,
    }]


def test_custom_responses_preserves_explicit_sdk_retry_override():
    response, created = _capture_client_kwargs(_settings(3))
    assert response.text == "ok"
    assert created == [{
        "api_key": "fixture-key",
        "base_url": "https://provider.example/v1",
        "max_retries": 3,
    }]
