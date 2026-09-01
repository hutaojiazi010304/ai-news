import json
import os
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from scripts import update_news as un


def ts(year, month, day):
    return int(datetime(year, month, day, tzinfo=timezone.utc).timestamp() * 1000)


NOW_MAY = datetime(2026, 5, 1, tzinfo=timezone.utc)
NOW_SEP = datetime(2026, 9, 1, tzinfo=timezone.utc)


class DeepSeekParserTests(unittest.TestCase):
    def _page(self, posts):
        inner = '0:["$","$L1",null,{"posts":' + json.dumps(posts, ensure_ascii=False, separators=(",", ":")) + "}]"
        escaped = json.dumps(inner)[1:-1]
        return f'<html><script>self.__next_f.push([1,"{escaped}"])</script></html>'

    def test_parses_flight_payload_titles_dates_slugs(self):
        posts = [
            {"title": "DeepSeek-V4 预览版：迈入百万上下文普惠时代", "date": "2026-04-24",
             "description": "V4 预览版上线", "slug": "v4-preview", "locale": "zh"},
            {"title": "old one", "date": "2025-01-01", "description": "x", "slug": "old", "locale": "zh"},
        ]
        items = un.parse_deepseek_news_items(self._page(posts), NOW_MAY)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].source, "DeepSeek")
        self.assertEqual(items[0].title, "DeepSeek-V4 预览版：迈入百万上下文普惠时代")
        self.assertEqual(items[0].url, "https://deepseek.com/news/v4-preview/")
        self.assertEqual(items[0].published_at.date(), datetime(2026, 4, 24).date())

    def test_window_drops_stale_posts(self):
        posts = [{"title": "t", "date": "2026-04-24", "description": "x", "slug": "s", "locale": "zh"}]
        self.assertEqual(un.parse_deepseek_news_items(self._page(posts), NOW_SEP), [])


class MiniMaxParserTests(unittest.TestCase):
    def test_parses_api_news_payload(self):
        payload = json.dumps({
            "data": [
                {"title": "MiniMax H3 正式开源", "slug": "minimax-h3-open-source",
                 "publishDate": str(ts(2026, 8, 3)), "summary": "s"},
                {"title": "stale", "slug": "stale", "publishDate": ts(2025, 1, 1)},
                {"title": "no slug", "slug": "", "publishDate": ts(2026, 8, 1)},
            ]
        })
        items = un.parse_minimax_news_items(payload, NOW_SEP)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].source, "MiniMax")
        self.assertEqual(items[0].url, "https://www.minimaxi.com/news/minimax-h3-open-source")

    def test_invalid_json_returns_empty(self):
        self.assertEqual(un.parse_minimax_news_items("<html>not json", NOW_SEP), [])


class SeedParserTests(unittest.TestCase):
    PAGE = (
        '<html><script>var x = {"(locale$)/blog/page":{"article_list":[{"ArticleMeta":{'
        '"ID":1766,"ArticleID":%d,"ArticleType":2,"Author":"","Status":2,"PublishDate":%d,'
        '"ResearchArea":[]},"ArticleSubContentEn":{"Title":"EN title"},"ArticleSubContentZh":{"Title":"音视频全双工大模型发布"}},'
        '{"ArticleMeta":{"ID":1700,"ArticleID":%d,"ArticleType":2,"Author":"","Status":1,"PublishDate":%d},'
        '"ArticleSubContentZh":{"Title":"draft should be skipped"}}]}}</script></html>'
    )

    def test_parses_published_articles_preferring_zh_title(self):
        page = self.PAGE % (ts(2026, 8, 4), ts(2026, 8, 4), ts(2026, 8, 1), ts(2026, 8, 1))
        items = un.parse_seed_blog_items(page, NOW_SEP)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].title, "音视频全双工大模型发布")
        self.assertEqual(items[0].url, "https://seed.bytedance.com/zh/blog/1766")
        self.assertEqual(items[0].source, "ByteDance Seed")


class ZhipuParserTests(unittest.TestCase):
    RSC = (
        '1:["$","$L13",null,{"navConfig":[{"id":2,"article":{"id":161,'
        '"title_zh":"GLM-5.2\\u4e0a\\u7ebf\\u5e76\\u5f00\\u6e90","title_en":"GLM-5.2",'
        '"createAt":"2026-06-16T16:00:00.000Z","category":"blog"}},'
        '{"id":2,"article":{"id":161,"title_zh":"GLM-5.2\\u4e0a\\u7ebf\\u5e76\\u5f00\\u6e90",'
        '"title_en":"GLM-5.2","createAt":"2026-06-16T16:00:00.000Z"}},'
        '{"id":3,"article":{"id":100,"title_zh":"old","title_en":"old",'
        '"createAt":"2025-01-01T00:00:00.000Z"}}]}]'
    )

    def test_parses_rsc_articles_dedup_and_window(self):
        items = un.parse_zhipu_news_items(self.RSC, datetime(2026, 6, 20, tzinfo=timezone.utc))
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].title, "GLM-5.2上线并开源")
        self.assertEqual(items[0].url, "https://www.zhipuai.cn/news/161")
        self.assertEqual(items[0].source, "Zhipu AI")


class KimiParserTests(unittest.TestCase):
    CARD = (
        '<a href="/en/blog/{slug}" aria-label="{title}" class="absolute"></a>'
        '<div class="card-media"><img src="https://kimi-file.kimi.ai/x/2026-07-31/up?x=1"/></div>'
        '<div class="card-body"><h4 class="card-title">{title}</h4>'
        '<p class="card-date m-0">{date}</p></div>'
    )

    def test_parses_cards_with_aria_label_and_card_date(self):
        page = (
            "<html>"
            + self.CARD.format(slug="kimi-k3", title="Kimi K3", date="2026-07-16")
            + '<a href="/en/blog/nav-only" aria-label="Nav Only"></a>'  # no card-date -> skipped
            + self.CARD.format(slug="old-post", title="Old", date="2025-01-01")
            + self.CARD.format(slug="kimi-k3", title="Kimi K3", date="2026-07-16")  # dup
        )
        items = un.parse_kimi_blog_items(page, datetime(2026, 7, 20, tzinfo=timezone.utc))
        self.assertEqual([i.title for i in items], ["Kimi K3"])
        self.assertEqual(items[0].url, "https://www.kimi.com/en/blog/kimi-k3")
        self.assertEqual(items[0].source, "Moonshot AI (Kimi)")
        self.assertEqual(items[0].published_at.date(), datetime(2026, 7, 16).date())


class CnOfficialWiringTests(unittest.TestCase):
    def test_disabled_sources_skip_cn_fetchers(self):
        called = []

        def make_stub(name):
            def stub(session, now):
                called.append(name)
                return []

            return stub

        fetchers = tuple(
            (title, make_stub(title)) for title, _ in un.CN_OFFICIAL_PAGE_FETCHERS
        )

        class ExplodingSession:
            def get(self, url, **kwargs):
                raise ConnectionError("network disabled in test")

        with patch.dict(os.environ, {"DISABLED_SOURCES": "deepseek,minimax"}):
            with patch.object(un, "CN_OFFICIAL_PAGE_FETCHERS", fetchers):
                with patch.object(un, "OFFICIAL_AI_FEEDS", tuple()):
                    with self.assertRaises(ValueError):
                        un.fetch_official_ai_updates(ExplodingSession(), NOW_SEP)

        self.assertNotIn("DeepSeek", called)
        self.assertNotIn("MiniMax", called)
        self.assertIn("Zhipu AI", called)
        self.assertEqual(len(called), 3)


if __name__ == "__main__":
    unittest.main()
