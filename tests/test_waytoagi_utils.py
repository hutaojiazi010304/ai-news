import unittest
from datetime import datetime

from scripts.update_news import (
    SH_TZ,
    block_text_runs,
    clean_update_title,
    decode_escaped_json,
    extract_waytoagi_recent_updates_from_block_map,
    infer_shanghai_year_for_month_day,
    parse_md_heading,
    parse_ym_heading,
)


class WaytoAgiUtilsTests(unittest.TestCase):
    def test_parse_ym_heading(self):
        self.assertEqual(parse_ym_heading("2026 年 2 月"), (2026, 2))

    def test_parse_md_heading(self):
        self.assertEqual(parse_md_heading("2 月 9 日"), (2, 9))

    def test_clean_update_title(self):
        self.assertEqual(clean_update_title("《 》  AI  更新  测试  "), "AI 更新 测试")

    def test_decode_escaped_json(self):
        raw = '{\\"id\\":\\"x\\",\\"type\\":\\"mention_doc\\",\\"data\\":{\\"title\\":\\"历史更新\\"}}'
        obj = decode_escaped_json(raw)
        self.assertEqual(obj["data"]["title"], "历史更新")

    def test_infer_shanghai_year_for_month_day(self):
        now = datetime(2026, 1, 2, 10, 0, tzinfo=SH_TZ)
        self.assertEqual(infer_shanghai_year_for_month_day(now, 12, 31), 2025)
        self.assertEqual(infer_shanghai_year_for_month_day(now, 1, 1), 2026)

    def test_extract_recent_updates_from_block_map(self):
        now = datetime(2026, 2, 20, 10, 0, tzinfo=SH_TZ)
        block_map = {
            "sec": {
                "data": {
                    "type": "heading1",
                    "parent_id": "root",
                    "text": {"initialAttributedTexts": {"text": {"0": "近 7 日更新日志"}}},
                }
            },
            "h1": {
                "data": {
                    "type": "heading3",
                    "parent_id": "root",
                    "text": {"initialAttributedTexts": {"text": {"0": "2 月 20 日"}}},
                }
            },
            "b1": {
                "data": {
                    "type": "bullet",
                    "parent_id": "h1",
                    "text": {"initialAttributedTexts": {"text": {"0": "《 》 OpenClaw 新教程"}}},
                }
            },
            "h2": {
                "data": {
                    "type": "heading3",
                    "parent_id": "other-root",
                    "text": {"initialAttributedTexts": {"text": {"0": "2 月 20 日"}}},
                }
            },
            "b2": {
                "data": {
                    "type": "bullet",
                    "parent_id": "h2",
                    "text": {"initialAttributedTexts": {"text": {"0": "不会被收集"}}},
                }
            },
        }
        out = extract_waytoagi_recent_updates_from_block_map(block_map, now, "https://example.com")
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["date"], "2026-02-20")
        self.assertEqual(out[0]["title"], "OpenClaw 新教程")

    def test_block_text_runs_with_inline_component(self):
        """A block with an inline-component mention_doc should produce a linked
        title run and a plain summary run."""
        block_data = {
            "type": "bullet",
            "parent_id": "h1",
            "text": {
                "apool": {
                    "nextNum": 3,
                    "numToAttrib": {
                        "0": ["author", "12345"],
                        "1": [
                            "inline-component",
                            '{"id":"abc","type":"mention_doc","data":{"token":"T1","raw_url":"https://example.com/article","title":"AI 混合搜索入门"}}',
                        ],
                        "2": ["link-id", "uuid-1"],
                    },
                },
                "initialAttributedTexts": {
                    "attribs": {"0": "*0+1*0*1*2+1*0+3p"},
                    "text": {"0": "《 》这篇文章介绍了混合搜索的方方面面"},
                },
            },
        }
        runs = block_text_runs(block_data)
        self.assertEqual(len(runs), 2)
        self.assertEqual(runs[0]["text"], "AI 混合搜索入门")
        self.assertEqual(runs[0]["link"], "https://example.com/article")
        self.assertEqual(runs[1]["text"], "这篇文章介绍了混合搜索的方方面面")
        self.assertIsNone(runs[1]["link"])

    def test_block_text_runs_without_link(self):
        """A block without inline-component falls back to a single plain run."""
        block_data = {
            "type": "bullet",
            "parent_id": "h1",
            "text": {
                "initialAttributedTexts": {
                    "text": {"0": "纯文字更新，没有链接"},
                },
            },
        }
        runs = block_text_runs(block_data)
        self.assertEqual(len(runs), 1)
        self.assertEqual(runs[0]["text"], "纯文字更新，没有链接")
        self.assertIsNone(runs[0]["link"])

    def test_extract_updates_linked_title_and_summary(self):
        """When a block has an inline-component, the extracted title should be
        the mention_doc title (not the full paragraph) and summary should be
        the remaining text."""
        now = datetime(2026, 2, 20, 10, 0, tzinfo=SH_TZ)
        block_map = {
            "sec": {
                "data": {
                    "type": "heading1",
                    "parent_id": "root",
                    "text": {"initialAttributedTexts": {"text": {"0": "近 7 日更新日志"}}},
                }
            },
            "h1": {
                "data": {
                    "type": "heading3",
                    "parent_id": "root",
                    "text": {"initialAttributedTexts": {"text": {"0": "2 月 20 日"}}},
                }
            },
            "b1": {
                "data": {
                    "type": "bullet",
                    "parent_id": "h1",
                    "text": {
                        "apool": {
                            "nextNum": 3,
                            "numToAttrib": {
                                "0": ["author", "99999"],
                                "1": [
                                    "inline-component",
                                    '{"id":"xyz","type":"mention_doc","data":{"token":"T2","raw_url":"https://example.com/real-article","title":"BrowserAct 拆解"}}',
                                ],
                                "2": ["link-id", "uuid-2"],
                            },
                        },
                        "initialAttributedTexts": {
                            "attribs": {"0": "*0+1*0*1*2+1*0+20"},
                            "text": {
                                "0": "《 》这篇文章拆解了 BrowserAct 的核心亮点以及安装步骤。"
                            },
                        },
                    },
                },
            },
        }
        out = extract_waytoagi_recent_updates_from_block_map(
            block_map, now, "https://example.com/page"
        )
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["title"], "BrowserAct 拆解")
        self.assertEqual(
            out[0]["summary"],
            "这篇文章拆解了 BrowserAct 的核心亮点以及安装步骤。",
        )
        self.assertEqual(out[0]["url"], "https://example.com/real-article")


if __name__ == "__main__":
    unittest.main()
