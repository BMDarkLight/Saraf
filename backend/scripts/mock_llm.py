"""Scripted OpenAI-compatible LLM for UI/e2e testing without real inference.

Picks tool calls from keywords in the user's message, then streams a short reply once
tool results come back. Run: uv run python scripts/mock_llm.py  (serves :1234/v1)
"""

import json
import time
import uuid

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse

app = FastAPI()

RULES = [
    (("حرارتی", "heatmap"), [("show_market_heatmap", {"quote": "USDT", "limit": 30})]),
    (("مقایسه", "compare"), [("show_comparison_chart", {"symbols": "BTCUSDT,ETHUSDT,SOLUSDT", "hours": 72})]),
    (("پلنر", "planner"), [("show_position_planner", {"symbol": "SOLUSDT", "side": "SHORT", "leverage": 4, "margin": 50})]),
    (("تست هشدار", "test alert"), [("create_price_alert", {"symbol": "BTCUSDT", "condition": "above", "price": 1,
                                                           "note": "هشدار آزمایشی که فوراً فعال می‌شود"})]),
    (("هشدار", "alert"), [("create_price_alert", {"symbol": "BTCUSDT", "condition": "below", "price": 80000,
                                                   "note": "حمایت ۸۰ هزار"})]),
    (("برگه", "ticket"), [("show_trade_ticket", {"market": "futures", "symbol": "ETHUSDT", "side": "LONG",
                                                  "amount": 50, "leverage": 3, "note": "پیشنهاد اولیه؛ قابل ویرایش"})]),
    (("گزارش", "report"), [("render_dashboard", {"title": "گزارش سریع بازار", "blocks": [
        {"type": "metric", "label": "BTC", "value": "83,300", "delta": 2.4, "tone": "good"},
        {"type": "metric", "label": "ETH", "value": "2,455", "delta": -0.8},
        {"type": "metric", "label": "ترس و طمع", "value": "63", "tone": "info"},
        {"type": "progress", "label": "بازارهای مثبت", "percent": 68, "tone": "good"},
        {"type": "table", "columns": ["بازار", "تغییر"], "rows": [["NEARUSDT", "+3.4%"], ["XRPUSDT", "+2.1%"]]},
        {"type": "callout", "tone": "warning", "label": "ریسک", "text": "اسپرد در آلتکوین‌های کم‌حجم بالاست."}]})]),
    (("نمودار", "chart"), [("show_price_chart", {"symbol": "BTCUSDT", "resolution": "60", "levels": [
        {"price": 81300, "label": "Support", "kind": "support"}, {"price": 84500, "label": "Resistance", "kind": "resistance"}]})]),
    (("لانگ", "long"), [("futures_open_position", {"symbol": "ETHUSDT", "side": "LONG", "leverage": 3, "margin": 50,
                                                  "reason": "روند صعودی ساعتی"})]),
    (("عمق", "depth"), [("order_book", {"symbol": "BTCUSDT", "levels": 8})]),
]
DEFAULT = [("top_movers", {"hours": 4, "quote": "USDT", "limit": 5}), ("fear_greed_index", {})]


def sse(obj) -> str:
    return f"data: {json.dumps(obj, ensure_ascii=False)}\n\n"


def chunk(delta: dict, finish=None) -> dict:
    return {"id": "mock", "object": "chat.completion.chunk", "created": int(time.time()), "model": "mock",
            "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}


@app.get("/v1/models")
def models():
    return {"data": [{"id": "mock", "object": "model"}]}


@app.post("/v1/chat/completions")
async def completions(req: Request):
    body = await req.json()
    msgs = body["messages"]
    last = msgs[-1]
    after_tools = last["role"] == "tool" or str(last.get("content", "")).startswith("[tool result")
    notice = str(last.get("content", "")).startswith("[system notice]")
    user_text = next((m["content"] for m in reversed(msgs) if m["role"] == "user"
                      and not str(m["content"]).startswith(("[tool result", "[system notice]"))), "")

    def gen():
        yield sse(chunk({"role": "assistant", "reasoning_content": "بررسی درخواست کاربر و انتخاب ابزار مناسب… "}))
        if "slow" in user_text.lower() and not (after_tools or notice):  # long reply, to test the Stop button
            for i in range(80):
                yield sse(chunk({"content": f"کلمه{i} "}))
                time.sleep(0.25)
        elif after_tools or notice:
            text = ("نتیجه اقدام ثبت شد. ✅" if notice else
                    "نتیجه را در کامپوننت بالا می‌بینید. **خلاصه:** داده‌ها از والکس به‌صورت زنده گرفته شد.")
            for w in text.split(" "):
                yield sse(chunk({"content": w + " "}))
                time.sleep(0.02)
        else:
            calls = next((c for keys, c in RULES if any(k in user_text.lower() for k in keys)), DEFAULT)
            for i, (name, args) in enumerate(calls):
                yield sse(chunk({"tool_calls": [{"index": i, "id": f"call_{uuid.uuid4().hex[:6]}", "type": "function",
                                                 "function": {"name": name, "arguments": json.dumps(args, ensure_ascii=False)}}]}))
        yield sse(chunk({}, "stop"))
        yield "data: [DONE]\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream")


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=1234)
