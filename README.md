# ImmoCheck

Automated apartment application bot for ImmoScout24, WG-Gesucht, and immobilie1.de.

Polls Gmail for listing alert emails, evaluates each apartment with an LLM, drafts a personalized German application, and submits it — or queues it for manual review.

> ⚠️ Personal-use project shared for educational purposes. Automating these platforms may
> violate their Terms of Service — use at your own risk. See the [Disclaimer](#disclaimer).

---

## How it works

1. Fetches unread alert emails from Gmail via IMAP
2. Parses listing URLs and metadata from email HTML
3. Pre-filters by price, image count, and blocklist keywords (no network call)
4. Loads each listing page with Playwright to extract full details
5. Evaluates with an LLM (Google Gemini, Anthropic, or Ollama)
6. If approved: drafts a personalized German application
7. Attempts to submit via browser automation, or queues for manual copy/paste
8. Sends an email confirmation with the listing and drafted application

---

## Setup

Requires **Python 3.10+** and **git**. Check your Python version with `python --version`.

### 1. Clone the repository

The repository is **private**, so you'll need access and an authenticated GitHub account.

**HTTPS** (prompts for a GitHub username + [personal access token](https://github.com/settings/tokens)):
```bash
git clone https://github.com/quzle/immocheck.git
cd immocheck
```

**SSH** (if you have an [SSH key](https://docs.github.com/en/authentication/connecting-to-github-with-ssh) added to GitHub):
```bash
git clone git@github.com:quzle/immocheck.git
cd immocheck
```

### 2. Create a virtual environment

**macOS / Linux**
```bash
python3 -m venv .venv
source .venv/bin/activate
```

**Windows**
```bat
python -m venv .venv
.venv\Scripts\activate
```

### 3. Install dependencies

```bash
pip install -r requirements.txt
playwright install chromium
```

### 4. Configure credentials

Copy `.env.example` to `.env.local` and fill in your real values:

**macOS / Linux**
```bash
cp .env.example .env.local
```

**Windows**
```bat
copy .env.example .env.local
```

Required variables (see `.env.example` for the full list with descriptions):

| Variable | Description |
|---|---|
| `IMAP_EMAIL` | Gmail address that receives listing alerts |
| `IMAP_PASSWORD` | Gmail **app password** — not your account password. Generate at [myaccount.google.com/apppasswords](https://myaccount.google.com/apppasswords) (requires 2-step verification) |
| `IMAP_FOLDER` | Name of the Gmail label for ImmoScout24 emails |
| `GEMINI_API_KEY` | Google Gemini API key (or set `ANTHROPIC_API_KEY` and `LLM_PROVIDER=anthropic`) |

**Getting a free Gemini API key.** Google AI Studio gives you an API key with a free tier that's plenty for personal use:

1. Go to [aistudio.google.com/apikey](https://aistudio.google.com/apikey) and sign in with a Google account.
2. Click **Create API key** (choose **Create API key in new project** if you don't have one).
3. Copy the key and paste it into `GEMINI_API_KEY` in `.env.local`.

The default model (`gemini`) uses this key. The free tier has generous daily limits — well within what ImmoCheck needs for a single search. (Prefer Anthropic instead? Set `ANTHROPIC_API_KEY` and `LLM_PROVIDER=anthropic`; for a fully local, no-key option, see [Local LLM with Ollama](#advanced-usage).)

### 5. Log in to ImmoScout24

The bot loads listing pages with Playwright and needs an active ImmoScout24 session. It uses its **own dedicated Chrome profile** (set by `CHROME_USER_DATA_DIR` in `.env.local`, defaulting to `~/.chrome_profile_immocheck`), kept separate from your everyday Chrome — so both can run at the same time without conflict. Leave `CHROME_USER_DATA_DIR` at its default.

Run the login helper:

```bash
python test_login.py
```

A browser window opens — log in to ImmoScout24. The script detects the successful login and the session is saved into the dedicated profile directory. It is reused automatically on every run, so you only do this once (repeat only if ImmoScout24 logs you out).

### 6. Set up your tenant profile and applicant details

The personal templates ship as `.example` files. Copy them to their working filenames (these working copies are gitignored, so your details never get committed):

**macOS / Linux**
```bash
cp templates/renter_profile.example.txt templates/renter_profile.txt
cp templates/application_template.example.txt templates/application_template.txt
cp templates/application_template_verbose.example.txt templates/application_template_verbose.txt
cp templates/applicant_form.example.json templates/applicant_form.json
```

**Windows**
```bat
copy templates\renter_profile.example.txt templates\renter_profile.txt
copy templates\application_template.example.txt templates\application_template.txt
copy templates\application_template_verbose.example.txt templates\application_template_verbose.txt
copy templates\applicant_form.example.json templates\applicant_form.json
```

Then fill in your details:
- `templates/renter_profile.txt` — your tenant profile (age, job, income, etc.), used by the LLM
- `templates/application_template.txt` — your application text, personalized per listing by the LLM
- `templates/application_template_verbose.txt` — the longer application variant (used by the verbose draft mode)
- `templates/applicant_form.json` — your name, contact, and address used to autofill the ImmoScout24 contact form

> Without these working copies the bot exits at startup with `No such file or directory: 'templates/renter_profile.txt'`. They are gitignored, so they are **not** restored by `git checkout` — recreate them from the `.example` files if they go missing.

### 7. Customize location scoring *(optional)*

Set `OFFICE_LOCATION` and `TRANSIT_LINES` in `.env.local` to get commute-aware apartment scoring (e.g. `OFFICE_LOCATION="Marienplatz, Munich"`, `TRANSIT_LINES="U3, U6"`). Leave them generic to disable location-specific scoring.

### 8. First run — dry run recommended

Set `DRY_RUN=true` in `.env.local`, then:

```bash
python main.py
```

---

## Gmail setup

1. Create a Gmail label (e.g. `ImmoScout`) for alert emails
2. Add a Gmail filter to auto-label incoming alerts from ImmoScout24 (filter by sender: `noreply@immobilienscout24.de`)
3. Generate an app password at [myaccount.google.com/apppasswords](https://myaccount.google.com/apppasswords)
4. Set `IMAP_EMAIL`, `IMAP_PASSWORD`, and `IMAP_FOLDER` in `.env.local`

**Additional platforms (optional).** Create a separate Gmail label per platform, filter
its alert emails into that label, and point the matching variable at it:

| Platform | Variable | Example label | Filter by sender |
|---|---|---|---|
| WG-Gesucht | `IMAP_WGGESUCHT_FOLDER` | `WG-Gesucht` | `noreply@wg-gesucht.de` |
| immobilie1.de | `IMAP_IMMOBILIE1_FOLDER` | `Immobilie1` | `noreply@immobilie1.de` |

Leave a variable blank to disable that platform. (immobilie1 alerts are sent via Brevo
with click-tracking links; the parser resolves each link to the real listing URL.)

---

## Submission modes

> **Note:** Automatic browser submission is currently unreliable — ImmoScout24's CAPTCHA system generally blocks Playwright automation. The recommended mode is `FORCE_EMAIL_FALLBACK=true`, which queues applications and emails you the listing and drafted message for manual submission.

### Fallback / manual *(recommended)*

Set `FORCE_EMAIL_FALLBACK=true` in `.env.local`. Approved applications are written to `outputs/pending_applications.jsonl` and you receive an email notification with the drafted message. Use the dashboard to review and copy them:

```bash
python tools/generate_pending_html.py
```

### Browser automation *(experimental)*

Playwright attempts to fill and submit the contact form automatically. This may work intermittently but is frequently interrupted by CAPTCHA. Try `PLAYWRIGHT_HEADLESS=false` to help avoid detection.

---

## Key settings

| Variable | Default | Description |
|---|---|---|
| `POLL_INTERVAL` | `300` | Seconds between Gmail checks |
| `MAX_WARMMIETE` | `1500` | Maximum warm rent budget (€) |
| `MIN_IMAGES` | `2` | Minimum images required per listing |
| `LLM_PROVIDER` | `gemini` | LLM backend: `anthropic`, `gemini`, or `ollama` |
| `FORCE_EMAIL_FALLBACK` | `true` | Skip browser auto-submission; queue + email every application for manual submission |
| `DRY_RUN` | `true` | Only affects the browser path (`FORCE_EMAIL_FALLBACK=false`): skip the final submit click. Does not disable fallback emails |
| `ENABLE_TRANSLATION` | `false` | Translate drafted applications to English (requires DeepL key) |
| `MOCK_LLM` | `false` | Use mock responses for development (no API calls) |

---

## Project structure

```
.env.example                  Template — copy to .env.local and fill in values
ImmoCheck.command             Launcher script (macOS)
ImmoCheck.bat                 Launcher script (Windows)
templates/
  *.example.txt / .json       Templates to copy and fill in (committed)
  renter_profile.txt              Your tenant profile (gitignored; from .example)
  application_template.txt        Your application text (gitignored; from .example)
  application_template_verbose.txt  Longer application variant (gitignored; from .example)
  applicant_form.json             Your contact-form details (gitignored; from .example)
  prompts.json                    LLM evaluation, drafting, and scoring prompts
data/
  processed_listings.json     Deduplication database (created on first run)
outputs/
  logs/                       Session logs (yymmdd-hhmm-immoCheck.log)
  debug_emails/               Raw parsed email HTML
  pending_applications.jsonl  Queue of approved applications
  actions.jsonl               Structured log of all listing decisions
tools/
  generate_pending_html.py    Dashboard for pending applications
  send_pending_emails.py      Re-send notification emails for queued apps
tests/
  sample_alert.html           Sample alert email for testing
```

---

## Log status tags

| Tag | Meaning |
|---|---|
| `[QUEUE]` | Task queued |
| `[LOAD]` | Page load started |
| `[EXTRACT]` | Extracting listing details |
| `[EVAL]` | LLM evaluation in progress |
| `[DRAFT]` | Drafting application |
| `[SUBMIT]` | Submitting application |
| `[APPROVE]` | Submitted successfully |
| `[REJECT_FILTER]` | Rejected by blocklist/price/images |
| `[REJECT_LLM]` | Rejected by LLM evaluation |
| `[ERROR]` | Error — see log for details |

Grep logs by tag:
```bash
grep "\[EVAL\]" outputs/logs/*
```

---

## Troubleshooting

**Bot rejected a good listing**  
Check the log reason tag. Edit `templates/prompts.json` if the LLM criteria are too strict, or `templates/renter_profile.txt` if your profile doesn't match the listing.

**Bot never processes listings**  
Verify `.env.local` credentials (`IMAP_EMAIL`, `IMAP_PASSWORD`, API key). Confirm `IMAP_FOLDER` matches the Gmail label name exactly (case-sensitive). Check `outputs/logs/` for errors.

**CAPTCHA blocking submission**  
Use `FORCE_EMAIL_FALLBACK=true`. If you want to try browser automation, set `PLAYWRIGHT_HEADLESS=false`.

---

## Advanced usage

**Multiple profiles**

macOS / Linux:
```bash
cp .env.local .env.profile2
# edit .env.profile2
DOTENV_FILE=.env.profile2 python main.py
```

Windows (PowerShell):
```powershell
copy .env.local .env.profile2
# edit .env.profile2
$env:DOTENV_FILE=".env.profile2"; python main.py
```

**Local LLM with Ollama**
```bash
ollama serve && ollama pull qwen3:14b
# set LLM_PROVIDER=ollama in .env.local
python main.py
```

**Verbose logging**
```bash
DEBUG=1 python main.py
```

**Preview the notification email**

Send yourself one test notification built from a saved listing snapshot — uses
the real extraction and email code (only the LLM scores/translation are mocked),
so it reflects exactly what production sends:
```bash
python main.py --test-email                          # oldest saved snapshot
python main.py --test-email outputs/submitted/is24_168763633.mhtml   # a specific one
```

---

## Modules

| File | Purpose |
|---|---|
| `main.py` | Entry point and async orchestration loop |
| `config.py` | Configuration loading and validation |
| `email_ingestion.py` | Gmail IMAP polling |
| `email_parser.py` | Parse ImmoScout24 alert emails |
| `listing_filters.py` | Fast pre-filter (blocklist, price, images) |
| `page_scraper.py` | Playwright web scraping |
| `llm_evaluator.py` | LLM evaluation and drafting (3 providers) |
| `browser.py` | Browser automation |
| `application_fallback.py` | Fallback queue and email notification |
| `email_notifications.py` | HTML email construction and delivery |
| `state.py` | JSON-backed deduplication |
| `translation.py` | Optional translation (DeepL) |
| `wg_gesucht_scraper.py` | WG-Gesucht scraper |
| `immobilie1_scraper.py` | immobilie1.de scraper |

---

## Disclaimer

ImmoCheck is an independent, personal-use project shared for educational purposes.
It is **not affiliated with, endorsed by, or connected to** ImmoScout24, WG-Gesucht,
immobilie1.de, or any other listing platform. All product names, logos, and trademarks
are the property of their respective owners.

Automating access to these platforms — scraping listing pages, polling for alerts, or
submitting applications programmatically — **may violate their Terms of Service** and
could result in your account being rate-limited, suspended, or banned. Automated access
can also be subject to local laws.

**You use this software entirely at your own risk and are solely responsible** for how
you use it, for complying with each platform's terms and applicable law, and for the
content of any application you submit. Please be considerate: keep polling intervals
reasonable and don't hammer the platforms. The software is provided "as is", without
warranty of any kind, as set out in the [LICENSE](LICENSE).

---

## License

Released under the [MIT License](LICENSE).
