#!/usr/bin/env python3
"""One-command setup for ImmoCheck.

Creates a virtual environment, installs dependencies, and walks you through an
interactive Q&A that writes your config files — no hand-editing required.

    python setup.py                 # full setup (venv + deps + config wizard)
    python setup.py --skip-install  # just re-run the config wizard
    python setup.py --config-only   # alias for --skip-install

Uses only the Python standard library, so it runs before anything is installed.
"""
import argparse
import getpass
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
VENV_DIR = ROOT / ".venv"
ENV_LOCAL = ROOT / ".env.local"
ENV_EXAMPLE = ROOT / ".env.example"
APPLICANT_FORM = ROOT / "templates" / "applicant_form.json"
APPLICANT_FORM_EXAMPLE = ROOT / "templates" / "applicant_form.example.json"

# Freeform text templates copied verbatim from their .example siblings.
TEXT_TEMPLATES = [
    "renter_profile.txt",
    "application_template.txt",
    "application_template_verbose.txt",
]

# ── tiny terminal helpers ─────────────────────────────────────────────────────

BOLD, DIM, GREEN, YELLOW, CYAN, RESET = (
    ("\033[1m", "\033[2m", "\033[32m", "\033[33m", "\033[36m", "\033[0m")
    if sys.stdout.isatty() else ("", "", "", "", "", "")
)


def step(msg: str) -> None:
    print(f"\n{BOLD}{CYAN}▶ {msg}{RESET}")


def ok(msg: str) -> None:
    print(f"  {GREEN}✓{RESET} {msg}")


def warn(msg: str) -> None:
    print(f"  {YELLOW}!{RESET} {msg}")


def ask(prompt: str, default: str = "", secret: bool = False) -> str:
    """Prompt with an optional default. Returns the trimmed answer or default."""
    suffix = f" {DIM}[{default}]{RESET}" if default else ""
    label = f"  {prompt}{suffix}: "
    value = (getpass.getpass(label) if secret else input(label)).strip()
    return value or default


def ask_choice(prompt: str, choices: list[str], default: str) -> str:
    opts = "/".join(c if c != default else c.upper() for c in choices)
    while True:
        value = ask(f"{prompt} ({opts})", default).lower()
        if value in choices:
            return value
        warn(f"Please choose one of: {', '.join(choices)}")


def ask_yes_no(prompt: str, default: bool = False) -> bool:
    d = "y" if default else "n"
    return ask_choice(prompt, ["y", "n"], d) == "y"

# ── venv + dependency install ─────────────────────────────────────────────────

def venv_python() -> Path:
    """Path to the venv's Python interpreter (platform-aware)."""
    if os.name == "nt":
        return VENV_DIR / "Scripts" / "python.exe"
    return VENV_DIR / "bin" / "python"


def in_virtualenv() -> bool:
    return sys.prefix != sys.base_prefix


def install_dependencies() -> None:
    step("Setting up the virtual environment and dependencies")

    if VENV_DIR.exists():
        ok(f"Reusing existing virtual environment ({VENV_DIR.name})")
    elif in_virtualenv():
        ok(f"Already inside a virtual environment ({sys.prefix})")
    else:
        print("  Creating virtual environment...")
        subprocess.run([sys.executable, "-m", "venv", str(VENV_DIR)], check=True)
        ok("Virtual environment created (.venv)")

    py = str(venv_python()) if VENV_DIR.exists() else sys.executable

    print("  Installing Python dependencies (this can take a minute)...")
    subprocess.run([py, "-m", "pip", "install", "-q", "--upgrade", "pip"], check=True)
    subprocess.run([py, "-m", "pip", "install", "-q", "-r", str(ROOT / "requirements.txt")], check=True)
    ok("Dependencies installed")

    print("  Installing the Chromium browser for Playwright...")
    subprocess.run([py, "-m", "playwright", "install", "chromium"], check=True)
    ok("Chromium installed")

# ── config wizard: .env.local ─────────────────────────────────────────────────

def render_env(example_text: str, values: dict[str, str]) -> str:
    """Rewrite .env.example, substituting collected values.

    Preserves all comments and optional settings. Uncomments a key if it was
    commented out in the template but we now have a value for it (e.g. switching
    the LLM provider to Anthropic).
    """
    lines = []
    for line in example_text.splitlines():
        m = re.match(r"^(#\s*)?([A-Z0-9_]+)=", line)
        if m and m.group(2) in values:
            key = m.group(2)
            after_eq = line.split("=", 1)[1]
            inline = ""
            if "#" in after_eq:  # preserve a trailing inline comment (often a URL)
                inline = "   #" + after_eq.split("#", 1)[1].rstrip()
            lines.append(f'{key}="{values[key]}"{inline}')
        else:
            lines.append(line)
    return "\n".join(lines) + "\n"


def configure_env() -> str:
    """Interactive Q&A → writes .env.local. Returns the Gmail address entered."""
    step("Configuring credentials and search settings (.env.local)")

    if ENV_LOCAL.exists() and not ask_yes_no(f"{ENV_LOCAL.name} already exists. Overwrite it?", False):
        warn("Keeping your existing .env.local — skipping credential setup.")
        return ""

    values: dict[str, str] = {}

    print(f"\n  {BOLD}Gmail{RESET} {DIM}(the inbox that receives your listing alerts){RESET}")
    print(f"  {DIM}Create an app password at https://myaccount.google.com/apppasswords{RESET}")
    email = ask("Gmail address", "")
    values["IMAP_EMAIL"] = email
    values["IMAP_PASSWORD"] = ask("Gmail app password", secret=True)
    values["IMAP_FOLDER"] = ask("Gmail label for ImmoScout24 alerts", "ImmoScout")
    values["NOTIFICATION_RECIPIENTS"] = ask("Send notifications to", email or "")

    print(f"\n  {BOLD}LLM provider{RESET} {DIM}(scores apartments and drafts applications){RESET}")
    provider = ask_choice("Provider", ["gemini", "anthropic", "ollama"], "gemini")
    values["LLM_PROVIDER"] = provider
    if provider == "gemini":
        print(f"  {DIM}Free API key: https://aistudio.google.com/apikey{RESET}")
        values["GEMINI_API_KEY"] = ask("Gemini API key", secret=True)
    elif provider == "anthropic":
        print(f"  {DIM}API key: https://console.anthropic.com/{RESET}")
        values["ANTHROPIC_API_KEY"] = ask("Anthropic API key", secret=True)
        values["LLM_MODEL"] = "claude-haiku-4-5-20251001"
    else:
        warn("Ollama needs no key — make sure `ollama serve` is running.")

    print(f"\n  {BOLD}Search{RESET}")
    values["MAX_WARMMIETE"] = ask("Maximum warm rent (EUR)", "1500")

    print(f"\n  {BOLD}Commute scoring{RESET} {DIM}(optional — press Enter to skip){RESET}")
    office = ask("Your office / anchor location", "")
    if office:
        values["OFFICE_LOCATION"] = office
        values["TRANSIT_LINES"] = ask("Nearby transit lines (e.g. U4, U5, S7)", "")

    example_text = ENV_EXAMPLE.read_text(encoding="utf-8")
    ENV_LOCAL.write_text(render_env(example_text, values), encoding="utf-8")
    ok(f"Wrote {ENV_LOCAL.name}")
    return email

# ── config wizard: applicant_form.json ────────────────────────────────────────

def configure_applicant_form(default_email: str) -> None:
    step("Your contact details for auto-submission (templates/applicant_form.json)")
    print(f"  {DIM}Only used by the experimental browser auto-submit. The recommended{RESET}")
    print(f"  {DIM}email-fallback mode doesn't need this — safe to skip.{RESET}")

    if not ask_yes_no("Fill in your contact details now?", False):
        warn("Skipped — browser autofill will be disabled until you add these.")
        return

    data = json.loads(APPLICANT_FORM_EXAMPLE.read_text(encoding="utf-8"))
    # Prompt only for the personal fields; keep the example's select defaults.
    prompts = {
        "firstName": ("First name", ""),
        "lastName": ("Last name", ""),
        "emailAddress": ("Contact email", default_email),
        "phoneNumber": ("Phone number", ""),
        "street": ("Street", ""),
        "houseNumber": ("House number", ""),
        "postcode": ("Postcode", ""),
        "city": ("City", ""),
    }
    for field in data.get("fields", []):
        if field["name"] in prompts:
            label, default = prompts[field["name"]]
            field["value"] = ask(label, default)

    APPLICANT_FORM.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    ok(f"Wrote {APPLICANT_FORM.relative_to(ROOT)}")

# ── config wizard: text templates ─────────────────────────────────────────────

def seed_text_templates() -> list[str]:
    step("Seeding your profile and application templates")
    created = []
    tdir = ROOT / "templates"
    for name in TEXT_TEMPLATES:
        working = tdir / name
        example = tdir / name.replace(".txt", ".example.txt")
        if working.exists():
            ok(f"{name} already exists — left as-is")
        elif example.exists():
            shutil.copyfile(example, working)
            created.append(name)
            ok(f"Created {name} from template")
        else:
            warn(f"No template found for {name}")
    return created

# ── main ──────────────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(description="Set up ImmoCheck.")
    parser.add_argument(
        "--skip-install", "--config-only", action="store_true", dest="skip_install",
        help="Skip venv/dependency install and only run the config wizard.",
    )
    args = parser.parse_args()
    os.chdir(ROOT)

    print(f"{BOLD}🏠 ImmoCheck setup{RESET}")

    try:
        if not args.skip_install:
            install_dependencies()

        email = configure_env()
        configure_applicant_form(email)
        created = seed_text_templates()
    except KeyboardInterrupt:
        print("\nSetup cancelled.")
        return 130
    except subprocess.CalledProcessError as e:
        print(f"\n{YELLOW}A setup command failed:{RESET} {e}")
        return 1

    # ── next steps ────────────────────────────────────────────────────────────
    step("Almost there — a few things left to do")
    n = 1
    if created:
        print(f"  {n}. Edit your profile so the LLM applies as you:")
        for name in created:
            print(f"       templates/{name}")
        n += 1

    activate = ".venv\\Scripts\\activate" if os.name == "nt" else "source .venv/bin/activate"
    print(f"  {n}. Set up a Gmail label + filter for your alert emails (see the README).")
    n += 1
    print(f"  {n}. Activate the environment and log in to ImmoScout24 once:")
    print(f"       {activate}")
    print(f"       python test_login.py")
    n += 1
    print(f"  {n}. Start it up:")
    print(f"       python main.py")

    print(f"\n{GREEN}{BOLD}Done.{RESET} Re-run config anytime with {DIM}python setup.py --config-only{RESET}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
