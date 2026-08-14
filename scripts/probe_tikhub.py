#!/usr/bin/env python3
"""Probe TikHub Douyin/Xiaohongshu search without running the full pipeline."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os

import requests

try:
    from scripts.update_news import fetch_tikhub_search, iso
except ModuleNotFoundError:  # pragma: no cover - direct `python scripts/probe_tikhub.py`
    from update_news import fetch_tikhub_search, iso


def main() -> int:
    parser = argparse.ArgumentParser(description="Probe TikHub search and print sanitized mapped items")
    parser.add_argument("--query", default=os.environ.get("TIKHUB_QUERY") or "AI,大模型,Agent")
    parser.add_argument("--platforms", default=os.environ.get("TIKHUB_PLATFORMS") or "douyin,xiaohongshu")
    parser.add_argument("--max-results", type=int, default=int(os.environ.get("TIKHUB_MAX_RESULTS") or "10"))
    parser.add_argument("--base-url", default=os.environ.get("TIKHUB_API_BASE_URL") or "https://api.tikhub.io")
    args = parser.parse_args()

    api_key = (os.environ.get("TIKHUB_API_KEY") or "").strip()
    if not api_key:
        print(json.dumps({"ok": False, "error": "missing_tikhub_api_key"}, ensure_ascii=False, indent=2))
        return 2

    now = datetime.now(timezone.utc)
    platforms = [part.strip().lower() for part in args.platforms.split(",") if part.strip()]
    items, diagnostics = fetch_tikhub_search(
        requests.Session(),
        api_key=api_key,
        query=args.query,
        now=now,
        max_results=max(1, args.max_results),
        platforms=platforms,
        base_url=args.base_url,
    )
    payload = {
        "ok": True,
        "generated_at": iso(now),
        "item_count": len(items),
        "diagnostics": diagnostics,
        "items": [
            {
                "site_id": item.site_id,
                "site_name": item.site_name,
                "source": item.source,
                "title": item.title,
                "url": item.url,
                "published_at": iso(item.published_at),
                "meta": item.meta,
            }
            for item in items[: min(len(items), 10)]
        ],
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if items else 1


if __name__ == "__main__":
    raise SystemExit(main())
