from typing import Annotated

from fastapi import Depends, Request

from app.agent.interface import AgentService
from app.config import Settings


def get_agent(request: Request) -> AgentService:
    return request.app.state.agent


def get_app_settings(request: Request) -> Settings:
    return request.app.state.settings


AgentDep = Annotated[AgentService, Depends(get_agent)]
SettingsDep = Annotated[Settings, Depends(get_app_settings)]
