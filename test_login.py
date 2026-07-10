#!/usr/bin/env python3
"""Test script to log in to IS24 and save the session."""
import asyncio
from browser import launch_browser, save_browser_storage_state

async def is_logged_in(page):
    """Check if user is logged in by checking for meinkonto page."""
    try:
        # Check current URL - if we're on meinkonto, we're logged in
        current_url = page.url
        if "meinkonto" in current_url and "login" not in current_url:
            return True
        return False
    except Exception:
        return False

async def main():
    print("Launching browser for login...")
    print("=" * 60)

    browser, context, page = await launch_browser()
    await page.goto("https://www.immobilienscout24.de/meinkonto/")

    print("Browser opened. Log in to IS24 now...")
    print("Waiting for successful login...")
    print("-" * 60)

    # Wait up to 5 minutes for successful login
    for attempt in range(300):
        await asyncio.sleep(1)

        if await is_logged_in(page):
            print("\n✓ Login detected!")
            print("=" * 60)
            print("Saving session...")

            storage_state = await context.storage_state()
            cookies = storage_state.get('cookies', [])
            print(f"Found {len(cookies)} cookies from your login")

            await save_browser_storage_state(context)
            await browser.close()

            print("✓ Session saved successfully!")
            print("You can now run: python main.py")
            print("=" * 60)
            return

        if attempt % 30 == 0 and attempt > 0:
            print(f"Still waiting... ({attempt}s elapsed)")

    # Timeout - save whatever we have
    print("\n⚠ Login not detected within 5 minutes. Saving partial session...")
    await save_browser_storage_state(context)
    await browser.close()
    print("Session saved (may be incomplete).")

if __name__ == "__main__":
    asyncio.run(main())
