"""End-to-end check of the strategy controls in a real browser, against scripts/ui_demo.py.

  uv run python scripts/ui_demo.py &          # http://127.0.0.1:8000 (fake data, no systemd, scripted assistant)
  uv run --with playwright python scripts/ui_e2e.py [--chromium /path/to/chrome]
Every step clicks what a person clicks and then checks both the page and the files the server wrote
(data/engine/control.json, tuning.env, copy.json). Exits non-zero on the first failure.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from playwright.async_api import Page, async_playwright

URL = "http://127.0.0.1:8000/"
ENG = Path(__file__).resolve().parents[2] / "data" / "engine"
passed: list[str] = []


def ok(cond: bool, what: str) -> None:
    if not cond:
        print(f"FAIL: {what}")
        sys.exit(1)
    passed.append(what)
    print(f"ok   {what}")


def env() -> dict:
    f = ENG / "tuning.env"
    if not f.exists():
        return {}
    return dict(line.split("=", 1) for line in f.read_text().splitlines() if "=" in line and not line.startswith("#"))


def control() -> dict:
    try:
        return json.loads((ENG / "control.json").read_text())
    except (OSError, ValueError):
        return {}


async def checked(pg: Page, sel: str) -> bool:
    return await pg.locator(sel).first.get_attribute("aria-checked") == "true"


async def confirm(pg: Page) -> None:
    await pg.locator(".modal .modal-actions .btn").first.click()
    await pg.wait_for_selector(".modal", state="detached", timeout=5000)


async def view(pg: Page, name: str) -> None:
    await pg.click(f".view-switch >> text={name}")
    await pg.wait_for_timeout(400)


CARD_SW = ".rail-card .card-h [role=switch]"
HEAD_SW = ".spage-h [role=switch]"


async def desktop(pg: Page) -> None:
    await pg.goto(URL)
    await pg.wait_for_selector(CARD_SW)

    # --- one on/off state, the same on both pages
    ok(await checked(pg, CARD_SW), "engine card switch starts on")
    await pg.click(CARD_SW)
    await pg.wait_for_function(f"document.querySelector('{CARD_SW}').getAttribute('aria-checked') === 'false'")
    ok(control().get("paused") is True, "switching off on the trade page writes paused=true")
    await view(pg, "استراتژی")
    await pg.wait_for_selector(HEAD_SW)
    ok(not await checked(pg, HEAD_SW), "strategy header switch shows off at once (same state)")
    ok(await pg.locator(".spage-h .run-toggle").count() == 0, "no second, different toggle in the strategy header")
    await pg.click(HEAD_SW)                                   # live mode: turning on asks first
    await pg.wait_for_selector(".modal")
    ok(control().get("paused") is True, "turning on in live mode waits for the confirmation")
    await confirm(pg)
    await pg.wait_for_function(f"document.querySelector('{HEAD_SW}').getAttribute('aria-checked') === 'true'")
    ok(control().get("paused") is False, "confirmed on the strategy page writes paused=false")
    await view(pg, "معامله")
    await pg.wait_for_function(f"document.querySelector('{CARD_SW}').getAttribute('aria-checked') === 'true'", timeout=3000)
    ok(True, "trade page card follows the strategy page's switch")

    # --- settings: a number and the end time (Tehran time in, UTC saved)
    await view(pg, "استراتژی")
    await pg.click(".stabs >> text=تنظیمات")
    await pg.wait_for_selector("#f-engine_max_positions")
    ok(await pg.locator(".run-panel .run-toggle").count() == 1, "real/simulated account choice lives on the settings tab")
    await pg.fill("#f-engine_max_positions", "2")
    await pg.fill("#f-engine_end_time", "2026-10-26T22:00")
    await pg.wait_for_selector(".savebar")
    ok("2" in (await pg.locator(".savebar b").inner_text()), "save bar counts the 2 unsaved changes")
    await pg.click(".savebar >> text=ذخیره و اعمال")
    await pg.wait_for_selector(".modal .diff")
    await confirm(pg)
    e = env()
    ok(e.get("ENGINE_MAX_POSITIONS") == "2", "max positions saved to tuning.env")
    ok(e.get("ENGINE_END_TIME") == "2026-10-26T18:30:00Z", "end time 22:00 Tehran saved as 18:30Z")
    await pg.wait_for_timeout(800)
    ok(await pg.locator(".savebar").count() == 0, "save bar gone after saving")

    # bad pair: a 5% stop under the running 6% pause is refused with a message, nothing written
    await pg.fill("#f-engine_drawdown_stop_pct", "5")
    await pg.click(".savebar >> text=ذخیره و اعمال")
    await pg.locator(".modal .modal-actions .btn").first.click()
    await pg.wait_for_selector(".modal .err-box")
    ok("pause" in await pg.locator(".modal .err-box").inner_text() and "ENGINE_DRAWDOWN_STOP_PCT" not in env(),
       "a 5% stop below the running 6% pause is refused, not saved")
    await pg.click(".modal >> text=انصراف")
    await pg.click(".savebar >> text=لغو تغییرات")

    # --- assistant: applies itself, undo reverts only its own change; proposal applied by hand
    await pg.click(".stabs >> text=دستیار")
    await pg.wait_for_selector(".schat textarea")
    ok(await checked(pg, ".schat-h [role=switch]"), "direct-apply is on by default")
    await pg.fill(".schat textarea", "ریسک را نصف کن")
    await pg.keyboard.press("Enter")
    await pg.wait_for_selector(".proposal.applied")
    ok(env().get("ENGINE_RISK_PER_TRADE_PCT") == "0.005", "assistant applied risk 0.5% itself")
    await pg.click(".proposal.applied >> text=برگرداندن")
    await confirm(pg)
    await pg.wait_for_selector(".proposal.applied.undone")
    e = env()
    ok("ENGINE_RISK_PER_TRADE_PCT" not in e, "undo removed the assistant's change")
    ok(e.get("ENGINE_MAX_POSITIONS") == "2" and e.get("ENGINE_END_TIME") == "2026-10-26T18:30:00Z",
       "undo kept the settings saved by hand (max positions, end time)")
    ok(await pg.locator(".proposal.applied >> text=برگرداندن").count() == 0, "no second undo button")
    await pg.fill(".schat textarea", "دروازه چطور؟")
    await pg.keyboard.press("Enter")
    await pg.wait_for_selector(".proposal:not(.applied) >> text=اعمال")
    ok("ENGINE_BREADTH_GATE_PCT" not in env(), "a plain question only proposes")
    await pg.click(".proposal:not(.applied) .btn")
    await confirm(pg)
    ok(env().get("ENGINE_BREADTH_GATE_PCT") == "30.0", "proposal applied from its button")

    # history: list, new chat, reopen, delete
    await pg.click(".schat-h >> text=گفتگوها")
    await pg.wait_for_selector(".hist-row")
    ok(await pg.locator(".hist-row").count() == 1, "the conversation is listed")
    await pg.click(".schat-h >> text=گفتگوی تازه")
    ok(await pg.locator(".smsg").count() == 0, "new chat starts empty")
    await pg.click(".schat-h >> text=گفتگوها")
    await pg.click(".hist-open")
    await pg.wait_for_selector(".smsg")
    ok(await pg.locator(".smsg").count() == 4 and await pg.locator(".proposal.applied.undone").count() == 1,
       "reopened chat has its 4 messages and the undone change")
    await pg.click(".schat-h >> text=گفتگوها")
    await pg.click(".hist-del")
    await pg.wait_for_timeout(500)
    ok(await pg.locator(".hist-row").count() == 0, "conversation deleted")

    # --- copy-trading keeps the person's end time
    await pg.click(".stabs >> text=کپی‌تریدینگ")
    await pg.locator(".p-card:not(.trader)").nth(4).click()     # cautious (the live trader cards come first)
    await pg.click("text=کپی این پرسونا")
    await confirm(pg)
    e = env()
    ok(e.get("ENGINE_BREADTH_GATE_PCT") == "40.0" and e.get("ENGINE_MAX_POSITIONS") == "1", "persona preset applied")
    ok(e.get("ENGINE_END_TIME") == "2026-10-26T18:30:00Z", "copying a persona kept the custom end time")
    await pg.click("text=توقف کپی")
    await confirm(pg)
    ok(env() == {"ENGINE_END_TIME": "2026-10-26T18:30:00Z"}, "stopping the copy keeps only the end time")

    # --- live trader personas: listed, profile opens, a trader added by address is followed
    await pg.click("text=→ همه پرسوناها")
    await pg.wait_for_selector(".p-card.trader")
    ok(await pg.locator(".p-card.trader").count() >= 1, "live trader cards are listed above the personas")
    await pg.locator(".p-card.trader").first.click()
    await pg.wait_for_selector(".pos-table")
    ok(await pg.locator(".live-chip").count() >= 1, "trader profile shows live positions")
    await pg.click(".profile-id >> text=کپی زنده")
    await pg.wait_for_selector(".modal .share")
    await pg.locator(".modal .share input").fill("40")
    await confirm(pg)
    e = env()
    ok(e.get("ENGINE_FOLLOW_ADDRESS", "").startswith("0x") and e.get("ENGINE_FOLLOW_PCT") == "0.4",
       "«کپی زنده» points the engine at the trader with 40% of the account")
    ok(e.get("ENGINE_END_TIME") == "2026-10-26T18:30:00Z", "live copy keeps the custom end time")
    ok(json.loads((ENG / "copy.json").read_text())["kind"] == "trader", "the copy is recorded as a trader copy")
    await pg.wait_for_selector(".profile-id >> text=توقف کپی")
    await pg.click(".profile-id >> text=توقف کپی")
    await confirm(pg)
    ok("ENGINE_FOLLOW_ADDRESS" not in env() and not (ENG / "copy.json").exists(), "stopping the live copy clears it")
    await pg.click("text=→ همه")
    await pg.click("text=+ افزودن تریدر")
    addr = "0x" + "ab" * 20
    await pg.fill(".add-trader input.ltr", addr)
    await pg.fill(".add-trader input:not(.ltr)", "Famous Trader")
    await pg.click(".add-trader >> text=دنبال کردن")
    await pg.wait_for_timeout(600)
    pinned = json.loads((ENG / "traders" / "pinned.json").read_text())
    ok(pinned[0]["address"] == addr and pinned[0]["name"] == "Famous Trader", "a trader added by address is saved")
    ok(await pg.locator(".p-card.trader >> text=Famous Trader").count() == 1, "the added trader appears in the list")
    await pg.locator(".p-card.trader >> text=Famous Trader").click()
    await pg.click("text=حذف از فهرست")
    await pg.wait_for_timeout(600)
    ok(json.loads((ENG / "traders" / "pinned.json").read_text()) == [], "the added trader can be removed")

    # --- move a coin bought outside the engine into the core
    await view(pg, "معامله")
    await pg.click("text=به هسته تتری")
    await pg.wait_for_selector(".modal")
    await confirm(pg)
    ok(control().get("consolidate") == ["USOON"] and control().get("paused") is False,
       "«به هسته تتری» requests USOON and keeps the switch state")


async def mobile(pg: Page) -> None:
    await pg.goto(URL)
    await pg.wait_for_selector(".mobile-tabs")
    for name in ("بازار", "ایجنت", "دارایی", "استراتژی"):
        await pg.click(f".mobile-tabs >> text={name}")
        await pg.wait_for_timeout(300)
        wide = await pg.evaluate("document.documentElement.scrollWidth > window.innerWidth + 1")
        ok(not wide, f"mobile «{name}»: no sideways scroll")
    ok(await pg.locator(HEAD_SW).is_visible(), "mobile strategy page shows the on/off switch")


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--chromium", default="/opt/pw-browsers/chromium-1194/chrome-linux/chrome")
    args = ap.parse_args()
    for f in ("tuning.env", "control.json", "copy.json", "traders/pinned.json"):
        (ENG / f).unlink(missing_ok=True)
    async with async_playwright() as p:
        b = await p.chromium.launch(executable_path=args.chromium)
        try:
            await desktop(await b.new_page(viewport={"width": 1440, "height": 950}))
            await mobile(await b.new_page(viewport={"width": 390, "height": 844}, is_mobile=True))
        finally:
            await b.close()
    print(f"\nall {len(passed)} checks passed")


if __name__ == "__main__":
    asyncio.run(main())
