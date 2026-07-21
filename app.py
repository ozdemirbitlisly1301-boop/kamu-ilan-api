from flask import Flask, jsonify
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin

app = Flask(__name__)

@app.route("/")
def home():
    site = "https://kamuilan.sbb.gov.tr/"

   try:
    response = requests.get(
        site,
        headers={"User-Agent": "Mozilla/5.0"},
        timeout=10
    )
    response.raise_for_status()
except requests.RequestException as hata:
    return jsonify({
        "hata": "İlan sitesine bağlanılamadı",
        "detay": str(hata),
        "ilanlar": []
    }), 200

    soup = BeautifulSoup(response.text, "html.parser")
    ilanlar = []
    eklenenler = set()

    for link in soup.find_all("a", href=True):
        href = link["href"]
        baslik = " ".join(link.get_text(" ", strip=True).split())

        if "ilanDetay.aspx" in href and baslik:
            tam_url = urljoin(site, href)

            if tam_url not in eklenenler:
                eklenenler.add(tam_url)
                ilanlar.append({
                    "baslik": baslik,
                    "kurum": "Kamu Personeli Alım İlanları",
                    "url": tam_url
                })

        if len(ilanlar) >= 20:
            break

    return jsonify({
        "site_durumu": response.status_code,
        "ilan_sayisi": len(ilanlar),
        "ilanlar": ilanlar
    })

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
