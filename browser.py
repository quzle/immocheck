"""Playwright browser automation: launch, manage context, and submit applications."""
import logging
import asyncio
import random
from typing import Optional
import json
from pathlib import Path
from config import CHROME_USER_DATA_DIR, DRY_RUN, PLAYWRIGHT_HEADLESS
from playwright.async_api import async_playwright, Browser, BrowserContext, Page

logger = logging.getLogger(__name__)

# Storage state file for persisting cookies/login across sessions
STORAGE_STATE_FILE = Path("data/browser_storage_state.json")

# Applicant details used to autofill the ImmoScout24 contact form.
# Real values live in this gitignored file; see applicant_form.example.json.
APPLICANT_FORM_FILE = Path("templates/applicant_form.json")


def _load_applicant_form_fields() -> list[tuple[str, str, str]]:
    """Load contact-form field values from templates/applicant_form.json.

    Returns a list of (field_name, value, field_type) tuples. Returns an empty
    list (with a warning) if the file is missing or invalid, so submission can
    proceed without autofill rather than crashing.
    """
    if not APPLICANT_FORM_FILE.exists():
        logger.warning(
            f"{APPLICANT_FORM_FILE} not found — skipping contact-form autofill. "
            f"Copy templates/applicant_form.example.json and fill in your details."
        )
        return []
    try:
        data = json.loads(APPLICANT_FORM_FILE.read_text(encoding="utf-8"))
        return [(f["name"], f["value"], f["type"]) for f in data.get("fields", [])]
    except Exception as e:
        logger.warning(f"Could not load {APPLICANT_FORM_FILE}: {e}")
        return []


class InsufficientImagesError(Exception):
    """Raised when a listing has insufficient real images."""
    pass


async def launch_browser() -> tuple[Browser, BrowserContext, Page]:
    """
    Launch Chromium browser with anti-detection (stealth) and persistent user profile.
    Returns (browser, context, page) tuple.
    """
    playwright = await async_playwright().start()

    # Validate Chrome user data directory
    if not CHROME_USER_DATA_DIR:
        logger.warning("CHROME_USER_DATA_DIR not configured, launching without persistent profile")
        chrome_data_dir = None
    else:
        chrome_data_dir = str(Path(CHROME_USER_DATA_DIR).expanduser())
        if not Path(chrome_data_dir).exists():
            # Create the (dedicated) profile dir so Playwright can populate it.
            # Falling back to non-persistent mode here would silently never build
            # the profile, so login would never stick across runs.
            logger.info(f"Creating new Chrome profile directory: {chrome_data_dir}")
            Path(chrome_data_dir).mkdir(parents=True, exist_ok=True)

    launch_args = [
        "--disable-blink-features=AutomationControlled",
        "--disable-dev-shm-usage",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-extensions",
        "--disable-sync",
    ]

    common_context_args = {
        "viewport": {"width": 1280, "height": 720},
        "user_agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    }

    try:
        if chrome_data_dir:
            # Persistent profile: Playwright requires launch_persistent_context() —
            # passing --user-data-dir to launch() is rejected. The profile dir itself
            # holds cookies/login, so storage_state restore is neither needed nor allowed.
            logger.info(f"Using persistent Chrome profile: {chrome_data_dir}")
            context = await playwright.chromium.launch_persistent_context(
                chrome_data_dir,
                headless=PLAYWRIGHT_HEADLESS,
                args=launch_args,
                **common_context_args,
            )
            # No standalone Browser object with a persistent context; close via context.
            browser = context.browser or context
            page = context.pages[0] if context.pages else await context.new_page()
        else:
            browser = await playwright.chromium.launch(
                headless=PLAYWRIGHT_HEADLESS,
                args=launch_args,
            )

            # Create context with stealth patches and restore storage state (cookies/login)
            context_args = dict(common_context_args)

            # Restore cookies/login from previous session if available
            if STORAGE_STATE_FILE.exists():
                try:
                    with open(STORAGE_STATE_FILE) as f:
                        storage_state = json.load(f)
                    context_args["storage_state"] = storage_state
                    logger.info("Restored login session from previous run")
                except Exception as e:
                    logger.warning(f"Could not restore storage state: {e}")

            context = await browser.new_context(**context_args)
            page = None

        # Apply stealth patches
        await context.add_init_script("""
        Object.defineProperty(navigator, 'webdriver', {
            get: () => false,
        });
        Object.defineProperty(navigator, 'plugins', {
            get: () => [1, 2, 3, 4, 5],
        });
        Object.defineProperty(navigator, 'languages', {
            get: () => ['en-US', 'en'],
        });
        window.chrome = {
            runtime: {}
        };
        """)

        if page is None:
            page = await context.new_page()

        mode_desc = "visible" if not PLAYWRIGHT_HEADLESS else "headless"
        logger.info(f"Browser launched successfully in {mode_desc} mode with stealth patches")
        return browser, context, page

    except Exception as e:
        logger.error(f"Failed to launch browser: {e}")
        await playwright.stop()
        raise


async def _check_for_captcha(page: Page) -> bool:
    """Return True if a CAPTCHA widget is currently visible on the page."""
    captcha_selectors = [
        '[id*="captcha"]',
        '[class*="captcha"]',
        'iframe[src*="captcha"]',
        'img[alt*="captcha" i]',
    ]
    for selector in captcha_selectors:
        try:
            if await page.locator(selector).count() > 0:
                return True
        except Exception:
            pass
    return False


async def capture_page_mhtml(page: Page) -> Optional[str]:
    """Capture the current page as MHTML and return the content string."""
    try:
        # IS24 (and many SPAs) set overflow:hidden on html/body and scroll via an
        # inner div. That inner scroll doesn't work in a static MHTML file, so we
        # override it before capturing so the saved page scrolls naturally in Chrome.
        await page.evaluate("""() => {
            document.documentElement.style.overflow = 'auto';
            document.documentElement.style.height   = 'auto';
            document.body.style.overflow = 'auto';
            document.body.style.height   = 'auto';
        }""")

        # Inject a minimal lightbox so image thumbnails are clickable in the saved file.
        # JavaScript executes normally in MHTML files opened in Chrome, so this is
        # baked into the snapshot and works without any external dependencies.
        await page.evaluate("""() => {
            const style = document.createElement('style');
            style.textContent = `
                #_immo_lb {
                    display: none; position: fixed; inset: 0;
                    background: rgba(0,0,0,0.92); z-index: 999999;
                    cursor: zoom-out; align-items: center; justify-content: center;
                }
                #_immo_lb.open { display: flex; }
                #_immo_lb img { max-width: 95vw; max-height: 95vh; object-fit: contain; border-radius: 4px; }
            `;
            document.head.appendChild(style);

            const overlay = document.createElement('div');
            overlay.id = '_immo_lb';
            overlay.innerHTML = '<img id="_immo_lb_img" src="">';
            document.body.appendChild(overlay);

            overlay.addEventListener('click', () => overlay.classList.remove('open'));
            document.addEventListener('keydown', e => {
                if (e.key === 'Escape') overlay.classList.remove('open');
            });

            document.querySelectorAll('img').forEach(img => {
                const src = img.src || img.dataset.src;
                if (!src || src.startsWith('data:') || img.naturalWidth < 50) return;
                img.style.cursor = 'zoom-in';
                img.addEventListener('click', e => {
                    e.stopPropagation();
                    document.getElementById('_immo_lb_img').src = src;
                    document.getElementById('_immo_lb').classList.add('open');
                });
            });
        }""")
        client = await page.context.new_cdp_session(page)
        result = await client.send("Page.captureSnapshot")
        await client.detach()
        return result["data"]
    except Exception as e:
        logger.warning(f"Could not capture MHTML snapshot: {e}")
        return None


def save_listing_snapshot(expose_id: str, mhtml_content: str) -> None:
    """Write a previously captured MHTML snapshot to outputs/submitted/."""
    try:
        output_dir = Path("outputs/submitted")
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / f"{expose_id}.mhtml"
        output_path.write_text(mhtml_content, encoding="utf-8")
        logger.info(f"Saved listing snapshot: {output_path}")
    except Exception as e:
        logger.warning(f"Could not save listing snapshot for {expose_id}: {e}")


async def submit_application(page: Page, url: str, message: str) -> bool:
    """
    Navigate to listing, fill contact form, and submit application.
    Returns True if successful, False otherwise.
    """
    try:
        logger.info(f"Navigating to listing: {url}")
        await page.goto(url, wait_until="domcontentloaded", timeout=30000)

        # Wait for page to load
        await asyncio.sleep(1)

        # Dismiss cookie consent popup if present
        try:
            cookie_selectors = [
                'button[class*="cookie"]',
                'button:has-text("Accept")',
                'button:has-text("Akzeptieren")',
                '[data-testid="cookie-accept"]',
                '.usercentrics-button[data-testid="uc-accept-all-button"]',
            ]
            for selector in cookie_selectors:
                try:
                    btn = page.locator(selector).first
                    if await btn.is_visible(timeout=1000):
                        await btn.click()
                        logger.info("[IS24] Dismissed cookie consent popup")
                        await asyncio.sleep(0.5)
                        break
                except Exception:
                    continue
        except Exception as e:
            logger.debug(f"[IS24] No cookie popup found or already dismissed: {e}")

        await asyncio.sleep(0.5)

        # IS24 contact form is a React modal — must click "Nachricht" button first to open it
        contact_selectors = [
            'button[data-qa="sendButton"]',
            'button[data-testid="contact-button"]',
            'a[data-qa="sendButton"]',
            'button:has-text("Nachricht")',
            'a:has-text("Nachricht")',
            '#is24-contact-sidebar button',
        ]

        contact_button = None
        for selector in contact_selectors:
            try:
                btn = page.locator(selector).first
                if await btn.is_visible(timeout=2000):
                    contact_button = btn
                    logger.info(f"[IS24] Found contact button: {selector}")
                    break
            except Exception:
                continue

        if not contact_button:
            logger.error("[IS24] Could not find contact button — form will not open")
            return False

        await contact_button.click()
        logger.info("[IS24] Clicked contact button, waiting for form modal...")

        # Wait for the React modal to render the textarea
        try:
            await page.wait_for_selector('textarea', state='visible', timeout=8000)
        except Exception:
            logger.error("[IS24] Textarea did not appear after clicking contact button")
            return False

        await asyncio.sleep(0.5)

        # Try to find the message textarea (now inside the opened modal)
        # ImmoScout24 typically has a form with message textarea
        message_selectors = [
            'textarea[data-testid*="message"]',
            'textarea[data-testid*="contact"]',
            'textarea[name*="message"]',
            'textarea[name*="kontakt"]',
            'textarea[id*="message"]',
            'textarea[id*="contact"]',
            'textarea.application-message',
            'textarea',
        ]

        textarea = None
        for selector in message_selectors:
            try:
                textarea = page.locator(selector).first
                if await textarea.is_visible():
                    logger.info(f"Found message field with selector: {selector}")
                    break
            except Exception:
                continue

        if not textarea:
            logger.error("Could not locate message textarea on the page")
            return False

        # Clear existing content and type message with human-like delays
        await textarea.click()
        await textarea.fill("")

        logger.info(f"Typing application message ({len(message)} characters)...")
        for char in message:
            await page.keyboard.type(char)
            # Random delay between 50-150ms per keystroke
            await asyncio.sleep(0.05 + (hash(char) % 100) / 1000)

        await asyncio.sleep(0.5)

        # Fill personal details form fields
        # Contact form includes: name, email, phone, address, employment info, etc.
        try:
            # Load form field values from gitignored templates/applicant_form.json
            form_fields = _load_applicant_form_fields()

            for field_name, field_value, field_type in form_fields:
                try:
                    if field_type == 'skip':
                        logger.debug(f"Skipping field {field_name} (pre-filled or disabled)")
                        continue

                    if field_type == 'select':
                        # For select fields, try multiple selector patterns
                        selectors = [
                            f'select[name="{field_name}"]',
                            f'select[data-testid="{field_name}"]',
                            f'[data-testid="{field_name}"]',
                        ]

                        elem = None
                        for selector in selectors:
                            try:
                                candidate = page.locator(selector).first
                                if await candidate.is_visible():
                                    elem = candidate
                                    break
                            except Exception:
                                continue

                        if elem:
                            await elem.select_option(label=field_value)
                            logger.info(f"Set {field_name} to {field_value}")
                            await asyncio.sleep(random.uniform(0.3, 1.0))
                        else:
                            logger.warning(f"Could not locate select field: {field_name}")

                    else:  # text input
                        # For text inputs, try multiple selector patterns
                        selectors = [
                            f'input[name="{field_name}"]',
                            f'input[data-testid="{field_name}"]',
                            f'[data-testid="{field_name}"] input',
                            f'[data-testid="{field_name}"]',
                        ]

                        elem = None
                        for selector in selectors:
                            try:
                                candidate = page.locator(selector).first
                                if await candidate.is_visible():
                                    # Check if field is disabled before trying to fill
                                    is_disabled = await candidate.is_disabled()
                                    if is_disabled:
                                        logger.info(f"Skipping {field_name} (disabled, pre-filled)")
                                        break
                                    elem = candidate
                                    break
                            except Exception:
                                continue

                        if elem:
                            await elem.fill(field_value)
                            logger.info(f"Set {field_name} to {field_value}")
                            await asyncio.sleep(random.uniform(0.3, 1.0))
                        elif field_type != 'skip':
                            logger.warning(f"Could not locate text field: {field_name}")

                except Exception as e:
                    logger.warning(f"Error filling field {field_name}: {e}")

        except Exception as e:
            logger.warning(f"Error filling contact form fields: {e}")

        # If DRY_RUN is enabled, don't actually submit
        if DRY_RUN:
            logger.info("DRY_RUN mode: Not submitting form")
            return True

        # Find and click submit button
        submit_button = None
        submit_selectors = [
            'button[data-qa="sendButton"]',
            'button[data-testid="send-button"]',
            'button[type="submit"]',
            'button[name*="submit"]',
            'button:has-text("Senden")',
            'button:has-text("Send")',
            'button:has-text("Absenden")',
            'button:has-text("submit")',
        ]

        for selector in submit_selectors:
            try:
                button = page.locator(selector).first
                if await button.is_visible():
                    submit_button = button
                    logger.info(f"Found submit button with selector: {selector}")
                    break
            except Exception:
                continue

        if not submit_button:
            logger.error("Could not locate submit button on the page")
            return False

        # Check for form validation errors before submitting
        try:
            error_fields = await page.locator('[aria-invalid="true"]').count()
            if error_fields > 0:
                logger.error(f"Form has {error_fields} invalid fields - cannot submit")
                # Log which fields have errors
                invalid_elements = await page.locator('[aria-invalid="true"]').all()
                for elem in invalid_elements:
                    try:
                        test_id = await elem.get_attribute('data-testid')
                        logger.error(f"  Invalid field: {test_id}")
                    except:
                        pass
                return False
        except Exception as e:
            logger.warning(f"Could not check for validation errors: {e}")

        # Verify button is enabled before clicking
        try:
            is_disabled = await submit_button.is_disabled()
            if is_disabled:
                logger.error("Submit button is disabled - form may have validation errors")
                return False
        except Exception as e:
            logger.warning(f"Could not check button disabled state: {e}")

        # Click submit button with verification
        try:
            # Scroll button into view with a human-like pause before clicking
            await submit_button.scroll_into_view_if_needed()
            await asyncio.sleep(random.uniform(1.0, 2.5))

            # Try direct JavaScript click as most reliable method
            element_handle = await submit_button.element_handle()
            await element_handle.evaluate('el => el.click()')
            logger.info("✓ Submit button clicked via JavaScript")
        except Exception as e:
            logger.warning(f"JavaScript click failed: {e}, trying force click")
            try:
                await submit_button.click(force=True)
                logger.info("✓ Submit button clicked (forced)")
            except Exception as e2:
                logger.warning(f"Force click failed: {e2}, trying keyboard Enter")
                try:
                    # Fallback: try pressing Enter on the form/button
                    await submit_button.focus()
                    await submit_button.press("Enter")
                    logger.info("✓ Submit triggered via Enter key")
                except Exception as e3:
                    logger.error(f"Failed to submit via JavaScript, click, or Enter: {e3}")
                    return False

        # Verify button click was processed (button should become disabled or form should change)
        await asyncio.sleep(0.5)
        try:
            # Check if button is now disabled (indicates form was submitted)
            is_now_disabled = await submit_button.is_disabled()
            if is_now_disabled:
                logger.info("✓ Submit button became disabled after click - submission processed")
                await asyncio.sleep(1.5)  # Give page time to show confirmation
            else:
                logger.warning("⚠ Submit button still enabled after click - may not have submitted")
        except Exception as e:
            logger.debug(f"Could not verify button state after click: {e}")

        # Wait for page response after submission
        await asyncio.sleep(1)

        # Check for CAPTCHA before evaluating success/failure
        if await _check_for_captcha(page):
            logger.warning("[IS24] CAPTCHA detected after submission — triggering manual fallback")
            return False

        # Check for success indicators
        success_selectors = [
            'text="success"',
            'text="erfolgreich"',
            'text="thank you"',
            'text="danke"',
            'text="Vielen Dank"',
        ]

        for selector in success_selectors:
            try:
                if await page.locator(selector).count() > 0:
                    logger.info("✓ Success confirmation found on page")
                    return True
            except Exception:
                continue

        # Check for error/validation messages
        error_selectors = [
            'text="error"',
            'text="Fehler"',
            'text="erforderlich"',  # Required field
            '[class*="error"]',
            '[class*="invalid"]',
        ]

        for selector in error_selectors:
            try:
                if await page.locator(selector).count() > 0:
                    logger.error("✗ Form validation error detected after submission")
                    return False
            except Exception:
                continue

        # Check if the form is still visible (indicates submission failed)
        try:
            form_visible = await page.locator('textarea[data-testid="message"], input[data-testid="firstName"]').count() > 0
            if form_visible:
                logger.warning("⚠ Form still visible after submission attempt - submission may have failed")
                return False
        except Exception:
            pass

        logger.warning("⚠ No success or error indicators found - submission status unclear, treating as failed")
        return False

    except Exception as e:
        logger.error(f"Error during application submission: {e}")
        return False


async def submit_wg_gesucht_application(page: Page, url: str, message: str) -> bool:
    """
    Navigate to WG-Gesucht listing, dismiss ad overlay, and submit application.
    WG-Gesucht requires clicking "Nachricht schreiben" to open the contact form.
    Returns True if successful, False otherwise.
    """
    try:
        logger.info(f"Navigating to WG-Gesucht listing: {url}")
        await page.goto(url, wait_until="domcontentloaded", timeout=30000)

        # Dismiss ad/sponsor overlays (import from scraper to reuse logic)
        from wg_gesucht_scraper import _dismiss_wgg_overlays
        await _dismiss_wgg_overlays(page)

        # Wait for page to settle
        await page.wait_for_load_state("domcontentloaded")
        await asyncio.sleep(1)

        # Try to find and click the "Kontakt aufnehmen" / "Nachricht schreiben" button
        contact_selectors = [
            'a:has-text("Nachricht schreiben")',
            'button:has-text("Nachricht schreiben")',
            'a:has-text("Kontakt aufnehmen")',
            'button:has-text("Kontakt aufnehmen")',
            'a.btn-contact',
            'a[href*="kontakt"]',
        ]

        contact_button = None
        for selector in contact_selectors:
            try:
                btn = page.locator(selector).first
                if await btn.is_visible():
                    contact_button = btn
                    logger.info(f"Found contact button with selector: {selector}")
                    break
            except Exception:
                continue

        if not contact_button:
            logger.error("Could not locate contact button on WG-Gesucht page")
            return False

        # Click contact button to open message form
        await contact_button.click()
        logger.info("Clicked contact button")

        # Wait for form to appear
        try:
            await page.wait_for_load_state("domcontentloaded", timeout=10000)
        except Exception:
            pass
        await asyncio.sleep(1)

        # Try to find the message textarea
        message_selectors = [
            'textarea[name*="message"]',
            'textarea[name*="nachricht"]',
            'textarea[id*="message"]',
            'textarea[id*="contact"]',
            'textarea.application-message',
            'textarea',
        ]

        textarea = None
        for selector in message_selectors:
            try:
                textarea = page.locator(selector).first
                if await textarea.is_visible():
                    logger.info(f"Found message field with selector: {selector}")
                    break
            except Exception:
                continue

        if not textarea:
            logger.error("Could not locate message textarea on WG-Gesucht page")
            return False

        # Clear and fill message with human-like delays
        await textarea.click()
        await textarea.fill("")

        logger.info(f"Typing WG-Gesucht application message ({len(message)} characters)...")
        for char in message:
            await page.keyboard.type(char)
            # Random delay between 50-150ms per keystroke
            await asyncio.sleep(0.05 + (hash(char) % 100) / 1000)

        await asyncio.sleep(0.5)

        # If DRY_RUN is enabled, don't actually submit
        if DRY_RUN:
            logger.info("DRY_RUN mode: Not submitting WG-Gesucht form")
            return True

        # Find and click submit button
        submit_button = None
        submit_selectors = [
            'button[type="submit"]',
            'button[name*="submit"]',
            'button:has-text("Senden")',
            'button:has-text("Send")',
            'button:has-text("submit")',
        ]

        for selector in submit_selectors:
            try:
                button = page.locator(selector).first
                if await button.is_visible():
                    submit_button = button
                    logger.info(f"Found submit button with selector: {selector}")
                    break
            except Exception:
                continue

        if not submit_button:
            logger.error("Could not locate submit button on WG-Gesucht page")
            return False

        # Check for form validation errors before submitting
        try:
            error_fields = await page.locator('[aria-invalid="true"]').count()
            if error_fields > 0:
                logger.error(f"WG-Gesucht form has {error_fields} invalid fields - cannot submit")
                # Log which fields have errors
                invalid_elements = await page.locator('[aria-invalid="true"]').all()
                for elem in invalid_elements:
                    try:
                        test_id = await elem.get_attribute('data-testid')
                        logger.error(f"  Invalid field: {test_id}")
                    except:
                        pass
                return False
        except Exception as e:
            logger.warning(f"Could not check WG-Gesucht validation errors: {e}")

        # Verify button is enabled before clicking
        try:
            is_disabled = await submit_button.is_disabled()
            if is_disabled:
                logger.error("WG-Gesucht submit button is disabled - form may have validation errors")
                return False
        except Exception as e:
            logger.warning(f"Could not check WG-Gesucht button disabled state: {e}")

        # Click submit button with verification
        try:
            # Scroll button into view and force click (ignore pointer event interception)
            await submit_button.scroll_into_view_if_needed()
            await asyncio.sleep(0.3)

            # Try direct JavaScript click as most reliable method
            element_handle = await submit_button.element_handle()
            await element_handle.evaluate('el => el.click()')
            logger.info("✓ WG-Gesucht submit button clicked via JavaScript")
        except Exception as e:
            logger.warning(f"WG-Gesucht JavaScript click failed: {e}, trying force click")
            try:
                await submit_button.click(force=True)
                logger.info("✓ WG-Gesucht submit button clicked (forced)")
            except Exception as e2:
                logger.warning(f"WG-Gesucht force click failed: {e2}, trying keyboard Enter")
                try:
                    # Fallback: try pressing Enter on the form/button
                    await submit_button.focus()
                    await submit_button.press("Enter")
                    logger.info("✓ WG-Gesucht submit triggered via Enter key")
                except Exception as e3:
                    logger.error(f"Failed to submit WG-Gesucht via JavaScript, click, or Enter: {e3}")
                    return False

        # Verify button click was processed (button should become disabled or form should change)
        await asyncio.sleep(0.5)
        try:
            # Check if button is now disabled (indicates form was submitted)
            is_now_disabled = await submit_button.is_disabled()
            if is_now_disabled:
                logger.info("✓ WG-Gesucht submit button became disabled after click - submission processed")
                await asyncio.sleep(1.5)  # Give page time to show confirmation
            else:
                logger.warning("⚠ WG-Gesucht submit button still enabled after click - may not have submitted")
        except Exception as e:
            logger.debug(f"Could not verify WG-Gesucht button state after click: {e}")

        # Wait for page response after submission
        await asyncio.sleep(1)

        # Check for success indicators
        success_selectors = [
            'text="success"',
            'text="erfolgreich"',
            'text="thank you"',
            'text="danke"',
            'text="Vielen Dank"',
        ]

        for selector in success_selectors:
            try:
                if await page.locator(selector).count() > 0:
                    logger.info("✓ WG-Gesucht success confirmation found on page")
                    return True
            except Exception:
                continue

        # Check for error/validation messages
        error_selectors = [
            'text="error"',
            'text="Fehler"',
            'text="erforderlich"',  # Required field
            '[class*="error"]',
            '[class*="invalid"]',
        ]

        for selector in error_selectors:
            try:
                if await page.locator(selector).count() > 0:
                    logger.error("✗ WG-Gesucht form validation error detected after submission")
                    return False
            except Exception:
                continue

        # Check if the form is still visible (indicates submission failed)
        try:
            form_visible = await page.locator('textarea[name*="message"], textarea[name*="nachricht"]').count() > 0
            if form_visible:
                logger.warning("⚠ WG-Gesucht form still visible after submission attempt - submission may have failed")
                return False
        except Exception:
            pass

        logger.warning("⚠ WG-Gesucht: No success or error indicators found - submission status unclear, treating as failed")
        return False

    except Exception as e:
        logger.error(f"Error during WG-Gesucht application submission: {e}")
        return False


async def save_browser_storage_state(context: BrowserContext) -> bool:
    """Save browser storage state (cookies, localStorage, etc) for next session."""
    try:
        STORAGE_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        storage_state = await context.storage_state()
        with open(STORAGE_STATE_FILE, 'w') as f:
            json.dump(storage_state, f, indent=2)
        logger.info(f"Saved browser session to {STORAGE_STATE_FILE}")
        return True
    except Exception as e:
        logger.warning(f"Could not save storage state: {e}")
        return False


async def test_browser_launch():
    """Test browser launch with sample navigation."""
    try:
        logger.info("Testing browser launch...")
        browser, context, page = await launch_browser()

        logger.info("Navigating to ImmoScout24 account page...")
        await page.goto("https://www.immobilienscout24.de/meinkonto/", wait_until="domcontentloaded")

        # Check if we're logged in (would redirect to login if not)
        current_url = page.url
        if "meinkonto" in current_url or "login" not in current_url:
            logger.info(f"Navigation successful, current URL: {current_url}")
        else:
            logger.warning(f"May not be logged in, URL: {current_url}")

        await asyncio.sleep(2)
        await context.close()
        await browser.close()
        logger.info("Browser test completed successfully")
        return True

    except Exception as e:
        logger.error(f"Browser test failed: {e}")
        return False


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    result = asyncio.run(test_browser_launch())
    exit(0 if result else 1)
