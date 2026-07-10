"""Parse WG-Gesucht alert emails and extract listing URLs."""
import logging
import re
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)


def parse_wg_gesucht_email(msg) -> list[dict]:
    """
    Extract listing stubs from WG-Gesucht alert email.
    WG-Gesucht emails are minimal: just a title and a URL to each listing.
    Returns list of {url, title, description, image_count, source}.
    """
    listings = []

    # Walk MIME tree to find HTML part
    html_content = None
    for part in msg.walk():
        if part.get_content_type() == 'text/html':
            try:
                html_content = part.get_payload(decode=True).decode('utf-8', errors='ignore')
                break
            except Exception as e:
                logger.debug(f"Error decoding HTML part: {e}")
                continue

    if not html_content:
        logger.warning("No HTML content found in WG-Gesucht email")
        return []

    # Parse HTML
    try:
        soup = BeautifulSoup(html_content, 'lxml')
    except Exception as e:
        logger.error(f"Failed to parse WG-Gesucht email HTML: {e}")
        return []

    # Find all links matching WG-Gesucht listing pattern
    # Pattern: https://www.wg-gesucht.de/12443563.html
    wgg_url_pattern = re.compile(r'https://www\.wg-gesucht\.de/(\d+)\.html')

    seen_urls = set()
    links = soup.find_all('a', href=True)

    for link in links:
        href = link.get('href', '')
        match = wgg_url_pattern.search(href)

        if not match:
            continue

        # Clean URL (strip campaign params)
        clean_url = wgg_url_pattern.search(href).group(0)

        if clean_url in seen_urls:
            continue
        seen_urls.add(clean_url)

        # Extract title from link text or nearby text
        title = link.get_text(strip=True)
        if not title:
            # Fallback: look for nearby text or use generic title
            parent = link.parent
            if parent:
                title = parent.get_text(strip=True)
                # Truncate to first sentence or reasonable length
                title = title.split('\n')[0][:100]
            if not title:
                title = "WG-Gesucht Listing"

        listing = {
            'url': clean_url,
            'title': title,
            'description': '',  # No description in WG-Gesucht email
            'image_count': 0,   # Not available from email; counted after page load
            'source': 'wggesucht',
        }

        listings.append(listing)
        logger.debug(f"Parsed WG-Gesucht listing: {clean_url} | {title}")

    if not listings:
        logger.info("No WG-Gesucht listings found in email")

    return listings
