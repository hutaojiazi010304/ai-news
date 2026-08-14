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
    response.json.return_value = {"data": [{"url": url}]}
    return response


def offline_session() -> MagicMock:
    """Session mock that refuses all network I/O: cover/fetch degrade cleanly."""
    session = MagicMock()
    session.get.side_effect = requests.ConnectionError("offline")
    session.post.side_effect = requests.ConnectionError("offline")
    return session


def make_text_router(reason=None, title=None):
    """Route module-level requests.post (text completions) by system prompt.

    Returns (side_effect, counters). A spec may be a MagicMock response,
    an exception instance (raised), or a callable(counters) -> response.
    """
    calls = {"reason": 0, "title": 0}

    def side_effect(url, **kwargs):
        payload = kwargs.get("json") or {}
        messages = payload.get("messages")
        system = str(((messages or [{}])[0] or {}).get("content") or "")
        key = "reason" if "值得读" in system else "title"
        calls[key] += 1
        spec = {"reason": reason, "title": title}[key]
        if spec is None:
            raise AssertionError(f"unexpected text api call ({key}): {url}")
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
    reason = "这是上游已经写好的推荐语，包含具体事实，长度也足够。"
    with tempfile.TemporaryDirectory() as tmp:
        data_dir, assets_dir = write_fixture(
            tmp, [make_item(1, reason=reason, summary="摘要内容")]
        )
        make_static_asset(assets_dir)
        output_dir = Path(tmp) / "weixin"
        side_effect, calls = make_text_router(
            reason=AssertionError("reason must not be called"),
            title=text_response("今日AI头条标题"),
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
        assert calls["title"] == 1
        html_text = (output_dir / "index.html").read_text(encoding="utf-8")
        assert reason in html_text


def test_reason_fill_success_writes_cache():
    generated = "OpenAI 发布了新一代模型，推理成本下降一半，支持更长上下文窗口。"
    with tempfile.TemporaryDirectory() as tmp:
        data_dir, assets_dir = write_fixture(
            tmp, [make_item(1, summary="这是一段足够长的摘要内容，用于生成推荐语。")]
        )
        make_static_asset(assets_dir)
        output_dir = Path(tmp) / "weixin"
        side_effect, calls = make_text_router(
            reason=text_response(generated),
            title=text_response("今日AI头条标题"),
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
    generated = "OpenAI 发布了新一代模型，推理成本下降一半，支持更长上下文窗口。"
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
            title=text_response("今日AI头条标题"),
        )
        run_patched(BASE_ENV, side_effect, offline_session(), base_args)
        assert first_calls["reason"] == 1

        side_effect, second_calls = make_text_router(
            reason=AssertionError("cached reason must not be regenerated"),
            title=text_response("今日AI头条标题"),
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
            title=text_response("今日AI头条标题"),
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
            title=text_response("今日AI头条标题"),
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

def test_title_llm_success():
    with tempfile.TemporaryDirectory() as tmp:
        data_dir, assets_dir = write_fixture(
            tmp, [make_item(1, reason="已有推荐语，字数足够长。")]
        )
        make_static_asset(assets_dir)
        output_dir = Path(tmp) / "weixin"
        side_effect, calls = make_text_router(title=text_response("新模型发布，成本降半"))

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
        assert calls["title"] == 1
        meta = read_json(output_dir / "meta.json")
        assert meta["title"] == "新模型发布，成本降半"


def test_title_overlong_falls_back():
    with tempfile.TemporaryDirectory() as tmp:
        data_dir, assets_dir = write_fixture(
            tmp, [make_item(1, reason="已有推荐语，字数足够长。")]
        )
        make_static_asset(assets_dir)
        output_dir = Path(tmp) / "weixin"
        side_effect, _ = make_text_router(title=text_response("字" * 40))

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
        assert len(meta["title"]) <= gwa.TITLE_MAX_CHARS
        assert "今日精选" in meta["title"]


def test_title_exception_falls_back():
    with tempfile.TemporaryDirectory() as tmp:
        data_dir, assets_dir = write_fixture(
            tmp, [make_item(1, reason="已有推荐语，字数足够长。")]
        )
        make_static_asset(assets_dir)
        output_dir = Path(tmp) / "weixin"
        side_effect, _ = make_text_router(title=requests.ConnectionError("api down"))

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
        assert "今日精选" in meta["title"]


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


def test_image_success_and_crop():
    png_bytes = make_png_bytes(1000, 500)
    with tempfile.TemporaryDirectory() as tmp:
        data_dir, assets_dir = write_fixture(
            tmp, [make_item(1, reason="已有推荐语，字数足够长。")]
        )
        make_static_asset(assets_dir)
        output_dir = Path(tmp) / "weixin"
        side_effect, calls = make_text_router(title=text_response("今日AI头条标题"))

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
        assert calls["title"] == 1
        assert session.post.call_count == 1
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
            tmp, [make_item(1, reason="已有推荐语，字数足够长。")]
        )
        make_static_asset(assets_dir)
        output_dir = Path(tmp) / "weixin"
        side_effect, _ = make_text_router(title=text_response("今日AI头条标题"))

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
            tmp, [make_item(1, reason="已有推荐语，字数足够长。")]
        )
        make_static_asset(assets_dir)
        output_dir = Path(tmp) / "weixin"
        side_effect, _ = make_text_router(title=text_response("今日AI头条标题"))

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
        # Second payload must drop the size parameter.
        second_payload = session.post.call_args_list[1].kwargs["json"]
        assert "size" not in second_payload
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
        assert "❶ 高分条目乙" in html_text
        assert "❷ 中分条目丙" in html_text
        assert "❸ 低分条目甲" in html_text


def test_multi_source_item_lists_subtitles():
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
        assert "子标题A报道" in html_text
        assert "子标题B报道" in html_text
        assert "子标题C报道" in html_text
        assert "3 个来源" in html_text
        # Article body must not contain hyperlinks or images.
        assert "<a " not in html_text
        assert "<img" not in html_text


def test_dry_run_writes_nothing():
    with tempfile.TemporaryDirectory() as tmp:
        data_dir, assets_dir = write_fixture(
            tmp,
            [make_item(1, summary="这是一段足够长的摘要内容，用于生成推荐语。")],
        )
        make_static_asset(assets_dir)
        output_dir = Path(tmp) / "weixin"
        side_effect, calls = make_text_router(
            reason=text_response("一条符合要求的中文推荐语，长度足够，引用了具体事实。"),
            title=text_response("今日AI头条标题"),
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
