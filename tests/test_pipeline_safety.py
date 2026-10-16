from app.extraction.gemini import ExtractionResult

def test_extraction_results_are_real_by_default():
    assert not ExtractionResult(is_event=False).is_mock

def test_mock_result_is_explicit():
    assert ExtractionResult(is_event=False, is_mock=True).is_mock
