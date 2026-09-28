import sqlite3
from datetime import datetime
from pathlib import Path
import shutil
import openpyxl

BASE = Path(__file__).resolve().parent
DB = BASE / "finance.db"

# GANTI jika nama file Excel berbeda.
EXCEL = BASE / "Financial_Jayson_Agustus_2026_FULL.xlsx"

if not EXCEL.exists():
    raise FileNotFoundError(
        f"File Excel tidak ditemukan:\n{EXCEL}\n\n"
        "Pastikan file Excel berada di folder FinanceDashboard."
    )

# 1) Backup database sebelum import
if DB.exists():
    backup = BASE / f"finance_backup_before_import_{datetime.now():%Y%m%d_%H%M%S}.db"
    shutil.copy2(DB, backup)
    print(f"Backup dibuat: {backup.name}")

# 2) Baca Excel
wb = openpyxl.load_workbook(EXCEL, data_only=True)
ws = wb["Agustus - Semua Transaksi"]
asset_ws = wb["Aset - Akhir Agustus"]

# 3) Buka database
conn = sqlite3.connect(DB)
cur = conn.cursor()

# Pastikan tabel utama ada
cur.execute("""
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

cur.execute("""
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

# Tambahkan kolom jika DB lama belum punya
for table, col, definition in [
    ("transaksi", "dari_platform", "TEXT"),
    ("transaksi", "ke_platform", "TEXT"),
    ("aset", "keuntungan", "REAL"),
    ("aset", "imbal_hasil", "REAL"),
]:
    try:
        cur.execute(f"ALTER TABLE {table} ADD COLUMN {col} {definition}")
    except sqlite3.OperationalError:
        pass

# 4) Bersihkan data transaksi/aset lama sesuai permintaan user
cur.execute("DELETE FROM transaksi")
cur.execute("DELETE FROM aset")

def clean(v):
    if v is None:
        return ""
    return str(v).strip()

def excel_date(v):
    if v is None:
        return ""
    if hasattr(v, "strftime"):
        return v.strftime("%Y-%m-%d %H:%M") if getattr(v, "hour", 0) else v.strftime("%Y-%m-%d")
    s = clean(v)
    # Data sumber menggunakan DD-MM-YYYY
    for fmt in ("%d-%m-%Y %H:%M", "%d-%m-%Y", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            d = datetime.strptime(s, fmt)
            return d.strftime("%Y-%m-%d %H:%M") if d.hour else d.strftime("%Y-%m-%d")
        except ValueError:
            pass
    return s

def platform_from_excel(v):
    p = clean(v)
    # Pertahankan nama platform dari Finance HQ
    return p if p else "Lainnya"

def transfer_endpoints(platform, catatan):
    c = clean(catatan)
    # Dari/ke berdasarkan catatan Finance HQ jika tersedia
    if "BCA → Stockbit" in c:
        return "BCA", "Stockbit"
    if "Stockbit → BCA" in c:
        return "Stockbit", "BCA"
    if "Mandiri → SeaBank" in c:
        return "Mandiri", "SeaBank"
    return platform, ""

# 5) Import semua transaksi dari sheet utama
inserted = 0
for row in ws.iter_rows(min_row=2, values_only=True):
    if not any(v is not None for v in row):
        continue

    tanggal, platform, keterangan, jumlah, jenis, kategori, catatan = row[:7]

    if jumlah is None:
        continue

    tanggal = excel_date(tanggal)
    platform = platform_from_excel(platform)
    keterangan = clean(keterangan)
    jenis = clean(jenis)
    kategori = clean(kategori)
    catatan = clean(catatan)

    dari_platform = ""
    ke_platform = ""

    if jenis.lower() == "transfer":
        dari_platform, ke_platform = transfer_endpoints(platform, catatan)

    # Aturan Finance HQ: pemasukan dari nama orang -> catatan kosong
    if jenis.lower() == "pemasukan" and keterangan and catatan == "":
        catatan = ""

    cur.execute("""
        INSERT INTO transaksi
        (tanggal, keterangan, kategori, platform, tipe, nominal, dari_platform, ke_platform)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        tanggal,
        keterangan,
        kategori,
        platform,
        jenis,
        float(jumlah),
        dari_platform,
        ke_platform
    ))
    inserted += 1

# 6) Import aset yang memiliki nilai.
# Jika nilai kosong di Finance HQ, TIDAK diisi dengan tebakan.
assets_inserted = 0
for row in asset_ws.iter_rows(min_row=2, values_only=True):
    if not any(v is not None for v in row):
        continue

    nama, jenis, nilai, platform, catatan = row[:5]
    if nilai is None:
        print(f"Aset dilewati karena nilai kosong: {nama}")
        continue

    cur.execute("""
        INSERT INTO aset
        (nama, jenis, kepemilikan, tanggal, nilai, keuntungan, imbal_hasil, catatan)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        clean(nama),
        clean(jenis),
        clean(platform),
        "2026-08-31",
        float(nilai),
        None,
        None,
        clean(catatan)
    ))
    assets_inserted += 1

conn.commit()

# 7) Verifikasi jumlah
tx_count = cur.execute("SELECT COUNT(*) FROM transaksi").fetchone()[0]
asset_count = cur.execute("SELECT COUNT(*) FROM aset").fetchone()[0]

income = cur.execute("""
    SELECT COALESCE(SUM(nominal),0)
    FROM transaksi
    WHERE tipe = 'Pemasukan'
""").fetchone()[0]

expense = cur.execute("""
    SELECT COALESCE(SUM(nominal),0)
    FROM transaksi
    WHERE tipe = 'Pengeluaran'
""").fetchone()[0]

transfer = cur.execute("""
    SELECT COALESCE(SUM(nominal),0)
    FROM transaksi
    WHERE tipe = 'Transfer'
""").fetchone()[0]

conn.close()

print("\n========================================")
print("IMPORT FINANCE HQ SELESAI")
print("========================================")
print(f"Transaksi masuk : {tx_count}")
print(f"Aset masuk      : {asset_count}")
print(f"Pemasukan       : Rp{income:,.2f}")
print(f"Pengeluaran     : Rp{expense:,.2f}")
print(f"Transfer        : Rp{transfer:,.2f}")
print("========================================")
print("Tutup script ini lalu jalankan app.py seperti biasa.")
