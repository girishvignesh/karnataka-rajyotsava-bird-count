# Karnataka Rajyotsava Bird Count Dashboard

Public dashboard for the Karnataka Rajyotsava Bird Count,
October 30–November 2, 2026.

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