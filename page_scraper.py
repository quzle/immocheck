"""Scrape listing details from ImmoScout24 pages with Playwright."""
import logging
import re
from bs4 import BeautifulSoup
from playwright.async_api import Page

logger = logging.getLogger(__name__)


def extract_listing_from_soup(soup: BeautifulSoup, url: str) -> dict:
    """
    Build the listing dict from page HTML (everything except the image count,
    which needs a live page). Shared by extract_listing_details and the
    `--test-email` helper so both exercise the same extraction logic.
    """
    return {
        'url': url,
        'warmmiete': extract_warmmiete(soup),
        'location': extract_location(soup),
        'rooms': extract_rooms(soup),
        'size_sqm': extract_size(soup),
        'property_type': extract_property_type(soup),
        'description': extract_description(soup),
        'landlord_name': extract_landlord_name(soup),
        'availability': extract_availability(soup),
        'outdoor_space': extract_outdoor_space(soup),
    }


async def extract_listing_details(page: Page, url: str) -> dict:
    """
    Extract full listing details from ImmoScout24 page.
    Returns dict with: price, location, rooms, property_type, description, image_count
    """
    try:
        content = await page.content()
        soup = BeautifulSoup(content, 'lxml')

        listing_details = extract_listing_from_soup(soup, url)
        # Count real images (needs the live page, not just the HTML)
        listing_details['image_count'] = await count_images_on_page(page)

        logger.info(
            f"Extracted listing: {listing_details['rooms']}Z, {listing_details['size_sqm']}m², "
            f"€{listing_details['warmmiete']} Warm, {listing_details['location']}, "
            f"Available: {listing_details['availability']}, Contact: {listing_details['landlord_name']}"
        )
        return listing_details

    except Exception as e:
        logger.error(f"Error extracting listing details: {e}")
        return {}


def extract_warmmiete(soup: BeautifulSoup) -> int:
    """Extract warm rent price from page."""
    try:
        # Try to find price using ImmoScout24 specific selectors first
        price_selectors = [
            ('div.is24qa-warmmiete-main span.is24-preis-value', 'Warmmiete via data-qa'),
            ('div[data-qa="warmmiete"] span.is24-preis-value', 'Warmmiete via data-qa variant'),
            ('div.is24qa-kaltmiete-main span.is24-preis-value', 'Kaltmiete (fallback)'),
        ]

        for selector, description in price_selectors:
            try:
                # Use CSS selector to find price element
                elements = soup.select(selector)
                if elements:
                    for element in elements:
                        price_text = element.get_text(strip=True)
                        match = re.search(r'(\d+(?:\.\d+)?)\s*€', price_text)
                        if match:
                            price_str = match.group(1).replace('.', '').replace(',', '')
                            price = int(float(price_str))
                            logger.debug(f"Extracted price ({description}): €{price}")
                            return price
            except Exception:
                continue

        # Fallback: Look for Warmmiete in text patterns
        text = soup.get_text()
        match = re.search(r'Warmmiete[:\s]*€?\s*(\d+(?:\.\d+)?)', text, re.IGNORECASE)
        if match:
            price_str = match.group(1).replace('.', '').replace(',', '')
            return int(float(price_str))

        # Last resort: look for any price pattern
        match = re.search(r'(\d+(?:\.\d+)?)\s*€', text)
        if match:
            price_str = match.group(1).replace('.', '').replace(',', '')
            return int(float(price_str))

        return 0
    except Exception as e:
        logger.warning(f"Could not extract Warmmiete: {e}")
        return 0


def extract_location(soup: BeautifulSoup) -> str:
    """Extract location/address from page."""
    try:
        # IS24 expose pages expose the address via a data-qa attribute (the old
        # `address-block` class no longer exists). Try the stable selectors first.
        address_el = soup.select_one('[data-qa="is24-expose-address"]') or soup.select_one('.address')
        if address_el:
            address_text = ' '.join(address_el.get_text(separator=' ', strip=True).split())
            # Strip IS24's "exact address hidden" boilerplate (both wordings).
            address_text = re.sub(
                r'\s*Die vollständige Adresse der Immobilie '
                r'(?:erhalten Sie vom Anbieter|wird erst nach Veröffentlichung des Inserats angezeigt)\.?\s*$',
                '', address_text,
            ).strip()
            if address_text:
                return address_text

        # Fallback: Look for location patterns in full text
        text = soup.get_text()

        # Try to find in common location structures
        location_patterns = [
            r'(?:in|In)\s+([A-Z][a-zA-Z\-]+(?:\s+[A-Z][a-zA-Z\-]+)*)',
            r'([A-Z][a-zA-Z\-]+)\s*(?:München|Munich|Berlin)',
        ]

        for pattern in location_patterns:
            match = re.search(pattern, text)
            if match:
                location = match.group(1).strip()
                if location and len(location) > 2:
                    return location

        # Last resort: look for München
        if 'München' in text or 'Munich' in text:
            return 'München'

        return 'Unknown'
    except Exception as e:
        logger.warning(f"Could not extract location: {e}")
        return 'Unknown'


def extract_rooms(soup: BeautifulSoup) -> int:
    """Extract number of rooms from page."""
    try:
        text = soup.get_text()

        # Look for room patterns: "2-Zimmer", "2 Zimmer", "2Z"
        match = re.search(r'(\d+)\s*(?:\-)?Zimmer', text, re.IGNORECASE)
        if match:
            return int(match.group(1))

        return 0
    except Exception as e:
        logger.warning(f"Could not extract room count: {e}")
        return 0


def extract_size(soup: BeautifulSoup) -> int:
    """Extract property size in square meters from page."""
    try:
        # Try to find size using ImmoScout24 specific selector first
        size_div = soup.find('div', {'class': lambda x: x and 'is24qa-flaeche-main' in x})
        if size_div:
            text = size_div.get_text(strip=True)
            match = re.search(r'(\d+(?:\.\d+)?)\s*m²', text, re.IGNORECASE)
            if match:
                size_str = match.group(1).replace('.', '').replace(',', '')
                return int(float(size_str))

        # Fallback: Look for size pattern in full text
        text = soup.get_text()
        match = re.search(r'(?:Fläche|Größe|Size)[:\s]*(\d+(?:\.\d+)?)\s*m²', text, re.IGNORECASE)
        if match:
            size_str = match.group(1).replace('.', '').replace(',', '')
            return int(float(size_str))

        # Last resort: Look for any m² pattern
        match = re.search(r'(\d+(?:\.\d+)?)\s*m²', text, re.IGNORECASE)
        if match:
            size_str = match.group(1).replace('.', '').replace(',', '')
            return int(float(size_str))

        return 0
    except Exception as e:
        logger.warning(f"Could not extract property size: {e}")
        return 0


def extract_property_type(soup: BeautifulSoup) -> str:
    """Extract property type (apartment, house, etc)."""
    try:
        text = soup.get_text().lower()

        if 'wohnung' in text or 'apartment' in text:
            return 'Wohnung'
        elif 'haus' in text or 'house' in text:
            return 'Haus'
        elif 'studio' in text:
            return 'Studio'

        return 'Wohnung'  # Default
    except Exception as e:
        logger.warning(f"Could not extract property type: {e}")
        return 'Unknown'


def extract_landlord_name(soup: BeautifulSoup) -> str:
    """Extract landlord/contact person name from listing page."""
    try:
        # First try to find the contact name using data-qa attribute (most reliable)
        contact_div = soup.find('div', {'data-qa': 'contactName'})
        if contact_div:
            name = contact_div.get_text(strip=True)
            if name and len(name) > 2:
                # Remove title prefixes like "Herr" or "Frau" if present
                name = re.sub(r'^(Herr|Frau|Dr\.?|Prof\.?)\s+', '', name, flags=re.IGNORECASE).strip()
                return name

        # Fallback: Look for common patterns like "Kontaktperson: Name" or "Anbieter: Name"
        text = soup.get_text()
        patterns = [
            r'Kontaktperson[:\s]+([A-Z][a-zA-Z\s\-]+)',
            r'Anbieter[:\s]+([A-Z][a-zA-Z\s\-]+)',
            r'Vermieter[:\s]+([A-Z][a-zA-Z\s\-]+)',
            r'Contact[:\s]+([A-Z][a-zA-Z\s\-]+)',
            r'Name[:\s]+([A-Z][a-zA-Z\s\-]+)',
        ]

        for pattern in patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                name = match.group(1).strip()
                if name and len(name) > 2:
                    return name

        return ''
    except Exception as e:
        logger.warning(f"Could not extract landlord name: {e}")
        return ''


def extract_availability(soup: BeautifulSoup) -> str:
    """Extract move-in date / availability from ImmoScout24 page."""
    try:
        # Use ImmoScout24 specific selector for "Bezugsfrei ab" (available from)
        avail_dd = soup.find('dd', {'class': 'is24qa-bezugsfrei-ab'})
        if avail_dd:
            text = avail_dd.get_text(strip=True)
            if text:
                return text

        # Fallback: Look for "ab" (from) pattern with dates in page text
        full_text = soup.get_text()

        # Pattern 1: "ab 01.06.2026" or "ab Sofort"
        match = re.search(r'\bab\s+(Sofort|[\d\.]+)', full_text, re.IGNORECASE)
        if match:
            return match.group(1).strip()

        # Pattern 2: "Bezugsfrei ab ..."
        match = re.search(r'Bezugsfrei\s+ab\s+(Sofort|[\d\.]+)', full_text, re.IGNORECASE)
        if match:
            return match.group(1).strip()

        return ''
    except Exception as e:
        logger.warning(f"Could not extract availability: {e}")
        return ''


def extract_description(soup: BeautifulSoup) -> str:
    """Extract full description from page."""
    try:
        # Remove script and style elements
        for script in soup(['script', 'style']):
            script.decompose()

        # Get text and clean up
        text = soup.get_text(separator=' ', strip=True)

        # Keep first 2000 characters of meaningful text
        return text[:2000]
    except Exception as e:
        logger.warning(f"Could not extract description: {e}")
        return ""


async def count_images_on_page(page: Page) -> int:
    """
    Count the number of images on the current page.
    Excludes tiny tracking/icon images (< 50px in any dimension).
    """
    try:
        image_count = await page.evaluate("""() => {
            const images = document.querySelectorAll('img');
            let count = 0;
            for (let img of images) {
                const rect = img.getBoundingClientRect();
                // Only count images that are at least 50px in width and height
                if (rect.width >= 50 && rect.height >= 50) {
                    // Also check if image is actually loaded/visible
                    if (img.complete && img.naturalWidth > 0) {
                        count++;
                    }
                }
            }
            return count;
        }""")
        logger.info(f"Found {image_count} properly sized images on page")
        return image_count
    except Exception as e:
        logger.warning(f"Error counting images: {e}, defaulting to 0")
        return 0


def extract_outdoor_space(soup: BeautifulSoup) -> str:
    """Return comma-separated outdoor features found in page text (Balkon, Terrasse, Garten, Loggia)."""
    text = soup.get_text()
    found = [kw for kw in ['Balkon', 'Terrasse', 'Garten', 'Loggia']
             if re.search(rf'\b{kw}\b', text, re.IGNORECASE)]
    return ', '.join(found)
