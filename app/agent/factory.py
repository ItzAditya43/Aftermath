from app.agent.interface import AgentService
from app.config import Settings


def create_agent(settings: Settings) -> AgentService:
    """Return the agent selected by AGENT_MODE ("mock" or "real")."""
    if settings.agent_mode == "real":
        from app.agent.real_agent import RealAgent  # imported lazily so mock mode never needs its deps

        return RealAgent(settings)

    from app.agent.mock_agent import MockAgent

    return MockAgent(token_delay_ms=settings.mock_token_delay_ms)
