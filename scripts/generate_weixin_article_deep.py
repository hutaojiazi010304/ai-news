"""Deep-read (精读版) WeChat weekly article generator.

The only remaining WeChat push variant (the older flat and grouped layouts
were retired and their shared infrastructure merged into this file). Stories
are laid out in grouped sections and upgraded for close reading:

- Selection: the weekly story pool rebuilt from ``data/archive.json``
  (falling back to ``daily-brief.json``), top stories by ``peak_score``
  (or ``importance_score`` for the weekly pool, which carries no peak)
  only (default 20; override via ``--max-items`` or
  ``WEIXIN_DEEP_MAX_ITEMS``). Empty sections are skipped.
- Guides: longer, written in a relayed-news style (facts and numbers only,
  no fabrication, no fixed "据 X 报道" opening). Stored in
  ``weixin-deep/reason-cache.json`` (``DEEP_CACHE_VERSION``).
- Images: each item gets ONE real illustration pulled from its original
  article page at publish time (direct fetch, self-hostable reader fallback).
  Extraction is scoped to the article body (<article> element, or the page
  cut at a recommendation heading) so related-news thumbnails can never
  substitute for body art. When the body has NO image at all and the
  page's recommendation widget carries a card whose title clearly reports
  the same story (matched against the page's own headline, double-gated
  by score and margin), that card's image is borrowed — a same-topic
  illustration beats none; near-misses keep the item image-less. No
  AI-generated filler. Images are saved under ``images/`` (committed,
  served by Pages) with a 「图源：domain」credit line for internal
  redistribution. Rendered AFTER the guide, centered at
  ``DEEP_IMAGE_WIDTH_PERCENT`` of the column width.
- Title/digest: fixed templates ("X月X日-X月X日｜本周精读N条"), no LLM.
  The publish helper block (title/digest/read-more URL) is NOT
  rendered into the page body (it kept getting pasted into the editor by
  accident); it is written to ``publish-info.txt`` next to the article.
  Per-story titles get the ``ensure_zh_titles`` backfill:
  pure-English titles left over by a broken upstream translation chain are
  translated with Qwen before guides and rendering, cached in this
  variant's own cache (``tt1|`` entries); failures keep the English title
  as-is.
- Cover: the top story's own downloaded illustration, center-cropped to
  2.35:1; when the top story has no image, the next item in selection
  order (score-descending) that has one. Only when NO item carries an
  image does it fall back to ``resolve_cover`` (Qwen image generation
  with the static brand cover as the last resort).

Design constraints: standalone JSON-file interface, exit 0 on every
graceful path, keyless runs degrade (upstream reasons, static cover,
no images only when the network refuses them — image fetching itself needs
no API key, so the session is created unconditionally).

Output (default ``weixin-deep/``):

- ``index.html``        inline-style article (this variant MAY use ``<img>``:
                        the preview page is canonical; pasting into an editor
                        strips external images and they are re-inserted
                        manually for the internal service account)
- ``meta.json``         layout="deep", sections census, per-story images map
- ``cover.jpg|.png``    2.35:1 cover (top item's illustration first;
                        generated or static only when no item has an image)
- ``reason-cache.json`` deep-guide cache (21-day TTL)
- ``publish-info.txt``  title/digest/read-more URL for manual publishing
- ``images/``           one downloaded article image per story that has one
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
import unicodedata
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests

# Shared WeChat push infrastructure, merged from the retired flat
# (1.0) and grouped (2.0) layout scripts so this file stands alone:
# weekly story-pool selection, cache plumbing, English-title
# translation, cover generation and meta/config building.


# The pipeline module (update_news.py), reused at push time: the first-party
# source whitelist that refreshes story categories (see
# first_party_category_override), and the weekly selection pipeline (see
# build_weekly_brief) which rebuilds the story pool from data/archive.json.
# Guarded so a minimal environment (requests only) still runs — it then falls
# back to daily-brief.json and trusts the persisted categories as before.
try:  # imported as part of the repo package (tests)
    from scripts import update_news as _un
except ImportError:
    try:  # run directly as a script next to update_news.py
        import update_news as _un
    except ImportError:  # update_news deps (bs4, dateutil, ...) not installed
        _un = None


_source_tier_for_record = getattr(_un, "source_tier_for_record", None) if _un is not None else None


DEFAULT_API_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"


DEFAULT_TEXT_MODEL = "qwen3.8-max"


# Sync-capable Qwen-Image model; qwen-image-max / qwen-image-plus also work
# (override via WEIXIN_IMAGE_MODEL).
DEFAULT_IMAGE_MODEL = "qwen-image-2.0-pro"


DEFAULT_BRAND_NAME = "AI 雷达"


DEFAULT_RADAR_URL = "https://hutaojiazi010304.github.io/ai-news-radar/"


DEFAULT_MAX_ITEMS = 20


# Weekly push cadence: the issue is selected from the pipeline's 21-day
# archive (data/archive.json) instead of the daily brief's 24h window.
# Overridable via WEIXIN_LOOKBACK_DAYS (clamped to 1..20 days).
WEEKLY_LOOKBACK_DAYS_DEFAULT = 7


WEEKLY_LOOKBACK_DAYS_MIN = 1


WEEKLY_LOOKBACK_DAYS_MAX = 20  # the archive keeps 21 days; leave headroom


# Weekly freshness curve: a story keeps full recency for the first 6 days
# (flat segment) and only then decays with a gentle 48h half-life, so an
# important event published earlier in the week is not down-ranked by age
# at push time. The daily pipeline keeps the default 72h half-life with no
# flat segment (update_news.py headline_freshness_score).
WEEKLY_FRESHNESS_FLAT_HOURS = 144.0


WEEKLY_FRESHNESS_HALF_LIFE_HOURS = 48.0


# Merge/dedup windows wider than the daily pipeline (6h): follow-up
# reporting across days belongs to the same story, and same-site rewrites
# syndicated days apart must still collapse.
WEEKLY_TITLE_WINDOW_HOURS = 72


WEEKLY_NEAR_DUP_WINDOW_HOURS = 168.0


# Default per-issue cap on official-tier stories (override via
# WEIXIN_OFFICIAL_CAP; set 0 for no cap). Official changelogs easily fill an
# entire 7-day pool (a typical week: 40+ of the ~60 gated stories are
# official, and their fixed editorial/tier floor keeps every one of them
# above the best industry story), crowding industry/multi-source/watch items
# out of the issue entirely. The cap applies at the very END of selection:
# the uncapped mechanism runs over the full pool exactly as before, the
# issue's first 16 officials in display order stay untouched, and the freed
# slots go to the next non-official stories. (Trimming the pool beforehand
# removed the fresh-channel tail that feeds the source penalty and changed
# which officials survive — the opposite of the intended effect.)
WEEKLY_OFFICIAL_CAP_DEFAULT = 16


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


# ---------------------------------------------------------------------------
# Weekly story pool (rebuilt from the pipeline archive)
# ---------------------------------------------------------------------------

def _weekly_lookback_days() -> int:
    raw = os.environ.get("WEIXIN_LOOKBACK_DAYS", "").strip()
    if not raw:
        return WEEKLY_LOOKBACK_DAYS_DEFAULT
    try:
        value = int(raw)
    except ValueError:
        return WEEKLY_LOOKBACK_DAYS_DEFAULT
    return max(WEEKLY_LOOKBACK_DAYS_MIN, min(WEEKLY_LOOKBACK_DAYS_MAX, value))


def _weekly_official_cap() -> int:
    """WEIXIN_OFFICIAL_CAP: max official-tier stories per issue.

    Defaults to ``WEEKLY_OFFICIAL_CAP_DEFAULT`` (16); set ``0`` to disable
    the cap entirely. The cap is applied at the very END of selection (see
    ``_select_with_official_cap``): the uncapped mechanism runs unchanged,
    the issue's first N officials in display order stay untouched, and the
    freed slots go to the next non-official stories. The candidate pool is
    never trimmed."""
    raw = os.environ.get("WEIXIN_OFFICIAL_CAP", "").strip()
    if not raw:
        return WEEKLY_OFFICIAL_CAP_DEFAULT
    try:
        return max(0, int(raw))
    except ValueError:
        return WEEKLY_OFFICIAL_CAP_DEFAULT


def _select_with_official_cap(gated: list[dict], max_items: int, cap: int) -> list[dict]:
    """Uncapped selection mechanism, official cap applied only at the end.

    Runs the same greedy diversity selection over the FULL pool that the
    uncapped version uses, then — if the resulting issue carries more than
    ``cap`` official stories — keeps the first ``cap`` officials in the
    issue's display (score-descending) order and backfills the freed slots
    with the non-official stories the same mechanism picks next. Nothing is
    trimmed from the pool beforehand: the greedy sees exactly the candidates
    of the uncapped run, so the surviving officials are literally the
    uncapped issue's first officials, untouched.
    """
    full_order = _un.select_diverse_stories(gated, len(gated))
    article = full_order[:max_items]
    officials = [s for s in article if str(s.get("category") or "") == "official"]
    if len(officials) <= cap:
        return article
    kept_ids = {
        id(s)
        for s in sorted(
            officials,
            key=lambda s: (-_un.story_gate_score(s), str(s.get("title") or "")),
        )[:cap]
    }
    kept = [s for s in article if id(s) in kept_ids]
    nonofficials = [
        s
        for s in article
        if id(s) not in kept_ids and str(s.get("category") or "") != "official"
    ]
    backfill: list[dict] = []
    for story in full_order[max_items:]:
        if len(nonofficials) + len(backfill) >= max_items - len(kept):
            break
        if str(story.get("category") or "") == "official":
            continue
        backfill.append(story)
    return kept + nonofficials + backfill


def _apply_pipeline_enhance_cache(items: list[dict], cache: dict[str, str]) -> None:
    """Restore title_enhanced_zh / recommend_reason_zh from the pipeline's
    persisted cache entries (``te1|`` / ``re1|`` key namespaces).

    update_news.py's add_title_enhancements()/add_recommend_reasons() return
    before even reading the cache when DEEPSEEK_API_KEY is absent — and the
    local weekly push only carries the Qwen key — so the entries are looked
    up directly with the pipeline's key formula:
    ``prefix + sha1(normalize_url(url) + "|" + title)`` where title is the
    bilingual pass's ``title_en or title_original or title``. Empty values
    are negative-cache entries and are skipped. Must run after
    add_bilingual_fields so the key titles exist.
    """
    for item in items:
        url = _un.normalize_url(str(item.get("url") or ""))
        title = str(
            item.get("title_en") or item.get("title_original") or item.get("title") or ""
        ).strip()
        key_body = hashlib.sha1(f"{url}|{title}".encode("utf-8")).hexdigest()
        enhanced = cache.get(_un.TITLE_ENHANCE_CACHE_PREFIX + key_body) or ""
        if enhanced:
            item["title_enhanced_zh"] = enhanced
        reason = cache.get(_un.RECOMMEND_REASON_CACHE_PREFIX + key_body) or ""
        if reason:
            item["recommend_reason_zh"] = reason


def build_weekly_brief(
    data_dir: Path, now: datetime, max_items: int, pool_size: int | None = None
) -> dict | None:
    """Rebuild the story pool for the weekly push from the pipeline archive.

    daily-brief.json only covers the pipeline's 24h window, so the weekly
    issue rebuilds stories from ``data/archive.json`` (the 21-day item
    store): filter to the lookback window, replay the pipeline
    normalisation / AI filter / dedup / story merge with week-wide windows,
    then rescore every story with the weekly freshness curve (flat for the
    first 6 days, gentle half-life after) so an important event published
    earlier in the week is not out-ranked by fresher mid-tier items.

    Strictly read-only: nothing under ``data_dir`` is ever written; in
    particular the in-memory title-cache mutations from the bilingual and
    enhance passes are never persisted. Returns a brief-shaped payload
    (``generated_at`` / ``window_hours`` / ``total_items`` / ``items``), or
    None when the weekly pool cannot be built (pipeline module unavailable,
    archive missing/corrupt/empty, or no story passes the quality gate) —
    the caller then falls back to daily-brief.json.

    ``pool_size`` over-selects the guide-writing pool: the selection
    mechanism returns up to ``pool_size`` candidates (at least ``max_items``)
    so items whose guide ends up empty can be dropped and backfilled before
    the issue narrows back to ``max_items`` (see ``fill_reasons``). None
    keeps the former exact-``max_items`` selection. The official cap still
    bounds the pool, so the final issue (a subset of it) stays ≤ cap too.
    """
    if _un is None:
        return None
    try:
        payload = json.loads((data_dir / "archive.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError):
        return None
    records = payload.get("items") if isinstance(payload, dict) else None
    if not isinstance(records, list) or not records:
        return None

    lookback_days = _weekly_lookback_days()
    window_hours = lookback_days * 24
    cutoff = now - timedelta(days=lookback_days)

    pool: list[dict] = []
    for record in records:
        if not isinstance(record, dict):
            continue
        ts = _un.event_time(record)
        if not ts or ts < cutoff:
            continue
        item = dict(record)
        item["title"] = _un.maybe_fix_mojibake(str(item.get("title") or ""))
        item["source"] = _un.maybe_fix_mojibake(
            _un.normalize_source_for_display(
                str(item.get("site_id") or ""),
                str(item.get("source") or ""),
                str(item.get("url") or ""),
            )
        )
        if (
            str(item.get("site_id") or "") == "aihubtoday"
            and _un.is_hubtoday_placeholder_title(str(item.get("title") or ""))
        ):
            continue
        item = _un.add_ai_relevance_fields(item)
        if not item.get("ai_is_related", False):
            continue
        item = _un.add_source_tier_fields(item)
        pool.append(item)

    if not pool:
        return None
    pool = _un.normalize_aihubtoday_records(pool)
    # Cache-only bilingual pass: zero translation budgets make this a pure
    # offline cache lookup. The returned cache dict may gain entries in
    # memory; it is never written back — data/ stays read-only for the
    # weekly push.
    title_cache = _un.load_title_zh_cache(data_dir / "title-zh-cache.json")
    pool, _unused_all, title_cache = _un.add_bilingual_fields(
        pool, [], title_cache, 0
    )
    _apply_pipeline_enhance_cache(pool, title_cache)

    # Rescoring below needs the FULL original items: the truncated
    # primary_item copied into story records lacks site_id/published_at/
    # ai_score and would collapse the score. Index before dedupe drops any.
    by_id = {str(item.get("id") or ""): item for item in pool if item.get("id")}

    deduped = _un.dedupe_items_by_title_url(pool, random_pick=False)
    deduped = _un.suppress_near_duplicate_items(
        deduped, window_hours=WEEKLY_NEAR_DUP_WINDOW_HOURS
    )
    stories, _events = _un.merge_story_items(
        deduped,
        now,
        window_hours=window_hours,
        title_window_hours=WEEKLY_TITLE_WINDOW_HOURS,
    )

    for story in stories:
        primary_item = story.get("primary_item") or {}
        full = by_id.get(str(primary_item.get("id") or ""))
        if not isinstance(full, dict):
            continue
        try:
            source_count = int(story.get("source_count") or 1)
        except (TypeError, ValueError):
            source_count = 1
        importance = _un.calculate_item_importance(
            full,
            now,
            window_hours,
            duplicate_count=source_count,
            half_life_hours=WEEKLY_FRESHNESS_HALF_LIFE_HOURS,
            flat_hours=WEEKLY_FRESHNESS_FLAT_HOURS,
        )
        score = importance["score"]
        story["score"] = score
        story["importance"] = score
        story["importance_score"] = score
        story["importance_breakdown"] = importance["breakdown"]
        category = _un.story_category(score, full, source_count)
        story["category"] = category
        story["importance_label"] = _un.importance_label(category)
        story["reasons"] = _un.story_reasons(full, score, source_count)

    gated = [story for story in stories if _un.story_passes_brief_gate(story)]
    if not gated:
        return None
    cap = _weekly_official_cap()
    limit = max_items if pool_size is None else max(pool_size, max_items)
    if cap > 0:
        items = _select_with_official_cap(gated, limit, cap)
    else:
        items = _un.select_diverse_stories(gated, limit)
    if not items:
        return None
    return {
        "generated_at": now.astimezone(timezone.utc).isoformat(),
        "window_hours": window_hours,
        "total_items": len(items),
        "items": items,
    }


def load_push_brief(
    data_dir: Path, max_items: int, pool_size: int | None = None
) -> dict | None:
    """Unified input for the push scripts: the weekly story pool rebuilt
    from the pipeline archive, falling back to the 24h daily brief.

    Fallback (logged) when the pipeline module is unavailable,
    ``WEIXIN_FORCE_DAILY=1`` is set, archive.json is missing/corrupt/empty,
    or no story passes the quality gate. Fixtures that ship only a
    daily-brief.json therefore keep working unchanged.

    ``pool_size`` is forwarded to ``build_weekly_brief`` (over-selected
    guide-writing pool); the daily-brief fallback ignores it — the daily
    snapshot carries at most 20 items, so backfill is best-effort there.
    """
    data_dir = Path(data_dir)
    if str(os.environ.get("WEIXIN_FORCE_DAILY") or "").strip() == "1":
        print("weixin: WEIXIN_FORCE_DAILY=1, using daily-brief.json")
        return load_brief(data_dir / "daily-brief.json")
    brief = build_weekly_brief(
        data_dir, datetime.now(timezone.utc), max_items, pool_size
    )
    if brief is not None:
        print(
            "weixin: weekly brief from archive: "
            f"{brief.get('total_items')} items over {brief.get('window_hours')}h"
        )
        return brief
    print("weixin: weekly brief unavailable, falling back to daily-brief.json")
    return load_brief(data_dir / "daily-brief.json")


def first_party_category_override(item: dict) -> str | None:
    """Return ``"official"`` when the story's primary source is a first-party
    channel per the live aihot whitelist, otherwise ``None``.

    The category persisted in daily-brief.json was computed by the cloud
    pipeline when the story was created. Stories created before a whitelist
    change (or while the cloud still runs an older commit) keep their stale
    label — e.g. an official company blog stuck on 行业动态. Re-deriving the
    category from the *current* whitelist at push time keeps the article
    in sync without waiting for the story to be re-created. Only
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
    # point of selection — one fix covers the whole article.
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


# Umbrella ``source_name``s label an aggregate adapter, not a publisher:
# "Official AI Updates" bundles every first-party channel (OpenAI News,
# GitHub Changelog, Google AI Blog, Hugging Face Blog, …) and "AI HOT"
# bundles the trending-channel aggregate. Displaying the bucket repeats a
# generic label; the specific channel (``source``) is the meaningful name,
# so display resolves buckets to their channel (real publishers pass
# through unchanged).
UMBRELLA_SOURCE_NAMES = {"Official AI Updates", "AI HOT"}


def item_channel_source(item: dict) -> str:
    """The specific feed/channel an item came from (``source``)."""
    channel = str(item.get("source") or "").strip()
    if channel:
        return channel
    primary = item.get("primary_item")
    if isinstance(primary, dict):
        channel = str(primary.get("source") or "").strip()
        if channel:
            return channel
    for src in item.get("sources") or []:
        if isinstance(src, dict):
            channel = str(src.get("source") or "").strip()
            if channel:
                return channel
    return ""


def origin_url_key(entry: dict, fallback_index: int) -> str:
    """Canonical URL key that decides origin identity for a pipeline entry.

    Mirrors ``update_news.distinct_story_source_count``: entries linking the
    same canonical URL are copies of one origin; entries without a URL each
    get their own key since they cannot be proven copies of anything. Falls
    back to plain string comparison when the pipeline module is unavailable.
    """
    url = str(entry.get("url") or "")
    if _un is not None:
        canonical = _un.canonical_story_url(url)
    else:
        canonical = url.strip().lower().rstrip("/")
    return canonical or f"__no_url__{entry.get('id') or fallback_index}"


def split_origin_sources(item: dict) -> tuple[list[str], int, list[str]]:
    """Origin vs repost display names for any story.

    Generalizes the former single-origin split to every story: one pipeline
    entry per distinct canonical URL is a credited origin (``M`` in the meta
    line's 「M 个来源 · N 个转载」), every extra entry that repeats an
    already-credited URL is a repost (转载), so ``N`` is the pipeline entry
    count minus the origin count. Entries are tier-sorted upstream, so a
    URL's first representative is its highest-priority channel; the
    pipeline-chosen primary entry (id match, else the first entry for
    id-less data) opens the origin list under the item-level display name,
    exactly as the old single-origin branch did. Returns ``(origin names,
    origin count, repost names)``; names are deduped — a channel fetched
    twice never shows up as its own repost — and the origin count tracks
    URLs, not names, so it always equals the pipeline's ``source_count``.
    """
    sources = [s for s in (item.get("sources") or []) if isinstance(s, dict)]
    if not sources:
        return [], 0, []
    primary = item.get("primary_item")
    primary_id = str(primary.get("id") or "") if isinstance(primary, dict) else ""
    primary_index: int | None = 0
    if primary_id:
        primary_index = None
        for index, entry in enumerate(sources):
            if str(entry.get("id") or "") == primary_id:
                primary_index = index
                break
    order = list(range(len(sources)))
    if primary_index:
        order.insert(0, order.pop(primary_index))
    origin_names: list[str] = []
    repost_names: list[str] = []
    claimed: set[str] = set()
    origin_count = 0
    for index in order:
        entry = sources[index]
        key = origin_url_key(entry, index)
        name = (
            item_display_source(item)
            if index == primary_index
            else item_display_source(entry)
        )
        if key in claimed:
            if name and name not in origin_names and name not in repost_names:
                repost_names.append(name)
            continue
        claimed.add(key)
        origin_count += 1
        if name and name not in origin_names:
            origin_names.append(name)
    return origin_names, origin_count, repost_names


def trim_source_annotation(name: str) -> str:
    """Drop the trailing fetch-method / provenance annotation from a channel
    name: "Hacker News 热门（buzzing.cc 中文翻译）" → "Hacker News 热门",
    "Qwen：Blog Retrieval（API）" → "Qwen：Blog Retrieval". Every item shows
    its 原文 link right under the meta line, so the real publisher is one tap
    away and the annotation is noise for readers. Display-only: the data
    keeps the full name (tier overrides match on it)."""
    trimmed = re.sub(r"[（(][^（）()]*[）)]\s*$", "", name).strip()
    return trimmed or name


def item_display_source(item: dict) -> str:
    """Source name for display: umbrella buckets resolve to their channel."""
    source_name = str(item.get("source_name") or "").strip()
    if not source_name:
        for src in item.get("sources") or []:
            if isinstance(src, dict):
                source_name = str(src.get("source_name") or "").strip()
                if source_name:
                    break
    if source_name in UMBRELLA_SOURCE_NAMES:
        channel = item_channel_source(item)
        if channel:
            return trim_source_annotation(channel)
    return trim_source_annotation(source_name)


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


# ---------------------------------------------------------------------------
# English-title backfill translation
# ---------------------------------------------------------------------------

# Upstream (update_news.py add_bilingual_fields) translates English headlines
# into bilingual "中文 / English" titles, but when that chain is down stories
# reach the brief with pure-English titles. The article is Chinese-first, so
# translate the residue here with the same Qwen text model that writes the
# guides. Mirrors the upstream translation prompt: Chinese output, entities
# (products/companies/models/people) kept verbatim in English.
TITLE_TRANSLATE_SYSTEM_PROMPT = (
    "你是科技新闻编辑，把英文 AI/科技新闻标题翻译成地道的简体中文，"
    "用作微信公众号推文里的条目标题。"
    "产品名、公司名、模型名、媒体名、人名一律保留英文原文，不翻译也不音译。"
    "用自然的中文新闻标题表达，避免翻译腔，信息量贴近原标题。"
    "只返回译文本身，不加引号，不加任何解释。"
)
TITLE_TRANSLATE_CACHE_PREFIX = "tt1|"
TITLE_TRANSLATE_MIN_CJK = 4
TITLE_TRANSLATE_MAX_CHARS = 90


def title_needs_translation(title: str) -> bool:
    """True when no Chinese survives ``strip_english_tail`` and the rest
    still reads as English prose.

    Bare version tags ("v2.1.245") and other non-prose strings are skipped:
    translating them produces garbage, so they pass through untranslated.
    The letter-count rule mirrors update_news.py's ``is_mostly_english``.
    """
    text = strip_english_tail(str(title or "").strip())
    if not text or has_cjk(text):
        return False
    letters = re.findall(r"[A-Za-z]", text)
    return len(letters) >= max(6, len(text) // 4)


def validate_title_translation(original: str, translated: str) -> bool:
    """Sanity bounds on a title translation candidate (fresh or cached)."""
    text = str(translated or "").strip()
    if not text or not has_cjk(text):
        return False
    if text == str(original or "").strip():
        return False
    if len(CJK_RE.findall(text)) < TITLE_TRANSLATE_MIN_CJK:
        return False
    if len(text) > TITLE_TRANSLATE_MAX_CHARS:
        return False
    if "http" in text:
        return False
    return True


def translate_title_to_zh(title: str, cfg: Config) -> str | None:
    """One Qwen call translating a pure-English headline; None on failure."""
    content = call_text_api(
        [
            {"role": "system", "content": TITLE_TRANSLATE_SYSTEM_PROMPT},
            {"role": "user", "content": str(title or "").strip()},
        ],
        cfg,
        temperature=0.2,
        timeout=60.0,
    )
    if content and validate_title_translation(title, content):
        return content.strip()
    return None


def ensure_zh_titles(items: list[dict], cache: dict, cfg: Config, stats: dict) -> None:
    """Translate pure-English story titles in place.

    Runs after select_items and before guide generation in every article
    variant, so guides, the cover headline and the rendered titles all see
    the Chinese form. Translations are cached per original title — they are
    layout-agnostic and live in this variant's own reason cache. Failures
    degrade to the original English title; without an API
    key this is a no-op (the article still renders, English titles intact).

    Note: rewriting ``item["title"]`` changes the guide cache key of the
    affected stories once (story_id|title_hash), so their guides regenerate
    on the first run after the switch — a one-time cost.
    """
    for item in items:
        title = str(item.get("title") or "").strip()
        if not title_needs_translation(title):
            continue
        if not cfg.get("api_key"):
            # Keyless: nothing can translate; count so the summary line
            # still reports how many titles stay English.
            stats["titles_skipped"] = stats.get("titles_skipped", 0) + 1
            continue
        key = TITLE_TRANSLATE_CACHE_PREFIX + title_hash(title)
        entry = cache.get("entries", {}).get(key)
        cached = str(entry.get("zh_title") or "").strip() if isinstance(entry, dict) else ""
        if cached and validate_title_translation(title, cached):
            item["title_pre_translate"] = title  # keeps --regenerate fragments working
            item["title"] = cached
            stats["titles_cached"] = stats.get("titles_cached", 0) + 1
            continue
        translated = translate_title_to_zh(title, cfg)
        if translated:
            item["title_pre_translate"] = title  # keeps --regenerate fragments working
            item["title"] = translated
            cache["entries"][key] = {
                "zh_title": translated,
                "created_at": utcnow_iso(),
            }
            stats["titles_translated"] = stats.get("titles_translated", 0) + 1
            print(f"weixin: 标题翻译：{title[:48]} → {translated}")
        else:
            stats["titles_skipped"] = stats.get("titles_skipped", 0) + 1
            print(f"weixin: 标题翻译失败，保留英文原标题：{title[:48]}", file=sys.stderr)


# ---------------------------------------------------------------------------
# --regenerate: re-roll cached guides by display number / fragment / story id
# ---------------------------------------------------------------------------

def parse_regenerate_specs(value: str) -> list[str]:
    """Comma-separated ``--regenerate`` value into individual specs."""
    return [s.strip() for s in str(value or "").split(",") if s.strip()]


def _regenerate_position(spec: str) -> int | None:
    """Spec as a 1-based display position (``3`` or ``③``); None otherwise.

    NB: ``isdigit()`` is True for ``③`` too but ``int()`` rejects it, so
    plain digits are identified via ``isdecimal()``.
    """
    if spec.isdecimal():
        return int(spec)
    if len(spec) == 1 and spec in CIRCLED_NUMS:
        return CIRCLED_NUMS.index(spec) + 1
    return None


def match_regenerate(items: list[dict], specs: list[str]) -> tuple[set, list]:
    """Resolve ``--regenerate`` specs to story ids.

    A spec may be: a display position as shown in the article (``3`` or
    ``③`` — the selection order, which is also the display order),
    an exact story_id, or a title fragment. Fragments match
    case-insensitively against the current display title AND the pre-
    translation English title, so whatever a maintainer reads — in the
    article or in the brief — works. ``all`` selects every picked item.
    Returns ``(matched story_ids, unmatched specs)``.
    """
    wanted: set = set()
    unmatched: list = []
    for spec in specs:
        if spec.lower() == "all":
            wanted.update(str(it.get("story_id") or "") for it in items)
            continue
        position = _regenerate_position(spec)
        if position is not None:
            if 1 <= position <= len(items):
                wanted.add(str(items[position - 1].get("story_id") or ""))
            else:
                unmatched.append(spec)
            continue
        lowered = spec.lower()
        hits = []
        for it in items:
            story_id = str(it.get("story_id") or "")
            if spec == story_id:
                hits.append(story_id)
                continue
            haystacks = (
                str(it.get("title") or ""),
                str(it.get("title_pre_translate") or ""),
                str(it.get("title_original") or ""),
            )
            if any(lowered in text.lower() for text in haystacks if text):
                hits.append(story_id)
        if hits:
            wanted.update(hits)
        else:
            unmatched.append(spec)
    wanted.discard("")
    return wanted, unmatched


def drop_cache_entries(cache: dict, story_ids: set) -> int:
    """Drop cached guides for the given story ids; returns count dropped.

    Used by ``--regenerate``: guide generation is stochastic sampling, so
    quality varies run to run under an identical prompt. Re-rolling the
    specific entries a maintainer is unhappy with is the practical quality
    lever — no need to lower standards or regenerate the whole issue.
    Keys are matched on the ``story_id|`` prefix only, so ``tt1|`` title
    translations are never dropped.
    """
    entries = cache.get("entries") or {}
    doomed = [k for k in entries if k.split("|", 1)[0] in story_ids]
    for key in doomed:
        del entries[key]
    return len(doomed)


def report_regenerate(
    prefix: str, items: list[dict], wanted: set, unmatched: list, dropped: int
) -> None:
    """Print what --regenerate matched (or the item menu when nothing did).

    A mistyped spec must not fail silently: when nothing matched, the full
    numbered item list is printed so the maintainer can retry with a number.
    """
    for spec in unmatched:
        print(f"{prefix}: --regenerate 未命中：{spec}", file=sys.stderr)
    matched = [
        (num, it)
        for num, it in enumerate(items, 1)
        if str(it.get("story_id") or "") in wanted
    ]
    if matched:
        print(f"{prefix}: --regenerate 已清除 {dropped} 条缓存导读，将重新生成：")
        for num, it in matched:
            print(f"  {circled_number(num)} {str(it.get('title') or '')[:60]}")
    elif unmatched:
        print(f"{prefix}: 本期条目如下，可用序号或标题片段重试：", file=sys.stderr)
        for num, it in enumerate(items, 1):
            print(
                f"  {circled_number(num)} {str(it.get('title') or '')[:60]}",
                file=sys.stderr,
            )


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


# Section grouping + colour styles (from the retired grouped layout;
# still the deep layout's section/box rendering basis).


# Display order for the sections: official first, then high-score industry
# news, multi-source discussions and the watchlist. Empty sections are
# skipped entirely; unknown categories (should not happen) go last.
CATEGORY_ORDER = ("official", "industry", "multi_source", "watch")


# Per-section title colors (deep, brand-like tones). The large enclosing box
# is drawn in the SAME hue at higher transparency (rgba) so each section reads
# as one color family. Inline styles only — the WeChat editor strips classes
# and <style> blocks.
CATEGORY_COLORS = {
    "official": "#13501B",
    "industry": "#215F9A",
    "multi_source": "#C04F15",
    "watch": "#595959",
}


# Box transparency: border is the hue at moderate alpha, background a faint
# wash of the same hue.
BOX_BORDER_ALPHA = 0.45


BOX_BACKGROUND_ALPHA = 0.08


def with_alpha(hex_color: str, alpha: float) -> str:
    """Return ``rgba(r, g, b, alpha)`` for a ``#rrggbb`` color."""
    h = hex_color.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return f"rgba({r}, {g}, {b}, {alpha})"


def _build_styles() -> dict[str, dict[str, str]]:
    styles = {}
    for category, color in CATEGORY_COLORS.items():
        styles[category] = {
            "color": color,
            "border": with_alpha(color, BOX_BORDER_ALPHA),
            "background": with_alpha(color, BOX_BACKGROUND_ALPHA),
        }
    return styles


CATEGORY_STYLES = _build_styles()


DEFAULT_STYLE = CATEGORY_STYLES["watch"]


def group_items(items: list[dict]) -> list[tuple[str, list[dict]]]:
    """Group the picked items by story category, preserving in-group order.

    ``select_items`` already ranks by peak_score DESC; grouping keeps that
    order inside each section so nothing about the selection changes.
    """
    grouped: dict[str, list[dict]] = {}
    for item in items:
        category = str(item.get("category") or "watch").strip() or "watch"
        grouped.setdefault(category, []).append(item)
    ordered = [(cat, grouped[cat]) for cat in CATEGORY_ORDER if cat in grouped]
    ordered.extend(
        (cat, grouped[cat]) for cat in grouped if cat not in CATEGORY_ORDER
    )
    return ordered


DEFAULT_OUTPUT_DIR = "weixin-deep"
DEFAULT_DEEP_MAX_ITEMS = 20
# Backup candidates carried into deep-guide writing beyond max_items
# (override via WEIXIN_DEEP_POOL_EXTRA).
DEFAULT_DEEP_POOL_EXTRA = 10
# Reader fallback base URL (same "/<target-url>" API shape as r.jina.ai);
# point at a self-hosted jina-ai/reader instance for fully local deployments.
JINA_READER_BASE_URL = os.environ.get("JINA_READER_BASE_URL", "https://r.jina.ai").rstrip("/")

# Independent cache version: bumped only when the deep prompt/bounds change
# in a way that invalidates EXISTING entries. v8 drops the source line from
# both the prompt and the user content ("可在正文里自然提及信源" made guides
# open with "Official AI Updates 发布/披露"-style attributions; the specific
# channel now appears only in the meta line, via item_display_source) — v7
# entries were still free to name the source in the body. (v7 itself:
# grounding stopped trusting chrome-card <article> elements (see
# DEEP_ARTICLE_MIN_BODY_CHARS) and attribution stopped using the umbrella
# bucket name. v6: hard length ceiling restored and the prompt's "可适当写长"
# permission revoked — v5 entries read noticeably longer/padded. v5:
# generation reverted to the pre-highlight prompt and marking moved to a
# separate second call — the combined prompt degraded guide quality, so v4
# entries had to go too.) Mismatched versions are rejected by
# load_deep_cache, forcing regeneration.
DEEP_CACHE_VERSION = 8

# The prompt targets
# 150-350 chars and the ceiling is enforced too: full-text grounding is
# often rich enough that the model overshoots into padded, multi-topic
# recaps without a hard bound (the brief "可适当写长" experiment drifted
# every guide ~50-200 chars longer and pulled in peripheral reactions).
DEEP_REASON_MIN_CHARS = 80
DEEP_REASON_MAX_CHARS = 450
# Highlight marks: the model brackets the most worth-reading fragments with
# 【】 while generating (summaries/conclusions first, then theme-tied names
# or numbers); rendering turns them into bold spans in the section color.
# Cap aligned with the maintainer's hand-marked examples (up to 4 per guide).
# Bad markup degrades to an un-highlighted guide — formatting never loses text.
DEEP_REASON_MAX_MARKS = 4
# One mark covering this share of the whole guide means "highlighted
# everything" — treat as bad markup and drop the highlights.
DEEP_REASON_MARK_MAX_COVERAGE = 0.8
# A persisted summary must be this long before it alone can ground a
# 150-350-char report; anything thinner falls back to a full-text fetch
# (the item
# count is small and bounded, so per-item fetching is affordable). A thin
# summary is NOT thrown away, though: it is kept as the last-resort
# grounding when the fetch yields nothing usable (real facts still beat
# wall pages or no grounding at all).
DEEP_SUMMARY_MIN_GROUNDING_CHARS = 120
# Same idea for the scoped <article> element: huggingface.co blog pages
# wrap ONLY sidebar/model cards in <article> (the post sits outside every
# one), so "the longest <article>" can be ~130 chars of card chrome that
# either starves the guide of grounding or grounds it on the wrong text.
# The longest <article> only counts as the body when its stripped text
# clears this floor; otherwise extraction falls back to the recommendation
# heading cut / whole page. 300 keeps every real article (thousands of
# chars) while clearing the largest observed card with >2x margin. Image
# scoping stays unguarded so card thumbnails still never leak in.
DEEP_ARTICLE_MIN_BODY_CHARS = 300

# All three values are HARD WALL-CLOCK DEADLINES per request (enforced by
# bounded_get on top of requests' per-chunk timeout): a slow trickle would
# otherwise defeat the per-chunk timeout and keep a request open almost
# indefinitely.
PAGE_FETCH_TIMEOUT = 15.0
IMAGE_DOWNLOAD_TIMEOUT = 20.0
# Pages under 300 chars are almost certainly bot walls/redirect stubs;
# fall back to the reader proxy for those too.
PAGE_MIN_HTML_CHARS = 300
# Anti-bot wall pages can STILL clear FULL_TEXT_MIN_CHARS — WeChat's
# 「环境异常」CAPTCHA gate, once fetched through the reader proxy, is padded
# by the proxy's own Title:/URL Source:/Warning: headers past the floor, and
# the length check alone would happily feed that boilerplate to a guide
# (observed: a guide grounded on nothing but the wall, which the model could
# only answer by restating the title). Such pages count as fetch failures,
# so deep_reason_context degrades to the thin summary, if any.
WALL_TEXT_MARKERS = (
    "环境异常，完成验证后即可继续访问",
    "maybe requiring CAPTCHA",
)
# Body budgets (bytes): bounded_get stops reading once exceeded, so a
# mislabelled huge file can never be buffered fully into RAM.
PAGE_MAX_BYTES = 8_000_000
IMAGE_MAX_BYTES = 2_500_000
# Tracking pixels compress to a few hundred bytes.
IMAGE_MIN_BYTES = 512
# Decoded or declared dimensions below this are UI chrome, not content art.
IMAGE_MIN_DIMENSION = 120
# Keep committed images phone-friendly and the repo growth bounded.
IMAGE_MAX_WIDTH = 1080
IMAGE_JPEG_QUALITY = 82
MAX_IMAGE_CANDIDATES = 10
# Display width of the article image relative to the text column. Full-width
# pictures dominate the short deep items, so the image renders AFTER the
# guide, shrunk to this percent (aspect ratio kept) and centered; tweak
# here, nowhere else.
DEEP_IMAGE_WIDTH_PERCENT = 65

# Recommendation-widget image borrowing: considered ONLY when the body scope
# yields zero image candidates. A card's title (its <img alt>) is compared
# against the page's OWN headline — not the brief title, which is translated
# while card titles come in the page language (English on aibase), so a
# cross-language pair could never match. Thresholds measured on live aibase
# pages: same-story pairs scored 0.72-0.76, the closest near-miss (a related
# but different story about the same product) 0.59, unrelated cards <= 0.28.
# The winner must clear the floor AND lead the runner-up by the margin, so
# "same product, different event" cards never borrow into the wrong article;
# a genuine two-card cluster stays ambiguous and the item keeps no image.
REC_BORROW_MIN_SCORE = 0.65
REC_BORROW_MIN_MARGIN = 0.08
# Card alt texts shorter than this are chrome, not a recommendation title.
REC_CARD_MIN_ALT_CHARS = 4

IMG_TAG_RE = re.compile(r"<img\b[^>]*>", re.IGNORECASE)
ATTR_RE = re.compile(r"([\w-]+)\s*=\s*(\"[^\"]*\"|'[^']*'|[^\s>]+)", re.IGNORECASE)
MD_IMAGE_RE = re.compile(r"!\[[^\]]*\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
H1_TAG_RE = re.compile(r"<h1\b[^>]*>(.*?)</h1>", re.IGNORECASE | re.DOTALL)

# Attribute precedence for lazy-loaded pages; the first non-empty wins.
IMAGE_SRC_ATTRS = ("src", "data-src", "data-original", "data-lazy-src")
IMAGE_SRCSET_ATTRS = ("srcset", "data-srcset")
# Substring skip-list applied to the whole URL. Deliberately excludes
# "banner" (Chinese sites often name the article hero image banner.*),
# "ad-" (false-positives on head-/download-) and "thumb"/"share"
# (thumbnails and share cards are usually the real content image).
IMAGE_URL_SKIP_MARKERS = (
    "logo", "icon", "avatar", "favicon", "emoji", "qrcode", "badge",
    "button", "1x1", "1px", "pixel", "spacer", "blank", "placeholder",
    "loading", "spinner", "sprite", "tracking", "beacon",
)
IMAGE_EXT_BLOCKLIST = (".svg", ".ico")

# Body scoping for image/grounding extraction. News sites (aibase among
# them) wrap the article in an <article> element while the "related news"
# widget ("AI News Recommendations") lives OUTSIDE it, so whole-page
# scanning let recommendation thumbnails win whenever the body image failed
# to download. Prefer the longest <article>; on pages without one, cut the
# page at the first recommendation heading instead (HTML <hN> or markdown
# #). No signal at all → whole page, i.e. the legacy behavior.
ARTICLE_TAG_RE = re.compile(r"<article\b[^>]*>.*?</article>", re.IGNORECASE | re.DOTALL)
REC_HEADING_RE = (
    r"AI\s*News\s*Recommendations|推荐阅读|相关推荐|相关阅读|为你推荐|猜你喜欢"
)
REC_HEADING_HTML_RE = re.compile(
    rf"<h[1-6][^>]*>\s*(?:{REC_HEADING_RE})\s*</h[1-6]>", re.IGNORECASE
)
REC_HEADING_MD_RE = re.compile(
    rf"^#{{1,6}}\s*(?:{REC_HEADING_RE})\s*$", re.IGNORECASE | re.MULTILINE
)

DEEP_REASON_SYSTEM_PROMPT = (
    "你是科技新闻编辑，负责把一篇具体文章的核心内容转述成一段「精读导读」，"
    "用于微信公众号每周 AI 精选的深度版（精读版）推文。"
    "用转述式报道的口吻写：开头直接复述原文最关键的内容，"
    "不要使用「据某某报道」之类的固定开场，正文中不要提及信源名称；"
    "用自己的话复述原文最关键的内容：做了什么、数字是多少、结论是什么；"
    "优先保留正文中的具体事实与数字。"
    "只复述正文中明确出现的信息，不得编造、推断或补充任何正文之外的事实、"
    "数字、日期与意义。"
    "不要添加「展示了……」「标志着……」「为……开启了新篇章」之类的意义话术；"
    "除非原文本身就是评论，才可以转述其观点，并注明是原文观点。"
    "字数控制在一百五十到三百五十之间，信息密度优先，不要为凑字数注水；"
    "正文素材再多，也只挑最核心的事实、数字与结论来写，"
    "不要逐点罗列次要细节，不写背景铺垫、外界反应、观点争议等外围内容。"
    "导读中不得出现任何网址、链接或链接文字（如 GitHub、官网地址），"
    "需要提及页面时只描述它是什么（如「官方更新日志」）。"
    "公司名、产品名一律只用原文中出现的形式（通常是英文），"
    "绝不要附加中文翻译、音译或括号注释，哪怕你自认为知道官方中文名；"
    "人名按国籍写：华人用中文名（如周鸿祎），拿不准时保留英文，同样不得自行音译。"
    "若提供的正文显然不是文章正文（导航、目录、验证页等），就把标题本身包含的"
    "信息整理成转述，不编造标题之外的细节，也不要在导读里解释正文缺失或无法转述。"
    "只输出这段导读本身，不加引号，不加任何解释或前缀。"
)

# Highlighting runs as a SEPARATE second call over the finished guide:
# folding these instructions into the generation prompt measurably degraded
# guide quality (lost punctuation, leaked meta commentary about the source
# text). The marker must return the guide verbatim plus 【】 brackets.
DEEP_MARK_SYSTEM_PROMPT = (
    "你是校对员，唯一的工作是给一段已成稿的新闻导读添加高亮标记："
    "从中挑出最值得读的片段，用中文方括号【】原样包住，然后输出标注后的导读。"
    "挑选原则：优先标能概括全文核心或带结论性的短语、句子"
    "（包括简短的判断性短语）；与主题紧密相关的关键名称或数字"
    "（产品名、版本号、核心数据等）也可以标；"
    "句子先总说后展开（冒号或列举引出细节）时，标前面的总说部分，"
    "不标段尾的展开细节；同类信息不要重复标。"
    "总共不超过四处，可以更少，互不重叠；全文平铺直叙、挑不出时可以不标。"
    "严格要求：除添加【】外，不得改动、增删原文的任何文字与标点，"
    "原文缺句号等问题也保持原样，不要替它修正。"
    "只输出标注后的导读，不加任何解释或前缀。"
)


# ---------------------------------------------------------------------------
# Selection / cache
# ---------------------------------------------------------------------------

def resolve_deep_max_items(args: argparse.Namespace) -> int:
    """--max-items > WEIXIN_DEEP_MAX_ITEMS > 20."""
    if args.max_items is not None:
        return max(1, args.max_items)
    try:
        value = int(os.environ.get("WEIXIN_DEEP_MAX_ITEMS") or DEFAULT_DEEP_MAX_ITEMS)
    except ValueError:
        value = DEFAULT_DEEP_MAX_ITEMS
    return max(1, value)


def deep_pool_extra() -> int:
    """WEIXIN_DEEP_POOL_EXTRA: backup candidates for deep-guide writing.

    An item whose deep guide ends up empty is dropped and the next backup
    moves up (see ``fill_deep_reasons``), keeping the issue at its full size
    whenever the pool allows. Garbage/negative values clamp to 0.
    """
    raw = os.environ.get("WEIXIN_DEEP_POOL_EXTRA", "").strip()
    if not raw:
        return DEFAULT_DEEP_POOL_EXTRA
    try:
        return max(0, int(raw))
    except ValueError:
        return DEFAULT_DEEP_POOL_EXTRA


def load_deep_cache(path: Path) -> dict:
    """Same shape as ``load_cache`` but versioned independently."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError):
        return {"version": DEEP_CACHE_VERSION, "entries": {}}
    if not isinstance(data, dict) or data.get("version") != DEEP_CACHE_VERSION:
        return {"version": DEEP_CACHE_VERSION, "entries": {}}
    entries = data.get("entries")
    if not isinstance(entries, dict):
        entries = {}
    return {"version": DEEP_CACHE_VERSION, "entries": entries}


# ---------------------------------------------------------------------------
# Deep guides
# ---------------------------------------------------------------------------

def validate_deep_reason(content: str, title: str) -> bool:
    content = str(content or "").strip()
    if not content or not has_cjk(content):
        return False
    if len(content) < DEEP_REASON_MIN_CHARS or len(content) > DEEP_REASON_MAX_CHARS:
        return False
    if content == str(title or "").strip():
        return False
    if "http" in content:
        return False
    if any(marker in content for marker in REFUSAL_MARKERS):
        return False
    return True


def parse_deep_marks(content: str):
    """Split a 【】-marked guide into ``(plain_text, [(start, end), ...])``.

    Offsets index into ``plain_text``. Returns None for malformed markup
    (unpaired, nested or empty marks) so callers can degrade to the
    un-highlighted text instead of losing the guide over formatting.
    """
    text = str(content or "")
    plain: list[str] = []
    marks: list[tuple[int, int]] = []
    open_at: int | None = None
    for ch in text:
        if ch == "【":
            if open_at is not None:
                return None
            open_at = len(plain)
        elif ch == "】":
            if open_at is None or len(plain) == open_at:
                return None
            marks.append((open_at, len(plain)))
            open_at = None
        else:
            plain.append(ch)
    if open_at is not None:
        return None
    return "".join(plain), marks


def strip_deep_marks(content: str) -> str:
    return str(content or "").replace("【", "").replace("】", "")


def deep_marks_usable(plain: str, marks: list[tuple[int, int]]) -> bool:
    """Too many marks, or one mark covering most of the guide, is a marking
    failure: render the guide without highlights rather than a wall of color."""
    if len(marks) > DEEP_REASON_MAX_MARKS:
        return False
    limit = max(1, int(len(plain) * DEEP_REASON_MARK_MAX_COVERAGE))
    return all(end - start < limit for start, end in marks)


def deep_reason_context(
    item: dict, session: requests.Session | None, net_state: dict | None = None
) -> str | None:
    """Grounding for deep guides: long summary first, full-text fetch second.

    A deep
    150-350-char report needs real substance: summaries under 120 chars
    degrade to a live fetch of the article, whose text is usually richer.

    The fetch goes through fetch_page_html (direct → reader fallback, both under
    hard wall-clock deadlines) and is scoped to the article body, so
    recommendation widgets below it can never leak into the grounding. When
    the direct HTML cannot be scoped at all (JS-shell pages whose <article>
    elements are all chrome), the reader proxy gets a second chance so the
    guide is grounded on the real article instead of navigation text.

    When the fetch yields nothing usable — request failure, text under the
    floor, or an anti-bot wall page (is_wall_text) — the meatiest thin
    summary is returned instead of nothing: real facts let the model write a
    proportionally shorter but grounded report, instead of improvising over
    wall boilerplate or the bare title.
    """
    title = str(item.get("title") or "").strip()
    # ensure_zh_titles may have replaced item["title"] with a Chinese
    # translation; keep comparing summaries against the original title too,
    # so a lazy feed's summary that merely repeats the (English) title is
    # still rejected as no-grounding.
    title_original = re.sub(r"\s+", " ", str(item.get("title_original") or "")).strip()
    candidates: list[str] = []
    primary = item.get("primary_item")
    if isinstance(primary, dict):
        candidates.append(str(primary.get("summary") or ""))
    for src in item.get("sources") or []:
        if isinstance(src, dict):
            candidates.append(str(src.get("summary") or ""))
    thin_grounding: str | None = None
    for candidate in candidates:
        grounding = summary_grounding(candidate, title)
        if title_original and grounding == title_original:
            grounding = None
        if not grounding:
            continue
        if len(grounding) >= DEEP_SUMMARY_MIN_GROUNDING_CHARS:
            return grounding[:FULL_TEXT_MAX_CHARS]
        # Keep the meatiest thin summary as the last-resort grounding for
        # when the full-text fetch comes back empty or walled off.
        if thin_grounding is None or len(grounding) > len(thin_grounding):
            thin_grounding = grounding
    url = str(item.get("primary_url") or item.get("url") or "").strip()
    if not url.startswith(("http://", "https://")) or session is None:
        return thin_grounding
    payload = fetch_page_html(session, url, net_state)
    if payload is None:
        return thin_grounding
    body, kind = payload
    if kind == "html" and body_scope_degraded(body, kind, DEEP_ARTICLE_MIN_BODY_CHARS):
        # JS-shell pages (github.blog is the observed case): the article body
        # never reaches the server HTML, every <article> is a profile or
        # related-post card, and scoping degrades to the whole page — which
        # strips down to navigation text, grounding the guide on menus. The
        # reader proxy renders the actual article, so try it before settling
        # for chrome (costs one extra call, and only on unscopable pages).
        jina_payload = fetch_jina_bytes(
            session, url, PAGE_FETCH_TIMEOUT, PAGE_MAX_BYTES, net_state
        )
        if jina_payload is not None:
            jina_text = jina_payload.decode("utf-8", errors="replace")
            if jina_text.strip():
                body, kind = jina_text, "markdown"
    text = strip_html_text(
        scope_to_article_body(body, kind, DEEP_ARTICLE_MIN_BODY_CHARS)
    )
    if len(text) >= FULL_TEXT_MIN_CHARS and not is_wall_text(text):
        return text[:FULL_TEXT_MAX_CHARS]
    return thin_grounding


def _anchor_deep_marks(text: str, fragments: list[str]) -> list[tuple[int, int]]:
    """Locate the model's chosen spans in the ORIGINAL guide, in order.

    The marker model often "fixes" punctuation outside the marks; its span
    choices are still good. Re-anchoring keeps them while the guide text
    itself stays byte-identical. Spans that cannot be placed (not found,
    nearly whole-guide, cap reached) are dropped.
    """
    limit = max(1, int(len(text) * DEEP_REASON_MARK_MAX_COVERAGE))
    anchors: list[tuple[int, int]] = []
    search_from = 0
    for frag in fragments:
        if len(anchors) >= DEEP_REASON_MAX_MARKS:
            break
        if not frag or len(frag) >= limit:
            continue
        pos = text.find(frag, search_from)
        if pos == -1:
            continue
        anchors.append((pos, pos + len(frag)))
        search_from = pos + len(frag)
    return anchors


def apply_deep_marks(text: str, marks: list[tuple[int, int]]) -> str:
    """Rebuild ``text`` with 【】 brackets around the ``marks`` offsets."""
    parts: list[str] = []
    pos = 0
    for start, end in marks:
        parts.append(text[pos:start])
        parts.append(f"【{text[start:end]}】")
        pos = end
    parts.append(text[pos:])
    return "".join(parts)


def add_deep_marks(guide: str, cfg: dict, label: str = "") -> str:
    """Second, strictly separated LLM pass that only brackets key fragments.

    Mixing marking instructions into the generation prompt measurably
    degraded guide quality (lost punctuation, leaked meta commentary about
    the source text), so marking runs on its own call over the finished
    guide. Clean verbatim output is used as-is; when the marker rewrites
    anything outside the brackets (most commonly "fixing" punctuation) its
    SPAN CHOICES are kept but re-anchored in the original guide, so the
    guide text never changes. Only when no span can be anchored is the
    attempt discarded — with one reinforced retry before giving up.
    """
    text = str(guide or "").strip()
    if not text:
        return text
    tag = label or text[:20]
    reminder = ""
    for attempt in (1, 2):
        content = call_text_api(
            [
                {"role": "system", "content": DEEP_MARK_SYSTEM_PROMPT},
                {"role": "user", "content": text + reminder},
            ],
            cfg,
        )
        cause = None
        if content:
            marked = str(content).strip()
            parsed = parse_deep_marks(marked)
            if parsed is None:
                cause = "【】不成对或嵌套"
            else:
                plain, marks = parsed
                if plain == text and deep_marks_usable(plain, marks):
                    if not marks:
                        print(
                            f"weixin-deep: 标注：模型判断无可标片段：{tag}",
                            flush=True,
                        )
                    return marked
                fragments = [plain[s:e] for s, e in marks]
                anchors = _anchor_deep_marks(text, fragments)
                if anchors:
                    print(
                        "weixin-deep: 标注：模型改动原文已纠正，"
                        f"按原文重新锚定 {len(anchors)} 处：{tag}",
                        flush=True,
                    )
                    return apply_deep_marks(text, anchors)
                cause = "标记片段与原文对不上"
        else:
            cause = "API 未返回内容"
        print(
            f"weixin-deep: 标注第 {attempt}/2 次未采用（{cause}）：{tag}",
            file=sys.stderr,
            flush=True,
        )
        reminder = (
            "\n\n（注意：这是第二次机会。除添加【】外，不得改动原文的任何字符，"
            "包括标点与空格；【】必须成对，总共不超过四处。）"
        )
    return text


def _deep_reject_cause(stripped: str, title: str) -> str:
    """Human-readable cause for a validate_deep_reason failure (bounds are
    re-checked inline so the diagnostic names the REAL cause)."""
    if not has_cjk(stripped):
        return "无中文"
    if len(stripped) < DEEP_REASON_MIN_CHARS:
        return f"字数 {len(stripped)} 不足 {DEEP_REASON_MIN_CHARS}"
    if len(stripped) > DEEP_REASON_MAX_CHARS:
        return f"字数 {len(stripped)} 超出上限 {DEEP_REASON_MAX_CHARS}"
    if stripped == title:
        return "与标题相同"
    if "http" in stripped:
        return "含 URL"
    return "含拒答话术"


def generate_deep_reason(item: dict, context: str, cfg: dict) -> str | None:
    title = str(item.get("title") or "").strip()
    if not title or not context:
        return None
    user_content = f"标题：{title}\n\n正文：\n{context}"
    reminder = ""
    for attempt in (1, 2):
        content = call_text_api(
            [
                {"role": "system", "content": DEEP_REASON_SYSTEM_PROMPT},
                {"role": "user", "content": user_content + reminder},
            ],
            cfg,
        )
        if not content:
            print("weixin-deep: 深度导读：API 未返回内容", file=sys.stderr, flush=True)
            return None
        stripped = str(content).strip()
        if validate_deep_reason(stripped, title):
            return add_deep_marks(stripped, cfg, title)
        cause = _deep_reject_cause(stripped, title)
        if attempt == 1:
            # Most rejections are stochastic overshoots (length, meta
            # commentary about the body); one reinforced retry recovers
            # them — mirrors the marking pass's single retry.
            print(
                f"weixin-deep: 深度导读初稿未过校验（{cause}），强化重试：{title[:24]}",
                file=sys.stderr,
                flush=True,
            )
            reminder = (
                "\n\n（严格遵守要求：只输出导读本身，不加任何解释、前缀或对"
                "正文质量的评价；字数控制在一百五十到三百五十之间。）"
            )
            continue
        # Silent rejects made failures impossible to diagnose; show what the
        # model returned and which bound it tripped, even after the retry.
        print(
            f"weixin-deep: 深度导读被校验拒绝（{cause}）：{stripped[:60]}…",
            file=sys.stderr,
            flush=True,
        )
        return None
    return None


def _fill_one_deep_reason(
    item: dict,
    cache: dict,
    cfg: dict,
    session: requests.Session | None,
    stats: dict,
    net_state: dict | None = None,
) -> str:
    """Attach ``weixin_deep_reason`` to a single item; returns the outcome
    label for the progress line (the former fill-loop body).

    Keyed: deep-cache hit > fresh deep generation > upstream reason > "".
    Keyless: upstream reason (any length) > deep cache > "".
    """
    title = str(item.get("title") or "")
    existing = existing_reason(item)

    key = cache_key(str(item.get("story_id") or ""), title)
    entry = cache.get("entries", {}).get(key)
    cached_reason = ""
    if isinstance(entry, dict) and entry.get("title_hash") == title_hash(title):
        cached_reason = str(entry.get("reason") or "").strip()

    if cfg["api_key"]:
        if cached_reason:
            item["weixin_deep_reason"] = cached_reason
            stats["cached"] += 1
            return "缓存"
        context = deep_reason_context(item, session, net_state)
        reason = generate_deep_reason(item, context, cfg) if context else None
        if reason:
            item["weixin_deep_reason"] = reason
            cache["entries"][key] = {
                "reason": reason,
                "title_hash": title_hash(title),
                "created_at": utcnow_iso(),
            }
            stats["generated"] += 1
            return "生成"
        item["weixin_deep_reason"] = existing or ""
        stats["skipped"] += 1
        # Distinguish the two silent-skip causes on the progress
        # line itself (validation/API details go to stderr).
        return "回退上游（无素材）" if not context else "回退上游（生成失败）"
    if existing:
        item["weixin_deep_reason"] = existing
        stats["reused"] += 1
        return "复用上游"
    if cached_reason:
        item["weixin_deep_reason"] = cached_reason
        stats["cached"] += 1
        return "缓存"
    item["weixin_deep_reason"] = ""
    stats["skipped"] += 1
    return "跳过"


def fill_deep_reasons(
    items: list[dict],
    cache: dict,
    cfg: dict,
    session: requests.Session | None,
    stats: dict,
    net_state: dict | None = None,
    max_items: int | None = None,
) -> list[dict]:
    """Fill deep guides candidate by candidate; return the kept issue items.

    Drop-and-backfill driver: a candidate
    whose final ``weixin_deep_reason`` ends up empty is dropped
    (``stats["dropped"]``, progress suffix 淘汰) and the next candidate
    moves up; stops early once ``max_items`` items are kept, so candidates
    past the cutoff cost no grounding fetches or API calls. Image fetching
    downstream then only runs for kept items. Safety net: if EVERY candidate
    ends up empty, fall back to the top ``max_items`` candidates rendered
    as-is, so the article never regresses to zero items.

    Progress is logged per item (flushed): a run that stalls is then always
    identifiable by its last printed line.
    """
    total = len(items)
    kept: list[dict] = []
    for i, item in enumerate(items, start=1):
        if max_items is not None and len(kept) >= max_items:
            break
        outcome = _fill_one_deep_reason(item, cache, cfg, session, stats, net_state)
        if not str(item.get("weixin_deep_reason") or "").strip():
            stats["dropped"] = stats.get("dropped", 0) + 1
            outcome += "→淘汰"
        else:
            kept.append(item)
        title = str(item.get("title") or "")
        story_id = str(item.get("story_id") or "")
        print(
            f"weixin-deep: [{i}/{total}] 导读：{outcome}｜{(title or story_id)[:24]}",
            flush=True,
        )
    if not kept:
        fallback = list(items)[:max_items] if max_items is not None else list(items)
        print(
            f"weixin-deep: 全部 {len(items)} 条候选均无导读素材，"
            f"退回未过滤的前 {len(fallback)} 条",
            file=sys.stderr,
        )
        return fallback
    return kept


# ---------------------------------------------------------------------------
# Article images
# ---------------------------------------------------------------------------

def bounded_get(
    session: requests.Session | None,
    url: str,
    timeout: float,
    max_bytes: int,
    accept=None,
) -> bytes | None:
    """GET under a hard wall-clock deadline and byte budget; None on reject.

    ``requests`` timeouts bound individual socket operations (connect, the
    gap between chunks), NOT the transfer as a whole: a slow trickle keeps a
    request open almost indefinitely, and a non-streaming read buffers the
    entire body into RAM before any size check. This helper streams instead
    and aborts once ``timeout`` seconds of wall clock or ``max_bytes`` are
    consumed. ``accept(response)`` may veto a response (headers are already
    available) before its body is downloaded.

    RequestException propagates: callers decide whether a failure is final
    (mark the fallback dead) or just "try the next candidate".
    """
    url = str(url or "").strip()
    if session is None or not url.startswith(("http://", "https://")):
        return None
    deadline = time.monotonic() + timeout
    response = session.get(url, timeout=timeout, stream=True)
    try:
        if response.status_code != 200:
            return None
        if accept is not None and not accept(response):
            return None
        buf = bytearray()
        for chunk in response.iter_content(chunk_size=65536):
            if time.monotonic() > deadline:
                return None
            if chunk:
                buf.extend(chunk)
                if len(buf) > max_bytes:
                    return None
        return bytes(buf)
    finally:
        try:
            response.close()
        except Exception:  # noqa: BLE001 - closing is best-effort
            pass


def fetch_jina_bytes(
    session: requests.Session | None,
    url: str,
    timeout: float,
    max_bytes: int,
    net_state: dict | None = None,
) -> bytes | None:
    """Reader fallback (JINA_READER_BASE_URL, default r.jina.ai) with a run-wide circuit breaker.

    The proxy needs no key but is rate-limited, and on networks that cannot
    reach it at all every fallback burns the full connect timeout (observed:
    15s each, dozens of times per run). After the first failure the rest of
    the run skips it outright; jina availability is effectively all-or-nothing
    per network, so one failure predicts the rest.
    """
    if session is None or (net_state is not None and net_state.get("jina_down")):
        return None
    try:
        payload = bounded_get(session, f"{JINA_READER_BASE_URL}/{url}", timeout, max_bytes)
    except requests.RequestException:
        payload = None
    if payload is None and net_state is not None and not net_state.get("jina_down"):
        net_state["jina_down"] = True
        print(
            "weixin-deep: reader 兜底本次不可用，后续条目跳过",
            file=sys.stderr,
            flush=True,
        )
    return payload


# When the publisher's terminal exports HTTPS_PROXY (needed for overseas
# originals), domestic hosts (aibase pages, the chinaz image CDN) ride the
# proxy too and can die there — observed: SSLError on upload.chinaz.com,
# whole-page failures on www.aibase.com. A second session that ignores the
# environment restores the true-direct route as a fallback. It is only
# touched after the env-routed attempt fails, so overseas behavior and
# proxy-less runs are unchanged.
_DIRECT_SESSION: requests.Session | None = None


def env_proxy_configured() -> bool:
    return any(
        os.environ.get(key)
        for key in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy")
    )


def direct_session() -> requests.Session:
    global _DIRECT_SESSION
    if _DIRECT_SESSION is None:
        _DIRECT_SESSION = create_session()
        _DIRECT_SESSION.trust_env = False
    return _DIRECT_SESSION


def bounded_get_alt_route(
    session: requests.Session | None,
    url: str,
    timeout: float,
    max_bytes: int,
    accept=None,
) -> bytes | None:
    """Retry ``bounded_get`` on the true-direct route; None when there is no
    alternate route (no env proxy) or it also fails."""
    if session is None or not env_proxy_configured():
        return None
    try:
        return bounded_get(direct_session(), url, timeout, max_bytes, accept=accept)
    except requests.RequestException:
        return None


def fetch_page_html(
    session: requests.Session | None,
    url: str,
    net_state: dict | None = None,
    timeout: float = PAGE_FETCH_TIMEOUT,
) -> tuple[str, str] | None:
    """(payload, kind) with kind in {"html", "markdown"}, or None.

    Direct fetch first; non-200, request errors and suspiciously short
    bodies (bot walls) fall back to the reader (r.jina.ai by default), whose markdown
    keeps image links as ``![alt](url)``.
    """
    url = str(url or "").strip()
    if not url.startswith(("http://", "https://")) or session is None:
        return None
    try:
        payload = bounded_get(session, url, timeout, PAGE_MAX_BYTES)
    except requests.RequestException:
        payload = None
    if payload is None:
        # The env proxy can break domestic hosts; try the direct route
        # before spending the reader fallback on it.
        payload = bounded_get_alt_route(session, url, timeout, PAGE_MAX_BYTES)
    if payload is not None:
        text = payload.decode("utf-8", errors="replace")
        if len(text) >= PAGE_MIN_HTML_CHARS:
            return text, "html"
    payload = fetch_jina_bytes(session, url, timeout, PAGE_MAX_BYTES, net_state)
    if payload is not None:
        text = payload.decode("utf-8", errors="replace")
        if text.strip():
            return text, "markdown"
    return None


def _parse_attrs(tag: str) -> dict[str, str]:
    attrs: dict[str, str] = {}
    for match in ATTR_RE.finditer(tag):
        name = match.group(1).lower()
        value = match.group(2).strip()
        if len(value) >= 2 and value[0] in "\"'" and value[-1] == value[0]:
            value = value[1:-1]
        attrs.setdefault(name, value.strip())
    return attrs


def _first_srcset_entry(value: str | None) -> str:
    first = str(value or "").split(",")[0].strip()
    # Drop the width/density descriptor ("800w", "2x").
    return first.split()[0] if first else ""


def _declared_px(value: str | None) -> int:
    digits = re.sub(r"\D", "", str(value or ""))
    if not digits:
        return 0
    try:
        return int(digits)
    except ValueError:
        return 0


def image_attrs_from_tag(tag: str) -> tuple[str, int, int]:
    """(url, declared_width, declared_height) for one ``<img>`` tag."""
    attrs = _parse_attrs(tag)
    url = ""
    for name in IMAGE_SRC_ATTRS:
        candidate = str(attrs.get(name) or "").strip()
        if candidate:
            url = candidate
            break
    if not url:
        for name in IMAGE_SRCSET_ATTRS:
            candidate = _first_srcset_entry(attrs.get(name))
            if candidate:
                url = candidate
                break
    return url, _declared_px(attrs.get("width")), _declared_px(attrs.get("height"))


def is_skippable_image_url(url: str) -> bool:
    lowered = str(url or "").lower()
    if any(marker in lowered for marker in IMAGE_URL_SKIP_MARKERS):
        return True
    try:
        path = urlparse(lowered).path
    except ValueError:
        path = lowered
    return any(path.endswith(ext) for ext in IMAGE_EXT_BLOCKLIST)


def absolutize_image_url(url: str, base_url: str) -> str:
    """Absolute http(s) URL or "" (data: URIs and fragments drop out here)."""
    url = str(url or "").strip()
    if url.startswith("//"):
        url = "https:" + url
    if not url.startswith(("http://", "https://")):
        if not base_url:
            return ""
        url = urljoin(base_url, url)
    if not url.startswith(("http://", "https://")):
        return ""
    return url


def scope_to_article_body(
    text: str, kind: str = "html", min_body_chars: int = 0
) -> str:
    """Restrict extraction to the article body; whole page when unlocatable.

    The longest <article> element wins (recommendation cards on some sites
    use <article> too, but the body is the longest one). Cutting at a
    recommendation heading is the fallback for pages without semantic
    markup and for reader-proxy markdown.

    ``min_body_chars`` (used by guide grounding) guards that assumption:
    when the longest <article>'s stripped text is shorter, the <article>
    elements are chrome, not the body — huggingface.co blog pages wrap
    ONLY sidebar/model cards in <article> while the post itself sits
    outside every one, so "longest wins" returned ~100 chars of card text
    and guides were grounded on it (or got no grounding at all). Such a
    page is then treated like one without semantic markup and falls back
    to the heading cut / whole page. Image scoping keeps the unguarded
    behavior so card thumbnails still never leak into the candidate list.
    """
    if kind == "html":
        articles = ARTICLE_TAG_RE.findall(str(text or ""))
        if articles:
            longest = max(articles, key=len)
            if not min_body_chars or len(strip_html_text(longest)) >= min_body_chars:
                return longest
        cut = REC_HEADING_HTML_RE.search(str(text or ""))
        if cut:
            return str(text)[: cut.start()]
    else:
        cut = REC_HEADING_MD_RE.search(str(text or ""))
        if cut:
            return str(text)[: cut.start()]
    return str(text or "")


def body_scope_degraded(body: str, kind: str, min_body_chars: int = 0) -> bool:
    """True when scope_to_article_body would return the WHOLE payload: no
    qualifying <article> (html) and no recommendation-heading cut. Such an
    html page is typically a JS shell whose stripped text is navigation
    chrome; deep_reason_context answers it with a reader-proxy second chance
    instead of grounding a guide on menus."""
    text = str(body or "")
    if kind == "html":
        articles = ARTICLE_TAG_RE.findall(text)
        if articles:
            longest = max(articles, key=len)
            if not min_body_chars or len(strip_html_text(longest)) >= min_body_chars:
                return False
        return REC_HEADING_HTML_RE.search(text) is None
    return REC_HEADING_MD_RE.search(text) is None


def is_wall_text(text: str) -> bool:
    """True when the fetched payload is an anti-bot wall, not an article.

    Walls are long enough to clear FULL_TEXT_MIN_CHARS (reader proxies pad
    them with their own Title:/URL Source:/Warning: headers), so the length
    check alone would pass them to the guide as "the body". Matching any
    marker treats the fetch as failed.
    """
    text = str(text or "")
    return any(marker in text for marker in WALL_TEXT_MARKERS)


def extract_image_candidates(
    payload: str, base_url: str, kind: str = "html"
) -> list[str]:
    """Ordered, de-duplicated content-image candidates from the article body.

    Candidates are drawn from the body scope only (see scope_to_article_body):
    recommendation-widget thumbnails outside it never enter the list, so a
    flaky body-image download degrades to "no image" instead of "wrong
    image". For reader-proxy markdown, ``![alt](url)`` links come first (the
    body images), then any embedded raw ``<img>`` tags; for plain HTML only
    the tags. Document order is preserved within each pass.
    """
    text = scope_to_article_body(payload, kind)
    raw: list[tuple[str, int, int]] = []
    if kind == "markdown":
        raw.extend((match.group(1), 0, 0) for match in MD_IMAGE_RE.finditer(text))
    for tag in IMG_TAG_RE.findall(text):
        url, width, height = image_attrs_from_tag(tag)
        if url:
            raw.append((url, width, height))

    candidates: list[str] = []
    seen: set[str] = set()
    for raw_url, width, height in raw:
        if len(candidates) >= MAX_IMAGE_CANDIDATES:
            break
        url = absolutize_image_url(raw_url, base_url)
        if not url or url in seen or is_skippable_image_url(url):
            continue
        if width and width < IMAGE_MIN_DIMENSION:
            continue
        if height and height < IMAGE_MIN_DIMENSION:
            continue
        seen.add(url)
        candidates.append(url)
    return candidates


def normalize_for_similarity(text: str) -> str:
    """Lowercased alphanumerics + CJK only, NFKC-normalized: the comparison
    alphabet for title matching. Everything else (punctuation, whitespace,
    HTML-entity leftovers) is noise."""
    lowered = unicodedata.normalize("NFKC", str(text or "")).lower()
    return re.sub(r"[^a-z0-9一-鿿]+", "", lowered)


def _char_bigrams(text: str) -> set[str]:
    if not text:
        return set()
    if len(text) == 1:
        return {text}
    return {text[i : i + 2] for i in range(len(text) - 1)}


def title_similarity(left: str, right: str) -> float:
    """Bigram overlap coefficient in 0..1: the intersection over the SMALLER
    side's bigram count. Size-agnostic by design — a card that re-headlines
    half of a long compound headline still scores high."""
    ga = _char_bigrams(normalize_for_similarity(left))
    gb = _char_bigrams(normalize_for_similarity(right))
    if not ga or not gb:
        return 0.0
    return len(ga & gb) / min(len(ga), len(gb))


def extract_page_heading(payload: str, kind: str = "html") -> str:
    """The page's own headline (<h1> for html, first #-line for reader
    markdown), used for recommendation-card matching.

    Deliberately NOT the brief title: card titles come in the page language
    (English on aibase) while brief titles are translated (Chinese), so a
    cross-language comparison could never reach the borrow threshold.
    """
    text = str(payload or "")
    if kind == "html":
        match = H1_TAG_RE.search(text)
        if not match:
            return ""
        return strip_html_text(match.group(1)).strip()
    match = re.search(r"^#\s+(.+?)\s*$", text, re.MULTILINE)
    return match.group(1).strip() if match else ""


def extract_rec_section(payload: str, kind: str = "html") -> str:
    """The page region OUTSIDE the article body, where recommendation
    widgets live. Empty when the body boundary is not identifiable —
    without it, content images and card thumbnails are not distinguishable."""
    if kind != "html":
        return ""
    text = str(payload or "")
    body = scope_to_article_body(text, "html")
    if not body or body == text:
        return ""
    return text.replace(body, "", 1)


def extract_rec_image_cards(
    payload: str, base_url: str, kind: str = "html"
) -> list[tuple[str, str]]:
    """(card title, image url) pairs from the recommendation widget.

    The card's ``<img alt>`` carries the recommended article's headline —
    the comparison text for borrowing. Images without a real alt,
    skippable URLs (logo/icon class) and small declared dimensions drop
    out, mirroring the body-candidate filters.
    """
    cards: list[tuple[str, str]] = []
    for tag in IMG_TAG_RE.findall(extract_rec_section(payload, kind)):
        # Unescape entities (&#x27; …) so matching and the progress log see
        # the clean card title.
        alt = strip_html_text(_parse_attrs(tag).get("alt") or "")
        if len(alt) < REC_CARD_MIN_ALT_CHARS:
            continue
        url, width, height = image_attrs_from_tag(tag)
        if not url:
            continue
        url = absolutize_image_url(url, base_url)
        if not url or is_skippable_image_url(url):
            continue
        if width and width < IMAGE_MIN_DIMENSION:
            continue
        if height and height < IMAGE_MIN_DIMENSION:
            continue
        cards.append((alt, url))
    return cards


def pick_rec_borrow_image(
    payload: str, base_url: str, kind: str = "html"
) -> tuple[str, str] | None:
    """(image url, card title) of a recommendation card clearly reporting
    the same story as this page; None otherwise.

    The match runs on the page's own headline and is double-gated
    (REC_BORROW_MIN_SCORE plus REC_BORROW_MIN_MARGIN over the runner-up),
    so both unrelated cards and "same product, different event" cards are
    rejected. Cards sharing one image URL collapse into a single contender,
    so a reused thumbnail cannot fabricate a runner-up.
    """
    heading = extract_page_heading(payload, kind)
    if not heading:
        return None
    by_url: dict[str, tuple[float, str]] = {}
    for alt, url in extract_rec_image_cards(payload, base_url, kind):
        score = title_similarity(heading, alt)
        kept = by_url.get(url)
        if kept is None or score > kept[0]:
            by_url[url] = (score, alt)
    if not by_url:
        return None
    ranked = sorted(
        ((score, alt, url) for url, (score, alt) in by_url.items()),
        key=lambda entry: (entry[0], entry[1], entry[2]),
        reverse=True,
    )
    best_score, best_alt, best_url = ranked[0]
    if best_score < REC_BORROW_MIN_SCORE:
        return None
    runner_up = ranked[1][0] if len(ranked) > 1 else 0.0
    if best_score - runner_up < REC_BORROW_MIN_MARGIN:
        return None
    return best_url, best_alt


def credit_domain(url: str) -> str:
    """Reader-facing credit: the article host, minus www, last two labels.

    The naive two-label heuristic mis-handles co.uk/com.cn style hosts;
    acceptable for an internal credit line (the article domain is credited,
    never the CDN the bytes came from).
    """
    try:
        host = urlparse(str(url or "")).netloc.lower()
    except ValueError:
        return ""
    host = host.split("@")[-1].split(":")[0]
    if host.startswith("www."):
        host = host[4:]
    labels = [label for label in host.split(".") if label]
    if len(labels) >= 2:
        return ".".join(labels[-2:])
    return host


def save_image_bytes(
    data: bytes, source_url: str, images_dir: Path, story_id: str
) -> str | None:
    """Persist one candidate as ``images/{story_id}.<ext>``; None = reject.

    With Pillow: decoded dimensions are enforced and wide images are shrunk
    to ≤1080px JPEG q82 (repo-growth bound). Without Pillow the raw bytes
    are kept verbatim and the decoded-size check is skipped (documented
    trade-off; the byte-size and URL filters still apply).
    """
    images_dir.mkdir(parents=True, exist_ok=True)
    try:
        from PIL import Image
    except ImportError:
        Image = None
    if Image is not None:
        try:
            img = Image.open(io.BytesIO(data))
            width, height = img.size
            if width < IMAGE_MIN_DIMENSION or height < IMAGE_MIN_DIMENSION:
                return None
            if width > IMAGE_MAX_WIDTH:
                new_h = max(1, int(height * IMAGE_MAX_WIDTH / width))
                img = img.resize((IMAGE_MAX_WIDTH, new_h))
            if img.mode not in ("RGB", "L"):
                # Composite onto white instead of a direct convert("RGB"):
                # palette PNGs with byte transparency warn and drop their
                # transparent pixels to arbitrary palette colors (observed on
                # openai.com images).
                rgba = img.convert("RGBA")
                background = Image.new("RGB", rgba.size, (255, 255, 255))
                background.paste(rgba, mask=rgba.getchannel("A"))
                img = background
            img.save(
                images_dir / f"{story_id}.jpg",
                format="JPEG",
                quality=IMAGE_JPEG_QUALITY,
            )
            return f"images/{story_id}.jpg"
        except Exception:  # noqa: BLE001 - corrupted/spoofed bytes: next candidate
            return None
    # Pillow-less fallback: keep the original bytes and extension.
    path_lower = urlparse(str(source_url or "")).path.lower()
    ext = ".jpg"
    for candidate_ext in (".jpeg", ".jpg", ".png", ".webp", ".gif"):
        if path_lower.endswith(candidate_ext):
            ext = candidate_ext
            break
    try:
        (images_dir / f"{story_id}{ext}").write_bytes(data)
    except OSError:
        return None
    return f"images/{story_id}{ext}"


def _looks_like_image_response(response) -> bool:
    content_type = str(response.headers.get("Content-Type") or "").lower()
    return content_type.startswith("image/") and "svg" not in content_type


def download_item_image(
    session: requests.Session | None,
    candidates: list[str],
    images_dir: Path,
    story_id: str,
    article_url: str,
) -> tuple[str, str] | None:
    """First candidate that downloads as a real image; ("images/…", credit)
    or None. Never raises — every failure just means "no image today".

    Downloads are bounded (wall-clock deadline + byte budget, budget enforced
    mid-stream), so one hostile candidate can stall neither the run nor RAM.
    """
    if session is None or not candidates or not story_id:
        return None
    # Drop stale outputs of this story (previous runs / other extensions)
    # before today's pick lands, so no dead file survives a re-run.
    try:
        for stale in images_dir.glob(f"{story_id}.*"):
            stale.unlink()
    except OSError:
        pass
    credit = credit_domain(article_url)
    for candidate in candidates:
        try:
            data = bounded_get(
                session,
                candidate,
                IMAGE_DOWNLOAD_TIMEOUT,
                IMAGE_MAX_BYTES,
                accept=_looks_like_image_response,
            )
        except requests.RequestException:
            data = None
        if data is None or len(data) < IMAGE_MIN_BYTES:
            # Domestic CDNs can die on the env-proxy route (SSL interception);
            # give the same candidate one true-direct attempt before moving on.
            data = bounded_get_alt_route(
                session,
                candidate,
                IMAGE_DOWNLOAD_TIMEOUT,
                IMAGE_MAX_BYTES,
                accept=_looks_like_image_response,
            )
        if data is None or len(data) < IMAGE_MIN_BYTES:
            continue
        saved = save_image_bytes(data, candidate, images_dir, story_id)
        if saved:
            return saved, credit
    return None


def fill_deep_images(
    items: list[dict],
    session: requests.Session | None,
    output_dir: Path,
    net_state: dict | None = None,
) -> tuple[int, int]:
    """Fetch one article image per item; returns (found, missed).

    Misses (bot walls, text-only articles, flaky proxies) simply leave the
    item image-less — that is the agreed product behavior, not an error.
    """
    images_dir = Path(output_dir) / "images"
    found = missed = 0
    written: set[str] = set()
    total = len(items)
    print(f"weixin-deep: 开始抓取原文插图（共 {total} 条）…", flush=True)
    for i, item in enumerate(items, start=1):
        article_url = item_original_url(item)
        payload = (
            fetch_page_html(session, article_url, net_state) if article_url else None
        )
        candidates: list[str] = []
        borrowed_alt = ""
        if payload is not None:
            body, kind = payload
            candidates = extract_image_candidates(body, article_url, kind)
            if not candidates:
                # No body image: when the recommendation widget carries a
                # card that clearly reports the same story, borrow ITS image
                # — a same-topic illustration beats none. The match is
                # double-gated; near-misses keep the item image-less.
                borrow = pick_rec_borrow_image(body, article_url, kind)
                if borrow:
                    borrow_url, borrowed_alt = borrow
                    candidates = [borrow_url]
        story_id = str(item.get("story_id") or "").strip() or title_hash(
            str(item.get("title") or "")
        )
        result = download_item_image(session, candidates, images_dir, story_id, article_url)
        if result:
            rel_path, credit = result
            item["deep_image"] = rel_path
            item["deep_image_credit"] = credit
            if borrowed_alt:
                item["deep_image_borrowed"] = True
            written.add(Path(rel_path).name)
            found += 1
            note = f"，推荐区同题报道「{borrowed_alt[:30]}」" if borrowed_alt else ""
            print(
                f"weixin-deep: [{i}/{total}] 插图：{rel_path}（图源：{credit}{note}）",
                flush=True,
            )
        else:
            missed += 1
            print(
                f"weixin-deep: [{i}/{total}] 插图：无图（原文无可用图片或抓取失败）",
                flush=True,
            )
    # Prune files no longer referenced by today's selection (the images/
    # directory is script-owned; nothing else writes into it).
    if images_dir.is_dir():
        for path in images_dir.iterdir():
            if path.is_file() and path.name not in written:
                try:
                    path.unlink()
                except OSError:
                    pass
    return found, missed


def resolve_deep_cover(
    items: list[dict], output_dir: Path
) -> tuple[bytes, str, str] | None:
    """(bytes, "cover.jpg", rel image path) for the deep cover; None if no
    item carries an illustration (caller falls back to ``resolve_cover``).

    The deep cover IS a real article image: the top story's illustration
    first, else the next item in selection order (score-descending) that
    has one, center-cropped to the shared 2.35:1 cover format. Readers see
    the day's headline art, not a generated scene or the static fallback.
    """
    for item in items:
        rel = str(item.get("deep_image") or "").strip()
        if not rel:
            continue
        try:
            data = (Path(output_dir) / rel).read_bytes()
        except OSError:
            continue
        cropped = crop_cover(data)
        if cropped is None:
            continue
        return cropped, "cover.jpg", rel
    return None


# ---------------------------------------------------------------------------
# HTML rendering (inline styles only; <img> allowed in THIS variant)
# ---------------------------------------------------------------------------

def render_deep_reason_html(reason: str, accent_color: str) -> str:
    """Render a guide with 【】 marks as bold spans in the section color.

    Any markup problem (or no marks at all) falls back to plain escaped
    text: highlighting is decoration and must never break the guide.
    """
    parsed = parse_deep_marks(reason)
    if parsed is None:
        return esc(strip_deep_marks(reason))
    plain, marks = parsed
    if not marks or not deep_marks_usable(plain, marks):
        return esc(plain)
    parts: list[str] = []
    pos = 0
    for start, end in marks:
        parts.append(esc(plain[pos:start]))
        parts.append(
            f'<strong style="color:{accent_color};">{esc(plain[start:end])}</strong>'
        )
        pos = end
    parts.append(esc(plain[pos:]))
    return "".join(parts)


def render_deep_item_html(item: dict, idx: int, accent_color: str) -> str:
    title = strip_english_tail(str(item.get("title") or "").strip())
    reason = str(item.get("weixin_deep_reason") or "").strip()
    sources = [s for s in (item.get("sources") or []) if isinstance(s, dict)]
    source_count = item.get("source_count")
    try:
        source_count = int(source_count)
    except (TypeError, ValueError):
        source_count = len(sources) or 1
    category = str(item.get("category") or "").strip()
    source_name = item_display_source(item)

    parts = ['<section style="margin:30px 0 0;padding:0;">']
    parts.append(
        '<p style="margin:0;font-size:16px;line-height:1.55;font-weight:bold;'
        f'color:#1f1f1f;">{circled_number(idx + 1)} {esc(title)}</p>'
    )
    if reason:
        parts.append(
            '<p style="margin:10px 0 0;font-size:15px;line-height:1.8;'
            f'color:#555555;">{render_deep_reason_html(reason, accent_color)}</p>'
        )
    image_path = str(item.get("deep_image") or "").strip()
    if image_path:
        credit = str(item.get("deep_image_credit") or "").strip()
        parts.append('<section style="margin:12px 0 0;">')
        parts.append(
            f'<img src="{esc(image_path)}" alt="" '
            f'style="width:{DEEP_IMAGE_WIDTH_PERCENT}%;display:block;'
            'margin:0 auto;">'
        )
        if credit:
            parts.append(
                '<p style="margin:6px 0 0;font-size:12px;line-height:1.5;'
                f'color:#b2b2b2;text-align:center;">图源：{esc(credit)}</p>'
            )
        parts.append("</section>")
    origin_names, origin_count, repost_names = split_origin_sources(item)
    # Channel lists moved into the meta line; this grey line only survives
    # as the title fallback for stories without a title.
    if len(sources) > 1 and not title:
        line_title = ""
        for src in sources:
            line_title = strip_english_tail(str(src.get("title") or "").strip())
            if line_title:
                break
        if line_title:
            parts.append(
                '<p style="margin:6px 0 0;font-size:13px;line-height:1.6;'
                f'color:#999999;">{esc(line_title)}</p>'
            )
    # Meta line: origins (one per distinct canonical
    # URL) are credited as 来源, pipeline entries repeating an
    # already-credited URL as 转载 ("官方更新 · OpenAI News, NewsNow ·
    # 2 个来源 · Buzzing · 1 个转载"); a single origin collapses to
    # "官方更新 · Qwen Blog · 1 个来源 · Buzzing, Info Flow · 2 个转载",
    # and without reposts the tail is dropped.
    category_zh = CATEGORY_LABEL_ZH.get(category, category)
    if origin_names:
        meta_bits = [
            bit
            for bit in (
                category_zh,
                ", ".join(origin_names),
                f"{origin_count} 个来源",
            )
            if bit
        ]
        if repost_names:
            meta_bits.append(f"{', '.join(repost_names)} · {len(repost_names)} 个转载")
    else:
        meta_bits = [bit for bit in (category_zh, source_name, f"{source_count} 个来源") if bit]
    if meta_bits:
        meta = " · ".join(esc(bit) for bit in meta_bits)
        parts.append(
            f'<p style="margin:8px 0 0;font-size:12px;color:#b2b2b2;">{meta}</p>'
        )
    link = item_original_url(item)
    if link:
        parts.append(
            '<p style="margin:8px 0 0;font-size:13px;line-height:1.6;'
            f'color:#576b95;word-break:break-all;">原文：{esc(link)}</p>'
        )
    parts.append("</section>")
    return "\n".join(parts)


def render_deep_group_section(category: str, items: list[dict]) -> str:
    """Section frame (centered color title + enclosing box) around the
    deep item renderer."""
    label = CATEGORY_LABEL_ZH.get(category, category)
    style = CATEGORY_STYLES.get(category, DEFAULT_STYLE)
    parts = [
        '<section style="margin:34px 0 0;">',
        (
            '<p style="margin:0 0 14px;text-align:center;font-size:17px;'
            f'font-weight:bold;letter-spacing:2px;color:{style["color"]};">'
            f'{esc(label)}</p>'
        ),
        (
            f'<section style="border:1px solid {style["border"]};'
            f'border-radius:10px;background-color:{style["background"]};'
            'padding:2px 14px 14px;">'
        ),
    ]
    # Per-section numbering restarts at ①.
    for idx, item in enumerate(items):
        parts.append(render_deep_item_html(item, idx, style["color"]))
    parts.append("</section>")
    parts.append("</section>")
    return "\n".join(parts)


def render_deep_article_html(
    items: list[dict],
    *,
    title: str,
    brand: str,
    issue_label: str,
    issue_range: str,
) -> str:
    sections_html = "\n".join(
        render_deep_group_section(category, group)
        for category, group in group_items(items)
    )
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
<p style="margin:3px 0 0;font-size:13px;color:#999999;">{esc(issue_label)} · 每周 AI 精读</p>
</section>

<p style="margin:0 0 4px;font-size:18px;font-weight:bold;line-height:1.5;color:#111111;">{esc(title)}</p>

{sections_html}

<section style="margin-top:30px;border-top:1px dashed #d9d9d9;padding-top:16px;">
<p style="margin:0;font-size:13px;line-height:1.7;color:#999999;">以上内容由 {esc(brand)} 自动整理自{esc(issue_range)}的公开信源，图片来自原文页面，原文出处链接见每条信息下方。</p>
</section>

</section>
</body>
</html>
"""


# ---------------------------------------------------------------------------
# Title / digest (fixed templates; never LLM-dependent)
# ---------------------------------------------------------------------------

def issue_range_label(brief: dict, now_cn: datetime) -> str:
    """Publish-window label like 「8月22日-8月28日」, derived from the actual
    brief window: the weekly pool spans its lookback days, the daily
    fallback collapses to the single issue day."""
    try:
        window_hours = int(brief.get("window_hours"))
    except (TypeError, ValueError):
        window_hours = _weekly_lookback_days() * 24
    days = max(1, round(window_hours / 24))
    end = f"{now_cn.month}月{now_cn.day}日"
    if days <= 1:
        return end
    start = now_cn - timedelta(days=days - 1)
    return f"{start.month}月{start.day}日-{end}"


def deep_title(brand: str, range_label: str, count: int) -> str:
    return f"{brand} · {range_label}｜本周精读{count}条"


def make_deep_digest(brand: str, count: int, headline: str, issue_label: str) -> str:
    digest = f"{brand}{issue_label}精读 {count} 条 AI 要闻：{headline}。更多条目见「阅读原文」。"
    return digest[:DIGEST_MAX_CHARS]


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="WeChat weekly article generator (deep-read / 精读版 layout)"
    )
    parser.add_argument("--data-dir", default="data", help="data directory")
    parser.add_argument(
        "--output-dir",
        default=DEFAULT_OUTPUT_DIR,
        help=f"output directory (default {DEFAULT_OUTPUT_DIR})",
    )
    parser.add_argument("--assets-dir", default="assets", help="assets directory")
    parser.add_argument(
        "--max-items",
        type=int,
        default=None,
        help="max items (defaults to WEIXIN_DEEP_MAX_ITEMS env or 20)",
    )
    parser.add_argument(
        "--no-images",
        action="store_true",
        help="skip original-article image fetching (fast/offline smoke runs)",
    )
    parser.add_argument(
        "--regenerate",
        default="",
        help=(
            "comma-separated display numbers (3 or ③), story ids or title "
            "fragments (中英文均可，忽略大小写): matching cached guides are "
            "dropped before the run so they get re-rolled; 'all' re-rolls "
            "every picked item"
        ),
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="run without writing any files"
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    started = time.monotonic()
    args = parse_args(argv)

    if os.environ.get("WEIXIN_ENABLED", "").strip() == "0":
        print("weixin-deep: disabled via WEIXIN_ENABLED=0, nothing to do")
        return 0

    cfg = build_config(args)
    cfg["max_items"] = resolve_deep_max_items(args)
    data_dir = Path(args.data_dir)
    output_dir = Path(args.output_dir)
    assets_dir = Path(args.assets_dir)

    # Over-selected pool: max_items + backup candidates, so items whose deep
    # guide ends up empty can be dropped and backfilled (fill_deep_reasons)
    # without the issue shrinking below max_items.
    pool_size = cfg["max_items"] + deep_pool_extra()
    brief = load_push_brief(data_dir, cfg["max_items"], pool_size=pool_size)
    if brief is None:
        print(
            f"weixin-deep: no usable brief under {data_dir} "
            "(weekly pool and daily-brief.json both unavailable), nothing to do"
        )
        return 0

    candidates = select_items(brief, pool_size)
    if not candidates:
        print("weixin-deep: brief has no items, nothing to do")
        return 0

    now_cn = datetime.now(TZ_CN)
    issue_date = now_cn.strftime("%Y-%m-%d")
    issue_label = f"{now_cn.month}月{now_cn.day}日 {WEEKDAY_CN[now_cn.weekday()]}"
    range_label = issue_range_label(brief, now_cn)

    # Images and grounding fetches do not need the Qwen key, so the session
    # exists unconditionally.
    session = create_session()
    # Run-wide network state (currently the reader circuit breaker).
    net_state: dict = {}
    # Deep-guide cache lives next to the article output (21-day TTL).
    cache_path = output_dir / "reason-cache.json"
    cache = load_deep_cache(cache_path)
    stats = {"reused": 0, "cached": 0, "generated": 0, "skipped": 0, "dropped": 0}

    # Translate leftover pure-English titles before deep guides are written,
    # so guides, the cover headline and the rendered titles all use Chinese.
    # Runs over the full candidate pool: translations are cached, so promoted
    # backups already have Chinese titles.
    ensure_zh_titles(candidates, cache, cfg, stats)

    # Re-roll cached guides named via --regenerate. Runs AFTER title
    # translation so fragments can match the Chinese display titles the
    # maintainer reads in the article (the pre-translation English title is
    # kept on the item and matches too).
    specs = parse_regenerate_specs(args.regenerate)
    if specs:
        wanted, unmatched = match_regenerate(candidates, specs)
        dropped = drop_cache_entries(cache, wanted)
        report_regenerate("weixin-deep", candidates, wanted, unmatched, dropped)

    items = fill_deep_reasons(
        candidates, cache, cfg, session, stats, net_state,
        max_items=cfg["max_items"],
    )

    images_found = images_missed = 0
    if not args.dry_run and not args.no_images:
        images_found, images_missed = fill_deep_images(items, session, output_dir, net_state)

    headline = strip_english_tail(str(items[0].get("title") or "").strip())
    title = deep_title(cfg["brand"], range_label, len(items))
    digest = make_deep_digest(cfg["brand"], len(items), headline, issue_label)

    item_cover = resolve_deep_cover(items, output_dir)
    if item_cover is not None:
        cover_bytes, cover_filename, cover_rel = item_cover
        cover_mode, cover_scene = "item", False
        print(f"weixin-deep: 封面：采用条目插图 {cover_rel}", flush=True)
    else:
        cover_bytes, cover_filename, cover_mode, cover_scene = resolve_cover(
            headline, cfg, session, assets_dir
        )

    groups = group_items(items)
    html_text = render_deep_article_html(
        items,
        title=title,
        brand=cfg["brand"],
        issue_label=issue_label,
        issue_range=range_label,
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
    meta["layout"] = "deep"
    meta["sections"] = [
        {
            "category": category,
            "label": CATEGORY_LABEL_ZH.get(category, category),
            "count": len(group),
        }
        for category, group in groups
    ]
    # Per-story image map, ready for future API draft delivery. "borrowed"
    # marks images taken from a same-story recommendation card instead of
    # the article body (the only place illustration provenance matters).
    meta["images"] = {
        str(item.get("story_id") or ""): {
            "file": str(item.get("deep_image") or ""),
            "credit": str(item.get("deep_image_credit") or ""),
            "borrowed": bool(item.get("deep_image_borrowed")),
        }
        for item in items
        if item.get("deep_image")
    }

    if not args.dry_run:
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / "index.html").write_text(html_text, encoding="utf-8")
        (output_dir / "meta.json").write_text(
            json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        # Publish helper info as a separate text file: the block used to be
        # rendered at the page bottom and kept getting pasted into the editor.
        (output_dir / "publish-info.txt").write_text(
            f"标题：{title}\n摘要：{digest}\n阅读原文：{cfg['radar_url']}\n",
            encoding="utf-8",
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
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        save_cache(cache_path, cache, datetime.now(timezone.utc))

    print(
        "weixin-deep: items={items} sections={sections} "
        "reasons reused={reused} cached={cached} generated={generated} "
        "skipped={skipped} dropped={dropped} "
        "titles translated={titles_translated} "
        "cached={titles_cached} kept_english={titles_skipped} "
        "images found={found} missed={missed} "
        "cover_mode={cover_mode} cover_scene={cover_scene} "
        "elapsed={elapsed:.0f}s dry_run={dry_run}".format(
            items=len(items),
            sections=",".join(
                f"{CATEGORY_LABEL_ZH.get(cat, cat)}×{len(group)}"
                for cat, group in groups
            ),
            reused=stats["reused"],
            cached=stats["cached"],
            generated=stats["generated"],
            skipped=stats["skipped"],
            dropped=stats.get("dropped", 0),
            titles_translated=stats.get("titles_translated", 0),
            titles_cached=stats.get("titles_cached", 0),
            titles_skipped=stats.get("titles_skipped", 0),
            found=images_found,
            missed=images_missed,
            cover_mode=cover_mode,
            cover_scene=1 if cover_scene else 0,
            elapsed=time.monotonic() - started,
            dry_run=1 if args.dry_run else 0,
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
