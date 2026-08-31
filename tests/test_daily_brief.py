from __future__ import annotations

from datetime import datetime, timedelta, timezone

from scripts.update_news import (
    add_source_tier_fields,
    apply_story_peak_scores,
    build_daily_brief_payload,
    build_merge_log_payload,
    build_stories_payload,
    calculate_item_importance,
    clean_feed_summary,
    editorial_score,
    fetch_feed_as_official_items,
    headline_freshness_score,
    load_story_peak_state,
    merge_story_items,
    select_diverse_stories,
    story_gate_score,
    story_passes_brief_gate,
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


# ---------------------------------------------------------------------------
# Peak-score tracking (daily-push support): the brief must judge a story by
# the best score it reached during the window, not by its decayed score at
# rebuild time, so single-source stories do not age out before a 10:00 push.
# ---------------------------------------------------------------------------

def test_brief_gate_reads_peak_score_when_present():
    # Single-source story whose current score decayed below the gate after a
    # strong start: the persisted peak keeps it brief-eligible.
    decayed = {"source_count": 1, "score": 0.65, "peak_score": 0.78}
    assert story_passes_brief_gate(decayed) is True
    assert story_gate_score(decayed) == 0.78

    # Without a persisted peak the gate falls back to the current score.
    no_peak = {"source_count": 1, "score": 0.65}
    assert story_passes_brief_gate(no_peak) is False
    fresh = {"source_count": 1, "score": 0.8}
    assert story_passes_brief_gate(fresh) is True

    # Multi-source stories still need a quality floor: a low-score story
    # must not ride into the brief on source count alone (mirrors of one
    # forum thread can fake a high source count).
    multi = {"source_count": 2, "score": 0.3, "peak_score": 0.3}
    assert story_passes_brief_gate(multi) is False
    multi_ok = {"source_count": 2, "score": 0.65}
    assert story_passes_brief_gate(multi_ok) is True
    # The multi-source floor also reads the persisted peak.
    multi_peak = {"source_count": 3, "score": 0.5, "peak_score": 0.66}
    assert story_passes_brief_gate(multi_peak) is True
    # One source below the score gate stays out even above the multi floor.
    single_mid = {"source_count": 1, "score": 0.66}
    assert story_passes_brief_gate(single_mid) is False


def test_apply_story_peak_scores_keeps_max_across_runs():
    state = {"schema_version": 1, "stories": {}}
    first_run = [{"story_id": "story_a", "score": 0.8}]
    apply_story_peak_scores(first_run, state, NOW)
    assert first_run[0]["peak_score"] == 0.8
    assert state["stories"]["story_a"]["peak_score"] == 0.8

    # Ten hours later the recency component has decayed the score; the peak
    # must not shrink with it.
    later_run = [{"story_id": "story_a", "score": 0.69}]
    apply_story_peak_scores(later_run, state, NOW + timedelta(hours=10))
    assert later_run[0]["peak_score"] == 0.8
    assert state["stories"]["story_a"]["peak_score"] == 0.8

    # A later run may raise the peak (e.g. more sources join the story).
    hotter_run = [{"story_id": "story_a", "score": 0.86}]
    apply_story_peak_scores(hotter_run, state, NOW + timedelta(hours=12))
    assert hotter_run[0]["peak_score"] == 0.86


def test_apply_story_peak_scores_prunes_stale_entries():
    stale_seen = (NOW - timedelta(days=4)).isoformat().replace("+00:00", "Z")
    fresh_seen = NOW.isoformat().replace("+00:00", "Z")
    state = {
        "schema_version": 1,
        "stories": {
            "old_story": {"peak_score": 0.9, "last_seen_at": stale_seen},
            "recent_story": {"peak_score": 0.8, "last_seen_at": fresh_seen},
        },
    }
    apply_story_peak_scores([{"story_id": "new_story", "score": 0.5}], state, NOW)
    assert "old_story" not in state["stories"]
    assert "recent_story" in state["stories"]
    assert "new_story" in state["stories"]


def test_load_story_peak_state_tolerates_missing_and_corrupt_files(tmp_path):
    missing = tmp_path / "story-peak-state.json"
    assert load_story_peak_state(missing) == {"schema_version": 1, "stories": {}}

    corrupt = tmp_path / "corrupt.json"
    corrupt.write_text("{not json", encoding="utf-8")
    state = load_story_peak_state(corrupt)
    assert state == {"schema_version": 1, "stories": {}}

    # Malformed entries are dropped, valid ones survive the round trip.
    partial = tmp_path / "partial.json"
    partial.write_text(
        '{"schema_version": 1, "stories": {'
        '"good": {"peak_score": 0.75, "last_seen_at": "2026-06-02T12:00:00Z"},'
        '"bad": {"peak_score": "nope"},'
        '"also_bad": 3'
        "}}",
        encoding="utf-8",
    )
    state = load_story_peak_state(partial)
    assert set(state["stories"]) == {"good"}
    assert state["stories"]["good"]["peak_score"] == 0.75


def test_select_diverse_stories_ranks_by_peak_score():
    """Over-capacity selection must not let fresher mid-tier items push an
    early-window high-peak story out of the brief."""
    early_peak = {
        "story_id": "early",
        "title": "Early major model release dominates benchmarks",
        "source": "Source A",
        "source_count": 1,
        "score": 0.66,       # decayed by push time
        "peak_score": 0.85,  # earned hours ago
    }
    fresh = {
        "story_id": "fresh",
        "title": "Fresh minor tooling update ships today",
        "source": "Source B",
        "source_count": 1,
        "score": 0.74,
        "peak_score": 0.74,
    }
    picked = select_diverse_stories([fresh, early_peak], limit=2)
    assert [story["story_id"] for story in picked] == ["early", "fresh"]


def test_daily_brief_keeps_decayed_single_source_story_via_peak():
    """End-to-end shape: a story list whose only high-peak entry has decayed
    below the gate still lands in the brief payload."""
    stories = [
        {"story_id": "kept", "title": "Kept story", "source_count": 1,
         "score": 0.68, "peak_score": 0.8},
        {"story_id": "dropped", "title": "Dropped story", "source_count": 1,
         "score": 0.68, "peak_score": 0.6},
    ]
    payload = build_daily_brief_payload(stories, generated_at="2026-06-02T12:00:00Z", window_hours=24)
    assert payload["total_items"] == 1
    assert payload["items"][0]["story_id"] == "kept"


# ---------------------------------------------------------------------------
# Recency half-life: tuned to 72h so a story keeps most of its recency value
# across the 24h window before a once-daily push. Frontend skins mirror this
# constant (freshnessPercent in assets/app.js and classic/assets/app.js).
# ---------------------------------------------------------------------------

def test_headline_freshness_uses_72h_half_life():
    fresh = make_item(1, hours_ago=0)
    one_half_life = make_item(2, hours_ago=72)
    in_window = make_item(3, hours_ago=24)

    assert headline_freshness_score(fresh, NOW) == 1.0
    assert headline_freshness_score(one_half_life, NOW) == 0.5
    # 24h of age costs ~21% of recency (0.5 ** (24/72) ≈ 0.794); under the
    # old 48h half-life the same story had already lost ~29%.
    assert abs(headline_freshness_score(in_window, NOW) - 0.5 ** (24 / 72)) < 1e-9


def test_importance_recency_component_follows_72h_half_life():
    item = make_item(1, hours_ago=24)
    breakdown = calculate_item_importance(item, NOW, 24)["breakdown"]
    assert abs(breakdown["recency"] - 0.5 ** (24 / 72)) < 1e-4


# ---------------------------------------------------------------------------
# flat_hours (weekly push): the score stays at 1.0 until the item is
# flat_hours old and only then decays. flat_hours=0 keeps the historical
# behaviour bit-for-bit (frontend mirror + daily pipeline are untouched).
# ---------------------------------------------------------------------------

def test_headline_freshness_flat_segment_holds_full_score():
    flat = 144.0  # 6 days: the weekly push keeps the first 6 days undecayed
    inside_flat = make_item(1, hours_ago=140)  # still inside the flat segment
    at_boundary = make_item(2, hours_ago=144)  # exactly 6 days: decay starts now
    just_past_flat = make_item(3, hours_ago=168)  # 7 days: 24h into the decay
    deep = make_item(4, hours_ago=192)  # 8 days: 48h into the decay

    assert headline_freshness_score(inside_flat, NOW, flat_hours=flat) == 1.0
    assert headline_freshness_score(at_boundary, NOW, flat_hours=flat) == 1.0
    assert abs(
        headline_freshness_score(just_past_flat, NOW, half_life_hours=48.0, flat_hours=flat)
        - 0.5 ** (24 / 48)
    ) < 1e-9
    assert abs(
        headline_freshness_score(deep, NOW, half_life_hours=48.0, flat_hours=flat)
        - 0.5 ** (48 / 48)
    ) < 1e-9


def test_headline_freshness_flat_zero_matches_default():
    for hours_ago in (0, 24, 72, 168):
        item = make_item(1, hours_ago=hours_ago)
        assert headline_freshness_score(item, NOW, flat_hours=0.0) == headline_freshness_score(item, NOW)


def test_importance_flat_hours_passthrough():
    item = make_item(1, hours_ago=140)  # inside the weekly flat segment, decayed daily
    weekly = calculate_item_importance(item, NOW, 168, half_life_hours=48.0, flat_hours=144.0)
    daily = calculate_item_importance(item, NOW, 168)
    assert weekly["breakdown"]["recency"] == 1.0
    assert abs(daily["breakdown"]["recency"] - 0.5 ** (140 / 72)) < 1e-4
    assert weekly["score"] > daily["score"]


# ---------------------------------------------------------------------------
# Official-feed summaries: the pipeline persists the RSS <description> as
# clean plain text so downstream guide writing (weixin daily push) can use
# it as offline grounding instead of fetching bot-blocked live pages.
# ---------------------------------------------------------------------------

def test_clean_feed_summary_strips_html_and_truncates():
    html = "<p>OpenAI reaffirms <b>Zero Data Retention</b> for API customers.</p>"
    assert clean_feed_summary(html) == "OpenAI reaffirms Zero Data Retention for API customers."
    assert clean_feed_summary("") == ""
    assert clean_feed_summary(None) == ""
    assert len(clean_feed_summary("x" * 5000)) <= 800


OFFICIAL_FEED_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
<title>OpenAI News</title>
<item>
  <title>Offering Zero Data Retention for frontier models</title>
  <link>https://openai.com/index/offering-zero-data-retention-for-frontier-models</link>
  <pubDate>Tue, 02 Jun 2026 06:00:00 GMT</pubDate>
  <description><![CDATA[<p>OpenAI reaffirms <b>Zero Data Retention</b> for eligible
    API customers and previews new privacy features.</p>]]></description>
</item>
<item>
  <title>An item without a description</title>
  <link>https://openai.com/index/no-description</link>
  <pubDate>Tue, 02 Jun 2026 05:00:00 GMT</pubDate>
</item>
</channel></rss>
"""


def test_fetch_feed_as_official_items_persists_cleaned_rss_summary():
    class FakeResponse:
        content = OFFICIAL_FEED_XML

        def raise_for_status(self):
            return None

    class FakeSession:
        def get(self, url, **kwargs):
            return FakeResponse()

    feed = {
        "title": "OpenAI News",
        "xml_url": "https://openai.com/news/rss.xml",
        "html_url": "https://openai.com/news",
    }
    items = fetch_feed_as_official_items(FakeSession(), feed, NOW)

    assert len(items) == 2
    with_summary = next(it for it in items if "Zero Data" in it.title)
    assert with_summary.meta["summary"] == (
        "OpenAI reaffirms Zero Data Retention for eligible API customers "
        "and previews new privacy features."
    )
    without = next(it for it in items if it.title == "An item without a description")
    assert without.meta["summary"] is None
