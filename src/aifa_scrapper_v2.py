# src/aifa_full_scraper.py
import os, re, time, csv
from urllib.parse import urljoin
from pathlib import Path

import requests
import pandas as pd
from pdfminer.high_level import extract_text
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

# CONFIG
AIFA_URL = "https://medicinali.aifa.gov.it/it/#/it/"
OUTPUT_DIR = Path("outputs")
OUTPUT_DIR.mkdir(exist_ok=True)
SUBSTANCES = ["Linezolid"]  # put more substances if you want

# ---------- helpers ----------
def accept_modal_if_present(page):
    """Click the disclaimer checkbox and ACCETTA (exact selectors from your screenshot)."""
    time.sleep(0.6)
    try:
        checkbox = page.query_selector("#disclaimercheck")
        if checkbox and checkbox.is_visible():
            try:
                checkbox.scroll_into_view_if_needed()
                checkbox.click()
            except Exception:
                page.evaluate("(el) => el.click()", checkbox)
            time.sleep(0.4)
        # wait for enabled button
        try:
            btn = page.wait_for_selector('button.btn.btn-outline-secondary:not([disabled])', timeout=5000)
            if btn:
                try:
                    btn.scroll_into_view_if_needed()
                    btn.click()
                except Exception:
                    page.evaluate("(el)=>el.click()", btn)
                time.sleep(0.5)
                return True
        except PWTimeout:
            return False
    except Exception:
        return False
    return False

def find_search_input_and_button(page, timeout=5000):
    """Return (input_el, button_el) tuned to AIFA search area."""
    end = time.time() + (timeout / 1000.0)
    input_selectors = [
        'input.mat-mdc-autocomplete-trigger',
        'input[placeholder*="Ricerca"]',
        'input[placeholder*="Search"]',
        'input[aria-haspopup="listbox"]',
        'input[role="combobox"]',
        'input[type="text"]'
    ]
    button_selectors = [
        'button#basic-addon2',
        'button.search-button',
        'button:has-text("Cerca")',
        'button:has-text("Search")'
    ]
    found_input = None
    found_btn = None
    while time.time() < end:
        for sel in input_selectors:
            try:
                el = page.query_selector(sel)
                if el and el.is_visible():
                    found_input = el
                    break
            except Exception:
                continue
        for sel in button_selectors:
            try:
                b = page.query_selector(sel)
                if b and b.is_visible():
                    found_btn = b
                    break
            except Exception:
                continue
        if found_input or found_btn:
            break
        time.sleep(0.12)
    try:
        if found_input:
            found_input.scroll_into_view_if_needed()
        if found_btn:
            found_btn.scroll_into_view_if_needed()
    except Exception:
        pass
    return found_input, found_btn

def click_autocomplete_suggestion(page, substance, timeout=6000):
    """Click a suggestion item containing the substance text."""
    sub_upper = substance.upper()
    end = time.time() + (timeout / 1000.0)
    selectors = [
        "ul[role='listbox'] li",
        "div[role='listbox'] div[role='option']",
        "mat-option",
        "li",
        "div.autocomplete-list div",
    ]
    while time.time() < end:
        for sel in selectors:
            try:
                nodes = page.query_selector_all(sel)
            except Exception:
                nodes = []
            for n in nodes:
                try:
                    if not n.is_visible():
                        continue
                    txt = (n.inner_text() or "").strip()
                    if not txt:
                        continue
                    if sub_upper in txt.upper():
                        try:
                            n.scroll_into_view_if_needed()
                            n.click()
                            return True
                        except Exception:
                            try:
                                page.evaluate("(el)=>el.click()", n)
                                return True
                            except Exception:
                                continue
                except Exception:
                    continue
        time.sleep(0.2)
    return False

def wait_for_results_loaded(page, timeout=15000):
    """Wait until at least one result card exists."""
    try:
        page.wait_for_selector("app-forma-dosaggio, a[href*='/dettaglio/'], .custom-card-result", timeout=timeout)
        return True
    except Exception:
        return False

def download_binary_response(response, dest_path):
    try:
        body = response.body()
        with open(dest_path, "wb") as f:
            f.write(body)
        return dest_path
    except Exception as e:
        print("Download response error:", e)
        return None

def download_binary_fallback(url, dest_path):
    try:
        r = requests.get(url, timeout=60)
        r.raise_for_status()
        with open(dest_path, "wb") as f:
            f.write(r.content)
        return dest_path
    except Exception as e:
        print("Requests fallback failed:", e)
        return None

def extract_from_pdf(path):
    try:
        text = extract_text(path)
    except Exception as e:
        print("PDF parse error:", e)
        return None, None
    if not text or len(text) < 10:
        return None, None
    mah = None
    manu = None
    p_mah = [r"Azienda titolare[:\s]*([^\n]{2,300})", r"Titolare[:\s]*([^\n]{2,300})", r"Marketing Authorisation Holder[:\s]*([^\n]{2,300})"]
    p_manu = [r"Produttore[:\s]*([^\n]{2,300})", r"Manufacturer[:\s]*([^\n]{2,300})", r"Wytwórca[:\s]*([^\n]{2,300})"]
    for p in p_mah:
        m = re.search(p, text, re.I)
        if m:
            mah = " ".join(m.group(1).strip().split())
            break
    for p in p_manu:
        m = re.search(p, text, re.I)
        if m:
            manu = " ".join(m.group(1).strip().split())
            break
    return mah, manu

# ---------- detail-page extractors ----------
def extract_product_name(page):
    try:
        h = page.query_selector("h1") or page.query_selector(".text-primary h1") or page.query_selector(".product-title")
        if h:
            return h.inner_text().strip()
    except Exception:
        pass
    # fallback: title in large header
    try:
        html = page.content()
        m = re.search(r"<h1[^>]*>([^<]{3,300})</h1>", html, re.I)
        if m:
            return m.group(1).strip()
    except Exception:
        pass
    return None

def extract_ma_holder_from_detail(page):
    try:
        html = page.content()
        m = re.search(r"Azienda titolare[:\s]*([^\n<]{3,300})", html, re.I)
        if m:
            return " ".join(m.group(1).strip().split())
        m2 = re.search(r"Pharmaceutical Company[:\s]*([^\n<]{3,300})", html, re.I)
        if m2:
            return " ".join(m2.group(1).strip().split())
    except Exception:
        pass
    return None

def find_pdf_links_on_detail(page):
    links = []
    # Try known link texts
    texts = ["Foglio Illustrativo", "Riassunto Caratteristiche Prodotto", "Riassunto Caratteristiche", "RCP", "FI", "Summary of Product Characteristics"]
    for t in texts:
        try:
            el = page.query_selector(f'a:has-text("{t}")')
            if el:
                href = el.get_attribute("href")
                if href:
                    # If href is hash-fragment, make full url
                    if href.startswith("#"):
                        full = page.url.split("#")[0] + href.replace("#", "")
                    else:
                        full = urljoin(page.url, href)
                    links.append(full)
        except Exception:
            pass
    # fallback to any anchor with .pdf
    try:
        for a in page.query_selector_all("a"):
            try:
                h = a.get_attribute("href") or ""
                if ".pdf" in h.lower():
                    full = urljoin(page.url, h)
                    links.append(full)
            except Exception:
                continue
    except Exception:
        pass
    # unique
    return list(dict.fromkeys(links))

# ---------- main iteration over results ----------
def iterate_results_and_scrape(page, substance):
    scraped = []
    if not wait_for_results_loaded(page, timeout=15000):
        print("Results not loaded for", substance)
        return scraped

    # count cards and iterate by index, re-query each time (DOM may re-render)
    index = 0
    while True:
        # re-query cards
        try:
            cards = page.query_selector_all("app-forma-dosaggio, .custom-card-result, a[href*='/dettaglio/']")
        except Exception:
            cards = []
        if not cards or index >= len(cards):
            break

        card = cards[index]
        print(f"[{substance}] clicking card {index+1}/{len(cards)}")
        # try to click anchor inside card
        try:
            anchor = card.query_selector("a[href*='/dettaglio/']")
            if anchor and anchor.is_visible():
                anchor.click()
            else:
                # fallback: click card element itself
                try:
                    card.scroll_into_view_if_needed()
                    card.click()
                except Exception:
                    page.evaluate("(el)=>el.click()", card)
        except Exception as e:
            print("Card click failed:", e)
            index += 1
            continue

        # wait for detail navigation
        try:
            page.wait_for_url("**/dettaglio/**", timeout=8000)
        except Exception:
            # fallback wait for H1 or known detail marker
            try:
                page.wait_for_selector("h1, .details-main, .app-details-page", timeout=8000)
            except Exception:
                pass
        time.sleep(0.6)

        # Extract detail info
        try:
            medicine_name = extract_product_name(page) or f"{substance}_no_title"
            ma_holder = extract_ma_holder_from_detail(page)
            pdf_links = find_pdf_links_on_detail(page)

            pdf_paths = []
            manufacturer = None

            # For each pdf link: navigate/click to trigger pdf response and capture it
            for i, pdf_link in enumerate(pdf_links):
                print(" -> downloading pdf:", pdf_link)
                fname = OUTPUT_DIR / f"{substance.replace(' ','_')}_{index+1}_{i+1}.pdf"
                saved = None
                # Try to request via page.request.get (preserves cookies)
                try:
                    resp = page.request.get(pdf_link, timeout=60000)
                    if resp and resp.ok and "pdf" in (resp.headers.get("content-type","") or "").lower():
                        with open(fname, "wb") as f:
                            f.write(resp.body())
                        saved = str(fname)
                    else:
                        # sometimes we must navigate to pdf link (SPA) to get actual file
                        page.goto(pdf_link, timeout=20000)
                        # wait for any response that is pdf
                        try:
                            r = page.wait_for_response(lambda r: "pdf" in (r.headers.get("content-type","") or "").lower(), timeout=10000)
                            if r:
                                with open(fname, "wb") as f:
                                    f.write(r.body())
                                saved = str(fname)
                        except Exception:
                            # fallback to plain requests
                            saved = download_binary_fallback(pdf_link, str(fname))
                except Exception as e:
                    print("pdf request error:", e)
                    saved = download_binary_fallback(pdf_link, str(fname))
                if saved:
                    pdf_paths.append(saved)
                    # parse for manufacturer (if not already found)
                    _, manu = extract_from_pdf(saved)
                    if manu and not manufacturer:
                        manufacturer = manu

            # if manufacturer not found yet, also try searching page HTML
            if not manufacturer:
                ph = page.content()
                m = re.search(r"Produttore[:\s]*([^\n<]{3,200})", ph, re.I)
                if m:
                    manufacturer = " ".join(m.group(1).strip().split())

            scraped.append({
                "substance": substance,
                "medicine_name": medicine_name,
                "product_url": page.url,
                "ma_holder": ma_holder,
                "manufacturer": manufacturer,
                "pdf_paths": ";".join(pdf_paths)
            })
            print("Scraped:", medicine_name, ma_holder, manufacturer)

        except Exception as e:
            print("Error extracting detail:", e)
            # save debug file
            debug_file = OUTPUT_DIR / f"debug_detail_{substance}_{index+1}.html"
            debug_file.write_text(page.content(), encoding="utf-8")
            print("Saved debug to", debug_file)

        # go back to results
        try:
            page.go_back()
            wait_for_results_loaded(page, timeout=8000)
            time.sleep(0.6)
        except Exception as e:
            print("Go back failed:", e)
            # if back fails - reload results via search page
            try:
                page.goto(AIFA_URL, timeout=15000)
                time.sleep(0.6)
                accept_modal_if_present(page)
                # refill search - simpler: break and let caller re-run search
                break
            except Exception:
                break

        index += 1

    return scraped

# ---------- main ----------
def main():
    all_rows = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)  # set False to watch
        ctx = browser.new_context()
        page = ctx.new_page()
        page.goto(AIFA_URL, timeout=60000)
        time.sleep(0.6)
        accept_modal_if_present(page)

        for s in SUBSTANCES:
            print("Searching substance:", s)
            # go to home to ensure fresh input
            page.goto(AIFA_URL, timeout=60000)
            time.sleep(0.6)
            accept_modal_if_present(page)

            inp, btn = find_search_input_and_button(page)
            if not inp:
                print("Search input not found on page.")
                continue

            # type and pick suggestion
            inp.click()
            inp.fill(s)
            time.sleep(0.4)
            clicked = click_autocomplete_suggestion(page, s, timeout=6000)
            if not clicked:
                # fallback: press Enter or click search button
                page.keyboard.press("Enter")
                time.sleep(0.6)
                if btn:
                    try:
                        btn.click()
                    except Exception:
                        page.evaluate("(b)=>b.click()", btn)

            # wait for results
            time.sleep(1.0)
            # iterate results and scrape detail pages
            rows = iterate_results_and_scrape(page, s)
            all_rows.extend(rows)

        browser.close()

    # save CSV
    if all_rows:
        df = pd.DataFrame(all_rows)
        out_csv = OUTPUT_DIR / "results.csv"
        df.to_csv(out_csv, index=False)
        print("Saved results to", out_csv)
    else:
        print("No rows scraped. Check logs / debug files in outputs/")

if __name__ == "__main__":
    main()
