import json

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect
from support import (
    LOCAL_HEADERS,
    LOCAL_URL,
    DemoFactory,
    simulation_settings,
    start_message,
)

from scam_the_scamers.config import Settings
from scam_the_scamers.main import create_app


def test_no_keys_still_serves_ui_and_liveness_but_cannot_start_provider():
    with TestClient(create_app(Settings(_env_file=None)), base_url=LOCAL_URL) as client:
        assert client.get("/").status_code == 200
        assert client.get("/assets/listener.js").status_code == 200
        assert client.get("/assets/capture-worklet.js").status_code == 200
        assert client.get("/healthz").status_code == 200
        assert client.get("/readyz").status_code == 503
        config = client.get("/api/config").json()
        assert config["missing"] == ["GRADIUM_API_KEY", "GEMINI_API_KEY or LLM_API_KEY"]
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect(
                "ws://localhost:8082/audio/stream", headers=LOCAL_HEADERS
            ):
                pass


def test_config_does_not_expose_secrets():
    with TestClient(create_app(simulation_settings()), base_url=LOCAL_URL) as client:
        assert client.get("/readyz").status_code == 200
        response = client.get("/api/config")
        assert response.json()["configured"]
        assert "offline" not in response.text
        assert "frame-ancestors 'none'" in client.get("/").headers["content-security-policy"]


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"origin": "https://evil.example"},
        {"origin": "null"},
        {"origin": "http://localhost:9999"},
        {"origin": "http://localhost:8082.evil.example"},
        {"origin": "http://localhost:8082/path"},
        {"origin": "https://evil.example", "x-forwarded-host": "evil.example"},
    ],
)
def test_foreign_or_missing_origin_is_rejected(headers):
    factory = DemoFactory()
    with TestClient(create_app(simulation_settings(), factory), base_url=LOCAL_URL) as client:
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect("ws://localhost:8082/audio/stream", headers=headers):
                pass
    assert not factory.sessions


def test_nonlocal_host_is_rejected():
    with TestClient(create_app(simulation_settings()), base_url="http://evil.example") as client:
        assert client.get("/").status_code == 400


@pytest.mark.parametrize(
    "message",
    [
        {},
        [],
        {"event": "start", "encoding": "pcm_s16le", "sample_rate": 8000, "channels": 1},
        {"event": "start", "encoding": "pcm_s16le", "sample_rate": 24000, "channels": 2},
    ],
)
def test_invalid_start_does_not_open_provider(message):
    factory = DemoFactory()
    app = create_app(simulation_settings(), factory)
    with TestClient(app, base_url=LOCAL_URL) as client:
        with client.websocket_connect(
            "ws://localhost:8082/audio/stream", headers=LOCAL_HEADERS
        ) as ws:
            ws.send_json(message)
            assert ws.receive()["code"] == 1008
    assert not factory.sessions
    assert not app.state.registry.sockets


def test_same_origin_localhost_and_loopback_work():
    for url in (LOCAL_URL, "http://127.0.0.1:8082"):
        with TestClient(create_app(simulation_settings(), DemoFactory()), base_url=url) as client:
            with client.websocket_connect(
                url.replace("http:", "ws:") + "/audio/stream", headers={"origin": url}
            ) as ws:
                ws.send_json(start_message())
                while ws.receive_json()["event"] != "ready":
                    pass


def test_email_recipient_is_local_configuration_not_a_browser_relay():
    from test_providers import email_settings

    settings = email_settings()
    factory = DemoFactory(settings)
    with TestClient(create_app(settings, factory), base_url=LOCAL_URL) as client:
        response = client.get("/api/config")
        assert response.headers["cache-control"] == "no-store"
        assert response.json()["fallback_email"] == "caregiver@example.com"
        assert "fake-resend" not in response.text
        with client.websocket_connect(
            "ws://localhost:8082/audio/stream", headers=LOCAL_HEADERS
        ) as ws:
            ws.send_json({**start_message(), "recipient": "another@example.com"})
            assert ws.receive()["code"] == 1008
    assert not factory.sessions


def test_websocket_query_parameters_are_rejected():
    with TestClient(create_app(simulation_settings()), base_url=LOCAL_URL) as client:
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect(
                "ws://localhost:8082/audio/stream?recipient=x", headers=LOCAL_HEADERS
            ):
                pass


@pytest.mark.parametrize(
    ("number", "normalized"),
    [
        ("+12013759200", "+12013759200"),
        ("+1 (201) 375-9200", "+12013759200"),
        ("001 201 375 9200", "+12013759200"),
        ("+22375888", "+22375888"),
        ("+555123456", "+555123456"),
    ],
)
def test_caller_lookup_warns_for_preserved_database_number_without_cloud_keys(
    number, normalized, caplog
):
    factory = DemoFactory()
    with TestClient(create_app(Settings(_env_file=None), factory), base_url=LOCAL_URL) as client:
        response = client.post(
            "/api/caller-lookup", json={"caller_number": number}, headers=LOCAL_HEADERS
        )
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    caller = response.json()["caller"]
    assert caller["status"] == "matched"
    assert caller["number"] == normalized
    assert caller["source"] == "shalom_karr_ai_blocklist"
    assert caller["listed_at"]
    assert number not in caplog.text
    assert not factory.sessions


@pytest.mark.parametrize(
    ("number", "expected"),
    [("", "not_provided"), ("not a number", "invalid"), ("079 999 99 99", "no_match")],
)
def test_caller_lookup_preserves_unknown_states(number, expected):
    with TestClient(create_app(Settings(_env_file=None)), base_url=LOCAL_URL) as client:
        response = client.post(
            "/api/caller-lookup", json={"caller_number": number}, headers=LOCAL_HEADERS
        )
    assert response.json()["caller"]["status"] == expected
    if expected == "no_match":
        assert response.json()["caller"]["number"] == "+41799999999"


def test_caller_lookup_reports_unavailable_database(tmp_path):
    settings = Settings(_env_file=None, scam_db_path=tmp_path / "missing.db")
    with TestClient(create_app(settings), base_url=LOCAL_URL) as client:
        response = client.post(
            "/api/caller-lookup",
            json={"caller_number": "+12013759200"},
            headers=LOCAL_HEADERS,
        )
    assert response.json()["caller"]["status"] == "unavailable"


@pytest.mark.parametrize(
    "payload",
    [
        None,
        [],
        {},
        {"caller_number": 123},
        {"caller_number": "1" * 65},
        {"caller_number": "", "extra": True},
    ],
)
def test_caller_lookup_rejects_invalid_payload(payload):
    with TestClient(create_app(Settings(_env_file=None)), base_url=LOCAL_URL) as client:
        response = client.post(
            "/api/caller-lookup",
            content=json.dumps(payload),
            headers={**LOCAL_HEADERS, "content-type": "application/json"},
        )
    assert response.status_code == 422
    assert response.headers["cache-control"] == "no-store"


def test_caller_lookup_bounds_body_and_rejects_foreign_origin():
    with TestClient(create_app(Settings(_env_file=None)), base_url=LOCAL_URL) as client:
        assert (
            client.post(
                "/api/caller-lookup", json={"caller_number": "1" * 2000}, headers=LOCAL_HEADERS
            ).status_code
            == 413
        )
        for origin in [None, "https://evil.example", "http://localhost:8082/path"]:
            assert (
                client.post(
                    "/api/caller-lookup",
                    json={"caller_number": "+12013759200"},
                    headers={"origin": origin} if origin else {},
                ).status_code
                == 403
            )
        assert (
            client.post(
                "/api/caller-lookup", data={"caller_number": "+12013759200"}, headers=LOCAL_HEADERS
            ).status_code
            == 415
        )
