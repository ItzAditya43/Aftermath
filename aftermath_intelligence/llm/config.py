"""LLM configuration read from environment variables (never from code)."""
from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

SUPPORTED_PROVIDERS = ("groq", "gemini", "ollama")

DEFAULT_MODELS = {
    # Verified against the Groq and Gemini model lists in Nov 2026; override with *_MODEL.
    "groq": "openai/gpt-oss-120b",
    "gemini": "gemini-3.5-flash-lite",
    "ollama": "gpt-oss:120b-cloud",
}


@dataclass(frozen=True)
class ProviderConfig:
    name: str
    model: str
    api_key: str = ""
    base_url: str | None = None

    @property
    def configured(self) -> bool:
        return self.name == "ollama" or bool(self.api_key)


@dataclass(frozen=True)
class LLMSettings:
    providers: tuple[ProviderConfig, ...]
    timeout_seconds: float = 30.0
    max_retries: int = 2              # per provider, only for retryable errors
    max_retry_delay_seconds: float = 4.0
    circuit_failure_threshold: int = 3
    circuit_cooldown_seconds: float = 60.0
    cache_enabled: bool = True
    cache_dir: Path | None = None
    extra: dict[str, str] = field(default_factory=dict)

    @property
    def order(self) -> tuple[str, ...]:
        return tuple(p.name for p in self.providers)

    def provider(self, name: str) -> ProviderConfig | None:
        return next((p for p in self.providers if p.name == name), None)

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None, *, overrides: Mapping[str, str] | None = None,
                 dotenv: Path | None = Path(".env")) -> "LLMSettings":
        env: dict[str, str] = {}
        if environ is None:
            env.update(read_dotenv(dotenv) if dotenv else {})
            env.update(os.environ)
        else:
            env.update(environ)
        env.update({k: v for k, v in (overrides or {}).items() if v})

        primary = (env.get("AI_PROVIDER") or "groq").strip().lower()
        chain = [primary] + [p.strip().lower() for p in (env.get("AI_FALLBACK_PROVIDER") or "").split(",") if p.strip()]
        for name in chain:
            if name not in SUPPORTED_PROVIDERS:
                raise ValueError(f"Unsupported AI provider {name!r}; use one of {', '.join(SUPPORTED_PROVIDERS)}")
        order = list(dict.fromkeys(chain))

        def config(name: str) -> ProviderConfig:
            upper = name.upper()
            return ProviderConfig(
                name=name,
                model=(env.get(f"{upper}_MODEL") or DEFAULT_MODELS[name]).strip(),
                api_key=(env.get(f"{upper}_API_KEY") or "").strip(),
                base_url=(env.get(f"{upper}_BASE_URL") or "").strip() or None,
            )

        cache_dir = env.get("AFTERMATH_CACHE_DIR")
        return cls(
            providers=tuple(config(name) for name in order),
            timeout_seconds=float(env.get("AFTERMATH_LLM_TIMEOUT_SECONDS") or 30),
            max_retries=int(env.get("AFTERMATH_LLM_MAX_RETRIES") or 2),
            cache_enabled=(env.get("AFTERMATH_LLM_CACHE", "1") != "0"),
            cache_dir=Path(cache_dir) if cache_dir else None,
        )


def read_dotenv(path: Path | None) -> dict[str, str]:
    """Parse simple KEY=value lines without shell evaluation."""
    values: dict[str, str] = {}
    if not path or not path.is_file():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip("\"'")
    return values
