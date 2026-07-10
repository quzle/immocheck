import os
from dotenv import load_dotenv

# Load config from .env.local (copied from .env.example) or a custom DOTENV_FILE.
# To use a custom file: DOTENV_FILE=.env.profile2 python main.py
load_dotenv(os.getenv('DOTENV_FILE', '.env.local'), override=True)

# IMAP Settings
IMAP_HOST = os.getenv('IMAP_SERVER', 'imap.gmail.com')
IMAP_USER = os.getenv('IMAP_EMAIL')
IMAP_PASS = os.getenv('IMAP_PASSWORD')
IMAP_FOLDER = os.getenv('IMAP_FOLDER', 'INBOX')

# LLM Settings
LLM_PROVIDER = os.getenv('LLM_PROVIDER', 'gemini')
MOCK_LLM = os.getenv('MOCK_LLM', 'false').lower() == 'true'

# LLM Model Selection
# For Anthropic: claude-3-5-haiku-20241022, claude-3-5-sonnet-20241022, claude-opus-4-1
# For Gemini: gemini-2.0-flash
LLM_MODEL = os.getenv('LLM_MODEL', 'claude-3-5-sonnet-20241022')

# Try to get the API key based on the provider
if LLM_PROVIDER.lower() == 'anthropic':
    LLM_API_KEY = os.getenv('ANTHROPIC_API_KEY') or os.getenv('LLM_API_KEY')
elif LLM_PROVIDER.lower() == 'gemini':
    LLM_API_KEY = os.getenv('GEMINI_API_KEY')
elif LLM_PROVIDER.lower() == 'ollama':
    LLM_API_KEY = None  # Ollama doesn't require an API key
else:
    LLM_API_KEY = os.getenv('LLM_API_KEY')  # Default fallback

# Ollama Settings (for local LLM support)
OLLAMA_HOST = os.getenv('OLLAMA_HOST', 'http://localhost:11434')
OLLAMA_MODEL = os.getenv('OLLAMA_MODEL', 'qwen3:14b')
OLLAMA_TIMEOUT = int(os.getenv('OLLAMA_TIMEOUT', '600'))
OLLAMA_NUM_CTX = int(os.getenv('OLLAMA_NUM_CTX', '8192'))

# Submission Settings
# When true, skip browser auto-submission entirely and always queue the
# application + email the manual submission instructions.
# (Legacy name was FORCE_CLIPBOARD_FALLBACK; still read for backwards compatibility.)
FORCE_EMAIL_FALLBACK = (
    os.getenv('FORCE_EMAIL_FALLBACK')
    or os.getenv('FORCE_CLIPBOARD_FALLBACK', 'false')
).lower() == 'true'

# Browser Settings
PLAYWRIGHT_HEADLESS = os.getenv('PLAYWRIGHT_HEADLESS', 'false').lower() == 'true'

# Translation Settings
ENABLE_TRANSLATION = os.getenv('ENABLE_TRANSLATION', 'true').lower() == 'true'
TRANSLATION_TARGET_LANGUAGE = os.getenv('TRANSLATION_TARGET_LANGUAGE', 'EN')

# Concurrency & Performance Settings
LLM_CONCURRENCY = int(os.getenv('LLM_CONCURRENCY', '1'))

# Application Settings
CHROME_USER_DATA_DIR = os.getenv('CHROME_USER_DATA_DIR')
MAX_WARMMIETE = int(os.getenv('MAX_WARMMIETE', '2000'))
MIN_IMAGES = int(os.getenv('MIN_IMAGES', '2'))

# Location scoring (optional) — injected into the scoring prompt so no personal
# location data needs to live in templates/prompts.json
OFFICE_LOCATION = os.getenv('OFFICE_LOCATION', 'the city centre')
TRANSIT_LINES = os.getenv('TRANSIT_LINES', 'the main transit lines')

# Google Maps Static API key. When set, a static map of the listing's location
# is embedded in notification emails. Leave empty to disable the map.
MAPS_API_KEY = os.getenv('MAPS_API_KEY', '')

# Multi-source email folders
IMAP_WGGESUCHT_FOLDER = os.getenv('IMAP_WGGESUCHT_FOLDER', '')  # empty = disabled
IMAP_IMMOBILIE1_FOLDER = os.getenv('IMAP_IMMOBILIE1_FOLDER', '')  # empty = disabled

# Push notifications (ntfy.sh)
NTFY_TOPIC = os.getenv('NTFY_TOPIC', '')   # e.g. immocheck-xk7q2m9p — leave empty to disable
NTFY_SERVER = os.getenv('NTFY_SERVER', 'https://ntfy.sh')

# Other settings
POLL_INTERVAL = int(os.getenv('POLL_INTERVAL', '60'))
DRY_RUN = os.getenv('DRY_RUN', 'true').lower() == 'true'

# Notification recipients: comma-separated list of email addresses.
# Defaults to the IMAP account itself if not set.
_raw_recipients = os.getenv('NOTIFICATION_RECIPIENTS', '')
NOTIFICATION_RECIPIENTS: list[str] = (
    [r.strip() for r in _raw_recipients.split(',') if r.strip()]
    if _raw_recipients.strip()
    else []  # resolved to [IMAP_USER] at send time so the default stays dynamic
)

def validate_config():
    """Ensure all required environment variables are set."""
    required_vars = [
        ('IMAP_EMAIL', IMAP_USER),
        ('IMAP_PASSWORD', IMAP_PASS),
        ('IMAP_FOLDER', IMAP_FOLDER)
    ]

    missing = [name for name, val in required_vars if not val]

    if missing:
        raise ValueError(f"Missing required environment variables: {', '.join(missing)}")

    # Check that LLM configuration is valid
    if not MOCK_LLM:
        if LLM_PROVIDER.lower() in ['anthropic', 'gemini'] and not LLM_API_KEY:
            raise ValueError(f"LLM_PROVIDER={LLM_PROVIDER} requires an API key, but none configured")

    # print("Configuration loaded successfully.") # Avoid printing secrets or too much noise

if __name__ == "__main__":
    validate_config()
    print(f"IMAP_HOST: {IMAP_HOST}")
    print(f"IMAP_USER: {IMAP_USER}")
    print(f"IMAP_FOLDER: {IMAP_FOLDER}")
    print(f"LLM_PROVIDER: {LLM_PROVIDER}")
    print(f"MOCK_LLM: {MOCK_LLM}")
    if LLM_PROVIDER.lower() == 'ollama':
        print(f"OLLAMA_HOST: {OLLAMA_HOST}")
        print(f"OLLAMA_MODEL: {OLLAMA_MODEL}")
        print(f"OLLAMA_TIMEOUT: {OLLAMA_TIMEOUT}")
    print(f"FORCE_EMAIL_FALLBACK: {FORCE_EMAIL_FALLBACK}")
    print(f"PLAYWRIGHT_HEADLESS: {PLAYWRIGHT_HEADLESS}")
    print(f"MAX_WARMMIETE: {MAX_WARMMIETE}")
    print(f"DRY_RUN: {DRY_RUN}")
