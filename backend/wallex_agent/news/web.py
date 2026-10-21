"""Internet tools: crypto news feeds, web/news search and page reading."""

from __future__ import annotations

import asyncio
import email.utils
import re
import time
import xml.etree.ElementTree as ET

import httpx
from bs4 import BeautifulSoup

FEEDS = {
    "cointelegraph": "https://cointelegraph.com/rss",
    "coindesk": "https://www.coindesk.com/arc/outboundfeeds/rss/",
    "decrypt": "https://decrypt.co/feed",
    "theblock": "https://www.theblock.co/rss.xml",
    "bitcoinmagazine": "https://bitcoinmagazine.com/.rss/full/",
    "arzdigital": "https://arzdigital.com/feed/",
}
UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/537.36 Chrome/126 Safari/537.36"}


def _clean(html: str, limit: int = 300) -> str:
    text = BeautifulSoup(html or "", "html.parser").get_text(" ", strip=True)
    return text[:limit] + ("…" if len(text) > limit else "")


def _ts(s: str | None) -> float:
    if not s:
        return 0.0
    try:
        return email.utils.parsedate_to_datetime(s).timestamp()
    except (TypeError, ValueError):
        return 0.0


async def _fetch_feed(http: httpx.AsyncClient, name: str, url: str) -> list[dict]:
    try:
        r = await http.get(url)
        root = ET.fromstring(r.content)
    except Exception:
        return []
    items = []
    for it in root.iter("item"):
        pub = it.findtext("pubDate")
        items.append({"source": name, "title": (it.findtext("title") or "").strip(),
                      "url": (it.findtext("link") or "").strip(), "published": pub, "_ts": _ts(pub),
                      "summary": _clean(it.findtext("description") or "")})
    return items


async def crypto_news(query: str | None = None, limit: int = 12, hours: int = 48) -> list[dict]:
    """Latest headlines from major crypto outlets, optionally filtered by keyword(s)."""
    async with httpx.AsyncClient(timeout=10, headers=UA, follow_redirects=True) as http:
        results = await asyncio.gather(*(_fetch_feed(http, n, u) for n, u in FEEDS.items()))
    items = [i for feed in results for i in feed]
    cutoff = time.time() - hours * 3600
    items = [i for i in items if not i["_ts"] or i["_ts"] >= cutoff]
    if query:
        terms = [t.lower() for t in re.split(r"[,\s]+", query) if t]
        items = [i for i in items if any(t in (i["title"] + " " + i["summary"]).lower() for t in terms)]
    items.sort(key=lambda i: i["_ts"], reverse=True)
    for i in items:
        i.pop("_ts", None)
    return items[:limit]


async def web_search(query: str, limit: int = 8, news: bool = False, tavily_key: str = "") -> list[dict]:
    if tavily_key:
        async with httpx.AsyncClient(timeout=20) as http:
            r = await http.post("https://api.tavily.com/search", json={
                "api_key": tavily_key, "query": query, "max_results": limit,
                "topic": "news" if news else "general"})
            return [{"title": x["title"], "url": x["url"], "snippet": x.get("content", "")[:400]}
                    for x in r.json().get("results", [])]

    def _ddg():
        from ddgs import DDGS
        with DDGS() as d:
            res = d.news(query, max_results=limit) if news else d.text(query, max_results=limit)
            return list(res)

    try:
        raw = await asyncio.to_thread(_ddg)
    except Exception as e:
        return [{"error": f"search failed: {e}"}]
    return [{"title": x.get("title"), "url": x.get("url") or x.get("href"),
             "snippet": (x.get("body") or "")[:400], "date": x.get("date"), "source": x.get("source")}
            for x in raw]


async def read_url(url: str, max_chars: int = 6000) -> dict:
    if not re.match(r"^https?://", url):
        return {"error": "only http(s) URLs are allowed"}
    async with httpx.AsyncClient(timeout=15, headers=UA, follow_redirects=True) as http:
        r = await http.get(url)
    soup = BeautifulSoup(r.text, "html.parser")
    for tag in soup(["script", "style", "nav", "footer", "header", "aside", "form"]):
        tag.decompose()
    main = soup.find("article") or soup.find("main") or soup.body or soup
    text = re.sub(r"\n{3,}", "\n\n", main.get_text("\n", strip=True))
    return {"url": str(r.url), "title": soup.title.string.strip() if soup.title and soup.title.string else None,
            "content": text[:max_chars], "truncated": len(text) > max_chars}
