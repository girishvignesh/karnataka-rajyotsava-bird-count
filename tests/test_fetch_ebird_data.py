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
            "IN-KA-MY", "Mysuru", {"housep": "House Sparrow"}, set()
        )

        self.assertEqual(len(checklists), 12)
        self.assertEqual(checklists[0]["species"], ["House Sparrow"])
        self.api_get.assert_any_call(
            "/product/lists/IN-KA-MY/2026/10/29", {"maxResults": 200}
        )

    def test_saturated_feed_is_not_silently_truncated(self):
        feed = [
            {"subId": f"S{index}"} for index in range(200)
        ]
        self.api_get.side_effect = [feed] + [{"obs": []}] * 200
        limited_feeds = set()
        checklists = fetch_ebird_data.fetch_district_checklists(
            "IN-KA-MY", "Mysuru", {}, limited_feeds
        )
        self.assertEqual(len(checklists), 200)
        self.assertEqual(limited_feeds, {("Mysuru", "2026-10-29")})

    def test_feed_and_detail_errors_abort_fetch(self):
        for responses in [
            [URLError("feed failed")],
            [[{"subId": "S1"}], URLError("details failed")],
        ]:
            with self.subTest(responses=responses):
                self.api_get.side_effect = responses
                with self.assertRaises(URLError):
                    fetch_ebird_data.fetch_district_checklists(
                        "IN-KA-MY", "Mysuru", {}, set()
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
            self.assertFalse((Path(output_dir) / "checklists.json").exists())


class ArchiveTests(unittest.TestCase):
    def setUp(self):
        self.directory = self.enterContext(tempfile.TemporaryDirectory())
        self.output = Path(self.directory)
        self.enterContext(patch.object(fetch_ebird_data, "OUT", self.output))
        self.enterContext(patch("builtins.print"))
        config = self.output / "districts.json"
        config.write_text(
            '{"districts": [{"code": "IN-KA-MY", "name": "Mysuru"}]}',
            encoding="utf-8",
        )
        self.enterContext(patch.object(fetch_ebird_data, "CONFIG", config))
        self.api_get = self.enterContext(patch.object(fetch_ebird_data, "api_get"))
        self.dates = self.enterContext(
            patch.object(
                fetch_ebird_data, "event_dates", return_value=[date(2026, 10, 29)]
            )
        )

    def run_refresh(self, ids_by_day, species="housep"):
        def response(path, params=None):
            if path == "/ref/taxonomy/ebird":
                return [{"speciesCode": species, "comName": species}]
            if path.startswith("/product/lists/"):
                return [
                    {"subId": checklist_id}
                    for checklist_id in ids_by_day[path.rsplit("/", 1)[-1]]
                ]
            return {
                "obs": [{"speciesCode": species}],
                "userDisplayName": "Not stored",
            }

        self.api_get.side_effect = response
        fetch_ebird_data.main()

    def read_json(self, filename):
        return json.loads((self.output / filename).read_text(encoding="utf-8"))

    def test_two_disjoint_pulls_accumulate_400_and_preserve_warning(self):
        self.run_refresh({"29": [f"S{i}" for i in range(200)]})
        self.run_refresh({"29": [f"S{i}" for i in range(200, 400)]})

        archive = self.read_json("checklists.json")
        summary = self.read_json("karnataka_summary.json")
        self.assertEqual(len(archive["checklists"]), 400)
        self.assertEqual(summary["checklists"], 400)
        self.assertEqual(summary["species"], 1)
        self.assertTrue(summary["coverageWarning"])
        self.assertEqual(
            set(archive["checklists"][0]),
            {"checklistId", "date", "district", "species"},
        )
        self.assertFalse((self.output / "checklists.json.tmp").exists())

        self.run_refresh({"29": []})
        self.assertEqual(self.read_json("karnataka_summary.json")["checklists"], 400)
        self.assertTrue(self.read_json("karnataka_summary.json")["coverageWarning"])

    def test_overlap_updates_existing_records_without_double_counting(self):
        self.run_refresh({"29": ["S1", "S2"]})
        self.run_refresh({"29": ["S2", "S3"]}, species="eursta")
        archive, limited_feeds = fetch_ebird_data.load_archive()
        self.assertEqual(len(archive), 3)
        self.assertEqual(archive["S2"]["species"], ["eursta"])
        self.assertEqual(archive["S1"]["species"], ["housep"])
        self.assertFalse(limited_feeds)
        self.assertFalse(self.read_json("karnataka_summary.json")["coverageWarning"])

    def test_next_day_and_late_submissions_preserve_previous_day(self):
        self.run_refresh({"29": ["S1", "S2"]})
        self.dates.return_value = [date(2026, 10, 29), date(2026, 10, 30)]
        self.run_refresh({"29": ["S3"], "30": ["S4", "S5"]})
        summary = self.read_json("karnataka_summary.json")
        self.assertEqual(summary["checklists"], 5)
        self.assertEqual(
            self.read_json("daily_summary.json"),
            [
                {"date": "2026-10-29", "checklists": 3, "species": 1},
                {"date": "2026-10-30", "checklists": 2, "species": 1},
            ],
        )
        self.assertEqual(self.read_json("district_summary.json")[0]["checklists"], 5)

    def test_failed_refresh_preserves_archive_and_all_summaries(self):
        self.run_refresh({"29": ["S1"]})
        original = {
            path: path.read_bytes() for path in self.output.glob("*.json")
        }
        self.api_get.side_effect = [
            [{"speciesCode": "housep", "comName": "House Sparrow"}],
            [{"subId": "S2"}, {"subId": "S3"}],
            {"obs": [{"speciesCode": "housep"}]},
            URLError("failed details"),
        ]
        with self.assertRaises(URLError):
            fetch_ebird_data.main()
        self.assertEqual(
            original, {path: path.read_bytes() for path in original}
        )

    def test_corrupt_archive_fails_without_overwriting(self):
        path = self.output / "checklists.json"
        path.write_text("{bad json", encoding="utf-8")
        with self.assertRaises(json.JSONDecodeError):
            fetch_ebird_data.main()
        self.assertEqual(path.read_text(encoding="utf-8"), "{bad json")
        self.api_get.assert_not_called()

    def test_archive_for_other_event_is_rejected(self):
        self.run_refresh({"29": ["S1"]})
        archive = self.read_json("checklists.json")
        archive["startDate"] = "2025-10-29"
        (self.output / "checklists.json").write_text(
            json.dumps(archive), encoding="utf-8"
        )
        with self.assertRaisesRegex(ValueError, "different event"):
            fetch_ebird_data.main()

    def test_duplicate_archive_ids_are_rejected(self):
        self.run_refresh({"29": ["S1"]})
        archive = self.read_json("checklists.json")
        archive["checklists"] *= 2
        (self.output / "checklists.json").write_text(
            json.dumps(archive), encoding="utf-8"
        )
        with self.assertRaisesRegex(ValueError, "duplicate"):
            fetch_ebird_data.load_archive()


if __name__ == "__main__":
    unittest.main()
