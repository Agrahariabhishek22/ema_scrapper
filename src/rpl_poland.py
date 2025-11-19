#!/usr/bin/env python3
"""
rpl_playwright.py

Scrapes the interactive RPL UI (https://rejestry.ezdrowie.gov.pl/rpl/search/public)
Workflow:
 - open page
 - enter substance in "Nazwa produktu" input
 - click "Szukaj"
 - for each result card: click "Materiały do pobrania", extract MA holder & manufacturer(s)
 - paginate using the next button and repeat
Output: rpl_results.csv

Usage:
    python rpl_playwright.py "Aripiprazole" --headless --max-pages 5
"""

import csv
import time
import argparse
from typing import List, Optional
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout

# Example uploaded screenshot path (for your reference):
# /mnt/data/Screenshot (834).png

URL = "https://rejestry.ezdrowie.gov.pl/rpl/search/public"

# SELECTORS (tweak here if site changes)
SELECTORS = {
    "search_input": 'input[formcontrolname="name"]',  # Nazwa produktu input
    "search_button_text": "Szukaj",
    # card heuristics: many page nodes have class 'cez-cell'; this aims to capture product cards
    "card_locator": "xpath=//*[contains(@class,'cez-cell') and (contains(@class,'c-col') or contains(@class,'cez-list'))]",
    # Material button inside card: span text anchor -> ancestor button
    "materials_button_in_card_xpath": ".//span[contains(normalize-space(.), 'Materiały do pobrania')]/ancestor::button",
    # fallback: look for button containing 'Materiały'
    "materials_button_fallback_xpath": ".//button[contains(., 'Materiały') or contains(., 'Materiały do pobrania')]",
    # product name and MA number heuristics inside card
    "product_name_rel": ".//h1 | .//h2 | .//h3 | .//*[contains(@class,'title') or contains(@class,'product')][1]",
    "ma_number_rel": ".//*[contains(translate(normalize-space(.),'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'), 'numer pozwolenia') or contains(., 'MA number') or contains(@class,'ma-number')]",
    # next page button (id seen in screenshots); fallback to next paginator class or Polish text
    "next_button_css": "button#cez-list-organizer-footer-0-paginator-footer-next",
    "next_button_fallback": "xpath=//button[contains(@class,'cez-paginator-next') or contains(., 'następna') or contains(., 'następna strona')]",
    # container that wraps the list of cards (to wait for changes)
    "cards_container": "xpath=//app-search-results | xpath=//div[contains(@class,'cez-list') or contains(@class,'list-organizer')]",
}

# Labels (lowercased) to search for when extracting values in the detail area
LABEL_VARIANTS = {
    "ma_holder": [
        "marketing authorisation holder",
        "podmiot odpowiedzialny",
        "holder",
        "równoległy",
        "ma holder",
    ],
    "manufacturer": [
        "wytwórca",
        "manufacturer",
        "manufacturer or importer responsible for batch release",
        "wytwórca lub importer",
        "producent",
    ],
}


def lower_text(s: Optional[str]) -> str:
    return (s or "").strip().lower()


def text_contains_any(target: str, variants: List[str]) -> bool:
    t = lower_text(target)
    for v in variants:
        if v in t:
            return True
    return False


def extract_value_by_label(container, label_variants: List[str]) -> Optional[str]:
    """
    Try to find elements inside `container` that contain any of the label variants,
    then collect nearby text (following-sibling, same block) as the value.
    Returns joined string or None.
    """
    # find all text-containing elements in container and inspect them
    try:
        elems = container.query_selector_all("*")
    except Exception:
        return None

    found_values = []
    for el in elems:
        try:
            txt = el.inner_text().strip()
        except Exception:
            txt = ""
        if not txt:
            continue
        # if this element looks like a label (contains one of variants)
        if any(v in txt.lower() for v in label_variants):
            # attempt strategies to get value:
            # 1) following sibling
            try:
                nxt = el.evaluate_handle("e => e.nextElementSibling")
                if nxt:
                    try:
                        vtxt = nxt.inner_text().strip()
                        if vtxt:
                            found_values.append(vtxt)
                            continue
                    except Exception:
                        pass
            except Exception:
                pass
            # 2) parent block text (exclude the label itself)
            try:
                parent = el.evaluate_handle("e => e.parentElement")
                if parent:
                    try:
                        ptext = parent.inner_text().strip()
                        # remove label text portion
                        remainder = ptext.replace(txt, "").strip()
                        if remainder:
                            found_values.append(remainder)
                            continue
                    except Exception:
                        pass
            except Exception:
                pass
            # 3) search for nearby <p> or <div> with non-empty text after label in DOM order
            try:
                following = container.query_selector_all("p,div,span,li")
                addnext = False
                for f in following:
                    try:
                        ftxt = f.inner_text().strip()
                    except Exception:
                        ftxt = ""
                    if not ftxt:
                        continue
                    if addnext and ftxt:
                        found_values.append(ftxt)
                        break
                    if f == el:
                        addnext = True
            except Exception:
                pass

    if not found_values:
        return None
    # deduplicate preserving order
    dedup = []
    for v in found_values:
        if v not in dedup:
            dedup.append(v)
    return " | ".join(dedup)


def run_scrape(substance: str, headless: bool = True, max_pages: Optional[int] = None):
    results = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=headless)
        context = browser.new_context()
        page = context.new_page()

        page.goto(URL, wait_until="networkidle", timeout=60000)
        # wait for input to appear
        try:
            page.wait_for_selector(SELECTORS["search_input"], timeout=15000)
            search_input = page.query_selector(SELECTORS["search_input"])
        except PlaywrightTimeout:
            # fallback by placeholder
            try:
                search_input = page.wait_for_selector("input[placeholder='Wpisz']", timeout=10000)
            except PlaywrightTimeout:
                raise RuntimeError("Search input not found; update SELECTORS")

        # fill and search
        search_input.fill(substance)
        # try clicking explicit Szukaj button
        try:
            btn = page.locator(f"button:has-text('{SELECTORS['search_button_text']}')")
            btn.first.click(timeout=5000)
        except Exception:
            # fallback press Enter
            search_input.press("Enter")

        # wait until cards appear
        try:
            page.wait_for_selector(SELECTORS["card_locator"], timeout=20000)
        except PlaywrightTimeout:
            # maybe zero results
            pass

        page_index = 1
        while True:
            print(f"[info] processing page {page_index} ...")
            # ensure cards visible
            try:
                page.wait_for_timeout(800)  # small pause
                cards = page.query_selector_all(SELECTORS["card_locator"])
            except Exception:
                cards = []

            # filter out empty nodes
            filtered_cards = []
            for c in cards:
                try:
                    if c.inner_text().strip():
                        filtered_cards.append(c)
                except Exception:
                    continue

            if not filtered_cards:
                print("[info] No cards found on this page.")
            for idx, card in enumerate(filtered_cards, start=1):
                # attempt product name and MA number
                try:
                    pn = card.query_selector("xpath=" + SELECTORS["product_name_rel"])
                    product_name = pn.inner_text().strip() if pn else ""
                except Exception:
                    product_name = ""
                try:
                    ma_el = card.query_selector("xpath=" + SELECTORS["ma_number_rel"])
                    ma_number = ma_el.inner_text().strip() if ma_el else ""
                except Exception:
                    ma_number = ""

                # find materials button inside card
                materials_btn = None
                try:
                    materials_btn = card.query_selector("xpath=" + SELECTORS["materials_button_in_card_xpath"])
                except Exception:
                    pass
                if not materials_btn:
                    try:
                        materials_btn = card.query_selector("xpath=" + SELECTORS["materials_button_fallback_xpath"])
                    except Exception:
                        materials_btn = None

                ma_holder = None
                manufacturers = None

                if materials_btn:
                    try:
                        materials_btn.scroll_into_view_if_needed()
                        materials_btn.click(timeout=5000)
                        # allow UI to expand/load
                        page.wait_for_timeout(800)
                    except Exception:
                        try:
                            page.evaluate("(el) => el.click()", materials_btn)
                            page.wait_for_timeout(700)
                        except Exception:
                            print("[warn] could not click materials button for card", idx)

                    # After clicking, try to extract using the card as the container
                    try:
                        ma_holder = extract_value_by_label(card, LABEL_VARIANTS["ma_holder"])
                    except Exception:
                        ma_holder = None
                    try:
                        manufacturers = extract_value_by_label(card, LABEL_VARIANTS["manufacturer"])
                    except Exception:
                        manufacturers = None

                    # if still not found, try extracting from a broader detail pane (page-level)
                    if not ma_holder or not manufacturers:
                        # try to look at the whole page for labels (sometimes details render in a side panel outside the card)
                        try:
                            page_panel = page
                            if not ma_holder:
                                ma_holder = extract_value_by_label(page_panel, LABEL_VARIANTS["ma_holder"])
                            if not manufacturers:
                                manufacturers = extract_value_by_label(page_panel, LABEL_VARIANTS["manufacturer"])
                        except Exception:
                            pass

                else:
                    print(f"[info] Card #{idx}: 'Materiały do pobrania' button not found.")

                print(f"  - Card #{idx} product='{(product_name or '')[:60]}' MA_holder='{ma_holder}' manufacturers='{manufacturers}'")
                results.append({
                    "search_term": substance,
                    "product_name": product_name,
                    "ma_number": ma_number,
                    "ma_holder": ma_holder or "",
                    "manufacturers": manufacturers or "",
                    "card_index": idx,
                    "page_index": page_index,
                })
                # small pause between cards
                page.wait_for_timeout(200)

            # Pagination: click next
            next_btn = None
            try:
                next_btn = page.query_selector(SELECTORS["next_button_css"])
            except Exception:
                next_btn = None
            if not next_btn:
                try:
                    next_btn = page.query_selector(SELECTORS["next_button_fallback"])
                except Exception:
                    next_btn = None

            def is_disabled(el):
                try:
                    if not el:
                        return True
                    cls = el.get_attribute("class") or ""
                    if "disabled" in (cls or "").lower():
                        return True
                    if el.get_attribute("aria-disabled") == "true":
                        return True
                    if el.get_attribute("disabled") is not None:
                        return True
                except Exception:
                    return False
                return False

            if not next_btn:
                print("[info] Next button not found; finishing.")
                break
            if is_disabled(next_btn):
                print("[info] Next button disabled; finished.")
                break

            # click next and wait for new content
            try:
                next_btn.scroll_into_view_if_needed()
                next_btn.click(timeout=5000)
            except Exception:
                try:
                    page.evaluate("(el) => el.click()", next_btn)
                except Exception:
                    print("[warn] Could not click next button, quitting")
                    break

            # wait for content update: either container changes or short sleep
            try:
                # wait until first card text differs or timeout
                page.wait_for_timeout(1000)
            except Exception:
                pass

            page_index += 1
            if max_pages and page_index > max_pages:
                print("[info] reached max_pages limit.")
                break

        # Save results to CSV
        csvfile = "rpl_results.csv"
        fieldnames = ["search_term", "product_name", "ma_number", "ma_holder", "manufacturers", "card_index", "page_index"]
        with open(csvfile, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            for r in results:
                writer.writerow(r)

        print(f"[done] saved {len(results)} rows to {csvfile}")
        context.close()
        browser.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Scrape RPL UI with Playwright")
    parser.add_argument("substance", help="Substance to search for (e.g. Aripiprazole)")
    parser.add_argument("--headless", action="store_true", help="Run browser headless")
    parser.add_argument("--max-pages", type=int, default=None, help="Maximum pages to scrape")
    args = parser.parse_args()

    run_scrape(args.substance, headless=args.headless, max_pages=args.max_pages)
