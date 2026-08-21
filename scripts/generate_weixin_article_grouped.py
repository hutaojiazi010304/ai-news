"""Grouped-layout variant of the WeChat daily article generator.

This variant consumes the exact same ``data/daily-brief.json`` through the
exact same selection, guide-writing and cover logic as
``generate_weixin_article.py`` (imported, not copied — so the 20 picked
items and their texts are guaranteed identical). The only difference is the
body layout: items are rendered grouped by story category
(官方更新 → 行业动态 → 多源热议 → 值得关注), each group under a centered,
color-coded title and inside its own large box, with per-section numbering —
instead of one flat ranked list.

Output goes to ``weixin-grouped/`` by default so both variants coexist for
side-by-side comparison:

    python scripts/generate_weixin_article.py   --data-dir data --output-dir weixin
    python scripts/generate_weixin_article_grouped.py --data-dir data --output-dir weixin-grouped

Shared assets (by design, confirmed as product decisions):

- Reason cache: read/written from the main variant's output dir
  (``weixin/reason-cache.json``). Guides are per-item and layout-agnostic,
  so whichever variant runs first warms the cache for the other — no
  duplicate Qwen text calls.
- Cover: when the main variant already produced a cover for the same issue
  date (checked via ``weixin/meta.json``), it is copied verbatim; only
  otherwise does this script fall back to the identical ``resolve_cover``
  pipeline. Saves one qwen-image call and keeps both covers identical.

Title and digest are byte-identical to the main variant on purpose: the two
versions must differ in layout only.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

try:  # imported as part of the repo package (tests)
    from scripts.generate_weixin_article import (
        CATEGORY_LABEL_ZH,
        TZ_CN,
        WEEKDAY_CN,
        build_config,
        build_meta,
        create_session,
        esc,
        fallback_title,
        fill_reasons,
        load_brief,
        load_cache,
        make_digest,
        render_item_html,
        resolve_cover,
        save_cache,
        select_items,
        strip_english_tail,
    )
except ImportError:  # run directly as a script
    from generate_weixin_article import (
        CATEGORY_LABEL_ZH,
        TZ_CN,
        WEEKDAY_CN,
        build_config,
        build_meta,
        create_session,
        esc,
        fallback_title,
        fill_reasons,
        load_brief,
        load_cache,
        make_digest,
        render_item_html,
        resolve_cover,
        save_cache,
        select_items,
        strip_english_tail,
    )

# Display order for the sections: official first, then high-score industry
# news, multi-source discussions and the watchlist. Empty sections are
# skipped entirely; unknown categories (should not happen) go last.
CATEGORY_ORDER = ("official", "industry", "multi_source", "watch")

# Per-section title colors (deep, brand-like tones). The large enclosing box
# is drawn in the SAME hue at higher transparency (rgba) so each section reads
# as one color family. Inline styles only — the WeChat editor strips classes
# and <style> blocks.
CATEGORY_COLORS = {
    "official": "#13501B",
    "industry": "#215F9A",
    "multi_source": "#C04F15",
    "watch": "#595959",
}

# Box transparency: border is the hue at moderate alpha, background a faint
# wash of the same hue.
BOX_BORDER_ALPHA = 0.45
BOX_BACKGROUND_ALPHA = 0.08


def with_alpha(hex_color: str, alpha: float) -> str:
    """Return ``rgba(r, g, b, alpha)`` for a ``#rrggbb`` color."""
    h = hex_color.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return f"rgba({r}, {g}, {b}, {alpha})"


def _build_styles() -> dict[str, dict[str, str]]:
    styles = {}
    for category, color in CATEGORY_COLORS.items():
        styles[category] = {
            "color": color,
            "border": with_alpha(color, BOX_BORDER_ALPHA),
            "background": with_alpha(color, BOX_BACKGROUND_ALPHA),
        }
    return styles


CATEGORY_STYLES = _build_styles()
DEFAULT_STYLE = CATEGORY_STYLES["watch"]

DEFAULT_OUTPUT_DIR = "weixin-grouped"
DEFAULT_MAIN_OUTPUT_DIR = "weixin"


def group_items(items: list[dict]) -> list[tuple[str, list[dict]]]:
    """Group the picked items by story category, preserving in-group order.

    ``select_items`` already ranks by peak_score DESC; grouping keeps that
    order inside each section so nothing about the selection changes.
    """
    grouped: dict[str, list[dict]] = {}
    for item in items:
        category = str(item.get("category") or "watch").strip() or "watch"
        grouped.setdefault(category, []).append(item)
    ordered = [(cat, grouped[cat]) for cat in CATEGORY_ORDER if cat in grouped]
    ordered.extend(
        (cat, grouped[cat]) for cat in grouped if cat not in CATEGORY_ORDER
    )
    return ordered


def render_group_section(category: str, items: list[dict]) -> str:
    label = CATEGORY_LABEL_ZH.get(category, category)
    style = CATEGORY_STYLES.get(category, DEFAULT_STYLE)
    parts = [
        '<section style="margin:34px 0 0;">',
        # Centered, color-coded section title (no item count by design).
        (
            '<p style="margin:0 0 14px;text-align:center;font-size:17px;'
            f'font-weight:bold;letter-spacing:2px;color:{style["color"]};">'
            f'{esc(label)}</p>'
        ),
        # One large box enclosing every item of this section.
        (
            f'<section style="border:1px solid {style["border"]};'
            f'border-radius:10px;background-color:{style["background"]};'
            'padding:2px 14px 14px;">'
        ),
    ]
    # Per-section numbering restarts at ①.
    for idx, item in enumerate(items):
        parts.append(render_item_html(item, idx))
    parts.append("</section>")
    parts.append("</section>")
    return "\n".join(parts)


def render_grouped_article_html(
    items: list[dict],
    *,
    title: str,
    digest: str,
    brand: str,
    issue_label: str,
    radar_url: str,
) -> str:
    sections_html = "\n".join(
        render_group_section(category, group) for category, group in group_items(items)
    )
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(title)}</title>
</head>
<body style="margin:0;padding:0;background-color:#f5f6f7;">
<section style="max-width:677px;margin:0 auto;background-color:#ffffff;padding:28px 20px 36px;">

<section style="border-left:4px solid #07c160;padding-left:12px;margin:0 0 20px;">
<p style="margin:0;font-size:19px;font-weight:bold;color:#1f1f1f;letter-spacing:1px;">{esc(brand)}</p>
<p style="margin:3px 0 0;font-size:13px;color:#999999;">{esc(issue_label)} · 每日 AI 精选</p>
</section>

<p style="margin:0 0 4px;font-size:18px;font-weight:bold;line-height:1.5;color:#111111;">{esc(title)}</p>

{sections_html}

<section style="margin-top:30px;border-top:1px dashed #d9d9d9;padding-top:16px;">
<p style="margin:0;font-size:13px;line-height:1.7;color:#999999;">以上内容由 {esc(brand)} 自动整理自过去 24 小时的公开信源，原文链接见每条信息下方。</p>
</section>

<section style="margin-top:22px;background-color:#f5f6f7;padding:14px 16px;">
<p style="margin:0;font-size:12px;font-weight:bold;color:#666666;">以下为发布辅助信息（复制用，粘贴时请勿包含本区块）</p>
<p style="margin:10px 0 0;font-size:13px;line-height:1.7;color:#666666;">标题：{esc(title)}</p>
<p style="margin:6px 0 0;font-size:13px;line-height:1.7;color:#666666;">摘要：{esc(digest)}</p>
<p style="margin:6px 0 0;font-size:13px;line-height:1.7;color:#666666;">阅读原文：{esc(radar_url)}</p>
</section>

</section>
</body>
</html>
"""


def reuse_cover(main_output_dir: Path, issue_date: str) -> tuple[bytes | None, str | None]:
    """Copy the main variant's cover when it belongs to the same issue date.

    Guarded by the main variant's ``meta.json`` so a stale cover from a
    previous day is never reused before the main variant has run today.
    """
    try:
        meta = json.loads((main_output_dir / "meta.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None, None
    if not isinstance(meta, dict) or str(meta.get("issue_date") or "") != issue_date:
        return None, None
    name = str(meta.get("cover") or "").strip()
    if not name:
        return None, None
    try:
        data = (main_output_dir / name).read_bytes()
    except OSError:
        return None, None
    return (data, name) if data else (None, None)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="WeChat daily article generator (grouped-by-category layout)"
    )
    parser.add_argument("--data-dir", default="data", help="data directory")
    parser.add_argument(
        "--output-dir",
        default=DEFAULT_OUTPUT_DIR,
        help=f"output directory (default {DEFAULT_OUTPUT_DIR})",
    )
    parser.add_argument(
        "--main-output-dir",
        default=DEFAULT_MAIN_OUTPUT_DIR,
        help=(
            "main variant output dir used for the shared reason cache and "
            f"cover reuse (default {DEFAULT_MAIN_OUTPUT_DIR})"
        ),
    )
    parser.add_argument("--assets-dir", default="assets", help="assets directory")
    parser.add_argument(
        "--max-items",
        type=int,
        default=None,
        help="max items (defaults to WEIXIN_MAX_ITEMS env or 20)",
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="run without writing any files"
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    if os.environ.get("WEIXIN_ENABLED", "").strip() == "0":
        print("weixin-grouped: disabled via WEIXIN_ENABLED=0, nothing to do")
        return 0

    cfg = build_config(args)
    data_dir = Path(args.data_dir)
    output_dir = Path(args.output_dir)
    main_output_dir = Path(args.main_output_dir)
    assets_dir = Path(args.assets_dir)

    brief = load_brief(data_dir / "daily-brief.json")
    if brief is None:
        print(
            f"weixin-grouped: {data_dir / 'daily-brief.json'} not found or invalid, "
            "nothing to do"
        )
        return 0

    items = select_items(brief, cfg["max_items"])
    if not items:
        print("weixin-grouped: brief has no items, nothing to do")
        return 0

    now_cn = datetime.now(TZ_CN)
    issue_date = now_cn.strftime("%Y-%m-%d")
    issue_label = f"{now_cn.month}月{now_cn.day}日 {WEEKDAY_CN[now_cn.weekday()]}"

    session = create_session() if cfg["api_key"] else None
    # Shared with the main variant: guides are per-item and layout-agnostic.
    cache_path = main_output_dir / "reason-cache.json"
    cache = load_cache(cache_path)
    stats = {"reused": 0, "cached": 0, "generated": 0, "skipped": 0}

    fill_reasons(items, cache, cfg, session, stats)

    headline = strip_english_tail(str(items[0].get("title") or "").strip())
    # Identical to the main variant by design — the two versions must differ
    # in layout only.
    title = fallback_title(cfg["brand"], now_cn, len(items))
    digest = make_digest(cfg["brand"], len(items), headline, issue_label)

    cover_bytes, cover_filename = reuse_cover(main_output_dir, issue_date)
    if cover_bytes is not None:
        cover_mode, cover_scene = "reused", False
    else:
        cover_bytes, cover_filename, cover_mode, cover_scene = resolve_cover(
            headline, cfg, session, assets_dir
        )

    groups = group_items(items)
    html_text = render_grouped_article_html(
        items,
        title=title,
        digest=digest,
        brand=cfg["brand"],
        issue_label=issue_label,
        radar_url=cfg["radar_url"],
    )
    meta = build_meta(
        issue_date=issue_date,
        brand=cfg["brand"],
        title=title,
        digest=digest,
        cover_filename=cover_filename if cover_bytes else None,
        radar_url=cfg["radar_url"],
        item_count=len(items),
        cfg=cfg,
    )
    meta["layout"] = "grouped"
    meta["sections"] = [
        {"category": category, "label": CATEGORY_LABEL_ZH.get(category, category), "count": len(group)}
        for category, group in groups
    ]

    if not args.dry_run:
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / "index.html").write_text(html_text, encoding="utf-8")
        (output_dir / "meta.json").write_text(
            json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        if cover_bytes:
            # Remove the other cover variant so no stale file is served.
            for name in ("cover.jpg", "cover.png"):
                if name != cover_filename:
                    try:
                        (output_dir / name).unlink()
                    except OSError:
                        pass
            (output_dir / cover_filename).write_bytes(cover_bytes)
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        save_cache(cache_path, cache, datetime.now(timezone.utc))

    print(
        "weixin-grouped: items={items} sections={sections} "
        "reasons reused={reused} cached={cached} generated={generated} "
        "skipped={skipped} cover_mode={cover_mode} cover_scene={cover_scene} "
        "dry_run={dry_run}".format(
            items=len(items),
            sections=",".join(
                f"{CATEGORY_LABEL_ZH.get(cat, cat)}×{len(group)}"
                for cat, group in groups
            ),
            reused=stats["reused"],
            cached=stats["cached"],
            generated=stats["generated"],
            skipped=stats["skipped"],
            cover_mode=cover_mode,
            cover_scene=1 if cover_scene else 0,
            dry_run=1 if args.dry_run else 0,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
