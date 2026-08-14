from __future__ import annotations

from datetime import datetime, timezone

from scripts.update_news import parse_tikhub_douyin_items


NOW = datetime(2026, 6, 19, 12, 0, tzinfo=timezone.utc)


def test_douyin_parser_keeps_video_description_not_nested_music_title():
    payload = {
        "data": {
            "items": [
                {
                    "aweme_info": {
                        "aweme_id": "video-1",
                        "desc": "用 Claude Code 搭建多智能体工作流",
                        "create_time": 1_771_800_000,
                        "author": {"nickname": "AI 大道"},
                    },
                    "music": {"id": "music-1", "title": "@AI大道创作的原声"},
                }
            ]
        }
    }

    items = parse_tikhub_douyin_items(payload, now=NOW, keyword="AI", limit=10)

    assert len(items) == 1
    assert items[0].title == "用 Claude Code 搭建多智能体工作流"
    assert items[0].source == "AI 大道"


def test_douyin_parser_skips_videos_without_a_real_title():
    payload = {
        "data": {
            "items": [
                {
                    "aweme_info": {
                        "aweme_id": "video-2",
                        "desc": "@AI大道创作的原声",
                        "author": {"nickname": "AI 大道"},
                    }
                }
            ]
        }
    }

    assert parse_tikhub_douyin_items(payload, now=NOW, keyword="AI", limit=10) == []
