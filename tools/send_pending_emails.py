#!/usr/bin/env python3
"""Send emails for all pending applications in the queue."""

import json
import logging
from pathlib import Path
from email_notifications import send_application_email

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

PENDING_APPLICATIONS_FILE = "outputs/pending_applications.jsonl"


def send_pending_emails():
    """Send emails for all pending applications"""

    if not Path(PENDING_APPLICATIONS_FILE).exists():
        logger.error(f"{PENDING_APPLICATIONS_FILE} not found")
        return

    applications = []
    with open(PENDING_APPLICATIONS_FILE, 'r') as f:
        for line in f:
            if line.strip():
                try:
                    applications.append(json.loads(line))
                except json.JSONDecodeError:
                    continue

    if not applications:
        logger.info("No pending applications to send")
        return

    logger.info(f"Sending emails for {len(applications)} application(s)...")

    sent_count = 0
    failed_count = 0

    for app in applications:
        expose_id = app.get('expose_id', '')
        message = app.get('message', '')

        # Reconstruct listing dict from stored data
        listing = {
            'url': app.get('url', ''),
            'landlord_name': app.get('landlord_name', 'Unknown'),
            'rooms': app.get('rooms', 0),
            'size_sqm': app.get('size_sqm', 0),
            'warmmiete': app.get('warmmiete', 0),
            'location': app.get('location', 'Unknown'),
            'property_type': app.get('property_type', 'Wohnung'),
        }

        try:
            if send_application_email(listing, message, expose_id):
                sent_count += 1
                logger.info(f"✓ Sent email for {expose_id}")
            else:
                failed_count += 1
                logger.error(f"✗ Failed to send email for {expose_id}")
        except Exception as e:
            failed_count += 1
            logger.error(f"✗ Error sending email for {expose_id}: {e}")

    logger.info("\nEmail sending complete:")
    logger.info(f"  Sent: {sent_count}")
    logger.info(f"  Failed: {failed_count}")


if __name__ == "__main__":
    send_pending_emails()
