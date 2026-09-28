import os
import sys
import sqlite3
import shutil
import hashlib
from datetime import datetime
from flask import Flask, request, render_template_string, redirect, url_for, flash

# ============================================================
# FINANCE HQ + AI IMPORTER
# Satu server, satu database
# ============================================================

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATABASE = os.path.join(BASE_DIR, "finance.db")

# Cari AI Finance Importer yang berada satu folder dengan FinanceDashboard
AI_DIR = os.path.abspath(os.path.join(BASE_DIR, "..", "AIFinanceImporter"))

if os.path.isdir(AI_DIR):
    sys.path.insert(0, AI_DIR)

    # ai_reader.py memakai load_dotenv() tanpa path.
    # Karena server dijalankan dari FinanceDashboard, load .env AI secara eksplisit.
    try:
        from dotenv import load_dotenv
        load_dotenv(os.path.join(AI_DIR, ".env"))
    except Exception:
        pass

try:
    from ai_reader import read_transaction_screenshot
    AI_AVAILABLE = True
    AI_ERROR = ""
except Exception as e:
    AI_AVAILABLE = False
    AI_ERROR = str(e)

app = Flask(__name__)
app.secret_key = "finance-hq-local-secret"

UPLOAD_FOLDER = os.path.join(BASE_DIR, "ai_uploads")
BACKUP_FOLDER = os.path.join(BASE_DIR, "backups")
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(BACKUP_FOLDER, exist_ok=True)

PLATFORMS = ["BCA", "SeaBank", "Mandiri", "Cash", "Bibit/Stockbit", "Lainnya"]
TYPES = ["Pengeluaran", "Pemasukan", "Transfer"]

CURRENT_TRANSACTIONS = []


# ============================================================
# DATABASE
# ============================================================

def get_db():
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_db()

    conn.execute("""
        CREATE TABLE IF NOT EXISTS transaksi (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tanggal TEXT,
            keterangan TEXT,
            kategori TEXT,
            platform TEXT,
            tipe TEXT,
            nominal REAL,
            dari_platform TEXT,
            ke_platform TEXT
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS aset (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nama TEXT,
            jenis TEXT,
            kepemilikan TEXT,
            tanggal TEXT,
            nilai REAL,
            keuntungan REAL,
            imbal_hasil REAL,
            catatan TEXT
        )
    """)

    conn.commit()
    conn.close()


def backup_database():
    if not os.path.exists(DATABASE):
        return None

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = os.path.join(BACKUP_FOLDER, f"finance_backup_{stamp}.db")
    shutil.copy2(DATABASE, path)
    return path


def normalize_type(value):
    value = (value or "").strip().lower()

    if value in ("pemasukan", "income", "masuk"):
        return "Pemasukan"

    if value in ("pengeluaran", "expense", "keluar"):
        return "Pengeluaran"

    if value in ("transfer",):
        return "Transfer"

    return value.title() if value else ""


def normalize_nominal(value):
    if value is None or value == "":
        return None

    try:
        return int(float(value))
    except Exception:
        return None


def fingerprint(t):
    text = "|".join([
        str(t.get("tanggal") or "").strip(),
        str(t.get("keterangan") or "").strip().lower(),
        str(t.get("nominal") or ""),
        str(t.get("tipe") or "").strip().lower(),
        str(t.get("platform") or "").strip().lower(),
    ])
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def existing_fingerprints():
    conn = get_db()
    rows = conn.execute("""
        SELECT tanggal, keterangan, nominal, tipe, platform
        FROM transaksi
    """).fetchall()
    conn.close()

    result = set()
    for row in rows:
        result.add(fingerprint(dict(row)))
    return result


# ============================================================
# HTML
# ============================================================

STYLE = """
<style>
*{box-sizing:border-box}
body{font-family:Arial,sans-serif;background:#f4f6f8;margin:0;color:#111827}
nav{background:#111827;padding:14px 24px;display:flex;gap:8px;flex-wrap:wrap}
nav a{color:white;text-decoration:none;padding:9px 13px;border-radius:7px}
nav a:hover{background:#374151}
.container{max-width:1450px;margin:25px auto;padding:0 18px}
.card{background:white;border-radius:14px;padding:22px;margin-bottom:18px;box-shadow:0 2px 10px rgba(0,0,0,.07)}
h1{margin-top:0}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:14px}
.stat{background:#f3f4f6;padding:18px;border-radius:10px}
.stat b{display:block;font-size:25px;margin-top:5px}
button,.btn{background:#111827;color:white;border:0;padding:10px 15px;border-radius:8px;cursor:pointer;text-decoration:none;display:inline-block}
.btn.green{background:#15803d}
.btn.red{background:#dc2626}
.btn.blue{background:#2563eb}
input,select{padding:8px;border:1px solid #d1d5db;border-radius:7px;width:100%}
input[type=file]{width:auto}
table{width:100%;border-collapse:collapse;margin-top:14px}
th,td{padding:9px;border-bottom:1px solid #e5e7eb;text-align:left;vertical-align:middle}
th{background:#f3f4f6}
.ok{color:#15803d;font-weight:bold}
.warn{color:#b45309;font-weight:bold}
.review{color:#dc2626;font-weight:bold}
.danger{background:#fef2f2;color:#991b1b;padding:12px;border-radius:8px}
.success{background:#ecfdf5;color:#166534;padding:12px;border-radius:8px}
.info{background:#eff6ff;color:#1d4ed8;padding:12px;border-radius:8px}
.small{font-size:12px;color:#6b7280}
.actions{display:flex;gap:8px;flex-wrap:wrap}
.sticky{position:sticky;top:0;background:white;padding:10px 0;z-index:3}
</style>
"""


def page(title, body):
    return render_template_string("""
    <!doctype html>
    <html>
    <head>
        <meta charset="utf-8">
        <title>{{ title }}</title>
        {{ style|safe }}
    </head>
    <body>
        <nav>
            <a href="{{ url_for('dashboard') }}">🏠 Dashboard</a>
            <a href="{{ url_for('transaksi') }}">💳 Transaksi</a>
            <a href="{{ url_for('pemasukan') }}">💰 Pemasukan</a>
            <a href="{{ url_for('transfer') }}">🔄 Transfer</a>
            <a href="{{ url_for('aset') }}">🏦 Aset</a>
            <a href="{{ url_for('ai_importer') }}">🤖 AI Importer</a>
        </nav>
        <div class="container">
            {{ body|safe }}
        </div>
    </body>
    </html>
    """, title=title, style=STYLE, body=body)


# ============================================================
# DASHBOARD
# ============================================================

@app.route("/")
def dashboard():
    conn = get_db()

    income = conn.execute("""
        SELECT COALESCE(SUM(nominal),0) FROM transaksi
        WHERE tipe='Pemasukan'
    """).fetchone()[0]

    expense = conn.execute("""
        SELECT COALESCE(SUM(nominal),0) FROM transaksi
        WHERE tipe='Pengeluaran'
    """).fetchone()[0]

    transfer = conn.execute("""
        SELECT COALESCE(SUM(nominal),0) FROM transaksi
        WHERE tipe='Transfer'
    """).fetchone()[0]

    count = conn.execute("SELECT COUNT(*) FROM transaksi").fetchone()[0]
    conn.close()

    body = f"""
    <div class="card">
        <h1>💰 Finance HQ</h1>
        <p class="small">Finance HQ + AI Finance Importer dalam satu server.</p>
    </div>

    <div class="grid">
        <div class="stat">💰 Pemasukan<b>Rp{income:,.0f}</b></div>
        <div class="stat">💸 Pengeluaran<b>Rp{expense:,.0f}</b></div>
        <div class="stat">📈 Saldo Bersih<b>Rp{income-expense:,.0f}</b></div>
        <div class="stat">🔄 Transfer<b>Rp{transfer:,.0f}</b></div>
        <div class="stat">🧾 Transaksi<b>{count}</b></div>
    </div>

    <div class="card">
        <h2>🤖 AI Finance Importer</h2>
        <p>Upload screenshot → AI membaca → review → cek duplikat → import ke database Finance HQ.</p>
        <a class="btn blue" href="{url_for('ai_importer')}">Buka AI Importer</a>
    </div>
    """
    return page("Finance HQ", body)


# ============================================================
# TRANSAKSI
# ============================================================

@app.route("/transaksi")
def transaksi():
    conn = get_db()
    rows = conn.execute("""
        SELECT * FROM transaksi
        ORDER BY tanggal DESC, id DESC
    """).fetchall()
    conn.close()

    trs = ""
    for r in rows:
        trs += f"""
        <tr>
            <td>{r['tanggal'] or ''}</td>
            <td>{r['keterangan'] or ''}</td>
            <td>{r['kategori'] or ''}</td>
            <td>{r['platform'] or ''}</td>
            <td>{r['tipe'] or ''}</td>
            <td>Rp{float(r['nominal'] or 0):,.0f}</td>
            <td>
                <form method="post" action="{url_for('delete_transaksi', transaction_id=r['id'])}"
                      onsubmit="return confirm('Hapus transaksi ini?')">
                    <button class="btn red" type="submit">🗑️</button>
                </form>
            </td>
        </tr>
        """

    body = f"""
    <div class="card">
        <h1>💳 Transaksi</h1>
        <table>
            <thead><tr>
                <th>Tanggal</th><th>Keterangan</th><th>Kategori</th>
                <th>Platform</th><th>Tipe</th><th>Nominal</th><th></th>
            </tr></thead>
            <tbody>{trs}</tbody>
        </table>
    </div>
    """
    return page("Transaksi", body)


@app.route("/transaksi/delete/<int:transaction_id>", methods=["POST"])
def delete_transaksi(transaction_id):
    conn = get_db()
    conn.execute("DELETE FROM transaksi WHERE id=?", (transaction_id,))
    conn.commit()
    conn.close()
    return redirect(url_for("transaksi"))


@app.route("/pemasukan")
def pemasukan():
    conn = get_db()
    rows = conn.execute("""
        SELECT * FROM transaksi
        WHERE tipe='Pemasukan'
        ORDER BY tanggal DESC, id DESC
    """).fetchall()
    conn.close()

    trs = "".join(
        f"<tr><td>{r['tanggal'] or ''}</td><td>{r['keterangan'] or ''}</td>"
        f"<td>{r['kategori'] or ''}</td><td>{r['platform'] or ''}</td>"
        f"<td>Rp{float(r['nominal'] or 0):,.0f}</td></tr>"
        for r in rows
    )

    body = f"""
    <div class="card">
        <h1>💰 Pemasukan</h1>
        <table><thead><tr>
        <th>Tanggal</th><th>Keterangan</th><th>Kategori</th><th>Platform</th><th>Nominal</th>
        </tr></thead><tbody>{trs}</tbody></table>
    </div>
    """
    return page("Pemasukan", body)


@app.route("/transfer")
def transfer():
    conn = get_db()
    rows = conn.execute("""
        SELECT * FROM transaksi
        WHERE tipe='Transfer'
        ORDER BY tanggal DESC, id DESC
    """).fetchall()
    conn.close()

    trs = "".join(
        f"<tr><td>{r['tanggal'] or ''}</td><td>{r['keterangan'] or ''}</td>"
        f"<td>{r['dari_platform'] or ''}</td><td>→</td>"
        f"<td>{r['ke_platform'] or ''}</td>"
        f"<td>Rp{float(r['nominal'] or 0):,.0f}</td></tr>"
        for r in rows
    )

    body = f"""
    <div class="card">
        <h1>🔄 Transfer Antar Rekening</h1>
        <table><thead><tr>
        <th>Tanggal</th><th>Keterangan</th><th>Dari</th><th></th><th>Ke</th><th>Nominal</th>
        </tr></thead><tbody>{trs}</tbody></table>
    </div>
    """
    return page("Transfer", body)


@app.route("/aset")
def aset():
    conn = get_db()
    rows = conn.execute("""
        SELECT * FROM aset
        ORDER BY tanggal DESC, id DESC
    """).fetchall()
    conn.close()

    trs = "".join(
        f"<tr><td>{r['nama'] or ''}</td><td>{r['jenis'] or ''}</td>"
        f"<td>{r['kepemilikan'] or ''}</td><td>{r['tanggal'] or ''}</td>"
        f"<td>Rp{float(r['nilai'] or 0):,.0f}</td>"
        f"<td>{r['catatan'] or ''}</td></tr>"
        for r in rows
    )

    body = f"""
    <div class="card">
        <h1>🏦 Aset</h1>
        <table><thead><tr>
        <th>Nama</th><th>Jenis</th><th>Kepemilikan</th><th>Tanggal</th><th>Nilai</th><th>Catatan</th>
        </tr></thead><tbody>{trs}</tbody></table>
    </div>
    """
    return page("Aset", body)


# ============================================================
# AI IMPORTER
# ============================================================

AI_HTML = """
<div class="card">
    <h1>🤖 AI Finance Importer</h1>
    <p>Screenshot → AI → Review → Duplicate Check → Import Finance HQ</p>

    {% if not ai_available %}
        <div class="danger">
            ❌ AI Reader belum bisa dipanggil.<br>
            <span class="small">{{ ai_error }}</span>
        </div>
    {% else %}
        <div class="info">
            Pilih platform screenshot sekali. Platform tersebut akan diterapkan
            ke semua transaksi yang platform-nya kosong.
        </div>

        <form method="post" enctype="multipart/form-data">
            <p><b>🏦 Platform Screenshot</b></p>
            <select name="batch_platform">
                <option value="">-- Pilih Platform --</option>
                {% for p in platforms %}
                    <option value="{{ p }}">{{ p }}</option>
                {% endfor %}
            </select>

            <p><b>📸 Screenshot</b></p>
            <input type="file" name="screenshots" accept="image/*" multiple required>
            <br><br>
            <button class="btn blue" type="submit">🔎 Baca Screenshot</button>
        </form>
    {% endif %}
</div>

{% if transactions %}
<div class="card">
    <div class="sticky">
        <h2>📊 Hasil AI</h2>
        <div class="grid">
            <div class="stat">Total<b>{{ total }}</b></div>
            <div class="stat">Siap<b>{{ ready }}</b></div>
            <div class="stat">Platform<b>{{ need_platform }}</b></div>
            <div class="stat">Review<b>{{ review }}</b></div>
            <div class="stat">Duplikat<b>{{ duplicates }}</b></div>
        </div>
    </div>

    {% if review %}
        <div class="danger">⚠️ Masih ada data yang wajib direview.</div>
    {% elif need_platform %}
        <div class="info">🏦 Pilih platform untuk transaksi yang masih kosong.</div>
    {% else %}
        <div class="success">✅ Semua transaksi siap diproses.</div>
    {% endif %}

    <form method="post" action="{{ url_for('ai_save_review') }}">
        <table>
            <thead><tr>
                <th>#</th><th>Tanggal</th><th>Keterangan</th><th>Kategori</th>
                <th>Catatan</th><th>Nominal</th><th>Tipe</th><th>Platform</th>
                <th>Status</th><th>Hapus</th>
            </tr></thead>
            <tbody>
            {% for t in transactions %}
                <tr>
                    <td>{{ loop.index }}</td>
                    <td><input name="tanggal_{{ loop.index0 }}" value="{{ t.tanggal or '' }}"></td>
                    <td><input name="keterangan_{{ loop.index0 }}" value="{{ t.keterangan or '' }}"></td>
                    <td><input name="kategori_{{ loop.index0 }}" value="{{ t.kategori or '' }}"></td>
                    <td><input name="catatan_{{ loop.index0 }}" value="{{ t.catatan or '' }}"></td>
                    <td><input type="number" name="nominal_{{ loop.index0 }}" value="{{ t.nominal if t.nominal is not none else '' }}"></td>
                    <td>
                        <select name="tipe_{{ loop.index0 }}">
                            {% for typ in types %}
                                <option value="{{ typ }}" {% if t.tipe|lower == typ|lower %}selected{% endif %}>{{ typ }}</option>
                            {% endfor %}
                        </select>
                    </td>
                    <td>
                        <select name="platform_{{ loop.index0 }}">
                            <option value="">-- Pilih --</option>
                            {% for p in platforms %}
                                <option value="{{ p }}" {% if t.platform == p %}selected{% endif %}>{{ p }}</option>
                            {% endfor %}
                        </select>
                    </td>
                    <td>
                        {% if t.perlu_review %}
                            <span class="review">🔴 REVIEW</span>
                        {% elif t.duplicate %}
                            <span class="review">🟠 DUPLIKAT</span>
                        {% elif t.perlu_platform %}
                            <span class="warn">🟡 PLATFORM</span>
                        {% else %}
                            <span class="ok">🟢 SIAP</span>
                        {% endif %}
                    </td>
                    <td>
                        <button type="button" class="btn red" onclick="deleteRow({{ loop.index0 }})">🗑️</button>
                        <input type="hidden" id="delete_{{ loop.index0 }}" name="delete_{{ loop.index0 }}" value="0">
                    </td>
                </tr>
                {% if t.alasan_review %}
                <tr>
                    <td></td>
                    <td colspan="9" class="small">⚠️ {{ t.alasan_review }}</td>
                </tr>
                {% endif %}
            {% endfor %}
            </tbody>
        </table>

        <br>
        <button class="btn" type="submit">💾 Simpan Hasil Review</button>
    </form>

    {% if can_import %}
    <form method="post" action="{{ url_for('ai_import') }}" style="margin-top:12px"
          onsubmit="return confirm('Import transaksi yang sudah siap ke Finance HQ? Database akan dibackup otomatis.')">
        <button class="btn green" type="submit">🚀 IMPORT KE FINANCE HQ</button>
    </form>
    {% else %}
        <div class="warning" style="margin-top:15px">
            🔒 Tombol import aktif setelah semua data wajib sudah lengkap.
        </div>
    {% endif %}
</div>
{% endif %}

<script>
function deleteRow(i){
    document.getElementById("delete_"+i).value="1";
    const row = document.getElementById("delete_"+i).closest("tr");
    row.style.display="none";
}
</script>
"""


def calculate_ai():
    total = len(CURRENT_TRANSACTIONS)
    review = sum(1 for t in CURRENT_TRANSACTIONS if t.get("perlu_review"))
    need_platform = sum(
        1 for t in CURRENT_TRANSACTIONS
        if t.get("perlu_platform") and not t.get("perlu_review")
    )
    duplicates = sum(1 for t in CURRENT_TRANSACTIONS if t.get("duplicate"))
    ready = total - review - need_platform - duplicates

    return total, max(ready, 0), need_platform, review, duplicates


@app.route("/ai-importer", methods=["GET", "POST"])
def ai_importer():
    global CURRENT_TRANSACTIONS

    error = None

    if request.method == "POST":
        if not AI_AVAILABLE:
            error = "AI Reader tidak tersedia: " + AI_ERROR
        else:
            files = request.files.getlist("screenshots")
            batch_platform = request.form.get("batch_platform", "").strip()

            all_transactions = []

            try:
                for file in files:
                    if not file or not file.filename:
                        continue

                    ext = os.path.splitext(file.filename)[1].lower()
                    if ext not in [".jpg", ".jpeg", ".png", ".webp"]:
                        continue

                    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
                    filename = f"{stamp}{ext}"
                    filepath = os.path.join(UPLOAD_FOLDER, filename)
                    file.save(filepath)

                    result = read_transaction_screenshot(filepath)
                    transactions = result.get("transaksi", [])

                    for t in transactions:
                        # Platform screenshot berlaku untuk semua yang kosong
                        if not t.get("platform") and batch_platform:
                            t["platform"] = batch_platform
                            t["perlu_platform"] = False

                        # Aturan Finance HQ
                        if (t.get("kategori") or "").strip().lower() == "makanan":
                            t["catatan"] = "makanan"

                        t["tipe"] = normalize_type(t.get("tipe"))
                        t["nominal"] = normalize_nominal(t.get("nominal"))

                        # Data dasar yang kosong = review
                        if (
                            not t.get("tanggal")
                            or not t.get("keterangan")
                            or t.get("nominal") is None
                            or not t.get("tipe")
                        ):
                            t["perlu_review"] = True

                        # Platform kosong = hanya perlu platform
                        t["perlu_platform"] = not bool(t.get("platform"))

                        all_transactions.append(t)

                # Tandai duplikat terhadap DB
                db_fps = existing_fingerprints()
                seen_batch = set()

                for t in all_transactions:
                    fp = fingerprint(t)
                    if fp in db_fps or fp in seen_batch:
                        t["duplicate"] = True
                    else:
                        t["duplicate"] = False
                        seen_batch.add(fp)

                CURRENT_TRANSACTIONS = all_transactions

            except Exception as e:
                error = str(e)

    total, ready, need_platform, review, duplicates = calculate_ai()

    can_import = (
        total > 0
        and ready == total
        and review == 0
        and need_platform == 0
        and duplicates == 0
    )

    body = render_template_string(
        AI_HTML,
        transactions=CURRENT_TRANSACTIONS,
        total=total,
        ready=ready,
        need_platform=need_platform,
        review=review,
        duplicates=duplicates,
        can_import=can_import,
        platforms=PLATFORMS,
        types=TYPES,
        ai_available=AI_AVAILABLE,
        ai_error=AI_ERROR if not AI_AVAILABLE else error
    )

    if error and AI_AVAILABLE:
        body = f'<div class="card"><div class="danger">❌ {error}</div></div>' + body

    return page("AI Finance Importer", body)


@app.route("/ai-importer/save-review", methods=["POST"])
def ai_save_review():
    global CURRENT_TRANSACTIONS

    updated = []

    for i, old in enumerate(CURRENT_TRANSACTIONS):
        if request.form.get(f"delete_{i}") == "1":
            continue

        t = old.copy()

        t["tanggal"] = request.form.get(f"tanggal_{i}", "").strip() or None
        t["keterangan"] = request.form.get(f"keterangan_{i}", "").strip() or None
        t["kategori"] = request.form.get(f"kategori_{i}", "").strip() or None
        t["catatan"] = request.form.get(f"catatan_{i}", "").strip() or None
        t["nominal"] = normalize_nominal(request.form.get(f"nominal_{i}", ""))
        t["tipe"] = normalize_type(request.form.get(f"tipe_{i}", ""))
        t["platform"] = request.form.get(f"platform_{i}", "").strip() or None

        if (t.get("kategori") or "").lower() == "makanan":
            t["catatan"] = "makanan"

        t["perlu_platform"] = not bool(t.get("platform"))

        if (
            not t.get("tanggal")
            or not t.get("keterangan")
            or t.get("nominal") is None
            or not t.get("tipe")
        ):
            t["perlu_review"] = True
        else:
            t["perlu_review"] = False
            t["alasan_review"] = ""

        updated.append(t)

    # Cek duplicate lagi setelah user edit
    db_fps = existing_fingerprints()
    seen = set()

    for t in updated:
        fp = fingerprint(t)
        t["duplicate"] = fp in db_fps or fp in seen
        if not t["duplicate"]:
            seen.add(fp)

    CURRENT_TRANSACTIONS = updated

    return redirect(url_for("ai_importer"))


@app.route("/ai-importer/import", methods=["POST"])
def ai_import():
    global CURRENT_TRANSACTIONS

    total, ready, need_platform, review, duplicates = calculate_ai()

    if (
        total == 0
        or ready != total
        or review > 0
        or need_platform > 0
        or duplicates > 0
    ):
        flash("Import dibatalkan. Masih ada data yang belum siap.")
        return redirect(url_for("ai_importer"))

    backup = backup_database()

    try:
        conn = get_db()

        for t in CURRENT_TRANSACTIONS:
            tipe = normalize_type(t.get("tipe"))

            conn.execute("""
                INSERT INTO transaksi
                (tanggal, keterangan, kategori, platform, tipe, nominal, dari_platform, ke_platform)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                t.get("tanggal"),
                t.get("keterangan"),
                t.get("kategori"),
                t.get("platform"),
                tipe,
                t.get("nominal"),
                t.get("dari_platform"),
                t.get("ke_platform")
            ))

        conn.commit()
        conn.close()

        imported = len(CURRENT_TRANSACTIONS)
        CURRENT_TRANSACTIONS = []

        msg = f"✅ Berhasil import {imported} transaksi ke Finance HQ."
        if backup:
            msg += f" Backup dibuat: {os.path.basename(backup)}"

        flash(msg)

    except Exception as e:
        flash("❌ Import gagal: " + str(e))

    return redirect(url_for("ai_importer"))


# ============================================================
# START
# ============================================================

init_db()

if __name__ == "__main__":
    print("")
    print("==============================================")
    print("💰 FINANCE HQ + 🤖 AI IMPORTER")
    print("==============================================")
    print("Server: http://127.0.0.1:5000")
    print("Database:", DATABASE)
    print("AI Reader:", "AKTIF" if AI_AVAILABLE else "TIDAK AKTIF")
    if not AI_AVAILABLE:
        print("AI Error:", AI_ERROR)
    print("==============================================")
    print("")

    app.run(host="127.0.0.1", port=5000, debug=True)
