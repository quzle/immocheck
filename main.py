import logging
import asyncio
import json
import sys
from datetime import datetime
from typing import Optional
from pathlib import Path

from config import POLL_INTERVAL, FORCE_EMAIL_FALLBACK, LLM_CONCURRENCY, MIN_IMAGES, MAX_WARMMIETE, ENABLE_TRANSLATION, TRANSLATION_TARGET_LANGUAGE, validate_config
from email_ingestion import fetch_unread_emails, mark_emails_read
from email_parser import parse_alert_email
from listing_filters import apply_email_prefilter, check_availability_duration
from page_scraper import extract_listing_details
from wg_gesucht_scraper import extract_wg_gesucht_listing
from immobilie1_scraper import extract_immobilie1_listing
from browser import submit_wg_gesucht_application
from llm_evaluator import evaluate_listing, draft_application, score_listing
from browser import launch_browser, submit_application, InsufficientImagesError, save_browser_storage_state, capture_page_mhtml, save_listing_snapshot
from application_fallback import fallback_apply
from email_notifications import send_application_email, send_captcha_failure_email
from push_notifications import notify_auto_submitted, notify_manual_required, notify_captcha_failed
from state import is_processed, mark_processed, extract_listing_id, get_statistics, track_failure, should_retry, clear_failure, get_failure_info, get_captcha_retry_listings, captcha_retries_exhausted, MAX_CAPTCHA_RETRIES
from utils import load_text_file, ensure_working_file
from translation import translate_text

# Setup logging
LOG_DIR = "outputs/logs"
LOG_JSONL = "outputs/actions.jsonl"


LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"


class PrependingPollHandler(logging.Handler):
    """
    Buffers log records for one poll cycle and prepends them to the log file
    when flush_poll() is called. This keeps the log file newest-first.
    Console output is unaffected and always streams in real time.
    """

    def __init__(self, filepath: Path):
        super().__init__()
        self.filepath = filepath
        self.setFormatter(logging.Formatter(LOG_FORMAT))
        self._buffer: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self._buffer.append(self.format(record))

    def flush_poll(self) -> None:
        """Prepend buffered records (oldest→newest) before existing file content."""
        if not self._buffer:
            return
        new_block = "\n".join(self._buffer) + "\n"
        existing = self.filepath.read_text(encoding="utf-8") if self.filepath.exists() else ""
        self.filepath.write_text(new_block + existing, encoding="utf-8")
        self._buffer.clear()


_poll_handler: Optional[PrependingPollHandler] = None


def setup_logging():
    """Configure logging to file and stdout with timestamped session files."""
    global _poll_handler
    logger = logging.getLogger()
    logger.setLevel(logging.DEBUG)

    # Only add handlers if they don't already exist (prevent duplicates)
    if not logger.handlers:
        # Create logs directory if it doesn't exist
        logs_path = Path(LOG_DIR)
        logs_path.mkdir(exist_ok=True)

        # Generate timestamped filename (yymmdd-hhmm-immoCheck.log)
        now = datetime.now()
        timestamp = now.strftime("%y%m%d-%H%M")
        log_file = logs_path / f"{timestamp}-immoCheck.log"

        # File handler: buffers per poll and prepends so newest is always at top
        _poll_handler = PrependingPollHandler(log_file)
        logger.addHandler(_poll_handler)

        # Console handler: streams in real time (unaffected by file ordering)
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setFormatter(logging.Formatter(LOG_FORMAT))
        logger.addHandler(console_handler)

        logger.info(f"Logging to: {log_file}")

    return logger


logger = setup_logging()

# Track in-flight operations for heartbeat
in_flight_listings = {}


def log_heartbeat(expose_id: str, operation: str, elapsed_secs: float):
    """Log a heartbeat for a long-running operation."""
    logger.info(f"[HEARTBEAT] Listing {expose_id}: {operation} ({elapsed_secs:.1f}s elapsed)")


async def heartbeat_task(interval: int = 30):
    """Background task that logs periodic heartbeats for in-flight operations."""
    while True:
        try:
            await asyncio.sleep(interval)
            now = datetime.now()
            for expose_id, info in in_flight_listings.items():
                elapsed = (now - info["start_time"]).total_seconds()
                log_heartbeat(expose_id, info["operation"], elapsed)
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.warning(f"Error in heartbeat task: {e}")


def detect_captcha_on_page(page_text: str) -> bool:
    """Detect if page is showing a CAPTCHA challenge.

    Uses specific indicators that are unlikely to appear in normal listing pages.
    """
    page_lower = page_text.lower()
    # Very specific CAPTCHA indicators that rarely appear in normal content
    strong_indicators = [
        "i am not a robot",  # Google reCAPTCHA English
        "ich bin kein roboter",  # IS24 German CAPTCHA page title
        "überprüfen sie, dass sie kein roboter sind",  # Google reCAPTCHA German - exact phrase
        "please verify that you are human",  # reCAPTCHA variant
        "verify that you're not a bot",  # Common CAPTCHA phrasing
    ]

    # Check for strong indicators first
    if any(indicator in page_lower for indicator in strong_indicators):
        return True

    # Only flag if we see CAPTCHA library names (these are very unlikely in listing content)
    weak_indicators = [
        "g-recaptcha",
        "h-captcha",
        "cloudflare challenge",
    ]

    # Count weak indicators - need multiple to avoid false positives
    weak_count = sum(1 for indicator in weak_indicators if indicator in page_lower)
    return weak_count >= 2


def log_listing_action(
    expose_id: str, url: str, filter_result: str, llm_decision: str,
    llm_reason: str, submission_result: str, error: str = ""
):
    """Log listing action to JSONL file."""
    try:
        entry = {
            "timestamp": datetime.now().isoformat(),
            "expose_id": expose_id,
            "url": url,
            "filter_result": filter_result,
            "llm_decision": llm_decision,
            "llm_reason": llm_reason,
            "submission_result": submission_result,
            "error": error,
        }
        with open(LOG_JSONL, 'a') as f:
            f.write(json.dumps(entry) + '\n')
    except Exception as e:
        logger.error(f"Failed to write to log.jsonl: {e}")


async def process_listing(listing: dict, profile: str, template: str, browser, context, semaphore, page_semaphore, submission_semaphore) -> dict:
    """
    Process a single listing through the entire pipeline.

    Workflow:
    1. Email pre-filter (blocklist keywords only)
    2. Load URL with Playwright and extract full details
    3. Validate images count and price
    4. LLM evaluation on full details
    5. Draft application
    6. Submit
    """
    url = listing.get('url', '')
    expose_id = extract_listing_id(url)

    logger.info(f"[QUEUE] Listing {expose_id}")

    # Check if already processed
    if is_processed(expose_id):
        logger.info(f"[SKIP] Listing {expose_id} already processed")
        return {
            "processed": False,
            "reason": "Already processed",
            "expose_id": expose_id,
        }

    # Check if this listing is in the failures file and should be retried
    failure_info = get_failure_info(expose_id)
    if failure_info:
        if should_retry(expose_id, max_parse_retries=3):
            logger.info(f"[RETRY] Listing {expose_id}: retrying after previous {failure_info.get('last_error_type')} error (attempt {failure_info.get('retry_count')})")
        else:
            logger.info(f"[SKIP] Listing {expose_id} already retried max times ({failure_info.get('retry_count')} attempts)")
            return {
                "processed": False,
                "reason": "Max retries exhausted",
                "expose_id": expose_id,
            }

    # 1. Email pre-filter (blocklist keywords only - Python based, fast)
    # Skip for retry-queue listings (e.g. CAPTCHA retries): they already passed
    # the prefilter on their original attempt, and we don't have title/description
    # cached when re-queuing from failed_listings.json.
    is_retry = listing.get('_from_retry_queue', False)
    logger.debug(f"[FILTER] Applying email pre-filter for {expose_id}")
    if is_retry:
        logger.debug(f"[FILTER] Skipping pre-filter for {expose_id} (retry-queue entry)")
        filter_passed, filter_reason = True, ""
    else:
        filter_passed, filter_reason = apply_email_prefilter(listing)
    if not filter_passed:
        logger.info(f"[REJECT_FILTER] Listing {expose_id}: {filter_reason}")
        mark_processed(expose_id, "REJECTED_PREFILTER", reason=filter_reason)
        log_listing_action(expose_id, url, f"REJECTED: {filter_reason}", "N/A", "N/A", "N/A")
        return {
            "processed": True,
            "approved": False,
            "reason": f"Pre-filter: {filter_reason}",
            "expose_id": expose_id,
        }

    # 2. Load URL with Playwright and extract full listing details
    logger.info(f"[WAIT_LOAD] Listing {expose_id} waiting for page load slot...")
    if not (browser and context):
        logger.error(f"[ERROR] Listing {expose_id} browser not available")
        return {
            "processed": True,
            "approved": False,
            "reason": "Browser not available",
            "expose_id": expose_id,
        }

    try:
        # Use page_semaphore to serialize page loading and avoid anti-bot detection
        async with page_semaphore:
            logger.debug(f"[LOAD] Listing {expose_id} page load started")
            page = await context.new_page()
            await page.goto(url, wait_until="domcontentloaded", timeout=30000)

            # Try to dismiss cookies popup if present (common on German real estate sites)
            try:
                # Try ImmoScout24 specific cookie button first
                accept_button = page.locator('button[data-testid="uc-accept-all-button"]').first
                if await accept_button.is_visible(timeout=2000):
                    await accept_button.click()
                    logger.debug(f"Dismissed cookies popup for {expose_id}")
            except Exception:
                try:
                    # Fallback: Try other common cookie button patterns
                    accept_button = page.locator('button:has-text("akzeptieren"), button:has-text("Akzeptieren"), button:has-text("Accept")').first
                    if await accept_button.is_visible(timeout=2000):
                        await accept_button.click()
                        logger.debug(f"Dismissed cookies popup (fallback) for {expose_id}")
                except Exception:
                    pass  # No cookies popup or couldn't dismiss it, continue anyway

            # Wait for page to settle after cookie dismissal (domcontentloaded for IS24 SPA)
            try:
                await page.wait_for_load_state("domcontentloaded", timeout=10000)
            except Exception:
                # If load state times out, continue anyway - the page is likely loaded
                logger.debug(f"Page load timeout for {expose_id}, continuing with partial load")

            await asyncio.sleep(2)  # Human-like delay between page loads

            # Check page content
            page_text = await page.content()

            # Check if listing is deactivated FIRST (more specific than CAPTCHA indicators)
            # Phrases for both IS24 and WG-Gesucht
            deactivated_phrases = [
                "Angebot wurde deaktiviert",  # IS24
                "Das Angebot ist nicht mehr verfügbar",  # IS24
                "Dieses Inserat ist nicht mehr aktiv",  # WG-Gesucht
                "nicht mehr verfügbar",  # WG-Gesucht (fallback)
            ]
            if any(phrase in page_text for phrase in deactivated_phrases):
                logger.info(f"[REJECT_DEACTIVATED] Listing {expose_id}")
                await page.close()
                mark_processed(expose_id, "REJECTED_DEACTIVATED", reason="Listing deactivated")
                log_listing_action(expose_id, url, "REJECTED: Listing deactivated", "N/A", "N/A", "N/A")
                return {
                    "processed": True,
                    "approved": False,
                    "reason": "Listing deactivated or no longer available",
                    "expose_id": expose_id,
                }

            # Check if CAPTCHA is present (after deactivated check to avoid false positives)
            if detect_captcha_on_page(page_text):
                await page.close()
                track_failure(expose_id, url, "captcha", "CAPTCHA challenge on page load")

                if captcha_retries_exhausted(expose_id):
                    # Give up on this listing — notify the user so they can review manually.
                    failure_info = get_failure_info(expose_id)
                    retry_count = failure_info.get('retry_count', 0)
                    logger.error(f"[CAPTCHA_GIVE_UP] Listing {expose_id}: still blocked after {retry_count} attempts — notifying user")
                    try:
                        send_captcha_failure_email(expose_id, url, retry_count)
                    except Exception as e:
                        logger.error(f"Failed to send CAPTCHA failure email for {expose_id}: {e}")
                    try:
                        notify_captcha_failed(expose_id, url)
                    except Exception as e:
                        logger.warning(f"Failed to send CAPTCHA push notification for {expose_id}: {e}")
                    mark_processed(expose_id, "CAPTCHA_FAILED", reason=f"CAPTCHA blocked after {retry_count} retries — manual review required")
                    log_listing_action(expose_id, url, "CAPTCHA: gave up — manual review", "N/A", "N/A", "N/A")
                    return {
                        "processed": True,  # Stop retrying
                        "approved": False,
                        "reason": "CAPTCHA retries exhausted — user notified",
                        "expose_id": expose_id,
                    }

                logger.warning(f"[CAPTCHA] Listing {expose_id}: CAPTCHA on page load — queued for retry (max {MAX_CAPTCHA_RETRIES})")
                log_listing_action(expose_id, url, "CAPTCHA: will retry", "N/A", "N/A", "N/A")
                return {
                    "processed": False,  # Not marked processed — retried next run
                    "approved": False,
                    "reason": "CAPTCHA challenge detected - will retry",
                    "expose_id": expose_id,
                }

            # Extract full details from page (dispatch by platform)
            logger.debug(f"[EXTRACT] Listing {expose_id} extracting details")
            source = listing.get('source', 'immoscout24')
            snapshot_mhtml = None
            try:
                if source == 'wggesucht':
                    full_listing = await asyncio.wait_for(extract_wg_gesucht_listing(page, url), timeout=30)
                elif source == 'immobilie1':
                    full_listing = await asyncio.wait_for(extract_immobilie1_listing(page, url), timeout=30)
                else:
                    full_listing = await asyncio.wait_for(extract_listing_details(page, url), timeout=30)
                snapshot_mhtml = await capture_page_mhtml(page)
            except asyncio.TimeoutError:
                logger.error(f"[EXTRACT_TIMEOUT] Listing {expose_id} extraction took too long")
                full_listing = {}
            finally:
                await page.close()

        # Add delay between processing listings (outside the page load semaphore)
        await asyncio.sleep(1)

        if not full_listing:
            logger.error(f"[EXTRACT_FAIL] Listing {expose_id}")
            return {
                "processed": True,
                "approved": False,
                "reason": "Failed to extract details",
                "expose_id": expose_id,
            }

        logger.debug(f"[EXTRACT_OK] Listing {expose_id}")

        # 3. Validate images and price
        image_count = full_listing.get('image_count', 0)
        if image_count < MIN_IMAGES:
            logger.info(f"[REJECT_IMAGES] Listing {expose_id}: {image_count} images < {MIN_IMAGES} required")
            mark_processed(expose_id, "REJECTED_IMAGES", reason=f"{image_count} images < {MIN_IMAGES} required")
            log_listing_action(expose_id, url, "REJECTED: Insufficient images", "N/A", "N/A", "N/A")
            return {
                "processed": True,
                "approved": False,
                "reason": f"Insufficient images: {image_count} < {MIN_IMAGES}",
                "expose_id": expose_id,
            }

        warmmiete = full_listing.get('warmmiete', 0)
        if warmmiete > 0 and warmmiete > MAX_WARMMIETE:
            logger.info(f"[REJECT_PRICE] Listing {expose_id}: €{warmmiete} > €{MAX_WARMMIETE}")
            mark_processed(expose_id, "REJECTED_PRICE", reason=f"€{warmmiete} > €{MAX_WARMMIETE} budget")
            log_listing_action(expose_id, url, f"REJECTED: Price €{warmmiete}", "N/A", "N/A", "N/A")
            return {
                "processed": True,
                "approved": False,
                "reason": f"Price exceeds limit: €{warmmiete}",
                "expose_id": expose_id,
            }

        # Check availability duration for WG-Gesucht (reject if <= 2 years)
        availability_passed, availability_reason = check_availability_duration(full_listing)
        if not availability_passed:
            logger.info(f"[REJECT_DURATION] Listing {expose_id}: {availability_reason}")
            mark_processed(expose_id, "REJECTED_DURATION", reason=availability_reason)
            log_listing_action(expose_id, url, f"REJECTED: {availability_reason}", "N/A", "N/A", "N/A")
            return {
                "processed": True,
                "approved": False,
                "reason": availability_reason,
                "expose_id": expose_id,
            }

    except Exception as e:
        logger.error(f"[ERROR] Listing {expose_id} page load/parse error: {e}")
        return {
            "processed": True,
            "approved": False,
            "reason": f"Page parsing error: {e}",
            "expose_id": expose_id,
        }

    # 4. Evaluate with LLM (now with full listing details from page)
    async with semaphore:
        logger.info(f"[EVAL] Listing {expose_id}")
        in_flight_listings[expose_id] = {"operation": "evaluate", "start_time": datetime.now()}

        try:
            # Run LLM evaluation in executor to avoid blocking
            evaluation = await asyncio.get_event_loop().run_in_executor(
                None, evaluate_listing, full_listing, profile
            )
            llm_decision = evaluation.get('decision', 'REJECT')
            llm_reason = evaluation.get('reason', '')
        finally:
            in_flight_listings.pop(expose_id, None)

    if llm_decision == 'ERROR':
        error_type = evaluation.get('error_type', 'unknown')
        if should_retry(expose_id, max_parse_retries=3):
            # Retryable error - don't mark as processed
            track_failure(expose_id, url, error_type, llm_reason)
            logger.warning(f"[EVAL_RETRY] Listing {expose_id} ({error_type}): will retry next run")
            log_listing_action(expose_id, url, "PASSED", "ERROR", f"{error_type}: {llm_reason[:100]}", "N/A", f"Will retry ({error_type})")
            return {
                "processed": False,  # Don't mark as processed, retry next run
                "approved": False,
                "reason": f"LLM {error_type}: will retry",
                "expose_id": expose_id,
            }
        else:
            # Non-retryable error or retries exhausted
            track_failure(expose_id, url, error_type, llm_reason)
            failure_info = get_failure_info(expose_id)
            retry_count = failure_info.get('retry_count', 0)
            logger.error(f"[EVAL_FAIL] Listing {expose_id} ({error_type}, {retry_count} retries): giving up")
            mark_processed(expose_id, "EVAL_FAILED", reason=f"LLM failed after {retry_count} retries ({error_type})")
            log_listing_action(expose_id, url, "PASSED", "ERROR", f"{error_type}: {llm_reason[:100]}", "N/A", f"Failed after {retry_count} retries")
            return {
                "processed": True,  # Mark as processed to stop retrying
                "approved": False,
                "reason": f"LLM failed after {retry_count} attempts",
                "expose_id": expose_id,
            }

    if llm_decision == 'REJECT':
        logger.info(f"[REJECT_LLM] Listing {expose_id}: {llm_reason}")
        clear_failure(expose_id)  # Clear any previous failures for this listing
        mark_processed(expose_id, "REJECTED_LLM", reason=llm_reason)
        log_listing_action(expose_id, url, "PASSED", "REJECT", llm_reason, "N/A")
        return {
            "processed": True,
            "approved": False,
            "reason": f"LLM: {llm_reason}",
            "expose_id": expose_id,
        }

    logger.debug(f"[EVAL_PASS] Listing {expose_id} passed LLM evaluation")

    # 4. Score listing on 5 criteria
    logger.info(f"[SCORE] Listing {expose_id}")
    scores = score_listing(full_listing)
    full_listing['scores'] = scores
    logger.info(f"[SCORE_OK] Listing {expose_id}: overall={scores['overall']}★ "
                f"commute={scores['commute']['score']} location={scores['location']['score']} "
                f"size={scores['size']['score']} price={scores['price']['score']} "
                f"avail={scores['availability']['score']}")

    # 5. Draft application (with semaphore to limit concurrency)
    async with semaphore:
        logger.debug(f"[DRAFT] Listing {expose_id}")
        in_flight_listings[expose_id] = {"operation": "draft", "start_time": datetime.now()}

        try:
            message = await asyncio.get_event_loop().run_in_executor(
                None, draft_application, full_listing, profile, template
            )
        finally:
            in_flight_listings.pop(expose_id, None)

    # Check if draft_application returned error info
    draft_result = message if isinstance(message, dict) else {"message": message, "error_type": None}
    message = draft_result.get("message", "")
    draft_error_type = draft_result.get("error_type")

    if draft_error_type:
        if should_retry(expose_id, max_parse_retries=3):
            # Retryable error - don't mark as processed
            track_failure(expose_id, url, draft_error_type, "Draft generation failed")
            logger.warning(f"[DRAFT_RETRY] Listing {expose_id} ({draft_error_type}): will retry next run")
            log_listing_action(expose_id, url, "PASSED", "APPROVE", llm_reason, "N/A", f"Draft failed ({draft_error_type}), will retry")
            return {
                "processed": False,  # Don't mark as processed, retry next run
                "approved": False,
                "reason": f"Draft {draft_error_type}: will retry",
                "expose_id": expose_id,
            }
        else:
            # Non-retryable error or retries exhausted
            track_failure(expose_id, url, draft_error_type, "Draft generation failed")
            failure_info = get_failure_info(expose_id)
            retry_count = failure_info.get('retry_count', 0)
            logger.error(f"[DRAFT_FAIL] Listing {expose_id} ({draft_error_type}, {retry_count} retries): giving up")
            mark_processed(expose_id, "DRAFT_FAILED")
            log_listing_action(expose_id, url, "PASSED", "APPROVE", llm_reason, "FAILED", f"Draft failed after {retry_count} retries")
            return {
                "processed": True,  # Mark as processed to stop retrying
                "approved": False,
                "reason": f"Draft failed after {retry_count} attempts",
                "expose_id": expose_id,
            }

    if not message:
        logger.error(f"[DRAFT_FAIL] Listing {expose_id}: empty response")
        mark_processed(expose_id, "DRAFT_FAILED")
        log_listing_action(expose_id, url, "PASSED", "APPROVE", llm_reason, "FAILED", "Draft generation failed")
        return {
            "processed": True,
            "approved": False,
            "reason": "Failed to draft application",
            "expose_id": expose_id,
        }

    logger.debug(f"[DRAFT_OK] Listing {expose_id} message drafted ({len(message)} chars)")

    # 5.5 Translate application (optional)
    message_translated = message
    if ENABLE_TRANSLATION:
        logger.debug(f"[TRANSLATE] Listing {expose_id}")
        message_translated = await asyncio.get_event_loop().run_in_executor(
            None, translate_text, message, TRANSLATION_TARGET_LANGUAGE
        )
        if message_translated != message:
            logger.debug(f"[TRANSLATE_OK] Listing {expose_id} ({len(message_translated)} chars)")
        else:
            logger.debug(f"[TRANSLATE_SKIP] Listing {expose_id} translation not available")

    # Submit application
    logger.debug(f"[SUBMIT] Listing {expose_id}")
    try:
        # Determine submission method: automatic (playwright) for 3.5+ stars, email fallback otherwise.
        # Only platforms with a browser form-submitter are eligible for auto-submission;
        # others (e.g. immobilie1) always use the email fallback.
        SUBMITTABLE_SOURCES = {'immoscout24', 'wggesucht'}
        source = listing.get('source', 'immoscout24')
        overall_score = scores.get('overall', 0)
        use_automatic_submission = (
            (overall_score >= 3.5) and not FORCE_EMAIL_FALLBACK and source in SUBMITTABLE_SOURCES
        )
        submission_method = "playwright" if use_automatic_submission else "email_fallback"

        if overall_score < 3.5:
            logger.info(f"[SUBMIT_MANUAL] Listing {expose_id}: score {overall_score:.1f}★ < 3.5, using manual fallback")

        page = None
        success = False

        # Prepare listing with translation for email notification
        listing_with_translation = full_listing.copy()
        listing_with_translation['message_translated'] = message_translated

        # Try Playwright submission only for 4+ star listings
        # Use submission_semaphore to serialize submissions and avoid bot detection
        if use_automatic_submission and browser and context:
            try:
                async with submission_semaphore:
                    page = await context.new_page() if hasattr(context, 'new_page') else context
                    source = listing.get('source', 'immoscout24')
                    if source == 'wggesucht':
                        success = await submit_wg_gesucht_application(page, url, message)
                    else:
                        success = await submit_application(page, url, message)
                    if success:
                        submission_method = "playwright"
                        if snapshot_mhtml:
                            save_listing_snapshot(expose_id, snapshot_mhtml)
                        # Send confirmation email for Playwright submission too
                        try:
                            logger.debug(f"[EMAIL] Listing {expose_id} sending confirmation email")
                            send_application_email(listing_with_translation, message, expose_id, submission_status="auto_submitted")
                        except Exception as e:
                            logger.warning(f"Failed to send confirmation email for {expose_id}: {e}")
                        try:
                            notify_auto_submitted(
                                expose_id, url,
                                location=full_listing.get('location', ''),
                                rooms=full_listing.get('rooms', 0),
                                size_sqm=full_listing.get('size_sqm', 0),
                                warmmiete=full_listing.get('warmmiete', 0),
                                overall_score=scores.get('overall', 0),
                                application_text=message,
                            )
                        except Exception as e:
                            logger.warning(f"Failed to send push notification for {expose_id}: {e}")
            except InsufficientImagesError as e:
                logger.warning(f"[REJECT_IMAGES] Listing {expose_id}: {e}")
                mark_processed(expose_id, "REJECTED_IMAGES")
                log_listing_action(expose_id, url, "PASSED", "APPROVE", llm_reason, "REJECTED_IMAGES", str(e))
                return {
                    "processed": True,
                    "approved": True,
                    "submitted": False,
                    "reason": f"Insufficient real images: {e}",
                    "expose_id": expose_id,
                }
            except Exception as e:
                logger.warning(f"Playwright submission failed: {e}, trying fallback")
                success = False

        # Fallback: queue the application and email manual submission instructions
        if not success:
            logger.debug(f"[SUBMIT_FALLBACK] Listing {expose_id}")
            submission_method = "email_fallback"
            if snapshot_mhtml:
                save_listing_snapshot(expose_id, snapshot_mhtml)
            try:
                notify_manual_required(
                    expose_id, url,
                    location=full_listing.get('location', ''),
                    rooms=full_listing.get('rooms', 0),
                    size_sqm=full_listing.get('size_sqm', 0),
                    warmmiete=full_listing.get('warmmiete', 0),
                    overall_score=scores.get('overall', 0),
                    application_text=message,
                )
            except Exception as e:
                logger.warning(f"Failed to send push notification for {expose_id}: {e}")
            success = fallback_apply(url, message, expose_id, listing_with_translation)

        if success:
            logger.info(f"[APPROVE] Listing {expose_id} submitted via {submission_method}")
            mark_processed(
                expose_id, "APPROVE",
                application_text=message,
                submission_method=submission_method,
                submission_result="success"
            )
            log_listing_action(expose_id, url, "PASSED", "APPROVE", llm_reason, submission_method)
            return {
                "processed": True,
                "approved": True,
                "submitted": True,
                "submission_method": submission_method,
                "expose_id": expose_id,
            }
        else:
            logger.warning(f"[SUBMIT_FAIL] Listing {expose_id}")
            mark_processed(
                expose_id, "APPROVE",
                application_text=message,
                submission_method=submission_method,
                submission_result="failed"
            )
            log_listing_action(expose_id, url, "PASSED", "APPROVE", llm_reason, "FAILED", "Submission error")
            return {
                "processed": True,
                "approved": True,
                "submitted": False,
                "reason": "Submission failed",
                "expose_id": expose_id,
            }

    except Exception as e:
        logger.error(f"[ERROR] Listing {expose_id} submission error: {e}")
        mark_processed(
            expose_id,
            "ERROR",
            application_text=message,
            submission_result="failed"
        )
        log_listing_action(expose_id, url, "PASSED", "APPROVE", llm_reason, "ERROR", str(e))
        return {
            "processed": True,
            "approved": True,
            "submitted": False,
            "error": str(e),
            "expose_id": expose_id,
        }


def send_test_email(snapshot_path: str = None) -> bool:
    """
    Send one notification email built from a real saved listing snapshot, so you
    can eyeball the actual email output without running the full pipeline.

    Uses the production code: the real page_scraper extractor builds the listing
    from saved HTML, and the real send_application_email renders + sends it. Only
    the LLM-derived bits absent from the page (scores, translation) are mocked.
    Run with:  python main.py --test-email  [optional/path/to/snapshot.mhtml]
    """
    import glob
    import email as emaillib
    from bs4 import BeautifulSoup
    from page_scraper import extract_listing_from_soup

    if not snapshot_path:
        snapshots = glob.glob("outputs/submitted/is24_*.mhtml")
        if not snapshots:
            logger.error("No snapshots in outputs/submitted/ to build a test email from")
            return False
        # Lowest expose ID ≈ oldest listing — safe stale data for a test.
        snapshot_path = min(snapshots, key=lambda p: int(Path(p).stem.split('_')[1]))

    logger.info(f"Building test email from snapshot: {snapshot_path}")
    with open(snapshot_path) as f:
        mime = emaillib.message_from_file(f)
    html = next((part.get_payload(decode=True).decode("utf-8", "ignore")
                 for part in mime.walk() if part.get_content_type() == "text/html"), None)
    if not html:
        logger.error(f"Could not extract HTML from snapshot: {snapshot_path}")
        return False

    expose_id = Path(snapshot_path).stem.split('_')[1]
    url = f"https://www.immobilienscout24.de/expose/{expose_id}"
    listing = extract_listing_from_soup(BeautifulSoup(html, "lxml"), url)

    # Mock the fields that come from the LLM, not the page.
    listing['scores'] = {
        'overall': 4.2,
        'commute': {'score': 4.0, 'reason': '[TEST] ~20 min to the office'},
        'location': {'score': 4.5, 'reason': '[TEST] desirable district'},
        'size': {'score': 4.0, 'reason': '[TEST] ample space'},
        'price': {'score': 4.0, 'reason': '[TEST] within budget'},
        'availability': {'score': 4.5, 'reason': '[TEST] available soon'},
    }
    listing['message_translated'] = "[TEST] English translation of the application message."
    message = (
        "[TEST EMAIL] Sehr geehrte Damen und Herren,\n\n"
        "ich interessiere mich sehr für Ihre Wohnung und würde mich über eine "
        "Besichtigung freuen.\n\nMit freundlichen Grüßen"
    )

    logger.info(f"Sending test email for listing {expose_id} ({listing.get('location')})...")
    ok = send_application_email(listing, message, expose_id, submission_status="pending_manual")
    logger.info("Test email sent ✓" if ok else "Test email failed ✗")
    return ok


async def main_loop():
    """Main polling loop."""
    logger.info(f"ImmoCheck started, polling every {POLL_INTERVAL} seconds")
    logger.info(f"Poll interval: {POLL_INTERVAL}s, Max price: €{MAX_WARMMIETE}, Force email fallback: {FORCE_EMAIL_FALLBACK}")
    logger.info(f"LLM concurrency limit: {LLM_CONCURRENCY}")

    # Load profile and template
    try:
        profile = load_text_file(ensure_working_file("templates/renter_profile.txt"))
        template = load_text_file(ensure_working_file("templates/application_template.txt"))
    except Exception as e:
        logger.error(f"Failed to load profile or template: {e}")
        return

    browser = None
    context = None

    # Always try to launch browser for page loading and detail extraction
    # FORCE_EMAIL_FALLBACK only controls the submission method, not page loading
    from config import PLAYWRIGHT_HEADLESS
    logger.info(f"Submission method: {'email fallback (manual)' if FORCE_EMAIL_FALLBACK else 'playwright (automatic)'}")
    logger.info(f"Browser mode: {'headless' if PLAYWRIGHT_HEADLESS else 'visible'}")
    try:
        logger.info("Attempting to launch Playwright browser for page loading...")
        browser, context, page = await launch_browser()
        await page.close()
        logger.info("Browser launched successfully for page loading and detail extraction")
    except Exception as e:
        logger.error(f"Browser launch failed: {e}")
        logger.warning("Page loading will not work without browser. Listings cannot be processed.")
        import traceback
        traceback.print_exc()
        browser = None
        context = None
        return

    # Create semaphore for concurrent LLM calls
    semaphore = asyncio.Semaphore(LLM_CONCURRENCY)

    # Create semaphore for serialized page loading (max 1 concurrent page load)
    # This prevents anti-bot detection from opening too many pages simultaneously
    page_semaphore = asyncio.Semaphore(1)

    # Create semaphore for serialized submissions (max 1 concurrent submission)
    # This prevents bot detection from too many concurrent form submissions
    submission_semaphore = asyncio.Semaphore(1)

    # Start heartbeat task
    heartbeat = asyncio.create_task(heartbeat_task(interval=30))

    try:
        while True:
            try:
                logger.info("=" * 60)
                logger.info("Polling for new emails...")

                # Fetch emails
                emails = fetch_unread_emails()
                if not emails:
                    logger.info("No new emails found")

                # Collect all listing processing tasks and email UIDs.
                # queued_ids tracks expose_ids already scheduled this cycle to avoid
                # processing the same listing twice when it appears in both an email
                # and the CAPTCHA retry queue simultaneously.
                processing_tasks = []
                queued_ids: set[str] = set()
                uids_by_folder: dict[str, list[str]] = {}

                if emails:
                    logger.info(f"Found {len(emails)} unread emails")

                    # Process each email
                    for email_msg, source, uid, folder in emails:
                        uids_by_folder.setdefault(folder, []).append(uid)
                        try:
                            listings = parse_alert_email(email_msg, source=source)
                            logger.info(f"Extracted {len(listings)} {source} listings from email")

                            # Queue each listing for processing
                            for listing in listings:
                                eid = extract_listing_id(listing.get('url', ''))
                                if eid and eid in queued_ids:
                                    logger.debug(f"[SKIP_DUP] {eid} already queued this cycle (from email)")
                                    continue
                                queued_ids.add(eid)
                                task = process_listing(listing, profile, template, browser, context, semaphore, page_semaphore, submission_semaphore)
                                processing_tasks.append(task)

                        except Exception as e:
                            logger.error(f"Error parsing {source} email: {e}")

                # Also queue listings stuck on CAPTCHA that are due for another attempt.
                # This is decoupled from emails: once an alert email is marked read it never
                # comes back, but failed_listings.json persists the retry queue across runs.
                captcha_retries = get_captcha_retry_listings()
                if captcha_retries:
                    logger.info(f"[CAPTCHA_QUEUE] Re-queuing {len(captcha_retries)} listing(s) stuck on CAPTCHA")
                    for retry in captcha_retries:
                        if retry['expose_id'] in queued_ids:
                            # Fresh email arrived for this listing; it will be processed from
                            # email instead. Clear the failure so retry_count doesn't drift.
                            logger.info(f"[CAPTCHA_SKIP_DUP] {retry['expose_id']} already queued from email this cycle — skipping retry-queue duplicate")
                            continue
                        queued_ids.add(retry['expose_id'])
                        retry_listing = {
                            'url': retry['url'],
                            'source': retry['source'],
                            '_from_retry_queue': True,
                        }
                        logger.info(f"[CAPTCHA_RETRY] {retry['expose_id']} (attempt {retry['retry_count'] + 1}/{MAX_CAPTCHA_RETRIES + 1})")
                        task = process_listing(retry_listing, profile, template, browser, context, semaphore, page_semaphore, submission_semaphore)
                        processing_tasks.append(task)

                # Process all listings concurrently (limited by semaphore)
                if processing_tasks:
                    try:
                        results = await asyncio.gather(*processing_tasks, return_exceptions=True)
                        for i, result in enumerate(results):
                            if isinstance(result, Exception):
                                import traceback
                                logger.error(f"Error processing listing {i}: {result}\n{''.join(traceback.format_exception(type(result), result, result.__traceback__))}")
                    except Exception as e:
                        logger.error(f"Error in concurrent processing: {e}")

                # Mark fetched emails as read now that processing is complete
                if uids_by_folder:
                    mark_emails_read(uids_by_folder)

                if emails or captcha_retries:
                    stats_after = get_statistics()
                    logger.info(
                        f"Poll complete. Total: {stats_after['total_processed']} "
                        f"(Approved: {stats_after['approved']}, Rejected: {stats_after['rejected']})"
                    )

                # Sleep before next poll
                logger.info(f"Sleeping for {POLL_INTERVAL} seconds...")
                if _poll_handler:
                    _poll_handler.flush_poll()
                await asyncio.sleep(POLL_INTERVAL)

            except KeyboardInterrupt:
                logger.info("Received interrupt signal, exiting...")
                if _poll_handler:
                    _poll_handler.flush_poll()
                break
            except Exception as e:
                logger.error(f"Error in main loop: {e}")
                logger.info(f"Continuing after error, sleeping {POLL_INTERVAL} seconds...")
                if _poll_handler:
                    _poll_handler.flush_poll()
                await asyncio.sleep(POLL_INTERVAL)

    finally:
        # Cancel heartbeat task
        heartbeat.cancel()
        try:
            await heartbeat
        except asyncio.CancelledError:
            pass

        if browser:
            try:
                # Save browser session (cookies/login) for next run
                if context:
                    await save_browser_storage_state(context)
                await browser.close()
                logger.info("Browser closed")
            except Exception as e:
                logger.warning(f"Error closing browser: {e}")

        logger.info("ImmoCheck stopped")
        final_stats = get_statistics()
        logger.info(f"Final statistics: {final_stats}")
        if _poll_handler:
            _poll_handler.flush_poll()


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="ImmoCheck apartment application bot")
    parser.add_argument(
        "--test-email", nargs="?", const=True, default=False, metavar="SNAPSHOT",
        help="Send one test notification email from a saved listing snapshot "
             "(optionally a specific .mhtml path), then exit. Useful for checking email output.",
    )
    args = parser.parse_args()

    try:
        validate_config()
        if args.test_email:
            snapshot = args.test_email if isinstance(args.test_email, str) else None
            sys.exit(0 if send_test_email(snapshot) else 1)
        asyncio.run(main_loop())
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
        sys.exit(0)
    except Exception as e:
        logger.error(f"Fatal error: {e}")
        sys.exit(1)
