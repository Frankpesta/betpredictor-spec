"""football-data.co.uk parsing + upsert, offline (saved sample CSVs, docs/02 §1)."""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

import httpx
import pytest
from sqlalchemy import func, select

from engine.config import Settings, get_settings
from engine.db.models import HistoricalOdds, League, Match, Team
from engine.db.session import session_scope
from engine.ingest import football_data as fd
from engine.ingest.fetch import PoliteClient
from engine.jobs import job_run, sync_leagues

FIXTURES = Path(__file__).parent / "fixtures" / "football_data"
E0_2627 = (FIXTURES / "E0_2627_sample.csv").read_bytes()
E0_1920 = (FIXTURES / "E0_1920_sample.csv").read_bytes()

HEADER = "Div,Date,Time,HomeTeam,AwayTeam,FTHG,FTAG,B365>2.5,B365<2.5,AHh,B365AHH,B365AHA\n"


def _odds(row: fd.FdRow) -> dict[tuple[str, str, str, str], tuple[float, float]]:
    return {(o.bookmaker, o.timing, o.market, o.selection): (o.line, o.odds) for o in row.odds}


# ---- pure helpers ---------------------------------------------------------


@pytest.mark.parametrize(
    ("season", "code"), [("2024-25", "2425"), ("2019-20", "1920"), ("2099-00", "9900")]
)
def test_season_code(season: str, code: str) -> None:
    assert fd.season_code(season) == code


def test_row_hash_is_sha256_of_raw_identity() -> None:
    import hashlib

    expect = hashlib.sha256(b"E0|21/08/2026|Arsenal|Coventry").hexdigest()
    assert fd.row_hash("E0", "21/08/2026", "Arsenal", "Coventry") == expect


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("1.95", 1.95),
        ("1.0", None),
        ("0.5", None),
        ("", None),
        ("abc", None),
        (None, None),
        (" 2 ", 2.0),
    ],
)
def test_parse_odds(raw: str | None, expected: float | None) -> None:
    assert fd.parse_odds(raw) == expected


@pytest.mark.parametrize(
    ("d", "t", "expected", "missing"),
    [
        # BST (UTC+1)
        ("21/08/2026", "20:00", datetime(2026, 8, 21, 19, 0, tzinfo=UTC), False),
        # GMT (UTC+0)
        ("14/12/2019", "12:30", datetime(2019, 12, 14, 12, 30, tzinfo=UTC), False),
        # two-digit year
        ("14/12/19", "12:30", datetime(2019, 12, 14, 12, 30, tzinfo=UTC), False),
        # no Time -> 15:00 UK
        ("14/12/2019", "", datetime(2019, 12, 14, 15, 0, tzinfo=UTC), True),
        ("10/08/2019", None, datetime(2019, 8, 10, 14, 0, tzinfo=UTC), True),
    ],
)
def test_parse_kickoff_uk_local_to_utc(
    d: str, t: str | None, expected: datetime, missing: bool
) -> None:
    assert fd.parse_kickoff(d, t) == (expected, missing)


def test_parse_kickoff_rejects_garbage() -> None:
    with pytest.raises(ValueError):
        fd.parse_kickoff("2019-08-10", "15:00")


# ---- real sample files ------------------------------------------------------


def test_parse_current_season_sample() -> None:
    p = fd.parse_csv(E0_2627, "E0")
    assert p.rows_read == 4 and len(p.rows) == 4 and not p.skipped
    r = p.rows[0]
    assert (r.home, r.away, r.home_goals, r.away_goals, r.status) == (
        "Arsenal",
        "Coventry",
        3,
        0,
        "finished",
    )
    assert r.kickoff_utc == datetime(2026, 8, 21, 19, 0, tzinfo=UTC)
    odds = _odds(r)
    assert odds[("B365", "open", "OU", "over")] == (2.5, 1.57)
    assert odds[("B365", "open", "OU", "under")] == (2.5, 2.38)
    # home favourite -> negative home handicap, both sides carry the home-perspective line
    assert odds[("B365", "open", "AH", "home")] == (-2.0, 2.03)
    assert odds[("B365", "open", "AH", "away")] == (-2.0, 1.78)
    assert odds[("B365", "close", "AH", "home")] == (-2.0, 2.05)
    # 2026-27 has no Pinnacle columns: reported, not a crash
    assert {"P>2.5", "PC<2.5", "PAHH", "PCAHA"} <= set(p.missing_columns)
    assert not any(o.bookmaker == "PS" for o in r.odds)
    # away favourite (Hull v Man United) -> positive home handicap
    assert _odds(p.rows[1])[("B365", "open", "AH", "home")][0] == 1.5
    # docs/10 §1: first-half goals (HTHG/HTAG)
    assert (r.ht_home_goals, r.ht_away_goals) == (2, 0)
    assert p.bad_ht_goals == 0


@pytest.mark.parametrize(
    ("ft", "ht", "expected", "bad"),
    [
        (("2", "1"), ("1", "0"), (1, 0), 0),
        (("2", "1"), ("", ""), (None, None), 0),  # no half-time data: not an error
        (("2", "1"), ("3", "0"), (None, None), 1),  # more than full time
        (("2", "1"), ("1", ""), (None, None), 1),  # only one side
        (("2", "1"), ("x", "0"), (None, None), 1),
        (("", ""), ("1", "0"), (None, None), 0),  # unplayed: never carries half-time goals
    ],
)
def test_half_time_goals(
    ft: tuple[str, str], ht: tuple[str, str], expected: tuple[int | None, int | None], bad: int
) -> None:
    body = (
        "Div,Date,Time,HomeTeam,AwayTeam,FTHG,FTAG,HTHG,HTAG\n"
        f"E0,10/08/2019,15:00,Burnley,Southampton,{ft[0]},{ft[1]},{ht[0]},{ht[1]}\n"
    )
    p = fd.parse_csv(body.encode(), "E0")
    (row,) = p.rows
    assert (row.ht_home_goals, row.ht_away_goals) == expected
    assert p.bad_ht_goals == bad


def test_open_and_close_ah_lines_are_independent() -> None:
    rows = fd.parse_csv(E0_2627, "E0").rows
    everton = next(r for r in rows if r.home == "Everton")
    assert _odds(everton)[("AVG", "close", "AH", "home")][0] == -0.25


def test_parse_older_season_sample_has_pinnacle() -> None:
    p = fd.parse_csv(E0_1920, "E0")
    assert len(p.rows) == 4
    r = p.rows[0]
    assert (r.home, r.away) == ("Liverpool", "Norwich")
    odds = _odds(r)
    assert odds[("PS", "open", "OU", "over")] == (2.5, 1.4)
    assert odds[("PS", "close", "AH", "home")] == (-2.25, 1.94)
    assert not {"P>2.5", "PAHH", "PC>2.5", "PCAHH"} & set(p.missing_columns)
    assert len(r.odds) == 32  # 4 bookmakers x 2 timings x (OU over/under + AH home/away)


# ---- edge cases -------------------------------------------------------------


def test_edge_cases_are_counted_not_crashed() -> None:
    csv_text = (
        HEADER
        + "E0,10/08/19,15:00,Burnley,Southampton,3,0,1.9,1.0,-0.25,1.95,abc\n"  # yy date, bad odds
        + "E0,11/08/2019,16:30,Leicester,Wolves,,,2.1,1.8,,1.9,2.0\n"  # unplayed, no AH line
        + "E0,notadate,15:00,A,B,1,1,,,,,\n"  # bad date
        + "E1,11/08/2019,15:00,Hull,Leeds,1,1,,,,,\n"  # wrong division
        + "E0,11/08/2019,15:00,,Leeds,1,1,,,,,\n"  # missing team
        + "E0,12/08/2019,15:00,Man City,Spurs,x,1,,,,,\n"  # bad goals
        + "E0,10/08/19,15:00,Burnley,Southampton,3,0,1.9,2.0,-0.25,1.95,2\n"  # duplicate
        + ",,,,,,,,,,,\n"
        + ",,,,,,,,,,,\n"
    )
    p = fd.parse_csv(csv_text.encode(), "E0")
    assert p.rows_read == 7
    assert p.skipped == {
        "empty_row": 2,
        "bad_date": 1,
        "wrong_div": 1,
        "unresolved_team": 1,
        "bad_goals": 1,
        "duplicate_in_file": 1,
    }
    burnley, leicester = p.rows
    assert burnley.kickoff_utc == datetime(2019, 8, 10, 14, 0, tzinfo=UTC)
    assert set(_odds(burnley)) == {("B365", "open", "OU", "over"), ("B365", "open", "AH", "home")}
    assert leicester.status == "scheduled" and leicester.home_goals is None
    assert not any(o.market == "AH" for o in leicester.odds)
    assert p.ah_line_missing == 2
    assert p.odds_missing == 2  # "1.0" and "abc"


def test_missing_time_column_defaults_to_1500_uk() -> None:
    csv_text = "Div,Date,HomeTeam,AwayTeam,FTHG,FTAG\nE0,14/12/2019,Arsenal,Man City,0,3\n"
    p = fd.parse_csv(csv_text.encode(), "E0")
    assert p.time_missing == 1
    assert p.rows[0].kickoff_utc == datetime(2019, 12, 14, 15, 0, tzinfo=UTC)
    assert "Time" in p.missing_columns


def test_latin1_and_bom() -> None:
    body = "Div,Date,Time,HomeTeam,AwayTeam,FTHG,FTAG\nSP1,01/09/2019,18:00,Alavés,Cádiz,1,0\n"
    assert fd.parse_csv(body.encode("latin-1"), "SP1").rows[0].home == "Alavés"
    bom = b"\xef\xbb\xbf" + body.encode()
    assert fd.parse_csv(bom, "SP1").rows[0].away == "Cádiz"


def test_required_column_missing_raises() -> None:
    with pytest.raises(ValueError, match="FTHG"):
        fd.parse_csv(b"Div,Date,Time,HomeTeam,AwayTeam,FTAG\n", "E0")


# ---- DB upsert --------------------------------------------------------------


def _epl(s: object) -> League:
    from sqlalchemy.orm import Session

    assert isinstance(s, Session)
    sync_leagues(s, get_settings())
    s.flush()
    return s.scalars(select(League).where(League.key == "EPL")).one()


def test_upsert_is_idempotent(migrated_db: Path) -> None:
    parsed = fd.parse_csv(E0_1920, "E0")
    with session_scope(migrated_db) as s:
        first = fd.upsert_file(s, _epl(s), "2019-20", parsed)
    assert (first.inserted, first.updated, first.teams_created) == (4, 0, 5)
    assert first.odds_inserted == 4 * 32
    with session_scope(migrated_db) as s:
        again = fd.upsert_file(s, _epl(s), "2019-20", parsed)
        assert (again.inserted, again.updated, again.unchanged) == (0, 0, 4)
        assert (again.odds_inserted, again.odds_updated, again.odds_unchanged) == (0, 0, 128)
        assert s.scalar(select(func.count()).select_from(Match)) == 4
        assert s.scalar(select(func.count()).select_from(HistoricalOdds)) == 128
        assert s.scalar(select(func.count()).select_from(Team)) == 5


def test_upsert_updates_changed_values_and_ah_line(migrated_db: Path) -> None:
    base = HEADER + "E0,10/08/2019,15:00,Burnley,Southampton,{g},0,1.9,2.0,{line},1.95,1.95\n"
    with session_scope(migrated_db) as s:
        fd.upsert_file(
            s, _epl(s), "2019-20", fd.parse_csv(base.format(g="", line="-0.25").encode(), "E0")
        )
    with session_scope(migrated_db) as s:
        c = fd.upsert_file(
            s, _epl(s), "2019-20", fd.parse_csv(base.format(g="3", line="-0.5").encode(), "E0")
        )
        assert (c.inserted, c.updated) == (0, 1)
        assert (c.odds_inserted, c.odds_updated, c.odds_unchanged) == (0, 2, 2)
        m = s.scalars(select(Match)).one()
        assert (m.status, m.home_goals, m.away_goals) == ("finished", 3, 0)
        lines = s.scalars(select(HistoricalOdds.line).where(HistoricalOdds.market == "AH")).all()
        assert lines == [-0.5, -0.5]


def test_corrected_date_updates_same_fixture(migrated_db: Path) -> None:
    """A Date correction changes the hash; the natural key must still find the fixture."""
    row = HEADER + "E0,{d},15:00,Burnley,Southampton,3,0,,,,,\n"
    with session_scope(migrated_db) as s:
        fd.upsert_file(
            s, _epl(s), "2019-20", fd.parse_csv(row.format(d="10/08/2019").encode(), "E0")
        )
    with session_scope(migrated_db) as s:
        c = fd.upsert_file(
            s, _epl(s), "2019-20", fd.parse_csv(row.format(d="11/08/2019").encode(), "E0")
        )
        assert (c.inserted, c.updated) == (0, 1)
        m = s.scalars(select(Match)).one()
        assert m.fd_row_hash == fd.row_hash("E0", "11/08/2019", "Burnley", "Southampton")


# ---- job with a mocked site -------------------------------------------------


def _settings(tmp_path: Path, seasons: list[str]) -> Settings:
    base = get_settings()
    return base.model_copy(
        update={
            "general": base.general.model_copy(
                update={"raw_dir": str(tmp_path / "raw"), "reports_dir": str(tmp_path / "rep")}
            ),
            "leagues": base.leagues.model_copy(
                update={"enabled": ["EPL"], "history_seasons": seasons}
            ),
        }
    )


def _client(settings: Settings, handler: httpx.MockTransport, today: date) -> PoliteClient:
    return PoliteClient(
        settings, fd.SOURCE, transport=handler, sleep=lambda _s: None, today=lambda: today
    )


def test_ingest_all_fetches_caches_and_reuses(migrated_db: Path, tmp_path: Path) -> None:
    settings = _settings(tmp_path, ["2019-20"])
    urls: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        urls.append(str(req.url))
        return httpx.Response(200, content=E0_1920)

    with session_scope(migrated_db) as s:
        sync_leagues(s, settings)
    today = date(2026, 9, 27)
    with (
        job_run("ingest", settings, migrated_db) as ctx,
        _client(settings, httpx.MockTransport(handler), today) as c,
    ):
        fd.ingest_all(ctx, c, settings.enabled_leagues())
    assert urls == ["https://football-data.co.uk/mmz4281/1920/E0.csv"]
    cached = tmp_path / "raw" / "football_data" / "2026-09-27" / "E0_1920.csv"
    assert cached.read_bytes() == E0_1920
    rep = ctx.summary["football_data"]["EPL 2019-20"]
    assert (rep["rows_read"], rep["inserted"], rep["skipped_empty"]) == (4, 4, 0)

    # Finished season + cache saved after it ended -> no second request, same rows.
    with (
        job_run("ingest", settings, migrated_db) as ctx2,
        _client(settings, httpx.MockTransport(handler), date(2026, 10, 1)) as c2,
    ):
        fd.ingest_all(ctx2, c2, settings.enabled_leagues())
        assert c2.requests_made == 0
    assert len(urls) == 1
    rep2 = ctx2.summary["football_data"]["EPL 2019-20"]
    assert (rep2["inserted"], rep2["unchanged"], rep2["from_cache"]) == (0, 4, True)


def test_ingest_all_stops_on_403(migrated_db: Path, tmp_path: Path) -> None:
    settings = _settings(tmp_path, ["2019-20", "2020-21"])
    calls = 0

    def handler(req: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(403)

    with session_scope(migrated_db) as s:
        sync_leagues(s, settings)
    with (
        job_run("ingest", settings, migrated_db) as ctx,
        _client(settings, httpx.MockTransport(handler), date(2026, 9, 27)) as c,
    ):
        fd.ingest_all(ctx, c, settings.enabled_leagues())
    assert calls == 1  # no retry, no second season
    assert ctx.summary["football_data_blocked"] is True
    assert any("blocked" in w for w in ctx.summary["warnings"])
