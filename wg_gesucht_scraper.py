"""Scrape WG-Gesucht listing pages with Playwright, handling ad overlays and modal dismissals."""
import logging
import asyncio
import re
from bs4 import BeautifulSoup
from playwright.async_api import Page

logger = logging.getLogger(__name__)


async def _dismiss_wgg_overlays(page: Page) -> None:
    """
    Dismiss WG-Gesucht's ad/sponsor overlays and cookie consent banners.
    If modal persists, we proceed anyway (some pages may have no images/content to load).
    """
    try:
        close_selectors = [
            'button.close-ad',
            '[data-close]',
            'button[aria-label*="close" i]',
            'button[aria-label*="schlie" i]',
            '.wgg-modal-close',
            'button:has-text("Schließen")',
            'button:has-text("Überspringen")',
            'button:has-text("Skip")',
            '.modal-close',
            '.ad-close',
        ]

        # Try each selector with short timeout
        for selector in close_selectors:
            try:
                button = page.locator(selector).first
                await button.wait_for(state='visible', timeout=500)
                logger.debug(f"Found close button: {selector}")
                await button.click()
                await asyncio.sleep(0.5)
                return
            except Exception:
                pass

        # Strategy 2: Escape key
        logger.debug("Trying Escape key to dismiss modal")
        await page.keyboard.press('Escape')
        await asyncio.sleep(0.2)

        # Strategy 3: Try clicking in center of page (might dismiss overlay)
        logger.debug("Trying click to dismiss modal")
        try:
            await page.click('body', position={'x': 400, 'y': 400})
            await asyncio.sleep(0.2)
        except Exception:
            pass

        # Strategy 4: JS removal of modal elements
        logger.debug("Removing modal elements via JavaScript")
        await page.evaluate("""
            const selectors = ['.modal', '.overlay', '[class*="ad-"], [class*="sponsor"]', '[id*="modal"]'];
            document.querySelectorAll(selectors.join(', ')).forEach(el => {
                try { el.remove(); } catch(e) {}
            });
        """)

        logger.debug("Ad dismissal complete (modal may persist on no-image listings)")

    except Exception as e:
        logger.warning(f"Error dismissing overlays: {e}")


async def extract_wg_gesucht_listing(page: Page, url: str) -> dict:
    """
    Extract full listing details from WG-Gesucht page.
    Handles ad modal dismissal. Works even if modal persists (some pages have no images).
    Returns dict with: price (warmmiete), location, rooms, size_sqm, property_type,
    description, landlord_name, image_count.
    """
    try:
        logger.debug("WG-Gesucht: Dismissing ad overlays...")
        await _dismiss_wgg_overlays(page)
        logger.debug("WG-Gesucht: Ad overlay handling complete")

        # Wait briefly for content to be visible (short timeout for no-image pages)
        logger.debug("WG-Gesucht: Waiting for page content...")
        try:
            # Try waiting for DOM content (faster than networkidle)
            await page.wait_for_load_state("domcontentloaded", timeout=5000)
        except Exception:
            logger.debug("DOM load timeout")

        # Additional brief wait for any overlays to settle
        await asyncio.sleep(0.5)

        logger.debug("WG-Gesucht: Getting page content...")
        content = await page.content()
        soup = BeautifulSoup(content, 'lxml')

        logger.debug("WG-Gesucht: Extracting price...")
        warmmiete = extract_wgg_price(soup)

        logger.debug("WG-Gesucht: Extracting location...")
        location = extract_wgg_location(soup)

        logger.debug("WG-Gesucht: Extracting rooms...")
        rooms = extract_wgg_rooms(soup)

        logger.debug("WG-Gesucht: Extracting size...")
        size_sqm = extract_wgg_size(soup)

        property_type = 'Wohnung'

        logger.debug("WG-Gesucht: Extracting description...")
        description = extract_wgg_description(soup)

        logger.debug("WG-Gesucht: Extracting landlord...")
        landlord_name = extract_wgg_landlord(soup)

        logger.debug("WG-Gesucht: Extracting availability...")
        availability = extract_wgg_availability(soup)

        logger.debug("WG-Gesucht: Counting images...")
        from page_scraper import count_images_on_page
        image_count = await count_images_on_page(page)

        logger.debug("WG-Gesucht: Extracting outdoor space...")
        outdoor_space = extract_outdoor_space(soup)

        listing_details = {
            'url': url,
            'warmmiete': warmmiete,
            'location': location,
            'rooms': rooms,
            'size_sqm': size_sqm,
            'property_type': property_type,
            'description': description,
            'landlord_name': landlord_name,
            'availability': availability,
            'image_count': image_count,
            'outdoor_space': outdoor_space,
            'source': 'wggesucht',
        }

        logger.info(f"Extracted WG-Gesucht listing: {rooms}Z, {size_sqm}m², €{warmmiete} Warm, {location}, Available: {availability}")
        return listing_details

    except Exception as e:
        logger.error(f"Error extracting WG-Gesucht listing details: {e}")
        import traceback
        logger.debug(f"Traceback: {traceback.format_exc()}")
        return {}


def extract_wgg_price(soup: BeautifulSoup) -> int:
    """Extract warm rent price from WG-Gesucht page."""
    try:
        # Strategy 1: Look for key_fact_value with "Gesamtmiete" label
        key_facts = soup.find_all('div', {'class': 'col-xs-4'})
        for fact_div in key_facts:
            detail = fact_div.find(['div', 'span'], {'class': 'key_fact_detail'})
            if detail and 'Gesamtmiete' in detail.get_text():
                value = fact_div.find('b', {'class': 'key_fact_value'})
                if value:
                    price_str = value.get_text(strip=True).replace('€', '').strip()
                    try:
                        return int(price_str)
                    except ValueError:
                        continue

        # Strategy 2: More specific regex for price (larger numbers followed by €)
        text = soup.get_text()
        match = re.search(r'(\d{3,5})\s*€', text)
        if match:
            return int(match.group(1))

        # Fallback
        logger.debug("Could not extract WG-Gesucht price using known patterns")
        return 0

    except Exception as e:
        logger.warning(f"Error extracting WG-Gesucht price: {e}")
        return 0


def extract_wgg_location(soup: BeautifulSoup) -> str:
    """Extract location/address from WG-Gesucht page."""
    try:
        # Strategy 1: Look for address in section_panel with "Adresse" title
        for panel in soup.find_all(class_='section_panel'):
            title = panel.find(class_='section_panel_title')
            if title and 'Adresse' in title.get_text():
                # Address is in a col-xs-12 div with class "col-xs-12"
                addr_divs = panel.find_all('div', {'class': re.compile(r'col-xs-12')})
                for addr_div in addr_divs:
                    text = addr_div.get_text(strip=True)
                    # Remove the "Adresse" prefix and other metadata
                    text = re.sub(r'^Adresse', '', text).strip()
                    # Clean up extra whitespace
                    text = ' '.join(text.split())
                    if text and len(text) > 5:
                        return text[:100]

        # Strategy 2: Look for common address keywords in page text
        text = soup.get_text()
        if any(city in text for city in ['München', 'Berlin', 'Hamburg', 'Köln']):
            # Try to extract from title or metadata
            title = soup.find('title')
            if title:
                title_text = title.get_text()
                # Extract location from title like "...Wohnung in München-Au-Haidhausen"
                match = re.search(r'(?:in|in\s+)([^-]+(?:-[^-]+)*)', title_text)
                if match:
                    return match.group(1).strip()[:100]

        # Fallback
        logger.debug("Could not extract WG-Gesucht location")
        return 'Unknown'

    except Exception as e:
        logger.warning(f"Error extracting WG-Gesucht location: {e}")
        return 'Unknown'


def extract_wgg_rooms(soup: BeautifulSoup) -> int:
    """Extract number of rooms from WG-Gesucht page."""
    try:
        # Strategy 1: Look for key_fact_value with "Zimmer" label
        key_facts = soup.find_all('div', {'class': 'col-xs-4'})
        for fact_div in key_facts:
            detail = fact_div.find(['div', 'span'], {'class': 'key_fact_detail'})
            if detail and 'Zimmer' in detail.get_text():
                value = fact_div.find('b', {'class': 'key_fact_value'})
                if value:
                    rooms_str = value.get_text(strip=True)
                    # Handle German decimal comma: "2,5" -> 2
                    rooms_str = rooms_str.replace(',', '.')
                    try:
                        return int(float(rooms_str))
                    except ValueError:
                        continue

        # Strategy 2: Regex in title/full text
        text = soup.get_text()
        match = re.search(r'(\d+(?:[,\.]\d+)?)\s*(?:\-)?(?:Zimmer|Zim)', text, re.IGNORECASE)
        if match:
            rooms_str = match.group(1).replace(',', '.')
            return int(float(rooms_str))

        logger.debug("Could not extract WG-Gesucht room count")
        return 0

    except Exception as e:
        logger.warning(f"Error extracting WG-Gesucht room count: {e}")
        return 0


def extract_wgg_size(soup: BeautifulSoup) -> int:
    """Extract property size in square meters from WG-Gesucht page."""
    try:
        # Strategy 1: Look for key_fact_value with "Größe" label
        key_facts = soup.find_all('div', {'class': 'col-xs-4'})
        for fact_div in key_facts:
            detail = fact_div.find(['div', 'span'], {'class': 'key_fact_detail'})
            if detail and 'Größe' in detail.get_text():
                value = fact_div.find('b', {'class': 'key_fact_value'})
                if value:
                    size_str = value.get_text(strip=True).replace('m²', '').replace('m2', '').strip()
                    try:
                        return int(float(size_str))
                    except ValueError:
                        continue

        # Strategy 2: Regex in full text
        text = soup.get_text()
        match = re.search(r'(\d+(?:\.\d+)?)\s*(?:m²|m2|qm)', text, re.IGNORECASE)
        if match:
            size_str = match.group(1).replace('.', '').replace(',', '')
            return int(float(size_str))

        logger.debug("Could not extract WG-Gesucht property size")
        return 0

    except Exception as e:
        logger.warning(f"Error extracting WG-Gesucht property size: {e}")
        return 0


def extract_wgg_availability(soup: BeautifulSoup) -> str:
    """Extract move-in date / availability from WG-Gesucht page.
    Returns string like "16.06.2026" or "16.06.2026 - 30.06.2027" if end date exists.
    """
    try:
        start_date = None
        end_date = None

        # Look for Verfügbarkeit section with "frei ab" and "frei bis" labels
        sections = soup.find_all('div', {'class': 'col-xs-12'})
        for section in sections:
            title = section.find('h2', {'class': 'section_panel_title'})
            if title and 'Verfügbarkeit' in title.get_text():
                # Found availability section, now extract dates from rows
                rows = section.find_all('div', {'class': 'row'})
                for row in rows:
                    detail = row.find('span', {'class': 'section_panel_detail'})
                    value = row.find('span', {'class': 'section_panel_value'})

                    if detail and value:
                        detail_text = detail.get_text(strip=True)
                        value_text = value.get_text(strip=True)

                        if 'frei ab' in detail_text.lower():
                            start_date = value_text
                        elif 'frei bis' in detail_text.lower():
                            end_date = value_text

        # Return availability string
        if start_date:
            if end_date:
                return f"{start_date} - {end_date}"
            else:
                return start_date

        # Fallback: Look for common patterns in page text
        text = soup.get_text()

        # Pattern: "frei ab DD.MM.YYYY"
        match = re.search(r'frei\s+ab\s+(\d{2}\.\d{2}\.\d{4})', text, re.IGNORECASE)
        if match:
            return match.group(1)

        return ''
    except Exception as e:
        logger.warning(f"Error extracting WG-Gesucht availability: {e}")
        return ''


def extract_wgg_description(soup: BeautifulSoup) -> str:
    """Extract full description from WG-Gesucht page."""
    try:
        # Try to find description by ID
        description_elem = soup.find('div', {'id': 'ad_description_text'})
        if description_elem:
            text = description_elem.get_text(separator=' ', strip=True)
            return text[:2000]

        # Fallback: look for any description-like div
        for div in soup.find_all('div', {'class': re.compile(r'description', re.I)}):
            text = div.get_text(separator=' ', strip=True)
            if len(text) > 100:
                return text[:2000]

        # Last fallback: get main content text (excluding headers, nav, etc.)
        text = soup.get_text(separator=' ', strip=True)
        return text[:2000]

    except Exception as e:
        logger.warning(f"Error extracting WG-Gesucht description: {e}")
        return ""


def extract_wgg_landlord(soup: BeautifulSoup) -> str:
    """Extract landlord/contact person name from WG-Gesucht listing page."""
    try:
        import json

        # Strategy 1: Look for structured data (JSON-LD) with author/creator
        for script in soup.find_all('script', {'type': 'application/ld+json'}):
            try:
                data = json.loads(script.string)
                if isinstance(data, dict):
                    if 'author' in data:
                        return data['author'][:50]
                    if 'creator' in data:
                        return data['creator'][:50]
            except:
                continue

        # Strategy 2: Look for profile initials and member since info
        profile_init = soup.find('div', {'class': 'profile_image_initials'})
        if profile_init:
            # Try to find the full name near the initials
            parent = profile_init.parent
            if parent:
                # Look for any text that looks like a name in the next siblings
                for sibling in parent.find_all(['div', 'span'], recursive=False):
                    text = sibling.get_text(strip=True)
                    if text and len(text) > 2 and len(text) < 50 and not text.startswith('Mitglied'):
                        return text
                # If no name found, use initials
                initials = profile_init.get_text(strip=True)
                if initials:
                    return initials

        # Strategy 3: Look for text like "Vermieter:" or similar
        text = soup.get_text()
        patterns = [
            r'(?:Vermie|Anbieter|Vermieter)[:\s]+([A-Z][a-zA-Z\s\-]+)',
            r'(?:Name|Provider|Anbieter)[:\s]+([A-Z][a-zA-Z\s\-]+)',
        ]
        for pattern in patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                return match.group(1).strip()[:50]

        logger.debug("Could not extract WG-Gesucht landlord name")
        return ''

    except Exception as e:
        logger.warning(f"Error extracting WG-Gesucht landlord name: {e}")
        return ''


def extract_outdoor_space(soup: BeautifulSoup) -> str:
    """Return comma-separated outdoor features found in page text (Balkon, Terrasse, Garten, Loggia)."""
    text = soup.get_text()
    found = [kw for kw in ['Balkon', 'Terrasse', 'Garten', 'Loggia']
             if re.search(rf'\b{kw}\b', text, re.IGNORECASE)]
    return ', '.join(found)
