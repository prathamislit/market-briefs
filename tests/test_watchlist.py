import json
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import watchlist


NOW = datetime(2026, 11, 24, 13, 15, tzinfo=timezone.utc)


class WatchlistTests(unittest.TestCase):
    def test_official_calendars_filter_and_normalize(self):
        bea = {"file_last_updated": "2026-10-22T12:48:27.656675",
               "Gross Domestic Product": {"release_dates": [
            "2026-11-24T13:15:00+00:00", "2026-11-25T13:30:00+00:00",
            "2026-11-28T13:30:00+00:00"]},
               "Personal Income and Outlays": {"release_dates": ["2026-11-25T13:30:00+00:00"]},
               "Corporate Profits": {"release_dates": ["2026-11-25T13:30:00+00:00"]}}
        bls = ("BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nSUMMARY:Employment\\, Situation\r\n"
               "DTSTART;TZID=US-Eastern:20261125T083000\r\nEND:VEVENT\r\n"
               "BEGIN:VEVENT\r\nSUMMARY:Older event\r\nDTSTART:20261123T133000Z\r\n"
               "END:VEVENT\r\nEND:VCALENDAR\r\n").encode()
        responses = {watchlist.BEA_URL: json.dumps(bea).encode(), watchlist.BLS_URL: bls}
        result = watchlist.collect_watchlist(NOW, responses.__getitem__)
        self.assertEqual(result["failures"], [])
        self.assertEqual(len(result["events"]), 4)
        self.assertEqual({event["title"] for event in result["events"] if event["source"] == "BEA"},
                         {"Gross Domestic Product", "Personal Income and Outlays", "Corporate Profits"})
        self.assertEqual({event["source"] for event in result["events"]}, {"BEA", "BLS"})
        self.assertEqual({event["starts_at"] for event in result["events"]}, {"2026-11-25T13:30:00+00:00"})
        self.assertTrue(all(event["source_url"].startswith("https://") for event in result["events"]))

    def test_source_failure_is_explicit_and_other_source_survives(self):
        def fetch(url):
            if url == watchlist.BEA_URL:
                raise OSError("offline")
            return ("BEGIN:VCALENDAR\nBEGIN:VEVENT\nSUMMARY:CPI release\n"
                    "DTSTART:20261125T133000Z\nEND:VEVENT\nEND:VCALENDAR\n").encode()
        result = watchlist.collect_watchlist(NOW, fetch)
        self.assertEqual(len(result["events"]), 1)
        self.assertEqual(result["failures"][0]["source"], "BEA")

    def test_bad_ics_and_oversized_response_do_not_invent_events(self):
        response = {watchlist.BEA_URL: b"x" * (watchlist.MAX_BYTES + 1),
                    watchlist.BLS_URL: b"BEGIN:VCALENDAR\nBEGIN:VEVENT\nSUMMARY:Event\nEND:VEVENT\nEND:VCALENDAR"}
        result = watchlist.collect_watchlist(NOW, response.__getitem__)
        self.assertEqual(result["events"], [])
        self.assertEqual({failure["source"] for failure in result["failures"]}, {"BEA", "BLS"})

    def test_bea_unknown_metadata_and_empty_calendar_fail_explicitly(self):
        for payload in ({"file_last_updated": "2026-11-24T12:00:00+00:00"},
                        {"file_last_updated": "2026-11-24T12:00:00+00:00",
                         "unexpected": "not a release"}):
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                watchlist._bea(json.dumps(payload).encode())

    def test_clock_requires_timezone(self):
        with self.assertRaises(ValueError):
            watchlist.collect_watchlist(datetime(2026, 11, 24))

    def test_all_day_event_keeps_date_without_fabricated_time(self):
        bea = b'{"Gross Domestic Product":{"release_dates":[]}}'
        bls = ("BEGIN:VCALENDAR\nBEGIN:VEVENT\nSUMMARY:Market holiday\n"
               "DTSTART;VALUE=DATE:20261126\nEND:VEVENT\nEND:VCALENDAR").encode()
        result = watchlist.collect_watchlist(NOW, {watchlist.BEA_URL: bea, watchlist.BLS_URL: bls}.__getitem__)
        self.assertIsNone(result["events"][0]["starts_at"])
        self.assertEqual(result["events"][0]["date"], "2026-11-26")


if __name__ == "__main__":
    unittest.main()
