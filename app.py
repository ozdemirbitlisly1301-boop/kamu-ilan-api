from flask import Flask, jsonify, request
from pathlib import Path
import json
import threading

app = Flask(__name__)

JSON_DOSYASI = Path(__file__).resolve().parent / "ilanlar.json"

CACHE = {
    "degisiklik_zamani": None,
    "veri": None,
}

LOCK = threading.Lock()


def json_verisini_oku():
    if not JSON_DOSYASI.exists():
        return {
            "status": "hata",
            "mesaj": "ilanlar.json henüz oluşturulmamış.",
            "ilan_sayisi": 0,
            "ilanlar": [],
        }

    degisiklik_zamani = JSON_DOSYASI.stat().st_mtime

    with LOCK:
        if (
            CACHE["veri"] is not None
            and CACHE["degisiklik_zamani"] == degisiklik_zamani
        ):
            return CACHE["veri"]

        try:
            with JSON_DOSYASI.open("r", encoding="utf-8") as dosya:
                veri = json.load(dosya)

            if not isinstance(veri, dict):
                raise ValueError("JSON ana yapısı nesne olmalıdır.")

            ilanlar = veri.get("ilanlar", [])

            if not isinstance(ilanlar, list):
                ilanlar = []

            veri["ilanlar"] = ilanlar
            veri["ilan_sayisi"] = len(ilanlar)
            veri["status"] = "ok"

            CACHE["veri"] = veri
            CACHE["degisiklik_zamani"] = degisiklik_zamani

            return veri

        except Exception as hata:
            return {
                "status": "hata",
                "mesaj": f"JSON okunamadı: {type(hata).__name__}",
                "ilan_sayisi": 0,
                "ilanlar": [],
            }


def filtrele(ilanlar):
    arama = request.args.get("q", "").strip().casefold()
    sehir = request.args.get("sehir", "").strip().casefold()
    tur = request.args.get("tur", "").strip().casefold()
    kaynak = request.args.get("kaynak", "").strip().casefold()

    sonuc = []

    for ilan in ilanlar:
        baslik = str(ilan.get("baslik", ""))
        kurum = str(ilan.get("kurum", ""))
        ilan_sehri = str(ilan.get("sehir", ""))
        ilan_turu = str(ilan.get("tur", ""))
        ilan_kaynagi = str(ilan.get("kaynak", ""))

        aranacak_metin = f"{baslik} {kurum} {ilan_sehri}".casefold()

        if arama and arama not in aranacak_metin:
            continue

        if sehir and sehir != ilan_sehri.casefold():
            continue

        if tur and tur != ilan_turu.casefold():
            continue

        if kaynak and kaynak not in ilan_kaynagi.casefold():
            continue

        sonuc.append(ilan)

    return sonuc


@app.route("/", methods=["GET"])
@app.route("/ilanlar", methods=["GET"])
def ilanlari_getir():
    veri = json_verisini_oku()

    if veri.get("status") != "ok":
        return jsonify(veri), 500

    ilanlar = filtrele(veri.get("ilanlar", []))

    cevap = {
        "status": "ok",
        "guncellenme_zamani": veri.get("guncellenme_zamani", ""),
        "ilan_sayisi": len(ilanlar),
        "ilanlar": ilanlar,
    }

    return jsonify(cevap)


@app.route("/saglik", methods=["GET"])
def saglik():
    veri = json_verisini_oku()

    return jsonify({
        "status": "calisiyor",
        "json_mevcut": JSON_DOSYASI.exists(),
        "ilan_sayisi": len(veri.get("ilanlar", [])),
        "guncellenme_zamani": veri.get("guncellenme_zamani", ""),
    })


@app.errorhandler(404)
def bulunamadi(_):
    return jsonify({
        "status": "hata",
        "mesaj": "Adres bulunamadı.",
    }), 404


@app.errorhandler(500)
def sunucu_hatasi(_):
    return jsonify({
        "status": "hata",
        "mesaj": "Sunucu hatası oluştu.",
    }), 500


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
