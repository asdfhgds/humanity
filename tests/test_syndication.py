import pytest

from app.models.schemas import Source, SourceIndependence, SourceType
from app.verification.syndication import SyndicationDetector


def source(identifier, publisher, content, **values):
    return Source(id=identifier, publisher=publisher, title="Hospital strike reported", url=f"https://{identifier}.example/report", content=content, source_type=SourceType.NEWS, **values)


def test_identical_reports_are_not_counted_as_independent():
    first = source("one", "Outlet One", "Reuters reported that a hospital was struck in Gaza City.", content_hash="same")
    second = source("two", "Outlet Two", "Reuters reported that a hospital was struck in Gaza City.", content_hash="same")
    result = SyndicationDetector().compare(first, second)
    assert result.relationship == SourceIndependence.LIKELY_COPIED
    assert "duplicate_content_hash" in result.evidence


def test_distinct_reporting_stays_independent():
    first = source("one", "Local paper", "Witnesses described a strike near the hospital.")
    second = source("two", "Humanitarian group", "Aid workers said evacuation routes were blocked after shelling.")
    assert SyndicationDetector().compare(first, second).relationship == SourceIndependence.INDEPENDENT


def test_shared_origin_url_is_syndication_not_deletion():
    first = source("one", "Outlet One", "Brief report", original_source_url="https://wire.example/story/1")
    second = source("two", "Outlet Two", "Different rewrite", original_source_url="https://wire.example/story/1")
    result = SyndicationDetector().compare(first, second)
    assert result.relationship == SourceIndependence.LIKELY_SYNDICATED
    assert "shared_origin_url" in result.evidence


def test_associated_press_attribution_is_detected_as_syndication():
    first = source(
        "one",
        "Regional Outlet",
        "Officials confirmed the courthouse was damaged during overnight clashes.",
        author="AP",
    )
    second = source(
        "two",
        "Local Outlet",
        "Officials confirmed the courthouse was damaged during overnight clashes.",
    )

    result = SyndicationDetector().compare(first, second)

    assert result.relationship == SourceIndependence.LIKELY_SYNDICATED
    assert "shared_wire_attribution:ap" in result.evidence


@pytest.mark.parametrize(
    "text",
    ["A map shows the route.", "A happy crowd gathered.", "Capital city officials spoke."],
)
def test_ap_wire_detection_requires_a_standalone_word(text):
    report = source("one", "Local Outlet", text)

    assert SyndicationDetector._wire_service(report) is None


def test_near_identical_content_without_wire_attribution_is_copied_not_independent():
    article_text = (
        "Officials confirmed that the courthouse was damaged during overnight clashes "
        "and emergency crews treated residents nearby."
    )
    first = source("one", "Regional Outlet", article_text)
    second = source(
        "two",
        "Local Outlet",
        article_text.replace("confirmed", "said"),
    )

    result = SyndicationDetector().compare(first, second)

    assert result.relationship == SourceIndependence.LIKELY_SYNDICATED
    assert any(item.startswith("near_identical_text:") for item in result.evidence)
    assert not any(item.startswith("shared_wire_attribution:") for item in result.evidence)
