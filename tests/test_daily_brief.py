from __future__ import annotations

from datetime import datetime, timedelta, timezone

from scripts.update_news import (
    add_source_tier_fields,
    build_daily_brief_payload,
    build_merge_log_payload,
    build_stories_payload,
    calculate_item_importance,
    editorial_score,
    merge_story_items,
    waytoagi_updates_to_raw_items,
)


NOW = datetime(2026, 6, 2, 12, 0, tzinfo=timezone.utc)


def make_item(
    idx: int,
    *,
    site_id: str = "official_ai",
    title: str | None = None,
    hours_ago: int = 1,
    ai_score: float = 0.9,
) -> dict:
    item = {
        "id": f"item-{idx}",
        "site_id": site_id,
        "site_name": site_id.replace("_", " ").title(),
        "source": "Test Feed",
        "title": title or f"OpenAI ships Codex data pipeline update {idx}",
        "url": f"https://example.com/news/{idx}",
        "published_at": (NOW - timedelta(hours=hours_ago)).isoformat().replace("+00:00", "Z"),
        "ai_is_related": True,
        "ai_score": ai_score,
    }
    return add_source_tier_fields(item)


def test_importance_score_favors_official_relevant_recent_items():
    official = make_item(1, site_id="official_ai", hours_ago=1, ai_score=0.95)
    discussion = make_item(2, site_id="newsnow", hours_ago=20, ai_score=0.65)

    official_score = calculate_item_importance(official, NOW, 24)["score"]
    discussion_score = calculate_item_importance(discussion, NOW, 24)["score"]

    assert official_score > discussion_score


def test_importance_score_uses_aihot_editorial_score():
    strong = make_item(1, site_id="aihot", ai_score=0.7)
    weak = make_item(2, site_id="aihot", ai_score=0.7)
    strong["aihot_score"] = 88
    weak["aihot_score"] = 60

    strong_importance = calculate_item_importance(strong, NOW, 24)
    weak_importance = calculate_item_importance(weak, NOW, 24)

    assert editorial_score(strong) == 0.88
    assert strong_importance["score"] > weak_importance["score"]
    assert "editorial" in strong_importance["breakdown"]


def test_waytoagi_latest_updates_become_community_raw_items():
    payload = {
        "root_url": "https://waytoagi.example/wiki",
        "latest_date": "2026-06-15",
        "updates_today": [
            {"date": "2026-06-15", "title": "Agent loop community writeup", "url": "https://waytoagi.example/wiki"}
        ],
    }

    items = waytoagi_updates_to_raw_items(payload, NOW)

    assert len(items) == 1
    assert items[0].site_id == "waytoagi"
    assert items[0].site_name == "WaytoAGI"
    assert items[0].source == "社区更新 · 2026-06-15"
    assert items[0].published_at == NOW


def test_daily_brief_respects_20_cap_when_enough_distinct_stories_exist():
    # Titles must be genuinely distinct: same-cluster stories are now
    # deliberately suppressed at selection time, so near-identical titles
    # may no longer fill the brief.
    subjects = [
        "quantum annealing", "protein folding", "code review bots", "speech synthesis",
        "robot grasping", "wafer yields", "vector databases", "edge inference",
        "retrieval pipelines", "agent sandboxing", "diffusion video", "tokenizer design",
        "kernel fusion", "sparse attention", "memory tiering", "eval harnesses",
        "watermark detection", "policy gradients", "scene graphs", "voice cloning",
        "data curation", "reward modeling", "chip packaging", "model routing", "cache layouts",
    ]
    items = [make_item(i, title=f"Briefing {i}: advances in {subjects[i]} reshape AI workloads") for i in range(25)]
    stories, _events = merge_story_items(items, NOW, 24, title_threshold=1.1)

    payload = build_daily_brief_payload(stories, generated_at="2026-06-02T12:00:00Z", window_hours=24)

    assert len(stories) == 25
    assert payload["total_items"] == 20
    assert len(payload["items"]) == 20


def test_daily_brief_record_supports_bole_output_contract():
    items = [
        make_item(1, title="OpenAI releases Codex agent orchestration"),
        make_item(2, site_id="aihot", title="OpenAI releases Codex agent orchestration", ai_score=0.86),
    ]
    stories, events = merge_story_items(items, NOW, 24)

    payload = build_daily_brief_payload(stories, generated_at="2026-06-02T12:00:00Z", window_hours=24)
    record = payload["items"][0]

    assert events
    assert record["title"]
    assert record["url"]
    assert record["primary_url"] == record["url"]
    assert record["source"]
    assert record["source_name"]
    assert record["source_count"] == 2
    assert record["score"] == record["importance"] == record["importance_score"]
    assert record["category"] in {"official", "multi_source", "industry", "watch"}
    assert record["reasons"]
    assert record["earliest_at"]
    assert record["latest_at"]
    assert len(record["items"]) == 2
    assert len(record["sources"]) == 2
    assert record["primary_item"]["id"] == "item-1"


def test_stories_and_merge_log_payload_shapes_are_explicit():
    items = [
        make_item(1, title="OpenAI releases Codex agent orchestration"),
        make_item(2, title="OpenAI releases Codex agent orchestration"),
    ]
    stories, events = merge_story_items(items, NOW, 24)

    stories_payload = build_stories_payload(stories, generated_at="2026-06-02T12:00:00Z", window_hours=24)
    merge_payload = build_merge_log_payload(events, generated_at="2026-06-02T12:00:00Z")

    assert stories_payload["total_stories"] == 1
    assert stories_payload["stories"][0]["story_id"]
    assert merge_payload["merge_strategy"] == "url_or_title_similarity_v0_6"
    assert merge_payload["total_events"] == len(events) == 1
