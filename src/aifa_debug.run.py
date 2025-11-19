# src/debug_search_run_safe.py
import time
import traceback
from pathlib import Path
from urllib.parse import urljoin
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

AIFA_URL = "https://medicinali.aifa.gov.it/it/#/it/"
SUBSTANCE = "Linezolid"
OUT = Path("debug_outputs")
OUT.mkdir(parents=True, exist_ok=True
          )

def accept_modal(page):
    time.sleep(0.6)
    try:
        checkbox = page.query_selector("#disclaimercheck")
        if checkbox and checkbox.is_visible():
            try:
                checkbox.scroll_into_view_if_needed()
                checkbox.click()
            except Exception:
                page.evaluate("(el)=>el.click()", checkbox)
            time.sleep(0.4)
        try:
            btn = page.wait_for_selector('button.btn.btn-outline-secondary:not([disabled])', timeout=4000)
            if btn and btn.is_visible():
                try:
                    btn.scroll_into_view_if_needed()
                    btn.click()
                except Exception:
                    page.evaluate("(el)=>el.click()", btn)
                time.sleep(0.6)
                return True
        except PWTimeout:
            return False
    except Exception:
        return False
    return False

def find_search_input_and_button(page):
    input_selectors = [
        'input.mat-mdc-autocomplete-trigger',
        'input[placeholder*="Ricerca"]',
        'input[placeholder*="Search"]',
        'input[aria-haspopup="listbox"]',
        'input[role="combobox"]',
        'input[type="text"]',
        'input'
    ]
    button_selectors = [
        'button#basic-addon2',
        'button.search-button',
        'button:has-text("Cerca")',
        'button:has-text("Search")'
    ]
    search_input = None
    search_button = None
    for s in input_selectors:
        try:
            el = page.query_selector(s)
            if el and el.is_visible():
                search_input = el
                break
        except Exception:
            continue
    for b in button_selectors:
        try:
            btn = page.query_selector(b)
            if btn and btn.is_visible():
                search_button = btn
                break
        except Exception:
            continue
    return search_input, search_button

def get_visible_cards(page):
    try:
        cards = page.query_selector_all("app-forma-dosaggio, .custom-card-result, a[href*='/dettaglio/']")
        visible = [c for c in cards if c.is_visible()]
        return visible
    except Exception:
        return []

def safe_write(path: Path, text: str):
    try:
        path.write_text(text, encoding="utf-8")
        print("Saved:", path)
    except Exception as e:
        print("Failed saving", path, ":", e)

def safe_screenshot(page, path: Path):
    try:
        page.screenshot(path=path, full_page=True)
        print("Screenshot saved:", path)
    except Exception as e:
        print("Screenshot failed:", e)

def wait_for_results(page, timeout=10000):
    try:
        page.wait_for_selector("app-forma-dosaggio, a[href*='/dettaglio/'], .custom-card-result", timeout=timeout)
        return True
    except Exception:
        return False

# ------------------ Wait for detail page to fully render (robust) ------------------
# call this after clicking the card and after trying url/selector waits

def wait_for_detail_ready(page, timeout=15000):
    """Wait until detail page appears fully rendered before saving.
       Uses multiple strategies: networkidle, presence of key selectors,
       absence of loading spinner, and final small sleep as safeguard.
    """
    start = time.time()
    try:
        # 1) wait for network idle (lets SPA finish requests)
        try:
            page.wait_for_load_state("networkidle", timeout=5000)
        except Exception:
            # networkidle may timeout for some SPAs; ignore and continue
            pass

        # 2) wait for any of these reliable "detail ready" selectors (whichever appears first)
        detail_selectors = [
            "h1",  # product title
            "text=Azienda titolare",  # visible label text
            'a:has-text("Foglio Illustrativo")',
            'a:has-text("Riassunto Caratteristiche")',
            ".scheda", ".details-main", ".product-detail", "app-dettaglio"
        ]
        selector_found = False
        for sel in detail_selectors:
            try:
                page.wait_for_selector(sel, timeout=3000)
                selector_found = True
                break
            except Exception:
                continue

        if not selector_found:
            # 3) If none of those appeared quickly, wait until loading spinner disappears (if any)
            # Common spinner selectors - adjust if site uses different one
            spinner_selectors = [".spinner", ".loading", ".loader", ".busy"]
            # wait briefly for any spinner to appear then disappear
            for s in spinner_selectors:
                try:
                    # wait a short time for spinner to be visible (if it is)
                    page.wait_for_selector(s, timeout=800)
                    # if visible, wait until it's gone
                    page.wait_for_selector(s + ":not(:visible)", timeout=8000)
                    selector_found = True
                    break
                except Exception:
                    continue

        # 4) final small sleep to let render settle (tweak as needed)
        elapsed = (time.time() - start) * 1000
        remaining = max(0, 300 + int(2000 - elapsed))  # ensure at least ~300ms, at most ~2000ms extra
        time.sleep(0.3 if remaining < 300 else remaining/1000.0)

        return True
    except Exception:
        # don't raise — return False to let caller decide fallback behavior
        return False

    

def debug_run():
    browser = None
    page = None
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=False, slow_mo=60)
            page = browser.new_page()
            print("Opening AIFA...")
            page.goto(AIFA_URL, timeout=60000)
            time.sleep(0.8)

            accepted = accept_modal(page)
            print("Modal accepted?", accepted)

            inp, btn = find_search_input_and_button(page)
            if not inp:
                print("Search input not found. Saving full page for debug.")
                safe_write(OUT / "debug_no_input.html", page.content())
                safe_screenshot(page, OUT / "debug_no_input.png")
                return

            # Type and click search button (no suggestion)
            try:
                inp.click()
                time.sleep(0.12)
                inp.fill(SUBSTANCE)
                time.sleep(0.25)
            except Exception as e:
                print("Typing failed:", e)

            clicked = False
            if btn:
                try:
                    btn.scroll_into_view_if_needed()
                    btn.click()
                    clicked = True
                except Exception:
                    try:
                        page.evaluate("(b)=>b.click()", btn)
                        clicked = True
                    except Exception as e:
                        print("Could not click button:", e)

            if not clicked:
                try:
                    page.keyboard.press("Enter")
                    time.sleep(0.3)
                except Exception:
                    pass

            print("Search invoked (button clicked):", clicked)

            # Wait for results
            if not wait_for_results(page, timeout=15000):
                print("Results did NOT load. Saving debug snapshot.")
                safe_write(OUT / "debug_no_results.html", page.content())
                safe_screenshot(page, OUT / "debug_no_results.png")
                return

            # Save results page once
            safe_write(OUT / "debug_search_click_simple.html", page.content())
            safe_screenshot(page, OUT / "debug_search_click_simple.png")

            # Iterate visible cards
            cards = get_visible_cards(page)
            print("Visible medicine count:", len(cards))

            idx = 0
            while True:
                cards = get_visible_cards(page)  # refresh
                if idx >= len(cards):
                    print("Processed all visible cards.")
                    break

                print(f"\n--- Processing card {idx+1} / {len(cards)} ---")
                card = cards[idx]
                try:
                    card.scroll_into_view_if_needed()
                    card.click()
                except Exception:
                    try:
                        page.evaluate("(el)=>el.click()", card)
                    except Exception as e:
                        print("Card click failed:", e)
                        idx += 1
                        continue

                # robust wait for detail (several fallbacks)
                detail_loaded = False
                try:
                    page.wait_for_url("**/dettaglio/**", timeout=10000)
                    detail_loaded = True
                except Exception:
                    pass

                if not detail_loaded:
                    try:
                        page.wait_for_selector("h1, .details-main, .app-details-page", timeout=10000)
                        detail_loaded = True
                    except Exception:
                        pass

                if not detail_loaded:
                    print("Detail page did not load - saving snapshot and going back.")
                    # try save what we have (if page still valid)
                    try:
                        safe_write(OUT / f"detail_failed_{idx+1}.html", page.content())
                        safe_screenshot(page, OUT / f"detail_failed_{idx+1}.png")
                    except Exception as e:
                        print("Could not save failed detail:", e)
                    # go back if possible and continue
                    try:
                        page.go_back()
                        time.sleep(0.8)
                    except Exception:
                        pass
                    idx += 1
                    continue

                # # Save successful detail debug
                # detail_html = OUT / f"detail_{idx+1}.html"
                # detail_png  = OUT / f"detail_{idx+1}.png"
                # try:
                #     safe_write(detail_html, page.content())
                # except Exception as e:
                #     print("Could not write detail html:", e)
                # safe_screenshot(page, detail_png)
                # --- usage: call wait_for_detail_ready(page) before saving ---
                ok = wait_for_detail_ready(page, timeout=15000)
                if not ok:
                    # fallback: give small extra pause then continue to save whatever is present
                    time.sleep(1.0)

                # Now save detail HTML and screenshot (after we waited)
                detail_html = OUT / f"detail_{idx+1}.html"
                detail_png  = OUT / f"detail_{idx+1}.png"

                try:
                    detail_html.write_text(page.content(), encoding="utf-8")
                    print("✔ Saved HTML:", detail_html)
                except Exception as e:
                    print("✖ Failed saving HTML:", e)

                try:
                    page.screenshot(path=detail_png, full_page=True)
                    print("✔ Saved screenshot:", detail_png)
                except Exception as e:
                    print("✖ Screenshot failed:", e)


                # print a couple of extracted things for quick verification
                try:
                    title_el = page.query_selector("h1")
                    title = title_el.inner_text().strip() if title_el else "(no h1)"
                except Exception:
                    title = "(error reading title)"
                print("Title:", title)

                # back to results
                try:
                    page.go_back()
                    # small wait
                    time.sleep(0.8)
                    # ensure results loaded again
                    wait_for_results(page, timeout=8000)
                except Exception as e:
                    print("Could not go back cleanly:", e)
                    # if cannot go back, break loop to avoid infinite errors
                    break

                idx += 1

            print("\nDEBUG RUN COMPLETE. Check", OUT.resolve())

    except Exception as e:
        print("Unhandled exception in debug_run:", e)
        traceback.print_exc()
        # Try to save page content if page is still available
        try:
            if 'page' in locals() and page is not None:
                safe_write(OUT / "crash_snapshot.html", page.content())
                safe_screenshot(page, OUT / "crash_snapshot.png")
        except Exception as ee:
            print("Failed to save crash snapshot:", ee)
    finally:
        try:
            if 'browser' in locals() and browser is not None:
                browser.close()
        except Exception:
            pass

if __name__ == "__main__":
    debug_run()
