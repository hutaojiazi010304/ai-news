from __future__ import annotations

from datetime import datetime, timedelta, timezone

from scripts.update_news import add_source_tier_fields, merge_story_items


NOW = datetime(2026, 6, 2, 12, 0, tzinfo=timezone.utc)


def make_item(idx: int, *, title: str, url: str, hours_ago: int = 1, site_id: str = "aihot") -> dict:
    item = {
        "id": f"item-{idx}",
        "site_id": site_id,
        "site_name": site_id.title(),
        "source": "Test Feed",
        "title": title,
        "url": url,
        "published_at": (NOW - timedelta(hours=hours_ago)).isoformat().replace("+00:00", "Z"),
        "ai_is_related": True,
        "ai_score": 0.9,
    }
    return add_source_tier_fields(item)


def test_url_params_are_ignored_for_canonical_merge():
    items = [
        make_item(1, title="OpenAI releases a Codex update", url="https://example.com/openai-codex?utm_source=rss"),
        make_item(2, title="OpenAI releases a Codex update", url="https://example.com/openai-codex?ref=feed"),
    ]

    stories, events = merge_story_items(items, NOW, 24)

    assert len(stories) == 1
    assert stories[0]["duplicate_count"] == 2
    assert events[0]["reason"] == "canonical_url"
    assert events[0]["similarity"] == 1.0


def test_similar_titles_within_window_merge():
    items = [
        make_item(1, title="OpenAI launches Codex cloud agent for teams", url="https://example.com/a"),
        make_item(2, title="OpenAI launches Codex cloud agents for teams", url="https://example.org/b", hours_ago=2),
    ]

    stories, events = merge_story_items(items, NOW, 24)

    assert len(stories) == 1
    assert stories[0]["duplicate_count"] == 2
    assert events[0]["reason"] == "title_similarity"
    assert events[0]["similarity"] >= 0.86


def test_different_model_vendor_events_do_not_merge():
    items = [
        make_item(1, title="OpenAI launches GPT-5 coding model for agents", url="https://example.com/gpt5"),
        make_item(2, title="Anthropic launches Claude 5 coding model for agents", url="https://example.org/claude5"),
    ]

    stories, events = merge_story_items(items, NOW, 24)

    assert len(stories) == 2
    assert events == []


def test_same_site_shared_generic_url_with_different_titles_does_not_merge():
    # WaytoAGI publishes distinct community notes under one shared Feishu wiki
    # URL. Grouping purely by URL used to lump unrelated notes into one fat
    # "multi-source" story - each distinct note must stay its own story.
    shared_url = "https://waytoagi.feishu.cn/wiki/QPe5w5g7UisbEkkow8XcDmOpn8e?fromScene=spaceOverview"
    items = [
        make_item(
            1,
            title="如果你最近也在用 Codex、Claude Code 搭建网站，这套DESIGN.md工具方案能帮你摆脱千篇一律的廉价模板感",
            url=shared_url,
            site_id="waytoagi",
            hours_ago=1,
        ),
        make_item(
            2,
            title="常年批量制作淘宝、亚马逊商品图的手工艺商家，这套子母二段式提示词体系可以帮你处理AI篡改产品造型的低效难题",
            url=shared_url,
            site_id="waytoagi",
            hours_ago=2,
        ),
        make_item(
            3,
            title="AJ受邀参加了D20峰会 AI 云智能分论坛 Go! Vibe Designing，做了一场设计流程重组的分享",
            url=shared_url,
            site_id="waytoagi",
            hours_ago=3,
        ),
    ]

    stories, events = merge_story_items(items, NOW, 24)

    assert len(stories) == 3
    assert all(story["duplicate_count"] == 1 for story in stories)
    assert events == []


def test_same_site_shared_url_and_same_title_still_merges():
    # The same WaytoAGI note reappearing across consecutive pipeline runs
    # (identical URL and title) must still dedupe into a single story.
    shared_url = "https://waytoagi.feishu.cn/wiki/QPe5w5g7UisbEkkow8XcDmOpn8e?fromScene=spaceOverview"
    title = "如果你最近也在用 Codex、Claude Code 搭建网站，这套DESIGN.md工具方案能帮你摆脱千篇一律的廉价模板感"
    items = [
        make_item(1, title=title, url=shared_url, site_id="waytoagi", hours_ago=1),
        make_item(2, title=title, url=shared_url, site_id="waytoagi", hours_ago=25),
    ]

    stories, events = merge_story_items(items, NOW, 24)

    assert len(stories) == 1
    assert stories[0]["duplicate_count"] == 2
    assert events[0]["reason"] == "canonical_url"
    assert events[0]["similarity"] == 1.0
