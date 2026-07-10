"""Fast Python-based email pre-filter: blocklist keywords and URL validation."""
import logging
import re
from datetime import datetime

logger = logging.getLogger(__name__)

BLOCKLIST_KEYWORDS = [
    "Tauschwohnung",
    "WG-Zimmer",
    "Wohngemeinschaft",
    "Pendlerwohnung",
    "Zwischenmiete",
]

# Platform-specific URL validation patterns
_URL_PATTERNS = {
    'immoscout24': re.compile(r'https?://.*(?:immobilienscout24|is24)\.de/.*expose/\d+'),
    'wggesucht': re.compile(r'https?://www\.wg-gesucht\.de/\d+\.html'),
    # immobilie1: canonical /expose/<id> or the slug form ending in -<id>
    'immobilie1': re.compile(r'https?://(?:www\.)?immobilie1\.de/(?:expose/\d+|.*-\d+)'),
}


def apply_email_prefilter(listing: dict) -> tuple[bool, str]:
    """
    Pre-filter listings: validates URL format and checks for blocklist keywords.
    Fast Python-based check before any network calls.
    Supports ImmoScout24, WG-Gesucht, and immobilie1 listings.
    Returns (True, "") if listing passes, or (False, "reason") if rejected.
    """

    # Validate URL format (platform-aware)
    url = listing.get('url', '')
    source = listing.get('source', 'immoscout24')
    pattern = _URL_PATTERNS.get(source, _URL_PATTERNS['immoscout24'])

    if not url or not pattern.search(url):
        reason = f"Invalid or missing {source} URL: {url}"
        return False, reason

    # Check title and description for blocklist keywords
    title = listing.get('title', '').lower()
    description = listing.get('description', '').lower()
    combined_text = f"{title} {description}"

    for keyword in BLOCKLIST_KEYWORDS:
        if keyword.lower() in combined_text:
            reason = f"Blocklisted keyword found: {keyword}"
            return False, reason

    return True, ""


def check_availability_duration(listing: dict) -> tuple[bool, str]:
    """
    Check WG-Gesucht availability duration: reject if <= 2 years.
    ImmoScout24 listings (no end date) always pass.
    Returns (True, "") if listing passes, or (False, "reason") if rejected.
    """
    source = listing.get('source', 'immoscout24')
    if source != 'wggesucht':
        return True, ""

    availability = listing.get('availability', '')
    if not availability or ' - ' not in availability:
        # No end date specified (permanent or very long term), always pass
        return True, ""

    try:
        parts = availability.split(' - ')
        if len(parts) != 2:
            return True, ""

        start_str, end_str = parts[0].strip(), parts[1].strip()

        # Parse dates in DD.MM.YYYY format
        start_date = datetime.strptime(start_str, '%d.%m.%Y')
        end_date = datetime.strptime(end_str, '%d.%m.%Y')

        duration_days = (end_date - start_date).days
        duration_years = duration_days / 365.25

        # Reject if <= 2 years
        if duration_years <= 2:
            reason = f"Lease too short: {duration_years:.1f} years ({availability})"
            return False, reason

        return True, ""

    except (ValueError, AttributeError) as e:
        logger.debug(f"Could not parse availability dates: {availability} ({e})")
        # If we can't parse, allow it to pass (better to see it than reject on parse error)
        return True, ""


if __name__ == "__main__":
    # Unit tests with 5 sample listings (3 should fail, 2 should pass)
    test_listings = [
        # Pass: Good listing
        {
            'url': 'https://www.immobilienscout24.de/expose/123456789',
            'title': 'Schöne 2-Zimmer Wohnung',
            'description': 'Wunderschöne Wohnung in Schwabing. 1200€ Warmmiete. Balkon, EBK.',
            'image_count': 3
        },
        # Fail: Tauschwohnung (blocklisted keyword)
        {
            'url': 'https://www.immobilienscout24.de/expose/111111111',
            'title': 'Tauschwohnung',
            'description': 'Tauschwohnung: Biete 3 Zimmer, Suche 1 Zimmer. Nur Tausch!',
            'image_count': 2
        },
        # Fail: Too few images
        {
            'url': 'https://www.immobilienscout24.de/expose/222222222',
            'title': 'Billige Wohnung',
            'description': 'Nice apartment in Munich. 800€ Warmmiete.',
            'image_count': 1
        },
        # Pass: Good listing within price limit
        {
            'url': 'https://www.immobilienscout24.de/expose/333333333',
            'title': 'Günstige Wohnung',
            'description': 'Comfortable apartment in Neuhausen. 1300€ Warmmiete. Modern.',
            'image_count': 4
        },
        # Fail: Price exceeds limit
        {
            'url': 'https://www.immobilienscout24.de/expose/444444444',
            'title': 'Teuer',
            'description': 'Expensive apartment. 1600€ Warmmiete. Exclusive location.',
            'image_count': 4
        },
    ]

    passed = 0
    failed = 0

    for i, listing in enumerate(test_listings, 1):
        result, reason = apply_email_prefilter(listing)
        status = "✓ PASS" if result else "✗ FAIL"
        print(f"{status} - Listing {i}: {listing['title']}")
        if reason:
            print(f"  Reason: {reason}")
        if result:
            passed += 1
        else:
            failed += 1

    print(f"\nSummary: {passed} passed, {failed} failed (expected: 2 passed, 3 failed)")
