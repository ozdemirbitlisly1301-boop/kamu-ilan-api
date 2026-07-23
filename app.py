from flask import Flask, jsonify
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
import re
import threading

app = Flask(__name__)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 Chrome/126.0 Safari/537.36"
    ),
    "Accept-Language": "tr-TR,tr;q=0.9,en;q=0.8",
}

KAYNAKLAR = [
    {
        "ad": "İŞKUR Kamu Memur",
        "tur": "Memur",
        "url": "https://www.iskur.gov.tr/ilanlar/kamu-memur-alim-ilanlari/",
    },
    {
        "ad": "İŞKUR Kamu İşçi",
        "tur": "İşçi",
        "url": "https://www.iskur.gov.tr/ilanlar/kurumdisi-kamu-isci-alim-ilanlari/",
    },
]

SEHIRLER = {
    "adana": "Adana",
    "adiyaman": "Adıyaman",
    "afyonkarahisar": "Afyonkarahisar",
    "agri": "Ağrı",
    "aksaray": "Aksaray",
    "amasya": "Amasya",
    "ankara": "Ankara",
    "antalya": "Antalya",
    "ardahan": "Ardahan",
    "artvin": "Artvin",
    "aydin": "Aydın",
    "balikesir": "Balıkesir",
    "bartin": "Bartın",
    "batman": "Batman",
    "bayburt": "Bayburt",
    "bilecik": "Bilecik",
    "bingol": "Bingöl",
    "bitlis": "Bitlis",
    "bolu": "Bolu",
    "burdur": "Burdur",
    "bursa": "Bursa",
    "canakkale": "Çanakkale",
    "cankiri": "Çankırı",
    "corum": "Çorum",
    "denizli": "Denizli",
    "diyarbakir": "Diyarbakır",
    "duzce": "Düzce",
    "edirne": "Edirne",
    "elazig": "Elazığ",
    "erzincan": "Erzincan",
    "erzurum": "Erzurum",
    "eskisehir": "Eskişehir",
    "gaziantep": "Gaziantep",
    "giresun": "Giresun",
    "gumushane": "Gümüşhane",
    "hakkari": "Hakkari",
    "hatay": "Hatay",
    "igdir": "Iğdır",
    "isparta": "Isparta",
    "istanbul": "İstanbul",
    "izmir": "İzmir",
    "kahramanmaras": "Kahramanmaraş",
    "karabuk": "Karabük",
    "karaman": "Karaman",
    "kars": "Kars",
    "kastamonu": "Kastamonu",
    "kayseri": "Kayseri",
    "kirikkale": "Kırıkkale",
    "kirklareli": "Kırklareli",
    "kirsehir": "Kırşehir",
    "kilis": "Kilis",
    "kocaeli": "Kocaeli",
    "konya": "Konya",
    "kutahya": "Kütahya",
    "malatya": "Malatya",
    "manisa": "Manisa",
    "mardin": "Mardin",
    "mersin": "Mersin",
    "mugla": "Muğla",
    "mus": "Muş",
    "nevsehir": "Nevşehir",
    "nigde": "Niğde",
    "ordu": "Ordu",
    "osmaniye": "Osmaniye",
    "rize": "Rize",
    "sakarya": "Sakarya",
    "samsun": "Samsun",
    "siirt": "Siirt",
    "sinop": "Sinop",
    "sivas": "Sivas",
    "sanliurfa": "Şanlıurfa",
    "sirnak": "Şırnak",
    "tekirdag": "Tekirdağ",
    "tokat": "Tokat",
    "trabzon": "Trabzon",
    "tunceli": "Tunceli",
    "usak": "Uşak",
    "van": "Van",
    "yalova": "Yalova",
    "yozgat": "Yozgat",
    "zonguldak": "Zonguldak",
}

CACHE = {
    "zaman": None,
    "ilanlar": [],
    "hatalar": [],
}

CACHE_LOCK = threading.Lock()
CACHE_SURESI = timedelta(minutes=20)


def temizle(metin):
    return re.sub(r"\s+", " ", metin or "").strip()


def tarih_bul(metin):
    eslesme = re.search(
        r"\b(\d{1,2}[./-]\d{1,2}[./-]\d{4})(?:\s+\d{1,2}:\d{2})?",
        metin,
    )
    return eslesme.group(0) if eslesme else ""


def baslik_temizle(metin):
    metin = temizle(metin)

    metin = re.sub(
        r"\b\d{1,2}[./-]\d{1,2}[./-]\d{4}"
        r"(?:\s+\d{1,2}:\d{2})?",
        " ",
        metin,
    )

    metin = re.sub(
        r"\(?\s*son\s+\d+\s+gün\s+kaldı\s*\)?",
        " ",
        metin,
        flags=re.IGNORECASE,
    )

    metin = re.sub(
        r"İlanın Son Tarihi|İlanın Başlığı|İlanın Detayı",
        " ",
        metin,
        flags=re.IGNORECASE,
    )

    return temizle(metin).strip(" -|")


def ilan_linki_mi(href):
    if not href:
        return False

    href_kucuk = href.lower()

    engellenenler = [
        "facebook.com",
        "instagram.com",
        "youtube.com",
        "x.com",
        "maps.app",
        "javascript:",
        "mailto:",
        "tel:",
    ]

    if any(kelime in href_kucuk for kelime in engellenenler):
        return False

    return (
        "/medya/" in href_kucuk
        or href_kucuk.endswith(".pdf")
        or "ilan" in href_kucuk
    )


def sayfadan_ilanlari_al(kaynak, sehir_kodu, sehir_adi):
    url = (
        f"{kaynak['url']}?"
        f"idId={sehir_kodu}&il={requests.utils.quote(sehir_adi)}"
    )

    response = requests.get(
        url,
        headers=HEADERS,
        timeout=15,
    )
    response.raise_for_status()

    soup = BeautifulSoup(response.text, "html.parser")
    bulunanlar = []

    # Önce tablo satırlarını kontrol et
    for satir in soup.select("tr"):
        link = satir.find("a", href=True)

        if not link:
            continue

        href = link.get("href", "").strip()

        if not ilan_linki_mi(href):
            continue

        satir_metni = temizle(satir.get_text(" ", strip=True))

        if len(satir_metni) < 8:
            continue

        tam_link = urljoin(url, href)
        tarih = tarih_bul(satir_metni)
        baslik = baslik_temizle(satir_metni)

        if not baslik:
            baslik = temizle(link.get_text(" ", strip=True))

        if baslik and len(baslik) >= 5:
            bulunanlar.append(
                {
                    "baslik": baslik[:300],
                    "kurum": baslik[:150],
                    "sehir": sehir_adi,
                    "tur": kaynak["tur"],
                    "kaynak": kaynak["ad"],
                    "son_basvuru": tarih,
                    "link": tam_link,
                    "basvuru_linki": tam_link,
                }
            )

    # Bazı İŞKUR sayfalarında ilanlar div/list şeklinde olabilir
    for link in soup.select("a[href]"):
        href = link.get("href", "").strip()

        if not ilan_linki_mi(href):
            continue

        tam_link = urljoin(url, href)

        kapsayici = link.find_parent(["li", "article", "tr"])

        if kapsayici is None:
            kapsayici = link.parent

        metin = temizle(
            kapsayici.get_text(" ", strip=True)
            if kapsayici
            else link.get_text(" ", strip=True)
        )

        link_metni = temizle(link.get_text(" ", strip=True))

        if len(metin) < 8:
            metin = link_metni

        baslik = baslik_temizle(metin)
        tarih = tarih_bul(metin)

        yasak_basliklar = [
            "kamu memur alım ilanları",
            "kurumdışı kamu işçi alım ilanları",
            "ana sayfa",
            "ilanlar",
            "daha fazla",
        ]

        if (
            not baslik
            or len(baslik) < 5
            or baslik.lower() in yasak_basliklar
        ):
            continue

        bulunanlar.append(
            {
                "baslik": baslik[:300],
                "kurum": baslik[:150],
                "sehir": sehir_adi,
                "tur": kaynak["tur"],
                "kaynak": kaynak["ad"],
                "son_basvuru": tarih,
                "link": tam_link,
                "basvuru_linki": tam_link,
            }
        )

    return bulunanlar


def tum_ilanlari_topla():
    ilanlar = []
    hatalar = []

    gorevler = []

    for kaynak in KAYNAKLAR:
        for sehir_kodu, sehir_adi in SEHIRLER.items():
            gorevler.append((kaynak, sehir_kodu, sehir_adi))

    with ThreadPoolExecutor(max_workers=18) as executor:
        future_map = {
            executor.submit(
                sayfadan_ilanlari_al,
                kaynak,
                sehir_kodu,
                sehir_adi,
            ): (kaynak["ad"], sehir_adi)
            for kaynak, sehir_kodu, sehir_adi in gorevler
        }

        for future in as_completed(future_map):
            kaynak_adi, sehir_adi = future_map[future]

            try:
                ilanlar.extend(future.result())
            except Exception as hata:
                hatalar.append(
                    f"{kaynak_adi} / {sehir_adi}: "
                    f"{type(hata).__name__}"
                )

    # Aynı linkteki ilanları tekilleştir
    benzersiz = {}

    for ilan in ilanlar:
        link = ilan.get("link", "").split("#")[0].rstrip("/")

        if not link:
            continue

        anahtar = link.lower()

        if anahtar not in benzersiz:
            ilan["id"] = str(abs(hash(anahtar)))
            benzersiz[anahtar] = ilan

    sonuc = list(benzersiz.values())

    sonuc.sort(
        key=lambda ilan: (
            ilan.get("son_basvuru", ""),
            ilan.get("baslik", ""),
        ),
        reverse=True,
    )

    return sonuc, hatalar


def cache_gecerli_mi():
    zaman = CACHE["zaman"]

    return (
        zaman is not None
        and datetime.utcnow() - zaman < CACHE_SURESI
        and len(CACHE["ilanlar"]) > 0
    )


@app.route("/", methods=["GET"])
def ana_sayfa():
    with CACHE_LOCK:
        if cache_gecerli_mi():
            return jsonify(
                {
                    "status": "ok",
                    "ilan_sayisi": len(CACHE["ilanlar"]),
                    "ilanlar": CACHE["ilanlar"],
                    "cache": True,
                    "hata_sayisi": len(CACHE["hatalar"]),
                }
            )

    ilanlar, hatalar = tum_ilanlari_topla()

    with CACHE_LOCK:
        CACHE["zaman"] = datetime.utcnow()
        CACHE["ilanlar"] = ilanlar
        CACHE["hatalar"] = hatalar

    return jsonify(
        {
            "status": "ok" if ilanlar else "veri_alinamadi",
            "ilan_sayisi": len(ilanlar),
            "ilanlar": ilanlar,
            "cache": False,
            "hata_sayisi": len(hatalar),
            "hatalar": hatalar[:10],
        }
    )


@app.route("/yenile", methods=["GET"])
def yenile():
    ilanlar, hatalar = tum_ilanlari_topla()

    with CACHE_LOCK:
        CACHE["zaman"] = datetime.utcnow()
        CACHE["ilanlar"] = ilanlar
        CACHE["hatalar"] = hatalar

    return jsonify(
        {
            "status": "ok" if ilanlar else "veri_alinamadi",
            "ilan_sayisi": len(ilanlar),
            "ilanlar": ilanlar,
            "hata_sayisi": len(hatalar),
            "hatalar": hatalar[:10],
        }
    )


@app.route("/saglik", methods=["GET"])
def saglik():
    return jsonify(
        {
            "status": "calisiyor",
            "zaman": datetime.utcnow().isoformat() + "Z",
        }
    )


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
