from __future__ import annotations

import asyncio
import hashlib
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional, Dict, List, Any
from urllib.parse import urlparse, urljoin
from collections import defaultdict

import httpx
from bs4 import BeautifulSoup
import feedparser

from app.utils import canonicalize_url, clean_text, is_likely_paywall
from app.config import SourceConfig, get_settings
from app.models.schemas import Source, SourceType
from app.datastore import get_source_store, get_data_store


@dataclass
class FetchResult:
    success: bool
    content: Optional[str] = None
    title: Optional[str] = None
    published_at: Optional[datetime] = None
    author: Optional[str] = None
    error: Optional[str] = None
    status_code: Optional[int] = None
    content_type: Optional[str] = None
    final_url: Optional[str] = None
    paywall_detected: bool = False


@dataclass
class RateLimitConfig:
    requests_per_minute: int = 30
    requests_per_hour: int = 500
    burst_limit: int = 5


class RateLimiter:
    def __init__(self, default_config: RateLimitConfig = None):
        self.default_config = default_config or RateLimitConfig()
        self.host_configs: Dict[str, RateLimitConfig] = {}
        self.request_history: Dict[str, List[float]] = defaultdict(list)
        self._lock = asyncio.Lock()

    def set_host_config(self, host: str, config: RateLimitConfig):
        self.host_configs[host] = config

    def get_config(self, host: str) -> RateLimitConfig:
        return self.host_configs.get(host, self.default_config)

    async def acquire(self, host: str) -> None:
        async with self._lock:
            config = self.get_config(host)
            now = time.time()
            history = self.request_history[host]
            
            # Clean old entries
            minute_ago = now - 60
            hour_ago = now - 3600
            history[:] = [t for t in history if t > hour_ago]
            
            # Check limits
            recent_minute = sum(1 for t in history if t > minute_ago)
            recent_hour = len(history)
            
            if recent_minute >= config.requests_per_minute:
                wait_time = 60 - (now - history[-(config.requests_per_minute)])
                if wait_time > 0:
                    await asyncio.sleep(wait_time)
            
            if recent_hour >= config.requests_per_hour:
                wait_time = 3600 - (now - history[-(config.requests_per_hour)])
                if wait_time > 0:
                    await asyncio.sleep(wait_time)
            
            # Check burst
            if len(history) >= config.burst_limit:
                recent_burst = history[-config.burst_limit:]
                if now - recent_burst[0] < 1.0:
                    await asyncio.sleep(1.0 - (now - recent_burst[0]))
            
            history.append(now)


class RobotsTxtCache:
    def __init__(self, ttl_hours: int = 24):
        self.cache: Dict[str, tuple] = {}  # host -> (rules, expiry)
        self.ttl = timedelta(hours=ttl_hours)
        self._lock = asyncio.Lock()

    async def can_fetch(self, client: httpx.AsyncClient, url: str, user_agent: str = "HUMANITY-Bot/1.0") -> bool:
        parsed = urlparse(url)
        host = parsed.netloc.lower()
        
        async with self._lock:
            if host in self.cache:
                rules, expiry = self.cache[host]
                if datetime.utcnow() < expiry:
                    return self._check_rules(rules, url, user_agent)
            
            # Fetch robots.txt
            robots_url = f"{parsed.scheme}://{host}/robots.txt"
            try:
                response = await client.get(robots_url, timeout=10.0)
                if response.status_code == 200:
                    rules = self._parse_robots(response.text)
                else:
                    rules = []
            except Exception:
                rules = []
            
            self.cache[host] = (rules, datetime.utcnow() + self.ttl)
            return self._check_rules(rules, url, user_agent)

    def _parse_robots(self, content: str) -> List[tuple]:
        rules = []
        current_agent = None
        for line in content.split('\n'):
            line = line.strip()
            if line.startswith('User-agent:'):
                current_agent = line.split(':', 1)[1].strip()
            elif line.startswith('Disallow:') and current_agent in ('*', 'HUMANITY-Bot'):
                path = line.split(':', 1)[1].strip()
                if path:
                    rules.append(('disallow', path))
        return rules

    def _check_rules(self, rules: List[tuple], url: str, user_agent: str) -> bool:
        parsed = urlparse(url)
        path = parsed.path
        if not path:
            path = '/'
        for rule_type, pattern in rules:
            if rule_type == 'disallow':
                if self._match_path(pattern, path):
                    return False
        return True

    def _match_path(self, pattern: str, path: str) -> bool:
        # Simple glob matching
        if pattern == '/':
            return True
        if pattern.endswith('*'):
            return path.startswith(pattern[:-1])
        return path == pattern or path.startswith(pattern + '/')


class RequestCache:
    def __init__(self, max_size: int = 1000, ttl_hours: int = 1):
        self.cache: Dict[str, tuple] = {}  # key -> (response, expiry)
        self.max_size = max_size
        self.ttl = timedelta(hours=ttl_hours)
        self._lock = asyncio.Lock()

    def _make_key(self, method: str, url: str) -> str:
        return f"{method}:{canonicalize_url(url)}"

    async def get(self, method: str, url: str) -> Optional[httpx.Response]:
        key = self._make_key(method, url)
        async with self._lock:
            if key in self.cache:
                response, expiry = self.cache[key]
                if datetime.utcnow() < expiry:
                    return response
                else:
                    del self.cache[key]
        return None

    async def set(self, method: str, url: str, response: httpx.Response):
        key = self._make_key(method, url)
        async with self._lock:
            if len(self.cache) >= self.max_size:
                # Remove oldest
                oldest_key = min(self.cache.keys(), key=lambda k: self.cache[k][1])
                del self.cache[oldest_key]
            self.cache[key] = (response, datetime.utcnow() + self.ttl)


class SourceFetcher:
    def __init__(self):
        self.settings = get_settings()
        self.client = httpx.AsyncClient(
            timeout=httpx.Timeout(30.0, connect=10.0),
            follow_redirects=True,
            headers={
                "User-Agent": "HUMANITY-Bot/1.0 (+https://github.com/humanity/events)"
            }
        )
        self.rate_limiter = RateLimiter()
        self.robots_cache = RobotsTxtCache()
        self.request_cache = RequestCache()
        self.source_store = get_source_store()
        
        # Set default host configs
        self.rate_limiter.set_host_config("reuters.com", RateLimitConfig(requests_per_minute=20, requests_per_hour=200))
        self.rate_limiter.set_host_config("apnews.com", RateLimitConfig(requests_per_minute=20, requests_per_hour=200))
        self.rate_limiter.set_host_config("bbc.com", RateLimitConfig(requests_per_minute=15, requests_per_hour=150))
        self.rate_limiter.set_host_config("aljazeera.com", RateLimitConfig(requests_per_minute=15, requests_per_hour=150))

    async def close(self):
        await self.client.aclose()

    async def fetch(self, source_config: SourceConfig) -> List[FetchResult]:
        """Fetch articles from a source based on its type."""
        if source_config.type == "rss" or source_config.url.endswith(('.xml', '.rss', '.atom')):
            return await self._fetch_rss(source_config)
        elif source_config.type == "news":
            return await self._fetch_news_site(source_config)
        else:
            return await self._fetch_generic(source_config)

    async def _fetch_rss(self, source_config: SourceConfig) -> List[FetchResult]:
        """Fetch and parse RSS/Atom feed."""
        results = []
        
        # Check robots.txt
        if not await self.robots_cache.can_fetch(self.client, str(source_config.url)):
            return [FetchResult(success=False, error="Blocked by robots.txt", status_code=403)]
        
        # Check cache
        cached = await self.request_cache.get("GET", str(source_config.url))
        if cached:
            feed_data = cached.text
        else:
            await self.rate_limiter.acquire(urlparse(str(source_config.url)).netloc)
            response = await self.client.get(str(source_config.url))
            if response.status_code != 200:
                return [FetchResult(success=False, error=f"HTTP {response.status_code}", status_code=response.status_code)]
            feed_data = response.text
            await self.request_cache.set("GET", str(source_config.url), response)
        
        feed = feedparser.parse(feed_data)
        
        for entry in feed.entries[:50]:  # Limit per feed
            article_url = entry.get('link', '')
            if not article_url:
                continue
            
            # Try to get full article content
            article_result = await self.fetch_article(article_url)
            if article_result.success:
                results.append(article_result)
            else:
                # Fallback to feed entry data
                results.append(FetchResult(
                    success=True,
                    content=entry.get('summary', '') or entry.get('description', ''),
                    title=entry.get('title', ''),
                    published_at=self._parse_date(entry.get('published', '') or entry.get('updated', '')),
                    author=entry.get('author', ''),
                    final_url=article_url,
                    paywall_detected=False
                ))
        
        return results

    async def _fetch_news_site(self, source_config: SourceConfig) -> List[FetchResult]:
        """Fetch article links from news site homepage/section."""
        results = []
        
        if not await self.robots_cache.can_fetch(self.client, str(source_config.url)):
            return [FetchResult(success=False, error="Blocked by robots.txt", status_code=403)]
        
        await self.rate_limiter.acquire(urlparse(str(source_config.url)).netloc)
        response = await self.client.get(str(source_config.url))
        if response.status_code != 200:
            return [FetchResult(success=False, error=f"HTTP {response.status_code}", status_code=response.status_code)]
        
        soup = BeautifulSoup(response.text, 'html.parser')
        
        # Extract article links - generic approach
        links = set()
        for a in soup.find_all('a', href=True):
            href = a['href']
            full_url = urljoin(str(source_config.url), href)
            if self._is_article_url(full_url, str(source_config.url)):
                links.add(full_url)
        
        # Fetch each article (limit)
        for url in list(links)[:20]:
            result = await self.fetch_article(url)
            if result.success:
                results.append(result)
        
        return results

    async def _fetch_generic(self, source_config: SourceConfig) -> List[FetchResult]:
        """Generic single URL fetch."""
        result = await self.fetch_article(str(source_config.url))
        return [result] if result.success else [FetchResult(success=False, error=result.error)]

    def _is_article_url(self, url: str, base_url: str) -> bool:
        """Heuristic to identify article URLs."""
        parsed = urlparse(url)
        base_parsed = urlparse(base_url)
        
        if parsed.netloc != base_parsed.netloc:
            return False
        
        path = parsed.path.lower()
        
        # Skip non-article paths
        skip_patterns = ['/tag/', '/category/', '/author/', '/search/', '/page/', 
                        '/login', '/register', '/subscribe', '/newsletter',
                        '.css', '.js', '.png', '.jpg', '.jpeg', '.gif', '.pdf']
        if any(p in path for p in skip_patterns):
            return False
        
        # Look for article-like patterns
        article_patterns = ['/202', '/2024/', '/2025/', '/2026/', '/news/', '/world/', 
                           '/politics/', '/article/', '/story/', '/post/']
        return any(p in path for p in article_patterns) or len(path.split('/')) >= 3

    def _parse_date(self, date_str: str) -> Optional[datetime]:
        if not date_str:
            return None
        for fmt in [
            "%a, %d %b %Y %H:%M:%S %z",
            "%a, %d %b %Y %H:%M:%S %Z",
            "%Y-%m-%dT%H:%M:%S%z",
            "%Y-%m-%dT%H:%M:%SZ",
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%d",
        ]:
            try:
                return datetime.strptime(date_str.strip(), fmt)
            except ValueError:
                continue
        return None

    async def fetch_article(self, url: str) -> FetchResult:
        """Fetch and extract content from a single article URL."""
        # Check robots.txt
        if not await self.robots_cache.can_fetch(self.client, url):
            return FetchResult(success=False, error="Blocked by robots.txt", status_code=403)
        
        # Check cache
        cached = await self.request_cache.get("GET", url)
        if cached:
            html = cached.text
            final_url = str(cached.url)
        else:
            # Rate limit
            host = urlparse(url).netloc
            await self.rate_limiter.acquire(host)
            
            try:
                response = await self.client.get(url)
                if response.status_code != 200:
                    return FetchResult(success=False, error=f"HTTP {response.status_code}", status_code=response.status_code)
                html = response.text
                final_url = str(response.url)
                await self.request_cache.set("GET", url, response)
            except Exception as e:
                return FetchResult(success=False, error=str(e), status_code=0)
        
        # Parse article
        return self._extract_article(html, url, final_url)

    def _extract_article(self, html: str, original_url: str, final_url: str) -> FetchResult:
        """Extract article content from HTML."""
        soup = BeautifulSoup(html, 'html.parser')
        
        # Remove unwanted elements
        for elem in soup(['script', 'style', 'nav', 'header', 'footer', 'aside', 'iframe', 'noscript']):
            elem.decompose()
        
        # Paywall detection
        paywall_detected = is_likely_paywall(soup.get_text())
        
        # Title extraction
        title = None
        for selector in ['h1', 'meta[property="og:title"]', 'meta[name="twitter:title"]', 'title']:
            elem = soup.select_one(selector)
            if elem:
                if elem.name == 'meta':
                    title = elem.get('content', '').strip()
                else:
                    title = elem.get_text().strip()
                if title:
                    break
        
        # Author extraction
        author = None
        for selector in ['meta[name="author"]', 'meta[property="article:author"]', '.author', '.byline', '[rel="author"]']:
            elem = soup.select_one(selector)
            if elem:
                if elem.name == 'meta':
                    author = elem.get('content', '').strip()
                else:
                    author = elem.get_text().strip()
                if author:
                    break
        
        # Date extraction
        published_at = None
        for selector in ['meta[property="article:published_time"]', 'meta[name="publish_date"]', 'time[datetime]', '.published', '.date']:
            elem = soup.select_one(selector)
            if elem:
                date_str = elem.get('datetime') or elem.get('content') or elem.get_text()
                published_at = self._parse_date(date_str.strip())
                if published_at:
                    break
        
        # Content extraction - try multiple strategies
        content = None
        
        # Strategy 1: Article tag
        article_elem = soup.select_one('article')
        if article_elem:
            content = article_elem.get_text()
        
        # Strategy 2: Common content selectors
        if not content or len(content) < 200:
            for selector in [
                '[itemprop="articleBody"]',
                '.article-body',
                '.article-content',
                '.entry-content',
                '.post-content',
                '.content',
                '#content',
                'main',
                '.main-content',
            ]:
                elem = soup.select_one(selector)
                if elem:
                    text = elem.get_text()
                    if len(text) > len(content or ''):
                        content = text
        
        # Strategy 3: All paragraph tags
        if not content or len(content) < 200:
            paragraphs = soup.find_all('p')
            content = '\n\n'.join(p.get_text() for p in paragraphs if len(p.get_text()) > 50)
        
        if content:
            content = clean_text(content)
            # Truncate if too long
            if len(content) > 50000:
                content = content[:50000] + "..."
        
        return FetchResult(
            success=bool(content and len(content) > 100),
            content=content,
            title=title,
            published_at=published_at,
            author=author,
            final_url=final_url,
            paywall_detected=paywall_detected,
            status_code=200
        )


async def fetch_from_source(source_config: SourceConfig) -> List[FetchResult]:
    """Convenience function to fetch from a single source."""
    fetcher = SourceFetcher()
    try:
        return await fetcher.fetch(source_config)
    finally:
        await fetcher.close()


async def fetch_from_multiple_sources(source_configs: List[SourceConfig], max_concurrent: int = 5) -> Dict[str, List[FetchResult]]:
    """Fetch from multiple sources concurrently with limit."""
    semaphore = asyncio.Semaphore(max_concurrent)
    
    async def fetch_one(config: SourceConfig):
        async with semaphore:
            fetcher = SourceFetcher()
            try:
                return config.name, await fetcher.fetch(config)
            finally:
                await fetcher.close()
    
    results = await asyncio.gather(*[fetch_one(c) for c in source_configs])
    return dict(results)