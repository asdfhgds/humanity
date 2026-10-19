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
    assert "persisted" in rights_config["policy"]["storage"]
    assert "short attributed excerpts" in rights_config["policy"]["public_display"]
    assert rights_config["policy"]["commercial_reuse"] == "not_cleared"
    assert rights_config["policy"]["redistribution"] == "not_cleared"

    for (group, name), source in configured_sources.items():
        rights = rights_sources[name]
        assert rights["group"] == group
        assert rights["type"] == source["type"]
        assert rights["rights_review"] == "pending"
