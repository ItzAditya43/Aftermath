# QA scripts

Black-box checks used for the Nov 2026 review (issues and PR evidence). They need a running stack.

| Script | What it checks | Needs |
| --- | --- | --- |
| `api_deep_test.py` | 19 API/engine checks: validation, demo alerts, prompt injection, secret handling, garbage/large logs, concurrency, resolve lifecycle, learning, latency | backend (use an isolated DB, it writes data) |
| `browser_flow.py` | Create → analyze → resolve in a real browser + mobile view, screenshots | backend + frontend |
| `ux_audit.py` | Font-size distribution, tap targets, overflow and axe-core WCAG A/AA at 4 screen sizes | frontend, `axe.min.js` path |
| `occlusion_check.py` | Whether the main action buttons are covered by other panels at common laptop sizes | frontend |

Setup (outside the project venv is fine):

```bash
python -m venv .qa-venv && .qa-venv/bin/pip install playwright httpx
.qa-venv/bin/playwright install chromium        # or set CHROMIUM_PATH=/usr/bin/chromium
npm i --prefix /tmp/axe axe-core@4              # for ux_audit.py
export AFTERMATH_UI_URL=http://localhost:3000 AFTERMATH_API_URL=http://localhost:8000
.qa-venv/bin/python scripts/qa/ux_audit.py docs/qa /tmp/axe/node_modules/axe-core/axe.min.js
```
