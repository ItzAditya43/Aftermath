"""End-to-end browser flow: create -> analyze -> resolve, plus a mobile view, with screenshots.

Usage: python scripts/qa/browser_flow.py <repo root> <output dir>   (AFTERMATH_UI_URL, CHROMIUM_PATH)
"""
import json
import os
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

UI_URL = os.getenv("AFTERMATH_UI_URL", "http://localhost:3000")

def _launch_args() -> dict:
    """Use CHROMIUM_PATH if set (e.g. /usr/bin/chromium), else Playwright's bundled browser."""
    path = os.getenv("CHROMIUM_PATH")
    return {"executable_path": path, "args": ["--no-sandbox"]} if path else {}


ROOT = Path(sys.argv[1])
OUT = Path(sys.argv[2])
OUT.mkdir(parents=True, exist_ok=True)
alerts = json.loads((ROOT / "data/demo_alerts.json").read_text())
alerts = alerts.get("alerts", alerts) if isinstance(alerts, dict) else alerts
alert = alerts[1]  # api-gateway 502 after deploy

console_errors = []
with sync_playwright() as p:
    browser = p.chromium.launch(**_launch_args())
    page = browser.new_page(viewport={"width": 1600, "height": 950})
    page.on("console", lambda m: console_errors.append(m.text) if m.type == "error" else None)
    page.on("pageerror", lambda e: console_errors.append(str(e)))

    page.goto(UI_URL, wait_until="networkidle")
    page.wait_for_timeout(2500)
    page.screenshot(path=OUT / "1-home.png")

    page.get_by_role("button", name="Create incident").first.click()
    page.fill('input[name="title"]', alert["title"])
    page.fill('input[name="service"]', alert["service"])
    page.fill('[name="alert_text"]', alert.get("alert_text", ""))
    page.fill('[name="logs"]', alert.get("logs", ""))
    page.wait_for_timeout(300)
    page.screenshot(path=OUT / "2-create-form.png")
    page.locator('button:has-text("Create incident")').last.click()
    page.wait_for_timeout(2000)
    page.screenshot(path=OUT / "3-incident-created.png")

    started = time.monotonic()
    page.get_by_role("button", name="Analyze incident").first.click()
    page.wait_for_timeout(1500)
    page.screenshot(path=OUT / "4-analysis-streaming.png")
    page.get_by_text("SUGGESTED RESPONSE").first.wait_for(timeout=90_000)
    page.wait_for_timeout(4000)  # let the constellation finish forming
    elapsed = time.monotonic() - started
    page.screenshot(path=OUT / "5-analysis-complete.png")
    body = page.inner_text("body")

    resolve = page.get_by_role("button", name="Record resolution")
    if resolve.count():
        resolve.first.click()
        page.wait_for_timeout(500)
        page.fill('[name="root_cause"]', "Bad route config in v2.58.0 pointed upstream at a removed port")
        page.fill('[name="fix_that_worked"]', "Rolled back api-gateway to v2.57.3 and fixed the upstream port")
        page.fill('[name="what_failed"]', "Restarted gateway pods; 502s came back within 5 minutes")
        page.screenshot(path=OUT / "6-resolution-form.png")
        page.locator('button:has-text("Save resolution")').click()
        page.wait_for_timeout(2500)
        page.screenshot(path=OUT / "7-resolved.png")

    mobile = browser.new_page(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True)
    mobile.goto(UI_URL, wait_until="networkidle")
    mobile.wait_for_timeout(2500)
    mobile.screenshot(path=OUT / "8-mobile.png")
    browser.close()

print(json.dumps({"analysis_seconds": round(elapsed, 1), "console_errors": console_errors[:10],
                  "has_root_cause": "Likely" in body or "Possibly" in body,
                  "excerpt": " ".join(body.split())[:900]}, indent=1))
