"""Tests for the weekly push path of scripts/generate_weixin_article_deep.py.

Covers ``build_weekly_brief`` (the 7-day story pool rebuilt from
``data/archive.json``) and ``load_push_brief`` (weekly pool with automatic
fallback to ``daily-brief.json``). Everything runs against temp dirs; the
real ``data/`` is never touched.
"""

from __future__ import annotations

import hashlib
import json
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import generate_weixin_article_deep as gwa
from scripts import update_news as un

NOW = datetime(2026, 8, 27, 12, 0, tzinfo=timezone.utc)

AI_TITLE_1 = "OpenAI launches GPT-6 Turbo for developers with native tool use"
AI_TITLE_2 = "OpenAI launches GPT-6 Turbo for all developers with native tool use"


def make_record(
    idx: int,
    title: str,
    url: str,
    hours_ago: float,
    *,
    site_id: str = "official_ai",
    source: str = "OpenAI News",
) -> dict:
    ts = NOW - timedelta(hours=hours_ago)
    iso = ts.isoformat().replace("+00:00", "Z")
    return {
        "id": f"item-{idx}",
        "site_id": site_id,
        "site_name": "Official AI",
        "source": source,
        "title": title,
        "url": url,
        "published_at": iso,
        "first_seen_at": iso,
    }


def write_data_dir(
    tmp: str,
    items: list[dict],
    *,
    title_cache: dict | None = None,
    daily_brief: dict | None = None,
) -> Path:
    data_dir = Path(tmp) / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "archive.json").write_text(
        json.dumps(
            {
                "generated_at": NOW.isoformat().replace("+00:00", "Z"),
                "window_days": 21,
                "total_items": len(items),
                "items": items,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    if title_cache is not None:
        (data_dir / "title-zh-cache.json").write_text(
            json.dumps(title_cache, ensure_ascii=False), encoding="utf-8"
        )
    if daily_brief is not None:
        (data_dir / "daily-brief.json").write_text(
            json.dumps(daily_brief, ensure_ascii=False), encoding="utf-8"
        )
    return data_dir


def brief_titles(brief: dict) -> set[str]:
    return {str(item.get("title") or "") for item in brief.get("items", [])}


def snapshot_dir(data_dir: Path) -> dict[str, bytes]:
    return {
        str(p.relative_to(data_dir)): p.read_bytes()
        for p in data_dir.rglob("*")
        if p.is_file()
    }


# ---------------------------------------------------------------------------
# Window filter: only the lookback window enters the pool
# ---------------------------------------------------------------------------

def test_weekly_window_keeps_recent_and_drops_old_items():
    items = [
        make_record(1, AI_TITLE_1, "https://openai.com/blog/gpt6-a", 140),  # 6 days ago: in
        make_record(2, "Anthropic ships Claude 5 with agent teams support",
                    "https://anthropic.com/news/claude5", 200),  # >8 days: out
    ]
    with tempfile.TemporaryDirectory() as tmp:
        data_dir = write_data_dir(tmp, items)
        brief = gwa.build_weekly_brief(data_dir, NOW, 20)

    assert brief is not None
    titles = brief_titles(brief)
    assert AI_TITLE_1 in titles
    assert "Anthropic ships Claude 5 with agent teams support" not in titles
    assert brief["window_hours"] == 168
    assert brief["total_items"] == len(brief["items"])


# ---------------------------------------------------------------------------
# Weekly freshness curve: flat for 6 days, 48h half-life after
# ---------------------------------------------------------------------------

def test_weekly_recency_flat_inside_six_days_and_decays_after():
    items = [
        make_record(1, AI_TITLE_1, "https://openai.com/blog/flat", 140),
        make_record(2, "Anthropic ships Claude 5 with agent teams support",
                    "https://anthropic.com/news/decay", 168),  # 24h past the flat segment
    ]
    with tempfile.TemporaryDirectory() as tmp:
        data_dir = write_data_dir(tmp, items)
        brief = gwa.build_weekly_brief(data_dir, NOW, 20)

    assert brief is not None
    recency_by_title = {
        str(item.get("title") or ""): item["importance_breakdown"]["recency"]
        for item in brief["items"]
    }
    assert recency_by_title[AI_TITLE_1] == 1.0
    assert abs(recency_by_title["Anthropic ships Claude 5 with agent teams support"]
               - 0.5 ** (24 / 48)) < 1e-3


# ---------------------------------------------------------------------------
# AI relevance filter (same criteria as the daily brief)
# ---------------------------------------------------------------------------

def test_weekly_pool_drops_non_ai_items():
    items = [
        make_record(1, AI_TITLE_1, "https://openai.com/blog/gpt6", 100),
        # official_ai is a trusted always-AI source, so the non-AI probe must
        # come from an untrusted site to exercise the relevance filter.
        make_record(2, "Local bakery opens second branch downtown",
                    "https://example.com/bakery", 100,
                    site_id="hackernews", source="Hacker News"),
    ]
    with tempfile.TemporaryDirectory() as tmp:
        data_dir = write_data_dir(tmp, items)
        brief = gwa.build_weekly_brief(data_dir, NOW, 20)

    assert brief is not None
    titles = brief_titles(brief)
    assert AI_TITLE_1 in titles
    assert "Local bakery opens second branch downtown" not in titles


# ---------------------------------------------------------------------------
# Story merge across days: near-identical headlines become one story
# ---------------------------------------------------------------------------

def test_weekly_merges_follow_up_coverage_into_one_story():
    items = [
        make_record(1, AI_TITLE_1, "https://openai.com/blog/gpt6-launch", 96),
        # Different site: same-site near-duplicates are collapsed by
        # suppress_near_duplicate_items as rewritten syndication, so the
        # follow-up report must come from another outlet to survive as a
        # second source.
        make_record(2, AI_TITLE_2, "https://technews.example/gpt6-followup", 30,
                    site_id="aibase", source="AIbase"),
    ]
    with tempfile.TemporaryDirectory() as tmp:
        data_dir = write_data_dir(tmp, items)
        brief = gwa.build_weekly_brief(data_dir, NOW, 20)

    assert brief is not None
    assert len(brief["items"]) == 1
    story = brief["items"][0]
    assert story["source_count"] == 2
    assert "multi_source" in story["reasons"]


# ---------------------------------------------------------------------------
# te1|/re1| cache restore (pins the pipeline's key formula)
# ---------------------------------------------------------------------------

def test_weekly_restores_enhanced_title_and_reason_from_pipeline_cache():
    url = "https://openai.com/blog/gpt6-cached"
    title = "OpenAI launches GPT-6 Turbo with native tool use for agents"
    key_body = hashlib.sha1(f"{un.normalize_url(url)}|{title}".encode("utf-8")).hexdigest()
    title_cache = {
        un.TITLE_ENHANCE_CACHE_PREFIX + key_body: "GPT-6 Turbo 发布：原生工具调用",
        un.RECOMMEND_REASON_CACHE_PREFIX + key_body: "GPT-6 Turbo 带来原生工具调用，值得细读。",
        un.TITLE_ENHANCE_CACHE_PREFIX + "deadbeef": "",  # negative cache: ignored
    }
    items = [make_record(1, title, url, 50)]
    with tempfile.TemporaryDirectory() as tmp:
        data_dir = write_data_dir(tmp, items, title_cache=title_cache)
        brief = gwa.build_weekly_brief(data_dir, NOW, 20)

    assert brief is not None
    story = brief["items"][0]
    assert story["title"] == "GPT-6 Turbo 发布：原生工具调用"
    assert story["primary_item"]["recommend_reason_zh"] == "GPT-6 Turbo 带来原生工具调用，值得细读。"


# ---------------------------------------------------------------------------
# Fallback to daily-brief.json
# ---------------------------------------------------------------------------

def make_daily_brief() -> dict:
    return {
        "generated_at": NOW.isoformat().replace("+00:00", "Z"),
        "window_hours": 24,
        "total_items": 1,
        "items": [{
            "story_id": "daily-story",
            "title": "Daily fallback story",
            "score": 0.8,
            "importance_score": 0.8,
            "category": "industry",
            "primary_item": {"title": "Daily fallback story"},
        }],
    }


def test_fallback_when_archive_missing(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        data_dir = Path(tmp) / "data"
        data_dir.mkdir()
        (data_dir / "daily-brief.json").write_text(
            json.dumps(make_daily_brief()), encoding="utf-8"
        )
        brief = gwa.load_push_brief(data_dir, 20)

    assert brief is not None
    assert brief_titles(brief) == {"Daily fallback story"}


def test_fallback_when_archive_corrupt(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        data_dir = write_data_dir(tmp, [make_record(1, AI_TITLE_1, "https://a.example/1", 10)],
                                  daily_brief=make_daily_brief())
        (data_dir / "archive.json").write_text("{ not json", encoding="utf-8")
        brief = gwa.load_push_brief(data_dir, 20)

    assert brief_titles(brief) == {"Daily fallback story"}


def test_fallback_when_no_story_passes_gate():
    # Discussion-tier items score ~0.58 < 0.72 gate: pool builds but is empty
    # after the quality gate, so the weekly brief must degrade to the daily one.
    items = [
        make_record(1, AI_TITLE_1, "https://news.ycombinator.com/item", 40,
                    site_id="hackernews", source="Hacker News"),
    ]
    with tempfile.TemporaryDirectory() as tmp:
        data_dir = write_data_dir(tmp, items, daily_brief=make_daily_brief())
        assert gwa.build_weekly_brief(data_dir, NOW, 20) is None
        brief = gwa.load_push_brief(data_dir, 20)

    assert brief_titles(brief) == {"Daily fallback story"}


def test_fallback_when_pipeline_module_unavailable(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        data_dir = write_data_dir(tmp, [make_record(1, AI_TITLE_1, "https://a.example/1", 10)],
                                  daily_brief=make_daily_brief())
        monkeypatch.setattr(gwa, "_un", None)
        assert gwa.build_weekly_brief(data_dir, NOW, 20) is None
        brief = gwa.load_push_brief(data_dir, 20)

    assert brief_titles(brief) == {"Daily fallback story"}


def test_force_daily_env_skips_weekly_build(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        data_dir = write_data_dir(tmp, [make_record(1, AI_TITLE_1, "https://a.example/1", 10)],
                                  daily_brief=make_daily_brief())
        monkeypatch.setenv("WEIXIN_FORCE_DAILY", "1")
        brief = gwa.load_push_brief(data_dir, 20)

    assert brief_titles(brief) == {"Daily fallback story"}


# ---------------------------------------------------------------------------
# data/ is strictly read-only for the weekly build
# ---------------------------------------------------------------------------

def test_weekly_build_does_not_write_to_data_dir():
    url = "https://openai.com/blog/gpt6-ro"
    title = "OpenAI launches GPT-6 Turbo with native tool use for agents"
    key_body = hashlib.sha1(f"{un.normalize_url(url)}|{title}".encode("utf-8")).hexdigest()
    title_cache = {un.TITLE_ENHANCE_CACHE_PREFIX + key_body: "缓存标题"}
    items = [make_record(1, title, url, 50)]
    with tempfile.TemporaryDirectory() as tmp:
        data_dir = write_data_dir(tmp, items, title_cache=title_cache)
        before = snapshot_dir(data_dir)
        gwa.build_weekly_brief(data_dir, NOW, 20)
        after = snapshot_dir(data_dir)

    assert before == after


# ---------------------------------------------------------------------------
# Lookback window env override + clamp
# ---------------------------------------------------------------------------

def test_lookback_env_override_shrinks_window(monkeypatch):
    items = [
        make_record(1, AI_TITLE_1, "https://openai.com/blog/gpt6-3d", 60),   # 2.5 days: in
        make_record(2, "Anthropic ships Claude 5 with agent teams support",
                    "https://anthropic.com/news/claude5-3d", 96),            # 4 days: out
    ]
    with tempfile.TemporaryDirectory() as tmp:
        data_dir = write_data_dir(tmp, items)
        monkeypatch.setenv("WEIXIN_LOOKBACK_DAYS", "3")
        brief = gwa.build_weekly_brief(data_dir, NOW, 20)

    assert brief is not None
    assert brief["window_hours"] == 72
    titles = brief_titles(brief)
    assert AI_TITLE_1 in titles
    assert "Anthropic ships Claude 5 with agent teams support" not in titles


def test_lookback_env_clamped_and_defaults(monkeypatch):
    monkeypatch.setenv("WEIXIN_LOOKBACK_DAYS", "99")
    assert gwa._weekly_lookback_days() == 20
    monkeypatch.setenv("WEIXIN_LOOKBACK_DAYS", "0")
    assert gwa._weekly_lookback_days() == 1
    monkeypatch.setenv("WEIXIN_LOOKBACK_DAYS", "not-a-number")
    assert gwa._weekly_lookback_days() == 7
    monkeypatch.delenv("WEIXIN_LOOKBACK_DAYS")
    assert gwa._weekly_lookback_days() == 7


# ---------------------------------------------------------------------------
# Official-source cap knob (default 16; set 0 for no cap)
#
# The cap is applied at the very END of selection, never to the pool: the
# uncapped greedy mechanism runs unchanged over the full pool, the issue's
# first N officials in display (score-descending) order stay untouched, and
# the freed slots are backfilled with the next non-official stories.
# ---------------------------------------------------------------------------

def test_official_cap_resolution(monkeypatch):
    monkeypatch.delenv("WEIXIN_OFFICIAL_CAP", raising=False)
    assert gwa._weekly_official_cap() == 16
    monkeypatch.setenv("WEIXIN_OFFICIAL_CAP", "0")
    assert gwa._weekly_official_cap() == 0
    monkeypatch.setenv("WEIXIN_OFFICIAL_CAP", "5")
    assert gwa._weekly_official_cap() == 5
    monkeypatch.setenv("WEIXIN_OFFICIAL_CAP", "not-a-number")
    assert gwa._weekly_official_cap() == 16


# 18 pairwise-distinct official stories for the default-cap end-to-end test.
# Distinct vendors/models plus distinct wording keep title similarity below
# the merge/near-dup thresholds, so exactly the cap — and not dedup — trims
# the selection.
CAP_POOL_TITLES = [
    "OpenAI ships GPT-6 Turbo with native tool use",
    "Anthropic releases Claude 5 agent teams for enterprises",
    "Google DeepMind previews Gemini 4 reasoning mode",
    "Microsoft embeds Copilot across Office desktop apps",
    "GitHub launches Copilot workspace for repository planning",
    "Hugging Face opens Smolmodels community repository",
    "Meta publishes Llama 4 fine-tuning toolkit",
    "DeepSeek updates V4 coder checkpoint",
    "Mistral ships Codestral autocomplete model",
    "xAI opens Grok enterprise API",
    "NVIDIA details Rubin AI rack power envelope",
    "Amazon adds Bedrock AI agents marketplace",
    "Apple ships on-device AI summarization SDK",
    "Samsung integrates AI translators into Galaxy phones",
    "Intel unveils Gaudi 4 AI training accelerator",
    "AMD previews MI450 AI inference platform",
    "IBM releases watsonx AI governance module",
    "Salesforce launches Agentforce AI billing tools",
]


def test_default_official_cap_keeps_uncapped_selection_prefix(monkeypatch):
    # The capped issue must contain exactly the uncapped issue's first 16
    # officials, untouched: the mechanism runs unchanged and the cap only
    # drops the issue's LAST officials — it never trims the pool or
    # reshuffles survivors. All 18 fixtures share one source, so pick order
    # equals display (score-descending) order and the kept officials are the
    # uncapped issue's first 16 entries.
    items = [
        make_record(i + 1, title, f"https://example.com/cap-{i}", 40)
        for i, title in enumerate(CAP_POOL_TITLES)
    ]
    with tempfile.TemporaryDirectory() as tmp:
        data_dir = write_data_dir(tmp, items)
        monkeypatch.delenv("WEIXIN_OFFICIAL_CAP", raising=False)
        capped = gwa.build_weekly_brief(data_dir, NOW, 20)
        monkeypatch.setenv("WEIXIN_OFFICIAL_CAP", "0")
        uncapped = gwa.build_weekly_brief(data_dir, NOW, 20)

    assert capped is not None
    assert uncapped is not None
    assert len(uncapped["items"]) == 18
    assert len(capped["items"]) == 16
    assert all(item["category"] == "official" for item in capped["items"])
    capped_titles = [item["title"] for item in capped["items"]]
    uncapped_titles = [item["title"] for item in uncapped["items"]]
    assert capped_titles == uncapped_titles[:16]


def test_official_cap_backfills_freed_slots_with_non_officials(monkeypatch):
    # Officials come from distinct channels so the same-source penalty stays
    # out of the picture; industry items are aihot records whose source is
    # NOT on the first-party whitelist, so they tier at ai_vertical (0.78)
    # and land in the "industry" category below the official floor.
    officials = [
        make_record(1, "OpenAI ships GPT-6 Turbo with native tool use",
                    "https://official.example/gpt6", 40, source="OpenAI News"),
        make_record(2, "Anthropic releases Claude 5 agent teams for enterprises",
                    "https://official.example/claude5", 40, source="Anthropic News"),
        make_record(3, "Google DeepMind previews Gemini 4 reasoning mode",
                    "https://official.example/gemini4", 40, source="Google DeepMind Blog"),
    ]
    industry_titles = [
        "Analysts say NVIDIA data-center revenue doubles on AI demand",
        "AI startups raise record funding round in latest quarter",
        "Researchers report breakthrough in LLM inference efficiency",
    ]
    industry = []
    for offset, title in enumerate(industry_titles):
        record = make_record(10 + offset, title, f"https://aihot.example/{offset}", 40,
                             site_id="aihot", source=f"AI Hot 观察{offset}")
        record["aihot_score"] = 80
        industry.append(record)

    with tempfile.TemporaryDirectory() as tmp:
        data_dir = write_data_dir(tmp, officials + industry)
        monkeypatch.setenv("WEIXIN_OFFICIAL_CAP", "2")
        capped = gwa.build_weekly_brief(data_dir, NOW, 5)
        monkeypatch.setenv("WEIXIN_OFFICIAL_CAP", "0")
        uncapped = gwa.build_weekly_brief(data_dir, NOW, 20)

    assert capped is not None
    assert uncapped is not None
    # Uncapped run keeps everything: 3 officials picked first, then industry.
    assert len(uncapped["items"]) == 6
    uncapped_officials = [i["title"] for i in uncapped["items"] if i["category"] == "official"]
    assert len(uncapped_officials) == 3
    # Capped run: exactly the first two officials of the uncapped mechanism...
    capped_officials = [i["title"] for i in capped["items"] if i["category"] == "official"]
    assert capped_officials == uncapped_officials[:2]
    # ...and the freed slots go to the industry pool (including the official
    # that scores ABOVE the industry items — raw score no longer resurrects
    # an official once the quota is full).
    assert len(capped["items"]) == 5
    capped_industry = {i["title"] for i in capped["items"] if i["category"] != "official"}
    assert capped_industry == set(industry_titles)
    assert uncapped_officials[2] not in {i["title"] for i in capped["items"]}


def test_official_cap_keeps_display_rank_not_pick_order():
    # The cap keeps the issue's first officials in DISPLAY (score) order,
    # not the greedy's pick order. The same-source penalty makes the third
    # item of channel A get picked AFTER the single item of channel B even
    # though it scores higher; when the cap boundary falls between them the
    # higher-scoring late pick must survive.
    stories = [
        {"title": "Official one alpha release", "source": "Channel A", "category": "official", "score": 0.90},
        {"title": "Official two beta release", "source": "Channel A", "category": "official", "score": 0.89},
        {"title": "Official three gamma release", "source": "Channel A", "category": "official", "score": 0.88},
        {"title": "Official four delta announcement", "source": "Channel B", "category": "official", "score": 0.83},
        {"title": "Industry funding round recap", "source": "Channel C", "category": "industry", "score": 0.75},
    ]
    # Pick order: A1 (.90), A2 (.89-.03=.86), B4 (.83 > A3's .88-.06=.82), A3.
    picked = gwa._select_with_official_cap(stories, 4, 3)
    titles = {s["title"] for s in picked}
    assert titles == {
        "Official one alpha release",
        "Official two beta release",
        "Official three gamma release",  # picked last, but outscores B4
        "Industry funding round recap",   # backfilled into the freed slot
    }
    assert "Official four delta announcement" not in titles


def test_official_cap_env_override_and_disable(monkeypatch):
    items = [
        make_record(1, AI_TITLE_1, "https://openai.com/blog/cap-a", 40),
        make_record(2, "Anthropic ships Claude 5 with agent teams support",
                    "https://anthropic.com/news/cap-b", 40),
    ]
    with tempfile.TemporaryDirectory() as tmp:
        data_dir = write_data_dir(tmp, items)
        monkeypatch.setenv("WEIXIN_OFFICIAL_CAP", "1")
        capped = gwa.build_weekly_brief(data_dir, NOW, 20)
        assert capped is not None
        assert len(capped["items"]) == 1
        assert capped["items"][0]["category"] == "official"

        monkeypatch.setenv("WEIXIN_OFFICIAL_CAP", "0")
        uncapped = gwa.build_weekly_brief(data_dir, NOW, 20)

    assert uncapped is not None
    assert len(uncapped["items"]) == 2
    assert all(item["category"] == "official" for item in uncapped["items"])


# ---------------------------------------------------------------------------
# Over-selected guide-writing pool (pool_size)
#
# The pool carries max_items + extra candidates so items whose guide ends up
# empty can be dropped and backfilled without the issue shrinking.
# pool_size=None keeps the exact-max_items selection.
# ---------------------------------------------------------------------------

# CAP_POOL_TITLES (18) plus six more pairwise-distinct official stories:
# distinct vendors and wording keep title similarity below the merge/near-dup
# thresholds, so the pool size — and not dedup — decides the selection count.
OVERSELECT_TITLES = CAP_POOL_TITLES + [
    "Oracle embeds AI agents into Fusion cloud apps",
    "Adobe ships Firefly video editing assistant",
    "Baidu releases Ernie 5 reasoning preview",
    "Alibaba opens Qwen 3 multimodal model weights",
    "Tencent adds AI coding helper to cloud dev tools",
    "Sony unveils AI motion capture studio kit",
]


def test_build_weekly_brief_pool_size_overselects(monkeypatch):
    assert len(OVERSELECT_TITLES) == 24
    items = [
        make_record(i + 1, title, f"https://example.com/pool-{i}", 40)
        for i, title in enumerate(OVERSELECT_TITLES)
    ]
    with tempfile.TemporaryDirectory() as tmp:
        data_dir = write_data_dir(tmp, items)
        # Uncapped: the official cap must not trim the over-selected pool here.
        monkeypatch.setenv("WEIXIN_OFFICIAL_CAP", "0")
        default = gwa.build_weekly_brief(data_dir, NOW, 20)
        oversel = gwa.build_weekly_brief(data_dir, NOW, 20, pool_size=24)

    assert default is not None
    assert oversel is not None
    # pool_size=None keeps the former exact-max_items selection untouched.
    assert len(default["items"]) == 20
    assert len(oversel["items"]) == 24
    assert oversel["total_items"] == 24
    # Same greedy mechanism, bigger limit: the over-selected pool starts with
    # exactly the exact-size selection (prefix property).
    assert [i["title"] for i in oversel["items"][:20]] == [
        i["title"] for i in default["items"]
    ]


def test_build_weekly_brief_pool_size_keeps_official_cap(monkeypatch):
    # 18 officials + 5 industry; default cap 16. The cap still bounds the
    # OVER-selected pool, so the final issue (a subset) stays within it.
    # Officials come from distinct channels so the same-source penalty stays
    # out of the picture and all of them rank ahead of the industry items.
    officials = [
        make_record(i + 1, title, f"https://example.com/cap-pool-{i}", 40,
                    source=f"官方渠道 {i}")
        for i, title in enumerate(CAP_POOL_TITLES)
    ]
    industry_titles = [
        "Analysts say NVIDIA data-center revenue doubles on AI demand",
        "AI startups raise record funding round in latest quarter",
        "Researchers report breakthrough in LLM inference efficiency",
        "Chipmakers race to ship lower-power AI accelerators",
        "Cloud providers cut AI inference prices amid competition",
    ]
    industry = []
    for offset, title in enumerate(industry_titles):
        record = make_record(30 + offset, title, f"https://aihot.example/p{offset}", 40,
                             site_id="aihot", source=f"AI Hot 观察{offset}")
        record["aihot_score"] = 80
        industry.append(record)

    with tempfile.TemporaryDirectory() as tmp:
        data_dir = write_data_dir(tmp, officials + industry)
        monkeypatch.delenv("WEIXIN_OFFICIAL_CAP", raising=False)
        brief = gwa.build_weekly_brief(data_dir, NOW, 20, pool_size=20)

    assert brief is not None
    officials_in_pool = [
        i for i in brief["items"] if i["category"] == "official"
    ]
    assert len(brief["items"]) == 20
    assert len(officials_in_pool) == 16
