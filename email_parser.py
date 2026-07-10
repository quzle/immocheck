"""Parse alert emails from multiple platforms (ImmoScout24, WG-Gesucht, etc)."""
import logging
from bs4 import BeautifulSoup
import re
from email.message import Message
from pathlib import Path
from datetime import datetime

logger = logging.getLogger(__name__)

# Create debug folder for email HTML files
DEBUG_FOLDER = Path("outputs/debug_emails")
DEBUG_FOLDER.mkdir(parents=True, exist_ok=True)


def _normalize_is24_url(url: str) -> str:
    """Convert IS24 email redirect URLs to direct listing URLs."""
    if not url:
        return url
    # Convert push.search.is24.de/email/expose/ID to www.immobilienscout24.de/expose/ID
    match = re.search(r'/expose/(\d+)', url)
    if match:
        expose_id = match.group(1)
        return f"https://www.immobilienscout24.de/expose/{expose_id}"
    return url


def save_debug_html(html_content: str, email_subject: str = None) -> str:
    """Save email HTML to debug folder and return the file path."""
    try:
        # Create filename from timestamp and subject
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        subject_safe = (email_subject or "email").replace("/", "_").replace(" ", "_")[:30]
        filename = f"{timestamp}_{subject_safe}.html"
        filepath = DEBUG_FOLDER / filename

        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(html_content)

        logger.info(f"Saved email HTML to {filepath}")
        return str(filepath)
    except Exception as e:
        logger.error(f"Failed to save debug HTML: {e}")
        return None


def parse_alert_email(msg: Message, source: str = 'immoscout24') -> list[dict]:
    """
    Dispatcher: routes email parsing to the appropriate platform-specific parser.
    Args:
        msg: The parsed email message
        source: Platform source ('immoscout24' or 'wggesucht')
    Returns: list of listing dicts with 'source' field added
    """
    if source == 'wggesucht':
        from wg_gesucht_parser import parse_wg_gesucht_email
        return parse_wg_gesucht_email(msg)
    elif source == 'immobilie1':
        return parse_immobilie1_email(msg)
    else:
        return parse_immoscout_email(msg)


def parse_immoscout_email(msg: Message) -> list[dict]:
    """
    Parses the HTML body of an ImmoScout24 alert email to extract listings.
    Returns a list of dictionaries with listing data, tagged with source='immoscout24'.
    """
    listings = []

    # Get email subject for debug logging
    email_subject = msg.get('Subject', 'Unknown Subject')
    logger.info(f"Parsing email: {email_subject}")

    # Extract HTML body
    html_content = ""
    if msg.is_multipart():
        for part in msg.walk():
            content_type = part.get_content_type()
            content_disposition = str(part.get("Content-Disposition"))
            if content_type == "text/html" and "attachment" not in content_disposition:
                html_content = part.get_payload(decode=True).decode(part.get_content_charset() or 'utf-8')
                break
    else:
        html_content = msg.get_payload(decode=True).decode(msg.get_content_charset() or 'utf-8')

    if not html_content:
        logger.warning(f"No HTML content found in email: {email_subject}")
        return []

    # Save debug HTML
    save_debug_html(html_content, email_subject)

    soup = BeautifulSoup(html_content, 'lxml')
    
    # ImmoScout24 alert emails usually contain listings in tables or specific div blocks.
    # We look for links that contain '/expose/' which is the typical URL pattern for listings.
    
    # Strategy: Find all <a> tags with expose links, then find their parent container.
    # Email links use push.search.is24.de/email/expose/, direct links use immobilienscout24.de/expose/
    expose_links = soup.find_all('a', href=re.compile(r'(immobilienscout24|is24).*?/expose/\d+', re.IGNORECASE))
    
    processed_urls = set()
    
    for link in expose_links:
        url = link.get('href')
        if not url or url in processed_urls:
            continue
        
        # Clean URL (remove tracking params if any)
        url = url.split('?')[0]
        if url in processed_urls:
            continue
            
        processed_urls.add(url)
        
        # Try to find the container block for this listing
        # Typically, a listing is wrapped in a table cell or a div.
        # We'll traverse up to find a suitable container.
        container = link.find_parent(['td', 'div', 'table'])
        if not container:
            continue
            
        # Extract title (headline)
        # Often the link text itself or a bold tag nearby
        title = link.get_text(strip=True)
        if not title:
            # Look for headers nearby
            header = container.find(['h1', 'h2', 'h3', 'h4', 'b', 'strong'])
            title = header.get_text(strip=True) if header else "Unknown Title"
            
        # Extract description
        # Get all text from the container, excluding the title if possible
        description = container.get_text(" ", strip=True)
        
        # Count images in this container
        image_count = len(container.find_all('img'))

        # Normalize URL (convert email redirects to direct URLs)
        normalized_url = _normalize_is24_url(url)

        listings.append({
            'url': normalized_url,
            'title': title,
            'description': description,
            'image_count': image_count,
            'source': 'immoscout24',
        })

    logger.info(f"Extracted {len(listings)} ImmoScout24 listings from email.")
    return listings

def _get_email_html(msg: Message) -> str:
    """Return the HTML body of an email message ('' if none)."""
    if msg.is_multipart():
        for part in msg.walk():
            content_type = part.get_content_type()
            content_disposition = str(part.get("Content-Disposition"))
            if content_type == "text/html" and "attachment" not in content_disposition:
                return part.get_payload(decode=True).decode(part.get_content_charset() or 'utf-8')
        return ""
    return msg.get_payload(decode=True).decode(msg.get_content_charset() or 'utf-8')


# Link texts in immobilie1 alert emails that are NOT listing titles.
_I1_NON_LISTING_LINK_TEXTS = {'ansehen', 'abmelden'}


def _resolve_immobilie1_url(tracking_url: str) -> str:
    """Follow an immobilie1 alert's click-tracking redirect to the real listing URL.

    immobilie1 alerts are sent via Brevo/Sendinblue, so every link is an opaque
    tracking URL (e.g. sendibt3.com/tr/cl/...) that 30x-redirects to
    https://www.immobilie1.de/expose/<id>?utm=... . Returns the final immobilie1
    URL (query stripped), or '' if it doesn't resolve to an immobilie1 listing.
    """
    import requests
    try:
        resp = requests.get(
            tracking_url, allow_redirects=True, timeout=20,
            headers={"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                                   "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"},
        )
        final = resp.url.split('?')[0]
        if 'immobilie1.de' in final and resp.status_code == 200:
            return final
        logger.warning(f"immobilie1 link did not resolve to a listing (status {resp.status_code}): {final}")
    except Exception as e:
        logger.warning(f"Could not resolve immobilie1 tracking link: {e}")
    return ''


def parse_immobilie1_email(msg: Message) -> list[dict]:
    """
    Parse an immobilie1.de alert email into listing dicts (source='immobilie1').

    immobilie1 alerts come through Brevo, so listing links are opaque tracking
    URLs that must be followed to recover the real https://www.immobilie1.de/...
    URL. We resolve only the title links (skipping the "Ansehen"/"abmelden"
    actions — importantly never following the unsubscribe link). The email HTML
    is saved to outputs/debug_emails/ for troubleshooting.
    """
    email_subject = msg.get('Subject', 'Unknown Subject')
    logger.info(f"Parsing immobilie1 email: {email_subject}")

    html_content = _get_email_html(msg)
    if not html_content:
        logger.warning(f"No HTML content found in immobilie1 email: {email_subject}")
        return []

    save_debug_html(html_content, email_subject)
    soup = BeautifulSoup(html_content, 'lxml')

    listings = []
    seen_urls = set()
    for link in soup.find_all('a', href=True):
        title = ' '.join(link.get_text(' ', strip=True).split())
        # Listing links carry the listing title. Skip empty (image) links, the
        # per-card "Ansehen" button, the footer link, and the unsubscribe link.
        if not title or title.lower() in _I1_NON_LISTING_LINK_TEXTS \
                or title.startswith('Weitere neue Angebote'):
            continue

        url = _resolve_immobilie1_url(link['href'])
        if not url or url in seen_urls:
            continue
        seen_urls.add(url)

        container = link.find_parent(['td', 'div', 'table'])
        description = container.get_text(" ", strip=True) if container else ""
        image_count = len(container.find_all('img')) if container else 0

        listings.append({
            'url': url,
            'title': title,
            'description': description,
            'image_count': image_count,
            'source': 'immobilie1',
        })

    if not listings:
        logger.warning(
            "immobilie1 parser found no listings. Inspect the saved HTML in "
            "outputs/debug_emails/ — link layout or tracking domain may have changed."
        )
    else:
        logger.info(f"Extracted {len(listings)} immobilie1 listings from email.")
    return listings


if __name__ == "__main__":
    # Test with a dummy HTML file if it exists
    import os
    sample_path = 'tests/sample_alert.html'
    if os.path.exists(sample_path):
        from email.message import EmailMessage
        msg = EmailMessage()
        with open(sample_path, 'r') as f:
            msg.set_content(f.read(), subtype='html')
        
        results = parse_alert_email(msg)
        for i, l in enumerate(results, 1):
            print(f"Listing {i}:")
            print(f"  URL: {l['url']}")
            print(f"  Title: {l['title']}")
            print(f"  Images: {l['image_count']}")
            print(f"  Desc: {l['description'][:100]}...")
    else:
        print(f"No sample file found at {sample_path}")
