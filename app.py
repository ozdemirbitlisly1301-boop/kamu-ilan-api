from flask import Flask, jsonify
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin

app = Flask(__name__)

KAYNAK = "https://www.ilan.gov.tr/ilan/kategori/44/kamu-personel-alim-ve-sinavlari"


@app.route("/")
def ilanlari_getir():
    response = requests.get(
        KAYNAK,
        headers={"User-Agent": "Mozilla/5.0"},
        timeout=20,
        verify=False,
    )
    response.raise_for_status()

    soup = BeautifulSoup(response.text, "html.parser")
    ilanlar = []
    eklenenler = set()

    for link in soup.select('a[href*="/ilan/"]'):
        baslik = link.get_text(" ", strip=True)
        adres = urljoin(KAYNAK, link.get("href", ""))

        if (
            len(baslik) > 10
            and "/kategori/" not in adres
            and adres not in eklenenler
        ):
            ilanlar.append({
                "baslik": baslik,
                "url": adres,
                "kaynak": "ilan.gov.tr",
            })
            eklenenler.add(adres)

    return jsonify({
        "ilan_sayisi": len(ilanlar),
        "ilanlar": ilanlar[:30],
    })


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
