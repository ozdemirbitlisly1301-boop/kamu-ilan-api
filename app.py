from flask import Flask, jsonify
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin

app = Flask(__name__)

@app.route("/")
def home():
    site = "https://www.ilan.gov.tr/ilan/kategori/44/kamu-personel-alim-ve-sinavlari"

    try:
        response = requests.get(
            site,
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=15
            verify=False
        )
        response.raise_for_status()
    except requests.RequestException as hata:
        return jsonify({
            "hata": str(hata),
            "ilanlar": []
        }), 200

    soup = BeautifulSoup(response.text, "html.parser")
    ilanlar = []
    eklenenler = set()

    for link in soup.find_all("a", href=True):
        baslik = " ".join(link.get_text(" ", strip=True).split())
        href = link["href"]
        tam_url = urljoin(site, href)

        if (
            baslik
            and "/ilan/" in tam_url
            and tam_url not in eklenenler
            and len(baslik) > 15
        ):
            eklenenler.add(tam_url)
            ilanlar.append({
                "baslik": baslik,
                "kurum": "Resmî Kamu İlanı",
                "url": tam_url
            })

        if len(ilanlar) >= 20:
            break

    return jsonify({
        "status": "ok",
        "ilan_sayisi": len(ilanlar),
        "ilanlar": ilanlar
    })

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
