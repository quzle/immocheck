import logging
import requests
from config import NTFY_TOPIC, NTFY_SERVER

logger = logging.getLogger(__name__)


def _send(title: str, body: str, priority: str, tags: list[str],
          listing_url: str | None = None) -> bool:
    """POST a notification to the ntfy topic. Returns True on success."""
    if not NTFY_TOPIC:
        return False

    headers = {
        "Title": title,
        "Priority": priority,
        "Tags": ",".join(tags),
    }
    if listing_url:
        headers["Actions"] = f"view, Open listing, {listing_url}"

    try:
        resp = requests.post(
            f"{NTFY_SERVER}/{NTFY_TOPIC}",
            data=body.encode("utf-8"),
            headers=headers,
            timeout=10,
        )
        resp.raise_for_status()
        logger.debug(f"[NTFY] Notification sent: {title!r}")
        return True
    except Exception as e:
        logger.warning(f"[NTFY] Failed to send notification: {e}")
        return False


def notify_auto_submitted(expose_id: str, url: str, location: str, rooms: float,
                          size_sqm: float, warmmiete: float, overall_score: float,
                          application_text: str = "") -> bool:
    score_str = f"{overall_score:.1f}/5 " if overall_score else ""
    title = f"Applied: {score_str}{location} - {rooms}Z, {size_sqm}m2, EUR{warmmiete:.0f}/mo"
    # Body is the application text only — tap to copy gives clean text
    body = application_text[:4096] if application_text else "Application sent automatically."
    return _send(title, body, priority="default", tags=["white_check_mark", "house"], listing_url=url)


def notify_manual_required(expose_id: str, url: str, location: str, rooms: float,
                           size_sqm: float, warmmiete: float, overall_score: float,
                           application_text: str = "") -> bool:
    score_str = f"{overall_score:.1f}/5 " if overall_score else ""
    title = f"Apply now: {score_str}{location} - {rooms}Z, {size_sqm}m2, EUR{warmmiete:.0f}/mo"
    body = application_text[:4096] if application_text else "Open listing to apply manually."
    return _send(title, body, priority="high", tags=["rotating_light", "house"], listing_url=url)


def notify_captcha_failed(expose_id: str, url: str) -> bool:
    title = "CAPTCHA block - manual review needed"
    body = f"Listing {expose_id} could not be loaded after all retries. Open to check manually."
    return _send(title, body, priority="high", tags=["no_entry", "house"], listing_url=url)
