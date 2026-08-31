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

from test_weixin_article import (
    BASE_ENV,
    COVER_SCENE_TEXT,
    LONG_GENERATED_REASON,
    make_item,
    make_static_asset,
    make_text_router,
    offline_session,
    read_json,
    text_response,
    write_fixture,
)


def make_categorized_item(idx: int, category: str, score: float) -> dict:
    item = make_item(idx, title=f"分类测试新闻 {idx}（{category}）", score=score)
    item["category"] = category
    return item


# ---------------------------------------------------------------------------
# group_items
# ---------------------------------------------------------------------------

def test_group_items_orders_categories_and_keeps_in_group_rank():
    items = [
        make_categorized_item(1, "multi_source", 90),
        make_categorized_item(2, "official", 80),
        make_categorized_item(3, "industry", 70),
        make_categorized_item(4, "watch", 60),
        make_categorized_item(5, "official", 50),
        make_categorized_item(6, "industry", 40),
    ]

    groups = gwag.group_items(items)

    assert [category for category, _ in groups] == [
        "official", "industry", "multi_source", "watch",
    ]
    by_category = dict(groups)
    # In-group order follows the peak_score ranking, untouched by grouping.
    assert [it["title"] for it in by_category["official"]] == [
        "分类测试新闻 2（official）", "分类测试新闻 5（official）",
    ]
    assert [it["title"] for it in by_category["industry"]] == [
        "分类测试新闻 3（industry）", "分类测试新闻 6（industry）",
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

def test_render_grouped_html_layout_order_centered_titles_and_boxes():
    items = [
        make_categorized_item(1, "official", 90),
        make_categorized_item(2, "official", 80),
        make_categorized_item(3, "industry", 70),
        make_categorized_item(4, "multi_source", 60),
        make_categorized_item(5, "watch", 50),
    ]
    html = gwag.render_grouped_article_html(
        items,
        title="AI 雷达 · 8月21日｜本周精选5条",
        digest="摘要",
        brand="AI 雷达",
        issue_label="8月21日 周五",
        radar_url="https://example.github.io/ai-news-radar/",
    )

    # Section order: 官方更新 → 行业动态 → 多源热议 → 值得关注. The ">label</p>"
    # marker only matches section titles — item meta lines carry the label
    # followed by " · source ...", never by "</p>".
    heads = ["官方更新", "行业动态", "多源热议", "值得关注"]
    positions = [html.index(f">{label}</p>") for label in heads]
    assert positions == sorted(positions)

    # Centered, color-coded titles, one per section, without item counts.
    assert html.count("text-align:center") == 4
    assert " 条</p>" not in html
    for style in gwag.CATEGORY_STYLES.values():
        assert f'color:{style["color"]};' in html

    # Boxes use the title hue with added transparency (rgba), derived — not
    # hand-matched — from CATEGORY_COLORS.
    for category, style in gwag.CATEGORY_STYLES.items():
        base = gwag.CATEGORY_COLORS[category]
        assert style["border"] == gwag.with_alpha(base, gwag.BOX_BORDER_ALPHA)
        assert style["background"] == gwag.with_alpha(base, gwag.BOX_BACKGROUND_ALPHA)

    # Every section's items sit inside one large enclosing box.
    assert html.count("border:1px solid") == 4
    assert html.count("border-radius:10px") == 4
    for style in gwag.CATEGORY_STYLES.values():
        assert f'border:1px solid {style["border"]}' in html
        assert f'background-color:{style["background"]}' in html

    # Numbering restarts at ① inside each section.
    assert html.count("①") == 4
    assert "②" in html

    # Every item (title + meta line + plain-text URL) is still rendered.
    for idx in range(1, 6):
        assert f"分类测试新闻 {idx}" in html
        assert f"https://example.com/story/{idx}" in html


def test_render_grouped_html_skips_empty_sections():
    items = [
        make_categorized_item(1, "official", 90),
        make_categorized_item(2, "industry", 70),
    ]
    html = gwag.render_grouped_article_html(
        items,
        title="t",
        digest="d",
        brand="AI 雷达",
        issue_label="8月21日 周五",
        radar_url="https://example.github.io/ai-news-radar/",
    )

    # 多源热议 / 值得关注 sections absent when empty.
    assert "多源热议" not in html
    assert "值得关注" not in html
    assert html.count("text-align:center") == 2


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
    assert re.fullmatch(r"AI 雷达 · \d+月\d+日｜本周精选4条", meta["title"])

    # All four items present, grouped, in the fixed section order.
    for idx in (1, 2, 3, 4):
        assert f"分类测试新闻 {idx}" in html_text
    assert html_text.index(">官方更新</p>") < html_text.index(">行业动态</p>")
    assert html_text.index(">行业动态</p>") < html_text.index(">值得关注</p>")

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


def test_end_to_end_stale_first_party_story_is_grouped_as_official(tmp_path):
    """The category override lives in the shared select_items, so the grouped
    variant must also sort a stale 'industry' story from a whitelisted
    official channel into the 官方更新 section."""
    item = make_item(1, title="官方博客发布新闻", score=95)
    item["category"] = "industry"  # stale persisted category
    item["source"] = "Claude：Blog（网页）"
    item["sources"] = [
        {
            "site_id": "aihot",
            "source": "Claude：Blog（网页）",
            "source_name": "AI HOT",
            "url": item["url"],
        }
    ]
    data_dir, assets_dir = write_fixture(tmp_path, [item])
    make_static_asset(assets_dir)
    grouped_dir = tmp_path / "weixin-grouped"
    args = [
        "--data-dir", str(data_dir),
        "--output-dir", str(grouped_dir),
        "--main-output-dir", str(tmp_path / "weixin"),
        "--assets-dir", str(assets_dir),
    ]

    with patch.dict("os.environ", {}, clear=True):
        rc = gwag.main(args)

    assert rc == 0
    meta = read_json(grouped_dir / "meta.json")
    assert meta["sections"] == [
        {"category": "official", "label": "官方更新", "count": 1}
    ]
    html_text = (grouped_dir / "index.html").read_text(encoding="utf-8")
    assert "官方博客发布新闻" in html_text
    assert "行业动态" not in html_text


def test_grouped_uses_translated_title_from_shared_cache(tmp_path):
    """Translations live in the shared main-variant cache: the grouped run
    must reuse them (zero text API calls) and render the Chinese title."""
    en_title = "Advancing price-performance for developers with GPT-5.6 in Kiro"
    zh_title = "GPT-5.6 接入 Kiro，为开发者提升模型性价比"
    item = make_item(1, title=en_title, score=92)
    item["category"] = "official"
    data_dir, assets_dir = write_fixture(tmp_path, [item])
    make_static_asset(assets_dir)

    main_dir = tmp_path / "weixin"
    main_dir.mkdir()
    issue_date = datetime.now(gwa.TZ_CN).strftime("%Y-%m-%d")
    (main_dir / "cover.jpg").write_bytes(b"\xff\xd8fake-jpeg-bytes")
    (main_dir / "meta.json").write_text(
        json.dumps({"issue_date": issue_date, "cover": "cover.jpg"}),
        encoding="utf-8",
    )
    # The main variant already translated this title into the shared cache.
    tt_key = gwa.TITLE_TRANSLATE_CACHE_PREFIX + gwa.title_hash(en_title)
    (main_dir / "reason-cache.json").write_text(
        json.dumps(
            {
                "version": gwa.CACHE_VERSION,
                "entries": {
                    tt_key: {
                        "zh_title": zh_title,
                        "created_at": "2026-08-25T00:00:00Z",
                    }
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    grouped_dir = tmp_path / "weixin-grouped"
    args = [
        "--data-dir", str(data_dir),
        "--output-dir", str(grouped_dir),
        "--main-output-dir", str(main_dir),
        "--assets-dir", str(assets_dir),
    ]
    # Every spec is None: ANY text API call fails the test — the cached
    # translation, the reused cover and the guide fallback (no grounding)
    # must make this a zero-call run.
    side_effect, calls = make_text_router()
    with patch.dict("os.environ", BASE_ENV, clear=True), patch(
        "scripts.generate_weixin_article.requests.post", side_effect=side_effect
    ), patch("scripts.generate_weixin_article.time.sleep"):
        rc = gwag.main(args)

    assert rc == 0
    assert calls["translate"] == 0
    html_text = (grouped_dir / "index.html").read_text(encoding="utf-8")
    assert zh_title in html_text
    assert en_title not in html_text


def test_grouped_regenerate_re_rolls_via_shared_cache(tmp_path):
    """--regenerate on the grouped variant drops the named entry from the
    SHARED cache, so exactly that guide is re-rolled (for both layouts)."""
    item1 = make_item(
        1, title="分类测试新闻 1", score=92,
        summary="这是第一条足够长的摘要内容，用于生成推荐语。",
    )
    item1["category"] = "official"
    item2 = make_item(
        2, title="分类测试新闻 2", score=88,
        summary="这是第二条足够长的摘要内容，用于生成推荐语。",
    )
    item2["category"] = "industry"
    data_dir, assets_dir = write_fixture(tmp_path, [item1, item2])
    make_static_asset(assets_dir)

    main_dir = tmp_path / "weixin"
    grouped_dir = tmp_path / "weixin-grouped"
    args = [
        "--data-dir", str(data_dir),
        "--output-dir", str(grouped_dir),
        "--main-output-dir", str(main_dir),
        "--assets-dir", str(assets_dir),
    ]

    def run(args_list, side_effect):
        with patch.dict("os.environ", BASE_ENV, clear=True), patch(
            "scripts.generate_weixin_article.requests.post", side_effect=side_effect
        ), patch(
            "scripts.generate_weixin_article_grouped.create_session",
            return_value=offline_session(),
        ), patch("scripts.generate_weixin_article.time.sleep"):
            return gwag.main(args_list)

    side_effect, first_calls = make_text_router(
        reason=text_response(LONG_GENERATED_REASON),
        scene=text_response(COVER_SCENE_TEXT),
    )
    rc = run(args, side_effect)
    assert rc == 0
    assert first_calls["reason"] == 2

    # Naming position 1 (overall selection order) re-rolls only that guide.
    side_effect, second_calls = make_text_router(
        reason=text_response(LONG_GENERATED_REASON),
        scene=text_response(COVER_SCENE_TEXT),
    )
    rc = run(args + ["--regenerate", "1"], side_effect)
    assert rc == 0
    assert second_calls["reason"] == 1
    # Both entries live in the shared (main-dir) cache, one freshly re-rolled.
    shared = read_json(main_dir / "reason-cache.json")
    guide_keys = [k for k in shared["entries"] if not k.startswith("tt1|")]
    assert sorted(guide_keys) == sorted(
        [gwa.cache_key("story_1", item1["title"]), gwa.cache_key("story_2", item2["title"])]
    )
