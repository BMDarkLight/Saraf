from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=(ROOT / ".env", ROOT / "backend" / ".env"), extra="ignore")

    # LLM (any OpenAI-compatible endpoint)
    llm_base_url: str = "https://api.openai.com/v1"
    llm_api_key: str = Field("", validation_alias=AliasChoices("LLM_API_KEY", "OPENAI_API_KEY"))
    llm_model: str = "gpt-4o"
    llm_temperature: float = 0.2
    llm_stream: bool = True
    # For strict local chat templates (e.g. Gemma in LM Studio): no enum/anyOf in tool schemas and
    # tool history sent as plain text instead of role:tool messages
    llm_compat_schemas: bool = False
    agent_max_steps: int = 12

    # Wallex
    wallex_base_url: str = "https://api.wallex.ir"
    wallex_api_key: str = ""
    # Futures endpoints are not in the public docs; plug them in here once known.
    wallex_futures_base_url: str = ""

    # Trading mode: "paper" simulates fills against live Wallex prices, "live" sends real orders.
    spot_mode: Literal["paper", "live"] = "paper"
    futures_mode: Literal["paper", "live"] = "paper"
    require_confirmation: bool = True

    # Risk limits (applied to both paper and live)
    max_order_notional_usdt: float = 500.0
    max_order_notional_tmn: float = 50_000_000.0
    max_leverage: int = 10
    max_open_positions: int = 10
    symbol_allowlist: str = ""  # comma separated, empty = all

    # Paper starting balances
    paper_usdt: float = 1000.0
    paper_tmn: float = 100_000_000.0

    # News / web
    tavily_api_key: str = ""

    data_dir: Path = ROOT / "data"

    @property
    def allowlist(self) -> set[str]:
        return {s.strip().upper() for s in self.symbol_allowlist.split(",") if s.strip()}


@lru_cache
def get_settings() -> Settings:
    s = Settings()
    s.data_dir.mkdir(parents=True, exist_ok=True)
    return s
