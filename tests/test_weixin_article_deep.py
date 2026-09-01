"""Tests for scripts/generate_weixin_article_deep.py (3.0 精读版).

Pins the three upgrades over the grouped variant: top-20 selection, longer
repeated-news style guides in an INDEPENDENT cache (the shared 1.0/2.0 cache
must never leak in), and one real article image per item with graceful
no-image degradation. Mock plumbing mirrors test_weixin_article.py: text
completions through module-level requests.post, everything else through the
session returned by create_session — nothing touches the real network.
"""

from __future__ import annotations

import io
import json
import sys
import warnings
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import requests

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

    「精读」marks the deep guide prompt (NOT 「转述」 — 1.0's prompt contains
    「转述其观点」 and would collide); 「插画设计师」 marks the cover scene;
    「校对员」 marks the highlight pass, which by default (mark=None) echoes
    the guide back unchanged — no highlights, text preserved verbatim;
    「地道的简体中文」 marks the English-title backfill translation.
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
        assert gwad.resolve_deep_max_items(args) == 20
    with patch.dict("os.environ", {"WEIXIN_DEEP_MAX_ITEMS": "7"}, clear=True):
        assert gwad.resolve_deep_max_items(args) == 7
    # The 1.0/2.0 knob must have NO effect on the deep variant (use a value
    # unlike the deep default so an accidental read-through cannot pass).
    with patch.dict("os.environ", {"WEIXIN_MAX_ITEMS": "35"}, clear=True):
        assert gwad.resolve_deep_max_items(args) == 20
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
    now_cn = datetime(2026, 8, 28, 10, 0, tzinfo=gwa.TZ_CN)
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
    html_text = (out_dir / "index.html").read_text(encoding="utf-8")
    assert "以下为发布辅助信息" not in html_text
    assert "阅读原文：" not in html_text
    info = (out_dir / "publish-info.txt").read_text(encoding="utf-8")
    lines = info.strip().splitlines()
    assert lines[0].startswith("标题：") and "本周精读1条" in lines[0]
    assert lines[1].startswith("摘要：")
    assert lines[2].startswith("阅读原文：")


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
    assert gwad.validate_deep_reason("好" * 450, title) is True  # == 上限
    assert gwad.validate_deep_reason("好" * 451, title) is False  # 超出上限
    assert gwad.validate_deep_reason("a" * 120, title) is False  # no CJK
    assert gwad.validate_deep_reason(title, title) is False
    assert gwad.validate_deep_reason("据某媒体报道，详情见 https://example.com 。" * 5, title) is False
    assert gwad.validate_deep_reason("很抱歉，无法生成导读。" + "填" * 100, title) is False


def test_generate_deep_reason_rejection_is_diagnosed(capsys):
    """A rejected generation must name its cause on stderr (no silent skips)."""
    item = make_item(1, title="诊断测试标题")
    cfg = {"api_key": "k", "base_url": "https://api.example/v1", "text_model": "m"}
    content_with_url = (
        "据 Example Source 报道，" + "内容" * 50
        + " 详见 https://github.com/openai/codex ，" + "。" * 10
    )

    with patch(
        "scripts.generate_weixin_article.requests.post",
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
        "scripts.generate_weixin_article.requests.post",
        return_value=text_response(long_content),
    ):
        result = gwad.generate_deep_reason(item, "正文内容若干", cfg)

    assert result is None
    err = capsys.readouterr().err
    assert "超出上限" in err


def test_generate_deep_reason_retries_once_after_rejection(capsys):
    """A stochastic overshoot gets one reinforced retry: the valid second
    draft flows on into the marking pass instead of dropping the item."""
    item = make_item(1, title="重试成功测试标题")
    cfg = {"api_key": "k", "base_url": "https://api.example/v1", "text_model": "m"}
    overlong = "该团队发布了新版本，" + "这是用于凑字数的测试句子内容。" * 40
    router, calls = make_deep_text_router(
        reason=lambda c: text_response(overlong if c["reason"] == 1 else LONG_DEEP_REASON)
    )

    with patch("scripts.generate_weixin_article.requests.post", side_effect=router):
        result = gwad.generate_deep_reason(item, "正文内容若干", cfg)

    assert result == LONG_DEEP_REASON
    assert calls["reason"] == 2   # overshoot + reinforced retry
    assert calls["mark"] == 1     # the valid retry still gets highlighted
    err = capsys.readouterr().err
    assert "强化重试" in err
    assert "被校验拒绝" not in err


def test_generate_deep_reason_rejects_after_retry_also_fails(capsys):
    """When the retry trips the same bound, the item degrades as before."""
    item = make_item(1, title="重试失败测试标题")
    cfg = {"api_key": "k", "base_url": "https://api.example/v1", "text_model": "m"}
    overlong = "该团队发布了新版本，" + "这是用于凑字数的测试句子内容。" * 40
    router, calls = make_deep_text_router(reason=text_response(overlong))

    with patch("scripts.generate_weixin_article.requests.post", side_effect=router):
        result = gwad.generate_deep_reason(item, "正文内容若干", cfg)

    assert result is None
    assert calls["reason"] == 2
    err = capsys.readouterr().err
    assert "强化重试" in err
    assert "超出上限" in err


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
        "scripts.generate_weixin_article.requests.post",
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
        "scripts.generate_weixin_article.requests.post",
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
        "scripts.generate_weixin_article.requests.post",
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
        "scripts.generate_weixin_article.requests.post",
        return_value=text_response(marked),
    ):
        assert gwad.add_deep_marks(guide, cfg) == marked

    with patch(
        "scripts.generate_weixin_article.requests.post",
        return_value=text_response(marked + "（补充）"),  # 改动原文 → 重锚定后只留片段
    ):
        assert gwad.add_deep_marks(guide, cfg) == marked

    with patch(
        "scripts.generate_weixin_article.requests.post",
        return_value=text_response("")), patch(  # 调用失败 → 弃标注
        "scripts.generate_weixin_article.time.sleep"
    ):
        assert gwad.add_deep_marks(guide, cfg) == guide


def test_add_deep_marks_salvages_rewritten_punctuation():
    """The marker 'fixing' a missing period must not leak into the guide:
    its span choices survive, re-anchored in the untouched original."""
    cfg = {"api_key": "k", "base_url": "https://api.example/v1", "text_model": "m"}
    guide = "该团队发布了新一代模型，" + "这是用于补足字数的测试句子。" * 8 + "官方确认全面开源"
    response = guide.replace("全面开源", "【全面开源】") + "。"  # 模型补了句号

    with patch(
        "scripts.generate_weixin_article.requests.post",
        return_value=text_response(response),
    ):
        result = gwad.add_deep_marks(guide, cfg)

    assert result == guide.replace("全面开源", "【全面开源】")
    assert not result.endswith("。")  # 原文没有的句号不会被带进来


def test_add_deep_marks_unanchorable_spans_drop(capsys):
    cfg = {"api_key": "k", "base_url": "https://api.example/v1", "text_model": "m"}
    guide = "该团队发布了新一代模型，" + "这是用于补足字数的测试句子。" * 8 + "官方确认全面开源。"

    with patch(
        "scripts.generate_weixin_article.requests.post",
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
        "scripts.generate_weixin_article.requests.post",
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
    # Same split as 1.0: a single-origin story (source_count == 1, several
    # entries linking the same article) names the primary as 来源 and the
    # other channels as 转载; the channel-list line is dropped.
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
    # Same generalization as 1.0: entries outnumber distinct URLs (official
    # post + mirror + a second origin such as the HN discussion page) →
    # 「M 个来源 · N 个转载」 with M = distinct canonical URLs and
    # N = entries − URLs, instead of listing every channel as a 来源.
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

    with patch("scripts.generate_weixin_article.requests.post", side_effect=side_effect):
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
        assert img.size == (gwa.COVER_W, gwa.COVER_H)  # 2.35:1 crop
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
    main_dir = tmp_path / "weixin"
    seeded = seed_main_cover(main_dir)
    deep_dir = tmp_path / "weixin-deep"
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
    # The cover is the headline item's own illustration cropped to 2.35:1 —
    # NOT the seeded same-day main-variant cover.
    saved = (deep_dir / "images" / "story_1.jpg").read_bytes()
    cover = (deep_dir / "cover.jpg").read_bytes()
    assert cover == gwa.crop_cover(saved)
    assert cover != seeded
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

    found, missed = gwad.fill_deep_images([item], session, tmp_path, {})

    assert (found, missed) == (1, 0)
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

    found, missed = gwad.fill_deep_images([item], session, tmp_path, {})

    assert (found, missed) == (1, 0)
    assert item["deep_image"] == "images/story_2.jpg"
    assert "deep_image_borrowed" not in item
    assert session.get.call_args_list[1].args[0] == "https://cdn.example.com/body.jpg"


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
    """Once r.jina.ai fails, the rest of the run must not pay its timeout."""
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

    assert net_state == {"jina_down": True}
    urls = [call.args[0] for call in session.get.call_args_list]
    assert urls == [
        "https://a.example/1",
        "https://r.jina.ai/https://a.example/1",
        "https://a.example/2",  # item B: jina attempt skipped entirely
    ]


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
    assert gwa.cache_key("story_1", zh_title) in deep_cache["entries"]
    assert gwa.cache_key("story_1", en_title) not in deep_cache["entries"]


# ---------------------------------------------------------------------------
# Guide-writing driver: drop items without guide material, backfill from the
# over-selected candidate pool (mirror of the 1.0 fill_reasons driver)
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


def test_deep_pool_extra_resolution(monkeypatch):
    monkeypatch.delenv("WEIXIN_DEEP_POOL_EXTRA", raising=False)
    assert gwad.deep_pool_extra() == 10
    monkeypatch.setenv("WEIXIN_DEEP_POOL_EXTRA", "3")
    assert gwad.deep_pool_extra() == 3
    monkeypatch.setenv("WEIXIN_DEEP_POOL_EXTRA", "not-a-number")
    assert gwad.deep_pool_extra() == 10
    # The 1.0/2.0 knob must have NO effect on the deep variant.
    monkeypatch.setenv("WEIXIN_DEEP_POOL_EXTRA", "")
    monkeypatch.setenv("WEIXIN_POOL_EXTRA", "35")
    assert gwad.deep_pool_extra() == 10
