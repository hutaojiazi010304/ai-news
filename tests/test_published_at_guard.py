"""Tests for the future-published-at defenses.

Real-world trigger: InfoQ CN's RSS labels Beijing time as GMT
("Tue, 14 Jul 2026 22:01:15 GMT" is actually 22:01 CST = 14:01 UTC), so the
parsed published_at leads real time by 8 hours and the site timeline shows
items from the future.
"""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from scripts.update_news import (
    CST_MISLABEL_OFFSET,
    FUTURE_PUBLISH_SKEW,
    build_story_record,
    correct_feed_published_batch,
    correct_future_published,
)

NOW = datetime(2026, 7, 14, 13, 29, 19, tzinfo=timezone.utc)


class CorrectFuturePublishedTest(unittest.TestCase):
    def test_past_time_unchanged(self):
        published = NOW - timedelta(hours=3)
        self.assertEqual(correct_future_published(published, NOW), published)

    def test_slightly_future_within_skew_unchanged(self):
        published = NOW + timedelta(minutes=90)
        self.assertEqual(correct_future_published(published, NOW), published)

    def test_none_passthrough(self):
        self.assertIsNone(correct_future_published(None, NOW))

    def test_cst_mislabeled_gmt_corrected_minus_8h(self):
        # InfoQ 病例：feed 写 22:01:15 GMT，实为北京时间 → 应纠正为 14:01:15Z
        published = datetime(2026, 7, 14, 22, 1, 15, tzinfo=timezone.utc)
        fixed = correct_future_published(published, NOW, assume_cst_mislabel=True)
        self.assertEqual(fixed, published - CST_MISLABEL_OFFSET)
        self.assertLessEqual(fixed, NOW + FUTURE_PUBLISH_SKEW)

    def test_future_non_cn_falls_back_to_now(self):
        published = NOW + timedelta(hours=8)
        self.assertEqual(correct_future_published(published, NOW), NOW)

    def test_far_future_cn_still_falls_back_to_now(self):
        # 减 8h 后仍在未来（源头日期整个写错）→ 回退抓取时间
        published = NOW + timedelta(hours=20)
        self.assertEqual(
            correct_future_published(published, NOW, assume_cst_mislabel=True), NOW
        )


class CorrectFeedPublishedBatchTest(unittest.TestCase):
    def test_one_future_entry_corrects_whole_feed(self):
        # InfoQ 病例:最新条目在未来 → 判定整个 feed 错标,全部减 8h,
        # 包括没越过"现在"、单条防御抓不到的条目
        future = NOW + timedelta(hours=2, minutes=32)   # 22:01 GMT 标错的那条
        stale = NOW + timedelta(hours=-0, minutes=-49)  # 18:40 GMT 标错但已"过去"
        fixed = correct_feed_published_batch([future, stale], NOW, assume_cst_mislabel=True)
        self.assertEqual(fixed, [future - CST_MISLABEL_OFFSET, stale - CST_MISLABEL_OFFSET])

    def test_healthy_feed_untouched(self):
        healthy = [NOW - timedelta(hours=1), NOW - timedelta(hours=5)]
        self.assertEqual(
            correct_feed_published_batch(healthy, NOW, assume_cst_mislabel=True), healthy
        )

    def test_non_cn_feed_falls_back_per_entry(self):
        future = NOW + timedelta(hours=8)
        past = NOW - timedelta(hours=1)
        fixed = correct_feed_published_batch([future, past], NOW, assume_cst_mislabel=False)
        self.assertEqual(fixed, [NOW, past])

    def test_empty_list(self):
        self.assertEqual(correct_feed_published_batch([], NOW, assume_cst_mislabel=True), [])


class StoryFutureTimeGuardTest(unittest.TestCase):
    def _item(self, item_id: str, published_at: str) -> dict:
        return {
            "id": item_id,
            "title": "腾讯混元 Hy3 量化版发布",
            "url": f"https://example.com/{item_id}",
            "source": "InfoQ CN",
            "site_id": "opmlrss",
            "site_name": "OPML RSS",
            "published_at": published_at,
        }

    def test_story_times_clamped_to_now(self):
        future_iso = (NOW + timedelta(hours=8)).isoformat().replace("+00:00", "Z")
        past_iso = (NOW - timedelta(hours=2)).isoformat().replace("+00:00", "Z")
        record = build_story_record(
            "story_test",
            [self._item("a", past_iso), self._item("b", future_iso)],
            NOW,
            24,
        )
        for field in ("earliest_at", "latest_at"):
            value = record[field]
            self.assertIsNotNone(value)
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            self.assertLessEqual(parsed, NOW + timedelta(minutes=10))


if __name__ == "__main__":
    unittest.main()
