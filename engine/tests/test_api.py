"""FastAPI engine API (docs/07 §1)."""

from __future__ import annotations

import threading
from collections.abc import Iterator
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from engine import jobs
from engine.api.main import create_app
from engine.config import get_settings
from engine.db.models import BacktestRun, JobRun, Slip, Team, TeamAlias, UnresolvedName
from engine.db.session import session_scope
from engine.jobs import JobContext, sync_leagues
from tests.fixtures.seed_demo import seed

HOST = {"host": "127.0.0.1:8765"}


@pytest.fixture
def client(migrated_db: Path) -> Iterator[TestClient]:
    app = create_app(get_settings(), migrated_db)
    with TestClient(app, base_url="http://127.0.0.1:8765") as c:
        yield c
    app.state.runner.join(5)


def _slip(db: Path, **kw: object) -> int:
    with session_scope(db) as s:
        slip = Slip(
            slip_type="daily_2odds",
            slip_date=date(2026, 9, 27),
            window_start_utc=datetime(2026, 9, 27, 12, tzinfo=UTC),
            window_end_utc=datetime(2026, 9, 28, 2, tzinfo=UTC),
            total_odds=2.1,
            p_all_win=0.5,
            expected_multiplier=1.05,
            model_version="dc-xg-v1",
            **kw,
        )
        s.add(slip)
        s.flush()
        return slip.id


def _gate(db: Path, passed: bool) -> None:
    with session_scope(db) as s:
        s.add(
            BacktestRun(
                started_at=datetime(2026, 9, 27, 10, tzinfo=UTC),
                finished_at=datetime(2026, 9, 27, 10, 1, tzinfo=UTC),
                config_json="{}",
                gate_passed=passed,
            )
        )


# ---- guards ---------------------------------------------------------------


def test_health_reports_gate(client: TestClient, migrated_db: Path) -> None:
    assert client.get("/health").json()["gate_passed"] is None
    _gate(migrated_db, False)
    body = client.get("/health").json()
    assert body["ok"] is True and body["gate_passed"] == 0 and body["job_running"] is False


def test_foreign_host_rejected(client: TestClient) -> None:
    assert client.get("/health", headers={"host": "192.168.1.5:8765"}).status_code == 403
    assert client.get("/health", headers={"host": "localhost:8765"}).status_code == 200


def test_cors_only_dashboard_origin(client: TestClient) -> None:
    ok = client.get("/health", headers={"origin": "http://localhost:3000"})
    assert ok.headers.get("access-control-allow-origin") == "http://localhost:3000"
    bad = client.get("/health", headers={"origin": "http://evil.example"})
    assert "access-control-allow-origin" not in bad.headers


# ---- jobs -----------------------------------------------------------------


def test_job_runs_in_background_and_is_recorded(
    client: TestClient, migrated_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake(ctx: JobContext) -> None:
        ctx.count("did", 3)

    monkeypatch.setitem(jobs.JOBS, "settle", fake)
    run_id = client.post("/jobs/settle").json()["job_run_id"]
    client.app.state.runner.join(5)  # type: ignore[attr-defined]
    body = client.get(f"/jobs/{run_id}").json()
    assert (body["job_name"], body["status"], body["summary"]) == ("settle", "success", {"did": 3})
    assert client.get("/jobs?limit=5").json()[0]["id"] == run_id


def test_second_job_while_running_is_409(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    release = threading.Event()

    def slow(ctx: JobContext) -> None:
        release.wait(5)

    monkeypatch.setitem(jobs.JOBS, "odds", slow)
    assert client.post("/jobs/odds").status_code == 200
    assert client.post("/jobs/picks").status_code == 409
    assert client.get("/health").json()["job_running"] is True
    release.set()
    client.app.state.runner.join(5)  # type: ignore[attr-defined]
    assert client.get("/health").json()["job_running"] is False


def test_failed_job_recorded(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    def broken(ctx: JobContext) -> None:
        raise RuntimeError("upstream exploded")

    monkeypatch.setitem(jobs.JOBS, "fit", broken)
    run_id = client.post("/jobs/fit").json()["job_run_id"]
    client.app.state.runner.join(5)  # type: ignore[attr-defined]
    body = client.get(f"/jobs/{run_id}").json()
    assert body["status"] == "failed" and "upstream exploded" in body["error"]


def test_daily_runs_steps_under_one_parent(
    client: TestClient, migrated_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for step in jobs.DAILY_SEQUENCE:
        monkeypatch.setitem(jobs.JOBS, step, lambda ctx: None)
    run_id = client.post("/jobs/daily").json()["job_run_id"]
    client.app.state.runner.join(5)  # type: ignore[attr-defined]
    body = client.get(f"/jobs/{run_id}").json()
    assert body["job_name"] == "daily" and body["status"] == "success"
    assert set(body["summary"]["steps"]) == set(jobs.DAILY_SEQUENCE)  # JSON keys are sorted
    with session_scope(migrated_db) as s:
        assert s.scalars(select(JobRun).where(JobRun.status == "running")).first() is None


def test_daily_continues_after_failed_ingest(
    client: TestClient, migrated_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ran: list[str] = []

    def blocked(ctx: JobContext) -> None:
        raise RuntimeError("source returned 429")

    for step in jobs.DAILY_SEQUENCE:
        monkeypatch.setitem(jobs.JOBS, step, lambda ctx: ran.append(ctx.name))
    monkeypatch.setitem(jobs.JOBS, "ingest", blocked)
    run_id = client.post("/jobs/daily").json()["job_run_id"]
    client.app.state.runner.join(5)  # type: ignore[attr-defined]
    body = client.get(f"/jobs/{run_id}").json()
    assert body["status"] == "success"
    assert ran == ["odds", "picks", "book"]
    ingest_id = body["summary"]["steps"]["ingest"]
    assert any(f"job run {ingest_id}" in w for w in body["summary"]["warnings"])
    ingest = client.get(f"/jobs/{ingest_id}").json()
    assert ingest["status"] == "failed" and "429" in ingest["error"]


def test_daily_stops_when_a_hard_step_fails(
    client: TestClient, migrated_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ran: list[str] = []

    def broken(ctx: JobContext) -> None:
        raise RuntimeError("odds exploded")

    for step in jobs.DAILY_SEQUENCE:
        monkeypatch.setitem(jobs.JOBS, step, lambda ctx: ran.append(ctx.name))
    monkeypatch.setitem(jobs.JOBS, "odds", broken)
    run_id = client.post("/jobs/daily").json()["job_run_id"]
    client.app.state.runner.join(5)  # type: ignore[attr-defined]
    body = client.get(f"/jobs/{run_id}").json()
    assert body["status"] == "failed" and "odds exploded" in body["error"]
    assert ran == ["ingest"]


@pytest.mark.parametrize("name", ["discover", "book-test", "nope"])
def test_cli_only_or_unknown_jobs_404(client: TestClient, name: str) -> None:
    assert client.post(f"/jobs/{name}").status_code == 404


def test_unknown_job_run_404(client: TestClient) -> None:
    assert client.get("/jobs/999").status_code == 404


# ---- slips ----------------------------------------------------------------


def test_placed_requires_confirmation_without_edge(client: TestClient, migrated_db: Path) -> None:
    sid = _slip(migrated_db)
    _gate(migrated_db, False)
    r = client.patch(f"/slips/{sid}/mode", json={"mode": "placed", "stake": 500})
    assert r.status_code == 400 and "not demonstrated an edge" in r.json()["detail"]
    r = client.patch(
        f"/slips/{sid}/mode", json={"mode": "placed", "stake": 500, "confirm_no_edge": True}
    )
    assert r.status_code == 200 and (r.json()["mode"], r.json()["stake"]) == ("placed", 500)
    r = client.patch(f"/slips/{sid}/mode", json={"mode": "paper"})
    assert (r.json()["mode"], r.json()["stake"]) == ("paper", None)


def test_placed_without_any_backtest_also_needs_confirmation(
    client: TestClient, migrated_db: Path
) -> None:
    sid = _slip(migrated_db)
    assert client.patch(f"/slips/{sid}/mode", json={"mode": "placed"}).status_code == 400


def test_placed_allowed_when_gate_passed(client: TestClient, migrated_db: Path) -> None:
    sid = _slip(migrated_db)
    _gate(migrated_db, True)
    assert client.patch(f"/slips/{sid}/mode", json={"mode": "placed"}).status_code == 200


@pytest.mark.parametrize(
    "body",
    [{"mode": "live"}, {"mode": "placed", "stake": -5}, {"mode": "paper", "extra": 1}],
)
def test_mode_body_validated(
    client: TestClient, migrated_db: Path, body: dict[str, object]
) -> None:
    sid = _slip(migrated_db)
    assert client.patch(f"/slips/{sid}/mode", json=body).status_code == 422


def test_settled_slip_is_read_only(client: TestClient, migrated_db: Path) -> None:
    sid = _slip(migrated_db, status="won", return_multiplier=2.1)
    _gate(migrated_db, True)
    assert client.patch(f"/slips/{sid}/mode", json={"mode": "placed"}).status_code == 409
    assert client.patch("/slips/999/mode", json={"mode": "paper"}).status_code == 404


def test_manual_booking_code(client: TestClient, migrated_db: Path) -> None:
    sid = _slip(migrated_db, booking_status="failed", booking_error="odds moved")
    r = client.patch(f"/slips/{sid}/booking", json={"booking_code": " x6yp10 "})
    assert r.status_code == 200
    assert (r.json()["booking_code"], r.json()["booking_status"]) == ("X6YP10", "manual")
    assert client.patch(f"/slips/{sid}/booking", json={"booking_code": "ab"}).status_code == 422
    assert client.patch(f"/slips/{sid}/booking", json={"booking_code": "AB-12;"}).status_code == 422


def test_rebook_resets_pending_and_books_only_that_slip(
    client: TestClient, migrated_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sid = _slip(migrated_db, booking_status="failed", booking_error="odds moved")
    seen: list[object] = []

    def fake_book(ctx: JobContext) -> None:
        seen.append(ctx.params.get("slip_id"))

    monkeypatch.setitem(jobs.JOBS, "book", fake_book)
    r = client.post(f"/slips/{sid}/rebook")
    assert r.status_code == 200
    client.app.state.runner.join(5)  # type: ignore[attr-defined]
    assert seen == [sid]
    with session_scope(migrated_db) as s:
        slip = s.get_one(Slip, sid)
        assert (slip.booking_status, slip.booking_error) == ("pending", None)


def test_rebook_refused_for_placed_slip(client: TestClient, migrated_db: Path) -> None:
    sid = _slip(migrated_db, mode="placed", booking_status="booked", booking_code="AAAA11")
    assert client.post(f"/slips/{sid}/rebook").status_code == 409


# ---- aliases --------------------------------------------------------------


def _team(db: Path) -> int:
    with session_scope(db) as s:
        sync_leagues(s, get_settings())
        s.flush()
        from engine.db.queries import league_by_key

        team = Team(league_id=league_by_key(s, "EPL").id, canonical_name="Arsenal")
        s.add(team)
        s.add(
            UnresolvedName(
                source="sportybet",
                raw_name="Arsenal FC",
                league_key="EPL",
                first_seen=datetime(2026, 9, 27, tzinfo=UTC),
                last_seen=datetime(2026, 9, 27, tzinfo=UTC),
            )
        )
        s.flush()
        return team.id


def test_alias_approval_clears_unresolved(client: TestClient, migrated_db: Path) -> None:
    tid = _team(migrated_db)
    r = client.post("/aliases", json={"source": "sportybet", "alias": "Arsenal FC", "team_id": tid})
    assert r.status_code == 200 and r.json()["unresolved_cleared"] == 1
    with session_scope(migrated_db) as s:
        assert s.scalars(select(UnresolvedName)).first() is None
        assert s.scalars(select(TeamAlias.alias)).all() == ["Arsenal FC"]
    dup = client.post(
        "/aliases", json={"source": "sportybet", "alias": "Arsenal FC", "team_id": tid}
    )
    assert dup.status_code == 409


@pytest.mark.parametrize(
    ("body", "code"),
    [
        ({"source": "bet9ja", "alias": "X", "team_id": 1}, 422),
        ({"source": "sportybet", "alias": "  ", "team_id": 1}, 422),
        ({"source": "sportybet", "alias": "X", "team_id": 999}, 404),
    ],
)
def test_alias_validation(client: TestClient, body: dict[str, object], code: int) -> None:
    assert client.post("/aliases", json=body).status_code == code


# ---- performance + demo seed ---------------------------------------------


def test_calibration_empty(client: TestClient) -> None:
    body = client.get("/performance/calibration").json()
    assert body["n"] == 0 and body["ece"] is None and len(body["reliability"]) == 10


def test_demo_seed_and_calibration(client: TestClient, migrated_db: Path) -> None:
    counts = seed(migrated_db)
    assert counts["matches"] > 0 and counts["slips"] > 0
    with session_scope(migrated_db) as s:
        names = s.scalars(select(Team.canonical_name)).all()
        assert names and all(n.startswith("DEMO") for n in names)
        assert {v for v in s.scalars(select(Slip.model_version))} == {"demo"}
        # A slip is settled only when none of its legs is pending.
        for slip in s.scalars(select(Slip)):
            if any(leg.result == "pending" for leg in slip.legs):
                assert slip.status in ("open", "lost"), slip.id
    body = client.get("/performance/calibration").json()
    assert body["n"] > 0 and 0 <= body["ece"] <= 1
    assert sum(b["count"] for b in body["reliability"]) == body["n"]
    with pytest.raises(SystemExit, match="already has matches"):
        seed(migrated_db)
