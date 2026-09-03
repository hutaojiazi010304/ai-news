#!/usr/bin/env python3
"""Explainable AI relevance scoring for news records."""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse

AI_KEYWORDS = [
    "a.i.",
    "agent view",
    "agent skills",
    "for agents",
    "parallel agent",
    "并行 agent",
    "known agents",
    "hermes-agent",
    "agentmemory",
    "aigc",
    "llm",
    "gpt",
    "claude",
    "gemini",
    "deepseek",
    "openai",
    "anthropic",
    "grok",
    "copilot",
    "codex",
    "mcp",
    "hugging face",
    "huggingface",
    "transformer",
    "prompt",
    "diffusion",
    "多模态",
    "交互模型",
    "变换器",
    "语言模型",
    "视觉语言模型",
    "基础模型",
    "本地模型",
    "具身智能",
    "大模型",
    "人工智能",
    "机器学习",
    "深度学习",
    "智能体",
    "算力",
    "推理",
    "微调",
]

TECH_KEYWORDS = [
    "robot",
    "robotics",
    "embodied",
    "autonomous",
    "vision",
    "chip",
    "semiconductor",
    "cuda",
    "npu",
    "gpu",
    "cloud",
    "developer",
    "benchmark",
    "dataset",
    "eval",
    "evaluation",
    "sandbox",
    "context",
    "开源",
    "技术",
    "编程",
    "软件",
    "沙箱",
    "上下文",
    "芯片",
    "机器人",
    "具身",
]

NOISE_KEYWORDS = [
    "娱乐",
    "明星",
    "八卦",
    "足球",
    "篮球",
    "彩票",
    "情感",
    "旅游",
    "美食",
]

COMMERCE_NOISE_KEYWORDS = [
    "淘宝",
    "天猫",
    "京东",
    "拼多多",
    "券后",
    "热销总榜",
    "促销",
    "优惠",
    "补贴",
    "下单",
    "首发价",
]

UNSAFE_HARD_PATTERNS = [
    re.compile(r"\bcreampie\b", re.I),
    re.compile(r"\bblowjob\b", re.I),
    re.compile(r"\bsuck (?:your|my) (?:dick|cock)\b", re.I),
    re.compile(r"中出|婊子|吸你的鸡鸡|操虚拟女友", re.I),
]

UNSAFE_PROMO_PATTERNS = [
    re.compile(r"\b(?:nsfw|nudes?|porn(?:ography)?)\b", re.I),
    re.compile(r"\buncensored pictures?\b", re.I),
    re.compile(r"\bvirtual girlfriends?\b", re.I),
    re.compile(r"\bknock her up\b", re.I),
    re.compile(r"未经审查的图片|虚拟女友|色情内容|成人内容", re.I),
]

EN_SIGNAL_RE = re.compile(
    r"(?i)(?<![a-z0-9])(ai|aigc|llm|gpt|openai|anthropic|deepseek|gemini|claude|grok|xai|robot|robotics|embodied|autonomous|machine learning|artificial intelligence|transformer|diffusion|agent)(?![a-z0-9])"
)
MEANINGFUL_EN_SIGNAL_RE = re.compile(
    r"(?i)(?<![a-z0-9])(ai|aigc|llm|gpt|openai|anthropic|deepseek|gemini|claude|grok|xai|robot|robotics|embodied|autonomous|machine learning|artificial intelligence|transformer|diffusion)(?![a-z0-9])"
)
# "cursor" needs its own word-boundary regex rather than living in the plain
# substring-matched AI_KEYWORDS list: "cursor" is a substring of ordinary
# words like "precursor" (e.g. Cloudflare's "Precursor" product announcement),
# which was scoring 0.65/AI-related purely off that false substring match.
CURSOR_SIGNAL_RE = re.compile(r"(?i)(?<![a-z0-9])cursor(?![a-z0-9])")
BROAD_AI_TERMS = {"agent", "模型", "推理"}
AI_RELEVANCE_THRESHOLD = 0.65
AI_BROAD_RELEVANCE_FLOOR = 0.3

SOURCE_PRIORS = {
    "official_ai": 0.35,
    "curated_media": 0.18,
    "aibase": 0.45,
    "aihot": 0.45,
    "aihubtoday": 0.45,
    "followbuilders": 0.25,
    "opmlrss": 0.15,
    "xapi": 0.15,
    "socialdata_x": 0.15,
}
AI_DEFAULT_SOURCES = {"aibase", "aihot", "aihubtoday"}
CURATED_MEDIA_TRUSTED_SOURCE_KEYWORDS = [
    "the decoder ai news",
    "techcrunch ai",
    "venturebeat ai",
    "artificial intelligence news",
    "claude code releases",
    "openrouter",
    "量子位",
    "新智元",
]
CURATED_MEDIA_RESEARCH_SOURCE_KEYWORDS = [
    "marktechpost research",
]
CURATED_MEDIA_RESEARCH_TERMS = [
    "paper",
    "arxiv",
    "research",
    "benchmark",
    "bench",
    "eval",
    "evaluation",
    "dataset",
    "model",
    "llm",
    "agent",
    "diffusion",
    "transformer",
    "multimodal",
    "reasoning",
    "inference",
    "training",
    "open-source",
    "robot",
    "governance",
]
CURATED_MEDIA_BUSINESS_TERMS = [
    "funding",
    "raises",
    "raised",
    "startup",
    "acquire",
    "acquisition",
    "merger",
    "revenue",
    "enterprise",
    "ipo",
    "valuation",
]

LABEL_KEYWORDS = [
    ("model_release", ["model", "gpt", "claude", "gemini", "deepseek", "llm", "模型", "大模型", "发布", "release"]),
    ("developer_tool", ["copilot", "codex", "mcp", "api", "sdk", "developer", "开发者", "编程", "代码", "coding"]),
    ("agent_workflow", ["agent", "智能体", "workflow", "工作流", "tool use", "function calling"]),
    ("research_paper", ["paper", "arxiv", "research", "benchmark", "eval", "论文", "研究", "评测", "榜单"]),
    ("infra_compute", ["gpu", "npu", "cuda", "chip", "semiconductor", "算力", "芯片", "推理"]),
    ("robotics", ["robot", "robotics", "embodied", "机器人", "具身"]),
    ("industry_business", ["funding", "acquire", "融资", "收购", "估值", "营收", "公司"]),
    ("ai_product_update", ["openai", "anthropic", "google", "perplexity", "cursor", "产品", "上线", "更新"]),
]

# ---------------------------------------------------------------------------
# Soft-content detection (survey conclusions / vendor roundups / product promos)
# ---------------------------------------------------------------------------
# These flags never change ai_score / is_ai_related. The importance scorer in
# update_news.py (calculate_item_importance) subtracts SOFT_CONTENT_PENALTY
# when ai_content_flags is non-empty, so marketing-shaped content drops below
# the curated brief gate while staying in the broad "all" pool. Detection is
# content-type based, not source-based: vendor self-posts and media coverage
# of the same content type are both flagged.

_MONTHS = r"(?:january|february|march|april|may|june|july|august|september|october|november|december)"

VENDOR_ROUNDUP_TITLE_RES = [
    # "The latest AI news we announced in August 2026"
    re.compile(
        rf"(?i)\b(?:the )?latest\b[^.!?]{{0,60}}\b(?:we announced|we shipped|we launched|announced|updates?|news)\b[^.!?]{{0,30}}\b{_MONTHS}\b\s+\d{{4}}"
    ),
    # "August 2026 roundup / recap / in review / highlights / digest"
    re.compile(rf"(?i)\b{_MONTHS}\s+\d{{4}}\s+(?:roundup|recap|in review|updates?|highlights|digest)\b"),
    re.compile(r"(?i)\b(?:monthly|weekly)\s+(?:roundup|recap|update|digest)\b"),
]
FIRST_PERSON_ROUNDUP_RE = re.compile(r"(?i)\bwe (?:announced|shipped|launched)\b")

# Strong promo signals. Note: sale/deal/bargain are intentionally excluded —
# business-deal coverage ("$45B compute deal") is real industry news.
PROMO_STRONG_RES = [
    re.compile(r"(?i)\bdiscount code\b|\bcoupon code\b|\bpromo code\b"),
    re.compile(r"(?i)\b(?:up to )?\d+% off\b"),
    re.compile(r"(?i)\bfree trial\b|\bclaim your\b|\bat no cost\b"),
    re.compile(r"(?i)\b(?:student|family) plan\b|\bfree for (?:students|teachers)\b"),
    re.compile(r"(?i)\blimited[- ]time (?:offer|deal|discount)\b|\bspecial offer\b"),
]
# Chinese promo words only count in the title: CN summaries routinely mention
# incidental pricing ("限时折扣") on genuine launches.
PROMO_STRONG_ZH_RE = re.compile(r"优惠|促销|折扣|免费领|立减|特惠|限时免费|学生专享|首月免费")
# "promotion/promotional" alone is ambiguous ("AMIE promotional video") — only
# counts alongside a pricing/free context.
PROMO_CONTEXT_RES = [re.compile(r"(?i)\b(?:promo|promotion|promotional)\b")]
PROMO_PRICE_CONTEXT_RE = re.compile(
    r"(?i)price|pricing|free|offer|discount|cost|plan|trial|subscription|deal|save|%|limits|bonus"
)
# When promo words appear only in the summary of a release-shaped title, treat
# the item as a launch, not a promotion ("GLM-5.3-Flash 开源：…定价为 …1/40"
# whose summary says "限时折扣").
RELEASE_TITLE_GUARD_RE = re.compile(
    r"(?i)开源|开放权重|open[- ]?weight|open[- ]?source|发布|上线|launch|releas|unveil|debut|introduc"
)

_EDUCATION_CONTEXT = (
    r"(?:students?|teachers?|classrooms?|universit\w+|schools?|education(?:al)?|"
    r"assignments?|critical[- ]thinking|originality|homework|academic performance)"
)
SOFT_STUDY_RES = [
    re.compile(r"(?i)\brandomized (?:controlled )?stud(?:y|ies)\b|\bcontrolled stud(?:y|ies)\b"),
    re.compile(r"(?i)\b(?:stud(?:y|ies)|survey|research)\b[^.!?]{0,50}\bfind(?:s|ing)?\b"),
    re.compile(rf"(?i)\b(?:stud(?:y|ies)|survey|research)\b[^.!?]{{0,60}}{_EDUCATION_CONTEXT}"),
    re.compile(r"(?i)\bwhat (?:students|teachers|users) (?:gain|learn)\b"),
    re.compile(r"调研报告|问卷调查|随机对照"),
]


def detect_soft_content_flags(record: dict[str, Any]) -> list[str]:
    """Classify marketing-shaped content that should not reach the curated pool.

    Returns a sorted subset of ``["promo_deal", "soft_study", "vendor_roundup"]``
    based on title + summary. Pure and side-effect free; relevance scoring
    (ai_score / is_ai_related) is untouched — the penalty is applied downstream
    in update_news.py's calculate_item_importance().
    """
    title = str(record.get("title") or "")
    summary = str(record.get("summary") or "")[:500]
    text = f"{title} {summary}"
    if not text.strip():
        return []

    flags: set[str] = set()

    # Vendor monthly roundups: first-person recap posts ("The latest AI news
    # we announced in August 2026"). Media-published recaps stay unflagged
    # unless the item comes from the official feed itself.
    if any(r.search(title) for r in VENDOR_ROUNDUP_TITLE_RES):
        if FIRST_PERSON_ROUNDUP_RE.search(text) or str(record.get("site_id") or "") == "official_ai":
            flags.add("vendor_roundup")

    # Product promotions / subscription deals.
    promo_in_title = any(r.search(title) for r in PROMO_STRONG_RES) or bool(PROMO_STRONG_ZH_RE.search(title))
    promo_in_summary = any(r.search(summary) for r in PROMO_STRONG_RES)
    if promo_in_title or promo_in_summary:
        release_title = bool(RELEASE_TITLE_GUARD_RE.search(title))
        if promo_in_title or not release_title:
            flags.add("promo_deal")
    else:
        ctx_in_title = any(r.search(title) for r in PROMO_CONTEXT_RES)
        ctx_match = ctx_in_title or any(r.search(summary) for r in PROMO_CONTEXT_RES)
        if ctx_match and PROMO_PRICE_CONTEXT_RE.search(text):
            if ctx_in_title or not bool(RELEASE_TITLE_GUARD_RE.search(title)):
                flags.add("promo_deal")

    # Survey / study-conclusion pieces ("What students gain from ...",
    # "..., study finds"). Plain technical papers do not match.
    if any(r.search(text) for r in SOFT_STUDY_RES):
        flags.add("soft_study")

    return sorted(flags)


def contains_any_keyword(haystack: str, keywords: list[str]) -> bool:
    h = haystack.lower()
    return any(k in h for k in keywords)


def contains_unsafe_promotional_content(text: str) -> bool:
    """Block explicit adult promotion without hiding a single policy/news mention."""
    if any(pattern.search(text) for pattern in UNSAFE_HARD_PATTERNS):
        return True
    return sum(bool(pattern.search(text)) for pattern in UNSAFE_PROMO_PATTERNS) >= 2


def matched_keywords(haystack: str, keywords: list[str]) -> list[str]:
    h = haystack.lower()
    return sorted({k for k in keywords if k in h})


def contains_meaningful_ai_signal(haystack: str) -> bool:
    h = haystack.lower()
    if MEANINGFUL_EN_SIGNAL_RE.search(h):
        return True
    if CURSOR_SIGNAL_RE.search(h):
        return True
    return any(k in h for k in AI_KEYWORDS if k not in BROAD_AI_TERMS)


def _label_for_text(text: str, has_tech: bool) -> str:
    for label, keywords in LABEL_KEYWORDS:
        if contains_any_keyword(text, keywords):
            return label
    if has_tech:
        return "ai_tech"
    return "ai_general"


def _result(
    *,
    is_ai_related: bool,
    score: float,
    label: str,
    reason: str,
    signals: list[str] | None = None,
    noise: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "is_ai_related": bool(is_ai_related),
        "score": round(max(0.0, min(1.0, score)), 2),
        "label": label,
        "reason": reason,
        "signals": signals or [],
        "noise": noise or [],
    }


def _score_ai_relevance_core(record: dict[str, Any]) -> dict[str, Any]:
    """Return an explainable relevance score while preserving the old keep/drop behavior."""
    site_id = str(record.get("site_id") or "")
    title = str(record.get("title") or "")
    source = str(record.get("source") or "")
    site_name = str(record.get("site_name") or "")
    url = str(record.get("url") or "")
    # Keyword matching is substring-based, so only the URL host may participate:
    # full URLs (e.g. Google News base64 paths) randomly contain substrings like
    # "llm"/"gpt" and turn unrelated world news into "AI" items.
    try:
        url_host = (urlparse(url).netloc or "").lower()
    except Exception:
        url_host = ""
    text = f"{title} {source} {site_name} {url_host}".lower()

    ai_signals = matched_keywords(text, AI_KEYWORDS)
    if CURSOR_SIGNAL_RE.search(text) and "cursor" not in ai_signals:
        ai_signals = sorted(ai_signals + ["cursor"])
    tech_signals = matched_keywords(text, TECH_KEYWORDS)
    noise = matched_keywords(text, NOISE_KEYWORDS) + matched_keywords(text, COMMERCE_NOISE_KEYWORDS)
    source_prior = SOURCE_PRIORS.get(site_id, 0.0)

    if contains_unsafe_promotional_content(text):
        return _result(
            is_ai_related=False,
            score=0.0,
            label="unsafe_content",
            reason="unsafe_promotional_content",
            signals=[],
            noise=["unsafe_promotional_content"],
        )

    if site_id == "curated_media":
        source_l = source.lower()
        title_l = title.lower()
        trusted_source = contains_any_keyword(source_l, CURATED_MEDIA_TRUSTED_SOURCE_KEYWORDS)
        research_source = contains_any_keyword(source_l, CURATED_MEDIA_RESEARCH_SOURCE_KEYWORDS)
        title_has_ai = contains_meaningful_ai_signal(title_l)
        title_has_broad_ai = contains_any_keyword(title_l, list(BROAD_AI_TERMS)) or EN_SIGNAL_RE.search(title_l) is not None
        title_has_research = contains_any_keyword(title_l, CURATED_MEDIA_RESEARCH_TERMS)

        if research_source and not (title_has_ai or title_has_research):
            return _result(
                is_ai_related=False,
                score=0.22,
                label="source_scope_drop",
                reason="curated_research_source_requires_research_or_ai_title_signal",
                signals=ai_signals + tech_signals,
                noise=noise,
            )

        if not (trusted_source or research_source or title_has_ai or (title_has_broad_ai and bool(tech_signals))):
            return _result(
                is_ai_related=False,
                score=source_prior + (0.28 if title_has_broad_ai else 0.0),
                label="source_scope_drop",
                reason="curated_media_requires_ai_title_or_trusted_ai_feed",
                signals=ai_signals + tech_signals,
                noise=noise,
            )

        if research_source or title_has_research:
            label = "research_paper"
        elif contains_any_keyword(title_l, CURATED_MEDIA_BUSINESS_TERMS):
            label = "industry_business"
        else:
            label = _label_for_text(text, bool(tech_signals))
        base = 0.58 if trusted_source else 0.5
        score = source_prior + base + min(0.12, 0.03 * len(ai_signals)) + min(0.08, 0.02 * len(tech_signals))
        if research_source:
            score = min(score, 0.76)
        if noise and not title_has_ai:
            score -= min(0.16, 0.04 * len(noise))
        return _result(
            is_ai_related=score >= AI_RELEVANCE_THRESHOLD,
            score=score,
            label=label,
            reason="curated_media_source_filter",
            signals=ai_signals + tech_signals or ([source_l] if trusted_source else []),
            noise=noise,
        )

    if site_id in AI_DEFAULT_SOURCES:
        return _result(
            is_ai_related=True,
            score=max(AI_RELEVANCE_THRESHOLD, 0.72 + source_prior),
            label=_label_for_text(text, bool(tech_signals)),
            reason="trusted_ai_source_default_keep",
            signals=ai_signals or [site_id],
            noise=noise,
        )

    has_ai = contains_meaningful_ai_signal(text)
    has_broad_ai = contains_any_keyword(text, list(BROAD_AI_TERMS)) or EN_SIGNAL_RE.search(text) is not None
    has_tech = bool(tech_signals)

    if not (has_ai or (has_broad_ai and has_tech)):
        return _result(
            is_ai_related=False,
            score=source_prior + (0.32 if has_broad_ai else 0.0) + (0.08 if has_tech else 0.0),
            label="not_ai",
            reason="missing_meaningful_ai_signal",
            signals=ai_signals + tech_signals,
            noise=noise,
        )

    if contains_any_keyword(text, COMMERCE_NOISE_KEYWORDS) and not has_ai:
        return _result(
            is_ai_related=False,
            score=0.25 + source_prior,
            label="commerce_noise",
            reason="commerce_noise_without_strong_ai_signal",
            signals=ai_signals + tech_signals,
            noise=noise,
        )

    if contains_any_keyword(text, NOISE_KEYWORDS) and not has_ai:
        return _result(
            is_ai_related=False,
            score=0.25 + source_prior,
            label="noise",
            reason="noise_without_strong_ai_signal",
            signals=ai_signals + tech_signals,
            noise=noise,
        )

    score = source_prior + (0.52 if has_ai else 0.34) + min(0.18, 0.04 * len(ai_signals)) + min(0.12, 0.03 * len(tech_signals))
    if noise:
        score -= min(0.18, 0.04 * len(noise))
    if has_broad_ai and has_tech and not has_ai:
        score = max(score, AI_RELEVANCE_THRESHOLD)
    if has_ai:
        score = max(score, AI_RELEVANCE_THRESHOLD)

    return _result(
        is_ai_related=True,
        score=score,
        label=_label_for_text(text, has_tech),
        reason="matched_ai_signal" if has_ai else "matched_broad_ai_plus_tech_signal",
        signals=ai_signals + tech_signals,
        noise=noise,
    )


def score_ai_relevance(record: dict[str, Any]) -> dict[str, Any]:
    """Relevance verdict plus soft-content flags.

    ``content_flags`` is additive metadata for the downstream importance
    penalty; it never alters ``score``/``is_ai_related``.
    """
    result = _score_ai_relevance_core(record)
    result["content_flags"] = detect_soft_content_flags(record)
    return result


def is_ai_related_record(record: dict[str, Any]) -> bool:
    return bool(score_ai_relevance(record)["is_ai_related"])


def is_broadly_ai_related(record: dict[str, Any]) -> bool:
    """Return True when a record clears the broad-AI floor (score >= 0.3).

    This is a looser gate than ``is_ai_related_record`` (which requires >= 0.65).
    Used by the "all-mode" UI view to filter out obviously-irrelevant noise while
    keeping items with at least tangential AI/tech signal.
    """
    return score_ai_relevance(record)["score"] >= AI_BROAD_RELEVANCE_FLOOR


def add_ai_relevance_fields(record: dict[str, Any]) -> dict[str, Any]:
    relevance = score_ai_relevance(record)
    out = dict(record)
    out["ai_is_related"] = relevance["is_ai_related"]
    out["ai_score"] = relevance["score"]
    out["ai_label"] = relevance["label"]
    out["ai_relevance_reason"] = relevance["reason"]
    out["ai_signals"] = relevance["signals"]
    out["ai_noise"] = relevance["noise"]
    out["ai_content_flags"] = relevance["content_flags"]
    return out
