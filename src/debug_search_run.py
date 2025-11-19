import time
import traceback
import re
import pdfplumber
import sqlite3
import pandas as pd
from pathlib import Path
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout, Page

# --- CONFIGURATION ---
AIFA_URL = "https://medicinali.aifa.gov.it/it/#/it/"
SUBSTANCES = ["Linezolid"]  # Add more substances here
OUT = Path("debug_outputs")
OUT.mkdir(parents=True, exist_ok=True)

# Global list for results
all_results = []

# --- 1. HELPER: TEXT CLEANING ---
def clean_text(text: str) -> str:
    """Removes newlines and extra spaces from extracted strings."""
    if not text:
        return "Not Found"
    # Replace newlines with spaces, remove multiple spaces, strip
    return re.sub(r'\s+', ' ', text).strip()

def sanitize_filename(name: str) -> str:
    """Removes illegal characters for file paths."""
    return re.sub(r'[\\/*?:"<>|]', "", name)

# --- 2. PDF PARSING HELPER ---
def extract_details_from_pdf(pdf_path: Path):
    ma_holder = "Not Found"
    manufacturer = "Not Found"
    
    try:
        full_text = ""
        with pdfplumber.open(pdf_path) as pdf:
            for page in pdf.pages:
                # Increased y_tolerance slightly for better multi-line capture
                text = page.extract_text(x_tolerance=2, y_tolerance=4) 
                if text:
                    full_text += text + "\n"
        
        # 1. MA Holder (Titolare AIC)
        # Looks for "Titolare..." followed by text, stopping at double newline, "Produttore", or "6. Contenuto"
        ma_pattern = re.compile(
            r"(?:Titolare dell['’]autorizzazione all['’]immissione in commercio|Titolare AIC|Titolare A\.I\.C\.)[:\.]?\s*([\s\S]*?)(?:\n\s*\n|\nProduttore|\n6\.\s*Contenuto|$)", 
            re.IGNORECASE | re.MULTILINE
        )
        match_ma = ma_pattern.search(full_text)
        
        if match_ma:
            raw_ma = match_ma.group(1)
            # Filter out lines that repeat the label
            lines = [line for line in raw_ma.split('\n') if "Titolare" not in line and line.strip()]
            ma_holder = clean_text(" ".join(lines))

        # 2. Manufacturer (Produttore)
        mfg_pattern = re.compile(
            r"(?:Produttore|Produttore responsabile|Fabbricante)[:\.]?\s*([\s\S]*?)(?:\n\s*\n|\nTitolare|\nQuesto foglio|$)",
            re.IGNORECASE | re.MULTILINE
        )
        match_mfg = mfg_pattern.search(full_text)
        
        if match_mfg:
            raw_mfg = match_mfg.group(1)
            lines = [line for line in raw_mfg.split('\n') if "Produttore" not in line and line.strip()]
            manufacturer = clean_text(" ".join(lines))
        
        return ma_holder, manufacturer

    except Exception as e:
        print(f"Error processing PDF {pdf_path.name}: {e}")
        return "Error Parsing PDF", "Error Parsing PDF"

# --- 3. PLAYWRIGHT HELPERS ---

def accept_modal(page: Page):
    """Handles the cookie/disclaimer modal."""
    time.sleep(1.0) # Wait for animation
    try:
        # 1. Checkbox
        checkbox = page.query_selector("#disclaimercheck")
        if checkbox and checkbox.is_visible():
            checkbox.scroll_into_view_if_needed()
            checkbox.click()
            time.sleep(0.5)
        
        # 2. Button
        btn = page.wait_for_selector('button.btn.btn-outline-secondary:not([disabled])', state='visible', timeout=3000)
        if btn:
            btn.click()
            time.sleep(1.0) # Wait for modal to disappear
            return True
    except Exception:
        pass # Modal might not be there
    return False

def perform_search(page: Page, substance: str):
    """Finds input, clears it, types substance, and clicks search."""
    try:
        # Robust input selector
        inp = page.wait_for_selector(
            'input.mat-mdc-autocomplete-trigger, input[placeholder*="Ricerca"], input[type="text"]', 
            state="visible", timeout=10000
        )
        if not inp: return False

        inp.click()
        page.keyboard.press("Control+A")
        page.keyboard.press("Backspace")
        time.sleep(0.2)
        inp.fill(substance)
        time.sleep(0.5)
        page.keyboard.press("Enter")
        return True
    except Exception as e:
        print(f"Search interaction failed: {e}")
        return False

def wait_for_results(page: Page, timeout=10000):
    """Waits for result cards to appear."""
    try:
        page.wait_for_selector("app-forma-dosaggio, .custom-card-result", timeout=timeout)
        # Optional: Wait for spinner to disappear
        try:
            page.wait_for_selector(".spinner", state="hidden", timeout=2000)
        except: pass
        return True
    except:
        return False

def get_visible_cards(page: Page):
    """Returns list of card elements."""
    try:
        return page.query_selector_all("app-forma-dosaggio, .custom-card-result, .card-body")
    except:
        return []

# --- 4. MAIN LOGIC ---

def run_scraper_for_substance(pw, substance: str):
    print(f"\n{'='*10} PROCESSING: {substance} {'='*10}")
    browser = pw.chromium.launch(headless=False, slow_mo=50)
    context = browser.new_context(accept_downloads=True)
    page = context.new_page()

    try:
        print(">> Opening AIFA...")
        page.goto(AIFA_URL, timeout=60000)
        accept_modal(page)

        # Perform Search
        if not perform_search(page, substance):
            print("!! Search failed.")
            return

        if not wait_for_results(page):
            print("!! No results found or timeout.")
            return

        # Save the Result URL (Crucial for navigation fix)
        search_results_url = page.url
        
        # Initial Count
        initial_cards = get_visible_cards(page)
        total_cards = len(initial_cards)
        print(f"Found {total_cards} medicines for {substance}.")

        # --- ITERATION LOOP ---
        for idx in range(total_cards):
            print(f"\n--- Item {idx+1}/{total_cards} ---")

            # 1. NAVIGATION CHECK
            # If we aren't on the search result page, go there.
            if page.url != search_results_url and "/dettaglio/" in page.url:
                print(">> Force navigating back to list...")
                page.goto(search_results_url)
                wait_for_results(page)

            # 2. RE-FETCH CARDS (Fresh DOM elements)
            cards = get_visible_cards(page)
            
            # Edge Case: List size changed?
            if idx >= len(cards):
                print(f"!! Index {idx} out of bounds. List changed. Skipping rest.")
                break

            card = cards[idx]

            # 3. CLICK & LOAD DETAIL
            try:
                card.scroll_into_view_if_needed()
                card.click()
            except Exception as e:
                print(f"!! Click failed: {e}")
                continue

            # Wait for detail
            try:
                page.wait_for_selector("h1", timeout=8000)
            except:
                print("!! Detail page timeout. Going next.")
                continue

            # 4. EXTRACT DATA
            # Title
            try:
                product_name = clean_text(page.locator("h1").first.inner_text())
            except: 
                product_name = "Unknown Title"
            
            print(f"Title: {product_name}")
            current_url = page.url

            # HTML MA Holder (Backup)
            ma_holder_html = "Not Found"
            try:
                owner_el = page.locator("p:has-text('Azienda titolare'), p:has-text('Owner:')").first
                if owner_el.count() > 0:
                    raw = owner_el.inner_text()
                    if ":" in raw:
                        ma_holder_html = clean_text(raw.split(":", 1)[1])
            except: pass

            # 5. PDF DOWNLOAD
            pdf_filename = "Not Found"
            ma_pdf, manu_pdf = "Not Found", "Not Found"
            
            try:
                # Specific selector for the FI link
                pdf_link = page.query_selector('a:has-text("Foglio Illustrativo"), a[href*="stampati"]')
                
                if pdf_link:
                    with page.expect_download(timeout=10000) as download_info:
                        # Force click usually works better on hidden/styled links
                        page.evaluate("(el) => el.click()", pdf_link)
                    
                    download = download_info.value
                    # Create safe filename
                    safe_sub = sanitize_filename(substance)
                    fname = f"{safe_sub}_{idx+1}.pdf"
                    save_path = OUT / fname
                    
                    download.save_as(save_path)
                    print(f"✔ PDF Saved: {fname}")
                    pdf_filename = fname

                    # Parse PDF
                    ma_pdf, manu_pdf = extract_details_from_pdf(save_path)
                    print(f"  MA (PDF): {ma_pdf[:40]}...")
                    print(f"  Mfg (PDF): {manu_pdf[:40]}...")
                else:
                    print("✖ No 'Foglio Illustrativo' link found.")

            except Exception as e:
                print(f"✖ PDF Error: {e}")

            # 6. CONSOLIDATE & SAVE
            final_ma = ma_pdf if ma_pdf != "Not Found" else ma_holder_html

            all_results.append({
                "Search_Substance": substance,
                "Product_Name": product_name,
                "MA_Holder": final_ma,
                "Manufacturer": manu_pdf,
                "Detail_URL": current_url,
                "PDF_File": pdf_filename
            })

            # 7. GO BACK LOGIC
            # Instead of page.go_back(), we rely on the start of the loop 
            # to check URL and page.goto(search_results_url) if needed.
            # But let's try a simple back first to save bandwidth.
            try:
                page.go_back()
                wait_for_results(page, timeout=5000)
            except:
                print(">> Standard 'Back' failed. Will force reload in next loop.")

    except Exception as e:
        print(f"CRITICAL ERROR for {substance}: {e}")
        traceback.print_exc()
    finally:
        browser.close()

# --- 5. EXECUTION ---

if __name__ == "__main__":
    try:
        with sync_playwright() as pw:
            for sub in SUBSTANCES:
                run_scraper_for_substance(pw, sub)
    except Exception as e:
        print(f"System Error: {e}")

    print(f"\n\n{'='*30}\nDATABASE SAVING\n{'='*30}")
    
    if all_results:
        df = pd.DataFrame(all_results)
        print(df.head())
        
        db_path = Path("medicines_refined.db")
        try:
            conn = sqlite3.connect(db_path)
            df.to_sql("medicine_data", conn, if_exists="replace", index=False)
            print(f"Data saved to {db_path}")
            conn.close()
        except Exception as e:
            print(f"DB Error: {e}")
    else:
        print("No data extracted.")