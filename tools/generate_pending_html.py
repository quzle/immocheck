#!/usr/bin/env python3
"""Generate HTML dashboard from pending applications queue."""

import json
from pathlib import Path
from datetime import datetime

PENDING_APPLICATIONS_FILE = "outputs/pending_applications.jsonl"
OUTPUT_HTML_FILE = "outputs/pending_applications.html"


def generate_html():
    """Generate HTML from pending_applications.jsonl"""

    # Read all applications
    applications = []
    if Path(PENDING_APPLICATIONS_FILE).exists():
        with open(PENDING_APPLICATIONS_FILE, 'r') as f:
            for line in f:
                if line.strip():
                    try:
                        applications.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue

    # Generate HTML
    html = """<!DOCTYPE html>
<html lang="de">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Pending Applications</title>
    <style>
        * {
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }

        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Oxygen, Ubuntu, Cantarell, sans-serif;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            padding: 20px;
            min-height: 100vh;
        }

        .container {
            max-width: 1200px;
            margin: 0 auto;
        }

        h1 {
            color: white;
            margin-bottom: 10px;
            text-align: center;
        }

        .summary {
            color: rgba(255,255,255,0.9);
            text-align: center;
            margin-bottom: 30px;
            font-size: 14px;
        }

        .applications-grid {
            display: grid;
            grid-template-columns: repeat(auto-fill, minmax(500px, 1fr));
            gap: 20px;
        }

        .application-card {
            background: white;
            border-radius: 8px;
            box-shadow: 0 4px 6px rgba(0,0,0,0.1);
            overflow: hidden;
            transition: transform 0.2s, box-shadow 0.2s;
        }

        .application-card:hover {
            transform: translateY(-2px);
            box-shadow: 0 8px 12px rgba(0,0,0,0.15);
        }

        .card-header {
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            padding: 15px;
            border-bottom: 1px solid rgba(0,0,0,0.1);
        }

        .card-title {
            font-size: 18px;
            font-weight: 600;
            margin-bottom: 5px;
        }

        .card-meta {
            font-size: 12px;
            opacity: 0.9;
        }

        .card-body {
            padding: 15px;
        }

        .listing-info {
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 10px;
            margin-bottom: 15px;
            font-size: 13px;
        }

        .info-item {
            background: #f5f5f5;
            padding: 8px;
            border-radius: 4px;
        }

        .info-label {
            font-weight: 600;
            color: #667eea;
            font-size: 11px;
            text-transform: uppercase;
        }

        .info-value {
            margin-top: 4px;
            color: #333;
        }

        .message-section {
            margin-top: 15px;
            border-top: 1px solid #eee;
            padding-top: 15px;
        }

        .message-label {
            font-weight: 600;
            color: #667eea;
            font-size: 11px;
            text-transform: uppercase;
            margin-bottom: 10px;
        }

        .message-content {
            background: #f9f9f9;
            padding: 12px;
            border-radius: 4px;
            font-size: 13px;
            line-height: 1.6;
            color: #333;
            white-space: pre-wrap;
            word-wrap: break-word;
            border-left: 3px solid #667eea;
            max-height: 300px;
            overflow-y: auto;
        }

        .card-footer {
            padding: 12px 15px;
            background: #f9f9f9;
            border-top: 1px solid #eee;
            display: flex;
            gap: 10px;
        }

        button {
            flex: 1;
            padding: 8px 12px;
            border: none;
            border-radius: 4px;
            font-size: 12px;
            font-weight: 600;
            cursor: pointer;
            transition: all 0.2s;
            text-transform: uppercase;
        }

        .btn-copy {
            background: #667eea;
            color: white;
        }

        .btn-copy:hover {
            background: #5568d3;
        }

        .btn-open {
            background: #48bb78;
            color: white;
        }

        .btn-open:hover {
            background: #38a169;
        }

        .empty-state {
            text-align: center;
            color: white;
            padding: 60px 20px;
        }

        .empty-state h2 {
            font-size: 24px;
            margin-bottom: 10px;
        }

        .timestamp {
            font-size: 11px;
            color: #999;
            margin-top: 5px;
        }

        @media (max-width: 768px) {
            .applications-grid {
                grid-template-columns: 1fr;
            }
        }
    </style>
</head>
<body>
    <div class="container">
        <h1>📋 Pending Applications</h1>
"""

    if applications:
        html += f'        <div class="summary">{len(applications)} application{"s" if len(applications) != 1 else ""} waiting to be submitted</div>\n'
        html += '        <div class="applications-grid">\n'

        for i, app in enumerate(applications, 1):
            timestamp = app.get('timestamp', '')
            url = app.get('url', '')
            message = app.get('message', '')
            landlord = app.get('landlord_name', 'Unknown')
            rooms = app.get('rooms', 0)
            size = app.get('size_sqm', 0)
            warmmiete = app.get('warmmiete', 0)
            location = app.get('location', 'Unknown')
            property_type = app.get('property_type', 'Wohnung')

            # Format timestamp
            try:
                dt = datetime.fromisoformat(timestamp)
                timestamp_str = dt.strftime('%d.%m.%Y %H:%M')
            except:
                timestamp_str = timestamp

            html += f'''            <div class="application-card">
                <div class="card-header">
                    <div class="card-title">{landlord}</div>
                    <div class="card-meta">
                        {rooms}Z • {size}m² • €{warmmiete} • {location}
                        <div class="timestamp">{timestamp_str}</div>
                    </div>
                </div>
                <div class="card-body">
                    <div class="listing-info">
                        <div class="info-item">
                            <div class="info-label">Rooms</div>
                            <div class="info-value">{rooms} Zimmer</div>
                        </div>
                        <div class="info-item">
                            <div class="info-label">Size</div>
                            <div class="info-value">{size} m²</div>
                        </div>
                        <div class="info-item">
                            <div class="info-label">Rent</div>
                            <div class="info-value">€{warmmiete}/month</div>
                        </div>
                        <div class="info-item">
                            <div class="info-label">Type</div>
                            <div class="info-value">{property_type}</div>
                        </div>
                    </div>
                    <div class="listing-info">
                        <div class="info-item" style="grid-column: 1 / -1;">
                            <div class="info-label">Location</div>
                            <div class="info-value">{location}</div>
                        </div>
                    </div>
                    <div class="message-section">
                        <div class="message-label">Application Message</div>
                        <div class="message-content">{message}</div>
                    </div>
                </div>
                <div class="card-footer">
                    <button class="btn-copy" onclick="copyMessage(this)">Copy Message</button>
                    <button class="btn-open" onclick="window.open('{url}', '_blank')">Open Listing</button>
                </div>
            </div>
'''

        html += '        </div>\n'
    else:
        html += '''        <div class="empty-state">
            <h2>✨ All caught up!</h2>
            <p>No pending applications. Keep an eye out for new listings!</p>
        </div>
'''

    html += """    </div>

    <script>
        function copyMessage(button) {
            const card = button.closest('.application-card');
            const messageContent = card.querySelector('.message-content').textContent;

            navigator.clipboard.writeText(messageContent).then(() => {
                const originalText = button.textContent;
                button.textContent = '✓ Copied!';
                button.style.background = '#38a169';

                setTimeout(() => {
                    button.textContent = originalText;
                    button.style.background = '';
                }, 2000);
            }).catch(err => {
                alert('Failed to copy: ' + err);
            });
        }
    </script>
</body>
</html>
"""

    # Write HTML file
    with open(OUTPUT_HTML_FILE, 'w', encoding='utf-8') as f:
        f.write(html)

    print(f"✓ Generated {OUTPUT_HTML_FILE}")
    print(f"  {len(applications)} application(s) ready to submit")
    if applications:
        print(f"  Open in browser: open {OUTPUT_HTML_FILE}")


if __name__ == "__main__":
    generate_html()
