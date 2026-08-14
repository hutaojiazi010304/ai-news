import unittest
import warnings
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

from dateutil.parser import UnknownTimezoneWarning

from scripts.update_news import (
    make_item_id,
    normalize_url,
    parse_date_any,
    parse_iso,
    parse_opml_subscriptions,
    parse_relative_time_zh,
)


class UtilsTests(unittest.TestCase):
    def test_normalize_url_removes_tracking(self):
        raw = "https://example.com/path?a=1&utm_source=x&fbclid=abc"
        self.assertEqual(normalize_url(raw), "https://example.com/path?a=1")

    def test_make_item_id_stable(self):
        a = make_item_id("site", "src", "Title", "https://a.com?p=1&utm_source=x")
        b = make_item_id("site", "src", "Title", "https://a.com?p=1")
        self.assertEqual(a, b)

    def test_parse_relative_time_zh_minutes(self):
        now = datetime(2026, 2, 19, 12, 0, tzinfo=timezone.utc)
        dt = parse_relative_time_zh("8分钟前", now)
        self.assertEqual(dt, datetime(2026, 2, 19, 11, 52, tzinfo=timezone.utc))

    def test_parse_date_any_english_rfc_not_misparsed_as_today(self):
        now = datetime(2026, 2, 21, 4, 30, tzinfo=timezone.utc)
        dt = parse_date_any("Tue, 07 Oct 2025 03:00:00 GMT", now)
        self.assertEqual(dt, datetime(2025, 10, 7, 3, 0, tzinfo=timezone.utc))

    def test_parse_date_any_handles_ambiguous_timezone_abbreviations(self):
        now = datetime(2026, 2, 21, 4, 30, tzinfo=timezone.utc)
        cases = (
            (
                "Mon, 24 Feb 2025 10:00:00 CET",
                datetime(2025, 2, 24, 9, 0, tzinfo=timezone.utc),
            ),
            (
                "Mon, 24 Feb 2025 10:00:00 EST",
                datetime(2025, 2, 24, 15, 0, tzinfo=timezone.utc),
            ),
        )

        with warnings.catch_warnings():
            warnings.simplefilter("error", UnknownTimezoneWarning)
            for value, expected in cases:
                with self.subTest(timezone=value.rsplit(" ", 1)[-1]):
                    self.assertEqual(parse_date_any(value, now), expected)

    def test_parse_iso_handles_ambiguous_timezone_abbreviation(self):
        with warnings.catch_warnings():
            warnings.simplefilter("error", UnknownTimezoneWarning)
            dt = parse_iso("Mon, 24 Feb 2025 10:00:00 CET")

        self.assertEqual(dt, datetime(2025, 2, 24, 9, 0, tzinfo=timezone.utc))

    def test_parse_opml_subscriptions(self):
        opml = """<?xml version="1.0" encoding="UTF-8"?>
<opml version="2.0"><body>
<outline text="A" title="A" xmlUrl="https://a.com/feed.xml" />
<outline text="A2" title="A2" xmlUrl="https://a.com/feed.xml" />
<outline text="B" xmlUrl="https://b.com/rss" />
</body></opml>"""
        with TemporaryDirectory() as td:
            p = Path(td) / "x.opml"
            p.write_text(opml, encoding="utf-8")
            feeds = parse_opml_subscriptions(p)
        self.assertEqual(len(feeds), 2)
        self.assertEqual(feeds[0]["title"], "A")
        self.assertEqual(feeds[1]["title"], "B")


if __name__ == "__main__":
    unittest.main()
