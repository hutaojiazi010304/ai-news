#!/usr/bin/env python3
"""WeChat Official Account daily article generator.

Reads ``{data-dir}/daily-brief.json`` produced by ``update_news.py``, picks
the top curated items, (re)writes per-item reading guides (direct content
summaries, length follows the content) with a Qwen text model, uses a
fixed-template title and digest, and composes
a 2.35:1 cover image (headline first rewritten into a concrete, AI-focused
visual scene by the text model, then drawn by a Qwen image model,
three-level fallback), then renders a self-contained inline-style HTML page that can be copied into the
WeChat editor, plus ``meta.json`` for future API draft delivery. Every item
carries its original article URL as plain text (WeChat strips external
hyperlinks, so no ``<a>`` tags are used).

Design constraints:

- Standalone: communicates with the rest of the pipeline only through JSON
  files. Never imports ``update_news.py``.
- Zero hard dependencies beyond ``requests`` (``Pillow`` only for cover
  processing); public repo stays runnable key-free.
- Runs without any API key: reuses existing recommend reasons from the data
  (long ones as-is, short ones kept as fallback), keeps the fixed-template
  title and the static brand cover.
- Exit code is always 0 except for argument errors or corrupted input JSON.

Outputs (under ``--output-dir``, default ``weixin/``):

- ``index.html``       inline-style article for copy & paste
- ``meta.json``        title/digest/cover/read_more_url (API-draft-ready)
- ``cover.jpg|.png``   2.35:1 cover (1664x708)
- ``reason-cache.json`` recommend reason cache (21-day TTL)
"""

from __future__ import annotations

import argparse
import hashlib
import html as html_mod
import io
import json
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

# The pipeline's first-party source whitelist, reused to refresh story
# categories at push time (see first_party_category_override). Guarded so a
# minimal environment (requests only) still runs — it then trusts the
# persisted categories as before.
try:  # imported as part of the repo package (tests)
    from scripts.update_news import source_tier_for_record as _source_tier_for_record
except ImportError:
    try:  # run directly as a script next to update_news.py
        from update_news import source_tier_for_record as _source_tier_for_record
    except ImportError:  # update_news deps (bs4, dateutil, ...) not installed
        _source_tier_for_record = None

DEFAULT_API_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
DEFAULT_TEXT_MODEL = "qwen3.8-max"
# Sync-capable Qwen-Image model; qwen-image-max / qwen-image-plus also work
# (override via WEIXIN_IMAGE_MODEL).
DEFAULT_IMAGE_MODEL = "qwen-image-2.0-pro"
DEFAULT_BRAND_NAME = "AI 雷达"
DEFAULT_RADAR_URL = "https://hutaojiazi010304.github.io/ai-news-radar/"
DEFAULT_MAX_ITEMS = 20

TZ_CN = timezone(timedelta(hours=8))
WEEKDAY_CN = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")

# Chinese labels for the story category keys produced by update_news.py's
# story_category(); unknown keys pass through unchanged.
CATEGORY_LABEL_ZH = {
    "official": "官方更新",
    "multi_source": "多源热议",
    "industry": "行业动态",
    "watch": "值得关注",
}

CACHE_VERSION = 6  # v5 cached guides before the absolute no-annotation rule; regen

# Item numbers as circled digits. The filled ❶–❿ glyphs render thin on web
# views, so use the single outlined ①–⑳ set (same style as ⑪) for all 20
# items — consistent weight, always legible.
CIRCLED_NUMS = tuple("①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳")
CACHE_MAX_AGE_DAYS = 21
REASON_RETRY_BACKOFF_SECONDS = 2.0
# LLM output bounds: the prompt has no fixed target (length follows the
# content), so the bounds only reject degenerate output.
REASON_MIN_CHARS = 40
REASON_MAX_CHARS = 260
# Keyless degradation only: an upstream reason must be this long to be
# preferred over a cached long-format reason; with an API key Qwen writes
# every guide itself (see fill_reasons).
REASON_MIN_REUSE_CHARS = 120
FULL_TEXT_MAX_CHARS = 3500
FULL_TEXT_MIN_CHARS = 120
# A persisted summary (the RSS description captured by the pipeline) is the
# preferred grounding for guide writing: it is an offline, deterministic
# asset, while fetching the live page depends on the local network and is
# frequently bot-blocked (403 / timeouts). Summaries below this length — or
# reduced to nothing once boilerplate is stripped — degrade to a full-text
# fetch instead. The bound is low because CJK summaries are dense.
SUMMARY_MIN_GROUNDING_CHARS = 20
# WordPress feeds append "The post <title> appeared first on <blog>." —
# pure boilerplate that must never ground a guide.
SUMMARY_BOILERPLATE_RE = re.compile(
    r"\s*\bthe post\b.*?appeared first on.*$", re.IGNORECASE | re.DOTALL
)
COVER_W, COVER_H = 1664, 708  # 2.35:1
# qwen-image-2.0 series recommended 16:9 size (total pixels must stay within
# the 512*512..2048*2048 range); crop_cover then trims it to the 2.35:1 banner.
IMAGE_REQUEST_SIZE = "2688*1536"
# Qwen-Image models are not served under the OpenAI-compatible routes
# (POST .../compatible-mode/v1/images/generations 404s); the native sync
# multimodal-generation API is required instead.
IMAGE_API_PATH = "/api/v1/services/aigc/multimodal-generation/generation"
# WeChat title length budget; the fixed template title must stay within it.
TITLE_MAX_CHARS = 30
DIGEST_MAX_CHARS = 120
USER_AGENT = (
    "Mozilla/5.0 (compatible; ai-news-radar-weixin/1.0; +https://github.com)"
)

# Headlines containing any of these words are treated as negative news; the
# cover falls back to the neutral brand template prompt instead of drawing
# imagery from such a headline.
NEGATIVE_WORDS = (
    "事故", "裁员", "泄露", "去世", "逝世", "诉讼", "起诉", "处罚",
    "罚款", "宕机", "漏洞", "攻击", "黑客", "诈骗", "破产", "倒闭",
    "亏损", "暴跌", "崩盘", "封禁", "下架", "召回", "丑闻", "危机",
)

IMAGE_NEGATIVE_PROMPT = "文字, 字母, 数字, 水印, 低质量, 模糊, 变形"

TAG_RE = re.compile(r"<[^>]+>")
BLOCK_TAG_RE = re.compile(r"(?is)<(script|style|noscript|svg|head)[^>]*>.*?</\1>")
CJK_RE = re.compile(r"[一-鿿]")
WHITESPACE_RE = re.compile(r"\s+")
BILINGUAL_TITLE_RE = re.compile(r"\s+/\s+")

REASON_SYSTEM_PROMPT = (
    "你是科技新闻编辑，负责为一篇具体的文章写一段「为什么值得读」的中文导读，"
    "用于微信公众号每日 AI 精选推文。"
    "直接概括原文最关键的内容：具体事实、数字、产品、结论，让读者不看原文也知道重点。"
    "以内容本身为主语，不要用「本文」「该文」「作者」这类以文章为主语的开头。"
    "不要自行添加「展示了……」「标志着……」「为……提供了新范式」之类的意义话术；"
    "除非原文本身就是评论观点，才可以转述其观点。"
    "若提供的正文显然不是文章正文（导航、目录、验证页等），就直接把标题本身包含的"
    "信息整理成导读，不编造标题之外的细节，也不要在导读里解释正文缺失或无法概括。"
    "字数跟随内容，简洁为好，通常六七十到两百字，不卡死，不要为凑字数注水，也别超过两百字。"
    "不得编造原文之外的数字或事实。"
    "公司名、产品名一律只用原文中出现的形式（通常是英文），"
    "绝不要附加中文翻译、音译或括号注释，哪怕你自认为知道官方中文名；"
    "人名按国籍写：华人用中文名（如周鸿祎），拿不准时保留英文，同样不得自行音译。"
    "只输出这段导读本身，不加引号，不加任何解释或前缀。"
)

BRAND_COVER_PROMPT = (
    "编辑插画风格横幅封面图，主题：AI 科技日报。"
    "深色背景上的雷达屏幕扫描出光点与数据流，扁平插画，科技感，干净留白。"
    "不要出现任何文字、字母、数字或水印。"
)

# The text model rewrites the headline into a concrete drawing prompt:
# keep the scene tied to the actual AI subject (repos, models, data flows),
# allow brand marks when the headline names them, and avoid stock metaphors
# (ships, mountains) that read as unrelated scenery.
COVER_SCENE_SYSTEM_PROMPT = (
    "你是插画设计师，把一条 AI 新闻标题翻译成一句话的封面画面描述，"
    "供文生图模型绘制扁平插画横幅封面。"
    "画面直接描绘新闻报道的具体内容：产品、技术动作、数据流向，"
    "并紧贴 AI 主题（代码、模型、服务器、机器人、芯片等科技元素）。"
    "可以直接出现标题中提到的公司或产品商标元素（如 GitHub 的猫形标志）；"
    "少用航海、登山、过河之类的隐喻画面，不描绘具体真实人物。"
    "画面中不要出现任何文字、字母、数字。"
    "输出一句 30 到 60 字的中文，只输出描述本身。"
)
COVER_SCENE_MIN_CHARS = 10
COVER_SCENE_MAX_CHARS = 80


class Config(dict):
    """Plain dict of runtime settings; alias for readability."""


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def has_cjk(text: str) -> bool:
    return bool(CJK_RE.search(str(text or "")))


def strip_english_tail(title: str) -> str:
    """Keep only the leading Chinese part of a bilingual "中文标题 / English
    Title" headline.

    Upstream sources (AI HOT and friends) join the Chinese title with the
    English original using " / "; the WeChat article shows the Chinese part
    only. Titles are left untouched unless the split yields at least two
    segments and the first one carries CJK text, so pure-English titles and
    other uses of " / " survive as-is.
    """
    text = str(title or "").strip()
    parts = [part.strip() for part in BILINGUAL_TITLE_RE.split(text) if part.strip()]
    if len(parts) < 2 or not has_cjk(parts[0]):
        return text
    return parts[0]


def esc(value) -> str:
    return html_mod.escape(str(value or ""), quote=True)


def strip_html_text(html_text: str) -> str:
    text = BLOCK_TAG_RE.sub(" ", str(html_text or ""))
    text = TAG_RE.sub(" ", text)
    text = html_mod.unescape(text)
    return WHITESPACE_RE.sub(" ", text).strip()


def title_hash(title: str) -> str:
    return hashlib.sha1(str(title).encode("utf-8")).hexdigest()[:8]


def cache_key(story_id: str, title: str) -> str:
    return f"{story_id}|{title_hash(title)}"


# ---------------------------------------------------------------------------
# Input loading / selection
# ---------------------------------------------------------------------------

def load_brief(path: Path) -> dict | None:
    try:
        brief = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError):
        return None
    if not isinstance(brief, dict) or not isinstance(brief.get("items"), list):
        return None
    return brief


def first_party_category_override(item: dict) -> str | None:
    """Return ``"official"`` when the story's primary source is a first-party
    channel per the live aihot whitelist, otherwise ``None``.

    The category persisted in daily-brief.json was computed by the cloud
    pipeline when the story was created. Stories created before a whitelist
    change (or while the cloud still runs an older commit) keep their stale
    label — e.g. an official company blog stuck on 行业动态. Re-deriving the
    category from the *current* whitelist at push time keeps both article
    variants in sync without waiting for the story to be re-created. Only
    promotes to "official"; never alters any other category.
    """
    if _source_tier_for_record is None:
        return None
    primary = item.get("primary_item")
    primary = primary if isinstance(primary, dict) else {}
    primary_source = str(item.get("source") or primary.get("source") or "").strip()
    if not primary_source:
        return None
    site_id = str(primary.get("site_id") or "").strip()
    if not site_id:
        # Story-level primary_item may lack site_id; find the sources[] ref
        # matching the primary source string to recover it.
        for ref in item.get("sources") or []:
            if not isinstance(ref, dict):
                continue
            if str(ref.get("source") or "").strip() != primary_source:
                continue
            ref_site = str(ref.get("site_id") or "").strip()
            if ref_site:
                site_id = ref_site
                break
    if not site_id:
        return None
    if _source_tier_for_record(site_id, primary_source) is not None:
        return "official"
    return None


def select_items(brief: dict, max_items: int) -> list[dict]:
    """Items sorted by peak_score DESC (the brief is not pre-sorted).

    ``peak_score`` is the best importance the story reached during the
    24h window (persisted by update_news.py). Ranking the daily push by it
    keeps an important story published early in the window from sinking
    below fresher mid-tier items just because its recency component has
    decayed by push time. Falls back to importance_score for briefs
    produced before peak tracking existed."""
    items = [item for item in brief.get("items", []) if isinstance(item, dict)]
    items.sort(
        key=lambda it: -(
            float(it.get("peak_score"))
            if it.get("peak_score") is not None
            else float(it.get("importance_score") or 0)
        )
    )
    selected = items[:max_items]
    # Refresh categories against the current first-party whitelist so stories
    # persisted before a whitelist change are not mislabelled. Shared entry
    # point of both article variants — one fix covers the flat and the
    # grouped layout together.
    for item in selected:
        override = first_party_category_override(item)
        if override:
            item["category"] = override
    return selected


def existing_reason(item: dict) -> str | None:
    """Reuse an upstream-generated recommend reason when present."""
    primary = item.get("primary_item")
    if isinstance(primary, dict):
        reason = str(primary.get("recommend_reason_zh") or "").strip()
        if reason:
            return reason
    for src in item.get("sources") or []:
        if not isinstance(src, dict):
            continue
        reason = str(src.get("recommend_reason_zh") or "").strip()
        if reason:
            return reason
    return None


# ---------------------------------------------------------------------------
# Cache (mirrors scripts/persona_score.py)
# ---------------------------------------------------------------------------

def load_cache(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError):
        return {"version": CACHE_VERSION, "entries": {}}
    if not isinstance(data, dict) or data.get("version") != CACHE_VERSION:
        return {"version": CACHE_VERSION, "entries": {}}
    entries = data.get("entries")
    if not isinstance(entries, dict):
        entries = {}
    return {"version": CACHE_VERSION, "entries": entries}


def prune_cache(cache: dict, now: datetime) -> None:
    cutoff = now - timedelta(days=CACHE_MAX_AGE_DAYS)
    kept = {}
    for key, entry in cache.get("entries", {}).items():
        if not isinstance(entry, dict):
            continue
        created_at = entry.get("created_at")
        try:
            when = datetime.fromisoformat(str(created_at).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            continue
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        if when >= cutoff:
            kept[key] = entry
    cache["entries"] = kept


def save_cache(path: Path, cache: dict, now: datetime) -> None:
    prune_cache(cache, now)
    path.write_text(
        json.dumps(cache, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


# ---------------------------------------------------------------------------
# Network helpers
# ---------------------------------------------------------------------------

def create_session() -> requests.Session:
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})
    return session


def fetch_full_text(session: requests.Session, url: str, timeout: float = 20.0) -> str | None:
    """Direct fetch first, r.jina.ai reader fallback. Returns cleaned text."""
    url = str(url or "").strip()
    if not url.startswith(("http://", "https://")):
        return None
    for candidate in (url, f"https://r.jina.ai/{url}"):
        try:
            response = session.get(candidate, timeout=timeout)
            if response.status_code != 200:
                continue
            text = strip_html_text(response.text)
            if len(text) >= FULL_TEXT_MIN_CHARS:
                return text[:FULL_TEXT_MAX_CHARS]
        except requests.RequestException:
            continue
    return None


def summary_grounding(summary: str, title: str) -> str | None:
    """Return ``summary`` as grounding text, or None if it is not usable.

    Rejects empty text, title duplicates, and anything too short to ground a
    guide; strips WordPress boilerplate ("The post … appeared first on …")
    so only editorial content is kept.
    """
    s = re.sub(r"\s+", " ", str(summary or "")).strip()
    if not s or s == str(title or "").strip():
        return None
    s = SUMMARY_BOILERPLATE_RE.sub("", s).strip()
    if len(s) < SUMMARY_MIN_GROUNDING_CHARS or s == str(title or "").strip():
        return None
    return s


def reason_context(item: dict, session: requests.Session | None) -> str | None:
    """Grounding context for reason writing: summary first, full text second.

    A persisted summary (captured by the pipeline from the RSS description)
    is preferred whenever it passes summary_grounding(): it needs no network
    access, so guide generation does not depend on the live page being
    fetchable from the local machine. Only when no summary qualifies does
    the script fall back to fetching the article itself.
    """
    title = str(item.get("title") or "").strip()
    candidates: list[str] = []
    primary = item.get("primary_item")
    if isinstance(primary, dict):
        candidates.append(str(primary.get("summary") or ""))
    for src in item.get("sources") or []:
        if isinstance(src, dict):
            candidates.append(str(src.get("summary") or ""))
    for candidate in candidates:
        grounding = summary_grounding(candidate, title)
        if grounding:
            return grounding[:FULL_TEXT_MAX_CHARS]
    url = str(item.get("primary_url") or item.get("url") or "").strip()
    if url and session is not None:
        return fetch_full_text(session, url)
    return None


def call_text_api(
    messages: list[dict], cfg: Config, *, temperature: float = 0.3, timeout: float = 120.0
) -> str | None:
    """One Qwen chat completion with a single retry (2s backoff).

    ``enable_thinking`` is switched off: Qwen3 thinking mode stalls
    non-streaming requests on DashScope (read timeouts) and adds minutes
    of latency even when it does answer — neither helps with writing
    short guides. If an endpoint rejects the parameter (HTTP 400), the
    retry drops it and tries again.
    """
    url = f"{cfg['base_url'].rstrip('/')}/chat/completions"
    headers = {
        "Authorization": f"Bearer {cfg['api_key']}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": cfg["text_model"],
        "temperature": temperature,
        "messages": messages,
        "enable_thinking": False,
    }
    last_error = None
    for attempt in range(2):
        if attempt:
            time.sleep(REASON_RETRY_BACKOFF_SECONDS)
        try:
            response = requests.post(url, json=payload, headers=headers, timeout=timeout)
            if response.status_code == 400 and "enable_thinking" in payload:
                # Model/endpoint does not know the parameter: retry without it.
                payload = {k: v for k, v in payload.items() if k != "enable_thinking"}
                last_error = f"HTTP 400: {response.text[:200]}"
                continue
            if response.status_code != 200:
                # Include the server's message: it distinguishes an invalid
                # key (401) from a model/permission/region denial (403).
                last_error = f"HTTP {response.status_code}: {response.text[:200]}"
                continue
            body = response.json()
            choices = body.get("choices") if isinstance(body, dict) else None
            if not isinstance(choices, list) or not choices:
                last_error = "empty choices"
                continue
            content = str(((choices[0] or {}).get("message") or {}).get("content") or "")
            content = content.strip().strip("\"'“”「」").strip()
            if content:
                return content
            last_error = "empty content"
        except (requests.RequestException, ValueError, KeyError, IndexError, TypeError) as exc:
            last_error = str(exc)
    print(f"weixin: text api failed: {last_error}", file=sys.stderr)
    return None


# Phrases a model uses when it refuses to summarize (e.g. the fetched "full
# text" was only site navigation). A refusal must never render as a guide.
REFUSAL_MARKERS = (
    "无法据此", "无法提取", "无法生成导读", "有效导读", "导航菜单",
    "栏目索引", "正文内容仅", "未提供正文",
)


def validate_reason(content: str, title: str) -> bool:
    content = str(content or "").strip()
    if not content or not has_cjk(content):
        return False
    if len(content) < REASON_MIN_CHARS or len(content) > REASON_MAX_CHARS:
        return False
    if content == str(title or "").strip():
        return False
    if "http" in content:
        return False
    if any(marker in content for marker in REFUSAL_MARKERS):
        return False
    return True


def generate_reason(item: dict, context: str, cfg: Config) -> str | None:
    title = str(item.get("title") or "").strip()
    if not title or not context:
        return None
    content = call_text_api(
        [
            {"role": "system", "content": REASON_SYSTEM_PROMPT},
            {"role": "user", "content": f"标题：{title}\n\n正文：\n{context}"},
        ],
        cfg,
    )
    if content and validate_reason(content, title):
        return content
    return None


def fill_reasons(
    items: list[dict],
    cache: dict,
    cfg: Config,
    session: requests.Session | None,
    stats: dict,
) -> None:
    """Attach ``weixin_reason`` to each item.

    With an API key, Qwen is the single source of guide text: cached
    long-format reason > fresh Qwen generation > upstream reason as a
    last-resort fallback (any length) when Qwen has no grounding or fails.
    Keyless runs degrade gracefully: long upstream reason > cache > short
    upstream reason > empty.
    """
    for item in items:
        title = str(item.get("title") or "")
        story_id = str(item.get("story_id") or "")
        existing = existing_reason(item)

        key = cache_key(story_id, title)
        entry = cache.get("entries", {}).get(key)
        cached_reason = ""
        if isinstance(entry, dict) and entry.get("title_hash") == title_hash(title):
            cached_reason = str(entry.get("reason") or "").strip()

        if cfg["api_key"]:
            if cached_reason:
                item["weixin_reason"] = cached_reason
                stats["cached"] += 1
                continue
            context = reason_context(item, session)
            reason = generate_reason(item, context, cfg) if context else None
            if reason:
                item["weixin_reason"] = reason
                cache["entries"][key] = {
                    "reason": reason,
                    "title_hash": title_hash(title),
                    "created_at": utcnow_iso(),
                }
                stats["generated"] += 1
            else:
                item["weixin_reason"] = existing or ""
                stats["skipped"] += 1
            continue

        if existing and len(existing) >= REASON_MIN_REUSE_CHARS:
            item["weixin_reason"] = existing
            stats["reused"] += 1
            continue
        if cached_reason:
            item["weixin_reason"] = cached_reason
            stats["cached"] += 1
            continue
        item["weixin_reason"] = existing or ""
        stats["skipped"] += 1


# ---------------------------------------------------------------------------
# Title / digest
# ---------------------------------------------------------------------------

def fallback_title(brand: str, now_cn: datetime, count: int) -> str:
    return f"{brand} · {now_cn.month}月{now_cn.day}日｜今日精选{count}条"


def make_digest(brand: str, count: int, headline: str, issue_label: str) -> str:
    digest = f"{brand}{issue_label}精选 {count} 条 AI 要闻：{headline}。更多条目见「阅读原文」。"
    return digest[:DIGEST_MAX_CHARS]


# ---------------------------------------------------------------------------
# Cover image
# ---------------------------------------------------------------------------

def is_negative_headline(headline: str) -> bool:
    return any(word in str(headline or "") for word in NEGATIVE_WORDS)


def build_cover_prompt(headline: str, scene: str | None = None) -> tuple[str, str]:
    """Returns (prompt, mode). Negative headlines use the brand template.

    ``scene`` is the text model's brand-free visual description; it replaces
    the raw headline as the theme so the image model never sees brand names.
    """
    headline = str(headline or "").strip()
    if not headline or is_negative_headline(headline):
        return BRAND_COVER_PROMPT, "brand"
    prompt = (
        f"编辑插画风格横幅封面图，主题：{(scene or headline)[:60]}。"
        "扁平插画，科技感，明亮配色，干净留白，适合公众号头图。"
        "不要出现任何文字、字母、数字或水印。"
    )
    return prompt, "headline"


def validate_cover_scene(content: str) -> bool:
    content = str(content or "").strip()
    if not content or not has_cjk(content):
        return False
    if len(content) < COVER_SCENE_MIN_CHARS or len(content) > COVER_SCENE_MAX_CHARS:
        return False
    if "http" in content:
        return False
    return True


def generate_cover_scene(headline: str, cfg: Config) -> str | None:
    """One text-model pass rewriting the headline as a drawable, brand-free
    scene; raw headlines make the image model draw literal logos."""
    headline = str(headline or "").strip()
    if not headline:
        return None
    content = call_text_api(
        [
            {"role": "system", "content": COVER_SCENE_SYSTEM_PROMPT},
            {"role": "user", "content": f"标题：{headline}"},
        ],
        cfg,
    )
    if content and validate_cover_scene(content):
        return content
    return None


def image_api_url(base_url: str) -> str:
    """Native DashScope sync image endpoint derived from the text base URL.

    Strips a trailing ``/compatible-mode/v1`` so custom workspace domains
    (``{WorkspaceId}.cn-beijing.maas.aliyuncs.com``) keep working.
    """
    root = base_url.strip().rstrip("/")
    for suffix in ("/compatible-mode/v1", "/compatible-mode"):
        if root.endswith(suffix):
            root = root[: -len(suffix)]
            break
    return root.rstrip("/") + IMAGE_API_PATH


def extract_image_url(body) -> str | None:
    """Generated image URL from a native multimodal-generation response:
    ``output.choices[0].message.content[].image`` (valid for 24h)."""
    if not isinstance(body, dict):
        return None
    output = body.get("output")
    if not isinstance(output, dict):
        return None
    choices = output.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        return None
    message = choices[0].get("message")
    content = message.get("content") if isinstance(message, dict) else None
    if isinstance(content, list):
        for part in content:
            if isinstance(part, dict):
                image = str(part.get("image") or "")
                if image.startswith("http"):
                    return image
    return None


def call_qwen_image(prompt: str, cfg: Config, session: requests.Session) -> bytes | None:
    """POST the native sync image API; defensive payload shrink on 400;
    downloads the result bytes immediately (result URLs expire in 24h)."""
    url = image_api_url(cfg["base_url"])
    headers = {
        "Authorization": f"Bearer {cfg['api_key']}",
        "Content-Type": "application/json",
    }
    base_payload = {
        "model": cfg["image_model"],
        "input": {
            "messages": [{"role": "user", "content": [{"text": prompt}]}],
        },
    }
    full_params = {
        "negative_prompt": IMAGE_NEGATIVE_PROMPT,
        # Keep prompt rewriting off: cover prompts carry a hard "no text"
        # constraint we don't want the model to rephrase away.
        "prompt_extend": False,
        "watermark": False,
        "size": IMAGE_REQUEST_SIZE,
    }
    payloads = [
        {**base_payload, "parameters": dict(full_params)},
        {
            **base_payload,
            "parameters": {k: v for k, v in full_params.items() if k != "size"},
        },
        dict(base_payload),
    ]
    for index, payload in enumerate(payloads):
        try:
            response = session.post(
                url, json=payload, headers=headers, timeout=cfg["image_timeout"]
            )
            if response.status_code == 400 and index < len(payloads) - 1:
                print(
                    f"weixin: image api rejected payload ({response.status_code}): "
                    f"{response.text[:200]} — retrying with smaller payload",
                    file=sys.stderr,
                )
                continue
            if response.status_code != 200:
                print(
                    f"weixin: image api HTTP {response.status_code}: {response.text[:200]}",
                    file=sys.stderr,
                )
                continue
            image_url = extract_image_url(response.json())
            if not image_url:
                continue
            download = session.get(image_url, timeout=cfg["download_timeout"])
            if download.status_code == 200 and download.content:
                return download.content
        except (requests.RequestException, ValueError, TypeError, KeyError) as exc:
            print(f"weixin: image api error: {exc}", file=sys.stderr)
            continue
    return None


def crop_cover(image_bytes: bytes) -> bytes | None:
    """Center-crop to 2.35:1 (1664x708) and re-encode as JPEG."""
    try:
        from PIL import Image
    except ImportError:
        print("weixin: Pillow not installed, skipping cover crop", file=sys.stderr)
        return None
    try:
        img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
        target_ratio = COVER_W / COVER_H
        width, height = img.size
        if width / height > target_ratio:
            new_w = int(height * target_ratio)
            left = (width - new_w) // 2
            img = img.crop((left, 0, left + new_w, height))
        else:
            new_h = int(width / target_ratio)
            top = (height - new_h) // 2
            img = img.crop((0, top, width, top + new_h))
        img = img.resize((COVER_W, COVER_H))
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=88)
        return buf.getvalue()
    except Exception as exc:  # noqa: BLE001 - never fail the run on cover issues
        print(f"weixin: cover crop failed: {exc}", file=sys.stderr)
        return None


def static_cover_bytes(assets_dir: Path) -> bytes | None:
    path = assets_dir / "weixin-cover-fallback.png"
    try:
        data = path.read_bytes()
    except OSError:
        return None
    return data or None


def resolve_cover(
    headline: str, cfg: Config, session: requests.Session | None, assets_dir: Path
) -> tuple[bytes | None, str, str, bool]:
    """Returns (bytes, filename, mode, scene_used). Mode: headline | brand | static."""
    static = static_cover_bytes(assets_dir)
    if not cfg["api_key"] or session is None:
        return static, "cover.png", "static", False
    prompt, mode = build_cover_prompt(headline)
    scene_used = False
    if mode == "headline":
        scene = generate_cover_scene(headline, cfg)
        if scene:
            prompt, _ = build_cover_prompt(headline, scene)
            scene_used = True
    image_bytes = call_qwen_image(prompt, cfg, session)
    if image_bytes is None and mode == "headline":
        # Level B: brand template prompt retry.
        image_bytes = call_qwen_image(BRAND_COVER_PROMPT, cfg, session)
        mode = "brand"
    if image_bytes is not None:
        cropped = crop_cover(image_bytes)
        if cropped is not None:
            return cropped, "cover.jpg", mode, scene_used
    return static, "cover.png", "static", False


def _write_png(path: Path, width: int, height: int, pixel_fn) -> None:
    """Minimal pure-Python PNG writer (RGB, no font needed) used for the
    static fallback cover when Pillow is unavailable."""
    import struct
    import zlib

    def chunk(tag: bytes, data: bytes) -> bytes:
        block = tag + data
        return struct.pack(">I", len(data)) + block + struct.pack(">I", zlib.crc32(block))

    raw = bytearray()
    for y in range(height):
        raw.append(0)  # filter type: none
        for x in range(width):
            r, g, b = pixel_fn(x, y)
            raw.extend((r, g, b))
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    png = (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", zlib.compress(bytes(raw), 9))
        + chunk(b"IEND", b"")
    )
    path.write_bytes(png)


def make_static_cover(path: Path) -> bool:
    """Generate the repo's static fallback cover. Uses Pillow (nicer, Latin
    text) when available, otherwise a pure-Python radar motif PNG (Pillow's
    default font has no CJK glyphs anyway, so no text is lost)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        try:
            _make_static_cover_pure(path)
            return True
        except Exception as exc:  # noqa: BLE001
            print(f"weixin: pure-python cover failed: {exc}", file=sys.stderr)
            return False
    width, height = COVER_W, COVER_H
    img = Image.new("RGB", (width, height), "#0b1e33")
    draw = ImageDraw.Draw(img)
    # Simple radar-like decoration: concentric circles on the right side.
    cx, cy = int(width * 0.78), height // 2
    for radius in (60, 110, 160, 210):
        draw.ellipse(
            (cx - radius, cy - radius, cx + radius, cy + radius),
            outline="#1f4d7a",
            width=2,
        )
    draw.ellipse((cx - 6, cy - 6, cx + 6, cy + 6), fill="#58c47c")
    draw.line((cx, cy, cx + 150, cy - 150), fill="#58c47c", width=3)

    def font(size: int):
        try:
            return ImageFont.load_default(size=size)
        except TypeError:
            return ImageFont.load_default()

    draw.text((72, height // 2 - 84), "AI RADAR", font=font(72), fill="#eaf2fb")
    draw.text((74, height // 2 + 18), "DAILY BRIEF", font=font(34), fill="#8fb3d9")
    draw.text((74, height // 2 + 68), "Curated AI news, every morning", font=font(20), fill="#5f83a8")

    img.save(path, format="PNG")
    return True


def _make_static_cover_pure(path: Path) -> None:
    """Radar-motif cover without any third-party dependency."""
    import math

    width, height = COVER_W, COVER_H
    bg = (11, 30, 51)          # #0b1e33
    ring = (31, 77, 122)       # #1f4d7a
    accent = (88, 196, 124)    # #58c47c
    cx, cy = int(width * 0.78), height // 2
    radii = (60.0, 110.0, 160.0, 210.0)
    # Left side: three rounded "text bars" as abstract brand marks.
    bars = ((72, height // 2 - 70, 400, 44), (72, height // 2 - 4, 260, 26), (72, height // 2 + 44, 330, 18))

    def pixel_fn(x: int, y: int) -> tuple[int, int, int]:
        for bx, by, bw, bh in bars:
            if bx <= x <= bx + bw and by <= y <= by + bh:
                return (234, 242, 251) if bh >= 40 else (143, 179, 217)
        dx, dy = x - cx, y - cy
        dist = math.hypot(dx, dy)
        for radius in radii:
            if abs(dist - radius) < 1.8:
                return ring
        if dist < 7:
            return accent
        # Sweep line at 45 degrees up-right.
        if dist < 214 and abs(dy + dx) < 2 and dx > 0 and dy < 0:
            return accent
        return bg

    _write_png(path, width, height, pixel_fn)


# ---------------------------------------------------------------------------
# HTML rendering (inline styles only; no <a>/<img>/<table>/flex/float/position)
# ---------------------------------------------------------------------------

def item_original_url(item: dict) -> str:
    """Best original-article URL for an item: primary > url > first source."""
    url = str(item.get("primary_url") or item.get("url") or "").strip()
    if url.startswith(("http://", "https://")):
        return url
    for src in item.get("sources") or []:
        if not isinstance(src, dict):
            continue
        candidate = str(src.get("url") or "").strip()
        if candidate.startswith(("http://", "https://")):
            return candidate
    return ""


def circled_number(num: int) -> str:
    """Circled item number in the unified outlined ①–⑳ style."""
    if 1 <= num <= len(CIRCLED_NUMS):
        return CIRCLED_NUMS[num - 1]
    return str(num)


def render_item_html(item: dict, idx: int) -> str:
    title = strip_english_tail(str(item.get("title") or "").strip())
    reason = str(item.get("weixin_reason") or "").strip()
    sources = [s for s in (item.get("sources") or []) if isinstance(s, dict)]
    source_count = item.get("source_count")
    try:
        source_count = int(source_count)
    except (TypeError, ValueError):
        source_count = len(sources) or 1
    category = str(item.get("category") or "").strip()
    source_name = str(item.get("source_name") or "").strip()
    if not source_name and sources:
        source_name = str(sources[0].get("source_name") or "").strip()

    parts = ['<section style="margin:26px 0 0;padding:0;">']
    parts.append(
        '<p style="margin:0;font-size:16px;line-height:1.55;font-weight:bold;'
        f'color:#1f1f1f;">{circled_number(idx + 1)} {esc(title)}</p>'
    )
    if reason:
        parts.append(
            '<p style="margin:8px 0 0;font-size:15px;line-height:1.75;'
            f'color:#666666;">{esc(reason)}</p>'
        )
    if len(sources) > 1:
        # Collapse the per-source title list into a single line that carries
        # every source name: "标题（Buzzing, NewsNow, Info Flow）".
        line_title = title
        if not line_title:
            for src in sources:
                line_title = strip_english_tail(str(src.get("title") or "").strip())
                if line_title:
                    break
        source_names = []
        for src in sources:
            sub_source = str(src.get("source_name") or "").strip()
            if sub_source and sub_source not in source_names:
                source_names.append(sub_source)
        if line_title or source_names:
            suffix = f"（{', '.join(esc(name) for name in source_names)}）" if source_names else ""
            parts.append(
                '<p style="margin:6px 0 0;font-size:13px;line-height:1.6;'
                f'color:#999999;">{esc(line_title)}{suffix}</p>'
            )
    # Meta line shows the Chinese category label and, for multi-source items,
    # the first source plus 等: "多源热议 · Buzzing等 · 3 个来源".
    meta_source = f"{source_name}等" if len(sources) > 1 and source_name else source_name
    category_zh = CATEGORY_LABEL_ZH.get(category, category)
    meta_bits = [bit for bit in (category_zh, meta_source, f"{source_count} 个来源") if bit]
    if meta_bits:
        meta = " · ".join(esc(bit) for bit in meta_bits)
        parts.append(
            f'<p style="margin:8px 0 0;font-size:12px;color:#b2b2b2;">{meta}</p>'
        )
    link = item_original_url(item)
    if link:
        # Plain-text URL on purpose: the WeChat editor strips external
        # hyperlinks, so this is the only way to hand readers the source.
        parts.append(
            '<p style="margin:8px 0 0;font-size:13px;line-height:1.6;'
            f'color:#576b95;word-break:break-all;">原文：{esc(link)}</p>'
        )
    parts.append("</section>")
    return "\n".join(parts)


def render_article_html(
    items: list[dict],
    *,
    title: str,
    digest: str,
    brand: str,
    issue_label: str,
    radar_url: str,
) -> str:
    items_html = "\n".join(render_item_html(item, idx) for idx, item in enumerate(items))
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(title)}</title>
</head>
<body style="margin:0;padding:0;background-color:#f5f6f7;">
<section style="max-width:677px;margin:0 auto;background-color:#ffffff;padding:28px 20px 36px;">

<section style="border-left:4px solid #07c160;padding-left:12px;margin:0 0 20px;">
<p style="margin:0;font-size:19px;font-weight:bold;color:#1f1f1f;letter-spacing:1px;">{esc(brand)}</p>
<p style="margin:3px 0 0;font-size:13px;color:#999999;">{esc(issue_label)} · 每日 AI 精选</p>
</section>

<p style="margin:0 0 4px;font-size:18px;font-weight:bold;line-height:1.5;color:#111111;">{esc(title)}</p>

{items_html}

<section style="margin-top:30px;border-top:1px dashed #d9d9d9;padding-top:16px;">
<p style="margin:0;font-size:13px;line-height:1.7;color:#999999;">以上内容由 {esc(brand)} 自动整理自过去 24 小时的公开信源，原文链接见每条信息下方。</p>
</section>

<section style="margin-top:22px;background-color:#f5f6f7;padding:14px 16px;">
<p style="margin:0;font-size:12px;font-weight:bold;color:#666666;">以下为发布辅助信息（复制用，粘贴时请勿包含本区块）</p>
<p style="margin:10px 0 0;font-size:13px;line-height:1.7;color:#666666;">标题：{esc(title)}</p>
<p style="margin:6px 0 0;font-size:13px;line-height:1.7;color:#666666;">摘要：{esc(digest)}</p>
<p style="margin:6px 0 0;font-size:13px;line-height:1.7;color:#666666;">阅读原文：{esc(radar_url)}</p>
</section>

</section>
</body>
</html>
"""


def build_meta(
    *,
    issue_date: str,
    brand: str,
    title: str,
    digest: str,
    cover_filename: str | None,
    radar_url: str,
    item_count: int,
    cfg: Config,
) -> dict:
    return {
        "generated_at": utcnow_iso(),
        "issue_date": issue_date,
        "brand": brand,
        "title": title,
        "digest": digest,
        "cover": cover_filename,
        "read_more_url": radar_url,
        "item_count": item_count,
        "delivery": "manual_copy",
        "text_model": cfg["text_model"],
        "image_model": cfg["image_model"],
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="WeChat daily article generator")
    parser.add_argument("--data-dir", default="data", help="data directory")
    parser.add_argument("--output-dir", default="weixin", help="output directory")
    parser.add_argument("--assets-dir", default="assets", help="assets directory")
    parser.add_argument(
        "--max-items",
        type=int,
        default=None,
        help="max items (defaults to WEIXIN_MAX_ITEMS env or 20)",
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="run without writing any files"
    )
    parser.add_argument(
        "--make-fallback-cover",
        action="store_true",
        help="generate assets/weixin-cover-fallback.png and exit",
    )
    return parser.parse_args(argv)


def build_config(args: argparse.Namespace) -> Config:
    max_items = args.max_items
    if max_items is None:
        try:
            max_items = int(os.environ.get("WEIXIN_MAX_ITEMS") or DEFAULT_MAX_ITEMS)
        except ValueError:
            max_items = DEFAULT_MAX_ITEMS
    return Config(
        api_key=os.environ.get("DASHSCOPE_API_KEY", "").strip(),
        base_url=(
            os.environ.get("DASHSCOPE_API_BASE_URL", "").strip() or DEFAULT_API_BASE_URL
        ),
        text_model=os.environ.get("WEIXIN_TEXT_MODEL", "").strip() or DEFAULT_TEXT_MODEL,
        image_model=(
            os.environ.get("WEIXIN_IMAGE_MODEL", "").strip() or DEFAULT_IMAGE_MODEL
        ),
        brand=os.environ.get("WEIXIN_BRAND_NAME", "").strip() or DEFAULT_BRAND_NAME,
        radar_url=os.environ.get("WEIXIN_RADAR_URL", "").strip() or DEFAULT_RADAR_URL,
        max_items=max(1, max_items),
        image_timeout=120.0,
        download_timeout=60.0,
    )


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    if args.make_fallback_cover:
        path = Path(args.assets_dir) / "weixin-cover-fallback.png"
        if make_static_cover(path):
            print(f"weixin: wrote fallback cover to {path}")
        return 0

    if os.environ.get("WEIXIN_ENABLED", "").strip() == "0":
        print("weixin: disabled via WEIXIN_ENABLED=0, nothing to do")
        return 0

    cfg = build_config(args)
    data_dir = Path(args.data_dir)
    output_dir = Path(args.output_dir)
    assets_dir = Path(args.assets_dir)

    brief = load_brief(data_dir / "daily-brief.json")
    if brief is None:
        print(f"weixin: {data_dir / 'daily-brief.json'} not found or invalid, nothing to do")
        return 0

    items = select_items(brief, cfg["max_items"])
    if not items:
        print("weixin: brief has no items, nothing to do")
        return 0

    now_cn = datetime.now(TZ_CN)
    issue_date = now_cn.strftime("%Y-%m-%d")
    issue_label = f"{now_cn.month}月{now_cn.day}日 {WEEKDAY_CN[now_cn.weekday()]}"

    session = create_session() if cfg["api_key"] else None
    cache_path = output_dir / "reason-cache.json"
    cache = load_cache(cache_path)
    stats = {"reused": 0, "cached": 0, "generated": 0, "skipped": 0}

    fill_reasons(items, cache, cfg, session, stats)

    headline = strip_english_tail(str(items[0].get("title") or "").strip())
    # Fixed template title 「AI 雷达 · X月X日｜今日精选N条」; it never depends
    # on the API key.
    title = fallback_title(cfg["brand"], now_cn, len(items))

    digest = make_digest(cfg["brand"], len(items), headline, issue_label)

    cover_bytes, cover_filename, cover_mode, cover_scene = resolve_cover(
        headline, cfg, session, assets_dir
    )

    html_text = render_article_html(
        items,
        title=title,
        digest=digest,
        brand=cfg["brand"],
        issue_label=issue_label,
        radar_url=cfg["radar_url"],
    )
    meta = build_meta(
        issue_date=issue_date,
        brand=cfg["brand"],
        title=title,
        digest=digest,
        cover_filename=cover_filename if cover_bytes else None,
        radar_url=cfg["radar_url"],
        item_count=len(items),
        cfg=cfg,
    )

    if not args.dry_run:
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / "index.html").write_text(html_text, encoding="utf-8")
        (output_dir / "meta.json").write_text(
            json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        if cover_bytes:
            # Remove the other cover variant so no stale file is served.
            for name in ("cover.jpg", "cover.png"):
                if name != cover_filename:
                    try:
                        (output_dir / name).unlink()
                    except OSError:
                        pass
            (output_dir / cover_filename).write_bytes(cover_bytes)
        save_cache(cache_path, cache, datetime.now(timezone.utc))

    print(
        "weixin: items={items} reasons reused={reused} cached={cached} "
        "generated={generated} skipped={skipped} "
        "cover_mode={cover_mode} cover_scene={cover_scene} "
        "dry_run={dry_run}".format(
            items=len(items),
            reused=stats["reused"],
            cached=stats["cached"],
            generated=stats["generated"],
            skipped=stats["skipped"],
            cover_mode=cover_mode,
            cover_scene=1 if cover_scene else 0,
            dry_run=1 if args.dry_run else 0,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
