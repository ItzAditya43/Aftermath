import hmac
from typing import Annotated

from fastapi import Depends, Header, Request

from app.agent.interface import AgentService
from app.config import Settings
from app.errors import AppError

# Hosts that count as "this machine" when no admin key is configured. "testclient" is the host Starlette's
# TestClient reports; it can't come from a real socket.
_LOCAL_HOSTS = {"127.0.0.1", "::1", "localhost", "testclient"}


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
