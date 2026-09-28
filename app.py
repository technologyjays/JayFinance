from flask import Flask, request, redirect, render_template_string
import sqlite3
import os
import json
import shutil
import hashlib
from datetime import date, datetime
from html import escape

# AI READER
try:
    from ai_reader import read_transaction_screenshot
    AI_READER_AVAILABLE = True
except Exception:
    AI_READER_AVAILABLE = False


app = Flask(__name__)

DATABASE = "finance.db"
UPLOAD_FOLDER = "ai_uploads"
BACKUP_FOLDER = "backups"
LOG_FOLDER = "logs"

os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(BACKUP_FOLDER, exist_ok=True)
os.makedirs(LOG_FOLDER, exist_ok=True)


# =========================================================
# CONFIG
# =========================================================

PLATFORMS = [
    "BCA",
    "SeaBank",
    "Mandiri",
    "Cash",
    "Bibit/Stockbit",
    "Lainnya"
]

TYPES = [
    "Pengeluaran",
    "Pemasukan",
    "Transfer"
]

CATEGORIES = [
    "Makanan & Minuman",
    "Belanja",
    "Transportasi",
    "Bensin",
    "Tagihan",
    "Hiburan",
    "Kesehatan",
    "Bunga",
    "Pengembalian",
    "Gaji",
    "Fee",
    "Lainnya"
]

# Data sementara AI importer.
# Karena ini aplikasi lokal satu user, cukup disimpan di memory.
CURRENT_AI_TRANSACTIONS = []


# =========================================================
# DATABASE
# =========================================================

def get_db():
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():

    conn = get_db()

    # -----------------------------------------------------
    # TRANSAKSI
    # -----------------------------------------------------

    conn.execute("""
        CREATE TABLE IF NOT EXISTS transaksi (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tanggal TEXT NOT NULL,
            keterangan TEXT NOT NULL,
            kategori TEXT NOT NULL,
            platform TEXT NOT NULL,
            tipe TEXT NOT NULL,
            nominal INTEGER NOT NULL,
            dari_platform TEXT,
            ke_platform TEXT
        )
    """)

    columns = [
        row["name"]
        for row in conn.execute(
            "PRAGMA table_info(transaksi)"
        ).fetchall()
    ]

    if "dari_platform" not in columns:
        conn.execute(
            "ALTER TABLE transaksi ADD COLUMN dari_platform TEXT"
        )

    if "ke_platform" not in columns:
        conn.execute(
            "ALTER TABLE transaksi ADD COLUMN ke_platform TEXT"
        )

    # -----------------------------------------------------
    # ASET
    # -----------------------------------------------------

    conn.execute("""
        CREATE TABLE IF NOT EXISTS aset (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nama TEXT NOT NULL,
            jenis TEXT NOT NULL,
            kepemilikan TEXT,
            tanggal TEXT NOT NULL,
            nilai INTEGER NOT NULL,
            keuntungan INTEGER DEFAULT 0,
            imbal_hasil REAL DEFAULT 0,
            catatan TEXT
        )
    """)

    conn.commit()
    conn.close()


init_db()


# =========================================================
# HELPER
# =========================================================

def rupiah(value):

    try:
        value = int(float(value or 0))
    except:
        value = 0

    if value < 0:
        return "-Rp " + f"{abs(value):,}".replace(",", ".")

    return "Rp " + f"{value:,}".replace(",", ".")


def parse_nominal(value):

    if value is None:
        return 0

    if isinstance(value, (int, float)):
        return int(value)

    value = str(value)

    value = (
        value
        .replace("Rp", "")
        .replace("rp", "")
        .replace(".", "")
        .replace(",", "")
        .replace(" ", "")
        .strip()
    )

    try:
        return int(float(value))
    except:
        return 0


def valid_bulan(bulan):

    try:
        datetime.strptime(bulan, "%Y-%m")
        return True
    except:
        return False


def clean_date(value):

    if not value:
        return ""

    value = str(value).strip()

    # Jika AI memberi timestamp
    if " " in value:
        value = value.split(" ")[0]

    if "T" in value:
        value = value.split("T")[0]

    return value


def make_backup():

    if not os.path.exists(DATABASE):
        return None

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    backup_path = os.path.join(
        BACKUP_FOLDER,
        f"finance_backup_{timestamp}.db"
    )

    shutil.copy2(DATABASE, backup_path)

    return backup_path


def write_log(data):

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    path = os.path.join(
        LOG_FOLDER,
        "ai_import.log"
    )

    with open(path, "a", encoding="utf-8") as f:

        f.write(
            f"\n[{timestamp}]\n"
        )

        f.write(
            json.dumps(
                data,
                ensure_ascii=False,
                indent=2
            )
        )

        f.write("\n")


def transaction_fingerprint(
    tanggal,
    keterangan,
    nominal,
    tipe,
    platform="",
    dari_platform="",
    ke_platform=""
):

    text = "|".join([
        clean_date(tanggal),
        str(keterangan or "").strip().lower(),
        str(int(nominal or 0)),
        str(tipe or "").strip().lower(),
        str(platform or "").strip().lower(),
        str(dari_platform or "").strip().lower(),
        str(ke_platform or "").strip().lower(),
    ])

    return hashlib.sha256(
        text.encode("utf-8")
    ).hexdigest()


def find_duplicate(
    tanggal,
    keterangan,
    nominal,
    tipe,
    platform="",
    dari_platform="",
    ke_platform=""
):

    conn = get_db()

    tanggal = clean_date(tanggal)

    # -----------------------------------------
    # Untuk transfer
    # -----------------------------------------

    if tipe == "Transfer":

        rows = conn.execute("""
            SELECT *
            FROM transaksi
            WHERE tanggal = ?
              AND nominal = ?
              AND tipe = 'Transfer'
        """, (
            tanggal,
            nominal
        )).fetchall()

    else:

        rows = conn.execute("""
            SELECT *
            FROM transaksi
            WHERE tanggal = ?
              AND nominal = ?
              AND tipe = ?
        """, (
            tanggal,
            nominal,
            tipe
        )).fetchall()

    conn.close()

    target_text = str(keterangan or "").strip().lower()

    for row in rows:

        existing_text = str(
            row["keterangan"] or ""
        ).strip().lower()

        # Cocok sangat kuat:
        # tanggal + nominal + tipe + platform + keterangan

        same_platform = (
            str(row["platform"] or "").lower()
            == str(platform or "").lower()
        )

        same_from = (
            str(row["dari_platform"] or "").lower()
            == str(dari_platform or "").lower()
        )

        same_to = (
            str(row["ke_platform"] or "").lower()
            == str(ke_platform or "").lower()
        )

        same_description = (
            existing_text == target_text
        )

        if tipe == "Transfer":

            if same_from and same_to:
                return row

        else:

            if same_platform and same_description:
                return row

    return None


# =========================================================
# STYLE
# =========================================================

STYLE = """
<style>

* {
    box-sizing: border-box;
}

body {
    margin: 0;
    font-family: Arial, sans-serif;
    background: #f4f6fa;
    color: #172033;
}

.navbar {
    background: #111827;
    color: white;
    padding: 16px 30px;
    display: flex;
    justify-content: space-between;
    align-items: center;
    flex-wrap: wrap;
    gap: 15px;
}

.logo {
    font-size: 21px;
    font-weight: bold;
}

.navlinks {
    display: flex;
    gap: 7px;
    flex-wrap: wrap;
}

.navlinks a {
    color: #d1d5db;
    text-decoration: none;
    padding: 8px 12px;
    border-radius: 8px;
    font-size: 14px;
}

.navlinks a:hover {
    background: #374151;
    color: white;
}

.container {
    max-width: 1500px;
    margin: 30px auto;
    padding: 0 20px;
}

.card {
    background: white;
    border-radius: 14px;
    padding: 24px;
    margin-bottom: 22px;
    box-shadow: 0 3px 12px rgba(0,0,0,.06);
}

h1, h2, h3 {
    margin-top: 0;
}

table {
    width: 100%;
    border-collapse: collapse;
}

th {
    background: #f1f3f7;
    text-align: left;
    padding: 12px;
    font-size: 13px;
}

td {
    padding: 11px 12px;
    border-bottom: 1px solid #e5e7eb;
    font-size: 14px;
}

.nominal {
    font-weight: bold;
    white-space: nowrap;
}

label {
    display: block;
    font-weight: bold;
    margin-top: 13px;
    margin-bottom: 6px;
}

input,
select {
    width: 100%;
    padding: 10px 12px;
    border: 1px solid #d1d5db;
    border-radius: 8px;
    background: white;
}

button,
.btn {
    border: 0;
    border-radius: 8px;
    padding: 10px 15px;
    cursor: pointer;
    text-decoration: none;
    display: inline-block;
    font-weight: bold;
}

button {
    background: #2563eb;
    color: white;
}

.btn-edit {
    background: #2563eb;
    color: white;
}

.btn-delete {
    background: #dc2626;
    color: white;
}

.btn-green {
    background: #16a34a;
    color: white;
}

.btn-gray {
    background: #6b7280;
    color: white;
}

.full-button {
    width: 100%;
    margin-top: 20px;
}

.badge {
    display: inline-block;
    padding: 5px 9px;
    border-radius: 999px;
    font-size: 12px;
    font-weight: bold;
}

.income {
    background: #dcfce7;
    color: #166534;
}

.expense {
    background: #fee2e2;
    color: #991b1b;
}

.transfer {
    background: #dbeafe;
    color: #1e40af;
}

.ready {
    background: #dcfce7;
    color: #166534;
}

.review {
    background: #fee2e2;
    color: #991b1b;
}

.platform {
    background: #fef3c7;
    color: #92400e;
}

.cards {
    display: grid;
    grid-template-columns: repeat(4, 1fr);
    gap: 15px;
}

.stat-card {
    background: white;
    padding: 20px;
    border-radius: 12px;
    box-shadow: 0 3px 12px rgba(0,0,0,.05);
}

.stat-title {
    color: #6b7280;
    font-size: 14px;
}

.stat-value {
    font-size: 24px;
    font-weight: bold;
    margin-top: 8px;
}

.grid {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 20px;
}

.empty {
    text-align: center;
    padding: 30px;
    color: #9ca3af;
}

.ai-info {
    background: #eff6ff;
    padding: 13px;
    border-radius: 10px;
    margin-bottom: 18px;
}

.ai-warning {
    background: #fef3c7;
    padding: 13px;
    border-radius: 10px;
    margin-bottom: 18px;
}

.ai-danger {
    background: #fee2e2;
    padding: 13px;
    border-radius: 10px;
    margin-bottom: 18px;
}

.ai-success {
    background: #dcfce7;
    padding: 13px;
    border-radius: 10px;
    margin-bottom: 18px;
}

.ai-table input,
.ai-table select {
    min-width: 110px;
    padding: 7px;
}

/* =========================================================
   TAMBAHAN: MONTH PICKER 150%
   ========================================================= */
.month-picker {
    width: 150%;
    max-width: 600px;
    min-height: 54px;
    padding: 14px 16px;
    font-size: 24px;
    font-weight: bold;
    border-radius: 10px;
}

/* =========================================================
   TAMBAHAN: GRAFIK PEMASUKAN VS PENGELUARAN
   ========================================================= */
.monthly-chart {
    display: flex;
    align-items: stretch;
    gap: 18px;
    min-height: 360px;
    padding: 20px 10px 10px;
    overflow-x: auto;
    border: 1px solid #e5e7eb;
    border-radius: 12px;
    background: #fafafa;
}

.monthly-chart-group {
    min-width: 105px;
    flex: 1 0 105px;
    display: flex;
    flex-direction: column;
    justify-content: flex-end;
    align-items: center;
}

.monthly-chart-bars {
    height: 270px;
    width: 100%;
    display: flex;
    align-items: flex-end;
    justify-content: center;
    gap: 7px;
    border-bottom: 1px solid #d1d5db;
}

.monthly-bar {
    width: 34px;
    min-height: 3px;
    border-radius: 7px 7px 0 0;
    transition: opacity .15s;
}

.monthly-bar:hover {
    opacity: .75;
}

.monthly-bar-income {
    background: #16a34a;
}

.monthly-bar-expense {
    background: #dc2626;
}

.monthly-chart-label {
    margin-top: 10px;
    font-size: 13px;
    font-weight: bold;
    white-space: nowrap;
}

.monthly-chart-values {
    display: flex;
    justify-content: center;
    gap: 6px;
    margin-top: 5px;
    font-size: 10px;
    color: #6b7280;
    white-space: nowrap;
}

.chart-legend {
    display: flex;
    gap: 20px;
    margin-bottom: 14px;
    font-size: 14px;
    font-weight: bold;
}

.chart-legend-item {
    display: flex;
    align-items: center;
    gap: 7px;
}

.chart-legend-dot {
    width: 13px;
    height: 13px;
    border-radius: 3px;
}

@media (max-width: 900px) {

    .cards {
        grid-template-columns: 1fr 1fr;
    }

    .grid {
        grid-template-columns: 1fr;
    }
}

@media (max-width: 600px) {

    .cards {
        grid-template-columns: 1fr;
    }

    .container {
        padding: 0 10px;
    }
}

</style>
"""


# =========================================================
# NAVBAR
# =========================================================

def navbar():

    return """
    <div class="navbar">

        <div class="logo">
            💰 Finance HQ
        </div>

        <div class="navlinks">

            <a href="/">🏠 Dashboard</a>

            <a href="/transaksi">
                💳 Transaksi
            </a>

            <a href="/pemasukan">
                💰 Pemasukan
            </a>

            <a href="/transfer">
                🔄 Transfer
            </a>

            <a href="/aset">
                💼 Aset
            </a>

            <a href="/laporan">
                📊 Laporan
            </a>

            <a href="/ai-importer">
                🤖 AI Importer
            </a>

        </div>

    </div>
    """


def page(title, body):

    return f"""
    <!DOCTYPE html>

    <html>

    <head>

        <meta charset="UTF-8">

        <meta name="viewport"
              content="width=device-width, initial-scale=1">

        <title>{escape(title)}</title>

        {STYLE}

    </head>

    <body>

        {navbar()}

        <div class="container">

            {body}

        </div>

    </body>

    </html>
    """


# =========================================================
# DASHBOARD
# =========================================================

@app.route("/")
def dashboard():

    conn = get_db()

    month_rows = conn.execute("""
        SELECT bulan
        FROM (
            SELECT DISTINCT substr(tanggal,1,7) AS bulan
            FROM transaksi

            UNION

            SELECT DISTINCT substr(tanggal,1,7) AS bulan
            FROM aset
        )

        WHERE bulan IS NOT NULL
          AND bulan <> ''

        ORDER BY bulan DESC
    """).fetchall()

    months = [
        row["bulan"]
        for row in month_rows
    ]

    bulan = request.args.get("bulan", "")

    if bulan not in months:

        bulan = (
            months[0]
            if months
            else date.today().strftime("%Y-%m")
        )

    transaksi_data = conn.execute("""
        SELECT *
        FROM transaksi
        WHERE tanggal LIKE ?
        ORDER BY tanggal DESC, id DESC
    """, (
        bulan + "%",
    )).fetchall()

    pemasukan = conn.execute("""
        SELECT COALESCE(SUM(nominal),0)
        FROM transaksi
        WHERE tanggal LIKE ?
          AND tipe='Pemasukan'
    """, (
        bulan + "%",
    )).fetchone()[0]

    pengeluaran = conn.execute("""
        SELECT COALESCE(SUM(nominal),0)
        FROM transaksi
        WHERE tanggal LIKE ?
          AND tipe='Pengeluaran'
    """, (
        bulan + "%",
    )).fetchone()[0]

    transfer = conn.execute("""
        SELECT COALESCE(SUM(nominal),0)
        FROM transaksi
        WHERE tanggal LIKE ?
          AND tipe='Transfer'
    """, (
        bulan + "%",
    )).fetchone()[0]

    aset_data = conn.execute("""
        SELECT *
        FROM aset
        WHERE tanggal LIKE ?
        ORDER BY tanggal DESC, id DESC
    """, (
        bulan + "%",
    )).fetchall()

    total_aset = sum(
        row["nilai"] or 0
        for row in aset_data
    )

    saldo = pemasukan - pengeluaran

    # -----------------------------------------------------
    # PLATFORM MOVEMENT
    # -----------------------------------------------------

    platform_data = {}

    for platform in PLATFORMS:

        perubahan = 0

        for row in transaksi_data:

            if row["tipe"] == "Pemasukan":

                if row["platform"] == platform:
                    perubahan += row["nominal"] or 0

            elif row["tipe"] == "Pengeluaran":

                if row["platform"] == platform:
                    perubahan -= row["nominal"] or 0

            elif row["tipe"] == "Transfer":

                if row["dari_platform"] == platform:
                    perubahan -= row["nominal"] or 0

                if row["ke_platform"] == platform:
                    perubahan += row["nominal"] or 0

        platform_data[platform] = perubahan

    # -----------------------------------------------------
    # EXPENSE CATEGORY
    # -----------------------------------------------------

    kategori_data = conn.execute("""
        SELECT kategori, SUM(nominal) AS total
        FROM transaksi
        WHERE tanggal LIKE ?
          AND tipe='Pengeluaran'
        GROUP BY kategori
        ORDER BY total DESC
    """, (
        bulan + "%",
    )).fetchall()

    # -----------------------------------------------------
    # GRAFIK PEMASUKAN VS PENGELUARAN PER BULAN
    # -----------------------------------------------------
    monthly_data = conn.execute("""
        SELECT
            substr(tanggal,1,7) AS bulan,
            COALESCE(SUM(
                CASE
                    WHEN tipe='Pemasukan' THEN nominal
                    ELSE 0
                END
            ),0) AS pemasukan,
            COALESCE(SUM(
                CASE
                    WHEN tipe='Pengeluaran' THEN nominal
                    ELSE 0
                END
            ),0) AS pengeluaran
        FROM transaksi
        WHERE tanggal IS NOT NULL
          AND tanggal <> ''
        GROUP BY substr(tanggal,1,7)
        ORDER BY bulan ASC
    """).fetchall()

    chart_max = max(
        [
            max(
                int(row["pemasukan"] or 0),
                int(row["pengeluaran"] or 0)
            )
            for row in monthly_data
        ] or [1]
    )

    chart_groups = ""

    for row in monthly_data:
        pemasukan_bulan = int(row["pemasukan"] or 0)
        pengeluaran_bulan = int(row["pengeluaran"] or 0)

        pemasukan_height = (
            max(3, round(pemasukan_bulan / chart_max * 100))
            if chart_max
            else 3
        )

        pengeluaran_height = (
            max(3, round(pengeluaran_bulan / chart_max * 100))
            if chart_max
            else 3
        )

        chart_groups += f"""
        <div class="monthly-chart-group">
            <div class="monthly-chart-bars">
                <div
                    class="monthly-bar monthly-bar-income"
                    style="height:{pemasukan_height}%;"
                    title="Pemasukan {escape(row['bulan'])}: {rupiah(pemasukan_bulan)}"
                ></div>

                <div
                    class="monthly-bar monthly-bar-expense"
                    style="height:{pengeluaran_height}%;"
                    title="Pengeluaran {escape(row['bulan'])}: {rupiah(pengeluaran_bulan)}"
                ></div>
            </div>

            <div class="monthly-chart-label">
                {escape(row["bulan"])}
            </div>

            <div class="monthly-chart-values">
                <span>↑ {rupiah(pemasukan_bulan)}</span>
                <span>↓ {rupiah(pengeluaran_bulan)}</span>
            </div>
        </div>
        """

    if not chart_groups:
        chart_groups = """
        <div class="empty" style="width:100%;">
            Belum ada data pemasukan/pengeluaran untuk dibuat grafik.
        </div>
        """

    conn.close()

    options = ""

    for m in months:

        selected = "selected" if m == bulan else ""

        options += (
            f'<option value="{escape(m)}" {selected}>'
            f'{escape(m)}'
            f'</option>'
        )

    rows = ""

    for row in transaksi_data:

        if row["tipe"] == "Transfer":

            platform = (
                escape(row["dari_platform"] or "-")
                + " → "
                + escape(row["ke_platform"] or "-")
            )

            badge = "transfer"

        else:

            platform = escape(
                row["platform"] or "-"
            )

            badge = (
                "income"
                if row["tipe"] == "Pemasukan"
                else "expense"
            )

        rows += f"""
        <tr>

            <td>{escape(row["tanggal"])}</td>

            <td>{escape(row["keterangan"])}</td>

            <td>{escape(row["kategori"])}</td>

            <td>{platform}</td>

            <td>
                <span class="badge {badge}">
                    {escape(row["tipe"])}
                </span>
            </td>

            <td class="nominal">
                {rupiah(row["nominal"])}
            </td>

        </tr>
        """

    if not rows:

        rows = """
        <tr>
            <td colspan="6" class="empty">
                Belum ada transaksi bulan ini.
            </td>
        </tr>
        """

    platform_rows = ""

    for platform, value in platform_data.items():

        platform_rows += f"""
        <tr>

            <td>{escape(platform)}</td>

            <td class="nominal">
                {rupiah(value)}
            </td>

        </tr>
        """

    category_rows = ""

    for row in kategori_data:

        category_rows += f"""
        <tr>

            <td>
                {escape(row["kategori"] or "-")}
            </td>

            <td class="nominal">
                {rupiah(row["total"])}
            </td>

        </tr>
        """

    body = f"""

    <div class="card">

        <h1>🏠 Dashboard</h1>

        <label>Pilih Bulan</label>

        <select
            class="month-picker"
            onchange="location.href='/?bulan=' + this.value"
        >

            {options}

        </select>

    </div>


    <div class="cards">

        <div class="stat-card">

            <div class="stat-title">
                Pemasukan
            </div>

            <div class="stat-value">
                {rupiah(pemasukan)}
            </div>

        </div>


        <div class="stat-card">

            <div class="stat-title">
                Pengeluaran
            </div>

            <div class="stat-value">
                {rupiah(pengeluaran)}
            </div>

        </div>


        <div class="stat-card">

            <div class="stat-title">
                Saldo Bersih
            </div>

            <div class="stat-value">
                {rupiah(saldo)}
            </div>

        </div>


        <div class="stat-card">

            <div class="stat-title">
                Total Transfer
            </div>

            <div class="stat-value">
                {rupiah(transfer)}
            </div>

        </div>

    </div>


    <div class="cards" style="margin-top:20px;">

        <div class="stat-card">

            <div class="stat-title">
                Total Aset
            </div>

            <div class="stat-value">
                {rupiah(total_aset)}
            </div>

        </div>


        <div class="stat-card">

            <div class="stat-title">
                Jumlah Transaksi
            </div>

            <div class="stat-value">
                {len(transaksi_data)}
            </div>

        </div>

    </div>


    <div class="card" style="margin-top:22px;">

        <h2>
            📊 Grafik Pemasukan vs Pengeluaran per Bulan
        </h2>

        <div class="chart-legend">
            <div class="chart-legend-item">
                <span
                    class="chart-legend-dot"
                    style="background:#16a34a;"
                ></span>
                Pemasukan
            </div>

            <div class="chart-legend-item">
                <span
                    class="chart-legend-dot"
                    style="background:#dc2626;"
                ></span>
                Pengeluaran
            </div>
        </div>

        <div class="monthly-chart">
            {chart_groups}
        </div>

    </div>


    <div class="grid" style="margin-top:22px;">

        <div class="card">

            <h2>
                💳 Pergerakan per Platform
            </h2>

            <table>

                <thead>
                    <tr>
                        <th>Platform</th>
                        <th>Perubahan</th>
                    </tr>
                </thead>

                <tbody>

                    {platform_rows}

                </tbody>

            </table>

        </div>


        <div class="card">

            <h2>
                📊 Pengeluaran per Kategori
            </h2>

            <table>

                <thead>
                    <tr>
                        <th>Kategori</th>
                        <th>Total</th>
                    </tr>
                </thead>

                <tbody>

                    {category_rows}

                </tbody>

            </table>

        </div>

    </div>


    <div class="card">

        <h2>
            💳 Transaksi {escape(bulan)}
        </h2>

        <div style="overflow-x:auto;">

            <table>

                <thead>

                    <tr>
                        <th>Tanggal</th>
                        <th>Keterangan</th>
                        <th>Kategori</th>
                        <th>Platform</th>
                        <th>Tipe</th>
                        <th>Nominal</th>
                    </tr>

                </thead>

                <tbody>

                    {rows}

                </tbody>

            </table>

        </div>

    </div>

    """

    return page(
        "Finance HQ - Dashboard",
        body
    )


# =========================================================
# TRANSAKSI
# =========================================================

@app.route("/transaksi")
def transaksi_page():

    conn = get_db()

    month_rows = conn.execute("""
        SELECT DISTINCT substr(tanggal,1,7) AS bulan
        FROM transaksi
        WHERE tanggal IS NOT NULL
          AND tanggal <> ''
        ORDER BY bulan DESC
    """).fetchall()

    months = [
        row["bulan"]
        for row in month_rows
    ]

    bulan = request.args.get(
        "bulan",
        ""
    )

    if bulan not in months:

        bulan = (
            months[0]
            if months
            else date.today().strftime("%Y-%m")
        )

    data = conn.execute("""
        SELECT *
        FROM transaksi
        WHERE tanggal LIKE ?
        ORDER BY tanggal DESC, id DESC
    """, (
        bulan + "%",
    )).fetchall()

    conn.close()

    options = ""

    for m in months:

        selected = (
            "selected"
            if m == bulan
            else ""
        )

        options += (
            f'<option value="{escape(m)}" {selected}>'
            f'{escape(m)}'
            f'</option>'
        )

    rows = ""

    for row in data:

        if row["tipe"] == "Transfer":

            platform = (
                escape(row["dari_platform"] or "-")
                + " → "
                + escape(row["ke_platform"] or "-")
            )

            badge = "transfer"

        else:

            platform = escape(
                row["platform"] or "-"
            )

            badge = (
                "income"
                if row["tipe"] == "Pemasukan"
                else "expense"
            )

        rows += f"""
        <tr>

            <td>{escape(row["tanggal"])}</td>

            <td>{escape(row["keterangan"])}</td>

            <td>{escape(row["kategori"])}</td>

            <td>{platform}</td>

            <td>
                <span class="badge {badge}">
                    {escape(row["tipe"])}
                </span>
            </td>

            <td class="nominal">
                {rupiah(row["nominal"])}
            </td>

            <td>

                <a
                    class="btn btn-edit"
                    href="/edit/{row["id"]}?bulan={bulan}"
                >
                    Edit
                </a>

                <a
                    class="btn btn-delete"
                    href="/hapus/{row["id"]}?bulan={bulan}"
                    onclick="return confirm('Hapus transaksi ini?')"
                >
                    Hapus
                </a>

            </td>

        </tr>
        """

    if not rows:

        rows = """
        <tr>
            <td colspan="7" class="empty">
                Belum ada transaksi.
            </td>
        </tr>
        """

    body = f"""

    <div class="card">

        <h1>💳 Transaksi</h1>

        <label>Pilih Bulan</label>

        <select
            class="month-picker"
            onchange="location.href='/transaksi?bulan=' + this.value"
        >

            {options}

        </select>

        <br><br>

        <a
            class="btn btn-green"
            href="/tambah"
        >
            + Tambah Transaksi
        </a>

    </div>


    <div class="card">

        <div style="overflow-x:auto;">

            <table>

                <thead>

                    <tr>

                        <th>Tanggal</th>
                        <th>Keterangan</th>
                        <th>Kategori</th>
                        <th>Platform</th>
                        <th>Tipe</th>
                        <th>Nominal</th>
                        <th>Aksi</th>

                    </tr>

                </thead>

                <tbody>

                    {rows}

                </tbody>

                <tfoot>

                    <tr>

                        <th colspan="5">
                            TOTAL TRANSAKSI
                        </th>

                        <th class="nominal">
                            {rupiah(sum(
                                row["nominal"] or 0
                                for row in data
                            ))}
                        </th>

                        <th></th>

                    </tr>

                </tfoot>

            </table>

        </div>

    </div>

    """

    return page(
        "Finance HQ - Transaksi",
        body
    )


# =========================================================
# TAMBAH TRANSAKSI
# =========================================================

@app.route("/tambah", methods=["GET", "POST"])
def tambah():

    if request.method == "POST":

        tanggal = request.form.get(
            "tanggal",
            ""
        )

        keterangan = request.form.get(
            "keterangan",
            ""
        ).strip()

        kategori = request.form.get(
            "kategori",
            "Lainnya"
        )

        tipe = request.form.get(
            "tipe",
            "Pengeluaran"
        )

        platform = request.form.get(
            "platform",
            ""
        )

        dari_platform = request.form.get(
            "dari_platform",
            ""
        )

        ke_platform = request.form.get(
            "ke_platform",
            ""
        )

        nominal = parse_nominal(
            request.form.get(
                "nominal",
                "0"
            )
        )

        if tipe == "Transfer":

            platform = ""

        else:

            dari_platform = ""
            ke_platform = ""

        if kategori == "Makanan & Minuman":
            pass

        conn = get_db()

        conn.execute("""
            INSERT INTO transaksi (
                tanggal,
                keterangan,
                kategori,
                platform,
                tipe,
                nominal,
                dari_platform,
                ke_platform
            )

            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            tanggal,
            keterangan,
            kategori,
            platform,
            tipe,
            nominal,
            dari_platform,
            ke_platform
        ))

        conn.commit()
        conn.close()

        return redirect("/transaksi")

    body = f"""

    <div class="card">

        <h1>➕ Tambah Transaksi</h1>

        <form method="POST">

            <label>Tanggal</label>

            <input
                type="date"
                name="tanggal"
                value="{date.today().isoformat()}"
                required
            >


            <label>Keterangan</label>

            <input
                name="keterangan"
                required
            >


            <label>Kategori</label>

            <select name="kategori">

                {
                    "".join(
                        f'<option value="{escape(c)}">{escape(c)}</option>'
                        for c in CATEGORIES
                    )
                }

            </select>


            <label>Tipe</label>

            <select
                name="tipe"
                id="tipe"
                onchange="ubahTipe()"
            >

                <option value="Pengeluaran">
                    Pengeluaran
                </option>

                <option value="Pemasukan">
                    Pemasukan
                </option>

                <option value="Transfer">
                    Transfer
                </option>

            </select>


            <div id="normal">

                <label>Platform</label>

                <select name="platform">

                    {
                        "".join(
                            f'<option value="{escape(p)}">{escape(p)}</option>'
                            for p in PLATFORMS
                        )
                    }

                </select>

            </div>


            <div
                id="transfer"
                style="display:none;"
            >

                <label>Dari</label>

                <select name="dari_platform">

                    {
                        "".join(
                            f'<option value="{escape(p)}">{escape(p)}</option>'
                            for p in PLATFORMS
                        )
                    }

                </select>


                <label>Ke</label>

                <select name="ke_platform">

                    {
                        "".join(
                            f'<option value="{escape(p)}">{escape(p)}</option>'
                            for p in PLATFORMS
                        )
                    }

                </select>

            </div>


            <label>Nominal</label>

            <input
                type="number"
                name="nominal"
                min="1"
                required
            >


            <button
                class="full-button"
                type="submit"
            >
                Simpan Transaksi
            </button>

        </form>

    </div>


    <script>

    function ubahTipe() {{

        const tipe =
            document.getElementById("tipe").value;

        const normal =
            document.getElementById("normal");

        const transfer =
            document.getElementById("transfer");

        if (tipe === "Transfer") {{

            normal.style.display = "none";
            transfer.style.display = "block";

        }} else {{

            normal.style.display = "block";
            transfer.style.display = "none";

        }}

    }}

    </script>

    """

    return page(
        "Tambah Transaksi",
        body
    )


# =========================================================
# EDIT TRANSAKSI
# =========================================================

@app.route("/edit/<int:id>", methods=["GET", "POST"])
def edit(id):

    conn = get_db()

    row = conn.execute("""
        SELECT *
        FROM transaksi
        WHERE id=?
    """, (
        id,
    )).fetchone()

    if not row:

        conn.close()

        return "Transaksi tidak ditemukan.", 404

    if request.method == "POST":

        tanggal = request.form.get(
            "tanggal",
            ""
        )

        keterangan = request.form.get(
            "keterangan",
            ""
        ).strip()

        kategori = request.form.get(
            "kategori",
            "Lainnya"
        )

        tipe = request.form.get(
            "tipe",
            "Pengeluaran"
        )

        platform = request.form.get(
            "platform",
            ""
        )

        dari_platform = request.form.get(
            "dari_platform",
            ""
        )

        ke_platform = request.form.get(
            "ke_platform",
            ""
        )

        nominal = parse_nominal(
            request.form.get(
                "nominal",
                "0"
            )
        )

        if tipe != "Transfer":

            dari_platform = ""
            ke_platform = ""

        else:

            platform = ""

        conn.execute("""
            UPDATE transaksi

            SET
                tanggal=?,
                keterangan=?,
                kategori=?,
                platform=?,
                tipe=?,
                nominal=?,
                dari_platform=?,
                ke_platform=?

            WHERE id=?
        """, (
            tanggal,
            keterangan,
            kategori,
            platform,
            tipe,
            nominal,
            dari_platform,
            ke_platform,
            id
        ))

        conn.commit()

        conn.close()

        bulan = request.args.get(
            "bulan",
            tanggal[:7]
        )

        return redirect(
            "/transaksi?bulan=" + bulan
        )

    conn.close()

    normal_display = (
        "none"
        if row["tipe"] == "Transfer"
        else "block"
    )

    transfer_display = (
        "block"
        if row["tipe"] == "Transfer"
        else "none"
    )

    category_options = ""

    for c in CATEGORIES:

        selected = (
            "selected"
            if c == row["kategori"]
            else ""
        )

        category_options += (
            f'<option value="{escape(c)}" {selected}>'
            f'{escape(c)}'
            f'</option>'
        )

    type_options = ""

    for t in TYPES:

        selected = (
            "selected"
            if t == row["tipe"]
            else ""
        )

        type_options += (
            f'<option value="{escape(t)}" {selected}>'
            f'{escape(t)}'
            f'</option>'
        )

    platform_options = ""

    for p in PLATFORMS:

        selected = (
            "selected"
            if p == row["platform"]
            else ""
        )

        platform_options += (
            f'<option value="{escape(p)}" {selected}>'
            f'{escape(p)}'
            f'</option>'
        )

    from_options = ""

    for p in PLATFORMS:

        selected = (
            "selected"
            if p == row["dari_platform"]
            else ""
        )

        from_options += (
            f'<option value="{escape(p)}" {selected}>'
            f'{escape(p)}'
            f'</option>'
        )

    to_options = ""

    for p in PLATFORMS:

        selected = (
            "selected"
            if p == row["ke_platform"]
            else ""
        )

        to_options += (
            f'<option value="{escape(p)}" {selected}>'
            f'{escape(p)}'
            f'</option>'
        )

    body = f"""

    <div class="card">

        <h1>✏️ Edit Transaksi</h1>

        <form method="POST">

            <label>Tanggal</label>

            <input
                type="date"
                name="tanggal"
                value="{escape(row["tanggal"][:10])}"
                required
            >


            <label>Keterangan</label>

            <input
                name="keterangan"
                value="{escape(row["keterangan"])}"
                required
            >


            <label>Kategori</label>

            <select name="kategori">

                {category_options}

            </select>


            <label>Tipe</label>

            <select
                name="tipe"
                id="tipe"
                onchange="ubahTipe()"
            >

                {type_options}

            </select>


            <div
                id="normal"
                style="display:{normal_display};"
            >

                <label>Platform</label>

                <select name="platform">

                    {platform_options}

                </select>

            </div>


            <div
                id="transfer"
                style="display:{transfer_display};"
            >

                <label>Dari</label>

                <select name="dari_platform">

                    {from_options}

                </select>


                <label>Ke</label>

                <select name="ke_platform">

                    {to_options}

                </select>

            </div>


            <label>Nominal</label>

            <input
                type="number"
                name="nominal"
                value="{row["nominal"]}"
                min="1"
                required
            >


            <button
                class="full-button"
                type="submit"
            >
                Simpan Perubahan
            </button>

        </form>

    </div>


    <script>

    function ubahTipe() {{

        const tipe =
            document.getElementById("tipe").value;

        const normal =
            document.getElementById("normal");

        const transfer =
            document.getElementById("transfer");

        if (tipe === "Transfer") {{

            normal.style.display = "none";
            transfer.style.display = "block";

        }} else {{

            normal.style.display = "block";
            transfer.style.display = "none";

        }}

    }}

    </script>

    """

    return page(
        "Edit Transaksi",
        body
    )


# =========================================================
# HAPUS
# =========================================================

@app.route("/hapus/<int:id>")
def hapus(id):

    conn = get_db()

    conn.execute("""
        DELETE FROM transaksi
        WHERE id=?
    """, (
        id,
    ))

    conn.commit()
    conn.close()

    bulan = request.args.get(
        "bulan",
        date.today().strftime("%Y-%m")
    )

    return redirect(
        "/transaksi?bulan=" + bulan
    )


# =========================================================
# PEMASUKAN
# =========================================================

@app.route("/pemasukan")
def pemasukan_page():

    conn = get_db()

    months = conn.execute("""
        SELECT DISTINCT substr(tanggal,1,7) AS bulan
        FROM transaksi
        WHERE tipe='Pemasukan'
        ORDER BY bulan DESC
    """).fetchall()

    month_values = [
        row["bulan"]
        for row in months
    ]

    bulan = request.args.get(
        "bulan",
        ""
    )

    if bulan not in month_values:

        bulan = (
            month_values[0]
            if month_values
            else date.today().strftime("%Y-%m")
        )

    data = conn.execute("""
        SELECT *
        FROM transaksi

        WHERE tanggal LIKE ?
          AND tipe='Pemasukan'

        ORDER BY tanggal DESC, id DESC
    """, (
        bulan + "%",
    )).fetchall()

    total = sum(
        row["nominal"] or 0
        for row in data
    )

    conn.close()

    options = ""

    for m in month_values:

        selected = (
            "selected"
            if m == bulan
            else ""
        )

        options += (
            f'<option value="{escape(m)}" {selected}>'
            f'{escape(m)}'
            f'</option>'
        )

    rows = ""

    for row in data:

        rows += f"""
        <tr>

            <td>{escape(row["tanggal"])}</td>

            <td>{escape(row["keterangan"])}</td>

            <td>{escape(row["kategori"])}</td>

            <td>{escape(row["platform"])}</td>

            <td class="nominal">
                {rupiah(row["nominal"])}
            </td>

            <td>

                <a
                    class="btn btn-edit"
                    href="/edit/{row["id"]}?bulan={bulan}"
                >
                    Edit
                </a>

                <a
                    class="btn btn-delete"
                    href="/hapus/{row["id"]}?bulan={bulan}"
                    onclick="return confirm('Hapus pemasukan?')"
                >
                    Hapus
                </a>

            </td>

        </tr>
        """

    if not rows:

        rows = """
        <tr>
            <td colspan="6" class="empty">
                Belum ada pemasukan.
            </td>
        </tr>
        """

    body = f"""

    <div class="card">

        <h1>💰 Pemasukan</h1>

        <label>Pilih Bulan</label>

        <select
            class="month-picker"
            onchange="location.href='/pemasukan?bulan=' + this.value"
        >

            {options}

        </select>

    </div>


    <div class="card">

        <h2>
            Total Pemasukan:
            {rupiah(total)}
        </h2>

        <div style="overflow-x:auto;">

            <table>

                <thead>

                    <tr>

                        <th>Tanggal</th>
                        <th>Keterangan</th>
                        <th>Kategori</th>
                        <th>Platform</th>
                        <th>Nominal</th>
                        <th>Aksi</th>

                    </tr>

                </thead>

                <tbody>

                    {rows}

                </tbody>

                <tfoot>

                    <tr>

                        <th colspan="4">
                            TOTAL
                        </th>

                        <th class="nominal">
                            {rupiah(total)}
                        </th>

                        <th></th>

                    </tr>

                </tfoot>

            </table>

        </div>

    </div>

    """

    return page(
        "Finance HQ - Pemasukan",
        body
    )


# =========================================================
# TRANSFER
# =========================================================

@app.route("/transfer")
def transfer_page():

    conn = get_db()

    months = conn.execute("""
        SELECT DISTINCT substr(tanggal,1,7) AS bulan
        FROM transaksi
        WHERE tipe='Transfer'
        ORDER BY bulan DESC
    """).fetchall()

    month_values = [
        row["bulan"]
        for row in months
    ]

    bulan = request.args.get(
        "bulan",
        ""
    )

    if bulan not in month_values:

        bulan = (
            month_values[0]
            if month_values
            else date.today().strftime("%Y-%m")
        )

    data = conn.execute("""
        SELECT *
        FROM transaksi

        WHERE tanggal LIKE ?
          AND tipe='Transfer'

        ORDER BY tanggal DESC, id DESC
    """, (
        bulan + "%",
    )).fetchall()

    total = sum(
        row["nominal"] or 0
        for row in data
    )

    conn.close()

    options = ""

    for m in month_values:

        selected = (
            "selected"
            if m == bulan
            else ""
        )

        options += (
            f'<option value="{escape(m)}" {selected}>'
            f'{escape(m)}'
            f'</option>'
        )

    rows = ""

    for row in data:

        rows += f"""
        <tr>

            <td>{escape(row["tanggal"])}</td>

            <td>{escape(row["keterangan"])}</td>

            <td>{escape(row["dari_platform"] or "-")}</td>

            <td>{escape(row["ke_platform"] or "-")}</td>

            <td class="nominal">
                {rupiah(row["nominal"])}
            </td>

            <td>

                <a
                    class="btn btn-edit"
                    href="/edit/{row["id"]}?bulan={bulan}"
                >
                    Edit
                </a>

                <a
                    class="btn btn-delete"
                    href="/hapus/{row["id"]}?bulan={bulan}"
                    onclick="return confirm('Hapus transfer?')"
                >
                    Hapus
                </a>

            </td>

        </tr>
        """

    if not rows:

        rows = """
        <tr>
            <td colspan="6" class="empty">
                Belum ada transfer.
            </td>
        </tr>
        """

    body = f"""

    <div class="card">

        <h1>🔄 Transfer Antar Rekening</h1>

        <label>Pilih Bulan</label>

        <select
            class="month-picker"
            onchange="location.href='/transfer?bulan=' + this.value"
        >

            {options}

        </select>

    </div>


    <div class="card">

        <div style="overflow-x:auto;">

            <table>

                <thead>

                    <tr>

                        <th>Tanggal</th>
                        <th>Keterangan</th>
                        <th>Dari</th>
                        <th>Ke</th>
                        <th>Nominal</th>
                        <th>Aksi</th>

                    </tr>

                </thead>

                <tbody>

                    {rows}

                </tbody>

                <tfoot>

                    <tr>

                        <th colspan="4">
                            TOTAL TRANSFER
                        </th>

                        <th class="nominal">
                            {rupiah(total)}
                        </th>

                        <th></th>

                    </tr>

                </tfoot>

            </table>

        </div>

    </div>

    """

    return page(
        "Finance HQ - Transfer",
        body
    )


# =========================================================
# ASET
# =========================================================

@app.route("/aset")
def aset_page():

    conn = get_db()

    months = conn.execute("""
        SELECT DISTINCT substr(tanggal,1,7) AS bulan
        FROM aset
        ORDER BY bulan DESC
    """).fetchall()

    month_values = [
        row["bulan"]
        for row in months
    ]

    bulan = request.args.get(
        "bulan",
        ""
    )

    if bulan not in month_values:

        bulan = (
            month_values[0]
            if month_values
            else date.today().strftime("%Y-%m")
        )

    data = conn.execute("""
        SELECT *
        FROM aset

        WHERE tanggal LIKE ?

        ORDER BY tanggal DESC, id DESC
    """, (
        bulan + "%",
    )).fetchall()

    total = sum(
        row["nilai"] or 0
        for row in data
    )

    conn.close()

    options = ""

    for m in month_values:

        selected = (
            "selected"
            if m == bulan
            else ""
        )

        options += (
            f'<option value="{escape(m)}" {selected}>'
            f'{escape(m)}'
            f'</option>'
        )

    rows = ""

    for row in data:

        rows += f"""
        <tr>

            <td>{escape(row["nama"])}</td>

            <td>{escape(row["jenis"])}</td>

            <td>{escape(row["kepemilikan"] or "-")}</td>

            <td>{escape(row["tanggal"])}</td>

            <td class="nominal">
                {rupiah(row["nilai"])}
            </td>

            <td class="nominal">
                {rupiah(row["keuntungan"])}
            </td>

            <td>
                {row["imbal_hasil"] or 0:.2f}%
            </td>

            <td>{escape(row["catatan"] or "")}</td>

        </tr>
        """

    if not rows:

        rows = """
        <tr>
            <td colspan="8" class="empty">
                Belum ada aset.
            </td>
        </tr>
        """

    body = f"""

    <div class="card">

        <h1>💼 Aset</h1>

        <label>Pilih Bulan</label>

        <select
            class="month-picker"
            onchange="location.href='/aset?bulan=' + this.value"
        >

            {options}

        </select>

    </div>


    <div class="card">

        <a
            class="btn btn-green"
            href="/aset/tambah"
        >
            + Tambah Aset
        </a>

        <br><br>

        <h2>
            Total Aset:
            {rupiah(total)}
        </h2>

        <div style="overflow-x:auto;">

            <table>

                <thead>

                    <tr>

                        <th>Nama</th>
                        <th>Jenis</th>
                        <th>Kepemilikan</th>
                        <th>Tanggal</th>
                        <th>Nilai</th>
                        <th>Keuntungan</th>
                        <th>Imbal Hasil</th>
                        <th>Catatan</th>

                    </tr>

                </thead>

                <tbody>

                    {rows}

                </tbody>

            </table>

        </div>

    </div>

    """

    return page(
        "Finance HQ - Aset",
        body
    )


# =========================================================
# TAMBAH ASET
# =========================================================

@app.route("/aset/tambah", methods=["GET", "POST"])
def tambah_aset():

    if request.method == "POST":

        nama = request.form.get(
            "nama",
            ""
        ).strip()

        jenis = request.form.get(
            "jenis",
            ""
        ).strip()

        kepemilikan = request.form.get(
            "kepemilikan",
            ""
        ).strip()

        tanggal = request.form.get(
            "tanggal",
            ""
        )

        nilai = parse_nominal(
            request.form.get(
                "nilai",
                "0"
            )
        )

        keuntungan = parse_nominal(
            request.form.get(
                "keuntungan",
                "0"
            )
        )

        try:

            imbal_hasil = float(
                request.form.get(
                    "imbal_hasil",
                    "0"
                )
            )

        except:

            imbal_hasil = 0

        catatan = request.form.get(
            "catatan",
            ""
        ).strip()

        conn = get_db()

        conn.execute("""
            INSERT INTO aset (
                nama,
                jenis,
                kepemilikan,
                tanggal,
                nilai,
                keuntungan,
                imbal_hasil,
                catatan
            )

            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            nama,
            jenis,
            kepemilikan,
            tanggal,
            nilai,
            keuntungan,
            imbal_hasil,
            catatan
        ))

        conn.commit()
        conn.close()

        return redirect("/aset")

    body = """

    <div class="card">

        <h1>➕ Tambah Aset</h1>

        <form method="POST">

            <label>Nama</label>

            <input
                name="nama"
                required
            >


            <label>Jenis</label>

            <input
                name="jenis"
                placeholder="Contoh: Saham / Reksadana"
                required
            >


            <label>Kepemilikan</label>

            <input
                name="kepemilikan"
                placeholder="Pribadi"
            >


            <label>Tanggal</label>

            <input
                type="date"
                name="tanggal"
                value=""
                required
            >


            <label>Nilai</label>

            <input
                type="number"
                name="nilai"
                min="0"
                required
            >


            <label>Keuntungan</label>

            <input
                type="number"
                name="keuntungan"
                value="0"
            >


            <label>Imbal Hasil (%)</label>

            <input
                type="number"
                step="0.01"
                name="imbal_hasil"
                value="0"
            >


            <label>Catatan</label>

            <input
                name="catatan"
            >


            <button
                class="full-button"
                type="submit"
            >
                Simpan Aset
            </button>

        </form>

    </div>

    """

    return page(
        "Tambah Aset",
        body
    )


# =========================================================
# LAPORAN
# =========================================================

@app.route("/laporan")
def laporan():

    conn = get_db()

    data = conn.execute("""
        SELECT

            substr(tanggal,1,7) AS bulan,

            SUM(
                CASE
                    WHEN tipe='Pemasukan'
                    THEN nominal
                    ELSE 0
                END
            ) AS pemasukan,

            SUM(
                CASE
                    WHEN tipe='Pengeluaran'
                    THEN nominal
                    ELSE 0
                END
            ) AS pengeluaran,

            SUM(
                CASE
                    WHEN tipe='Transfer'
                    THEN nominal
                    ELSE 0
                END
            ) AS transfer

        FROM transaksi

        GROUP BY substr(tanggal,1,7)

        ORDER BY bulan DESC

    """).fetchall()

    conn.close()

    rows = ""

    for row in data:

        saldo = (
            (row["pemasukan"] or 0)
            -
            (row["pengeluaran"] or 0)
        )

        rows += f"""
        <tr>

            <td>{escape(row["bulan"])}</td>

            <td class="nominal">
                {rupiah(row["pemasukan"])}
            </td>

            <td class="nominal">
                {rupiah(row["pengeluaran"])}
            </td>

            <td class="nominal">
                {rupiah(saldo)}
            </td>

            <td class="nominal">
                {rupiah(row["transfer"])}
            </td>

        </tr>
        """

    body = f"""

    <div class="card">

        <h1>📊 Laporan Bulanan</h1>

        <div style="overflow-x:auto;">

            <table>

                <thead>

                    <tr>

                        <th>Bulan</th>
                        <th>Pemasukan</th>
                        <th>Pengeluaran</th>
                        <th>Saldo Bersih</th>
                        <th>Transfer</th>

                    </tr>

                </thead>

                <tbody>

                    {rows}

                </tbody>

            </table>

        </div>

    </div>

    """

    return page(
        "Finance HQ - Laporan",
        body
    )


# =========================================================
# AI IMPORTER
# =========================================================

@app.route("/ai-importer", methods=["GET", "POST"])
def ai_importer():

    global CURRENT_AI_TRANSACTIONS

    message = ""
    message_type = "ai-info"

    if request.method == "POST":

        if not AI_READER_AVAILABLE:

            message = (
                "ai_reader.py belum tersedia "
                "di folder FinanceDashboard."
            )

            message_type = "ai-danger"

        else:

            files = request.files.getlist(
                "screenshots"
            )

            selected_platform = request.form.get(
                "platform_screenshot",
                ""
            )

            all_transactions = []

            errors = []

            for file in files:

                if not file:
                    continue

                filename = file.filename or ""

                if not filename:
                    continue

                safe_name = (
                    datetime.now().strftime("%Y%m%d_%H%M%S_%f")
                    + "_"
                    + os.path.basename(filename)
                )

                filepath = os.path.join(
                    UPLOAD_FOLDER,
                    safe_name
                )

                file.save(filepath)

                try:

                    result = read_transaction_screenshot(
                        filepath
                    )

                    if isinstance(result, dict):

                        transactions = result.get(
                            "transactions",
                            []
                        )

                    elif isinstance(result, list):

                        transactions = result

                    else:

                        transactions = []

                    for transaction in transactions:

                        transaction["source_file"] = safe_name

                        # Platform screenshot berlaku
                        # hanya jika AI belum menemukan platform.

                        if (
                            selected_platform
                            and not transaction.get("platform")
                        ):

                            transaction["platform"] = (
                                selected_platform
                            )

                            transaction["perlu_platform"] = False

                        transaction[
                            "tanggal"
                        ] = clean_date(
                            transaction.get("tanggal")
                        )

                        transaction[
                            "nominal"
                        ] = parse_nominal(
                            transaction.get("nominal")
                        )

                        # Makanan selalu catatan makanan

                        kategori = str(
                            transaction.get(
                                "kategori",
                                ""
                            )
                        )

                        if (
                            "makanan"
                            in kategori.lower()
                        ):

                            transaction["catatan"] = (
                                "makanan"
                            )

                        # -------------------------------------------------
                        # DUPLICATE CHECK
                        # -------------------------------------------------

                        duplicate = find_duplicate(
                            transaction.get("tanggal"),
                            transaction.get("keterangan"),
                            transaction.get("nominal"),
                            transaction.get("tipe"),
                            transaction.get("platform"),
                            transaction.get("dari_platform"),
                            transaction.get("ke_platform")
                        )

                        if duplicate:

                            transaction[
                                "duplicate"
                            ] = True

                            transaction[
                                "duplicate_id"
                            ] = duplicate["id"]

                            transaction[
                                "duplicate_text"
                            ] = (
                                f"ID {duplicate['id']} - "
                                f"{duplicate['tanggal']} - "
                                f"{duplicate['keterangan']} - "
                                f"{rupiah(duplicate['nominal'])}"
                            )

                        else:

                            transaction[
                                "duplicate"
                            ] = False

                            transaction[
                                "duplicate_id"
                            ] = None

                            transaction[
                                "duplicate_text"
                            ] = ""

                        # -------------------------------------------------
                        # STATUS
                        # -------------------------------------------------

                        critical_missing = []

                        if not transaction.get("tanggal"):
                            critical_missing.append(
                                "tanggal"
                            )

                        if not transaction.get(
                            "keterangan"
                        ):
                            critical_missing.append(
                                "keterangan"
                            )

                        if not transaction.get(
                            "nominal"
                        ):
                            critical_missing.append(
                                "nominal"
                            )

                        if not transaction.get(
                            "tipe"
                        ):
                            critical_missing.append(
                                "tipe"
                            )

                        transaction[
                            "critical_missing"
                        ] = critical_missing

                        if critical_missing:

                            transaction[
                                "perlu_review"
                            ] = True

                            transaction[
                                "alasan_review"
                            ] = (
                                "Data kurang: "
                                + ", ".join(
                                    critical_missing
                                )
                            )

                        elif transaction.get(
                            "duplicate"
                        ):

                            transaction[
                                "perlu_review"
                            ] = True

                            transaction[
                                "alasan_review"
                            ] = (
                                "Duplikat transaksi "
                                f"dengan ID "
                                f"{transaction['duplicate_id']}"
                            )

                        elif transaction.get(
                            "perlu_review"
                        ):

                            transaction[
                                "alasan_review"
                            ] = (
                                transaction.get(
                                    "alasan_review"
                                )
                                or
                                "Perlu pengecekan"
                            )

                        else:

                            transaction[
                                "perlu_review"
                            ] = False

                            transaction[
                                "alasan_review"
                            ] = ""

                        all_transactions.append(
                            transaction
                        )

                except Exception as e:

                    errors.append(
                        f"{filename}: {str(e)}"
                    )

            CURRENT_AI_TRANSACTIONS = (
                all_transactions
            )

            if all_transactions:

                message = (
                    f"AI membaca "
                    f"{len(all_transactions)} transaksi."
                )

                if errors:

                    message += (
                        " Beberapa file gagal dibaca."
                    )

                    message_type = "ai-warning"

                else:

                    message_type = "ai-success"

            else:

                message = (
                    "Tidak ada transaksi yang berhasil "
                    "dibaca."
                )

                message_type = "ai-danger"

    # =====================================================
    # RENDER RESULT
    # =====================================================

    rows = ""

    for i, transaction in enumerate(
        CURRENT_AI_TRANSACTIONS
    ):

        perlu_review = transaction.get(
            "perlu_review",
            False
        )

        perlu_platform = transaction.get(
            "perlu_platform",
            False
        )

        duplicate = transaction.get(
            "duplicate",
            False
        )

        if duplicate:

            status = """
            <span class="badge review">
                ⚠️ DUPLIKAT
            </span>
            """

        elif perlu_review:

            status = """
            <span class="badge review">
                🔴 REVIEW
            </span>
            """

        elif perlu_platform:

            status = """
            <span class="badge platform">
                🟡 PLATFORM
            </span>
            """

        else:

            status = """
            <span class="badge ready">
                🟢 SIAP
            </span>
            """

        platform_options = ""

        for p in PLATFORMS:

            selected = (
                "selected"
                if p == transaction.get(
                    "platform"
                )
                else ""
            )

            platform_options += (
                f'<option value="{escape(p)}" '
                f'{selected}>'
                f'{escape(p)}'
                f'</option>'
            )

        type_options = ""

        for t in TYPES:

            selected = (
                "selected"
                if t == transaction.get(
                    "tipe"
                )
                else ""
            )

            type_options += (
                f'<option value="{escape(t)}" '
                f'{selected}>'
                f'{escape(t)}'
                f'</option>'
            )

        duplicate_info = ""

        if duplicate:

            duplicate_info = f"""
            <div style="
                margin-top:6px;
                color:#991b1b;
                font-size:12px;
            ">
                {escape(
                    transaction.get(
                        "duplicate_text",
                        ""
                    )
                )}
            </div>
            """

        rows += f"""

        <tr>

            <td>
                {i + 1}
            </td>

            <td>

                <input
                    type="date"
                    name="tanggal_{i}"
                    value="{escape(
                        transaction.get(
                            "tanggal",
                            ""
                        )
                    )}"
                >

            </td>

            <td>

                <input
                    name="keterangan_{i}"
                    value="{escape(
                        transaction.get(
                            "keterangan",
                            ""
                        )
                    )}"
                >

                {duplicate_info}

            </td>

            <td>

                <input
                    name="kategori_{i}"
                    value="{escape(
                        transaction.get(
                            "kategori",
                            "Lainnya"
                        )
                    )}"
                >

            </td>

            <td>

                <input
                    name="catatan_{i}"
                    value="{escape(
                        transaction.get(
                            "catatan",
                            ""
                        ) or ""
                    )}"
                >

            </td>

            <td>

                <input
                    type="number"
                    name="nominal_{i}"
                    value="{transaction.get("nominal", 0)}"
                >

            </td>

            <td>

                <select name="tipe_{i}">

                    {type_options}

                </select>

            </td>

            <td>

                <select name="platform_{i}">

                    <option value="">
                        -- Pilih --
                    </option>

                    {platform_options}

                </select>

            </td>

            <td>

                {status}

            </td>

        </tr>

        """

    result_section = ""

    if CURRENT_AI_TRANSACTIONS:

        result_section = f"""

        <div class="card">

            <h2>
                🔍 Hasil Pembacaan AI
            </h2>

            <div class="ai-info">

                <b>
                    Jangan langsung import.
                </b>

                Periksa data terlebih dahulu.
                Jika ada tulisan REVIEW atau DUPLIKAT,
                jangan masukkan sebelum diperiksa.

            </div>


            <form method="POST"
                  action="/ai-importer/review">

                <div style="overflow-x:auto;">

                    <table class="ai-table">

                        <thead>

                            <tr>

                                <th>#</th>
                                <th>Tanggal</th>
                                <th>Keterangan</th>
                                <th>Kategori</th>
                                <th>Catatan</th>
                                <th>Nominal</th>
                                <th>Tipe</th>
                                <th>Platform</th>
                                <th>Status</th>

                            </tr>

                        </thead>

                        <tbody>

                            {rows}

                        </tbody>

                    </table>

                </div>


                <br>


                <button
                    type="submit"
                    class="btn-green"
                >
                    💾 Simpan Perubahan Review
                </button>

            </form>


            <br>


            <form
                method="POST"
                action="/ai-importer/import"
                onsubmit="
                    return confirm(
                        'Yakin import transaksi yang sudah siap?'
                    );
                "
            >

                <button
                    type="submit"
                    class="btn-green"
                >
                    🚀 IMPORT KE FINANCE HQ
                </button>

            </form>

        </div>

        """

    body = f"""

    <div class="card">

        <h1>
            🤖 AI Finance Importer
        </h1>

        <p>
            Screenshot → AI → Review →
            Duplicate Check → Import Finance HQ
        </p>

        <div class="ai-info">

            <b>Platform Screenshot</b>

            <br>

            Pilih platform sekali.
            Platform ini akan dipakai untuk transaksi
            yang platform-nya tidak terbaca dari screenshot.

        </div>


        {
            f'''
            <div class="{message_type}">
                {escape(message)}
            </div>
            '''
            if message
            else ""
        }


        <form
            method="POST"
            enctype="multipart/form-data"
        >

            <label>
                Platform Screenshot
            </label>

            <select
                name="platform_screenshot"
            >

                <option value="">
                    -- Pilih Platform --
                </option>

                {
                    "".join(
                        f'<option value="{escape(p)}">'
                        f'{escape(p)}'
                        f'</option>'
                        for p in PLATFORMS
                    )
                }

            </select>


            <label>
                Screenshot
            </label>

            <input
                type="file"
                name="screenshots"
                accept="image/*"
                multiple
                required
            >


            <button
                type="submit"
                class="full-button"
            >
                🔍 Baca Screenshot dengan AI
            </button>

        </form>

    </div>


    {result_section}

    """

    return page(
        "AI Finance Importer",
        body
    )


# =========================================================
# AI REVIEW SAVE
# =========================================================

@app.route(
    "/ai-importer/review",
    methods=["POST"]
)
def ai_importer_review():

    global CURRENT_AI_TRANSACTIONS

    for i, transaction in enumerate(
        CURRENT_AI_TRANSACTIONS
    ):

        transaction["tanggal"] = request.form.get(
            f"tanggal_{i}",
            ""
        )

        transaction["keterangan"] = request.form.get(
            f"keterangan_{i}",
            ""
        ).strip()

        transaction["kategori"] = request.form.get(
            f"kategori_{i}",
            "Lainnya"
        )

        transaction["catatan"] = request.form.get(
            f"catatan_{i}",
            ""
        ).strip()

        transaction["nominal"] = parse_nominal(
            request.form.get(
                f"nominal_{i}",
                "0"
            )
        )

        transaction["tipe"] = request.form.get(
            f"tipe_{i}",
            "Pengeluaran"
        )

        transaction["platform"] = request.form.get(
            f"platform_{i}",
            ""
        )

        # Makanan
        if (
            "makanan"
            in str(
                transaction["kategori"]
            ).lower()
        ):

            transaction["catatan"] = "makanan"

        # Platform missing
        if not transaction["platform"]:

            transaction["perlu_platform"] = True

        else:

            transaction["perlu_platform"] = False

        # Critical validation

        missing = []

        if not transaction["tanggal"]:
            missing.append("tanggal")

        if not transaction["keterangan"]:
            missing.append("keterangan")

        if not transaction["nominal"]:
            missing.append("nominal")

        if not transaction["tipe"]:
            missing.append("tipe")

        if missing:

            transaction["perlu_review"] = True

            transaction["alasan_review"] = (
                "Data kurang: "
                + ", ".join(missing)
            )

        elif transaction["duplicate"]:

            transaction["perlu_review"] = True

            transaction["alasan_review"] = (
                "Duplikat transaksi"
            )

        else:

            transaction["perlu_review"] = False

            transaction["alasan_review"] = ""

    return redirect("/ai-importer")


# =========================================================
# AI IMPORT KE FINANCE HQ
# =========================================================

@app.route(
    "/ai-importer/import",
    methods=["POST"]
)
def ai_importer_import():

    global CURRENT_AI_TRANSACTIONS

    if not CURRENT_AI_TRANSACTIONS:

        return redirect("/ai-importer")

    # -----------------------------------------------------
    # VALIDATE SEMUA DATA DULU
    # -----------------------------------------------------

    ready_transactions = []

    blocked = []

    for i, transaction in enumerate(
        CURRENT_AI_TRANSACTIONS
    ):

        if transaction.get(
            "perlu_review",
            False
        ):

            blocked.append(
                f"Baris {i + 1}: "
                "masih REVIEW"
            )

            continue

        if transaction.get(
            "perlu_platform",
            False
        ):

            blocked.append(
                f"Baris {i + 1}: "
                "platform belum dipilih"
            )

            continue

        if transaction.get(
            "duplicate",
            False
        ):

            blocked.append(
                f"Baris {i + 1}: "
                "duplikat"
            )

            continue

        if not transaction.get(
            "tanggal"
        ):

            blocked.append(
                f"Baris {i + 1}: tanggal kosong"
            )

            continue

        if not transaction.get(
            "keterangan"
        ):

            blocked.append(
                f"Baris {i + 1}: keterangan kosong"
            )

            continue

        if not transaction.get(
            "nominal"
        ):

            blocked.append(
                f"Baris {i + 1}: nominal kosong"
            )

            continue

        ready_transactions.append(
            transaction
        )

    if blocked:

        return page(
            "AI Importer - Belum Siap",
            f"""

            <div class="card">

                <h1>
                    ⚠️ Belum Bisa Import
                </h1>

                <div class="ai-danger">

                    Ada data yang masih harus
                    diperiksa terlebih dahulu.

                </div>

                <ul>

                    {
                        "".join(
                            f"<li>{escape(x)}</li>"
                            for x in blocked
                        )
                    }

                </ul>

                <br>

                <a
                    href="/ai-importer"
                    class="btn btn-edit"
                >
                    ← Kembali ke Review
                </a>

            </div>

            """
        )

    if not ready_transactions:

        return redirect("/ai-importer")

    # -----------------------------------------------------
    # BACKUP
    # -----------------------------------------------------

    backup_path = make_backup()

    # -----------------------------------------------------
    # INSERT
    # -----------------------------------------------------

    conn = get_db()

    imported = 0

    try:

        for transaction in ready_transactions:

            tanggal = clean_date(
                transaction.get(
                    "tanggal"
                )
            )

            keterangan = str(
                transaction.get(
                    "keterangan",
                    ""
                )
            ).strip()

            kategori = str(
                transaction.get(
                    "kategori",
                    "Lainnya"
                )
            ).strip()

            catatan = str(
                transaction.get(
                    "catatan",
                    ""
                ) or ""
            ).strip()

            platform = str(
                transaction.get(
                    "platform",
                    ""
                ) or ""
            ).strip()

            tipe = str(
                transaction.get(
                    "tipe",
                    "Pengeluaran"
                )
            ).strip()

            nominal = parse_nominal(
                transaction.get(
                    "nominal"
                )
            )

            dari_platform = str(
                transaction.get(
                    "dari_platform",
                    ""
                ) or ""
            ).strip()

            ke_platform = str(
                transaction.get(
                    "ke_platform",
                    ""
                ) or ""
            ).strip()

            # -------------------------------------------------
            # SPECIAL RULES
            # -------------------------------------------------

            if (
                "makanan"
                in kategori.lower()
            ):

                catatan = "makanan"

            # Transfer tidak masuk platform biasa
            if tipe == "Transfer":

                platform = ""

            else:

                dari_platform = ""
                ke_platform = ""

            # -------------------------------------------------
            # FINAL DUPLICATE CHECK
            # -------------------------------------------------

            duplicate = find_duplicate(
                tanggal,
                keterangan,
                nominal,
                tipe,
                platform,
                dari_platform,
                ke_platform
            )

            if duplicate:

                continue

            conn.execute("""
                INSERT INTO transaksi (

                    tanggal,
                    keterangan,
                    kategori,
                    platform,
                    tipe,
                    nominal,
                    dari_platform,
                    ke_platform

                )

                VALUES (?, ?, ?, ?, ?, ?, ?, ?)

            """, (
                tanggal,
                keterangan,
                kategori,
                platform,
                tipe,
                nominal,
                dari_platform,
                ke_platform
            ))

            imported += 1

        conn.commit()

    except Exception:

        conn.rollback()

        conn.close()

        raise

    conn.close()

    # -----------------------------------------------------
    # LOG
    # -----------------------------------------------------

    write_log({
        "action": "AI_IMPORT",
        "imported": imported,
        "backup": backup_path,
        "transactions": ready_transactions
    })

    # -----------------------------------------------------
    # CLEAR PENDING
    # -----------------------------------------------------

    CURRENT_AI_TRANSACTIONS = []

    return page(
        "AI Importer - Berhasil",
        f"""

        <div class="card">

            <h1>
                ✅ Import Berhasil
            </h1>

            <div class="ai-success">

                <h2>
                    {imported} transaksi
                    berhasil masuk Finance HQ.
                </h2>

            </div>

            <p>
                Database Finance HQ sudah diperbarui.
            </p>

            <p>
                Backup otomatis:
                <b>
                    {escape(
                        os.path.basename(
                            backup_path or "-"
                        )
                    )}
                </b>
            </p>

            <br>

            <a
                href="/"
                class="btn btn-green"
            >
                🏠 Kembali ke Dashboard
            </a>

            &nbsp;

            <a
                href="/transaksi"
                class="btn btn-edit"
            >
                💳 Lihat Transaksi
            </a>

        </div>

        """
    )


# =========================================================
# ERROR HANDLER
# =========================================================

@app.errorhandler(Exception)
def handle_error(error):

    return page(
        "Finance HQ - Error",
        f"""

        <div class="card">

            <h1>
                ⚠️ Terjadi Error
            </h1>

            <div class="ai-danger">

                {escape(str(error))}

            </div>

            <br>

            <a
                href="/"
                class="btn btn-edit"
            >
                ← Kembali ke Dashboard
            </a>

        </div>

        """
    ), 500


# =========================================================
# RUN
# =========================================================

if __name__ == "__main__":

    print("")
    print("======================================")
    print("       FINANCE HQ")
    print("======================================")
    print("")
    print("Dashboard:")
    print("http://127.0.0.1:5000")
    print("")
    print("AI Importer:")
    print("http://127.0.0.1:5000/ai-importer")
    print("")
    print("======================================")
    print("")

    app.run(
        debug=True,
        host="127.0.0.1",
        port=5000
    )