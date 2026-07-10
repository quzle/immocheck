"""Fallback application submission: store to file and notify user via email."""
import logging
import json
from datetime import datetime
from email_notifications import send_application_email

logger = logging.getLogger(__name__)

PENDING_APPLICATIONS_FILE = "outputs/pending_applications.jsonl"


def fallback_apply(url: str, message: str, expose_id: str = None, listing: dict = None) -> bool:
    """
    Fallback: Store application in file and send email notification.
    """
    try:
        # Store application to file for async submission with full listing details
        if expose_id:
            store_pending_application(url, message, expose_id, listing)

        # Send email notification with application details
        if expose_id and listing:
            logger.info(f"Sending application email for {expose_id}...")
            if send_application_email(listing, message, expose_id, submission_status="pending_manual"):
                logger.info(f"Application email sent successfully for {expose_id}")
            else:
                logger.warning(f"Failed to send application email for {expose_id}")

        return True

    except Exception as e:
        logger.error(f"Fallback apply failed: {e}")
        return False


def store_pending_application(url: str, message: str, expose_id: str, listing: dict = None) -> bool:
    """
    Store pending application to JSONL file for async submission with full listing details.
    """
    try:
        entry = {
            "timestamp": datetime.now().isoformat(),
            "expose_id": expose_id,
            "url": url,
            "message": message,
        }

        # Add listing details if available
        if listing:
            if listing.get('warmmiete'):
                entry["warmmiete"] = listing.get('warmmiete')
            if listing.get('size_sqm'):
                entry["size_sqm"] = listing.get('size_sqm')
            if listing.get('location'):
                entry["location"] = listing.get('location')
            if listing.get('rooms'):
                entry["rooms"] = listing.get('rooms')
            if listing.get('landlord_name'):
                entry["landlord_name"] = listing.get('landlord_name')
            if listing.get('property_type'):
                entry["property_type"] = listing.get('property_type')
            if listing.get('message_translated'):
                entry["message_translated"] = listing.get('message_translated')

        with open(PENDING_APPLICATIONS_FILE, 'a') as f:
            f.write(json.dumps(entry, ensure_ascii=False) + '\n')
        logger.info(f"Stored pending application {expose_id} in {PENDING_APPLICATIONS_FILE}")
        return True
    except Exception as e:
        logger.error(f"Failed to store pending application: {e}")
        return False
