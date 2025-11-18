# src/main.py
from ema_helpers import get_country_links
from country_aifa import search_aifa_and_download
from pdf_utils import extract_from_pdf
import pandas as pd

SUBSTANCES = ["Linezolid","Aripiprazole","Pantoprazole","Quetiapine","Methotrexate"]

def main():
    links = get_country_links()
    results = []
    if 'italy' in links:
        for s in SUBSTANCES:
            pdf_path = search_aifa_and_download(links['italy'], s)
            if pdf_path:
                info = extract_from_pdf(pdf_path)
                results.append({
                    "country": "Italy",
                    "substance": s,
                    "pdf": pdf_path,
                    "mah": info.get("mah"),
                    "manufacturer": info.get("manufacturer")
                })
    df = pd.DataFrame(results)
    df.to_csv("outputs/results.csv", index=False)
    print("Saved outputs/results.csv")

if __name__ == "__main__":
    main()
