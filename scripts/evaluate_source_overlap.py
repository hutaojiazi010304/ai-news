#!/usr/bin/env python3
"""Evaluate whether a candidate source overlaps too much with existing sources.

This script is intentionally maintainer-facing. It does not change the daily
news pipeline; it produces an intake report that helps decide whether a new RSS
source should become a public default, remain an advanced OPML source, or be
skipped as too duplicative.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from datetime import UTC, datetime, timedelta
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

import requests

# Allow `python scripts/evaluate_source_overlap.py ...` from the repo root while
# keeping package imports working under pytest.
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.update_news import (  # noqa: E402
    event_time,
    iso,
    normalize_url,
    parse_date_any,
    parse_feed_entries_via_xml,
    utc_now,
)

TITLE_SOURCE_SUFFIXES = {
    "ai hot",
    "aihot",
    "hacker news",
    "product hunt",
    "techcrunch",
    "the verge",
    "venturebeat",
}

TITLE_DROP_WORDS = {
    "exclusive",
    "breaking",
    "news",
    "the",
    "a",
    "an",
    "from",
    "by",
    "via",
}

ENTITY_TERMS = {
    "openai",
    "anthropic",
    "claude",
    "google",
    "gemini",
    "deepseek",
    "qwen",
    "llama",
    "meta",
    "mistral",
    "perplexity",
    "cursor",
    "codex",
    "copilot",
    "github",
    "runway",
    "kling",
}


def normalize_title_for_overlap(title: str) -> str:
    """Normalize a title for overlap comparison, not for display."""
    text = (title or "").strip().lower()
    if not text:
        return ""

    # Drop common source suffixes like "— AI HOT" when they are clearly not part
    # of the story title.
    parts = re.split(r"\s+[—–-]\s+", text)
    if len(parts) > 1 and parts[-1].strip(" .") in TITLE_SOURCE_SUFFIXES:
        text = " — ".join(parts[:-1])

    # Remove bracketed labels such as [Exclusive], not bracketed product names.
    text = re.sub(r"\[(exclusive|breaking|update|news)\]", " ", text, flags=re.I)
    text = re.sub(r"\b20\d{2}\b", " ", text)
    text = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", " ", text)
    tokens = [token for token in text.split() if token not in TITLE_DROP_WORDS]
    return " ".join(tokens)


def title_similarity(a: str, b: str) -> float:
    """Return a stable 0-1 title similarity score with light token reordering."""
    na = normalize_title_for_overlap(a)
    nb = normalize_title_for_overlap(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0

    seq_score = SequenceMatcher(None, na, nb).ratio()
    ta = set(na.split())
    tb = set(nb.split())
    if not ta or not tb:
        return round(seq_score, 3)
    token_score = len(ta & tb) / len(ta | tb)
    sorted_score = SequenceMatcher(None, " ".join(sorted(ta)), " ".join(sorted(tb))).ratio()
    return round(max(seq_score, token_score, sorted_score), 3)


def extract_entities(title: str) -> set[str]:
    normalized = normalize_title_for_overlap(title)
    tokens = set(normalized.split())
    return {term for term in ENTITY_TERMS if term in tokens}


def tokens_for_overlap(title: str) -> set[str]:
    return {token for token in normalize_title_for_overlap(title).split() if len(token) > 1}


def _days_apart(a: dict[str, Any], b: dict[str, Any]) -> float | None:
    ta = event_time(a)
    tb = event_time(b)
    if not ta or not tb:
        return None
    return abs((ta - tb).total_seconds()) / 86400


def classify_overlap(
    candidate: dict[str, Any],
    existing: dict[str, Any],
    *,
    title_threshold: float = 0.88,
    possible_title_threshold: float = 0.62,
) -> dict[str, Any] | None:
    """Classify overlap between one candidate item and one existing record."""
    candidate_url = normalize_url(str(candidate.get("url") or ""))
    existing_url = normalize_url(str(existing.get("url") or ""))
    score = 0.0
    match_type = ""
    hard_duplicate = False

    if candidate_url and existing_url and candidate_url == existing_url:
        score = 1.0
        match_type = "url_exact"
        hard_duplicate = True
    else:
        candidate_title = str(candidate.get("title") or "")
        existing_title = str(existing.get("title") or "")
        score = title_similarity(candidate_title, existing_title)
        if normalize_title_for_overlap(candidate_title) == normalize_title_for_overlap(existing_title) and candidate_title.strip().lower() == existing_title.strip().lower():
            match_type = "title_exact"
            hard_duplicate = True
        elif score >= title_threshold:
            match_type = "title_similarity"
            hard_duplicate = True
        else:
            candidate_entities = extract_entities(str(candidate.get("title") or ""))
            existing_entities = extract_entities(str(existing.get("title") or ""))
            days = _days_apart(candidate, existing)
            if (
                score >= possible_title_threshold
                and candidate_entities
                and candidate_entities & existing_entities
                and (days is None or days <= 2)
            ):
                match_type = "entity_time_possible"
                hard_duplicate = False
            else:
                return None

    return {
        "candidate_title": candidate.get("title"),
        "candidate_url": candidate_url,
        "matched_title": existing.get("title"),
        "matched_url": existing_url,
        "matched_site_id": existing.get("site_id"),
        "matched_site_name": existing.get("site_name"),
        "match_type": match_type,
        "score": round(score, 3),
        "hard_duplicate": hard_duplicate,
    }


def make_recommendation(item_count: int, duplicate_rate: float) -> dict[str, Any]:
    sample_too_small = item_count < 5
    if sample_too_small:
        decision = "watchlist"
        reason = "样本少于5条，不建议仅凭重复率直接拒绝；先进入观察列表。"
    elif duplicate_rate >= 0.65:
        decision = "skip_duplicate"
        reason = "最近内容和已有信源高度重复，默认不录入公共源。"
    elif duplicate_rate >= 0.35:
        decision = "watchlist"
        reason = "重复率中等，建议先作为OPML高级源或观察源，而不是直接进入默认源。"
    else:
        decision = "accept_default"
        reason = "重复率较低，能为默认源补充明显新增信息。"
    return {
        "decision": decision,
        "reason": reason,
        "sample_too_small": sample_too_small,
    }


def filter_recent_records(records: list[dict[str, Any]], now: datetime, lookback_days: int) -> list[dict[str, Any]]:
    cutoff = now - timedelta(days=lookback_days)
    out = []
    for record in records:
        ts = event_time(record)
        if ts and ts >= cutoff:
            out.append(record)
    return out


def evaluate_source_overlap(
    candidate_items: list[dict[str, Any]],
    baseline_items: list[dict[str, Any]],
    *,
    candidate: dict[str, Any],
    generated_at: datetime,
    lookback_days: int,
) -> dict[str, Any]:
    matches: list[dict[str, Any]] = []
    possible_matches: list[dict[str, Any]] = []
    duplicate_candidate_indexes: set[int] = set()

    candidate_site_id = str(candidate.get("site_id") or "")
    baseline = [item for item in baseline_items if str(item.get("site_id") or "") != candidate_site_id]

    url_index: dict[str, list[dict[str, Any]]] = {}
    token_index: dict[str, set[int]] = {}
    for baseline_idx, existing in enumerate(baseline):
        url = normalize_url(str(existing.get("url") or ""))
        if url:
            url_index.setdefault(url, []).append(existing)
        for token in tokens_for_overlap(str(existing.get("title") or "")):
            token_index.setdefault(token, set()).add(baseline_idx)

    for idx, item in enumerate(candidate_items):
        best_hard: dict[str, Any] | None = None
        best_possible: dict[str, Any] | None = None

        candidate_url = normalize_url(str(item.get("url") or ""))
        candidates_to_compare: list[dict[str, Any]] = []
        if candidate_url in url_index:
            candidates_to_compare.extend(url_index[candidate_url])
        else:
            candidate_indexes: set[int] = set()
            for token in tokens_for_overlap(str(item.get("title") or "")):
                candidate_indexes.update(token_index.get(token, set()))
            candidates_to_compare.extend(baseline[i] for i in candidate_indexes)

        for existing in candidates_to_compare:
            match = classify_overlap(item, existing)
            if not match:
                continue
            if match["hard_duplicate"]:
                if best_hard is None or match["score"] > best_hard["score"]:
                    best_hard = match
            elif best_possible is None or match["score"] > best_possible["score"]:
                best_possible = match
        if best_hard:
            duplicate_candidate_indexes.add(idx)
            matches.append(best_hard)
        elif best_possible:
            possible_matches.append(best_possible)

    item_count = len(candidate_items)
    duplicate_count = len(duplicate_candidate_indexes)
    possible_duplicate_count = len(possible_matches)
    unique_count = max(0, item_count - duplicate_count - possible_duplicate_count)
    duplicate_rate = round(duplicate_count / item_count, 3) if item_count else 0.0
    unique_rate = round(unique_count / item_count, 3) if item_count else 0.0

    source_counter = Counter(str(match.get("matched_site_id") or "") for match in matches if match.get("matched_site_id"))
    name_by_id = {
        str(match.get("matched_site_id") or ""): str(match.get("matched_site_name") or match.get("matched_site_id") or "")
        for match in matches
    }

    return {
        "generated_at": iso(generated_at),
        "candidate": {
            "site_id": candidate_site_id,
            "site_name": candidate.get("site_name") or candidate_site_id,
            "url": candidate.get("url"),
            "item_count": item_count,
            "lookback_days": lookback_days,
        },
        "baseline": {
            "source_count": len({str(item.get("site_id") or "") for item in baseline if item.get("site_id")}),
            "item_count": len(baseline),
            "window_days": lookback_days,
        },
        "overlap": {
            "duplicate_count": duplicate_count,
            "possible_duplicate_count": possible_duplicate_count,
            "unique_count": unique_count,
            "duplicate_rate": duplicate_rate,
            "unique_rate": unique_rate,
        },
        "top_overlapping_sources": [
            {
                "site_id": site_id,
                "site_name": name_by_id.get(site_id) or site_id,
                "matched_count": count,
            }
            for site_id, count in source_counter.most_common(10)
        ],
        "recommendation": make_recommendation(item_count=item_count, duplicate_rate=duplicate_rate),
        "matches": matches,
        "possible_matches": possible_matches,
    }


def load_archive_records(path: Path) -> list[dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, dict):
        if "items" in data and isinstance(data["items"], list):
            return [item for item in data["items"] if isinstance(item, dict)]
        return [item for item in data.values() if isinstance(item, dict)]
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    raise ValueError(f"Unsupported baseline JSON shape: {path}")


def fetch_rss_candidate(source_url: str, source_name: str, site_id: str, now: datetime) -> list[dict[str, Any]]:
    resp = requests.get(source_url, timeout=25, headers={"User-Agent": "AI-News-Radar/0.3 SourceOverlapCheck"})
    resp.raise_for_status()
    entries = parse_feed_entries_via_xml(resp.content)
    out: list[dict[str, Any]] = []
    for entry in entries:
        title = str(entry.get("title") or "").strip()
        url = normalize_url(str(entry.get("link") or entry.get("url") or ""))
        if not title or not url.startswith("http"):
            continue
        published = parse_date_any(entry.get("published") or entry.get("updated") or entry.get("pubDate"), now)
        out.append(
            {
                "site_id": site_id,
                "site_name": source_name,
                "source": source_name,
                "title": title,
                "url": url,
                "published_at": iso(published),
                "first_seen_at": iso(now),
            }
        )
    return out


def print_summary(report: dict[str, Any]) -> None:
    candidate = report["candidate"]
    overlap = report["overlap"]
    recommendation = report["recommendation"]
    print(f"Source: {candidate['site_name']} ({candidate['site_id']})")
    print(f"Items: {candidate['item_count']} | Baseline: {report['baseline']['item_count']}")
    print(
        "Duplicate rate: "
        f"{overlap['duplicate_rate']:.3f} "
        f"({overlap['duplicate_count']} hard, {overlap['possible_duplicate_count']} possible)"
    )
    print(f"Unique rate: {overlap['unique_rate']:.3f} ({overlap['unique_count']} unique)")
    print(f"Recommendation: {recommendation['decision']} — {recommendation['reason']}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate candidate source overlap against AI News Radar baseline")
    parser.add_argument("--source-url", required=True, help="Candidate RSS/Atom URL")
    parser.add_argument("--source-name", required=True, help="Human-readable candidate source name")
    parser.add_argument("--site-id", default="candidate", help="Candidate site_id used in the report")
    parser.add_argument("--baseline", default="data/archive.json", help="Baseline archive/latest JSON path")
    parser.add_argument("--lookback-days", type=int, default=7, help="Recent window to compare")
    parser.add_argument("--output", default="", help="Optional JSON report output path")
    args = parser.parse_args()

    now = utc_now()
    baseline_path = Path(args.baseline).expanduser()
    baseline_items = filter_recent_records(load_archive_records(baseline_path), now, max(1, args.lookback_days))
    candidate_items = filter_recent_records(
        fetch_rss_candidate(args.source_url, args.source_name, args.site_id, now),
        now,
        max(1, args.lookback_days),
    )
    report = evaluate_source_overlap(
        candidate_items,
        baseline_items,
        candidate={"site_id": args.site_id, "site_name": args.source_name, "url": args.source_url},
        generated_at=now,
        lookback_days=max(1, args.lookback_days),
    )

    if args.output:
        output_path = Path(args.output).expanduser()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"Wrote overlap report: {output_path}")
    print_summary(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
