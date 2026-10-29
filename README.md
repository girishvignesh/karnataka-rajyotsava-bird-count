# Karnataka Rajyotsava Bird Count Dashboard

Public dashboard for the Karnataka Rajyotsava Bird Count,
October 29–November 2, 2026.

## Features

- Total eBird checklists submitted across Karnataka
- Total unique species recorded
- Districts covered
- District-wise checklist and species totals
- Daily progress chart
- Karnataka district map
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
the maximum of 200 per district per day. If that limit is reached, it fails
explicitly rather than publishing potentially truncated totals.

## Setup

1. Add your eBird API key as a GitHub Actions secret:

   ```text
   EBIRD_API_KEY
   ```

2. Verify district codes in `config/districts.json`.
3. Enable GitHub Pages using **GitHub Actions** as the source.
4. Push to `main`.

## Data note

This portal displays aggregated checklist and species totals only.
It does not publish participant contact information or raw eBird data.

Data source: eBird, Cornell Lab of Ornithology.
Species names are resolved from eBird taxonomy using the species codes in
checklist observations.

## Tests

Run `python -m unittest discover -s tests -v` from the project root.