from pathlib import Path
import json

from flask import Flask, Response

app = Flask(__name__)

VERI_DOSYASI = Path(__file__).with_name("ilanlar.json")


def json_cevabi(veri, durum=200):
    return Response(
        json.dumps(veri, ensure_ascii=False),
        status=durum,
        mimetype="application/json",
    )


@app.after_request
def basliklari_ekle(response):
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
    return response


@app.get("/")
def ana_sayfa():
    return json_cevabi({
        "durum": "çalışıyor",
        "servis": "KPSS Kariyer Kamu İlan API",
        "ilanlar_endpoint": "/ilanlar",
    })


@app.get("/ilanlar")
def ilanlar():
    try:
        if not VERI_DOSYASI.exists():
            return json_cevabi(
                {"hata": "ilanlar.json dosyası bulunamadı."},
                404,
            )

        with VERI_DOSYASI.open("r", encoding="utf-8") as dosya:
            veri = json.load(dosya)

        if not isinstance(veri, dict):
            return json_cevabi(
                {"hata": "ilanlar.json biçimi geçersiz."},
                500,
            )

        veri.setdefault("ilanlar", [])
        veri.setdefault("haberler", [])
        return json_cevabi(veri)

    except json.JSONDecodeError as hata:
        return json_cevabi(
            {"hata": f"ilanlar.json okunamadı: {hata}"},
            500,
        )
    except Exception as hata:
        return json_cevabi(
            {"hata": f"Sunucu hatası: {hata}"},
            500,
        )


@app.get("/saglik")
def saglik():
    return json_cevabi({"durum": "ok"})
