"""End-to-end tests for the streaming stack: runner, bus, gateway, REST.

These exercise the real composition -- a live twin registry ticking inside the
app, publishing through the bus, reaching a WebSocket client -- rather than
mocking the pieces apart.
"""

from __future__ import annotations

import json
from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from at_api.main import create_app
from at_config import Profile, Settings


@pytest.fixture
def streaming_app() -> FastAPI:
    """App backed by synthetic telemetry so tests never need the dataset."""
    return create_app(
        Settings(
            profile=Profile.CI,
            service_name="api-test",
            log_level="WARNING",
            twin_synthetic=True,
            twin_subset="FD001",
            tick_hz=40.0,
            replay_speed=8.0,
        )
    )


@pytest.fixture
def live(streaming_app: FastAPI) -> Iterator[TestClient]:
    with TestClient(streaming_app) as client:
        yield client


# ── REST ─────────────────────────────────────────────────────────────────────


def test_fleet_endpoint_returns_engines(live: TestClient) -> None:
    body = live.get("/api/v1/fleet").json()
    assert body["total"] == 24
    assert len(body["items"]) == 24
    assert body["aggregates"]["engines"] == 24


def test_fleet_rows_carry_the_fields_the_ui_needs(live: TestClient) -> None:
    row = live.get("/api/v1/fleet").json()["items"][0]
    for field in (
        "engine_id",
        "tail_number",
        "unit_number",
        "cycle",
        "health_index",
        "health_band",
    ):
        assert field in row, f"fleet row missing {field}"


def test_fleet_is_sorted_worst_first_by_default(live: TestClient) -> None:
    scores = [row["health_index"] for row in live.get("/api/v1/fleet").json()["items"]]
    assert scores == sorted(scores)


def test_fleet_sort_order_can_be_reversed(live: TestClient) -> None:
    scores = [row["health_index"] for row in live.get("/api/v1/fleet?order=desc").json()["items"]]
    assert scores == sorted(scores, reverse=True)


def test_fleet_pagination(live: TestClient) -> None:
    body = live.get("/api/v1/fleet?page=1&size=5").json()
    assert len(body["items"]) == 5
    assert body["has_next"] is True


def test_fleet_band_filter(live: TestClient) -> None:
    body = live.get("/api/v1/fleet?band=HEALTHY").json()
    assert all(row["health_band"] == "HEALTHY" for row in body["items"])


def test_unknown_band_is_rejected_with_a_problem_document(live: TestClient) -> None:
    response = live.get("/api/v1/fleet?band=NOPE")
    assert response.status_code == 400
    body = response.json()
    assert body["code"] == "VALIDATION_FAILED"
    assert body["errors"][0]["field"] == "band"


def test_fleet_search_by_unit(live: TestClient) -> None:
    body = live.get("/api/v1/fleet?search=3").json()
    assert body["total"] >= 1


def test_fleet_summary_excludes_the_row_list(live: TestClient) -> None:
    body = live.get("/api/v1/fleet/summary").json()
    assert "engines_list" not in body
    assert body["engines"] == 24


def test_engine_detail_by_unit_number(live: TestClient) -> None:
    body = live.get("/api/v1/engines/1").json()
    assert body["unit_number"] == 1
    assert "components" in body
    assert "sensors" in body


def test_engine_detail_by_uuid(live: TestClient) -> None:
    engine_id = live.get("/api/v1/fleet").json()["items"][0]["engine_id"]
    assert live.get(f"/api/v1/engines/{engine_id}").json()["engine_id"] == engine_id


def test_unknown_engine_returns_problem_document(live: TestClient) -> None:
    response = live.get("/api/v1/engines/NOPE")
    assert response.status_code == 404
    assert response.json()["code"] == "ENGINE_NOT_FOUND"
    assert response.headers["content-type"].startswith("application/problem+json")


def test_system_endpoint_reports_tick_metrics(live: TestClient) -> None:
    body = live.get("/api/v1/system").json()
    assert body["engines"] == 24
    assert "tick_p99_ms" in body


def test_dashboard_is_served(live: TestClient) -> None:
    response = live.get("/dashboard")
    assert response.status_code == 200
    assert "AeroTwin" in response.text


# ── commands ─────────────────────────────────────────────────────────────────


def test_command_is_accepted_asynchronously(live: TestClient) -> None:
    """Commands return 202: the tick loop is the only writer (Doc 01 section 1.5)."""
    response = live.post("/api/v1/engines/1/commands/pause")
    assert response.status_code == 202
    assert response.json()["command"] == "PAUSE"


def test_unknown_command_is_rejected(live: TestClient) -> None:
    response = live.post("/api/v1/engines/1/commands/explode")
    assert response.status_code == 400
    assert response.json()["errors"][0]["field"] == "command"


def test_command_on_unknown_engine_is_404(live: TestClient) -> None:
    assert live.post("/api/v1/engines/99999/commands/pause").status_code == 404


# ── WebSocket ────────────────────────────────────────────────────────────────


def test_websocket_handshake_sends_welcome(live: TestClient) -> None:
    with live.websocket_connect("/ws/v1") as socket:
        welcome = socket.receive_json()
        assert welcome["type"] == "welcome"
        assert welcome["protocol"] == 1
        assert "connection_id" in welcome


def test_subscribe_is_answered_with_a_snapshot_before_deltas(live: TestClient) -> None:
    """A newly opened view must be correct immediately, not blank until a change."""
    with live.websocket_connect("/ws/v1") as socket:
        socket.receive_json()  # welcome
        socket.send_json({"type": "subscribe", "channels": ["fleet"]})

        confirmation = socket.receive_json()
        assert confirmation["type"] == "subscribed"
        assert confirmation["channels"] == ["fleet"]

        snapshot = socket.receive_json()
        assert snapshot["type"] == "fleet.snapshot"
        assert snapshot["payload"]["engines"] == 24
        assert len(snapshot["payload"]["engines_list"]) == 24


def test_deltas_arrive_after_the_snapshot(live: TestClient) -> None:
    with live.websocket_connect("/ws/v1") as socket:
        socket.receive_json()
        socket.send_json({"type": "subscribe", "channels": ["fleet"]})
        socket.receive_json()  # subscribed
        socket.receive_json()  # snapshot

        seen: set[str] = set()
        for _ in range(6):
            seen.add(socket.receive_json().get("type", ""))
        assert "fleet.delta" in seen


def test_heartbeat_round_trip(live: TestClient) -> None:
    with live.websocket_connect("/ws/v1") as socket:
        socket.receive_json()
        socket.send_json({"type": "ping", "seq": 99})
        assert socket.receive_json() == {"type": "pong", "seq": 99}


def test_twin_channel_delivers_engine_state(live: TestClient) -> None:
    engine_id = live.get("/api/v1/fleet").json()["items"][0]["engine_id"]
    with live.websocket_connect("/ws/v1") as socket:
        socket.receive_json()
        socket.send_json({"type": "subscribe", "channels": [f"twin:{engine_id}"]})
        socket.receive_json()  # subscribed
        snapshot = socket.receive_json()
        assert snapshot["type"] == "twin.snapshot"
        assert snapshot["payload"]["engine_id"] == engine_id


def test_unsubscribe_stops_the_channel(live: TestClient) -> None:
    with live.websocket_connect("/ws/v1") as socket:
        socket.receive_json()
        socket.send_json({"type": "subscribe", "channels": ["fleet"]})
        socket.receive_json()
        socket.receive_json()
        socket.send_json({"type": "unsubscribe", "channels": ["fleet"]})

        for _ in range(8):
            message = socket.receive_json()
            if message.get("type") == "unsubscribed":
                assert message["channels"] == ["fleet"]
                return
        pytest.fail("no unsubscribe confirmation received")


def test_malformed_frame_is_reported_not_fatal(live: TestClient) -> None:
    with live.websocket_connect("/ws/v1") as socket:
        socket.receive_json()
        socket.send_text("{not json")
        assert socket.receive_json()["type"] == "error"
        # Connection must survive a bad frame.
        socket.send_json({"type": "ping", "seq": 1})
        assert socket.receive_json()["type"] == "pong"


def test_unknown_frame_type_is_reported(live: TestClient) -> None:
    with live.websocket_connect("/ws/v1") as socket:
        socket.receive_json()
        socket.send_json({"type": "teleport"})
        assert "unknown frame type" in socket.receive_json()["message"]


def test_oversized_frame_closes_the_connection(live: TestClient) -> None:
    from starlette.websockets import WebSocketDisconnect

    with pytest.raises(WebSocketDisconnect), live.websocket_connect("/ws/v1") as socket:
        socket.receive_json()
        socket.send_text(json.dumps({"type": "ping", "pad": "x" * 9000}))
        socket.receive_json()


def test_multiple_clients_receive_the_same_stream(live: TestClient) -> None:
    with live.websocket_connect("/ws/v1") as first, live.websocket_connect("/ws/v1") as second:
        for socket in (first, second):
            socket.receive_json()
            socket.send_json({"type": "subscribe", "channels": ["fleet"]})
            assert socket.receive_json()["type"] == "subscribed"
            assert socket.receive_json()["type"] == "fleet.snapshot"
