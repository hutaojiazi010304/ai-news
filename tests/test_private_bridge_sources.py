from datetime import datetime, timezone
import json
import unittest

from scripts.update_news import (
    parse_jike_public_items,
    parse_telegram_public_items,
    resolve_opml_bridge_source,
)


class PrivateBridgeSourceTests(unittest.TestCase):
    def test_resolves_rsshub_telegram_to_public_preview(self):
        bridge = resolve_opml_bridge_source("https://rsshub.app/telegram/channel/AI_News_CN")
        self.assertEqual(bridge["bridge_type"], "telegram")
        self.assertEqual(bridge["bridge_slug"], "AI_News_CN")
        self.assertEqual(bridge["url"], "https://t.me/s/AI_News_CN")

    def test_resolves_rsshub_jike_topic_to_mobile_page(self):
        bridge = resolve_opml_bridge_source("https://rsshub.app/jike/topic/63579abb6724cc583b9bba9a")
        self.assertEqual(bridge["bridge_type"], "jike")
        self.assertEqual(bridge["bridge_kind"], "topic")
        self.assertEqual(bridge["url"], "https://m.okjike.com/topics/63579abb6724cc583b9bba9a")

    def test_parse_telegram_public_items(self):
        html = """
        <div class="tgme_widget_message" data-post="AI_News_CN/123">
          <div class="tgme_widget_message_text">Claude Code 发布了新的 Agent 能力</div>
          <time datetime="2026-05-12T01:02:03+00:00"></time>
        </div>
        """
        items = parse_telegram_public_items(
            html,
            now=datetime(2026, 5, 12, tzinfo=timezone.utc),
            source_name="ChatGPT / AI新闻聚合",
            slug="AI_News_CN",
        )
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].url, "https://t.me/AI_News_CN/123")
        self.assertEqual(items[0].meta["bridge_type"], "telegram")

    def test_parse_jike_public_items(self):
        payload = {
            "props": {
                "pageProps": {
                    "posts": [
                        {
                            "id": "post123",
                            "content": "Andrej Karpathy 讨论了 Agentic Engineering 与 Vibe Coding",
                            "createdAt": "2026-05-01T03:12:09.999Z",
                        }
                    ]
                }
            }
        }
        html = f'<script id="__NEXT_DATA__" type="application/json">{json.dumps(payload)}</script>'
        items = parse_jike_public_items(
            html,
            now=datetime(2026, 5, 12, tzinfo=timezone.utc),
            source_name="AI探索站 - 即刻圈子",
            source_url="https://m.okjike.com/topics/63579abb6724cc583b9bba9a",
        )
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].url, "https://m.okjike.com/originalPosts/post123")
        self.assertEqual(items[0].meta["bridge_type"], "jike")


if __name__ == "__main__":
    unittest.main()
