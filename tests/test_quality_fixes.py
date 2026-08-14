"""Tests for the v0.6.x quality fixes: URL-host-only relevance matching,
same-source decay in the daily brief, and near-duplicate item suppression."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from scripts.ai_relevance import score_ai_relevance
from scripts.update_news import select_diverse_stories, suppress_near_duplicate_items


NOW = datetime(2026, 6, 11, 12, 0, tzinfo=timezone.utc)


class TestUrlHostOnlyRelevance:
    def test_base64_url_path_cannot_fake_ai_signal(self):
        # Real-world case: Google News base64 path contains "llm" by accident.
        rec = {
            "site_id": "buzzing",
            "site_name": "Buzzing",
            "source": "news.google.com",
            "title": "巴勒斯坦人称，以色列定居者阻碍了村庄附近的灭火工作 - Reuters",
            "url": "https://news.google.com/rss/articles/CBMiwgFBVllmRkNCSmxQNnR6WktHcmdMQ2NuNHFjUmlEQk1nTGxsbUFCMEhRaEExWXg3?oc=5",
        }
        result = score_ai_relevance(rec)
        assert not result["is_ai_related"]
        assert result["label"] == "not_ai"

    def test_url_host_still_contributes_signal(self):
        rec = {
            "site_id": "opmlrss",
            "site_name": "OPML RSS",
            "source": "Some Blog",
            "title": "Weekly product update",
            "url": "https://openai.com/blog/weekly-update",
        }
        result = score_ai_relevance(rec)
        assert result["is_ai_related"]

    def test_dotted_ai_styled_title_keeps_signal(self):
        rec = {
            "site_id": "buzzing",
            "site_name": "Buzzing",
            "source": "news.google.com",
            "title": "Trump Muses About Government Taking a Piece of A.I. Companies - NYT",
            "url": "https://news.google.com/rss/articles/CCC?oc=5",
        }
        result = score_ai_relevance(rec)
        assert result["is_ai_related"]


def make_story(idx: int, source: str, score: float) -> dict:
    return {
        "story_id": f"story_{idx}",
        "title": f"Story {idx}",
        "source": source,
        "score": score,
    }


class TestSelectDiverseStories:
    def test_one_prolific_source_cannot_fill_the_brief(self):
        stories = [make_story(i, "AIbase", 0.81) for i in range(15)]
        stories += [make_story(100 + i, f"Official {i}", 0.78) for i in range(10)]
        picked = select_diverse_stories(stories, 20)
        aibase = sum(1 for s in picked if s["source"] == "AIbase")
        assert len(picked) == 20
        assert aibase < 15
        assert any(s["source"].startswith("Official") for s in picked[:10])

    def test_top_story_always_survives(self):
        stories = [make_story(0, "AIbase", 0.95)] + [make_story(i, f"S{i}", 0.5) for i in range(1, 5)]
        picked = select_diverse_stories(stories, 3)
        assert picked[0]["story_id"] == "story_0"


def make_dup_item(idx: int, title: str, minutes_ago: int, site_id: str = "buzzing", tier_rank: int = 5) -> dict:
    ts = (NOW - timedelta(minutes=minutes_ago)).isoformat()
    return {
        "id": f"item_{idx}",
        "site_id": site_id,
        "site_name": site_id,
        "source": "news.google.com",
        "title": title,
        "url": f"https://example.com/{idx}",
        "published_at": ts,
        "first_seen_at": ts,
        "source_tier_rank": tier_rank,
        "ai_score": 0.65,
    }


class TestSuppressNearDuplicateItems:
    def test_rewritten_syndication_collapses(self):
        a = make_dup_item(1, "加拿大推出法案，禁止16岁以下儿童使用社交媒体，并对人工智能聊天机器人进行监管 - Reuters", 10)
        b = make_dup_item(2, "加拿大推出立法，禁止16岁以下儿童使用社交媒体，并对人工智能聊天机器人进行监管 - Reuters", 9)
        out = suppress_near_duplicate_items([a, b])
        assert len(out) == 1

    def test_distinct_stories_survive(self):
        a = make_dup_item(1, "OpenAI 发布 GPT-6 模型，推理能力大幅提升", 10)
        b = make_dup_item(2, "Anthropic 发布 Claude 6 模型，推理能力大幅提升", 9)
        out = suppress_near_duplicate_items([a, b])
        assert len(out) == 2

    def test_cross_site_duplicates_are_kept(self):
        a = make_dup_item(1, "加拿大推出法案，禁止16岁以下儿童使用社交媒体，并对人工智能聊天机器人进行监管", 10, site_id="buzzing")
        b = make_dup_item(2, "加拿大推出立法，禁止16岁以下儿童使用社交媒体，并对人工智能聊天机器人进行监管", 9, site_id="techurls")
        out = suppress_near_duplicate_items([a, b])
        assert len(out) == 2

    def test_keeps_more_authoritative_copy(self):
        low = make_dup_item(1, "小米开源终端 AI 编程助手 MiMo Code，内置免费顶级多模态模型", 10, tier_rank=5)
        high = make_dup_item(2, "小米开源终端AI编程助手 MiMo Code，内置免费顶级多模态模型", 9, tier_rank=0)
        high["site_id"] = "buzzing"
        out = suppress_near_duplicate_items([low, high])
        assert len(out) == 1
        assert out[0]["id"] == "item_2"
