from flask import Flask, jsonify
import requests
import xml.etree.ElementTree as ET
from urllib.parse import quote, urlparse
from email.utils import parsedate_to_datetime

app = Flask(__name__)

ARAMALAR = [
    {
        "kaynak": "Kariyer Kapısı",
        "sorgu": 'site:kariyerkapisi.gov.tr/IlanDetay "personel alım"'
    },
    {
        "kaynak": "İlan.gov.tr",
        "sorgu": 'site:ilan.gov.tr "personel alımı"'
    }
]

IZINLI_ALANLAR = {
    "kariyerkapisi.gov.tr",
    "www.kariyerkapisi.gov.tr",
    "ilan.gov.tr",
    "www.ilan.gov.tr"
}


def tarih_duzenle(tarih_metni):
    try:
        tarih = parsedate_to_datetime(tarih_metni)
        return tarih.strftime("%Y-%m-%d")
    except Exception:
        return ""


def ilanlari_getir():
    ilanlar = []
    eklenen_linkler = set()

    headers = {
        "User-Agent": "Mozilla/5.0"
    }

    for arama in ARAMALAR:
        rss_url = (
            "https://www.bing.com/search"
            f"?format=rss&q={quote(arama['sorgu'])}"
        )

        try:
            response = requests.get(
                rss_url,
                headers=headers,
                timeout=20
            )
            response.raise_for_status()

            root = ET.fromstring(response.content)

            for item in root.findall(".//item"):
                baslik = item.findtext("title", "").strip()
                link = item.findtext("link", "").strip()
                tarih = item.findtext("pubDate", "").strip()

                alan_adi = urlparse(link).netloc.lower()

                if (
                    not baslik
                    or not link
                    or alan_adi not in IZINLI_ALANLAR
                    or link in eklenen_linkler
                ):
                    continue

                eklenen_linkler.add(link)

                ilanlar.append({
                    "baslik": baslik,
                    "kurum": arama["kaynak"],
                    "tarih": tarih_duzenle(tarih),
                    "url": link
                })

        except Exception as hata:
            print(f"{arama['kaynak']} hatası: {hata}")

    ilanlar.sort(
        key=lambda ilan: ilan.get("tarih", ""),
        reverse=True
    )

    return ilanlar[:40]


@app.route("/")
def home():
    ilanlar = ilanlari_getir()

    return jsonify({
        "status": "ok",
        "ilan_sayisi": len(ilanlar),
        "ilanlar": ilanlar
    })


@app.route("/health")
def health():
    return jsonify({
        "status": "çalışıyor"
    })


if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=10000
    )
