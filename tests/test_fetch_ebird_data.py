import json
import tempfile
import unittest
from datetime import date, datetime, timezone
from email.message import Message
from pathlib import Path
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError, URLError

from scripts import fetch_ebird_data
from scripts.fetch_ebird_data import species_names


class SpeciesNamesTests(unittest.TestCase):
    def test_resolves_species_codes_and_keeps_unmapped_codes(self):
        observations = [
            {"speciesCode": "housep", "comName": "House Sparrow"},
            {"speciesCode": "eursta"},
            {"speciesCode": "newcode"},
            {"comName": "House Sparrow"},
            {},
        ]

        self.assertEqual(
            species_names(observations, {"eursta": "European Starling"}),
            ["European Starling", "House Sparrow", "newcode"],
        )

    def test_build_summaries_counts_resolved_species(self):
        observations = [
            {"speciesCode": "eursta"},
            {"speciesCode": "housep"},
        ]
        checklist = {
            "district": "Mysuru",
            "date": "2026-10-30",
            "species": species_names(
                observations,
                {
                    "eursta": "European Starling",
                    "housep": "House Sparrow",
                },
            ),
        }

        with tempfile.TemporaryDirectory() as output_dir:
            with patch.object(fetch_ebird_data, "OUT", Path(output_dir)):
                fetch_ebird_data.build_summaries([checklist])

            summary = json.loads(
                (Path(output_dir) / "karnataka_summary.json").read_text(
                    encoding="utf-8"
                )
            )

        self.assertEqual(summary["checklists"], 1)
        self.assertEqual(summary["species"], 2)
        self.assertEqual(
            summary["speciesList"], ["European Starling", "House Sparrow"]
        )


class ApiGetTests(unittest.TestCase):
    def setUp(self):
        self.sleep = self.enterContext(
            patch.object(fetch_ebird_data.time, "sleep")
        )
        self.urlopen = self.enterContext(
            patch.object(fetch_ebird_data, "urlopen")
        )
        self.enterContext(patch.dict("os.environ", {"EBIRD_API_KEY": "test-key"}))
        self.enterContext(patch("builtins.print"))
        response = MagicMock()
        response.__enter__.return_value.read.return_value = b'{"ok": true}'
        self.response = response
        self.urlopen.return_value = response

    def http_error(self, code, retry_after=None):
        headers = Message()
        if retry_after is not None:
            headers["Retry-After"] = retry_after
        return HTTPError("https://api.ebird.org/v2/test", code, "", headers, None)

    def test_paces_requests_and_encodes_parameters(self):
        self.assertEqual(
            fetch_ebird_data.api_get("/test", {"maxResults": 200}), {"ok": True}
        )
        self.sleep.assert_called_once_with(
            fetch_ebird_data.REQUEST_DELAY_SECONDS
        )
        request = self.urlopen.call_args.args[0]
        self.assertEqual(
            request.full_url, "https://api.ebird.org/v2/test?maxResults=200"
        )

    def test_retries_rate_limit_and_honors_retry_after(self):
        self.urlopen.side_effect = [
            self.http_error(429, "120"),
            self.response,
        ]
        self.assertEqual(fetch_ebird_data.api_get("/test"), {"ok": True})
        self.assertEqual(
            [call.args[0] for call in self.sleep.call_args_list], [3, 120, 3]
        )

    def test_honors_http_date_retry_after(self):
        self.urlopen.side_effect = [
            self.http_error(429, "Thu, 29 Oct 2026 10:02:00 GMT"),
            self.response,
        ]
        with patch.object(fetch_ebird_data, "datetime") as clock:
            clock.now.return_value = datetime(
                2026, 10, 29, 10, 0, tzinfo=timezone.utc
            )
            fetch_ebird_data.api_get("/test")
        self.assertEqual(
            [call.args[0] for call in self.sleep.call_args_list], [3, 120, 3]
        )

    def test_retries_transient_failures_with_exponential_backoff(self):
        self.urlopen.side_effect = [
            self.http_error(503),
            URLError("connection reset"),
            TimeoutError("timed out"),
            self.response,
        ]
        self.assertEqual(fetch_ebird_data.api_get("/test"), {"ok": True})
        self.assertEqual(
            [call.args[0] for call in self.sleep.call_args_list],
            [3, 60, 3, 120, 3, 240, 3],
        )

    def test_exhausted_retries_raise_instead_of_returning_partial_data(self):
        self.urlopen.side_effect = self.http_error(429)
        with self.assertRaises(HTTPError):
            fetch_ebird_data.api_get("/test")
        self.assertEqual(
            self.urlopen.call_count, fetch_ebird_data.MAX_REQUEST_ATTEMPTS
        )

    def test_non_transient_http_error_is_not_retried(self):
        self.urlopen.side_effect = self.http_error(403)
        with self.assertRaises(HTTPError):
            fetch_ebird_data.api_get("/test")
        self.assertEqual(self.urlopen.call_count, 1)


class EventDatesTests(unittest.TestCase):
    def test_before_event_has_no_dates(self):
        self.assertEqual(list(fetch_ebird_data.event_dates(date(2026, 10, 28))), [])

    def test_includes_elapsed_days_from_29th_but_not_future_dates(self):
        self.assertEqual(
            list(fetch_ebird_data.event_dates(date(2026, 10, 30))),
            [date(2026, 10, 29), date(2026, 10, 30)],
        )

    def test_after_event_includes_entire_event(self):
        dates = list(fetch_ebird_data.event_dates(date(2026, 11, 3)))
        self.assertEqual(len(dates), 5)
        self.assertEqual(dates[-1], date(2026, 11, 2))

    def test_today_uses_india_timezone(self):
        with patch.object(fetch_ebird_data, "datetime") as clock:
            clock.now.return_value = datetime(
                2026, 10, 29, 0, 15, tzinfo=fetch_ebird_data.IST
            )
            self.assertEqual(
                list(fetch_ebird_data.event_dates()), [date(2026, 10, 29)]
            )
        clock.now.assert_called_once_with(fetch_ebird_data.IST)


class FetchChecklistsTests(unittest.TestCase):
    def setUp(self):
        self.api_get = self.enterContext(
            patch.object(fetch_ebird_data, "api_get")
        )
        self.enterContext(
            patch.object(
                fetch_ebird_data, "event_dates", return_value=[date(2026, 10, 29)]
            )
        )
        self.enterContext(patch("builtins.print"))

    def test_requests_maximum_feed_and_keeps_more_than_ten_checklists(self):
        feed = [{"subId": f"S{index}"} for index in range(12)]
        details = {"obs": [{"speciesCode": "housep"}]}
        self.api_get.side_effect = [feed] + [details] * 12

        checklists = fetch_ebird_data.fetch_district_checklists(
            "IN-KA-MY", "Mysuru", {"housep": "House Sparrow"}
        )

        self.assertEqual(len(checklists), 12)
        self.assertEqual(checklists[0]["species"], ["House Sparrow"])
        self.api_get.assert_any_call(
            "/product/lists/IN-KA-MY/2026/10/29", {"maxResults": 200}
        )

    def test_saturated_feed_is_not_silently_truncated(self):
        self.api_get.return_value = [
            {"subId": f"S{index}"} for index in range(200)
        ]
        with self.assertRaisesRegex(RuntimeError, "200-result limit"):
            fetch_ebird_data.fetch_district_checklists("IN-KA-MY", "Mysuru", {})

    def test_feed_and_detail_errors_abort_fetch(self):
        for responses in [
            [URLError("feed failed")],
            [[{"subId": "S1"}], URLError("details failed")],
        ]:
            with self.subTest(responses=responses):
                self.api_get.side_effect = responses
                with self.assertRaises(URLError):
                    fetch_ebird_data.fetch_district_checklists(
                        "IN-KA-MY", "Mysuru", {}
                    )

    def test_failed_refresh_leaves_existing_summary_unchanged(self):
        with tempfile.TemporaryDirectory() as output_dir:
            summary_path = Path(output_dir) / "karnataka_summary.json"
            original = '{"checklists": 98}'
            summary_path.write_text(original, encoding="utf-8")
            self.api_get.side_effect = [
                [{"speciesCode": "housep", "comName": "House Sparrow"}],
                URLError("feed failed"),
            ]
            with patch.object(fetch_ebird_data, "OUT", Path(output_dir)):
                with self.assertRaises(URLError):
                    fetch_ebird_data.main()
            self.assertEqual(summary_path.read_text(encoding="utf-8"), original)


if __name__ == "__main__":
    unittest.main()
