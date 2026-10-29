# Karnataka Rajyotsava Bird Count 2026 Dashboard

Public dashboard for the Karnataka Rajyotsava Bird Count,
October 29–November 2, 2026.

## Features

- Total eBird checklists submitted across Karnataka
- Total unique species recorded
- Districts covered
- District-wise checklist and species totals
- Daily progress chart
- Searchable species list

## Automatic updates

GitHub Actions refreshes data from the eBird API every 12 hours:

- 08:30 IST
- 20:30 IST

It then rebuilds the dashboard and publishes it using GitHub Pages.

Every refresh queries all elapsed event dates, starting October 29, rather than
only the hours since the previous refresh. Dates are observation dates, not
submission dates. Future dates are skipped using India Standard Time.

API requests are paced and transient errors (including HTTP 429 rate limits) are
retried with exponential backoff and respect for `Retry-After`. If a request
still fails, the workflow fails without replacing the published summaries with
partial data. Per-district, per-date checklist counts are logged during refresh.

The historical checklist feed defaults to only 10 results. The fetcher requests
the maximum of 200 per district per day per refresh. If that limit is reached,
the dashboard publishes accumulated totals with a visible incomplete-coverage
warning. That warning persists because later refreshes cannot prove that
previously missed checklists have been recovered.

## Accumulated checklist archive

Each successful refresh saves `data/checklists.json`, keyed logically by eBird
checklist ID. New IDs are added, existing IDs are updated from freshly fetched
details, and previously saved checklists absent from the latest feed are kept.
State, district and daily summaries are rebuilt from the entire archive, so a
new day does not reset earlier days. All elapsed event dates are fetched again
to discover late submissions.

The existing workflow commits the archive together with the summaries, allowing
the next GitHub Actions run to resume collection. Keep this file when deploying
or updating the code. A malformed archive or an archive for a different event
date range causes the refresh to fail rather than discard saved data.

Two non-overlapping 200-result feeds can accumulate 400 unique checklists for
one district/day. Overlapping feeds add fewer new IDs; collection is not
guaranteed exhaustive. Checklists removed from eBird cannot be inferred from
their absence in a limited feed, so archived records require manual
reconciliation for deletions.

On the first run, the archive starts with retrievable checklists. Existing
aggregate summaries cannot be converted back into individual checklist records.

## Setup

1. Add your eBird API key as a GitHub Actions secret:

   ```text
   EBIRD_API_KEY
   ```

2. Verify district codes in `config/districts.json`.
3. Enable GitHub Pages using **GitHub Actions** as the source.
4. Push to `main`.

## Data note

The dashboard displays aggregated checklist and species totals. The public
archive contains only checklist IDs, observation dates, district names and
species names; it does not store observer names, contacts, coordinates or raw
API responses.

Data source: eBird, Cornell Lab of Ornithology.
Species names are resolved from eBird taxonomy using the species codes in
checklist observations.

## Tests

Run `python -m unittest discover -s tests -v` from the project root.