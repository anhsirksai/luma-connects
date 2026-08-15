from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from invite_finder import auth, db
from invite_finder.api import deps
from invite_finder.api.app import app
from invite_finder.config import Settings
from invite_finder.conversation import ConversationDeps
from invite_finder.runner import RunManager
from invite_finder.store import auth_store


class FakeLinq:
    def __init__(self, enabled=True):
        self.sent: list[tuple[str, str]] = []
        self.enabled = enabled

    def send_message(self, to, text):
        self.sent.append((",".join(to), text))
        return {}

    def send_to_chat(self, chat_id, text):
        self.sent.append((chat_id, text))
        return {}

    @property
    def last(self) -> str:
        return self.sent[-1][1] if self.sent else ""


@pytest.fixture()
def ctx(tmp_path):
    db_path = str(tmp_path / "auth.db")
    conn = db.connect(db_path)
    linq = FakeLinq()
    state = {"settings": Settings(
        invite_db_path=db_path,
        invite_offline=True,
        admin_phone="+14155550000",
        linq_api_key="test-key",
    )}

    app.dependency_overrides[deps.get_settings] = lambda: state["settings"]
    app.dependency_overrides[deps.get_conn] = lambda: conn
    app.dependency_overrides[deps.get_linq_client] = lambda: linq
    app.dependency_overrides[deps.get_conversation_deps] = lambda: ConversationDeps(
        settings=state["settings"],
        conn_factory=lambda: db.connect(db_path),
        run_manager=RunManager(lambda: db.connect(db_path)),
        build_client=lambda *a, **k: None,
        linq=linq,
    )

    yield {"client": TestClient(app), "conn": conn, "linq": linq, "state": state}

    app.dependency_overrides.clear()
    conn.close()


def login(ctx) -> str:
    """Full round trip: request a code, read it out of the text, exchange it."""
    ctx["client"].post("/api/auth/request-code")
    code = "".join(c for c in ctx["linq"].last.split("passcode:")[1][:10] if c.isdigit())
    response = ctx["client"].post("/api/auth/verify", json={"code": code})
    return response.json()["token"]


# --- the gate ----------------------------------------------------------------


def test_data_routes_are_closed_without_a_session(ctx) -> None:
    for path in ("/api/events", "/api/events/1", "/api/events/1/people", "/api/runs/1"):
        response = ctx["client"].get(path)
        assert response.status_code == 401, path
        assert response.json()["error"]["code"] == "http_error"


def test_full_passcode_round_trip_opens_the_gate(ctx) -> None:
    assert ctx["client"].get("/api/events").status_code == 401

    response = ctx["client"].post("/api/auth/request-code")
    assert response.status_code == 200
    assert response.json()["sent"] is True

    # The code goes to the operator's number and nowhere else.
    recipient, message = ctx["linq"].sent[-1]
    assert recipient == "+14155550000"
    assert "admin passcode" in message.lower()

    code = "".join(c for c in message.split("passcode:")[1][:10] if c.isdigit())
    assert len(code) == 6

    token = ctx["client"].post("/api/auth/verify", json={"code": code}).json()["token"]
    assert ctx["client"].get(
        "/api/events", headers={"Authorization": f"Bearer {token}"}
    ).status_code == 200


def test_x_admin_token_header_also_works(ctx) -> None:
    token = login(ctx)
    assert ctx["client"].get(
        "/api/events", headers={"X-Admin-Token": token}
    ).status_code == 200


def test_wrong_expired_and_absent_codes_are_indistinguishable(ctx) -> None:
    ctx["client"].post("/api/auth/request-code")
    wrong = ctx["client"].post("/api/auth/verify", json={"code": "000000"})
    never = ctx["client"].post("/api/auth/verify", json={"code": "123456"})
    assert wrong.status_code == never.status_code == 401
    assert wrong.json()["error"]["message"] == never.json()["error"]["message"]


def test_a_code_cannot_be_used_twice(ctx) -> None:
    ctx["client"].post("/api/auth/request-code")
    message = ctx["linq"].last
    code = "".join(c for c in message.split("passcode:")[1][:10] if c.isdigit())

    assert ctx["client"].post("/api/auth/verify", json={"code": code}).status_code == 200
    # Replay of a code seen in transit must fail.
    assert ctx["client"].post("/api/auth/verify", json={"code": code}).status_code == 401


def test_requesting_a_new_code_invalidates_the_previous_one(ctx) -> None:
    ctx["client"].post("/api/auth/request-code")
    first = "".join(c for c in ctx["linq"].last.split("passcode:")[1][:10] if c.isdigit())

    # Backdate the request so the rate limiter lets a second code through;
    # we're exercising invalidation here, not throttling.
    old = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
    ctx["conn"].execute("UPDATE admin_passcodes SET created_at = ?", (old,))
    ctx["conn"].commit()
    issued = auth.issue_passcode(ctx["conn"])

    assert ctx["client"].post("/api/auth/verify", json={"code": first}).status_code == 401
    assert ctx["client"].post(
        "/api/auth/verify", json={"code": issued.code}
    ).status_code == 200


def test_brute_force_burns_the_code(ctx) -> None:
    ctx["client"].post("/api/auth/request-code")
    real = "".join(c for c in ctx["linq"].last.split("passcode:")[1][:10] if c.isdigit())

    for _ in range(auth.MAX_ATTEMPTS_PER_CODE + 1):
        ctx["client"].post("/api/auth/verify", json={"code": "999999"})

    # Even the correct code is dead once it has been guessed at too often.
    assert ctx["client"].post("/api/auth/verify", json={"code": real}).status_code == 401


def test_code_requests_are_rate_limited(ctx) -> None:
    assert ctx["client"].post("/api/auth/request-code").status_code == 200
    second = ctx["client"].post("/api/auth/request-code")
    # Otherwise anyone who finds the URL can ring the operator's phone forever.
    assert second.status_code == 429
    assert len(ctx["linq"].sent) == 1


def test_failed_delivery_does_not_lock_the_operator_out(ctx) -> None:
    """A code nobody received must leave no trace — otherwise a transient Linq
    failure costs you the rate-limit window and kills the previous code too."""
    from invite_finder.linq import LinqError

    class BrokenLinq(FakeLinq):
        def send_message(self, to, text):
            raise LinqError("Linq /messages failed with 401")

    broken = BrokenLinq()
    app.dependency_overrides[deps.get_linq_client] = lambda: broken

    assert ctx["client"].post("/api/auth/request-code").status_code == 502
    assert ctx["conn"].execute(
        "SELECT COUNT(*) n FROM admin_passcodes"
    ).fetchone()["n"] == 0

    # Immediately retrying must work, not hit the throttle.
    app.dependency_overrides[deps.get_linq_client] = lambda: ctx["linq"]
    assert ctx["client"].post("/api/auth/request-code").status_code == 200


def test_network_failures_roll_back_too_not_just_linq_errors(ctx) -> None:
    """A refused connection or timeout raises requests' exceptions, not
    LinqError. Caught in a real smoke test: those escaped as a 500 and left an
    undelivered code holding the rate-limit window."""
    import requests

    class UnreachableLinq(FakeLinq):
        def send_message(self, to, text):
            raise requests.exceptions.ConnectionError("connection refused")

    app.dependency_overrides[deps.get_linq_client] = lambda: UnreachableLinq()

    response = ctx["client"].post("/api/auth/request-code")
    assert response.status_code == 502, "must not surface as an unhandled 500"
    assert ctx["conn"].execute(
        "SELECT COUNT(*) n FROM admin_passcodes"
    ).fetchone()["n"] == 0

    app.dependency_overrides[deps.get_linq_client] = lambda: ctx["linq"]
    assert ctx["client"].post("/api/auth/request-code").status_code == 200


def test_no_code_is_minted_when_delivery_is_impossible(ctx) -> None:
    ctx["state"]["settings"] = Settings(
        invite_db_path=ctx["state"]["settings"].invite_db_path,
        invite_offline=True,
        admin_phone="+14155550000",
        linq_api_key="",          # nothing can be delivered
    )
    app.dependency_overrides[deps.get_linq_client] = lambda: FakeLinq(enabled=False)

    assert ctx["client"].post("/api/auth/request-code").status_code == 503
    assert ctx["conn"].execute(
        "SELECT COUNT(*) n FROM admin_passcodes"
    ).fetchone()["n"] == 0


def test_logout_revokes_the_session(ctx) -> None:
    token = login(ctx)
    headers = {"Authorization": f"Bearer {token}"}
    assert ctx["client"].get("/api/events", headers=headers).status_code == 200

    ctx["client"].post("/api/auth/logout", headers=headers)
    assert ctx["client"].get("/api/events", headers=headers).status_code == 401


def test_expired_sessions_are_rejected(ctx) -> None:
    ctx["state"]["settings"] = Settings(
        invite_db_path=ctx["state"]["settings"].invite_db_path,
        invite_offline=True,
        admin_phone="+14155550000",
        linq_api_key="test-key",
        admin_session_ttl_hours=-1,   # already expired on issue
    )
    token = login(ctx)
    assert ctx["client"].get(
        "/api/events", headers={"Authorization": f"Bearer {token}"}
    ).status_code == 401


# --- what must stay reachable ------------------------------------------------


def test_webhooks_are_not_gated(ctx) -> None:
    """Stripe and Linq call machine-to-machine and cannot present a passcode.
    Gating them would silently stop all payments."""
    stripe = ctx["client"].post(
        "/api/webhooks/stripe",
        json={"type": "payment_intent.created", "data": {"object": {}}},
    )
    linq = ctx["client"].post(
        "/api/webhooks/linq",
        json={"event_type": "message.delivered", "data": {}},
    )
    assert stripe.status_code == 200
    assert linq.status_code == 200


def test_health_is_open_and_reports_the_gate(ctx) -> None:
    body = ctx["client"].get("/api/health").json()
    assert body["admin_auth"] == "on"


def test_gate_is_open_when_no_admin_phone_is_configured(ctx) -> None:
    """No phone means no way to deliver a passcode, so enforcing it would lock
    the operator out of their own API. Health must admit this."""
    ctx["state"]["settings"] = Settings(
        invite_db_path=ctx["state"]["settings"].invite_db_path,
        invite_offline=True,
        admin_phone="",
    )
    assert ctx["client"].get("/api/events").status_code == 200
    assert ctx["client"].get("/api/health").json()["admin_auth"] == "off"
    assert ctx["client"].post("/api/auth/request-code").status_code == 503


# --- unit level --------------------------------------------------------------


def test_only_hashes_are_persisted(ctx) -> None:
    issued = auth.issue_passcode(ctx["conn"])
    token = auth.start_session(ctx["conn"], ctx["state"]["settings"])

    dump = " ".join(
        str(tuple(r)) for r in ctx["conn"].execute("SELECT * FROM admin_passcodes")
    ) + " ".join(
        str(tuple(r)) for r in ctx["conn"].execute("SELECT * FROM admin_sessions")
    )
    # A stolen database must not yield a working credential.
    assert issued.code not in dump
    assert token not in dump


def test_bearer_parsing_tolerates_a_bare_token() -> None:
    assert auth.bearer_from_header("Bearer abc123") == "abc123"
    assert auth.bearer_from_header("bearer abc123") == "abc123"
    assert auth.bearer_from_header("abc123") == "abc123"
    assert auth.bearer_from_header(None) == ""


def test_generated_codes_are_six_digits() -> None:
    for _ in range(50):
        code = auth.generate_code()
        assert len(code) == 6 and code.isdigit()
