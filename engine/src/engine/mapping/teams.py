"""Team-name resolution (docs/02 §3).

- Canonical name = football-data.co.uk spelling; only the football-data ingest creates teams.
- Every other source resolves names through `team_aliases` (source, alias) -> team,
  **exact match only**. Aliases come from `aliases_seed.toml`, which a human approves.
- `make map-teams` proposes candidates for unresolved names with normalised fuzzy
  matching restricted to the same league, but never accepts one automatically.
"""

from __future__ import annotations

import json
import re
import tomllib
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from rapidfuzz import fuzz
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from engine.db.base import utcnow
from engine.db.models import League, Match, Team, TeamAlias, UnresolvedName
from engine.db.session import session_scope
from engine.logging import get_logger

if TYPE_CHECKING:
    from engine.jobs import JobContext

log = get_logger(__name__)

SEED_PATH = Path(__file__).with_name("aliases_seed.toml")
SEED_SOURCES = ("understat", "sportybet")
PROPOSALS_FILE = "alias_proposals.toml"

# docs/02 §3.1 rule 4: tokens dropped before fuzzy comparison.
_DROP_TOKENS = frozenset({"fc", "afc", "cf", "sc", "ac"})


def normalise(name: str) -> str:
    """lowercase, strip accents, '&'->'and', drop '.', drop fc/afc/cf/sc/ac tokens."""
    s = unicodedata.normalize("NFKD", name)
    s = "".join(ch for ch in s if not unicodedata.combining(ch)).lower()
    s = s.replace("&", " and ").replace(".", " ")
    tokens = [t for t in re.split(r"\s+", s) if t and t not in _DROP_TOKENS]
    return " ".join(tokens)


def fuzzy_score(raw: str, candidate: str) -> float:
    return float(fuzz.token_set_ratio(normalise(raw), normalise(candidate)))


def best_candidate(raw: str, candidates: list[str]) -> tuple[str | None, float]:
    """Highest token_set_ratio candidate.

    token_set_ratio scores 100 whenever one name's tokens are a subset of the other's
    ("paris sg" vs "paris" from "Paris FC"), so ties are broken by plain `ratio` on the
    normalised strings, then alphabetically, keeping output deterministic.
    """
    best: tuple[float, float, str] | None = None
    norm_raw = normalise(raw)
    for c in sorted(candidates):
        key = (fuzzy_score(raw, c), float(fuzz.ratio(norm_raw, normalise(c))), c)
        if best is None or key[:2] > best[:2]:
            best = key
    if best is None or best[0] == 0:
        return (None, 0.0)
    return (best[2], best[0])


# ---------------------------------------------------------------------------
# Seed file
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SeedAlias:
    source: str
    league_key: str
    alias: str
    canonical: str


def load_seed(path: Path = SEED_PATH) -> list[SeedAlias]:
    """Parse `[<source>.<LEAGUE>] "alias" = "canonical"` tables; reject conflicts."""
    with path.open("rb") as f:
        data = tomllib.load(f)
    out: list[SeedAlias] = []
    seen: dict[tuple[str, str], str] = {}
    for source, leagues in data.items():
        if source not in SEED_SOURCES:
            raise ValueError(f"{path.name}: unknown source [{source}]")
        for league_key, pairs in leagues.items():
            for alias, canonical in pairs.items():
                prev = seen.get((source, alias))
                if prev is not None and prev != canonical:
                    raise ValueError(
                        f"{path.name}: {source} alias {alias!r} maps to both "
                        f"{prev!r} and {canonical!r}"
                    )
                seen[(source, alias)] = canonical
                out.append(SeedAlias(source, league_key, alias, canonical))
    return out


@dataclass
class SeedResult:
    inserted: int = 0
    updated: int = 0
    unchanged: int = 0
    missing_canonical: list[str] = field(default_factory=list)


def apply_seed(session: Session, seeds: list[SeedAlias]) -> SeedResult:
    """Upsert seed aliases into team_aliases. The seed is the source of truth."""
    res = SeedResult()
    teams = {t.canonical_name: t.id for t in session.scalars(select(Team))}
    existing = {(a.source, a.alias): a for a in session.scalars(select(TeamAlias))}
    for sa in seeds:
        team_id = teams.get(sa.canonical)
        if team_id is None:
            res.missing_canonical.append(
                f"{sa.source}.{sa.league_key}: {sa.alias} -> {sa.canonical}"
            )
            continue
        cur = existing.get((sa.source, sa.alias))
        if cur is None:
            row = TeamAlias(team_id=team_id, source=sa.source, alias=sa.alias)
            session.add(row)
            existing[(sa.source, sa.alias)] = row
            res.inserted += 1
        elif cur.team_id != team_id:
            log.warning(
                "alias re-pointed by seed",
                extra={"fields": {"source": sa.source, "alias": sa.alias, "to": sa.canonical}},
            )
            cur.team_id = team_id
            res.updated += 1
        else:
            res.unchanged += 1
    session.flush()
    return res


# ---------------------------------------------------------------------------
# Runtime resolution (exact only)
# ---------------------------------------------------------------------------


class Resolver:
    """Exact (source, alias) -> team_id lookups; records every miss in `unresolved_names`."""

    def __init__(self, session: Session, source: str) -> None:
        self._session = session
        self.source = source
        self._map = {
            a.alias: a.team_id
            for a in session.scalars(select(TeamAlias).where(TeamAlias.source == source))
        }
        self.misses: Counter[tuple[str, str | None]] = Counter()

    def resolve(self, raw_name: str, league_key: str | None) -> int | None:
        team_id = self._map.get(raw_name)
        if team_id is None:
            self.misses[(raw_name, league_key)] += 1
        return team_id

    def flush_misses(self, now: datetime | None = None) -> int:
        """Upsert misses into unresolved_names; returns how many distinct names were recorded."""
        now = now or utcnow()
        for raw_name, league_key in self.misses:
            record_unresolved(self._session, self.source, raw_name, league_key, now)
        n = len(self.misses)
        self.misses.clear()
        return n


def record_unresolved(
    session: Session, source: str, raw_name: str, league_key: str | None, now: datetime
) -> None:
    row = session.scalars(
        select(UnresolvedName).where(
            UnresolvedName.source == source, UnresolvedName.raw_name == raw_name
        )
    ).one_or_none()
    if row is None:
        session.add(
            UnresolvedName(
                source=source,
                raw_name=raw_name,
                league_key=league_key,
                first_seen=now,
                last_seen=now,
            )
        )
    else:
        row.last_seen = now
        row.league_key = league_key or row.league_key
    session.flush()


def clear_resolved(session: Session) -> int:
    """Delete unresolved_names rows that now have an exact alias."""
    aliases = {(a.source, a.alias) for a in session.scalars(select(TeamAlias))}
    done = [
        u.id for u in session.scalars(select(UnresolvedName)) if (u.source, u.raw_name) in aliases
    ]
    if done:
        session.execute(delete(UnresolvedName).where(UnresolvedName.id.in_(done)))
    return len(done)


# ---------------------------------------------------------------------------
# make map-teams
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Proposal:
    source: str
    raw_name: str
    league_key: str | None
    candidate: str | None
    score: float


def league_team_names(session: Session) -> dict[str, list[str]]:
    """Canonical names of every team that has played a match in each league (any season)."""
    out: dict[str, set[str]] = {}
    for side in (Match.home_team_id, Match.away_team_id):
        rows = session.execute(
            select(League.key, Team.canonical_name)
            .join(Match, Match.league_id == League.id)
            .join(Team, Team.id == side)
            .distinct()
        )
        for league_key, name in rows:
            out.setdefault(league_key, set()).add(name)
    return {k: sorted(v) for k, v in out.items()}


def propose(session: Session) -> list[Proposal]:
    by_league = league_team_names(session)
    out: list[Proposal] = []
    for u in session.scalars(
        select(UnresolvedName).order_by(
            UnresolvedName.source, UnresolvedName.league_key, UnresolvedName.raw_name
        )
    ):
        candidates = by_league.get(u.league_key or "", [])
        cand, score = best_candidate(u.raw_name, candidates)
        out.append(Proposal(u.source, u.raw_name, u.league_key, cand, score))
    return out


def _toml_str(s: str) -> str:
    return json.dumps(s, ensure_ascii=False)


def proposals_toml(proposals: list[Proposal]) -> str:
    """Same table layout as aliases_seed.toml so approved lines can be copied across."""
    lines = [
        "# Alias PROPOSALS from `make map-teams` — NOT applied automatically.",
        "# Review each line; copy the correct ones into",
        "# engine/src/engine/mapping/aliases_seed.toml, then re-run `make map-teams`.",
        "",
    ]
    groups: dict[tuple[str, str], list[Proposal]] = {}
    for p in proposals:
        groups.setdefault((p.source, p.league_key or "UNKNOWN"), []).append(p)
    for (source, league_key), ps in sorted(groups.items()):
        lines.append(f"[{source}.{league_key}]")
        for p in ps:
            if p.candidate is None:
                lines.append(f"# {_toml_str(p.raw_name)} = ???  # no candidate in league")
            else:
                lines.append(
                    f"{_toml_str(p.raw_name)} = {_toml_str(p.candidate)}  # score {p.score:.0f}"
                )
        lines.append("")
    return "\n".join(lines)


def format_table(proposals: list[Proposal]) -> str:
    header = ("raw_name", "league", "best candidate", "score")
    rows = [
        (p.raw_name, p.league_key or "?", p.candidate or "-", f"{p.score:.0f}") for p in proposals
    ]
    widths = [max(len(r[i]) for r in [header, *rows]) for i in range(4)]
    fmt = " | ".join(f"{{:<{w}}}" for w in widths)
    return "\n".join(
        [fmt.format(*header), "-+-".join("-" * w for w in widths)] + [fmt.format(*r) for r in rows]
    )


def run_map_teams(ctx: JobContext, seed_path: Path = SEED_PATH) -> str:
    """Apply the seed, drop now-resolved names, propose candidates for the rest."""
    seeds = load_seed(seed_path)
    with session_scope(ctx.db_path) as s:
        res = apply_seed(s, seeds)
        cleared = clear_resolved(s)
        proposals = propose(s)
    ctx.note("aliases_inserted", res.inserted)
    ctx.note("aliases_updated", res.updated)
    ctx.note("aliases_unchanged", res.unchanged)
    ctx.note("unresolved_cleared", cleared)
    ctx.note("unresolved_remaining", len(proposals))
    for m in res.missing_canonical:
        ctx.warn(f"seed canonical team not found (ingest first?): {m}")

    out_dir = ctx.settings.reports_path
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / PROPOSALS_FILE
    out_file.write_text(proposals_toml(proposals), encoding="utf-8")
    ctx.note("proposals_file", str(out_file))

    table = format_table(proposals) if proposals else "(no unresolved names)"
    return (
        f"aliases: +{res.inserted} inserted, {res.updated} updated, {res.unchanged} unchanged; "
        f"{cleared} names newly resolved; {len(proposals)} unresolved\n\n{table}\n\n"
        f"proposals written to {out_file}"
    )
