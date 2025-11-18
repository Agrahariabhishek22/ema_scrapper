# src/ema_helpers.py
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin

EMA_URL = "https://www.ema.europa.eu/en/medicines/national-registers-authorised-medicines"

def get_country_links():
    r = requests.get(EMA_URL, timeout=20)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    links = {}
    for a in soup.find_all("a", href=True):
        href = a['href']
        text = (a.get_text() or "").lower()
        if "italy" in text or "aifa" in href or "medicinali.aifa.gov.it" in href:
            links['italy'] = href if href.startswith("http") else urljoin(EMA_URL, href)
        if "poland" in text or "polska" in text or "poland" in href:
            links['poland'] = href if href.startswith("http") else urljoin(EMA_URL, href)
    return links

if __name__ == "__main__":
    print(get_country_links())
