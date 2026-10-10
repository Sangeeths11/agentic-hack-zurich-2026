import json

import httpx
import pytest
from support import simulation_settings

from scam_the_scamers.gemini_risk import extract_signals, generate_warning
from scam_the_scamers.risk import POINTS_BY_CATEGORY


class _MockTransport(httpx.AsyncBaseTransport):
    def __init__(self, handler):
        self._handler = handler

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        return self._handler(request)


@pytest.fixture
def mock_client(monkeypatch):
    def install(handler):
        original_init = httpx.AsyncClient.__init__

        def patched_init(self, *args, **kwargs):
            kwargs["transport"] = _MockTransport(handler)
            original_init(self, *args, **kwargs)

        monkeypatch.setattr(httpx.AsyncClient, "__init__", patched_init)

    return install


def _llm_response(categories: list[str]) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "choices": [
                {"message": {"content": json.dumps({"categories": categories})}},
            ]
        },
    )


def _plain_text_response(text: str) -> httpx.Response:
    return httpx.Response(200, json={"choices": [{"message": {"content": text}}]})


async def test_extracted_categories_are_scored_at_their_risk_py_points(mock_client):
    mock_client(lambda request: _llm_response(["secrecy", "financial_request"]))
    settings = simulation_settings()
    result = await extract_signals(settings, "Wire the money and don't tell your daughter.")
    assert not result.error
    assert {s.category: s.points for s in result.signals} == {
        "secrecy": POINTS_BY_CATEGORY["secrecy"],
        "financial_request": POINTS_BY_CATEGORY["financial_request"],
    }


async def test_request_targets_the_configured_openai_compatible_endpoint(mock_client):
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("authorization")
        seen["body"] = json.loads(request.content)
        return _llm_response([])

    mock_client(handler)
    settings = simulation_settings(
        llm_base_url="https://generativelanguage.googleapis.com/v1beta/openai",
        llm_api_key="gemini-test-key",
        llm_model="gemini-2.0-flash",
    )
    await extract_signals(settings, "hello")
    assert seen["url"] == "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"
    assert seen["auth"] == "Bearer gemini-test-key"
    assert seen["body"]["model"] == "gemini-2.0-flash"


async def test_empty_category_list_scores_nothing(mock_client):
    mock_client(lambda request: _llm_response([]))
    settings = simulation_settings()
    result = await extract_signals(settings, "Hi, just calling to say hello.")
    assert result.signals == []
    assert not result.error


async def test_unknown_category_from_llm_is_dropped_not_crashed(mock_client):
    mock_client(lambda request: _llm_response(["secrecy", "something_the_model_made_up"]))
    settings = simulation_settings()
    result = await extract_signals(settings, "...")
    assert [s.category for s in result.signals] == ["secrecy"]


async def test_missing_api_key_short_circuits_without_a_network_call(mock_client):
    called = False

    def handler(request):
        nonlocal called
        called = True
        return _llm_response([])

    mock_client(handler)
    settings = simulation_settings(llm_api_key="")
    result = await extract_signals(settings, "some text")
    assert result.error == "llm api key not configured"
    assert not called


async def test_empty_text_short_circuits():
    settings = simulation_settings()
    result = await extract_signals(settings, "   ")
    assert result.signals == []
    assert not result.error


async def test_non_200_degrades_to_an_error_result_without_raising(mock_client):
    mock_client(lambda request: httpx.Response(429, json={"error": "rate limited"}))
    settings = simulation_settings()
    result = await extract_signals(settings, "some text")
    assert result.error == "llm returned 429"
    assert result.signals == []


async def test_network_failure_degrades_to_an_error_result_without_raising(mock_client):
    def handler(request):
        raise httpx.ConnectTimeout("boom", request=request)

    mock_client(handler)
    settings = simulation_settings()
    result = await extract_signals(settings, "some text")
    assert "ConnectTimeout" in result.error


async def test_malformed_response_shape_degrades_without_raising(mock_client):
    mock_client(lambda request: httpx.Response(200, json={"unexpected": "shape"}))
    settings = simulation_settings()
    result = await extract_signals(settings, "some text")
    assert "unexpected llm response" in result.error
    assert result.signals == []


async def test_non_list_categories_field_degrades_without_raising(mock_client):
    mock_client(
        lambda request: httpx.Response(
            200,
            json={
                "choices": [
                    {"message": {"content": json.dumps({"categories": "not-a-list"})}},
                ]
            },
        )
    )
    settings = simulation_settings()
    result = await extract_signals(settings, "some text")
    assert "unexpected llm response" in result.error


async def test_generate_warning_returns_the_models_sentence(mock_client):
    mock_client(lambda request: _plain_text_response("Let's just double check this together."))
    settings = simulation_settings()
    result = await generate_warning(settings, "amber", ["secrecy"])
    assert result.text == "Let's just double check this together."
    assert not result.error


async def test_generate_warning_is_not_forced_into_json_mode(mock_client):
    seen = {}

    def handler(request):
        seen["body"] = json.loads(request.content)
        return _plain_text_response("One moment please.")

    mock_client(handler)
    settings = simulation_settings()
    await generate_warning(settings, "red", ["financial_request", "secrecy"])
    assert "response_format" not in seen["body"]
    assert json.loads(seen["body"]["messages"][1]["content"]) == {
        "risk_level": "red",
        "detected_signals": ["financial_request", "secrecy"],
    }


async def test_generate_warning_strips_wrapping_quotes(mock_client):
    mock_client(lambda request: _plain_text_response('"Let\'s pause for a moment."'))
    settings = simulation_settings()
    result = await generate_warning(settings, "critical", ["financial_request"])
    assert result.text == "Let's pause for a moment."


async def test_generate_warning_empty_reply_is_an_error(mock_client):
    mock_client(lambda request: _plain_text_response("   "))
    settings = simulation_settings()
    result = await generate_warning(settings, "amber", [])
    assert result.error == "empty warning text"
    assert result.text == ""


async def test_generate_warning_degrades_on_missing_key():
    settings = simulation_settings(llm_api_key="")
    result = await generate_warning(settings, "amber", ["urgency"])
    assert result.error == "llm api key not configured"
