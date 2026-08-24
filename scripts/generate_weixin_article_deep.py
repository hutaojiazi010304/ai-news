"""Deep-read (精读版) variant of the WeChat daily article generator.

Third layout variant beside ``generate_weixin_article.py`` (1.0, flat list)
and ``generate_weixin_article_grouped.py`` (2.0, grouped boxes). It keeps the
2.0 grouped layout but upgrades the content for close reading:

- Selection: the top 10 stories by ``peak_score`` only (default; override via
  ``--max-items`` or ``WEIXIN_DEEP_MAX_ITEMS`` — deliberately NOT
  ``WEIXIN_MAX_ITEMS``, so 1.0/2.0 stay untouched). Empty sections are
  skipped, exactly like 2.0.
- Guides: longer, written in a relayed-news style ("据 X 报道" attribution,
  facts and numbers only, no fabrication). Stored in an INDEPENDENT cache
  (``weixin-deep/reason-cache.json``, ``DEEP_CACHE_VERSION``): the shared
  1.0/2.0 cache keys only on story_id+title, so reusing it would hit the
  stale short guides forever and the new style would never take effect.
- Images: each item gets ONE real illustration pulled from its original
  article page at publish time (direct fetch, r.jina.ai fallback). Articles
  without a usable image simply render without one; no AI-generated filler.
  Images are saved under ``images/`` (committed, served by Pages) with a
  「图源：domain」credit line for internal redistribution.
- Title/digest: fixed templates ("今日精读N条"), no LLM, as in 1.0/2.0.
- Cover: reused from the main variant for the same issue date (2.0 logic),
  otherwise the identical 1.0 ``resolve_cover`` pipeline.

Design constraints mirror 1.0/2.0: standalone JSON-file interface, exit 0 on
every graceful path, keyless runs degrade (upstream reasons, static cover,
no images only when the network refuses them — image fetching itself needs
no API key, so the session is created unconditionally).

Output (default ``weixin-deep/``):

- ``index.html``        inline-style article (this variant MAY use ``<img>``:
                        the preview page is canonical; pasting into an editor
                        strips external images and they are re-inserted
                        manually for the internal service account)
- ``meta.json``         layout="deep", sections census, per-story images map
- ``cover.jpg|.png``    2.35:1 cover (reused or regenerated)
- ``reason-cache.json`` deep-guide cache (21-day TTL, independent)
- ``images/``           one downloaded article image per story that has one
"""

from __future__ import annotations

import argparse
import io
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests

try:  # imported as part of the repo package (tests)
    from scripts.generate_weixin_article import (
        CATEGORY_LABEL_ZH,
        DIGEST_MAX_CHARS,
        FULL_TEXT_MAX_CHARS,
        REFUSAL_MARKERS,
        TZ_CN,
        WEEKDAY_CN,
        build_config,
        build_meta,
        cache_key,
        call_text_api,
        circled_number,
        create_session,
        esc,
        existing_reason,
        fetch_full_text,
        has_cjk,
        item_original_url,
        load_brief,
        resolve_cover,
        save_cache,
        select_items,
        strip_english_tail,
        summary_grounding,
        title_hash,
        utcnow_iso,
    )
    from scripts.generate_weixin_article_grouped import (
        CATEGORY_STYLES,
        DEFAULT_STYLE,
        group_items,
        reuse_cover,
    )
except ImportError:  # run directly as a script
    from generate_weixin_article import (
        CATEGORY_LABEL_ZH,
        DIGEST_MAX_CHARS,
        FULL_TEXT_MAX_CHARS,
        REFUSAL_MARKERS,
        TZ_CN,
        WEEKDAY_CN,
        build_config,
        build_meta,
        cache_key,
        call_text_api,
        circled_number,
        create_session,
        esc,
        existing_reason,
        fetch_full_text,
        has_cjk,
        item_original_url,
        load_brief,
        resolve_cover,
        save_cache,
        select_items,
        strip_english_tail,
        summary_grounding,
        title_hash,
        utcnow_iso,
    )
    from generate_weixin_article_grouped import (
        CATEGORY_STYLES,
        DEFAULT_STYLE,
        group_items,
        reuse_cover,
    )

DEFAULT_OUTPUT_DIR = "weixin-deep"
DEFAULT_MAIN_OUTPUT_DIR = "weixin"
DEFAULT_DEEP_MAX_ITEMS = 10

# Independent cache version: bumped only when the deep prompt/bounds change.
# 1.0 bumping its own CACHE_VERSION never invalidates this cache and vice
# versa (load_deep_cache rejects mismatched versions, forcing regeneration).
DEEP_CACHE_VERSION = 1

# Deep guides are longer than 1.0's 40-260 window; the bounds only reject
# degenerate output since the prompt itself targets 150-350 chars.
DEEP_REASON_MIN_CHARS = 80
DEEP_REASON_MAX_CHARS = 450
# A persisted summary must be this long before it alone can ground a
# 150-350-char report; anything thinner falls back to a full-text fetch
# (deliberate inversion of 1.0's 20-char summary-first policy — only 10
# items run locally, so per-item fetching is affordable).
DEEP_SUMMARY_MIN_GROUNDING_CHARS = 120

PAGE_FETCH_TIMEOUT = 15.0
IMAGE_DOWNLOAD_TIMEOUT = 30.0
# Pages under 300 chars are almost certainly bot walls/redirect stubs;
# fall back to the reader proxy for those too.
PAGE_MIN_HTML_CHARS = 300
IMAGE_MAX_BYTES = 2_500_000
# Tracking pixels compress to a few hundred bytes.
IMAGE_MIN_BYTES = 512
# Decoded or declared dimensions below this are UI chrome, not content art.
IMAGE_MIN_DIMENSION = 120
# Keep committed images phone-friendly and the repo growth bounded.
IMAGE_MAX_WIDTH = 1080
IMAGE_JPEG_QUALITY = 82
MAX_IMAGE_CANDIDATES = 10

IMG_TAG_RE = re.compile(r"<img\b[^>]*>", re.IGNORECASE)
ATTR_RE = re.compile(r"([\w-]+)\s*=\s*(\"[^\"]*\"|'[^']*'|[^\s>]+)", re.IGNORECASE)
MD_IMAGE_RE = re.compile(r"!\[[^\]]*\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")

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

DEEP_REASON_SYSTEM_PROMPT = (
    "你是科技新闻编辑，负责把一篇具体文章的核心内容转述成一段「精读导读」，"
    "用于微信公众号每日 AI 精选的深度版（精读版）推文。"
    "用转述式报道的口吻写：第一句以「据 {信源} 报道」开头，信源名取自用户提供的"
    "「信源」一行，原样使用，不得改写、翻译或替换成母公司名称；"
    "未提供信源行时改用「据相关报道」，不得编造信源名。"
    "之后用自己的话复述原文最关键的内容：做了什么、数字是多少、结论是什么；"
    "优先保留正文中的具体事实与数字。"
    "只复述正文中明确出现的信息，不得编造、推断或补充任何正文之外的事实、"
    "数字、日期与意义。"
    "不要添加「展示了……」「标志着……」「为……开启了新篇章」之类的意义话术；"
    "除非原文本身就是评论，才可以转述其观点，并注明是原文观点。"
    "字数控制在一百五十到三百五十之间，信息密度优先，不要为凑字数注水。"
    "公司名、产品名一律只用原文中出现的形式（通常是英文），"
    "绝不要附加中文翻译、音译或括号注释，哪怕你自认为知道官方中文名；"
    "人名按国籍写：华人用中文名（如周鸿祎），拿不准时保留英文，同样不得自行音译。"
    "若提供的正文显然不是文章正文（导航、目录、验证页等），就把标题本身包含的"
    "信息整理成转述，不编造标题之外的细节，也不要在导读里解释正文缺失或无法转述。"
    "只输出这段导读本身，不加引号，不加任何解释或前缀。"
)


# ---------------------------------------------------------------------------
# Selection / cache
# ---------------------------------------------------------------------------

def resolve_deep_max_items(args: argparse.Namespace) -> int:
    """--max-items > WEIXIN_DEEP_MAX_ITEMS > 10.

    ``WEIXIN_MAX_ITEMS`` is deliberately ignored: 1.0/2.0 keep their own
    20-item default, this variant keeps its own 10-item default.
    """
    if args.max_items is not None:
        return max(1, args.max_items)
    try:
        value = int(os.environ.get("WEIXIN_DEEP_MAX_ITEMS") or DEFAULT_DEEP_MAX_ITEMS)
    except ValueError:
        value = DEFAULT_DEEP_MAX_ITEMS
    return max(1, value)


def load_deep_cache(path: Path) -> dict:
    """Same shape as 1.0's load_cache but versioned independently."""
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


def deep_reason_context(item: dict, session: requests.Session | None) -> str | None:
    """Grounding for deep guides: long summary first, full-text fetch second.

    Unlike 1.0 (any summary ≥20 chars is enough for a short guide), a deep
    150-350-char report needs real substance: summaries under 120 chars
    degrade to a live fetch of the article, whose text is usually richer.
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
        if grounding and len(grounding) >= DEEP_SUMMARY_MIN_GROUNDING_CHARS:
            return grounding[:FULL_TEXT_MAX_CHARS]
    url = str(item.get("primary_url") or item.get("url") or "").strip()
    if url and session is not None:
        return fetch_full_text(session, url, timeout=PAGE_FETCH_TIMEOUT)
    return None


def _item_source_name(item: dict) -> str:
    source_name = str(item.get("source_name") or "").strip()
    if source_name:
        return source_name
    for src in item.get("sources") or []:
        if isinstance(src, dict):
            name = str(src.get("source_name") or "").strip()
            if name:
                return name
    return ""


def generate_deep_reason(item: dict, context: str, cfg: dict) -> str | None:
    title = str(item.get("title") or "").strip()
    if not title or not context:
        return None
    source_name = _item_source_name(item)
    user_content = f"标题：{title}\n"
    if source_name:
        user_content += f"\n信源：{source_name}\n"
    user_content += f"\n正文：\n{context}"
    content = call_text_api(
        [
            {"role": "system", "content": DEEP_REASON_SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ],
        cfg,
    )
    if content and validate_deep_reason(content, title):
        return content
    return None


def fill_deep_reasons(
    items: list[dict],
    cache: dict,
    cfg: dict,
    session: requests.Session | None,
    stats: dict,
) -> None:
    """Attach ``weixin_deep_reason`` to each item.

    Keyed: deep-cache hit > fresh deep generation > upstream reason > "".
    Keyless: upstream reason (any length) > deep cache > "". The cache is
    the deep variant's own file — never the shared 1.0/2.0 one.
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
                item["weixin_deep_reason"] = cached_reason
                stats["cached"] += 1
                continue
            context = deep_reason_context(item, session)
            reason = generate_deep_reason(item, context, cfg) if context else None
            if reason:
                item["weixin_deep_reason"] = reason
                cache["entries"][key] = {
                    "reason": reason,
                    "title_hash": title_hash(title),
                    "created_at": utcnow_iso(),
                }
                stats["generated"] += 1
            else:
                item["weixin_deep_reason"] = existing or ""
                stats["skipped"] += 1
            continue

        if existing:
            item["weixin_deep_reason"] = existing
            stats["reused"] += 1
            continue
        if cached_reason:
            item["weixin_deep_reason"] = cached_reason
            stats["cached"] += 1
            continue
        item["weixin_deep_reason"] = ""
        stats["skipped"] += 1


# ---------------------------------------------------------------------------
# Article images
# ---------------------------------------------------------------------------

def fetch_page_html(
    session: requests.Session | None, url: str, timeout: float = PAGE_FETCH_TIMEOUT
) -> tuple[str, str] | None:
    """(payload, kind) with kind in {"html", "markdown"}, or None.

    Direct fetch first; non-200, request errors and suspiciously short
    bodies (bot walls) fall back to the r.jina.ai reader, whose markdown
    keeps image links as ``![alt](url)``.
    """
    url = str(url or "").strip()
    if not url.startswith(("http://", "https://")) or session is None:
        return None
    try:
        response = session.get(url, timeout=timeout)
        if response.status_code == 200:
            text = str(response.text or "")
            if len(text) >= PAGE_MIN_HTML_CHARS:
                return text, "html"
    except requests.RequestException:
        pass
    try:
        response = session.get(f"https://r.jina.ai/{url}", timeout=timeout)
        if response.status_code == 200 and str(response.text or "").strip():
            return str(response.text), "markdown"
    except requests.RequestException:
        pass
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


def extract_image_candidates(
    payload: str, base_url: str, kind: str = "html"
) -> list[str]:
    """Ordered, de-duplicated content-image candidates from a page body.

    For reader-proxy markdown, ``![alt](url)`` links come first (the body
    images), then any embedded raw ``<img>`` tags; for plain HTML only the
    tags. Document order is preserved within each pass.
    """
    text = str(payload or "")
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
                img = img.convert("RGB")
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


def download_item_image(
    session: requests.Session | None,
    candidates: list[str],
    images_dir: Path,
    story_id: str,
    article_url: str,
) -> tuple[str, str] | None:
    """First candidate that downloads as a real image; ("images/…", credit)
    or None. Never raises — every failure just means "no image today"."""
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
            response = session.get(candidate, timeout=IMAGE_DOWNLOAD_TIMEOUT)
        except requests.RequestException:
            continue
        if response.status_code != 200:
            continue
        content_type = str(response.headers.get("Content-Type") or "").lower()
        if not content_type.startswith("image/") or "svg" in content_type:
            continue
        data = response.content or b""
        if len(data) < IMAGE_MIN_BYTES or len(data) > IMAGE_MAX_BYTES:
            continue
        saved = save_image_bytes(data, candidate, images_dir, story_id)
        if saved:
            return saved, credit
    return None


def fill_deep_images(
    items: list[dict], session: requests.Session | None, output_dir: Path
) -> tuple[int, int]:
    """Fetch one article image per item; returns (found, missed).

    Misses (bot walls, text-only articles, flaky proxies) simply leave the
    item image-less — that is the agreed product behavior, not an error.
    """
    images_dir = Path(output_dir) / "images"
    found = missed = 0
    written: set[str] = set()
    for item in items:
        article_url = item_original_url(item)
        payload = fetch_page_html(session, article_url) if article_url else None
        candidates: list[str] = []
        if payload is not None:
            body, kind = payload
            candidates = extract_image_candidates(body, article_url, kind)
        story_id = str(item.get("story_id") or "").strip() or title_hash(
            str(item.get("title") or "")
        )
        result = download_item_image(session, candidates, images_dir, story_id, article_url)
        if result:
            rel_path, credit = result
            item["deep_image"] = rel_path
            item["deep_image_credit"] = credit
            written.add(Path(rel_path).name)
            found += 1
        else:
            missed += 1
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


# ---------------------------------------------------------------------------
# HTML rendering (inline styles only; <img> allowed in THIS variant)
# ---------------------------------------------------------------------------

def render_deep_item_html(item: dict, idx: int) -> str:
    title = strip_english_tail(str(item.get("title") or "").strip())
    reason = str(item.get("weixin_deep_reason") or "").strip()
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

    parts = ['<section style="margin:30px 0 0;padding:0;">']
    parts.append(
        '<p style="margin:0;font-size:16px;line-height:1.55;font-weight:bold;'
        f'color:#1f1f1f;">{circled_number(idx + 1)} {esc(title)}</p>'
    )
    image_path = str(item.get("deep_image") or "").strip()
    if image_path:
        credit = str(item.get("deep_image_credit") or "").strip()
        parts.append('<section style="margin:12px 0 0;">')
        parts.append(
            f'<img src="{esc(image_path)}" alt="" '
            'style="width:100%;display:block;border-radius:8px;">'
        )
        if credit:
            parts.append(
                '<p style="margin:6px 0 0;font-size:12px;line-height:1.5;'
                f'color:#b2b2b2;text-align:center;">图源：{esc(credit)}</p>'
            )
        parts.append("</section>")
    if reason:
        parts.append(
            '<p style="margin:10px 0 0;font-size:15px;line-height:1.8;'
            f'color:#555555;">{esc(reason)}</p>'
        )
    if len(sources) > 1:
        # Same merged-source line as 1.0: "标题（Buzzing, NewsNow, …）".
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
        parts.append(
            '<p style="margin:8px 0 0;font-size:13px;line-height:1.6;'
            f'color:#576b95;word-break:break-all;">原文：{esc(link)}</p>'
        )
    parts.append("</section>")
    return "\n".join(parts)


def render_deep_group_section(category: str, items: list[dict]) -> str:
    """2.0's section frame (centered color title + enclosing box) with the
    deep item renderer; 2.0 itself stays byte-identical."""
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
    # Per-section numbering restarts at ① (2.0 convention).
    for idx, item in enumerate(items):
        parts.append(render_deep_item_html(item, idx))
    parts.append("</section>")
    parts.append("</section>")
    return "\n".join(parts)


def render_deep_article_html(
    items: list[dict],
    *,
    title: str,
    digest: str,
    brand: str,
    issue_label: str,
    radar_url: str,
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
<p style="margin:3px 0 0;font-size:13px;color:#999999;">{esc(issue_label)} · 每日 AI 精读</p>
</section>

<p style="margin:0 0 4px;font-size:18px;font-weight:bold;line-height:1.5;color:#111111;">{esc(title)}</p>

{sections_html}

<section style="margin-top:30px;border-top:1px dashed #d9d9d9;padding-top:16px;">
<p style="margin:0;font-size:13px;line-height:1.7;color:#999999;">以上内容由 {esc(brand)} 自动整理自过去 24 小时的公开信源，图片来自原文页面，原文出处链接见每条信息下方。</p>
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


# ---------------------------------------------------------------------------
# Title / digest (fixed templates; never LLM-dependent)
# ---------------------------------------------------------------------------

def deep_title(brand: str, now_cn: datetime, count: int) -> str:
    return f"{brand} · {now_cn.month}月{now_cn.day}日｜今日精读{count}条"


def make_deep_digest(brand: str, count: int, headline: str, issue_label: str) -> str:
    digest = f"{brand}{issue_label}精读 {count} 条 AI 要闻：{headline}。更多条目见「阅读原文」。"
    return digest[:DIGEST_MAX_CHARS]


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="WeChat daily article generator (deep-read / 精读版 layout)"
    )
    parser.add_argument("--data-dir", default="data", help="data directory")
    parser.add_argument(
        "--output-dir",
        default=DEFAULT_OUTPUT_DIR,
        help=f"output directory (default {DEFAULT_OUTPUT_DIR})",
    )
    parser.add_argument(
        "--main-output-dir",
        default=DEFAULT_MAIN_OUTPUT_DIR,
        help=(
            "main variant output dir used for same-day cover reuse "
            f"(default {DEFAULT_MAIN_OUTPUT_DIR})"
        ),
    )
    parser.add_argument("--assets-dir", default="assets", help="assets directory")
    parser.add_argument(
        "--max-items",
        type=int,
        default=None,
        help="max items (defaults to WEIXIN_DEEP_MAX_ITEMS env or 10)",
    )
    parser.add_argument(
        "--no-images",
        action="store_true",
        help="skip original-article image fetching (fast/offline smoke runs)",
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="run without writing any files"
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    if os.environ.get("WEIXIN_ENABLED", "").strip() == "0":
        print("weixin-deep: disabled via WEIXIN_ENABLED=0, nothing to do")
        return 0

    cfg = build_config(args)
    cfg["max_items"] = resolve_deep_max_items(args)
    data_dir = Path(args.data_dir)
    output_dir = Path(args.output_dir)
    main_output_dir = Path(args.main_output_dir)
    assets_dir = Path(args.assets_dir)

    brief = load_brief(data_dir / "daily-brief.json")
    if brief is None:
        print(
            f"weixin-deep: {data_dir / 'daily-brief.json'} not found or invalid, "
            "nothing to do"
        )
        return 0

    items = select_items(brief, cfg["max_items"])
    if not items:
        print("weixin-deep: brief has no items, nothing to do")
        return 0

    now_cn = datetime.now(TZ_CN)
    issue_date = now_cn.strftime("%Y-%m-%d")
    issue_label = f"{now_cn.month}月{now_cn.day}日 {WEEKDAY_CN[now_cn.weekday()]}"

    # Images and grounding fetches do not need the Qwen key, so the session
    # exists unconditionally (1.0 gates it on the key only because all its
    # network use is key-bound).
    session = create_session()
    # Deliberately NOT the shared weixin/reason-cache.json: deep guides are a
    # different style and would otherwise hit stale short guides forever.
    cache_path = output_dir / "reason-cache.json"
    cache = load_deep_cache(cache_path)
    stats = {"reused": 0, "cached": 0, "generated": 0, "skipped": 0}

    fill_deep_reasons(items, cache, cfg, session, stats)

    images_found = images_missed = 0
    if not args.dry_run and not args.no_images:
        images_found, images_missed = fill_deep_images(items, session, output_dir)

    headline = strip_english_tail(str(items[0].get("title") or "").strip())
    title = deep_title(cfg["brand"], now_cn, len(items))
    digest = make_deep_digest(cfg["brand"], len(items), headline, issue_label)

    cover_bytes, cover_filename = reuse_cover(main_output_dir, issue_date)
    if cover_bytes is not None:
        cover_mode, cover_scene = "reused", False
    else:
        cover_bytes, cover_filename, cover_mode, cover_scene = resolve_cover(
            headline, cfg, session, assets_dir
        )

    groups = group_items(items)
    html_text = render_deep_article_html(
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
    meta["layout"] = "deep"
    meta["sections"] = [
        {
            "category": category,
            "label": CATEGORY_LABEL_ZH.get(category, category),
            "count": len(group),
        }
        for category, group in groups
    ]
    # Per-story image map, ready for future API draft delivery.
    meta["images"] = {
        str(item.get("story_id") or ""): {
            "file": str(item.get("deep_image") or ""),
            "credit": str(item.get("deep_image_credit") or ""),
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
        "skipped={skipped} images found={found} missed={missed} "
        "cover_mode={cover_mode} cover_scene={cover_scene} "
        "dry_run={dry_run}".format(
            items=len(items),
            sections=",".join(
                f"{CATEGORY_LABEL_ZH.get(cat, cat)}×{len(group)}"
                for cat, group in groups
            ),
            reused=stats["reused"],
            cached=stats["cached"],
            generated=stats["generated"],
            skipped=stats["skipped"],
            found=images_found,
            missed=images_missed,
            cover_mode=cover_mode,
            cover_scene=1 if cover_scene else 0,
            dry_run=1 if args.dry_run else 0,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
