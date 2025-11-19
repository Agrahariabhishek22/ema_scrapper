

import csv
import time
import argparse
from typing import Optional, List
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout

URL_LIVE = "https://rejestry.ezdrowie.gov.pl/rpl/search/public"
OUT_CSV = "rpl_results.csv"

# Tuned selectors based on the HTML you provided
SELECTORS = {
    # search input
    "search_input": 'input[formcontrolname="name"]',
    # explicit button text
    "search_button_text": "Szukaj",
    # card-level elements (custom tags used by the site)
    "card_tag": "cez-list-tile",
    "row_tag": "app-search-results-public-row",
    # inside card: materials button
    "materials_button_text": "Materiały do pobrania",
    # next paginator buttons (try these ids/classes)
    "next_ids": [
        "cez-list-organizer-0-paginator-desktop-next",
        "cez-list-organizer-footer-0-paginator-footer-next",
    ],
    "next_class_fallback": "cez-paginator-next",
    # heuristics for product name and ma number inside card
    "product_name_rel": "xpath=.//app-double-label-field[1]//p | .//h3 | .//h2 | .//h1",
    "ma_number_rel": "xpath=.//p[contains(translate(., 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'numer pozwolenia') or contains(., 'MA number') or contains(@class,'ma-number') or contains(., 'nr pozwolenia')]",
}

# label variants to detect MA holder and manufacturer text (lowercased)
LABEL_VARIANTS = {
    "ma_holder": [
        "podmiot odpowiedzialny",
        "marketing authorisation holder",
        "marketing authorisation holder (holder)",
        "podmiot",
        "holder",
    ],
    "manufacturer": [
        "wytwórca lub importer",
        "wytwórca",
        "manufacturer",
        "producent",
        "manufacturer or importer responsible for batch release",
    ],
}

# timeouts
SHORT = 1.0
MID = 5.0
LONG = 15.0

def lower(s: Optional[str]) -> str:
    return (s or "").strip().lower()

def text_contains_any(target: str, variants: List[str]) -> bool:
    t = lower(target)
    for v in variants:
        if v in t:
            return True
    return False

def extract_by_label_in_container(container, label_variants: List[str]) -> Optional[str]:
    """
    Improved: locate elements that contain label variants, then pick the nearest
    value text and CLEAN it by removing label-like lines (Polish/English).
    Returns joined cleaned string or None.
    """
    try:
        elems = container.query_selector_all("*")
    except Exception:
        return None

    values = []
    label_variants_lower = [v.lower() for v in label_variants]

    for el in elems:
        try:
            txt = el.inner_text().strip()
        except Exception:
            txt = ""
        if not txt:
            continue
        low = txt.lower()
        # If element contains any label variant
        if any(v in low for v in label_variants_lower):
            # Strategy 1: try next sibling's inner text
            try:
                nxt = el.evaluate_handle("e => e.nextElementSibling")
                if nxt:
                    try:
                        cand = nxt.as_element().inner_text().strip()
                        if cand:
                            values.append(cand)
                            continue
                    except Exception:
                        pass
            except Exception:
                pass

            # Strategy 2: parent block text minus known label lines
            try:
                parent = el.evaluate_handle("e => e.parentElement")
                if parent:
                    try:
                        ptext = parent.as_element().inner_text().strip()
                        lines = [ln.strip() for ln in ptext.splitlines() if ln.strip()]
                        cleaned = []
                        for ln in lines:
                            lnl = ln.lower()
                            # drop if it matches any label variants or common heading keywords
                            if any(lab in lnl for lab in label_variants_lower):
                                continue
                            if any(keyword in lnl for keyword in [
                                "name of the mp", "nazwa produktu", "nazwa powszechnie stosowana",
                                "ma number", "numer pozwolenia", "pharmaceutical form", "postać farmaceutyczna",
                                "substance", "substancja", "kod atc", "pack", "opakowania", "gtin"
                            ]):
                                continue
                            cleaned.append(ln)
                        if cleaned:
                            values.append(" | ".join(cleaned))
                            continue
                    except Exception:
                        pass
            except Exception:
                pass

            # Strategy 3: scan following p/div/span nodes and take the first non-empty after the label occurrence
            try:
                siblings = container.query_selector_all("p,div,span,li")
                seen = False
                for s in siblings:
                    try:
                        stext = s.inner_text().strip()
                    except Exception:
                        stext = ""
                    if not stext:
                        continue
                    if not seen:
                        if txt in stext:
                            seen = True
                        continue
                    else:
                        values.append(stext)
                        break
            except Exception:
                pass

    if not values:
        return None

    # dedupe & further clean each candidate
    out = []
    for v in values:
        parts = [p.strip() for p in v.split("|")]
        good_parts = []
        for p in parts:
            pl = p.lower()
            if any(lab in pl for lab in label_variants_lower):
                continue
            if len(pl) < 2:
                continue
            good_parts.append(p)
        if good_parts:
            out.append(" | ".join(good_parts))

    if not out:
        return None
    # if multiple candidate blocks, join with " || "
    return " || ".join(out)
    """
    Improved: look for elements whose text contains a label variant,
    then extract the nearest value text and CLEAN it by removing lines
    that are label-like (Polish/English headings). Returns a joined string
    of value lines or None.
    """
    try:
        elems = container.query_selector_all("*")
    except Exception:
        return None

    values = []
    label_variants_lower = [v.lower() for v in label_variants]

    for el in elems:
        try:
            txt = el.inner_text().strip()
        except Exception:
            txt = ""
        if not txt:
            continue
        low = txt.lower()
        # if this element contains any label variant
        if any(v in low for v in label_variants_lower):
            # strategy: try next sibling element's inner text first
            try:
                nxt = el.evaluate_handle("e => e.nextElementSibling")
                if nxt:
                    try:
                        cand = nxt.as_element().inner_text().strip()
                        if cand:
                            values.append(cand)
                            continue
                    except Exception:
                        pass
            except Exception:
                pass

            # fallback: parent block text minus label substrings
            try:
                parent = el.evaluate_handle("e => e.parentElement")
                if parent:
                    ptext = parent.as_element().inner_text().strip()
                    # remove any label lines found inside the parent
                    lines = [ln.strip() for ln in ptext.splitlines() if ln.strip()]
                    cleaned = []
                    for ln in lines:
                        lnl = ln.lower()
                        # drop line if it contains any known label words
                        if any(lab in lnl for lab in label_variants_lower):
                            continue
                        # also drop obvious headings like "name of the mp", "nazwa produktu", "ma number", etc.
                        if any(keyword in lnl for keyword in ["name of the mp", "nazwa produktu", "ma number", "numer pozwolenia", "pharmaceutical form", "postać farmaceutyczna"]):
                            continue
                        cleaned.append(ln)
                    if cleaned:
                        values.append(" | ".join(cleaned))
                        continue
            except Exception:
                pass

            # last resort: scan following p/div/span nodes and take first non-empty
            try:
                siblings = container.query_selector_all("p,div,span,li")
                seen = False
                for s in siblings:
                    try:
                        stext = s.inner_text().strip()
                    except Exception:
                        stext = ""
                    if not stext:
                        continue
                    if not seen:
                        if txt in stext:
                            seen = True
                        continue
                    else:
                        # found next non-empty after label
                        values.append(stext)
                        break
            except Exception:
                pass

    if not values:
        return None
    # dedupe preserving order, and further clean each value (strip label-like prefixes)
    out = []
    for v in values:
        # drop lines that repeat the label itself
        parts = [p.strip() for p in v.split("|")]
        good_parts = []
        for p in parts:
            pl = p.lower()
            if any(lab in pl for lab in label_variants_lower):
                continue
            if len(pl) < 2:
                continue
            good_parts.append(p)
        if good_parts:
            joined = " | ".join(good_parts)
            # final small clean: remove duplicated label words at start like "Podmiot odpowiedzialny: "
            joined = joined
            out.append(joined)
    if not out:
        return None
    # if multiple candidate blocks, join with " || " (rare)
    return " || ".join(out)

def find_next_button(page):
    # try known ids first
    for nid in SELECTORS["next_ids"]:
        try:
            sel = f"button#{nid}"
            el = page.query_selector(sel)
            if el:
                return el
        except Exception:
            pass
    # try class fallback
    try:
        el = page.query_selector(f"button.{SELECTORS['next_class_fallback']}")
        if el:
            return el
    except Exception:
        pass
    # try text-based (Polish 'następna' or 'następna strona')
    try:
        el = page.query_selector("xpath=//button[contains(., 'następna') or contains(., 'następna strona') or contains(., 'Następna')]")
        if el:
            return el
    except Exception:
        pass
    return None

def clean_cell(s: Optional[str]) -> str:
    if not s:
        return ""
    # remove common leftover label phrases
    for phrase in [
        "Nazwa produktu leczniczego", "Name of the MP", "Nazwa powszechnie stosowana",
        "MA number", "Numer pozwolenia", "Pharmaceutical form", "Postać farmaceutyczna",
        "Substancja czynna", "Active substance", "Kod ATC", "GTIN", "Opakowania",
        "Packing", "Zawartość opakowania", "Package content"
    ]:
        s = s.replace(phrase, "")
    # normalize whitespace and separators
    s = " ".join(s.split())
    # remove leading/trailing separators
    s = s.strip(" |,-:")
    return s.strip()


def scrape(substance: str, headless: bool = True, max_pages: Optional[int] = None, local_html: Optional[str] = None):
    results = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=headless)
        context = browser.new_context()
        page = context.new_page()

        # load either live site or local debug file
        if local_html:
            # local_html should be absolute path; Playwright can open file://
            if local_html.startswith("http://") or local_html.startswith("https://"):
                page.goto(local_html, wait_until="networkidle", timeout=60000)
            else:
                page.goto("file://" + local_html, wait_until="domcontentloaded", timeout=60000)
        else:
            page.goto(URL_LIVE, wait_until="networkidle", timeout=60000)

        # find and fill search input
        try:
            page.wait_for_selector(SELECTORS["search_input"], timeout=15000)
            search_input = page.query_selector(SELECTORS["search_input"])
        except PlaywrightTimeout:
            # fallback: try placeholder
            try:
                search_input = page.query_selector("input[placeholder='Wpisz']")
            except Exception:
                search_input = None

        if not search_input:
            raise RuntimeError("Search input not found. Inspect page and update selector.")

        search_input.fill(substance)
        time.sleep(0.2)

        # click Szukaj or press Enter
        try:
            btn = page.locator(f"button:has-text('{SELECTORS['search_button_text']}')").first
            btn.click(timeout=5000)
        except Exception:
            try:
                search_input.press("Enter")
            except Exception:
                print("[warn] Could not trigger search by button or Enter. Proceeding (maybe results loaded via other event).")

        # wait for results container or a short timeout
        try:
            # wait for either card_tag or row_tag to appear
            page.wait_for_selector(SELECTORS["card_tag"], timeout=15000)
        except PlaywrightTimeout:
            try:
                page.wait_for_selector(SELECTORS["row_tag"], timeout=8000)
            except PlaywrightTimeout:
                # fallback short sleep
                time.sleep(2.0)

        page_index = 1
        while True:
            print(f"[info] processing page {page_index} ...")
            time.sleep(0.8)  # small pause for UI to settle

            # collect cards (prefer cez-list-tile; fallback to app-search-results-public-row)
            cards = []
            try:
                cards = page.query_selector_all(SELECTORS["card_tag"])
            except Exception:
                cards = []
            if not cards:
                try:
                    cards = page.query_selector_all(SELECTORS["row_tag"])
                except Exception:
                    cards = []

            # filter visible/meaningful cards
            filtered = []
            for c in cards:
                try:
                    if c.inner_text().strip():
                        filtered.append(c)
                except Exception:
                    continue

            if not filtered:
                print("[info] No cards found on this page.")
            for idx, card in enumerate(filtered, start=1):
                # product name (cleaned)
                try:
                    pn_el = card.query_selector(SELECTORS["product_name_rel"])
                    product_name_raw = pn_el.inner_text().strip() if pn_el else ""
                    # split into lines and pick the best candidate (longest non-label-like)
                    parts = [ln.strip() for ln in product_name_raw.splitlines() if ln.strip()]
                    product_name = ""
                    if parts:
                        # filter out lines that look like labels
                        filtered_parts = [p for p in parts if not any(k in p.lower() for k in [
                            "nazwa produktu", "name of the mp", "nazwa powszechnie stosowana",
                            "inn/common", "pharmaceutical form", "postać farmaceutyczna"
                        ])]
                        if filtered_parts:
                            # choose the longest remaining
                            product_name = max(filtered_parts, key=lambda s: len(s))
                        else:
                            # fallback to longest overall
                            product_name = max(parts, key=lambda s: len(s))
                    else:
                        product_name = product_name_raw
                except Exception:
                    product_name = ""

                # materials button
                materials_btn = None
                try:
                    materials_btn = card.query_selector(f"xpath=.//button[.//span[contains(normalize-space(.), '{SELECTORS['materials_button_text']}')]]")
                except Exception:
                    materials_btn = None
                if not materials_btn:
                    try:
                        materials_btn = card.query_selector(f"xpath=.//button[contains(., '{SELECTORS['materials_button_text']}') or contains(., 'Materiały')]")
                    except Exception:
                        materials_btn = None

                ma_holder = None
                manufacturers = None

                if materials_btn:
                    try:
                        materials_btn.scroll_into_view_if_needed()
                        materials_btn.click(timeout=6000)
                        page.wait_for_timeout(700)
                    except Exception:
                        try:
                            page.evaluate("(el) => el.click()", materials_btn)
                            page.wait_for_timeout(700)
                        except Exception:
                            print("[warn] clicking materials button failed for card", idx)

                    # attempt to extract within the card
                    try:
                        ma_holder = extract_by_label_in_container(card, LABEL_VARIANTS["ma_holder"])
                    except Exception:
                        ma_holder = None
                    try:
                        manufacturers = extract_by_label_in_container(card, LABEL_VARIANTS["manufacturer"])
                    except Exception:
                        manufacturers = None

                    # if not found inside card, search the whole page (details sometimes render outside the card)
                    if (not ma_holder) or (not manufacturers):
                        try:
                            ma_holder = ma_holder or extract_by_label_in_container(page, LABEL_VARIANTS["ma_holder"])
                        except Exception:
                            pass
                        try:
                            manufacturers = manufacturers or extract_by_label_in_container(page, LABEL_VARIANTS["manufacturer"])
                        except Exception:
                            pass

                else:
                    print(f"[info] Card #{idx}: 'Materiały do pobrania' not found.")

                # final cleanup of extracted cells
                product_name = clean_cell(product_name)
                ma_holder = clean_cell(ma_holder or "")
                manufacturers = clean_cell(manufacturers or "")

                print(f"  - product='{(product_name or '')[:80]}' MA_holder='{ma_holder}' manufacturers='{manufacturers}'")
                results.append({
                    "search_term": substance,
                    "product_name": product_name,
                    "ma_holder": ma_holder or "",
                    "manufacturers": manufacturers or "",
                })

                page.wait_for_timeout(200)

            # pagination: find and click next
            next_btn = find_next_button(page)
            if not next_btn:
                print("[info] Next button not found; finishing.")
                break

            # check disabled state
            try:
                cls = (next_btn.get_attribute("class") or "").lower()
                aria = next_btn.get_attribute("aria-disabled") or ""
                disabled_attr = next_btn.get_attribute("disabled")
                if "disabled" in cls or aria == "true" or disabled_attr is not None:
                    print("[info] Next button disabled; reached last page.")
                    break
            except Exception:
                pass

            try:
                next_btn.scroll_into_view_if_needed()
                next_btn.click(timeout=6000)
            except Exception:
                try:
                    page.evaluate("(el) => el.click()", next_btn)
                except Exception:
                    print("[warn] Could not click next button; stopping pagination.")
                    break

            # wait a short while for new page to render
            page.wait_for_timeout(900)
            page_index += 1
            if max_pages and page_index > max_pages:
                print("[info] reached max_pages limit.")
                break

        # final pass: ensure cells are cleaned (redundant but safe)
        for r in results:
            r["product_name"] = clean_cell(r.get("product_name", ""))
            r["ma_holder"] = clean_cell(r.get("ma_holder", ""))
            r["manufacturers"] = clean_cell(r.get("manufacturers", ""))

        # save csv
        fieldnames = ["search_term", "product_name", "ma_holder", "manufacturers"]
        with open(OUT_CSV, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            for r in results:
                writer.writerow(r)

        print(f"[done] saved {len(results)} rows to {OUT_CSV}")
        context.close()
        browser.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="RPL Playwright scraper (updated selectors)")
    parser.add_argument("substance", help="Substance to search for (e.g. Aripiprazole)")
    parser.add_argument("--headless", action="store_true", help="Run browser headless")
    parser.add_argument("--max-pages", type=int, default=None, help="Maximum pages to scrape")
    parser.add_argument("--local-html", type=str, default=None, help="(Optional) Path to local HTML file for debugging (e.g. /mnt/data/rpl_search_page.html)")
    args = parser.parse_args()

    scrape(args.substance, headless=args.headless, max_pages=args.max_pages, local_html=args.local_html)
