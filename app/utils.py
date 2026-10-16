from __future__ import annotations

import hashlib
import re
import unicodedata
from datetime import datetime
from typing import Optional
from urllib.parse import urlparse, parse_qsl, urlencode, urlunparse


def canonicalize_url(url: str) -> str:
    """Normalize URL for deduplication."""
    url_str = str(url)
    try:
        parsed = urlparse(url_str.strip())
        scheme = parsed.scheme.lower()
        netloc = parsed.netloc.lower()
        netloc = re.sub(r"^www\.", "", netloc)
        path = parsed.path.rstrip("/")
        if not path:
            path = "/"
        query_params = parse_qsl(parsed.query, keep_blank_values=True)
        tracking_params = {
            "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
            "fbclid", "gclid", "ref", "source", "campaign_id", "utm_id",
            "mc_cid", "mc_eid", "_ga", "_gl", "amp"
        }
        filtered_params = [(k, v) for k, v in query_params if k not in tracking_params]
        filtered_params.sort()
        query = urlencode(filtered_params)
        return urlunparse((scheme, netloc, path, "", query, ""))

    except Exception:
        return url_str.strip()


def compute_hash(text: str) -> str:
    """Compute SHA256 hash of text."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def compute_article_hash(title: str, url: str) -> str:
    """Compute hash for article deduplication."""
    canonical = canonicalize_url(url)
    combined = f"{title.strip().lower()}|{canonical}"
    return compute_hash(combined)[:16]


def slugify(text: str, max_length: int = 80) -> str:
    """Convert text to URL-friendly slug."""
    text = unicodedata.normalize("NFKD", text)
    text = text.encode("ascii", "ignore").decode("ascii")
    text = re.sub(r"[^\w\s-]", "", text).strip().lower()
    text = re.sub(r"[-\s]+", "-", text)
    return text[:max_length].strip("-")


def extract_domain(url: str) -> str:
    """Extract domain from URL."""
    try:
        return urlparse(url).netloc.lower().replace("www.", "")
    except Exception:
        return ""


def clean_text(text: str) -> str:
    """Clean and normalize text."""
    text = unicodedata.normalize("NFKC", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def truncate(text: str, max_length: int, suffix: str = "…") -> str:
    """Truncate text to max length."""
    if len(text) <= max_length:
        return text
    return text[:max_length - len(suffix)].rstrip() + suffix


def parse_date_flexible(date_str: str) -> Optional[datetime]:
    """Parse date from various formats."""
    formats = [
        "%Y-%m-%dT%H:%M:%S.%fZ",
        "%Y-%m-%dT%H:%M:%SZ",
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d",
        "%d %b %Y %H:%M:%S",
        "%d %B %Y",
        "%b %d, %Y",
        "%B %d, %Y",
    ]
    for fmt in formats:
        try:
            return datetime.strptime(date_str.strip(), fmt)
        except ValueError:
            continue
    return None


def estimate_reading_time(text: str, words_per_minute: int = 200) -> int:
    """Estimate reading time in minutes."""
    word_count = len(text.split())
    return max(1, round(word_count / words_per_minute))


def format_number(num: Optional[int]) -> str:
    """Format number with commas."""
    if num is None:
        return "—"
    if num >= 1_000_000:
        return f"{num / 1_000_000:.1f}M"
    if num >= 1_000:
        return f"{num / 1_000:.1f}K"
    return f"{num:,}"


def format_range(min_val: Optional[int], max_val: Optional[int]) -> str:
    """Format min-max range."""
    if min_val is not None and max_val is not None:
        if min_val == max_val:
            return str(min_val)
        return f"{min_val}–{max_val}"
    elif min_val is not None:
        return f"at least {min_val}"
    elif max_val is not None:
        return f"up to {max_val}"
    return "—"


def time_ago(dt: datetime) -> str:
    """Human-readable time difference."""
    now = datetime.utcnow()
    diff = now - dt.replace(tzinfo=None)
    seconds = diff.total_seconds()

    if seconds < 60:
        return "just now"
    if seconds < 3600:
        minutes = int(seconds / 60)
        return f"{minutes}m ago"
    if seconds < 86400:
        hours = int(seconds / 3600)
        return f"{hours}h ago"
    if seconds < 604800:
        days = int(seconds / 86400)
        return f"{days}d ago"
    return dt.strftime("%b %d, %Y")


def is_likely_paywall(text: str) -> bool:
    """Simple paywall detection based on common indicators."""
    text_lower = text.lower()
    paywall_indicators = [
        "subscribe to read",
        "subscription required",
        "premium content",
        "paywall",
        "member-only",
        "members only",
        "sign up to continue",
        "create an account to read",
        "log in to read",
        "login to read",
        "register to read",
        "unlock this article",
        "continue reading with",
        "subscribe now",
        "get unlimited access",
    ]
    return any(indicator in text_lower for indicator in paywall_indicators)
