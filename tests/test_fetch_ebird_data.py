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

    def test_day_before_event_has_no_dates(self):
        self.assertEqual(list(fetch_ebird_data.event_dates(date(2026, 10, 29))), [])

    def test_includes_elapsed_days_from_30th_but_not_future_dates(self):
        self.assertEqual(
            list(fetch_ebird_data.event_dates(date(2026, 10, 30))),
            [date(2026, 10, 30)],
        )

    def test_after_event_includes_entire_event(self):
        dates = list(fetch_ebird_data.event_dates(date(2026, 11, 3)))
        self.assertEqual(
            dates,
            [date(2026, 10, 30), date(2026, 10, 31),
             date(2026, 11, 1), date(2026, 11, 2)],
        )
        self.assertEqual(dates[-1], date(2026, 11, 2))

    def test_today_uses_india_timezone(self):
        with patch.object(fetch_ebird_data, "datetime") as clock:
            clock.now.return_value = datetime(
                2026, 10, 30, 0, 15, tzinfo=fetch_ebird_data.IST
            )
            self.assertEqual(
                list(fetch_ebird_data.event_dates()), [date(2026, 10, 30)]
            )
        clock.now.assert_called_once_with(fetch_ebird_data.IST)


class DeploymentWindowTests(unittest.TestCase):
    def test_deploy_refresh_requests_only_elapsed_karnataka_event_dates(self):
        config = json.loads(fetch_ebird_data.CONFIG.read_text(encoding="utf-8"))
        for today, expected_days in (
            (date(2026, 10, 29), []),
            (date(2026, 10, 30), ["2026/10/30"]),
            (date(2026, 11, 3), ["2026/10/30", "2026/10/31", "2026/11/1", "2026/11/2"]),
        ):
            with self.subTest(today=today), tempfile.TemporaryDirectory() as output_dir:
                with (
                    patch.object(fetch_ebird_data, "OUT", Path(output_dir)),
                    patch.object(fetch_ebird_data, "api_get", return_value=[]) as api_get,
                    patch.object(fetch_ebird_data, "datetime", wraps=datetime) as clock,
                    patch("builtins.print"),
                ):
                    clock.now.return_value = datetime(
                        today.year, today.month, today.day, tzinfo=fetch_ebird_data.IST
                    )
                    fetch_ebird_data.main()

                feed_paths = [
                    call.args[0] for call in api_get.call_args_list
                    if call.args[0].startswith("/product/lists/")
                ]
                self.assertEqual(
                    feed_paths,
                    [
                        f"/product/lists/{district['code']}/{day}"
                        for district in config["districts"] for day in expected_days
                    ],
                )
                self.assertTrue(all("/IN-KA-" in path for path in feed_paths))
                summary = json.loads(
                    (Path(output_dir) / "karnataka_summary.json").read_text(encoding="utf-8")
                )
                self.assertEqual(summary["checklists"], 0)
                self.assertEqual(summary["startDate"], "2026-10-30")
                self.assertEqual(summary["endDate"], "2026-11-02")


class ChecklistDateTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch("builtins.print"))

    def test_both_dates_must_be_inclusive_event_dates(self):
        days = [
            "2025-10-30", "2026-10-29", "2026-10-30",
            "2026-10-31", "2026-11-01", "2026-11-02", "2026-11-03",
        ]
        eligible = {"2026-10-30", "2026-10-31", "2026-11-01", "2026-11-02"}
        for observed in days:
            for submitted in days:
                with self.subTest(observed=observed, submitted=submitted):
                    record = fetch_ebird_data.checklist_record(
                        "S1", "Mysuru",
                        {
                            "obsDt": f"{observed} 00:00",
                            "creationDt": f"{submitted} 23:59:59",
                            "lastEditedDt": "2026-12-01",
                            "subnational1Code": "IN-KA",
                            "durationHrs": 0.25,
                        },
                        {},
                    )
                    self.assertEqual(
                        record is not None, observed in eligible and submitted in eligible
                    )

    def test_missing_or_invalid_dates_fail_explicitly(self):
        for field in ("obsDt", "creationDt"):
            for invalid in (None, "", "not-a-date", "2026-02-30", 123):
                with self.subTest(field=field, value=invalid):
                    details = {"obsDt": "2026-10-30", "creationDt": "2026-10-30"}
                    details[field] = invalid
                    with self.assertRaisesRegex(ValueError, field):
                        fetch_ebird_data.checklist_record("S1", "Mysuru", details, {})

    def test_explicit_offsets_are_converted_to_ist_at_boundaries(self):
        self.assertEqual(
            fetch_ebird_data.checklist_date("2026-10-29T18:30:00Z", "creationDt", "S1"),
            date(2026, 10, 30),
        )
        self.assertEqual(
            fetch_ebird_data.checklist_date("2026-11-02T18:29:59Z", "creationDt", "S1"),
            date(2026, 11, 2),
        )
        self.assertEqual(
            fetch_ebird_data.checklist_date("2026-11-02T18:30:00Z", "creationDt", "S1"),
            date(2026, 11, 3),
        )


class ChecklistScopeTests(unittest.TestCase):
    def setUp(self):
        self.print = self.enterContext(patch("builtins.print"))
        self.details = {
            "obsDt": "2026-10-30", "creationDt": "2026-10-30",
            "subnational1Code": "IN-KA", "durationHrs": 0.25,
        }

    def test_duration_threshold_is_exactly_fifteen_minutes_in_hours(self):
        for duration in (0, 0.1, 14 / 60, 0.249999, 0.25, 0.250001, 1, 2):
            with self.subTest(duration=duration):
                self.details["durationHrs"] = duration
                record = fetch_ebird_data.checklist_record("S1", "Mysuru", self.details, {})
                self.assertEqual(record is not None, duration >= 0.25)
                if record is not None:
                    self.assertEqual(record["durationHrs"], duration)
                    self.assertEqual(record["subnational1Code"], "IN-KA")

    def test_missing_duration_is_excluded_with_reason(self):
        for omit in (True, False):
            with self.subTest(omit=omit):
                if omit:
                    self.details.pop("durationHrs")
                else:
                    self.details["durationHrs"] = None
                self.assertIsNone(
                    fetch_ebird_data.checklist_record("S1", "Mysuru", self.details, {})
                )
                self.print.assert_called_with(
                    "Excluding S1: durationHrs is not reported."
                )

    def test_malformed_duration_fails_explicitly(self):
        for duration in ("0.25", "", True, False, -1, float("nan"), float("inf"), []):
            with self.subTest(duration=duration):
                self.details["durationHrs"] = duration
                with self.assertRaisesRegex(ValueError, "durationHrs"):
                    fetch_ebird_data.checklist_record("S1", "Mysuru", self.details, {})

    def test_non_karnataka_checklists_are_excluded_despite_district_feed(self):
        for state_code in ("IN-TN", "IN-KL", "US-NY", "IN-KA-MY"):
            with self.subTest(state_code=state_code):
                self.details["subnational1Code"] = state_code
                self.assertIsNone(
                    fetch_ebird_data.checklist_record("S1", "Mysuru", self.details, {})
                )
                self.print.assert_called()

    def test_missing_or_invalid_state_fails_explicitly(self):
        for state_code in (None, "", 123):
            with self.subTest(state_code=state_code):
                self.details["subnational1Code"] = state_code
                with self.assertRaisesRegex(ValueError, "subnational1Code"):
                    fetch_ebird_data.checklist_record("S1", "Mysuru", self.details, {})


class FetchChecklistsTests(unittest.TestCase):
    def setUp(self):
        self.api_get = self.enterContext(
            patch.object(fetch_ebird_data, "api_get")
        )
        self.enterContext(
            patch.object(
                fetch_ebird_data, "event_dates", return_value=[date(2026, 10, 30)]
            )
        )
        self.enterContext(patch("builtins.print"))

    def test_requests_maximum_feed_and_keeps_more_than_ten_checklists(self):
        feed = [{"subId": f"S{index}"} for index in range(12)]
        details = {
            "obsDt": "2026-10-30 08:00", "creationDt": "2026-10-30 12:00",
            "subnational1Code": "IN-KA", "durationHrs": 0.25,
            "obs": [{"speciesCode": "housep"}],
        }
        self.api_get.side_effect = [feed] + [details] * 12

        checklists = fetch_ebird_data.fetch_district_checklists(
            "IN-KA-MY", "Mysuru", {"housep": "House Sparrow"}, set()
        )

        self.assertEqual(len(checklists), 12)
        self.assertEqual(checklists[0]["species"], ["House Sparrow"])
        self.api_get.assert_any_call(
            "/product/lists/IN-KA-MY/2026/10/30", {"maxResults": 200}
        )

    def test_saturated_feed_is_not_silently_truncated(self):
        feed = [
            {"subId": f"S{index}"} for index in range(200)
        ]
        self.api_get.side_effect = [feed] + [{
            "obsDt": "2026-10-30", "creationDt": "2026-10-30", "obs": [],
            "subnational1Code": "IN-KA", "durationHrs": 0.25,
        }] * 200
        limited_feeds = set()
        checklists = fetch_ebird_data.fetch_district_checklists(
            "IN-KA-MY", "Mysuru", {}, limited_feeds
        )
        self.assertEqual(len(checklists), 200)
        self.assertEqual(limited_feeds, {("Mysuru", "2026-10-30")})

    def test_feed_day_is_not_used_as_observation_or_submission_date(self):
        self.api_get.side_effect = [
            [{"subId": "old"}, {"subId": "late"}, {"subId": "valid"}],
            {"obsDt": "2026-10-29", "creationDt": "2026-10-30"},
            {"obsDt": "2026-10-30", "creationDt": "2026-11-03"},
            {
                "obsDt": "2026-10-31", "creationDt": "2026-11-02",
                "subnational1Code": "IN-KA", "durationHrs": 0.25,
            },
        ]
        excluded_ids = set()
        checklists = fetch_ebird_data.fetch_district_checklists(
            "IN-KA-MY", "Mysuru", {}, set(), excluded_ids
        )
        self.assertEqual([record["checklistId"] for record in checklists], ["valid"])
        self.assertEqual(checklists[0]["date"], "2026-10-31")
        self.assertEqual(checklists[0]["submittedDate"], "2026-11-02")
        self.assertEqual(excluded_ids, {"old", "late"})

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

    def test_non_karnataka_district_is_rejected_before_api_request(self):
        for code in ("IN-TN-CH", "IN-KL", "IN-KAX-MY"):
            with self.subTest(code=code):
                with self.assertRaisesRegex(ValueError, "not in Karnataka"):
                    fetch_ebird_data.fetch_district_checklists(code, "Other", {}, set())
        self.api_get.assert_not_called()

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
                fetch_ebird_data, "event_dates", return_value=[date(2026, 10, 30)]
            )
        )

    def run_refresh(self, ids_by_day, species="housep"):
        observation_dates = {
            checklist_id: day.isoformat()
            for day in self.dates.return_value
            for checklist_id in ids_by_day.get(str(day.day), [])
        }

        def response(path, params=None):
            if path == "/ref/taxonomy/ebird":
                return [{"speciesCode": species, "comName": species}]
            if path.startswith("/product/lists/"):
                return [
                    {"subId": checklist_id}
                    for checklist_id in ids_by_day[path.rsplit("/", 1)[-1]]
                ]
            return {
                "obsDt": observation_dates.get(path.rsplit("/", 1)[-1], "2026-10-30"),
                "creationDt": "2026-11-01",
                "subnational1Code": "IN-KA", "durationHrs": 0.25,
                "obs": [{"speciesCode": species}],
                "userDisplayName": "Not stored",
            }

        self.api_get.side_effect = response
        fetch_ebird_data.main()

    def read_json(self, filename):
        return json.loads((self.output / filename).read_text(encoding="utf-8"))

    def test_two_disjoint_pulls_accumulate_400_and_preserve_warning(self):
        self.run_refresh({"30": [f"S{i}" for i in range(200)]})
        self.run_refresh({"30": [f"S{i}" for i in range(200, 400)]})

        archive = self.read_json("checklists.json")
        summary = self.read_json("karnataka_summary.json")
        self.assertEqual(len(archive["checklists"]), 400)
        self.assertEqual(summary["checklists"], 400)
        self.assertEqual(summary["species"], 1)
        self.assertTrue(summary["coverageWarning"])
        self.assertEqual(
            set(archive["checklists"][0]),
            {
                "checklistId", "date", "submittedDate", "district", "species",
                "subnational1Code", "durationHrs",
            },
        )
        self.assertFalse((self.output / "checklists.json.tmp").exists())

        self.run_refresh({"30": []})
        self.assertEqual(self.read_json("karnataka_summary.json")["checklists"], 400)
        self.assertTrue(self.read_json("karnataka_summary.json")["coverageWarning"])

    def test_overlap_updates_existing_records_without_double_counting(self):
        self.run_refresh({"30": ["S1", "S2"]})
        self.run_refresh({"30": ["S2", "S3"]}, species="eursta")
        archive, limited_feeds = fetch_ebird_data.load_archive()
        self.assertEqual(len(archive), 3)
        self.assertEqual(archive["S2"]["species"], ["eursta"])
        self.assertEqual(archive["S1"]["species"], ["housep"])
        self.assertFalse(limited_feeds)
        self.assertFalse(self.read_json("karnataka_summary.json")["coverageWarning"])

    def test_next_day_and_late_submissions_preserve_previous_day(self):
        self.run_refresh({"30": ["S1", "S2"]})
        self.dates.return_value = [date(2026, 10, 30), date(2026, 10, 31)]
        self.run_refresh({"30": ["S3"], "31": ["S4", "S5"]})
        summary = self.read_json("karnataka_summary.json")
        self.assertEqual(summary["checklists"], 5)
        self.assertEqual(
            self.read_json("daily_summary.json"),
            [
                {"date": "2026-10-30", "checklists": 3, "species": 1},
                {"date": "2026-10-31", "checklists": 2, "species": 1},
            ],
        )
        self.assertEqual(self.read_json("district_summary.json")[0]["checklists"], 5)

    def test_failed_refresh_preserves_archive_and_all_summaries(self):
        self.run_refresh({"30": ["S1"]})
        original = {
            path: path.read_bytes() for path in self.output.glob("*.json")
        }
        self.api_get.side_effect = [
            [{"speciesCode": "housep", "comName": "House Sparrow"}],
            [{"subId": "S2"}, {"subId": "S3"}],
            {
                "obsDt": "2026-10-30", "creationDt": "2026-10-30",
                "subnational1Code": "IN-KA", "durationHrs": 0.25,
                "obs": [{"speciesCode": "housep"}],
            },
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
        self.run_refresh({"30": ["S1"]})
        archive = self.read_json("checklists.json")
        archive["startDate"] = "2025-10-29"
        (self.output / "checklists.json").write_text(
            json.dumps(archive), encoding="utf-8"
        )
        with self.assertRaisesRegex(ValueError, "different event"):
            fetch_ebird_data.main()

    def test_duplicate_archive_ids_are_rejected(self):
        self.run_refresh({"30": ["S1"]})
        archive = self.read_json("checklists.json")
        archive["checklists"] *= 2
        (self.output / "checklists.json").write_text(
            json.dumps(archive), encoding="utf-8"
        )
        with self.assertRaisesRegex(ValueError, "duplicate"):
            fetch_ebird_data.load_archive()

    def test_legacy_archive_rechecks_submission_dates_and_removes_october_29(self):
        archive = {
            "startDate": "2026-10-29",
            "endDate": "2026-11-02",
            "checklists": [
                {
                    "checklistId": checklist_id, "date": day,
                    "district": "Mysuru", "species": ["housep"],
                }
                for checklist_id, day in [
                    ("old", "2026-10-29"), ("late", "2026-10-30"),
                    ("valid", "2026-10-30"),
                ]
            ],
            "limitedFeeds": [{"district": "Mysuru", "date": "2026-10-29"}],
        }
        (self.output / "checklists.json").write_text(json.dumps(archive), encoding="utf-8")

        def response(path, params=None):
            if path == "/ref/taxonomy/ebird":
                return []
            if path.startswith("/product/lists/"):
                return []
            return {
                "obsDt": "2026-10-30",
                "creationDt": "2026-11-03" if path.endswith("/late") else "2026-11-02",
                "subnational1Code": "IN-KA", "durationHrs": 0.25,
            }

        self.api_get.side_effect = response
        fetch_ebird_data.main()
        saved = self.read_json("checklists.json")
        self.assertEqual(saved["startDate"], "2026-10-30")
        self.assertEqual([record["checklistId"] for record in saved["checklists"]], ["valid"])
        self.assertEqual(saved["checklists"][0]["submittedDate"], "2026-11-02")
        self.assertEqual(saved["limitedFeeds"], [])
        self.assertEqual(self.read_json("karnataka_summary.json")["checklists"], 1)

    def test_newly_ineligible_checklist_is_removed_from_archive(self):
        self.run_refresh({"30": ["S1"]})
        self.api_get.side_effect = [
            [],
            [{"subId": "S1"}],
            {"obsDt": "2026-10-29", "creationDt": "2026-10-30"},
        ]
        fetch_ebird_data.main()
        self.assertEqual(self.read_json("checklists.json")["checklists"], [])
        self.assertEqual(self.read_json("karnataka_summary.json")["checklists"], 0)
        self.assertEqual(self.read_json("daily_summary.json"), [])
        self.assertEqual(self.read_json("district_summary.json"), [])

    def test_missing_submission_date_does_not_replace_published_files(self):
        self.run_refresh({"30": ["S1"]})
        original = {path: path.read_bytes() for path in self.output.glob("*.json")}
        self.api_get.side_effect = [
            [], [{"subId": "S2"}], {"obsDt": "2026-10-30"},
        ]
        with self.assertRaisesRegex(ValueError, "creationDt"):
            fetch_ebird_data.main()
        self.assertEqual(original, {path: path.read_bytes() for path in original})

    def test_archived_submission_outside_window_is_removed_without_feed_entry(self):
        self.run_refresh({"30": ["S1"]})
        archive = self.read_json("checklists.json")
        archive["checklists"][0]["submittedDate"] = "2026-11-03"
        (self.output / "checklists.json").write_text(json.dumps(archive), encoding="utf-8")
        self.run_refresh({"30": []})
        self.assertEqual(self.read_json("checklists.json")["checklists"], [])
        self.assertEqual(self.read_json("karnataka_summary.json")["checklists"], 0)

    def test_current_range_legacy_archive_requires_date_revalidation(self):
        self.run_refresh({"30": ["S1"]})
        archive = self.read_json("checklists.json")
        del archive["checklists"][0]["submittedDate"]
        (self.output / "checklists.json").write_text(json.dumps(archive), encoding="utf-8")
        self.api_get.side_effect = [
            [], {"obsDt": "2026-10-30", "creationDt": "2026-11-03"}, [],
        ]
        fetch_ebird_data.main()
        self.api_get.assert_any_call("/product/checklist/view/S1")
        self.assertEqual(self.read_json("checklists.json")["checklists"], [])
        self.assertEqual(self.read_json("karnataka_summary.json")["checklists"], 0)

    def test_refresh_filters_dates_state_and_duration_before_publishing(self):
        details = {
            "valid": {"durationHrs": 0.25},
            "long": {"durationHrs": 1.5},
            "short": {"durationHrs": 0.249999},
            "unknown-duration": {"durationHrs": None},
            "other-state": {"subnational1Code": "IN-TN"},
            "old": {"obsDt": "2026-10-29"},
            "old-year": {"obsDt": "2025-10-30", "creationDt": "2025-10-30"},
            "late": {"creationDt": "2026-11-03"},
            "after-event": {"obsDt": "2026-11-03"},
            "last-day": {"obsDt": "2026-11-02", "creationDt": "2026-11-02"},
        }

        def response(path, params=None):
            if path == "/ref/taxonomy/ebird":
                return []
            if path.startswith("/product/lists/"):
                self.assertEqual(path, "/product/lists/IN-KA-MY/2026/10/30")
                return [{"subId": checklist_id} for checklist_id in details]
            checklist_id = path.rsplit("/", 1)[-1]
            return {
                "obsDt": "2026-10-30", "creationDt": "2026-10-30",
                "subnational1Code": "IN-KA", "durationHrs": 0.25,
                "obs": [{"comName": checklist_id}],
                **details[checklist_id],
            }

        self.api_get.side_effect = response
        fetch_ebird_data.main()
        saved = self.read_json("checklists.json")["checklists"]
        self.assertEqual({record["checklistId"] for record in saved}, {"valid", "long", "last-day"})
        summary = self.read_json("karnataka_summary.json")
        self.assertEqual(summary["checklists"], 3)
        self.assertEqual(summary["speciesList"], ["last-day", "long", "valid"])
        self.assertEqual(
            self.read_json("daily_summary.json"),
            [
                {"date": "2026-10-30", "checklists": 2, "species": 2},
                {"date": "2026-11-02", "checklists": 1, "species": 1},
            ],
        )
        self.assertEqual(self.read_json("district_summary.json")[0]["checklists"], 3)

    def test_legacy_effort_and_state_are_revalidated_even_if_absent_from_feed(self):
        for missing_field in ("durationHrs", "subnational1Code"):
            with self.subTest(missing_field=missing_field):
                self.run_refresh({"30": ["valid", "short", "other"]})
                archive = self.read_json("checklists.json")
                for checklist in archive["checklists"]:
                    del checklist[missing_field]
                (self.output / "checklists.json").write_text(
                    json.dumps(archive), encoding="utf-8"
                )

                def response(path, params=None):
                    if path == "/ref/taxonomy/ebird" or path.startswith("/product/lists/"):
                        return []
                    return {
                        "obsDt": "2026-10-30", "creationDt": "2026-10-30",
                        "durationHrs": 0.1 if path.endswith("/short") else 0.25,
                        "subnational1Code": "IN-TN" if path.endswith("/other") else "IN-KA",
                    }

                self.api_get.side_effect = response
                fetch_ebird_data.main()
                saved = self.read_json("checklists.json")["checklists"]
                self.assertEqual([record["checklistId"] for record in saved], ["valid"])
                self.assertEqual(saved[0]["durationHrs"], 0.25)
                self.assertEqual(saved[0]["subnational1Code"], "IN-KA")
                self.assertEqual(self.read_json("karnataka_summary.json")["checklists"], 1)

    def test_ineligible_archived_effort_or_state_is_removed_without_feed_entry(self):
        for field, value in (
            ("durationHrs", 0.1), ("durationHrs", None), ("subnational1Code", "IN-TN"),
        ):
            with self.subTest(field=field, value=value):
                self.run_refresh({"30": ["S1"]})
                archive = self.read_json("checklists.json")
                archive["checklists"][0][field] = value
                (self.output / "checklists.json").write_text(
                    json.dumps(archive), encoding="utf-8"
                )
                self.run_refresh({"30": []})
                self.assertEqual(self.read_json("checklists.json")["checklists"], [])
                self.assertEqual(self.read_json("karnataka_summary.json")["checklists"], 0)

    def test_invalid_fetched_effort_does_not_replace_published_files(self):
        self.run_refresh({"30": ["S1"]})
        original = {path: path.read_bytes() for path in self.output.glob("*.json")}
        self.api_get.side_effect = [
            [], [{"subId": "S2"}],
            {
                "obsDt": "2026-10-30", "creationDt": "2026-10-30",
                "subnational1Code": "IN-KA", "durationHrs": "bad",
            },
        ]
        with self.assertRaisesRegex(ValueError, "durationHrs"):
            fetch_ebird_data.main()
        self.assertEqual(original, {path: path.read_bytes() for path in original})

    def test_newly_shortened_checklist_is_removed_from_archive(self):
        self.run_refresh({"30": ["S1"]})
        self.api_get.side_effect = [
            [], [{"subId": "S1"}],
            {
                "obsDt": "2026-10-30", "creationDt": "2026-10-30",
                "subnational1Code": "IN-KA", "durationHrs": 14 / 60,
            },
        ]
        fetch_ebird_data.main()
        self.assertEqual(self.read_json("checklists.json")["checklists"], [])
        self.assertEqual(self.read_json("karnataka_summary.json")["checklists"], 0)


if __name__ == "__main__":
    unittest.main()
