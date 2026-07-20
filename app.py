from flask import Flask, jsonify
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin

app = Flask(__name__)

@app.route("/")
def home():
    url = "https://www.ilan.gov.tr/ilan/kategori/44/kamu-personel-alim-ve-sinavlari"

    headers = {
        "User-Agent": "Mozilla/5.0"
    }

    response = requests.get(
        url,
        headers=headers,
        verify=False,
        timeout=20
    )

    soup = BeautifulSoup(response.text, "html.parser")
    print(response.status_code)
    print(response.text[:500])
    ilanlar = []
    eklenenler = set()

    for link in soup.find_all("a", href=True):
        href = link.get("href", "")
        baslik = " ".join(link.get_text(" ", strip=True).split())

        if (
            "/ilan/" in href
            and "/kategori/" not in href
            and len(baslik) > 15
        ):
            tam_url = urljoin("https://www.ilan.gov.tr", href)

            if tam_url not in eklenenler:
                eklenenler.add(tam_url)

                ilanlar.append({
                    "baslik": baslik,
                    "kurum": "Resmî İlan",
                    "url": tam_url
                })

        if len(ilanlar) == 20:
            break

    return jsonify({
        "ilan_sayisi": len(ilanlar),
        "ilanlar": ilanlar
    })

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
