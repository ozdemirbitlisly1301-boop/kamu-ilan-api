from flask import Flask, jsonify

app = Flask(__name__)

@app.route("/")
def home():
    return jsonify({
        "status": "ok",
        "message": "API çalışıyor",
        "ilanlar": [
            {
                "baslik": "Test İlanı",
                "kurum": "Test Kurumu",
                "url": "https://example.com"
            }
        ]
    })

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
