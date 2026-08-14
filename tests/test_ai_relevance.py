import unittest

from scripts.ai_relevance import (
    AI_BROAD_RELEVANCE_FLOOR,
    AI_RELEVANCE_THRESHOLD,
    add_ai_relevance_fields,
    is_ai_related_record,
    score_ai_relevance,
)


class AiRelevanceScoringTests(unittest.TestCase):
    def test_scores_strong_ai_signal_with_reason(self):
        rec = {
            "site_id": "techurls",
            "site_name": "TechURLs",
            "source": "Hacker News",
            "title": "OpenAI releases new GPT model",
            "url": "https://example.com/ai",
        }
        result = score_ai_relevance(rec)
        self.assertTrue(result["is_ai_related"])
        self.assertGreaterEqual(result["score"], 0.65)
        self.assertEqual(result["label"], "model_release")
        self.assertIn("openai", result["signals"])
        self.assertIn("matched_ai_signal", result["reason"])

    def test_rejects_broad_model_without_tech_context(self):
        rec = {
            "site_id": "buzzing",
            "site_name": "Buzzing",
            "source": "general",
            "title": "这个商业模型终于跑通了",
            "url": "https://example.com/model",
        }
        result = score_ai_relevance(rec)
        self.assertFalse(result["is_ai_related"])
        self.assertLess(result["score"], 0.65)
        self.assertEqual(result["reason"], "missing_meaningful_ai_signal")

    def test_accepts_broad_ai_plus_tech_context(self):
        rec = {
            "site_id": "techurls",
            "site_name": "TechURLs",
            "source": "GitHub",
            "title": "开源推理框架支持更多GPU后端",
            "url": "https://example.com/inference-gpu",
        }
        result = score_ai_relevance(rec)
        self.assertTrue(result["is_ai_related"])
        self.assertGreaterEqual(result["score"], 0.65)
        self.assertEqual(result["reason"], "matched_broad_ai_plus_tech_signal")
        self.assertIn("gpu", result["signals"])

    def test_accepts_agent_context_as_developer_tool(self):
        rec = {
            "site_id": "opmlrss",
            "site_name": "OPML RSS",
            "source": "BestBlogs.dev",
            "title": "分层记忆：Agent 中的上下文管理",
            "url": "https://example.com/agent-context",
        }
        result = score_ai_relevance(rec)
        self.assertTrue(result["is_ai_related"])
        self.assertGreaterEqual(result["score"], 0.65)
        self.assertEqual(result["label"], "agent_workflow")

    def test_trusted_ai_source_defaults_to_keep(self):
        rec = {
            "site_id": "aihot",
            "site_name": "AI HOT",
            "source": "AI HOT",
            "title": "今日值得关注的产品更新",
            "url": "https://aihot.virxact.com/post/1",
        }
        result = score_ai_relevance(rec)
        self.assertTrue(result["is_ai_related"])
        self.assertGreaterEqual(result["score"], 0.65)
        self.assertEqual(result["reason"], "trusted_ai_source_default_keep")

    def test_rejects_explicit_adult_promotion_even_with_ai_keyword(self):
        rec = {
            "site_id": "socialdata_x",
            "site_name": "SocialData X",
            "source": "@spam_account",
            "title": "AI virtual girlfriends with uncensored pictures and explicit promotion",
            "url": "https://x.com/spam_account/status/1",
        }
        result = score_ai_relevance(rec)
        self.assertFalse(result["is_ai_related"])
        self.assertEqual(result["reason"], "unsafe_promotional_content")

    def test_keeps_neutral_safety_news_with_single_adult_term(self):
        rec = {
            "site_id": "techurls",
            "site_name": "TechURLs",
            "source": "AI policy",
            "title": "OpenAI publishes a safety policy for detecting AI-generated pornography",
            "url": "https://example.com/ai-safety-policy",
        }
        result = score_ai_relevance(rec)
        self.assertTrue(result["is_ai_related"])

    def test_curated_media_keeps_trusted_ai_feed(self):
        rec = {
            "site_id": "curated_media",
            "site_name": "Curated Media",
            "source": "TechCrunch AI",
            "title": "Startup raises funding for enterprise workflow automation",
            "url": "https://techcrunch.com/example",
        }
        result = score_ai_relevance(rec)
        self.assertTrue(result["is_ai_related"])
        self.assertEqual(result["reason"], "curated_media_source_filter")
        self.assertEqual(result["label"], "industry_business")

    def test_curated_general_feed_requires_title_signal(self):
        rec = {
            "site_id": "curated_media",
            "site_name": "Curated Media",
            "source": "The Verge",
            "title": "A new phone accessory launches this week",
            "url": "https://www.theverge.com/example",
        }
        result = score_ai_relevance(rec)
        self.assertFalse(result["is_ai_related"])
        self.assertEqual(result["reason"], "curated_media_requires_ai_title_or_trusted_ai_feed")

    def test_curated_research_feed_is_research_labeled_and_capped(self):
        rec = {
            "site_id": "curated_media",
            "site_name": "Curated Media",
            "source": "MarkTechPost Research",
            "title": "A new benchmark evaluates multimodal LLM reasoning",
            "url": "https://www.marktechpost.com/example",
        }
        result = score_ai_relevance(rec)
        self.assertTrue(result["is_ai_related"])
        self.assertEqual(result["label"], "research_paper")
        self.assertLessEqual(result["score"], 0.76)

    def test_zeli_non_ai_title_is_not_forced_through_allowlist(self):
        # fetch_zeli() hardcodes source="Hacker News · 24h最热" for every scraped
        # item regardless of topic, so zeli must not get a special-cased branch
        # that force-passes every item on that fixed source string alone.
        rec = {
            "site_id": "zeli",
            "site_name": "Zeli",
            "source": "Hacker News · 24h最热",
            "title": "LAPD contract with Flock expires",
            "url": "https://zeli.app/hacker-news/1",
        }
        result = score_ai_relevance(rec)
        self.assertFalse(result["is_ai_related"])
        self.assertLess(result["score"], AI_RELEVANCE_THRESHOLD)
        self.assertLess(result["score"], AI_BROAD_RELEVANCE_FLOOR)
        self.assertNotEqual(result["reason"], "zeli_24h_hot_allowlist")

    def test_zeli_ai_relevant_title_still_passes(self):
        rec = {
            "site_id": "zeli",
            "site_name": "Zeli",
            "source": "Hacker News · 24h最热",
            "title": "Anthropic's Claude uploaded my home directory to a remote server",
            "url": "https://zeli.app/hacker-news/2",
        }
        result = score_ai_relevance(rec)
        self.assertTrue(result["is_ai_related"])
        self.assertGreaterEqual(result["score"], AI_RELEVANCE_THRESHOLD)

    def test_zeli_grok_title_is_recognized_as_ai_relevant(self):
        # Real-world zeli item found post-fix: "Grok uploaded my user directory
        # to xAI's servers" scored 0.0 before grok/xai were added as AI signal
        # keywords, silently dropping genuinely AI-relevant content the same
        # way the removed allowlist bug over-included irrelevant content.
        rec = {
            "site_id": "zeli",
            "site_name": "Zeli",
            "source": "Hacker News · 24h最热",
            "title": "Grok uploaded my user directory to xAI's servers",
            "url": "https://zeli.app/hacker-news/3",
        }
        result = score_ai_relevance(rec)
        self.assertTrue(result["is_ai_related"])
        self.assertGreaterEqual(result["score"], AI_RELEVANCE_THRESHOLD)

    def test_precursor_is_not_falsely_matched_as_cursor_signal(self):
        # Real-world zeli/techurls/newsnow item found post-fix: Cloudflare's
        # "Precursor" product announcement scored 0.65/AI-related because the
        # old substring-based AI_KEYWORDS list matched "cursor" inside
        # "precursor". "cursor" now requires word boundaries.
        rec = {
            "site_id": "zeli",
            "site_name": "Zeli",
            "source": "Hacker News · 24h最热",
            "title": "Precursor",
            "url": "https://blog.cloudflare.com/introducing-precursor",
        }
        result = score_ai_relevance(rec)
        self.assertFalse(result["is_ai_related"])
        self.assertLess(result["score"], AI_RELEVANCE_THRESHOLD)

    def test_real_cursor_mention_still_matches(self):
        rec = {
            "site_id": "zeli",
            "site_name": "Zeli",
            "source": "Hacker News · 24h最热",
            "title": "Cursor adds new agent mode for large codebases",
            "url": "https://zeli.app/hacker-news/4",
        }
        result = score_ai_relevance(rec)
        self.assertTrue(result["is_ai_related"])
        self.assertIn("cursor", result["signals"])

    def test_adds_public_debug_fields(self):
        rec = {
            "site_id": "official_ai",
            "site_name": "Official AI Updates",
            "source": "GitHub Changelog",
            "title": "GitHub Copilot adds a coding agent",
            "url": "https://example.com/copilot-agent",
        }
        out = add_ai_relevance_fields(rec)
        self.assertTrue(out["ai_is_related"])
        self.assertIn("ai_score", out)
        self.assertIn("ai_label", out)
        self.assertIn("ai_relevance_reason", out)
        self.assertIn("ai_signals", out)
        self.assertTrue(is_ai_related_record(rec))


if __name__ == "__main__":
    unittest.main()
