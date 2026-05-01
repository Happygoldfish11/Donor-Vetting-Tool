import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import pandas as pd

from vetting_core import Person, RebnyMember, is_republican_recipient, lookup_rebny_from_members, people_from_dataframe
from tools.download_rebny_members import extract_from_json, looks_like_person_name


class CoreTests(unittest.TestCase):
    def test_people_from_dataframe(self):
        df = pd.DataFrame({"First Name": ["Jane"], "Last Name": ["Doe"], "Zip Code": ["10001-0000"]})
        people, _ = people_from_dataframe(df)
        self.assertEqual(len(people), 1)
        self.assertEqual(people[0].zip_code, "10001")

    def test_rebny_exact(self):
        result = lookup_rebny_from_members(Person("Jane", "Doe"), [RebnyMember("Jane A. Doe")])
        self.assertTrue(result.found)
        self.assertEqual(result.result, "FOUND")

    def test_rebny_review(self):
        result = lookup_rebny_from_members(Person("Jane", "Doe"), [RebnyMember("J. Doe")])
        self.assertTrue(result.review)
        self.assertEqual(result.result, "REVIEW")

    def test_rebny_clean(self):
        result = lookup_rebny_from_members(Person("Jane", "Doe"), [RebnyMember("Alice Smith")])
        self.assertFalse(result.found)
        self.assertEqual(result.result, "Clean")

    def test_republican_detection(self):
        self.assertTrue(is_republican_recipient("Republican National Committee"))
        self.assertFalse(is_republican_recipient("Community Housing PAC", "DEM"))

    def test_downloader_parser(self):
        self.assertTrue(looks_like_person_name("Jane Doe"))
        members = extract_from_json({"items": [{"name": "Jane Doe", "company": "Example Realty"}]})
        self.assertEqual(members[0].name, "Jane Doe")


if __name__ == "__main__":
    unittest.main(verbosity=2)
