#!/usr/bin/env python3
"""One-off connectivity probe: which pipeline sources are reachable and
return items under the current network, direct vs local proxy.

Reuses the pipeline's own fetch/parse functions so the result reflects
what update_news.py would actually get. Read-only: never writes data/.
"""

from __future__ import annotations

import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import requests  # noqa: E402

import scripts.update_news as un  # noqa: E402

PROXY = "http://127.0.0.1:7897"
FEED_HEADERS = {
    "User-Agent": un.BROWSER_UA,
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml, */*",
}


def make_session(proxy: str | None) -> requests.Session:
    s = requests.Session()
    s.trust_env = False  # ignore env vars / Windows registry proxy
    if proxy:
        s.proxies = {"http": proxy, "https": proxy}
    return s


def err_str(exc: Exception) -> str:
    msg = str(exc).splitlines()[0][:140] if str(exc) else ""
    name = type(exc).__name__
    return f"{name}: {msg}" if msg else name


def probe_endpoint(sess, fn) -> tuple[bool, int, int, str]:
    t0 = time.perf_counter()
    try:
        items = fn()
        return True, len(items), int((time.perf_counter() - t0) * 1000), ""
    except Exception as exc:
        return False, 0, int((time.perf_counter() - t0) * 1000), err_str(exc)


def probe_round(mode: str, proxy: str | None) -> list[dict]:
    sess = make_session(proxy)
    now = datetime.now(timezone.utc)
    rows: list[dict] = []

    def record(group: str, name: str, ok: bool, count: int, ms: int, err: str):
        rows.append(
            {
                "mode": mode,
                "group": group,
                "name": name,
                "ok": ok,
                "items": count,
                "ms": ms,
                "error": err,
            }
        )
        flag = "OK  " if ok else "FAIL"
        detail = f"{count} items" if ok else err
        print(f"[{mode}] {flag} {name:<34} {ms:>6}ms  {detail}", flush=True)

    # --- single-endpoint adapters (same functions as collect_all) ---
    adapters = [
        ("AI Breakfast", un.fetch_ai_breakfast),
        ("Follow Builders", un.fetch_follow_builders),
        ("TechURLs", un.fetch_techurls),
        ("Buzzing", un.fetch_buzzing),
        ("Info Flow (iris)", un.fetch_iris),
        ("BestBlogs", un.fetch_bestblogs),
        ("Zeli", un.fetch_zeli),
        ("Hacker News (Algolia)", un.fetch_hacker_news_algolia),
        ("AI HubToday", un.fetch_ai_hubtoday),
        ("AIbase", un.fetch_aibase),
        ("AI HOT", un.fetch_aihot),
        ("NewsNow", un.fetch_newsnow),
    ]
    for name, fn in adapters:
        ok, count, ms, err = probe_endpoint(sess, lambda fn=fn: fn(sess, now))
        record("adapter", name, ok, count, ms, err)

    # --- official_ai: per-feed breakdown ---
    for feed in un.OFFICIAL_AI_FEEDS:
        ok, count, ms, err = probe_endpoint(
            sess, lambda feed=feed: un.fetch_feed_as_official_items(sess, feed, now)
        )
        record("official_feed", str(feed.get("title")), ok, count, ms, err)

    ok, count, ms, err = probe_endpoint(
        sess,
        lambda: un.parse_anthropic_news_items(
            sess.get(
                "https://www.anthropic.com/news",
                timeout=20,
                headers={"User-Agent": un.BROWSER_UA},
            ).text,
            now,
        ),
    )
    record("official_page", "Anthropic News (page)", ok, count, ms, err)

    ok, count, ms, err = probe_endpoint(
        sess,
        lambda: un.parse_openai_codex_changelog_items(
            sess.get(
                "https://developers.openai.com/codex/changelog",
                timeout=20,
                headers={"User-Agent": un.BROWSER_UA},
            ).text,
            now,
        ),
    )
    record("official_page", "OpenAI Codex Changelog (page)", ok, count, ms, err)

    # --- curated_media: per-feed breakdown (same GET as the adapter) ---
    for feed in un.CURATED_AI_MEDIA_FEEDS:
        def fetch_curated(feed=feed):
            resp = sess.get(str(feed["xml_url"]), timeout=20, headers=FEED_HEADERS)
            resp.raise_for_status()
            return un.parse_curated_ai_media_feed_items(resp.content, feed, now)

        ok, count, ms, err = probe_endpoint(sess, fetch_curated)
        record("curated_feed", str(feed.get("title")), ok, count, ms, err)

    # --- waytoagi (separate flow in main) ---
    def fetch_waytoagi():
        payload = un.fetch_waytoagi_recent_7d(sess, now, un.WAYTOAGI_DEFAULT)
        items = payload.get("items") if isinstance(payload, dict) else None
        return items if isinstance(items, list) else []

    ok, count, ms, err = probe_endpoint(sess, fetch_waytoagi)
    record("adapter", "WayToAGI (feishu)", ok, count, ms, err)

    return rows


def main() -> int:
    all_rows: list[dict] = []

    proxy_alive = False
    try:
        r = make_session(PROXY).get("https://www.bing.com/", timeout=8)
        proxy_alive = True
        print(f"proxy {PROXY}: alive (HTTP {r.status_code})")
    except Exception as exc:
        print(f"proxy {PROXY}: NOT reachable ({err_str(exc)}) -> proxy round skipped")

    print("\n=== direct (no proxy) ===")
    all_rows.extend(probe_round("direct", None))

    if proxy_alive:
        print("\n=== via proxy ===")
        all_rows.extend(probe_round("proxy", PROXY))

    # ---- summary matrix ----
    names: list[tuple[str, str]] = []
    for row in all_rows:
        key = (row["group"], row["name"])
        if key not in names:
            names.append(key)

    by_key = {}
    for row in all_rows:
        by_key[(row["mode"], row["group"], row["name"])] = row

    print("\n=== SUMMARY (ok/items) ===")
    header = f"{'source':<42} {'direct':>14} {'proxy':>14}"
    print(header)
    for group, name in names:
        cells = []
        for mode in ("direct", "proxy"):
            row = by_key.get((mode, group, name))
            if row is None:
                cells.append("-")
            elif row["ok"]:
                cells.append(f"OK ({row['items']} items)")
            else:
                cells.append("FAIL")
        print(f"{name:<42} {cells[0]:>14} {cells[1]:>14}")

    out = ROOT / "probe-sources-report.json"
    out.write_text(json.dumps(all_rows, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\nfull report: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
