"""Explicit deployment modes; a pilot cannot silently fall back to demo storage or identity."""
from dataclasses import dataclass
import os


@dataclass(frozen=True)
class Settings:
    mode: str = "demo"
    session_days: int = 30
    max_sessions: int = 1000
    max_live_sessions: int = 8
    max_session_records: int = 500

    @classmethod
    def from_env(cls):
        if any(os.getenv(k, "").lower() in {"true", "1", "yes"} for k in ("LANGSMITH_TRACING", "LANGCHAIN_TRACING_V2", "LANGCHAIN_TRACING")):
            raise ValueError("disable implicit model tracing before serving customer records")
        value = cls(mode=os.getenv("ATEZAIN_MODE", "demo"),
                    session_days=int(os.getenv("ATEZAIN_SESSION_DAYS", "30")),
                    max_sessions=int(os.getenv("ATEZAIN_MAX_SESSIONS", "1000")),
                    max_live_sessions=int(os.getenv("ATEZAIN_MAX_LIVE_SESSIONS", "8")),
                    max_session_records=int(os.getenv("ATEZAIN_MAX_RECORDS", "500")))
        if value.mode not in {"demo", "pilot"}:
            raise ValueError("ATEZAIN_MODE must be demo or pilot")
        if min(value.session_days, value.max_sessions, value.max_live_sessions, value.max_session_records) <= 0:
            raise ValueError("resource limits must be positive")
        if value.mode == "pilot":
            if not (os.getenv("ATEZAIN_DSN") or os.getenv("DATABASE_URL")):
                raise ValueError("pilot mode requires persistent Postgres storage")
            if len(os.getenv("ATEZAIN_OWNER_TOKEN", "")) < 32:
                raise ValueError("pilot mode requires an owner token of at least 32 characters")
            if os.getenv("ATEZAIN_MODEL", "stub") != "groq" or not os.getenv("GROQ_API_KEY"):
                raise ValueError("pilot mode requires the configured model and its key")
        return value
