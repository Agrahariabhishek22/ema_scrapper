import time
import traceback
import re
import pdfplumber
import sqlite3
import pandas as pd
from pathlib import Path
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout, Page, Playwright

# --- CONFIGURATION ---
AIFA_URL = "https://medicinali.aifa.gov.it/it/#/it/"
SUBSTANCES = ["Linezolid"]  # Aap yahan aur medicines add kar sakte hain
OUT = Path("debug_outputs")
OUT.mkdir(parents=True, exist_ok=True)

# DB mein save karne ke liye global list
all_results = []

# --- 1. PDF PARSING HELPER ---
def extract_details_from_pdf(pdf_path: Path):
    """PDF se text nikalta hai aur Titolare/Produttore dhoondhta hai."""
    ma_holder = "Not Found"
    manufacturer = "Not Found"
    
    try:
        with pdfplumber.open(pdf_path) as pdf:
            full_text = ""
            for page in pdf.pages:
                text = page.extract_text(x_tolerance=1, y_tolerance=3) 
                if text:
                    full_text += text + "\n"
        
        # 1. MA Holder (Titolare AIC)
        ma_pattern = re.compile(
            r"(Titolare dell'autorizzazione all'immissione in commercio|Titolare AIC):?\s*([\s\S]*?)(?:\n\n|\nProduttore|\n6\.\s*Contenuto della confezione|$)", 
            re.IGNORECASE | re.MULTILINE
        )
        match_ma = ma_pattern.search(full_text)
        if match_ma:
            lines = match_ma.group(2).strip().split('\n')
            ma_holder = next((line.strip() for line in lines if line.strip() and "Titolare" not in line), "Not Found")

        # 2. Manufacturer (Produttore)
        mfg_pattern = re.compile(
            r"(Produttore|Produttore responsabile del rilascio dei lotti):?\s*([\s\S]*?)(?:\n\n|\nTitolare|\nQuesto foglio illustrativo|$)",
            re.IGNORECASE | re.MULTILINE
        )
        match_mfg = mfg_pattern.search(full_text)
        if match_mfg:
            lines = match_mfg.group(2).strip().split('\n')
            manufacturer = next((line.strip() for line in lines if line.strip() and "Produttore" not in line), "Not Found")
        
        return ma_holder, manufacturer

    except Exception as e:
        print(f"Error processing file {pdf_path.name}: {e}")
        return f"Error: {e}", f"Error: {e}"

# --- 2. PLAYWRIGHT HELPER FUNCTIONS ---

def accept_modal(page: Page):
    """Disclaimer modal ko handle karta hai."""
    time.sleep(1)
    try:
        # Checkbox click
        checkbox = page.locator("#disclaimercheck")
        if checkbox.is_visible():
            checkbox.click()
            time.sleep(0.5)
        
        # Button click
        btn = page.locator('button.btn.btn-outline-secondary:not([disabled])')
        if btn.is_visible():
            btn.click()
            time.sleep(1)
            return True
    except Exception:
        pass
    return False

def get_visible_cards(page: Page):
    """Result page par jitne cards dikh rahe hain unhe list mein return karta hai."""
    try:
        # Selector wahi rakha hai jo kaam kar raha tha
        cards = page.query_selector_all("app-forma-dosaggio, .custom-card-result, a[href*='/dettaglio/']")
        visible = [c for c in cards if c.is_visible()]
        return visible
    except Exception:
        return []

def wait_for_results(page: Page, timeout=10000):
    """Wait karta hai jab tak result list load na ho jaye."""
    try:
        page.wait_for_selector("app-forma-dosaggio, a[href*='/dettaglio/']", state="visible", timeout=timeout)
        return True
    except Exception:
        return False

def wait_for_detail_ready(page: Page, timeout=15000):
    """Detail page load hone ka wait karta hai."""
    try:
        page.wait_for_load_state("domcontentloaded")
        # Owner ya H1 ka wait karo
        page.wait_for_selector("h1, p:has-text('Azienda titolare')", timeout=timeout)
        return True
    except Exception:
        return False

# --- 3. MAIN SCRAPER FUNCTION (FIXED) ---

def run_scraper_for_substance(pw: Playwright, substance: str):
    browser = None
    page = None
    print(f"\n\n{'='*11} PROCESSING: {substance} {'='*11}")
    
    try:
        browser = pw.chromium.launch(headless=False, slow_mo=50)
        context = browser.new_context(accept_downloads=True)
        page = context.new_page()
        
        print("Opening AIFA...")
        page.goto(AIFA_URL, timeout=60000)
        accept_modal(page)

        # --- SEARCH ---
        print(f"Searching for: {substance}")
        try:
            page.locator("input.mat-mdc-autocomplete-trigger").fill(substance)
            time.sleep(0.5)
            page.keyboard.press("Enter")
        except Exception as e:
            print(f"Search input failed: {e}")
            return

        if not wait_for_results(page, timeout=15000):
            print("Results load nahi huye (Timeout). Skipping.")
            return
        
        # URL save kar lo taaki baad mein wapas aa sakein
        search_results_url = page.url
        
        # --- FIX: DYNAMIC COUNTING ---
        # Pehli baar cards count karo
        cards_list = get_visible_cards(page)
        total_cards = len(cards_list)
        print(f"Found {total_cards} medicines for {substance}.")

        if total_cards == 0:
            print("No medicines found.")
            return

        # Loop utni baar chalega jitne cards mile (11 mile to 11, 20 mile to 20)
        for idx in range(total_cards):
            print(f"\n--- Processing Card {idx + 1} of {total_cards} ---")

            # Step A: Ensure we are on the results page
            if page.url != search_results_url:
                print("Navigating back to results...")
                page.goto(search_results_url)
                wait_for_results(page)
                time.sleep(1) # Thoda stability wait

            # Step B: Re-fetch cards (DOM refresh ho jata hai back aane par)
            current_cards = get_visible_cards(page)
            
            # FIX: Agar index available cards se zyada ho jaye (mismatch handling)
            if idx >= len(current_cards):
                print(f"Warning: Sirf {len(current_cards)} cards mile, lekin hum index {idx} dhund rahe the. Loop break.")
                break

            card = current_cards[idx]

            # Step C: Click Card
            try:
                card.scroll_into_view_if_needed()
                card.click(timeout=5000)
            except Exception as e:
                print(f"Card click failed: {e}")
                continue # Agle card par jao

            # Step D: Detail Page Processing
            if not wait_for_detail_ready(page):
                print("Detail page load nahi hua.")
                continue

            # Data Extraction
            product_name = page.locator("h1").inner_text().strip() if page.locator("h1").is_visible() else "(no name)"
            
            ma_holder_html = "Not Found"
            owner_locator = page.locator("p:has-text('Azienda titolare'), p:has-text('Owner:')").first
            if owner_locator.is_visible():
                txt = owner_locator.inner_text()
                ma_holder_html = txt.split(":", 1)[1].strip() if ":" in txt else txt

            print(f"Product: {product_name}")

            # PDF Download Logic
            pdf_filename = "Not Found"
            ma_holder_pdf = "Not Found in PDF"
            manufacturer_pdf = "Not Found in PDF"
            
            pdf_btn = page.locator('a:has-text("Foglio Illustrativo")')
            if pdf_btn.is_visible():
                try:
                    with page.expect_download(timeout=10000) as download_info:
                        pdf_btn.click()
                    download = download_info.value
                    safe_filename = f"{substance}_{idx+1}.pdf".replace(" ", "_")
                    save_path = OUT / safe_filename
                    download.save_as(save_path)
                    
                    pdf_filename = safe_filename
                    print("PDF Downloaded.")
                    
                    # PDF Read
                    ma_holder_pdf, manufacturer_pdf = extract_details_from_pdf(save_path)
                except Exception as e:
                    print(f"PDF handling error: {e}")
            
            # Final Data Save
            final_ma = ma_holder_html if ma_holder_html != "Not Found" else ma_holder_pdf
            
            all_results.append({
                "Search_Substance": substance,
                "Product_Name": product_name,
                "MA_Holder": final_ma,
                "Manufacturer": manufacturer_pdf,
                "PDF_File": pdf_filename
            })

            # Loop continue karega, agla iteration wapas upar jayega aur Step A mein "Back" karega
            
    except Exception as e:
        print(f"Critical Error for {substance}: {e}")
        traceback.print_exc()
    finally:
        if browser:
            browser.close()
            print(f"Browser closed for {substance}.")

# --- 4. SCRIPT EXECUTION ---
if __name__ == "__main__":
    print("--- Playwright Scraper Started ---")
    try:
        with sync_playwright() as pw:
            for substance_name in SUBSTANCES:
                run_scraper_for_substance(pw, substance_name)
    except Exception as e:
        print(f"Main Process Error: {e}")

    print("\n" + "="*30)
    print("--- SAVING DATA ---")
    
    if all_results:
        df = pd.DataFrame(all_results)
        print(df)
        
        # Save to DB
        try:
            conn = sqlite3.connect("medicines.db")
            df.to_sql("medicine_data", conn, if_exists="replace", index=False)
            print("Data saved to medicines.db")
            conn.close()
        except Exception as e:
            print(f"DB Error: {e}")
            # CSV backup
            df.to_csv("medicines_backup.csv", index=False)
            print("Saved to CSV as backup.")
    else:
        print("No data extracted.")