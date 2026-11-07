from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.api.deps import AgentDep, require_admin
from app.db import SessionDep
from app.errors import error_responses
from app.schemas import ResetResponse, SeedResponse
from app.seeding import reset_database, seed_database

router = APIRouter(
    prefix="/admin",
    tags=["admin"],
    dependencies=[Depends(require_admin)],
    responses=error_responses(401, 403),
)


@router.post("/seed", response_model=SeedResponse)
async def seed(
    session: SessionDep,
    agent: AgentDep,
    rebase_dates: Annotated[
        bool, Query(description="Shift seed dates by whole weeks so the newest incident is ~1 week old")
    ] = True,
) -> SeedResponse:
    """Load the 20 historical incidents from data/seed_incidents.json and call agent.ingest_history().

    Idempotent: incidents whose ID already exists are skipped.
    """
    return await seed_database(session, agent, rebase_dates=rebase_dates)


@router.post("/reset", response_model=ResetResponse)
async def reset(session: SessionDep, agent: AgentDep) -> ResetResponse:
    """Delete every incident, timeline event and memory reference (and the agent's memory, if it supports reset)."""
    return await reset_database(session, agent)
