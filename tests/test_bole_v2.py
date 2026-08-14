"""Tests for Bole picks v2: quality gate (宁缺毋滥), same-cluster suppression
across the whole window, and the scoring backtest comparison core."""

from __future__ import annotations

from scripts.backtest_scoring import compare_scoring
from scripts.update_news import (
    build_daily_brief_payload,
    select_diverse_stories,
    story_passes_brief_gate,
)


def make_story(idx: int, *, source: str = "AIbase", score: float = 0.8, sources: int = 1, title: str | None = None) -> dict:
    return {
        "story_id": f"story_{idx}",
        "title": title or f"Story number {idx} about something specific enough",
        "source": source,
        "score": score,
        "source_count": sources,
    }


class TestBriefGate:
    def test_multi_source_passes_regardless_of_score(self):
        assert story_passes_brief_gate(make_story(1, score=0.4, sources=2))

    def test_single_source_needs_strong_score(self):
        assert not story_passes_brief_gate(make_story(1, score=0.71, sources=1))
        assert story_passes_brief_gate(make_story(1, score=0.72, sources=1))

    def test_quiet_day_yields_empty_brief_not_padding(self):
        weak = [make_story(i, score=0.5, sources=1) for i in range(8)]
        payload = build_daily_brief_payload(weak, generated_at="2026-06-11T12:00:00Z", window_hours=24)
        assert payload["total_items"] == 0
        assert payload["items"] == []


class TestClusterSuppression:
    def test_same_event_hours_apart_takes_one_slot(self):
        a = make_story(1, score=0.9, title="加拿大推出法案，禁止16岁以下儿童使用社交媒体，并对人工智能聊天机器人进行监管 - Reuters")
        b = make_story(2, score=0.85, source="Other", title="加拿大推出立法，禁止16岁以下儿童使用社交媒体，并对人工智能聊天机器人进行监管 - Reuters")
        c = make_story(3, score=0.8, source="Third", title="OpenAI 发布全新 Codex 智能体编排能力，开发者可并行调度")
        picked = select_diverse_stories([a, b, c], 3)
        titles = [s["story_id"] for s in picked]
        assert "story_1" in titles
        assert "story_2" not in titles
        assert "story_3" in titles

    def test_distinct_vendors_not_suppressed(self):
        a = make_story(1, score=0.9, title="OpenAI 发布 GPT-6 模型，推理能力大幅提升值得关注")
        b = make_story(2, score=0.85, source="Other", title="Anthropic 发布 Claude 6 模型，推理能力大幅提升值得关注")
        picked = select_diverse_stories([a, b], 2)
        assert len(picked) == 2


class TestCompareScoring:
    def test_reports_flips_and_keep_rates(self):
        items = [
            {"site_id": "s1", "title": "item one", "url": "https://e.com/1"},
            {"site_id": "s1", "title": "item two", "url": "https://e.com/2"},
            {"site_id": "s2", "title": "item three", "url": "https://e.com/3"},
        ]

        def baseline(record):
            return {"is_ai_related": True, "label": "ai_general", "score": 0.7}

        def candidate(record):
            keep = record["title"] != "item two"
            return {"is_ai_related": keep, "label": "model_release" if keep else "not_ai", "score": 0.8 if keep else 0.1}

        report = compare_scoring(items, baseline, candidate)
        assert report["total_items"] == 3
        assert report["kept_baseline"] == 3
        assert report["kept_candidate"] == 2
        assert report["flips_to_drop_count"] == 1
        assert report["flips_to_drop_samples"][0]["title"] == "item two"
        assert report["label_moves"] == {"ai_general -> model_release": 2}
        assert report["per_site"]["s1"]["kept_candidate"] == 1
