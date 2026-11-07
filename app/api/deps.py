import hmac
from typing import Annotated

from fastapi import Depends, Header, Request

from app.agent.interface import AgentService
from app.config import Settings
from app.errors import AppError

# Hosts that count as "this machine" when no admin key is configured. Taken from the socket, never from headers.
_LOCAL_HOSTS = {"127.0.0.1", "::1", "localhost"}


def get_agent(request: Request) -> AgentService:
    return request.app.state.agent


def get_app_settings(request: Request) -> Settings:
    return request.app.state.settings


AgentDep = Annotated[AgentService, Depends(get_agent)]
SettingsDep = Annotated[Settings, Depends(get_app_settings)]


def require_admin(
    request: Request,
    settings: SettingsDep,
    x_admin_key: Annotated[str | None, Header(description="Required when ADMIN_API_KEY is set")] = None,
) -> None:
    """Guard destructive admin routes.

    With ADMIN_API_KEY set, the X-Admin-Key header must match it (constant-time compare), otherwise 401.
    Without a key, only requests from localhost are allowed (403 otherwise), so local demos keep working
    while anyone else on the network, or a deployed instance, cannot seed or wipe the data.
    """
    if settings.admin_api_key:
        if x_admin_key is None or not hmac.compare_digest(x_admin_key.encode(), settings.admin_api_key.encode()):
            raise AppError(401, "unauthorized", "Missing or invalid X-Admin-Key header")
        return
    host = request.client.host if request.client else None
    if host not in _LOCAL_HOSTS:
        raise AppError(403, "forbidden", "Admin routes are only available from localhost unless ADMIN_API_KEY is set")


def require_api_key(
    settings: SettingsDep,
    x_api_key: Annotated[str | None, Header(description="Required when API_KEY is set")] = None,
) -> None:
    """When API_KEY is set, create / analyze / resolve need a matching X-API-Key header (401 otherwise)."""
    if settings.api_key and (
        x_api_key is None or not hmac.compare_digest(x_api_key.encode(), settings.api_key.encode())
    ):
        raise AppError(401, "unauthorized", "Missing or invalid X-API-Key header")


def _rate_limit(bucket: str):
    def check(request: Request) -> None:
        limiter = request.app.state.rate_limiters[bucket]
        client = request.client.host if request.client else "unknown"
        wait = limiter.check(client)
        if wait is not None:
            raise AppError(
                429, "rate_limited",
                f"Too many requests; at most {limiter.limit} per minute. Retry in {limiter.retry_after_header(wait)} s.",
                headers={"Retry-After": limiter.retry_after_header(wait)},
            )

    return check


# Order matters: the key is checked first, so unauthenticated calls never use up a client's quota.
AnalyzeGuard = [Depends(require_api_key), Depends(_rate_limit("analyze"))]
WriteGuard = [Depends(require_api_key), Depends(_rate_limit("write"))]
