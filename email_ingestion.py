import imaplib
import email
from email import policy
from email.parser import BytesParser
import ssl
import logging
import config

logger = logging.getLogger(__name__)

def fetch_unread_emails() -> list[tuple[email.message.Message, str, str, str]]:
    """
    Connects to Gmail via IMAP, fetches unread emails from configured folders,
    and returns a list of (parsed email message, source platform, IMAP UID, folder name) tuples.
    Supports ImmoScout24, WG-Gesucht, and immobilie1 alert folders.
    Returns: list of (email.message.Message, str, str, str) where:
      - email.message.Message: parsed email
      - str (source): 'immoscout24', 'wggesucht', or 'immobilie1'
      - str (uid): IMAP message UID
      - str (folder): folder name
    """
    logger.info(f"Connecting to {config.IMAP_HOST}...")

    # Define folders to check
    folders_to_check = [
        (config.IMAP_FOLDER, 'immoscout24'),
    ]
    if config.IMAP_WGGESUCHT_FOLDER:
        folders_to_check.append((config.IMAP_WGGESUCHT_FOLDER, 'wggesucht'))
    if config.IMAP_IMMOBILIE1_FOLDER:
        folders_to_check.append((config.IMAP_IMMOBILIE1_FOLDER, 'immobilie1'))

    # Create a secure SSL context
    context = ssl.create_default_context()

    try:
        # Connect to the server
        mail = imaplib.IMAP4_SSL(config.IMAP_HOST, 993, ssl_context=context)

        # Login
        mail.login(config.IMAP_USER, config.IMAP_PASS)

        emails = []

        for folder_name, source in folders_to_check:
            # Select the folder
            status, data = mail.select(folder_name)
            if status != 'OK':
                logger.warning(f"Could not select folder {folder_name}: {data}")
                continue

            # Search for UNSEEN messages
            status, messages = mail.search(None, 'UNSEEN')
            if status != 'OK':
                logger.warning(f"Error searching for UNSEEN messages in {folder_name}: {messages}")
                continue

            message_ids = messages[0].split()
            logger.info(f"Found {len(message_ids)} unread email(s) in {folder_name} ({source}).")

            for num in message_ids:
                # Decode message ID if it's bytes
                if isinstance(num, bytes):
                    num = num.decode('utf-8')

                # Fetch the email data
                status, data = mail.fetch(num, '(RFC822)')
                if status != 'OK':
                    logger.warning(f"Failed to fetch message ID {num} from {folder_name}")
                    continue

                # Parse the email content
                msg = BytesParser(policy=policy.default).parsebytes(data[0][1])
                emails.append((msg, source, num, folder_name))

        mail.logout()
        return emails

    except Exception as e:
        logger.exception(f"An error occurred during email fetching: {e}")
        return []

def mark_emails_read(uids_by_folder: dict[str, list[str]]) -> None:
    """Open a fresh IMAP connection and mark the given message UIDs as Seen."""
    if not uids_by_folder:
        return
    try:
        context = ssl.create_default_context()
        mail = imaplib.IMAP4_SSL(config.IMAP_HOST, 993, ssl_context=context)
        mail.login(config.IMAP_USER, config.IMAP_PASS)
        for folder, uids in uids_by_folder.items():
            mail.select(folder)
            for uid in uids:
                mail.store(uid, '+FLAGS', '\\Seen')
            logger.info(f"Marked {len(uids)} email(s) as read in {folder}")
        mail.logout()
    except Exception as e:
        logger.warning(f"Failed to mark emails as read: {e}")


if __name__ == "__main__":
    # Test block
    unread_emails = fetch_unread_emails()
    if unread_emails:
        print("\nUnread alerts found:")
        for i, (msg, source, uid, folder) in enumerate(unread_emails, 1):
            print(f"{i}. Subject: {msg['subject']} (UID: {uid}, Folder: {folder})")
    else:
        print("\nNo unread alerts found.")
