# app.py
import streamlit as st
import pandas as pd
from datetime import datetime, date
from sqlalchemy import create_engine, text
from io import BytesIO
import altair as alt
import plotly.express as px
import matplotlib.pyplot as plt
import math
import traceback
import string, random
import smtplib
from email.mime.text import MIMEText
import re

# ---------------------------
# Config / Secrets
# ---------------------------
DB_URL = st.secrets["DB_URL"]
ADMIN_PASSWORD = st.secrets.get("ADMIN_PASSWORD")  
SELLER_PASSWORD = st.secrets.get("SELLER_PASSWORD")
KASIR_PASSWORDS = st.secrets["KASIR_PASSWORDS"]
EMAIL = st.secrets["EMAIL"]
APP_PASSWORD = st.secrets["APP_PASSWORD"]
ADMIN_EMAIL = st.secrets["ADMIN_EMAIL"]

engine = create_engine(DB_URL, future=True)

# ---------------------------
# Database initialization
# ---------------------------
def init_db():
    try:
        with engine.begin() as conn:
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS vouchers (
                    code TEXT PRIMARY KEY,
                    initial_value INTEGER NOT NULL,
                    balance INTEGER NOT NULL,
                    nama TEXT,
                    no_hp TEXT,
                    status TEXT DEFAULT 'inactive',
                    seller TEXT,
                    tanggal_penjualan DATE,
                    tanggal_aktivasi DATE,
                    tunai INTEGER,
                    jenis_kupon TEXT NOT NULL,
                    FOREIGN KEY(jenis_kupon) REFERENCES jenis_db(jenis_kupon)
                )
            """))
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS transactions (
                    id SERIAL PRIMARY KEY,
                    code TEXT NOT NULL,
                    used_amount INTEGER NOT NULL,
                    tanggal_transaksi TIMESTAMP NOT NULL,
                    branch TEXT,
                    items TEXT,
                    diskon INTEGER
                )
            """))
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS menu_items (
                    id SERIAL PRIMARY KEY,
                    kategori TEXT,
                    nama_item TEXT,
                    keterangan TEXT,
                    harga_sedati INTEGER,
                    harga_twsari INTEGER,
                    harga_kesambi INTEGER,
                    harga_tulangan INTEGER,
                    status TEXT
                )
            """))
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS jenis_db (
                    jenis_kupon TEXT PRIMARY KEY,
                    awal_berlaku DATE NOT NULL,
                    akhir_berlaku DATE NOT NULL
                )
            """))
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS kategori_menu (
                    id_kategori SERIAL PRIMARY KEY,
                    nama_kategori TEXT,
                    status_kategori TEXT
                )
            """))

    except Exception as e:
        st.error(f"Gagal inisialisasi database: {e}")
        st.stop()

def aktivasi_notification(voucher_code, seller_name, buyer_name, buyer_phone):
    subject = f"[INFO] Voucher {voucher_code} Ingin Diaktivasi oleh Seller"
    body = f"""
    Halo Admin,
    
    Voucher ingin diaktivasi seller.
    
    Kode Voucher : {voucher_code}
    Seller       : {seller_name}
    Pembeli      : {buyer_name}
    No HP        : {buyer_phone}
    
    Lihat detail lebih lengkap pada aplikasi Pawon Sappitoe.
    Salam,
    Sistem Pawon Sappitoe
    """

    msg = MIMEText(body)
    msg["Subject"] = subject
    msg["From"] = EMAIL
    msg["To"] = ADMIN_EMAIL

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(EMAIL, APP_PASSWORD)
            server.sendmail(EMAIL, ADMIN_EMAIL, msg.as_string())
        return True
    except Exception as e:
        print("Email error:", e)
        return False

def transaksi_notification(tanggal_transaksi, branch, total):
    subject = f"[INFO] Ada Transaksi Baru yang Masuk"
    body = f"""
    Halo Admin,
    
    Ada transaksi baru yang telah masuk.
    
    Tanggal Transaksi : {tanggal_transaksi}
    Cabang            : {branch}
    Total pembelian   : {total}
    
    Lihat detail lebih lengkap pada aplikasi Pawon Sappitoe.
    Salam,
    Sistem Pawon Sappitoe
    """

    msg = MIMEText(body)
    msg["Subject"] = subject
    msg["From"] = EMAIL
    msg["To"] = ADMIN_EMAIL

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(EMAIL, APP_PASSWORD)
            server.sendmail(EMAIL, ADMIN_EMAIL, msg.as_string())
        return True
    except Exception as e:
        print("Email error:", e)
        return False

def daftar_notification(nama, nohp):
    subject = f"[INFO] Ada Seller Baru yang Mendaftar"
    body = f"""
    Halo Admin,
    
    Ada Seller baru yang baru saja mendaftar.
    
    Nama Seller       : {nama}
    Nomor HP Seller   : {nohp}
    
    Seller tersebut bisa diterima pada aplikasi Pawon Sappitoe.
    Salam,
    Sistem Pawon Sappitoe
    """

    msg = MIMEText(body)
    msg["Subject"] = subject
    msg["From"] = EMAIL
    msg["To"] = ADMIN_EMAIL

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(EMAIL, APP_PASSWORD)
            server.sendmail(EMAIL, ADMIN_EMAIL, msg.as_string())
        return True
    except Exception as e:
        print("Email error:", e)
        return False

def reset_redeem_state():
    for key in [
        "redeem_step",
        "entered_code",
        "order_items",
        "checkout_total",
        "isvoucher",
        "voucher_row",
        "newbal",
        "show_success"
    ]:
        st.session_state.pop(key, None)

def show_back_to_login_button(role=""):
    st.markdown("---")
    if st.button("⬅️ Kembali ke Halaman Login"):
        # Reset semua state login
        st.session_state.admin_logged_in = False
        st.session_state.seller_logged_in = False
        st.session_state.kasir_logged_in = False

        # Reset page/page flags
        st.session_state.page = None

        # Reset transaksi (jika kasir)
        if role == "kasir":
            reset_redeem_state()

        st.rerun()


def generate_code(length=6):
    """Generate kode kombinasi huruf + angka sepanjang 6 karakter."""
    characters = string.ascii_uppercase + string.digits
    return ''.join(random.choice(characters) for _ in range(length))

def kode_exists(kode):
    """Cek apakah kode sudah ada di vouchers."""
    with engine.connect() as conn:
        r = conn.execute(
            text("SELECT 1 FROM vouchers WHERE code = :c"),
            {"c": kode}
        ).fetchone()
    return r is not None


def insert_jenis_if_not_exists(jenis, awal, akhir):
    """Insert data jenis ke jenis_db hanya jika belum ada."""
    with engine.begin() as conn:
        exists = conn.execute(
            text("SELECT 1 FROM jenis_db WHERE jenis_kupon = :j"),
            {"j": jenis}
        ).fetchone()

        if not exists:
            conn.execute(
                text("""
                    INSERT INTO jenis_db (jenis_kupon, awal_berlaku, akhir_berlaku)
                    VALUES (:j, :a, :b)
                """),
                {"j": jenis, "a": awal, "b": akhir}
            )


def insert_voucher(code, initial_value, jenis, awal, akhir):
    """Insert satu voucher baru."""
    with engine.begin() as conn:
        conn.execute(
            text("""
                INSERT INTO vouchers
                (code, initial_value, balance, jenis_kupon, tanggal_penjualan, tanggal_aktivasi, status)
                VALUES (:c, :iv, :bal, :jenis, :awal, :akhir, 'inactive')
            """),
            {
                "c": code,
                "iv": initial_value,
                "bal": initial_value,
                "jenis": jenis,
                "awal": awal,
                "akhir": akhir
            }
        )


# ---------------------------
# DB helpers
# ---------------------------
def find_voucher(code):
    try:
        with engine.connect() as conn:
            row = conn.execute(text("""
                SELECT 
                    v.code,
                    v.initial_value,
                    v.balance,
                    v.nama,
                    v.no_hp,
                    v.status,
                    v.seller,
                    v.tanggal_aktivasi,
                    j.awal_berlaku,
                    j.akhir_berlaku
                FROM vouchers v
                JOIN jenis_db j 
                ON v.jenis_kupon = j.jenis_kupon
                WHERE v.code = :code
                LIMIT 1
            """), {"code": code}).fetchone()
        return row
    except Exception as e:
        st.error(f"DB error saat cari voucher: {e}")
        return None

def update_voucher_detail(code, nama, no_hp, status, tanggal_aktivasi):
    try:
        with engine.begin() as conn:
            conn.execute(text("""
                UPDATE vouchers
                SET nama = :nama,
                    no_hp = :no_hp,
                    status = :status,
                    tanggal_aktivasi = :tanggal_aktivasi
                WHERE code = :code
            """), {"nama": nama, "no_hp": no_hp, "status": status, "tanggal_aktivasi": tanggal_aktivasi, "code": code})
        return True
    except Exception as e:
        st.error(f"Gagal update voucher: {e}")
        return False

def atomic_redeem(code, amount, branch, items_str, diskon):
    try:
        if code is None:
            with engine.begin() as conn:
                conn.execute(text("""
                    INSERT INTO transactions 
                    (code, used_amount, tanggal_transaksi, branch, items, tunai, isvoucher, diskon)
                    VALUES (NULL, :tunai, :now, :branch, :items, :tunai, 'no', :diskon)
                """), {
                    "now": datetime.utcnow(),
                    "branch": branch,
                    "items": items_str,
                    "used_amount": amount,
                    "tunai": amount,
                    "diskon": diskon
                })

                # Update terjual untuk menu
                items = [x.strip() for x in items_str.split(",")]
                for i in items:
                    if " x" not in i:
                        continue
                    nama_item, qty = i.split(" x")
                    qty = int(qty)

                    # Nama cabang -> nama kolom
                    mapping = {
                        "tawangsari": "terjual_twsari",
                        "sedati": "terjual_sedati",
                        "kesambi": "terjual_kesambi",
                        "tulangan": "terjual_tulangan"
                    }

                    col = mapping.get(branch.lower(), None)   # None kalau branch tidak ditemukan
                    if not col:
                        return False, f"Cabang '{branch}' tidak dikenali.", None

                    conn.execute(text(f"""
                        UPDATE menu_items
                        SET {col} = COALESCE({col}, 0) + :qty
                        WHERE nama_item = :item
                    """), {"qty": qty, "item": nama_item})

                return True, "Transaksi cash berhasil 💸", None
        else:    
            with engine.begin() as conn:
    
                # Ambil saldo voucher (lock row)
                r = conn.execute(
                    text("SELECT balance, COALESCE(tunai, 0) FROM vouchers WHERE code = :c AND status = 'active' FOR UPDATE"),
                    {"c": code}
                ).fetchone()
    
                if not r:
                    return False, "Voucher tidak ditemukan.", None
    
                balance = int(r[0])
                tunai_existing = int(r[1])
    
                # Jika saldo 0 → tidak boleh dipakai
                if balance <= 0:
                    return False, "Saldo voucher sudah habis.", balance
    
                # Hitung saldo baru & cash shortage
                if amount > balance:
                    shortage = amount - balance  # kekurangan
                    new_balance = 0
                else:
                    shortage = 0
                    new_balance = balance - amount
    
                # Update status voucher
                new_status = "habis" if new_balance == 0 else "active"
    
                # Update saldo, status, dan tunai (tambahkan shortage)
                conn.execute(
                    text("""
                        UPDATE vouchers 
                        SET balance = :newbal,
                            status = :newstatus,
                            tunai = :newtunai
                        WHERE code = :c
                    """),
                    {
                        "newbal": new_balance,
                        "newstatus": new_status,
                        "newtunai": tunai_existing + shortage,
                        "c": code
                    }
                )
    
                # Simpan transaksi ke database
                conn.execute(text("""
                    INSERT INTO transactions 
                    (code, used_amount, tanggal_transaksi, branch, items, tunai, isvoucher, diskon)
                    VALUES (:c, :amt, :now, :branch, :items, :tunai, 'yes', :diskon)
                """), {
                    "c": code,
                    "amt": amount,
                    "now": datetime.utcnow(),
                    "branch": branch,
                    "items": items_str,
                    "tunai": shortage,
                    "diskon": diskon
                })
    
                # Update penjualan menu
                items = [x.strip() for x in items_str.split(",")]
                for i in items:
                    if " x" not in i:
                        continue
                    nama_item, qty = i.split(" x")
                    qty = int(qty)
                    mapping = {
                        "tawangsari": "terjual_twsari",
                        "sedati": "terjual_sedati",
                        "kesambi": "terjual_kesambi",
                        "tulangan": "terjual_tulangan"
                    }

                    col = mapping.get(branch.lower(), None) 
                    if not col:
                        return False, f"Cabang '{branch}' tidak dikenali.", None
                    
                    conn.execute(text(f"""
                        UPDATE menu_items
                        SET {col} = COALESCE({col}, 0) + :qty
                        WHERE nama_item = :item
                    """), {"qty": qty, "item": nama_item})
    
                return True, "Redeem berhasil ✅", new_balance

    except Exception as e:
        traceback.print_exc()
        return False, f"DB error saat redeem: {e}", None

def list_vouchers(filter_status=None, search=None, limit=5000, offset=0):
    q = "SELECT v.code, v.initial_value, v.balance, v.nama, v.no_hp, v.status, v.seller, v.tanggal_aktivasi, j.awal_berlaku, j.akhir_berlaku FROM vouchers v JOIN jenis_db j ON v.jenis_kupon = j.jenis_kupon"
    clauses = []
    params = {}
    if filter_status == "aktif":
        clauses.append("status ILIKE 'active'")
    elif filter_status == "habis":
        clauses.append("balance = 0")
    if search:
        clauses.append("code ILIKE :search")
        params["search"] = f"%{search}%"
    if clauses:
        q += " WHERE " + " AND ".join(clauses)
    q += " ORDER BY awal_berlaku DESC LIMIT :limit OFFSET :offset"
    params["limit"] = limit
    params["offset"] = offset
    with engine.connect() as conn:
        df = pd.read_sql(text(q), conn, params=params)
    if "status" in df.columns:
        df["status"] = df["status"].fillna("inactive")
    else:
        df["status"] = "inactive"
    return df

def to_int_or_none(value):
    if value in ("", None):
        return None
    try:
        return int(value)
    except:
        return None

def to_none_if_empty(value: str):
    if value is None:
        return None
    value = value.strip()
    return value if value != "" else None

def to_upper_or_none(value: str):
    if value is None:
        return None
    value = value.strip()
    return value.upper() if value != "" else None
    
def list_all_menu():
    query = """
        SELECT * FROM menu_items
        ORDER BY kategori, nama_item
    """
    with engine.begin() as conn:
        res = conn.execute(text(query)).mappings().all()
    return res

def add_menu_item(kategori, nama_item, keterangan,
                  harga_sedati, harga_twsari, harga_kesambi, harga_tulangan):

    query = """
        INSERT INTO menu_items (
            kategori, nama_item, keterangan,
            harga_sedati, harga_twsari, harga_kesambi, harga_tulangan
        ) VALUES (
            :kategori, :nama_item, :keterangan,
            :harga_sedati, :harga_twsari, :harga_kesambi, :harga_tulangan
        )
    """

    params = {
        "kategori": to_upper_or_none(kategori),
        "nama_item": to_upper_or_none(nama_item),
        "keterangan": to_none_if_empty(keterangan),
        "harga_sedati": to_int_or_none(harga_sedati),
        "harga_twsari": to_int_or_none(harga_twsari),
        "harga_kesambi": to_int_or_none(harga_kesambi),
        "harga_tulangan": to_int_or_none(harga_tulangan)
    }

    with engine.begin() as conn:
        conn.execute(text(query), params)


def update_menu_item(id_menu, kategori, nama_item, keterangan,
                     harga_sedati, harga_twsari, harga_kesambi, harga_tulangan, status):

    query = """
        UPDATE menu_items SET
            kategori = :kategori,
            nama_item = :nama_item,
            keterangan = :keterangan,
            harga_sedati = :harga_sedati,
            harga_twsari = :harga_twsari,
            harga_kesambi = :harga_kesambi,
            harga_tulangan = :harga_tulangan,
            status = :status
        WHERE id_menu = :id_menu
    """

    params = {
        "id_menu": id_menu,
        "kategori": to_upper_or_none(kategori),
        "nama_item": to_upper_or_none(nama_item),
        "keterangan": keterangan,
        "harga_sedati": to_int_or_none(harga_sedati),
        "harga_twsari": to_int_or_none(harga_twsari),
        "harga_kesambi": to_int_or_none(harga_kesambi),
        "harga_tulangan": to_int_or_none(harga_tulangan),
        "status": status
    }

    with engine.begin() as conn:
        conn.execute(text(query), params)


def delete_menu_item(id_menu):
    try:
        with engine.begin() as conn:
            conn.execute(text("""
                DELETE FROM menu_items WHERE id_menu = :id_menu
            """), {"id_menu": id_menu})
        return True
    except Exception as e:
        st.error(f"Error saat menghapus menu: {e}")
        return False

def get_kategori_list():
    query = text("""
        SELECT DISTINCT kategori
        FROM menu_items
        WHERE kategori IS NOT NULL AND kategori <> ''
        ORDER BY kategori
    """)
    with engine.begin() as conn:
        rows = conn.execute(query).fetchall()
    # ambil kolom pertama dari tiap row jadi list
    return [r[0] for r in rows]

def list_all_menu():
    try:
        with engine.connect() as conn:
            result = conn.execute(text("""
                SELECT *
                FROM menu_items
                ORDER BY kategori, nama_item
            """))

            rows = result.fetchall()

            menu_list = []
            for r in rows:
                menu_list.append({
                    "id_menu": r[7],
                    "kategori": r[0],
                    "nama_item": r[1],
                    "keterangan": r[2],
                    "harga_sedati": r[3],
                    "harga_twsari": r[4],
                    "harga_kesambi" : r[8],
                    "harga_tulangan" : r[10],
                    "status" : r[12]
                })

            return menu_list

    except Exception as e:
        st.error(f"Error saat mengambil menu: {e}")
        return []

def get_menu_from_db(branch):
    try:
        with engine.connect() as conn:
            df = pd.read_sql(text("""
                SELECT id_menu, kategori, nama_item, keterangan,
                       harga_sedati, harga_twsari, harga_kesambi, harga_tulangan, status
                FROM menu_items
            """), conn)

        mapping_harga = {
            "Tawangsari": "harga_twsari",
            "Sedati": "harga_sedati",
            "Kesambi": "harga_kesambi",
            "Tulangan": "harga_tulangan"
        }
        harga_col = mapping_harga.get(branch)
        if not harga_col:
            return []

        menu_list = []
        for _, row in df.iterrows():
            id_menu = row["id_menu"]
            harga = row[harga_col]

            # ✅ hindari cannot convert nan to int
            if pd.isna(id_menu) or pd.isna(harga):
                continue

            menu_list.append({
                "id_menu": int(id_menu),
                "nama": str(row["nama_item"]),
                "harga": int(harga),
                "kategori": str(row["kategori"]) if row["kategori"] is not None else "Lainnya",
                "keterangan": "" if row["keterangan"] is None else str(row["keterangan"]),
                "status": str(row["status"])
            })

        return menu_list

    except Exception as e:
        st.error(f"Gagal ambil menu dari DB: {e}")
        return []

def get_full_menu():
    query = """
        SELECT 
            id_menu,
            nama_item,
            keterangan,
            harga_twsari,
            harga_sedati,
            harga_kesambi,
            harga_tulangan,
            status,
            COALESCE(terjual_twsari, 0) AS terjual_twsari,
            COALESCE(terjual_sedati, 0) AS terjual_sedati,
            COALESCE(terjual_kesambi, 0) AS terjual_kesambi,
            COALESCE(terjual_tulangan, 0) AS terjual_tulangan
        FROM menu_items
        ORDER BY id_menu;
    """

    with engine.connect() as conn:
        df = pd.read_sql(text(query), conn)
    return df


def count_vouchers(filter_status=None, search=None):
    q = "SELECT count(*) FROM vouchers"
    clauses = []
    params = {}
    if filter_status == "aktif":
        clauses.append("status ILIKE 'active'")
    elif filter_status == "habis":
        clauses.append("balance = 0")
    if search:
        clauses.append("code ILIKE :search")
        params["search"] = f"%{search}%"
    if clauses:
        q += " WHERE " + " AND ".join(clauses)
    with engine.connect() as conn:
        return int(conn.execute(text(q), params).scalar())
    
def run_query(query, params=None):
    with engine.connect() as conn:
        if params:
            result = conn.execute(text(query), params)
        else:
            result = conn.execute(text(query))
        return pd.DataFrame(result.fetchall(), columns=result.keys())


def list_transactions(limit=5000):
    query = f"""
        SELECT 
            t.id,
            t.code,
            t.used_amount,
            t.tanggal_transaksi,
            t.branch,
            t.items,
            t.tunai,
            t.isvoucher,
            t.diskon,
            v.initial_value
        FROM transactions t
        LEFT JOIN vouchers v ON t.code = v.code
        ORDER BY t.tanggal_transaksi DESC
        LIMIT {limit};
    """
    return run_query(query)



def df_to_csv_bytes(df: pd.DataFrame):
    buf = BytesIO()
    df.to_csv(buf, index=False)
    buf.seek(0)
    return buf.read()


# ---------------------------
# Seller activation helper
# ---------------------------
def seller_activate_voucher(code, seller_input, buyer_name, buyer_phone):
    """
    Attempts to activate voucher by seller.
    Returns (ok: bool, message: str)
    Rules:
      - voucher must exist
      - voucher.seller must be present (assigned by admin) and equal to seller_input
      - voucher.status must not be 'active'
      - on success: set nama, no_hp, status='active', tanggal_penjualan = CURRENT_DATE
    """
    try:
        with engine.begin() as conn:
            row = conn.execute(text("""
                SELECT code, status, seller
                FROM vouchers
                WHERE code = :c
                FOR UPDATE
            """), {"c": code}).fetchone()

            if not row:
                return False, "Voucher tidak ditemukan."

            _, status_db, seller_db = row

            # not assigned to seller yet
            if seller_db is None or str(seller_db).strip() == "":
                return False, "Voucher belum diassign ke seller. Hubungi admin."

            # seller mismatch
            if str(seller_db).strip() != str(seller_input).strip():
                return False, "Nama seller tidak sesuai dengan data voucher. Aktivasi ditolak."

            # already active
            if status_db is not None and str(status_db).lower() == "active":
                return False, "Voucher sudah aktif dan terkunci."

            # all good -> update
            conn.execute(text("""
                UPDATE vouchers
                SET nama = :buyer_name,
                    no_hp = :buyer_phone,
                    status = 'active',
                    tanggal_penjualan = CURRENT_DATE
                WHERE code = :c
            """), {"buyer_name": buyer_name or None, "buyer_phone": buyer_phone or None, "c": code})

            return True, "Aktivasi berhasil. Voucher telah diaktifkan dan terkunci."

    except Exception as e:
        traceback.print_exc()
        return False, f"DB error saat aktivasi: {e}"

# ============================================================
# SESSION HELPERS
# ============================================================
def ensure_session_state():
    defaults = {
        "admin_logged_in": False,
        "seller_logged_in": False,
        "kasir_logged_in": False,

        "id_seller": None,
        "nama_seller": None,

        "page": None,
        "cabang": None,

        # REDEEM STATE
        "redeem_step": 1,
        "entered_code": "",
        "isvoucher": "no",
        "voucher_row": None,
        "selected_branch": None,
        "order_items": {},
        "checkout_total": 0,

        # Voucher admin edit
        "edit_code": None,
        "vouchers_page_idx": 0,
        "vouchers_per_page": 10,
        "show_success": False,
        "newbal": 0,
    }

    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v


def admin_logout():
    st.session_state.admin_logged_in = False
    st.session_state.page = None


def seller_logout():
    st.session_state.seller_logged_in = False
    st.session_state.id_seller = None
    st.session_state.nama_seller = None
    st.session_state.page = None


def kasir_logout():
    st.session_state.kasir_logged_in = False
    st.session_state.page = None
    st.session_state.cabang = None


# ============================================================
# INITIALIZE
# ============================================================
init_db()
ensure_session_state()

st.set_page_config(page_title="Pawon Sappitoe", layout="wide")
st.title("🎫 Pawon Sappitoe — Sistem Transaksi")


# ============================================================
# LOGIN PAGE (PAGE PERTAMA)
# ============================================================
def show_login_page():
    st.header("🔐 Masuk ke Sistem Pawon Sappitoe")

    tab_kasir, tab_daftar, tab_seller, tab_admin = st.tabs(
        ["Kasir", "Daftar Seller", "Seller", "Admin"]
    )

    # ADMIN LOGIN
    with tab_admin:
        pwd = st.text_input("Password Admin", type="password")
        if st.button("Login Admin"):
            if pwd == ADMIN_PASSWORD:
                st.session_state.admin_logged_in = True
                st.success("Login berhasil!")
                st.rerun()
            else:
                st.error("Password salah")

    # SELLER LOGIN
    with tab_seller:
        seller_id = st.text_input("ID Seller")
        if st.button("Login Seller"):
            if not seller_id.strip():
                st.error("ID tidak boleh kosong")
            else:
                with engine.connect() as conn:
                    row = conn.execute(
                        text("SELECT id_seller, nama_seller, status FROM seller WHERE id_seller = :id"),
                        {"id": seller_id.upper()}
                    ).fetchone()

                if not row:
                    st.error("ID seller tidak ditemukan")
                else:
                    sid, sname, sstatus = row
                    if sstatus != "diterima":
                        st.error("Akun belum disetujui admin")
                    else:
                        st.session_state.seller_logged_in = True
                        st.session_state.id_seller = sid
                        st.session_state.nama_seller = sname
                        st.success(f"Selamat datang, {sname}!")
                        st.rerun()

    # KASIR LOGIN
    with tab_kasir:
        pwd = st.text_input("Password Kasir", type="password")

        if st.button("Login Kasir"):
            if pwd in KASIR_PASSWORDS:
                st.session_state.kasir_logged_in = True
                st.session_state.page = "kasir"
                st.session_state.cabang = KASIR_PASSWORDS[pwd]  # ✅ SET CABANG OTOMATIS
                
                st.success(f"Login kasir berhasil — Cabang: {st.session_state.cabang.upper()}")
                st.rerun()
            else:
                st.error("Password kasir salah")


    # DAFTAR SELLER
    with tab_daftar:
        st.header("📋 Daftar Sebagai Seller")
        st.write("Silakan isi data berikut untuk mendaftar sebagai seller.")
        
        with st.form("form_daftar_seller"):
            nama = st.text_input("Nama lengkap")
            nohp = st.text_input("No HP")
            id_seller = st.text_input("Buat ID unik Anda (3 digit)").upper().strip()
            st.caption("ID terdiri dari 3 karakter huruf/angka, contoh: A9X, 4TB, B01")
        
            submit = st.form_submit_button("Daftar")
        
        if submit:
            # === Validasi basic ===
            if not id_seller:
                st.error("ID Seller tidak boleh kosong.")
                st.stop()

            if len(nohp) < 11 or len(nohp) > 13:
                st.error("Nomor HP seharusnya 11-13 digit.")
                st.stop()
            
            if len(id_seller) != 3:
                st.error("ID harus 3 karakter!")
                st.stop()
        
            if not nama.strip():
                st.error("Nama tidak boleh kosong.")
                st.stop()
        
            if not nohp.strip():
                st.error("No HP tidak boleh kosong.")
                st.stop()
        
            try:
                # === Cek ID apakah sudah ada ===
                with engine.connect() as conn:
                    exists = conn.execute(
                        text("SELECT 1 FROM seller WHERE id_seller = :id"),
                        {"id": id_seller}
                    ).fetchone()
        
                if exists:
                    st.error("❌ ID sudah digunakan seller lain! Silakan buat ID baru.")
                    st.stop()
        
                # === Simpan ke database ===
                with engine.begin() as conn:
                    conn.execute(
                        text("""
                            INSERT INTO seller (nama_seller, no_hp, status, id_seller)
                            VALUES (:nama, :no_hp, :status, :id_seller)
                        """),
                        {
                            "nama": nama.strip(),
                            "no_hp": nohp.strip(),
                            "status": "belum diterima",
                            "id_seller": id_seller,
                        }
                    )
        
                st.success(f"🎉 Pendaftaran berhasil! Admin akan segera memverifikasi akun Anda.")
                st.warning(
                    f"⚠️ **PENTING!** Simpan ID ini baik-baik untuk login nanti:\n\n"
                    f"🔐 **ID Seller Anda: {id_seller}**"
                )
                daftar_notification(
                    nama = nama,
                    nohp = nohp
                )
        
            except Exception as e:
                st.error("❌ Terjadi error saat menyimpan data")
                st.code(str(e))


# ============================================================
# ROUTING — WAJIB LOGIN
# ============================================================
if not (
    st.session_state.admin_logged_in or
    st.session_state.seller_logged_in or
    st.session_state.kasir_logged_in
):
    show_login_page()
    st.stop()



# ---------------------------
# Page: Aktivasi Voucher (admin) — inline edit (unchanged except access)
# ---------------------------
def page_admin():
    st.header("Halaman Admin")
    show_back_to_login_button("admin")
    tab_edit, tab_edit_seller, tab_laporan, tab_histori, tab_menu, tab_kupon= st.tabs(["Informasi Kupon", "Edit Seller", "Laporan warung", "Histori", "Kelola Menu", "Kelola Kupon"])

    with tab_edit:
        st.subheader("Informasi Kupon")

        # Search & Filter Inputs
        col1, col2, col3, col4 = st.columns([3, 2, 1, 1])
        with col1:
            kode_cari = st.text_input(
                "Cari kode kupon",
                placeholder="Masukkan kode",
            ).strip().upper()

        with col2:
            cari_berdasarkan = st.selectbox(
                "Cari berdasarkan",
                ["Kode", "Nama Seller", "Nama Pembeli"]
            )

        with col3:
            filter_status = st.selectbox(
                "Filter Status",
                ["semua", "active", "habis", "proses", "inactive"]
            )

        with col4:
            filter_nominal = st.selectbox(
                "Filter Nominal",
                ["semua", "50000", "100000", "200000"],
                index=0
            )
        
        # Query builder
        try:
            query = "SELECT * FROM vouchers"
            where_conditions = []
            params = {}
        
            # Filter status
            if filter_status != "semua":
                where_conditions.append("status = :status")
                params["status"] = filter_status
        
            # Filter kode
            if kode_cari:
                if cari_berdasarkan == "Kode":
                    where_conditions.append("UPPER(code) LIKE :val")
                elif cari_berdasarkan == "Nama Seller":
                    where_conditions.append("UPPER(seller) LIKE :val")
                elif cari_berdasarkan == "Nama Pembeli":
                    where_conditions.append("UPPER(nama) LIKE :val")
            
                params["val"] = f"%{kode_cari.upper()}%"
                    
            # Filter nominal
            if filter_nominal != "semua":
                where_conditions.append("initial_value = :nominal")
                params["nominal"] = int(filter_nominal)
        
            # Gabungkan SQL final
            if where_conditions:
                query += " WHERE " + " AND ".join(where_conditions)
        
            query += """
             ORDER BY 
                 CASE 
                    WHEN seller IS NOT NULL AND seller <> '' THEN 1
                    ELSE 2
                END,
                CASE 
                    WHEN status = 'active' THEN 1
                    WHEN status = 'habis' THEN 2
                    WHEN status = 'proses' THEN 3
                    WHEN status = 'inactive' THEN 4
                    ELSE 5
                END,
                CASE
                    WHEN initial_value = 50000 THEN 1
                    WHEN initial_value = 100000 THEN 2
                    WHEN initial_value = 200000 THEN 3
                    ELSE 4
                END,
                code ASC
            """
        
            with engine.connect() as conn:
                df_voucher = pd.read_sql(text(query), conn, params=params)
        
            if df_voucher.empty:
                st.info("Tidak ada voucher ditemukan.")
            else:
        
                # Format nominal
                format_nominal = lambda x: "-" if pd.isna(x) else f"Rp {int(x):,}"
                for col in ["initial_value", "balance", "tunai"]:
                    if col in df_voucher:
                        df_voucher[col] = df_voucher[col].apply(format_nominal)
        
                # Status + badge warna 🎨
                def status_badge(x):
                    if x == "active" or x == "Active":
                        return "🟢 active"
                    elif x == "habis" or x == "sold out":
                        return "🔴 habis" 
                    elif x == "proses":
                        return "🟡 proses"
                    return "⚪ inactive"
        
                df_voucher["status"] = df_voucher["status"].apply(status_badge)
        
                # Display tabel dengan badge
                st.dataframe(
                    df_voucher[
                        ["code", "nama", "no_hp", "status", "tanggal_aktivasi",
                         "initial_value", "balance", "tunai", "seller", "tanggal_penjualan"]
                    ],
                    use_container_width=True,
                )
        
                # Jika search cocok dengan 1 voucher → tampilkan form edit
                if kode_cari:
                    match_col = {
                        "Kode": "code",
                        "Nama Seller": "seller",
                        "Nama Pembeli": "nama"
                    }.get(cari_berdasarkan, "code")
                
                    matched = df_voucher[
                        df_voucher[match_col].astype(str).str.upper() == kode_cari.upper()
                    ]
                    if matched.empty:
                        st.warning("Tidak ditemukan voucher yang cocok dengan pencarian.")
                    else:
                        v = matched.iloc[0]
                        st.markdown("---")
                        st.subheader(f"✏️ Edit Kupon: {v['code']}")
        
                        with st.form(key=f"edit_form_{v['code']}"):
                            nama_in = st.text_input("Nama Pembeli", v["nama"] or "")
                            nohp_in = st.text_input("No HP Pembeli", v["no_hp"] or "")
                            status_in = st.selectbox(
                                "Status",
                                ["inactive", "active", "habis"],
                                index=["inactive", "active", "habis"].index(
                                    v["status"] if v["status"] in ["inactive", "active", "habis"] else "active"
                                )
                            )
                            nama_sell = st.text_input("Nama Seller", v["seller"] or "")
                            tgl_jual_in = st.date_input(
                                "Tanggal Penjualan",
                                value=v["tanggal_penjualan"] if isinstance(v["tanggal_penjualan"], (date, datetime)) else date.today()
                            )
                            tgl_aktif_in = st.date_input(
                                "Tanggal Aktivasi",
                                value=v["tanggal_aktivasi"] if isinstance(v["tanggal_aktivasi"], (date, datetime)) else date.today()
                            )
        
                            submit = st.form_submit_button("💾 Simpan Perubahan")
                            if submit:
                                if not nama_in:
                                    st.error("Nama Pembeli tidak boleh kosong.")
                                    st.stop()

                                if not nohp_in:
                                    st.error("Nomor HP Pembeli tidak boleh kosong.")
                                    st.stop()

                                if not nama_sell:
                                    st.error("Nama Seller tidak boleh kosong.")
                                    st.stop()
                                    
                                with engine.begin() as conn2:
                                    conn2.execute(text("""
                                        UPDATE vouchers
                                        SET nama = :nama,
                                            no_hp = :no_hp,
                                            status = :status,
                                            seller = :seller,
                                            tanggal_penjualan = :tgl_jual,
                                            tanggal_aktivasi = :tgl_aktif
                                        WHERE code = :code
                                    """), {
                                        "nama": nama_in.strip(),
                                        "no_hp": nohp_in.strip(),
                                        "status": status_in,
                                        "seller": nama_sell.strip(),
                                        "tgl_jual": tgl_jual_in.strftime("%Y-%m-%d"),
                                        "tgl_aktif": tgl_aktif_in.strftime("%Y-%m-%d"),
                                        "code": v["code"]
                                    })
        
                                st.success(f"Voucher {v['code']} berhasil diupdate.")
                                st.rerun()
        
        except Exception as e:
            st.error("❌ Terjadi error saat memuat data kupon.")
            st.code(str(e))
        
    with tab_histori:
            st.subheader("Histori Transaksi")
            df_tx = list_transactions(limit=5000)  # pastikan JOIN ke vouchers untuk ambil initial_value
            df_tx = df_tx.sort_values(by="id", ascending=False).reset_index(drop=True)

            if df_tx.empty:
                st.info("Belum ada transaksi")
            else:
                # Normalisasi kolom
                df_tx["tanggal_transaksi"] = pd.to_datetime(df_tx["tanggal_transaksi"]).dt.date
                df_tx["code"] = df_tx["code"].fillna("")
                df_tx["isvoucher"] = df_tx["isvoucher"].fillna("no")
                min_date = df_tx["tanggal_transaksi"].min()
                max_date = df_tx["tanggal_transaksi"].max()

                # Filter input
                col1, col2, col3, col4, col5 = st.columns([2, 1.3, 1.3, 1.3, 1.3])
                with col1:
                    search_code = st.text_input("Cari kode kupon untuk detail histori", "").strip()
                with col2:
                    start_date = st.date_input("Tanggal Mulai", value=min_date, min_value=min_date, max_value=max_date)
                with col3:
                    end_date = st.date_input("Tanggal Akhir", value=max_date, min_value=min_date, max_value=max_date)
                with col4:
                    filter_cabang = st.selectbox("Filter Cabang", ["semua", "Sedati", "Tawangsari", "Kesambi", "Tulangan"])
                with col5:
                    filter_kupon = st.selectbox("Filter Kupon", ["semua", "Kupon", "Non Kupon"])

                # Filter tanggal
                if start_date > end_date:
                    st.error("❌ Tanggal Mulai tidak boleh setelah Tanggal Akhir")
                    st.stop()
                df_tx = df_tx[(df_tx["tanggal_transaksi"] >= start_date) & (df_tx["tanggal_transaksi"] <= end_date)]

                # Filter cabang
                if filter_cabang != "semua":
                    df_tx = df_tx[df_tx["branch"] == filter_cabang]

                if filter_kupon != "semua":
                    if filter_kupon == "Kupon":
                        filter_kupon = "yes"
                    else:
                        filter_kupon = "no"
                    df_tx = df_tx[df_tx["isvoucher"] == filter_kupon]

                if df_tx.empty:
                    st.warning("Tidak ada transaksi dengan filter tersebut.")
                    st.stop()

                # 🔥 Hitung total uang (hanya dari used_amount)
                total_uang_filtered = df_tx["used_amount"].fillna(0).sum()
                total_cash_filtered = df_tx["tunai"].fillna(0).sum()
                total_kupon_filtered = total_uang_filtered - total_cash_filtered
                total_uang_diskon = df_tx["diskon"].fillna(0).sum()

                cola, colb, colc, cold = st.columns(4)
                with cola:
                    st.metric("Total Pendapatan", f"Rp {total_uang_filtered:,}")
                with colb:
                    st.metric("Total Pendapatan Cash", f"Rp {total_cash_filtered:,}")
                with colc:
                    st.metric("Total Pemakaian Kupon", f"Rp {total_kupon_filtered:,}")
                with cold:
                    st.metric("Total Pemakaian Diskon", f"Rp {total_uang_diskon:,}")
                

                # =============================
                # 🔍 PENCARIAN DETAIL KUPON (DITARUH DI PALING ATAS)
                # =============================
                if search_code:
                    # Filter hanya transaksi yang menggunakan voucher
                    df_voucher_top = df_tx[df_tx["isvoucher"] == "yes"]
                    df_filtered_top = df_voucher_top[df_voucher_top["code"].str.contains(search_code.upper(), case=False, na=False)]

                    st.subheader(f"Detail Kupon: {search_code.upper()}")

                    if df_filtered_top.empty:
                        st.warning(f"Tidak ada transaksi voucher untuk kupon {search_code}")
                    else:
                        with st.expander("ℹ️ Informasi Lengkap Kupon", expanded=True):
                            total_transaksi = len(df_filtered_top)

                            total_nominal = df_filtered_top["used_amount"].sum()
                            initial_val = df_filtered_top["initial_value"].iloc[0]

                            st.write(f"- Initial Value: Rp {initial_val:,}")
                            st.write(f"- Jumlah transaksi: {total_transaksi}")
                            st.write(f"- Total nominal terpakai: Rp {total_nominal:,}")

                            df_tmp_display = df_filtered_top.rename(columns={
                                "tanggal_transaksi": "Tanggal_transaksi",
                                "initial_value": "Initial_value",
                                "used_amount": "Total",
                                "tunai": "Tunai",
                                "branch": "Cabang",
                                "items": "Menu",
                                "code": "Kode",
                                "diskon": "Diskon"
                            })

                            st.dataframe(
                                df_tmp_display[["Tanggal_transaksi", "Kode", "Initial_value",
                                                "Total", "Tunai", "Diskon", "Cabang", "Menu"]],
                                use_container_width=True
                            )

                            st.download_button(
                                f"Download CSV {search_code.upper()}",
                                data=df_to_csv_bytes(df_tmp_display),
                                file_name=f"transactions_{search_code.upper()}.csv",
                                mime="text/csv"
                            )

                    st.markdown("---")
                df_display = df_tx.rename(columns={
                            "code": "Kode",
                            "used_amount": "Total",
                            "tanggal_transaksi": "Tanggal_transaksi",
                            "branch": "Cabang",
                            "items": "Menu",
                            "tunai": "Tunai",
                            "isvoucher": "kupon digunakan",
                            "initial_value": "Initial_value",
                            "diskon": "Diskon"
                        })
                        # df_display["Tunai"] = df_display["Tunai"].apply(lambda x: "tidak ada" if x == 0 else f"Rp {int(x):,}")
                # df_display["Tunai"] = df_display["Initi"].apply(lambda x: "tidak ada" if x == 0 else f"Rp {int(x):,}")
                # df_display["Total"] = df_display["Total"].apply(lambda x: "tidak ada" if x == 0 else f"Rp {int(x):,}")
                df_display["kupon digunakan"] = df_display["kupon digunakan"].apply(lambda x: "1" if x == "yes" else "0")
                df_display.loc[df_display["kupon digunakan"] == "0", "Total"] = df_display["Tunai"]
                df_display["Diskon"] = pd.to_numeric(df_display["Diskon"], errors="coerce").fillna(0)
                df_display.loc[df_display["Diskon"] > 0, "Total"] = df_display["Total"] + df_display["Diskon"]

                # Tampilkan tabel histori
                st.dataframe(
                    df_display[["id", "Tanggal_transaksi", "kupon digunakan", "Kode", "Initial_value",
                                "Total", "Tunai", "Diskon", "Cabang", "Menu"]],
                    use_container_width=True
                )

                st.download_button(
                    "Download CSV Transaksi",
                    data=df_to_csv_bytes(df_display),
                    file_name="transactions.csv",
                    mime="text/csv"
                )

            menu_list = []

            for idx, row in df_tx.iterrows():
                menu_items = str(row["items"]).split(",")  # Pecah per menu
                for item in menu_items:
                    item = item.strip()
            
                    # Contoh item: "NASI PUTIH x6"
                    match = re.match(r"(.+?) x(\d+)", item)
                    if match:
                        nama_menu = match.group(1).strip()
                        jumlah = int(match.group(2))
                        menu_list.append({
                            "Tanggal": row["tanggal_transaksi"],
                            "Menu": nama_menu,
                            "Jumlah": jumlah
                        })
            
            # Buat dataframe menu
            df_menu = pd.DataFrame(menu_list)
            
            if df_menu.empty:
                st.info("Tidak ada menu terjual pada tanggal tersebut.")
            else:
                df_pivot = df_menu.groupby("Menu")["Jumlah"].sum().reset_index()
            
                st.subheader("📊 Penjualan Per Menu")
                st.dataframe(df_pivot, use_container_width=True)

    with tab_edit_seller:
        st.subheader("Kelola Seller")
        tab_kepemilikan, tab_acc, tab_aktivasi = st.tabs(["Kepemilikan Kupon", "Penerimaan Seller", "Aktivasi Kupon"])
    
        with tab_kepemilikan:
            st.subheader("🎯 Serahkan Kupon ke Seller")
    
            try:
                with engine.connect() as conn:
                    df_seller = pd.read_sql("""
                        SELECT * FROM seller
                        WHERE status = 'diterima'
                        ORDER BY nama_seller ASC
                    """, conn)
    
                if df_seller.empty:
                    st.info("Belum ada seller yang berstatus 'diterima'.")
                else:
                    selected_seller = st.selectbox(
                        "Pilih Seller untuk diberikan kupon",
                        df_seller["nama_seller"].tolist()
                    )
    
                    selected_row = df_seller[df_seller["nama_seller"] == selected_seller].iloc[0]
                    seller_hp = selected_row["no_hp"]
                    id_unik = selected_row["id_seller"]
    
                    # Ambil voucher yang dimiliki seller
                    with engine.connect() as conn:
                        df_current_voucher = pd.read_sql(
                            text("""
                                SELECT code, initial_value, balance, status, tanggal_penjualan
                                FROM vouchers
                                WHERE seller = :seller
                                ORDER BY tanggal_penjualan DESC NULLS LAST
                            """),
                            conn,
                            params={"seller": selected_seller}
                        )
    
                    st.markdown("---")
                    st.subheader("📋 Informasi Seller")
                    st.write(f"**Nama:** {selected_seller}")
                    st.write(f"**No HP:** {seller_hp}")
                    st.write(f"**ID Seller:** {id_unik}")
                    st.write(f"**Jumlah kupon yang dimiliki:** {len(df_current_voucher)}")
    
                    if not df_current_voucher.empty:
                        st.markdown("**Kupon yang saat ini dimiliki:**")
                        df_sorted = df_current_voucher.sort_values(
                            by=["status", "tanggal_penjualan"],
                            ascending=[True, False]
                        )
                    
                        st.dataframe(
                            df_sorted[["code", "tanggal_penjualan", "status"]],
                            use_container_width=True
                        )
                    else:
                        st.info("Seller ini belum memiliki voucher apa pun.")
    
                    # Ambil voucher yang belum diassign
                    with engine.connect() as conn:
                        df_voucher = pd.read_sql("""
                            SELECT code, initial_value, balance, status
                            FROM vouchers
                            WHERE seller IS NULL OR TRIM(seller) = ''
                        """, conn)
    
                    st.markdown("---")
                    st.subheader(f"🧾 Pilih Kupon Baru untuk {selected_seller}")
    
                    if df_voucher.empty:
                        st.info("Semua kupon sudah diassign ke seller.")
                    else:
                        selected_vouchers = st.multiselect(
                            "Pilih kode kupon yang akan diberikan",
                            df_voucher["code"].tolist()
                        )
    
                        if st.button("💾 Simpan Penyerahan Kupon") and selected_vouchers:
                            try:
                                today = date.today()
    
                                with engine.begin() as conn2:
                                    for code in selected_vouchers:
                                        conn2.execute(
                                            text("""
                                                UPDATE vouchers
                                                SET seller = :seller,
                                                    tanggal_penjualan = :tgl
                                                WHERE code = :code
                                            """),
                                            {
                                                "seller": selected_seller,
                                                "tgl": today,
                                                "code": code
                                            }
                                        )
    
                                with engine.connect() as conn3:
                                    df_changed = pd.read_sql(
                                        text("""
                                            SELECT code, seller, tanggal_penjualan
                                            FROM vouchers
                                            WHERE code IN :codes
                                        """),
                                        conn3,
                                        params={"codes": tuple(selected_vouchers)}
                                    )
    
                                st.success(f"✅ {len(selected_vouchers)} kupon berhasil diassign ke seller {selected_seller}.")
                                st.markdown("### 🔍 Kupon yang baru saja diubah:")
                                st.dataframe(df_changed, use_container_width=True)
    
                            except Exception as e:
                                st.error("❌ Gagal menyimpan assign kupon ke database.")
                                st.code(str(e))
    
            except Exception as e:
                st.error("❌ Gagal memuat data seller atau kupon.")
                st.code(str(e))
    
        with tab_acc:
            st.subheader("🧾 Daftar Calon Seller")
            st.write("Berikut adalah daftar seller yang mendaftar. Klik 'Accept' untuk menyetujui pendaftaran.")
    
            try:
                with engine.connect() as conn:
                    df_seller_pending = pd.read_sql("""
                        SELECT * FROM seller
                        WHERE status = 'belum diterima'
                        ORDER BY nama_seller ASC
                    """, conn)
        
                if df_seller_pending.empty:
                    st.info("Belum ada data seller yang mendaftar.")
                else:
                    for idx, row in df_seller_pending.iterrows():
                        col1, col2, col3, col4 = st.columns([3, 3, 2, 2])
                        with col1:
                            st.write(f"**Nama:** {row['nama_seller']}")
                            st.write(f"No HP: {row['no_hp']}")
                        with col2:
                            st.write(f"Status: {row['status'] or '-'}")
                            st.write(f"ID Seller: {row['id_seller']}")
                        with col3:
                            if st.button("✅ Accept", key=f"accept_{row['nama_seller']}_{idx}"):
                                try:
                                    with engine.begin() as conn2:
                                        conn2.execute(
                                            text("""
                                                UPDATE seller
                                                SET status = 'diterima'
                                                WHERE nama_seller = :nama_seller
                                                  AND no_hp = :no_hp
                                            """),
                                            {"nama_seller": row["nama_seller"], "no_hp": row["no_hp"]}
                                        )
                                    st.success(f"Seller {row['nama_seller']} diterima ✅")
                                    st.rerun()
                                except Exception as e:
                                    st.error(f"Gagal update status seller: {e}")
        
                        with col4:
                            if st.button("🗑️ Hapus", key=f"hapus_{row['nama_seller']}_{idx}"):
                                try:
                                    with engine.begin() as conn3:
                                        conn3.execute(
                                            text("""
                                                DELETE FROM seller
                                                WHERE nama_seller = :nama_seller
                                                  AND no_hp = :no_hp
                                            """),
                                            {"nama_seller": row["nama_seller"], "no_hp": row["no_hp"]}
                                        )
                                    st.warning(f"Data seller {row['nama_seller']} telah dihapus ❌")
                                    st.rerun()
                                except Exception as e:
                                    st.error(f"Gagal menghapus data seller: {e}")
        
            except Exception as e:
                st.error("❌ Gagal mengambil data seller dari database.")
                st.code(str(e))
                
        with tab_aktivasi:
            st.subheader("🎟️ Daftar Kupon Berstatus 'Proses'")
            st.write("Berikut adalah daftar kupon yang masih dalam proses validasi.")
        
            try:
                # Ambil data kupon yang masih proses
                with engine.connect() as conn:
                    df_voucher_pending = pd.read_sql("""
                        SELECT 
                            code,
                            initial_value,
                            status,
                            seller,
                            nama
                        FROM vouchers
                        WHERE status = 'proses'
                        ORDER BY code ASC
                    """, conn)
        
                if df_voucher_pending.empty:
                    st.info("Belum ada kupon yang ingin diaktivasi.")
                else:
                    for idx, row in df_voucher_pending.iterrows():
                        col1, col2, col3, col4 = st.columns([3, 3, 2, 2])
        
                        # Kolom informasi
                        with col1:
                            st.write(f"**Seller:** {row['seller'] or '-'}")
                            st.write(f"**Pembeli:** {row['nama'] or '-'}")
        
                        with col2:
                            st.write(f"Kode Kupon: **{row['code']}**")
                            st.write(f"Initial Value: Rp {int(row['initial_value']):,}")
        
                        # Tombol Accept
                        with col3:
                            if st.button("✅ Aktivasi", key=f"accept_{row['code']}_{idx}"):
                                try:
                                    today = date.today()
                                    with engine.begin() as conn2:
                                        conn2.execute(
                                            text("""
                                                UPDATE vouchers
                                                SET status = 'active', tanggal_aktivasi = :tanggal_aktivasi
                                                WHERE code = :code
                                            """),
                                            {"code": row["code"], "tanggal_aktivasi": today}
                                        )
                                    st.success(f"Kupon {row['code']} telah diaktivasi ✅")
                                    st.rerun()
                                except Exception as e:
                                    st.error(f"Gagal mengubah status kupon: {e}")
        
                        # Tombol Hapus
                        with col4:
                            if st.button("🗑️ Tolak", key=f"hapus_{row['code']}_{idx}"):
                                try:
                                    with engine.begin() as conn3:
                                        conn3.execute(
                                            text("""
                                                UPDATE vouchers
                                                SET status = 'inactive', nama = NULL, no_hp = NULL, tanggal_aktivasi = NULL
                                                WHERE code = :code
                                            """),
                                            {"code": row["code"]}
                                        )
                                    st.warning(f"Data kupon {row['code']} telah dihapus ❌")
                                    st.rerun()
                                except Exception as e:
                                    st.error(f"Gagal menghapus data kupon: {e}")
        
            except Exception as e:
                st.error("❌ Gagal mengambil data kupon dari database.")
                st.code(str(e))

    with tab_laporan:
        st.subheader("Laporan Warung")
    
        # Tabs untuk membagi laporan
        tab_voucher, tab_transaksi, tab_seller = st.tabs(["Kupon", "Transaksi", "Seller"])
    
        df_vouchers = list_vouchers(limit=5000)
        df_tx = list_transactions(limit=100000)
    
        if "tanggal_transaksi" in df_tx.columns:
            df_tx["tanggal_transaksi"] = pd.to_datetime(df_tx["tanggal_transaksi"])
    
        # # ===== TAB Voucher =====
        with tab_voucher:
            st.subheader("📊 Laporan Kupon")
        
            # ============================
            # 🔍 FILTER
            # ============================
            with st.expander("🔎 Filter Laporan"):
                colf1, colf2 = st.columns(2)
        
                # Filter cabang
                branch_filter = colf1.selectbox(
                    "Pilih Cabang",
                    ["Semua", "Sedati", "Tawangsari", "Kesambi", "Tulangan"]
                )
        
                # Filter tanggal
                tanggal_awal = colf1.date_input("Tanggal Awal", value=date.today().replace(day=1))
                tanggal_akhir = colf2.date_input("Tanggal Akhir", value=date.today())
        
            # ============================
            # 📥 LOAD DATA
            # ============================
            vouchers = pd.read_sql("SELECT * FROM vouchers", engine)
            transactions = pd.read_sql("SELECT * FROM transactions", engine)
        
            # Normalisasi
            vouchers["initial_value"] = vouchers["initial_value"].fillna(0).astype(float)
            vouchers["balance"] = vouchers["balance"].fillna(0).astype(float)
            vouchers["tanggal_penjualan"] = pd.to_datetime(vouchers["tanggal_penjualan"], errors="coerce")
            transactions["tanggal_transaksi"] = pd.to_datetime(transactions.get("tanggal_transaksi"), errors="coerce")
        
            # ============================
            # 🔎 APPLY FILTER
            # ============================
        
            # Filter cabang pada transaksi (bukan pada voucher)
            if branch_filter != "Semua":
                transactions = transactions[transactions["branch"] == branch_filter]
        
            # Filter tanggal transaksi
            transactions = transactions[
                (transactions["tanggal_transaksi"].dt.date >= tanggal_awal) &
                (transactions["tanggal_transaksi"].dt.date <= tanggal_akhir)
            ]
        
            # ============================
            # 🔢 PERHITUNGAN SUMMARY
            # ============================
            vouchers["used_value"] = vouchers["initial_value"] - vouchers["balance"]
        
            summary = {
                "total_voucher_dijual": len(vouchers),
                "total_voucher_aktif": len(vouchers[vouchers["status"] == "active"]),
                "total_voucher_inaktif": len(vouchers[vouchers["status"] == "inactive"]),
                "total_voucher_habis": len(vouchers[vouchers["balance"] <= 0]),  
                "total_voucher_terpakai": len(transactions["code"].unique()),
                "total_saldo_belum_terpakai": vouchers["balance"].sum(),
                "total_saldo_sudah_terpakai": vouchers["used_value"].sum(),
            }
        
            # ============================
            # 🟦 SUMMARY CARDS
            # ============================
            col1, col2, col3 = st.columns(3)
            col1.metric("🎫 Total Kupon Dijual", summary["total_voucher_dijual"])
            col2.metric("📌 Kupon Aktif", summary["total_voucher_aktif"])
            col3.metric("🚫 Kupon Inaktif", summary["total_voucher_inaktif"])
        
            col4, col5, col6 = st.columns(3)
            col4.metric("🔥 Kupon Habis", summary["total_voucher_habis"])  # 🔴 Tambahan
            col5.metric("💸 Saldo Sudah Terpakai", f"Rp {summary['total_saldo_sudah_terpakai']:,.0f}")
            col6.metric("💰 Saldo Belum Terpakai", f"Rp {summary['total_saldo_belum_terpakai']:,.0f}")

            col7, _, _ = st.columns(3)
            col7.metric("✅ Total Kupon Terpakai", summary["total_voucher_terpakai"])

            st.markdown("---")
        
            # ============================
            # 📈 GRAFIK TRANSAKSI PER HARI (TERFILTER)
            # ============================
            if not transactions.empty:
                redeem_daily = transactions.groupby(transactions["tanggal_transaksi"].dt.date).size()
                st.subheader("📈 Penukaran Kupon per Hari")
                st.line_chart(redeem_daily)
            else:
                st.info("Belum ada transaksi untuk filter ini.")
        
            st.markdown("---")
        
            # ============================
            # 📊 TOTAL NILAI TRANSAKSI PER HARI
            # ============================
            if not transactions.empty:
                total_transaksi = transactions.groupby(transactions["tanggal_transaksi"].dt.date)["used_amount"].sum()
                st.subheader("📊 Total Nilai Transaksi per Hari")
                st.bar_chart(total_transaksi)
        
            st.markdown("---")
        
            # ============================
            # 🧩 PIE CHART STATUS (TIDAK TERPENGARUH FILTER)
            # ============================
            st.subheader("🧩 Status Kupon (Semua Data)")
            
            status_count = vouchers["status"].value_counts().reset_index()
            status_count.columns = ["status", "jumlah"]
        
            color_map = {
                "active": "#23C552",
                "habis": "#FF4646",
                "inactive": "#A8A8A8",
                "soldout": "#C60000"
            }
        
            fig = px.pie(
                status_count,
                names="status",
                values="jumlah",
                title="Distribusi Status Kupon",
                color="status",
                color_discrete_map=color_map,
                hole=0.35
            )
        
            fig.update_layout(
                legend_title="Status Kupon",
                title_x=0.5,
                margin=dict(t=40, b=10, l=10, r=10)
            )
        
            st.plotly_chart(fig, use_container_width=False, width=500)
        
            st.markdown("---")
        
            # ============================
            # 📥 EXPORT CSV (SETELAH FILTER)
            # ============================
            csv = vouchers.to_csv(index=False).encode("utf-8")
            st.download_button(
                label="📥 Download Laporan Voucher (CSV)",
                data=csv,
                file_name="voucher_report.csv",
                mime="text/csv",
            )

    
        # # ===== TAB Transaksi ====
        with tab_transaksi:
            st.subheader("📊 Ringkasan Transaksi")
        
            # Load data transaksi
            df_tx = pd.read_sql("SELECT * FROM transactions", engine)
        
            if df_tx.empty:
                st.info("Belum ada data transaksi.")
            else:
                # Pastikan kolom tanggal dalam bentuk datetime
                df_tx["tanggal_transaksi"] = pd.to_datetime(df_tx["tanggal_transaksi"])
        
                # =============================================
                # 🔍 FILTER AREA
                # =============================================
                st.markdown("### 🔎 Filter Transaksi")
        
                # 4 kolom: Dari, Sampai, Cabang, Jenis Transaksi
                f1, f2, f3, f4 = st.columns([1, 1, 1, 1])
        
                # Filter tanggal "DARI"
                with f1:
                    start_date = st.date_input(
                        "Dari tanggal",
                        df_tx["tanggal_transaksi"].min().date()
                    )
        
                # Filter tanggal "SAMPAI"
                with f2:
                    end_date = st.date_input(
                        "Sampai tanggal",
                        df_tx["tanggal_transaksi"].max().date()
                    )
        
                # Filter CABANG
                with f3:
                    cabang_list = ["Semua"] + sorted(
                        df_tx["branch"].dropna().unique().tolist()
                    )
                    selected_cabang = st.selectbox("Cabang", cabang_list)
        
                # Filter JENIS TRANSAKSI (Kupon / Non Kupon)
                with f4:
                    filter_kupon = st.selectbox(
                        "Jenis transaksi",
                        ["Semua", "Kupon", "Non Kupon"]
                    )
        
                # =============================================
                # 🔄 APPLY FILTER TANGGAL + CABANG
                # =============================================
                df_filtered = df_tx[
                    (df_tx["tanggal_transaksi"].dt.date >= start_date) &
                    (df_tx["tanggal_transaksi"].dt.date <= end_date)
                ].copy()
        
                if selected_cabang != "Semua":
                    df_filtered = df_filtered[df_filtered["branch"] == selected_cabang]
        
                # =============================================
                # 🎫 NORMALISASI KUPON (kolom isvoucher: yes/no)
                # =============================================
                if not df_filtered.empty and "isvoucher" in df_filtered.columns:
                    df_filtered["isvoucher_norm"] = (
                        df_filtered["isvoucher"]
                        .astype(str)
                        .str.strip()
                        .str.lower()
                        .map({"yes": 1, "no": 0})
                        .fillna(0)
                        .astype(int)
                    )
        
                    if filter_kupon == "Kupon":
                        df_filtered = df_filtered[df_filtered["isvoucher_norm"] == 1]
                    elif filter_kupon == "Non Kupon":
                        df_filtered = df_filtered[df_filtered["isvoucher_norm"] == 0]
                else:
                    # Kalau kolom isvoucher tidak ada, bikin norm = 0
                    if not df_filtered.empty:
                        df_filtered["isvoucher_norm"] = 0
        
                # =============================================
                # CEK SETELAH SEMUA FILTER
                # =============================================
                if df_filtered.empty:
                    st.info("Tidak ada transaksi pada filter yang dipilih.")
                else:
                    # Pastikan used_amount numerik
                    df_filtered["used_amount"] = pd.to_numeric(
                        df_filtered["used_amount"],
                        errors="coerce"
                    ).fillna(0)
        
                    # =============================================
                    # SUMMARY TRANSAKSI
                    # =============================================
                    total_tx = len(df_filtered)
                    total_tx_nominal = df_filtered["used_amount"].sum()
                    avg_tx = df_filtered["used_amount"].mean() if total_tx > 0 else 0
        
                    st.write(f"- Total transaksi: {total_tx:,}")
                    st.write(f"- Total nominal digunakan: Rp {int(total_tx_nominal):,}")
                    st.write(f"- Rata-rata nominal transaksi: Rp {int(avg_tx):,}")
        
                    st.markdown("---")
        
                    # =======================================================
                    # 🏪 TRANSAKSI PER CABANG
                    # =======================================================
                    st.subheader("🏪 Total Transaksi per Cabang")
        
                    # Pakai size() biar nggak tergantung kolom tertentu
                    tx_count = (
                        df_filtered.groupby("branch")
                        .size()
                        .reset_index(name="Jumlah Transaksi")
                    )
                    tx_count.rename(columns={"branch": "Cabang"}, inplace=True)
                    st.bar_chart(tx_count, x="Cabang", y="Jumlah Transaksi")
        
                    st.subheader("💰 Total Nominal per Cabang")
                    tx_sum = (
                        df_filtered.groupby("branch")["used_amount"]
                        .sum()
                        .reset_index()
                    )
                    tx_sum.columns = ["Cabang", "Total Nominal"]
                    st.bar_chart(tx_sum, x="Cabang", y="Total Nominal")
        
                    st.markdown("---")
        
                    # =======================================================
                    # 🏆 TOP 5 KUPON PALING SERING DIPAKAI
                    # =======================================================
                    st.subheader("🏆 Top 5 Kupon Paling Sering Digunakan")
        
                    # Hanya ambil transaksi yang memang kupon
                    df_voucher = df_filtered[df_filtered["isvoucher_norm"] == 1].copy()
        
                    # Buang code kosong / null
                    df_voucher = df_voucher[
                        df_voucher["code"].notna() & (df_voucher["code"] != "")
                    ]
        
                    if df_voucher.empty:
                        st.info("Tidak ada transaksi kupon pada filter ini.")
                    else:
                        top_voucher = (
                            df_voucher.groupby("code")["code"]
                            .count()
                            .sort_values(ascending=False)
                            .head(5)
                            .reset_index(name="Jumlah Transaksi")
                        )
        
                        st.table(top_voucher)
                        st.bar_chart(top_voucher, x="code", y="Jumlah Transaksi")
        
                    st.markdown("---")
        
                    # =======================================================
                    # 📥 DOWNLOAD DATA TRANSAKSI TERFILTER
                    # =======================================================
                    st.subheader("📥 Download Data Transaksi")
        
                    csv_data = df_filtered.to_csv(index=False).encode("utf-8")
        
                    st.download_button(
                        label="📥 Download Transaksi (CSV)",
                        data=csv_data,
                        file_name="transaksi_filter.csv",
                        mime="text/csv"
                    )

            # ===== TAB Seller =====
            with tab_seller:
                st.subheader("📊 Analisis Kupon per Seller")
            
                if "seller" not in df_vouchers.columns:
                    st.warning("Kolom 'seller' tidak tersedia.")
                else:
                    df_vouchers["seller"] = df_vouchers["seller"].fillna("-")
                    df_seller_only = df_vouchers[df_vouchers["seller"] != "-"].copy()
                
                    if df_seller_only.empty:
                        st.info("Belum ada kupon yang dibawa seller.")
                    else:
                        # --- Normalize status for clean analytics ---
                        df_seller_only["status_clean"] = (
                            df_seller_only["status"].astype(str).str.lower().replace({
                                "sold out": "habis"
                            })
                        )
                    
                        status_pivot = (
                            df_seller_only.pivot_table(
                                index="seller",
                                columns="status_clean",
                                values="code",
                                aggfunc="count",
                                fill_value=0
                            )
                            .reset_index()
                        )
                    
                        # Pastikan kolom lengkap
                        for col in ["active", "habis", "inactive"]:
                            if col not in status_pivot.columns:
                                status_pivot[col] = 0
                    
                        status_pivot["Total"] = status_pivot[["active", "habis", "inactive"]].sum(axis=1)
                        status_pivot = status_pivot.sort_values(by="Total", ascending=False)
                    
                        st.dataframe(status_pivot, use_container_width=True)
                    
                        fig = px.bar(
                            status_pivot,
                            x="seller",
                            y=["active", "habis", "inactive"],
                            title="Distribusi Status Kupon per Seller",
                            color_discrete_map={
                                "active": "#2ecc71",   # Hijau
                                "habis": "#e74c3c",    # Merah
                                "inactive": "#bdc3c7"  # Abu-abu
                            }
                        )
                        fig.update_layout(
                            xaxis_tickangle=-30,
                            legend_title_text="Status"
                        )
                        st.plotly_chart(fig, use_container_width=True) 
    with tab_menu:
        st.subheader("Kelola Menu")
        tab1, tab2, tab3 = st.tabs(["📋 Lihat Menu", "➕ Tambah Menu", "✏️ Edit / Hapus Menu"])


        # ============================
        # TAB 1 — LIST MENU
        # ============================
        with tab1:
            st.subheader("📋 Daftar Menu")

            df_menu = get_full_menu()

            st.dataframe(
                df_menu,
                use_container_width=True,
                height=500
        )

        # ============================
        # TAB 2 — ADD MENU
        # ============================
        with tab2:
            st.header("➕ Tambah Menu Baru")

            kategori = get_kategori_list()

            # Tambahkan opsi dummy di awal
            options = ["-- Pilih Kategori --"] + kategori + ["+ Tambah kategori baru"]

            kategori_selected = st.selectbox(
                "Kategori",
                options=options,
                index=0,
                key="kategori_tambah_select",
            )

            kategori_value = None

            if kategori_selected == "+ Tambah kategori baru":
                kategori_value = st.text_input(
                    "Kategori baru",
                    key="kategori_tambah_baru"
                )
            elif kategori_selected != "-- Pilih Kategori --":
                kategori_value = kategori_selected

            nama_item = st.text_input("Nama Item")
            keterangan = st.text_area("Keterangan")

            harga_sedati = st.text_input("Harga Sedati (boleh kosong)")
            harga_twsari = st.text_input("Harga Tawangsari (boleh kosong)")
            harga_kesambi = st.text_input("Harga Kesambi (boleh kosong)")
            harga_tulangan = st.text_input("Harga Tulangan (boleh kosong)")

            if st.button("Simpan Menu"):
            # validasi sederhana
                if not kategori_value:
                    st.error("Kategori belum dipilih / diisi.")
                else:
                    add_menu_item(
                        kategori=kategori_value,
                        nama_item=nama_item,
                        keterangan=keterangan,
                        harga_sedati=harga_sedati,
                        harga_twsari=harga_twsari,
                        harga_kesambi=harga_kesambi,
                        harga_tulangan=harga_tulangan,
                    )
                    st.success("Menu berhasil ditambahkan!")
                    st.rerun()


        # ============================
        # TAB 3 — EDIT / DELETE MENU
        # ============================
        with tab3:
            st.header("✏️ Edit atau Hapus Menu")

            menu_list = list_all_menu()

            if not menu_list:
                st.info("Belum ada menu untuk diedit.")
            else:
                # Dropdown pilih item berdasarkan ID + nama
                pilih = st.selectbox(
                    "Pilih menu yang akan diedit",
                    [(m["id_menu"], f"{m['nama_item']} - {m['kategori']}") for m in menu_list],
                    format_func=lambda x: x[1]
                )

                id_menu = pilih[0]

                # Ambil data lama
                selected = next(m for m in menu_list if m["id_menu"] == id_menu)

                # FORM EDIT
                kategori = st.text_input("Kategori", value=selected["kategori"])
                nama_item = st.text_input("Nama Item", value=selected["nama_item"])
                keterangan = st.text_area("Keterangan", value=selected["keterangan"])
                status_options = ["aktif", "inaktif"]
                default_status = (selected.get("status") or "").strip().lower()
                status = st.selectbox(
                    "Status",
                    status_options,
                    index=status_options.index(default_status)
                )

                harga_sedati = st.text_input("Harga Sedati", value=str(selected["harga_sedati"] or ""))
                harga_twsari = st.text_input("Harga Tawangsari", value=str(selected["harga_twsari"] or ""))
                harga_kesambi = st.text_input("Harga Kesambi", value=str(selected["harga_kesambi"] or ""))
                harga_tulangan = st.text_input("Harga Tulangan", value=str(selected["harga_tulangan"] or ""))

                col1, col2 = st.columns(2)

                with col1:
                    if st.button("Simpan Perubahan"):
                        update_menu_item(
                            id_menu, kategori, nama_item, keterangan,
                            harga_sedati, harga_twsari, harga_kesambi, harga_tulangan, status
                        )
                        st.success("Menu berhasil diperbarui!")
                        st.rerun()

                with col2:
                    if st.button("Hapus Menu 🗑️"):
                        delete_menu_item(id_menu)
                        st.warning("Menu berhasil dihapus!")
                        st.rerun()
    with tab_kupon:
        st.subheader("🎫 Buat Kupon Baru")

        jenis_list = []
        with engine.connect() as conn:
            df_jenis = pd.read_sql("SELECT jenis_kupon FROM jenis_db", conn)
            jenis_list = df_jenis['jenis_kupon'].tolist()

        col1, col2 = st.columns(2)
        
        with col1:
            jenis_kupon = st.text_input("Jenis Kupon (contoh: Reguler, Promo, Makanan)")
            initial_value = st.number_input("Initial Value", min_value=0)
            jumlah_kode = st.number_input("Jumlah Kupon yang Dibuat", min_value=1, value=1)

        with col2:
            awal_berlaku = st.date_input("Tanggal Awal Berlaku")
            akhir_berlaku = st.date_input("Tanggal Akhir Berlaku")

        if st.button("🚀 Generate Kupon"):
            if akhir_berlaku < awal_berlaku:
                st.error("Tanggal akhir tidak boleh lebih awal dari tanggal mulai!")
                st.stop()

            if jenis_kupon.strip() == "":
                st.error("Jenis kupon wajib diisi!")
                st.stop()

            insert_jenis_if_not_exists(jenis_kupon, awal_berlaku, akhir_berlaku)

            created_codes = []

            for _ in range(jumlah_kode):
                new_code = generate_code(6)   

                while kode_exists(new_code):
                    new_code = generate_code(6)

                insert_voucher(
                    new_code,
                    initial_value,
                    jenis_kupon,
                    awal_berlaku,
                    akhir_berlaku
                )

                created_codes.append(new_code)

            st.success(f"{len(created_codes)} kupon berhasil dibuat! 🎉")

            # Tampilan kode kupon ala kartu
            st.markdown("### 🎟️ Kode Kupon Baru")

            for c in created_codes:
                st.markdown(
                    f"""
                    <div style="
                        padding:10px 18px;
                        background:#f0f2f6;
                        border-radius:8px;
                        border:1px solid #d9d9d9;
                        width:220px;
                        margin-bottom:6px;
                        font-size:20px;
                        font-weight:600;
                        letter-spacing:2px;
                        text-align:center;
                    ">
                    {c}
                    </div>
                    """,
                    unsafe_allow_html=True
                )


# ---------------------------
# Page: Seller Activation (seller-only)
# ---------------------------
def page_seller_activation():
    st.header("Halaman Seller")
    show_back_to_login_button("seller")
    st.subheader("Aktivasi Kupon")

    st.info(
        "Masukkan kode kupon, setelah itu masukkan nama dan nomer HP pembeli kupon untuk aktivasi."
    )

    with st.form(key="seller_activation_form"):
        seller_name_input = st.session_state.get("nama_seller", "-")
        st.success(f"Seller: **{seller_name_input}** ")
        kode = st.text_input("Kode Kupon").strip().upper()
        buyer_name_input = st.text_input("Nama Pembeli").strip()
        buyer_phone_input = st.text_input("No HP Pembeli").strip()
        submit = st.form_submit_button("Simpan dan Aktifkan")

    if submit:
        if not kode:
            st.error("Masukkan kode kupon.")
            return

        if not seller_name_input:
            st.error("Masukkan nama seller (sesuai yang terdaftar pada voucher).")
            return

        try:
            with engine.begin() as conn:
                # Cek apakah voucher ada dan seller cocok
                result = conn.execute(
                    text("""
                        SELECT seller, status 
                        FROM vouchers 
                        WHERE code = :code
                    """),
                    {"code": kode}
                ).fetchone()

                if not result:
                    st.error("Kode kupon tidak ditemukan.")
                    return

                db_seller, db_status = result

                # Jika voucher belum diassign seller oleh admin
                if not db_seller or db_seller.strip() == "":
                    st.error("Kupon belum diserahkan ke seller mana pun. Aktivasi ditolak.")
                    return

                # Jika seller input tidak cocok dengan seller di database
                if db_seller != st.session_state.nama_seller:
                    st.error("Voucher bukan milik Anda.")
                    return

                # if tanggal_penjualan and tanggal_aktivasi < tanggal_penjualan:
                #     st.error(f"❌ Tanggal Aktivasi tidak boleh sebelum Tanggal Penjualan ({tanggal_penjualan})")
                #     tanggal_aktivasi = None  # opsional: reset nilai agar user pilih ulang
                #     return

                # Jika sudah aktif sebelumnya
                if db_status and db_status.lower() == "active":
                    st.warning("Kupon ini sudah diaktivasi sebelumnya.")
                    return

                # Update data voucher
                conn.execute(
                    text("""
                        UPDATE vouchers
                        SET nama = :nama,
                            no_hp = :no_hp,
                            status = 'proses'
                        WHERE code = :code
                    """),
                    {
                        "nama": buyer_name_input,
                        "no_hp": buyer_phone_input,
                        "code": kode,
                    }
                )

            st.success(f"✅ Kupon {kode} berhasil diaktivasi untuk pembeli {buyer_name_input}.")
            aktivasi_notification(
                voucher_code=kode,
                seller_name=seller_name_input,
                buyer_name=buyer_name_input,
                buyer_phone=buyer_phone_input
            )

        except Exception as e:
            st.error("❌ Terjadi kesalahan saat mengupdate data kupon.")
            st.code(str(e))

    st.warning(
        "Jika ingin memeriksa status kupon anda, silahkan pakai fitur Lacak Kupon di bawah. "
        "Jika perlu koreksi, minta admin untuk ubah data."
    )

    st.markdown("---")
    st.subheader("Lacak kupon")
    
    lacak_code = st.text_input("Masukkan kode kupon").strip().upper()
    if st.button("Cek Kupon"):
        if not lacak_code:
            st.error("Tidak bisa dilacak, kode belum diinput.")
            return
        else:
            with engine.connect() as conn:
                row = conn.execute(text("""
                    SELECT 
                        code,
                        initial_value,
                        status,
                        tanggal_aktivasi
                    FROM vouchers
                    WHERE code = :code
                    LIMIT 1
                """), {"code": lacak_code}).fetchone()

            if not row:
                st.error("Tidak ada kupon yang ditemukan.")
                return
            else:
                code, initial_value, status, tanggal_aktivasi = row
                if status == "active":
                    st.success(f"Kupon berkode {code} Rp. {initial_value}, berstatus {status} dengan tanggal aktivasi: {tanggal_aktivasi}.")
                elif status ==  "proses":
                    st.info(f"Kupon berkode {code} Rp. {initial_value}, berstatus {status}. Segera bayar kupon agar dapat diaktivasi oleh admin.")
                elif status == "inavtive" :
                    st.info(f"Kupon berkode {code} Rp. {initial_value}, berstatus {status}. Aktivasi kupon ditolak oleh admin, hubungi admin untuk info lebih lanjut.")
                else:
                    st.info(f"Kupon sedang berstatus {status}.")

def reset_redeem_state():
    for key in [
        "redeem_step",
        "entered_code",
        "order_items",
        "checkout_total",
        "isvoucher",
        "voucher_row",
        "newbal",
        "show_success"
    ]:
        st.session_state.pop(key, None)

def validate_voucher_and_show_info(row, total):
    """
    row = hasil query voucher
    total = total pembayaran
    Fungsi ini akan mengupdate session_state:
    - isvoucher
    - voucher_row
    - redeem_error
    """
    code, initial_value, balance, nama, no_hp, status, seller, tanggal_aktivasi, awal_berlaku, akhir_berlaku = row
    today = date.today()

    # Cek masa berlaku
    if today < awal_berlaku:
        st.session_state["redeem_error"] = (
            f"⛔ Kupon belum dapat digunakan.\n"
            f"Berlaku mulai: {awal_berlaku}"
        )
        return

    if today > akhir_berlaku:
        st.session_state["redeem_error"] = (
            f"⛔ Kupon sudah tidak berlaku.\n"
            f"Masa berlaku berakhir: {akhir_berlaku}"
        )
        return

    # Normalisasi status
    status_normalized = (status or "").strip().lower()

    if status_normalized == "inactive":
        st.session_state["redeem_error"] = "⛔ Kupon belum aktif."
        return

    if status_normalized == "habis" or balance <= 0:
        st.session_state["redeem_error"] = "⛔ Saldo kupon sudah habis."
        return

    if status_normalized == "proses":
        st.session_state["redeem_error"] = "⛔ Kupon masih belum diaktivasi admin."
        return

    if status_normalized != "active":
        st.session_state["redeem_error"] = f"⛔ Status kupon tidak valid: {status}"
        return

    # Cek apakah H+1
    if tanggal_aktivasi is None:
        st.session_state["redeem_error"] = "⛔ kupon belum diaktifkan."
        return

    if hasattr(tanggal_aktivasi, "date"):
        tgl_aktivasi = tanggal_aktivasi.date()
    else:
        try:
            tgl_aktivasi = datetime.strptime(str(tanggal_aktivasi), "%Y-%m-%d").date()
        except:
            tgl_aktivasi = None

    if tgl_aktivasi == date.today():
        st.session_state["redeem_error"] = "⛔ Kupon hanya bisa digunakan H+1 setelah aktivasi."
        return

    # Jika semua OK → simpan data voucher
    st.session_state.isvoucher = "yes"
    st.session_state.voucher_row = row

    # Tampilkan info voucher
    st.write(f"Kupon: {code}")
    st.write(f"- Atas nama: {nama}")
    st.write(f"- No HP: {no_hp}")
    st.write(f"- Nilai awal: Rp {int(initial_value):,}")
    st.write(f"**Sisa Saldo Kupon: Rp {int(balance):,}**")

    saldo = int(balance)
    if total > saldo:
        st.warning(f"Saldo kupon kurang {total - saldo:,} — sisanya harus bayar cash.")


def page_kasir():
    st.header("Halaman Transaksi Kasir")
    show_back_to_login_button("kasir")
    tukar_kupon, riwayat_pesan = st.tabs(["Pemesanan", "Riwayat Pemesanan"])

    with tukar_kupon:
        st.header("Pemesanan")
    
        # # Inisialisasi state
        # if "redeem_step" not in st.session_state:
        #     st.session_state.redeem_step = 1
        # if "entered_code" not in st.session_state:
        #     st.session_state.entered_code = ""
    
        # ---------------------------
        # STEP 1 — PILIH MENU
        # ---------------------------
        # =========================
        # INIT STATE
        # =========================
        if "redeem_step" not in st.session_state:
            st.session_state.redeem_step = 1

        if "order_items" not in st.session_state:
            # key: id_menu (int), value: qty (int)
            st.session_state.order_items = {}

        # =========================
        # STEP 1 — PILIH MENU
        # =========================
        if st.session_state.redeem_step == 1:

            if "redeem_error" in st.session_state:
                del st.session_state["redeem_error"]

            selected_branch = st.session_state.cabang
            st.session_state.selected_branch = selected_branch
            st.info(f"🏪 Cabang aktif: {selected_branch}")

            # Ambil menu dari DB (WAJIB ada id_menu)
            menu_items = get_menu_from_db(selected_branch)

            if not menu_items:
                st.info("Tidak ada menu tersedia untuk cabang ini.")
                st.stop()

            # Normalisasi + validasi
            normalized = []
            for it in menu_items:
                try:
                    id_menu = it.get("id_menu")
                    nama = it.get("nama")
                    kategori = it.get("kategori")
                    harga = it.get("harga")
                    keterangan = it.get("keterangan", "")

                    if id_menu is None or pd.isna(id_menu):
                        continue
                    if harga is None or pd.isna(harga):
                        continue
                    if not nama or pd.isna(nama):
                        continue
                    if not kategori or pd.isna(kategori):
                        kategori = "Lainnya"

                    normalized.append({
                        "id_menu": int(id_menu),
                        "nama": str(nama),
                        "kategori": str(kategori),
                        "keterangan": "" if keterangan is None else str(keterangan),
                        "harga": int(harga),
                    })
                except Exception:
                    continue

            if not normalized:
                st.info("Tidak ada menu tersedia untuk cabang ini.")
                st.stop()

            menu_items = normalized

            categories = sorted({item["kategori"] for item in menu_items})
            search_query = st.text_input("🔍 Cari menu").strip().lower()
            st.write("*Pilih menu:*")

            def render_item_number_input(item: dict):
                id_menu = item["id_menu"]
                key = f"qty_{selected_branch}_{id_menu}"  # key unik

                old_qty = st.session_state.order_items.get(id_menu, 0)
                qty = st.number_input(
                    f"{item['nama']} (Rp {item['harga']:,})",
                    min_value=0,
                    value=int(old_qty),
                    step=1,
                    key=key
                )
                st.session_state.order_items[id_menu] = int(qty)

            # =========================
            # RENDER MENU (SEARCH / TABS)
            # =========================
            if search_query:
                filtered = [item for item in menu_items if search_query in item["nama"].lower()]
                if not filtered:
                    st.info("Menu tidak ditemukan")
                else:
                    for item in filtered:
                        render_item_number_input(item)
            else:
                cat_tabs = st.tabs(categories)
                for i, cat in enumerate(categories):
                    with cat_tabs[i]:
                        cat_items = [item for item in menu_items if item["kategori"] == cat]
                        for item in cat_items:
                            render_item_number_input(item)

            # =========================
            # HITUNG TOTAL
            # =========================
            price_by_id_menu = {item["id_menu"]: item["harga"] for item in menu_items}

            checkout_total = sum(
                price_by_id_menu.get(id_menu, 0) * qty
                for id_menu, qty in st.session_state.order_items.items()
                if qty > 0
            )

            st.session_state.checkout_total = int(checkout_total)
            st.write(f"**Total sementara: Rp {checkout_total:,}**")

            # =========================
            # LANJUT
            # =========================
            if st.button("Cek dan Lanjut"):
                if checkout_total == 0:
                    st.warning("Pilih minimal 1 menu!")
                else:
                    st.session_state.redeem_step = 2
                    st.rerun()

    
        # ---------------------------
        # STEP 2 — KONFIRMASI PEMBAYARAN
        # ---------------------------
        if st.session_state.redeem_step == 2:
            st.header("Konfirmasi Pembayaran")
        
            if "isvoucher" not in st.session_state:
                st.session_state.isvoucher = "no"
        
            menu_items = get_menu_from_db(st.session_state.selected_branch)
        
            # MAP BERDASARKAN id_menu (bukan nama)
            price_by_id_menu = {item["id_menu"]: int(item["harga"]) for item in menu_items}
            name_by_id_menu  = {item["id_menu"]: item["nama"] for item in menu_items}
        
            ordered_items = {id_menu: qty for id_menu, qty in st.session_state.order_items.items() if qty > 0}
        
            if not ordered_items:
                st.warning("Tidak ada menu yang dipilih.")
                st.stop()
        
            st.write("## Detail Pesanan")
            st.write(f"- Cabang: {st.session_state.selected_branch}")
        
            total = int(st.session_state.checkout_total)
        
            for id_menu, qty in ordered_items.items():
                nama = name_by_id_menu.get(id_menu, f"Menu ID {id_menu}")
                harga = price_by_id_menu.get(id_menu, 0)
                st.write(f"- {nama} x{qty} — Rp {harga * qty:,}")
        
            st.write(f"### Total: Rp {total:,}")
            entered_code = st.text_input(
                "Masukkan kode kupon (opsional)",
                value=st.session_state.entered_code
            ).strip().upper()
    
            st.session_state.entered_code = entered_code
            shortage = 0
    
            if st.button("Cek Kupon"):
                code = st.session_state.entered_code
                st.session_state.pop("redeem_error", None)
                st.session_state.isvoucher = "no"
    
                if not code:
                    st.session_state["redeem_error"] = "⚠️ Kode tidak boleh kosong"
                else:
                    row = find_voucher(code)
                    if not row:
                        st.session_state["redeem_error"] = "❌ Kupon tidak ditemukan."
                    else:
                        validate_voucher_and_show_info(row, total)
    
            if "redeem_error" in st.session_state:
                st.error(st.session_state["redeem_error"])
    
            st.write(f"### Total Sementara: Rp {total:,}")
            # ========================
            # INPUT DISKON
            # ========================

            if "diskon_persen" not in st.session_state:
                st.session_state.diskon_persen = 0

            # Input diskon, tapi hanya jika total >= 25000
            if total >= 1000:
                diskon = st.number_input(
                    "Masukkan diskon (nominal)",
                    min_value=0,
                    max_value=total,
                    value=0,
                    step=1000,
                    key="diskon_input"
                )
            else:
                diskon = 0

            # Hitung total setelah diskon
            total_setelah_diskon = total - diskon
            if total_setelah_diskon < 0:
                total_setelah_diskon = 0

            st.write(f"### Total Akhir: Rp {total_setelah_diskon:,}")

            # Simpan ke session_state
            st.session_state.total_setelah_diskon = total_setelah_diskon
            st.session_state.diskon = diskon
    
            cA, cB = st.columns(2)
            with cA:
                if st.button("Ya, Bayar"):
                    items_str = ", ".join([f"{k} x{v}" for k, v in ordered_items.items()])
                    branch = st.session_state.selected_branch
                    final_total = total_setelah_diskon  # total akhir

                    diskon_persen = st.session_state.diskon_persen

                    if st.session_state.isvoucher == "yes" and "voucher_row" in st.session_state:
                        code = st.session_state.voucher_row[0]

                        ok, msg, newbal = atomic_redeem(code, final_total, branch, items_str, diskon)
                        st.session_state.newbal = newbal

                        # =============== UPDATE DISKON KE TABLE VOUCHERS ==================
                        try:
                            with engine.begin() as conn:
                                conn.execute(
                                    text("""
                                        UPDATE vouchers
                                        SET diskon = :disc
                                        WHERE kode = :kode
                                    """),
                                    {"disc": diskon_persen, "kode": code}
                                )
                        except Exception as e:
                            st.error(f"Gagal menyimpan diskon: {e}")
                        # ==================================================================

                    else:
                        ok, msg, _ = atomic_redeem(None, final_total, branch, items_str, diskon)

                    transaksi_notification(date.today(), branch, final_total)

                    if ok:
                        st.session_state.show_success = True
                        st.session_state.redeem_step = 3
                        st.session_state.entered_code = ""
                        st.session_state.pop("redeem_error", None)
                        st.rerun()
                    else:
                        st.error(msg)
                        st.session_state.redeem_step = 1
                        st.rerun()
    
            with cB:
                if st.button("Tidak, Kembali"):
                    st.session_state.redeem_step = 1
                    st.session_state.entered_code = ""
                    st.rerun()
    
        # ---------------------------
        # STEP 3 — TRANSAKSI BERHASIL
        # ---------------------------
        if st.session_state.redeem_step == 3:
            if st.session_state.show_success:
                if st.session_state.isvoucher == "yes":
                    st.success(
                        f"🎉 TRANSAKSI BERHASIL 🎉\nSisa saldo sekarang: Rp {int(st.session_state.newbal):,}"
                    )
                else:
                    st.success("🎉 TRANSAKSI BERHASIL 🎉")
    
                if st.button("Tutup"):
                    reset_redeem_state()
                    st.session_state.show_success = False
                    st.rerun()
                    
    with riwayat_pesan:
        st.subheader("Riwayat Transaksi")
        df_tx = list_transactions(limit=5000)  # pastikan JOIN ke vouchers untuk ambil initial_value
        df_tx = df_tx.sort_values(by="id", ascending=False).reset_index(drop=True)

        if df_tx.empty:
            st.info("Belum ada transaksi")
        else:
            # Normalisasi kolom
            df_tx["tanggal_transaksi"] = pd.to_datetime(df_tx["tanggal_transaksi"]).dt.date
            df_tx["code"] = df_tx["code"].fillna("")
            df_tx["isvoucher"] = df_tx["isvoucher"].fillna("no")
            min_date = df_tx["tanggal_transaksi"].min()
            max_date = df_tx["tanggal_transaksi"].max()

            # Filter input
            col1, col2, col3, col4 = st.columns([2, 1.3, 1.3, 1.3])
            with col1:
                search_code = st.text_input("Cari kode kupon untuk detail histori", "").strip()
            with col2:
                start_date = st.date_input("Tanggal Mulai", value=min_date, min_value=min_date, max_value=max_date)
            with col3:
                end_date = st.date_input("Tanggal Akhir", value=max_date, min_value=min_date, max_value=max_date)
            # with col4:
            #     filter_cabang = st.selectbox("Filter Cabang", ["semua", "Sedati", "Tawangsari", "Kesambi", "Tulangan"])
            with col4:
                filter_kupon = st.selectbox("Filter Kupon", ["semua", "Kupon", "Non Kupon"])

            # Filter tanggal
            if start_date > end_date:
                st.error("❌ Tanggal Mulai tidak boleh setelah Tanggal Akhir")
                st.stop()
            df_tx = df_tx[(df_tx["tanggal_transaksi"] >= start_date) & (df_tx["tanggal_transaksi"] <= end_date)]

            # Filter cabang
            filter_cabang = st.session_state.cabang
            # if filter_cabang != "semua":
            df_tx = df_tx[df_tx["branch"] == filter_cabang]

            if filter_kupon != "semua":
                if filter_kupon == "Kupon":
                    filter_kupon = "yes"
                else:
                    filter_kupon = "no"
                df_tx = df_tx[df_tx["isvoucher"] == filter_kupon]

            if df_tx.empty:
                st.warning("Tidak ada transaksi dengan filter tersebut.")
                st.stop()

            # 🔥 Hitung total uang (hanya dari used_amount)
            total_uang_filtered = df_tx["used_amount"].fillna(0).sum()
            total_cash_filtered = df_tx["tunai"].fillna(0).sum()
            total_kupon_filtered = total_uang_filtered - total_cash_filtered

            cola, colb, colc = st.columns(3)
            with cola:
                st.metric("Total Pendapatan", f"Rp {total_uang_filtered:,}")
            with colb:
                st.metric("Total Pendapatan Cash", f"Rp {total_cash_filtered:,}")
            with colc:
                st.metric("Total Pendapatan Dari Kupon", f"Rp {total_kupon_filtered:,}")
            

            # =============================
            # 🔍 PENCARIAN DETAIL KUPON (DITARUH DI PALING ATAS)
            # =============================
            if search_code:
                # Filter hanya transaksi yang menggunakan voucher
                df_voucher_top = df_tx[df_tx["isvoucher"] == "yes"]
                df_filtered_top = df_voucher_top[df_voucher_top["code"].str.contains(search_code.upper(), case=False, na=False)]

                st.subheader(f"Detail Kupon: {search_code.upper()}")

                if df_filtered_top.empty:
                    st.warning(f"Tidak ada transaksi voucher untuk kupon {search_code}")
                else:
                    with st.expander("ℹ️ Informasi Lengkap Kupon", expanded=True):
                        total_transaksi = len(df_filtered_top)

                        total_nominal = df_filtered_top["used_amount"].sum()
                        initial_val = df_filtered_top["initial_value"].iloc[0]

                        st.write(f"- Initial Value: Rp {initial_val:,}")
                        st.write(f"- Jumlah transaksi: {total_transaksi}")
                        st.write(f"- Total nominal terpakai: Rp {total_nominal:,}")

                        df_tmp_display = df_filtered_top.rename(columns={
                            "tanggal_transaksi": "Tanggal_transaksi",
                            "initial_value": "Initial_value",
                            "used_amount": "Total",
                            "tunai": "Tunai",
                            "branch": "Cabang",
                            "items": "Menu",
                            "code": "Kode"
                        })

                        st.dataframe(
                            df_tmp_display[["Tanggal_transaksi", "Kode", "Initial_value",
                                            "Total", "Tunai", "Cabang", "Menu"]],
                            use_container_width=True
                        )

                        st.download_button(
                            f"Download CSV {search_code.upper()}",
                            data=df_to_csv_bytes(df_tmp_display),
                            file_name=f"transactions_{search_code.upper()}.csv",
                            mime="text/csv"
                        )

                st.markdown("---")
            df_display = df_tx.rename(columns={
                        "code": "Kode",
                        "used_amount": "Total",
                        "tanggal_transaksi": "Tanggal_transaksi",
                        "branch": "Cabang",
                        "items": "Menu",
                        "tunai": "Tunai",
                        "isvoucher": "kupon digunakan",
                        "initial_value": "Initial_value"
                    })
                    # df_display["Tunai"] = df_display["Tunai"].apply(lambda x: "tidak ada" if x == 0 else f"Rp {int(x):,}")
                    # df_display["Total"] = df_display["Total"].apply(lambda x: "tidak ada" if x == 0 else f"Rp {int(x):,}")
                    # df_display["Tunai"] = df_display["Initi"].apply(lambda x: "tidak ada" if x == 0 else f"Rp {int(x):,}")
            df_display["kupon digunakan"] = df_display["kupon digunakan"].apply(lambda x: "1" if x == "yes" else "0")
            df_display.loc[df_display["kupon digunakan"] == "0", "Total"] = df_display["Tunai"]

            # Tampilkan tabel histori
            st.dataframe(
                df_display[["id", "Tanggal_transaksi", "kupon digunakan", "Kode", "Initial_value",
                            "Total", "Tunai", "Cabang", "Menu"]],
                use_container_width=True
            )

            st.download_button(
                "Download CSV Transaksi",
                data=df_to_csv_bytes(df_display),
                file_name="transactions.csv",
                mime="text/csv"
            )

        menu_list = []

        for idx, row in df_tx.iterrows():
            menu_items = str(row["items"]).split(",")  # Pecah per menu
            for item in menu_items:
                item = item.strip()
        
                # Contoh item: "NASI PUTIH x6"
                match = re.match(r"(.+?) x(\d+)", item)
                if match:
                    nama_menu = match.group(1).strip()
                    jumlah = int(match.group(2))
                    menu_list.append({
                        "Tanggal": row["tanggal_transaksi"],
                        "Menu": nama_menu,
                        "Jumlah": jumlah
                    })
        
        # Buat dataframe menu
        df_menu = pd.DataFrame(menu_list)
        
        if df_menu.empty:
            st.info("Tidak ada menu terjual pada tanggal tersebut.")
        else:
            df_pivot = df_menu.groupby("Menu")["Jumlah"].sum().reset_index()
        
            st.subheader("📊 Penjualan Per Menu")
            st.dataframe(df_pivot, use_container_width=True)


# Jika admin login → langsung ke halaman admin
if st.session_state.admin_logged_in and not st.session_state.seller_logged_in:
    page_admin()
    st.stop()

# Jika seller login → langsung ke halaman seller
if st.session_state.seller_logged_in and not st.session_state.admin_logged_in:
    page_seller_activation()
    st.stop()

if st.session_state.kasir_logged_in and not st.session_state.admin_logged_in:
    page_kasir()
    st.stop()


