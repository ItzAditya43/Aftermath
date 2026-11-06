"""Measure readability and usability of the running frontend at several screen sizes."""
import json
import os
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

UI_URL = os.getenv("AFTERMATH_UI_URL", "http://localhost:3000")

def _launch_args() -> dict:
    """Use CHROMIUM_PATH if set (e.g. /usr/bin/chromium), else Playwright's bundled browser."""
    path = os.getenv("CHROMIUM_PATH")
    return {"executable_path": path, "args": ["--no-sandbox"]} if path else {}


OUT = Path(sys.argv[1])
AXE = sys.argv[2]
OUT.mkdir(parents=True, exist_ok=True)

VIEWPORTS = {"desktop-1440": (1440, 900, False), "laptop-1280": (1280, 720, False),
             "tablet-768": (768, 1024, True), "mobile-390": (390, 844, True)}

MEASURE = r"""
() => {
  const visible = el => { const r = el.getBoundingClientRect(); const s = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none' && +s.opacity > 0.05
      && r.bottom > 0 && r.right > 0 && r.top < innerHeight && r.left < innerWidth; };
  const texts = [];
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  while (walker.nextNode()) {
    const n = walker.currentNode; const t = n.textContent.trim();
    if (t.length < 2 || !n.parentElement || !visible(n.parentElement)) continue;
    const s = getComputedStyle(n.parentElement);
    texts.push({t: t.slice(0, 40), size: parseFloat(s.fontSize), upper: s.textTransform === 'uppercase' || t === t.toUpperCase() && /[A-Z]/.test(t),
                mono: /mono/i.test(s.fontFamily), ls: parseFloat(s.letterSpacing) || 0, chars: t.length});
  }
  const chars = texts.reduce((a, x) => a + x.chars, 0);
  const pct = f => Math.round(100 * texts.filter(f).reduce((a, x) => a + x.chars, 0) / Math.max(1, chars));
  const sizes = {};
  texts.forEach(x => { sizes[x.size] = (sizes[x.size] || 0) + x.chars; });
  const targets = [...document.querySelectorAll('button, a, input, select, textarea, [role=button]')].filter(visible)
    .map(el => { const r = el.getBoundingClientRect(); return {label: (el.getAttribute('aria-label') || el.innerText || el.name || el.tagName).trim().slice(0, 30), w: Math.round(r.width), h: Math.round(r.height)}; });
  return {
    text_nodes: texts.length,
    min_font_px: Math.min(...texts.map(x => x.size)),
    pct_chars_under_12px: pct(x => x.size < 12),
    pct_chars_under_10px: pct(x => x.size < 10),
    pct_chars_uppercase: pct(x => x.upper),
    pct_chars_monospace: pct(x => x.mono),
    chars_by_font_px: Object.fromEntries(Object.entries(sizes).sort((a, b) => a[0] - b[0])),
    smallest_examples: texts.filter(x => x.size < 10).slice(0, 8).map(x => `${x.size}px "${x.t}"`),
    tap_targets_under_44px: targets.filter(t => t.w < 44 || t.h < 44).map(t => `${t.label} ${t.w}x${t.h}`).slice(0, 12),
    horizontal_overflow: document.documentElement.scrollWidth > innerWidth + 1,
    visible_buttons: targets.length,
  };
}
"""

report = {}
with sync_playwright() as p:
    browser = p.chromium.launch(**_launch_args())
    for name, (w, h, mobile) in VIEWPORTS.items():
        page = browser.new_page(viewport={"width": w, "height": h}, is_mobile=mobile, has_touch=mobile)
        page.goto(UI_URL, wait_until="networkidle")
        page.wait_for_timeout(3500)
        page.screenshot(path=OUT / f"{name}.png")
        data = page.evaluate(MEASURE)
        page.add_script_tag(path=AXE)
        axe = page.evaluate("""async () => { const r = await axe.run(document, {runOnly: ['wcag2a','wcag2aa']});
          return r.violations.map(v => ({id: v.id, impact: v.impact, nodes: v.nodes.length,
            sample: v.nodes.slice(0,3).map(n => n.failureSummary.split('\\n').slice(-1)[0].slice(0,140))})); }""")
        data["axe_violations"] = axe
        if mobile:
            # What a first-time judge must discover: how many taps to see the answer?
            details = page.get_by_role("button", name="Details")
            data["mobile_has_details_button"] = details.count() > 0
            if details.count():
                details.first.click()
                page.wait_for_timeout(1200)
                page.screenshot(path=OUT / f"{name}-details.png")
            sel = page.locator("select").all()
            data["mobile_selects"] = [s.evaluate("e => e.options[e.selectedIndex]?.text") for s in sel]
        report[name] = data
        page.close()
    browser.close()
print(json.dumps(report, indent=1))
