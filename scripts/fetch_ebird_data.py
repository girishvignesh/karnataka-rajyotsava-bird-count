import json
import os
import time
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config" / "districts.json"
OUT = ROOT / "data"

API_BASE = "https://api.ebird.org/v2"
EVENT_START = date(2026, 10, 29)
EVENT_END = date(2026, 11, 2)
REQUEST_DELAY_SECONDS = 1


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

    with urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def event_dates():
    current = EVENT_START
    while current <= EVENT_END:
        yield current
        current += timedelta(days=1)


def fetch_district_checklists(district_code: str, district_name: str):
    checklists = []

    for event_day in event_dates():
        path = (
            f"/product/lists/{district_code}/"
            f"{event_day.year}/{event_day.month}/{event_day.day}"
        )

        try:
            checklist_feed = api_get(path)
        except Exception as error:
            print(f"Failed checklist feed for {district_name} on {event_day}: {error}")
            continue

        for entry in checklist_feed:
            checklist_id = entry.get("subId")
            if not checklist_id:
                continue

            try:
                checklist_details = api_get(
                    f"/product/checklist/view/{checklist_id}"
                )
            except Exception as error:
                print(f"Failed checklist details for {checklist_id}: {error}")
                continue

            observations = checklist_details.get("obs", [])
            species = sorted(
                {
                    observation["comName"]
                    for observation in observations
                    if observation.get("comName")
                }
            )

            checklists.append(
                {
                    "checklistId": checklist_id,
                    "date": event_day.isoformat(),
                    "district": district_name,
                    "observer": checklist_details.get("userDisplayName", "eBird user"),
                    "species": species,
                    "url": f"https://ebird.org/checklist/{checklist_id}",
                }
            )

            time.sleep(REQUEST_DELAY_SECONDS)

        time.sleep(REQUEST_DELAY_SECONDS)

    return checklists


def build_summaries(checklists):
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
        "lastUpdated": datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"),
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
    all_checklists = []

    for district in config["districts"]:
        print(f"Fetching {district['name']}…")
        all_checklists.extend(
            fetch_district_checklists(district["code"], district["name"])
        )

    build_summaries(all_checklists)
    print(f"Dashboard updated with {len(all_checklists)} checklists.")


if __name__ == "__main__":
    main()
