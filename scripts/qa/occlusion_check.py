"""Report whether the main action buttons are covered by other panels at common laptop sizes."""
import os
from playwright.sync_api import sync_playwright

UI_URL = os.getenv("AFTERMATH_UI_URL", "http://localhost:3000")

def _launch_args() -> dict:
    """Use CHROMIUM_PATH if set (e.g. /usr/bin/chromium), else Playwright's bundled browser."""
    path = os.getenv("CHROMIUM_PATH")
    return {"executable_path": path, "args": ["--no-sandbox"]} if path else {}

with sync_playwright() as p:
    b = p.chromium.launch(**_launch_args())
    for w, h in [(1280, 720), (1366, 768), (1440, 900), (1920, 1080)]:
        page = b.new_page(viewport={"width": w, "height": h})
        page.goto(UI_URL, wait_until="networkidle")
        page.wait_for_timeout(3000)
        res = page.evaluate("""() => [...document.querySelectorAll('button')].filter(x => /Re-analyze|Analyze incident|Record resolution/.test(x.innerText)).map(btn => {
            const r = btn.getBoundingClientRect(); const top = document.elementFromPoint(r.x + r.width/2, r.y + r.height/2);
            return `${btn.innerText.trim()}: y=${Math.round(r.y)} ${btn.contains(top) ? 'CLICKABLE' : 'HIDDEN under ' + (top?.closest('[aria-label]')?.getAttribute('aria-label') || top?.tagName)}`; })""")
        print(f"{w}x{h}:", res)
        page.close()
    b.close()
