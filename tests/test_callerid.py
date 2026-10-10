import httpx
import pytest

from familyshield.callerid import lookup_caller_id
from familyshield.simulation import simulation_settings


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


async def test_successful_lookup_returns_line_type_and_carrier(mock_client):
    def handler(request):
        assert request.url.path == "/v2/PhoneNumbers/+41790000000"
        return httpx.Response(
            200,
            json={
                "line_type_intelligence": {"type": "mobile", "carrier_name": "Swisscom"},
                "caller_name": {"caller_name": "Anna M."},
            },
        )

    mock_client(handler)
    result = await lookup_caller_id(simulation_settings(), "+41790000000")
    assert result.line_type == "mobile"
    assert result.carrier_name == "Swisscom"
    assert result.caller_name == "Anna M."
    assert not result.error


async def test_non_200_degrades_to_an_error_result_without_raising(mock_client):
    mock_client(lambda request: httpx.Response(404, json={"message": "not found"}))
    result = await lookup_caller_id(simulation_settings(), "+41790000000")
    assert result.error == "lookup returned 404"
    assert result.line_type == ""


async def test_network_failure_degrades_to_an_error_result_without_raising(mock_client):
    def handler(request):
        raise httpx.ConnectTimeout("boom", request=request)

    mock_client(handler)
    result = await lookup_caller_id(simulation_settings(), "+41790000000")
    assert "ConnectTimeout" in result.error


async def test_missing_phone_number_short_circuits():
    result = await lookup_caller_id(simulation_settings(), "")
    assert result.error == "no phone number"
