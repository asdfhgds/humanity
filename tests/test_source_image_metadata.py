from app.sources.fetcher import SourceFetcher


def test_extract_article_resolves_source_image_metadata():
    fetcher = SourceFetcher.__new__(SourceFetcher)

    result = fetcher._extract_article(
        """
        <html>
          <head><meta property="og:image" content="/images/report.jpg"></head>
          <body><article><p>Latest news reporting with substantial article text about a humanitarian event, with enough context and verified details for our source extraction pipeline.</p></article></body>
        </html>
        """,
        "https://news.example/story",
        "https://news.example/story",
    )

    assert result.success
    assert result.image_url == "https://news.example/images/report.jpg"


def test_extract_article_ignores_non_http_image_metadata():
    fetcher = SourceFetcher.__new__(SourceFetcher)

    result = fetcher._extract_article(
        """
        <html>
          <head><meta property="og:image" content="javascript:alert(1)"></head>
          <body><article><p>Latest news reporting with substantial article text about a humanitarian event, with enough context and verified details for our source extraction pipeline.</p></article></body>
        </html>
        """,
        "https://news.example/story",
        "https://news.example/story",
    )

    assert result.image_url is None
