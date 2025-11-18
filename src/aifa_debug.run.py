# src/debug_run_aifa.py
import time, re
from pathlib import Path
from urllib.parse import urljoin
from playwright.sync_api import sync_playwright

AIFA_URL = "https://medicinali.aifa.gov.it/it/#/it/"
SUBSTANCE = "Linezolid"
OUT = Path("debug_outputs")
OUT.mkdir(exist_ok=True)

HEADLESS = False   # LIVE MODE

# -------------------- HELPERS --------------------

def accept_modal(page):
    time.sleep(1)
    try:
        checkbox = page.query_selector("#disclaimercheck")
        if checkbox:
            checkbox.click()
            time.sleep(0.4)
        btn = page.wait_for_selector('button.btn.btn-outline-secondary:not([disabled])', timeout=6000)
        btn.click()
        time.sleep(1)
        print("✔ Modal accepted")
        return True
    except:
        print("✖ Modal NOT shown")
        return False


def find_search(page):
    inputs = [
        'input.mat-mdc-autocomplete-trigger',
        'input[placeholder*="Ricerca"]',
        'input[placeholder*="Search"]',
        'input[role="combobox"]',
        'input[type="text"]',
    ]
    btns = [
        'button#basic-addon2',
        'button.search-button',
        'button:has-text("Cerca")'
    ]

    # input
    inp = None
    for sel in inputs:
        el = page.query_selector(sel)
        if el:
            inp = el
            break

    # button
    btn = None
    for sel in btns:
        el = page.query_selector(sel)
        if el:
            btn = el
            break

    return inp, btn


def wait_results(page):
    try:
        page.wait_for_selector("app-forma-dosaggio, .custom-card-result", timeout=15000)
        return True
    except:
        return False


def extract_product_name(page):
    try:
        h = page.query_selector("h1")
        return h.inner_text().strip()
    except:
        return "UNKNOWN"


# -------------------- MAIN DEBUG --------------------

def debug_run():
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=HEADLESS, slow_mo=120)
        page = browser.new_page()

        print("Navigating to AIFA...")
        page.goto(AIFA_URL)
        time.sleep(1)
        accept_modal(page)

        # find search input & button
        inp, btn = find_search(page)
        if not inp or not btn:
            print("❌ Could not find search input or button")
            OUT.joinpath("no_input_debug.html").write_text(page.content(), encoding="utf-8")
            page.screenshot(path=OUT/"no_input_debug.png", full_page=True)
            return

        print("Typing:", SUBSTANCE)
        inp.click()
        inp.fill(SUBSTANCE)
        time.sleep(0.5)

        print("Clicking Cerca button...")
        try:
            btn.click()
        except:
            page.evaluate("(b)=>b.click()", btn)
        time.sleep(1)

        print("Waiting for results...")
        if not wait_results(page):
            print("❌ Results did NOT load")
            OUT.joinpath("results_not_found.html").write_text(page.content(), encoding="utf-8")
            return

        print("✔ Results loaded!")

        # ----------- iterate medicine cards (LIVE DEBUG) -----------
        cards = page.query_selector_all("app-forma-dosaggio, .custom-card-result")
        print(f"Found {len(cards)} medicines")

        for idx, card in enumerate(cards, start=1):
            print(f"\n=== Medicine {idx} ===")

            # Click the card
            try:
                card.click()
            except:
                page.evaluate("(el)=>el.click()", card)

            # ------------- WAIT FOR DETAIL PAGE SAFELY -------------

            detail_loaded = False

            # 1) Try URL pattern
            try:
                page.wait_for_url("**/dettaglio/**", timeout=8000)
                detail_loaded = True
            except:
                pass

            # 2) Try h1
            if not detail_loaded:
                try:
                    page.wait_for_selector("h1", timeout=6000)
                    detail_loaded = True
                except:
                    pass

            # 3) Try common detail containers
            if not detail_loaded:
                try:
                    page.wait_for_selector(".container, .row, app-dettaglio", timeout=6000)
                    detail_loaded = True
                except:
                    pass

            # 4) If still not loaded → SKIP THIS MEDICINE
            if not detail_loaded:
                print("✖ Detail page did NOT load. Skipping this medicine.")
                page.go_back()
                time.sleep(1)
                continue


            time.sleep(1)

            # Save detail debug
            html_path = OUT / f"detail_{idx}.html"
            png_path  = OUT / f"detail_{idx}.png"

            html_path.write_text(page.content(), encoding="utf-8")
            page.screenshot(path=png_path, full_page=True)

            print("✔ Detail page captured:", html_path.name)

            # Extract product name
            title = extract_product_name(page)
            print("Product name:", title)

            # Extract MA Holder
            html = page.content()
            m = re.search(r"Azienda titolare[:\s]*([^\n<]{3,300})", html, re.I)
            mah = m.group(1).strip() if m else "NOT FOUND"
            print("MA Holder:", mah)

            # Find PDF links (debug only)
            pdf_links = []
            anchors = page.query_selector_all("a")
            for a in anchors:
                href = a.get_attribute("href") or ""
                if ".pdf" in href.lower():
                    pdf_links.append(urljoin(page.url, href))

            print("PDF links found:", pdf_links)

            # GO BACK
            print("Going back to results...")
            page.go_back()
            time.sleep(1)
            wait_results(page)

        print("\nDEBUG RUN COMPLETE")
        print("Check folder:", OUT)

        time.sleep(5)
        browser.close()


if __name__ == "__main__":
    debug_run()
