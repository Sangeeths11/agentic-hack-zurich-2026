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
        assert config["missing"] == ["GRADIUM_API_KEY", "LLM_API_KEY"]
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
                assert ws.receive_json()["event"] == "ready"
