#!/usr/bin/env python3
"""Generate a lightweight audit report for AI relevance scoring output."""

from __future__ import annotations

import argparse
import collections
import json
from pathlib import Path
from typing import Any


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def item_time(item: dict[str, Any]) -> str:
    return str(item.get("published_at") or item.get("first_seen_at") or "")[:19]


def short(text: str, max_chars: int = 96) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= max_chars else text[: max_chars - 1].rstrip() + "…"


def table_rows(items: list[dict[str, Any]], limit: int) -> list[str]:
    rows = []
    for item in items[:limit]:
        rows.append(
            "| {score:.2f} | {label} | {site} | {source} | {title} | {reason} |".format(
                score=float(item.get("ai_score") or 0),
                label=str(item.get("ai_label") or ""),
                site=str(item.get("site_name") or item.get("site_id") or ""),
                source=short(str(item.get("source") or ""), 32),
                title=short(str(item.get("title_bilingual") or item.get("title") or ""), 72),
                reason=str(item.get("ai_relevance_reason") or ""),
            )
        )
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit AI relevance scoring output")
    parser.add_argument("--data-dir", default="data", help="Directory containing latest-24h*.json")
    parser.add_argument("--output", required=True, help="Markdown report path")
    parser.add_argument("--sample-size", type=int, default=30)
    args = parser.parse_args()

    data_dir = Path(args.data_dir)
    latest = load_json(data_dir / "latest-24h.json")
    latest_all = load_json(data_dir / "latest-24h-all.json")
    kept_dedup = list(latest.get("items_ai") or latest.get("items") or [])
    raw = list(latest_all.get("items_all_raw") or [])
    kept_raw = [item for item in raw if item.get("ai_is_related")]
    dropped = [item for item in raw if not item.get("ai_is_related")]

    label_counts = collections.Counter(str(item.get("ai_label") or "unknown") for item in kept_raw)
    site_kept = collections.Counter(str(item.get("site_id") or "unknown") for item in kept_raw)
    site_raw = collections.Counter(str(item.get("site_id") or "unknown") for item in raw)
    site_rows = []
    for site_id, raw_count in site_raw.most_common():
        kept_count = site_kept.get(site_id, 0)
        site_rows.append(f"| {site_id} | {kept_count} | {raw_count} | {kept_count / raw_count:.1%} |")

    review_band = [
        item
        for item in raw
        if 0.45 <= float(item.get("ai_score") or 0) < float(latest.get("ai_relevance_threshold") or 0.65)
    ]
    high_drops = sorted(dropped, key=lambda item: float(item.get("ai_score") or 0), reverse=True)
    top_kept = sorted(kept_raw, key=lambda item: float(item.get("ai_score") or 0), reverse=True)

    lines = [
        "# AI Relevance Audit — v0.4.0",
        "",
        "## Summary",
        "",
        f"- generated_at: `{latest.get('generated_at')}`",
        f"- topic_filter: `{latest.get('topic_filter')}`",
        f"- threshold: `{latest.get('ai_relevance_threshold')}`",
        f"- raw 24h items: `{len(raw)}`",
        f"- AI kept raw items: `{len(kept_raw)}`",
        f"- AI kept dedup items: `{len(kept_dedup)}`",
        f"- dropped raw items: `{len(dropped)}`",
        f"- raw keep rate: `{(len(kept_raw) / len(raw)) if raw else 0:.1%}`",
        f"- review-band items (0.45 <= score < threshold): `{len(review_band)}`",
        "",
        "## Label distribution",
        "",
        "| label | count |",
        "| --- | ---: |",
    ]
    lines.extend(f"| {label} | {count} |" for label, count in label_counts.most_common())
    lines.extend([
        "",
        "## Source keep rate",
        "",
        "| site_id | kept | raw | keep_rate |",
        "| --- | ---: | ---: | ---: |",
    ])
    lines.extend(site_rows)
    lines.extend([
        "",
        "## High-score kept samples",
        "",
        "| score | label | site | source | title | reason |",
        "| ---: | --- | --- | --- | --- | --- |",
    ])
    lines.extend(table_rows(top_kept, args.sample_size))
    lines.extend([
        "",
        "## Highest-score dropped samples",
        "",
        "Use this section to catch false negatives before changing thresholds.",
        "",
        "| score | label | site | source | title | reason |",
        "| ---: | --- | --- | --- | --- | --- |",
    ])
    lines.extend(table_rows(high_drops, args.sample_size))
    lines.extend([
        "",
        "## Review-band samples",
        "",
        "These are the best candidates for future LLM or manual second-pass review.",
        "",
        "| score | label | site | source | title | reason |",
        "| ---: | --- | --- | --- | --- | --- |",
    ])
    lines.extend(table_rows(sorted(review_band, key=lambda item: float(item.get("ai_score") or 0), reverse=True), args.sample_size))
    lines.append("")

    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines), encoding="utf-8")
    print(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
