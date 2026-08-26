"""Tests for scripts/generate_weixin_article.py.

Mock plumbing notes:

- Text completions go through module-level ``requests.post``.
- Image generation and full-text/image downloads go through the
  ``requests.Session`` returned by ``create_session`` — so tests patch
  ``scripts.generate_weixin_article.create_session`` with a MagicMock
  session whose ``post`` / ``get`` are under test control. Nothing here
  ever touches the real network.
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import requests

from scripts import generate_weixin_article as gwa


# ---------------------------------------------------------------------------
# Fixture builders
# ---------------------------------------------------------------------------

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
    gwa._write_png(
        assets_dir / "weixin-cover-fallback.png", 120, 51, lambda x, y: (1, 2, 3)
    )


def make_png_bytes(width: int = 1000, height: int = 500) -> bytes:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "probe.png"
        gwa._write_png(path, width, height, lambda x, y: (x % 256, y % 256, 30))
        return path.read_bytes()


def text_response(content: str) -> MagicMock:
    response = MagicMock()
    response.status_code = 200
    response.raise_for_status.return_value = None
    response.json.return_value = {"choices": [{"message": {"content": content}}]}
    return response


def image_url_response(url: str = "https://img.example/cover.png") -> MagicMock:
    response = MagicMock()
    response.status_code = 200
    response.json.return_value = {
        "output": {
            "choices": [
                {
                    "finish_reason": "stop",
                    "message": {
                        "role": "assistant",
                        "content": [{"image": url}],
                    },
                }
            ]
        },
        "usage": {"image_count": 1},
    }
    return response


def offline_session() -> MagicMock:
    """Session mock that refuses all network I/O: cover/fetch degrade cleanly."""
    session = MagicMock()
    session.get.side_effect = requests.ConnectionError("offline")
    session.post.side_effect = requests.ConnectionError("offline")
    return session


def make_text_router(reason=None, scene=None, translate=None):
    """Mock module-level requests.post (text completions).

    Only reading-guide, cover-scene generation and the English-title
    backfill translation may hit the text API (the article title is a
    fixed template), so any other call fails the test. Returns
    (side_effect, counters). A spec may be a MagicMock response, an exception
    instance (raised), or a callable(counters) -> response.
    """
    calls = {"reason": 0, "scene": 0, "translate": 0}

    def side_effect(url, **kwargs):
        payload = kwargs.get("json") or {}
        messages = payload.get("messages")
        system = str(((messages or [{}])[0] or {}).get("content") or "")
        if "插画设计师" in system:
            which, spec = "scene", scene
        elif "地道的简体中文" in system:
            which, spec = "translate", translate
        elif "值得读" in system:
            which, spec = "reason", reason
        else:
            raise AssertionError(f"unexpected non-reason text api call: {url}")
        calls[which] += 1
        if spec is None:
            raise AssertionError(f"unexpected {which} api call: {url}")
        if isinstance(spec, BaseException):
            raise spec
        if isinstance(spec, MagicMock):
            # NB: MagicMock itself is callable, so check it before callable().
            return spec
        if callable(spec):
            return spec(calls)
        return spec

    return side_effect, calls


def run_main(
    data_dir: Path,
    output_dir: Path,
    assets_dir: Path,
    extra_args: list[str] | None = None,
) -> int:
    args = [
        "--data-dir", str(data_dir),
        "--output-dir", str(output_dir),
        "--assets-dir", str(assets_dir),
    ] + (extra_args or [])
    return gwa.main(args)


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def run_patched(env: dict, post_side_effect, session, args_list) -> int:
    """Run main with the text post router + a mocked session factory."""
    with patch.dict("os.environ", env, clear=True), patch(
        "scripts.generate_weixin_article.requests.post", side_effect=post_side_effect
    ), patch(
        "scripts.generate_weixin_article.create_session", return_value=session
    ), patch("scripts.generate_weixin_article.time.sleep"):
        return gwa.main(args_list)


BASE_ENV = {"DASHSCOPE_API_KEY": "test-key"}

# Long enough (>= gwa.REASON_MIN_REUSE_CHARS) to be reused without an LLM call.
LONG_EXISTING_REASON = (
    "这是上游已经写好的导读：新发布的模型在多项推理基准上较上一代提升约三成，"
    "单位推理成本下降近一半，上下文窗口扩展到百万级 token，并同步开放了权重与评测细节，"
    "适合关注模型进展的读者深入了解这次发布的具体内容与影响。"
    "这也让它成为当天最值得细读的发布说明之一。"
)
# Long enough (>= gwa.REASON_MIN_CHARS) to pass validate_reason.
LONG_GENERATED_REASON = (
    "OpenAI 发布了新一代模型，推理成本较上一代下降一半，"
    "上下文窗口扩展到 200 万 token，并同步开放 API 与评测细节，"
    "官方称其在多项基准上领先同级竞品。"
)
# Within gwa.COVER_SCENE_* bounds: a brand-free visual scene description.
COVER_SCENE_TEXT = (
    "两条发光的数据流从一座机柜流向另一座机柜，象征代码托管服务的迁移与替代。"
)


# ---------------------------------------------------------------------------
# Degradation & input guards
# ---------------------------------------------------------------------------

def test_no_key_degradation():
    with tempfile.TemporaryDirectory() as tmp:
        data_dir, assets_dir = write_fixture(tmp, [make_item(1), make_item(2)])
        make_static_asset(assets_dir)
        output_dir = Path(tmp) / "weixin"

        with patch.dict("os.environ", {}, clear=True), patch(
            "scripts.generate_weixin_article.requests.post"
        ) as mock_post, patch(
            "scripts.generate_weixin_article.create_session"
        ) as mock_session_factory:
            rc = run_main(data_dir, output_dir, assets_dir)

        assert rc == 0
        assert mock_post.call_count == 0
        assert mock_session_factory.call_count == 0
        html_text = (output_dir / "index.html").read_text(encoding="utf-8")
        assert "测试新闻标题 1" in html_text
        meta = read_json(output_dir / "meta.json")
        assert meta["delivery"] == "manual_copy"
        assert "今日精选2条" in meta["title"]  # template fallback title
        assert meta["cover"] == "cover.png"
        assert (output_dir / "cover.png").is_file()


def test_missing_brief():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        data_dir = root / "data"
        data_dir.mkdir()
        output_dir = root / "weixin"
        assets_dir = root / "assets"
        assets_dir.mkdir()

        with patch.dict("os.environ", BASE_ENV, clear=True), patch(
            "scripts.generate_weixin_article.requests.post"
        ) as mock_post:
            rc = run_main(data_dir, output_dir, assets_dir)

        assert rc == 0
        assert mock_post.call_count == 0
        assert not output_dir.exists()


def test_weixin_enabled_killswitch():
    with tempfile.TemporaryDirectory() as tmp:
        data_dir, assets_dir = write_fixture(tmp, [make_item(1)])
        output_dir = Path(tmp) / "weixin"

        with patch.dict("os.environ", {"WEIXIN_ENABLED": "0"}, clear=True), patch(
            "scripts.generate_weixin_article.requests.post"
        ) as mock_post:
            rc = run_main(data_dir, output_dir, assets_dir)

        assert rc == 0
        assert mock_post.call_count == 0
        assert not output_dir.exists()


# ---------------------------------------------------------------------------
# Recommend reasons
# ---------------------------------------------------------------------------

def test_existing_reason_reused_without_llm():
    """Keyless runs reuse a long upstream reason as-is (no network calls)."""
    reason = LONG_EXISTING_REASON
    with tempfile.TemporaryDirectory() as tmp:
        data_dir, assets_dir = write_fixture(
            tmp, [make_item(1, reason=reason, summary="摘要内容")]
        )
        make_static_asset(assets_dir)
        output_dir = Path(tmp) / "weixin"

        with patch.dict("os.environ", {}, clear=True), patch(
            "scripts.generate_weixin_article.requests.post"
        ) as mock_post, patch(
            "scripts.generate_weixin_article.create_session"
        ) as mock_session_factory:
            rc = run_main(data_dir, output_dir, assets_dir)

        assert rc == 0
        assert mock_post.call_count == 0
        assert mock_session_factory.call_count == 0
        html_text = (output_dir / "index.html").read_text(encoding="utf-8")
        assert reason in html_text


def test_keyed_run_prefers_qwen_over_upstream_reason():
    """With a key, Qwen is the single guide author: even a long upstream
    reason is replaced by a freshly generated one."""
    with tempfile.TemporaryDirectory() as tmp:
        data_dir, assets_dir = write_fixture(
            tmp,
            [
                make_item(
                    1,
                    reason=LONG_EXISTING_REASON,
                    summary="这是一段足够长的摘要内容，用于生成推荐语。",
                )
            ],
        )
        make_static_asset(assets_dir)
        output_dir = Path(tmp) / "weixin"
        side_effect, calls = make_text_router(
            reason=text_response(LONG_GENERATED_REASON),
            scene=text_response(COVER_SCENE_TEXT),
        )

        rc = run_patched(
            BASE_ENV,
            side_effect,
            offline_session(),
            [
                "--data-dir", str(data_dir),
                "--output-dir", str(output_dir),
                "--assets-dir", str(assets_dir),
            ],
        )

        assert rc == 0
        assert calls["reason"] == 1
        html_text = (output_dir / "index.html").read_text(encoding="utf-8")
        assert LONG_GENERATED_REASON in html_text
        assert LONG_EXISTING_REASON not in html_text


def test_reason_fill_success_writes_cache():
    generated = LONG_GENERATED_REASON
    with tempfile.TemporaryDirectory() as tmp:
        data_dir, assets_dir = write_fixture(
            tmp, [make_item(1, summary="这是一段足够长的摘要内容，用于生成推荐语。")]
        )
        make_static_asset(assets_dir)
        output_dir = Path(tmp) / "weixin"
        side_effect, calls = make_text_router(
            reason=text_response(generated),
            scene=text_response(COVER_SCENE_TEXT),
        )

        rc = run_patched(
            BASE_ENV,
            side_effect,
            offline_session(),
            [
                "--data-dir", str(data_dir),
                "--output-dir", str(output_dir),
                "--assets-dir", str(assets_dir),
            ],
        )

        assert rc == 0
        assert calls["reason"] == 1
        html_text = (output_dir / "index.html").read_text(encoding="utf-8")
        assert generated in html_text
        cache = read_json(output_dir / "reason-cache.json")
        entries = cache["entries"]
        assert len(entries) == 1
        entry = next(iter(entries.values()))
        assert entry["reason"] == generated
        assert entry["title_hash"]


def test_reason_cache_hit_skips_llm():
    generated = LONG_GENERATED_REASON
    with tempfile.TemporaryDirectory() as tmp:
        data_dir, assets_dir = write_fixture(
            tmp, [make_item(1, summary="这是一段足够长的摘要内容，用于生成推荐语。")]
        )
        make_static_asset(assets_dir)
        output_dir = Path(tmp) / "weixin"
        base_args = [
            "--data-dir", str(data_dir),
            "--output-dir", str(output_dir),
            "--assets-dir", str(assets_dir),
        ]

        side_effect, first_calls = make_text_router(
            reason=text_response(generated),
            scene=text_response(COVER_SCENE_TEXT),
        )
        run_patched(BASE_ENV, side_effect, offline_session(), base_args)
        assert first_calls["reason"] == 1

        side_effect, second_calls = make_text_router(
            reason=AssertionError("cached reason must not be regenerated"),
            scene=text_response(COVER_SCENE_TEXT),
        )
        rc = run_patched(BASE_ENV, side_effect, offline_session(), base_args)

        assert rc == 0
        assert second_calls["reason"] == 0
        html_text = (output_dir / "index.html").read_text(encoding="utf-8")
        assert generated in html_text


def test_reason_validation_rejects_bad_output():
    bad_reason = "too short no cjk"
    with tempfile.TemporaryDirectory() as tmp:
        data_dir, assets_dir = write_fixture(
            tmp, [make_item(1, summary="这是一段足够长的摘要内容，用于生成推荐语。")]
        )
        make_static_asset(assets_dir)
        output_dir = Path(tmp) / "weixin"
        side_effect, calls = make_text_router(
            reason=text_response(bad_reason),
            scene=text_response(COVER_SCENE_TEXT),
        )

        rc = run_patched(
            BASE_ENV,
            side_effect,
            offline_session(),
            [
                "--data-dir", str(data_dir),
                "--output-dir", str(output_dir),
                "--assets-dir", str(assets_dir),
            ],
        )

        assert rc == 0
        assert calls["reason"] == 1
        html_text = (output_dir / "index.html").read_text(encoding="utf-8")
        assert bad_reason not in html_text


def test_fetch_failure_skips_reason_without_llm():
    with tempfile.TemporaryDirectory() as tmp:
        # No summary anywhere: the script must try to fetch full text, fail,
        # and skip the reason instead of generating without grounding.
        data_dir, assets_dir = write_fixture(tmp, [make_item(1, summary=None)])
        make_static_asset(assets_dir)
        output_dir = Path(tmp) / "weixin"
        side_effect, calls = make_text_router(
            reason=AssertionError("no grounding => no reason call"),
            scene=text_response(COVER_SCENE_TEXT),
        )

        rc = run_patched(
            BASE_ENV,
            side_effect,
            offline_session(),
            [
                "--data-dir", str(data_dir),
                "--output-dir", str(output_dir),
                "--assets-dir", str(assets_dir),
            ],
        )

        assert rc == 0
        assert calls["reason"] == 0


# ---------------------------------------------------------------------------
# Title / digest
# ---------------------------------------------------------------------------

def test_title_always_uses_fixed_template():
    """The title is the fixed template even with an API key; nothing calls
    the text API for it."""
    with tempfile.TemporaryDirectory() as tmp:
        data_dir, assets_dir = write_fixture(
            tmp, [make_item(1, reason=LONG_EXISTING_REASON)]
        )
        make_static_asset(assets_dir)
        output_dir = Path(tmp) / "weixin"
        side_effect, calls = make_text_router(
            reason=AssertionError("the title is a fixed template, not an LLM call"),
            scene=text_response(COVER_SCENE_TEXT),
        )

        rc = run_patched(
            BASE_ENV,
            side_effect,
            offline_session(),
            [
                "--data-dir", str(data_dir),
                "--output-dir", str(output_dir),
                "--assets-dir", str(assets_dir),
            ],
        )

        assert rc == 0
        assert calls["reason"] == 0
        meta = read_json(output_dir / "meta.json")
        assert meta["title"].startswith("AI 雷达")
        assert "今日精选1条" in meta["title"]
        assert len(meta["title"]) <= gwa.TITLE_MAX_CHARS


def test_digest_length_contract():
    long_headline = "这是一个非常长的头条新闻标题，" * 10
    digest = gwa.make_digest("AI 雷达", 20, long_headline, "8月14日 周五")
    assert len(digest) <= gwa.DIGEST_MAX_CHARS
    assert digest.startswith("AI 雷达")


def test_fallback_title_contract():
    from datetime import datetime

    now_cn = datetime(2026, 8, 14, 8, 0, tzinfo=gwa.TZ_CN)
    title = gwa.fallback_title("AI 雷达", now_cn, 20)
    assert len(title) <= gwa.TITLE_MAX_CHARS


# ---------------------------------------------------------------------------
# Cover
# ---------------------------------------------------------------------------

def test_negative_headline_switches_to_brand_prompt():
    prompt, mode = gwa.build_cover_prompt("某大厂宣布大规模裁员两万人")
    assert mode == "brand"
    assert prompt == gwa.BRAND_COVER_PROMPT

    prompt, mode = gwa.build_cover_prompt("OpenAI 发布新模型")
    assert mode == "headline"
    assert "OpenAI" in prompt


def test_cover_prompt_prefers_scene_over_raw_headline():
    headline = "Cursor 推出 Origin 代码托管服务，作为 GitHub 的替代方案"
    prompt, mode = gwa.build_cover_prompt(headline, scene=COVER_SCENE_TEXT)
    assert mode == "headline"
    assert COVER_SCENE_TEXT in prompt
    assert "GitHub" not in prompt
    assert "Cursor" not in prompt
    # Without a scene the raw headline remains the theme (last resort).
    raw_prompt, raw_mode = gwa.build_cover_prompt(headline)
    assert raw_mode == "headline"
    assert "GitHub" in raw_prompt


def test_validate_cover_scene_rejects_bad_output():
    assert gwa.validate_cover_scene("") is False
    assert gwa.validate_cover_scene("brand logo only") is False
    assert gwa.validate_cover_scene("太短") is False
    assert gwa.validate_cover_scene("一个场景" * 30) is False
    assert gwa.validate_cover_scene("https://x.co 的数据流场景描述内容") is False
    assert gwa.validate_cover_scene(COVER_SCENE_TEXT) is True


def test_reason_validation_rejects_refusal():
    """A model refusal (fetched "full text" was only site navigation) must
    never render as a guide."""
    refusal = (
        "提供的正文内容仅为 GitHub 博客的导航菜单与栏目索引，未包含标题所述的具体技术细节，"
        "无法据此提取有效导读信息。"
    )
    assert gwa.validate_reason(refusal, "画布如何使代理工作流程可见") is False
    assert gwa.validate_reason(LONG_GENERATED_REASON, "任意标题") is True


def test_scene_failure_falls_back_to_raw_headline():
    png_bytes = make_png_bytes(1000, 500)
    with tempfile.TemporaryDirectory() as tmp:
        data_dir, assets_dir = write_fixture(
            tmp,
            [
                make_item(
                    1,
                    title="Cursor 推出 Origin，作为 GitHub 的替代方案",
                    reason=LONG_EXISTING_REASON,
                )
            ],
        )
        make_static_asset(assets_dir)
        output_dir = Path(tmp) / "weixin"
        # Scene output fails validation -> raw headline stays the theme.
        side_effect, calls = make_text_router(scene=text_response("logo"))

        session = MagicMock()
        session.post.return_value = image_url_response()

        def get_handler(url, **kwargs):
            response = MagicMock()
            response.status_code = 200
            response.content = png_bytes
            return response

        session.get.side_effect = get_handler

        rc = run_patched(
            BASE_ENV,
            side_effect,
            session,
            [
                "--data-dir", str(data_dir),
                "--output-dir", str(output_dir),
                "--assets-dir", str(assets_dir),
            ],
        )

        assert rc == 0
        assert calls["scene"] == 1
        post_call = session.post.call_args
        prompt_text = (
            post_call.kwargs["json"]["input"]["messages"][0]["content"][0]["text"]
        )
        assert "GitHub" in prompt_text


def test_image_api_url_derived_from_base_url():
    default = gwa.image_api_url(gwa.DEFAULT_API_BASE_URL)
    assert default == (
        "https://dashscope.aliyuncs.com/api/v1/services/aigc/"
        "multimodal-generation/generation"
    )
    # Custom workspace domains keep working.
    ws = gwa.image_api_url(
        "https://ws-123.cn-beijing.maas.aliyuncs.com/compatible-mode/v1"
    )
    assert ws == (
        "https://ws-123.cn-beijing.maas.aliyuncs.com/api/v1/services/aigc/"
        "multimodal-generation/generation"
    )


def test_extract_image_url_native_shape():
    body = {
        "output": {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": [{"text": "done"}, {"image": "https://oss/x.png"}],
                    }
                }
            ]
        }
    }
    assert gwa.extract_image_url(body) == "https://oss/x.png"
    assert gwa.extract_image_url({"output": {"choices": []}}) is None
    assert gwa.extract_image_url({"data": [{"url": "https://oss/x.png"}]}) is None
    assert gwa.extract_image_url(None) is None


def test_image_success_and_crop():
    png_bytes = make_png_bytes(1000, 500)
    with tempfile.TemporaryDirectory() as tmp:
        data_dir, assets_dir = write_fixture(
            tmp, [make_item(1, reason=LONG_EXISTING_REASON)]
        )
        make_static_asset(assets_dir)
        output_dir = Path(tmp) / "weixin"
        side_effect, calls = make_text_router(
            scene=text_response(COVER_SCENE_TEXT),
        )

        session = MagicMock()
        session.post.return_value = image_url_response()

        def get_handler(url, **kwargs):
            response = MagicMock()
            response.status_code = 200
            response.content = png_bytes
            return response

        session.get.side_effect = get_handler

        rc = run_patched(
            BASE_ENV,
            side_effect,
            session,
            [
                "--data-dir", str(data_dir),
                "--output-dir", str(output_dir),
                "--assets-dir", str(assets_dir),
            ],
        )

        assert rc == 0
        assert calls["reason"] == 0  # title no longer hits the text API
        assert session.post.call_count == 1
        # Native sync image endpoint, not the (404ing) OpenAI-compatible route.
        post_call = session.post.call_args
        assert post_call.args[0].endswith(gwa.IMAGE_API_PATH)
        payload = post_call.kwargs["json"]
        assert payload["model"] == gwa.DEFAULT_IMAGE_MODEL
        prompt_text = payload["input"]["messages"][0]["content"][0]["text"]
        assert prompt_text
        # The theme is the text model's brand-free scene, not the raw headline.
        assert calls["scene"] == 1
        assert COVER_SCENE_TEXT in prompt_text
        assert "测试新闻标题 1" not in prompt_text
        meta = read_json(output_dir / "meta.json")
        try:
            from PIL import Image

            assert meta["cover"] == "cover.jpg"
            with Image.open(output_dir / "cover.jpg") as img:
                assert img.size == (gwa.COVER_W, gwa.COVER_H)
        except ImportError:
            # Without Pillow locally the crop degrades to the static cover.
            assert meta["cover"] == "cover.png"


def test_image_all_fail_uses_static_cover():
    with tempfile.TemporaryDirectory() as tmp:
        data_dir, assets_dir = write_fixture(
            tmp, [make_item(1, reason=LONG_EXISTING_REASON)]
        )
        make_static_asset(assets_dir)
        output_dir = Path(tmp) / "weixin"
        side_effect, _ = make_text_router(scene=text_response(COVER_SCENE_TEXT))

        rc = run_patched(
            BASE_ENV,
            side_effect,
            offline_session(),
            [
                "--data-dir", str(data_dir),
                "--output-dir", str(output_dir),
                "--assets-dir", str(assets_dir),
            ],
        )

        assert rc == 0
        meta = read_json(output_dir / "meta.json")
        assert meta["cover"] == "cover.png"
        assert (output_dir / "cover.png").is_file()


def test_image_size_400_retries_without_size():
    png_bytes = make_png_bytes(800, 400)
    with tempfile.TemporaryDirectory() as tmp:
        data_dir, assets_dir = write_fixture(
            tmp, [make_item(1, reason=LONG_EXISTING_REASON)]
        )
        make_static_asset(assets_dir)
        output_dir = Path(tmp) / "weixin"
        side_effect, _ = make_text_router(scene=text_response(COVER_SCENE_TEXT))

        rejected = MagicMock()
        rejected.status_code = 400
        session = MagicMock()
        session.post.side_effect = [rejected, image_url_response()]

        def get_handler(url, **kwargs):
            response = MagicMock()
            response.status_code = 200
            response.content = png_bytes
            return response

        session.get.side_effect = get_handler

        rc = run_patched(
            BASE_ENV,
            side_effect,
            session,
            [
                "--data-dir", str(data_dir),
                "--output-dir", str(output_dir),
                "--assets-dir", str(assets_dir),
            ],
        )

        assert rc == 0
        assert session.post.call_count == 2
        # First payload carries size; the retry must drop it.
        first_payload = session.post.call_args_list[0].kwargs["json"]
        assert first_payload["parameters"]["size"] == gwa.IMAGE_REQUEST_SIZE
        second_payload = session.post.call_args_list[1].kwargs["json"]
        assert "size" not in second_payload.get("parameters", {})
        meta = read_json(output_dir / "meta.json")
        try:
            from PIL import Image  # noqa: F401

            assert meta["cover"] == "cover.jpg"
        except ImportError:
            assert meta["cover"] == "cover.png"


# ---------------------------------------------------------------------------
# Rendering / layout
# ---------------------------------------------------------------------------

def test_sort_descending_and_circled_numbers():
    items = [
        make_item(1, title="低分条目甲", score=5.0),
        make_item(2, title="高分条目乙", score=90.0),
        make_item(3, title="中分条目丙", score=50.0),
    ]
    with tempfile.TemporaryDirectory() as tmp:
        data_dir, assets_dir = write_fixture(tmp, items)
        output_dir = Path(tmp) / "weixin"

        with patch.dict("os.environ", {}, clear=True):
            rc = run_main(data_dir, output_dir, assets_dir)

        assert rc == 0
        html_text = (output_dir / "index.html").read_text(encoding="utf-8")
        pos_high = html_text.index("高分条目乙")
        pos_mid = html_text.index("中分条目丙")
        pos_low = html_text.index("低分条目甲")
        assert pos_high < pos_mid < pos_low
        # Numbers use the unified outlined ①–⑳ set, never the thin filled
        # ❶–❿ glyphs.
        assert "① 高分条目乙" in html_text
        assert "② 中分条目丙" in html_text
        assert "③ 低分条目甲" in html_text
        assert "❶" not in html_text


def test_select_items_prefers_peak_score_over_decayed_importance():
    """The daily push ranks by the window-peak importance so an important
    story published early in the window is not sunk below fresher mid-tier
    items by push time. items[0] also drives the headline and cover."""
    early = make_item(1, title="早间重要条目", score=60.0)
    early["peak_score"] = 0.9
    fresh = make_item(2, title="新近中分条目", score=70.0)
    fresh["peak_score"] = 0.72
    brief = {"items": [fresh, early]}

    ordered = gwa.select_items(brief, 10)
    assert [item["title"] for item in ordered] == ["早间重要条目", "新近中分条目"]


def test_select_items_falls_back_to_importance_without_peak():
    """Briefs produced before peak tracking existed keep the old order."""
    brief = {"items": [make_item(1, score=30.0), make_item(2, score=80.0)]}

    ordered = gwa.select_items(brief, 10)
    assert [item["importance_score"] for item in ordered] == [80.0, 30.0]


def _whitelist_item(idx: int, category: str, source: str, site_id: str) -> dict:
    """Story as persisted by the pipeline: precomputed category plus source
    strings exactly as the aihot public API reports them."""
    item = make_item(idx, title=f"白名单测试 {idx}")
    item["category"] = category
    item["source"] = source
    item["sources"] = [
        {
            "site_id": site_id,
            "source": source,
            "source_name": "AI HOT",
            "url": item["url"],
        }
    ]
    return item


def test_select_items_promotes_stale_first_party_story_to_official():
    """Stories persisted before a whitelist change keep their stale category;
    select_items re-derives it from the current whitelist. Regression: the
    Claude Blog Computer Use story stayed 行业动态 after the whitelist fix
    because the cloud pipeline had already persisted category=industry."""
    item = _whitelist_item(1, "industry", "Claude：Blog（网页）", "aihot")
    # Real data: the story-level primary_item carries no site_id, so the
    # override must recover it from the matching sources[] ref.
    assert "site_id" not in item["primary_item"]

    selected = gwa.select_items({"items": [item]}, 20)

    assert selected[0]["category"] == "official"
    # The rendered meta line follows the refreshed category.
    assert "官方更新" in gwa.render_item_html(selected[0], 0)


def test_select_items_promotes_stale_watch_story_to_official():
    item = _whitelist_item(1, "watch", "Claude：Blog（网页）", "aihot")

    selected = gwa.select_items({"items": [item]}, 20)

    assert selected[0]["category"] == "official"


def test_select_items_keeps_non_whitelist_aihot_category():
    item = _whitelist_item(1, "industry", "IT之家（RSS）", "aihot")

    selected = gwa.select_items({"items": [item]}, 20)

    assert selected[0]["category"] == "industry"


def test_select_items_does_not_promote_non_aihot_site():
    """The whitelist only applies to the aihot channel."""
    item = _whitelist_item(1, "industry", "Claude：Blog（网页）", "hackernews")

    selected = gwa.select_items({"items": [item]}, 20)

    assert selected[0]["category"] == "industry"


def test_first_party_override_needs_primary_source_and_site():
    # No primary source string anywhere → no override.
    bare = make_item(1)
    bare["category"] = "industry"
    assert gwa.first_party_category_override(bare) is None
    # Source string present but no site_id recoverable → no override.
    nosite = _whitelist_item(2, "industry", "Claude：Blog（网页）", "aihot")
    nosite["sources"][0].pop("site_id")
    assert gwa.first_party_category_override(nosite) is None


# ---------------------------------------------------------------------------
# Summary-first grounding: a persisted RSS summary is an offline asset and
# must be preferred over fetching the live page (which is often bot-blocked);
# only unusable summaries degrade to a full-text fetch.
# ---------------------------------------------------------------------------

def test_summary_grounding_gates():
    title = "GitHub Copilot app for Beginners"
    assert gwa.summary_grounding("", title) is None
    assert gwa.summary_grounding(None, title) is None
    assert gwa.summary_grounding(title, title) is None
    assert gwa.summary_grounding("too short", title) is None

    usable = "The My work pane tracks multiple Copilot sessions so you can resume."
    assert gwa.summary_grounding(usable, title) == usable

    # WordPress boilerplate is stripped; what remains is too short to ground.
    boilerplate_only = "The post Copilot update appeared first on The GitHub Blog."
    assert gwa.summary_grounding(boilerplate_only, title) is None

    # Boilerplate tail stripped, real editorial content kept.
    mixed = usable + " The post Copilot update appeared first on The GitHub Blog."
    assert gwa.summary_grounding(mixed, title) == usable


def test_reason_context_uses_summary_without_any_fetch():
    summary = "A persisted RSS summary long enough to ground the guide text."
    item = {
        "title": "GitHub Copilot app for Beginners",
        "primary_url": "https://github.blog/x",
        "primary_item": {"summary": summary},
        "sources": [],
    }
    # session=None proves the network path is never attempted.
    assert gwa.reason_context(item, None) == summary


def test_reason_context_degrades_to_fetch_when_summary_unusable():
    calls = []

    class FakeResponse:
        status_code = 200
        text = "<p>" + "Full article body text for grounding. " * 8 + "</p>"

    class FakeSession:
        def get(self, url, **kwargs):
            calls.append(url)
            return FakeResponse()

    item = {
        "title": "GitHub Copilot app for Beginners",
        "primary_url": "https://github.blog/x",
        "primary_item": {"summary": "short"},
        "sources": [
            {"summary": "The post Copilot update appeared first on The GitHub Blog."}
        ],
    }
    context = gwa.reason_context(item, FakeSession())
    assert calls == ["https://github.blog/x"]
    assert context and "Full article body text" in context


def test_multi_source_item_merges_source_names():
    sources = [
        {"title": "子标题A报道", "url": "https://a.example/1", "source_name": "Source A"},
        {"title": "子标题B报道", "url": "https://b.example/2", "source_name": "Source B"},
        {"title": "子标题C报道", "url": "https://c.example/3", "source_name": "Source C"},
    ]
    item = make_item(1, title="合并后的大事件标题", score=80.0, sources=sources)
    item["source_count"] = 3
    item["source_name"] = "Source A"

    with tempfile.TemporaryDirectory() as tmp:
        data_dir, assets_dir = write_fixture(tmp, [item])
        output_dir = Path(tmp) / "weixin"

        with patch.dict("os.environ", {}, clear=True):
            rc = run_main(data_dir, output_dir, assets_dir)

        assert rc == 0
        html_text = (output_dir / "index.html").read_text(encoding="utf-8")
        # One merged line: story title + every source name joined by ", ".
        assert "合并后的大事件标题（Source A, Source B, Source C）" in html_text
        # Per-source titles are no longer listed individually.
        assert "子标题A报道" not in html_text
        assert "子标题B报道" not in html_text
        assert "子标题C报道" not in html_text
        # Meta line names the first source plus 等.
        assert "Source A等 · 3 个来源" in html_text
        # Each item carries its original URL as plain text.
        assert "原文：https://example.com/story/1" in html_text
        # Article body must not contain hyperlinks or images.
        assert "<a " not in html_text
        assert "<img" not in html_text


def test_single_source_meta_has_no_suffix():
    with tempfile.TemporaryDirectory() as tmp:
        data_dir, assets_dir = write_fixture(tmp, [make_item(1)])
        output_dir = Path(tmp) / "weixin"

        with patch.dict("os.environ", {}, clear=True):
            rc = run_main(data_dir, output_dir, assets_dir)

        assert rc == 0
        html_text = (output_dir / "index.html").read_text(encoding="utf-8")
        # Single-source items keep the bare source name and no merged line.
        assert "Example Source · 1 个来源" in html_text
        assert "Example Source等" not in html_text
        assert "Example Source）" not in html_text


def test_item_display_source_resolves_umbrella_buckets():
    # "Official AI Updates" and "AI HOT" are aggregate buckets, not
    # publishers: display resolves them to the specific channel (``source``).
    official = {"source_name": "Official AI Updates", "source": "OpenAI News"}
    assert gwa.item_display_source(official) == "OpenAI News"

    aihot = {"source_name": "AI HOT", "source": "GitHub Blog"}
    assert gwa.item_display_source(aihot) == "GitHub Blog"

    # Real publisher names pass through unchanged.
    assert gwa.item_display_source(make_item(1)) == "Example Source"

    # A bucket with no channel falls back to the bucket name rather than
    # rendering blank.
    assert (
        gwa.item_display_source({"source_name": "Official AI Updates"})
        == "Official AI Updates"
    )


def test_meta_line_shows_channel_for_umbrella_bucket():
    # The rendered meta line names the specific channel, not the umbrella
    # bucket that would repeat across the whole official/hot section.
    official = make_item(1, title="官方渠道条目")
    official["source_name"] = "Official AI Updates"
    official["source"] = "OpenAI News"
    for src in official["sources"] + [official["primary_item"]]:
        src["source_name"] = "Official AI Updates"
        src["source"] = "OpenAI News"

    html = gwa.render_item_html(official, 0)
    assert "OpenAI News" in html
    assert "Official AI Updates" not in html

    aihot = make_item(2, title="热点渠道条目")
    aihot["source_name"] = "AI HOT"
    aihot["source"] = "GitHub Blog"
    for src in aihot["sources"] + [aihot["primary_item"]]:
        src["source_name"] = "AI HOT"
        src["source"] = "GitHub Blog"

    html = gwa.render_item_html(aihot, 0)
    assert "GitHub Blog" in html
    assert "AI HOT" not in html


def test_category_renders_chinese_label():
    with tempfile.TemporaryDirectory() as tmp:
        items = [make_item(1, title="官方条目"), make_item(2, title="多源条目")]
        items[0]["category"] = "official"
        items[1]["category"] = "multi_source"
        data_dir, assets_dir = write_fixture(tmp, items)
        output_dir = Path(tmp) / "weixin"

        with patch.dict("os.environ", {}, clear=True):
            rc = run_main(data_dir, output_dir, assets_dir)

        assert rc == 0
        html_text = (output_dir / "index.html").read_text(encoding="utf-8")
        assert "官方更新 · Example Source · 1 个来源" in html_text
        assert "多源热议 · Example Source · 1 个来源" in html_text
        # Raw English category keys no longer reach the page.
        assert "multi_source" not in html_text
        assert "· official ·" not in html_text


def test_strip_english_tail_keeps_chinese_only():
    assert gwa.strip_english_tail("中文标题 / English Title") == "中文标题"
    assert (
        gwa.strip_english_tail("Qwen 3.8 表现出色 / Qwen 3.8 is excellent")
        == "Qwen 3.8 表现出色"
    )
    # Multiple segments: keep the leading Chinese one.
    assert gwa.strip_english_tail("中文 / English one / English two") == "中文"
    # No bilingual split: unchanged.
    assert gwa.strip_english_tail("纯中文标题") == "纯中文标题"
    assert gwa.strip_english_tail("Pure English title") == "Pure English title"
    assert gwa.strip_english_tail("") == ""
    # Leading segment without CJK is not treated as a bilingual title.
    assert gwa.strip_english_tail("AI / ML 工具发布") == "AI / ML 工具发布"


def test_bilingual_title_renders_chinese_only():
    bilingual = (
        "Qwen 3.8 27B 表现出色，但默认推理强度过高导致过度思考 / "
        "Qwen 3.8 27B is excellent, but it defaults to wildly overthinking things"
    )
    with tempfile.TemporaryDirectory() as tmp:
        data_dir, assets_dir = write_fixture(tmp, [make_item(1, title=bilingual)])
        output_dir = Path(tmp) / "weixin"

        with patch.dict("os.environ", {}, clear=True):
            rc = run_main(data_dir, output_dir, assets_dir)

        assert rc == 0
        html_text = (output_dir / "index.html").read_text(encoding="utf-8")
        assert "① Qwen 3.8 27B 表现出色，但默认推理强度过高导致过度思考</p>" in html_text
        # The English tail disappears everywhere, digest included.
        assert "overthinking" not in html_text
        assert " / " not in html_text


def test_items_carry_original_link_as_plain_text():
    with tempfile.TemporaryDirectory() as tmp:
        data_dir, assets_dir = write_fixture(tmp, [make_item(1), make_item(2)])
        output_dir = Path(tmp) / "weixin"

        with patch.dict("os.environ", {}, clear=True):
            rc = run_main(data_dir, output_dir, assets_dir)

        assert rc == 0
        html_text = (output_dir / "index.html").read_text(encoding="utf-8")
        assert "原文：https://example.com/story/1" in html_text
        assert "原文：https://example.com/story/2" in html_text
        assert "<a " not in html_text


def test_short_existing_reason_is_rewritten():
    short_reason = "上游留下的短推荐语。"
    with tempfile.TemporaryDirectory() as tmp:
        data_dir, assets_dir = write_fixture(
            tmp,
            [
                make_item(
                    1,
                    reason=short_reason,
                    summary="这是一段足够长的摘要内容，用于生成推荐语。",
                )
            ],
        )
        make_static_asset(assets_dir)
        output_dir = Path(tmp) / "weixin"
        side_effect, calls = make_text_router(
            reason=text_response(LONG_GENERATED_REASON),
            scene=text_response(COVER_SCENE_TEXT),
        )

        rc = run_patched(
            BASE_ENV,
            side_effect,
            offline_session(),
            [
                "--data-dir", str(data_dir),
                "--output-dir", str(output_dir),
                "--assets-dir", str(assets_dir),
            ],
        )

        assert rc == 0
        assert calls["reason"] == 1
        html_text = (output_dir / "index.html").read_text(encoding="utf-8")
        assert LONG_GENERATED_REASON in html_text
        assert short_reason not in html_text


def test_no_key_keeps_short_existing_reason():
    short_reason = "上游留下的短推荐语。"
    with tempfile.TemporaryDirectory() as tmp:
        data_dir, assets_dir = write_fixture(
            tmp, [make_item(1, reason=short_reason)]
        )
        make_static_asset(assets_dir)
        output_dir = Path(tmp) / "weixin"

        with patch.dict("os.environ", {}, clear=True):
            rc = run_main(data_dir, output_dir, assets_dir)

        assert rc == 0
        html_text = (output_dir / "index.html").read_text(encoding="utf-8")
        # Without an API key the short reason is kept rather than dropped.
        assert short_reason in html_text


def test_dry_run_writes_nothing():
    with tempfile.TemporaryDirectory() as tmp:
        data_dir, assets_dir = write_fixture(
            tmp,
            [make_item(1, summary="这是一段足够长的摘要内容，用于生成推荐语。")],
        )
        make_static_asset(assets_dir)
        output_dir = Path(tmp) / "weixin"
        side_effect, calls = make_text_router(
            reason=text_response(LONG_GENERATED_REASON),
            scene=text_response(COVER_SCENE_TEXT),
        )

        rc = run_patched(
            BASE_ENV,
            side_effect,
            offline_session(),
            [
                "--data-dir", str(data_dir),
                "--output-dir", str(output_dir),
                "--assets-dir", str(assets_dir),
                "--dry-run",
            ],
        )

        assert rc == 0
        assert not output_dir.exists()
        assert calls["reason"] == 1  # full pipeline ran, nothing persisted


# ---------------------------------------------------------------------------
# English-title backfill translation
# ---------------------------------------------------------------------------

EN_STORY_TITLE = "Advancing price-performance for developers with GPT-5.6 in Kiro"
ZH_STORY_TRANSLATION = "GPT-5.6 接入 Kiro，为开发者提升模型性价比"


def test_title_needs_translation_rules():
    assert gwa.title_needs_translation(EN_STORY_TITLE) is True
    assert gwa.title_needs_translation(
        "OpenAI is building AI agents for everything. Will everyone use them?"
    ) is True
    # Chinese part survives strip_english_tail: nothing to translate.
    assert gwa.title_needs_translation("中文标题 / English Title") is False
    assert gwa.title_needs_translation("纯中文标题") is False
    # Non-prose strings pass through: translating them yields garbage.
    assert gwa.title_needs_translation("v2.1.245") is False
    assert gwa.title_needs_translation("") is False


def test_validate_title_translation_bounds():
    assert gwa.validate_title_translation(EN_STORY_TITLE, ZH_STORY_TRANSLATION) is True
    # No CJK / identical to the original / degenerate / too long / URL.
    assert gwa.validate_title_translation(EN_STORY_TITLE, "still all english") is False
    assert gwa.validate_title_translation(EN_STORY_TITLE, EN_STORY_TITLE) is False
    assert gwa.validate_title_translation(EN_STORY_TITLE, "短") is False
    assert gwa.validate_title_translation(EN_STORY_TITLE, "字" * 91) is False
    assert gwa.validate_title_translation(EN_STORY_TITLE, "译文带链接 http://x.com") is False


def test_english_title_translated_rendered_and_cached():
    with tempfile.TemporaryDirectory() as tmp:
        data_dir, assets_dir = write_fixture(
            tmp,
            [
                make_item(
                    1,
                    title=EN_STORY_TITLE,
                    summary="这是一段足够长的摘要内容，用于生成推荐语。",
                ),
                make_item(2, summary="这是另一段足够长的摘要内容，用于生成推荐语。"),
            ],
        )
        make_static_asset(assets_dir)
        output_dir = Path(tmp) / "weixin"
        base_args = [
            "--data-dir", str(data_dir),
            "--output-dir", str(output_dir),
            "--assets-dir", str(assets_dir),
        ]

        side_effect, calls = make_text_router(
            reason=text_response(LONG_GENERATED_REASON),
            scene=text_response(COVER_SCENE_TEXT),
            translate=text_response(ZH_STORY_TRANSLATION),
        )
        rc = run_patched(BASE_ENV, side_effect, offline_session(), base_args)

        assert rc == 0
        assert calls["translate"] == 1
        html_text = (output_dir / "index.html").read_text(encoding="utf-8")
        assert ZH_STORY_TRANSLATION in html_text
        assert EN_STORY_TITLE not in html_text
        cache = read_json(output_dir / "reason-cache.json")
        tt_key = gwa.TITLE_TRANSLATE_CACHE_PREFIX + gwa.title_hash(EN_STORY_TITLE)
        assert cache["entries"][tt_key]["zh_title"] == ZH_STORY_TRANSLATION

        # Second run: translation comes from the cache, no new API call.
        side_effect, second_calls = make_text_router(
            reason=AssertionError("reasons are cached too"),
            scene=text_response(COVER_SCENE_TEXT),
            translate=AssertionError("cached translation must not be regenerated"),
        )
        rc = run_patched(BASE_ENV, side_effect, offline_session(), base_args)
        assert rc == 0
        assert second_calls["translate"] == 0
        html_text = (output_dir / "index.html").read_text(encoding="utf-8")
        assert ZH_STORY_TRANSLATION in html_text


def test_english_title_kept_without_key():
    with tempfile.TemporaryDirectory() as tmp:
        data_dir, assets_dir = write_fixture(
            tmp, [make_item(1, title=EN_STORY_TITLE, reason=LONG_EXISTING_REASON)]
        )
        make_static_asset(assets_dir)
        output_dir = Path(tmp) / "weixin"

        with patch.dict("os.environ", {}, clear=True):
            rc = run_main(data_dir, output_dir, assets_dir)

        assert rc == 0
        html_text = (output_dir / "index.html").read_text(encoding="utf-8")
        assert EN_STORY_TITLE in html_text


def test_translation_failure_keeps_original_title():
    with tempfile.TemporaryDirectory() as tmp:
        data_dir, assets_dir = write_fixture(
            tmp,
            [
                make_item(
                    1,
                    title=EN_STORY_TITLE,
                    summary="这是一段足够长的摘要内容，用于生成推荐语。",
                )
            ],
        )
        make_static_asset(assets_dir)
        output_dir = Path(tmp) / "weixin"
        # The model echoes the English original: validation must reject it
        # and the item keeps its untranslated title.
        side_effect, calls = make_text_router(
            reason=text_response(LONG_GENERATED_REASON),
            scene=text_response(COVER_SCENE_TEXT),
            translate=text_response(EN_STORY_TITLE),
        )

        rc = run_patched(
            BASE_ENV,
            side_effect,
            offline_session(),
            [
                "--data-dir", str(data_dir),
                "--output-dir", str(output_dir),
                "--assets-dir", str(assets_dir),
            ],
        )

        assert rc == 0
        assert calls["translate"] == 1
        html_text = (output_dir / "index.html").read_text(encoding="utf-8")
        assert EN_STORY_TITLE in html_text


def test_bilingual_title_triggers_no_translation():
    bilingual = "阿里发布全新大模型 / Alibaba Releases a New Large Model"
    with tempfile.TemporaryDirectory() as tmp:
        data_dir, assets_dir = write_fixture(
            tmp,
            [
                make_item(
                    1,
                    title=bilingual,
                    summary="这是一段足够长的摘要内容，用于生成推荐语。",
                )
            ],
        )
        make_static_asset(assets_dir)
        output_dir = Path(tmp) / "weixin"
        side_effect, calls = make_text_router(
            reason=text_response(LONG_GENERATED_REASON),
            scene=text_response(COVER_SCENE_TEXT),
            translate=AssertionError("bilingual titles need no translation"),
        )

        rc = run_patched(
            BASE_ENV,
            side_effect,
            offline_session(),
            [
                "--data-dir", str(data_dir),
                "--output-dir", str(output_dir),
                "--assets-dir", str(assets_dir),
            ],
        )

        assert rc == 0
        assert calls["translate"] == 0
        html_text = (output_dir / "index.html").read_text(encoding="utf-8")
        assert "阿里发布全新大模型" in html_text
        assert "Alibaba Releases" not in html_text


# ---------------------------------------------------------------------------
# --regenerate: re-roll guides by display number / fragment / story id
# ---------------------------------------------------------------------------

def test_match_regenerate_specs():
    items = [
        {"story_id": "story_1", "title": "连线、运行、部署：Gradio 中的 AI 工作流实战"},
        {
            "story_id": "story_2",
            "title": "GPT-5.6 接入 Kiro，为开发者提升模型性价比",
            "title_pre_translate": EN_STORY_TITLE,
        },
        {"story_id": "story_3", "title": "第三条中文标题"},
    ]
    # Display positions: Arabic digits and circled digits, out-of-range misses.
    assert gwa.match_regenerate(items, ["2"]) == ({"story_2"}, [])
    assert gwa.match_regenerate(items, ["③"]) == ({"story_3"}, [])
    assert gwa.match_regenerate(items, ["9"]) == (set(), ["9"])
    # Exact story id.
    assert gwa.match_regenerate(items, ["story_1"]) == ({"story_1"}, [])
    # Title fragments: Chinese display title, pre-translation English title,
    # case-insensitive.
    assert gwa.match_regenerate(items, ["连线"]) == ({"story_1"}, [])
    assert gwa.match_regenerate(items, ["price-performance"]) == ({"story_2"}, [])
    assert gwa.match_regenerate(items, ["gpt-5.6"]) == ({"story_2"}, [])
    # 'all' selects everything.
    assert gwa.match_regenerate(items, ["ALL"]) == (
        {"story_1", "story_2", "story_3"},
        [],
    )
    # Multiple specs accumulate; unknown specs are reported untouched.
    wanted, unmatched = gwa.match_regenerate(items, ["1", "不存在的片段"])
    assert wanted == {"story_1"}
    assert unmatched == ["不存在的片段"]


def test_regenerate_by_display_number_re_rolls_one_guide():
    with tempfile.TemporaryDirectory() as tmp:
        data_dir, assets_dir = write_fixture(
            tmp,
            [
                make_item(1, score=90, summary="这是第一条足够长的摘要内容，用于生成推荐语。"),
                make_item(2, score=80, summary="这是第二条足够长的摘要内容，用于生成推荐语。"),
            ],
        )
        make_static_asset(assets_dir)
        output_dir = Path(tmp) / "weixin"
        base_args = [
            "--data-dir", str(data_dir),
            "--output-dir", str(output_dir),
            "--assets-dir", str(assets_dir),
        ]

        side_effect, calls = make_text_router(
            reason=text_response(LONG_GENERATED_REASON),
            scene=text_response(COVER_SCENE_TEXT),
        )
        rc = run_patched(BASE_ENV, side_effect, offline_session(), base_args)
        assert rc == 0
        assert calls["reason"] == 2

        # Only the item named by its display number is re-rolled.
        side_effect, second = make_text_router(
            reason=text_response(LONG_GENERATED_REASON),
            scene=text_response(COVER_SCENE_TEXT),
        )
        rc = run_patched(
            BASE_ENV, side_effect, offline_session(), base_args + ["--regenerate", "②"]
        )
        assert rc == 0
        assert second["reason"] == 1


def test_regenerate_matches_translated_display_title():
    """The maintainer reads the Chinese display title in the article, so a
    fragment of the on-the-fly translation must match — and the English
    original must keep matching too."""
    with tempfile.TemporaryDirectory() as tmp:
        data_dir, assets_dir = write_fixture(
            tmp,
            [
                make_item(
                    1,
                    title=EN_STORY_TITLE,
                    summary="这是一段足够长的摘要内容，用于生成推荐语。",
                )
            ],
        )
        make_static_asset(assets_dir)
        output_dir = Path(tmp) / "weixin"
        base_args = [
            "--data-dir", str(data_dir),
            "--output-dir", str(output_dir),
            "--assets-dir", str(assets_dir),
        ]

        side_effect, calls = make_text_router(
            reason=text_response(LONG_GENERATED_REASON),
            scene=text_response(COVER_SCENE_TEXT),
            translate=text_response(ZH_STORY_TRANSLATION),
        )
        rc = run_patched(BASE_ENV, side_effect, offline_session(), base_args)
        assert rc == 0
        assert calls["translate"] == 1 and calls["reason"] == 1

        # Chinese fragment of the translated display title.
        side_effect, second = make_text_router(
            reason=text_response(LONG_GENERATED_REASON),
            scene=text_response(COVER_SCENE_TEXT),
            translate=AssertionError("translation is cached"),
        )
        rc = run_patched(
            BASE_ENV, side_effect, offline_session(), base_args + ["--regenerate", "接入 Kiro"]
        )
        assert rc == 0
        assert second["translate"] == 0
        assert second["reason"] == 1  # matched and re-rolled

        # English fragment of the pre-translation title still matches too.
        side_effect, third = make_text_router(
            reason=text_response(LONG_GENERATED_REASON),
            scene=text_response(COVER_SCENE_TEXT),
            translate=AssertionError("translation is cached"),
        )
        rc = run_patched(
            BASE_ENV,
            side_effect,
            offline_session(),
            base_args + ["--regenerate", "price-performance"],
        )
        assert rc == 0
        assert third["reason"] == 1


def test_regenerate_miss_prints_menu_and_changes_nothing(capsys):
    with tempfile.TemporaryDirectory() as tmp:
        data_dir, assets_dir = write_fixture(
            tmp, [make_item(1, summary="这是一段足够长的摘要内容，用于生成推荐语。")]
        )
        make_static_asset(assets_dir)
        output_dir = Path(tmp) / "weixin"
        base_args = [
            "--data-dir", str(data_dir),
            "--output-dir", str(output_dir),
            "--assets-dir", str(assets_dir),
        ]
        side_effect, calls = make_text_router(
            reason=text_response(LONG_GENERATED_REASON),
            scene=text_response(COVER_SCENE_TEXT),
        )
        rc = run_patched(BASE_ENV, side_effect, offline_session(), base_args)
        assert rc == 0 and calls["reason"] == 1

        # A mistyped spec must not fail silently: nothing is re-rolled and
        # stderr lists the numbered items for a retry.
        side_effect, second = make_text_router(
            reason=AssertionError("nothing may be re-rolled on a miss"),
            scene=text_response(COVER_SCENE_TEXT),
        )
        capsys.readouterr()  # drop run-1 output
        rc = run_patched(
            BASE_ENV, side_effect, offline_session(), base_args + ["--regenerate", "不存在的片段"]
        )
        assert rc == 0
        assert second["reason"] == 0
        err = capsys.readouterr().err
        assert "未命中" in err
        assert "测试新闻标题 1" in err


