import logging
import smtplib
from urllib.parse import quote_plus
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from config import IMAP_USER, IMAP_PASS, NOTIFICATION_RECIPIENTS, MAPS_API_KEY

logger = logging.getLogger(__name__)


def _maps_search_url(location: str) -> str:
    """Google Maps deep link that opens an interactive map for the address."""
    return f"https://www.google.com/maps/search/?api=1&query={quote_plus(location)}"


def _static_map_url(location: str) -> str:
    """Google Static Maps image URL with a marker at the address (retina scale)."""
    enc = quote_plus(location)
    return (
        "https://maps.googleapis.com/maps/api/staticmap"
        f"?center={enc}&zoom=15&size=600x280&scale=2"
        f"&markers=color:0x667eea%7C{enc}&key={MAPS_API_KEY}"
    )


def _map_html(location: str) -> str:
    """Return a clickable static-map table row, or '' when maps are disabled/location unknown."""
    if not MAPS_API_KEY or not location or location == 'Unknown':
        return ''
    return f"""
              <tr>
                <td style="padding:20px 22px 0">
                  <a href="{_maps_search_url(location)}" style="text-decoration:none;color:inherit">
                    <img src="{_static_map_url(location)}" alt="Map of {location}" width="544"
                         style="display:block;width:100%;height:auto;border-radius:10px;border:1px solid #e5e7eb" />
                  </a>
                </td>
              </tr>"""


def _stars(score: float) -> str:
    """Render 0-5 score as Unicode star string (full + half + empty)."""
    full = int(score)
    half = 1 if (score - full) >= 0.25 else 0
    empty = 5 - full - half
    return '★' * full + ('½' if half else '') + '☆' * empty


def send_application_email(listing: dict, message: str, expose_id: str, submission_status: str = "pending_manual") -> bool:
    """
    Send application notification email via Gmail.
    submission_status: "auto_submitted" (Playwright succeeded) or "pending_manual" (needs manual action)
    """
    try:
        # Determine status display based on submission method
        auto_submitted = (submission_status == "auto_submitted")
        if auto_submitted:
            banner_bg, banner_text, banner_msg = "#ecfdf5", "#065f46", "✓&nbsp; Application submitted automatically."
            message_label = "Application message (sent)"
            footer_text = "This application was submitted automatically by ImmoCheck."
        else:
            banner_bg, banner_text, banner_msg = "#fffbeb", "#92400e", "Action required &middot; copy the message below and send it on the listing page."
            message_label = "Application message &middot; copy &amp; paste"
            footer_text = "Review the message below, then submit it manually on the listing page."

        # Extract listing details
        url = listing.get('url', '')
        landlord = listing.get('landlord_name') or 'Unknown'
        rooms = listing.get('rooms', 0)
        size_sqm = listing.get('size_sqm', 0)
        warmmiete = listing.get('warmmiete', 0)
        location = listing.get('location', 'Unknown')
        property_type = listing.get('property_type', 'Wohnung')
        message_translated = listing.get('message_translated', '')
        source_labels = {'immoscout24': 'ImmoScout24', 'wggesucht': 'WG-Gesucht', 'immobilie1': 'immobilie1'}
        source_label = source_labels.get(listing.get('source', 'immoscout24'), 'ImmoScout24')

        # --- Header: source eyebrow, location headline, rent hero ---
        rent_html = (f'<div style="margin-top:14px;font-size:26px;font-weight:700;color:#ffffff">'
                     f'&euro;{warmmiete}<span style="font-size:14px;font-weight:500;color:#9ca3af"> / Monat warm</span></div>'
                     ) if warmmiete else ''
        header_html = f"""
              <tr>
                <td style="background:#111827;padding:22px 22px 24px">
                  <div style="font-size:12px;font-weight:700;letter-spacing:.08em;text-transform:uppercase;color:#818cf8">New match &middot; {source_label}</div>
                  <h1 style="margin:8px 0 0;font-size:22px;line-height:1.3;color:#ffffff;font-weight:700">{location}</h1>
                  {rent_html}
                </td>
              </tr>
              <tr>
                <td style="background:{banner_bg};color:{banner_text};padding:12px 22px;font-size:14px;font-weight:600">{banner_msg}</td>
              </tr>"""

        # --- Facts table ---
        fact_rows = ""
        for label, value in [("Property type", property_type), ("Rooms", f"{rooms} Zimmer"),
                             ("Size", f"{size_sqm} m&sup2;"), ("Landlord", landlord)]:
            fact_rows += (
                f'<tr><td style="padding:8px 0;font-size:14px;color:#6b7280;width:130px">{label}</td>'
                f'<td style="padding:8px 0;font-size:14px;color:#111827;font-weight:600">{value}</td></tr>'
            )
        facts_html = f"""
              <tr>
                <td style="padding:20px 22px 4px">
                  <table role="presentation" width="100%" style="border-collapse:collapse">{fact_rows}</table>
                </td>
              </tr>"""

        # --- Score card ---
        score_html = ""
        scores = listing.get('scores', {})
        if scores:
            overall = scores.get('overall', 0)
            rows_html = ''
            for key, label in [('commute', 'Commute'), ('location', 'Location'), ('size', 'Size'),
                                ('price', 'Price'), ('availability', 'Availability')]:
                s = scores.get(key, {})
                rows_html += f"""
                        <tr>
                          <td style="padding:7px 0;font-size:14px;color:#374151;white-space:nowrap;width:90px">{label}</td>
                          <td style="padding:7px 10px;color:#f59e0b;font-size:15px;white-space:nowrap;letter-spacing:1px">{_stars(s.get('score', 0))}</td>
                          <td style="padding:7px 0;font-size:13px;color:#6b7280">{s.get('reason', '')}</td>
                        </tr>"""
            score_html = f"""
              <tr>
                <td style="padding:20px 22px 0">
                  <table role="presentation" width="100%" style="border-collapse:collapse;background:#f9fafb;border:1px solid #eef0f3;border-radius:10px">
                    <tr><td style="padding:16px 18px">
                      <table role="presentation" width="100%" style="border-collapse:collapse">
                        <tr>
                          <td style="font-size:12px;font-weight:700;letter-spacing:.06em;text-transform:uppercase;color:#6b7280">Match score</td>
                          <td align="right" style="font-size:15px;color:#f59e0b;white-space:nowrap">{_stars(overall)} <span style="color:#111827;font-weight:700">{overall:.1f}</span><span style="color:#9ca3af">/5</span></td>
                        </tr>
                      </table>
                      <table role="presentation" width="100%" style="border-collapse:collapse;margin-top:10px;border-top:1px solid #eef0f3">{rows_html}
                      </table>
                    </td></tr>
                  </table>
                </td>
              </tr>"""

        # --- Optional English translation (subtle, for understanding before copying) ---
        translation_html = ""
        if message_translated and message_translated != message:
            translation_html = f"""
              <tr>
                <td style="padding:20px 22px 0">
                  <div style="font-size:12px;font-weight:700;letter-spacing:.06em;text-transform:uppercase;color:#6b7280;margin-bottom:8px">English translation &middot; for reference</div>
                  <div style="background:#f9fafb;border:1px solid #eef0f3;border-radius:8px;padding:16px;font-size:14px;line-height:1.65;color:#6b7280;white-space:pre-wrap;word-wrap:break-word">{message_translated}</div>
                </td>
              </tr>"""

        # --- German application message: the thing the user copies. Isolated and
        # prominent, sized for mobile readability + easy long-press selection. ---
        copy_html = f"""
              <tr>
                <td style="padding:20px 22px 0">
                  <table role="presentation" width="100%" style="border-collapse:collapse;border:1px solid #c7d2fe;border-radius:12px;overflow:hidden">
                    <tr><td style="background:#eef2ff;padding:12px 16px">
                      <table role="presentation" width="100%" style="border-collapse:collapse">
                        <tr>
                          <td style="font-size:12px;font-weight:700;letter-spacing:.05em;text-transform:uppercase;color:#4338ca">{message_label}</td>
                          <td align="right" style="font-size:11px;color:#6366f1;white-space:nowrap">long-press to select &amp; copy</td>
                        </tr>
                      </table>
                    </td></tr>
                    <tr><td style="background:#ffffff;padding:16px;font-size:15px;line-height:1.7;color:#111827;white-space:pre-wrap;word-wrap:break-word">{message}</td></tr>
                  </table>
                </td>
              </tr>"""
        message_html = translation_html + copy_html

        # --- CTA + footer ---
        cta_footer_html = f"""
              <tr>
                <td style="padding:22px 22px 4px">
                  <a href="{url}" style="display:block;background:#4f46e5;color:#ffffff;text-decoration:none;text-align:center;font-weight:600;font-size:15px;padding:14px;border-radius:8px">View listing on {source_label} &rarr;</a>
                </td>
              </tr>
              <tr>
                <td style="padding:22px 22px 26px">
                  <div style="border-top:1px solid #eef0f3;padding-top:18px;font-size:13px;color:#6b7280;line-height:1.5">{footer_text}</div>
                  <div style="font-size:12px;color:#9ca3af;margin-top:8px">ImmoCheck &middot; your apartment-hunting assistant &middot; Expos&eacute; {expose_id}</div>
                </td>
              </tr>"""

        # --- Assemble ---
        html_content = f"""<!DOCTYPE html>
        <html>
          <head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
          <body style="margin:0;padding:0;background:#f4f5f7;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Arial,sans-serif">
            <table role="presentation" width="100%" style="border-collapse:collapse;background:#f4f5f7">
              <tr><td align="center" style="padding:24px 12px">
                <table role="presentation" width="600" style="width:600px;max-width:100%;border-collapse:collapse;background:#ffffff;border:1px solid #e5e7eb;border-radius:14px;overflow:hidden">
                  {header_html}
                  {_map_html(location)}
                  {facts_html}
                  {score_html}
                  {message_html}
                  {cta_footer_html}
                </table>
              </td></tr>
            </table>
          </body>
        </html>"""

        # Create plain text version as fallback
        text_content = f"""
Application Ready for Submission
{'='*50}

Landlord: {landlord}
Rooms: {rooms} Zimmer
Size: {size_sqm} m²
Rent: €{warmmiete}/month
Location: {location}{(chr(10) + 'Map: ' + _maps_search_url(location)) if (MAPS_API_KEY and location and location != 'Unknown') else ''}
Listing: {url}

{'='*50}
SCORE SUMMARY
{'='*50}
"""

        scores = listing.get('scores', {})
        if scores:
            text_content += f"""
Commute:      {_stars(scores['commute']['score'])} ({scores['commute']['score']:.1f}/5) — {scores['commute']['reason']}
Location:     {_stars(scores['location']['score'])} ({scores['location']['score']:.1f}/5) — {scores['location']['reason']}
Size:         {_stars(scores['size']['score'])} ({scores['size']['score']:.1f}/5) — {scores['size']['reason']}
Price:        {_stars(scores['price']['score'])} ({scores['price']['score']:.1f}/5) — {scores['price']['reason']}
Availability: {_stars(scores['availability']['score'])} ({scores['availability']['score']:.1f}/5) — {scores['availability']['reason']}
──────────────────────────────
OVERALL:      {_stars(scores['overall'])} ({scores['overall']:.1f}/5)
"""

        text_label = "APPLICATION MESSAGE (SENT)" if auto_submitted else "APPLICATION MESSAGE — COPY & PASTE"
        text_content += f"""
{'='*50}
{text_label}:
{'='*50}

{message}
"""

        if message_translated and message_translated != message:
            text_content += f"""
{'='*50}
APPLICATION MESSAGE - ENGLISH (for reference):
{'='*50}

{message_translated}
"""

        text_content += f"""
{'='*50}
ImmoCheck - Your personal apartment hunting assistant
{footer_text}
"""

        # Send email via Gmail
        sender_email = IMAP_USER
        sender_password = IMAP_PASS
        recipients = NOTIFICATION_RECIPIENTS if NOTIFICATION_RECIPIENTS else [IMAP_USER]

        # Create message
        msg = MIMEMultipart('alternative')
        scores = listing.get('scores', {})
        overall = scores.get('overall', 0)
        star_str = f"{overall:.1f}★ " if overall else ""
        status_prefix = "SENT" if auto_submitted else "TODO"
        msg['Subject'] = f"ImmoCheck [{status_prefix}] {star_str}{location} - {landlord} ({rooms}Z, {size_sqm}m²)"
        msg['From'] = sender_email
        msg['To'] = ', '.join(recipients)

        # Attach plain text and HTML versions
        part1 = MIMEText(text_content, 'plain')
        part2 = MIMEText(html_content, 'html')
        msg.attach(part1)
        msg.attach(part2)

        # Send via Gmail SMTP
        with smtplib.SMTP_SSL('smtp.gmail.com', 465) as server:
            server.login(sender_email, sender_password)
            server.sendmail(sender_email, recipients, msg.as_string())

        logger.info(f"Application email sent for listing {expose_id} to {', '.join(recipients)}")
        return True

    except smtplib.SMTPAuthenticationError:
        logger.error("Gmail authentication failed. Check IMAP_EMAIL and IMAP_PASSWORD (use app password if 2FA is enabled)")
        return False
    except Exception as e:
        logger.error(f"Failed to send application email: {e}")
        return False


def send_captcha_failure_email(expose_id: str, url: str, retry_count: int) -> bool:
    """
    Notify the user that a listing could not be processed because IS24 kept showing
    a CAPTCHA across every retry. The listing needs manual review so it isn't lost.
    """
    try:
        sender_email = IMAP_USER
        sender_password = IMAP_PASS
        recipients = NOTIFICATION_RECIPIENTS if NOTIFICATION_RECIPIENTS else [IMAP_USER]

        html_content = f"""
        <html>
          <body style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Arial,sans-serif;line-height:1.6;color:#333">
            <div style="max-width:600px;margin:0 auto;padding:20px">
              <div style="background:#dc2626;color:white;padding:14px 20px;border-radius:8px;font-weight:700;font-size:15px;margin-bottom:20px;text-align:center">
                ⚠️ CAPTCHA Block — Manual Review Required
              </div>
              <p>ImmoCheck could not load this listing after <strong>{retry_count}</strong> retries because ImmoScout24 kept presenting a CAPTCHA challenge.</p>
              <p>The listing has been removed from the automated retry queue. Please open it manually to check if it's worth applying to.</p>
              <div style="background:#f9f9f9;padding:16px;border:1px solid #eee;border-radius:6px;margin:20px 0">
                <div style="margin-bottom:8px"><strong>Expose ID:</strong> {expose_id}</div>
                <div><strong>URL:</strong> <a href="{url}">{url}</a></div>
              </div>
              <div style="text-align:center;margin-top:24px">
                <a href="{url}" style="display:inline-block;padding:12px 24px;background:#667eea;color:white;text-decoration:none;border-radius:6px;font-weight:600">Open Listing</a>
              </div>
              <div style="text-align:center;font-size:12px;color:#999;margin-top:24px;padding-top:16px;border-top:1px solid #eee">
                ImmoCheck — Your personal apartment hunting assistant
              </div>
            </div>
          </body>
        </html>
        """

        text_content = (
            f"CAPTCHA Block — Manual Review Required\n"
            f"{'='*50}\n\n"
            f"ImmoCheck could not load this listing after {retry_count} retries because\n"
            f"ImmoScout24 kept presenting a CAPTCHA challenge.\n\n"
            f"The listing has been removed from the automated retry queue. Please open\n"
            f"it manually to check if it's worth applying to.\n\n"
            f"Expose ID: {expose_id}\n"
            f"URL: {url}\n"
        )

        msg = MIMEMultipart('alternative')
        msg['Subject'] = f"ImmoCheck [CAPTCHA] Manual review needed — {expose_id}"
        msg['From'] = sender_email
        msg['To'] = ', '.join(recipients)
        msg.attach(MIMEText(text_content, 'plain'))
        msg.attach(MIMEText(html_content, 'html'))

        with smtplib.SMTP_SSL('smtp.gmail.com', 465) as server:
            server.login(sender_email, sender_password)
            server.sendmail(sender_email, recipients, msg.as_string())

        logger.info(f"CAPTCHA failure email sent for listing {expose_id} to {', '.join(recipients)}")
        return True

    except smtplib.SMTPAuthenticationError:
        logger.error("Gmail authentication failed. Check IMAP_EMAIL and IMAP_PASSWORD (use app password if 2FA is enabled)")
        return False
    except Exception as e:
        logger.error(f"Failed to send CAPTCHA failure email: {e}")
        return False
