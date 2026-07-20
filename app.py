from flask import Flask, jsonify

app = Flask(__name__)

@app.route("/")
def home():
    return jsonify({
        "ilan_sayisi": 3,
        "ilanlar": [
            {
                "baslik": "Sağlık Bakanlığı Personel Alımı",
                "kurum": "Sağlık Bakanlığı",
                "url": "https://www.saglik.gov.tr"
            },
            {
                "baslik": "Adalet Bakanlığı Personel Alımı",
                "kurum": "Adalet Bakanlığı",
                "url": "https://www.adalet.gov.tr"
            },
            {
                "baslik": "Tarım ve Orman Bakanlığı Personel Alımı",
                "kurum": "Tarım ve Orman Bakanlığı",
                "url": "https://www.tarimorman.gov.tr"
            }
        ]
    })

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
