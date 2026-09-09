"""Tests for scripts/generate_weixin_article_deep.py (3.0 精读版).

Pins the deep-read behavior: top-20 selection, longer repeated-news style
guides in the deep cache, and one real article image per item with graceful
no-image degradation. Mock plumbing: text completions through module-level
requests.post, everything else through the session returned by
create_session — nothing touches the real network.
"""

from __future__ import annotations

import io
import json
import sys
import tempfile
import warnings
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import requests

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import generate_weixin_article_deep as gwad


# ---------------------------------------------------------------------------
# Fixture builders (self-contained)
# ---------------------------------------------------------------------------

BASE_ENV = {"DASHSCOPE_API_KEY": "test-key"}


def _write_png(path: Path, width: int, height: int, pixel_fn) -> None:
    """Minimal pure-Python PNG writer (RGB, no font / Pillow needed)."""
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


def make_item(
    idx: int,
    *,
    title: str | None = None,
    score: float = 50.0,
    reason: str | None = None,
    summary: str | None = None,
    sources: list[dict] | None = None,
) -> dict:
    return {
        "story_id": f"story_{idx}",
        "title": title or f"测试新闻标题 {idx}",
        "url": f"https://example.com/story/{idx}",
        "primary_url": f"https://example.com/story/{idx}",
        "importance_score": score,
        "importance_label": "high",
        "category": "model",
        "source_name": "Example Source",
        "source_count": 1,
        "source_names": ["Example Source"],
        "sources": sources
        or [
            {
                "title": title or f"测试新闻标题 {idx}",
                "url": f"https://example.com/story/{idx}",
                "source_name": "Example Source",
                "summary": summary,
                "recommend_reason_zh": reason,
            }
        ],
        "primary_item": {
            "title": title or f"测试新闻标题 {idx}",
            "url": f"https://example.com/story/{idx}",
            "source_name": "Example Source",
            "summary": summary,
            "recommend_reason_zh": reason,
        },
    }


def write_fixture(tmp: str | Path, items: list[dict]) -> tuple[Path, Path]:
    root = Path(tmp)
    data_dir = root / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    brief = {
        "generated_at": "2026-08-13T00:00:00Z",
        "window_hours": 24,
        "total_items": len(items),
        "items": items,
    }
    (data_dir / "daily-brief.json").write_text(
        json.dumps(brief, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    assets_dir = root / "assets"
    assets_dir.mkdir(parents=True, exist_ok=True)
    return data_dir, assets_dir


def make_static_asset(assets_dir: Path) -> None:
    _write_png(
        assets_dir / "weixin-cover-fallback.png", 120, 51, lambda x, y: (1, 2, 3)
    )


def make_png_bytes(width: int = 1000, height: int = 500) -> bytes:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "probe.png"
        _write_png(path, width, height, lambda x, y: (x % 256, y % 256, 30))
        return path.read_bytes()


def text_response(content: str) -> MagicMock:
    response = MagicMock()
    response.status_code = 200
    response.raise_for_status.return_value = None
    response.json.return_value = {"choices": [{"message": {"content": content}}]}
    return response


def offline_session() -> MagicMock:
    """Session mock that refuses all network I/O: cover/fetch degrade cleanly."""
    session = MagicMock()
    session.get.side_effect = requests.ConnectionError("offline")
    session.post.side_effect = requests.ConnectionError("offline")
    return session


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


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
# A realistic deep guide as the model would return one (passes validation).
LONG_DEEP_REASON = (
    "据 Example Source 报道，该团队发布了新一代推理模型，官方给出的数据显示其"
    "推理成本较上一代下降约五成，上下文窗口扩展到一百二十万 token，并同步开放了"
    "评测细节与接口文档，首批合作伙伴已接入测试。报道还提到，新模型在多项公开"
    "基准上的成绩超过上一代，团队称后续将逐步开放更多能力。"
)

# Long enough to pass summary_grounding on its own, but far below the deep
# 120-char threshold — must trigger a full-text fetch.
THIN_SUMMARY = "这是一段很短的摘要，不足以支撑精读导读。"


def make_deep_text_router(reason=None, scene=None, mark=None, translate=None):
    """Route text completions by system-prompt markers.

    「精读」marks the deep guide prompt (not 「转述」, which appears in other
    pipeline prompts and would collide); 「插画设计师」 marks the cover scene;
    「校对员」 marks the highlight pass, which by default (mark=None) echoes
    the guide back unchanged — no highlights, text preserved verbatim;
    「地道的简体中文」 marks the English-title backfill translation.
    A cover-scene call with no scene spec (scene=None) fails like an offline
    text endpoint, so runs without a seeded scene degrade to the static cover.
    Any other call fails the test.
    """
    calls = {"reason": 0, "scene": 0, "mark": 0, "translate": 0}

    def side_effect(url, **kwargs):
        payload = kwargs.get("json") or {}
        messages = payload.get("messages")
        system = str(((messages or [{}])[0] or {}).get("content") or "")
        if "插画设计师" in system:
            which, spec = "scene", scene
        elif "地道的简体中文" in system:
            which, spec = "translate", translate
        elif "精读" in system:
            which, spec = "reason", reason
        elif "校对员" in system:
            which, spec = "mark", mark
        else:
            raise AssertionError(f"unexpected text api call: {url}")
        calls[which] += 1
        if which == "mark" and spec is None:
            user = str(((messages or [{}])[-1] or {}).get("content") or "")
            return text_response(user)
        if which == "scene" and spec is None:
            # Simulate an unreachable text endpoint: resolve_cover then
            # degrades to the static fallback cover.
            raise requests.ConnectionError("offline")
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
        "scripts.generate_weixin_article_deep.requests.post", side_effect=post_side_effect
    ), patch(
        "scripts.generate_weixin_article_deep.create_session", return_value=session
    ), patch("scripts.generate_weixin_article_deep.time.sleep"):
        return gwad.main(args_list)


def page_response(img_tag: str) -> MagicMock:
    """Streaming mock: bounded_get reads via iter_content (stream=True)."""
    response = MagicMock()
    response.status_code = 200
    html = (
        "<html><body><article>"
        f"<p>{'这是一段用于测试的正文内容。' * 30}</p>"
        f"{img_tag}"
        "</article></body></html>"
    )
    response.iter_content.return_value = [html.encode("utf-8")]
    return response


def image_response(
    data: bytes,
    content_type: str = "image/jpeg",
    status_code: int = 200,
) -> MagicMock:
    """Streaming mock: bounded_get reads via iter_content (stream=True)."""
    response = MagicMock()
    response.status_code = status_code
    response.headers = {"Content-Type": content_type}
    response.iter_content.return_value = [data]
    return response


class FakeResponse:
    def __init__(self, status_code: int = 200, text: str = ""):
        self.status_code = status_code
        self.text = text
        self.headers = {"Content-Type": "text/html"}

    def iter_content(self, chunk_size=None):
        return iter([self.text.encode("utf-8")])

    def close(self):
        pass


class FakeSession:
    """Records GETs; serves one fixed body."""

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
        assert gwad.resolve_deep_max_items(args) == 20
    with patch.dict("os.environ", {"WEIXIN_DEEP_MAX_ITEMS": "7"}, clear=True):
        assert gwad.resolve_deep_max_items(args) == 7
    # CLI beats env.
    args.max_items = 3
    with patch.dict("os.environ", {"WEIXIN_DEEP_MAX_ITEMS": "7"}, clear=True):
        assert gwad.resolve_deep_max_items(args) == 3


def test_top20_selection_cap_and_ranking(tmp_path):
    items = [make_item(idx, title=f"精读选条测试第{idx}条", score=100.0 - idx) for idx in range(1, 26)]
    data_dir, assets_dir = write_fixture(tmp_path, items)
    make_static_asset(assets_dir)
    out_dir = tmp_path / "weixin-deep"
    args = [
        "--data-dir", str(data_dir),
        "--output-dir", str(out_dir),
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
    assert meta["item_count"] == 20
    html_text = (out_dir / "index.html").read_text(encoding="utf-8")
    for idx in range(1, 21):
        assert f"精读选条测试第{idx}条" in html_text
    for idx in range(21, 26):
        assert f"精读选条测试第{idx}条" not in html_text


# ---------------------------------------------------------------------------
# Weekly labels: date-range title/footer; the publish helper block moved out
# of the page body into publish-info.txt
# ---------------------------------------------------------------------------

def test_issue_range_label_follows_brief_window():
    now_cn = datetime(2026, 8, 28, 10, 0, tzinfo=gwad.TZ_CN)
    assert gwad.issue_range_label({"window_hours": 168}, now_cn) == "8月22日-8月28日"
    assert gwad.issue_range_label({"window_hours": 72}, now_cn) == "8月26日-8月28日"
    # Daily fallback window collapses to the single issue day.
    assert gwad.issue_range_label({"window_hours": 24}, now_cn) == "8月28日"
    # Missing window falls back to the weekly lookback (default 7 days).
    with patch.dict("os.environ", {}, clear=True):
        assert gwad.issue_range_label({}, now_cn) == "8月22日-8月28日"


def test_deep_title_uses_range_label():
    assert (
        gwad.deep_title("AI 雷达", "8月22日-8月28日", 20)
        == "AI 雷达 · 8月22日-8月28日｜本周精读20条"
    )


def test_publish_info_file_replaces_helper_block(tmp_path):
    item = make_item(1, title="发布辅助信息测试条目")
    data_dir, assets_dir = write_fixture(tmp_path, [item])
    make_static_asset(assets_dir)
    out_dir = tmp_path / "weixin-deep"
    args = [
        "--data-dir", str(data_dir),
        "--output-dir", str(out_dir),
        "--assets-dir", str(assets_dir),
        "--no-images",
    ]
    with patch.dict("os.environ", {}, clear=True), patch(
        "scripts.generate_weixin_article_deep.create_session",
        return_value=offline_session(),
    ):
        rc = gwad.main(args)

    assert rc == 0
    html_text = (out_dir / "index.html").read_text(encoding="utf-8")
    assert "以下为发布辅助信息" not in html_text
    assert "阅读原文：" not in html_text
    info = (out_dir / "publish-info.txt").read_text(encoding="utf-8")
    lines = info.strip().splitlines()
    assert lines[0].startswith("标题：") and "本周精读1条" in lines[0]
    assert lines[1].startswith("摘要：")
    assert lines[2].startswith("阅读原文：")


# ---------------------------------------------------------------------------
# Deep guide: cache versioning, generation, validation, grounding
# ---------------------------------------------------------------------------

def test_deep_reason_cache_written_under_deep_version(tmp_path):
    """The deep guide cache is written into the deep output dir under its
    own version tag, so entries can never be confused with other caches."""
    title = "缓存版本测试标题"
    item = make_item(1, title=title, summary=DEEP_SUMMARY)
    data_dir, assets_dir = write_fixture(tmp_path, [item])
    make_static_asset(assets_dir)
    deep_dir = tmp_path / "weixin-deep"
    key = gwad.cache_key("story_1", title)
    side_effect, calls = make_deep_text_router(reason=text_response(LONG_DEEP_REASON))
    args = [
        "--data-dir", str(data_dir),
        "--output-dir", str(deep_dir),
        "--assets-dir", str(assets_dir),
        "--no-images",
    ]

    rc = run_deep_patched(BASE_ENV, side_effect, offline_session(), args)

    assert rc == 0
    assert calls["reason"] == 1
    html_text = (deep_dir / "index.html").read_text(encoding="utf-8")
    assert LONG_DEEP_REASON in html_text
    deep_cache = read_json(deep_dir / "reason-cache.json")
    assert deep_cache["version"] == gwad.DEEP_CACHE_VERSION
    assert deep_cache["entries"][key]["reason"] == LONG_DEEP_REASON


def test_deep_reason_generated_and_cached(tmp_path):
    data_dir, assets_dir = write_fixture(
        tmp_path, [make_item(1, summary=DEEP_SUMMARY)]
    )
    make_static_asset(assets_dir)
    deep_dir = tmp_path / "weixin-deep"
    args = [
        "--data-dir", str(data_dir),
        "--output-dir", str(deep_dir),
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


def test_cached_refusal_guide_is_not_served_from_ttl():
    """A cached guide that trips REFUSAL_MARKERS — the 403 「无法获取正文」
    placeholders cached before the marker existed — must NOT ride the 21-day
    TTL: it fails re-validation, falls through to regeneration, and with no
    grounding offline ends up empty so fill_deep_reasons drops/backfills it.
    A cached good guide is still served untouched."""
    refusal = (
        "【ChatGPT iOS 版更新至 1.2026.237】。原文页面返回 403 Forbidden 错误，"
        "由于源页面访问受限，无法获取并转述本次更新的实质信息，"
        "除标题所示版本号外无其他有效事实可供提取。"
    )
    title = "缓存淘汰测试标题"
    stats = {"reused": 0, "cached": 0, "generated": 0, "skipped": 0, "dropped": 0}

    # (a) cached refusal -> rejected on re-validation -> not served, ends empty
    bad_item = make_item(1, title=title)  # no summary, no upstream reason
    bad_cache = {
        "version": gwad.DEEP_CACHE_VERSION,
        "entries": {
            gwad.cache_key("story_1", title): {
                "reason": refusal,
                "title_hash": gwad.title_hash(title),
                "created_at": "2026-09-03T00:00:00Z",
            }
        },
    }
    outcome = gwad._fill_one_deep_reason(
        bad_item, bad_cache, {"api_key": "k"}, None, stats, None
    )
    assert bad_item.get("weixin_deep_reason", "") == ""  # placeholder not shipped
    assert stats["cached"] == 0
    assert outcome.startswith("回退上游")

    # (b) cached good guide -> served as before
    good_item = make_item(2, title=title)
    good_cache = {
        "version": gwad.DEEP_CACHE_VERSION,
        "entries": {
            gwad.cache_key("story_2", title): {
                "reason": LONG_DEEP_REASON,
                "title_hash": gwad.title_hash(title),
                "created_at": "2026-09-03T00:00:00Z",
            }
        },
    }
    outcome2 = gwad._fill_one_deep_reason(
        good_item, good_cache, {"api_key": "k"}, None, stats, None
    )
    assert good_item["weixin_deep_reason"] == LONG_DEEP_REASON
    assert stats["cached"] == 1
    assert outcome2 == "缓存"


def test_drop_cache_entries():
    cache = {
        "version": gwad.DEEP_CACHE_VERSION,
        "entries": {
            "story_1|aaa": {"reason": "甲"},
            "story_2|bbb": {"reason": "乙"},
        },
    }
    assert gwad.drop_cache_entries(cache, {"story_1"}) == 1
    assert list(cache["entries"]) == ["story_2|bbb"]
    assert gwad.drop_cache_entries(cache, {"没有这个条目"}) == 0


def test_regenerate_flag_forces_regeneration(tmp_path):
    """A cached entry is re-rolled when named via --regenerate."""
    data_dir, assets_dir = write_fixture(
        tmp_path, [make_item(1, summary=DEEP_SUMMARY)]
    )
    make_static_asset(assets_dir)
    deep_dir = tmp_path / "weixin-deep"
    args = [
        "--data-dir", str(data_dir),
        "--output-dir", str(deep_dir),
        "--assets-dir", str(assets_dir),
        "--no-images",
    ]

    side_effect, first_calls = make_deep_text_router(reason=text_response(LONG_DEEP_REASON))
    run_deep_patched(BASE_ENV, side_effect, offline_session(), args)
    assert first_calls["reason"] == 1

    side_effect, second_calls = make_deep_text_router(
        reason=text_response(LONG_DEEP_REASON)
    )
    rc = run_deep_patched(
        BASE_ENV, side_effect, offline_session(), args + ["--regenerate", "story_1"]
    )
    assert rc == 0
    assert second_calls["reason"] == 1  # 缓存被清除 → 重新生成


def test_regenerate_by_number_and_chinese_fragment(tmp_path):
    """The maintainer-friendly specs: a display number, or a fragment of the
    Chinese title as READ in the article (which only exists after the
    on-the-fly translation — matching must run late enough to see it)."""
    en_title = "Wire It, Run It, Deploy It: AI Workflows in Gradio"
    zh_title = "Gradio 串起 AI 工作流：接线、运行、部署一步到位"
    data_dir, assets_dir = write_fixture(
        tmp_path, [make_item(1, title=en_title, summary=DEEP_SUMMARY)]
    )
    make_static_asset(assets_dir)
    deep_dir = tmp_path / "weixin-deep"
    args = [
        "--data-dir", str(data_dir),
        "--output-dir", str(deep_dir),
        "--assets-dir", str(assets_dir),
        "--no-images",
    ]

    side_effect, first_calls = make_deep_text_router(
        reason=text_response(LONG_DEEP_REASON),
        translate=text_response(zh_title),
    )
    run_deep_patched(BASE_ENV, side_effect, offline_session(), args)
    assert first_calls["reason"] == 1 and first_calls["translate"] == 1

    # Re-roll by display number; the translation stays cached.
    side_effect, second_calls = make_deep_text_router(
        reason=text_response(LONG_DEEP_REASON),
        translate=AssertionError("translation is cached"),
    )
    rc = run_deep_patched(
        BASE_ENV, side_effect, offline_session(), args + ["--regenerate", "1"]
    )
    assert rc == 0
    assert second_calls["reason"] == 1 and second_calls["translate"] == 0

    # Re-roll by a Chinese display-title fragment.
    side_effect, third_calls = make_deep_text_router(
        reason=text_response(LONG_DEEP_REASON),
        translate=AssertionError("translation is cached"),
    )
    rc = run_deep_patched(
        BASE_ENV, side_effect, offline_session(), args + ["--regenerate", "接线、运行"]
    )
    assert rc == 0
    assert third_calls["reason"] == 1

    # An unmatched spec re-rolls nothing.
    side_effect, fourth_calls = make_deep_text_router(
        reason=AssertionError("nothing may be re-rolled on a miss"),
        translate=AssertionError("translation is cached"),
    )
    rc = run_deep_patched(
        BASE_ENV, side_effect, offline_session(), args + ["--regenerate", "不存在的片段"]
    )
    assert rc == 0
    assert fourth_calls["reason"] == 0


def test_deep_validation_bounds():
    good = LONG_DEEP_REASON
    title = "校验测试标题"
    assert gwad.validate_deep_reason(good, title) is True
    assert gwad.validate_deep_reason("据某媒体报道，这是一条很短的消息。", title) is False  # <80
    assert gwad.validate_deep_reason("好" * 450, title) is True  # 旧上限，仍合法
    assert gwad.validate_deep_reason("好" * 530, title) is True  # == 上限
    assert gwad.validate_deep_reason("好" * 531, title) is False  # 超出上限
    assert gwad.validate_deep_reason("a" * 120, title) is False  # no CJK
    assert gwad.validate_deep_reason(title, title) is False
    assert gwad.validate_deep_reason("据某媒体报道，详情见 https://example.com 。" * 5, title) is False
    assert gwad.validate_deep_reason("很抱歉，无法生成导读。" + "填" * 100, title) is False
    # fetch-blocked refusal (page 403'd / walled): no news, must be refused
    # so the item is dropped and backfilled rather than shipping a placeholder
    assert gwad.validate_deep_reason(
        "原文页面返回 403 Forbidden 错误，源页面访问受限，无法获取并转述本次更新的"
        "实质信息，除版本号外无其他有效事实可供提取。" + "填" * 60,
        title,
    ) is False


def test_generate_deep_reason_rejection_is_diagnosed(capsys):
    """A rejected generation must name its cause on stderr (no silent skips)."""
    item = make_item(1, title="诊断测试标题")
    cfg = {"api_key": "k", "base_url": "https://api.example/v1", "text_model": "m"}
    content_with_url = (
        "据 Example Source 报道，" + "内容" * 50
        + " 详见 https://github.com/openai/codex ，" + "。" * 10
    )

    with patch(
        "scripts.generate_weixin_article_deep.requests.post",
        return_value=text_response(content_with_url),
    ):
        assert gwad.generate_deep_reason(item, "正文内容若干", cfg) is None

    err = capsys.readouterr().err
    assert "含 URL" in err


def test_generate_deep_reason_rejects_overlong(capsys):
    """Overlong generations are rejected: the hard ceiling is back because
    rich full-text grounding makes the model overshoot into padded recaps."""
    item = make_item(1, title="超长导读测试标题")
    cfg = {"api_key": "k", "base_url": "https://api.example/v1", "text_model": "m"}
    long_content = "该团队发布了新版本，" + "这是用于凑字数的测试句子内容。" * 40  # ~600 字

    with patch(
        "scripts.generate_weixin_article_deep.requests.post",
        return_value=text_response(long_content),
    ):
        result = gwad.generate_deep_reason(item, "正文内容若干", cfg)

    assert result is None
    err = capsys.readouterr().err
    assert "超出上限" in err


def test_generate_deep_reason_compresses_overlong_draft(capsys):
    """An over-length draft is COMPRESSED (delete-only), not regenerated from
    scratch — the second call uses the compress prompt, and the compressed,
    valid guide flows on into the marking pass instead of dropping the item."""
    item = make_item(1, title="压缩兜底测试标题")
    cfg = {"api_key": "k", "base_url": "https://api.example/v1", "text_model": "m"}
    overlong = "该团队发布了新版本，" + "这是用于凑字数的测试句子内容。" * 40  # ~609 字
    reason_systems: list[str] = []

    def side_effect(url, **kwargs):
        messages = (kwargs.get("json") or {}).get("messages") or [{}]
        system = str((messages[0] or {}).get("content") or "")
        if "校对员" in system:  # highlight pass echoes the guide verbatim
            user = str((messages[-1] or {}).get("content") or "")
            return text_response(user)
        reason_systems.append(system)
        # 1st reason call = fresh generation (overshoots); 2nd = compression.
        return text_response(overlong if len(reason_systems) == 1 else LONG_DEEP_REASON)

    with patch("scripts.generate_weixin_article_deep.requests.post", side_effect=side_effect):
        result = gwad.generate_deep_reason(item, "正文内容若干", cfg)

    assert result == LONG_DEEP_REASON
    assert reason_systems[0] == gwad.DEEP_REASON_SYSTEM_PROMPT
    assert reason_systems[1] == gwad.DEEP_REASON_COMPRESS_SYSTEM_PROMPT
    err = capsys.readouterr().err
    assert "压缩重试" in err
    assert "强化重试" not in err   # over-length never takes the regen path
    assert "被校验拒绝" not in err


def test_generate_deep_reason_drops_when_compression_keeps_overshooting(capsys):
    """When both compression passes still overshoot the ceiling, the item
    degrades as before (empty reason → dropped/backfilled)."""
    item = make_item(1, title="压缩仍超长测试标题")
    cfg = {"api_key": "k", "base_url": "https://api.example/v1", "text_model": "m"}
    overlong = "该团队发布了新版本，" + "这是用于凑字数的测试句子内容。" * 40
    router, calls = make_deep_text_router(reason=text_response(overlong))

    with patch("scripts.generate_weixin_article_deep.requests.post", side_effect=router):
        result = gwad.generate_deep_reason(item, "正文内容若干", cfg)

    assert result is None
    assert calls["reason"] == 3   # generation + two compression passes
    assert calls["mark"] == 0     # never reached the highlight pass
    err = capsys.readouterr().err
    assert "压缩重试" in err
    assert "超出上限" in err
    assert "压缩后仍未过校验" in err


def test_generate_deep_reason_two_stage_compression_recovers_merged(capsys):
    """A merged big-story cluster whose draft overshoots, and whose FIRST
    compression still overshoots, is recovered by the second (tighter) pass —
    exactly the NVIDIA/GPT-6 case that used to drop with no fallback."""
    item = make_event_story(
        "rep", "NVIDIA 宣布以 129.303 亿美元收购 Hugging Face", summary=DEEP_SUMMARY
    )
    item["event_cluster"] = {
        "rep_source_count": 1,
        "members": [
            {"story_id": "m1", "title": "成员角度", "site": "aihot",
             "url": "https://m.test/1", "summary": "黄仁勋表示这桩联姻很合适"}
        ],
        "fingerprint": "fp1",
    }
    cfg = {"api_key": "k", "base_url": "https://api.example/v1", "text_model": "m"}
    overlong = "该团队发布了新版本，" + "这是用于凑字数的测试句子内容。" * 40    # ~609 (>530)
    still_over = "该团队发布了新版本，" + "这是用于凑字数的测试句子内容。" * 36  # ~549 (>530)
    reason_systems: list[str] = []

    def side_effect(url, **kwargs):
        messages = (kwargs.get("json") or {}).get("messages") or [{}]
        system = str((messages[0] or {}).get("content") or "")
        if "校对员" in system:
            user = str((messages[-1] or {}).get("content") or "")
            return text_response(user)
        reason_systems.append(system)
        if len(reason_systems) == 1:
            return text_response(overlong)      # merged generation overshoots
        if len(reason_systems) == 2:
            return text_response(still_over)    # compression #1 still >530
        return text_response(LONG_DEEP_REASON)  # compression #2 lands it

    with patch("scripts.generate_weixin_article_deep.requests.post", side_effect=side_effect):
        result = gwad.generate_deep_reason(item, "正文内容若干", cfg)

    assert result == LONG_DEEP_REASON
    assert reason_systems[0] == gwad.DEEP_REASON_MERGED_SYSTEM_PROMPT
    assert reason_systems[1] == gwad.DEEP_REASON_COMPRESS_SYSTEM_PROMPT
    assert reason_systems[2] == gwad.DEEP_REASON_COMPRESS_SYSTEM_PROMPT
    err = capsys.readouterr().err
    assert "压缩重试 #2" in err   # both passes were needed


def test_generate_deep_reason_too_short_regenerates_not_compresses(capsys):
    """A too-SHORT draft needs MORE content, not compression — it takes the
    original reinforced regeneration; the compress prompt is never used."""
    item = make_item(1, title="过短强化重试测试")
    cfg = {"api_key": "k", "base_url": "https://api.example/v1", "text_model": "m"}
    short = "据 Example Source 报道，该团队发布了新版本。"  # <80 字, otherwise valid
    reason_systems: list[str] = []

    def side_effect(url, **kwargs):
        messages = (kwargs.get("json") or {}).get("messages") or [{}]
        system = str((messages[0] or {}).get("content") or "")
        if "校对员" in system:
            user = str((messages[-1] or {}).get("content") or "")
            return text_response(user)
        reason_systems.append(system)
        return text_response(short if len(reason_systems) == 1 else LONG_DEEP_REASON)

    with patch("scripts.generate_weixin_article_deep.requests.post", side_effect=side_effect):
        result = gwad.generate_deep_reason(item, "正文内容若干", cfg)

    assert result == LONG_DEEP_REASON
    # Both reason calls use the SINGLE generation prompt (fresh regen with a
    # reminder in the USER turn), never the compress prompt.
    assert reason_systems == [
        gwad.DEEP_REASON_SYSTEM_PROMPT, gwad.DEEP_REASON_SYSTEM_PROMPT
    ]
    assert gwad.DEEP_REASON_COMPRESS_SYSTEM_PROMPT not in reason_systems
    err = capsys.readouterr().err
    assert "强化重试" in err
    assert "压缩重试" not in err


def test_parse_deep_marks():
    parsed = gwad.parse_deep_marks("甲【乙】丙【丁】")
    assert parsed == ("甲乙丙丁", [(1, 2), (3, 4)])
    assert gwad.parse_deep_marks("【甲】") == ("甲", [(0, 1)])
    assert gwad.parse_deep_marks("没有标记") == ("没有标记", [])
    assert gwad.parse_deep_marks("【不成对") is None
    assert gwad.parse_deep_marks("不成对】") is None
    assert gwad.parse_deep_marks("【【嵌套】】") is None
    assert gwad.parse_deep_marks("【】空的") is None
    assert gwad.strip_deep_marks("甲【乙】丙") == "甲乙丙"


def test_deep_marks_usable():
    plain = "这是一段足够长的导读文本内容。"  # 15 字
    assert gwad.deep_marks_usable(plain, []) is True
    assert gwad.deep_marks_usable(plain, [(0, 3), (4, 6)]) is True
    assert gwad.deep_marks_usable(plain, [(0, 1), (2, 3), (4, 5), (6, 7)]) is True  # 4 处合法
    assert gwad.deep_marks_usable(plain, [(0, 1), (2, 3), (4, 5), (6, 7), (8, 9)]) is False  # >4 处
    assert gwad.deep_marks_usable(plain, [(0, 12)]) is False  # 覆盖 ≥80%


def test_render_deep_reason_html():
    html = gwad.render_deep_reason_html("重点【结论句】收尾", "#13501B")
    assert html == '重点<strong style="color:#13501B;">结论句</strong>收尾'
    assert gwad.render_deep_reason_html("无标记", "#13501B") == "无标记"
    assert "<strong" not in gwad.render_deep_reason_html("【不成对", "#13501B")
    assert "【" not in gwad.render_deep_reason_html("【不成对", "#13501B")
    assert gwad.render_deep_reason_html("a<b", "#000000") == "a&lt;b"


def test_generate_deep_reason_keeps_valid_marks():
    """Generation and marking are two calls: guide first, brackets second."""
    item = make_item(1, title="高亮生成测试标题")
    cfg = {"api_key": "k", "base_url": "https://api.example/v1", "text_model": "m"}
    plain = (
        "该团队发布了新一代模型，" + "这是用于补足字数的测试句子。" * 8
        + "官方确认全面开源，定价为每月 10 美元。"
    )
    marked = (
        "该团队发布了新一代模型，" + "这是用于补足字数的测试句子。" * 8
        + "官方确认【全面开源】，定价为【每月 10 美元】。"
    )

    with patch(
        "scripts.generate_weixin_article_deep.requests.post",
        side_effect=[text_response(plain), text_response(marked)],
    ):
        result = gwad.generate_deep_reason(item, "正文内容若干", cfg)

    assert result == marked.strip()
    assert gwad.validate_deep_reason(gwad.strip_deep_marks(result), item["title"]) is True


def test_generate_deep_reason_salvages_bad_marks():
    """A bad marking pass never damages the guide: span choices are kept
    only when they can be re-anchored in the ORIGINAL text."""
    item = make_item(1, title="高亮降级测试标题")
    cfg = {"api_key": "k", "base_url": "https://api.example/v1", "text_model": "m"}
    body = "发布说明正文。" + "这是用于补足字数的测试句子。" * 8

    over_marked_response = (
        "【发布】说明【正文】。"
        + "【这是】用于【补足】字数的【测试】句子。"
        + "这是用于补足字数的测试句子。" * 7
    )
    with patch(
        "scripts.generate_weixin_article_deep.requests.post",
        side_effect=[text_response(body), text_response(over_marked_response)],
    ):
        over_marked = gwad.generate_deep_reason(item, "正文内容若干", cfg)
    # 5 处超限 → 截前 4 处，其余原文一字不动
    assert over_marked == (
        "【发布】说明【正文】。"
        + "【这是】用于【补足】字数的测试句子。"
        + "这是用于补足字数的测试句子。" * 7
    )

    marked_body = body.replace("发布说明正文", "【发布说明正文】", 1)
    with patch(
        "scripts.generate_weixin_article_deep.requests.post",
        side_effect=[
            text_response(body),
            text_response(marked_body + "（完）"),  # 改写了原文，但片段可锚定
        ],
    ):
        rewritten = gwad.generate_deep_reason(item, "正文内容若干", cfg)
    assert rewritten == marked_body  # 只保留能锚定的片段，改写被丢弃


def test_add_deep_marks_verbatim_and_failures():
    cfg = {"api_key": "k", "base_url": "https://api.example/v1", "text_model": "m"}
    guide = (
        "该团队发布了新一代模型，" + "这是用于补足字数的测试句子。" * 8
        + "官方确认全面开源。"
    )
    marked = guide.replace("全面开源", "【全面开源】")

    with patch(
        "scripts.generate_weixin_article_deep.requests.post",
        return_value=text_response(marked),
    ):
        assert gwad.add_deep_marks(guide, cfg) == marked

    with patch(
        "scripts.generate_weixin_article_deep.requests.post",
        return_value=text_response(marked + "（补充）"),  # 改动原文 → 重锚定后只留片段
    ):
        assert gwad.add_deep_marks(guide, cfg) == marked

    with patch(
        "scripts.generate_weixin_article_deep.requests.post",
        return_value=text_response("")), patch(  # 调用失败 → 弃标注
        "scripts.generate_weixin_article_deep.time.sleep"
    ):
        assert gwad.add_deep_marks(guide, cfg) == guide


def test_add_deep_marks_salvages_rewritten_punctuation():
    """The marker 'fixing' a missing period must not leak into the guide:
    its span choices survive, re-anchored in the untouched original."""
    cfg = {"api_key": "k", "base_url": "https://api.example/v1", "text_model": "m"}
    guide = "该团队发布了新一代模型，" + "这是用于补足字数的测试句子。" * 8 + "官方确认全面开源"
    response = guide.replace("全面开源", "【全面开源】") + "。"  # 模型补了句号

    with patch(
        "scripts.generate_weixin_article_deep.requests.post",
        return_value=text_response(response),
    ):
        result = gwad.add_deep_marks(guide, cfg)

    assert result == guide.replace("全面开源", "【全面开源】")
    assert not result.endswith("。")  # 原文没有的句号不会被带进来


def test_add_deep_marks_unanchorable_spans_drop(capsys):
    cfg = {"api_key": "k", "base_url": "https://api.example/v1", "text_model": "m"}
    guide = "该团队发布了新一代模型，" + "这是用于补足字数的测试句子。" * 8 + "官方确认全面开源。"

    with patch(
        "scripts.generate_weixin_article_deep.requests.post",
        return_value=text_response("【完全找不到出处的片段】"),
    ):
        assert gwad.add_deep_marks(guide, cfg, "锚定失败测试") == guide

    err = capsys.readouterr().err
    assert "标记片段与原文对不上" in err


def test_add_deep_marks_retry_recovers(capsys):
    """An unanchorable first attempt is diagnosed and a clean second attempt wins."""
    cfg = {"api_key": "k", "base_url": "https://api.example/v1", "text_model": "m"}
    guide = (
        "该团队发布了新一代模型，" + "这是用于补足字数的测试句子。" * 8
        + "官方确认全面开源。"
    )
    marked = guide.replace("全面开源", "【全面开源】")

    with patch(
        "scripts.generate_weixin_article_deep.requests.post",
        side_effect=[
            text_response(guide + "。"),  # 第一次只改写、没加标记 → 无片段可锚定
            text_response(marked),
        ],
    ):
        assert gwad.add_deep_marks(guide, cfg, "重试测试") == marked

    err = capsys.readouterr().err
    assert "标记片段与原文对不上" in err
    assert "重试测试" in err


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


def test_deep_grounding_scoped_to_article_body():
    # Recommendation-widget text below the body must not leak into the
    # guide grounding.
    thin_item = make_item(2, summary=THIN_SUMMARY)
    body = (
        "<article><p>" + "这是抓回来的正文内容，用来补足摘要的信息量。" * 20 + "</p></article>"
        "<h3>AI News Recommendations</h3>"
        "<p>推荐新闻标题不应进入导读取材。</p>"
    )
    session = FakeSession(text=body)

    grounding = gwad.deep_reason_context(thin_item, session)

    assert grounding and "抓回来的正文内容" in grounding
    assert "推荐新闻标题" not in grounding


def test_scope_to_article_body_gate_rejects_chrome_cards():
    # huggingface.co blog pages wrap ONLY sidebar cards in <article>; the
    # post sits outside every one. Gated (grounding) scoping must reject
    # such chrome and fall back to the whole page; ungated (image) scoping
    # keeps longest-<article>-wins so card thumbnails never leak in.
    cards = "<article>卡片一 • 479</article><article>卡片二 • 2.92k</article>"
    page = "<div>" + "正文内容。" * 100 + "</div>" + cards

    assert gwad.scope_to_article_body(page, "html") == "<article>卡片二 • 2.92k</article>"
    assert gwad.scope_to_article_body(
        page, "html", gwad.DEEP_ARTICLE_MIN_BODY_CHARS
    ) == page

    # A real body clears the gate and still wins over the cards.
    real = "<article><p>" + "正文。" * 400 + "</p></article>"
    assert gwad.scope_to_article_body(
        real + cards, "html", gwad.DEEP_ARTICLE_MIN_BODY_CHARS
    ) == real


def test_deep_grounding_survives_chrome_article_cards():
    # With every <article> being a small card, grounding must fall back to
    # the whole page instead of a ~100-char card snippet.
    thin_item = make_item(2, summary=THIN_SUMMARY)
    cards = "".join(
        f"<article>Card {i} • Text-to-Video • Updated Aug 25 • 479</article>"
        for i in range(20)
    )
    body = (
        "<html><body><nav>Models Datasets Spaces Docs</nav>"
        "<h1>Build Anything with gr.Workflow</h1>"
        "<div>" + "这是博客正文里的真实内容，介绍工作流怎么搭建。" * 30 + "</div>"
        + cards
        + "</body></html>"
    )
    session = FakeSession(text=body)

    grounding = gwad.deep_reason_context(thin_item, session)

    assert grounding and "真实内容" in grounding
    assert len(grounding) > 500  # the real body, not a card snippet


def test_body_scope_degraded_flags_unscopable_pages():
    # Chrome-only <article>s (all under the guard) and no recommendation
    # heading: scoping would fall back to the whole page -> degraded.
    cards = "".join(f"<article>related card {i} title byline</article>" for i in range(5))
    shell = f"<html><body><nav>Skip to content</nav>{cards}</body></html>"
    assert gwad.body_scope_degraded(shell, "html", gwad.DEEP_ARTICLE_MIN_BODY_CHARS)

    # A real <article> clearing the guard -> not degraded.
    real = f"<html><body>{cards}<article>{'real body sentence. ' * 40}</article></body></html>"
    assert not gwad.body_scope_degraded(real, "html", gwad.DEEP_ARTICLE_MIN_BODY_CHARS)

    # A recommendation-heading cut also counts as a body scope.
    rec = "<html><body><p>lead</p><h2>推荐阅读</h2><ul><li>x</li></ul></body></html>"
    assert not gwad.body_scope_degraded(rec, "html", gwad.DEEP_ARTICLE_MIN_BODY_CHARS)

    # Markdown: degraded only when no recommendation heading exists.
    assert gwad.body_scope_degraded("plain markdown", "markdown")
    assert not gwad.body_scope_degraded("body\n# 推荐阅读\ncards", "markdown")


class PerUrlSession:
    """Serves a fixed body per requested URL (records call order)."""

    def __init__(self, pages: dict[str, str]):
        self.pages = pages
        self.calls: list[str] = []

    def get(self, url, timeout=None, **kwargs):
        self.calls.append(str(url))
        return FakeResponse(200, self.pages.get(str(url), ""))


def test_deep_grounding_uses_reader_proxy_on_shell_page():
    # github.blog-style JS shell: the direct HTML is big enough to pass the
    # bot-wall floor, but every <article> is a profile/related card and the
    # whole page strips down to navigation text. The reader proxy's markdown
    # (which carries the real body) must win over that garbage.
    shell = (
        "<html><body><nav>Skip to content Blog Changelog Docs Customer stories</nav>"
        + "".join(f"<article>related card {i} title byline</article>" for i in range(5))
        + "</body></html>"
    )
    markdown = "Title: T\nMarkdown Content: " + "这是文章正文里的真实内容。" * 40
    session = PerUrlSession({
        "https://a.example/story": shell,
        "https://r.jina.ai/https://a.example/story": markdown,
    })
    item = make_item(1, summary=THIN_SUMMARY)
    item["url"] = "https://a.example/story"
    item["primary_url"] = "https://a.example/story"

    grounding = gwad.deep_reason_context(item, session, {})

    assert grounding and "真实内容" in grounding
    assert session.calls == [
        "https://a.example/story",
        "https://r.jina.ai/https://a.example/story",
    ]


def test_deep_grounding_uses_direct_page_when_shell_and_jina_down():
    # Reader proxy unavailable (circuit breaker): the page's own text is the
    # best grounding left — keep the legacy whole-page behavior.
    shell = (
        "<html><body><nav>Skip to content Blog Changelog Docs</nav>"
        + "".join(f"<article>related card {i} title byline</article>" for i in range(5))
        + "<p>" + "页面里仅有的可读文字内容。" * 20 + "</p></body></html>"
    )
    session = PerUrlSession({"https://a.example/story": shell})
    item = make_item(1, summary=THIN_SUMMARY)
    item["url"] = "https://a.example/story"
    item["primary_url"] = "https://a.example/story"

    grounding = gwad.deep_reason_context(item, session, {"jina_down": True})

    assert grounding and "可读文字内容" in grounding
    assert session.calls == ["https://a.example/story"]


def test_deep_grounding_skips_reader_proxy_when_body_scopes():
    # A page whose body DOES scope never pays for the second chance.
    body = (
        "<html><body><nav>menu</nav><article>"
        + "这是正文里的真实内容句子。" * 60
        + "</article></body></html>"
    )
    session = FakeSession(text=body)
    item = make_item(2, summary=THIN_SUMMARY)

    grounding = gwad.deep_reason_context(item, session, {})

    assert grounding and "真实内容" in grounding
    assert session.calls == ["https://example.com/story/2"]


def test_is_wall_text_markers():
    wall_zh = "## 环境异常\n当前环境异常，完成验证后即可继续访问。"
    wall_en = "Warning: This page maybe requiring CAPTCHA, make sure you are authorized."
    assert gwad.is_wall_text(wall_zh)
    assert gwad.is_wall_text(wall_en)
    assert not gwad.is_wall_text("这是正常的文章正文，介绍了一次模型发布。")
    assert not gwad.is_wall_text("")
    assert not gwad.is_wall_text(None)


def test_deep_grounding_thin_summary_when_no_fetch():
    # No usable session/URL: the thin summary still grounds the guide
    # instead of nothing.
    item = make_item(2, summary=THIN_SUMMARY)
    assert gwad.deep_reason_context(item, None) == THIN_SUMMARY


def test_deep_grounding_thin_summary_when_fetch_returns_wall():
    # WeChat case: the reader proxy comes back with a CAPTCHA wall that
    # clears FULL_TEXT_MIN_CHARS (padded by the proxy's own headers). The
    # wall must count as a failed fetch, so the thin summary wins.
    wall = (
        "Title: Weixin Official Accounts Platform\n\n"
        "URL Source: https://example.com/story/2\n\n"
        "Warning: This page maybe requiring CAPTCHA, please make sure you "
        "are authorized to access this page.\n\n"
        "Markdown Content:\n## 环境异常\n当前环境异常，完成验证后即可继续访问。\n"
    )
    session = FakeSession(text=wall)
    item = make_item(2, summary=THIN_SUMMARY)

    grounding = gwad.deep_reason_context(item, session, {})

    assert grounding == THIN_SUMMARY


def test_deep_grounding_thin_summary_on_shell_page_and_wall_proxy():
    # The observed incident end to end: mp.weixin.qq.com serves a JS shell
    # (no article in the server HTML), the reader proxy then returns the
    # CAPTCHA wall instead of the article. Both routes are unusable, so
    # the thin summary becomes the grounding.
    shell = (
        "<html><body><div>"
        + "： ， 。 视频 小程序 赞 ，轻点两下取消赞 在看 ，轻点两下取消在看 " * 10
        + "</div></body></html>"
    )
    wall = (
        "Title: Weixin Official Accounts Platform\n\n"
        "URL Source: https://mp.weixin.example/s?x=1\n\n"
        "Warning: This page maybe requiring CAPTCHA, please make sure you "
        "are authorized to access this page.\n\n"
        "Markdown Content:\n## 环境异常\n当前环境异常，完成验证后即可继续访问。\n"
    )
    session = PerUrlSession({
        "https://mp.weixin.example/s?x=1": shell,
        "https://r.jina.ai/https://mp.weixin.example/s?x=1": wall,
    })
    item = make_item(1, summary=THIN_SUMMARY)
    item["url"] = "https://mp.weixin.example/s?x=1"
    item["primary_url"] = "https://mp.weixin.example/s?x=1"

    grounding = gwad.deep_reason_context(item, session, {})

    assert grounding == THIN_SUMMARY
    assert session.calls == [
        "https://mp.weixin.example/s?x=1",
        "https://r.jina.ai/https://mp.weixin.example/s?x=1",
    ]


def test_deep_grounding_thin_summary_when_fetch_empty():
    # Fetch yields nothing usable (empty body): thin summary still grounds.
    session = FakeSession(text="")
    item = make_item(2, summary=THIN_SUMMARY)

    grounding = gwad.deep_reason_context(item, session, {})

    assert grounding == THIN_SUMMARY


def test_deep_grounding_prefers_real_body_over_thin_summary():
    # A successful full-text fetch still wins over the thin summary; the
    # fallback only fires when the fetch produces nothing usable.
    body = f"<p>{'这是抓回来的正文内容，用来补足摘要的信息量。' * 20}</p>"
    session = FakeSession(text=body)
    item = make_item(2, summary=THIN_SUMMARY)

    grounding = gwad.deep_reason_context(item, session, {})

    assert grounding and "抓回来的正文内容" in grounding
    assert THIN_SUMMARY not in grounding


def test_deep_meta_line_shows_channel_for_umbrella_bucket():
    # "Official AI Updates" and "AI HOT" are aggregate buckets, not
    # publishers: the meta line resolves them to the specific channel
    # (shared item_display_source). Real publisher names pass through, and
    # a bucket with no channel falls back to the bucket name.
    official = make_item(1, title="官方渠道元信息")
    official["weixin_deep_reason"] = LONG_DEEP_REASON
    official["source_name"] = "Official AI Updates"
    official["source"] = "OpenAI News"
    for src in official["sources"] + [official["primary_item"]]:
        src["source_name"] = "Official AI Updates"
        src["source"] = "OpenAI News"

    html = gwad.render_deep_item_html(official, 0, "#13501B")
    assert "OpenAI News · 1 个来源" in html
    assert "Official AI Updates" not in html

    aihot = make_item(2, title="热点渠道元信息")
    aihot["weixin_deep_reason"] = LONG_DEEP_REASON
    aihot["source_name"] = "AI HOT"
    aihot["source"] = "GitHub Blog"

    html = gwad.render_deep_item_html(aihot, 0, "#13501B")
    assert "GitHub Blog · 1 个来源" in html
    assert "AI HOT" not in html

    no_channel = make_item(3, title="无渠道兜底")
    no_channel["weixin_deep_reason"] = LONG_DEEP_REASON
    no_channel["source_name"] = "Official AI Updates"
    no_channel.pop("source", None)

    html = gwad.render_deep_item_html(no_channel, 0, "#13501B")
    assert "Official AI Updates · 1 个来源" in html


def test_deep_single_origin_meta_shows_source_and_reposts():
    # A single-origin story (source_count == 1, several entries linking the
    # same article) names the primary as 来源 and the other channels as 转载;
    # the channel-list line is dropped.
    url = "https://qwen.ai/blog?id=qwen3.8-flash-next"
    sources = [
        {"id": "p1", "title": "官方博客", "url": url,
         "source_name": "AI HOT", "source": "Qwen Blog"},
        {"id": "r1", "title": "镜像标题", "url": url,
         "source_name": "Buzzing", "source": "qwen.ai"},
        {"id": "r2", "title": "HN 讨论", "url": url,
         "source_name": "Info Flow", "source": "Hacker News"},
    ]
    item = make_item(1, title="开源新模型", sources=sources)
    item["source_count"] = 1
    item["source_name"] = "AI HOT"
    item["source"] = "Qwen Blog"
    item["primary_item"] = dict(
        item["primary_item"], id="p1", source_name="AI HOT", source="Qwen Blog"
    )

    html = gwad.render_deep_item_html(item, 0, "#13501B")
    assert "Qwen Blog · 1 个来源 · Buzzing, Info Flow · 2 个转载" in html
    assert "（Qwen Blog, Buzzing, Info Flow）" not in html


def test_deep_mixed_origins_split_by_url_not_entry_count():
    # Entries outnumber distinct URLs (official post + mirror + a second
    # origin such as the HN discussion page) → 「M 个来源 · N 个转载」 with
    # M = distinct canonical URLs and N = entries − URLs, instead of listing
    # every channel as a 来源.
    openai_url = "https://openai.com/index/hugging-face-incident"
    sources = [
        {"id": "m1", "title": "镜像", "url": openai_url, "source_name": "Buzzing"},
        {"id": "p1", "title": "官方原文", "url": openai_url,
         "source_name": "Official AI Updates", "source": "OpenAI News"},
        {"id": "n1", "title": "HN 讨论",
         "url": "https://news.ycombinator.com/item?id=49454314",
         "source_name": "NewsNow"},
    ]
    item = make_item(1, title="多出处混合故事", sources=sources)
    item["weixin_deep_reason"] = LONG_DEEP_REASON
    item["category"] = "official"
    item["source_count"] = 2
    item["source_name"] = "Official AI Updates"
    item["source"] = "OpenAI News"
    item["primary_item"] = dict(
        item["primary_item"], id="p1",
        source_name="Official AI Updates", source="OpenAI News",
    )

    html = gwad.render_deep_item_html(item, 0, "#13501B")
    assert "官方更新 · OpenAI News, NewsNow · 2 个来源 · Buzzing · 1 个转载" in html


def test_deep_reason_user_content_has_no_source_line():
    # Guides must not name their source: the user content is exactly
    # title + body, with no 信源 line (the channel appears only in the
    # meta line under the item).
    item = make_item(1, title="官方渠道新闻")
    item["source_name"] = "Official AI Updates"
    item["source"] = "OpenAI News"
    cfg = {"api_key": "k", "base_url": "https://api.example/v1", "text_model": "m"}
    captured = []
    router, calls = make_deep_text_router(reason=text_response(LONG_DEEP_REASON))

    def side_effect(url, **kwargs):
        captured.append(kwargs.get("json") or {})
        return router(url, **kwargs)

    with patch("scripts.generate_weixin_article_deep.requests.post", side_effect=side_effect):
        result = gwad.generate_deep_reason(item, "正文内容若干", cfg)

    assert result
    assert calls["reason"] == 1
    reason_user = captured[0]["messages"][1]["content"]
    assert reason_user == "标题：官方渠道新闻\n\n正文：\n正文内容若干"
    assert "信源" not in reason_user
    assert "OpenAI News" not in reason_user
    assert "Official AI Updates" not in reason_user


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


def test_extract_image_candidates_drops_byline_headshot():
    """The Verge puts the author headshot inside <article> ahead of the hero
    art, and its first <img> declares no dimensions (the 36x36 copy comes
    later), so only the URL skip-list can drop it — otherwise the byline
    portrait ships as the article illustration (observed 2026-09-07)."""
    base = "https://www.theverge.com/tech/985474/nvidia-buying-hugging-face"
    html = (
        '<img src="https://platform.theverge.com/wp-content/uploads/sites/2/'
        'chorus/author_profile_images/195820/JESSICA_WEATHERBED.0.jpg" '
        'alt="Jess Weatherbed">'
        '<img src="https://platform.theverge.com/wp-content/uploads/sites/2/'
        'chorus/uploads/chorus_asset/file/25835739/STKP210_JENSEN_HUANG_B.jpg" '
        'alt="Digital photo collage of Nvidia CEO Jensen Huang.">'
    )

    candidates = gwad.extract_image_candidates(html, base, "html")

    assert candidates == [
        "https://platform.theverge.com/wp-content/uploads/sites/2/"
        "chorus/uploads/chorus_asset/file/25835739/STKP210_JENSEN_HUANG_B.jpg"
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


def test_extract_image_candidates_scopes_to_article_body():
    # Recommendation-widget thumbnails live OUTSIDE <article>; they must not
    # become candidates even though they precede nothing and follow the body.
    base = "https://site.example.com/news/1"
    html = (
        '<img src="https://cdn.example.com/logo.png">'
        "<article><p>body</p>"
        '<img src="https://cdn.example.com/body.jpg" width="800">'
        "</article>"
        '<h3 class="text-xl">AI News Recommendations</h3>'
        '<img src="https://cdn.example.com/rec1.jpg" width="500">'
        '<img src="https://cdn.example.com/rec2.jpg" width="500">'
    )

    assert gwad.extract_image_candidates(html, base, "html") == [
        "https://cdn.example.com/body.jpg"
    ]


def test_extract_image_candidates_longest_article_wins():
    # Recommendation cards may use <article> too; the body is the longest one.
    base = "https://site.example.com/news/1"
    html = (
        "<article>"
        '<img src="https://cdn.example.com/teaser.jpg" width="500">'
        "</article>"
        "<article><p>" + "正文。" * 30 + "</p>"
        '<img src="https://cdn.example.com/body.jpg" width="800">'
        "</article>"
        "<article>"
        '<img src="https://cdn.example.com/card.jpg" width="500">'
        "</article>"
    )

    assert gwad.extract_image_candidates(html, base, "html") == [
        "https://cdn.example.com/body.jpg"
    ]


def test_extract_image_candidates_cuts_at_recommendation_heading():
    base = "https://site.example.com/news/1"
    html = (
        '<img src="https://cdn.example.com/body.jpg" width="800">'
        '<h2 class="x">推荐阅读</h2>'
        '<img src="https://cdn.example.com/rec.jpg" width="500">'
    )
    markdown = (
        "![body](https://cdn.example.com/body.jpg)\n"
        "## AI News Recommendations\n"
        "![rec](https://cdn.example.com/rec.jpg)\n"
    )

    assert gwad.extract_image_candidates(html, base, "html") == [
        "https://cdn.example.com/body.jpg"
    ]
    assert gwad.extract_image_candidates(markdown, base, "markdown") == [
        "https://cdn.example.com/body.jpg"
    ]


def test_extract_image_candidates_no_body_signal_keeps_whole_page():
    base = "https://site.example.com/news/1"
    html = (
        '<img src="https://cdn.example.com/a.jpg" width="800">'
        '<img src="https://cdn.example.com/b.jpg" width="800">'
    )

    assert gwad.extract_image_candidates(html, base, "html") == [
        "https://cdn.example.com/a.jpg",
        "https://cdn.example.com/b.jpg",
    ]


# ---------------------------------------------------------------------------
# Recommendation-card image borrowing
# ---------------------------------------------------------------------------

GOOD_CARD_ALT = "Google Gemma Downloads Exceed One Billion Barrier!"
UNRELATED_CARD_ALT = "Apple Announces New MacBook Air Lineup Today"


def html_response(html: str) -> MagicMock:
    """Streaming mock for a page fetch (bounded_get reads via iter_content)."""
    response = MagicMock()
    response.status_code = 200
    response.iter_content.return_value = [html.encode("utf-8")]
    return response


def borrow_page(heading: str, cards: list[tuple[str, str]]) -> str:
    """Page shape mirroring aibase: an image-less <article> body followed by
    recommendation cards (title in the img alt, thumbnail as src)."""
    card_html = "".join(
        f'<a target="_blank" href="/news/{30000 + n}">'
        f'<img alt="{alt}" src="{src}"></a>'
        for n, (alt, src) in enumerate(cards)
    )
    return (
        "<html><head><title>title</title></head><body>"
        f"<h1>{heading}</h1>"
        "<article><p>" + "纯文字正文，没有任何图片。" * 20 + "</p></article>"
        '<h3 class="text-xl">AI News Recommendations</h3>'
        f"{card_html}</body></html>"
    )


def test_title_similarity_bounds():
    assert gwad.title_similarity(
        "Google Gemma Downloads Exceed 1 Billion!",
        "google gemma downloads exceed 1 billion",
    ) == 1.0  # case and punctuation normalize away
    assert gwad.title_similarity(
        "Google Gemma Downloads Exceed One Billion Barrier",
        "Gemma Downloads Exceed One Billion Barrier",
    ) > gwad.REC_BORROW_MIN_SCORE
    # Same product, different event must stay under the floor.
    assert gwad.title_similarity(
        "Farewell Vanity Fire! OpenAI Acts Urgently: Codex Resets Quota Tomorrow",
        "OpenAI Fully Open Sources Codex Harness AI Project",
    ) < gwad.REC_BORROW_MIN_SCORE
    assert gwad.title_similarity("", "anything at all") == 0.0


def test_extract_page_heading():
    html = '<div><h1 class="x">All-Round <b>King</b> &amp; Co</h1><p>x</p></div>'
    assert gwad.extract_page_heading(html, "html") == "All-Round King & Co"
    assert gwad.extract_page_heading("<p>no heading here</p>", "html") == ""
    assert gwad.extract_page_heading("# Deep Title\n\nbody", "markdown") == "Deep Title"
    assert gwad.extract_page_heading("no heading line", "markdown") == ""


def test_extract_rec_image_cards_only_outside_body():
    page = borrow_page("Heading", [(GOOD_CARD_ALT, "https://cdn.example.com/rec.jpg")])
    assert gwad.extract_rec_image_cards(page, "https://site.example/news/1") == [
        (GOOD_CARD_ALT, "https://cdn.example.com/rec.jpg")
    ]
    # Without an identifiable body boundary there is no rec region to borrow from.
    flat = '<h1>H</h1><img alt="' + GOOD_CARD_ALT + '" src="https://cdn.example.com/x.jpg">'
    assert gwad.extract_rec_image_cards(flat, "https://site.example/news/1") == []
    # Reader markdown carries no card structure.
    assert (
        gwad.extract_rec_image_cards(page, "https://site.example/news/1", "markdown")
        == []
    )


def test_extract_rec_image_cards_unescapes_alt_entities():
    page = borrow_page("Heading", [
        ("Anthropic&#x27;s Flagship Model Faces Cold Reception",
         "https://cdn.example.com/rec.jpg"),
    ])
    assert gwad.extract_rec_image_cards(page, "https://site.example/news/1") == [
        ("Anthropic's Flagship Model Faces Cold Reception",
         "https://cdn.example.com/rec.jpg"),
    ]


def test_extract_rec_image_cards_filters():
    page = borrow_page("Heading", [
        ("", "https://cdn.example.com/noalt.jpg"),                        # empty alt
        ("ab", "https://cdn.example.com/shortalt.jpg"),                   # alt too short
        ("A Real Card Title", "https://cdn.example.com/logo-main.png"),   # skippable URL
        ("Another Real Card", "/rel/pic.jpg"),                            # relative
    ])
    assert gwad.extract_rec_image_cards(page, "https://site.example/news/1") == [
        ("Another Real Card", "https://site.example/rel/pic.jpg")
    ]


def test_pick_rec_borrow_image_same_story_wins():
    page = borrow_page(
        "Google Gemma Downloads Exceed One Billion Barrier",
        [
            (GOOD_CARD_ALT, "https://cdn.example.com/same.jpg"),
            (UNRELATED_CARD_ALT, "https://cdn.example.com/other.jpg"),
        ],
    )
    assert gwad.pick_rec_borrow_image(page, "https://site.example/news/1") == (
        "https://cdn.example.com/same.jpg",
        GOOD_CARD_ALT,
    )


def test_pick_rec_borrow_image_rejects_cross_event_and_ambiguous():
    # Same product, different event: below the score floor.
    page = borrow_page(
        "Farewell Vanity Fire! OpenAI Acts Urgently: Codex Resets Quota Tomorrow",
        [
            ("OpenAI Fully Open Sources Codex Harness AI Project",
             "https://cdn.example.com/a.jpg"),
            (UNRELATED_CARD_ALT, "https://cdn.example.com/b.jpg"),
        ],
    )
    assert gwad.pick_rec_borrow_image(page, "https://site.example/news/1") is None
    # Two near-identical matches with DIFFERENT images: no clear winner, the
    # margin gate keeps the item image-less (no-image beats a wrong image).
    page = borrow_page(
        "Google Gemma Downloads Exceed One Billion",
        [
            ("Google Gemma Downloads Surpass One Billion", "https://cdn.example.com/a.jpg"),
            ("Google Gemma Downloads Reach One Billion", "https://cdn.example.com/b.jpg"),
        ],
    )
    assert gwad.pick_rec_borrow_image(page, "https://site.example/news/1") is None


def test_pick_rec_borrow_image_shared_thumbnail_collapses():
    # Two cards sharing one image collapse into a single contender, so the
    # duplicate cannot eat the winner's margin.
    shared = "https://cdn.example.com/shared.jpg"
    page = borrow_page(
        "Google Gemma Downloads Exceed One Billion Barrier",
        [
            (GOOD_CARD_ALT, shared),
            ("Google Gemma Downloads Surpass One Billion Barrier", shared),
            (UNRELATED_CARD_ALT, "https://cdn.example.com/other.jpg"),
        ],
    )
    result = gwad.pick_rec_borrow_image(page, "https://site.example/news/1")
    assert result is not None and result[0] == shared


def test_pick_rec_borrow_image_needs_heading():
    page = borrow_page("", [(GOOD_CARD_ALT, "https://cdn.example.com/same.jpg")])
    assert gwad.pick_rec_borrow_image(page, "https://site.example/news/1") is None


def test_resolve_deep_cover_prefers_first_item_with_image(tmp_path):
    images_dir = tmp_path / "images"
    images_dir.mkdir()
    (images_dir / "story_2.jpg").write_bytes(make_png_bytes(600, 400))
    items = [
        {"story_id": "story_1"},  # top story has no image
        {"story_id": "story_2", "deep_image": "images/story_2.jpg"},
        {"story_id": "story_3", "deep_image": "images/story_3.jpg"},  # file missing
    ]

    result = gwad.resolve_deep_cover(items, tmp_path)

    assert result is not None
    cover_bytes, filename, rel = result
    assert filename == "cover.jpg"
    assert rel == "images/story_2.jpg"  # first item IN ORDER that has an image
    try:
        from PIL import Image

        img = Image.open(io.BytesIO(cover_bytes))
        assert img.size == (gwad.COVER_W, gwad.COVER_H)  # 2.35:1 crop
    except ImportError:
        pass

    # No item with a readable image → None, the caller falls back.
    assert (
        gwad.resolve_deep_cover(
            [
                {"story_id": "story_1"},
                {"story_id": "story_3", "deep_image": "images/story_3.jpg"},
            ],
            tmp_path,
        )
        is None
    )


def test_e2e_deep_cover_uses_top_item_illustration(tmp_path):
    data_dir, assets_dir = write_fixture(
        tmp_path, [make_item(1, summary=DEEP_SUMMARY)]
    )
    make_static_asset(assets_dir)
    deep_dir = tmp_path / "weixin-deep"
    args = [
        "--data-dir", str(data_dir),
        "--output-dir", str(deep_dir),
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
    # The cover is the headline item's own illustration cropped to 2.35:1.
    saved = (deep_dir / "images" / "story_1.jpg").read_bytes()
    cover = (deep_dir / "cover.jpg").read_bytes()
    assert cover == gwad.crop_cover(saved)
    meta = read_json(deep_dir / "meta.json")
    assert meta["cover"] == "cover.jpg"


def test_fill_deep_images_borrows_rec_card_image(tmp_path):
    page = borrow_page(
        "Google Gemma Downloads Exceed One Billion Barrier",
        [
            (GOOD_CARD_ALT, "https://cdn.example.com/same.jpg"),
            (UNRELATED_CARD_ALT, "https://cdn.example.com/other.jpg"),
        ],
    )
    session = MagicMock()
    session.get.side_effect = [
        html_response(page),                        # page fetch
        image_response(make_png_bytes(600, 400)),   # borrowed card image
    ]
    item = make_item(1, title="谷歌 Gemma 下载量突破十亿")

    found, missed, dup_avoided = gwad.fill_deep_images([item], session, tmp_path, {})

    assert (found, missed, dup_avoided) == (1, 0, 0)
    assert item["deep_image"] == "images/story_1.jpg"
    assert item["deep_image_credit"] == "example.com"
    assert item.get("deep_image_borrowed") is True
    assert (tmp_path / "images" / "story_1.jpg").exists()
    # Only the winning card's image was downloaded.
    assert session.get.call_count == 2
    assert session.get.call_args_list[1].args[0] == "https://cdn.example.com/same.jpg"


def test_fill_deep_images_body_image_preempts_borrow(tmp_path):
    # A body image always wins; rec cards never compete with it.
    page = (
        "<h1>Google Gemma Downloads Exceed One Billion Barrier</h1>"
        "<article><p>" + "这是足够长的正文文字内容。" * 30 + "</p>"
        '<img src="https://cdn.example.com/body.jpg"></article>'
        "<h3>AI News Recommendations</h3>"
        '<a href="/news/9"><img alt="' + GOOD_CARD_ALT + '" '
        'src="https://cdn.example.com/rec.jpg"></a>'
    )
    session = MagicMock()
    session.get.side_effect = [
        html_response(page),
        image_response(make_png_bytes(600, 400)),
    ]
    item = make_item(2, title="谷歌 Gemma 下载量突破十亿")

    found, missed, dup_avoided = gwad.fill_deep_images([item], session, tmp_path, {})

    assert (found, missed, dup_avoided) == (1, 0, 0)
    assert item["deep_image"] == "images/story_2.jpg"
    assert "deep_image_borrowed" not in item
    assert session.get.call_args_list[1].args[0] == "https://cdn.example.com/body.jpg"


def shell_page() -> str:
    """github.blog-style JS shell: every <article> is a short author or
    related-post card, there is no recommendation heading, and the real
    body is client-rendered — so body_scope_degraded(…, 300) is True and a
    whole-page image scan would return card chrome."""
    return (
        "<html><head><title>title</title></head><body>"
        "<h1>How we make AI coding more cost efficient</h1>"
        "<nav>" + "menu item " * 40 + "</nav>"
        '<article class="author-card"><p>Cassidy is a senior director.</p></article>'
        '<article class="related-post"><a href="/p/1">'
        '<img alt="GitHub Copilot app for Beginners" '
        'src="https://cdn.example.com/card-thumb.jpg"></a></article>'
        '<div id="app"></div>'
        "</body></html>"
    )


def test_fill_deep_images_shell_page_uses_reader_body(tmp_path):
    # On a JS shell the reader-rendered body supplies the candidates; the
    # whole-page card thumbnail must never become a candidate.
    page = shell_page()
    jina_md = (
        "Title: How we make AI coding more cost efficiency\n\n"
        "Intro paragraph.\n\n"
        "![Chart of A/B results](https://cdn.example.com/blog-graphic.png)\n\n"
        "More body text.\n"
    )
    session = MagicMock()
    session.get.side_effect = [
        html_response(page),                       # direct page fetch
        html_response(jina_md),                    # reader render of the body
        image_response(make_png_bytes(600, 400)),  # body figure download
    ]
    item = make_item(1, title="如何在保证任务质量的前提下降低 AI 编程成本")

    found, missed, dup_avoided = gwad.fill_deep_images([item], session, tmp_path, {})

    assert (found, missed, dup_avoided) == (1, 0, 0)
    assert item["deep_image"] == "images/story_1.jpg"
    assert "deep_image_borrowed" not in item
    urls = [call.args[0] for call in session.get.call_args_list]
    assert urls == [
        "https://example.com/story/1",
        f"{gwad.JINA_READER_BASE_URL}/https://example.com/story/1",
        "https://cdn.example.com/blog-graphic.png",  # NOT card-thumb.jpg
    ]


def test_fill_deep_images_shell_page_jina_down_no_image(tmp_path):
    # Shell page + reader unavailable: the card thumbnail is chrome, not a
    # body image and not a title-matched rec card — the item stays
    # image-less (宁缺毋错).
    session = MagicMock()
    session.get.side_effect = [html_response(shell_page())]
    item = make_item(2, title="AI 编程成本优化")

    found, missed, dup_avoided = gwad.fill_deep_images(
        [item], session, tmp_path, {"jina_down": True}
    )

    assert (found, missed, dup_avoided) == (0, 1, 0)
    assert "deep_image" not in item
    assert session.get.call_count == 1  # no reader attempt, no image fetch


def test_download_item_image_dedup_url_and_digest(tmp_path):
    # Same-issue dedup: a claimed URL is skipped without a request, and
    # byte-identical content from another URL (CDN mirror / reused card)
    # is rejected after download.
    bytes_a = make_png_bytes(600, 400)
    bytes_b = make_png_bytes(500, 300)
    dedupe = gwad.ImageDedup()
    session = MagicMock()
    session.get.side_effect = [
        image_response(bytes_a),  # story A claims a.jpg
        image_response(bytes_b),  # story B falls through to b.jpg
        image_response(bytes_a),  # story C's mirror serves A's exact bytes
    ]
    images_dir = tmp_path / "images"

    res_a = gwad.download_item_image(
        session, ["https://cdn.example.com/a.jpg"],
        images_dir, "story_a", "https://site.example/a", dedupe,
    )
    assert res_a is not None

    res_b = gwad.download_item_image(
        session,
        ["https://cdn.example.com/a.jpg", "https://cdn.example.com/b.jpg"],
        images_dir, "story_b", "https://site.example/b", dedupe,
    )
    assert res_b is not None
    assert session.get.call_args_list[1].args[0] == "https://cdn.example.com/b.jpg"
    assert dedupe.skips == 1  # URL-level skip, no request spent

    res_c = gwad.download_item_image(
        session, ["https://cdn.example.com/mirror.jpg"],
        images_dir, "story_c", "https://site.example/c", dedupe,
    )
    assert res_c is None
    assert dedupe.skips == 2  # digest-level skip after download
    assert not (images_dir / "story_c.jpg").exists()


# ---------------------------------------------------------------------------
# Image download
# ---------------------------------------------------------------------------

def test_fetch_page_html_direct_route_fallback_under_env_proxy():
    # Env-proxied session dies on a domestic host; the true-direct route
    # (trust_env=False) must rescue it before the jina fallback.
    gwad._DIRECT_SESSION = None
    page = "<html>" + "x" * 400 + "</html>"
    try:
        with patch.dict(
            "os.environ", {"HTTPS_PROXY": "http://127.0.0.1:7897"}
        ), patch.object(gwad, "create_session", return_value=FakeSession(text=page)):
            result = gwad.fetch_page_html(
                offline_session(), "https://www.aibase.com/news/1"
            )
    finally:
        gwad._DIRECT_SESSION = None

    assert result == (page, "html")


def test_fetch_page_html_no_env_proxy_skips_alt_route():
    gwad._DIRECT_SESSION = None
    try:
        with patch.dict("os.environ", {}, clear=True), patch.object(
            gwad, "create_session", side_effect=AssertionError("no alt route expected")
        ):
            result = gwad.fetch_page_html(
                offline_session(), "https://www.aibase.com/news/1", {"jina_down": True}
            )
    finally:
        gwad._DIRECT_SESSION = None

    assert result is None


def test_download_item_image_direct_route_fallback(tmp_path):
    # The proxied route SSL-fails on the domestic CDN; the same candidate
    # must be retried true-direct instead of being dropped.
    gwad._DIRECT_SESSION = None
    direct = MagicMock()
    direct.get.return_value = image_response(make_png_bytes(600, 400))
    proxied = MagicMock()
    proxied.get.side_effect = requests.exceptions.SSLError("intercepted")
    try:
        with patch.dict(
            "os.environ", {"HTTPS_PROXY": "http://127.0.0.1:7897"}
        ), patch.object(gwad, "create_session", return_value=direct):
            result = gwad.download_item_image(
                proxied,
                ["https://upload.chinaz.com/2026/0824/x.jpg"],
                tmp_path / "images",
                "story_9",
                "https://www.aibase.com/news/9",
            )
    finally:
        gwad._DIRECT_SESSION = None

    assert result == ("images/story_9.jpg", "aibase.com")
    assert (tmp_path / "images" / "story_9.jpg").exists()


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


def make_transparent_palette_png() -> bytes:
    """Palette PNG with byte-transparency (the openai.com case): converting
    it straight to RGB makes Pillow warn and maps transparent pixels to
    arbitrary palette colors."""
    from PIL import Image

    img = Image.new("P", (400, 300), color=1)
    img.putpalette([i % 256 for i in range(256 * 3)])
    buf = io.BytesIO()
    img.save(buf, format="PNG", transparency=bytes([0] * 128 + [255] * 128))
    return buf.getvalue()


def test_download_item_image_palette_transparency(tmp_path):
    session = MagicMock()
    session.get.return_value = image_response(
        make_transparent_palette_png(), "image/png"
    )
    images_dir = tmp_path / "images"

    with warnings.catch_warnings():
        warnings.simplefilter("error", UserWarning)  # old code path warned here
        result = gwad.download_item_image(
            session,
            ["https://cdn.example.com/p.png"],
            images_dir,
            "story_3",
            "https://example.com/a",
        )

    assert result == ("images/story_3.jpg", "example.com")
    assert (images_dir / "story_3.jpg").exists()


def test_bounded_get_enforces_budget_and_deadline():
    """The hang fixes: byte budget mid-stream and a wall-clock deadline."""
    session = MagicMock()

    # Byte budget: aborts once max_bytes is exceeded (no full buffering).
    session.get.return_value = image_response(b"x" * 1000)
    assert gwad.bounded_get(session, "https://a.example/x", 5.0, 500) is None

    # Small body inside the budget comes through intact.
    session.get.return_value = image_response(b"hello")
    assert gwad.bounded_get(session, "https://a.example/x", 5.0, 500) == b"hello"

    # Wall-clock deadline: the per-chunk requests timeout cannot catch a slow
    # trickle; bounded_get must stop on total elapsed time. Two chunks, the
    # monotonic clock jumps past the deadline before the second one.
    response = MagicMock()
    response.status_code = 200
    response.iter_content.return_value = [b"abcd", b"efgh"]
    session.get.return_value = response
    with patch(
        "scripts.generate_weixin_article_deep.time.monotonic",
        side_effect=[0.0, 0.5, 100.0],
    ):
        assert gwad.bounded_get(session, "https://a.example/x", 5.0, 500) is None


def test_jina_breaker_skips_after_first_failure():
    """Once the reader refuses connections, the rest of the run must not
    pay its timeout. Connection errors trip the breaker immediately."""
    session = MagicMock()
    not_found = MagicMock()
    not_found.status_code = 404
    session.get.side_effect = [
        not_found,                                    # direct, item A
        requests.ConnectionError("jina unreachable"),  # jina, item A
        not_found,                                    # direct, item B
    ]
    net_state: dict = {}

    assert gwad.fetch_page_html(session, "https://a.example/1", net_state) is None
    assert gwad.fetch_page_html(session, "https://a.example/2", net_state) is None

    assert net_state.get("jina_down") is True
    # Failed attempts are not memoized — the breaker alone stops retries.
    assert "https://a.example/1" not in net_state.get("jina_cache", {})
    urls = [call.args[0] for call in session.get.call_args_list]
    assert urls == [
        "https://a.example/1",
        "https://r.jina.ai/https://a.example/1",
        "https://a.example/2",  # item B: jina attempt skipped entirely
    ]


def test_jina_payload_memoized_per_url():
    """Guide grounding and image extraction both read the same shell page's
    reader render; the second request must hit the memo, not the rate limit."""
    session = MagicMock()
    session.get.return_value = html_response("markdown body")
    net_state: dict = {}

    first = gwad.fetch_jina_bytes(session, "https://a.example/1", 100000, net_state)
    second = gwad.fetch_jina_bytes(session, "https://a.example/1", 100000, net_state)

    assert first == second == b"markdown body"
    assert session.get.call_count == 1


def test_jina_soft_failures_trip_breaker_after_limit():
    """Timeouts/non-200 are 'slow render' shaped: one does not predict the
    next URL. The breaker trips only after READER_SOFT_FAILURE_LIMIT of them
    in a row (observed failure mode: a 16.8s warm render behind a 15s
    budget tripped the old first-failure breaker and blanked every later
    fallback of the run)."""
    session = MagicMock()
    not_found = MagicMock()
    not_found.status_code = 404
    session.get.return_value = not_found
    net_state: dict = {}

    for i in range(gwad.READER_SOFT_FAILURE_LIMIT - 1):
        assert gwad.fetch_jina_bytes(session, f"https://a.example/{i}", 100000, net_state) is None
        assert not net_state.get("jina_down")

    assert gwad.fetch_jina_bytes(session, "https://a.example/x", 100000, net_state) is None
    assert net_state.get("jina_down") is True
    assert session.get.call_count == gwad.READER_SOFT_FAILURE_LIMIT


def test_jina_success_resets_soft_failure_counter():
    session = MagicMock()
    not_found = MagicMock()
    not_found.status_code = 404
    session.get.side_effect = [
        not_found,                  # soft failure 1
        not_found,                  # soft failure 2
        html_response("markdown"),  # success: counter back to zero
        not_found,                  # soft failure 1 again
        not_found,                  # soft failure 2 again
    ]
    net_state: dict = {}

    assert gwad.fetch_jina_bytes(session, "https://a.example/1", 100000, net_state) is None
    assert gwad.fetch_jina_bytes(session, "https://a.example/2", 100000, net_state) is None
    assert gwad.fetch_jina_bytes(session, "https://a.example/3", 100000, net_state) == b"markdown"
    assert gwad.fetch_jina_bytes(session, "https://a.example/4", 100000, net_state) is None
    assert gwad.fetch_jina_bytes(session, "https://a.example/5", 100000, net_state) is None
    assert not net_state.get("jina_down")


def test_jina_timeout_counts_as_soft_failure():
    """A read timeout is a slow render, not a dead service: it counts
    toward the soft limit instead of tripping the breaker at once."""
    session = MagicMock()
    session.get.side_effect = requests.Timeout("render still running")
    net_state: dict = {}

    assert gwad.fetch_jina_bytes(session, "https://a.example/1", 100000, net_state) is None
    assert not net_state.get("jina_down")
    assert net_state.get("jina_soft_failures") == 1


def test_parse_attrs_unescapes_entities():
    # Server HTML entity-encodes attribute values; the literal &amp; in the
    # download URL 404s (observed: anthropic's /_next/image candidates).
    attrs = gwad._parse_attrs(
        '<img src="/_next/image?url=https%3A%2F%2Fcdn.example%2Fa.png&amp;w=3840&amp;q=75">'
    )
    assert attrs["src"] == "/_next/image?url=https%3A%2F%2Fcdn.example%2Fa.png&w=3840&q=75"


def test_image_candidates_lazy_attr_beats_placeholder_src():
    # ithome-style lazy loading: src is a 1x1 tracking pixel, the real
    # image URL sits in data-original and must win.
    page = (
        "<html><body><article>"
        "<p>" + "正文内容 " * 80 + "</p>"
        '<img src="//img.example/t.png" '
        'data-original="https://img.example/news/real.jpg">'
        "</article></body></html>"
    )
    candidates = gwad.extract_image_candidates(page, "https://www.ithome.com/0/1.htm", "html")
    assert candidates == ["https://img.example/news/real.jpg"]


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def test_render_deep_item_html_order_and_credit():
    item = make_item(1, title="深度版渲染测试")
    item["weixin_deep_reason"] = LONG_DEEP_REASON
    item["deep_image"] = "images/story_1.jpg"
    item["deep_image_credit"] = "example.com"

    html = gwad.render_deep_item_html(item, 0, "#13501B")

    assert "① 深度版渲染测试" in html
    assert '<img src="images/story_1.jpg"' in html
    assert "图源：example.com" in html
    # The guide leads, the image follows it — shrunk (aspect kept), centered
    # and square-cornered, not full-width.
    assert f"width:{gwad.DEEP_IMAGE_WIDTH_PERCENT}%" in html
    assert "width:100%" not in html
    assert "margin:0 auto" in html
    assert "border-radius" not in html
    assert (
        html.index("深度版渲染测试")
        < html.index(LONG_DEEP_REASON)
        < html.index("<img")
        < html.index("图源：example.com")
        < html.index("个来源")
        < html.index("原文：")
    )
    assert "<a " not in html


def test_render_deep_item_html_without_image_has_no_img_tag():
    item = make_item(1)
    item["weixin_deep_reason"] = LONG_DEEP_REASON

    html = gwad.render_deep_item_html(item, 0, "#595959")

    assert "<img" not in html
    assert "图源" not in html


def test_render_deep_item_html_image_without_guide_follows_title():
    # Degraded item (guide generation failed, upstream had none): the image
    # still renders, directly under the title.
    item = make_item(1, title="无导读有图测试")
    item["deep_image"] = "images/story_1.jpg"
    item["deep_image_credit"] = "example.com"

    html = gwad.render_deep_item_html(item, 0, "#595959")

    assert html.index("无导读有图测试") < html.index("<img")
    assert "图源：example.com" in html


def test_render_deep_item_html_highlights_marks_in_section_color():
    item = make_item(1, title="高亮渲染测试")
    item["weixin_deep_reason"] = (
        "该团队发布了新一代模型，" + "这是用于补足字数的测试句子。" * 8
        + "官方确认【全面开源】，并同步更新了文档。"
    )

    html = gwad.render_deep_item_html(item, 0, "#13501B")

    assert '<strong style="color:#13501B;">全面开源</strong>' in html
    assert "【" not in html and "】" not in html


def test_render_deep_group_section_threads_color_to_marks():
    item = make_item(1, title="分组高亮测试")
    item["category"] = "official"
    item["weixin_deep_reason"] = "更新内容说明。" * 10 + "结论是【正式发布】。"

    html = gwad.render_deep_group_section("official", [item])

    assert '<strong style="color:#13501B;">正式发布</strong>' in html


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
    deep_dir = tmp_path / "weixin-deep"
    args = [
        "--data-dir", str(data_dir),
        "--output-dir", str(deep_dir),
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
    # Keyless run: the cover degrades to the static fallback asset.
    assert meta["cover"] == "cover.png"
    assert (deep_dir / "cover.png").read_bytes() == (
        assets_dir / "weixin-cover-fallback.png"
    ).read_bytes()
    # The deep cache file is written into the deep output dir.
    assert (deep_dir / "reason-cache.json").exists()
    assert not (deep_dir / "images").exists()


def test_e2e_keyed_run_with_images(tmp_path):
    data_dir, assets_dir = write_fixture(
        tmp_path, [make_item(1, summary=DEEP_SUMMARY)]
    )
    make_static_asset(assets_dir)
    deep_dir = tmp_path / "weixin-deep"
    # A stale image from a previous day must be pruned.
    (deep_dir / "images").mkdir(parents=True)
    (deep_dir / "images" / "story_old.jpg").write_bytes(b"stale")
    args = [
        "--data-dir", str(data_dir),
        "--output-dir", str(deep_dir),
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
        "story_1": {
            "file": "images/story_1.jpg",
            "credit": "example.com",
            "borrowed": False,
        }
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


def test_english_title_translated_before_deep_guides(tmp_path):
    """A pure-English story title is translated BEFORE deep guides are
    written: the rendered title is Chinese and the guide cache key is
    derived from the translated title."""
    en_title = "Wire It, Run It, Deploy It: AI Workflows in Gradio"
    zh_title = "Gradio 串起 AI 工作流：接线、运行、部署一步到位"
    data_dir, assets_dir = write_fixture(
        tmp_path, [make_item(1, title=en_title, summary=DEEP_SUMMARY)]
    )
    make_static_asset(assets_dir)
    deep_dir = tmp_path / "weixin-deep"
    args = [
        "--data-dir", str(data_dir),
        "--output-dir", str(deep_dir),
        "--assets-dir", str(assets_dir),
        "--no-images",
    ]

    side_effect, calls = make_deep_text_router(
        reason=text_response(LONG_DEEP_REASON),
        translate=text_response(zh_title),
    )
    rc = run_deep_patched(BASE_ENV, side_effect, offline_session(), args)

    assert rc == 0
    assert calls["translate"] == 1
    html_text = (deep_dir / "index.html").read_text(encoding="utf-8")
    assert zh_title in html_text
    assert en_title not in html_text
    deep_cache = read_json(deep_dir / "reason-cache.json")
    assert gwad.cache_key("story_1", zh_title) in deep_cache["entries"]
    assert gwad.cache_key("story_1", en_title) not in deep_cache["entries"]


# ---------------------------------------------------------------------------
# Guide-writing driver: drop items without guide material, backfill from the
# over-selected candidate pool
# ---------------------------------------------------------------------------

def test_fill_deep_reasons_keyless_drops_empty_and_backfills():
    """Keyless deep reuses ANY-LENGTH upstream reasons; an item without any
    guide material is dropped and the next candidate moves up."""
    short_reason = "上游已有的一句短评。"
    candidates = [
        make_item(1, reason=short_reason),
        make_item(2),  # nothing anywhere -> empty deep guide -> dropped
        make_item(3, reason=short_reason),
    ]
    stats = {"reused": 0, "cached": 0, "generated": 0, "skipped": 0, "dropped": 0}
    cache = {"version": gwad.DEEP_CACHE_VERSION, "entries": {}}
    kept = gwad.fill_deep_reasons(
        candidates, cache, {"api_key": ""}, None, stats, None, max_items=2
    )
    assert [it["story_id"] for it in kept] == ["story_1", "story_3"]
    assert stats["dropped"] == 1
    assert stats["reused"] == 2


def test_fill_deep_reasons_empty_fallback_returns_top_candidates():
    """All-empty pool renders the unfiltered top max_items, as before."""
    candidates = [make_item(1), make_item(2), make_item(3)]
    stats = {"reused": 0, "cached": 0, "generated": 0, "skipped": 0, "dropped": 0}
    cache = {"version": gwad.DEEP_CACHE_VERSION, "entries": {}}
    kept = gwad.fill_deep_reasons(
        candidates, cache, {"api_key": ""}, None, stats, None, max_items=2
    )
    assert kept == candidates[:2]
    assert stats["dropped"] == 3


def guide_item(idx: int, category: str, has_guide: bool) -> dict:
    """Keyless-controllable candidate: an upstream reason keeps the item,
    none drops it (see fill_deep_reasons' keyless reuse behavior)."""
    item = make_item(idx, reason="上游已有的一句短评。" if has_guide else None)
    item["category"] = category
    return item


def test_fill_deep_reasons_official_drops_backfill_with_officials(monkeypatch):
    """A dropped official is replaced by the next OFFICIAL backup: the
    issue's category composition survives guide failures instead of
    drifting toward the backup pool's majority category."""
    monkeypatch.setenv("WEIXIN_OFFICIAL_CAP", "3")
    candidates = [
        guide_item(1, "official", False),  # dropped
        guide_item(2, "official", True),
        guide_item(3, "official", False),  # dropped
        guide_item(4, "industry", True),
        guide_item(5, "industry", True),
        guide_item(6, "official", True),   # official backup
        guide_item(7, "official", True),   # official backup
        guide_item(8, "industry", True),   # industry backup, never reached
    ]
    stats = {"reused": 0, "cached": 0, "generated": 0, "skipped": 0, "dropped": 0}
    cache = {"version": gwad.DEEP_CACHE_VERSION, "entries": {}}

    kept = gwad.fill_deep_reasons(
        candidates, cache, {"api_key": ""}, None, stats, None, max_items=5
    )

    assert [it["story_id"] for it in kept] == [
        "story_2", "story_6", "story_7",  # official quota (3) intact
        "story_4", "story_5",              # industry quota (2) intact
    ]
    assert stats["dropped"] == 2
    assert "weixin_deep_reason" not in candidates[7]  # early stop preserved


def test_fill_deep_reasons_cross_fills_when_stream_exhausts(monkeypatch):
    """When one category runs out of candidates the other cross-fills, so
    the issue still ships at its full size."""
    monkeypatch.setenv("WEIXIN_OFFICIAL_CAP", "3")
    candidates = [
        guide_item(1, "official", False),  # dropped
        guide_item(2, "official", False),  # dropped; officials exhausted
        guide_item(3, "industry", True),
        guide_item(4, "industry", True),
        guide_item(5, "industry", True),
        guide_item(6, "industry", True),
    ]
    stats = {"reused": 0, "cached": 0, "generated": 0, "skipped": 0, "dropped": 0}
    cache = {"version": gwad.DEEP_CACHE_VERSION, "entries": {}}

    kept = gwad.fill_deep_reasons(
        candidates, cache, {"api_key": ""}, None, stats, None, max_items=5
    )

    assert [it["story_id"] for it in kept] == [
        "story_3", "story_4", "story_5", "story_6"
    ]
    assert stats["dropped"] == 2


def test_fill_deep_reasons_cap_zero_keeps_flat_backfill(monkeypatch):
    """No cap = no quotas: the pool is consumed flat in score order."""
    monkeypatch.setenv("WEIXIN_OFFICIAL_CAP", "0")
    candidates = [
        guide_item(1, "official", False),  # dropped
        guide_item(2, "industry", True),   # promoted regardless of category
        guide_item(3, "official", True),
    ]
    stats = {"reused": 0, "cached": 0, "generated": 0, "skipped": 0, "dropped": 0}
    cache = {"version": gwad.DEEP_CACHE_VERSION, "entries": {}}

    kept = gwad.fill_deep_reasons(
        candidates, cache, {"api_key": ""}, None, stats, None, max_items=2
    )

    assert [it["story_id"] for it in kept] == ["story_2", "story_3"]
    assert stats["dropped"] == 1


def test_deep_pool_extra_resolution(monkeypatch):
    monkeypatch.delenv("WEIXIN_DEEP_POOL_EXTRA", raising=False)
    assert gwad.deep_pool_extra() == 10
    monkeypatch.setenv("WEIXIN_DEEP_POOL_EXTRA", "3")
    assert gwad.deep_pool_extra() == 3
    monkeypatch.setenv("WEIXIN_DEEP_POOL_EXTRA", "not-a-number")
    assert gwad.deep_pool_extra() == 10


# ---------------------------------------------------------------------------
# Same-event merge (weekly deep): signature, six gates, clustering, context.
# Fixtures mirror the real story-record shape merge_event_clusters consumes:
# sources[]/items[] are THE SAME list object, primary_item.id matches a ref,
# refs carry site_id (tier lookup) and the title family drives the signature.
# ---------------------------------------------------------------------------

MERGE_NOW = datetime(2026, 9, 7, 1, 9)


def make_event_story(
    sid: str,
    title: str,
    *,
    title_original: str | None = None,
    category: str = "industry",
    site_id: str = "aihot",
    source: str | None = None,
    score: float = 0.5,
    summary: str | None = None,
    url: str | None = None,
) -> dict:
    source = source or site_id
    url = url or f"https://{site_id}.test/{sid}"
    item_id = f"item_{sid}"
    ref = {
        "id": item_id,
        "title": title,
        "title_original": title_original,
        "url": url,
        "site_id": site_id,
        "source": source,
        "source_name": source,
        "summary": summary,
        "published_at": "2026-09-05T00:00:00Z",
    }
    refs = [ref]  # sources and items alias ONE list object (build_story_record)
    primary = {
        "id": item_id,
        "title": title,
        "title_original": title_original,
        "url": url,
        "source_name": source,
        "summary": summary,
    }
    return {
        "story_id": sid,
        "title": title,
        "title_original": title_original,
        "url": url,
        "primary_url": url,
        "category": category,
        "source_name": source,
        "source": source,
        "site_id": site_id,
        "source_count": 1,
        "item_count": 1,
        "duplicate_count": 1,
        "score": score,
        "importance_score": score,
        "sources": refs,
        "items": refs,
        "primary_item": primary,
    }


def _match(sig_a, sig_b, res_a, res_b, title_a, title_b, strong=None):
    """event_signals_match with hand-built signal/residual sets. ``strong``
    defaults to every token in both signatures (isolate gates 1/1b/3/5)."""
    if strong is None:
        strong = set(sig_a) | set(sig_b)
    return gwad.event_signals_match(
        frozenset(sig_a),
        frozenset(sig_b),
        set(res_a),
        set(res_b),
        title_a,
        title_b,
        set(strong),
    )


# --- _num_token_is_round: round figures collide, precise figures identify ---

def test_num_token_is_round():
    assert gwad._num_token_is_round("num:1.0e+09")  # $1B
    assert gwad._num_token_is_round("num:1.0e+07")  # $10M
    assert gwad._num_token_is_round("num:1.0e+04")  # 10,000
    assert not gwad._num_token_is_round("num:1.3e+10")  # $12.9B
    assert not gwad._num_token_is_round("num:3.5e+10")  # $35B
    assert not gwad._num_token_is_round("num:1.2e+03")  # 1,200
    assert not gwad._num_token_is_round("num:1.2e+07")  # $12.5M
    assert not gwad._num_token_is_round("model:gpt-6")  # not a num token
    assert not gwad._num_token_is_round("num:garbage")  # unparseable → safe


# --- event_signature: vendor / topic / model / number extraction ---

def test_event_signature_nvidia_acquisition():
    story = make_event_story(
        "s1", "NVIDIA to Acquire Hugging Face for $12.9 billion"
    )
    sig = gwad.event_signature(story)
    assert {"v:nvidia", "v:huggingface", "t:acquire", "num:1.3e+10"} <= sig


def test_event_signature_bilingual_both_sides_contribute():
    # Chinese display title + English original each feed the signature.
    story = make_event_story(
        "s1",
        "NVIDIA 宣布以 129.303 亿美元收购 Hugging Face",
        title_original="NVIDIA to Acquire Hugging Face",
    )
    sig = gwad.event_signature(story)
    assert "t:acquire" in sig  # 收购 + acquire
    assert "v:nvidia" in sig and "v:huggingface" in sig
    assert "num:1.3e+10" in sig  # 129.303 亿美元 ≈ $12.9B


def test_event_signature_fermat_specific_topic():
    story = make_event_story(
        "s1",
        "Anthropic 用 Claude 完成费马大定理首个 Lean 形式化证明",
        title_original="Formalizing Fermat's Last Theorem",
    )
    sig = gwad.event_signature(story)
    assert "t:fermat" in sig and "t:formal-proof" in sig
    assert "v:anthropic" in sig


# --- event_residual_tokens: stopword + vendor/topic surface stripping ---

def test_event_residual_strips_english_stops_and_surfaces():
    story = make_event_story(
        "s1", "NVIDIA releases a new AI model for the data center this year"
    )
    res = gwad.event_residual_tokens(story)
    # vendor + topic surfaces and generic function/domain words are gone
    assert "nvidia" not in res
    assert "ai" not in res and "model" not in res
    assert "the" not in res and "this" not in res and "year" not in res
    # discriminating object words survive
    assert "data" in res and "center" in res


def test_event_residual_reaction_title_can_be_empty():
    # A pure vendor+topic title leaves no object words → empty residual,
    # which the residual gate treats as absorbable (reactions/commentary).
    story = make_event_story("s1", "OpenAI 发布更新")
    assert gwad.event_residual_tokens(story) == set()


# --- _event_strong_tokens: DF weak line scales with the pool ---

def test_event_strong_tokens_df_line():
    # n=40 → weak_line = max(6, ceil(0.05*40)=2) = 6: df>=6 is weak.
    sigs = [frozenset({"v:openai"})] * 6 + [frozenset({"v:huggingface"})] * 5
    sigs += [frozenset({"model:gpt-6"})] * 40
    strong = gwad._event_strong_tokens(sigs, pool_size=40)
    assert "v:openai" not in strong  # df 6 >= weak line 6 → weak
    assert "v:huggingface" in strong  # df 5 < 6 → strong
    assert "model:gpt-6" in strong  # model: unconditionally strong

    # n=200 → weak_line = max(6, ceil(10)) = 10: a 6-df vendor is now strong.
    sigs2 = [frozenset({"v:openai"})] * 6 + [frozenset({"v:filler"})] * 10
    strong2 = gwad._event_strong_tokens(sigs2, pool_size=200)
    assert "v:openai" in strong2  # df 6 < 10 → strong in a big pool
    assert "v:filler" not in strong2  # df 10 >= 10 → weak


# --- event_signals_match: the six gates ---

def test_gate1_requires_two_shared_signals():
    assert _match(
        {"v:nvidia", "t:acquire"},
        {"v:nvidia", "t:release"},
        set(), set(),
        "Nvidia buys Hugging Face", "Nvidia releases driver",
    ) is None  # only v:nvidia shared


def test_gate1b_vendor_pair_without_event_type_is_not_an_event():
    # anthropic+meta co-occur across unrelated stories; no shared action.
    assert _match(
        {"v:anthropic", "v:meta"},
        {"v:anthropic", "v:meta"},
        {"spend", "compute"}, {"film", "festival"},
        "Meta and Anthropic compute spend", "Anthropic at Meta film festival",
    ) is None


def test_gate2_requires_a_shared_strong_signal():
    assert _match(
        {"v:openai", "t:release"},
        {"v:openai", "t:release"},
        set(), set(),
        "OpenAI releases ChatGPT update", "OpenAI launches new feature",
        strong=set(),  # nothing is strong in this pool
    ) is None


def test_gate3_overlap_ratio_floor():
    # shared 2 of 5 → 0.4 < 0.5: same vendor+action, different products.
    assert _match(
        {"v:x", "t:release", "model:m1", "num:1.1e+10", "person:p1"},
        {"v:x", "t:release", "model:m2", "num:1.2e+10", "person:p2"},
        set(), set(),
        "X releases m1", "X releases m2",
        strong={"v:x", "t:release"},
    ) is None


def test_gate5_distinctive_specific_topic_skips_residual():
    # fermat is a SPECIFIC topic → residual gate skipped even though the
    # two titles share no object words.
    shared = _match(
        {"v:anthropic", "t:fermat", "t:formal-proof"},
        {"v:anthropic", "t:fermat", "t:formal-proof"},
        {"lean", "proof"}, {"机器", "验证"},
        "Anthropic 费马大定理形式化证明",
        "Anthropic formalizes Fermat in Lean",
    )
    assert shared is not None and "t:fermat" in shared


def test_gate5_distinctive_vendor_pair_plus_topic_skips_residual():
    shared = _match(
        {"v:nvidia", "v:huggingface", "t:acquire", "num:1.3e+10"},
        {"v:nvidia", "v:huggingface", "t:acquire"},
        {"宣布以"}, {"buys", "front", "door"},
        "NVIDIA to Acquire Hugging Face for $12.9 billion",
        "Nvidia buys Hugging Face, the GitHub of AI",
    )
    assert shared is not None and "t:acquire" in shared


def test_gate5_round_number_collision_is_blocked():
    # Two unrelated OpenAI "$1 billion" offers: num:1.0e+09 is ROUND → not a
    # distinctive identifier → residual gate runs → disjoint residuals block.
    assert _match(
        {"num:1.0e+09", "v:openai"},
        {"num:1.0e+09", "v:openai"},
        {"ad", "business", "chatgpt", "annual"},
        {"cyber", "defence", "water", "banks"},
        "OpenAI says its ChatGPT ad business hits a $1 billion annual run rate",
        "OpenAI puts $1bn behind cyber defence for water utilities",
    ) is None


def test_gate5_precise_number_skips_residual():
    # num:1.2e+03 (1,200) is precise → distinctive → merges despite disjoint
    # residuals (the 1200-agents jailbreak story, two phrasings).
    shared = _match(
        {"num:1.2e+03", "v:openai"},
        {"num:1.2e+03", "v:openai"},
        {"智能体集体越狱攻击", "社区"}, {"agent", "秘密交流", "暴走"},
        "1200 个 AI 智能体集体越狱攻击开源社区",
        "1200个 Agent 秘密交流集体攻击 Hugging Face",
    )
    assert shared is not None and "num:1.2e+03" in shared


def test_gate5_residual_two_shared_tokens_merges():
    # Generic topic + single vendor → residual gate runs; {muse, spark} (2)
    # shared tokens confirm the same product (Muse Spark true pair).
    shared = _match(
        {"t:release", "v:meta"},
        {"t:release", "v:meta"},
        {"muse", "spark", "coding"},
        {"muse", "spark", "agentic"},
        "Meta 发布 Muse Spark 1.3 智能体能力提升",
        "Meta 发布 Muse Spark 1.3 编码能力",
    )
    assert shared is not None


def test_gate5_residual_single_family_token_is_blocked():
    # Muse Spark vs Muse Voice Transcribe: same vendor+release, but only the
    # family word "muse" is shared (1 < 2) → blocked (different products).
    assert _match(
        {"t:release", "v:meta"},
        {"t:release", "v:meta"},
        {"muse", "spark"},
        {"muse", "voice", "transcribe"},
        "Meta 发布 Muse Spark 1.3",
        "Meta Superintelligence Labs Releases Muse Voice Transcribe",
    ) is None


def test_gate5_empty_residual_reaction_passes():
    # A pure reaction (empty residual) merging into a generic vendor+topic
    # cluster is allowed: there are no object words to contradict.
    shared = _match(
        {"t:release", "v:meta"},
        {"t:release", "v:meta"},
        set(),  # reaction title reduced to nothing
        {"muse", "spark"},
        "Meta 发布 Muse Spark 1.3",
        "Meta 发布 Muse Spark 1.3 智能体能力提升",
    )
    assert shared is not None


# --- merge_event_clusters: clustering, record merge, invariants ---

def test_merge_picks_official_rep_and_folds_members():
    rep = make_event_story(
        "rep", "NVIDIA to Acquire Hugging Face for $12.9 billion",
        category="official", site_id="blogs.nvidia.com", source="NVIDIA Blog",
        score=0.90,
    )
    media = make_event_story(
        "media", "Nvidia buys Hugging Face, the GitHub of AI",
        site_id="theverge", source="The Verge", score=0.70,
    )
    social = make_event_story(
        "social", "NVIDIA 宣布收购 Hugging Face，黄仁勋称开放模型将受益",
        site_id="aihot", source="aihot", score=0.60,
    )
    surviving, absorbed, log = gwad.merge_event_clusters(
        [rep, media, social], by_id=None, now=MERGE_NOW, window_hours=168
    )
    assert [s["story_id"] for s in surviving] == ["rep"]
    assert {a["story_id"] for a in absorbed} == {"media", "social"}
    assert media["absorbed_into"] == "rep" and social["absorbed_into"] == "rep"
    # record merge: rep refs FIRST, members appended; sources IS items
    assert rep["sources"] is rep["items"]
    assert len(rep["sources"]) == 3
    assert rep["sources"][0]["id"] == "item_rep"
    assert rep["source_count"] == 3 and rep["item_count"] == 3
    cluster = rep["event_cluster"]
    assert cluster["rep_source_count"] == 1
    assert len(cluster["members"]) == 2
    assert cluster["fingerprint"]  # non-empty sha1 prefix
    # rep identity untouched (by_id rescoring / cache / --regenerate rely on it)
    assert rep["story_id"] == "rep" and rep["primary_item"]["id"] == "item_rep"
    assert len(log) == 1 and log[0]["representative"] == "rep"
    assert {m["story_id"] for m in log[0]["merged"]} == {"media", "social"}


def test_merge_kill_switch_passthrough(monkeypatch):
    monkeypatch.setenv("WEIXIN_EVENT_MERGE", "0")
    a = make_event_story("a", "NVIDIA to Acquire Hugging Face for $12.9 billion")
    b = make_event_story("b", "Nvidia buys Hugging Face, the GitHub of AI")
    surviving, absorbed, log = gwad.merge_event_clusters(
        [a, b], by_id=None, now=MERGE_NOW, window_hours=168
    )
    assert len(surviving) == 2 and absorbed == [] and log == []
    assert "absorbed_into" not in a and "absorbed_into" not in b


def test_merge_member_score_floor(monkeypatch):
    monkeypatch.delenv("WEIXIN_EVENT_MERGE", raising=False)
    rep = make_event_story(
        "rep", "NVIDIA to Acquire Hugging Face for $12.9 billion",
        category="official", score=0.90,
    )
    weak = make_event_story(
        "weak", "Nvidia buys Hugging Face, the GitHub of AI",
        score=gwad.EVENT_MERGE_MEMBER_MIN_SCORE - 0.05,  # below the floor
    )
    surviving, absorbed, log = gwad.merge_event_clusters(
        [rep, weak], by_id=None, now=MERGE_NOW, window_hours=168
    )
    assert {s["story_id"] for s in surviving} == {"rep", "weak"}
    assert absorbed == [] and log == []
    assert "absorbed_into" not in weak


def test_merge_full_connectivity_rejects_third_wheel():
    # A~B and A~C, but B!~C (B is a thin {model:gpt-6, t:release} title that
    # shares only model:gpt-6 with C's {model:gpt-6, v:openai}). The leader
    # cluster absorbs one; the other is rejected and survives on its own.
    a = make_event_story(
        "a", "OpenAI 发布 GPT-6 Astra", category="official", score=0.90
    )
    b = make_event_story("b", "GPT-6 Astra 正式推出", score=0.70)
    c = make_event_story(
        "c", "OpenAI says GPT-6 Astra is SOTA on benchmarks", score=0.60
    )
    surviving, absorbed, log = gwad.merge_event_clusters(
        [a, b, c], by_id=None, now=MERGE_NOW, window_hours=168
    )
    assert len(absorbed) == 1 and absorbed[0]["story_id"] == "b"
    assert {s["story_id"] for s in surviving} == {"a", "c"}
    assert a["event_cluster"]["rep_source_count"] == 1
    # the rejected pair is audited
    assert log and log[0].get("rejected_pairs") == [["b", "c"]]


def test_merge_max_cluster_cap(monkeypatch):
    monkeypatch.setattr(gwad, "EVENT_MERGE_MAX_CLUSTER", 2)
    stories = [
        make_event_story(
            sid, title, category="official" if sid == "r" else "industry",
            score=score,
        )
        for sid, title, score in [
            ("r", "NVIDIA to Acquire Hugging Face for $12.9 billion", 0.90),
            ("m1", "Nvidia buys Hugging Face, the GitHub of AI", 0.80),
            ("m2", "NVIDIA 宣布收购 Hugging Face，黄仁勋称开放模型将受益", 0.70),
        ]
    ]
    surviving, absorbed, log = gwad.merge_event_clusters(
        stories, by_id=None, now=MERGE_NOW, window_hours=168
    )
    # cap 2 == rep + 1 member; the third matching story is refused
    assert len(absorbed) == 1
    assert len(surviving) == 2


# --- merged_event_context: labelled blocks, degradation, member cap ---

def make_merged_item(rep_summary: str, members: list[dict]) -> dict:
    rep_ref = {
        "id": "item_rep", "title": "代表标题", "url": "https://rep.test/r",
        "site_id": "blogs.nvidia.com", "source": "NVIDIA Blog",
        "source_name": "NVIDIA Blog", "summary": rep_summary,
    }
    refs = [rep_ref]
    blocks = []
    for i, m in enumerate(members):
        site = m.get("site", "aihot")
        mref = {
            "id": f"item_m{i}", "title": m["title"],
            "url": f"https://m.test/{i}", "site_id": site,
            "source": site, "source_name": site, "summary": m.get("summary"),
        }
        refs.append(mref)
        blocks.append(
            {
                "story_id": f"story_m{i}", "title": m["title"], "site": site,
                "url": mref["url"], "summary": m.get("summary") or "",
            }
        )
    return {
        "story_id": "story_rep", "title": "代表标题",
        "url": "https://rep.test/r", "primary_url": "https://rep.test/r",
        "category": "official", "source_name": "NVIDIA Blog",
        "source_count": len(refs), "sources": refs, "items": refs,
        "primary_item": {
            "id": "item_rep", "title": "代表标题", "url": "https://rep.test/r",
            "source_name": "NVIDIA Blog", "summary": rep_summary,
        },
        "event_cluster": {
            "rep_source_count": 1, "members": blocks, "fingerprint": "abc123def456",
        },
    }


def test_merged_event_context_labels_and_degrades():
    item = make_merged_item(
        DEEP_SUMMARY,
        [
            {"title": "黄仁勋称开放模型将受益", "site": "x.com",
             "summary": "NVIDIA CEO 表示这桩联姻很合适"},
            {"title": "Verge: Nvidia buys HF", "site": "theverge"},  # no summary
        ],
    )
    ctx = gwad.merged_event_context(item, offline_session(), None)
    assert ctx is not None
    assert "【主源】" in ctx
    assert "【补充·x.com】黄仁勋称开放模型将受益" in ctx
    assert "NVIDIA CEO 表示" in ctx  # member summary woven in
    assert "【补充·theverge】Verge: Nvidia buys HF" in ctx
    # the no-summary member degrades to its title (no trailing colon+summary)
    assert "Verge: Nvidia buys HF：" not in ctx


def test_merged_event_context_caps_member_blocks(monkeypatch):
    monkeypatch.setattr(gwad, "EVENT_MERGE_CONTEXT_MEMBERS", 2)
    members = [
        {"title": f"成员标题{i}", "site": "aihot", "summary": f"摘要{i}"}
        for i in range(6)
    ]
    item = make_merged_item(DEEP_SUMMARY, members)
    ctx = gwad.merged_event_context(item, offline_session(), None)
    assert "成员标题0" in ctx and "成员标题1" in ctx
    assert "成员标题2" not in ctx  # beyond the CONTEXT_MEMBERS cap


def test_merged_event_context_truncates_to_rep_sources():
    # rep's OWN summary is short (< early-return floor) while a member carries
    # a long one: the 【主源】 grounding must come from the rep view (truncated
    # to rep_source_count), never from a member's summary masquerading as main.
    item = make_merged_item(
        "短摘要",  # rep summary too short to early-return on its own
        [{"title": "成员", "site": "aihot", "summary": DEEP_SUMMARY}],
    )
    ctx = gwad.merged_event_context(item, offline_session(), None)
    assert ctx is not None
    # the long member summary appears ONLY in its labelled 【补充】 block
    assert "【补充·aihot】成员" in ctx
    main_block = ctx.split("【补充")[0]
    assert DEEP_SUMMARY[:40] not in main_block


# --- prompt selection: merged clusters get the synthesis prompt ---

def _capture_reason_system(item):
    """Run generate_deep_reason offline, returning the system prompt used for
    the REASON call (the highlight pass is echoed)."""
    cfg = {"api_key": "k", "base_url": "https://api.example/v1", "text_model": "m"}
    captured: dict = {}

    def side_effect(url, **kwargs):
        messages = (kwargs.get("json") or {}).get("messages") or [{}]
        system = str((messages[0] or {}).get("content") or "")
        if "校对员" in system:  # highlight pass echoes the guide verbatim
            user = str((messages[-1] or {}).get("content") or "")
            return text_response(user)
        captured.setdefault("reason_system", system)
        return text_response(LONG_DEEP_REASON)

    with patch(
        "scripts.generate_weixin_article_deep.requests.post", side_effect=side_effect
    ):
        result = gwad.generate_deep_reason(item, "正文内容若干", cfg)
    return result, captured.get("reason_system")


def test_generate_deep_reason_uses_merged_prompt_for_clusters():
    item = make_event_story("rep", "NVIDIA 宣布以 129.303 亿美元收购 Hugging Face")
    item["event_cluster"] = {
        "rep_source_count": 1,
        "members": [
            {"story_id": "m1", "title": "成员", "site": "aihot",
             "url": "https://m.test/1", "summary": "角度"}
        ],
        "fingerprint": "fp1",
    }
    result, system = _capture_reason_system(item)
    assert result == LONG_DEEP_REASON
    assert system == gwad.DEEP_REASON_MERGED_SYSTEM_PROMPT


def test_generate_deep_reason_uses_single_prompt_without_cluster():
    item = make_event_story("s1", "NVIDIA 发布新驱动")  # no event_cluster
    _result, system = _capture_reason_system(item)
    assert system == gwad.DEEP_REASON_SYSTEM_PROMPT


# --- cache: symmetric cluster-fingerprint invalidation + no existing fallback ---

def _merged_cache_item(sid, title, fp, summary=DEEP_SUMMARY):
    item = make_event_story(sid, title, summary=summary)
    item["event_cluster"] = {
        "rep_source_count": 1,
        "members": [
            {"story_id": "m1", "title": "成员角度", "site": "aihot",
             "url": "https://m.test/1", "summary": "黄仁勋表示这桩联姻很合适"}
        ],
        "fingerprint": fp,
    }
    return item


def _empty_stats():
    return {"reused": 0, "cached": 0, "generated": 0, "skipped": 0, "dropped": 0}


def test_cache_fingerprint_match_serves_cached():
    title = "NVIDIA 宣布收购 Hugging Face"
    item = _merged_cache_item("rep", title, "fp1")
    key = gwad.cache_key("rep", title)
    cache = {
        "version": gwad.DEEP_CACHE_VERSION,
        "entries": {
            key: {"reason": LONG_DEEP_REASON, "title_hash": gwad.title_hash(title),
                  "cluster_fingerprint": "fp1", "created_at": "2026-09-03T00:00:00Z"}
        },
    }
    stats = _empty_stats()
    with patch(
        "scripts.generate_weixin_article_deep.requests.post",
        side_effect=AssertionError("cache hit must not call the API"),
    ):
        outcome = gwad._fill_one_deep_reason(
            item, cache, {"api_key": "k"}, None, stats, None
        )
    assert outcome == "缓存"
    assert item["weixin_deep_reason"] == LONG_DEEP_REASON
    assert stats["cached"] == 1


def test_cache_stale_fingerprint_regenerates():
    title = "NVIDIA 宣布收购 Hugging Face"
    item = _merged_cache_item("rep", title, "fp2")  # cluster membership changed
    key = gwad.cache_key("rep", title)
    stale = "这是一条过期的单角度缓存导读，指纹与当前事件簇不符，必须重新生成而非直接命中缓存。"
    cache = {
        "version": gwad.DEEP_CACHE_VERSION,
        "entries": {
            key: {"reason": stale, "title_hash": gwad.title_hash(title),
                  "cluster_fingerprint": "fp1", "created_at": "2026-09-03T00:00:00Z"}
        },
    }
    stats = _empty_stats()
    cfg = {"api_key": "k", "base_url": "https://api.example/v1", "text_model": "m"}
    router, calls = make_deep_text_router(reason=text_response(LONG_DEEP_REASON))
    with patch("scripts.generate_weixin_article_deep.requests.post", side_effect=router):
        outcome = gwad._fill_one_deep_reason(item, cache, cfg, None, stats, None)
    assert outcome == "生成"
    assert stats["generated"] == 1 and stats["cached"] == 0
    assert item["weixin_deep_reason"] == LONG_DEEP_REASON
    # entry rewritten with the CURRENT fingerprint, stale reason replaced
    assert cache["entries"][key]["cluster_fingerprint"] == "fp2"
    assert cache["entries"][key]["reason"] == LONG_DEEP_REASON


def test_cache_symmetric_dissolved_cluster_misses():
    # Item whose cluster dissolved (no fingerprint) must NOT be served a stale
    # MERGED entry (which carries one) — the check is symmetric ("" is a value).
    title = "NVIDIA 宣布收购 Hugging Face"
    item = make_event_story("rep", title, summary=DEEP_SUMMARY)  # NO event_cluster
    key = gwad.cache_key("rep", title)
    cache = {
        "version": gwad.DEEP_CACHE_VERSION,
        "entries": {
            key: {"reason": LONG_DEEP_REASON, "title_hash": gwad.title_hash(title),
                  "cluster_fingerprint": "fp1", "created_at": "2026-09-03T00:00:00Z"}
        },
    }
    stats = _empty_stats()
    cfg = {"api_key": "k", "base_url": "https://api.example/v1", "text_model": "m"}
    router, calls = make_deep_text_router(reason=text_response(LONG_DEEP_REASON))
    with patch("scripts.generate_weixin_article_deep.requests.post", side_effect=router):
        outcome = gwad._fill_one_deep_reason(item, cache, cfg, None, stats, None)
    assert outcome == "生成"  # regenerated as a SINGLE item, not served stale
    assert stats["generated"] == 1
    # single-item regeneration writes no cluster_fingerprint
    assert "cluster_fingerprint" not in cache["entries"][key]


def test_merged_generation_failure_never_uses_existing_reason():
    title = "NVIDIA 宣布收购 Hugging Face"
    item = _merged_cache_item("rep", title, "fp1")
    # Plant an upstream single-angle reason that MUST NOT backfill a merged
    # item (it would sneak a member's lone angle under the rep's title).
    item["primary_item"]["recommend_reason_zh"] = "上游单角度理由不应被采用"
    item["sources"][0]["recommend_reason_zh"] = "上游单角度理由不应被采用"
    cache = {"version": gwad.DEEP_CACHE_VERSION, "entries": {}}
    stats = _empty_stats()
    cfg = {"api_key": "k", "base_url": "https://api.example/v1", "text_model": "m"}
    overlong = "该团队发布了新版本，" + "这是用于凑字数的测试句子内容。" * 40  # rejected
    router, calls = make_deep_text_router(reason=text_response(overlong))
    with patch("scripts.generate_weixin_article_deep.requests.post", side_effect=router):
        outcome = gwad._fill_one_deep_reason(item, cache, cfg, None, stats, None)
    assert item.get("weixin_deep_reason", "") == ""  # empty → fill_deep_reasons drops it
    assert "上游单角度理由" not in str(item.get("weixin_deep_reason"))
    assert outcome == "合并导读生成失败"
    assert stats["skipped"] == 1 and stats["generated"] == 0
