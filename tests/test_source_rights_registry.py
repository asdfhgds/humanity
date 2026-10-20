from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def test_source_rights_registry_covers_configured_sources_and_matches_metadata():
    source_config = yaml.safe_load((ROOT / "config" / "sources.yaml").read_text(encoding="utf-8"))
    rights_config = yaml.safe_load(
        (ROOT / "config" / "source_rights.yaml").read_text(encoding="utf-8")
    )

    configured_sources = {
        (group, source["name"]): source
        for group, sources in source_config.items()
        for source in sources
    }
    rights_sources = rights_config["sources"]

    assert set(rights_sources) == {name for _, name in configured_sources}
    assert "source URL" in rights_config["policy"]["provenance"]
    required_fields = {
        "terms_url",
        "access_method",
        "attribution_requirements",
        "storage_retention_constraints",
        "display_rights",
        "commercial_use",
        "redistribution",
        "review_date",
        "evidence_reference",
    }

    for (group, name), source in configured_sources.items():
        rights = rights_sources[name]
        assert rights["group"] == group
        assert rights["type"] == source["type"]
        effective = {**rights_config["policy"], **rights}
        assert required_fields <= effective.keys()
        if effective["rights_review"] == "pending":
            assert effective["commercial_use"] == "not_cleared"
            assert effective["redistribution"] == "not_cleared"
            assert effective["review_date"] is None
            assert effective["evidence_reference"] is None


def test_structured_dataset_permissions_are_narrow_and_attributed():
    rights_config = yaml.safe_load(
        (ROOT / "config" / "source_rights.yaml").read_text(encoding="utf-8")
    )
    rights_sources = rights_config["sources"]

    gdelt = rights_sources["GDELT Events"]
    assert gdelt["rights_review"] == "reviewed"
    assert gdelt["commercial_use"] == "allowed"
    assert gdelt["redistribution"] == "allowed_with_attribution"
    assert "linked publisher article text" in gdelt["display_rights"]
    assert "GDELT Project" in gdelt["attribution_requirements"]

    ucdp = rights_sources["UCDP"]
    assert ucdp["rights_review"] == "reviewed"
    assert ucdp["commercial_use"] == "allowed_with_attribution"
    assert ucdp["redistribution"] == "allowed_with_attribution"
    assert "token" in ucdp["access_method"]
    assert "no HUMANITY adapter" in ucdp["access_method"]

    assert rights_sources["Reuters"]["rights_review"] == "pending"
    assert rights_config["policy"]["commercial_use"] == "not_cleared"
