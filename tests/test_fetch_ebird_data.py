import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

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


if __name__ == "__main__":
    unittest.main()
