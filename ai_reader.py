import os
import base64
import json

from openai import OpenAI
from dotenv import load_dotenv


load_dotenv()

client = OpenAI(
    api_key=os.getenv("OPENAI_API_KEY")
)


def encode_image(image_path):
    with open(image_path, "rb") as f:
        return base64.b64encode(f.read()).decode("utf-8")


def read_transaction_screenshot(image_path):

    image_data = encode_image(image_path)

    prompt = """
Kamu adalah AI pembaca transaksi Finance HQ.

Baca SEMUA transaksi yang terlihat pada screenshot.

ATURAN:
- Jangan mengarang data.
- Jangan menebak data yang tidak terlihat.
- Transaksi yang terpotong harus perlu_review=true.
- Jika tanggal tidak jelas, perlu_review=true.
- Jika nominal tidak jelas, perlu_review=true.
- Jika tipe transaksi tidak jelas, perlu_review=true.
- Jika platform/bank tidak terlihat tetapi transaksi lainnya jelas,
  perlu_review=false dan perlu_platform=true.
- Jangan mengarang nama bank/platform.
- Ambil hanya transaksi yang benar-benar terlihat.

ATURAN FINANCE HQ:
- BCA -> Stockbit = transfer
- Stockbit -> BCA = transfer
- Mandiri -> SeaBank = transfer
- Transfer antar rekening bukan pemasukan/pengeluaran.
- Jika kategori Makanan, catatan = "makanan".
- Pemasukan dari nama orang: catatan kosong.
- Catatan tidak boleh berisi tanggal.
- Pengembalian/refund yang jelas gunakan kategori Pengembalian.
- Jika tidak yakin, gunakan perlu_review=true.

TIPE:
- pemasukan
- pengeluaran
- transfer

Contoh:
Bunga Tabungan +Rp165 yang terlihat jelas,
tetapi bank tidak terlihat:
perlu_review=false
perlu_platform=true
platform=null

Transaksi yang terpotong:
perlu_review=true
"""

    schema = {
        "type": "object",
        "properties": {
            "transaksi": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "tanggal": {
                            "type": ["string", "null"]
                        },
                        "keterangan": {
                            "type": ["string", "null"]
                        },
                        "kategori": {
                            "type": ["string", "null"]
                        },
                        "catatan": {
                            "type": ["string", "null"]
                        },
                        "platform": {
                            "type": ["string", "null"]
                        },
                        "tipe": {
                            "type": ["string", "null"]
                        },
                        "nominal": {
                            "type": ["integer", "null"]
                        },
                        "dari_platform": {
                            "type": ["string", "null"]
                        },
                        "ke_platform": {
                            "type": ["string", "null"]
                        },
                        "perlu_platform": {
                            "type": "boolean"
                        },
                        "perlu_review": {
                            "type": "boolean"
                        },
                        "confidence": {
                            "type": "number"
                        },
                        "alasan_review": {
                            "type": ["string", "null"]
                        }
                    },
                    "required": [
                        "tanggal",
                        "keterangan",
                        "kategori",
                        "catatan",
                        "platform",
                        "tipe",
                        "nominal",
                        "dari_platform",
                        "ke_platform",
                        "perlu_platform",
                        "perlu_review",
                        "confidence",
                        "alasan_review"
                    ],
                    "additionalProperties": False
                }
            }
        },
        "required": [
            "transaksi"
        ],
        "additionalProperties": False
    }

    response = client.responses.create(
        model="gpt-5.6-luna",
        input=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "input_text",
                        "text": prompt
                    },
                    {
                        "type": "input_image",
                        "image_url": f"data:image/jpeg;base64,{image_data}"
                    }
                ]
            }
        ],
        text={
            "format": {
                "type": "json_schema",
                "name": "finance_transactions",
                "strict": True,
                "schema": schema
            }
        }
    )

    return json.loads(response.output_text)