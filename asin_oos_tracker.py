#!/usr/bin/env python3
"""
Rockwell ASIN OOS Tracker — server version (GitHub Actions)
7 ASINs × 7 cities — Price | Delivery | Seller (Clicktech / Other)
Sends HTML email via Gmail SMTP every run.
"""

import asyncio
import json
import os
import re
import smtplib
import sys
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

try:
    from playwright.async_api import async_playwright
except ImportError:
    print("playwright not installed")
    sys.exit(1)

# ── Config ────────────────────────────────────────────────────────────────────

ASINS = [
    "B085DGN48J", "B0D1R6G5MZ", "B091YZGX77", "B091YZYK2Y",
    "B0DD3TWMD6", "B0D3DBRGXH", "B08BJKT6SL", "B093SMWB6S",
    "B085DNFLGL", "B0DLWM5QHT", "B0CRTXGJ6Y", "B0DD3WLT3R",
    "B0BYZ2R4G6", "B0G343ZTYC", "B0D673WSKX", "B0CKYMZHVC",
    "B0BYZ38LB1", "B0GG4LL128", "B0CMMGMGTT", "B0DGCSVPMW",
    "B08BJJMKTN", "B08BJM4WXR", "B0FL2K9X1D", "B0DLWRV891",
    "B08BJKSN48", "B0G45CQK51", "B0G347KDFD", "B0DRPMBBMZ",
    "B0CRYZDBYL", "B0CRTXDRZK", "B0G34756BM", "B0BYZ2ZFBY",
    "B0CRZ2G3NX", "B0FJ1GWF9P", "B0FJ1GKY63", "B0F1TWDZKR",
    "B0F8QM74DC", "B0CCKXDR4K", "B0G458DQT8", "B0G45C1YS2",
]

MODEL_MAP = {
    "B085DGN48J": "GFR550DDUC",    "B0D1R6G5MZ": "MB100",
    "B091YZGX77": "SFR350DDU",     "B091YZYK2Y": "SFR550DDU",
    "B0DD3TWMD6": "SFR-750",       "B0D3DBRGXH": "SFR350GTS",
    "B08BJKT6SL": "GFR350DDUC",    "B093SMWB6S": "GFR910UC",
    "B085DNFLGL": "GFR450DDUC",    "B0DLWM5QHT": "MB49BL",
    "B0CRTXGJ6Y": "RWCSS 4080",    "B0DD3WLT3R": "SFR450GTS",
    "B0BYZ2R4G6": "RVC400A",       "B0G343ZTYC": "RVC400",
    "B0D673WSKX": "UF300A",        "B0CKYMZHVC": "RVC320A",
    "B0BYZ38LB1": "RVC500A",       "B0GG4LL128": "SFR250SDU-4S",
    "B0CMMGMGTT": "SFR70",         "B0DGCSVPMW": "RVC600A",
    "B08BJJMKTN": "SFR250GT",      "B08BJM4WXR": "SFR550GT",
    "B0FL2K9X1D": "MB55GR",        "B0DLWRV891": "MB49WH",
    "B08BJKSN48": "GFR250SDUC",    "B0G45CQK51": "SFR350SDU-5S",
    "B0G347KDFD": "RVC550",        "B0DRPMBBMZ": "RVC200A",
    "B0CRYZDBYL": "RMC30S",        "B0CRTXDRZK": "RWCSS 150150",
    "B0G34756BM": "RVC700",        "B0BYZ2ZFBY": "BB340C",
    "B0CRZ2G3NX": "RMC60D",        "B0FJ1GWF9P": "RWCSS6080ISIB",
    "B0FJ1GKY63": "RWCSS1540ISIA", "B0F1TWDZKR": "MB49GWH",
    "B0F8QM74DC": "FFP1063",       "B0CCKXDR4K": "COMBI400A",
    "B0G458DQT8": "GFR1210F-5S",   "B0G45C1YS2": "SFR450DDU-5S",
}

CITIES = {
    "Bangalore": 560055,
    "Hyderabad": 500055,
    "Delhi":     110055,
    "Mumbai":    400055,
    "Lucknow":   226016,
    "Kolkata":   700055,
    "Chennai":   600055,
}

# Read from environment variables (set as GitHub secrets)
GMAIL_USER     = os.environ.get("GMAIL_USER", "")
GMAIL_PASSWORD = os.environ.get("GMAIL_PASSWORD", "")
EMAIL_TO       = os.environ.get("EMAIL_TO", "skabhi@amazon.com")

STATE_FILE = Path("oos_tracker_state.json")
MONTHS     = ["jan","feb","mar","apr","may","jun","jul","aug","sep","oct","nov","dec"]


# ── Helpers ───────────────────────────────────────────────────────────────────

def delivery_short(msg: str) -> str:
    if not msg or msg in ("ERR", "", "N/A"):
        return "?"          # element not found — not OOS
    t = msg.lower()
    if "unavailable" in t or "currently" in t:
        return "OOS"
    if "today" in t:
        return "Today"
    if "tomorrow" in t:
        return "Tmrw"
    m = re.search(r"(\d{1,2})\s+(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)", t)
    if m:
        return f"{m.group(1)} {m.group(2)[:3].capitalize()}"
    return "?"


def seller_label(seller: str) -> str:
    if not seller or seller in ("OOS", "ERR"):
        return "OOS"
    if seller == "N/A":
        return "?"
    return seller[:10] if len(seller) > 10 else seller


def is_oos(d: dict) -> bool:
    price = d.get("price", "OOS")
    return not price or price in ("OOS", "N/A", "ERR") or str(price).upper() == "OOS"


def cell_text(d: dict) -> str:
    if not d or is_oos(d):
        return "OOS"
    price  = str(d.get("price", "")).replace(",", "").strip()
    deliv  = delivery_short(d.get("delivery", ""))
    seller = seller_label(d.get("seller", ""))
    if seller == "OOS":
        return "OOS"
    if deliv and deliv not in ("?", "OOS"):
        return f"{price} | {deliv} | {seller}"
    return f"{price} | {seller}"


# ── JS Fetch ──────────────────────────────────────────────────────────────────

FETCH_JS = """
async (asins) => {
    return await Promise.all(asins.map(async asin => {
        try {
            const r = await fetch(`https://www.amazon.in/dp/${asin}?th=1&psc=1`, {
                credentials: 'include',
                signal: AbortSignal.timeout(18000),
                headers: {
                    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
                    'Accept-Language': 'en-IN,en;q=0.9'
                }
            });
            if (r.status !== 200) return { asin, data: null, status: r.status };
            const html = await r.text();
            if (html.toLowerCase().includes('captcha')) return { asin, data: null, status: 429 };

            const p   = new DOMParser();
            const doc = p.parseFromString(html, 'text/html');
            const gi  = (id) => { const e = doc.getElementById(id); return e ? e.textContent.trim() : ''; };

            const priceEl = (
                doc.querySelector('.apexPriceToPay .a-price-whole') ||
                doc.querySelector('#corePriceDisplay_desktop_feature_div .a-price-whole') ||
                doc.querySelector('#apex_offerDisplay_desktop .a-price-whole') ||
                doc.querySelector('.a-price-whole')
            );
            const isOOS   = html.includes('Currently unavailable') && !priceEl;

            const delEl = doc.querySelector('#mir-layout-DELIVERY_BLOCK-slot-PRIMARY_DELIVERY_MESSAGE_LARGE')
                       || doc.getElementById('deliveryBlockMessage');

            return {
                asin, status: 200,
                data: {
                    title:    gi('productTitle').substring(0, 80),
                    price:    isOOS ? 'OOS' : (priceEl ? priceEl.textContent.trim().replace(/\\.$/,'') : 'N/A'),
                    seller:   isOOS ? 'OOS' : (gi('sellerProfileTriggerId') || 'Amazon Retail'),
                    delivery: delEl ? delEl.textContent.trim() : (isOOS ? 'OOS' : 'N/A'),
                }
            };
        } catch(e) {
            return { asin, data: null, status: 0 };
        }
    }));
}
"""


# ── Browser ───────────────────────────────────────────────────────────────────

async def change_pincode(page, pincode: int) -> bool:
    """Set pincode via Amazon's address-change API — no UI interaction needed."""
    # First land on amazon.in to establish session/cookies
    await page.goto("https://www.amazon.in", wait_until="domcontentloaded", timeout=30000)
    await asyncio.sleep(2)

    # Call Amazon's internal address-change endpoint directly (what the UI does behind the scenes)
    result = await page.evaluate("""
        async (pincode) => {
            try {
                const resp = await fetch('/portal-migration/hz/glow/address-change?actionSource=glow', {
                    method: 'POST',
                    credentials: 'include',
                    headers: {
                        'Content-Type': 'application/x-www-form-urlencoded',
                        'Accept': 'application/json, text/javascript, */*; q=0.01',
                        'X-Requested-With': 'XMLHttpRequest'
                    },
                    body: `locationType=LOCATION_INPUT&zipCode=${pincode}&storeContext=apparel&deviceType=web&pageType=Detail&actionSource=glow`
                });
                const data = await resp.json();
                return { ok: resp.ok, status: resp.status, data };
            } catch(e) {
                return { ok: false, error: e.message };
            }
        }
    """, str(pincode))

    if result.get("ok"):
        print(f"  ✓ Pincode {pincode} set via API")
        return True

    # Fallback: UI-based picker
    print(f"  ↳ API fallback (status {result.get('status')}) — trying UI...")
    try:
        await page.wait_for_selector(
            "#nav-global-location-popover-link, #glow-ingress-block", timeout=12000
        )
        btn = (await page.query_selector("#nav-global-location-popover-link")
               or await page.query_selector("#glow-ingress-block"))
        await btn.click()
        await page.wait_for_selector("#GLUXZipUpdateInput", timeout=8000)
        inp = await page.query_selector("#GLUXZipUpdateInput")
        sub = await page.query_selector('#GLUXZipUpdate input[type="submit"]')
        await inp.fill("")
        await inp.type(str(pincode), delay=80)
        await asyncio.sleep(1.5)
        await sub.click()
        await asyncio.sleep(6)
        await page.reload(wait_until="domcontentloaded", timeout=20000)
        await asyncio.sleep(2)
        cur = await page.query_selector("#glow-ingress-line2")
        loc = (await cur.inner_text()).strip() if cur else "unknown"
        print(f"  ✓ UI fallback → {loc}")
        return True
    except Exception as e:
        print(f"  ✗ Both methods failed: {e}")
        return False


async def fetch_city(pw, city: str, pincode: int) -> dict:
    print(f"\n→ {city} ({pincode})")
    results = {}

    browser = await pw.chromium.launch(
        headless=True,
        args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage"]
    )
    try:
        ctx  = await browser.new_context(
            viewport={"width": 1280, "height": 900},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
            locale="en-IN",
        )
        page = await ctx.new_page()

        ok = await change_pincode(page, pincode)
        if not ok:
            await asyncio.sleep(10)
            ok = await change_pincode(page, pincode)
        if not ok:
            for asin in ASINS:
                results[asin] = {"price": "ERR", "seller": "ERR", "delivery": "ERR", "title": ""}
            return results

        raw = await page.evaluate(FETCH_JS, ASINS)
        for item in raw:
            asin = item["asin"]
            if item.get("data"):
                results[asin] = item["data"]
                flag = "OOS" if is_oos(item["data"]) else item["data"].get("price","?")
                print(f"    {asin}: {flag}")
            else:
                results[asin] = {"price": "ERR", "seller": "ERR", "delivery": "ERR", "title": ""}
                print(f"    {asin}: HTTP {item.get('status', 0)}")
    finally:
        await browser.close()

    return results


# ── State ─────────────────────────────────────────────────────────────────────

def load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except Exception:
            pass
    return {}


def save_state(grid: dict):
    state = {
        city: {asin: {"oos": is_oos(d)} for asin, d in asins.items()}
        for city, asins in grid.items()
    }
    STATE_FILE.write_text(json.dumps(state, indent=2))


# ── HTML Report ───────────────────────────────────────────────────────────────

def build_html(grid: dict, ts: str, prev_state: dict) -> str:
    city_list = list(CITIES.keys())
    total     = len(ASINS) * len(CITIES)

    oos_count = sum(
        1 for city in city_list for asin in ASINS
        if is_oos(grid.get(city, {}).get(asin, {}))
    )
    in_stock = total - oos_count

    # Transitions
    new_oos    = []
    back_stock = []
    for city in city_list:
        for asin in ASINS:
            cur  = is_oos(grid.get(city, {}).get(asin, {}))
            prev = prev_state.get(city, {}).get(asin, {}).get("oos", False)
            if cur and not prev:
                new_oos.append((asin, city))
            elif not cur and prev:
                back_stock.append((asin, city))

    alert_html = ""
    if new_oos:
        items = "".join(f"<li style='margin:2pt 0;'>{asin} &mdash; {city}</li>" for asin, city in new_oos)
        alert_html += f"""<div style="background:#FDECEA;border-left:4pt solid #C0392B;padding:8pt 14pt;margin-bottom:10pt;font-size:12pt;color:#1a1a1a;">
          <strong style="color:#C0392B;">New OOS:</strong>
          <ul style="margin:4pt 0 0 16pt;padding:0;">{items}</ul></div>"""
    if back_stock:
        items = "".join(f"<li style='margin:2pt 0;'>{asin} &mdash; {city}</li>" for asin, city in back_stock)
        alert_html += f"""<div style="background:#EAFAF1;border-left:4pt solid #27AE60;padding:8pt 14pt;margin-bottom:10pt;font-size:12pt;color:#1a1a1a;">
          <strong style="color:#27AE60;">Back in Stock:</strong>
          <ul style="margin:4pt 0 0 16pt;padding:0;">{items}</ul></div>"""

    header_cells = (
        '<th nowrap="nowrap" style="background:#1A5276;color:white;padding:6pt 10pt;font-size:12pt;text-align:left;">ASIN</th>'
        '<th nowrap="nowrap" style="background:#1A5276;color:white;padding:6pt 10pt;font-size:12pt;text-align:left;">Model</th>'
    )
    for city in city_list:
        header_cells += f'<th nowrap="nowrap" style="background:#1A5276;color:white;padding:6pt 10pt;font-size:12pt;text-align:center;">{city}</th>'

    rows_html = ""
    for i, asin in enumerate(ASINS):
        row_bg = "#F2F3F4" if i % 2 == 0 else "#FFFFFF"
        model = MODEL_MAP.get(asin, "")
        row = (
            f'<tr><td nowrap="nowrap" style="background:{row_bg};padding:5pt 10pt;font-size:12pt;font-weight:bold;color:#1a1a1a;">{asin}</td>'
            f'<td nowrap="nowrap" style="background:{row_bg};padding:5pt 10pt;font-size:12pt;color:#555;">{model}</td>'
        )
        for city in city_list:
            d   = grid.get(city, {}).get(asin, {})
            txt = cell_text(d)
            if txt == "OOS":
                td_style = "background:#AED6F1;color:#1A5276;font-weight:bold;text-align:center;"
            elif "ERR" in txt:
                td_style = f"background:{row_bg};color:#AAA;text-align:center;"
            else:
                td_style = f"background:{row_bg};color:#1a1a1a;"
            row += f'<td nowrap="nowrap" style="{td_style}padding:5pt 10pt;font-size:12pt;">{txt}</td>'
        row += "</tr>"
        rows_html += row

    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"></head>
<body style="font-family:Calibri,Arial,sans-serif;font-size:12pt;color:#1a1a1a;margin:20pt;background:#fff;">
<h2 style="color:#1A5276;margin-bottom:4pt;font-size:16pt;">Rockwell ASIN Availability Tracker</h2>
<p style="color:#555;margin-top:0;font-size:12pt;">
  <strong>{ts}</strong> &nbsp;|&nbsp;
  In Stock: <strong style="color:#27AE60;">{in_stock}</strong> &nbsp;|&nbsp;
  OOS: <strong style="color:#C0392B;">{oos_count}</strong> / {total}
</p>
{alert_html}
<table style="border-collapse:collapse;border:1pt solid #BDC3C7;">
  <thead><tr>{header_cells}</tr></thead>
  <tbody>{rows_html}</tbody>
</table>
<p style="color:#888;font-size:11pt;margin-top:12pt;">
  Format: Price | Delivery | Seller &nbsp;|&nbsp;
  <span style="background:#AED6F1;padding:1pt 6pt;color:#1A5276;font-weight:bold;">Blue = OOS</span>
</p>
</body></html>"""


# ── Email ─────────────────────────────────────────────────────────────────────

def send_email(html_body: str, ts: str, oos_count: int):
    if not GMAIL_USER or not GMAIL_PASSWORD:
        print("⚠ GMAIL_USER / GMAIL_PASSWORD not set — skipping email")
        return

    subject = f"Rockwell OOS Tracker | {ts} | OOS: {oos_count}/49"
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = GMAIL_USER
    msg["To"]      = EMAIL_TO
    msg.attach(MIMEText(html_body, "html"))

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(GMAIL_USER, GMAIL_PASSWORD)
            server.sendmail(GMAIL_USER, EMAIL_TO, msg.as_string())
        print(f"✓ Email sent to {EMAIL_TO}")
    except Exception as e:
        print(f"✗ Email failed: {e}")


# ── Main ──────────────────────────────────────────────────────────────────────

async def main():
    ts = datetime.now().strftime("%d %b %Y %H:%M")
    print(f"\nRockwell OOS Tracker — {ts}")
    print("=" * 55)

    prev_state = load_state()
    grid = {}

    async with async_playwright() as pw:
        for city, pincode in CITIES.items():
            grid[city] = await fetch_city(pw, city, pincode)

    oos_count = sum(
        1 for city in CITIES for asin in ASINS
        if is_oos(grid.get(city, {}).get(asin, {}))
    )
    print(f"\n{'='*55}")
    print(f"✓ Done — OOS: {oos_count}/{len(ASINS)*len(CITIES)}")

    save_state(grid)
    html = build_html(grid, ts, prev_state)
    send_email(html, ts, oos_count)


if __name__ == "__main__":
    asyncio.run(main())
