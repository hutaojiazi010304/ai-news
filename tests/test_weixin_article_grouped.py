"""Tests for scripts/generate_weixin_article_grouped.py.

The grouped variant must not change what gets picked or written per item —
only the body layout differs from the main variant. These tests pin the
grouping order, per-section numbering, same-day cover reuse, shared reason
cache location, and the end-to-end file contract.
"""

from __future__ import annotations

import json
import re
import sys
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import generate_weixin_article as gwa
from scripts import generate_weixin_article_grouped as gwag

from test_weixin_article import make_item, make_static_asset, read_json, write_fixture


def make_categorized_item(idx: int, category: str, score: float) -> dict:
    item = make_item(idx, title=f"分类测试新闻 {idx}（{category}）", score=score)
    item["category"] = category
    return item


# ---------------------------------------------------------------------------
# group_items
# ---------------------------------------------------------------------------

def test_group_items_orders_categories_and_keeps_in_group_rank():
    items = [
        make_categorized_item(1, "industry", 90),
        make_categorized_item(2, "official", 80),
        make_categorized_item(3, "industry", 70),
        make_categorized_item(4, "watch", 60),
        make_categorized_item(5, "official", 50),
        make_categorized_item(6, "multi_source", 40),
    ]

    groups = gwag.group_items(items)

    assert [category for category, _ in groups] == [
        "official", "multi_source", "industry", "watch",
    ]
    by_category = dict(groups)
    # In-group order follows the peak_score ranking, untouched by grouping.
    assert [it["title"] for it in by_category["official"]] == [
        "分类测试新闻 2（official）", "分类测试新闻 5（official）",
    ]
    assert [it["title"] for it in by_category["industry"]] == [
        "分类测试新闻 1（industry）", "分类测试新闻 3（industry）",
    ]


def test_group_items_unknown_category_goes_last_with_label_passthrough():
    items = [
        make_categorized_item(1, "watch", 90),
        make_categorized_item(2, "breaking", 80),
    ]

    groups = gwag.group_items(items)

    assert [category for category, _ in groups] == ["watch", "breaking"]
    html = gwag.render_group_section("breaking", groups[1][1])
    assert "breaking" in html  # unknown labels pass through unchanged


def test_group_items_missing_category_defaults_to_watch():
    item = make_item(1)
    item.pop("category")

    groups = gwag.group_items([item])

    assert [category for category, _ in groups] == ["watch"]


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def test_render_grouped_html_restarts_numbering_per_section():
    items = [
        make_categorized_item(1, "official", 90),
        make_categorized_item(2, "official", 80),
        make_categorized_item(3, "industry", 70),
    ]
    html = gwag.render_grouped_article_html(
        items,
        title="AI 雷达 · 8月21日｜今日精选3条",
        digest="摘要",
        brand="AI 雷达",
        issue_label="8月21日 周五",
        radar_url="https://example.github.io/ai-news-radar/",
    )

    # Section headers in category order, each with its item count.
    official_head = html.index("官方更新<span")
    industry_head = html.index("行业动态<span")
    assert official_head < industry_head
    assert "2 条" in html[official_head:industry_head]
    assert "1 条" in html[industry_head:]

    # Numbering restarts at ① inside each section.
    assert html.count("①") == 2
    assert "②" in html

    # Every item (title + meta line + plain-text URL) is still rendered.
    for idx in (1, 2, 3):
        assert f"分类测试新闻 {idx}" in html
        assert f"https://example.com/story/{idx}" in html
    # 多源热议 / 值得关注 sections absent when empty.
    assert "多源热议" not in html
    assert "值得关注" not in html


# ---------------------------------------------------------------------------
# Cover reuse
# ---------------------------------------------------------------------------

def test_reuse_cover_only_for_same_issue_date(tmp_path):
    main_dir = tmp_path / "weixin"
    main_dir.mkdir()
    cover_bytes = b"\x89PNG fake cover"
    (main_dir / "cover.png").write_bytes(cover_bytes)

    (main_dir / "meta.json").write_text(
        json.dumps({"issue_date": "2026-08-21", "cover": "cover.png"}),
        encoding="utf-8",
    )
    assert gwag.reuse_cover(main_dir, "2026-08-21") == (cover_bytes, "cover.png")

    # Stale cover from a previous day must never be reused.
    assert gwag.reuse_cover(main_dir, "2026-08-22") == (None, None)

    # Missing meta / missing cover file degrade to regeneration.
    (main_dir / "meta.json").unlink()
    assert gwag.reuse_cover(main_dir, "2026-08-21") == (None, None)


# ---------------------------------------------------------------------------
# End-to-end (keyless) run
# ---------------------------------------------------------------------------

def test_end_to_end_keyless_run_writes_grouped_output(tmp_path):
    data_dir, assets_dir = write_fixture(
        tmp_path,
        [
            make_categorized_item(1, "official", 92),
            make_categorized_item(2, "industry", 88),
            make_categorized_item(3, "industry", 84),
            make_categorized_item(4, "watch", 70),
        ],
    )
    make_static_asset(assets_dir)

    # Main variant already ran today: cover reuse + shared cache expected.
    main_dir = tmp_path / "weixin"
    main_dir.mkdir()
    issue_date = datetime.now(gwa.TZ_CN).strftime("%Y-%m-%d")
    cover_bytes = b"\xff\xd8fake-jpeg-bytes"
    (main_dir / "cover.jpg").write_bytes(cover_bytes)
    (main_dir / "meta.json").write_text(
        json.dumps({"issue_date": issue_date, "cover": "cover.jpg"}),
        encoding="utf-8",
    )
    (main_dir / "reason-cache.json").write_text(
        json.dumps({"version": gwa.CACHE_VERSION, "entries": {}}),
        encoding="utf-8",
    )

    grouped_dir = tmp_path / "weixin-grouped"
    args = [
        "--data-dir", str(data_dir),
        "--output-dir", str(grouped_dir),
        "--main-output-dir", str(main_dir),
        "--assets-dir", str(assets_dir),
    ]
    with patch.dict("os.environ", {}, clear=True):
        rc = gwag.main(args)

    assert rc == 0
    html_text = (grouped_dir / "index.html").read_text(encoding="utf-8")
    meta = read_json(grouped_dir / "meta.json")

    # Layout marker + section census.
    assert meta["layout"] == "grouped"
    assert meta["item_count"] == 4
    assert meta["sections"] == [
        {"category": "official", "label": "官方更新", "count": 1},
        {"category": "industry", "label": "行业动态", "count": 2},
        {"category": "watch", "label": "值得关注", "count": 1},
    ]

    # Title identical to the main variant's fixed template.
    assert re.fullmatch(r"AI 雷达 · \d+月\d+日｜今日精选4条", meta["title"])

    # All four items present, grouped, in ranking order inside sections.
    for idx in (1, 2, 3, 4):
        assert f"分类测试新闻 {idx}" in html_text
    assert html_text.index("官方更新<span") < html_text.index("行业动态<span")
    assert html_text.index("行业动态<span") < html_text.index("值得关注<span")

    # Cover reused verbatim from the main variant; no image API was needed.
    assert meta["cover"] == "cover.jpg"
    assert (grouped_dir / "cover.jpg").read_bytes() == cover_bytes

    # Shared cache stays in the main variant's directory.
    assert (main_dir / "reason-cache.json").exists()
    assert not (grouped_dir / "reason-cache.json").exists()


def test_end_to_end_falls_back_to_static_cover_without_main_variant(tmp_path):
    data_dir, assets_dir = write_fixture(
        tmp_path, [make_categorized_item(1, "official", 92)]
    )
    make_static_asset(assets_dir)
    grouped_dir = tmp_path / "weixin-grouped"
    args = [
        "--data-dir", str(data_dir),
        "--output-dir", str(grouped_dir),
        "--main-output-dir", str(tmp_path / "weixin"),  # does not exist
        "--assets-dir", str(assets_dir),
    ]

    with patch.dict("os.environ", {}, clear=True):
        rc = gwag.main(args)

    assert rc == 0
    meta = read_json(grouped_dir / "meta.json")
    # Keyless + nothing to reuse → same static fallback as the main variant.
    assert meta["cover"] == "cover.png"
    assert (grouped_dir / "cover.png").exists()
    # Cache is written into the (freshly created) main dir, still shared.
    assert (tmp_path / "weixin" / "reason-cache.json").exists()
