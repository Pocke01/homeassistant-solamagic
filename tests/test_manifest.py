import json
from pathlib import Path

MANIFEST = Path(__file__).parents[1] / "custom_components" / "solamagic" / "manifest.json"


def test_matches_both_company_ids():
    ids = {m["manufacturer_id"] for m in json.loads(MANIFEST.read_text())["bluetooth"]}
    assert ids == {89, 2161}
    assert all(m["connectable"] for m in json.loads(MANIFEST.read_text())["bluetooth"])
