import os
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=(ROOT / ".env", ROOT / "backend" / ".env"), extra="ignore")

    # LLM (any OpenAI-compatible endpoint)
    llm_base_url: str = "https://api.openai.com/v1"
    llm_api_key: str = ""   # falls back to OPENAI_API_KEY only when LLM_API_KEY is empty (see get_settings)
    llm_model: str = "gpt-4o"
    llm_temperature: float = 0.2
    llm_stream: bool = True
    # For strict local chat templates (e.g. Gemma in LM Studio): no enum/anyOf in tool schemas and
    # tool history sent as plain text instead of role:tool messages
    llm_compat_schemas: bool = False
    agent_max_steps: int = 8
    # Conversation history sent to the LLM (chars); older turns are dropped. Every step of a turn resends
    # it, so this is the main cost lever on paid APIs.
    agent_history_chars: int = 24_000
    # Cap on each chat reply (max_completion_tokens); 0 = provider default
    llm_max_output_tokens: int = 1500

    # Venue the app trades and reads by default (see saraf/venues); tools and endpoints can name another
    default_venue: str = "wallex"

    # ---- Venue credentials, one block per venue
    # Wallex
    wallex_base_url: str = "https://api.wallex.ir"
    wallex_api_key: str = ""
    # Trade-credit sub-account (clientId from GET /sub-accounts); empty = the main account
    wallex_sub_account_client_id: str = ""
    # Futures endpoints are not in the public docs; plug them in here once known.
    wallex_futures_base_url: str = ""

    # Trading mode: "paper" simulates fills against the venue's live prices, "live" sends real orders.
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
    # Separate endpoint/key for the engine so chat usage can't exhaust the trading engine's budget
    engine_llm_base_url: str = ""                       # defaults to llm_base_url
    engine_llm_api_key: str = ""                        # defaults to llm_api_key
    # JSON merged into the LLM request body. Thinking models must answer directly, e.g. for Qwen on
    # GapGPT: {"reasoning_effort": "none"} (otherwise its reasoning eats the token budget: no answer)
    engine_llm_extra_body: str = ""
    engine_capital_tmn: float = 5_000_000               # challenge base capital (return is measured vs this)
    engine_universe: str = "auto"                       # "auto" or comma-separated TMN symbols
    engine_allowed_markets: str = ""                    # challenge's allowed list; empty = no restriction
    # "credit" = the markets disabled on trade-credit accounts (engine/markets.py), or a comma list, or empty
    engine_blocked_markets: str = "credit"
    engine_universe_size: int = 10
    engine_max_spread_pct: float = 0.4
    engine_timeframe_min: int = 15
    engine_fee_rate: float = 0.0025                     # taker, per side; check /v1/account/fee for the real rate
    engine_maker_fee_rate: float = -1.0                 # maker; <0 = same as the taker fee (conservative)
    # "maker": enter with a limit at the bid and rest the take-profit as a limit sell (no spread crossing);
    # stops and emergency exits stay market orders. "taker": market orders everywhere.
    engine_execution: Literal["maker", "taker"] = "maker"
    engine_entry_timeout_bars: int = 2                  # cancel an unfilled entry limit after this many bars
    # Cancel the entry limit once the ask moves this many ATRs above where it was at order time. Chosen
    # in-sample (14d to 2026-10-14): tighter was monotonically better - a limit that only fills after the
    # bounce has failed is adverse selection. See STRATEGY.md section 5.
    engine_entry_chase_atr: float = 0.1
    engine_max_positions: int = 3
    engine_max_position_pct: float = 0.25               # of equity, per position
    engine_max_exposure_pct: float = 0.75               # of equity, all positions
    engine_risk_per_trade_pct: float = 0.01             # equity lost if the stop is hit
    engine_drawdown_pause_pct: float = 0.06             # from peak: no new entries for a while
    engine_drawdown_stop_pct: float = 0.12              # from peak: flatten everything, stop trading
    engine_pause_hours: float = 12
    engine_min_atr_pct: float = 0.35                    # skip markets too quiet to beat fees
    engine_max_atr_pct: float = 4.0
    engine_dip_atr: float = 0.0                         # >0: dip_in_uptrend needs a 1h fall of this many ATRs (0 = fixed -1.5%)
    engine_min_score: float = 0.5                       # rule score needed to reach the decider
    engine_setups: str = "oversold_reversion,dip_in_uptrend"  # see strategy.ALL_SETUPS
    engine_max_ai_calls_per_bar: int = 4
    # Hard breadth gate: no new entries while fewer than this % of TMN markets are up over 24h (dip
    # buying into a broad sell-off was the biggest loss source in-sample). The AI can only be stricter.
    engine_breadth_gate_pct: float = 25.0
    # AI position reviews: "off" (exits follow the rules), "tighten" (AI may only raise stops), "full"
    # (AI may also exit). In-sample, AI exits cut winners before their targets, so the default is off.
    engine_ai_reviews: Literal["off", "tighten", "full"] = "off"
    engine_time_stop_bars: int = 32
    engine_cooldown_bars: int = 4
    engine_end_time: str = ""                           # ISO-8601 challenge end; flatten to TMN before it
    engine_no_entry_before_end_min: int = 240
    engine_flatten_before_end_min: int = 45
    # Strategy (docs/STRATEGY.md): "USDT" = USDT core + dip satellite on X/USDT markets; "TMN" = TMN markets only
    engine_quote: Literal["TMN", "USDT"] = "TMN"
    engine_core_enabled: bool = True                    # hold free TMN as USDT (only with ENGINE_QUOTE=USDT)
    engine_core_tmn_reserve: float = 30_000             # TMN left unconverted
    engine_core_guard_pct: float = 0.06                 # USDT/TMN this far below its peak -> core to TMN
    engine_core_exit_hours: float = 6                   # exit window before the challenge end
    engine_core_exit_slices: int = 3                    # USDT -> TMN in this many AI-timed slices
    engine_core_fee_rate: float = 0.003                 # taker fee on USDTTMN (TMN market) for backtests
    engine_monitor_seconds: int = 15
    # At the challenge end also sell coins the engine doesn't manage (bought by hand or through the chat assistant):
    # the challenge counts realized TMN, so nothing may be left in coins
    engine_final_sweep: bool = True
    engine_follow_address: str = ""   # a Hyperliquid account the engine copies (copy-trading a real trader); "" = off
    engine_follow_pct: float = 0.5    # at most this share of equity follows that trader's long positions

    # News / web
    tavily_api_key: str = ""

    data_dir: Path = ROOT / "data"

    @property
    def maker_fee(self) -> float:
        return self.engine_fee_rate if self.engine_maker_fee_rate < 0 else self.engine_maker_fee_rate

    @property
    def allowlist(self) -> set[str]:
        return {s.strip().upper() for s in self.symbol_allowlist.split(",") if s.strip()}


@lru_cache
def get_settings() -> Settings:
    s = Settings()
    # LLM_API_KEY (shell or .env) must win; a stray OPENAI_API_KEY in the shell used to override the
    # .env key because shell variables outrank .env in pydantic-settings.
    if not s.llm_api_key and os.environ.get("OPENAI_API_KEY"):
        s.llm_api_key = os.environ["OPENAI_API_KEY"]
    s.data_dir.mkdir(parents=True, exist_ok=True)
    return s
