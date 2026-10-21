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
    # Conversation history sent to the LLM (chars); older turns are dropped. Lower it for small local models.
    agent_history_chars: int = 60_000

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

    # ---- Autonomous trading engine (hackathon challenge) ----
    engine_mode: Literal["backtest", "shadow", "live"] = "shadow"
    engine_decider: Literal["ai", "rules"] = "ai"       # "rules" = baseline without the LLM
    engine_llm_model: str = ""                          # defaults to llm_model
    engine_capital_tmn: float = 5_000_000               # challenge base capital (return is measured vs this)
    engine_universe: str = "auto"                       # "auto" or comma-separated TMN symbols
    engine_allowed_markets: str = ""                    # challenge's allowed list; empty = no restriction
    engine_universe_size: int = 10
    engine_max_spread_pct: float = 0.4
    engine_timeframe_min: int = 15
    engine_fee_rate: float = 0.0025                     # per side; check /v1/account/fee for the real rate
    engine_max_positions: int = 3
    engine_max_position_pct: float = 0.25               # of equity, per position
    engine_max_exposure_pct: float = 0.75               # of equity, all positions
    engine_risk_per_trade_pct: float = 0.01             # equity lost if the stop is hit
    engine_drawdown_pause_pct: float = 0.06             # from peak: no new entries for a while
    engine_drawdown_stop_pct: float = 0.12              # from peak: flatten everything, stop trading
    engine_pause_hours: float = 12
    engine_min_atr_pct: float = 0.35                    # skip markets too quiet to beat fees
    engine_max_atr_pct: float = 4.0
    engine_min_score: float = 0.5                       # rule score needed to reach the decider
    engine_setups: str = "oversold_reversion,dip_in_uptrend"  # see strategy.ALL_SETUPS
    engine_max_ai_calls_per_bar: int = 4
    engine_time_stop_bars: int = 32
    engine_cooldown_bars: int = 4
    engine_end_time: str = ""                           # ISO-8601 challenge end; flatten to TMN before it
    engine_no_entry_before_end_min: int = 240
    engine_flatten_before_end_min: int = 45
    engine_monitor_seconds: int = 15

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
