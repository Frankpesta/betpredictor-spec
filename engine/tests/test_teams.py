"""Team-name resolution (docs/02 §3): normalisation, fuzzy proposals, seed, exact resolution."""

from __future__ import annotations

import tomllib
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from engine.config import get_settings
from engine.db.models import League, Match, Team, TeamAlias, UnresolvedName
from engine.db.session import session_scope
from engine.jobs import sync_leagues
from engine.mapping import teams as tm

# football-data canonical names as ingested on 2026-09-27 (docs/discovered/football-data.md)
EPL = [
    "Arsenal",
    "Brighton",
    "Man City",
    "Man United",
    "Newcastle",
    "Nott'm Forest",
    "Sheffield United",
    "Tottenham",
    "West Ham",
    "Wolves",
]
LALIGA = [
    "Alaves",
    "Ath Bilbao",
    "Ath Madrid",
    "Barcelona",
    "Betis",
    "Cadiz",
    "Celta",
    "Espanol",
    "Real Madrid",
    "Sociedad",
    "Vallecano",
]
SERIEA = ["Inter", "Juventus", "Milan", "Verona", "Roma", "Lazio"]
BUNDES = [
    "Bayern Munich",
    "Dortmund",
    "Ein Frankfurt",
    "FC Koln",
    "Hertha",
    "Leverkusen",
    "M'gladbach",
    "RB Leipzig",
    "Union Berlin",
]
LIGUE1 = ["Brest", "Paris SG", "Paris FC", "St Etienne", "Lyon", "Marseille"]


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Atlético Madrid", "atletico madrid"),
        ("FC Köln", "koln"),
        ("Brighton & Hove Albion", "brighton and hove albion"),
        ("AC Milan", "milan"),
        ("Borussia M.Gladbach", "borussia m gladbach"),
        ("AFC Bournemouth", "bournemouth"),
        ("Paris Saint-Germain", "paris saint-germain"),
        ("  Stade   Brestois ", "stade brestois"),
        ("Alavés", "alaves"),
    ],
)
def test_normalise(raw: str, expected: str) -> None:
    assert tm.normalise(raw) == expected


# Spellings from docs/02 §3.2 that fuzzy matching should propose correctly.
@pytest.mark.parametrize(
    ("raw", "league", "expected"),
    [
        ("Manchester United", EPL, "Man United"),
        ("Man Utd", EPL, "Man United"),
        ("Manchester City", EPL, "Man City"),
        ("Nottingham Forest", EPL, "Nott'm Forest"),
        ("Inter Milan", SERIEA, "Inter"),
        ("AC Milan", SERIEA, "Milan"),
        ("Hellas Verona", SERIEA, "Verona"),
        ("Atletico Madrid", LALIGA, "Ath Madrid"),
        ("Atlético Madrid", LALIGA, "Ath Madrid"),
        ("Real Betis", LALIGA, "Betis"),
        ("Real Sociedad", LALIGA, "Sociedad"),
        ("Celta Vigo", LALIGA, "Celta"),
        ("Alavés", LALIGA, "Alaves"),
        ("Cádiz", LALIGA, "Cadiz"),
        ("Borussia Dortmund", BUNDES, "Dortmund"),
        ("Bayer Leverkusen", BUNDES, "Leverkusen"),
        ("Eintracht Frankfurt", BUNDES, "Ein Frankfurt"),
        ("FC Köln", BUNDES, "FC Koln"),
        ("Hertha Berlin", BUNDES, "Hertha"),
        ("Bayern München", BUNDES, "Bayern Munich"),
        ("Paris SG", LIGUE1, "Paris SG"),
        ("Saint-Etienne", LIGUE1, "St Etienne"),
    ],
)
def test_fuzzy_proposals_for_tricky_spellings(raw: str, league: list[str], expected: str) -> None:
    assert tm.best_candidate(raw, league)[0] == expected


@pytest.mark.parametrize("raw", ["Manchester United", "Man Utd", "Man United", "Manchester Utd"])
def test_man_united_never_proposed_as_man_city(raw: str) -> None:
    assert tm.best_candidate(raw, EPL)[0] == "Man United"
    assert tm.fuzzy_score(raw, "Man United") > tm.fuzzy_score(raw, "Man City")


@pytest.mark.parametrize("raw", ["Manchester City", "Man City"])
def test_man_city_never_proposed_as_man_united(raw: str) -> None:
    assert tm.best_candidate(raw, EPL)[0] == "Man City"


def test_best_candidate_is_restricted_to_given_league() -> None:
    assert tm.best_candidate("Real Madrid", EPL)[0] != "Real Madrid"
    assert tm.best_candidate("x", []) == (None, 0.0)


# ---- seed file ----------------------------------------------------------------


def test_seed_file_parses_without_conflicts() -> None:
    seeds = tm.load_seed()
    keys = [(s.source, s.alias) for s in seeds]
    assert len(keys) == len(set(keys)), "an alias appears twice for one source"
    assert all(s.source in tm.SEED_SOURCES for s in seeds)


def test_seed_conflict_rejected(tmp_path: Path) -> None:
    p = tmp_path / "seed.toml"
    p.write_text(
        '[understat.EPL]\n"Man Utd" = "Man United"\n[understat.CHAMP]\n"Man Utd" = "Man City"\n'
    )
    with pytest.raises(ValueError, match="maps to both"):
        tm.load_seed(p)


def test_seed_unknown_source_rejected(tmp_path: Path) -> None:
    p = tmp_path / "seed.toml"
    p.write_text('[football_data.EPL]\n"A" = "B"\n')
    with pytest.raises(ValueError, match="unknown source"):
        tm.load_seed(p)


# ---- DB: seed application, exact resolution, proposals --------------------------


def _teams(s: Session) -> dict[str, int]:
    sync_leagues(s, get_settings())
    s.flush()
    epl = s.scalars(select(League).where(League.key == "EPL")).one()
    ids = {}
    for name in ("Man City", "Man United", "Wolves", "Arsenal"):
        t = Team(league_id=epl.id, canonical_name=name)
        s.add(t)
        s.flush()
        ids[name] = t.id
    # league membership for proposals comes from matches
    ko = datetime(2025, 8, 16, 14, tzinfo=UTC)
    s.add(
        Match(
            league_id=epl.id,
            season="2025-26",
            kickoff_utc=ko,
            home_team_id=ids["Man City"],
            away_team_id=ids["Man United"],
            status="scheduled",
        )
    )
    s.add(
        Match(
            league_id=epl.id,
            season="2025-26",
            kickoff_utc=ko,
            home_team_id=ids["Wolves"],
            away_team_id=ids["Arsenal"],
            status="scheduled",
        )
    )
    s.flush()
    return ids


SEED = [
    tm.SeedAlias("understat", "EPL", "Manchester United", "Man United"),
    tm.SeedAlias("understat", "EPL", "Manchester City", "Man City"),
    tm.SeedAlias("understat", "EPL", "Wolverhampton Wanderers", "Wolves"),
    tm.SeedAlias("understat", "EPL", "Arsenal", "Arsenal"),
    tm.SeedAlias("understat", "EPL", "Ghost FC", "Nonexistent"),
]


def test_apply_seed_idempotent_and_reports_missing(migrated_db: Path) -> None:
    with session_scope(migrated_db) as s:
        _teams(s)
        r1 = tm.apply_seed(s, SEED)
        r2 = tm.apply_seed(s, SEED)
    assert (r1.inserted, r1.updated, r1.unchanged) == (4, 0, 0)
    assert (r2.inserted, r2.updated, r2.unchanged) == (0, 0, 4)
    assert r1.missing_canonical == ["understat.EPL: Ghost FC -> Nonexistent"]


def test_resolver_is_exact_only_and_keeps_manchester_apart(migrated_db: Path) -> None:
    with session_scope(migrated_db) as s:
        ids = _teams(s)
        tm.apply_seed(s, SEED)
        r = tm.Resolver(s, "understat")
        assert r.resolve("Manchester United", "EPL") == ids["Man United"]
        assert r.resolve("Manchester City", "EPL") == ids["Man City"]
        assert r.resolve("Wolverhampton Wanderers", "EPL") == ids["Wolves"]
        # near misses are NOT resolved
        assert r.resolve("Manchester Utd", "EPL") is None
        assert r.resolve("manchester united", "EPL") is None
        # other sources don't borrow understat aliases
        assert tm.Resolver(s, "sportybet").resolve("Manchester United", "EPL") is None
        assert r.flush_misses() == 2
        names = set(s.scalars(select(UnresolvedName.raw_name)))
    assert names == {"Manchester Utd", "manchester united"}


def test_map_teams_proposes_but_never_accepts(migrated_db: Path, tmp_path: Path) -> None:
    from engine.jobs import job_run

    base = get_settings()
    settings = base.model_copy(
        update={"general": base.general.model_copy(update={"reports_dir": str(tmp_path)})}
    )
    seed = tmp_path / "seed.toml"
    seed.write_text('[understat.EPL]\n"Manchester City" = "Man City"\n', encoding="utf-8")
    now = datetime(2026, 9, 27, tzinfo=UTC)
    with session_scope(migrated_db) as s:
        _teams(s)
        for raw in ("Manchester City", "Manchester United", "Wolverhampton Wanderers"):
            tm.record_unresolved(s, "understat", raw, "EPL", now)

    with job_run("map-teams", settings, migrated_db) as ctx:
        out = tm.run_map_teams(ctx, seed_path=seed)

    assert "Manchester United" in out and "Man United" in out
    with session_scope(migrated_db) as s:
        aliases = {a.alias for a in s.scalars(select(TeamAlias))}
        remaining = set(s.scalars(select(UnresolvedName.raw_name)))
    assert aliases == {"Manchester City"}  # only the seeded one
    assert remaining == {"Manchester United", "Wolverhampton Wanderers"}
    assert ctx.summary["unresolved_cleared"] == 1

    proposals = tomllib.loads((tmp_path / tm.PROPOSALS_FILE).read_text(encoding="utf-8"))
    assert proposals["understat"]["EPL"]["Manchester United"] == "Man United"


# docs/02 §3.2 tricky pairs, as spelled by Understat (discovered 2026-09-27). Every pair
# that exists in the data must resolve to exactly this football-data team.
UNDERSTAT_TRICKY = {
    "Manchester United": "Man United",
    "Manchester City": "Man City",
    "Nottingham Forest": "Nott'm Forest",
    "Wolverhampton Wanderers": "Wolves",
    "Sheffield United": "Sheffield United",
    "West Bromwich Albion": "West Brom",
    "Inter": "Inter",
    "AC Milan": "Milan",
    "Atletico Madrid": "Ath Madrid",
    "Athletic Club": "Ath Bilbao",
    "Real Betis": "Betis",
    "Real Sociedad": "Sociedad",
    "Paris Saint Germain": "Paris SG",
    "Paris FC": "Paris FC",
    "Borussia Dortmund": "Dortmund",
    "Borussia M.Gladbach": "M'gladbach",
    "Bayer Leverkusen": "Leverkusen",
    "Eintracht Frankfurt": "Ein Frankfurt",
    "FC Cologne": "FC Koln",
    "Hertha Berlin": "Hertha",
    "Bayern Munich": "Bayern Munich",
    "Celta Vigo": "Celta",
    "Espanyol": "Espanol",
    "Alaves": "Alaves",
    "Cadiz": "Cadiz",
    "Verona": "Verona",
    "Saint-Etienne": "St Etienne",
    "Brest": "Brest",
}


def _seed_map(source: str) -> dict[str, str]:
    return {s.alias: s.canonical for s in tm.load_seed() if s.source == source}


@pytest.mark.parametrize(("alias", "canonical"), sorted(UNDERSTAT_TRICKY.items()))
def test_seed_tricky_pairs(alias: str, canonical: str) -> None:
    assert _seed_map("understat")[alias] == canonical


def test_seed_keeps_manchester_clubs_apart() -> None:
    seeds = tm.load_seed()
    united = {s.alias for s in seeds if s.canonical == "Man United"}
    city = {s.alias for s in seeds if s.canonical == "Man City"}
    assert united and city and not united & city
    assert all("city" not in a.lower() for a in united)
    assert all("united" not in a.lower() for a in city)


def test_understat_seed_is_one_to_one_per_league() -> None:
    """Understat spells each team one way; two spellings -> one team means a mapping error."""
    seen: dict[tuple[str, str, str], str] = {}
    for s in tm.load_seed():
        if s.source != "understat":
            continue
        key = (s.source, s.league_key, s.canonical)
        assert key not in seen, f"{s.canonical}: both {seen[key]!r} and {s.alias!r}"
        seen[key] = s.alias


# SportyBet spellings reviewed 2026-09-27 (incl. the five wrong fuzzy proposals).
SPORTYBET_TRICKY = {
    "Man Utd": "Man United",
    "Man City": "Man City",
    "Nottingham Forest": "Nott'm Forest",
    "PSG": "Paris SG",
    "Paris FC": "Paris FC",
    "Borussia M´gladbach": "M'gladbach",
    "Cologne": "FC Koln",
    "Athletic Bilbao": "Ath Bilbao",
    "Atletico Madrid": "Ath Madrid",
    "AC Milan": "Milan",
    "Inter": "Inter",
    "Ireland": "Republic of Ireland",
    "Northern Ireland": "Northern Ireland",
    "Congo": "Congo",
    "Congo DR": "DR Congo",
    "Czechia": "Czech Republic",
    "Korea Republic": "South Korea",
    "USA": "United States",
    "Turkiye": "Turkey",
}


@pytest.mark.parametrize(("alias", "canonical"), sorted(SPORTYBET_TRICKY.items()))
def test_sportybet_seed_tricky_pairs(alias: str, canonical: str) -> None:
    assert _seed_map("sportybet")[alias] == canonical


def test_non_fifa_sportybet_sides_stay_unmapped() -> None:
    assert not {"Bonaire", "Guadeloupe", "Martinique"} & set(_seed_map("sportybet"))
