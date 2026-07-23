import hashlib
import json
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode, urljoin
from urllib.request import Request, urlopen

from bs4 import BeautifulSoup


KAYNAKLAR = [
    {
        "kaynak": "İŞKUR Kamu Memur Alımları",
        "tur": "Memur",
        "url": (
            "https://www.iskur.gov.tr/ilanlar/"
            "kamu-memur-alim-ilanlari/"
        ),
    },
    {
        "kaynak": "İŞKUR Kurum Dışı Kamu İşçi Alımları",
        "tur": "İşçi",
        "url": (
            "https://www.iskur.gov.tr/ilanlar/"
            "kurumdisi-kamu-isci-alim-ilanlari/"
        ),
    },
]

SEHIRLER = [
    ("adana", "Adana"),
    ("adiyaman", "Adıyaman"),
    ("afyonkarahisar", "Afyonkarahisar"),
    ("agri", "Ağrı"),
    ("aksaray", "Aksaray"),
    ("amasya", "Amasya"),
    ("ankara", "Ankara"),
    ("antalya", "Antalya"),
    ("ardahan", "Ardahan"),
    ("artvin", "Artvin"),
    ("aydin", "Aydın"),
    ("balikesir", "Balıkesir"),
    ("bartin", "Bartın"),
    ("batman", "Batman"),
    ("bayburt", "Bayburt"),
    ("bilecik", "Bilecik"),
    ("bingol", "Bingöl"),
    ("bitlis", "Bitlis"),
    ("bolu", "Bolu"),
    ("burdur", "Burdur"),
    ("bursa", "Bursa"),
    ("canakkale", "Çanakkale"),
    ("cankiri", "Çankırı"),
    ("corum", "Çorum"),
    ("denizli", "Denizli"),
    ("diyarbakir", "Diyarbakır"),
    ("duzce", "Düzce"),
    ("edirne", "Edirne"),
    ("elazig", "Elazığ"),
    ("erzincan", "Erzincan"),
    ("erzurum", "Erzurum"),
    ("eskisehir", "Eskişehir"),
    ("gaziantep", "Gaziantep"),
    ("giresun", "Giresun"),
    ("gumushane", "Gümüşhane"),
    ("hakkari", "Hakkari"),
    ("hatay", "Hatay"),
    ("igdir", "Iğdır"),
    ("isparta", "Isparta"),
    ("istanbul", "İstanbul"),
    ("izmir", "İzmir"),
    ("kahramanmaras", "Kahramanmaraş"),
    ("karabuk", "Karabük"),
    ("karaman", "Karaman"),
    ("kars", "Kars"),
    ("kastamonu", "Kastamonu"),
    ("kayseri", "Kayseri"),
    ("kirikkale", "Kırıkkale"),
    ("kirklareli", "Kırklareli"),
    ("kirsehir", "Kırşehir"),
    ("kilis", "Kilis"),
    ("kocaeli", "Kocaeli"),
    ("konya", "Konya"),
    ("kutahya", "Kütahya"),
    ("malatya", "Malatya"),
    ("manisa", "Manisa"),
    ("mardin", "Mardin"),
    ("mersin", "Mersin"),
    ("mugla", "Muğla"),
    ("mus", "Muş"),
    ("nevsehir", "Nevşehir"),
    ("nigde", "Niğde"),
    ("ordu", "Ordu"),
    ("osmaniye", "Osmaniye"),
    ("rize", "Rize"),
    ("sakarya", "Sakarya"),
    ("samsun", "Samsun"),
    ("siirt", "Siirt"),
    ("sinop", "Sinop"),
    ("sivas", "Sivas"),
    ("sanliurfa", "Şanlıurfa"),
    ("sirnak", "Şırnak"),
    ("tekirdag", "Tekirdağ"),
    ("tokat", "Tokat"),
    ("trabzon", "Trabzon"),
    ("tunceli", "Tunceli"),
    ("usak", "Uşak"),
    ("van", "Van"),
    ("yalova", "Yalova"),
    ("yozgat", "Yozgat"),
    ("zonguldak", "Zonguldak"),
]

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml",
    "Accept-Language": "tr-TR,tr;q=0.9",
}

TARIH_DESENI = re.compile(
    r"\b(\d{1,2}[./-]\d{1,2}[./-]\d{4}"
    r"(?:\s+\d{1,2}:\d{2})?)"
)


def temizle(metin):
    return re.sub(r"\s+", " ", metin or "").strip()


def sayfayi_indir(url):
    son_hata = None

    for deneme in range(3):
        try:
            istek = Request(url, headers=HEADERS)

            with urlopen(istek, timeout=30) as cevap:
                return cevap.read()

        except Exception as hata:
            son_hata = hata
            time.sleep(2 + deneme)

    raise RuntimeError(str(son_hata))


def tarih_bul(metin):
    sonuc = TARIH_DESENI.search(metin)
    return sonuc.group(1) if sonuc else ""


def baslik_temizle(metin):
    metin = temizle(metin)

    metin = TARIH_DESENI.sub(" ", metin)

    metin = re.sub(
        r"\(?\s*(bugün sona eriyor|son \d+ gün kaldı)\s*\)?",
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


def ilan_id_uret(link):
    return hashlib.sha256(
        link.encode("utf-8")
    ).hexdigest()[:20]


def satirdan_ilan_al(satir, kaynak, sehir):
    link_etiketi = satir.select_one(
        'a[href*="/medya/"], a[href$=".pdf"], a[href*=".pdf?"]'
    )

    if link_etiketi is None:
        return None

    href = temizle(link_etiketi.get("href", ""))

    if not href:
        return None

    link = urljoin(kaynak["url"], href)

    hucreler = satir.find_all("td")
    satir_metni = temizle(
        satir.get_text(" ", strip=True)
    )

    son_basvuru = tarih_bul(satir_metni)
    baslik = ""

    # İŞKUR tablosunda genelde:
    # 1. sütun tarih, 2. sütun başlık, 3. sütun PDF bağlantısıdır.
    if len(hucreler) >= 2:
        baslik = baslik_temizle(
            hucreler[1].get_text(" ", strip=True)
        )

    if len(baslik) < 4:
        adaylar = []

        for hucre in hucreler:
            aday = baslik_temizle(
                hucre.get_text(" ", strip=True)
            )

            if len(aday) >= 4:
                adaylar.append(aday)

        if adaylar:
            baslik = max(adaylar, key=len)

    if len(baslik) < 4:
        baslik = baslik_temizle(satir_metni)

    if len(baslik) < 4:
        return None

    return {
        "id": ilan_id_uret(link),
        "baslik": baslik[:400],
        "kurum": baslik[:250],
        "sehir": sehir,
        "tur": kaynak["tur"],
        "kaynak": kaynak["kaynak"],
        "son_basvuru": son_basvuru,
        "link": link,
        "basvuru_linki": link,
        "kpss_durumu": "İlan belgesinde belirtilmiştir",
    }


def sayfadan_ilanlari_al(kaynak, sehir_kodu, sehir_adi):
    sorgu = urlencode({
        "idId": sehir_kodu,
        "il": sehir_adi,
    })

    adres = f"{kaynak['url']}?{sorgu}"
    html = sayfayi_indir(adres)
    soup = BeautifulSoup(html, "html.parser")

    ilanlar = []

    for satir in soup.find_all("tr"):
        ilan = satirdan_ilan_al(
            satir,
            kaynak,
            sehir_adi,
        )

        if ilan:
            ilanlar.append(ilan)

    # Tablo yapısı değişirse yedek yöntem
    if not ilanlar:
        for link_etiketi in soup.select(
            'a[href*="/medya/"], a[href$=".pdf"]'
        ):
            kapsayici = link_etiketi.find_parent(
                ["tr", "li", "article"]
            )

            if kapsayici is None:
                kapsayici = link_etiketi.parent

            if kapsayici is None:
                continue

            ilan = satirdan_ilan_al(
                kapsayici,
                kaynak,
                sehir_adi,
            )

            if ilan:
                ilanlar.append(ilan)

    return ilanlar


def gorevi_calistir(kaynak, sehir_kodu, sehir_adi):
    try:
        ilanlar = sayfadan_ilanlari_al(
            kaynak,
            sehir_kodu,
            sehir_adi,
        )

        return ilanlar, None

    except Exception as hata:
        mesaj = (
            f"{kaynak['kaynak']} / {sehir_adi}: "
            f"{type(hata).__name__} - {str(hata)[:150]}"
        )

        return [], mesaj


def tarih_siralama_degeri(ilan):
    metin = ilan.get("son_basvuru", "")

    for bicim in (
        "%d.%m.%Y %H:%M",
        "%d/%m/%Y %H:%M",
        "%d-%m-%Y %H:%M",
        "%d.%m.%Y",
        "%d/%m/%Y",
        "%d-%m-%Y",
    ):
        try:
            return datetime.strptime(metin, bicim)
        except ValueError:
            continue

    return datetime.max


def main():
    tum_ilanlar = []
    hatalar = []

    gorevler = [
        (kaynak, sehir_kodu, sehir_adi)
        for kaynak in KAYNAKLAR
        for sehir_kodu, sehir_adi in SEHIRLER
    ]

    # Render yerine GitHub Actions çalıştıracağı için
    # uzun işlem uygulamanın açılışını engellemez.
    with ThreadPoolExecutor(max_workers=6) as havuz:
        futures = {
            havuz.submit(
                gorevi_calistir,
                kaynak,
                sehir_kodu,
                sehir_adi,
            ): (kaynak["kaynak"], sehir_adi)
            for kaynak, sehir_kodu, sehir_adi in gorevler
        }

        tamamlanan = 0

        for future in as_completed(futures):
            ilanlar, hata = future.result()
            tum_ilanlar.extend(ilanlar)

            if hata:
                hatalar.append(hata)

            tamamlanan += 1
            print(
                f"Tamamlandı: {tamamlanan}/{len(gorevler)} "
                f"- bulunan toplam ilan: {len(tum_ilanlar)}"
            )

    benzersiz = {}

    for ilan in tum_ilanlar:
        anahtar = ilan["link"].split("#")[0].casefold()
        benzersiz[anahtar] = ilan

    sonuc = list(benzersiz.values())
    sonuc.sort(key=tarih_siralama_degeri)

    cikti = {
        "status": "ok" if sonuc else "veri_alinamadi",
        "guncellenme_zamani": datetime.now(
            timezone.utc
        ).isoformat(),
        "ilan_sayisi": len(sonuc),
        "ilanlar": sonuc,
        "hata_sayisi": len(hatalar),
        "hatalar": hatalar[:30],
    }

    gecici_dosya = Path("ilanlar.json.tmp")
    asil_dosya = Path("ilanlar.json")

    gecici_dosya.write_text(
        json.dumps(
            cikti,
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    gecici_dosya.replace(asil_dosya)

    print("--------------------------------")
    print(f"Toplam ilan: {len(sonuc)}")
    print(f"Hata sayısı: {len(hatalar)}")
    print("ilanlar.json oluşturuldu.")


if __name__ == "__main__":
    main()
