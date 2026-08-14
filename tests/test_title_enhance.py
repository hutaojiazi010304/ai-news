"""Tests for the LLM title-enhancement step: the enhance-gate, the
micro-crawl + DeepSeek rewrite validation, cache-key stability, and the
DEEPSEEK_API_KEY no-op wiring path. All network/LLM calls are mocked."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from scripts.update_news import (
    TITLE_ENHANCE_CACHE_PREFIX,
    add_title_enhancements,
    enhance_title_deepseek,
    fetch_title_context,
    title_needs_enhance,
)


DS_ENV = {"DEEPSEEK_API_KEY": "sk-test"}


def deepseek_ok_response(content: str) -> MagicMock:
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {"choices": [{"message": {"content": content}}]}
    return resp


class TestTitleNeedsEnhanceGate(unittest.TestCase):
    def test_short_english_title_is_gated(self):
        item = {
            "site_id": "producthunt",
            "source_tier": "discussion",
            "title_en": "AI Visibility",
            "title_original": "AI Visibility",
        }
        self.assertTrue(title_needs_enhance(item))

    def test_normal_length_official_title_is_not_gated(self):
        item = {
            "site_id": "official_ai",
            "source_tier": "official",
            "title_en": "OpenAI launches new Codex agent for developers this week",
            "title_original": "OpenAI launches new Codex agent for developers this week",
        }
        self.assertFalse(title_needs_enhance(item))

    def test_year_suffixed_aggregate_title_is_gated(self):
        item = {
            "site_id": "techurls",
            "source_tier": "aggregate",
            "title_en": "Major New AI Regulation Framework Overview And Policy Analysis (2024)",
            "title_original": "Major New AI Regulation Framework Overview And Policy Analysis (2024)",
        }
        self.assertTrue(title_needs_enhance(item))

    def test_short_effective_title_on_gated_tier_is_gated(self):
        item = {
            "site_id": "hackernews",
            "source_tier": "discussion",
            "title_en": "New chip breakthrough announced today",
            "title_original": "New chip breakthrough announced today",
            "title_zh": "新芯片突破",
        }
        self.assertTrue(title_needs_enhance(item))

    def test_curated_tier_never_gated_even_if_short(self):
        item = {
            "site_id": "curated_media",
            "source_tier": "curated",
            "title_en": "AI Visibility",
            "title_original": "AI Visibility",
        }
        self.assertFalse(title_needs_enhance(item))

    def test_long_chinese_aibase_title_on_aggregate_tier_is_not_gated(self):
        # Regression: rule 1 (English word-count <= 4) was counting
        # space-separated tokens on CJK titles too, so a long, already
        # self-explanatory Chinese title (1-2 "words" with no spaces)
        # false-positived as needing enhancement.
        zh_title = "Meta携手博通与台积电，自研AI芯片Iris将于9月正式量产"
        item = {
            "site_id": "aibase",
            "source_tier": "aggregate",
            "title": zh_title,
            "title_original": zh_title,
            "title_zh": zh_title,
            "title_en": None,
        }
        self.assertFalse(title_needs_enhance(item))

    def test_fullwidth_year_suffixed_cjk_title_on_discussion_tier_is_gated(self):
        # Regression for buzzing.cc-style upstream-pre-translated cryptic
        # titles: a long CJK title (>=12 chars) ending in a full-width
        # parenthesized year used to hit the CJK-length exemption before the
        # year-suffix rule ever ran, so it never got enhanced.
        zh_title = '"反向半人马"是解决人工智能悖论的答案（2025）'
        item = {
            "site_id": "buzzing",
            "source_tier": "discussion",
            "title": zh_title,
            "title_original": zh_title,
            "title_zh": zh_title,
            "title_en": None,
        }
        self.assertTrue(title_needs_enhance(item))

    def test_fullwidth_year_suffixed_cjk_title_on_official_tier_is_not_gated(self):
        zh_title = '"反向半人马"是解决人工智能悖论的答案（2025）'
        item = {
            "site_id": "official_media",
            "source_tier": "official",
            "title": zh_title,
            "title_original": zh_title,
            "title_zh": zh_title,
            "title_en": None,
        }
        self.assertFalse(title_needs_enhance(item))

    def test_short_chinese_title_under_12_chars_can_still_gate(self):
        # Sanity check for the new CJK floor: it only exempts titles that are
        # already long enough to be self-explanatory (>=12 chars), not short
        # cryptic Chinese titles on gated tiers.
        item = {
            "site_id": "hackernews",
            "source_tier": "discussion",
            "title": "新芯片发布",
            "title_original": "新芯片发布",
            "title_zh": "新芯片发布",
            "title_en": None,
        }
        self.assertTrue(title_needs_enhance(item))


class TestFetchTitleContextJinaFallback(unittest.TestCase):
    """ProductHunt-style Cloudflare 403s should fall back to r.jina.ai."""

    def make_blocked_response(self):
        resp = MagicMock()
        resp.raise_for_status.side_effect = Exception("403 Client Error: Forbidden")
        return resp

    def make_jina_response(self, markdown: str):
        resp = MagicMock()
        resp.raise_for_status.return_value = None
        resp.iter_content.return_value = iter([markdown.encode("utf-8")])
        return resp

    def test_direct_403_falls_back_to_jina_success(self):
        blocked = self.make_blocked_response()
        jina_markdown = (
            "Title: AI Visibility - Track your brand across AI answer engines\n"
            "\n"
            "URL Source: https://producthunt.com/posts/ai-visibility\n"
            "\n"
            "Markdown Content:\n"
            "* [Best of the day](https://www.producthunt.com/best/day)\n"
            "* [Best of the week](https://www.producthunt.com/best/week)\n"
            "AI Visibility monitors how your brand is mentioned by ChatGPT, Grok and other assistants.\n"
            "It scans millions of AI-generated answers daily for brand mentions.\n"
        )
        jina_resp = self.make_jina_response(jina_markdown)
        session = MagicMock()
        session.get.side_effect = [blocked, jina_resp]

        context = fetch_title_context(session, "https://producthunt.com/posts/ai-visibility")

        self.assertEqual(session.get.call_count, 2)
        second_call_url = session.get.call_args_list[1].args[0]
        self.assertEqual(second_call_url, "https://r.jina.ai/https://producthunt.com/posts/ai-visibility")
        self.assertIn("Title: AI Visibility", context)
        # Real prose survives the nav-link filter...
        self.assertIn("AI Visibility monitors how your brand is mentioned", context)
        # ...but link-spaghetti nav rows do not pollute the LLM prompt.
        self.assertNotIn("Best of the day", context)
        self.assertNotIn("producthunt.com/best", context)
        self.assertLessEqual(len(context), 800)

    def test_jina_nav_only_page_falls_back_to_title_line_only(self):
        blocked = self.make_blocked_response()
        jina_markdown = (
            "Title: AI Visibility - Track your brand across AI answer engines\n"
            "\n"
            "URL Source: https://producthunt.com/posts/ai-visibility\n"
            "\n"
            "Markdown Content:\n"
            "* [Best of the day](https://www.producthunt.com/best/day)\n"
            "* [Best of the week](https://www.producthunt.com/best/week)\n"
            "* [Categories](https://www.producthunt.com/categories)\n"
        )
        jina_resp = self.make_jina_response(jina_markdown)
        session = MagicMock()
        session.get.side_effect = [blocked, jina_resp]

        context = fetch_title_context(session, "https://producthunt.com/posts/ai-visibility")

        self.assertEqual(
            context, "Title: AI Visibility - Track your brand across AI answer engines"
        )
        self.assertNotIn("Best of the day", context)

    def test_direct_403_and_jina_failure_returns_empty(self):
        blocked = self.make_blocked_response()
        jina_blocked = MagicMock()
        jina_blocked.raise_for_status.side_effect = Exception("timeout")
        session = MagicMock()
        session.get.side_effect = [blocked, jina_blocked]

        context = fetch_title_context(session, "https://producthunt.com/posts/ai-visibility")

        self.assertEqual(context, "")
        self.assertEqual(session.get.call_count, 2)

    def test_direct_success_does_not_call_jina(self):
        ok_resp = MagicMock()
        ok_resp.raise_for_status.return_value = None
        html = b'<html><head><meta name="description" content="A perfectly good direct description."></head><body></body></html>'
        ok_resp.iter_content.return_value = iter([html])
        session = MagicMock()
        session.get.side_effect = [ok_resp]

        context = fetch_title_context(session, "https://example.com/a")

        self.assertEqual(session.get.call_count, 1)
        self.assertIn("A perfectly good direct description.", context)


class TestEnhanceTitleDeepseekValidation(unittest.TestCase):
    def setUp(self):
        self.title = "AI Visibility raises Grok concerns"
        self.context = "AI Visibility is a new product from Grok that monitors brand mentions."

    def test_fabricated_entity_title_rejected(self):
        with patch.dict("os.environ", DS_ENV, clear=True), patch(
            "scripts.update_news.requests.post",
            return_value=deepseek_ok_response("行业迎来新一轮技术变革与市场调整"),
        ):
            result = enhance_title_deepseek(self.title, self.context)
        self.assertIsNone(result)

    def test_too_short_result_rejected(self):
        with patch.dict("os.environ", DS_ENV, clear=True), patch(
            "scripts.update_news.requests.post",
            return_value=deepseek_ok_response("Grok来了"),
        ):
            result = enhance_title_deepseek(self.title, self.context)
        self.assertIsNone(result)

    def test_good_rewrite_accepted(self):
        good = "AI Visibility新品引发对Grok数据来源的担忧"
        with patch.dict("os.environ", DS_ENV, clear=True), patch(
            "scripts.update_news.requests.post",
            return_value=deepseek_ok_response(good),
        ):
            result = enhance_title_deepseek(self.title, self.context)
        self.assertEqual(result, good)

    def test_no_key_returns_none_without_network(self):
        with patch.dict("os.environ", {}, clear=True), patch(
            "scripts.update_news.requests.post"
        ) as mock_post:
            result = enhance_title_deepseek(self.title, self.context)
        self.assertIsNone(result)
        mock_post.assert_not_called()

    def test_long_raw_length_but_valid_effective_length_accepted(self):
        # Regression: raw len=51 (>40) previously rejected this excellent
        # rewrite, because English entity names (Claude, ChatGPT, Cursor)
        # count 1:1 with CJK chars under a raw-length cap even though they
        # read as a single "word" each editorially.
        good = "Second Brain for AI v2提供Claude、ChatGPT、Cursor免费持久记忆"
        self.assertGreater(len(good), 40)
        with patch.dict("os.environ", DS_ENV, clear=True), patch(
            "scripts.update_news.requests.post",
            return_value=deepseek_ok_response(good),
        ):
            result = enhance_title_deepseek("Second Brain for AI v2", self.context)
        self.assertEqual(result, good)

    def test_long_cjk_run_on_rejected_by_effective_length(self):
        run_on = "测" * 60
        with patch.dict("os.environ", DS_ENV, clear=True), patch(
            "scripts.update_news.requests.post",
            return_value=deepseek_ok_response(run_on),
        ):
            result = enhance_title_deepseek("新款设备", self.context)
        self.assertIsNone(result)


class TestAddTitleEnhancementsWiring(unittest.TestCase):
    def make_item(self, url="https://example.com/a", title_en="AI Visibility"):
        return {
            "id": "item-1",
            "site_id": "producthunt",
            "source_tier": "discussion",
            "url": url,
            "title": title_en,
            "title_en": title_en,
            "title_original": title_en,
        }

    def test_no_key_returns_items_unchanged_and_no_network(self):
        item = self.make_item()
        session = MagicMock()
        cache: dict[str, str] = {}
        with patch.dict("os.environ", {}, clear=True), patch(
            "scripts.update_news.fetch_title_context"
        ) as mock_fetch, patch(
            "scripts.update_news.enhance_title_deepseek"
        ) as mock_enhance:
            out_items, out_cache = add_title_enhancements([item], session, cache)
        self.assertEqual(out_items, [item])
        self.assertEqual(out_cache, {})
        mock_fetch.assert_not_called()
        mock_enhance.assert_not_called()
        session.get.assert_not_called()

    def test_cache_key_is_stable_across_runs_and_skips_second_llm_call(self):
        item = self.make_item()
        session = MagicMock()
        cache: dict[str, str] = {}
        enhanced_title = "AI Visibility发布品牌监测新品"
        with patch.dict("os.environ", DS_ENV, clear=True), patch(
            "scripts.update_news.fetch_title_context", return_value="some page context"
        ) as mock_fetch, patch(
            "scripts.update_news.enhance_title_deepseek", return_value=enhanced_title
        ) as mock_enhance:
            first_items, cache = add_title_enhancements([item], session, cache)

        self.assertEqual(len(cache), 1)
        self.assertTrue(next(iter(cache)).startswith(TITLE_ENHANCE_CACHE_PREFIX))
        self.assertEqual(first_items[0]["title_enhanced_zh"], enhanced_title)
        mock_fetch.assert_called_once()
        mock_enhance.assert_called_once()

        # Second run with the same url+title: same cache key hit, no new LLM/crawl calls.
        second_item = self.make_item()
        with patch.dict("os.environ", DS_ENV, clear=True), patch(
            "scripts.update_news.fetch_title_context"
        ) as mock_fetch2, patch(
            "scripts.update_news.enhance_title_deepseek"
        ) as mock_enhance2:
            second_items, cache = add_title_enhancements([second_item], session, cache)

        self.assertEqual(len(cache), 1)
        self.assertEqual(second_items[0]["title_enhanced_zh"], enhanced_title)
        mock_fetch2.assert_not_called()
        mock_enhance2.assert_not_called()

    def test_negative_cache_on_empty_context_skips_llm_and_is_not_retried(self):
        item = self.make_item()
        session = MagicMock()
        cache: dict[str, str] = {}
        with patch.dict("os.environ", DS_ENV, clear=True), patch(
            "scripts.update_news.fetch_title_context", return_value=""
        ) as mock_fetch, patch(
            "scripts.update_news.enhance_title_deepseek"
        ) as mock_enhance:
            out_items, cache = add_title_enhancements([item], session, cache)

        self.assertNotIn("title_enhanced_zh", out_items[0])
        self.assertEqual(len(cache), 1)
        mock_fetch.assert_called_once()
        mock_enhance.assert_not_called()

        # Re-run: negative cache hit means no repeat crawl.
        second_item = self.make_item()
        with patch.dict("os.environ", DS_ENV, clear=True), patch(
            "scripts.update_news.fetch_title_context"
        ) as mock_fetch2:
            out_items2, cache = add_title_enhancements([second_item], session, cache)
        self.assertNotIn("title_enhanced_zh", out_items2[0])
        mock_fetch2.assert_not_called()

    def test_ungated_item_is_left_untouched(self):
        item = self.make_item(title_en="OpenAI launches new Codex agent for developers this week")
        item["source_tier"] = "official"
        item["site_id"] = "official_ai"
        session = MagicMock()
        cache: dict[str, str] = {}
        with patch.dict("os.environ", DS_ENV, clear=True), patch(
            "scripts.update_news.fetch_title_context"
        ) as mock_fetch, patch(
            "scripts.update_news.enhance_title_deepseek"
        ) as mock_enhance:
            out_items, cache = add_title_enhancements([item], session, cache)
        self.assertNotIn("title_enhanced_zh", out_items[0])
        self.assertEqual(cache, {})
        mock_fetch.assert_not_called()
        mock_enhance.assert_not_called()

    def test_per_run_cap_blocks_further_crawls(self):
        items = [self.make_item(url=f"https://example.com/{i}") for i in range(3)]
        session = MagicMock()
        cache: dict[str, str] = {}
        with patch.dict("os.environ", DS_ENV, clear=True), patch(
            "scripts.update_news.fetch_title_context", return_value="ctx"
        ) as mock_fetch, patch(
            "scripts.update_news.enhance_title_deepseek", return_value="标题增强结果占位文本"
        ):
            add_title_enhancements(items, session, cache, max_new_per_run=1)
        self.assertEqual(mock_fetch.call_count, 1)
        self.assertEqual(len(cache), 1)


if __name__ == "__main__":
    unittest.main()
