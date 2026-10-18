"""Browser smoke test of the whole product with the offline model (CI job `e2e`).

    FAKE_LLM=1 uvicorn app.main:app --port 8766 &       (with the frontend built into frontend/out)
    python scripts/ui_smoke.py <screenshot folder> [--url http://localhost:8766]

Sign up -> example prompt -> build -> draft preview -> the generated app (sign up inside it)
-> a follow-up edit -> version 2. Fails on any page error.
"""

import os
import sys

from playwright.sync_api import sync_playwright

SP = sys.argv[1] if len(sys.argv) > 1 else "."
URL = sys.argv[sys.argv.index("--url") + 1] if "--url" in sys.argv else "http://localhost:8766"
errors = []
with sync_playwright() as p:
    chromium = os.environ.get("CHROMIUM_PATH")
    b = p.chromium.launch(executable_path=chromium) if chromium else p.chromium.launch()
    page = b.new_page(viewport={"width": 1440, "height": 900})
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(URL)
    page.get_by_text("New here? Create an account").click()
    page.get_by_label("Email").fill("demo@example.com"); page.get_by_label("Password").fill("long-enough-pw")
    page.get_by_role("button", name="Create account").click()
    page.get_by_role("button", name="Agent orchestrator").click()
    page.screenshot(path=f"{SP}/v3-home.png")
    page.get_by_role("button", name="Build it").click()
    page.get_by_text("Version 1").wait_for()
    # Nothing is built before the user approves the Architect's design.
    page.get_by_role("button", name="Approve and build").wait_for(timeout=60000)
    page.screenshot(path=f"{SP}/v5-design-review.png", full_page=True)
    page.get_by_role("button", name="Approve and build").click()
    page.wait_for_timeout(2500)
    page.screenshot(path=f"{SP}/v3-building.png")
    try:  # the Live tab shows what the agents write, while they write it
        page.get_by_text("Design (JSON)").first.wait_for(timeout=20000)
        page.screenshot(path=f"{SP}/v5-live-code.png")
    except Exception:  # noqa: BLE001
        print("live code not seen (the build was faster)")
    page.get_by_role("button", name="Preview").click()
    try:  # with the fast offline model the build may finish before the draft is shown
        page.get_by_text("Draft: every screen already works").wait_for(timeout=20000)
        page.screenshot(path=f"{SP}/v4-draft.png")
    except Exception:  # noqa: BLE001
        print("draft preview not seen (the build was faster)")
    page.get_by_text("Built and tested").first.wait_for(timeout=180000)
    page.get_by_role("link", name="Open in new tab").wait_for(timeout=180000)
    app = page.frame_locator("iframe")
    app.get_by_text("No account? Create one").click(timeout=30000)
    app.get_by_label("Email").fill("me@example.com"); app.get_by_label("Password").fill("long-enough-1")
    app.get_by_role("button", name="Create account").click()
    app.get_by_text("Welcome to").wait_for(timeout=15000)
    page.wait_for_timeout(800)
    page.screenshot(path=f"{SP}/v3-live.png")
    page.get_by_label("Ask for a change").fill("add tags to posts")
    page.get_by_role("button", name="Send").click()
    page.get_by_text("Version 2").wait_for()
    page.get_by_role("button", name="Approve and build").click(timeout=60000)
    page.get_by_text("Built and tested").nth(1).wait_for(timeout=180000)
    page.get_by_role("button", name="Quality").click()
    page.screenshot(path=f"{SP}/v3-v2.png")
    print("page errors:", errors)
    b.close()
if errors:
    sys.exit(1)
