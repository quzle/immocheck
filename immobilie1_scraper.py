"""Scrape immobilie1.de listing pages with Playwright.

immobilie1.de is a server-rendered Angular app styled with Tailwind utility
classes (no semantic data attributes). The listing facts render as label/value
rows — a bold, 1/3-width label <div> followed by a value <div> — which we parse
into a {label: value} dict and map onto our common listing schema.
"""
import logging
import asyncio
import re
from bs4 import BeautifulSoup
from playwright.async_api import Page

logger = logging.getLogger(__name__)


def _parse_int(text: str) -> int:
    """Pull the first integer from strings like '832 €', '31 m²', or '2.241 €'.

    German thousands separators ('.') and spaces are stripped, so '2.241' -> 2241.
    """
    if not text:
        return 0
    m = re.search(r'\d[\d.\s ]*', text)
    if not m:
        return 0
    digits = re.sub(r'[.\s ]', '', m.group(0))
    try:
        return int(digits)
    except ValueError:
        return 0


def _extract_facts(soup: BeautifulSoup) -> dict:
    """Build a {label: value} dict from the listing's label/value detail rows."""
    facts = {}
    for label_div in soup.find_all(
        "div", class_=lambda c: c and "font-bold" in c and "lg:w-1/3" in c
    ):
        label = ' '.join(label_div.get_text(' ', strip=True).split())
        value_div = label_div.find_next_sibling("div")
        if label and value_div and label not in facts:
            facts[label] = ' '.join(value_div.get_text(' ', strip=True).split())
    return facts


def _i1_warmmiete(facts: dict) -> int:
    """Warm rent (Miete inkl. Nebenkosten). Falls back to Kaltmiete + Betriebskosten."""
    for key in ('Miete inkl. Nebenkosten', 'Gesamtmiete', 'Warmmiete'):
        if facts.get(key):
            return _parse_int(facts[key])
    kalt = _parse_int(facts.get('Miete zzgl. NK', '') or facts.get('Kaltmiete', ''))
    nk = _parse_int(facts.get('Betriebskosten', '') or facts.get('Nebenkosten', ''))
    return kalt + nk if kalt else 0


def _i1_location(soup: BeautifulSoup) -> str:
    """Build a location string like 'Isarvorstadt, 80337 München' from the page."""
    plz_city = None
    # Prefer a clean "80337 München" element.
    node = soup.find(string=re.compile(r'^\s*\d{5}\s+[A-Za-zäöüÄÖÜß.\-/ ]+$'))
    if node:
        plz_city = ' '.join(node.split())
    else:
        # Fall back to the "City City Deutschland (PLZ)" header subtitle.
        m = re.search(r'([A-Za-zäöüÄÖÜß.\- ]+?)\s+Deutschland\s*\((\d{5})\)', soup.get_text())
        if m:
            plz_city = f"{m.group(2)} {m.group(1).split()[0]}"

    # District/Stadtteil from the headline ("... in der Isarvorstadt").
    district = None
    h1 = soup.find('h1')
    if h1:
        dm = re.search(r'\b(?:in der|in dem|im|in)\s+([A-ZÄÖÜ][\wäöüß\-]+)\s*$',
                       h1.get_text(strip=True))
        if dm:
            district = dm.group(1)

    parts = [p for p in (district, plz_city) if p]
    return ', '.join(parts) if parts else 'Unknown'


def _i1_description(soup: BeautifulSoup) -> str:
    """Concatenate the listing's description paragraphs (deduped)."""
    parts = []
    for div in soup.find_all('div', class_=lambda c: c and 'break-words' in c):
        text = ' '.join(div.get_text(' ', strip=True).split())
        if text and text not in parts:
            parts.append(text)
    return ' '.join(parts)[:2000]


def _i1_landlord(soup: BeautifulSoup) -> str:
    """Contact/agency name (e.g. 'Enzenhöfer Immobilien GmbH')."""
    el = soup.find('p', class_=lambda c: c and 'contact__title' in c)
    if el:
        return ' '.join(el.get_text(' ', strip=True).split())[:60]
    return ''


def _i1_outdoor(soup: BeautifulSoup) -> str:
    """Comma-separated outdoor features found in page text."""
    text = soup.get_text()
    found = [kw for kw in ('Balkon', 'Terrasse', 'Garten', 'Loggia')
             if re.search(rf'\b{kw}\b', text, re.IGNORECASE)]
    return ', '.join(dict.fromkeys(found))


def extract_immobilie1_from_soup(soup: BeautifulSoup, url: str) -> dict:
    """Build the listing dict from page HTML (everything except the image count).

    Shared by extract_immobilie1_listing and offline tests so both exercise the
    same extraction logic.
    """
    facts = _extract_facts(soup)
    return {
        'url': url,
        'warmmiete': _i1_warmmiete(facts),
        'location': _i1_location(soup),
        'rooms': _parse_int(facts.get('Zimmer', '')),
        'size_sqm': _parse_int(facts.get('Wohnfläche (ca.)', '') or facts.get('Wohnfläche', '')),
        'property_type': facts.get('Objektart') or facts.get('Hauptobjektart') or 'Wohnung',
        'description': _i1_description(soup),
        'landlord_name': _i1_landlord(soup),
        'availability': facts.get('Verfügbarkeit', ''),
        'outdoor_space': _i1_outdoor(soup),
        'source': 'immobilie1',
    }


async def extract_immobilie1_listing(page: Page, url: str) -> dict:
    """
    Extract full listing details from an immobilie1.de page.
    Returns dict with: warmmiete, location, rooms, size_sqm, property_type,
    description, landlord_name, availability, image_count, outdoor_space, source.
    """
    try:
        try:
            await page.wait_for_load_state("domcontentloaded", timeout=8000)
        except Exception:
            logger.debug("immobilie1: DOM load timeout")
        await asyncio.sleep(0.5)

        soup = BeautifulSoup(await page.content(), 'lxml')
        listing_details = extract_immobilie1_from_soup(soup, url)

        # Count real images (needs the live page, not just the HTML)
        from page_scraper import count_images_on_page
        listing_details['image_count'] = await count_images_on_page(page)

        logger.info(
            f"Extracted immobilie1 listing: {listing_details['rooms']}Z, "
            f"{listing_details['size_sqm']}m², €{listing_details['warmmiete']} Warm, "
            f"{listing_details['location']}, Available: {listing_details['availability']}, "
            f"Contact: {listing_details['landlord_name']}"
        )
        return listing_details

    except Exception as e:
        logger.error(f"Error extracting immobilie1 listing details: {e}")
        return {}
