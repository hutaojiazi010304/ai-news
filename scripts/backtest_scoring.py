#!/usr/bin/env python3
"""Offline scoring backtest: replay archived items through two versions of
ai_relevance and report exactly what a scoring change does before it ships.

Usage:
    python scripts/backtest_scoring.py --days 14
    python scripts/backtest_scoring.py --days 21 --baseline-rev master~10
    python scripts/backtest_scoring.py --days 7 --output reports/backtest

The baseline scorer is loaded from a git revision (default: HEAD, i.e. the
last committed version); the candidate scorer is the current working tree.
Rule of thumb borrowed from the project changelog discipline: scoring changes
should ship with a >=14 day replay attached.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import subprocess
import sys
import tempfile
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from scripts.ai_relevance import score_ai_relevance as candidate_scorer  # noqa: E402

UTC = timezone.utc


def load_scorer_from_rev(rev: str):
    source = subprocess.run(
        ["git", "show", f"{rev}:scripts/ai_relevance.py"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    with tempfile.NamedTemporaryFile("w", suffix="_ai_relevance_baseline.py", delete=False) as fh:
        fh.write(source)
        path = fh.name
    spec = importlib.util.spec_from_file_location("ai_relevance_baseline", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.score_ai_relevance


def parse_when(record: dict) -> datetime | None:
    for key in ("published_at", "first_seen_at", "last_seen_at"):
        raw = record.get(key)
        if not raw:
            continue
        try:
            return datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        except ValueError:
            continue
    return None


def compare_scoring(items: list[dict], baseline_fn, candidate_fn, sample_cap: int = 30) -> dict:
    """Pure comparison core (also used by tests): score every item with both
    scorers and summarise flips, label moves, and per-site keep rates."""
    flips_to_drop: list[dict] = []
    flips_to_keep: list[dict] = []
    label_moves: Counter = Counter()
    site_keep_baseline: Counter = Counter()
    site_keep_candidate: Counter = Counter()
    site_total: Counter = Counter()
    kept_baseline = kept_candidate = 0

    for record in items:
        site = str(record.get("site_id") or "?")
        site_total[site] += 1
        base = baseline_fn(record)
        cand = candidate_fn(record)
        if base["is_ai_related"]:
            kept_baseline += 1
            site_keep_baseline[site] += 1
        if cand["is_ai_related"]:
            kept_candidate += 1
            site_keep_candidate[site] += 1
        if base["is_ai_related"] != cand["is_ai_related"]:
            entry = {
                "site_id": site,
                "title": str(record.get("title") or "")[:120],
                "url": str(record.get("url") or "")[:160],
                "baseline": {"label": base["label"], "score": base["score"]},
                "candidate": {"label": cand["label"], "score": cand["score"]},
            }
            (flips_to_drop if base["is_ai_related"] else flips_to_keep).append(entry)
        elif base["is_ai_related"] and base["label"] != cand["label"]:
            label_moves[f"{base['label']} -> {cand['label']}"] += 1

    total = len(items)
    return {
        "total_items": total,
        "kept_baseline": kept_baseline,
        "kept_candidate": kept_candidate,
        "keep_rate_baseline": round(kept_baseline / total, 4) if total else 0,
        "keep_rate_candidate": round(kept_candidate / total, 4) if total else 0,
        "flips_to_drop_count": len(flips_to_drop),
        "flips_to_keep_count": len(flips_to_keep),
        "flips_to_drop_samples": flips_to_drop[:sample_cap],
        "flips_to_keep_samples": flips_to_keep[:sample_cap],
        "label_moves": dict(label_moves.most_common(20)),
        "per_site": {
            site: {
                "total": site_total[site],
                "kept_baseline": site_keep_baseline.get(site, 0),
                "kept_candidate": site_keep_candidate.get(site, 0),
            }
            for site in sorted(site_total, key=lambda s: -site_total[s])
        },
    }


def render_markdown(report: dict, days: int, baseline_rev: str) -> str:
    lines = [
        "# Scoring backtest report",
        "",
        f"- Window: last {days} days ({report['total_items']} archived items)",
        f"- Baseline: `{baseline_rev}` | Candidate: working tree",
        f"- Kept (baseline -> candidate): {report['kept_baseline']} -> {report['kept_candidate']}"
        f" ({report['keep_rate_baseline']:.1%} -> {report['keep_rate_candidate']:.1%})",
        f"- Flips AI->not_ai: **{report['flips_to_drop_count']}** | not_ai->AI: **{report['flips_to_keep_count']}**",
        "",
        "## Flip samples (AI -> not_ai)",
        "",
    ]
    for entry in report["flips_to_drop_samples"]:
        lines.append(
            f"- [{entry['site_id']}] {entry['title']} "
            f"({entry['baseline']['label']} {entry['baseline']['score']} -> {entry['candidate']['label']})"
        )
    lines += ["", "## Flip samples (not_ai -> AI)", ""]
    for entry in report["flips_to_keep_samples"]:
        lines.append(
            f"- [{entry['site_id']}] {entry['title']} "
            f"(-> {entry['candidate']['label']} {entry['candidate']['score']})"
        )
    lines += ["", "## Label moves (kept items)", ""]
    for move, count in report["label_moves"].items():
        lines.append(f"- {move}: {count}")
    lines += ["", "## Per-site keep counts (baseline -> candidate)", ""]
    for site, row in report["per_site"].items():
        delta = row["kept_candidate"] - row["kept_baseline"]
        flag = "" if delta == 0 else f"  **({delta:+d})**"
        lines.append(f"- {site}: {row['kept_baseline']} -> {row['kept_candidate']} / {row['total']}{flag}")
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--archive", default="data/archive.json", help="Path to archive.json")
    parser.add_argument("--days", type=int, default=14, help="Replay window in days")
    parser.add_argument("--baseline-rev", default="HEAD", help="Git revision for the baseline scorer")
    parser.add_argument("--output", default="reports/backtest", help="Output directory")
    parser.add_argument("--sample-cap", type=int, default=30, help="Max flip samples per direction")
    args = parser.parse_args()

    archive_path = REPO_ROOT / args.archive if not Path(args.archive).is_absolute() else Path(args.archive)
    payload = json.loads(archive_path.read_text())
    records = payload.get("items") if isinstance(payload, dict) else payload
    if isinstance(records, dict):
        records = list(records.values())

    cutoff = datetime.now(UTC) - timedelta(days=args.days)
    window = [r for r in records if (parse_when(r) or datetime.min.replace(tzinfo=UTC)) >= cutoff]
    print(f"Replaying {len(window)} of {len(records)} archived items (last {args.days} days)")

    baseline_fn = load_scorer_from_rev(args.baseline_rev)
    report = compare_scoring(window, baseline_fn, candidate_scorer, sample_cap=args.sample_cap)

    out_dir = REPO_ROOT / args.output if not Path(args.output).is_absolute() else Path(args.output)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M")
    json_path = out_dir / f"scoring-backtest-{stamp}.json"
    md_path = out_dir / f"scoring-backtest-{stamp}.md"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=1))
    md_path.write_text(render_markdown(report, args.days, args.baseline_rev))

    print(f"Kept: {report['kept_baseline']} -> {report['kept_candidate']}")
    print(f"Flips AI->not_ai: {report['flips_to_drop_count']} | not_ai->AI: {report['flips_to_keep_count']}")
    print(f"Report: {md_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
