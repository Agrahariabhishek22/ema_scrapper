# src/pdf_utils.py
from pdfminer.high_level import extract_text
import re

def extract_from_pdf(path):
    text = extract_text(path)
    # heuristics - add language-specific patterns
    patterns_mah = [
        r"Marketing Authorisation Holder[:\s]*([^\n]{2,200})",
        r"Titolare A.I.C\.[:\s]*([^\n]{2,200})",
        r"Titolare(?: del)?(?: AIC|A\.I\.C\.)[:\s]*([^\n]{2,200})",
        r"Titolare(?:.*)[:\s]*([A-Z][^\n]{5,200})"
    ]
    patterns_man = [
        r"Manufacturer[:\s]*([^\n]{2,200})",
        r"Producer[:\s]*([^\n]{2,200})",
        r"Producent[:\s]*([^\n]{2,200})",
        r"Produttore[:\s]*([^\n]{2,200})"
    ]
    mah = None
    manu = None
    for p in patterns_mah:
        m = re.search(p, text, re.I)
        if m:
            mah = m.group(1).strip()
            break
    for p in patterns_man:
        m = re.search(p, text, re.I)
        if m:
            manu = m.group(1).strip()
            break
    return {"mah": mah, "manufacturer": manu}
