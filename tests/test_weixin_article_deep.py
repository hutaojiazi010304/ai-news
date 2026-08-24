"""Tests for scripts/generate_weixin_article_deep.py (3.0 精读版).

Pins the three upgrades over the grouped variant: top-10 selection, longer
repeated-news style guides in an INDEPENDENT cache (the shared 1.0/2.0 cache
must never leak in), and one real article image per item with graceful
no-image degradation. Mock plumbing mirrors test_weixin_article.py: text
completions through module-level requests.post, everything else through the
session returned by create_session — nothing touches the real network.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import generate_weixin_article as gwa
from scripts import generate_weixin_article_deep as gwad

from test_weixin_article import (
    BASE_ENV,
    make_item,
    make_png_bytes,
    make_static_asset,
    offline_session,
    read_json,
    text_response,
    write_fixture,
)


def make_categorized_item(idx: int, category: str, score: float) -> dict:
    item = make_item(idx, title=f"精读分类新闻 {idx}", score=score)
    item["category"] = category
    return item


# Long enough (>= DEEP_SUMMARY_MIN_GROUNDING_CHARS) to ground a deep guide
# offline, so no full-text fetch is needed.
DEEP_SUMMARY = (
    "官方发布的摘要详细介绍了这次更新的具体内容，包括接口变化、性能数字、"
    "适配范围与后续计划，并给出了迁移示例和注意事项说明，足以支撑一段较长的转述。"
    "官方还补充了与上一代版本的对比数据，列出了各项基准测试的具体得分、延迟指标"
    "与吞吐量变化，并说明新接口在兼容性方面的处理方式以及已知的限制条件，"
    "方便开发者评估升级的成本与收益。"
)
# Within DEEP_REASON_* bounds; starts with the source-attribution opener.
LONG_DEEP_REASON = (
    "据 Example Source 报道，该团队发布了新一代推理模型，官方给出的数据显示其"
    "推理成本较上一代下降约五成，上下文窗口扩展到一百二十万 token，并同步开放了"
    "评测细节与接口文档，首批合作伙伴已接入测试。报道还提到，新模型在多项公开"
    "基准上的成绩超过上一代，团队称后续将逐步开放更多能力。"
)

# Long enough to pass summary_grounding on its own, but far below the deep
# 120-char threshold — must trigger a full-text fetch.
THIN_SUMMARY = "这是一段很短的摘要，不足以支撑精读导读。"


def make_deep_text_router(reason=None, scene=None):
    """Route text completions by system-prompt markers.

    「精读」marks the deep guide prompt (NOT 「转述」 — 1.0's prompt contains
    「转述其观点」 and would collide); 「插画设计师」 marks the cover scene.
    Any other call fails the test.
    """
    calls = {"reason": 0, "scene": 0}

    def side_effect(url, **kwargs):
        payload = kwargs.get("json") or {}
        messages = payload.get("messages")
        system = str(((messages or [{}])[0] or {}).get("content") or "")
        if "插画设计师" in system:
            which, spec = "scene", scene
        elif "精读" in system:
            which, spec = "reason", reason
        else:
            raise AssertionError(f"unexpected text api call: {url}")
        calls[which] += 1
        if isinstance(spec, BaseException):
            raise spec
        if isinstance(spec, MagicMock):
            return spec
        if callable(spec):
            return spec(calls)
        return spec

    return side_effect, calls


def run_deep_patched(env: dict, post_side_effect, session, args_list) -> int:
    """Run deep main with the text post router + a mocked session factory."""
    with patch.dict("os.environ", env, clear=True), patch(
        "scripts.generate_weixin_article.requests.post", side_effect=post_side_effect
    ), patch(
        "scripts.generate_weixin_article_deep.create_session", return_value=session
    ), patch("scripts.generate_weixin_article.time.sleep"):
        return gwad.main(args_list)


def seed_main_cover(main_dir: Path, cover_bytes: bytes = b"\xff\xd8fake-jpeg-bytes") -> bytes:
    """Pre-seed the main variant's output so the deep run REUSES its cover
    (keeps the image-generation API out of the test)."""
    main_dir.mkdir(parents=True, exist_ok=True)
    issue_date = datetime.now(gwa.TZ_CN).strftime("%Y-%m-%d")
    (main_dir / "cover.jpg").write_bytes(cover_bytes)
    (main_dir / "meta.json").write_text(
        json.dumps({"issue_date": issue_date, "cover": "cover.jpg"}),
        encoding="utf-8",
    )
    return cover_bytes


def page_response(img_tag: str) -> MagicMock:
    response = MagicMock()
    response.status_code = 200
    response.text = (
        "<html><body><article>"
        f"<p>{'这是一段用于测试的正文内容。' * 30}</p>"
        f"{img_tag}"
        "</article></body></html>"
    )
    return response


def image_response(
    data: bytes,
    content_type: str = "image/jpeg",
    status_code: int = 200,
) -> MagicMock:
    response = MagicMock()
    response.status_code = status_code
    response.headers = {"Content-Type": content_type}
    response.content = data
    return response


class FakeResponse:
    def __init__(self, status_code: int = 200, text: str = ""):
        self.status_code = status_code
        self.text = text


class FakeSession:
    """Records GETs; serves one fixed body (mirrors test_weixin_article)."""

    def __init__(self, text: str = ""):
        self.calls: list[str] = []
        self._text = text

    def get(self, url, timeout=None, **kwargs):
        self.calls.append(str(url))
        return FakeResponse(200, self._text)


# ---------------------------------------------------------------------------
# Max-items precedence
# ---------------------------------------------------------------------------

def test_resolve_deep_max_items_precedence():
    args = MagicMock()
    args.max_items = None

    with patch.dict("os.environ", {}, clear=True):
        assert gwad.resolve_deep_max_items(args) == 10
    with patch.dict("os.environ", {"WEIXIN_DEEP_MAX_ITEMS": "7"}, clear=True):
        assert gwad.resolve_deep_max_items(args) == 7
    # The 1.0/2.0 knob must have NO effect on the deep variant.
    with patch.dict("os.environ", {"WEIXIN_MAX_ITEMS": "20"}, clear=True):
        assert gwad.resolve_deep_max_items(args) == 10
    # CLI beats env.
    args.max_items = 3
    with patch.dict("os.environ", {"WEIXIN_DEEP_MAX_ITEMS": "7"}, clear=True):
        assert gwad.resolve_deep_max_items(args) == 3


def test_top10_selection_cap_and_ranking(tmp_path):
    items = [make_item(idx, title=f"精读选条测试第{idx}条", score=100.0 - idx) for idx in range(1, 15)]
    data_dir, assets_dir = write_fixture(tmp_path, items)
    make_static_asset(assets_dir)
    out_dir = tmp_path / "weixin-deep"
    args = [
        "--data-dir", str(data_dir),
        "--output-dir", str(out_dir),
        "--main-output-dir", str(tmp_path / "weixin"),
        "--assets-dir", str(assets_dir),
        "--no-images",
    ]
    with patch.dict("os.environ", {}, clear=True), patch(
        "scripts.generate_weixin_article_deep.create_session",
        return_value=offline_session(),
    ):
        rc = gwad.main(args)

    assert rc == 0
    meta = read_json(out_dir / "meta.json")
    assert meta["item_count"] == 10
    html_text = (out_dir / "index.html").read_text(encoding="utf-8")
    for idx in range(1, 11):
        assert f"精读选条测试第{idx}条" in html_text
    for idx in range(11, 15):
        assert f"精读选条测试第{idx}条" not in html_text


# ---------------------------------------------------------------------------
# Deep guide: cache isolation, generation, validation, grounding
# ---------------------------------------------------------------------------

def test_deep_cache_ignores_main_cache(tmp_path):
    title = "缓存隔离测试标题"
    item = make_item(1, title=title, summary=DEEP_SUMMARY)
    data_dir, assets_dir = write_fixture(tmp_path, [item])
    make_static_asset(assets_dir)
    main_dir = tmp_path / "weixin"
    seed_main_cover(main_dir)
    # Pre-fill the SHARED 1.0-format cache with a matching entry: the deep
    # variant must ignore it entirely and still generate.
    key = gwa.cache_key("story_1", title)
    stale_reason = "旧版短导读缓存内容，风格不同，不得被精读版使用。"
    (main_dir / "reason-cache.json").write_text(
        json.dumps(
            {
                "version": gwa.CACHE_VERSION,
                "entries": {
                    key: {
                        "reason": stale_reason,
                        "title_hash": gwa.title_hash(title),
                        "created_at": "2026-08-24T00:00:00Z",
                    }
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    deep_dir = tmp_path / "weixin-deep"
    side_effect, calls = make_deep_text_router(reason=text_response(LONG_DEEP_REASON))
    args = [
        "--data-dir", str(data_dir),
        "--output-dir", str(deep_dir),
        "--main-output-dir", str(main_dir),
        "--assets-dir", str(assets_dir),
        "--no-images",
    ]

    rc = run_deep_patched(BASE_ENV, side_effect, offline_session(), args)

    assert rc == 0
    # The stale shared-cache hit did NOT suppress generation.
    assert calls["reason"] == 1
    html_text = (deep_dir / "index.html").read_text(encoding="utf-8")
    assert LONG_DEEP_REASON in html_text
    assert stale_reason not in html_text
    # Deep cache lives in the deep dir under its own version.
    deep_cache = read_json(deep_dir / "reason-cache.json")
    assert deep_cache["version"] == gwad.DEEP_CACHE_VERSION
    assert deep_cache["entries"][key]["reason"] == LONG_DEEP_REASON
    # The shared cache file is untouched.
    main_cache = read_json(main_dir / "reason-cache.json")
    assert main_cache["entries"][key]["reason"] == stale_reason


def test_deep_reason_generated_and_cached(tmp_path):
    data_dir, assets_dir = write_fixture(
        tmp_path, [make_item(1, summary=DEEP_SUMMARY)]
    )
    make_static_asset(assets_dir)
    main_dir = tmp_path / "weixin"
    seed_main_cover(main_dir)
    deep_dir = tmp_path / "weixin-deep"
    args = [
        "--data-dir", str(data_dir),
        "--output-dir", str(deep_dir),
        "--main-output-dir", str(main_dir),
        "--assets-dir", str(assets_dir),
        "--no-images",
    ]

    side_effect, first_calls = make_deep_text_router(reason=text_response(LONG_DEEP_REASON))
    run_deep_patched(BASE_ENV, side_effect, offline_session(), args)
    assert first_calls["reason"] == 1

    side_effect, second_calls = make_deep_text_router(
        reason=AssertionError("cached deep reason must not be regenerated")
    )
    rc = run_deep_patched(BASE_ENV, side_effect, offline_session(), args)

    assert rc == 0
    assert second_calls["reason"] == 0
    html_text = (deep_dir / "index.html").read_text(encoding="utf-8")
    assert LONG_DEEP_REASON in html_text


def test_deep_validation_bounds():
    good = LONG_DEEP_REASON
    title = "校验测试标题"
    assert gwad.validate_deep_reason(good, title) is True
    assert gwad.validate_deep_reason("据某媒体报道，这是一条很短的消息。", title) is False  # <80
    assert gwad.validate_deep_reason("好" * (gwad.DEEP_REASON_MAX_CHARS + 1), title) is False
    assert gwad.validate_deep_reason("a" * 120, title) is False  # no CJK
    assert gwad.validate_deep_reason(title, title) is False
    assert gwad.validate_deep_reason("据某媒体报道，详情见 https://example.com 。" * 5, title) is False
    assert gwad.validate_deep_reason("很抱歉，无法生成导读。" + "填" * 100, title) is False


def test_deep_grounding_summary_threshold():
    long_summary_item = make_item(1, summary="长" * 130)
    # Long summary grounds offline — no session needed at all.
    assert gwad.deep_reason_context(long_summary_item, None) == "长" * 130

    thin_item = make_item(2, summary=THIN_SUMMARY)
    body = f"<p>{'这是抓回来的正文内容，用来补足摘要的信息量。' * 20}</p>"
    session = FakeSession(text=body)
    grounding = gwad.deep_reason_context(thin_item, session)
    assert grounding and "抓回来的正文内容" in grounding
    assert session.calls and session.calls[0] == "https://example.com/story/2"

    nothing = make_item(3)
    assert gwad.deep_reason_context(nothing, None) is None


def test_keyless_degradation_uses_upstream_reason(tmp_path):
    upstream = (
        "上游管线已经写好的较长推荐语：这次发布带来了新的接口与更高的吞吐，"
        "官方文档同步更新，开发者可以直接升级试用，整体兼容性保持不变。"
    )
    data_dir, assets_dir = write_fixture(
        tmp_path, [make_item(1, reason=upstream, summary=DEEP_SUMMARY)]
    )
    make_static_asset(assets_dir)
    deep_dir = tmp_path / "weixin-deep"
    args = [
        "--data-dir", str(data_dir),
        "--output-dir", str(deep_dir),
        "--main-output-dir", str(tmp_path / "weixin"),
        "--assets-dir", str(assets_dir),
        "--no-images",
    ]
    side_effect, calls = make_deep_text_router(
        reason=AssertionError("keyless run must not call the text api")
    )

    rc = run_deep_patched({}, side_effect, offline_session(), args)

    assert rc == 0
    assert calls["reason"] == 0
    html_text = (deep_dir / "index.html").read_text(encoding="utf-8")
    assert upstream in html_text


# ---------------------------------------------------------------------------
# Image extraction
# ---------------------------------------------------------------------------

def test_extract_image_candidates_from_html():
    base = "https://site.example.com/article/1"
    html = (
        '<img src="/static/logo.png">'
        '<img data-src="https://cdn.example.com/photo1.jpg" width="800">'
        '<img srcset="https://cdn.example.com/a-400.jpg 400w, '
        'https://cdn.example.com/a-800.jpg 800w" width="80">'
        '<img src="data:image/gif;base64,xyz">'
        '<img src="/content/photo2.png" width="600" height="400">'
        '<img src="https://cdn.example.com/icon-share.svg">'
    )

    candidates = gwad.extract_image_candidates(html, base, "html")

    assert candidates == [
        "https://cdn.example.com/photo1.jpg",       # lazy data-src picked up
        "https://site.example.com/content/photo2.png",  # relative absolutized
    ]


def test_extract_image_candidates_from_jina_markdown():
    base = "https://site.example.com/article/1"
    markdown = (
        "正文开头。\n"
        "![图一](https://cdn.example.com/pic1.jpg)\n"
        "![第二张](/rel/pic2.png)\n"
        '<img src="https://cdn.example.com/pic3.jpg">\n'
    )

    candidates = gwad.extract_image_candidates(markdown, base, "markdown")

    assert candidates == [
        "https://cdn.example.com/pic1.jpg",
        "https://site.example.com/rel/pic2.png",
        "https://cdn.example.com/pic3.jpg",
    ]


def test_extract_image_candidates_cap_and_dedup():
    base = "https://site.example.com/"
    html = "".join(
        f'<img src="https://cdn.example.com/img{n}.jpg" width="500">' for n in range(15)
    ) + '<img src="https://cdn.example.com/img0.jpg" width="500">'

    candidates = gwad.extract_image_candidates(html, base, "html")

    assert len(candidates) == gwad.MAX_IMAGE_CANDIDATES
    assert len(set(candidates)) == len(candidates)


# ---------------------------------------------------------------------------
# Image download
# ---------------------------------------------------------------------------

def test_download_item_image_happy_path(tmp_path):
    session = MagicMock()
    session.get.return_value = image_response(make_png_bytes(1000, 500))
    images_dir = tmp_path / "images"

    result = gwad.download_item_image(
        session,
        ["https://cdn.example.com/photo.jpg"],
        images_dir,
        "story_1",
        "https://www.example.com/article/9",
    )

    assert result == ("images/story_1.jpg", "example.com")
    saved = images_dir / "story_1.jpg"
    assert saved.exists() and saved.stat().st_size > 0
    try:
        from PIL import Image

        width, height = Image.open(saved).size
        assert width <= gwad.IMAGE_MAX_WIDTH
        assert width >= gwad.IMAGE_MIN_DIMENSION and height >= gwad.IMAGE_MIN_DIMENSION
    except ImportError:
        pass


def test_download_item_image_graceful_miss(tmp_path):
    images_dir = tmp_path / "images"
    candidates = ["https://cdn.example.com/photo.jpg"]
    article = "https://www.example.com/article/9"

    def run(response):
        session = MagicMock()
        session.get.return_value = response
        return gwad.download_item_image(session, candidates, images_dir, "story_1", article)

    # HTTP failure.
    assert run(image_response(b"", status_code=403)) is None
    # Not an image content type.
    assert run(image_response(b"<html>not an image</html>" * 50, "text/html")) is None
    # Oversized payload.
    assert run(image_response(b"x" * (gwad.IMAGE_MAX_BYTES + 1))) is None
    # Undersized decoded image (tracking-pixel class) — Pillow-only check.
    try:
        from PIL import Image  # noqa: F401
    except ImportError:
        pass
    else:
        assert run(image_response(make_png_bytes(50, 50))) is None
    # Nothing was ever written.
    assert not (images_dir / "story_1.jpg").exists()


def test_download_item_image_skips_bad_first_candidate(tmp_path):
    session = MagicMock()
    session.get.side_effect = [
        image_response(b"", status_code=403),
        image_response(make_png_bytes(600, 400)),
    ]
    images_dir = tmp_path / "images"

    result = gwad.download_item_image(
        session,
        ["https://cdn.example.com/broken.jpg", "https://cdn.example.com/ok.jpg"],
        images_dir,
        "story_2",
        "https://example.com/x",
    )

    assert result == ("images/story_2.jpg", "example.com")
    assert (images_dir / "story_2.jpg").exists()


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def test_render_deep_item_html_order_and_credit():
    item = make_item(1, title="深度版渲染测试")
    item["weixin_deep_reason"] = LONG_DEEP_REASON
    item["deep_image"] = "images/story_1.jpg"
    item["deep_image_credit"] = "example.com"

    html = gwad.render_deep_item_html(item, 0)

    assert "① 深度版渲染测试" in html
    assert '<img src="images/story_1.jpg"' in html
    assert "图源：example.com" in html
    assert (
        html.index("深度版渲染测试")
        < html.index("<img")
        < html.index("图源：example.com")
        < html.index(LONG_DEEP_REASON)
        < html.index("个来源")
        < html.index("原文：")
    )
    assert "<a " not in html


def test_render_deep_item_html_without_image_has_no_img_tag():
    item = make_item(1)
    item["weixin_deep_reason"] = LONG_DEEP_REASON

    html = gwad.render_deep_item_html(item, 0)

    assert "<img" not in html
    assert "图源" not in html


# ---------------------------------------------------------------------------
# End-to-end runs
# ---------------------------------------------------------------------------

def test_e2e_keyless_writes_deep_layout_no_images(tmp_path):
    data_dir, assets_dir = write_fixture(
        tmp_path,
        [
            make_categorized_item(1, "official", 92),
            make_categorized_item(2, "industry", 88),
            make_categorized_item(3, "industry", 84),
            make_categorized_item(4, "watch", 70),
        ],
    )
    make_static_asset(assets_dir)
    main_dir = tmp_path / "weixin"
    cover_bytes = seed_main_cover(main_dir)
    deep_dir = tmp_path / "weixin-deep"
    args = [
        "--data-dir", str(data_dir),
        "--output-dir", str(deep_dir),
        "--main-output-dir", str(main_dir),
        "--assets-dir", str(assets_dir),
        "--no-images",
    ]

    with patch.dict("os.environ", {}, clear=True), patch(
        "scripts.generate_weixin_article_deep.create_session",
        return_value=offline_session(),
    ):
        rc = gwad.main(args)

    assert rc == 0
    html_text = (deep_dir / "index.html").read_text(encoding="utf-8")
    meta = read_json(deep_dir / "meta.json")

    assert meta["layout"] == "deep"
    assert meta["item_count"] == 4
    assert meta["sections"] == [
        {"category": "official", "label": "官方更新", "count": 1},
        {"category": "industry", "label": "行业动态", "count": 2},
        {"category": "watch", "label": "值得关注", "count": 1},
    ]
    assert meta["images"] == {}
    assert "<img" not in html_text
    for idx in (1, 2, 3, 4):
        assert f"精读分类新闻 {idx}" in html_text
    assert html_text.index(">官方更新</p>") < html_text.index(">行业动态</p>")
    # Cover reused verbatim from the main variant.
    assert meta["cover"] == "cover.jpg"
    assert (deep_dir / "cover.jpg").read_bytes() == cover_bytes
    # The deep cache file is written into the deep dir (not the main dir).
    assert (deep_dir / "reason-cache.json").exists()
    assert not (deep_dir / "images").exists()


def test_e2e_keyed_run_with_images(tmp_path):
    data_dir, assets_dir = write_fixture(
        tmp_path, [make_item(1, summary=DEEP_SUMMARY)]
    )
    make_static_asset(assets_dir)
    main_dir = tmp_path / "weixin"
    seed_main_cover(main_dir)
    deep_dir = tmp_path / "weixin-deep"
    # A stale image from a previous day must be pruned.
    (deep_dir / "images").mkdir(parents=True)
    (deep_dir / "images" / "story_old.jpg").write_bytes(b"stale")
    args = [
        "--data-dir", str(data_dir),
        "--output-dir", str(deep_dir),
        "--main-output-dir", str(main_dir),
        "--assets-dir", str(assets_dir),
    ]

    session = MagicMock()
    session.get.side_effect = [
        page_response('<img src="https://cdn.example.com/photo.jpg">'),
        image_response(make_png_bytes(600, 400)),
    ]
    side_effect, calls = make_deep_text_router(reason=text_response(LONG_DEEP_REASON))

    rc = run_deep_patched(BASE_ENV, side_effect, session, args)

    assert rc == 0
    assert calls["reason"] == 1
    meta = read_json(deep_dir / "meta.json")
    assert meta["layout"] == "deep"
    assert meta["images"] == {
        "story_1": {"file": "images/story_1.jpg", "credit": "example.com"}
    }
    assert (deep_dir / "images" / "story_1.jpg").exists()
    assert not (deep_dir / "images" / "story_old.jpg").exists()
    html_text = (deep_dir / "index.html").read_text(encoding="utf-8")
    assert '<img src="images/story_1.jpg"' in html_text
    assert "图源：example.com" in html_text
    assert LONG_DEEP_REASON in html_text


def test_dry_run_writes_nothing(tmp_path):
    data_dir, assets_dir = write_fixture(tmp_path, [make_item(1, summary=DEEP_SUMMARY)])
    make_static_asset(assets_dir)
    deep_dir = tmp_path / "weixin-deep"
    args = [
        "--data-dir", str(data_dir),
        "--output-dir", str(deep_dir),
        "--main-output-dir", str(tmp_path / "weixin"),
        "--assets-dir", str(assets_dir),
        "--dry-run",
    ]

    with patch.dict("os.environ", {}, clear=True), patch(
        "scripts.generate_weixin_article_deep.create_session",
        return_value=offline_session(),
    ):
        rc = gwad.main(args)

    assert rc == 0
    assert not deep_dir.exists()


def test_killswitch_and_missing_brief(tmp_path):
    data_dir, assets_dir = write_fixture(tmp_path, [make_item(1)])
    deep_dir = tmp_path / "weixin-deep"
    args = [
        "--data-dir", str(data_dir),
        "--output-dir", str(deep_dir),
        "--assets-dir", str(assets_dir),
    ]

    with patch.dict("os.environ", {"WEIXIN_ENABLED": "0"}, clear=True):
        assert gwad.main(args) == 0
    assert not deep_dir.exists()

    with patch.dict("os.environ", {}, clear=True):
        assert (
            gwad.main(
                [
                    "--data-dir", str(tmp_path / "no-such-dir"),
                    "--output-dir", str(deep_dir),
                    "--assets-dir", str(assets_dir),
                ]
            )
            == 0
        )
    assert not deep_dir.exists()


def test_weixin_enabled_killswitch_message(capsys, tmp_path):
    with patch.dict("os.environ", {"WEIXIN_ENABLED": "0"}, clear=True):
        rc = gwad.main(["--data-dir", str(tmp_path), "--output-dir", str(tmp_path / "x")])
    assert rc == 0
    assert "disabled" in capsys.readouterr().out
