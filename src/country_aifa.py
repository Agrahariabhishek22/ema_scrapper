# src/country_aifa.py
from playwright.sync_api import sync_playwright
from urllib.parse import urljoin
import time
import requests, os

OUTPUT_DIR = "outputs"
os.makedirs(OUTPUT_DIR, exist_ok=True)

def search_aifa_and_download(aifa_url, substance):
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(aifa_url, timeout=60000)
        page.wait_for_load_state("networkidle")
        # --- FIND SEARCH BOX (inspect site and update selector) ---
        # Common italian placeholder: Cerca, Ricerca, "Cerca il medicinale"
        possible = [
            'input[type="search"]',
            'input[placeholder*="Cerca"]',
            'input[id*="search"]',
            'input[name*="search"]'
        ]
        sel = None
        for s in possible:
            if page.query_selector(s):
                sel = s
                break
        if not sel:
            print("Search input not auto-detected. Inspect the page and set selector.")
            browser.close()
            return None

        page.fill(sel, substance)
        page.keyboard.press("Enter")
        page.wait_for_load_state("networkidle")
        time.sleep(1)

        # --- CLICK FIRST RELEVANT RESULT ---
        # You must inspect the result list on AIFA and update this selector.
        result_sel = 'a'  # Replace with better selector e.g. 'a.product-link' after inspecting
        el = page.query_selector(result_sel)
        if not el:
            print("No result found.")
            browser.close()
            return None
        href = el.get_attribute("href")
        product_url = urljoin(aifa_url, href)
        page.goto(product_url)
        page.wait_for_load_state("networkidle")

        # --- FIND PDF LINK ---
        anchors = page.query_selector_all("a")
        pdf_url = None
        for a in anchors:
            h = a.get_attribute("href") or ""
            if ".pdf" in h.lower():
                pdf_url = urljoin(product_url, h)
                break
        browser.close()
        if not pdf_url:
            print("No pdf found on product page.")
            return None

        # download pdf
        local_fn = os.path.join(OUTPUT_DIR, f"{substance.replace(' ','_')}.pdf")
        r = requests.get(pdf_url)
        with open(local_fn, "wb") as f:
            f.write(r.content)
        return local_fn

if __name__ == "__main__":
    # example usage
    url = "https://medicinali.aifa.gov.it/"  # replace if different
    pdf = search_aifa_and_download(url, "Linezolid")
    print("Downloaded:", pdf)
