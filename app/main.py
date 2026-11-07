import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlmodel import Session, select

from app.agent.factory import create_agent
from app.api import admin, incidents, stats
from app.config import Settings, get_settings
from app.db import init_db, make_engine
from app.errors import register_error_handlers
from app.limits import BodySizeLimitMiddleware
from app.models import Incident
from app.seeding import seed_database, warm_agent_from_db

logger = logging.getLogger("aftermath")

DESCRIPTION = """
Backend for **Aftermath**, an incident response agent for on-call engineers.

Typical flow: `POST /incidents` → `GET /incidents/{id}/analyze` (SSE) → `POST /incidents/{id}/resolve`.
For a demo, start with `POST /admin/seed`.

Errors always look like `{"error": {"code": "...", "message": "..."}}`.
"""


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    engine = make_engine(settings.database_url)
    agent = create_agent(settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        init_db(engine)
        with Session(engine) as session:
            if settings.auto_seed and session.exec(select(Incident.id).limit(1)).first() is None:
                result = await seed_database(session, app.state.agent)
                logger.info("Auto-seeded %d incidents", result.seeded)
            # Rebuild agent memory from the DB on every start: the mock keeps memory in-process, and the real
            # agent's memory file may be missing (fresh machine, new container, deleted .aftermath/). Ingest is
            # idempotent (upsert by incident id) and the real agent's backtest skips already-graded incidents.
            await warm_agent_from_db(session, app.state.agent)
        yield
        engine.dispose()

    app = FastAPI(
        title="Aftermath API",
        version="0.1.0",
        description=DESCRIPTION,
        lifespan=lifespan,
        openapi_tags=[
            {"name": "incidents", "description": "Create, analyze (SSE) and resolve incidents"},
            {"name": "stats", "description": "Demo metrics"},
            {"name": "admin", "description": "Seed and reset demo data"},
            {"name": "health"},
        ],
    )
    app.state.settings = settings
    app.state.engine = engine
    app.state.agent = agent

    # Added before CORS so CORS wraps it and a 413 still carries the CORS headers the browser needs.
    app.add_middleware(BodySizeLimitMiddleware, max_bytes=settings.max_request_bytes)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    register_error_handlers(app)
    app.include_router(incidents.router)
    app.include_router(stats.router)
    app.include_router(admin.router)
    return app


logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
app = create_app()
