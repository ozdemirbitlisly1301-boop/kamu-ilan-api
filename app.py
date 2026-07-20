from flask import Flask, jsonify

app = Flask(__name__)

@app.route("/")
def home():
    return jsonify({
        "mesaj": "API çalışıyor",
        "ilan_sayisi": 1,
        "ilanlar": [
            {
                "baslik": "Test Kamu İlanı",
                "kurum": "KPSS Kariyer",
                "url": "https://www.ilan.gov.tr"
            }
        ]
    })

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
