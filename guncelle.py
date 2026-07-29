import hashlib
import io
import json
import os
import re
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode, urljoin
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup
from pypdf import PdfReader

try:
    import firebase_admin
    from firebase_admin import credentials, messaging
except ImportError:
    firebase_admin = None
    credentials = None
    messaging = None


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

OSYM_ARAMA_URL = "https://www.osym.gov.tr/arama"
OSYM_ARAMA_TERIMLERI = (
    "kpss tercih",
    "kpss yerleştirme",
    "kpss kadro pozisyon",
)

OSYM_HABER_ARAMA_TERIMLERI = (
    "kpss sonuçları",
    "kpss yerleştirme sonuçları",
    "kpss taban puanlar",
    "kpss atama sonuçları",
    "kpss tercih kılavuzu",
)

RESMI_DUYURU_KAYNAKLARI = (
    {
        "kaynak": "GSB Personel Alımları",
        "kurum": "Gençlik ve Spor Bakanlığı",
        "url": "https://www.gsb.gov.tr/tr/duyurular/",
        "link_parcalari": ("/tr/duyuru/", "/tr/haber-detay/"),
    },
    {
        "kaynak": "Aile Bakanlığı Personel Alımları",
        "kurum": "Aile ve Sosyal Hizmetler Bakanlığı",
        "url": "https://www.aile.gov.tr/pgm/duyurular",
        "link_parcalari": ("/pgm/duyurular/",),
    },
    {
        "kaynak": "Adalet Bakanlığı Personel Alımları",
        "kurum": "Adalet Bakanlığı",
        "url": "https://pgm.adalet.gov.tr/Home/",
        "link_parcalari": ("/Home/SayfaDetay/",),
    },
)

TURKCE_AYLAR = {
    "ocak": 1,
    "subat": 2,
    "mart": 3,
    "nisan": 4,
    "mayis": 5,
    "haziran": 6,
    "temmuz": 7,
    "agustos": 8,
    "eylul": 9,
    "ekim": 10,
    "kasim": 11,
    "aralik": 12,
}

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
    "Accept": "text/html,application/xhtml+xml,application/pdf,*/*",
    "Accept-Language": "tr-TR,tr;q=0.9",
}

TARIH_DESENI = re.compile(
    r"\b(\d{1,2}[./-]\d{1,2}[./-]\d{4}"
    r"(?:\s+\d{1,2}:\d{2})?)"
)

ANALIZ_SURUMU = 2
MAKSIMUM_PDF_BOYUTU = 30 * 1024 * 1024
MAKSIMUM_PDF_SAYFASI = 40


TR_CEVIRI = str.maketrans({
    "ç": "c", "Ç": "c",
    "ğ": "g", "Ğ": "g",
    "ı": "i", "İ": "i",
    "ö": "o", "Ö": "o",
    "ş": "s", "Ş": "s",
    "ü": "u", "Ü": "u",
})


def temizle(metin):
    return re.sub(r"\s+", " ", metin or "").strip()


def arama_metnine_cevir(metin):
    metin = (metin or "").translate(TR_CEVIRI)
    metin = unicodedata.normalize("NFKD", metin)
    metin = "".join(
        karakter
        for karakter in metin
        if not unicodedata.combining(karakter)
    )
    return temizle(metin.casefold())


def sayfayi_indir(url, params=None):
    son_hata = None

    for deneme in range(3):
        try:
            cevap = requests.get(
                url,
                params=params,
                headers=HEADERS,
                timeout=(10, 35),
            )
            cevap.raise_for_status()
            return cevap.content
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


def link_anahtari(link):
    return (link or "").split("#")[0].strip().casefold()


def pdf_linki_mi(href):
    href = (href or "").casefold()
    return "/medya/" in href or ".pdf" in href


def satirdan_ilan_al(satir, kaynak, sehir, kaynak_sayfa_linki):
    link_etiketi = None

    for aday in satir.find_all("a", href=True):
        if pdf_linki_mi(aday.get("href")):
            link_etiketi = aday
            break

    if link_etiketi is None:
        return None

    href = temizle(link_etiketi.get("href", ""))

    if not href:
        return None

    belge_linki = urljoin(kaynak["url"], href)
    hucreler = satir.find_all("td")
    satir_metni = temizle(satir.get_text(" ", strip=True))
    son_basvuru = tarih_bul(satir_metni)
    baslik = ""

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
        "id": ilan_id_uret(belge_linki),
        "baslik": baslik[:400],
        "kurum": baslik[:250],
        "sehir": sehir,
        "tur": kaynak["tur"],
        "kaynak": kaynak["kaynak"],
        "son_basvuru": son_basvuru,
        # Geriye dönük uyumluluk için link alanı belgeyi gösterir.
        "link": belge_linki,
        "belge_linki": belge_linki,
        "kaynak_sayfa_linki": kaynak_sayfa_linki,
        "basvuru_linki": "",
        "basvuru_online": False,
        "basvuru_aciklamasi": (
            "Başvuru yöntemi ilan belgesinden kontrol edilmelidir."
        ),
    }


def sayfadan_ilanlari_al(kaynak, sehir_kodu, sehir_adi):
    parametreler = {
        "idId": sehir_kodu,
        "il": sehir_adi,
    }
    html = sayfayi_indir(
        kaynak["url"],
        params=parametreler,
    )
    kaynak_sayfa_linki = (
        f"{kaynak['url']}?{urlencode(parametreler)}"
    )

    soup = BeautifulSoup(html, "html.parser")
    ilanlar = []

    for satir in soup.find_all("tr"):
        ilan = satirdan_ilan_al(
            satir,
            kaynak,
            sehir_adi,
            kaynak_sayfa_linki,
        )

        if ilan:
            ilanlar.append(ilan)

    if not ilanlar:
        for link_etiketi in soup.find_all("a", href=True):
            if not pdf_linki_mi(link_etiketi.get("href")):
                continue

            kapsayici = link_etiketi.find_parent(
                ["tr", "li", "article", "div"]
            )

            if kapsayici is None:
                continue

            ilan = satirdan_ilan_al(
                kapsayici,
                kaynak,
                sehir_adi,
                kaynak_sayfa_linki,
            )

            if ilan:
                ilanlar.append(ilan)

    return ilanlar


def osym_aktif_duyuru_mu(baslik):
    """Yalnızca başvuru/tercih süreci devam eden ÖSYM duyurularını kabul eder."""
    normal_baslik = arama_metnine_cevir(baslik)

    # Sonuç ve sınav sonrası haberleri uygulamada açık ilan gibi göstermeyelim.
    engellenen_ifadeler = (
        "sonuclari aciklandi",
        "sonuc aciklandi",
        "yerlestirme sonuclari",
        "sinav sonuclari",
        "cevap kagidi",
        "cevap anahtari",
        "soru kitapcigi",
        "degerlendirme raporu",
        "sayisal bilgiler",
        "taban puanlar",
        "ek yerlestirme sonuclari",
    )

    if any(ifade in normal_baslik for ifade in engellenen_ifadeler):
        return False

    aktif_ifadeler = (
        "tercih",
        "basvuru",
        "kilavuz",
        "kadro ve pozisyon",
    )

    return "kpss" in normal_baslik and any(
        ifade in normal_baslik for ifade in aktif_ifadeler
    )


def haber_kategorisi_bul(baslik):
    normal = arama_metnine_cevir(baslik)

    if "taban puan" in normal or "tavan puan" in normal:
        return "Taban-Tavan Puan"
    if "sonuc" in normal or "yerlestirme" in normal:
        return "Sonuç"
    if "atama" in normal or "atamaya hak" in normal:
        return "Atama"
    if "kilavuz" in normal or "tercih" in normal:
        return "Tercih ve Kılavuz"
    if "mulakat" in normal or "sozlu sinav" in normal:
        return "Mülakat"

    return "Kamu Personeli"


def osym_haber_duyurusu_mu(baslik):
    normal = arama_metnine_cevir(baslik)

    if "kpss" not in normal:
        return False

    haber_ifadeleri = (
        "sonuc",
        "yerlestirme",
        "taban puan",
        "tavan puan",
        "atama",
        "kilavuz",
        "kadro ve pozisyon",
    )

    return any(ifade in normal for ifade in haber_ifadeleri)


def osym_kpss_haberlerini_al():
    """ÖSYM sonuç, taban-tavan puan, atama ve kılavuz haberlerini alır."""
    haberler = []
    gorulen_linkler = set()

    for arama_terimi in OSYM_HABER_ARAMA_TERIMLERI:
        html = sayfayi_indir(
            OSYM_ARAMA_URL,
            params={
                "_Dil": "1",
                "aranan": arama_terimi,
            },
        )
        soup = BeautifulSoup(html, "html.parser")

        for link_etiketi in soup.find_all("a", href=True):
            baslik = temizle(link_etiketi.get_text(" ", strip=True))
            href = temizle(link_etiketi.get("href", ""))

            if len(baslik) < 15 or not href:
                continue

            if not osym_haber_duyurusu_mu(baslik):
                continue

            if href.startswith("#") or href.casefold().startswith("javascript:"):
                continue

            link = urljoin(OSYM_ARAMA_URL, href)
            normal_link = link.casefold()

            if "osym.gov.tr" not in normal_link or "/arama" in normal_link:
                continue

            anahtar = link_anahtari(link)

            if anahtar in gorulen_linkler:
                continue

            gorulen_linkler.add(anahtar)

            kapsayici = link_etiketi.find_parent(["li", "article", "div", "tr"])
            kapsayici_metni = (
                temizle(kapsayici.get_text(" ", strip=True))
                if kapsayici is not None
                else baslik
            )
            yayin_tarihi = tarih_bul(kapsayici_metni) or tarih_bul(baslik)

            haberler.append({
                "id": ilan_id_uret(link),
                "baslik": baslik[:400],
                "kurum": "ÖSYM",
                "kategori": haber_kategorisi_bul(baslik),
                "yayin_tarihi": yayin_tarihi,
                "kaynak": "ÖSYM KPSS Haberleri",
                "ozet": "ÖSYM tarafından yayımlanan resmî KPSS duyurusu.",
                "link": link,
            })

            if len(haberler) >= 60:
                return haberler

    return haberler


def osym_kpss_duyurularini_al():
    """ÖSYM'den yalnızca aktif KPSS başvuru ve tercih duyurularını alır."""
    ilanlar = []
    gorulen_linkler = set()

    for arama_terimi in OSYM_ARAMA_TERIMLERI:
        html = sayfayi_indir(
            OSYM_ARAMA_URL,
            params={
                "_Dil": "1",
                "aranan": arama_terimi,
            },
        )
        soup = BeautifulSoup(html, "html.parser")

        for link_etiketi in soup.find_all("a", href=True):
            baslik = temizle(link_etiketi.get_text(" ", strip=True))
            href = temizle(link_etiketi.get("href", ""))

            if len(baslik) < 15 or not href:
                continue

            if not osym_aktif_duyuru_mu(baslik):
                continue

            if href.startswith("#") or href.casefold().startswith("javascript:"):
                continue

            link = urljoin(OSYM_ARAMA_URL, href)
            normal_link = link.casefold()

            if "osym.gov.tr" not in normal_link:
                continue

            if "/arama" in normal_link:
                continue

            anahtar = link_anahtari(link)

            if anahtar in gorulen_linkler:
                continue

            gorulen_linkler.add(anahtar)

            kapsayici = link_etiketi.find_parent(["li", "article", "div", "tr"])
            kapsayici_metni = (
                temizle(kapsayici.get_text(" ", strip=True))
                if kapsayici is not None
                else baslik
            )
            yayin_tarihi = tarih_bul(kapsayici_metni) or tarih_bul(baslik)

            try:
                (
                    sayfa_basligi,
                    detay_metni,
                    belge_linki,
                    basvuru_linki,
                    basvuru_online,
                    basvuru_aciklamasi,
                ) = duyuru_icerigini_al(link)
            except Exception:
                sayfa_basligi = ""
                detay_metni = kapsayici_metni
                belge_linki = link
                basvuru_linki = ""
                basvuru_online = False
                basvuru_aciklamasi = (
                    "Online başvuru bağlantısı bulunamadı. ÖSYM duyuru "
                    "sayfasını kontrol edin."
                )

            son_basvuru = son_basvuru_tarihi_bul(detay_metni)

            ilanlar.append({
                "id": ilan_id_uret(link),
                "baslik": baslik_temizle(sayfa_basligi or baslik)[:400],
                "kurum": "ÖSYM",
                "sehir": "Türkiye Geneli",
                "tur": "KPSS Duyurusu",
                "kaynak": "ÖSYM KPSS Duyuruları",
                "son_basvuru": son_basvuru,
                "yayin_tarihi": yayin_tarihi,
                "link": link,
                "belge_linki": belge_linki,
                "kaynak_sayfa_linki": link,
                "basvuru_linki": basvuru_linki,
                "basvuru_online": basvuru_online,
                "basvuru_aciklamasi": basvuru_aciklamasi,
                "kpss_gerekli": True,
                "minimum_puan": None,
                "kpss_durumu": "KPSS tercih/yerleştirme duyurusu",
                "mezuniyetler": ["Ortaöğretim", "Önlisans", "Lisans"],
                "bolumler": ["Tüm Bölümler"],
                "pdf_isleme_durumu": "uygulanmaz",
                "analiz_surumu": ANALIZ_SURUMU,
            })

    return ilanlar


def personel_alim_duyurusu_mu(baslik):
    """Açık personel/işçi alım ilanlarını sonuç ve kurum içi haberlerden ayırır."""
    normal = arama_metnine_cevir(baslik)

    engellenen_ifadeler = (
        "sonuc",
        "yerlestirme sonucu",
        "basvuru sonucu",
        "sinav sonucu",
        "sozlu sinav",
        "uygulamali sinav",
        "mulakat",
        "taban puan",
        "tavan puan",
        "evrak",
        "belge teslim",
        "itiraz",
        "nakil",
        "yer degistirme",
        "gorevde yukselme",
        "unvan degisikligi",
        "atamaya hak kazanan",
        "atama islemleri",
    )

    if any(ifade in normal for ifade in engellenen_ifadeler):
        return False

    alim_ifadeleri = (
        "personel alim",
        "isci alim",
        "memur alim",
        "sozlesmeli personel",
        "bilisim personeli",
        "uzman yardimcisi",
        "mufettis yardimcisi",
        "zabit katibi",
        "mubasir",
        "destek personeli",
        "koruma ve guvenlik gorevlisi",
        "antrenor alimi",
        "genclik calisani alimi",
        "yurt yonetim personeli",
        "avukat ve muhendis alimi",
        "sosyal calismaci alimi",
        "psikolog alimi",
    )

    return any(ifade in normal for ifade in alim_ifadeleri)


def personel_haberi_mi(baslik):
    normal = arama_metnine_cevir(baslik)

    haber_ifadeleri = (
        "basvuru sonucu",
        "sinav sonucu",
        "sozlu sinav sonucu",
        "mulakat sonucu",
        "yerlestirme sonucu",
        "atamaya hak kazanan",
        "atama sonucu",
        "taban puan",
        "tavan puan",
        "belge teslim",
        "evrak teslim",
    )

    return any(ifade in normal for ifade in haber_ifadeleri)


def resmi_kaynaktan_haberleri_al(kaynak):
    """Bakanlıkların personel alımı sonuç ve atama haberlerini toplar."""
    html = sayfayi_indir(kaynak["url"])
    soup = BeautifulSoup(html, "html.parser")
    haberler = []
    gorulen_linkler = set()

    for link_etiketi in soup.find_all("a", href=True):
        baslik = temizle(link_etiketi.get_text(" ", strip=True))
        href = temizle(link_etiketi.get("href", ""))

        if len(baslik) < 12 or not href or not personel_haberi_mi(baslik):
            continue

        link = urljoin(kaynak["url"], href)
        normal_link = link.casefold()

        if not any(
            parca.casefold() in normal_link
            for parca in kaynak["link_parcalari"]
        ):
            continue

        anahtar = link_anahtari(link)
        if anahtar in gorulen_linkler:
            continue
        gorulen_linkler.add(anahtar)

        kapsayici = link_etiketi.find_parent(["li", "article", "div", "tr"])
        kapsayici_metni = (
            temizle(kapsayici.get_text(" ", strip=True))
            if kapsayici is not None
            else baslik
        )
        yayin_tarihi = tarih_bul(kapsayici_metni) or tarih_bul(baslik)

        haberler.append({
            "id": ilan_id_uret(link),
            "baslik": baslik[:400],
            "kurum": kaynak["kurum"],
            "kategori": haber_kategorisi_bul(baslik),
            "yayin_tarihi": yayin_tarihi,
            "kaynak": kaynak["kaynak"].replace("Personel Alımları", "Personel Haberleri"),
            "ozet": f"{kaynak['kurum']} tarafından yayımlanan resmî personel duyurusu.",
            "link": link,
        })

        if len(haberler) >= 30:
            break

    return haberler


def turkce_tarihleri_bul(metin):
    """Metindeki sayısal ve Türkçe ay adlarıyla yazılmış tarihleri bulur."""
    bulunanlar = []
    normal = arama_metnine_cevir(metin)

    for eslesme in re.finditer(
        r"\b(\d{1,2})[./-](\d{1,2})[./-](\d{4})\b",
        normal,
    ):
        try:
            tarih = datetime(
                int(eslesme.group(3)),
                int(eslesme.group(2)),
                int(eslesme.group(1)),
            )
            bulunanlar.append(tarih)
        except ValueError:
            continue

    ay_deseni = "|".join(TURKCE_AYLAR)

    # Örnek: 18 - 24 Mayıs 2026 -> son gün olan 24.05.2026
    for eslesme in re.finditer(
        rf"\b\d{{1,2}}\s*[-–]\s*(\d{{1,2}})\s+({ay_deseni})\s+(\d{{4}})\b",
        normal,
    ):
        try:
            bulunanlar.append(datetime(
                int(eslesme.group(3)),
                TURKCE_AYLAR[eslesme.group(2)],
                int(eslesme.group(1)),
            ))
        except ValueError:
            continue

    # Örnek: 24 Mayıs 2026
    for eslesme in re.finditer(
        rf"\b(\d{{1,2}})\s+({ay_deseni})\s+(\d{{4}})\b",
        normal,
    ):
        try:
            bulunanlar.append(datetime(
                int(eslesme.group(3)),
                TURKCE_AYLAR[eslesme.group(2)],
                int(eslesme.group(1)),
            ))
        except ValueError:
            continue

    return bulunanlar


def son_basvuru_tarihi_bul(metin):
    """Başvuru cümlelerindeki en ileri tarihi son başvuru olarak seçer."""
    normal = arama_metnine_cevir(metin)
    aday_tarihler = []

    anahtarlar = (
        "son basvuru",
        "basvurular",
        "basvuru tarih",
        "basvurularini",
        "basvuru suresi",
    )

    for anahtar in anahtarlar:
        baslangic = 0
        while True:
            konum = normal.find(anahtar, baslangic)
            if konum < 0:
                break

            pencere = normal[max(0, konum - 80):konum + 350]
            aday_tarihler.extend(turkce_tarihleri_bul(pencere))
            baslangic = konum + len(anahtar)

    if not aday_tarihler:
        return ""

    return max(aday_tarihler).strftime("%d.%m.%Y")


BILINEN_BASVURU_ADRESLERI = {
    "isealimkariyerkapisi.cbiko.gov.tr": (
        "https://isealimkariyerkapisi.cbiko.gov.tr"
    ),
    "kariyerkapisi.cbiko.gov.tr": (
        "https://kariyerkapisi.cbiko.gov.tr"
    ),
    "ais.osym.gov.tr": "https://ais.osym.gov.tr",
    "esube.iskur.gov.tr": "https://esube.iskur.gov.tr",
    "turkiye.gov.tr": "https://www.turkiye.gov.tr",
    "personeltemin.msb.gov.tr": "https://personeltemin.msb.gov.tr",
    "vatandas.jandarma.gov.tr": "https://vatandas.jandarma.gov.tr",
}

ONLINE_BASVURU_IFADELERI = (
    "online basvuru",
    "elektronik ortamda",
    "internet uzerinden",
    "e-devlet uzerinden",
    "kariyer kapisi",
    "basvuru adresi",
    "basvuru ekrani",
)

ONLINE_OLMAYAN_BASVURU_IFADELERI = (
    "sahsen basvuru",
    "basvurular sahsen",
    "sahsen yapil",
    "sahsen muracaat",
    "elden basvuru",
    "posta yoluyla",
    "kargo yoluyla",
    "kuruma teslim",
    "basvuru formu ile birlikte",
)


def guvenli_web_linki(href, temel_url):
    href = temizle(href)

    if not href:
        return ""

    normal = href.casefold()

    if normal.startswith(("javascript:", "mailto:", "tel:", "#")):
        return ""

    link = urljoin(temel_url, href)

    if link.casefold().startswith(("http://", "https://")):
        return link

    return ""


def html_basvuru_linki_bul(soup, temel_url):
    for etiket in soup.find_all("a", href=True):
        href = temizle(etiket.get("href", ""))
        link = guvenli_web_linki(href, temel_url)

        if not link:
            continue

        yazi = arama_metnine_cevir(
            etiket.get_text(" ", strip=True)
        )
        normal_link = link.casefold()

        if any(domain in normal_link for domain in BILINEN_BASVURU_ADRESLERI):
            return link

        if (
            "basvuru" in yazi
            or "basvuru" in arama_metnine_cevir(href)
            or "apply" in normal_link
        ):
            return link

    return ""


def metinden_basvuru_linki_bul(metin):
    for bulunan in re.findall(r"https?://[^\s<>'\"()]+", metin or ""):
        link = bulunan.rstrip(".,;:)]}")
        normal_link = link.casefold()

        if any(domain in normal_link for domain in BILINEN_BASVURU_ADRESLERI):
            return link

    normal_metin = arama_metnine_cevir(metin)

    for domain, adres in BILINEN_BASVURU_ADRESLERI.items():
        if domain in normal_metin:
            return adres

    return ""


def basvuru_bilgisi_bul(metin, soup=None, temel_url=""):
    link = ""

    if soup is not None:
        link = html_basvuru_linki_bul(soup, temel_url)

    if not link:
        link = metinden_basvuru_linki_bul(metin)

    if link:
        return True, link, "Başvurular online olarak yapılmaktadır."

    normal = arama_metnine_cevir(metin)

    if any(ifade in normal for ifade in ONLINE_OLMAYAN_BASVURU_IFADELERI):
        return (
            False,
            "",
            "Başvurular online değildir. Şahsen, posta veya ilanda "
            "belirtilen diğer yöntemle başvuru yapılmalıdır.",
        )

    if any(ifade in normal for ifade in ONLINE_BASVURU_IFADELERI):
        return (
            False,
            "",
            "İlanda online başvuru belirtilmiş ancak doğrudan başvuru "
            "bağlantısı bulunamadı. İlanın yayımlandığı sayfayı kontrol edin.",
        )

    return (
        False,
        "",
        "Online başvuru bağlantısı bulunamadı. Başvuru yöntemini ilan "
        "belgesinden kontrol edin.",
    )


def ilan_belgesi_linki_bul(soup, temel_url):
    for etiket in soup.find_all("a", href=True):
        href = temizle(etiket.get("href", ""))
        link = guvenli_web_linki(href, temel_url)

        if not link:
            continue

        yazi = arama_metnine_cevir(
            etiket.get_text(" ", strip=True)
        )
        normal_link = link.casefold()

        if ".pdf" in normal_link or "/medya/" in normal_link:
            return link

        if any(
            ifade in yazi
            for ifade in (
                "ilan metni",
                "ilan belgesi",
                "basvuru kilavuzu",
                "duyuru metni",
            )
        ):
            return link

    # Ayrı belge yoksa duyuru sayfasının kendisi belge görevi görür.
    return temel_url


def duyuru_icerigini_al(url):
    html = sayfayi_indir(url)
    soup = BeautifulSoup(html, "html.parser")

    temiz_soup = BeautifulSoup(html, "html.parser")
    for etiket in temiz_soup(["script", "style", "noscript", "svg"]):
        etiket.decompose()

    adaylar = []

    for secici in (
        "main",
        "article",
        ".detail",
        ".content",
        ".page-content",
        ".news-detail",
        ".duyuru-detay",
    ):
        for dugum in temiz_soup.select(secici):
            metin = temizle(dugum.get_text(" ", strip=True))
            if len(metin) >= 120:
                adaylar.append((len(metin), metin))

    if adaylar:
        detay_metni = max(adaylar, key=lambda oge: oge[0])[1]
    else:
        detay_metni = temizle(
            temiz_soup.get_text(" ", strip=True)
        )

    baslik_etiketi = temiz_soup.find(["h1", "h2"])
    sayfa_basligi = (
        temizle(baslik_etiketi.get_text(" ", strip=True))
        if baslik_etiketi is not None
        else ""
    )

    belge_linki = ilan_belgesi_linki_bul(soup, url)
    basvuru_online, basvuru_linki, basvuru_aciklamasi = (
        basvuru_bilgisi_bul(
            detay_metni,
            soup=soup,
            temel_url=url,
        )
    )

    return (
        sayfa_basligi,
        detay_metni,
        belge_linki,
        basvuru_linki,
        basvuru_online,
        basvuru_aciklamasi,
    )


def resmi_kaynaktan_ilanlari_al(kaynak):
    """GSB, Aile ve Adalet Bakanlığı resmî duyuru sayfalarını tarar."""
    html = sayfayi_indir(kaynak["url"])
    soup = BeautifulSoup(html, "html.parser")
    ilanlar = []
    gorulen_linkler = set()

    for link_etiketi in soup.find_all("a", href=True):
        baslik = temizle(link_etiketi.get_text(" ", strip=True))
        href = temizle(link_etiketi.get("href", ""))

        if len(baslik) < 12 or not href:
            continue

        link = urljoin(kaynak["url"], href)
        normal_link = link.casefold()

        if not any(
            parca.casefold() in normal_link
            for parca in kaynak["link_parcalari"]
        ):
            continue

        if not personel_alim_duyurusu_mu(baslik):
            continue

        anahtar = link_anahtari(link)

        if anahtar in gorulen_linkler:
            continue

        gorulen_linkler.add(anahtar)

        try:
            (
                sayfa_basligi,
                detay_metni,
                belge_linki,
                basvuru_linki,
                basvuru_online,
                basvuru_aciklamasi,
            ) = duyuru_icerigini_al(link)
        except Exception:
            sayfa_basligi = ""
            detay_metni = baslik
            belge_linki = link
            basvuru_linki = ""
            basvuru_online = False
            basvuru_aciklamasi = (
                "Online başvuru bağlantısı bulunamadı. Başvuru yöntemini "
                "ilan sayfasından kontrol edin."
            )

        gercek_baslik = baslik_temizle(sayfa_basligi or baslik)

        if not personel_alim_duyurusu_mu(gercek_baslik):
            continue

        son_basvuru = son_basvuru_tarihi_bul(detay_metni)
        kpss_gerekli, minimum_puan, kpss_durumu = (
            kpss_bilgisi_bul(detay_metni, "ok")
        )

        ilanlar.append({
            "id": ilan_id_uret(link),
            "baslik": gercek_baslik[:400],
            "kurum": kaynak["kurum"],
            "sehir": "Türkiye Geneli",
            "tur": "Personel Alımı",
            "kaynak": kaynak["kaynak"],
            "son_basvuru": son_basvuru,
            "link": link,
            "belge_linki": belge_linki,
            "kaynak_sayfa_linki": link,
            "basvuru_linki": basvuru_linki,
            "basvuru_online": basvuru_online,
            "basvuru_aciklamasi": basvuru_aciklamasi,
            "kpss_gerekli": kpss_gerekli,
            "minimum_puan": minimum_puan,
            "kpss_durumu": kpss_durumu,
            "mezuniyetler": mezuniyetleri_bul(detay_metni),
            "bolumler": bolumleri_bul(detay_metni),
            "pdf_isleme_durumu": "html_ok",
            "analiz_surumu": ANALIZ_SURUMU,
        })

        # Ana sayfadaki en güncel ilanlar yeterli; kaynak başına yükü sınırlayalım.
        if len(ilanlar) >= 30:
            break

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


def pdf_dosyasini_indir(url):
    son_hata = None

    for deneme in range(3):
        try:
            with requests.get(
                url,
                headers=HEADERS,
                timeout=(10, 45),
                stream=True,
            ) as cevap:
                cevap.raise_for_status()

                parcalar = []
                toplam = 0

                for parca in cevap.iter_content(chunk_size=64 * 1024):
                    if not parca:
                        continue

                    toplam += len(parca)

                    if toplam > MAKSIMUM_PDF_BOYUTU:
                        raise RuntimeError("PDF 30 MB sınırını aşıyor")

                    parcalar.append(parca)

                veri = b"".join(parcalar)

                if b"%PDF" not in veri[:1024]:
                    raise RuntimeError("Bağlantı PDF döndürmedi")

                return veri
        except Exception as hata:
            son_hata = hata
            time.sleep(2 + deneme)

    raise RuntimeError(str(son_hata))


def pdf_metnini_oku(url):
    try:
        pdf_verisi = pdf_dosyasini_indir(url)
        okuyucu = PdfReader(io.BytesIO(pdf_verisi), strict=False)

        if okuyucu.is_encrypted:
            try:
                okuyucu.decrypt("")
            except Exception:
                return "", "sifreli_pdf"

        parcalar = []
        sayfa_sayisi = min(
            len(okuyucu.pages),
            MAKSIMUM_PDF_SAYFASI,
        )

        for sayfa_no in range(sayfa_sayisi):
            try:
                metin = okuyucu.pages[sayfa_no].extract_text() or ""
            except Exception:
                metin = ""

            if metin:
                parcalar.append(metin)

        metin = temizle("\n".join(parcalar))

        if len(metin) < 80:
            return metin, "metin_yok"

        return metin[:250000], "ok"
    except Exception as hata:
        return "", f"hata:{type(hata).__name__}"


def minimum_kpss_puani_bul(normal_metin):
    puanlar = []

    for eslesme in re.finditer(r"\bkpss\b", normal_metin):
        baslangic = max(0, eslesme.start() - 80)
        bitis = min(len(normal_metin), eslesme.end() + 140)
        pencere = normal_metin[baslangic:bitis]

        desenler = [
            r"(?:en az|minimum|taban)\s*(\d{2,3}(?:[.,]\d+)?)\s*puan",
            r"(\d{2,3}(?:[.,]\d+)?)\s*(?:ve uzeri\s*)?kpss",
            r"kpss\s*p\s*\d{1,2}\s*(?:puan(?:indan)?\s*)?(\d{2,3}(?:[.,]\d+)?)",
            r"kpss[^\d]{0,45}(\d{2,3}(?:[.,]\d+)?)\s*puan",
        ]

        for desen in desenler:
            for bulunan in re.findall(desen, pencere):
                try:
                    deger = float(bulunan.replace(",", "."))
                except ValueError:
                    continue

                if 40 <= deger <= 100:
                    puanlar.append(deger)

    if not puanlar:
        return None

    sonuc = min(puanlar)
    return int(sonuc) if sonuc.is_integer() else sonuc


def kpss_bilgisi_bul(metin, pdf_durumu):
    normal = arama_metnine_cevir(metin)

    kpss_yok_ifadeleri = [
        "kpss sarti aranmamaktadir",
        "kpss puani aranmamaktadir",
        "kpss kosulu aranmamaktadir",
        "kpss sartsiz",
        "kpss siz",
        "kpss sart yok",
    ]

    if any(ifade in normal for ifade in kpss_yok_ifadeleri):
        return False, 0, "KPSS şartı yok"

    minimum_puan = minimum_kpss_puani_bul(normal)

    if minimum_puan is not None:
        return True, minimum_puan, f"En az {minimum_puan} KPSS puanı"

    if "kpss" in normal:
        return True, None, "KPSS şartı var; puan ilan belgesinde belirtilmiştir"

    if pdf_durumu != "ok":
        return None, None, "PDF metninden belirlenemedi"

    return None, None, "İlan metninde açık KPSS bilgisi bulunamadı"


def mezuniyetleri_bul(metin):
    normal = arama_metnine_cevir(metin)
    bulunanlar = []

    if re.search(
        r"\b(ortaogretim|lise|meslek lisesi|lise ve dengi)\b",
        normal,
    ):
        bulunanlar.append("Ortaöğretim")

    if re.search(r"\b(on lisans|onlisans|2 yillik)\b", normal):
        bulunanlar.append("Önlisans")

    lisans_kontrol = re.sub(
        r"\b(on lisans|onlisans|lisansustu)\b",
        " ",
        normal,
    )

    if re.search(r"\blisans\b", lisans_kontrol):
        bulunanlar.append("Lisans")

    return bulunanlar


def bolumleri_bul(metin):
    normal = arama_metnine_cevir(metin)
    bulunanlar = []

    bolum_desenleri = {
        "Maliye": [
            r"\bmaliye\b",
        ],
        "İşletme": [
            r"\bisletme\b",
        ],
        "Muhasebe": [
            r"\bmuhasebe\b",
            r"\bmuhasebe ve vergi uygulamalari\b",
            r"\bmuhasebe ve finansman\b",
        ],
        "Mekatronik": [
            r"\bmekatronik\b",
        ],
    }

    for bolum, desenler in bolum_desenleri.items():
        if any(re.search(desen, normal) for desen in desenler):
            bulunanlar.append(bolum)

    if not bulunanlar:
        bulunanlar.append("Diğer")

    return bulunanlar


def onceki_analizleri_yukle():
    dosya = Path("ilanlar.json")

    if not dosya.exists():
        return {}

    try:
        veri = json.loads(dosya.read_text(encoding="utf-8"))
        ilanlar = veri.get("ilanlar", [])
        sonuc = {}

        for ilan in ilanlar:
            link = link_anahtari(ilan.get("link", ""))

            if link:
                sonuc[link] = ilan

        return sonuc
    except Exception:
        return {}


def onceki_analiz_kullanilabilir(onceki):
    return (
        isinstance(onceki, dict)
        and onceki.get("analiz_surumu") == ANALIZ_SURUMU
        and onceki.get("pdf_isleme_durumu")
        in {"ok", "metin_yok", "sifreli_pdf"}
    )


def ilani_zenginlestir(ilan, onceki_analizler):
    if (
        ilan.get("kaynak") == "ÖSYM KPSS Duyuruları"
        or ilan.get("pdf_isleme_durumu") == "html_ok"
    ):
        return ilan, False

    anahtar = link_anahtari(ilan.get("link", ""))
    onceki = onceki_analizler.get(anahtar)

    kopyalanacak_alanlar = [
        "kpss_gerekli",
        "minimum_puan",
        "kpss_durumu",
        "mezuniyetler",
        "bolumler",
        "pdf_isleme_durumu",
        "analiz_surumu",
        "belge_linki",
        "kaynak_sayfa_linki",
        "basvuru_linki",
        "basvuru_online",
        "basvuru_aciklamasi",
    ]

    if onceki_analiz_kullanilabilir(onceki):
        for alan in kopyalanacak_alanlar:
            if alan in onceki:
                ilan[alan] = onceki.get(alan)

        return ilan, True

    pdf_metni, pdf_durumu = pdf_metnini_oku(
        ilan.get("belge_linki") or ilan.get("link", "")
    )

    kpss_gerekli, minimum_puan, kpss_durumu = (
        kpss_bilgisi_bul(pdf_metni, pdf_durumu)
    )
    basvuru_online, basvuru_linki, basvuru_aciklamasi = (
        basvuru_bilgisi_bul(
            pdf_metni,
            temel_url=ilan.get("kaynak_sayfa_linki", ""),
        )
    )

    if pdf_durumu != "ok" and not basvuru_linki:
        basvuru_aciklamasi = (
            "Online başvuru bağlantısı belirlenemedi. Başvuru yöntemini "
            "ilan belgesinden veya ilanın yayımlandığı sayfadan kontrol edin."
        )

    ilan.update({
        "kpss_gerekli": kpss_gerekli,
        "minimum_puan": minimum_puan,
        "kpss_durumu": kpss_durumu,
        "mezuniyetler": mezuniyetleri_bul(pdf_metni),
        "bolumler": bolumleri_bul(pdf_metni),
        "pdf_isleme_durumu": pdf_durumu,
        "analiz_surumu": ANALIZ_SURUMU,
        "belge_linki": ilan.get("belge_linki") or ilan.get("link", ""),
        "kaynak_sayfa_linki": ilan.get("kaynak_sayfa_linki", ""),
        "basvuru_linki": basvuru_linki,
        "basvuru_online": basvuru_online,
        "basvuru_aciklamasi": basvuru_aciklamasi,
    })

    return ilan, False


TURKIYE_SAATI = ZoneInfo("Europe/Istanbul")

SON_BASVURU_TARIH_BICIMLERI = (
    "%d.%m.%Y %H:%M",
    "%d/%m/%Y %H:%M",
    "%d-%m-%Y %H:%M",
    "%d.%m.%Y",
    "%d/%m/%Y",
    "%d-%m-%Y",
)


def son_basvuru_zamani(ilan):
    """İlanın son başvuru tarihini Türkiye saatine göre çözümler."""
    metin = temizle(str(ilan.get("son_basvuru", "")))

    if not metin:
        return None

    for bicim in SON_BASVURU_TARIH_BICIMLERI:
        try:
            tarih = datetime.strptime(metin, bicim)

            # Saat bilgisi yoksa ilan son günün 23:59:59'una kadar açık kalsın.
            if "%H:%M" not in bicim:
                tarih = tarih.replace(hour=23, minute=59, second=59)

            return tarih.replace(tzinfo=TURKIYE_SAATI)
        except ValueError:
            continue

    return None


def ilan_aktif_mi(ilan, simdi=None):
    """Son başvuru tarihi geçmiş ilanları ele."""
    son_zaman = son_basvuru_zamani(ilan)

    # Tarihi okunamayan ilanı yanlışlıkla silmemek için listede tut.
    if son_zaman is None:
        return True

    simdi = simdi or datetime.now(TURKIYE_SAATI)
    return son_zaman >= simdi


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


def haber_tarih_siralama_degeri(haber):
    metin = haber.get("yayin_tarihi", "")

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

    return datetime.min


def onceki_bildirim_kayitlarini_yukle():
    dosya = Path("ilanlar.json")

    if not dosya.exists():
        return set(), set(), False

    try:
        veri = json.loads(dosya.read_text(encoding="utf-8"))
        ilan_anahtarlari = {
            link_anahtari(ilan.get("link", ""))
            for ilan in veri.get("ilanlar", [])
            if link_anahtari(ilan.get("link", ""))
        }
        haber_anahtarlari = {
            link_anahtari(haber.get("link", ""))
            for haber in veri.get("haberler", [])
            if link_anahtari(haber.get("link", ""))
        }
        return ilan_anahtarlari, haber_anahtarlari, True
    except Exception:
        return set(), set(), False


def firebase_mesajlasmayi_hazirla():
    if firebase_admin is None or credentials is None or messaging is None:
        return None, "firebase-admin paketi kurulu değil"

    servis_hesabi_json = os.environ.get(
        "FIREBASE_SERVICE_ACCOUNT_JSON",
        "",
    ).strip()

    if not servis_hesabi_json:
        return None, "FIREBASE_SERVICE_ACCOUNT_JSON sırrı tanımlı değil"

    try:
        servis_hesabi = json.loads(servis_hesabi_json)

        try:
            firebase_admin.get_app()
        except ValueError:
            firebase_admin.initialize_app(
                credentials.Certificate(servis_hesabi)
            )

        return messaging, None
    except Exception as hata:
        return None, f"{type(hata).__name__}: {str(hata)[:180]}"


def tek_bildirim_gonder(mesajlasma, konu, baslik, govde, link, tur):
    mesaj = mesajlasma.Message(
        notification=mesajlasma.Notification(
            title=baslik[:100],
            body=govde[:240],
        ),
        data={
            "tur": tur,
            "link": (link or "")[:1000],
        },
        android=mesajlasma.AndroidConfig(
            priority="high",
            notification=mesajlasma.AndroidNotification(
                channel_id=konu,
            ),
        ),
        topic=konu,
    )
    return mesajlasma.send(mesaj)


def yeni_kayit_bildirimlerini_gonder(
    ilanlar,
    haberler,
    onceki_ilan_anahtarlari,
    onceki_haber_anahtarlari,
    onceki_dosya_vardi,
):
    sonuc = {
        "durum": "atlanmış",
        "yeni_ilan": 0,
        "yeni_haber": 0,
        "gonderilen": 0,
        "mesaj": "",
    }

    if not onceki_dosya_vardi:
        sonuc["mesaj"] = "İlk çalışma olduğu için toplu bildirim gönderilmedi."
        return sonuc

    yeni_ilanlar = [
        ilan
        for ilan in ilanlar
        if link_anahtari(ilan.get("link", ""))
        and link_anahtari(ilan.get("link", ""))
        not in onceki_ilan_anahtarlari
    ]
    yeni_haberler = [
        haber
        for haber in haberler
        if link_anahtari(haber.get("link", ""))
        and link_anahtari(haber.get("link", ""))
        not in onceki_haber_anahtarlari
    ]

    sonuc["yeni_ilan"] = len(yeni_ilanlar)
    sonuc["yeni_haber"] = len(yeni_haberler)

    if not yeni_ilanlar and not yeni_haberler:
        sonuc["durum"] = "yeni_kayit_yok"
        sonuc["mesaj"] = "Yeni ilan veya haber bulunmadı."
        return sonuc

    mesajlasma, hata = firebase_mesajlasmayi_hazirla()

    if mesajlasma is None:
        sonuc["mesaj"] = hata or "Firebase hazırlanamadı."
        return sonuc

    try:
        # Kullanıcıyı bildirim yağmuruna tutmamak için her türden en fazla 5 bildirim.
        for ilan in yeni_ilanlar[:5]:
            baslik = ilan.get("baslik") or ilan.get("kurum") or "Yeni kamu ilanı"
            kurum = ilan.get("kurum") or ilan.get("kaynak") or "Resmî kurum"
            sehir = ilan.get("sehir") or "Türkiye"
            govde = f"{kurum} • {sehir}"
            tek_bildirim_gonder(
                mesajlasma,
                "yeni_ilanlar",
                "Yeni ilan: " + temizle(baslik),
                temizle(govde),
                ilan.get("link", ""),
                "ilan",
            )
            sonuc["gonderilen"] += 1

        for haber in yeni_haberler[:5]:
            baslik = haber.get("baslik") or "Yeni KPSS ve atama haberi"
            kurum = haber.get("kurum") or haber.get("kaynak") or "Resmî kurum"
            kategori = haber.get("kategori") or "Haber"
            govde = f"{kurum} • {kategori}"
            tek_bildirim_gonder(
                mesajlasma,
                "yeni_haberler",
                temizle(baslik),
                temizle(govde),
                haber.get("link", ""),
                "haber",
            )
            sonuc["gonderilen"] += 1

        sonuc["durum"] = "gonderildi"
        sonuc["mesaj"] = (
            f"{sonuc['gonderilen']} telefon bildirimi gönderildi."
        )
    except Exception as hata:
        sonuc["durum"] = "hata"
        sonuc["mesaj"] = f"{type(hata).__name__}: {str(hata)[:180]}"

    return sonuc


def main():
    (
        onceki_ilan_anahtarlari,
        onceki_haber_anahtarlari,
        onceki_dosya_vardi,
    ) = onceki_bildirim_kayitlarini_yukle()

    tum_ilanlar = []
    tum_haberler = []
    hatalar = []

    gorevler = [
        (kaynak, sehir_kodu, sehir_adi)
        for kaynak in KAYNAKLAR
        for sehir_kodu, sehir_adi in SEHIRLER
    ]

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
                f"Sayfalar: {tamamlanan}/{len(gorevler)} "
                f"- bulunan: {len(tum_ilanlar)}"
            )

    try:
        osym_ilanlari = osym_kpss_duyurularini_al()
        tum_ilanlar.extend(osym_ilanlari)
        print(f"ÖSYM KPSS duyuruları: {len(osym_ilanlari)}")
    except Exception as hata:
        hatalar.append(
            "ÖSYM KPSS duyuruları: "
            f"{type(hata).__name__} - {str(hata)[:150]}"
        )

    try:
        osym_haberleri = osym_kpss_haberlerini_al()
        tum_haberler.extend(osym_haberleri)
        print(f"ÖSYM KPSS haberleri: {len(osym_haberleri)}")
    except Exception as hata:
        hatalar.append(
            "ÖSYM KPSS haberleri: "
            f"{type(hata).__name__} - {str(hata)[:150]}"
        )

    for resmi_kaynak in RESMI_DUYURU_KAYNAKLARI:
        try:
            resmi_ilanlar = resmi_kaynaktan_ilanlari_al(resmi_kaynak)
            tum_ilanlar.extend(resmi_ilanlar)
            print(
                f"{resmi_kaynak['kaynak']}: {len(resmi_ilanlar)}"
            )

            resmi_haberler = resmi_kaynaktan_haberleri_al(resmi_kaynak)
            tum_haberler.extend(resmi_haberler)
            print(
                f"{resmi_kaynak['kurum']} haberleri: {len(resmi_haberler)}"
            )
        except Exception as hata:
            hatalar.append(
                f"{resmi_kaynak['kaynak']}: "
                f"{type(hata).__name__} - {str(hata)[:150]}"
            )

    benzersiz = {}

    for ilan in tum_ilanlar:
        anahtar = link_anahtari(ilan.get("link", ""))

        if anahtar:
            benzersiz[anahtar] = ilan

    tum_benzersiz_ilanlar = list(benzersiz.values())
    simdi_tr = datetime.now(TURKIYE_SAATI)
    sonuc = [
        ilan
        for ilan in tum_benzersiz_ilanlar
        if ilan_aktif_mi(ilan, simdi_tr)
    ]
    suresi_dolmus_ilan_sayisi = len(tum_benzersiz_ilanlar) - len(sonuc)

    print(
        f"Süresi dolduğu için kaldırılan ilan: "
        f"{suresi_dolmus_ilan_sayisi}"
    )

    benzersiz_haberler = {}
    ilan_linkleri = set(benzersiz)

    for haber in tum_haberler:
        anahtar = link_anahtari(haber.get("link", ""))

        if anahtar and anahtar not in ilan_linkleri:
            benzersiz_haberler[anahtar] = haber

    haberler = list(benzersiz_haberler.values())
    haberler.sort(key=haber_tarih_siralama_degeri, reverse=True)

    onceki_analizler = onceki_analizleri_yukle()
    zenginlestirilmis = []
    yeniden_kullanilan = 0
    pdf_hatalari = 0

    with ThreadPoolExecutor(max_workers=4) as havuz:
        futures = {
            havuz.submit(
                ilani_zenginlestir,
                ilan,
                onceki_analizler,
            ): ilan.get("baslik", "")
            for ilan in sonuc
        }

        tamamlanan = 0

        for future in as_completed(futures):
            try:
                ilan, oncekiden = future.result()
                zenginlestirilmis.append(ilan)

                if oncekiden:
                    yeniden_kullanilan += 1

                if str(ilan.get("pdf_isleme_durumu", "")).startswith("hata:"):
                    pdf_hatalari += 1
            except Exception as hata:
                pdf_hatalari += 1
                hatalar.append(
                    f"PDF analizi: {type(hata).__name__} - {str(hata)[:150]}"
                )

            tamamlanan += 1
            print(
                f"PDF analizi: {tamamlanan}/{len(sonuc)} "
                f"- önbellekten: {yeniden_kullanilan}"
            )

    zenginlestirilmis.sort(key=tarih_siralama_degeri)

    bildirim_sonucu = yeni_kayit_bildirimlerini_gonder(
        zenginlestirilmis,
        haberler,
        onceki_ilan_anahtarlari,
        onceki_haber_anahtarlari,
        onceki_dosya_vardi,
    )
    print(f"Bildirim: {bildirim_sonucu['mesaj']}")

    cikti = {
        "status": "ok" if zenginlestirilmis else "veri_alinamadi",
        "guncellenme_zamani": datetime.now(
            timezone.utc
        ).isoformat(),
        "ilan_sayisi": len(zenginlestirilmis),
        "suresi_dolmus_ilan_sayisi": suresi_dolmus_ilan_sayisi,
        "ilanlar": zenginlestirilmis,
        "haber_sayisi": len(haberler),
        "haberler": haberler,
        "bildirim_sonucu": bildirim_sonucu,
        "hata_sayisi": len(hatalar),
        "pdf_hata_sayisi": pdf_hatalari,
        "hatalar": hatalar[:50],
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
    print(f"Toplam aktif ilan: {len(zenginlestirilmis)}")
    print(f"Kaldırılan süresi dolmuş ilan: {suresi_dolmus_ilan_sayisi}")
    print(f"Toplam haber: {len(haberler)}")
    print(f"Sayfa hatası: {len(hatalar)}")
    print(f"PDF hatası: {pdf_hatalari}")
    print("ilanlar.json oluşturuldu.")


if __name__ == "__main__":
    main()
