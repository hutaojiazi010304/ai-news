"""Tests for the DeepSeek-first title translation path: provider priority,
Google fallback, and the ds1| versioned cache-key compatibility strategy."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from scripts.update_news import (
    ZH_CACHE_DS_PREFIX,
    add_bilingual_fields,
    is_valid_zh_translation,
    load_translation_glossary,
    translate_to_zh_deepseek,
)


EN_TITLE = "OpenAI launches new Codex agent for developers"
DS_ZH = "OpenAI 为开发者推出全新 Codex 智能体"
GOOGLE_ZH = "OpenAI 为开发人员推出新的 Codex 代理"

DS_ENV = {"DEEPSEEK_API_KEY": "sk-test"}


def make_item(title: str = EN_TITLE) -> dict:
    return {"title": title, "url": "https://example.com/news/1"}


def deepseek_ok_response(content: str = DS_ZH) -> MagicMock:
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {"choices": [{"message": {"content": content}}]}
    return resp


def deepseek_error_response(status: int = 500) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status
    return resp


def google_session(translated: str = GOOGLE_ZH) -> MagicMock:
    session = MagicMock()
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = [[[translated, EN_TITLE]]]
    resp.raise_for_status.return_value = None
    session.get.return_value = resp
    return session


def run_enrich(session: MagicMock, cache: dict) -> tuple[list[dict], dict]:
    items_ai, _, cache_out = add_bilingual_fields(
        [make_item()], [], session, cache, max_new_translations=10
    )
    return items_ai, cache_out


class TestDeepSeekPriority(unittest.TestCase):
    def test_deepseek_success_skips_google(self):
        session = google_session()
        with patch.dict("os.environ", DS_ENV, clear=True), patch(
            "scripts.update_news.requests.post", return_value=deepseek_ok_response()
        ) as mock_post:
            items, cache = run_enrich(session, {})
        mock_post.assert_called_once()
        session.get.assert_not_called()
        self.assertEqual(items[0]["title_zh"], DS_ZH)

    def test_deepseek_failure_falls_back_to_google(self):
        session = google_session()
        with patch.dict("os.environ", DS_ENV, clear=True), patch(
            "scripts.update_news.requests.post", return_value=deepseek_error_response()
        ) as mock_post:
            items, cache = run_enrich(session, {})
        mock_post.assert_called_once()
        session.get.assert_called_once()
        self.assertEqual(items[0]["title_zh"], GOOGLE_ZH)
        # Google translations keep the bare cache key.
        self.assertEqual(cache.get(EN_TITLE), GOOGLE_ZH)
        self.assertNotIn(ZH_CACHE_DS_PREFIX + EN_TITLE, cache)

    def test_no_key_goes_straight_to_google(self):
        session = google_session()
        with patch.dict("os.environ", {}, clear=True), patch(
            "scripts.update_news.requests.post"
        ) as mock_post:
            items, cache = run_enrich(session, {})
        mock_post.assert_not_called()
        session.get.assert_called_once()
        self.assertEqual(items[0]["title_zh"], GOOGLE_ZH)


class TestTranslateToZhDeepseek(unittest.TestCase):
    def test_returns_none_without_key(self):
        with patch.dict("os.environ", {}, clear=True), patch(
            "scripts.update_news.requests.post"
        ) as mock_post:
            self.assertIsNone(translate_to_zh_deepseek(EN_TITLE))
        mock_post.assert_not_called()

    def test_returns_none_on_exception(self):
        with patch.dict("os.environ", DS_ENV, clear=True), patch(
            "scripts.update_news.requests.post", side_effect=Exception("boom")
        ):
            self.assertIsNone(translate_to_zh_deepseek(EN_TITLE))

    def test_returns_none_on_empty_choices(self):
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {"choices": []}
        with patch.dict("os.environ", DS_ENV, clear=True), patch(
            "scripts.update_news.requests.post", return_value=resp
        ):
            self.assertIsNone(translate_to_zh_deepseek(EN_TITLE))


class TestCacheKeyVersioning(unittest.TestCase):
    def test_deepseek_translation_cached_with_prefix(self):
        session = google_session()
        with patch.dict("os.environ", DS_ENV, clear=True), patch(
            "scripts.update_news.requests.post", return_value=deepseek_ok_response()
        ):
            _, cache = run_enrich(session, {})
        self.assertEqual(cache.get(ZH_CACHE_DS_PREFIX + EN_TITLE), DS_ZH)
        self.assertNotIn(EN_TITLE, cache)

    def test_prefixed_cache_hit_skips_all_network(self):
        session = google_session()
        cache = {ZH_CACHE_DS_PREFIX + EN_TITLE: DS_ZH}
        with patch.dict("os.environ", DS_ENV, clear=True), patch(
            "scripts.update_news.requests.post"
        ) as mock_post:
            items, _ = run_enrich(session, cache)
        mock_post.assert_not_called()
        session.get.assert_not_called()
        self.assertEqual(items[0]["title_zh"], DS_ZH)

    def test_bare_key_cache_is_miss_when_deepseek_key_present(self):
        session = google_session()
        cache = {EN_TITLE: GOOGLE_ZH}
        with patch.dict("os.environ", DS_ENV, clear=True), patch(
            "scripts.update_news.requests.post", return_value=deepseek_ok_response()
        ) as mock_post:
            items, cache_out = run_enrich(session, cache)
        mock_post.assert_called_once()
        self.assertEqual(items[0]["title_zh"], DS_ZH)
        # Old bare-key entry stays untouched; new entry lands under ds1| prefix.
        self.assertEqual(cache_out.get(EN_TITLE), GOOGLE_ZH)
        self.assertEqual(cache_out.get(ZH_CACHE_DS_PREFIX + EN_TITLE), DS_ZH)

    def test_bare_key_cache_hits_when_no_deepseek_key(self):
        session = google_session()
        cache = {EN_TITLE: GOOGLE_ZH}
        with patch.dict("os.environ", {}, clear=True), patch(
            "scripts.update_news.requests.post"
        ) as mock_post:
            items, _ = run_enrich(session, cache)
        mock_post.assert_not_called()
        session.get.assert_not_called()
        self.assertEqual(items[0]["title_zh"], GOOGLE_ZH)


class TestRepairTermTable(unittest.TestCase):
    def test_hugging_face_repair(self):
        from scripts.update_news import repair_zh_title_translation

        self.assertEqual(
            repair_zh_title_translation("Hugging Face releases new dataset", "拥抱脸发布新数据集"),
            "Hugging Face发布新数据集",
        )

    def test_transformer_not_repaired_for_movie_context(self):
        from scripts.update_news import repair_zh_title_translation

        self.assertEqual(
            repair_zh_title_translation("New Transformers movie trailer released", "新变形金刚电影预告发布"),
            "新变形金刚电影预告发布",
        )

    def test_openai_and_anthropic_repair(self):
        from scripts.update_news import repair_zh_title_translation

        self.assertEqual(
            repair_zh_title_translation("OpenAI and Anthropic partner up", "开放人工智能与人择合作"),
            "OpenAI与Anthropic合作",
        )


class TestGlossaryParsing(unittest.TestCase):
    def test_parses_terms_and_repairs_with_guard(self):
        content = (
            "# 注释行\n"
            "\n"
            "## 保护术语\n"
            "Claude\n"
            "Hugging Face\n"
            "\n"
            "## 修正规则\n"
            "法学硕士（LLM） => LLM\n"
            "克劳德 => Claude @Claude\n"
        )
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as f:
            f.write(content)
            path = f.name
        try:
            terms, repairs = load_translation_glossary(path)
        finally:
            Path(path).unlink()
        self.assertEqual(terms, ["Claude", "Hugging Face"])
        self.assertEqual(
            repairs,
            [("法学硕士（LLM）", "LLM", None), ("克劳德", "Claude", "Claude")],
        )

    def test_missing_file_returns_empty(self):
        terms, repairs = load_translation_glossary("/nonexistent/glossary-nope.txt")
        self.assertEqual(terms, [])
        self.assertEqual(repairs, [])

    def test_repo_glossary_covers_known_bad_cases(self):
        terms, repairs = load_translation_glossary()
        self.assertIn("Claude", terms)
        self.assertIn("LLM", terms)
        bads = [bad for bad, _, _ in repairs]
        self.assertIn("法学硕士（LLM）", bads)
        self.assertIn("克劳德", bads)
        self.assertIn("神鬼寓言", bads)


class TestGlossaryRepairCacheHit(unittest.TestCase):
    """三个线上实证坏翻译（谷歌时代缓存）：缓存命中后也要被词表 repair 修复。"""

    def run_cached(self, en_title: str, cached_zh: str) -> str:
        session = MagicMock()
        cache = {ZH_CACHE_DS_PREFIX + en_title: cached_zh}
        with patch.dict("os.environ", DS_ENV, clear=True), patch(
            "scripts.update_news.requests.post"
        ) as mock_post:
            items, _, _ = add_bilingual_fields(
                [{"title": en_title, "url": "https://example.com/news/g"}],
                [],
                session,
                cache,
                max_new_translations=10,
            )
        mock_post.assert_not_called()
        return items[0]["title_zh"]

    def test_llm_burnout_repaired(self):
        zh = self.run_cached("How to survive LLM burnout", "法学硕士（LLM）倦怠症生存指南")
        self.assertIn("LLM", zh)
        self.assertNotIn("法学硕士", zh)

    def test_claude_repaired(self):
        zh = self.run_cached("Claude ships a new feature", "克劳德推出新功能")
        self.assertIn("Claude", zh)
        self.assertNotIn("克劳德", zh)

    def test_fable_repaired_with_shuminghao_cleanup(self):
        zh = self.run_cached("Anthropic releases Fable 5", "Anthropic发布《神鬼寓言 5》")
        self.assertIn("Fable 5", zh)
        self.assertNotIn("神鬼寓言", zh)
        self.assertNotIn("《", zh)

    def test_guard_prevents_false_positive(self):
        # 原文不含 Claude，"克劳德"可能是真人名，不许替换。
        zh = self.run_cached(
            "Interview with director Chloe Zhao", "对话导演克劳德"
        )
        self.assertIn("克劳德", zh)

    def test_llm_bare_guard_prevents_false_positive(self):
        # 原文不含 LLM 时，"法学硕士"是真学位，不许替换。
        zh = self.run_cached(
            "Law school admissions are changing", "法学硕士招生正在变化"
        )
        self.assertIn("法学硕士", zh)


class TestDeepSeekPromptGlossary(unittest.TestCase):
    def test_system_prompt_contains_protected_terms(self):
        with patch.dict("os.environ", DS_ENV, clear=True), patch(
            "scripts.update_news.requests.post", return_value=deepseek_ok_response()
        ) as mock_post:
            translate_to_zh_deepseek(EN_TITLE)
        system_prompt = mock_post.call_args.kwargs["json"]["messages"][0]["content"]
        self.assertIn("Claude", system_prompt)
        self.assertIn("Hugging Face", system_prompt)


# A refusal sentence DeepSeek returns when the "title" it's asked to translate
# is really just a bare t.co link left over after compact_public_snippet
# truncation of a SocialData X tweet.
REFUSAL_ZH = "抱歉，我无法处理链接内容。请直接提供英文标题文本。"

# A degenerate single-word "translation" of a full headline.
DEGENERATE_ZH = "前体"


def failing_google_session() -> MagicMock:
    """A session whose .get() blows up, so translate_to_zh_cn() returns None
    (mirrors its except-Exception-return-None behavior) and the fallback
    chain bottoms out instead of masking the DeepSeek-side rejection."""
    session = MagicMock()
    session.get.side_effect = Exception("network down")
    return session


class TestIsValidZhTranslation(unittest.TestCase):
    def test_rejects_refusal_message(self):
        self.assertFalse(is_valid_zh_translation(EN_TITLE, REFUSAL_ZH))

    def test_rejects_refusal_message_case_insensitive_english_marker(self):
        self.assertFalse(is_valid_zh_translation(EN_TITLE, "Sorry, I cannot translate 这个链接"))

    def test_rejects_degenerate_single_word(self):
        self.assertFalse(is_valid_zh_translation(EN_TITLE, DEGENERATE_ZH))

    def test_rejects_empty_or_non_cjk(self):
        self.assertFalse(is_valid_zh_translation(EN_TITLE, ""))
        self.assertFalse(is_valid_zh_translation(EN_TITLE, "Precursor"))

    def test_accepts_normal_translation(self):
        self.assertTrue(is_valid_zh_translation(EN_TITLE, DS_ZH))
        self.assertTrue(is_valid_zh_translation(EN_TITLE, GOOGLE_ZH))

    def test_accepts_real_translation_that_contains_refusal_vocabulary(self):
        # Found in the live cache: real, correct translations that legitimately
        # contain "抱歉"/"我无法" as ordinary vocabulary (someone apologizing,
        # something being impossible) mid-sentence, not as a refusal opener.
        # A naive "marker anywhere in text" check false-flagged 22 good cached
        # entries; only a *leading* refusal phrase should be rejected.
        self.assertTrue(
            is_valid_zh_translation(
                "Sam Altman deeply sorry OpenAI didnt report mass shooting suspect",
                "Sam Altman 对 OpenAI 没有报告大规模枪击嫌疑人深感抱歉",
            )
        )
        self.assertTrue(
            is_valid_zh_translation(
                "I've been dating an AI companion for 3 years. I can't imagine life without him.",
                "我和一位人工智能伴侣已经约会三年了。我无法想象没有他的生活。",
            )
        )

    def test_accepts_translation_that_leads_with_i_cannot_as_real_content(self):
        # "I cannot stop doodling..." is the actual headline subject, not a
        # refusal — its translation legitimately starts with "我无法停止",
        # which must not be confused with a refusal opener like "我无法处理".
        self.assertTrue(
            is_valid_zh_translation(
                "I cannot stop doodling on the reMarkable Paper Pure, a new tablet",
                "我无法停止在 reMarkable Paper Pure 上涂鸦，这是一款新平板电脑",
            )
        )


class TestTranslationValidationGating(unittest.TestCase):
    """End-to-end: bad DeepSeek output must not leak into title_zh or the cache."""

    def test_refusal_response_is_not_cached_or_shown(self):
        session = failing_google_session()
        with patch.dict("os.environ", DS_ENV, clear=True), patch(
            "scripts.update_news.requests.post", return_value=deepseek_ok_response(REFUSAL_ZH)
        ) as mock_post:
            items, cache = run_enrich(session, {})
        mock_post.assert_called_once()
        session.get.assert_called_once()  # falls through to Google, which also fails
        self.assertIsNone(items[0]["title_zh"])
        self.assertNotIn(ZH_CACHE_DS_PREFIX + EN_TITLE, cache)
        self.assertNotIn(REFUSAL_ZH, cache.values())

    def test_degenerate_response_is_not_cached_or_shown(self):
        session = failing_google_session()
        with patch.dict("os.environ", DS_ENV, clear=True), patch(
            "scripts.update_news.requests.post", return_value=deepseek_ok_response(DEGENERATE_ZH)
        ) as mock_post:
            items, cache = run_enrich(session, {})
        mock_post.assert_called_once()
        self.assertIsNone(items[0]["title_zh"])
        self.assertNotIn(ZH_CACHE_DS_PREFIX + EN_TITLE, cache)
        self.assertNotIn(DEGENERATE_ZH, cache.values())

    def test_deepseek_refusal_falls_back_to_valid_google_translation(self):
        session = google_session()
        with patch.dict("os.environ", DS_ENV, clear=True), patch(
            "scripts.update_news.requests.post", return_value=deepseek_ok_response(REFUSAL_ZH)
        ) as mock_post:
            items, cache = run_enrich(session, {})
        mock_post.assert_called_once()
        session.get.assert_called_once()
        self.assertEqual(items[0]["title_zh"], GOOGLE_ZH)
        self.assertEqual(cache.get(EN_TITLE), GOOGLE_ZH)
        self.assertNotIn(ZH_CACHE_DS_PREFIX + EN_TITLE, cache)

    def test_normal_translation_still_passes_happy_path(self):
        session = google_session()
        with patch.dict("os.environ", DS_ENV, clear=True), patch(
            "scripts.update_news.requests.post", return_value=deepseek_ok_response()
        ) as mock_post:
            items, cache = run_enrich(session, {})
        mock_post.assert_called_once()
        session.get.assert_not_called()
        self.assertEqual(items[0]["title_zh"], DS_ZH)
        self.assertEqual(cache.get(ZH_CACHE_DS_PREFIX + EN_TITLE), DS_ZH)


class TestBroadPoolTranslationBudget(unittest.TestCase):
    """Part 2: latest_items_all gets its own small live-translation budget
    independent from items_ai's, instead of always being cache-only."""

    def test_broad_pool_stays_cache_only_when_budget_is_zero_by_default(self):
        session = google_session()
        with patch.dict("os.environ", DS_ENV, clear=True), patch(
            "scripts.update_news.requests.post"
        ) as mock_post:
            _, items_all, cache = add_bilingual_fields(
                [], [make_item()], session, {}, max_new_translations=0
            )
        mock_post.assert_not_called()
        session.get.assert_not_called()
        self.assertIsNone(items_all[0]["title_zh"])

    def test_broad_pool_live_translates_when_given_its_own_budget(self):
        session = google_session()
        with patch.dict("os.environ", DS_ENV, clear=True), patch(
            "scripts.update_news.requests.post", return_value=deepseek_ok_response()
        ) as mock_post:
            _, items_all, cache = add_bilingual_fields(
                [], [make_item()], session, {}, max_new_translations=0, max_new_translations_all=10
            )
        mock_post.assert_called_once()
        self.assertEqual(items_all[0]["title_zh"], DS_ZH)
        self.assertEqual(cache.get(ZH_CACHE_DS_PREFIX + EN_TITLE), DS_ZH)

    def test_items_ai_budget_is_independent_and_unaffected(self):
        # items_ai keeps getting its full budget regardless of the broad pool's.
        session = google_session()
        with patch.dict("os.environ", DS_ENV, clear=True), patch(
            "scripts.update_news.requests.post", return_value=deepseek_ok_response()
        ) as mock_post:
            items_ai, _, cache = add_bilingual_fields(
                [make_item()], [], session, {}, max_new_translations=10, max_new_translations_all=0
            )
        mock_post.assert_called_once()
        self.assertEqual(items_ai[0]["title_zh"], DS_ZH)


if __name__ == "__main__":
    unittest.main()
