"""Slip + alias endpoints (docs/07 §1).

The dashboard never writes to the DB; these are its only write paths.
"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from engine.api.routes_jobs import JobBusyError, runner_of
from engine.db.models import ALIAS_SOURCES, Slip, Team, TeamAlias
from engine.db.queries import latest_gate_passed
from engine.db.session import session_scope
from engine.mapping.teams import clear_resolved

router = APIRouter()


class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ModeBody(_Body):
    mode: Literal["paper", "placed"]
    stake: float | None = Field(default=None, gt=0)
    confirm_no_edge: bool = False


class BookingBody(_Body):
    # SportyBet share codes are short alphanumerics, e.g. "X6YP10" (docs/discovered).
    booking_code: str = Field(pattern=r"^\s*[A-Za-z0-9]{4,16}\s*$")

    @field_validator("booking_code")
    @classmethod
    def _norm(cls, v: str) -> str:
        return v.strip().upper()


class AliasBody(_Body):
    source: str
    alias: str = Field(min_length=1)
    team_id: int

    @field_validator("source")
    @classmethod
    def _source(cls, v: str) -> str:
        if v not in ALIAS_SOURCES:
            raise ValueError(f"source must be one of {', '.join(ALIAS_SOURCES)}")
        return v

    @field_validator("alias")
    @classmethod
    def _strip(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("alias must not be blank")
        return v


def slip_dict(slip: Slip) -> dict[str, Any]:
    return {
        "id": slip.id,
        "slip_type": slip.slip_type,
        "pool": slip.pool,
        "mode": slip.mode,
        "stake": slip.stake,
        "status": slip.status,
        "booking_status": slip.booking_status,
        "booking_code": slip.booking_code,
        "booking_error": slip.booking_error,
    }


def _open_slip(s: Session, slip_id: int) -> Slip:
    slip = s.get(Slip, slip_id)
    if slip is None:
        raise HTTPException(404, "slip not found")
    if slip.status != "open":
        raise HTTPException(409, f"slip is already settled ({slip.status}); history is read-only")
    return slip


@router.patch("/slips/{slip_id}/mode")
def set_mode(slip_id: int, body: ModeBody, request: Request) -> dict[str, Any]:
    with session_scope(runner_of(request).db_path) as s:
        slip = _open_slip(s, slip_id)
        if body.mode == "placed":
            if latest_gate_passed(s) != 1 and not body.confirm_no_edge:
                raise HTTPException(
                    400,
                    "Model has not demonstrated an edge — set confirm_no_edge=true to mark "
                    "this slip as placed anyway",
                )
            slip.mode, slip.stake = "placed", body.stake
        else:
            slip.mode, slip.stake = "paper", None
        return slip_dict(slip)


@router.patch("/slips/{slip_id}/booking")
def set_booking(slip_id: int, body: BookingBody, request: Request) -> dict[str, Any]:
    with session_scope(runner_of(request).db_path) as s:
        slip = _open_slip(s, slip_id)
        slip.booking_code, slip.booking_status = body.booking_code, "manual"
        return slip_dict(slip)


@router.post("/slips/{slip_id}/rebook")
def rebook(slip_id: int, request: Request) -> dict[str, int]:
    runner = runner_of(request)
    if runner.busy:
        raise HTTPException(409, "another job is running")
    with session_scope(runner.db_path) as s:
        slip = _open_slip(s, slip_id)
        if slip.mode == "placed":
            raise HTTPException(409, "slip is marked placed; its booking code must not change")
        slip.booking_status, slip.booking_error = "pending", None
    try:
        return {"job_run_id": runner.start("book", {"slip_id": slip_id})}
    except JobBusyError as exc:
        # Lost a race with another job: the slip stays `pending` and the next
        # `make book` / Run daily picks it up.
        raise HTTPException(409, str(exc)) from exc


@router.post("/aliases")
def add_alias(body: AliasBody, request: Request) -> dict[str, Any]:
    with session_scope(runner_of(request).db_path) as s:
        team = s.get(Team, body.team_id)
        if team is None:
            raise HTTPException(404, "team not found")
        existing = s.scalars(
            select(TeamAlias).where(TeamAlias.source == body.source, TeamAlias.alias == body.alias)
        ).first()
        if existing is not None:
            raise HTTPException(409, f"alias already maps to team {existing.team_id}")
        row = TeamAlias(team_id=team.id, source=body.source, alias=body.alias)
        s.add(row)
        s.flush()
        cleared = clear_resolved(s)
        return {
            "id": row.id,
            "team_id": team.id,
            "team": team.canonical_name,
            "source": body.source,
            "alias": body.alias,
            "unresolved_cleared": cleared,
        }
