import pytest
import pytest_asyncio
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

from app.sources.adapter import ReliefWebAdapter, _normalize_article, _generate_source_record_id, _extract_publisher_domain, get_settings
from app.config import SourceConfig
from app.models.schemas import SourceType
from app.sources.fetcher import FetchResult


@pytest.fixture
def sample_source_config():
    """Create a sample SourceConfig for ReliefWeb."""
    return SourceConfig(
        name="ReliefWeb API",
        url="https://api.reliefweb.int/v2/reports",
        type="api",
        country="global",
        language="en",
        priority=10,
        poll_interval_hours=2,
        robots_policy="allow",
        enabled=True,
        reason="Test ReliefWeb API",
        roles=["discovery", "evidence", "structured_data"],
        connector_type="api",
        timeout_seconds=60,
        rate_limit_per_minute=10,
        rate_limit_per_hour=200,
        max_articles_per_fetch=100,
        fetch_full_content=True,
        respect_robots_txt=True,
    )


@pytest.fixture
def sample_fetch_result():
    """Create a sample FetchResult for testing."""
    from app.sources.fetcher import FetchResult
    return FetchResult(
        success=True,
        content="Test article content about humanitarian crisis in Ukraine.",
        title="Humanitarian Crisis in Ukraine",
        final_url="https://reliefweb.int/report/ukraine/humanitarian-crisis",
        published_at=datetime(2024, 1, 15, 10, 30),
        author="John Doe",
        paywall_detected=False,
        content_type="application/json",
    )


class TestReliefWebAdapter:
    """Tests for the ReliefWebAdapter class."""

    def test_adapter_initialization(self, sample_source_config):
        """Test that ReliefWebAdapter initializes correctly."""
        adapter = ReliefWebAdapter(sample_source_config)
        
        assert adapter.connector_type == "api"
        assert "discovery" in adapter.source_roles
        assert "evidence" in adapter.source_roles
        assert "structured_data" in adapter.source_roles
        assert adapter.connector_name == "reliefweb_ReliefWeb API"
        assert adapter._source_config == sample_source_config

    def test_supports_incremental_fetch(self, sample_source_config):
        """Test that the adapter supports incremental fetching."""
        adapter = ReliefWebAdapter(sample_source_config)
        assert adapter.supports_incremental_fetch() is True

    def test_get_ingestion_stats(self, sample_source_config):
        """Test that ingestion stats are returned correctly."""
        adapter = ReliefWebAdapter(sample_source_config)
        stats = adapter.get_ingestion_stats()
        
        assert stats["connector_name"] == "reliefweb_ReliefWeb API"
        assert stats["records_fetched"] == 0
        assert stats["records_normalized"] == 0
        assert stats["errors"] == []
        assert stats["warnings"] == []

    def test_generate_source_record_id(self, sample_source_config):
        """Test source record ID generation using standalone function."""
        record_id = _generate_source_record_id("ReliefWeb", "https://example.com/article")
        
        assert isinstance(record_id, str)
        assert len(record_id) == 16  # SHA256 truncated to 16 chars

    def test_extract_publisher_domain(self):
        """Test publisher domain extraction."""
        assert _extract_publisher_domain("https://reliefweb.int/report/123") == "reliefweb.int"
        assert _extract_publisher_domain("https://www.reliefweb.int/report") == "reliefweb.int"
        assert _extract_publisher_domain("http://api.reliefweb.int/v2/reports") == "api.reliefweb.int"
        assert _extract_publisher_domain("https://example.com/path") == "example.com"
        # Invalid URL returns empty string
        assert _extract_publisher_domain("invalid-url") == ""


class TestNormalizeArticle:
    """Tests for the _normalize_article function."""

    def test_normalize_article_with_all_fields(self):
        """Test normalizing a FetchResult with all fields."""
        from app.sources.fetcher import FetchResult
        from app.config import SourceConfig
        
        article = FetchResult(
            success=True,
            content="Test article content about humanitarian crisis.",
            title="Test Humanitarian Crisis",
            final_url="https://reliefweb.int/report/test-crisis",
            published_at=datetime(2024, 1, 15, 10, 30),
            author="Jane Smith",
            paywall_detected=False,
            content_type="application/json",
        )
        
        source_config = SourceConfig(
            name="Test Source",
            url="https://example.com/feed",
            type="api",
            country="global",
            language="en",
            priority=10,
            poll_interval_hours=1,
            robots_policy="allow",
            enabled=True,
            reason="Test",
        )
        
        result = _normalize_article(article, source_config)
        
        assert result["publisher"] == "Test Source"
        assert result["article_title"] == "Test Humanitarian Crisis"
        assert result["original_url"] == "https://reliefweb.int/report/test-crisis"
        assert result["canonical_url"] == "https://reliefweb.int/report/test-crisis"
        assert result["article_title"] == "Test Humanitarian Crisis"
        assert result["publication_time"] == "2024-01-15T10:30:00"
        assert result["available_text"] == "Test article content about humanitarian crisis."
        assert result["content_hash"] is not None
        assert result["connector_metadata"]["feed_name"] == "Test Source"
        assert result["author"] == "Jane Smith"  # From FetchResult

    def test_normalize_article_with_none_published_at(self):
        """Test normalizing an article with None published_at."""
        from app.sources.fetcher import FetchResult
        
        article = FetchResult(
            success=True,
            content="Test content",
            title="Test Article",
            final_url="https://example.com/article",
            published_at=None,
            author="Author",
            paywall_detected=False,
        )
        
        source_config = SourceConfig(
            name="Test Source",
            url="https://example.com/feed",
            type="api",
            country="US",
            language="en",
            priority=5,
            poll_interval_hours=1,
            robots_policy="allow",
            enabled=True,
            reason="Test",
        )
        
        result = _normalize_article(article, source_config)
        
        assert result["publication_time"] is None
        assert result["retrieval_time"] is not None


class TestReliefWebAdapterIntegration:
    """Integration tests for ReliefWebAdapter (mocked)."""

    @pytest.mark.asyncio
    async def test_fetch_new_records_no_appname(self, sample_source_config):
        """Test that adapter raises error when appname is not configured."""
        adapter = ReliefWebAdapter(sample_source_config)
        
        # Clear the lru_cache on the original function and mock get_settings in the config module
        import app.config
        app.config.get_settings.cache_clear()
        with patch('app.config.get_settings') as mock_settings:
            mock_settings.return_value = MagicMock(reliefweb_appname="")
            
            with pytest.raises(ValueError, match="RELIEFWEB_APPNAME not configured"):
                await adapter.fetch_new_records(max_records=5)

    @pytest.mark.asyncio
    async def test_health_check_no_appname(self, sample_source_config):
        """Test health check returns False when appname not configured."""
        adapter = ReliefWebAdapter(sample_source_config)
        
        import app.config
        app.config.get_settings.cache_clear()
        with patch('app.config.get_settings') as mock_settings:
            mock_settings.return_value = MagicMock(reliefweb_appname="")
            
            result = await adapter.health_check()
            assert result is False

    @pytest.mark.asyncio
    async def test_fetch_new_records_no_client_error(self, sample_source_config):
        """Test fetch_new_records handles API error gracefully."""
        adapter = ReliefWebAdapter(sample_source_config)
        
        import app.config
        app.config.get_settings.cache_clear()
        with patch('app.config.get_settings') as mock_settings:
            mock_settings.return_value = MagicMock(reliefweb_appname="test")
            with patch.object(adapter, '_get_client') as mock_get_client:
                # Make the client.get raise an exception
                mock_client = AsyncMock()
                mock_get_client.return_value = mock_client
                mock_client.get.side_effect = Exception("API error")
                
                records = await adapter.fetch_new_records(max_records=5)
                assert records == []  # Should return empty list on error
                assert len(adapter._stats["errors"]) > 0


class TestNormalizeArticleFunction:
    """Tests for the _normalize_article function."""

    def test_normalize_article_with_all_fields(self):
        """Test normalizing a FetchResult with all fields."""
        from app.sources.fetcher import FetchResult
        from app.config import SourceConfig
        
        article = FetchResult(
            success=True,
            content="Full article content here.",
            title="Test Article Title",
            final_url="https://example.com/article/123",
            published_at=datetime(2024, 1, 15, 14, 30),
            author="John Reporter",
            paywall_detected=True,
            content_type="text/html",
        )
        
        source_config = SourceConfig(
            name="Test Publisher",
            url="https://example.com/feed",
            type="api",
            country="US",
            language="en",
            priority=5,
            poll_interval_hours=1,
            robots_policy="allow",
            enabled=True,
            reason="Test source",
        )
        
        result = _normalize_article(article, source_config)
        
        assert result["publisher"] == "Test Publisher"
        assert result["article_title"] == "Test Article Title"
        assert result["available_text"] == "Full article content here."
        assert result["author"] == "John Reporter"
        assert result["paywall_detected"] is True
        assert result["content_type"] == "text/html"

    def test_normalize_article_with_none_values(self):
        """Test normalizing an article with None values."""
        from app.sources.fetcher import FetchResult
        from app.config import SourceConfig
        
        article = FetchResult(
            success=True,
            content="Content only",
            title="Minimal Article",
            final_url="https://example.com/minimal",
            published_at=None,
            author=None,
            paywall_detected=False,
            content_type=None,
        )
        
        source_config = SourceConfig(
            name="Minimal Source",
            url="https://minimal.example.com",
            type="api",
            country="global",
            language="en",
            priority=1,
            poll_interval_hours=6,
            robots_policy="allow",
            enabled=True,
            reason="Minimal test",
        )
        
        result = _normalize_article(article, source_config)
        
        assert result["publisher"] == "Minimal Source"
        assert result["article_title"] == "Minimal Article"
        assert result["available_text"] == "Content only"
        assert result["publication_time"] is None
        assert result["author"] is None
        assert result["paywall_detected"] is False
        assert result["content_type"] is None


class TestUtilityFunctions:
    """Tests for standalone utility functions."""

    def test_generate_source_record_id(self):
        """Test source record ID generation."""
        record_id = _generate_source_record_id("Test Publisher", "https://example.com/article")
        
        assert isinstance(record_id, str)
        assert len(record_id) == 16
        # Should be deterministic
        assert record_id == _generate_source_record_id("Test Publisher", "https://example.com/article")

    def test_extract_publisher_domain(self):
        """Test publisher domain extraction."""
        assert _extract_publisher_domain("https://reliefweb.int/report/123") == "reliefweb.int"
        assert _extract_publisher_domain("https://www.reliefweb.int/report") == "reliefweb.int"
        assert _extract_publisher_domain("http://api.reliefweb.int/v2/reports") == "api.reliefweb.int"
        assert _extract_publisher_domain("https://example.com/path") == "example.com"
        # Invalid URL returns empty string
        assert _extract_publisher_domain("invalid-url") == ""

    def test_canonicalize_url(self):
        """Test URL canonicalization."""
        from app.utils import canonicalize_url
        
        url = "https://example.com/path?utm_source=test&ref=fbclid#section"
        canonical = canonicalize_url(url)
        
        assert "utm_source" not in canonical
        assert "fbclid" not in canonical
        assert "example.com/path" in canonical


if __name__ == "__main__":
    pytest.main([__file__, "-v"])