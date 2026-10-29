import json
import os
import time
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config" / "districts.json"
OUT = ROOT / "data"

API_BASE = "https://api.ebird.org/v2"
EVENT_START = date(2026, 10, 29)
EVENT_END = date(2026, 11, 2)
REQUEST_DELAY_SECONDS = 3
MAX_REQUEST_ATTEMPTS = 5
RETRY_BASE_SECONDS = 60
CHECKLIST_FEED_LIMIT = 200
IST = timezone(timedelta(hours=5, minutes=30))


def api_get(path: str, params: dict | None = None):
    api_key = os.environ.get("EBIRD_API_KEY")
    if not api_key:
        raise RuntimeError("Missing EBIRD_API_KEY environment variable.")

    url = f"{API_BASE}{path}"
    if params:
        url += f"?{urlencode(params)}"

    request = Request(
        url,
        headers={
            "X-eBirdApiToken": api_key,
            "Accept": "application/json",
        },
    )

    for attempt in range(MAX_REQUEST_ATTEMPTS):
        time.sleep(REQUEST_DELAY_SECONDS)
        try:
            with urlopen(request, timeout=30) as response:
                return json.loads(response.read().decode("utf-8"))
        except HTTPError as error:
            if error.code != 429 and not 500 <= error.code < 600:
                raise
            if attempt == MAX_REQUEST_ATTEMPTS - 1:
                raise
            delay = RETRY_BASE_SECONDS * 2**attempt
            retry_after = error.headers.get("Retry-After")
            if retry_after:
                try:
                    retry_seconds = float(retry_after)
                except ValueError:
                    retry_seconds = (
                        parsedate_to_datetime(retry_after)
                        - datetime.now(timezone.utc)
                    ).total_seconds()
                delay = max(delay, retry_seconds)
            print(
                f"eBird HTTP {error.code} for {path}; "
                f"retrying in {delay:g}s "
                f"(attempt {attempt + 2}/{MAX_REQUEST_ATTEMPTS}).",
                flush=True,
            )
        except (URLError, TimeoutError) as error:
            if attempt == MAX_REQUEST_ATTEMPTS - 1:
                raise
            delay = RETRY_BASE_SECONDS * 2**attempt
            print(
                f"eBird request failed for {path}: {error}; "
                f"retrying in {delay:g}s "
                f"(attempt {attempt + 2}/{MAX_REQUEST_ATTEMPTS}).",
                flush=True,
            )
        time.sleep(delay)


def event_dates(today: date | None = None):
    if today is None:
        today = datetime.now(IST).date()
    current = EVENT_START
    while current <= min(EVENT_END, today):
        yield current
        current += timedelta(days=1)


def fetch_taxonomy_names():
    taxonomy = api_get("/ref/taxonomy/ebird", {"fmt": "json", "locale": "en"})
    if not isinstance(taxonomy, list):
        raise ValueError("Unexpected eBird taxonomy response.")

    return {
        taxon["speciesCode"]: taxon["comName"]
        for taxon in taxonomy
        if taxon.get("speciesCode") and taxon.get("comName")
    }


def species_names(observations, taxonomy_names):
    return sorted(
        {
            observation.get("comName")
            or taxonomy_names.get(observation.get("speciesCode"))
            or observation.get("speciesCode")
            for observation in observations
            if observation.get("comName") or observation.get("speciesCode")
        }
    )


def fetch_district_checklists(
    district_code: str, district_name: str, taxonomy_names: dict,
    limited_feeds: set,
):
    checklists = []

    for event_day in event_dates():
        path = (
            f"/product/lists/{district_code}/"
            f"{event_day.year}/{event_day.month}/{event_day.day}"
        )

        checklist_feed = api_get(path, {"maxResults": CHECKLIST_FEED_LIMIT})
        if len(checklist_feed) >= CHECKLIST_FEED_LIMIT:
            limited_feeds.add((district_name, event_day.isoformat()))
            print(
                f"Checklist feed reached the {CHECKLIST_FEED_LIMIT}-result limit "
                f"for {district_name} on {event_day}; accumulated totals "
                "may be incomplete.",
                flush=True,
            )
        print(
            f"{district_name} on {event_day}: {len(checklist_feed)} checklists.",
            flush=True,
        )

        for entry in checklist_feed:
            checklist_id = entry["subId"]

            checklist_details = api_get(
                f"/product/checklist/view/{checklist_id}"
            )

            observations = checklist_details.get("obs", [])
            species = species_names(observations, taxonomy_names)

            checklists.append(
                {
                    "checklistId": checklist_id,
                    "date": event_day.isoformat(),
                    "district": district_name,
                    "species": species,
                }
            )

    return checklists


def load_archive():
    path = OUT / "checklists.json"
    if not path.exists():
        print("No checklist archive found; starting collection from this refresh.")
        return {}, set()

    archive = json.loads(path.read_text(encoding="utf-8"))
    if (
        archive["startDate"] != EVENT_START.isoformat()
        or archive["endDate"] != EVENT_END.isoformat()
    ):
        raise ValueError("Checklist archive belongs to a different event date range.")

    checklists = {}
    for checklist in archive["checklists"]:
        checklist_id = checklist["checklistId"]
        if (
            not isinstance(checklist_id, str)
            or not checklist_id
            or checklist_id in checklists
            or not isinstance(checklist["district"], str)
            or not checklist["district"]
            or not EVENT_START <= date.fromisoformat(checklist["date"]) <= EVENT_END
            or not isinstance(checklist["species"], list)
            or not all(isinstance(name, str) and name for name in checklist["species"])
        ):
            raise ValueError(f"Invalid or duplicate archived checklist: {checklist_id}")
        checklists[checklist_id] = checklist

    limited_feeds = set()
    for feed in archive["limitedFeeds"]:
        if (
            not isinstance(feed["district"], str)
            or not feed["district"]
            or not EVENT_START <= date.fromisoformat(feed["date"]) <= EVENT_END
        ):
            raise ValueError(f"Invalid archived limited feed: {feed}")
        limited_feeds.add((feed["district"], feed["date"]))
    return checklists, limited_feeds


def save_archive(checklists, limited_feeds):
    archive = {
        "startDate": EVENT_START.isoformat(),
        "endDate": EVENT_END.isoformat(),
        "checklists": sorted(
            checklists.values(), key=lambda record: (record["date"], record["checklistId"])
        ),
        "limitedFeeds": [
            {"district": district, "date": day}
            for district, day in sorted(limited_feeds)
        ],
    }
    OUT.mkdir(exist_ok=True)
    path = OUT / "checklists.json"
    temporary_path = path.with_suffix(".json.tmp")
    try:
        temporary_path.write_text(
            json.dumps(archive, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        temporary_path.replace(path)
    finally:
        temporary_path.unlink(missing_ok=True)


def build_summaries(checklists, limited_feeds=()):
    district_checklists = defaultdict(int)
    district_species = defaultdict(set)
    daily_checklists = defaultdict(int)
    daily_species = defaultdict(set)
    all_species = set()

    for checklist in checklists:
        district = checklist["district"]
        species = checklist["species"]
        day = checklist["date"]

        district_checklists[district] += 1
        district_species[district].update(species)

        daily_checklists[day] += 1
        daily_species[day].update(species)

        all_species.update(species)

    district_summary = [
        {
            "district": district,
            "checklists": district_checklists[district],
            "species": len(district_species[district]),
            "speciesList": sorted(district_species[district]),
        }
        for district in sorted(district_checklists)
    ]

    daily_summary = [
        {
            "date": day,
            "checklists": daily_checklists[day],
            "species": len(daily_species[day]),
        }
        for day in sorted(daily_checklists)
    ]

    karnataka_summary = {
        "state": "Karnataka",
        "event": "Karnataka Rajyotsava Bird Count",
        "startDate": EVENT_START.isoformat(),
        "endDate": EVENT_END.isoformat(),
        "checklists": len(checklists),
        "species": len(all_species),
        "districtsCovered": len(district_checklists),
        "speciesList": sorted(all_species),
        "lastUpdated": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "coverageWarning": (
            "Accumulated totals may be incomplete: the 200-checklist feed limit "
            "was reached for one or more district/dates. Refreshes can overlap "
            "and do not guarantee capturing every checklist."
            if limited_feeds else ""
        ),
    }

    OUT.mkdir(exist_ok=True)

    (OUT / "district_summary.json").write_text(
        json.dumps(district_summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    (OUT / "daily_summary.json").write_text(
        json.dumps(daily_summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    (OUT / "karnataka_summary.json").write_text(
        json.dumps(karnataka_summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def main():
    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    all_checklists, limited_feeds = load_archive()
    previous_count = len(all_checklists)
    taxonomy_names = fetch_taxonomy_names()

    for district in config["districts"]:
        print(f"Fetching {district['name']}…")
        for checklist in fetch_district_checklists(
            district["code"], district["name"], taxonomy_names, limited_feeds
        ):
            all_checklists[checklist["checklistId"]] = checklist

    save_archive(all_checklists, limited_feeds)
    build_summaries(list(all_checklists.values()), limited_feeds)
    print(
        f"Dashboard updated with {len(all_checklists)} accumulated checklists "
        f"({len(all_checklists) - previous_count} new)."
    )


if __name__ == "__main__":
    main()
