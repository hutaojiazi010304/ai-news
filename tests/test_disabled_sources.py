import os
import tempfile
import unittest
from contextlib import ExitStack
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from scripts import update_news as un


NOW = datetime(2026, 8, 31, tzinfo=timezone.utc)

CURATED_XML = """<?xml version='1.0' encoding='UTF-8'?>
<rss><channel><title>量子位</title>
<item>
<title>「灵犀智涌」发布新一代具身智能基座</title>
<link>https://www.qbitai.com/2026/08/example-1.html</link>
<pubDate>Sun, 30 Aug 2026 02:05:04 +0000</pubDate>
</item>
</channel></rss>""".encode("utf-8")


class FakeResponse:
    def __init__(self, content: bytes):
        self.content = content

    def raise_for_status(self):
        return None


class FakeSession:
    def __init__(self, content: bytes = b""):
        self.calls = []
        self.content = content

    def get(self, url, **kwargs):
        self.calls.append(url)
        if self.content is None:
            raise ConnectionError("network disabled in test")
        return FakeResponse(self.content)


class DisabledSourcesEnvTests(unittest.TestCase):
    def test_parses_comma_separated_names_case_insensitively(self):
        with patch.dict(os.environ, {"DISABLED_SOURCES": " NewsNow ,aibreakfast,, Hugging Face Blog "}):
            self.assertEqual(
                un.disabled_source_names(),
                {"newsnow", "aibreakfast", "hugging face blog"},
            )

    def test_missing_env_means_nothing_disabled(self):
        env = {k: v for k, v in os.environ.items() if k != "DISABLED_SOURCES"}
        with patch.dict(os.environ, env, clear=True):
            self.assertEqual(un.disabled_source_names(), set())

    def test_is_disabled_source_matches_title_by_containment(self):
        disabled = {"google deepmind", "hugging face blog"}
        self.assertTrue(un.is_disabled_source(disabled, title="Google DeepMind"))
        self.assertTrue(un.is_disabled_source(disabled, title="Google DeepMind Blog"))
        self.assertTrue(un.is_disabled_source(disabled, title="Hugging Face Blog"))
        self.assertFalse(un.is_disabled_source(disabled, title="OpenAI News"))

    def test_is_disabled_source_matches_url_only_exactly(self):
        disabled = {"https://huggingface.co/blog/feed.xml"}
        self.assertTrue(
            un.is_disabled_source(disabled, url="https://huggingface.co/blog/feed.xml")
        )
        self.assertFalse(
            un.is_disabled_source(disabled, url="https://huggingface.co/blog/feed.xml/other")
        )


class DisabledSourcesCollectorTests(unittest.TestCase):
    FETCH_NAMES = [
        "fetch_official_ai_updates",
        "fetch_curated_ai_media",
        "fetch_ai_breakfast",
        "fetch_follow_builders",
        "fetch_techurls",
        "fetch_buzzing",
        "fetch_iris",
        "fetch_bestblogs",
        "fetch_zeli",
        "fetch_hacker_news_algolia",
        "fetch_ai_hubtoday",
        "fetch_aibase",
        "fetch_aihot",
        "fetch_newsnow",
    ]

    def test_collect_all_skips_disabled_tasks(self):
        called = []

        def make_stub(name):
            def stub(session, now):
                called.append(name)
                return []

            return stub

        with patch.dict(os.environ, {"DISABLED_SOURCES": "newsnow,aibreakfast"}):
            with ExitStack() as stack:
                for name in self.FETCH_NAMES:
                    stack.enter_context(patch.object(un, name, make_stub(name)))
                items, statuses = un.collect_all(FakeSession(None), NOW)

        self.assertEqual(items, [])
        self.assertNotIn("fetch_newsnow", called)
        self.assertNotIn("fetch_ai_breakfast", called)
        self.assertIn("fetch_aihot", called)

        by_site = {status["site_id"]: status for status in statuses}
        self.assertTrue(by_site["newsnow"]["skipped"])
        self.assertEqual(by_site["newsnow"]["skip_reason"], "disabled_by_env")
        self.assertTrue(by_site["newsnow"]["ok"])
        self.assertTrue(by_site["aibreakfast"]["skipped"])
        self.assertNotIn("skipped", by_site["aihot"])

    def test_fetch_curated_ai_media_skips_disabled_feeds(self):
        feeds = [
            {
                "title": "Claude Code Releases",
                "xml_url": "https://github.com/anthropics/claude-code/releases.atom",
                "max_entries": 6,
            },
            {
                "title": "量子位",
                "xml_url": "https://www.qbitai.com/feed",
                "max_entries": 10,
            },
        ]
        with patch.dict(os.environ, {"DISABLED_SOURCES": "claude code releases"}):
            with patch.object(un, "CURATED_AI_MEDIA_FEEDS", tuple(feeds)):
                session = FakeSession(CURATED_XML)
                items = un.fetch_curated_ai_media(session, NOW)

        self.assertEqual(session.calls, ["https://www.qbitai.com/feed"])
        self.assertEqual([item.source for item in items], ["量子位"])

    def test_fetch_official_ai_updates_skips_disabled_feeds(self):
        fetched_titles = []

        def recorder(session, feed, now):
            fetched_titles.append(str(feed.get("title")))
            return []

        class ExplodingSession:
            def get(self, url, **kwargs):
                raise ConnectionError("pages are not part of this test")

        with patch.dict(os.environ, {"DISABLED_SOURCES": "openai skills"}):
            with patch.object(un, "fetch_feed_as_official_items", recorder):
                with self.assertRaises(ValueError):
                    # All feeds return no items and both page fetches fail,
                    # so the guard raises; we only care which feeds ran.
                    un.fetch_official_ai_updates(ExplodingSession(), NOW)

        self.assertNotIn("OpenAI Skills", fetched_titles)
        self.assertGreater(len(fetched_titles), 0)

    def test_fetch_opml_rss_marks_disabled_feeds_skipped(self):
        opml = """<?xml version='1.0' encoding='UTF-8'?>
<opml><body>
<outline title="Microsoft AI Blog" xmlUrl="https://news.microsoft.com/source/topics/ai/feed/"/>
</body></opml>"""
        with tempfile.NamedTemporaryFile("w", suffix=".opml", delete=False, encoding="utf-8") as f:
            f.write(opml)
            path = Path(f.name)
        try:
            with patch.dict(os.environ, {"DISABLED_SOURCES": "microsoft ai blog"}):
                items, summary, feed_statuses = un.fetch_opml_rss(NOW, path)
        finally:
            path.unlink()

        self.assertEqual(items, [])
        self.assertEqual(len(feed_statuses), 1)
        self.assertTrue(feed_statuses[0]["skipped"])
        self.assertEqual(feed_statuses[0]["skip_reason"], "disabled_by_env")
        self.assertEqual(feed_statuses[0]["feed_title"], "Microsoft AI Blog")


if __name__ == "__main__":
    unittest.main()
