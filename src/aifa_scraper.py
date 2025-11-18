# src/aifa_scraper.py
import os, re, time
from urllib.parse import urljoin
import requests
import pandas as pd

from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
from pdfminer.high_level import extract_text

AIFA_URL = "https://medicinali.aifa.gov.it/it/#/it/"
OUTPUT_DIR = "outputs"
os.makedirs(OUTPUT_DIR, exist_ok=True)

SUBSTANCES = ["Linezolid", "Aripiprazole", "Pantoprazole", "Quetiapine", "Methotrexate"]

def accept_modal_if_present(page):
    # try several ways to accept the modal shown in screenshots
    try:
        # Try to click a checkbox input inside any modal (English / Italian)
        checkbox = None
        for sel in ['input[type="checkbox"]', 'input[role="checkbox"]']:
            el = page.query_selector(sel)
            if el and el.is_visible():
                checkbox = el
                break
        if checkbox:
            try:
                checkbox.click()
            except Exception:
                page.evaluate("(el) => el.click()", checkbox)

        # Try label-based click (English / Italian)
        for label_text in ["I have read", "Ho letto", "Ho letto e compreso", "I have read and understood"]:
            lbl = page.query_selector(f'xpath=//label[contains(normalize-space(.), "{label_text}")]')
            if lbl and lbl.is_visible():
                try:
                    lbl.click()
                except Exception:
                    page.evaluate("(el) => el.click()", lbl)
                break

        # Click ACCEPT / ACCETTA button (enabled after checking)
        for btn_text in ["ACCEPT", "Accept", "ACCETTA", "Accetta"]:
            btn = page.query_selector(f'button:has-text("{btn_text}")')
            if btn and btn.is_visible():
                try:
                    btn.click()
                except Exception:
                    page.evaluate("(el) => el.click()", btn)
                time.sleep(0.5)
                return True
    except PWTimeout:
        pass
    return False

def find_search_input_locator(page):
    # Try to find the search input under the "Look for a drug" area or by placeholders
    selectors = [
        'section:has-text("Look for a drug") input',
        'section:has-text("Cerca un medicinale") input',
        'input[placeholder*="Search"]',
        'input[placeholder*="Cerca"]',
        'input[type="search"]',
        'input[role="combobox"]',
        'input[id*="search"]',
        'input'
    ]
    for sel in selectors:
        try:
            el = page.query_selector(sel)
            if el and el.is_visible():
                return el
        except Exception:
            continue
    return None

def download_pdf_content(page, pdf_url, out_path):
    # Prefer to use Playwright request context to preserve cookies/headers
    try:
        resp = page.request.get(pdf_url, timeout=60000)
        if resp and resp.ok:
            content = resp.body()
            with open(out_path, "wb") as f:
                f.write(content)
            return out_path
    except Exception:
        # fallback to requests
        try:
            r = requests.get(pdf_url, timeout=60)
            r.raise_for_status()
            with open(out_path, "wb") as f:
                f.write(r.content)
            return out_path
        except Exception as e:
            print("Download failed:", e)
    return None

def extract_ma_and_manufacturer_from_pdf(pdf_path):
    try:
        text = extract_text(pdf_path)
    except Exception as e:
        print("PDF extraction error:", e)
        return None, None
    if not text or len(text) < 10:
        return None, None

    # Heuristic patterns (English / Italian / Polish-ish). Tweak if needed.
    mah_patterns = [
        r"Marketing Authorisation Holder[:\s]*([^\n]{2,300})",
        r"Marketing Authorisation Holder \(MAH\)[:\s]*([^\n]{2,300})",
        r"Titolare A\.I\.C\.[:\s]*([^\n]{2,300})",
        r"Titolare(?: del)?(?: AIC|A\.I\.C\.)[:\s]*([^\n]{2,300})",
        r"Titolare[:\s]*([^\n]{2,300})",
        r"Possessore dell'autorizzazione[:\s]*([^\n]{2,300})"
    ]
    manu_patterns = [
        r"Manufacturer[:\s]*([^\n]{2,300})",
        r"Manufacturer \(.*\)[:\s]*([^\n]{2,300})",
        r"Produttore[:\s]*([^\n]{2,300})",
        r"Producent[:\s]*([^\n]{2,300})",
        r"Wytwórca[:\s]*([^\n]{2,300})"
    ]

    mah = None
    manu = None
    for p in mah_patterns:
        m = re.search(p, text, re.I)
        if m:
            mah = m.group(1).strip()
            break

    for p in manu_patterns:
        m = re.search(p, text, re.I)
        if m:
            manu = m.group(1).strip()
            break

    # fallback: try near keywords
    if not mah:
        m = re.search(r"Titolare(?:.*?)([A-Z][\w \-,&\.\(\)\/]{5,200})", text)
        if m:
            mah = m.group(1).strip()
    if not manu:
        m = re.search(r"Produttore(?:.*?)([A-Z][\w \-,&\.\(\)\/]{5,200})", text)
        if m:
            manu = m.group(1).strip()

    return mah, manu

def process_substance(page, substance):
    print("Searching:", substance)
    search_input = find_search_input_locator(page)
    if not search_input:
        print("Search input not found on page. Dumping page HTML for debug.")
        print(page.content()[:2000])
        return None

    # focus & type
    search_input.click()
    search_input.fill(substance)
    time.sleep(0.6)  # give autocomplete a moment
    # Try selecting first suggestion via arrow down + enter
    page.keyboard.press("ArrowDown")
    time.sleep(0.2)
    page.keyboard.press("Enter")
    # If suggestions didn't navigate, press Search button as fallback
    try:
        page.wait_for_load_state("networkidle", timeout=8000)
    except PWTimeout:
        pass

    # try clicking explicit Search button if present
    try:
        btn = page.query_selector('button:has-text("Search")') or page.query_selector('button:has-text("Cerca")')
        if btn and btn.is_visible():
            try:
                btn.click()
            except Exception:
                page.evaluate("(el)=>el.click()", btn)
            page.wait_for_load_state("networkidle", timeout=15000)
    except Exception:
        pass

    time.sleep(1)
    # Now we expect either a results page or a product page.
    # Try find PDF link on current page
    anchors = page.query_selector_all("a")
    pdf_url = None
    for a in anchors:
        try:
            h = a.get_attribute("href") or ""
            txt = (a.inner_text() or "").lower()
            if ".pdf" in h.lower() or "smpc" in txt or "scheda" in txt or "product information" in txt or "summary of product characteristics" in txt:
                pdf_url = urljoin(page.url, h)
                break
        except Exception:
            continue

    # If not found, search for known link texts
    if not pdf_url:
        for kw in ["smpc", "summary of product characteristics", "scheda tecnica", "scheda", "product information", "foglietto"]:
            a = page.query_selector(f'a:has-text("{kw}")')
            if a:
                h = a.get_attribute("href")
                if h:
                    pdf_url = urljoin(page.url, h)
                    break

    if not pdf_url:
        # look for buttons that might open PDF in new tab
        maybe = page.query_selector_all('a')
        for a in maybe:
            h = (a.get_attribute("href") or "").lower()
            if h.endswith(".pdf") or ".pdf?" in h:
                pdf_url = urljoin(page.url, h)
                break

    if not pdf_url:
        print("No PDF link found for", substance, "on page", page.url[:200])
        return None

    print("Found PDF:", pdf_url)
    filename = os.path.join(OUTPUT_DIR, f"{substance.replace(' ','_')}.pdf")
    out = download_pdf_content(page, pdf_url, filename)
    if not out:
        print("Failed to download PDF")
        return None

    mah, manu = extract_ma_and_manufacturer_from_pdf(out)
    return {"substance": substance, "pdf": out, "pdf_url": pdf_url, "mah": mah, "manufacturer": manu}

def main():
    results = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        context = browser.new_context()
        page = context.new_page()
        page.goto(AIFA_URL, timeout=60000)
        time.sleep(1)

        # Accept modal (if present)
        accepted = accept_modal_if_present(page)
        if accepted:
            print("Modal accepted")
            time.sleep(0.6)
        else:
            print("No modal accepted (maybe already closed)")

        # For each substance search & scrape
        for s in SUBSTANCES:
            try:
                # reload homepage to ensure consistent search box
                page.goto(AIFA_URL, timeout=60000)
                time.sleep(1)
                accept_modal_if_present(page)  # accept again if popped
                res = process_substance(page, s)
                if res:
                    results.append({"country": "Italy", **res})
            except Exception as e:
                print("Error on substance", s, ":", e)
        browser.close()

    # save results
    if results:
        df = pd.DataFrame(results)
        out_csv = os.path.join(OUTPUT_DIR, "results.csv")
        df.to_csv(out_csv, index=False)
        print("Saved results to", out_csv)
    else:
        print("No results scraped. Check selectors and page content.")

if __name__ == "__main__":
    main()
