from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import requests

from scripts import persona_score


PRAGMATIC_MD = """---
id: pragmatic
name: 实用派
name_en: Pragmatic
default: true
---

实用派 prompt 正文。
"""

CYNIC_MD = """---
id: cynic
name: 毒舌评论员
name_en: Cynic
---

毒舌评论员 prompt 正文。
"""

PAPER_POLICE_MD = """---
id: paper-police
name: 较真党
name_en: Stickler
---

较真党 prompt 正文。
"""


def make_brief_item(idx: int, *, title: str | None = None, importance: float = 70.5) -> dict:
    return {
        "story_id": f"story_{idx}",
        "title": title or f"Story number {idx}",
        "url": f"https://example.com/story/{idx}",
        "primary_url": f"https://example.com/story/{idx}",
        "importance_score": importance,
        "importance_label": "high",
        "category": "official",
        "reasons": ["official source"],
        "source_count": 2,
        "sources": [{"source": "Feed A"}, {"source": "Feed B"}],
        "extra_existing_field": {"nested": True},
    }


def write_fixture(tmp_path: Path, items: list[dict]) -> tuple[Path, Path]:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    brief = {
        "generated_at": "2026-07-10T00:00:00Z",
        "window_hours": 24,
        "total_items": len(items),
        "items": items,
    }
    (data_dir / "daily-brief.json").write_text(
        json.dumps(brief, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    personas_dir = tmp_path / "personas"
    personas_dir.mkdir()
    (personas_dir / "pragmatic.md").write_text(PRAGMATIC_MD, encoding="utf-8")
    (personas_dir / "cynic.md").write_text(CYNIC_MD, encoding="utf-8")
    (personas_dir / "paper-police.md").write_text(PAPER_POLICE_MD, encoding="utf-8")
    (personas_dir / "README.md").write_text("# not a persona\n", encoding="utf-8")
    return data_dir, personas_dir


def make_api_response(score: int, review: str) -> MagicMock:
    response = MagicMock()
    response.status_code = 200
    response.raise_for_status.return_value = None
    response.json.return_value = {
        "choices": [
            {
                "message": {
                    "content": json.dumps({"score": score, "review": review}, ensure_ascii=False)
                }
            }
        ]
    }
    return response


def run_main(data_dir: Path, personas_dir: Path, extra_args: list[str] | None = None) -> int:
    args = [
        "--data-dir",
        str(data_dir),
        "--personas-dir",
        str(personas_dir),
    ] + (extra_args or [])
    return persona_score.main(args)


def read_brief(data_dir: Path) -> dict:
    return json.loads((data_dir / "daily-brief.json").read_text(encoding="utf-8"))


def read_top3(data_dir: Path) -> dict:
    return json.loads((data_dir / "top3-personas.json").read_text(encoding="utf-8"))


def test_llm_scoring_writes_fields_and_top3(tmp_path):
    items = [make_brief_item(1, importance=90), make_brief_item(2, importance=50)]
    data_dir, personas_dir = write_fixture(tmp_path, items)

    with patch.dict("os.environ", {"DEEPSEEK_API_KEY": "test-key"}, clear=True), patch(
        "scripts.persona_score.requests.post",
        return_value=make_api_response(150, "有用" * 60),
    ):
        rc = run_main(data_dir, personas_dir)

    assert rc == 0
    brief = read_brief(data_dir)
    for original, item in zip(items, brief["items"]):
        # Persona fields present, score clamped to 0-100, review truncated.
        assert item["persona_id"] == "pragmatic"
        assert item["persona_score"] == 100
        assert len(item["persona_review"]) <= 60
        assert item["persona_meta"]["mode"] == "llm"
        assert item["persona_meta"]["model"] == "deepseek-chat"
        assert item["persona_meta"]["scored_at"]
        # Every pre-existing field is preserved verbatim.
        for key, value in original.items():
            assert item[key] == value
    # Top-level fields preserved.
    assert brief["generated_at"] == "2026-07-10T00:00:00Z"
    assert brief["window_hours"] == 24
    assert brief["total_items"] == 2

    top3 = read_top3(data_dir)
    assert top3["model"] == "deepseek-chat"
    assert {p["id"] for p in top3["personas"]} == {"pragmatic", "cynic", "paper-police"}
    assert len(top3["items"]) == 2
    first = top3["items"][0]
    assert first["rank"] == 1
    assert set(first["reviews"].keys()) == {"pragmatic", "cynic", "paper-police"}
    for review in first["reviews"].values():
        assert 0 <= review["score"] <= 100
        assert review["review"]


def test_fallback_without_api_key(tmp_path):
    items = [make_brief_item(1, importance=70.5)]
    data_dir, personas_dir = write_fixture(tmp_path, items)

    with patch.dict("os.environ", {}, clear=True), patch(
        "scripts.persona_score.requests.post"
    ) as mock_post:
        rc = run_main(data_dir, personas_dir)

    assert rc == 0
    assert mock_post.call_count == 0
    brief = read_brief(data_dir)
    item = brief["items"][0]
    assert item["persona_score"] == round(70.5)
    assert item["persona_id"] == "pragmatic"
    assert "persona_review" not in item
    assert item["persona_meta"]["mode"] == "rules_fallback"

    top3 = read_top3(data_dir)
    assert top3["items"] == []
    assert top3["generated_at"]


def test_single_item_failure_is_skipped(tmp_path):
    items = [make_brief_item(1), make_brief_item(2), make_brief_item(3)]
    data_dir, personas_dir = write_fixture(tmp_path, items)

    calls = {"n": 0}

    def post_side_effect(*args, **kwargs):
        calls["n"] += 1
        payload = kwargs["json"]
        user_content = json.loads(payload["messages"][1]["content"])
        if user_content["story_id"] == "story_2":
            raise requests.ConnectionError("boom")
        return make_api_response(80, "不错")

    with patch.dict("os.environ", {"DEEPSEEK_API_KEY": "test-key"}, clear=True), patch(
        "scripts.persona_score.requests.post", side_effect=post_side_effect
    ), patch("scripts.persona_score.time.sleep"):
        rc = run_main(data_dir, personas_dir)

    assert rc == 0
    brief = read_brief(data_dir)
    by_id = {item["story_id"]: item for item in brief["items"]}
    assert by_id["story_1"]["persona_score"] == 80
    assert by_id["story_3"]["persona_score"] == 80
    for key in ("persona_id", "persona_score", "persona_review", "persona_meta"):
        assert key not in by_id["story_2"]


def test_cache_makes_second_run_api_free_and_persona_change_invalidates(tmp_path):
    items = [make_brief_item(1)]
    data_dir, personas_dir = write_fixture(tmp_path, items)
    env = {"DEEPSEEK_API_KEY": "test-key"}

    with patch.dict("os.environ", env, clear=True), patch(
        "scripts.persona_score.requests.post",
        return_value=make_api_response(75, "第一次"),
    ) as first_post:
        run_main(data_dir, personas_dir)
    assert first_post.call_count > 0

    # Second run: everything served from cache, zero API calls.
    with patch.dict("os.environ", env, clear=True), patch(
        "scripts.persona_score.requests.post",
        return_value=make_api_response(75, "第一次"),
    ) as second_post:
        run_main(data_dir, personas_dir)
    assert second_post.call_count == 0

    # Changing a persona file invalidates its cache entries and re-scores.
    (personas_dir / "pragmatic.md").write_text(
        PRAGMATIC_MD + "\n新增一行改变文件内容。\n", encoding="utf-8"
    )
    with patch.dict("os.environ", env, clear=True), patch(
        "scripts.persona_score.requests.post",
        return_value=make_api_response(60, "重评"),
    ) as third_post:
        run_main(data_dir, personas_dir)
    assert third_post.call_count > 0
    brief = read_brief(data_dir)
    assert brief["items"][0]["persona_score"] == 60
    assert brief["items"][0]["persona_review"] == "重评"


def test_title_change_invalidates_cache(tmp_path):
    items = [make_brief_item(1, title="Original title")]
    data_dir, personas_dir = write_fixture(tmp_path, items)
    env = {"DEEPSEEK_API_KEY": "test-key"}

    with patch.dict("os.environ", env, clear=True), patch(
        "scripts.persona_score.requests.post",
        return_value=make_api_response(75, "旧标题"),
    ):
        run_main(data_dir, personas_dir)

    # Same story_id, new title: rewrite the brief with the changed title.
    brief = read_brief(data_dir)
    for item in brief["items"]:
        item["title"] = "Changed title"
        for key in ("persona_id", "persona_score", "persona_review", "persona_meta"):
            item.pop(key, None)
    (data_dir / "daily-brief.json").write_text(
        json.dumps(brief, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    with patch.dict("os.environ", env, clear=True), patch(
        "scripts.persona_score.requests.post",
        return_value=make_api_response(55, "新标题重评"),
    ) as post:
        run_main(data_dir, personas_dir)

    assert post.call_count > 0
    brief = read_brief(data_dir)
    assert brief["items"][0]["persona_score"] == 55
    assert brief["items"][0]["persona_review"] == "新标题重评"
