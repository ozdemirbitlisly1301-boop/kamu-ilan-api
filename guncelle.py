import hashlib
import io
import json
import os
import re
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlencode, urljoin, urlparse
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
OSYM_DUYURULAR_URL = "https://www.osym.gov.tr/Duyurular/Index"
OSYM_ARAMA_TERIMLERI = (
    "kpss tercih",
    "kpss yerleştirme",
    "kpss kadro pozisyon",
)

OSYM_HABER_ARAMA_TERIMLERI = (
    "kpss başvuruların alınması",
    "kpss başvuru",
    "kpss sonuçları",
    "kpss yerleştirme sonuçları",
    "kpss taban puanlar",
    "kpss atama sonuçları",
    "kpss tercih",
    "kpss tercih sonuçları",
    "ekpss",
    "yks başvuruların alınması",
    "yks başvuru",
    "yks sonuçları",
    "yks tercih",
    "yks tercih sonuçları",
    "yks ek yerleştirme",
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
    {
        "kaynak": "MSB / TSK Personel Temin İlanları",
        "kurum": "Millî Savunma Bakanlığı / Türk Silahlı Kuvvetleri",
        "url": "https://personeltemin.msb.gov.tr/",
        # MSB bazı güncel temin bağlantılarını
        # /AnaSayfa/DuyuruDetay?prmEncrypt=... biçiminde veriyor.
        # Eski filtre sondaki "/" işaretini zorunlu tuttuğu için bu
        # bağlantıları hiç görmüyor ve sonuç 0 çıkıyordu.
        "link_parcalari": ("/AnaSayfa/DuyuruDetay",),
        "kaynak_kodu": "msb_tsk",
    },
)


# Üniversite personel ilanları için iki katman kullanılır:
# 1) ilan.gov.tr merkezi resmî ilan portalı her çalışmada taranır.
# 2) YÖK'ün resmî üniversite listesinden bulunan .edu.tr siteleri dönüşümlü
#    olarak taranır. Böylece yaklaşık 200 siteyi her 15 dakikada bir aynı anda
#    zorlamak yerine, her çalışmada bir grup kontrol edilir.
UNIVERSITE_MERKEZI_KAYNAKLARI = (
    {
        "ad": "ilan.gov.tr Sözleşmeli Personel ve 4/B",
        "url": (
            "https://www.ilan.gov.tr/ilan/kategori/725/"
            "sozlesmeli-personel-ve-4-b-alimlari"
        ),
    },
    {
        "ad": "ilan.gov.tr Kamu Personel Alım ve Sınavları",
        "url": (
            "https://www.ilan.gov.tr/ilan/kategori/44/"
            "kamu-personel-alim-ve-sinavlari"
        ),
    },
    {
        "ad": "ilan.gov.tr Akademik Personel",
        "url": (
            "https://www.ilan.gov.tr/ilan/kategori/8/"
            "personel-alimi-akademik-kadro-ve-egitim-ilanlari"
        ),
    },
)

YOK_UNIVERSITE_LISTESI_URLLERI = (
    "https://www.yok.gov.tr/universiteler/universitelerimiz",
    "https://yokatlas.yok.gov.tr/",
)

UNIVERSITE_SITE_GRUP_BOYUTU = 12
UNIVERSITE_SITE_SAYFA_SINIRI = 5
UNIVERSITE_ILAN_GOV_SAYFA_SINIRI = 10
UNIVERSITE_DETAY_SINIRI = 220

UNIVERSITE_LINK_ONCELIK_KELIMELERI = (
    "personel",
    "personel daire",
    "pdb",
    "duyuru",
    "duyurular",
    "ilan",
    "ilanlar",
    "akademik",
    "idari",
    "sozlesmeli",
    "is ilan",
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

DOSYA_SURUMU = "UNIVERSITE-HABERLERI-V10-2026-08-03"
ANALIZ_SURUMU = 4
MAKSIMUM_PDF_BOYUTU = 30 * 1024 * 1024
MAKSIMUM_PDF_SAYFASI = 80


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
    """Yalnızca açık merkezi atama/tercih duyurularını ilan kabul eder.

    KPSS sınav başvuruları, sınava giriş belgeleri ve sonuç duyuruları haber
    bölümüne gider. ÖSYM Atamaları bölümüne yalnızca merkezi yerleştirme,
    tercih kılavuzu ve kadro/pozisyon duyuruları alınır.
    """
    normal_baslik = arama_metnine_cevir(baslik)

    # Sınavla ilgili başvuru ve sonuç duyuruları iş ilanı değildir.
    haber_ifadeleri = (
        "basvurularin alinmasi",
        "sinav basvurusu",
        "gec basvuru",
        "sinava giris belgeleri",
        "sinava giris belgesi",
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
    if any(ifade in normal_baslik for ifade in haber_ifadeleri):
        return False

    # İlan sayılacak ÖSYM kayıtları merkezi atama/tercih sürecini açıkça
    # belirtmelidir. Sadece "KPSS" ve "başvuru" geçmesi yeterli değildir.
    atama_ifadeleri = (
        "merkezi yerlestirme",
        "tercihlerin alinmasi",
        "tercih kilavuzu",
        "kadro ve pozisyon",
        "kadro pozisyon",
        "sozlesmeli pozisyon",
        "yerlestirme icin tercih",
    )

    return "kpss" in normal_baslik and any(
        ifade in normal_baslik for ifade in atama_ifadeleri
    )


def osym_sinav_turu_bul(baslik):
    normal = arama_metnine_cevir(baslik)

    if "yks" in normal or "yuksekogretim kurumlari sinavi" in normal:
        return "YKS"

    if "ekpss" in normal:
        return "EKPSS"

    if "kpss" in normal or "kamu personel secme sinavi" in normal:
        return "KPSS"

    return ""


def osym_haber_kategorisi_bul(baslik):
    normal = arama_metnine_cevir(baslik)
    sinav_turu = osym_sinav_turu_bul(baslik) or "ÖSYM"

    if (
        ("ek yerlestirme" in normal or "ek tercih" in normal)
        and "sonuc" in normal
    ):
        return f"{sinav_turu} Ek Yerleştirme Sonucu"

    if "ek yerlestirme" in normal or "ek tercih" in normal:
        return f"{sinav_turu} Ek Yerleştirme"

    if "tercih" in normal and "sonuc" in normal:
        return f"{sinav_turu} Tercih Sonucu"

    if "tercih" in normal:
        return f"{sinav_turu} Tercih"

    if (
        "basvuru" in normal
        or "basvurularin alinmasi" in normal
        or "gec basvuru" in normal
    ):
        return f"{sinav_turu} Başvuru"

    if "sonuc" in normal or "sayisal bilgiler" in normal:
        return f"{sinav_turu} Sonuç"

    if "kilavuz" in normal:
        return f"{sinav_turu} Kılavuz"

    if "yerlestirme" in normal:
        return f"{sinav_turu} Yerleştirme"

    return f"{sinav_turu} Duyuru"


def haber_kategorisi_bul(baslik):
    normal = arama_metnine_cevir(baslik)

    if osym_sinav_turu_bul(baslik):
        return osym_haber_kategorisi_bul(baslik)

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
    """KPSS, EKPSS ve YKS ile ilgili tüm resmî ÖSYM duyurularını kabul eder."""
    return bool(osym_sinav_turu_bul(baslik))


def osym_basligini_temizle(metin):
    metin = temizle(metin)
    aylar = (
        "Ocak|Şubat|Mart|Nisan|Mayıs|Haziran|Temmuz|"
        "Ağustos|Eylül|Ekim|Kasım|Aralık"
    )

    metin = re.sub(
        rf"^\s*\d{{1,2}}\s+(?:{aylar})\s+\d{{4}}\s*",
        "",
        metin,
        flags=re.IGNORECASE,
    )
    return temizle(metin).strip(" -|")


def osym_yayin_tarihi_bul(metin):
    sayisal = tarih_bul(metin)

    if sayisal:
        return sayisal

    tarihler = turkce_tarihleri_bul(metin)

    if tarihler:
        return max(tarihler).strftime("%d.%m.%Y")

    return ""


def osym_haber_kaydi_olustur(baslik, link, yayin_tarihi):
    sinav_turu = osym_sinav_turu_bul(baslik)

    return {
        "id": ilan_id_uret(link),
        "baslik": baslik[:400],
        "kurum": "ÖSYM",
        "kategori": osym_haber_kategorisi_bul(baslik),
        "yayin_tarihi": yayin_tarihi,
        "kaynak": f"ÖSYM {sinav_turu} Duyuruları",
        "ozet": (
            f"ÖSYM tarafından yayımlanan resmî {sinav_turu} "
            "başvuru, tercih, yerleştirme veya sonuç duyurusu."
        ),
        "link": link,
    }


def osym_duyurular_sayfasindan_haberleri_al():
    """
    ÖSYM'nin güncel Duyurular sayfasını doğrudan tarar.
    Yeni başvuru, tercih ve sonuç duyuruları arama terimine bağlı kalmaz.
    """
    html = sayfayi_indir(OSYM_DUYURULAR_URL)
    soup = BeautifulSoup(html, "html.parser")
    haberler = []
    gorulen_linkler = set()

    for link_etiketi in soup.find_all("a", href=True):
        ham_baslik = temizle(link_etiketi.get_text(" ", strip=True))
        href = temizle(link_etiketi.get("href", ""))

        if not href or len(ham_baslik) < 10:
            continue

        baslik = osym_basligini_temizle(ham_baslik)

        if len(baslik) < 10 or not osym_haber_duyurusu_mu(baslik):
            continue

        if href.startswith("#") or href.casefold().startswith("javascript:"):
            continue

        link = urljoin(OSYM_DUYURULAR_URL, href)
        normal_link = link.casefold()

        if "osym.gov.tr" not in normal_link:
            continue

        if any(
            parca in normal_link
            for parca in (
                "/arama",
                "/duyurular/index",
                "/sinavtakvimi",
                "/kpss-sinav-takvimi",
            )
        ):
            continue

        anahtar = link_anahtari(link)

        if not anahtar or anahtar in gorulen_linkler:
            continue

        gorulen_linkler.add(anahtar)

        kapsayici = link_etiketi.find_parent(["li", "article", "div", "tr"])
        kapsayici_metni = (
            temizle(kapsayici.get_text(" ", strip=True))
            if kapsayici is not None
            else ham_baslik
        )
        yayin_tarihi = (
            osym_yayin_tarihi_bul(ham_baslik)
            or osym_yayin_tarihi_bul(kapsayici_metni)
        )

        haberler.append(
            osym_haber_kaydi_olustur(
                baslik,
                link,
                yayin_tarihi,
            )
        )

        if len(haberler) >= 250:
            break

    return haberler


def osym_arama_sonuclarindan_haberleri_al():
    """Duyurular sayfası değişirse kullanılacak geniş kapsamlı yedek tarama."""
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
            ham_baslik = temizle(link_etiketi.get_text(" ", strip=True))
            baslik = osym_basligini_temizle(ham_baslik)
            href = temizle(link_etiketi.get("href", ""))

            if len(baslik) < 10 or not href:
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

            if not anahtar or anahtar in gorulen_linkler:
                continue

            gorulen_linkler.add(anahtar)

            kapsayici = link_etiketi.find_parent(["li", "article", "div", "tr"])
            kapsayici_metni = (
                temizle(kapsayici.get_text(" ", strip=True))
                if kapsayici is not None
                else ham_baslik
            )
            yayin_tarihi = (
                osym_yayin_tarihi_bul(ham_baslik)
                or osym_yayin_tarihi_bul(kapsayici_metni)
            )

            haberler.append(
                osym_haber_kaydi_olustur(
                    baslik,
                    link,
                    yayin_tarihi,
                )
            )

            if len(haberler) >= 250:
                return haberler

    return haberler


def osym_kpss_haberlerini_al():
    """
    KPSS, EKPSS ve YKS başvuru/sonuç/tercih/yerleştirme haberlerini alır.
    Eski fonksiyon adı ana akış bozulmasın diye korunmuştur.
    """
    haberler = osym_duyurular_sayfasindan_haberleri_al()

    if haberler:
        return haberler

    return osym_arama_sonuclarindan_haberleri_al()


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
        "kesin kayit",
        "egitim duyurusu",
        "on kayit",
        "ikinci siniflandirma",
        "siniflandirma sonrasi",
        "cagri ilani",
        "cagri durumu",
        "konaklama",
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
        "personel temini",
        "personel temin ilani",
        "uzman erbas temini",
        "uzman erbas temin faaliyeti",
        "sozlesmeli er temini",
        "sozlesmeli er temin faaliyeti",
        "muvazzaf subay temini",
        "sozlesmeli subay temini",
        "muvazzaf astsubay temini",
        "sozlesmeli astsubay temini",
        "askeri ogrenci temini",
        "devlet memuru temini",
        "surekli isci temini",
        "teknik sinif uzman erbas",
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


def yayin_tarihi_bul(metin):
    """
    Duyurunun yayımlanma tarihini bulur.

    Öncelikle açık bir "Yayın tarihi" işaretini arar. Bulamazsa metnin
    başlangıcındaki ilk tarihi kullanır; resmî duyuru sayfalarında yayın
    tarihi genellikle başlığın hemen üstünde yer alır.
    """
    normal = arama_metnine_cevir(metin)

    for anahtar in ("yayin tarihi", "duyuru tarihi", "yayinlanma tarihi"):
        konum = normal.find(anahtar)
        if konum >= 0:
            pencere = normal[konum:konum + 180]
            tarihler = turkce_tarihleri_bul(pencere)
            if tarihler:
                return tarihler[0].strftime("%d.%m.%Y")

    baslangic_tarihleri = turkce_tarihleri_bul(normal[:900])
    if baslangic_tarihleri:
        return baslangic_tarihleri[0].strftime("%d.%m.%Y")

    return ""



def son_basvuru_tarihi_bul(metin):
    """Başvuru bölümündeki en ileri tarihi son başvuru olarak seçer."""
    normal = arama_metnine_cevir(metin)
    aday_tarihler = []

    anahtarlar = (
        "son basvuru",
        "son muracaat",
        "basvuru bitis",
        "basvurunun son gunu",
        "basvurularin son gunu",
        "basvurular",
        "basvuru tarih",
        "basvurularini",
        "basvuru suresi",
        "muracaat tarih",
        "tercih islemleri",
        "tercih suresi",
        "tercih tarih",
        "bitis tarihi",
        "son tarih",
        "kadar uzatil",
        "suresinin uzatil",
        "sona erecektir",
        "sona erer",
    )

    for anahtar in anahtarlar:
        baslangic = 0
        while True:
            konum = normal.find(anahtar, baslangic)
            if konum < 0:
                break

            # Başlangıç ve bitiş tarihleri aynı cümlede olabildiği için geniş
            # bir pencere kullanıp en ileri tarihi seçiyoruz.
            pencere = normal[max(0, konum - 120):konum + 520]
            aday_tarihler.extend(turkce_tarihleri_bul(pencere))
            baslangic = konum + len(anahtar)

    if not aday_tarihler:
        return ""

    return max(aday_tarihler).strftime("%d.%m.%Y")


BILINEN_BASVURU_ADRESLERI = {
    "kariyerkapisi.gov.tr": "https://kariyerkapisi.gov.tr/isealim",
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
    "cevrimici basvuru",
    "elektronik ortamda",
    "elektronik olarak",
    "internet uzerinden",
    "e-devlet uzerinden",
    "e devlet uzerinden",
    "e-devlet kapisi uzerinden",
    "e devlet kapisi uzerinden",
    "kariyer kapisi",
    "kamu ise alim platformu",
    "basvuru adresi",
    "basvuru ekrani",
    "yalnizca elektronik",
    "sadece elektronik",
)

# Bu ifadeler fiziksel başvurunun reddedildiğini, dolayısıyla başvurunun
# online olduğunu gösterir.
SAHSEN_POSTA_KABUL_EDILMEZ_IFADELERI = (
    "sahsen veya posta yoluyla yapilan basvurular kabul edilmeyecektir",
    "sahsen ve posta yoluyla yapilan basvurular kabul edilmeyecektir",
    "sahsen ya da posta yoluyla yapilan basvurular kabul edilmeyecektir",
    "sahsen veya posta ile yapilan basvurular kabul edilmeyecektir",
    "sahsen ve posta ile yapilan basvurular kabul edilmeyecektir",
    "sahsen veya posta yoluyla basvuru kabul edilmeyecektir",
    "sahsen basvuru kabul edilmeyecektir",
    "posta yoluyla basvuru kabul edilmeyecektir",
    "elden basvuru kabul edilmeyecektir",
)

# Yalnızca gerçekten fiziksel başvuru tarif eden kesin ifadeler.
ONLINE_OLMAYAN_BASVURU_IFADELERI = (
    "basvurular sahsen yapilacaktir",
    "basvurular sahsen alinacaktir",
    "sahsen muracaat edilecektir",
    "elden teslim edilecektir",
    "posta yoluyla gonderilecektir",
    "kargo yoluyla gonderilecektir",
    "kuruma sahsen teslim",
    "basvuru formu ile birlikte kuruma teslim",
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
    """Sayfadaki gerçek başvuru bağlantısını puanlayarak bulur."""
    adaylar = []

    for sira, etiket in enumerate(soup.find_all("a", href=True)):
        href = temizle(etiket.get("href", ""))
        link = guvenli_web_linki(href, temel_url)

        if not link:
            continue

        yazi = arama_metnine_cevir(
            etiket.get_text(" ", strip=True)
        )
        normal_href = arama_metnine_cevir(href)
        normal_link = link.casefold()
        puan = 0

        if any(
            ifade in yazi
            for ifade in (
                "basvuru yapmak",
                "basvuru icin",
                "basvuru ekrani",
                "tercih yap",
                "e-devlet ile giris",
                "giris yapmak",
            )
        ):
            puan += 100

        if "basvuru" in yazi:
            puan += 45

        if any(
            ifade in normal_href
            for ifade in ("basvuru", "apply", "tercih", "giris", "login")
        ):
            puan += 35

        if any(
            domain in normal_link
            for domain in (
                "isealimkariyerkapisi.cbiko.gov.tr",
                "kariyerkapisi.cbiko.gov.tr",
                "ais.osym.gov.tr",
                "esube.iskur.gov.tr",
                "turkiye.gov.tr",
                "vatandas.jandarma.gov.tr",
            )
        ):
            puan += 30

        # MSB sitesindeki menü ve logo bağlantılarını başvuru sanma.
        if (
            "personeltemin.msb.gov.tr" in normal_link
            and not any(
                ifade in yazi or ifade in normal_href
                for ifade in (
                    "basvuru",
                    "tercih",
                    "giris",
                    "e-devlet",
                )
            )
        ):
            puan -= 80

        if puan > 0:
            adaylar.append((puan, -sira, link))

    if not adaylar:
        return ""

    adaylar.sort(reverse=True)
    return adaylar[0][2]


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
    metin = temizle(metin)
    normal = arama_metnine_cevir(metin)
    link = ""

    if soup is not None:
        link = html_basvuru_linki_bul(soup, temel_url)

    if not link:
        link = metinden_basvuru_linki_bul(metin)

    online_ifadesi_var = any(
        ifade in normal for ifade in ONLINE_BASVURU_IFADELERI
    )
    fiziksel_reddedilmis = any(
        ifade in normal for ifade in SAHSEN_POSTA_KABUL_EDILMEZ_IFADELERI
    )

    # Açık platform adı yazıyor fakat PDF'de URL parçalanmışsa güvenli ana
    # başvuru adresini kullan.
    if not link and "kariyer kapisi" in normal:
        link = "https://kariyerkapisi.gov.tr/isealim"
    elif not link and any(
        ifade in normal
        for ifade in ("e-devlet", "e devlet", "turkiye.gov.tr")
    ):
        link = "https://www.turkiye.gov.tr"

    if link or online_ifadesi_var or fiziksel_reddedilmis:
        if not link and temel_url:
            aday = guvenli_web_linki(temel_url, temel_url)
            if aday:
                link = aday

        aciklama = "Başvurular online olarak yapılmaktadır."
        if not link:
            aciklama = (
                "İlanda online başvuru belirtilmiştir. Doğrudan başvuru "
                "bağlantısı için ilanın yayımlandığı sayfayı açın."
            )

        return True, link, aciklama

    if any(ifade in normal for ifade in ONLINE_OLMAYAN_BASVURU_IFADELERI):
        return (
            False,
            "",
            "Başvurular ilanda belirtilen şahsen, posta veya teslim yöntemiyle "
            "yapılmalıdır.",
        )

    # Metin yetersizse yanlış biçimde "online değil" demeyelim.
    return (
        False,
        "",
        "Başvuru yöntemi metinden kesin olarak belirlenemedi. İlan belgesini "
        "ve yayımlandığı sayfayı kontrol edin.",
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

    tum_sayfa_metni = temizle(
        temiz_soup.get_text(" ", strip=True)
    )

    if adaylar:
        detay_metni = max(adaylar, key=lambda oge: oge[0])[1]
    else:
        detay_metni = tum_sayfa_metni

    # Bazı sitelerde yayın tarihi içerik kutusunun dışında kalır. Bu tarihi
    # detay metnine ekleyerek eski duyuruların aktif ilan sanılmasını önleriz.
    sayfa_yayin_tarihi = yayin_tarihi_bul(tum_sayfa_metni)
    if sayfa_yayin_tarihi:
        detay_metni = (
            f"Yayın tarihi: {sayfa_yayin_tarihi}. "
            f"{detay_metni}"
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


def msb_tsk_aktif_temin_mi(baslik, detay_metni, son_basvuru):
    """
    MSB ana sayfasındaki Güncel Teminler kartlarını kabul eder.

    Bazı MSB ilanlarında son başvuru tarihi detay sayfasından düzenli biçimde
    okunamadığı için ilanı yalnızca tarih bulunamadı diye silmeyiz. İlan,
    Güncel Teminler bölümünden kalktığında sonraki güncellemede zaten listeden
    otomatik çıkar.
    """
    normal_baslik = arama_metnine_cevir(baslik)
    normal_detay = arama_metnine_cevir(detay_metni)
    birlesik = f"{normal_baslik} {normal_detay}"

    temin_ifadeleri = (
        "uzman erbas temini",
        "uzman erbas temin faaliyeti",
        "teknik sinif uzman erbas",
        "sozlesmeli er temini",
        "sozlesmeli er temin faaliyeti",
        "muvazzaf subay temini",
        "sozlesmeli subay temini",
        "muvazzaf astsubay temini",
        "sozlesmeli astsubay temini",
        "devlet memuru temini",
        "sozlesmeli personel temini",
        "sozlesmeli personel",
        "bilisim personeli temini",
        "uzman yardimcisi temini",
        "surekli isci temini",
        "askeri ogrenci temini",
        "personel temini",
        "temin faaliyeti",
    )

    surec_duyurusu_ifadeleri = (
        "sonuc duyurusu",
        "sinav sonucu",
        "itiraz degerlendirme",
        "kesin kayit",
        "egitim duyurusu",
        "cagri ilani",
        "cagri durumu",
        "ikinci siniflandirma",
        "on kayit",
        "secim asamasi",
        "konaklama",
        "sinav asamasi",
    )

    if any(ifade in normal_baslik for ifade in surec_duyurusu_ifadeleri):
        return False

    return any(ifade in birlesik for ifade in temin_ifadeleri)


def resmi_kaynaktan_ilanlari_al(kaynak):
    """GSB, Aile ve Adalet Bakanlığı resmî duyuru sayfalarını tarar."""
    html = sayfayi_indir(kaynak["url"])
    soup = BeautifulSoup(html, "html.parser")
    ilanlar = []
    gorulen_linkler = set()

    for link_etiketi in soup.find_all("a", href=True):
        href = temizle(link_etiketi.get("href", ""))

        if not href:
            continue

        link = urljoin(kaynak["url"], href)
        normal_link = link.casefold()

        if not any(
            parca.casefold() in normal_link
            for parca in kaynak["link_parcalari"]
        ):
            continue

        kapsayici = link_etiketi.find_parent(
            ["li", "article", "div", "tr", "section"]
        )
        kapsayici_metni = (
            temizle(kapsayici.get_text(" ", strip=True))
            if kapsayici is not None
            else ""
        )
        liste_yayin_tarihi = yayin_tarihi_bul(kapsayici_metni)

        # MSB ana sayfası yalnızca güncel temin kartlarını gösterir. Diğer
        # resmî kaynaklarda ise arşiv yılları aynı sayfada bulunabildiği için,
        # çok eski kartları daha detay sayfasına gitmeden eliyoruz.
        if (
            kaynak.get("kaynak_kodu") != "msb_tsk"
            and liste_yayin_tarihi
        ):
            try:
                liste_tarihi = datetime.strptime(
                    liste_yayin_tarihi,
                    "%d.%m.%Y",
                ).replace(tzinfo=ZoneInfo("Europe/Istanbul"))
                simdi_tr = datetime.now(ZoneInfo("Europe/Istanbul"))

                if liste_tarihi < simdi_tr - timedelta(days=180):
                    continue
            except ValueError:
                pass

        # MSB kartlarında bağlantının kendi yazısı bazen boş veya kısadır.
        # Başlığı kartın içindeki başlık etiketlerinden ve erişilebilirlik
        # alanlarından da toplamaya çalış.
        baslik_adaylari = [
            temizle(link_etiketi.get_text(" ", strip=True)),
            temizle(link_etiketi.get("title", "")),
            temizle(link_etiketi.get("aria-label", "")),
            kapsayici_metni,
        ]

        if kapsayici is not None:
            for etiket_adi in ("h1", "h2", "h3", "h4", "h5", "strong"):
                for etiket in kapsayici.find_all(etiket_adi):
                    baslik_adaylari.append(
                        temizle(etiket.get_text(" ", strip=True))
                    )

        baslik_adaylari = [
            aday
            for aday in baslik_adaylari
            if 12 <= len(aday) <= 700
        ]

        uygun_basliklar = [
            aday
            for aday in baslik_adaylari
            if personel_alim_duyurusu_mu(aday)
        ]

        if not uygun_basliklar:
            continue

        # En kısa uygun metin çoğu zaman yalnızca gerçek kart başlığıdır;
        # tüm kart açıklamasını başlık olarak almamayı sağlar.
        baslik = min(uygun_basliklar, key=len)

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
            detay_metni = temizle(f"{kapsayici_metni} {baslik}")
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
        yayin_tarihi = (
            yayin_tarihi_bul(detay_metni)
            or liste_yayin_tarihi
        )

        if kaynak.get("kaynak_kodu") == "msb_tsk":
            if not son_basvuru:
                liste_tarihleri = turkce_tarihleri_bul(kapsayici_metni)
                if liste_tarihleri:
                    son_basvuru = max(liste_tarihleri).strftime("%d.%m.%Y")

            if not msb_tsk_aktif_temin_mi(
                gercek_baslik,
                detay_metni,
                son_basvuru,
            ):
                continue

        kpss_gerekli, minimum_puan, kpss_durumu = (
            kpss_bilgisi_bul(detay_metni, "ok")
        )

        ilanlar.append({
            "id": ilan_id_uret(link),
            "baslik": gercek_baslik[:400],
            "kurum": kaynak["kurum"],
            "sehir": "Türkiye Geneli",
            "tur": (
                "Askerî / MSB Personel Alımı"
                if kaynak.get("kaynak_kodu") == "msb_tsk"
                else "Personel Alımı"
            ),
            "kaynak": kaynak["kaynak"],
            "kaynak_kodu": kaynak.get("kaynak_kodu", "resmi_duyuru"),
            "son_basvuru": son_basvuru,
            "yayin_tarihi": yayin_tarihi,
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
        sayfa_sayisi = min(len(okuyucu.pages), MAKSIMUM_PDF_SAYFASI)

        for sayfa_no in range(sayfa_sayisi):
            sayfa = okuyucu.pages[sayfa_no]
            metin = ""

            # Layout modu tablo ve sütunlardaki tarih/URL'leri daha iyi korur.
            try:
                metin = sayfa.extract_text(extraction_mode="layout") or ""
            except TypeError:
                try:
                    metin = sayfa.extract_text() or ""
                except Exception:
                    metin = ""
            except Exception:
                try:
                    metin = sayfa.extract_text() or ""
                except Exception:
                    metin = ""

            if metin:
                parcalar.append(metin)

            # Yeterli metin ve başvuru tarihi bulunduysa çok uzun ekleri
            # gereksiz yere okumayıp GitHub Actions süresini koru.
            biriken = " ".join(parcalar)
            normal_biriken = arama_metnine_cevir(biriken)
            if (
                sayfa_no >= 7
                and len(biriken) >= 12000
                and any(
                    ifade in normal_biriken
                    for ifade in (
                        "son basvuru",
                        "basvuru tarih",
                        "bitis tarihi",
                        "sona erecektir",
                    )
                )
            ):
                break

        metin = temizle("\n".join(parcalar))

        if len(metin) < 80:
            return metin, "metin_yok"

        return metin[:400000], "ok"
    except Exception as hata:
        return "", f"hata:{type(hata).__name__}"


def minimum_kpss_puani_bul(normal_metin):
    """KPSS puan türündeki P3/P93/P94 sayılarını taban puan sanmadan okur."""
    puanlar = []

    # P3, P93, P94 gibi puan türleri sayı yakalama desenlerini yanıltmasın.
    puan_turleri_temiz = re.sub(
        r"\b(?:kpss\s*[/\-]?\s*)?p\s*\d{1,3}\b",
        " kpss_puan_turu ",
        normal_metin,
    )

    for eslesme in re.finditer(r"\bkpss(?:_puan_turu)?\b", puan_turleri_temiz):
        baslangic = max(0, eslesme.start() - 100)
        bitis = min(len(puan_turleri_temiz), eslesme.end() + 220)
        pencere = puan_turleri_temiz[baslangic:bitis]

        desenler = [
            r"(?:en az|minimum|taban)\s*(\d{2,3}(?:[.,]\d+)?)\s*puan",
            r"(\d{2,3}(?:[.,]\d+)?)\s*(?:puan\s*)?(?:ve|veya)\s*uzeri",
            r"(\d{2,3}(?:[.,]\d+)?)\s*ve\s*uzeri\s*puan",
            r"puan(?:indan|indan|turu(?:nden)?)?[^\d]{0,30}"
            r"(\d{2,3}(?:[.,]\d+)?)\s*(?:ve\s*uzeri\s*)?puan",
            r"kpss(?:_puan_turu)?[^\d]{0,80}"
            r"(\d{2,3}(?:[.,]\d+)?)\s*(?:ve\s*uzeri\s*)?puan",
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
    # Yalnızca başarıyla okunan PDF analizini kullan. Önceki metin_yok veya
    # şifreli sonuçlarını kalıcılaştırmayıp sonraki çalışmada yeniden dene.
    return (
        isinstance(onceki, dict)
        and onceki.get("analiz_surumu") == ANALIZ_SURUMU
        and onceki.get("pdf_isleme_durumu") == "ok"
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
        "son_basvuru",
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

    # MSB kayıtları HTML sayfasından hazırlandığı için PDF okuyucuya gönderilmez.
    if (
        ilan.get("kaynak_kodu") == "msb_tsk"
        and ilan.get("pdf_isleme_durumu") in ("html_ok", "html_baslik")
    ):
        return ilan, False

    belge_linki = ilan.get("belge_linki") or ilan.get("link", "")
    pdf_metni, pdf_durumu = pdf_metnini_oku(belge_linki)

    kpss_gerekli, minimum_puan, kpss_durumu = (
        kpss_bilgisi_bul(pdf_metni, pdf_durumu)
    )
    bulunan_online, bulunan_link, bulunan_aciklama = basvuru_bilgisi_bul(
        pdf_metni,
        temel_url=ilan.get("kaynak_sayfa_linki", "") or ilan.get("link", ""),
    )

    # İlan sayfasından daha önce bulunan online bilgisini PDF analizi silmesin.
    mevcut_online = ilan.get("basvuru_online") is True
    mevcut_link = temizle(ilan.get("basvuru_linki", ""))
    mevcut_aciklama = temizle(ilan.get("basvuru_aciklamasi", ""))

    if mevcut_online and not bulunan_online:
        basvuru_online = True
        basvuru_linki = mevcut_link
        basvuru_aciklamasi = (
            mevcut_aciklama or "Başvurular online olarak yapılmaktadır."
        )
    else:
        basvuru_online = bulunan_online
        basvuru_linki = bulunan_link or mevcut_link
        basvuru_aciklamasi = bulunan_aciklama or mevcut_aciklama

    if pdf_durumu != "ok" and not basvuru_linki and not mevcut_online:
        basvuru_aciklamasi = (
            "PDF metni tam okunamadı. Başvuru yöntemini ilan belgesinden veya "
            "ilanın yayımlandığı sayfadan kontrol edin."
        )

    # Liste sayfasında tarih yoksa veya PDF daha açık bir tarih veriyorsa PDF
    # içindeki son başvuru tarihini uygulamaya aktar.
    pdf_son_basvuru = son_basvuru_tarihi_bul(pdf_metni) if pdf_metni else ""
    son_basvuru = pdf_son_basvuru or temizle(ilan.get("son_basvuru", ""))

    ilan.update({
        "kpss_gerekli": kpss_gerekli,
        "minimum_puan": minimum_puan,
        "kpss_durumu": kpss_durumu,
        "mezuniyetler": mezuniyetleri_bul(pdf_metni),
        "bolumler": bolumleri_bul(pdf_metni),
        "pdf_isleme_durumu": pdf_durumu,
        "analiz_surumu": ANALIZ_SURUMU,
        "son_basvuru": son_basvuru,
        "belge_linki": belge_linki,
        "kaynak_sayfa_linki": ilan.get("kaynak_sayfa_linki", ""),
        "basvuru_linki": basvuru_linki,
        "basvuru_online": basvuru_online,
        "basvuru_aciklamasi": basvuru_aciklamasi,
    })

    return ilan, False



def universite_ifadesi_var_mi(metin):
    normal = arama_metnine_cevir(metin)
    return any(
        ifade in normal
        for ifade in (
            "universite",
            "yuksek teknoloji enstitusu",
            "yuksekogretim kurumu",
        )
    )


def universite_personel_ilani_mi(baslik, detay_metni="", kurum_adi=""):
    """Üniversiteye ait açık personel alımını sonuç duyurularından ayırır."""
    birlesik = arama_metnine_cevir(
        " ".join((baslik or "", detay_metni or "", kurum_adi or ""))
    )

    if not universite_ifadesi_var_mi(birlesik):
        return False

    engellenenler = (
        "basvuru sonucu",
        "basvuru sonuclari",
        "sinav sonucu",
        "sinav sonuclari",
        "on degerlendirme sonucu",
        "on degerlendirme sonuclari",
        "nihai degerlendirme sonucu",
        "nihai degerlendirme sonuclari",
        "yerlestirme sonucu",
        "yerlestirme sonuclari",
        "mulakat sonucu",
        "mulakat sonuclari",
        "sozlu sinav sonucu",
        "sozlu sinav sonuclari",
        "kazanan aday",
        "yedek aday",
        "atamaya hak kazanan",
        "evrak teslim",
        "belge teslim",
        "duzeltme ilani",
        "iptal ilani",
        "ilan iptali",
    )
    if any(ifade in birlesik for ifade in engellenenler):
        return False

    alim_ifadeleri = (
        "personel alim",
        "personel alimi",
        "personel alinacaktir",
        "sozlesmeli personel",
        "4/b personel",
        "4-b personel",
        "surekli isci",
        "gecici isci",
        "isci alim",
        "memur alim",
        "akademik personel",
        "ogretim uyesi alim",
        "ogretim elemani alim",
        "ogretim gorevlisi alim",
        "arastirma gorevlisi alim",
        "profesor alinacaktir",
        "docent alinacaktir",
        "doktor ogretim uyesi",
        "uzman alim",
        "kariyer kapisi",
        "kamu ise alim",
    )
    return any(ifade in birlesik for ifade in alim_ifadeleri)


def universite_personel_haberi_mi(baslik, detay_metni="", kurum_adi=""):
    """Üniversitelerin personel alımıyla ilgili sonuç ve süreç duyurularını seçer.

    Öğrenci kayıt/yerleştirme sonuçlarının yanlışlıkla Haberler'e girmemesi için
    hem sonuç/süreç ifadesi hem de personel-kadro bağlamı aranır.
    """
    birlesik = arama_metnine_cevir(
        " ".join((baslik or "", detay_metni or "", kurum_adi or ""))
    )

    haber_ifadeleri = (
        "basvuru sonucu",
        "basvuru sonuclari",
        "basvuru degerlendirme sonucu",
        "basvuru degerlendirme sonuclari",
        "on degerlendirme sonucu",
        "on degerlendirme sonuclari",
        "nihai degerlendirme sonucu",
        "nihai degerlendirme sonuclari",
        "sinav sonucu",
        "sinav sonuclari",
        "yazili sinav",
        "sozlu sinav",
        "uygulamali sinav",
        "mulakat",
        "yerlestirme sonucu",
        "yerlestirme sonuclari",
        "asil aday",
        "yedek aday",
        "kazanan aday",
        "atamaya hak kazanan",
        "atama sonucu",
        "atama sonuclari",
        "evrak teslim",
        "belge teslim",
        "goreve baslama",
        "duzeltme ilani",
        "duzeltme duyurusu",
        "iptal ilani",
        "ilan iptali",
    )
    if not any(ifade in birlesik for ifade in haber_ifadeleri):
        return False

    personel_baglamlari = (
        "personel",
        "personel daire",
        "/pdb",
        "pdb/",
        "sozlesmeli",
        "4/b",
        "4-b",
        "surekli isci",
        "gecici isci",
        "isci alim",
        "memur alim",
        "akademik personel",
        "ogretim uyesi",
        "ogretim elemani",
        "ogretim gorevlisi",
        "arastirma gorevlisi",
        "profesor",
        "docent",
        "doktor ogretim uyesi",
        "kadro",
        "atamaya hak kazanan",
        "ise alim",
    )
    return any(ifade in birlesik for ifade in personel_baglamlari)


def universite_haber_kategorisi_bul(metin):
    normal = arama_metnine_cevir(metin)
    if "ilan iptali" in normal or "iptal ilani" in normal:
        return "İptal"
    if "duzeltme" in normal:
        return "Düzeltme"
    if "belge teslim" in normal or "evrak teslim" in normal or "goreve baslama" in normal:
        return "Belge Teslimi"
    if "nihai degerlendirme" in normal:
        return "Nihai Değerlendirme"
    if "on degerlendirme" in normal:
        return "Ön Değerlendirme"
    if "mulakat" in normal or "sozlu sinav" in normal:
        return "Mülakat"
    if "yazili sinav" in normal or "uygulamali sinav" in normal or "sinav sonuc" in normal:
        return "Sınav Sonucu"
    if "atama" in normal or "asil aday" in normal or "yedek aday" in normal or "kazanan aday" in normal:
        return "Atama"
    if "yerlestirme" in normal:
        return "Yerleştirme"
    return "Sonuç"


def universite_haber_kaydi_olustur(
    link,
    baslik,
    kurum,
    kapsayici_metni="",
    kaynak="Üniversite Personel Haberleri",
):
    yayin_tarihi = tarih_bul(kapsayici_metni) or tarih_bul(baslik)
    kategori = universite_haber_kategorisi_bul(
        f"{baslik} {kapsayici_metni[:800]}"
    )
    return {
        "id": ilan_id_uret(link),
        "baslik": baslik_temizle(baslik)[:400],
        "kurum": temizle(kurum)[:180] or "Üniversite",
        "kategori": kategori,
        "yayin_tarihi": yayin_tarihi,
        "kaynak": kaynak,
        "kaynak_kodu": "universite_personel_haber",
        "ozet": (
            f"{temizle(kurum) or 'Üniversite'} tarafından yayımlanan resmî "
            f"personel {kategori.casefold()} duyurusu."
        ),
        "link": link,
    }


def universite_ilan_turu_bul(metin):
    normal = arama_metnine_cevir(metin)
    if any(
        ifade in normal
        for ifade in (
            "ogretim uyesi",
            "ogretim elemani",
            "ogretim gorevlisi",
            "arastirma gorevlisi",
            "profesor",
            "docent",
            "akademik personel",
        )
    ):
        return "Akademik Personel"
    if "surekli isci" in normal or "isci alim" in normal:
        return "İşçi"
    if "sozlesmeli personel" in normal or "4/b" in normal or "4-b" in normal:
        return "Sözleşmeli Personel"
    return "Üniversite Personel Alımı"


def universite_kurum_adi_bul(baslik, metin, varsayilan=""):
    aday_metin = temizle(f"{baslik} {metin[:1200]}")
    eslesme = re.search(
        r"([A-ZÇĞİÖŞÜa-zçğıöşü0-9 .'-]{3,120}?"
        r"(?:Üniversitesi|Yüksek Teknoloji Enstitüsü))"
        r"(?:\s+Rektörlüğünden|\s+Rektörlüğü|\s*[:\-]|\s*$)",
        aday_metin,
        flags=re.IGNORECASE,
    )
    if eslesme:
        return baslik_temizle(eslesme.group(1))[:180]
    return temizle(varsayilan)[:180] or "Üniversite"


def universite_sehir_bul(metin, kurum=""):
    # ilan.gov.tr ayrıntı alanındaki şehir değerini önce yakala.
    eslesme = re.search(
        r"Şehir\s+([A-ZÇĞİÖŞÜ ]{2,35}?)\s+(?:İlan Türü|Resm[iî] Gazete|Yayın)",
        metin,
        flags=re.IGNORECASE,
    )
    if eslesme:
        aday_ham = temizle(eslesme.group(1))
        aday_normal = arama_metnine_cevir(aday_ham)
        for kod, ad in SEHIRLER:
            if aday_normal == kod or re.search(rf"\b{re.escape(kod)}\b", aday_normal):
                return ad
        if len(aday_ham) <= 35:
            return aday_ham

    normal = arama_metnine_cevir(f"{kurum} {metin[:800]}")
    for kod, ad in SEHIRLER:
        if re.search(rf"\b{re.escape(kod)}\b", normal):
            return ad
    return "Türkiye Geneli"


def universite_yayin_tarihi_bul(metin):
    eslesme = re.search(
        r"Resm[iî]\s+Gazete\s+Yay[ıi]m\s+Tarihi\s*[:\-]?\s*"
        r"(\d{1,2}[./-]\d{1,2}[./-]\d{4})",
        metin,
        flags=re.IGNORECASE,
    )
    if eslesme:
        return eslesme.group(1)
    return yayin_tarihi_bul(metin)


def universite_detayindan_ilan_olustur(
    link,
    baslik_ipucu="",
    kurum_ipucu="",
    kaynak="Üniversite Resmî Duyuruları",
):
    html = sayfayi_indir(link)
    soup = BeautifulSoup(html, "html.parser")

    for etiket in soup(["script", "style", "noscript", "svg"]):
        etiket.decompose()

    sayfa_basligi = ""
    og_baslik = soup.find("meta", attrs={"property": "og:title"})
    if og_baslik and og_baslik.get("content"):
        sayfa_basligi = temizle(og_baslik.get("content"))
    if not sayfa_basligi and soup.find("h1"):
        sayfa_basligi = temizle(soup.find("h1").get_text(" ", strip=True))
    if not sayfa_basligi and soup.title:
        sayfa_basligi = temizle(soup.title.get_text(" ", strip=True))

    baslik = baslik_temizle(sayfa_basligi or baslik_ipucu)
    detay_metni = temizle(soup.get_text(" ", strip=True))
    kurum = universite_kurum_adi_bul(baslik, detay_metni, kurum_ipucu)

    if not universite_personel_ilani_mi(baslik, detay_metni, kurum):
        return None

    belge_linki = ilan_belgesi_linki_bul(soup, link)
    basvuru_online, basvuru_linki, basvuru_aciklamasi = basvuru_bilgisi_bul(
        detay_metni,
        soup=soup,
        temel_url=link,
    )
    yayin_tarihi = universite_yayin_tarihi_bul(detay_metni)
    son_basvuru = son_basvuru_tarihi_bul(detay_metni)
    kpss_gerekli, minimum_puan, kpss_durumu = kpss_bilgisi_bul(
        detay_metni,
        "ok",
    )

    # Ayrıntılı şartlar HTML içinde varsa doğrudan kullan. Sayfa sadece kısa
    # bir PDF bağlantısı içeriyorsa mevcut PDF analiz aşaması belgeyi okuyacak.
    html_yeterli = len(detay_metni) >= 900 and any(
        ifade in arama_metnine_cevir(detay_metni)
        for ifade in ("kpss", "aranan sart", "genel sart", "basvuru")
    )

    return {
        "id": ilan_id_uret(link),
        "baslik": baslik[:400],
        "kurum": kurum,
        "sehir": universite_sehir_bul(detay_metni, kurum),
        "tur": universite_ilan_turu_bul(f"{baslik} {detay_metni[:1500]}"),
        "kaynak": kaynak,
        "kaynak_kodu": "universite_personel",
        "son_basvuru": son_basvuru,
        "yayin_tarihi": yayin_tarihi,
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
        "pdf_isleme_durumu": "html_ok" if html_yeterli or not belge_linki else "bekliyor",
        "analiz_surumu": ANALIZ_SURUMU,
    }


def _ilan_gov_tr_ilan_linki_mi(link):
    ayrik = urlparse(link)
    host = ayrik.netloc.casefold().removeprefix("www.")
    return host == "ilan.gov.tr" and re.match(r"^/ilan/\d+(?:/|$)", ayrik.path) is not None


def ilan_gov_tr_sayfasindan_linkleri_bul(html, temel_url):
    soup = BeautifulSoup(html, "html.parser")
    bulunanlar = set()

    for etiket in soup.find_all("a", href=True):
        link = urljoin(temel_url, temizle(etiket.get("href", "")))
        if _ilan_gov_tr_ilan_linki_mi(link):
            bulunanlar.add(link.split("#")[0])

    # Bazı sayfalarda ilan kartları JavaScript/JSON içinde tutuluyor.
    ham = html.decode("utf-8", errors="ignore") if isinstance(html, bytes) else str(html)
    ham = ham.replace("\\/", "/")
    for parca in re.findall(r"(?:https?://www\.ilan\.gov\.tr)?(/ilan/\d+(?:/[^\"'<> ]*)?)", ham):
        link = urljoin("https://www.ilan.gov.tr", parca)
        if _ilan_gov_tr_ilan_linki_mi(link):
            bulunanlar.add(link.split("#")[0])

    return bulunanlar


def ilan_gov_tr_sitemap_linklerini_bul():
    """Liste sayfası JS ile boş dönerse sitemap üzerinden ilan bağlantısı bulur."""
    sitemap_adaylari = {
        "https://www.ilan.gov.tr/sitemap.xml",
        "https://www.ilan.gov.tr/sitemap_index.xml",
    }
    try:
        robots = sayfayi_indir("https://www.ilan.gov.tr/robots.txt")
        robots_metni = robots.decode("utf-8", errors="ignore")
        sitemap_adaylari.update(
            temizle(eslesme)
            for eslesme in re.findall(r"(?im)^Sitemap:\s*(https?://\S+)", robots_metni)
        )
    except Exception:
        pass

    sonuc = set()
    alt_sitemapler = []
    for sitemap in list(sitemap_adaylari)[:5]:
        try:
            xml = sayfayi_indir(sitemap)
        except Exception:
            continue
        metin = xml.decode("utf-8", errors="ignore")
        konumlar = re.findall(r"<loc>\s*([^<]+?)\s*</loc>", metin, flags=re.IGNORECASE)
        for konum in konumlar:
            konum = konum.replace("&amp;", "&").strip()
            if _ilan_gov_tr_ilan_linki_mi(konum):
                sonuc.add(konum.split("#")[0])
            elif "sitemap" in konum.casefold() and len(alt_sitemapler) < 12:
                alt_sitemapler.append(konum)

    # En güncel dosyalar genelde adında tarih/sayı taşıdığı için sondan başla.
    for sitemap in sorted(set(alt_sitemapler), reverse=True)[:6]:
        try:
            xml = sayfayi_indir(sitemap)
        except Exception:
            continue
        metin = xml.decode("utf-8", errors="ignore")
        for konum in re.findall(r"<loc>\s*([^<]+?)\s*</loc>", metin, flags=re.IGNORECASE):
            konum = konum.replace("&amp;", "&").strip()
            if _ilan_gov_tr_ilan_linki_mi(konum):
                sonuc.add(konum.split("#")[0])
        if len(sonuc) >= UNIVERSITE_DETAY_SINIRI:
            break

    return sonuc


def ilan_gov_tr_universite_ilanlarini_al(onceki_veri=None):
    linkler = set()
    hatalar = []

    for kaynak in UNIVERSITE_MERKEZI_KAYNAKLARI:
        ardisik_bos = 0
        for sayfa in range(UNIVERSITE_ILAN_GOV_SAYFA_SINIRI):
            try:
                html = sayfayi_indir(kaynak["url"], params={"currentPage": sayfa})
                bulunan = ilan_gov_tr_sayfasindan_linkleri_bul(html, kaynak["url"])
                yeni = bulunan - linkler
                linkler.update(bulunan)
                ardisik_bos = 0 if yeni else ardisik_bos + 1
                if ardisik_bos >= 2 and sayfa >= 2:
                    break
            except Exception as hata:
                hatalar.append(
                    f"{kaynak['ad']} sayfa {sayfa}: {type(hata).__name__} - {str(hata)[:120]}"
                )
                break

    if not linkler:
        try:
            linkler.update(ilan_gov_tr_sitemap_linklerini_bul())
        except Exception as hata:
            hatalar.append(f"ilan.gov.tr sitemap: {type(hata).__name__} - {str(hata)[:120]}")

    # İlan numarası yüksek olanlar yeni olduğundan önce onları kontrol et.
    def ilan_numarasi(link):
        eslesme = re.search(r"/ilan/(\d+)", link)
        return int(eslesme.group(1)) if eslesme else 0

    sirali_linkler = sorted(linkler, key=ilan_numarasi, reverse=True)[:UNIVERSITE_DETAY_SINIRI]
    ilanlar = []

    onceki_harita = {}
    if isinstance(onceki_veri, dict):
        for onceki_ilan in onceki_veri.get("ilanlar", []):
            if onceki_ilan.get("kaynak_kodu") != "universite_personel":
                continue
            anahtar = link_anahtari(onceki_ilan.get("link", ""))
            if anahtar and ilan_aktif_mi(onceki_ilan):
                onceki_harita[anahtar] = onceki_ilan

    yeni_linkler = []
    for link in sirali_linkler:
        onceki = onceki_harita.get(link_anahtari(link))
        if onceki:
            ilanlar.append(onceki)
        else:
            yeni_linkler.append(link)

    with ThreadPoolExecutor(max_workers=6) as havuz:
        futures = {
            havuz.submit(
                universite_detayindan_ilan_olustur,
                link,
                "",
                "",
                "ilan.gov.tr Üniversite Personel İlanları",
            ): link
            for link in yeni_linkler
        }
        for future in as_completed(futures):
            link = futures[future]
            try:
                ilan = future.result()
                if ilan:
                    ilanlar.append(ilan)
            except Exception as hata:
                hatalar.append(
                    f"ilan.gov.tr detay {link}: {type(hata).__name__} - {str(hata)[:100]}"
                )

    return ilanlar, hatalar, len(sirali_linkler)


def yoktan_universite_sitelerini_al(onceki_veri):
    siteler = {}
    hatalar = []

    for url in YOK_UNIVERSITE_LISTESI_URLLERI:
        try:
            html = sayfayi_indir(url)
            soup = BeautifulSoup(html, "html.parser")
        except Exception as hata:
            hatalar.append(f"YÖK üniversite listesi: {type(hata).__name__} - {str(hata)[:120]}")
            continue

        for etiket in soup.find_all("a", href=True):
            link = urljoin(url, temizle(etiket.get("href", "")))
            ayrik = urlparse(link)
            host = ayrik.netloc.casefold().removeprefix("www.")
            if not host.endswith(".edu.tr"):
                continue

            ad = temizle(etiket.get_text(" ", strip=True))
            if not universite_ifadesi_var_mi(ad):
                kapsayici = etiket.find_parent(["li", "tr", "div", "article"])
                if kapsayici is not None:
                    ad = temizle(kapsayici.get_text(" ", strip=True))
            if not universite_ifadesi_var_mi(ad):
                ad = host.split(".")[0].replace("-", " ").title() + " Üniversitesi"

            siteler[host] = {
                "ad": ad[:180],
                "url": f"{ayrik.scheme or 'https'}://{ayrik.netloc}/",
            }

    # YÖK sayfası geçici hata verirse daha önce keşfedilen resmî siteleri koru.
    onceki_siteler = onceki_veri.get("universite_kaynaklari", []) if isinstance(onceki_veri, dict) else []
    for kayit in onceki_siteler:
        if not isinstance(kayit, dict):
            continue
        link = temizle(kayit.get("url", ""))
        host = urlparse(link).netloc.casefold().removeprefix("www.")
        if host.endswith(".edu.tr"):
            siteler.setdefault(host, {"ad": temizle(kayit.get("ad", "")), "url": link})

    return sorted(siteler.values(), key=lambda x: arama_metnine_cevir(x.get("ad", ""))), hatalar


def _edu_tr_kok_alani(host):
    host = temizle(host).casefold().removeprefix("www.")
    parcalar = [parca for parca in host.split(".") if parca]
    if len(parcalar) >= 3 and parcalar[-2:] == ["edu", "tr"]:
        return ".".join(parcalar[-3:])
    return host


def universite_ana_sayfasindan_aday_sayfalari_bul(html, ana_url):
    soup = BeautifulSoup(html, "html.parser")
    ana_host = urlparse(ana_url).netloc.casefold().removeprefix("www.")
    ana_kok = _edu_tr_kok_alani(ana_host)
    puanli = []

    for etiket in soup.find_all("a", href=True):
        href = temizle(etiket.get("href", ""))
        link = urljoin(ana_url, href)
        ayrik = urlparse(link)
        host = ayrik.netloc.casefold().removeprefix("www.")
        # Personel Daire Başkanlığı çoğu üniversitede personel.univ.edu.tr gibi
        # ayrı bir alt alan adında çalışır. Aynı üniversitenin alt alanlarına izin ver.
        if _edu_tr_kok_alani(host) != ana_kok or ayrik.scheme not in ("http", "https"):
            continue

        metin = arama_metnine_cevir(
            f"{etiket.get_text(' ', strip=True)} {ayrik.path}"
        )
        puan = sum(1 for kelime in UNIVERSITE_LINK_ONCELIK_KELIMELERI if kelime in metin)
        if puan:
            puanli.append((puan, link.split("#")[0]))

    puanli.sort(key=lambda x: (-x[0], len(x[1])))
    sonuc = [ana_url]
    for _, link in puanli:
        if link not in sonuc:
            sonuc.append(link)
        if len(sonuc) >= UNIVERSITE_SITE_SAYFA_SINIRI:
            break
    return sonuc


def universite_sitesinden_ilanlari_al(kaynak):
    ana_url = kaynak.get("url", "")
    kurum = kaynak.get("ad", "Üniversite")
    ilan_adaylari = {}
    haber_adaylari = {}
    hatalar = []

    try:
        ana_html = sayfayi_indir(ana_url)
        sayfalar = universite_ana_sayfasindan_aday_sayfalari_bul(ana_html, ana_url)
    except Exception as hata:
        return [], [], [f"{kurum}: {type(hata).__name__} - {str(hata)[:120]}"]

    for sayfa_url in sayfalar:
        try:
            html = ana_html if sayfa_url == ana_url else sayfayi_indir(sayfa_url)
            soup = BeautifulSoup(html, "html.parser")
        except Exception as hata:
            hatalar.append(f"{kurum} sayfa: {type(hata).__name__} - {str(hata)[:100]}")
            continue

        for etiket in soup.find_all("a", href=True):
            baslik = temizle(etiket.get_text(" ", strip=True))
            if len(baslik) < 8:
                continue

            link = urljoin(sayfa_url, temizle(etiket.get("href", "")))
            ayrik = urlparse(link)
            if ayrik.scheme not in ("http", "https"):
                continue
            link = link.split("#")[0]

            kapsayici = etiket.find_parent(["li", "article", "tr", "div"])
            kapsayici_metni = temizle(
                kapsayici.get_text(" ", strip=True)
            ) if kapsayici else baslik
            baglam = f"{sayfa_url} {kapsayici_metni}"

            if universite_personel_ilani_mi(baslik, baglam, kurum):
                ilan_adaylari[link] = baslik
            elif universite_personel_haberi_mi(baslik, baglam, kurum):
                haber_adaylari[link] = (baslik, kapsayici_metni)

            if len(ilan_adaylari) >= 25 and len(haber_adaylari) >= 40:
                break

    ilanlar = []
    for link, baslik in list(ilan_adaylari.items())[:25]:
        try:
            ilan = universite_detayindan_ilan_olustur(
                link,
                baslik,
                kurum,
                "Üniversite Resmî Duyuruları",
            )
            if ilan:
                ilanlar.append(ilan)
        except Exception as hata:
            hatalar.append(f"{kurum} ilan detayı: {type(hata).__name__} - {str(hata)[:100]}")

    haberler = [
        universite_haber_kaydi_olustur(
            link,
            baslik,
            kurum,
            kapsayici_metni,
        )
        for link, (baslik, kapsayici_metni) in list(haber_adaylari.items())[:40]
    ]

    return ilanlar, haberler, hatalar


def universite_resmi_sitelerini_donusumlu_tara(onceki_veri):
    siteler, hatalar = yoktan_universite_sitelerini_al(onceki_veri)
    if not siteler:
        return [], [], siteler, 0, hatalar, 0

    baslangic = 0
    try:
        baslangic = int(onceki_veri.get("universite_tarama_sirasi", 0)) % len(siteler)
    except Exception:
        baslangic = 0

    secilenler = [
        siteler[(baslangic + sira) % len(siteler)]
        for sira in range(min(UNIVERSITE_SITE_GRUP_BOYUTU, len(siteler)))
    ]
    sonraki = (baslangic + len(secilenler)) % len(siteler)
    ilanlar = []
    haberler = []

    with ThreadPoolExecutor(max_workers=5) as havuz:
        futures = {
            havuz.submit(universite_sitesinden_ilanlari_al, kaynak): kaynak
            for kaynak in secilenler
        }
        for future in as_completed(futures):
            kaynak = futures[future]
            try:
                bulunan_ilanlar, bulunan_haberler, kaynak_hatalari = future.result()
                ilanlar.extend(bulunan_ilanlar)
                haberler.extend(bulunan_haberler)
                hatalar.extend(kaynak_hatalari)
            except Exception as hata:
                hatalar.append(
                    f"{kaynak.get('ad', 'Üniversite')}: {type(hata).__name__} - {str(hata)[:120]}"
                )

    return ilanlar, haberler, siteler, sonraki, hatalar, len(secilenler)



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


def yayin_zamani(ilan):
    metin = temizle(str(ilan.get("yayin_tarihi", "")))

    if not metin:
        return None

    for bicim in SON_BASVURU_TARIH_BICIMLERI:
        try:
            return datetime.strptime(
                metin,
                bicim,
            ).replace(tzinfo=TURKIYE_SAATI)
        except ValueError:
            continue

    return None


def ilan_aktif_mi(ilan, simdi=None):
    """
    Süresi biten ilanları ve tarihi olmayan çok eski arşiv duyurularını ele.
    """
    simdi = simdi or datetime.now(TURKIYE_SAATI)
    son_zaman = son_basvuru_zamani(ilan)

    if son_zaman is not None:
        return son_zaman >= simdi

    # MSB ana sayfasındaki kartlar "Güncel Teminler" listesinden gelir.
    if ilan.get("kaynak_kodu") == "msb_tsk":
        return True

    yayin = yayin_zamani(ilan)

    # Son başvuru tarihi bulunamadıysa, altı aydan eski resmî duyuruyu
    # aktif ilan olarak göstermeyelim.
    if yayin is not None:
        return yayin >= simdi - timedelta(days=180)

    # Tarih hiç okunamazsa başlıktaki açıkça eski yılı da güvenlik ağı
    # olarak kullan.
    baslik = arama_metnine_cevir(str(ilan.get("baslik", "")))
    yillar = [
        int(yil)
        for yil in re.findall(r"\b(20\d{2})\b", baslik)
    ]

    if yillar and max(yillar) < simdi.year:
        return False

    return True


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


def _fcm_metin(deger, sinir):
    if deger is None:
        return ""
    if isinstance(deger, (list, tuple, set)):
        metin = json.dumps(list(deger), ensure_ascii=False)
    elif isinstance(deger, dict):
        metin = json.dumps(deger, ensure_ascii=False)
    elif isinstance(deger, bool):
        metin = "true" if deger else "false"
    else:
        metin = str(deger)
    return temizle(metin)[:sinir]


def tek_bildirim_gonder(
    mesajlasma,
    konu,
    baslik,
    govde,
    link,
    tur,
    ek_veri=None,
):
    # Yalnızca veri mesajı gönderilir. Böylece telefon, profilini yerel olarak
    # karşılaştırır ve bildirimin gerçek başlığını kendisi oluşturur.
    veri = {
        "tur": _fcm_metin(tur, 20),
        "link": _fcm_metin(link, 900),
        "bildirim_basligi": _fcm_metin(baslik, 100),
        "bildirim_govdesi": _fcm_metin(govde, 230),
    }

    alan_sinirlari = {
        "kurum": 180,
        "ilan_basligi": 320,
        "sehir": 120,
        "son_basvuru": 50,
        "minimum_puan": 30,
        "mezuniyetler": 650,
        "bolumler": 1000,
        "kpss_durumu": 450,
        "kpss_puan_turu": 100,
        "kpss_turu": 100,
    }
    for anahtar, deger in (ek_veri or {}).items():
        if anahtar in alan_sinirlari:
            veri[anahtar] = _fcm_metin(deger, alan_sinirlari[anahtar])

    mesaj = mesajlasma.Message(
        data=veri,
        android=mesajlasma.AndroidConfig(
            priority="high",
            ttl=timedelta(hours=12),
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


def _eski_main():
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
        print(f"ÖSYM KPSS/EKPSS/YKS haberleri: {len(osym_haberleri)}")
    except Exception as hata:
        hatalar.append(
            "ÖSYM KPSS/EKPSS/YKS haberleri: "
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

    for haber in tum_haberler:
        anahtar = link_anahtari(haber.get("link", ""))

        # Başvuru/tercih duyurusu İlanlar bölümünde de olsa
        # Haberler bölümünde ayrıca gösterilsin.
        if anahtar:
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
        "otomatik_bildirim_testi": otomatik_bildirim_testi,
        "otomatik_bildirim_testi_yapildi": bool(
            otomatik_bildirim_testi.get("basarili")
        ),
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



# ============================================================================
# KPSS KARIYER TAM KAPSAM GÜNCELLEME KATMANI
# Bu bölüm eski çalışan çekicileri korur; ÖSYM, MSB/TSK, veri güvenliği ve
# bildirim davranışını genişletir.
# ============================================================================

# MSB/TSK için ayrı ve doğrudan Güncel Teminler tarayıcısı kullanılır.
MSB_ANA_SAYFA_URL = "https://personeltemin.msb.gov.tr/AnaSayfa"
MSB_TEMINLER_URL = "https://personeltemin.msb.gov.tr/AnaSayfa/Teminler"
MSB_BASVURU_URL = "https://personeltemin.msb.gov.tr/"

# GitHub Actions üzerinde MSB ana sayfası sürüm parametresi olmadan bazen
# Güncel Teminler kartlarını döndürmüyor. Kartların göründüğü sürümlü adresleri
# önce deniyoruz; sonra eski adreslere düşüyoruz.
MSB_GUNCEL_SAYFALARI = (
    "https://personeltemin.msb.gov.tr/?v=1.0.22",
    "https://personeltemin.msb.gov.tr/?v=1.0.23",
    "https://personeltemin.msb.gov.tr/?v=1.0.24",
    "https://personeltemin.msb.gov.tr/",
    MSB_ANA_SAYFA_URL,
    MSB_TEMINLER_URL,
)

# MSB, genel bakanlık taramasından çıkarılır; aşağıda özel olarak çekilir.
RESMI_DUYURU_KAYNAKLARI = tuple(
    kaynak
    for kaynak in RESMI_DUYURU_KAYNAKLARI
    if kaynak.get("kaynak_kodu") != "msb_tsk"
)

OSYM_HABER_ARAMA_TERIMLERI = (
    "yks", "kpss", "ekpss", "yökdil", "e-yökdil", "yds", "e-yds",
    "ales", "dgs", "msü", "ags", "öabt", "tus", "dus", "ydus",
    "eus", "tr-yös", "e-tep", "özyes", "hmgs", "sts",
)

OSYM_SINAV_ESLESMELERI = (
    ("e-YÖKDİL", ("e-yokdil", "elektronik yuksekogretim kurumlari yabanci dil")),
    ("YÖKDİL", ("yokdil", "yuksekogretim kurumlari yabanci dil sinavi")),
    ("e-YDS", ("e-yds", "elektronik yabanci dil sinavi")),
    ("YDS", ("yds", "yabanci dil bilgisi seviye tespit sinavi")),
    ("MEB-AGS", ("meb-ags", "akademi giris sinavi", " ogretmenlik alan bilgisi testi")),
    ("ÖABT", ("oabt", "ogretmenlik alan bilgisi testi")),
    ("TR-YÖS", ("tr-yos", "yurt disindan ogrenci kabul sinavi")),
    ("e-TEP", ("e-tep", "elektronik ingilizce yeterlik sinavi")),
    ("ÖZYES", ("ozyes", "ozel yetenek sinavi")),
    ("EKPSS", ("ekpss", "engelli kamu personeli secme sinavi")),
    ("KPSS", ("kpss", "kamu personel secme sinavi", "dhbt")),
    ("YKS", ("yks", "yuksekogretim kurumlari sinavi", "tyt", "ayt", "ydt")),
    ("ALES", ("ales", "akademik personel ve lisansustu egitimi giris sinavi")),
    ("DGS", ("dgs", "dikey gecis sinavi")),
    ("MSÜ", ("msu", "milli savunma universitesi askeri ogrenci aday belirleme")),
    ("TUS", ("tus", "tipta uzmanlik egitimi giris sinavi")),
    ("YDUS", ("ydus", "yan dal uzmanlik egitimi giris sinavi")),
    ("DUS", ("dus", "dis hekimliginde uzmanlik egitimi giris sinavi")),
    ("EUS", ("eus", "eczacilikta uzmanlik egitimi giris sinavi")),
    ("HMGS", ("hmgs", "hukuk mesleklerine giris sinavi")),
    ("STS", ("sts", "seviye tespit sinavi")),
    ("GUY", ("guy", "gelir uzman yardimciligi")),
    ("Adalet Bakanlığı Sınavları", ("adli yargi", "idari yargi", "adli yargi-avukat")),
)


def osym_sinav_turu_bul(baslik):
    normal = f" {arama_metnine_cevir(baslik)} "
    for ad, ifadeler in OSYM_SINAV_ESLESMELERI:
        if any(ifade in normal for ifade in ifadeler):
            return ad
    return ""


def osym_haber_kategorisi_bul(baslik):
    normal = arama_metnine_cevir(baslik)
    sinav_turu = osym_sinav_turu_bul(baslik) or "ÖSYM"

    if "gec basvuru" in normal:
        olay = "Geç Başvuru"
    elif "sinava giris belge" in normal or "giris belgeleri" in normal:
        olay = "Sınava Giriş Belgesi"
    elif (("ek yerlestirme" in normal or "ek tercih" in normal)
          and "sonuc" in normal):
        olay = "Ek Yerleştirme Sonucu"
    elif "ek yerlestirme" in normal or "ek tercih" in normal:
        olay = "Ek Yerleştirme"
    elif "tercih" in normal and ("sonuc" in normal or "yerlestirme" in normal):
        olay = "Tercih Sonucu"
    elif "tercih" in normal:
        olay = "Tercih"
    elif "basvuru" in normal or "basvurularin alinmasi" in normal:
        olay = "Başvuru"
    elif "sinav takvim" in normal or "tarih degisik" in normal or "guncelleme" in normal:
        olay = "Takvim Değişikliği"
    elif "cevap kagit" in normal or "aday cevap" in normal:
        olay = "Aday Cevapları"
    elif "soru kitap" in normal or "cevap anahtar" in normal:
        olay = "Soru ve Cevap Anahtarı"
    elif "sonuc" in normal or "sayisal bilgiler" in normal:
        olay = "Sonuç"
    elif "kilavuz" in normal:
        olay = "Kılavuz"
    elif "egitim bilgi" in normal or "bilgilerini kontrol" in normal:
        olay = "Eğitim Bilgisi"
    elif "yerlestirme" in normal:
        olay = "Yerleştirme"
    else:
        olay = "Duyuru"

    return f"{sinav_turu} {olay}"


def osym_haber_duyurusu_mu(baslik):
    """Adayları ilgilendiren geniş kapsamlı resmî ÖSYM duyurularını kabul eder."""
    normal = arama_metnine_cevir(baslik)

    engellenen = (
        "ihale", "sozlesmeli personel alim", "uzman yardimciligi yazili sinav",
        "basin duyurusu", "kurumsal mali durum", "faaliyet raporu",
    )
    if any(ifade in normal for ifade in engellenen):
        return False

    if osym_sinav_turu_bul(baslik):
        return True

    aday_ifadeleri = (
        "basvurularin alinmasi", "gec basvuru", "sinava giris belgeleri",
        "sinav sonuclari", "sonuclari aciklandi", "tercihlerin alinmasi",
        "tercih sonuclari", "ek yerlestirme", "kilavuz ve basvuru",
        "soru kitapcik", "cevap anahtar", "aday cevaplari",
        "sinav takviminde guncelleme",
    )
    return any(ifade in normal for ifade in aday_ifadeleri)


def osym_haber_kaydi_olustur(baslik, link, yayin_tarihi):
    sinav_turu = osym_sinav_turu_bul(baslik) or "Diğer ÖSYM"
    return {
        "id": ilan_id_uret(link),
        "baslik": baslik[:400],
        "kurum": "ÖSYM",
        "kategori": osym_haber_kategorisi_bul(baslik),
        "yayin_tarihi": yayin_tarihi,
        "kaynak": f"ÖSYM {sinav_turu} Duyuruları",
        "ozet": (
            f"ÖSYM tarafından yayımlanan resmî {sinav_turu} duyurusu. "
            "Başvuru, sınava giriş belgesi, sonuç, tercih, kılavuz veya "
            "takvim bilgisi için resmî sayfayı açın."
        ),
        "link": link,
    }


def haber_guncel_mi(haber, gun_sayisi=180):
    tarih = haber_tarih_siralama_degeri(haber)
    simdi = datetime.now()
    if tarih != datetime.min:
        return tarih >= simdi - timedelta(days=gun_sayisi)

    baslik = arama_metnine_cevir(haber.get("baslik", ""))
    yillar = [int(y) for y in re.findall(r"\b(20\d{2})\b", baslik)]
    return not yillar or max(yillar) >= simdi.year


def osym_duyurular_sayfasindan_haberleri_al():
    """ÖSYM Duyurular sayfasını tek istekle tarar; bütün aday duyurularını alır."""
    html = sayfayi_indir(OSYM_DUYURULAR_URL)
    soup = BeautifulSoup(html, "html.parser")
    haberler = []
    gorulen_linkler = set()

    for link_etiketi in soup.find_all("a", href=True):
        ham_baslik = temizle(link_etiketi.get_text(" ", strip=True))
        href = temizle(link_etiketi.get("href", ""))
        if not href or len(ham_baslik) < 10:
            continue

        baslik = osym_basligini_temizle(ham_baslik)
        if len(baslik) < 10 or not osym_haber_duyurusu_mu(baslik):
            continue
        if href.startswith("#") or href.casefold().startswith("javascript:"):
            continue

        link = urljoin(OSYM_DUYURULAR_URL, href)
        normal_link = link.casefold()
        if "osym.gov.tr" not in normal_link:
            continue
        if any(parca in normal_link for parca in (
            "/arama", "/duyurular/index", "/sinavtakvimi",
            "/kpss-sinav-takvimi",
        )):
            continue

        anahtar = link_anahtari(link)
        if not anahtar or anahtar in gorulen_linkler:
            continue

        kapsayici = link_etiketi.find_parent(["li", "article", "div", "tr"])
        kapsayici_metni = temizle(
            kapsayici.get_text(" ", strip=True) if kapsayici is not None else ham_baslik
        )
        yayin_tarihi = (
            osym_yayin_tarihi_bul(ham_baslik)
            or osym_yayin_tarihi_bul(kapsayici_metni)
        )
        haber = osym_haber_kaydi_olustur(baslik, link, yayin_tarihi)
        if not haber_guncel_mi(haber, 180):
            continue

        gorulen_linkler.add(anahtar)
        haberler.append(haber)
        if len(haberler) >= 400:
            break

    haberler.sort(key=haber_tarih_siralama_degeri, reverse=True)
    return haberler


def osym_kpss_haberlerini_al():
    """Adı uyumluluk için korunmuştur; bütün güncel ÖSYM aday haberlerini alır."""
    haberler = osym_duyurular_sayfasindan_haberleri_al()
    if haberler:
        return haberler
    yedek = osym_arama_sonuclarindan_haberleri_al()
    return [haber for haber in yedek if haber_guncel_mi(haber, 180)]


def _msb_baslik_adaylari(etiket):
    adaylar = [
        temizle(etiket.get_text(" ", strip=True)),
        temizle(etiket.get("title", "")),
        temizle(etiket.get("aria-label", "")),
    ]
    kapsayici = etiket.find_parent(["article", "li", "div", "section", "tr"])
    if kapsayici is not None:
        for ad in ("h1", "h2", "h3", "h4", "h5", "strong", "p"):
            for baslik_etiketi in kapsayici.find_all(ad, limit=8):
                adaylar.append(temizle(baslik_etiketi.get_text(" ", strip=True)))
        adaylar.append(temizle(kapsayici.get_text(" ", strip=True)))
    return [aday for aday in adaylar if 10 <= len(aday) <= 1000]


def _msb_aktif_temin_basligi_mi(baslik):
    normal = arama_metnine_cevir(baslik)
    engellenen = (
        "sonuc", "itiraz", "kesin kayit", "egitim duyurusu", "cagri ilani",
        "cagri durumu", "sinav asamasi", "secim asamasi", "on kayit",
        "ikinci siniflandirma", "konaklama", "yerlestirilme", "aday sorgulama",
    )
    if any(ifade in normal for ifade in engellenen):
        return False
    kabul = (
        "uzman erbas temini", "uzman erbas temin faaliyeti",
        "teknik sinif uzman erbas", "sozlesmeli er temini",
        "sozlesmeli er temin faaliyeti", "muvazzaf subay temini",
        "sozlesmeli subay temini", "muvazzaf astsubay temini",
        "sozlesmeli astsubay temini", "askeri ogrenci temini",
        "devlet memuru temini", "surekli isci temini",
        "sozlesmeli bilisim personeli temini", "personel temini",
    )
    return any(ifade in normal for ifade in kabul)


def _msb_detay_linki_mi(link):
    normal = link.casefold()
    return "personeltemin.msb.gov.tr" in normal and "duyurudetay" in normal


def _msb_sayfa_adaylarini_topla(html, sayfa_url):
    """MSB sayfasındaki DuyuruDetay bağlantılarını farklı HTML yapılarına göre bulur."""
    soup = BeautifulSoup(html, "html.parser")
    adaylar = []

    def aday_ekle(etiket, href):
        href = temizle(href).replace("&amp;", "&")
        if not href:
            return
        link = urljoin(sayfa_url, href)
        if _msb_detay_linki_mi(link):
            adaylar.append((etiket, link))

    for etiket in soup.find_all("a", href=True):
        aday_ekle(etiket, etiket.get("href", ""))

    # Site bazı sürümlerde bağlantıyı href yerine onclick, data-url veya
    # benzeri bir HTML özelliğinde tutabiliyor.
    for etiket in soup.find_all(True):
        ozellikler = " ".join(
            temizle(" ".join(deger) if isinstance(deger, list) else str(deger))
            for deger in etiket.attrs.values()
        )
        if "duyurudetay" not in ozellikler.casefold():
            continue
        for eslesme in re.finditer(
            r"((?:https?://[^\s'\"<>]+)?/?(?:AnaSayfa|Anasayfa)/DuyuruDetay/?\?[^\s'\"<>]+)",
            ozellikler,
            flags=re.IGNORECASE,
        ):
            aday_ekle(etiket, eslesme.group(1))

    # Son güvenlik ağı: DuyuruDetay adresi ham HTML içinde bulunuyorsa al.
    for eslesme in re.finditer(
        r"((?:https?://[^\s'\"<>]+)?/?(?:AnaSayfa|Anasayfa)/DuyuruDetay/?\?[^\s'\"<>]+)",
        html,
        flags=re.IGNORECASE,
    ):
        aday_ekle(None, eslesme.group(1))

    # Başlık bir bağlantının içinde değilse en yakın kapsayıcıdaki bağlantıyı da dene.
    for baslik_etiketi in soup.find_all(["h1", "h2", "h3", "h4", "h5", "strong"]):
        baslik_metni = temizle(baslik_etiketi.get_text(" ", strip=True))
        if not _msb_aktif_temin_basligi_mi(baslik_metni):
            continue
        baglanti = baslik_etiketi.find_parent("a", href=True)
        if baglanti is None:
            kapsayici = baslik_etiketi.find_parent(["article", "li", "div", "section", "tr"])
            baglanti = kapsayici.find("a", href=True) if kapsayici is not None else None
        if baglanti is not None:
            aday_ekle(baglanti, baglanti.get("href", ""))

    benzersiz = []
    gorulen = set()
    for etiket, link in adaylar:
        anahtar = link_anahtari(link)
        if not anahtar or anahtar in gorulen:
            continue
        gorulen.add(anahtar)
        benzersiz.append((etiket, link))
    return benzersiz


def _msb_guncel_temin_basliklarini_topla(html):
    """
    MSB sayfasında ayrıntı bağlantıları JavaScript içinde gizlense bile
    GÜNCEL TEMİNLER bölümündeki kart başlıklarını ve tarihlerini toplar.
    """
    soup = BeautifulSoup(html, "html.parser")
    satirlar = [
        temizle(satir)
        for satir in soup.get_text("\n", strip=True).splitlines()
        if temizle(satir)
    ]

    baslangic = None
    bitis = None
    for sira, satir in enumerate(satirlar):
        normal = arama_metnine_cevir(satir)
        if baslangic is None and "guncel teminler" in normal:
            baslangic = sira + 1
            continue
        if baslangic is not None and "guncel duyurular" in normal:
            bitis = sira
            break

    if baslangic is None:
        return []

    bolum = satirlar[baslangic:bitis]
    gruplar = []
    grup = []

    for satir in bolum:
        tarih_eslesmesi = re.search(r"\b\d{1,2}\.\d{1,2}\.20\d{2}\b", satir)
        if tarih_eslesmesi:
            if grup:
                gruplar.append((grup, tarih_eslesmesi.group(0)))
                grup = []
            continue
        grup.append(satir)

    if grup:
        gruplar.append((grup, ""))

    sonuc = []
    gorulen = set()

    for grup_satirlari, yayin_tarihi in gruplar:
        adaylar = []
        for satir in grup_satirlari:
            if len(satir) < 18:
                continue
            if _msb_aktif_temin_basligi_mi(satir):
                adaylar.append(satir)

        if not adaylar:
            continue

        # Kartta ana başlık ve kısa alt başlık birlikte bulunabiliyor.
        # En açıklayıcı olan uzun başlığı seç.
        baslik = max(adaylar, key=len)
        anahtar = arama_metnine_cevir(baslik)
        if not anahtar or anahtar in gorulen:
            continue

        gorulen.add(anahtar)
        sonuc.append({
            "baslik": baslik,
            "yayin_tarihi": yayin_tarihi,
        })

    # Tarih ayrımı bulunamazsa, bölümdeki uygun başlıkları doğrudan tara.
    if not sonuc:
        for satir in bolum:
            if len(satir) < 18 or not _msb_aktif_temin_basligi_mi(satir):
                continue
            anahtar = arama_metnine_cevir(satir)
            if not anahtar or anahtar in gorulen:
                continue
            gorulen.add(anahtar)
            sonuc.append({"baslik": satir, "yayin_tarihi": ""})

    return sonuc


def _msb_ham_html_basliklarini_topla(html):
    """Bölüm yapısı bozulsa bile görünür MSB temin başlıklarını toplar."""
    soup = BeautifulSoup(html, "html.parser")
    sonuc = []
    gorulen = set()

    for parca in soup.stripped_strings:
        metin = temizle(parca)
        if not (18 <= len(metin) <= 500):
            continue
        if not _msb_aktif_temin_basligi_mi(metin):
            continue
        anahtar = arama_metnine_cevir(metin)
        if not anahtar or anahtar in gorulen:
            continue
        gorulen.add(anahtar)
        sonuc.append({
            "baslik": metin,
            "yayin_tarihi": tarih_bul(metin),
        })

    return sonuc


def _msb_basliktan_yedek_ilan_uret(kayit):
    """Ayrıntı bağlantısı okunamadığında ana sayfa kartından güvenli ilan üretir."""
    baslik = baslik_temizle(kayit.get("baslik", ""))
    yayin_tarihi = temizle(kayit.get("yayin_tarihi", ""))
    benzersiz_kod = ilan_id_uret("msb_tsk|" + arama_metnine_cevir(baslik))
    link = MSB_ANA_SAYFA_URL + "?" + urlencode({"kpssKariyer": benzersiz_kod})
    kpss_gerekli, minimum_puan, kpss_durumu = kpss_bilgisi_bul(baslik, "ok")

    return {
        "id": benzersiz_kod,
        "baslik": baslik[:400],
        "kurum": "Millî Savunma Bakanlığı / Türk Silahlı Kuvvetleri",
        "sehir": "Türkiye Geneli",
        "tur": "Askerî / MSB Personel Alımı",
        "kaynak": "MSB / TSK Güncel Teminler",
        "kaynak_kodu": "msb_tsk",
        "son_basvuru": "",
        "yayin_tarihi": yayin_tarihi,
        "link": link,
        "belge_linki": "",
        "kaynak_sayfa_linki": MSB_GUNCEL_SAYFALARI[0],
        "basvuru_linki": MSB_BASVURU_URL,
        "basvuru_online": True,
        "basvuru_aciklamasi": (
            "Başvuru şartları ve tarihler MSB Personel Temin Sistemindeki "
            "Güncel Teminler kartından kontrol edilmelidir."
        ),
        "kpss_gerekli": kpss_gerekli,
        "minimum_puan": minimum_puan,
        "kpss_durumu": kpss_durumu,
        "mezuniyetler": mezuniyetleri_bul(baslik),
        "bolumler": bolumleri_bul(baslik),
        "pdf_isleme_durumu": "html_baslik",
        "analiz_surumu": ANALIZ_SURUMU,
    }


def msb_guncel_teminleri_al():
    """
    MSB ana sayfası ve Tüm Teminler sayfasındaki aktif teminleri toplar.

    Önemli: Ayrıntı bağlantısı çözümlemesi hata verse bile kart başlıkları önce
    doğrudan ilana çevrilir. Böylece tek bir bozuk bağlantı bütün MSB kaynağını
    sıfırlayamaz.
    """
    ilanlar = []
    baslik_indeksleri = {}
    gorulen_linkler = set()
    sayfa_hatalari = []
    sayfa_verileri = []

    for sayfa_url in MSB_GUNCEL_SAYFALARI:
        try:
            html = sayfayi_indir(sayfa_url)
            sayfa_verileri.append((sayfa_url, html))
            gorunen_metin = BeautifulSoup(html, "html.parser").get_text(" ", strip=True)
            normal_metin = arama_metnine_cevir(gorunen_metin)
            print(
                f"MSB sayfa kontrolü: {sayfa_url} | karakter={len(html)} "
                f"| güncel_teminler={'evet' if 'guncel teminler' in normal_metin else 'hayır'}"
            )
        except Exception as hata:
            sayfa_hatalari.append(
                f"{sayfa_url}: {type(hata).__name__} - {str(hata)[:160]}"
            )

    if not sayfa_verileri:
        raise RuntimeError("MSB sayfaları alınamadı: " + " | ".join(sayfa_hatalari))

    # 1) ÖNCE görünür kart başlıklarını ekle. Bu aşama ayrıntı bağlantılarından
    # bağımsızdır ve MSB sayfası açıldığı sürece ilanların sıfıra düşmesini önler.
    yedek_sayisi = 0
    for sayfa_url, html in sayfa_verileri:
        try:
            kayitlar = _msb_guncel_temin_basliklarini_topla(html)
            if not kayitlar:
                kayitlar = _msb_ham_html_basliklarini_topla(html)

            print(f"MSB başlık taraması: {sayfa_url} | {len(kayitlar)} aday")

            for kayit in kayitlar:
                yedek_ilan = _msb_basliktan_yedek_ilan_uret(kayit)
                yedek_ilan["kaynak_sayfa_linki"] = sayfa_url

                baslik_anahtari = arama_metnine_cevir(yedek_ilan.get("baslik", ""))
                if not baslik_anahtari or baslik_anahtari in baslik_indeksleri:
                    continue

                baslik_indeksleri[baslik_anahtari] = len(ilanlar)
                ilanlar.append(yedek_ilan)
                yedek_sayisi += 1
        except Exception as hata:
            sayfa_hatalari.append(
                f"başlık taraması {sayfa_url}: {type(hata).__name__} - {str(hata)[:160]}"
            )
            print(
                f"MSB başlık tarama hatası: {sayfa_url} | "
                f"{type(hata).__name__}: {str(hata)[:160]}"
            )

    # 2) Sonra ayrıntı bağlantılarını zenginleştirme amacıyla dene. Buradaki
    # hiçbir hata yukarıda oluşturulan güvenli kart ilanlarını silemez.
    for sayfa_url, html in sayfa_verileri:
        try:
            adaylar = _msb_sayfa_adaylarini_topla(html, sayfa_url)
            print(f"MSB ayrıntı bağlantısı: {sayfa_url} | {len(adaylar)} aday")
        except Exception as hata:
            sayfa_hatalari.append(
                f"bağlantı taraması {sayfa_url}: {type(hata).__name__} - {str(hata)[:160]}"
            )
            print(
                f"MSB bağlantı tarama hatası: {sayfa_url} | "
                f"{type(hata).__name__}: {str(hata)[:160]}"
            )
            continue

        for etiket, link in adaylar:
            try:
                link_anahtari_degeri = link_anahtari(link)
                if not link_anahtari_degeri or link_anahtari_degeri in gorulen_linkler:
                    continue
                gorulen_linkler.add(link_anahtari_degeri)

                liste_basligi = ""
                kapsayici_metni = ""
                liste_yayin_tarihi = ""

                if etiket is not None:
                    basliklar = [
                        aday
                        for aday in _msb_baslik_adaylari(etiket)
                        if _msb_aktif_temin_basligi_mi(aday)
                    ]
                    if basliklar:
                        liste_basligi = max(basliklar, key=len)

                    kapsayici = etiket.find_parent(
                        ["article", "li", "div", "section", "tr"]
                    )
                    kapsayici_metni = temizle(
                        kapsayici.get_text(" ", strip=True)
                        if kapsayici is not None
                        else liste_basligi
                    )
                    liste_yayin_tarihi = yayin_tarihi_bul(kapsayici_metni)

                try:
                    (
                        sayfa_basligi,
                        detay_metni,
                        belge_linki,
                        basvuru_linki,
                        basvuru_online,
                        basvuru_aciklamasi,
                    ) = duyuru_icerigini_al(link)
                except Exception as detay_hatasi:
                    print(
                        f"MSB ayrıntı okunamadı, kart korunuyor: "
                        f"{type(detay_hatasi).__name__}: {str(detay_hatasi)[:120]}"
                    )
                    continue

                gercek_baslik = baslik_temizle(sayfa_basligi or liste_basligi)
                if not gercek_baslik or not _msb_aktif_temin_basligi_mi(gercek_baslik):
                    continue

                son_basvuru = son_basvuru_tarihi_bul(detay_metni)
                yayin_tarihi = yayin_tarihi_bul(detay_metni) or liste_yayin_tarihi

                if not basvuru_linki:
                    basvuru_linki = MSB_BASVURU_URL
                    basvuru_online = True
                    basvuru_aciklamasi = (
                        "MSB personel temin başvuruları resmî Personel Temin Sistemi "
                        "üzerinden çevrimiçi yapılır. Ayrıntıları ilan sayfasından kontrol edin."
                    )

                kpss_gerekli, minimum_puan, kpss_durumu = kpss_bilgisi_bul(
                    detay_metni, "ok"
                )

                zengin_ilan = {
                    "id": ilan_id_uret(link),
                    "baslik": gercek_baslik[:400],
                    "kurum": "Millî Savunma Bakanlığı / Türk Silahlı Kuvvetleri",
                    "sehir": "Türkiye Geneli",
                    "tur": "Askerî / MSB Personel Alımı",
                    "kaynak": "MSB / TSK Güncel Teminler",
                    "kaynak_kodu": "msb_tsk",
                    "son_basvuru": son_basvuru,
                    "yayin_tarihi": yayin_tarihi,
                    "link": link,
                    "belge_linki": belge_linki or link,
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
                }

                baslik_anahtari = arama_metnine_cevir(gercek_baslik)
                eski_indeks = baslik_indeksleri.get(baslik_anahtari)
                if eski_indeks is None:
                    baslik_indeksleri[baslik_anahtari] = len(ilanlar)
                    ilanlar.append(zengin_ilan)
                else:
                    ilanlar[eski_indeks] = zengin_ilan

            except Exception as hata:
                sayfa_hatalari.append(
                    f"ayrıntı {link}: {type(hata).__name__} - {str(hata)[:160]}"
                )
                print(
                    f"MSB aday işleme hatası: {type(hata).__name__}: "
                    f"{str(hata)[:160]}"
                )
                continue

    print(f"MSB kart başlığı yedeğiyle eklenen: {yedek_sayisi}")
    print(f"MSB / TSK güvenli toplam: {len(ilanlar)}")

    if sayfa_hatalari:
        print("MSB sayfa uyarıları: " + " | ".join(sayfa_hatalari[:12]))

    return ilanlar

def msb_guncel_haberleri_al():
    """MSB'nin güncel sonuç, çağrı, kayıt ve eğitim duyurularını Haberler'e ekler."""
    html = sayfayi_indir(MSB_ANA_SAYFA_URL)
    soup = BeautifulSoup(html, "html.parser")
    haberler = []
    gorulen = set()
    haber_ifadeleri = (
        "sonuc", "kesin kayit", "egitim duyurusu", "cagri ilani",
        "cagri durumu", "sinav asamasi", "secim asamasi", "itiraz",
        "siniflandirma", "on kayit", "yerlestirilme",
    )

    for etiket in soup.find_all("a", href=True):
        href = temizle(etiket.get("href", ""))
        if not href:
            continue
        link = urljoin(MSB_ANA_SAYFA_URL, href)
        if not _msb_detay_linki_mi(link):
            continue
        anahtar = link_anahtari(link)
        if not anahtar or anahtar in gorulen:
            continue
        adaylar = _msb_baslik_adaylari(etiket)
        uygun = [a for a in adaylar if any(x in arama_metnine_cevir(a) for x in haber_ifadeleri)]
        if not uygun:
            continue
        baslik = min(uygun, key=len)
        kapsayici = etiket.find_parent(["article", "li", "div", "section", "tr"])
        kapsayici_metni = temizle(kapsayici.get_text(" ", strip=True) if kapsayici else baslik)
        yayin_tarihi = yayin_tarihi_bul(kapsayici_metni) or tarih_bul(kapsayici_metni)
        haber = {
            "id": ilan_id_uret(link),
            "baslik": baslik[:400],
            "kurum": "Millî Savunma Bakanlığı / Türk Silahlı Kuvvetleri",
            "kategori": "MSB / TSK Duyurusu",
            "yayin_tarihi": yayin_tarihi,
            "kaynak": "MSB Personel Temin Haberleri",
            "ozet": "MSB Personel Temin Sistemi tarafından yayımlanan resmî aday duyurusu.",
            "link": link,
        }
        if haber_guncel_mi(haber, 180):
            haberler.append(haber)
            gorulen.add(anahtar)
    return haberler


def onceki_veriyi_yukle():
    dosya = Path("ilanlar.json")
    if not dosya.exists():
        return {"ilanlar": [], "haberler": []}
    try:
        veri = json.loads(dosya.read_text(encoding="utf-8"))
        return veri if isinstance(veri, dict) else {"ilanlar": [], "haberler": []}
    except Exception:
        return {"ilanlar": [], "haberler": []}


def firebase_mesajlasmayi_hazirla():
    if firebase_admin is None or credentials is None or messaging is None:
        return None, "firebase-admin paketi kurulu değil"
    servis_hesabi_json = (
        os.environ.get("FIREBASE_SERVICE_ACCOUNT_JSON", "").strip()
        or os.environ.get("FIREBASE_SERVICE_ACCOUNT", "").strip()
    )
    if not servis_hesabi_json:
        return None, "Firebase servis hesabı GitHub Secret olarak tanımlı değil"
    try:
        servis_hesabi = json.loads(servis_hesabi_json)
        try:
            firebase_admin.get_app()
        except ValueError:
            firebase_admin.initialize_app(credentials.Certificate(servis_hesabi))
        return messaging, None
    except Exception as hata:
        return None, f"{type(hata).__name__}: {str(hata)[:180]}"


def _haber_bildirim_icin_guncel_mi(haber, gun=3):
    tarih = haber_tarih_siralama_degeri(haber)
    return tarih != datetime.min and tarih >= datetime.now() - timedelta(days=gun)


def _bildirim_metnini_kisalt(metin, sinir=96):
    metin = temizle(metin)
    if len(metin) <= sinir:
        return metin
    return metin[: max(1, sinir - 1)].rstrip(" ,.;:-") + "…"


def _turkce_buyuk(metin):
    ceviri = str.maketrans({
        "i": "İ", "ı": "I", "ğ": "Ğ", "ü": "Ü",
        "ş": "Ş", "ö": "Ö", "ç": "Ç",
    })
    return temizle(metin).translate(ceviri).upper()


def _ilan_bildirim_basligi(ilan):
    """Genel 'Yeni ilan' yerine kurumu ve alım türünü anlatan başlık üretir."""
    baslik = temizle(ilan.get("baslik") or "Kamu personeli alımı")
    kurum = temizle(ilan.get("kurum") or ilan.get("kaynak") or "Kamu kurumu")
    normal_baslik = arama_metnine_cevir(baslik)
    normal_kurum = arama_metnine_cevir(kurum)

    if ilan.get("kaynak_kodu") == "msb_tsk" or "milli savunma" in normal_kurum:
        msb_turleri = (
            ("sozlesmeli er", "MSB / TSK SÖZLEŞMELİ ER ALIMI"),
            ("uzman erbas", "MSB / TSK UZMAN ERBAŞ ALIMI"),
            ("muvazzaf subay", "MSB / TSK MUVAZZAF SUBAY ALIMI"),
            ("sozlesmeli subay", "MSB / TSK SÖZLEŞMELİ SUBAY ALIMI"),
            ("muvazzaf astsubay", "MSB / TSK MUVAZZAF ASTSUBAY ALIMI"),
            ("sozlesmeli astsubay", "MSB / TSK SÖZLEŞMELİ ASTSUBAY ALIMI"),
            ("askeri ogrenci", "MSÜ ASKERÎ ÖĞRENCİ ALIMI"),
            ("surekli isci", "MSB SÜREKLİ İŞÇİ ALIMI"),
            ("devlet memuru", "MSB DEVLET MEMURU ALIMI"),
            ("bilisim personeli", "MSB BİLİŞİM PERSONELİ ALIMI"),
        )
        for ifade, bildirim_basligi in msb_turleri:
            if ifade in normal_baslik:
                return bildirim_basligi
        return "MSB / TSK PERSONEL ALIMI"

    if "belediye" in normal_kurum:
        return _bildirim_metnini_kisalt(f"{_turkce_buyuk(kurum)} PERSONEL ALIMI")

    # İlanın kendi başlığı yeterince açıklayıcıysa onu doğrudan kullan.
    if any(ifade in normal_baslik for ifade in (
        "personel alimi", "isci alimi", "memur alimi", "personel temini",
        "isci temini", "memur temini", "sozlesmeli personel",
    )):
        return _bildirim_metnini_kisalt(_turkce_buyuk(baslik))

    return _bildirim_metnini_kisalt(f"{_turkce_buyuk(kurum)} ALIM İLANI")


def _ilan_bildirim_govdesi(ilan, bildirim_basligi):
    baslik = temizle(ilan.get("baslik") or "")
    sehir = temizle(ilan.get("sehir") or "")
    son_basvuru = temizle(ilan.get("son_basvuru") or "")
    parcalar = []

    if baslik and arama_metnine_cevir(baslik) != arama_metnine_cevir(bildirim_basligi):
        parcalar.append(_bildirim_metnini_kisalt(baslik, 150))
    if sehir and sehir.casefold() not in ("belirtilmemiş", "belirtilmemis"):
        parcalar.append(sehir)
    if son_basvuru and son_basvuru.casefold() not in ("belirtilmemiş", "belirtilmemis"):
        parcalar.append(f"Son başvuru: {son_basvuru}")

    if not parcalar:
        parcalar.append(temizle(ilan.get("kurum") or ilan.get("kaynak") or "Resmî kurum"))
    return _bildirim_metnini_kisalt(" • ".join(parcalar), 230)


def _haber_bildirim_basligi(haber):
    """ÖSYM ve resmî haberler için olayın ne olduğunu açıkça söyleyen başlık üretir."""
    baslik = temizle(haber.get("baslik") or "Yeni resmî duyuru")
    normal = arama_metnine_cevir(baslik)
    sinav = osym_sinav_turu_bul(baslik)

    if sinav:
        sinav = _turkce_buyuk(sinav)
        if "ek yerlestirme" in normal and "sonuc" in normal:
            return f"{sinav} EK YERLEŞTİRME SONUÇLARI AÇIKLANDI"
        if "yerlestirme" in normal and "sonuc" in normal:
            return f"{sinav} YERLEŞTİRME SONUÇLARI AÇIKLANDI"
        if "tercih" in normal and "sonuc" in normal:
            return f"{sinav} TERCİH SONUÇLARI AÇIKLANDI"
        if "sonuc" in normal:
            return f"{sinav} SONUÇLARI AÇIKLANDI"
        if "sinava giris belgesi" in normal or "giris belg" in normal:
            return f"{sinav} SINAVA GİRİŞ BELGESİ YAYIMLANDI"
        if "cevap kagidi" in normal or "aday cevap" in normal:
            return f"{sinav} CEVAP KÂĞITLARI ERİŞİME AÇILDI"
        if "tercih" in normal and any(x in normal for x in ("basvuru", "alinmaya", "basladi")):
            return f"{sinav} TERCİHLERİ BAŞLADI"
        if "basvuru" in normal and any(x in normal for x in ("alinmaya", "alinmasi", "alinmasina", "basladi", "kilavuz")):
            return f"{sinav} BAŞVURULARI BAŞLADI"
        if "kilavuz" in normal:
            return f"{sinav} KILAVUZU YAYIMLANDI"

    return _bildirim_metnini_kisalt(_turkce_buyuk(baslik))


def yeni_kayit_bildirimlerini_gonder(
    ilanlar, haberler, onceki_ilan_anahtarlari,
    onceki_haber_anahtarlari, onceki_dosya_vardi,
):
    sonuc = {"durum": "atlanmış", "yeni_ilan": 0, "yeni_haber": 0,
             "gonderilen": 0, "mesaj": ""}
    if not onceki_dosya_vardi:
        sonuc["mesaj"] = "İlk çalışma olduğu için toplu bildirim gönderilmedi."
        return sonuc

    yeni_ilanlar = [i for i in ilanlar if link_anahtari(i.get("link", ""))
                    and link_anahtari(i.get("link", "")) not in onceki_ilan_anahtarlari]
    yeni_haberler = [h for h in haberler if link_anahtari(h.get("link", ""))
                     and link_anahtari(h.get("link", "")) not in onceki_haber_anahtarlari
                     and _haber_bildirim_icin_guncel_mi(h)]
    yeni_ilanlar.sort(key=tarih_siralama_degeri)
    yeni_haberler.sort(key=haber_tarih_siralama_degeri, reverse=True)
    sonuc["yeni_ilan"] = len(yeni_ilanlar)
    sonuc["yeni_haber"] = len(yeni_haberler)
    if not yeni_ilanlar and not yeni_haberler:
        sonuc["durum"] = "yeni_kayit_yok"
        sonuc["mesaj"] = "Yeni ve güncel ilan veya haber bulunmadı."
        return sonuc

    mesajlasma, hata = firebase_mesajlasmayi_hazirla()
    if mesajlasma is None:
        sonuc["mesaj"] = hata or "Firebase hazırlanamadı."
        return sonuc

    try:
        for ilan in yeni_ilanlar[:8]:
            bildirim_basligi = _ilan_bildirim_basligi(ilan)
            govde = _ilan_bildirim_govdesi(ilan, bildirim_basligi)
            tek_bildirim_gonder(
                mesajlasma,
                "yeni_ilanlar",
                bildirim_basligi,
                govde,
                ilan.get("kaynak_sayfa_linki") or ilan.get("link", ""),
                "ilan",
                ek_veri={
                    "kurum": ilan.get("kurum") or ilan.get("kaynak") or "",
                    "ilan_basligi": ilan.get("baslik") or "",
                    "sehir": ilan.get("sehir") or "",
                    "son_basvuru": ilan.get("son_basvuru") or "",
                    "minimum_puan": ilan.get("minimum_puan") or 0,
                    "mezuniyetler": ilan.get("mezuniyetler") or [],
                    "bolumler": ilan.get("bolumler") or [],
                    "kpss_durumu": ilan.get("kpss_durumu") or "",
                    "kpss_puan_turu": ilan.get("kpss_puan_turu") or "",
                    "kpss_turu": ilan.get("kpss_turu") or "",
                },
            )
            sonuc["gonderilen"] += 1

        for haber in yeni_haberler[:8]:
            bildirim_basligi = _haber_bildirim_basligi(haber)
            kurum = temizle(haber.get("kurum") or haber.get("kaynak") or "Resmî kurum")
            kategori = temizle(haber.get("kategori") or "Haber")
            tek_bildirim_gonder(
                mesajlasma,
                "yeni_haberler",
                bildirim_basligi,
                _bildirim_metnini_kisalt(f"{kurum} • {kategori}", 230),
                haber.get("link", ""),
                "haber",
            )
            sonuc["gonderilen"] += 1

        sonuc["durum"] = "gonderildi"
        sonuc["mesaj"] = f"{sonuc['gonderilen']} telefon bildirimi gönderildi."
    except Exception as hata:
        sonuc["durum"] = "hata"
        sonuc["mesaj"] = f"{type(hata).__name__}: {str(hata)[:180]}"
    return sonuc

def ilk_otomatik_bildirim_testini_gonder(onceki_veri, normal_bildirim_sonucu):
    """
    Otomatik bildirim hattını bir kez gerçek GitHub Actions çalışmasından test eder.

    Test başarılı olduğunda ilanlar.json içine kalıcı bir işaret yazılır ve sonraki
    çalışmalarda tekrar gönderilmez. Normal yeni ilan/haber bildirimi zaten bu
    çalışmada gönderildiyse ayrıca test bildirimi atılmaz; sistem doğrulanmış sayılır.
    """
    sonuc = {
        "durum": "atlanmış",
        "basarili": False,
        "mesaj": "",
    }

    if isinstance(onceki_veri, dict) and onceki_veri.get(
        "otomatik_bildirim_testi_yapildi"
    ) is True:
        sonuc["durum"] = "daha_once_yapildi"
        sonuc["basarili"] = True
        sonuc["mesaj"] = "Daha önce başarıyla tamamlandı."
        return sonuc

    if isinstance(normal_bildirim_sonucu, dict) and int(
        normal_bildirim_sonucu.get("gonderilen", 0) or 0
    ) > 0:
        sonuc["durum"] = "normal_bildirimle_dogrulandi"
        sonuc["basarili"] = True
        sonuc["mesaj"] = "Yeni ilan/haber bildirimi gönderildi; otomatik sistem doğrulandı."
        return sonuc

    mesajlasma, hata = firebase_mesajlasmayi_hazirla()
    if mesajlasma is None:
        sonuc["durum"] = "hata"
        sonuc["mesaj"] = hata or "Firebase hazırlanamadı."
        return sonuc

    try:
        mesaj_id = tek_bildirim_gonder(
            mesajlasma,
            "yeni_ilanlar",
            "KPSS KARİYER BİLDİRİM SİSTEMİ AKTİF",
            "Yeni ilan ve haberler artık otomatik olarak bildirilecek.",
            "https://kamu-ilan-api-1.onrender.com/ilanlar",
            "ilan",
        )
        sonuc["durum"] = "gonderildi"
        sonuc["basarili"] = True
        sonuc["mesaj"] = f"Telefon test bildirimi gönderildi: {mesaj_id}"
    except Exception as hata:
        sonuc["durum"] = "hata"
        sonuc["mesaj"] = f"{type(hata).__name__}: {str(hata)[:180]}"

    return sonuc


def main():
    print(f"Dosya sürümü: {DOSYA_SURUMU}")
    onceki_veri = onceki_veriyi_yukle()
    (onceki_ilan_anahtarlari, onceki_haber_anahtarlari,
     onceki_dosya_vardi) = onceki_bildirim_kayitlarini_yukle()

    tum_ilanlar, tum_haberler, hatalar = [], [], []
    kaynak_sayilari = {}
    gorevler = [(kaynak, kod, ad) for kaynak in KAYNAKLAR for kod, ad in SEHIRLER]

    with ThreadPoolExecutor(max_workers=8) as havuz:
        futures = {havuz.submit(gorevi_calistir, kaynak, kod, ad): (kaynak["kaynak"], ad)
                   for kaynak, kod, ad in gorevler}
        for tamamlanan, future in enumerate(as_completed(futures), 1):
            ilanlar, hata = future.result()
            tum_ilanlar.extend(ilanlar)
            if hata:
                hatalar.append(hata)
            print(f"Sayfalar: {tamamlanan}/{len(gorevler)} - bulunan: {len(tum_ilanlar)}")

    try:
        osym_ilanlari = osym_kpss_duyurularini_al()
        tum_ilanlar.extend(osym_ilanlari)
        kaynak_sayilari["ÖSYM merkezi atama ilanları"] = len(osym_ilanlari)
        print(f"ÖSYM merkezi atama ilanları: {len(osym_ilanlari)}")
    except Exception as hata:
        hatalar.append(f"ÖSYM merkezi atama ilanları: {type(hata).__name__} - {str(hata)[:150]}")

    try:
        osym_haberleri = osym_kpss_haberlerini_al()
        tum_haberler.extend(osym_haberleri)
        kaynak_sayilari["ÖSYM tüm sınav haberleri"] = len(osym_haberleri)
        print(f"ÖSYM tüm sınav haberleri: {len(osym_haberleri)}")
    except Exception as hata:
        hatalar.append(f"ÖSYM tüm sınav haberleri: {type(hata).__name__} - {str(hata)[:150]}")

    msb_basarili = False
    try:
        msb_ilanlari = msb_guncel_teminleri_al()
        tum_ilanlar.extend(msb_ilanlari)
        kaynak_sayilari["MSB / TSK Güncel Teminler"] = len(msb_ilanlari)
        msb_basarili = bool(msb_ilanlari)
        print(f"MSB / TSK Güncel Teminler: {len(msb_ilanlari)}")
        if not msb_ilanlari:
            hatalar.append("MSB / TSK Güncel Teminler: sayfa açıldı ancak aktif kart okunamadı")
    except Exception as hata:
        hatalar.append(f"MSB / TSK Güncel Teminler: {type(hata).__name__} - {str(hata)[:150]}")

    try:
        msb_haberleri = msb_guncel_haberleri_al()
        tum_haberler.extend(msb_haberleri)
        kaynak_sayilari["MSB / TSK haberleri"] = len(msb_haberleri)
        print(f"MSB / TSK haberleri: {len(msb_haberleri)}")
    except Exception as hata:
        hatalar.append(f"MSB / TSK haberleri: {type(hata).__name__} - {str(hata)[:150]}")

    for resmi_kaynak in RESMI_DUYURU_KAYNAKLARI:
        try:
            resmi_ilanlar = resmi_kaynaktan_ilanlari_al(resmi_kaynak)
            tum_ilanlar.extend(resmi_ilanlar)
            kaynak_sayilari[resmi_kaynak["kaynak"]] = len(resmi_ilanlar)
            print(f"{resmi_kaynak['kaynak']}: {len(resmi_ilanlar)}")
            resmi_haberler = resmi_kaynaktan_haberleri_al(resmi_kaynak)
            tum_haberler.extend(resmi_haberler)
            print(f"{resmi_kaynak['kurum']} haberleri: {len(resmi_haberler)}")
        except Exception as hata:
            hatalar.append(f"{resmi_kaynak['kaynak']}: {type(hata).__name__} - {str(hata)[:150]}")

    # Türkiye'deki üniversite personel ilanları: merkezi resmî portal her
    # çalışmada, üniversitelerin resmî .edu.tr siteleri ise dönüşümlü taranır.
    universite_kaynaklari = []
    universite_tarama_sirasi = 0
    universite_tarama_ozeti = {
        "merkezi_kontrol_edilen_link": 0,
        "merkezi_bulunan_ilan": 0,
        "resmi_site_sayisi": 0,
        "bu_calismada_taranan_site": 0,
        "resmi_siteden_bulunan_ilan": 0,
        "resmi_siteden_bulunan_haber": 0,
    }

    try:
        merkezi_universite_ilanlari, merkezi_hatalar, kontrol_edilen = (
            ilan_gov_tr_universite_ilanlarini_al(onceki_veri)
        )
        tum_ilanlar.extend(merkezi_universite_ilanlari)
        hatalar.extend(merkezi_hatalar)
        kaynak_sayilari["ilan.gov.tr Üniversite Personel İlanları"] = len(
            merkezi_universite_ilanlari
        )
        universite_tarama_ozeti["merkezi_kontrol_edilen_link"] = kontrol_edilen
        universite_tarama_ozeti["merkezi_bulunan_ilan"] = len(merkezi_universite_ilanlari)
        print(
            "ilan.gov.tr üniversite personel ilanları: "
            f"{len(merkezi_universite_ilanlari)} "
            f"(kontrol edilen bağlantı: {kontrol_edilen})"
        )
    except Exception as hata:
        hatalar.append(
            f"ilan.gov.tr üniversite ilanları: {type(hata).__name__} - {str(hata)[:150]}"
        )

    try:
        (
            site_universite_ilanlari,
            site_universite_haberleri,
            universite_kaynaklari,
            universite_tarama_sirasi,
            site_hatalari,
            taranan_site_sayisi,
        ) = universite_resmi_sitelerini_donusumlu_tara(onceki_veri)
        tum_ilanlar.extend(site_universite_ilanlari)
        tum_haberler.extend(site_universite_haberleri)
        hatalar.extend(site_hatalari)
        kaynak_sayilari["Üniversite Resmî Duyuruları"] = len(site_universite_ilanlari)
        kaynak_sayilari["Üniversite Personel Haberleri"] = len(site_universite_haberleri)
        universite_tarama_ozeti["resmi_site_sayisi"] = len(universite_kaynaklari)
        universite_tarama_ozeti["bu_calismada_taranan_site"] = taranan_site_sayisi
        universite_tarama_ozeti["resmi_siteden_bulunan_ilan"] = len(site_universite_ilanlari)
        universite_tarama_ozeti["resmi_siteden_bulunan_haber"] = len(site_universite_haberleri)
        print(
            "Üniversite resmî siteleri: "
            f"{taranan_site_sayisi}/{len(universite_kaynaklari)} tarandı, "
            f"{len(site_universite_ilanlari)} ilan ve "
            f"{len(site_universite_haberleri)} personel haberi bulundu"
        )
    except Exception as hata:
        hatalar.append(
            f"Üniversite resmî siteleri: {type(hata).__name__} - {str(hata)[:150]}"
        )

    # Kaynak geçici hata verdiğinde uygulamanın tamamen boşalmaması için önceki
    # aktif veriyi güvenlik ağı olarak koru. Yeni kayıtlar her zaman önceliklidir.
    onceki_ilanlar = onceki_veri.get("ilanlar", []) if isinstance(onceki_veri, dict) else []
    yeni_universite_sayisi = sum(
        1 for ilan in tum_ilanlar if ilan.get("kaynak_kodu") == "universite_personel"
    )
    if yeni_universite_sayisi == 0:
        onceki_universite_ilanlari = [
            ilan
            for ilan in onceki_ilanlar
            if ilan.get("kaynak_kodu") == "universite_personel"
            and ilan_aktif_mi(ilan)
        ]
        if onceki_universite_ilanlari:
            print(
                "UYARI: Üniversite kaynakları geçici olarak boş döndü; "
                f"önceki {len(onceki_universite_ilanlari)} aktif üniversite ilanı korunuyor."
            )
            tum_ilanlar.extend(onceki_universite_ilanlari)
    if len(tum_ilanlar) < max(25, int(len(onceki_ilanlar) * 0.35)):
        print("UYARI: Yeni ilan sayısı olağandışı düşük; önceki aktif ilanlar birleştiriliyor.")
        tum_ilanlar.extend(onceki_ilanlar)
    elif not msb_basarili:
        tum_ilanlar.extend([i for i in onceki_ilanlar if i.get("kaynak_kodu") == "msb_tsk"])

    benzersiz = {}
    for ilan in tum_ilanlar:
        anahtar = link_anahtari(ilan.get("link", ""))
        if anahtar:
            benzersiz[anahtar] = ilan
    simdi_tr = datetime.now(TURKIYE_SAATI)
    aktif_ilanlar = [i for i in benzersiz.values() if ilan_aktif_mi(i, simdi_tr)]

    # Önceki ilanlar.json dosyasından gelebilecek yanlış sınıflandırılmış ÖSYM
    # sınav duyurularını da temizle. Bunlar Haberler bölümünde zaten tutulur.
    aktif_ilanlar = [
        ilan
        for ilan in aktif_ilanlar
        if not (
            arama_metnine_cevir(ilan.get("kurum", "")) == "osym"
            and not osym_aktif_duyuru_mu(ilan.get("baslik", ""))
        )
    ]
    suresi_dolmus = len(benzersiz) - len(aktif_ilanlar)
    print(f"Süresi dolduğu için kaldırılan ilan: {suresi_dolmus}")

    # Haberlerde geçici kaynak hatasına karşı son 180 günlük önceki kayıtları koru.
    onceki_haberler = onceki_veri.get("haberler", []) if isinstance(onceki_veri, dict) else []
    tum_haberler.extend([h for h in onceki_haberler if haber_guncel_mi(h, 180)])
    benzersiz_haberler = {}
    for haber in tum_haberler:
        anahtar = link_anahtari(haber.get("link", ""))
        if anahtar and haber_guncel_mi(haber, 180):
            benzersiz_haberler[anahtar] = haber
    haberler = list(benzersiz_haberler.values())
    haberler.sort(key=haber_tarih_siralama_degeri, reverse=True)

    onceki_analizler = onceki_analizleri_yukle()
    zenginlestirilmis, yeniden_kullanilan, pdf_hatalari = [], 0, 0
    with ThreadPoolExecutor(max_workers=6) as havuz:
        futures = {havuz.submit(ilani_zenginlestir, ilan, onceki_analizler): ilan.get("baslik", "")
                   for ilan in aktif_ilanlar}
        for tamamlanan, future in enumerate(as_completed(futures), 1):
            try:
                ilan, oncekiden = future.result()
                zenginlestirilmis.append(ilan)
                yeniden_kullanilan += int(oncekiden)
                if str(ilan.get("pdf_isleme_durumu", "")).startswith("hata:"):
                    pdf_hatalari += 1
            except Exception as hata:
                pdf_hatalari += 1
                hatalar.append(f"PDF analizi: {type(hata).__name__} - {str(hata)[:150]}")
            print(f"PDF analizi: {tamamlanan}/{len(aktif_ilanlar)} - önbellekten: {yeniden_kullanilan}")

    zenginlestirilmis.sort(key=tarih_siralama_degeri)
    bildirim_sonucu = yeni_kayit_bildirimlerini_gonder(
        zenginlestirilmis, haberler, onceki_ilan_anahtarlari,
        onceki_haber_anahtarlari, onceki_dosya_vardi,
    )
    print(f"Bildirim: {bildirim_sonucu['mesaj']}")

    print("Profil bildirimi testi: kapalı; yalnızca gerçek ilan ve haberler gönderilecek.")

    otomatik_bildirim_testi = {
        "durum": "kapali",
        "basarili": True,
        "mesaj": "Test bildirimi kapatıldı.",
    }

    print(f"Otomatik bildirim testi: {otomatik_bildirim_testi['mesaj']}")

    cikti = {
        "status": "ok" if zenginlestirilmis or haberler else "veri_alinamadi",
        "guncellenme_zamani": datetime.now(timezone.utc).isoformat(),
        "ilan_sayisi": len(zenginlestirilmis),
        "suresi_dolmus_ilan_sayisi": suresi_dolmus,
        "ilanlar": zenginlestirilmis,
        "haber_sayisi": len(haberler),
        "haberler": haberler,
        "kaynak_sayilari": kaynak_sayilari,
        "universite_kaynaklari": universite_kaynaklari,
        "universite_tarama_sirasi": universite_tarama_sirasi,
        "universite_tarama_ozeti": universite_tarama_ozeti,
        "bildirim_sonucu": bildirim_sonucu,
        "hata_sayisi": len(hatalar),
        "pdf_hata_sayisi": pdf_hatalari,
        "hatalar": hatalar[:80],
    }
    gecici = Path("ilanlar.json.tmp")
    gecici.write_text(json.dumps(cikti, ensure_ascii=False, indent=2), encoding="utf-8")
    gecici.replace(Path("ilanlar.json"))
    print("--------------------------------")
    print(f"Toplam aktif ilan: {len(zenginlestirilmis)}")
    print(f"Toplam güncel haber: {len(haberler)}")
    print(f"MSB / TSK ilanı: {sum(1 for i in zenginlestirilmis if i.get('kaynak_kodu') == 'msb_tsk')}")
    print(f"Sayfa hatası: {len(hatalar)} | PDF hatası: {pdf_hatalari}")
    print("ilanlar.json oluşturuldu.")

if __name__ == "__main__":
    main()
