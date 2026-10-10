import time
from xml.etree import ElementTree

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from starlette.websockets import WebSocketDisconnect

from familyshield.config import Settings
from familyshield.main import create_app
from familyshield.security import stream_ticket, valid_ticket
from familyshield.simulation import (
    ACCOUNT_SID,
    CALL_SID,
    DemoFactory,
    signature,
    simulation_settings,
    start_message,
)


def test_no_keys_fails_closed_but_liveness_works():
    with TestClient(create_app(Settings(_env_file=None))) as client:
        assert client.get("/healthz").status_code == 200
        assert client.get("/readyz").status_code == 503
        assert client.post("/twilio/voice").status_code == 503
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect("/twilio/stream"):
                pass


def test_signed_webhook_produces_stream_and_fallback():
    settings = simulation_settings()
    params = {"AccountSid": ACCOUNT_SID, "CallSid": CALL_SID}
    with TestClient(create_app(settings, DemoFactory())) as client:
        response = client.post(
            "/twilio/voice", data=params, headers=signature(settings, "/twilio/voice", params)
        )
        assert response.status_code == 200
        tree = ElementTree.fromstring(response.text)
        stream = tree.find("./Connect/Stream")
        assert stream.attrib["url"] == settings.websocket_url
        assert valid_ticket(settings, CALL_SID, stream.find("Parameter").attrib["value"])
        assert tree.find("Dial") is None
        assert tree.find("Say") is not None
        assert tree.find("Hangup") is not None
        assert client.get("/readyz").status_code == 200


def test_forged_proxy_headers_and_changed_form_do_not_pass():
    settings = simulation_settings()
    params = {"AccountSid": ACCOUNT_SID, "CallSid": CALL_SID}
    with TestClient(create_app(settings, DemoFactory())) as client:
        headers = signature(settings, "/twilio/voice", params)
        params["CallSid"] = "CA" + "9" * 32
        headers.update({"x-forwarded-host": "attacker.example", "x-forwarded-proto": "https"})
        assert client.post("/twilio/voice", data=params, headers=headers).status_code == 403
        assert client.post("/twilio/voice", data=params).status_code == 403
        assert client.post("/twilio/status", data=params).status_code == 403


def test_callback_is_authenticated_and_idempotent():
    settings = simulation_settings()
    params = {"AccountSid": ACCOUNT_SID, "CallSid": CALL_SID, "CallStatus": "completed"}
    with TestClient(create_app(settings, DemoFactory())) as client:
        for _ in range(2):
            assert (
                client.post(
                    "/twilio/status",
                    data=params,
                    headers=signature(settings, "/twilio/status", params),
                ).status_code
                == 204
            )


@pytest.mark.parametrize("ws_scheme", [True, False])
def test_websocket_accepts_signed_canonical_upgrade_and_stream_urls(ws_scheme):
    settings = simulation_settings()
    with TestClient(create_app(settings, DemoFactory())) as client:
        with client.websocket_connect(
            "/twilio/stream",
            headers=signature(
                settings,
                "/twilio/stream",
                websocket=ws_scheme,
            ),
        ) as ws:
            ws.send_json(start_message(stream_ticket(settings, CALL_SID)))
            assert ws.receive_json()["event"] == "media"


@pytest.mark.parametrize("mutation", ["account", "ticket", "format", "stream", "call"])
def test_invalid_start_is_rejected_before_provider_is_opened(mutation):
    settings = simulation_settings()
    factory = DemoFactory()
    message = start_message(stream_ticket(settings, CALL_SID))
    if mutation == "account":
        message["start"]["accountSid"] = "AC" + "7" * 32
    elif mutation == "ticket":
        message["start"]["customParameters"]["ticket"] = "bad"
    elif mutation == "format":
        message["start"]["mediaFormat"]["sampleRate"] = 24000
    elif mutation == "stream":
        message["streamSid"] = "MZ" + "9" * 32
    else:
        message["start"]["callSid"] = "CA" + "4" * 32
    app = create_app(settings, factory)
    with TestClient(app) as client:
        with client.websocket_connect(
            "/twilio/stream",
            headers=signature(
                settings,
                "/twilio/stream",
                websocket=True,
            ),
        ) as ws:
            ws.send_json(message)
            assert ws.receive()["code"] == 1008
        assert not factory.sessions
        assert not app.state.registry.sockets


def test_tickets_expire_and_are_bound_to_call():
    settings = simulation_settings()
    ticket = stream_ticket(settings, CALL_SID, int(time.time()) - 1)
    assert not valid_ticket(settings, CALL_SID, ticket)
    ticket = stream_ticket(settings, CALL_SID)
    assert not valid_ticket(settings, "CA" + "9" * 32, ticket)
    assert not valid_ticket(settings, CALL_SID, ticket + "x")


@pytest.mark.parametrize(
    "url",
    [
        "http://public.example",
        "https://example.com/path",
        "https://example.com?token=x",
        "https://user:pass@example.com",
    ],
)
def test_public_origin_must_be_unambiguous_https(url):
    with pytest.raises(ValidationError):
        simulation_settings(public_http_url=url)
