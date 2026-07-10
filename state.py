import json
import logging
from pathlib import Path
from datetime import datetime, timedelta
import re

logger = logging.getLogger(__name__)

STATE_FILE = "data/processed_listings.json"
FAILURES_FILE = "data/failed_listings.json"

# CAPTCHA retry policy: hit IS24 a handful of times, spaced out, before giving up.
MAX_CAPTCHA_RETRIES = 5
CAPTCHA_RETRY_INTERVAL_MINUTES = 60


def _load_state() -> dict:
    """Load processed listings from state file."""
    try:
        if Path(STATE_FILE).exists():
            with open(STATE_FILE, 'r') as f:
                return json.load(f)
    except Exception as e:
        logger.warning(f"Could not load state file: {e}")
    return {"processed": {}}


def _save_state(state: dict) -> bool:
    """Save processed listings to state file."""
    try:
        with open(STATE_FILE, 'w') as f:
            json.dump(state, f, indent=2)
        return True
    except Exception as e:
        logger.error(f"Failed to save state file: {e}")
        return False


def extract_listing_id(url: str) -> str:
    """
    Extract stable deduplication ID from listing URL (platform-agnostic).
    ImmoScout24: https://www.immobilienscout24.de/expose/123456 → is24_123456
    WG-Gesucht: https://www.wg-gesucht.de/12443563.html → wgg_12443563
    immobilie1: https://www.immobilie1.de/expose/32712915 OR
                https://www.immobilie1.de/80337-munchen-...-32712915 → imo1_32712915
    """
    # immobilie1 must be checked before the generic /expose/ pattern, since
    # immobilie1 also serves /expose/<id> URLs (host disambiguates it from IS24).
    if 'immobilie1.de' in url:
        match = re.search(r'/expose/(\d+)', url) or re.search(r'-(\d+)(?:[/?#]|$)', url)
        if match:
            return f"imo1_{match.group(1)}"
    # ImmoScout24
    match = re.search(r'/expose/(\d+)', url)
    if match:
        return f"is24_{match.group(1)}"
    # WG-Gesucht
    match = re.search(r'/(\d+)\.html', url)
    if match:
        return f"wgg_{match.group(1)}"
    return ""


def extract_expose_id(url: str) -> str:
    """Extract expose ID from ImmoScout24 URL (backward compatibility)."""
    listing_id = extract_listing_id(url)
    if listing_id.startswith('is24_'):
        return listing_id[5:]  # strip 'is24_' prefix
    return ""


def is_processed(expose_id: str) -> bool:
    """Check if a listing has already been processed (backward compatible with both formats)."""
    if not expose_id:
        return False

    state = _load_state()
    processed = state.get("processed", {})

    # Try exact match first (new format with platform prefix)
    if expose_id in processed:
        return True

    # For backward compatibility, try without prefix (old IS24 entries)
    # e.g., if expose_id is "is24_123456", also check "123456"
    if expose_id.startswith('is24_'):
        bare_id = expose_id[5:]
        if bare_id in processed:
            return True

    return False


def mark_processed(
    expose_id: str,
    decision: str,
    timestamp: str = None,
    application_text: str = None,
    submission_method: str = None,
    submission_result: str = None,
    reason: str = None,
) -> bool:
    """Mark a listing as processed with decision, timestamp, and submission details."""
    if not expose_id:
        return False

    state = _load_state()
    if "processed" not in state:
        state["processed"] = {}

    entry = {
        "decision": decision,
        "timestamp": timestamp or datetime.now().isoformat(),
    }

    if application_text is not None:
        entry["application_text"] = application_text

    if submission_method is not None:
        entry["submission_method"] = submission_method

    if submission_result is not None:
        entry["submission_result"] = submission_result

    if reason is not None:
        entry["reason"] = reason

    # Prepend new entry so latest listings appear first in the file
    new_processed = {expose_id: entry}
    new_processed.update(state["processed"])
    state["processed"] = new_processed

    _save_state(state)

    # Remove from failures — no point retrying something that's been handled.
    clear_failure(expose_id)

    return True


def get_statistics() -> dict:
    """Get statistics about processed listings."""
    state = _load_state()
    processed = state.get("processed", {})

    approved_count = sum(1 for item in processed.values() if item.get("decision") == "APPROVE")
    rejected_count = sum(1 for item in processed.values() if item.get("decision") == "REJECT")

    playwright_count = sum(1 for item in processed.values() if item.get("submission_method") == "playwright")
    email_fallback_count = sum(1 for item in processed.values() if item.get("submission_method") == "email_fallback")

    submitted_count = sum(1 for item in processed.values() if item.get("decision") == "APPROVE" and item.get("submission_method"))
    success_count = sum(1 for item in processed.values() if item.get("submission_result") == "success")
    failed_count = sum(1 for item in processed.values() if item.get("submission_result") == "failed")

    return {
        "total_processed": len(processed),
        "approved": approved_count,
        "rejected": rejected_count,
        "submitted": submitted_count,
        "submission_methods": {
            "playwright": playwright_count,
            "email_fallback": email_fallback_count,
        },
        "submission_results": {
            "success": success_count,
            "failed": failed_count,
        },
        "success_rate": f"{(success_count / submitted_count * 100):.1f}%" if submitted_count > 0 else "N/A",
    }


def clear_state() -> bool:
    """Clear all processed listings (use with caution)."""
    return _save_state({"processed": {}})


def _load_failures() -> dict:
    """Load failed listings from failures file."""
    try:
        if Path(FAILURES_FILE).exists():
            with open(FAILURES_FILE, 'r') as f:
                return json.load(f)
    except Exception as e:
        logger.warning(f"Could not load failures file: {e}")
    return {"failures": {}}


def _save_failures(failures: dict) -> bool:
    """Save failed listings to failures file."""
    try:
        Path(FAILURES_FILE).parent.mkdir(parents=True, exist_ok=True)
        with open(FAILURES_FILE, 'w') as f:
            json.dump(failures, f, indent=2)
        return True
    except Exception as e:
        logger.error(f"Failed to save failures file: {e}")
        return False


def track_failure(expose_id: str, url: str, error_type: str, error_msg: str) -> bool:
    """
    Track a listing failure.
    error_type: 'api_error' (always retry), 'parse_error' (retry up to 3 times), etc.
    Returns True if saved successfully.
    """
    if not expose_id:
        return False

    failures = _load_failures()
    if "failures" not in failures:
        failures["failures"] = {}

    is_new = expose_id not in failures["failures"]
    if is_new:
        failures["failures"][expose_id] = {
            "url": url,
            "retry_count": 0,
            "history": [],
        }

    entry = failures["failures"][expose_id]
    entry["last_error_type"] = error_type
    entry["last_error_msg"] = error_msg[:200]  # Truncate long messages
    entry["last_attempt"] = datetime.now().isoformat()
    entry["retry_count"] = entry.get("retry_count", 0) + 1

    entry["history"].append({
        "timestamp": datetime.now().isoformat(),
        "error_type": error_type,
        "msg": error_msg[:200],
    })

    # Prepend new failures so latest appear first; existing entries stay in place
    if is_new:
        new_failures = {expose_id: entry}
        new_failures.update(failures["failures"])
        failures["failures"] = new_failures

    return _save_failures(failures)


def should_retry(expose_id: str, max_parse_retries: int = 3) -> bool:
    """
    Check if a failed listing should be retried.
    - API errors: always retry (forever)
    - Parse errors: retry up to max_parse_retries times (e.g., 3 = 1 original + 3 retries = 4 attempts total)
    Returns True if should retry, False if retries exhausted.
    """
    failures = _load_failures()
    entry = failures["failures"].get(expose_id)

    if not entry:
        return False

    error_type = entry.get("last_error_type", "unknown")
    retry_count = entry.get("retry_count", 0)

    if error_type == "api_error":
        # Always retry API errors (network issues, rate limits, etc.)
        return True
    elif error_type == "parse_error":
        # Retry parse errors up to max_parse_retries times (3 = 3 retries after original attempt)
        return retry_count <= max_parse_retries
    elif error_type == "captcha":
        # CAPTCHA retries are time-spaced and capped; see get_captcha_retry_listings().
        return retry_count <= MAX_CAPTCHA_RETRIES
    else:
        # Unknown error type - don't retry
        return False


def get_captcha_retry_listings() -> list[dict]:
    """
    Return listings stuck on CAPTCHA that are due for another attempt.

    A listing is eligible if:
      - last_error_type == 'captcha'
      - retry_count <= MAX_CAPTCHA_RETRIES
      - At least CAPTCHA_RETRY_INTERVAL_MINUTES has passed since last_attempt

    Each entry is a dict with keys: expose_id, url, source, retry_count, last_attempt.
    Decoupled from email state so listings keep getting retried even after their
    triggering alert email has been marked read.
    """
    failures = _load_failures()
    now = datetime.now()
    eligible: list[dict] = []

    for expose_id, entry in failures.get("failures", {}).items():
        if entry.get("last_error_type") != "captcha":
            continue
        if entry.get("retry_count", 0) > MAX_CAPTCHA_RETRIES:
            continue

        last_attempt_str = entry.get("last_attempt")
        if last_attempt_str:
            try:
                last_attempt = datetime.fromisoformat(last_attempt_str)
                if now - last_attempt < timedelta(minutes=CAPTCHA_RETRY_INTERVAL_MINUTES):
                    continue
            except ValueError:
                pass  # malformed timestamp — fall through and retry

        # Derive source from the listing-ID prefix written by extract_listing_id().
        if expose_id.startswith("wgg_"):
            source = "wggesucht"
        elif expose_id.startswith("imo1_"):
            source = "immobilie1"
        else:
            source = "immoscout24"

        eligible.append({
            "expose_id": expose_id,
            "url": entry.get("url", ""),
            "source": source,
            "retry_count": entry.get("retry_count", 0),
            "last_attempt": last_attempt_str,
        })

    return eligible


def captcha_retries_exhausted(expose_id: str) -> bool:
    """True if this listing has hit its CAPTCHA retry cap."""
    entry = _load_failures().get("failures", {}).get(expose_id, {})
    if entry.get("last_error_type") != "captcha":
        return False
    return entry.get("retry_count", 0) > MAX_CAPTCHA_RETRIES


def clear_failure(expose_id: str) -> bool:
    """Remove a listing from the failures file (marks it as resolved)."""
    if not expose_id:
        return False

    failures = _load_failures()
    if expose_id in failures.get("failures", {}):
        del failures["failures"][expose_id]
        return _save_failures(failures)
    return True


def get_failure_info(expose_id: str) -> dict:
    """Get failure info for a listing."""
    failures = _load_failures()
    return failures["failures"].get(expose_id, {})


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    # Test state management
    print("Testing state management...")

    test_expose_id = "123456789"

    # Initially not processed
    if not is_processed(test_expose_id):
        print(f"✓ {test_expose_id} not yet processed")
    else:
        print(f"✗ {test_expose_id} should not be processed yet")

    # Mark as processed with full details
    if mark_processed(
        test_expose_id,
        "APPROVE",
        application_text="Sehr geehrte Damen und Herren, ich interessiere mich für diese Wohnung...",
        submission_method="playwright",
        submission_result="success",
    ):
        print(f"✓ Marked {test_expose_id} as APPROVE with submission details")
    else:
        print(f"✗ Failed to mark {test_expose_id}")

    # Test another listing with email fallback
    test_expose_id_2 = "987654321"
    if mark_processed(
        test_expose_id_2,
        "APPROVE",
        application_text="Sehr geehrte Damen und Herren, ich bin sehr interessiert...",
        submission_method="email_fallback",
        submission_result="success",
    ):
        print(f"✓ Marked {test_expose_id_2} as APPROVE with email fallback")
    else:
        print(f"✗ Failed to mark {test_expose_id_2}")

    # Test rejected listing (no submission details)
    test_expose_id_3 = "555555555"
    if mark_processed(test_expose_id_3, "REJECT"):
        print(f"✓ Marked {test_expose_id_3} as REJECT")
    else:
        print(f"✗ Failed to mark {test_expose_id_3}")

    # Now should be processed
    if is_processed(test_expose_id):
        print(f"✓ {test_expose_id} is now marked as processed")
    else:
        print(f"✗ {test_expose_id} should be processed")

    # Test URL extraction
    url = "https://www.immobilienscout24.de/expose/987654321"
    extracted_id = extract_expose_id(url)
    if extracted_id == "987654321":
        print(f"✓ Extracted expose ID: {extracted_id}")
    else:
        print(f"✗ Failed to extract expose ID from {url}")

    # Get statistics
    stats = get_statistics()
    print("\nStatistics:")
    for key, value in stats.items():
        print(f"  {key}: {value}")

    # Cleanup
    if clear_state():
        print("\n✓ State cleared")
