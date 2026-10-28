"""Capture the README screenshots from the demo app into docs/screenshots/.

  uv run python scripts/mock_llm.py &                                    # scripted LLM on :1234
  LLM_BASE_URL=http://127.0.0.1:1234/v1 uv run python scripts/ui_demo.py &   # fake venue on :8000
  uv run --with playwright python scripts/screenshots.py [--chromium /path/to/chrome]
Each chat shot runs in a fresh browser context, so it shows one question and the answer the mock LLM scripts for it.
"""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from playwright.async_api import Browser, Page, async_playwright

URL = "http://127.0.0.1:8000/"
OUT = Path(__file__).resolve().parents[2] / "docs" / "screenshots"
DESKTOP = {"width": 1440, "height": 900}
MOBILE = {"width": 390, "height": 844}

# file name -> message; the keywords match the rules in scripts/mock_llm.py
CHATS = {
    "chat-heatmap": "نقشه حرارتی بازار تتری را نشان بده (heatmap)",
    "chat-chart": "نمودار بیت‌کوین با حمایت و مقاومت (chart)",
    "chat-compare": "مقایسه BTC و ETH و SOL در ۷۲ ساعت (compare)",
    "chat-planner": "پلنر شورت سولانا با اهرم ۴ (planner)",
    "chat-ticket": "برگه سفارش لانگ اتریوم (ticket)",
    "chat-confirm": "یک پوزیشن لانگ اتریوم با اهرم ۳ باز کن (long)",
    "chat-report": "گزارش سریع بازار (report)",
}


async def new_page(b: Browser, viewport: dict, mobile: bool = False) -> Page:
    ctx = await b.new_context(viewport=viewport, color_scheme="dark", device_scale_factor=2,
                              is_mobile=mobile, has_touch=mobile, locale="fa-IR")
    page = await ctx.new_page()
    await page.goto(URL)
    await page.wait_for_load_state("networkidle")
    await page.wait_for_timeout(1500)   # let charts and the order book draw
    return page


async def shot(page: Page, name: str) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    await page.screenshot(path=str(OUT / f"{name}.png"))
    await page.context.close()
    print(f"ok   {name}.png")


async def ask(page: Page, text: str) -> None:
    box = page.locator("textarea")
    await box.fill(text)
    await box.press("Enter")
    await page.wait_for_timeout(6000)   # tool calls, the streamed reply and the widget's own data fetches


async def tab(page: Page, label: str) -> None:
    await page.get_by_role("button", name=label, exact=True).first.click()
    await page.wait_for_timeout(2000)


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--chromium", default="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
    args = ap.parse_args()
    async with async_playwright() as p:
        b = await p.chromium.launch(executable_path=args.chromium)

        await shot(await new_page(b, DESKTOP), "home")
        for name, text in CHATS.items():
            page = await new_page(b, DESKTOP)
            await ask(page, text)
            await shot(page, name)

        page = await new_page(b, DESKTOP)
        await tab(page, "بازار")
        await shot(page, "markets")

        for label, name in (("نمای کلی", "strategy-overview"), ("تنظیمات", "strategy-tuning"),
                            ("کپی‌تریدینگ", "strategy-personas"), ("دستیار", "strategy-chat"), ("شواهد", "strategy-evidence")):
            page = await new_page(b, DESKTOP)
            await tab(page, "استراتژی")
            await page.get_by_role("tab", name=label).click()
            await page.wait_for_timeout(1500)
            await shot(page, name)

        page = await new_page(b, MOBILE, mobile=True)
        await ask(page, CHATS["chat-heatmap"])
        await shot(page, "mobile-chat")
        await b.close()


if __name__ == "__main__":
    asyncio.run(main())
