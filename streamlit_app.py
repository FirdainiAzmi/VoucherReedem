# app.py
import streamlit as st
import pandas as pd
import time
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
import unicodedata
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
                CREATE TABLE IF NOT EXISTS public.vouchers (
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
                    satuan TEXT,
                    FOREIGN KEY(jenis_kupon) REFERENCES jenis_db(jenis_kupon)
                )
            """))
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS public.transactions (
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
                CREATE TABLE IF NOT EXISTS public.menu_items (
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
                CREATE TABLE IF NOT EXISTS public.jenis_db (
                    jenis_kupon TEXT PRIMARY KEY,
                    awal_berlaku DATE NOT NULL,
                    akhir_berlaku DATE NOT NULL
                )
            """))
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS public.kategori_menu (
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
            text("SELECT 1 FROM public.jenis_db WHERE jenis_kupon = :j"),
            {"j": jenis}
        ).fetchone()

        if not exists:
            conn.execute(
                text("""
                    INSERT INTO public.jenis_db (jenis_kupon, awal_berlaku, akhir_berlaku)
                    VALUES (:j, :a, :b)
                """),
                {"j": jenis, "a": awal, "b": akhir}
            )


def insert_voucher(code, initial_value, jenis, awal, akhir):
    """Insert satu voucher baru."""
    with engine.begin() as conn:
        conn.execute(
            text("""
                INSERT INTO public.vouchers
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
                FROM public.vouchers v
                JOIN public.jenis_db j 
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
                UPDATE public.vouchers
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
                    INSERT INTO public.transactions 
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
                        UPDATE public.menu_items
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
                        UPDATE public.vouchers 
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
                    INSERT INTO public.transactions 
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
                    qty = float(qty)
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
                        UPDATE public.menu_items
                        SET {col} = COALESCE({col}, 0) + :qty
                        WHERE nama_item = :item
                    """), {"qty": qty, "item": nama_item})
    
                return True, "Redeem berhasil ✅", new_balance

    except Exception as e:
        traceback.print_exc()
        return False, f"DB error saat redeem: {e}", None

def list_vouchers(filter_status=None, search=None, limit=5000, offset=0):
    q = "SELECT v.code, v.initial_value, v.balance, v.nama, v.no_hp, v.status, v.seller, v.tanggal_aktivasi, j.awal_berlaku, j.akhir_berlaku FROM public.vouchers v JOIN public.jenis_db j ON v.jenis_kupon = j.jenis_kupon"
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
        SELECT * FROM public.menu_items
        ORDER BY kategori, nama_item
    """
    with engine.begin() as conn:
        res = conn.execute(text(query)).mappings().all()
    return res

def list_all_kategori():
    query = """
        SELECT * FROM public.kategori_menu
        ORDER BY id_kategori
    """
    with engine.begin() as conn:
        res = conn.execute(text(query)).mappings().all()
    return res

def add_menu_item(kategori, nama_item, keterangan,
                  harga_sedati, harga_twsari, harga_kesambi, harga_tulangan, satuan):

    query = """
        INSERT INTO public.menu_items (
            kategori, nama_item, keterangan,
            harga_sedati, harga_twsari, harga_kesambi, harga_tulangan, satuan
        ) VALUES (
            :kategori, :nama_item, :keterangan,
            :harga_sedati, :harga_twsari, :harga_kesambi, :harga_tulangan, :satuan
        )
    """

    params = {
        "kategori": to_upper_or_none(kategori),
        "nama_item": to_upper_or_none(nama_item),
        "keterangan": to_none_if_empty(keterangan),
        "harga_sedati": to_int_or_none(harga_sedati),
        "harga_twsari": to_int_or_none(harga_twsari),
        "harga_kesambi": to_int_or_none(harga_kesambi),
        "harga_tulangan": to_int_or_none(harga_tulangan),
        "satuan" : to_upper_or_none(satuan)
    }

    with engine.begin() as conn:
        conn.execute(text(query), params)


def update_menu_item(id_menu, kategori, nama_item, keterangan,
                     harga_sedati, harga_twsari, harga_kesambi, harga_tulangan, status, satuan):

    query = """
        UPDATE public.menu_items SET
            kategori = :kategori,
            nama_item = :nama_item,
            keterangan = :keterangan,
            harga_sedati = :harga_sedati,
            harga_twsari = :harga_twsari,
            harga_kesambi = :harga_kesambi,
            harga_tulangan = :harga_tulangan,
            status = :status,
            satuan = :satuan
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
        "status": status,
        "satuan" : to_upper_or_none(satuan)
    }

    with engine.begin() as conn:
        conn.execute(text(query), params)

def update_kategori_menu(id_kategori, status_kategori):
    query = """
        UPDATE public.kategori_menu 
        SET status_kategori = :status_kategori
        WHERE id_kategori = :id_kategori
    """

    params = {
        "id_kategori": id_kategori,
        "status_kategori": status_kategori
    }

    with engine.begin() as conn:
        conn.execute(text(query), params)

def delete_menu_item(id_menu):
    try:
        with engine.begin() as conn:
            conn.execute(text("""
                DELETE FROM public.menu_items WHERE id_menu = :id_menu
            """), {"id_menu": id_menu})
        return True
    except Exception as e:
        st.error(f"Error saat menghapus menu: {e}")
        return False

def get_kategori_list():
    query = text("""
        SELECT DISTINCT kategori
        FROM public.menu_items
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
                FROM public.menu_items
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
                    "status" : r[12],
                    "satuan" : r[13]
                })

            return menu_list

    except Exception as e:
        st.error(f"Error saat mengambil menu: {e}")
        return []

def get_menu_from_db(branch):
    try:
        with engine.connect() as conn:
            df = pd.read_sql(text("""
                SELECT 
                    m.id_menu,
                    m.kategori,
                    m.nama_item,
                    m.keterangan,
                    m.harga_sedati,
                    m.harga_twsari,
                    m.harga_kesambi,
                    m.harga_tulangan,
                    m.status,
                    m.satuan,
                    k.status_kategori
                FROM public.menu_items AS m
                LEFT JOIN public.kategori_menu AS k 
                    ON m.kategori = k.nama_kategori
            """), conn)

        df = df[
            (df["status_kategori"].isna()) |
            (df["status_kategori"].str.lower() == "aktif")
        ]

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

            # Hindari error cannot convert nan to int
            if pd.isna(id_menu) or pd.isna(harga):
                continue

            menu_list.append({
                "id_menu": int(id_menu),
                "nama": str(row["nama_item"]),
                "harga": int(harga),
                "kategori": str(row["kategori"]) if row["kategori"] is not None else "Lainnya",
                "keterangan": "" if row["keterangan"] is None else str(row["keterangan"]),
                "status": str(row["status"]),
                "status_kategori": row["status_kategori"],
                "satuan": row["satuan"]
            })

        return menu_list

    except Exception as e:
        print("DB error:", e)
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
            satuan,
            COALESCE(terjual_twsari, 0) AS terjual_twsari,
            COALESCE(terjual_sedati, 0) AS terjual_sedati,
            COALESCE(terjual_kesambi, 0) AS terjual_kesambi,
            COALESCE(terjual_tulangan, 0) AS terjual_tulangan
        FROM public.menu_items
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
            v.initial_value,
            v.balance
        FROM public.transactions t
        LEFT JOIN public.vouchers v ON t.code = v.code
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
                FROM public.vouchers
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
                UPDATE public.vouchers
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

st.set_page_config(page_title="Pawon Sappitoe", layout="wide", page_icon="❄️") 

def inject_blue_theme():
    st.markdown("""
    <style>
        /* IMPORT FONT FUTURISTIK (TETAP SAMA) */
        @import url('https://fonts.googleapis.com/css2?family=Outfit:wght@300;500;800&display=swap');
        
        html, body, [class*="css"] {
            font-family: 'Outfit', sans-serif;
        }

        /* ============================================================
           FIX PENTING UNTUK HP (SUPAYA INPUT TIDAK JADI PUTIH/SILAU)
           ============================================================ */
        :root {
            color-scheme: dark; /* Memaksa mode gelap untuk elemen browser */
        }

        /* Input Field: Biru Gelap Transparan (Bukan Putih) */
        div[data-baseweb="input"], div[data-baseweb="base-input"] {
            background-color: rgba(10, 25, 47, 0.6) !important; 
            border: 1px solid rgba(136, 146, 176, 0.3) !important;
            border-radius: 10px !important;
            color: white !important;
        }

        /* Teks di dalam input: Putih Kebiruan (Jelas terbaca) */
        input[type="text"], input[type="password"], input[type="number"] {
            color: #e6f1ff !important; 
            -webkit-text-fill-color: #e6f1ff !important;
            caret-color: #00f2ff !important; 
        }

        /* ============================================================
           DESAIN UTAMA (BACKGROUND & CONTAINER)
           ============================================================ */

        /* Background Biru Laut (Tetap) */
        .stApp {
            background-color: #0D5EA6;
            background-image: 
                radial-gradient(at 0% 0%, rgba(0, 242, 255, 0.15) 0px, transparent 50%),
                radial-gradient(at 100% 100%, rgba(0, 100, 255, 0.15) 0px, transparent 50%);
            background-attachment: fixed;
        }

        /* Hilangkan Header/Footer bawaan */
        header, footer {visibility: hidden;}
        
        /* Container Tengah (Kaca Biru) */
        div[data-testid="column"]:nth-of-type(2) {
            background: rgba(10, 25, 47, 0.7);
            border: 1px solid rgba(100, 255, 218, 0.1);
            border-top: 1px solid rgba(100, 255, 218, 0.3);
            border-radius: 20px;
            padding: 40px;
            backdrop-filter: blur(15px);
            box-shadow: 0 0 40px rgba(0, 0, 0, 0.6);
        }

        /* ============================================================
           JUDUL: PAWON SAPPITOE (UBAH JADI PUTIH SOLID)
           ============================================================ */
        .cyber-title {
            font-size: 3rem;        /* Ukuran Tetap */
            font-weight: 800;       /* Tebal Tetap */
            text-align: center;
            
            /* GANTI DI SINI: Jadi Putih Solid */
            color: #FFFFFF !important; 
            
            /* Hapus efek gradient text yg bikin masalah, sisakan glow saja */
            text-shadow: 0 0 20px rgba(0, 242, 255, 0.5); /* Efek bersinar biru muda */
            margin-bottom: 5px;
        }
        
        .cyber-subtitle {
            text-align: center;
            color: #FFFFFF !important; /* Putih Solid */
            font-size: 1rem;
            letter-spacing: 1px;
            margin-bottom: 30px;
        }

        /* ============================================================
           ELEMENT LAIN (TABS, TOMBOL, ALERT) - TETAP SAMA
           ============================================================ */

        /* Tabs */
        .stTabs [data-baseweb="tab-list"] {
            gap: 10px;
            background-color: rgba(2, 12, 27, 0.5);
            padding: 8px;
            border-radius: 12px;
            border: 1px solid rgba(255,255,255,0.05);
        }
        .stTabs [data-baseweb="tab"] {
            height: 45px;
            border-radius: 8px;
            color: #8892b0;
            font-weight: 600;
            border: none;
            background-color: transparent;
        }
        .stTabs [aria-selected="true"] {
            background-color: rgba(0, 242, 255, 0.1) !important;
            color: #00f2ff !important;
            border: 1px solid rgba(0, 242, 255, 0.2) !important;
            box-shadow: 0 0 15px rgba(0, 242, 255, 0.1);
        }

        /* Tombol Login */
        .stButton > button {
            background: linear-gradient(90deg, #0072ff 0%, #00c6ff 100%) !important;
            color: white !important;
            border: none !important;
            border-radius: 6px !important;
            font-weight: bold !important;
            letter-spacing: 1px;
            padding: 12px 0 !important;
            transition: all 0.3s ease;
            box-shadow: 0 4px 15px rgba(0, 114, 255, 0.3);
        }
        .stButton > button:hover {
            transform: translateY(-2px);
            box-shadow: 0 0 25px rgba(0, 198, 255, 0.6);
        }
        
        /* Alert Box */
        .stAlert {
            background-color: rgba(0, 242, 255, 0.05);
            border: 1px solid rgba(0, 242, 255, 0.2);
            color: #e6f1ff;
        }

        /* Label Input (Password Outlet dll) */
        label p {
            color: #e6f1ff !important;
        }

    </style>
    """, unsafe_allow_html=True)

# ============================================================
# LOGIN PAGE FUNCTION
# ============================================================
def show_login_page():
    inject_blue_theme()
    
    # Layout 3 Kolom (Tengah Lebar)
    col1, col2, col3 = st.columns([1, 1.8, 1])
    
    with col2:
        # Header Visual
        st.markdown('<div class="cyber-title">PAWON SAPPITOE</div>', unsafe_allow_html=True)
        st.markdown('<div class="cyber-subtitle">WEB APP SISTEM KASIR DAN ADMIN</div>', unsafe_allow_html=True)

        # Tab Navigation
        tab_kasir, tab_daftar, tab_seller, tab_admin = st.tabs(
            ["💳 Kasir", "📝 Register", "👤 Seller", "🔐 Admin"]
        )

        # ================= KASIR LOGIN =================
        with tab_kasir:
            st.write("")
            st.markdown("<h5 style='color: #FFFFFF; margin-bottom: 10px;'>🏪 Akses Outlet</h5>", unsafe_allow_html=True)
            st.markdown("<h5 style='color: #FFFFFF; margin-bottom: 0px;'>Password Outlet</h5>", unsafe_allow_html=True)

            pwd = st.text_input("Password Outlet", type="password", key="kasir_pass", label_visibility="collapsed")

            if st.button("LOGIN KASIR", use_container_width=True):
                if pwd in KASIR_PASSWORDS:
                    # Animasi Loading
                    with st.spinner('Authenticating...'):
                        time.sleep(0.8) 
                    
                    st.session_state.kasir_logged_in = True
                    st.session_state.page = "kasir"
                    st.session_state.cabang = KASIR_PASSWORDS[pwd]
                    
                    st.success(f"ACCESS GRANTED: Cabang {st.session_state.cabang.upper()}")
                    st.balloons()
                    time.sleep(1)
                    st.rerun()
                else:
                    st.error("❌ Access Denied: Password Salah")

        # ================= DAFTAR SELLER =================
        with tab_daftar:
            st.write("")
            st.markdown("<h5 style='color: #FFFFFF; margin-bottom: 10px;'>✨ Join Mitra Baru</h5>", unsafe_allow_html=True)
            with st.form("form_daftar_seller"):
                col_a, col_b = st.columns(2)
                with col_a:
                    st.markdown("<h5 style='color: #FFFFFF; font-size: 15px; margin-bottom: 0px;'>Nama Lengkap</h5>", unsafe_allow_html=True)
                    nama = st.text_input("Nama Lengkap", label_visibility="collapsed")
                with col_b:
                    st.markdown("<h5 style='color: #FFFFFF; font-size: 15px; margin-bottom: 0px;'>No. Handphone</h5>", unsafe_allow_html=True)
                    nohp = st.text_input("No. Handphone", label_visibility="collapsed")
                st.markdown("<h5 style='color: #FFFFFF; font-size: 15px; margin-bottom: 0px;'>Buat ID Unik (3 Digit - Contoh: A01)</h5>", unsafe_allow_html=True)
                id_seller = st.text_input("Buat ID Unik (3 Digit - Contoh: A01)", max_chars=3, label_visibility="collapsed").upper().strip()
                
                st.write("")
                submit = st.form_submit_button("DAFTAR SEKARANG", use_container_width=True)
            
            if submit:
                # Validasi
                if not id_seller or len(id_seller) != 3:
                    st.error("ID harus tepat 3 karakter.")
                    st.stop()
                if not nama or not nohp:
                    st.error("Data harus lengkap.")
                    st.stop()

                try:
                    with engine.connect() as conn:
                        exists = conn.execute(
                            text("SELECT 1 FROM seller WHERE id_seller = :id"),
                            {"id": id_seller}
                        ).fetchone()
                    
                    if exists:
                        st.warning("⚠️ ID sudah terpakai. Gunakan ID lain.")
                    else:
                        with engine.begin() as conn:
                            conn.execute(
                                text("INSERT INTO seller (nama_seller, no_hp, status, id_seller) VALUES (:nama, :no_hp, :status, :id_seller)"),
                                {
                                    "nama": nama.strip(),
                                    "no_hp": nohp.strip(),
                                    "status": "belum diterima",
                                    "id_seller": id_seller,
                                }
                            )
                        st.success("✅ Registrasi Terkirim!")
                        st.info(f"ID Login Anda: **{id_seller}** (Simpan ID ini!)")
                        
                except Exception as e:
                    st.error(f"System Error: {e}")

        # ================= SELLER LOGIN =================
        with tab_seller:
            st.write("")
            st.markdown("<h5 style='color: #FFFFFF; margin-bottom: 10px;'>🚀 Login Mitra</h5>", unsafe_allow_html=True)
            st.markdown("<h5 style='color: #FFFFFF; font-size: 15px; margin-bottom: 0px;'>ID Seller (3 Digit)</h5>", unsafe_allow_html=True)
            seller_id = st.text_input("ID Seller (3 Digit)",  label_visibility="collapsed", key="seller_login_id")
            
            if st.button("LOGIN SELLER", use_container_width=True):
                if not seller_id.strip():
                    st.warning("Masukkan ID.")
                else:
                    try:
                        with engine.connect() as conn:
                            row = conn.execute(
                                text("SELECT id_seller, nama_seller, status FROM seller WHERE id_seller = :id"),
                                {"id": seller_id.upper()}
                            ).fetchone()

                        if not row:
                            st.error("❌ ID Tidak Ditemukan.")
                        else:
                            sid, sname, sstatus = row
                            if sstatus != "diterima":
                                st.warning("⏳ Akun dalam peninjauan admin.")
                            else:
                                st.session_state.seller_logged_in = True
                                st.session_state.id_seller = sid
                                st.session_state.nama_seller = sname
                                st.success(f"Welcome back, {sname}!")
                                st.rerun()
                    except Exception as e:
                        st.error(f"Connection Error: {e}")

        # ================= ADMIN LOGIN =================
        with tab_admin:
            st.write("")
            st.markdown("<h5 style='color: #FFFFFF; margin-bottom: 10px;'>🛡️ Admin</h5>", unsafe_allow_html=True)
            st.markdown("<h5 style='color: #FFFFFF; font-size: 15px; margin-bottom: 0px;'>Password Admin</h5>", unsafe_allow_html=True)
            pwd = st.text_input("Password Admin", label_visibility="collapsed", type="password", key="admin_pass")
            
            if st.button("LOGIN", use_container_width=True):
                if pwd == ADMIN_PASSWORD:
                    st.session_state.admin_logged_in = True
                    st.success("System Unlocked.")
                    st.rerun()
                else:
                    st.error("⛔ Unauthorized Access.")
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
                                        UPDATE public.vouchers
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

        df_tx = list_transactions(limit=5000)
        df_tx = df_tx.sort_values(by="id", ascending=False).reset_index(drop=True)

        if df_tx.empty:
            st.info("Belum ada transaksi")
            st.stop()

        # =============================
        # NORMALISASI DASAR
        # =============================
        df_tx["tanggal_transaksi"] = pd.to_datetime(df_tx["tanggal_transaksi"]).dt.date
        df_tx["code"] = df_tx["code"].fillna("")
        df_tx["isvoucher"] = df_tx["isvoucher"].fillna("no")
        df_tx["diskon"] = pd.to_numeric(df_tx["diskon"], errors="coerce").fillna(0)

        min_date = df_tx["tanggal_transaksi"].min()
        max_date = df_tx["tanggal_transaksi"].max()

        # =============================
        # FILTER INPUT
        # =============================
        today = date.today()

        default_start_date = today.replace(day=1)
        min_date = date(2020, 1, 1) 
        max_date = today
        col1, col2, col3, col4, col5 = st.columns([2, 1.3, 1.3, 1.3, 1.3])
        with col1:
            search_code = st.text_input("Cari kode kupon", "").strip()
        with col2:
            start_date = st.date_input(
                "Tanggal Mulai",
                value=default_start_date,
                min_value=min_date,
                max_value=max_date
            )
        with col3:
            end_date = st.date_input("Tanggal Akhir", max_date)
        with col4:
            filter_cabang = st.selectbox("Filter Cabang", ["semua", "Sedati", "Tawangsari", "Kesambi", "Tulangan"])
        with col5:
            filter_kupon = st.selectbox("Filter Kupon", ["semua", "Kupon", "Non Kupon"])

        # =============================
        # FILTER DATA
        # =============================
        df_filt = df_tx[
            (df_tx["tanggal_transaksi"] >= start_date) &
            (df_tx["tanggal_transaksi"] <= end_date)
        ]

        if filter_cabang != "semua":
            df_filt = df_filt[df_filt["branch"] == filter_cabang]

        if filter_kupon != "semua":
            df_filt = df_filt[df_filt["isvoucher"] == ("yes" if filter_kupon == "Kupon" else "no")]

        if search_code:
            df_filt = df_filt[
                (df_filt["isvoucher"] == "yes") &
                (df_filt["code"].str.contains(search_code.upper(), case=False, na=False))
            ]

        if df_filt.empty:
            st.warning("Tidak ada data sesuai filter.")
            st.stop()

        # =============================
        # METRIK
        # =============================
        total_uang = df_filt["used_amount"].sum()
        total_cash = df_filt["tunai"].sum()
        total_kupon = total_uang - total_cash
        total_diskon = df_filt["diskon"].sum()

        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Total Pendapatan", f"Rp {total_uang:,}")
        c2.metric("Cash", f"Rp {total_cash:,}")
        c3.metric("Kupon", f"Rp {total_kupon:,}")
        c4.metric("Diskon", f"Rp {int(total_diskon):,}")

        # =============================
        # DETAIL KUPON (JIKA SEARCH)
        # =============================
        if search_code:
            st.subheader(f"Detail Kupon: {search_code.upper()}")

            initial_val = df_filt["initial_value"].iloc[0]
            st.write(f"- Saldo awal: Rp {initial_val:,}")
            st.write(f"- Jumlah transaksi: {len(df_filt)}")
            st.write(f"- Total terpakai: Rp {df_filt['used_amount'].sum():,}")

            st.markdown("---")

        # =============================
        # TABEL HISTORI (WAJIB MENU)
        # =============================
        df_hist = df_filt.rename(columns={
            "tanggal_transaksi": "Tanggal transaksi",
            "code": "Kode",
            "used_amount": "Total",
            "initial_value": "Saldo awal",
            "branch": "Cabang",
            "items": "Menu",
            "tunai": "Tunai",
            "isvoucher": "kupon digunakan",
            "diskon": "Diskon"
        })

        df_hist["kupon digunakan"] = df_hist["kupon digunakan"].apply(lambda x: "1" if x == "yes" else "0")
        df_hist.loc[df_hist["kupon digunakan"] == "0", "Total"] = df_hist["Tunai"]
        df_hist.loc[df_hist["Diskon"] > 0, "Total"] += df_hist["Diskon"]

        # Hitung sisa saldo
        df_calc = df_hist.sort_values("id")
        saldo_map = {}
        df_calc["Sisa saldo"] = None

        for i, r in df_calc.iterrows():
            if r["kupon digunakan"] != "1":
                continue
            kode = r["Kode"]
            saldo = r["Saldo awal"] if kode not in saldo_map else saldo_map[kode]
            saldo = max(saldo - r["Total"], 0)
            saldo_map[kode] = saldo
            df_calc.at[i, "Sisa saldo"] = saldo

        df_hist["Sisa saldo"] = df_calc.sort_values("id", ascending=False)["Sisa saldo"].values

        st.dataframe(
            df_hist[[
                "id",
                "Tanggal transaksi",
                "kupon digunakan",
                "Kode",
                "Saldo awal",
                "Sisa saldo",
                "Total",
                "Tunai",
                "Diskon",
                "Cabang",
                "Menu"
            ]],
            use_container_width=True
        )

        # =============================
        # PENJUALAN MENU (SESUSAI FILTER / KODE)
        # =============================
        menu_rows = []

        for _, row in df_filt.iterrows():
            for item in str(row["items"]).split(","):
                m = re.match(r"(.+?)\s*[xX]\s*(\d+)", item.strip())
                if m:
                    menu_rows.append({
                        "Menu": m.group(1).strip().title(),
                        "Jumlah": int(m.group(2))
                    })

        df_menu = pd.DataFrame(menu_rows)

        if not df_menu.empty:
            st.subheader("📊 Penjualan Per Menu")
            st.dataframe(
                df_menu.groupby("Menu")["Jumlah"].sum().reset_index(),
                use_container_width=True
            )


    with tab_edit_seller:
        st.subheader("Kelola Seller")
        tab_kepemilikan, tab_acc, tab_aktivasi = st.tabs(["Kepemilikan Kupon", "Penerimaan Seller", "Aktivasi Kupon"])
    
        with tab_kepemilikan:
            st.subheader("🎯 Serahkan Kupon ke Seller")
    
            try:
                with engine.connect() as conn:
                    df_seller = pd.read_sql("""
                        SELECT * FROM public.seller
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
                            FROM public.vouchers
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
                                            FROM public.vouchers
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
                        FROM public.vouchers
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
                tanggal_akhir = colf2.date_input("Tanggal Akhir", value=date.today(), key="tanggal_akhir_laporan")
        
            # ============================
            # 📥 LOAD DATA
            # ============================
            vouchers = pd.read_sql("SELECT * FROM public.vouchers", engine)
            transactions = pd.read_sql("SELECT * FROM public.transactions", engine)
        
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
            df_tx = pd.read_sql("SELECT * FROM public.transactions", engine)
        
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
            satuan = st.selectbox(
                                    "Satuan",
                                    ["pcs", "kg"],
                                    index=0
                                )


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
                        satuan=satuan,
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

            tab21, tab22 = st.tabs(["Edit Menu", "Edit Kategori"])

            with tab21:
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
                    satuan_options = ["pcs", "kg"]
                    default_satuan = (selected.get("satuan") or "pcs").lower()

                    satuan = st.selectbox(
                        "Satuan",
                        satuan_options,
                        index=satuan_options.index(default_satuan)
                        if default_satuan in satuan_options else 0
)

                    status_options = ["aktif", "inaktif"]
                    default_status = (selected.get("status") or "").strip().lower()
                    
                    default_index = (
                        status_options.index(default_status)
                        if default_status in status_options
                        else 0
                    )
                    
                    status = st.selectbox(
                        "Status",
                        status_options,
                        index=default_index
                    )

                    # status_options = ["aktif", "inaktif"]
                    # default_status = (selected.get("status") or "").strip().lower()
                    # status = st.selectbox(
                    #     "Status",
                    #     status_options,
                    #     index=status_options.index(default_status)
                    # )
    
                    harga_sedati = st.text_input("Harga Sedati", value=str(selected["harga_sedati"] or ""))
                    harga_twsari = st.text_input("Harga Tawangsari", value=str(selected["harga_twsari"] or ""))
                    harga_kesambi = st.text_input("Harga Kesambi", value=str(selected["harga_kesambi"] or ""))
                    harga_tulangan = st.text_input("Harga Tulangan", value=str(selected["harga_tulangan"] or ""))
    
                    col1, col2 = st.columns(2)
    
                    with col1:
                        if st.button("Simpan Perubahan"):
                            update_menu_item(
                                id_menu, kategori, nama_item, keterangan,
                                harga_sedati, harga_twsari, harga_kesambi, harga_tulangan, status, satuan
                            )
                            st.success("Menu berhasil diperbarui!")
                            st.rerun()
    
                    with col2:
                        if st.button("Hapus Menu 🗑️"):
                            delete_menu_item(id_menu)
                            st.warning("Menu berhasil dihapus!")
                            st.rerun()

            with tab22:
                kategori_list = list_all_kategori()
                 
                if not kategori_list:
                    st.info("Belum ada kategori untuk diedit.")
                else:
                    # Mapping aman (id → label)
                    options = {
                        m["id_kategori"]: f"{m['nama_kategori']} - {m['status_kategori']}"
                        for m in kategori_list
                    }
            
                    pilih_id = st.selectbox(
                        "Pilih kategori yang akan diedit",
                        options.keys(),
                        format_func=lambda k: options[k]
                    )

                    selected_id = next(m["id_kategori"] for m in kategori_list if m["id_kategori"] == pilih_id)
                    selected_kategori = next(m for m in kategori_list if m["id_kategori"] == pilih_id)
            
                    kategori_options = ["aktif", "inaktif"]
                    default_kategori = (selected_kategori.get("status_kategori") or "").strip().lower()
            
                    status_kategori_new = st.selectbox(
                        "Status",
                        kategori_options,
                        index=kategori_options.index(default_kategori),
                        key=f"edit_status_{selected_id}"
                    )
            
                    if st.button("Simpan Perubahan", key=f"simpan_kategori_{selected_id}"):
                        update_kategori_menu(
                            pilih_id, status_kategori_new
                        )
                        st.success("Kategori berhasil diperbarui!")
                        st.rerun()
                 
    with tab_kupon:
        st.subheader("🎫 Buat Kupon Baru")

        jenis_list = []
        with engine.connect() as conn:
            df_jenis = pd.read_sql("SELECT jenis_kupon FROM public.jenis_db", conn)
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
                        FROM public.vouchers 
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
                        UPDATE public.vouchers
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
                    FROM public.vouchers
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

import streamlit as st
import pandas as pd
from datetime import date, datetime
from PIL import Image, ImageDraw, ImageFont
import io
from sqlalchemy import text 

st.set_page_config(
    page_title="Pawon Sappitoe",
    layout="wide",                    # Opsional: biar tampilan melebar
    initial_sidebar_state="expanded"  # <--- INI KUNCINYA (Supaya sidebar langsung terbuka)
)

# ============================================
# 1. HELPER: GENERATOR STRUK (LOGIKA ASLI)
# ============================================
def create_receipt_image(receipt):
    W, H = 500, 800
    bg_color = "white"
    text_color = "black"
    
    try:
        font_large = ImageFont.truetype("arial.ttf", 24)
        font_reg = ImageFont.truetype("arial.ttf", 18)
        font_bold = ImageFont.truetype("arialbd.ttf", 18)
    except:
        font_large = ImageFont.load_default()
        font_reg = ImageFont.load_default()
        font_bold = ImageFont.load_default()

    image = Image.new("RGB", (W, H), bg_color)
    draw = ImageDraw.Draw(image)

    y = 20
    margin = 20
    line_height = 25

    draw.text((W//2, y), "PAWON SAPPITOE", fill=text_color, anchor="ms", font=font_large)
    y += 35
    draw.text((W//2, y), f"Cabang: {receipt['cabang']}", fill=text_color, anchor="ms", font=font_reg)
    y += line_height
    draw.text((W//2, y), f"Tanggal: {receipt['tgl']}", fill=text_color, anchor="ms", font=font_reg)
    y += line_height + 10
    
    draw.line([(margin, y), (W-margin, y)], fill="black", width=2)
    y += 15

    for item in receipt['cart']:
        draw.text((margin, y), item['nama'], fill=text_color, font=font_bold)
        y += line_height
        qty_price = f"{item['qty']} x {item['harga_satuan']:,}"
        draw.text((margin, y), qty_price, fill="gray", font=font_reg)
        total_str = f"{item['total']:,}"
        bbox = draw.textbbox((0, 0), total_str, font=font_bold)
        text_width = bbox[2] - bbox[0]
        draw.text((W - margin - text_width, y), total_str, fill=text_color, font=font_bold)
        y += line_height + 5

    y += 10
    draw.line([(margin, y), (W-margin, y)], fill="black", width=2)
    y += 15

    def draw_row(label, value, color="black", is_bold=False):
        fnt = font_bold if is_bold else font_reg
        draw.text((margin, y), label, fill=color, font=fnt)
        val_str = f"{value:,}" if isinstance(value, int) else value
        bbox = draw.textbbox((0, 0), val_str, font=fnt)
        t_w = bbox[2] - bbox[0]
        draw.text((W - margin - t_w, y), val_str, fill=color, font=fnt)

    draw_row("Subtotal", receipt['subtotal'])
    y += line_height

    if receipt['diskon_manual'] > 0:
        draw_row("Diskon", f"- {receipt['diskon_manual']:,}", color="red")
        y += line_height

    if receipt['voucher_amt'] > 0:
        draw_row("Voucher", f"- {receipt['voucher_amt']:,}", color="blue")
        y += line_height

    y += 10
    draw_row("TOTAL BAYAR", f"Rp {receipt['total_final']:,}", is_bold=True)
    y += line_height + 20

    if receipt.get("voucher_details"):
        draw.line([(margin, y), (W-margin, y)], fill="gray", width=1)
        y += 15
        vd = receipt["voucher_details"]
        draw.text((margin, y), "INFO VOUCHER:", fill="black", font=font_bold)
        y += line_height
        draw.text((margin, y), f"Kode : {vd['code']}", fill="black", font=font_reg)
        y += line_height
        draw.text((margin, y), f"Nama : {vd['nama']}", fill="gray", font=font_reg)
        y += line_height
        draw.text((margin, y), f"HP   : {vd['hp']}", fill="gray", font=font_reg)
        y += line_height
        if receipt.get('sisa_saldo_voucher') is not None:
            sisa = int(receipt['sisa_saldo_voucher'])
            draw.text((margin, y), f"Sisa Saldo : Rp {sisa:,}", fill="black", font=font_bold)
            y += line_height
        y += 10

    y += 20
    draw.text((W//2, y), "*** TERIMA KASIH ***", fill=text_color, anchor="ms", font=font_reg)
    y += 40

    final_image = image.crop((0, 0, W, y))
    img_byte_arr = io.BytesIO()
    final_image.save(img_byte_arr, format='PNG')
    img_byte_arr = img_byte_arr.getvalue()
    return img_byte_arr

# ============================================
# 2. CSS CUSTOM (TAMPILAN BARU YG KAMU SUKA)
# ============================================
def apply_custom_css():
    st.markdown("""
    <style>
        /* =============================================
           1. SETUP TAMPILAN DASAR (Background Putih)
           ============================================= */
        .stApp { 
            background-color: #f8fafc !important; 
        }
        
        /* Default teks hitam/gelap untuk paragraf biasa */
        h1, h2, h3, h4, h5, h6, p, span, li, div {
            color: #1e293b;
        }

        /* Sidebar Background Putih */
        section[data-testid="stSidebar"] {
            background-color: #ffffff !important;
            border-right: 1px solid #e2e8f0;
        }

        /* =============================================
           2. INPUT FIELD (Cari, Qty, Kupon) - TIDAK DIUBAH
           ============================================= */
        /* Background Kotak Input Putih */
        div[data-baseweb="input"], div[data-baseweb="base-input"] {
            background-color: #ffffff !important;
            border: 1px solid #cbd5e1 !important;
            border-radius: 8px !important;
        }
        /* Tulisan yang diketik HITAM */
        input[type="text"], input[type="number"], input[type="password"] {
            color: #333333 !important;
            -webkit-text-fill-color: #333333 !important; 
        }

        /* =============================================
           3. SEMUA TOMBOL (FIX: BIRU & TULISAN PUTIH)
           ============================================= */
        
        /* A. TOMBOL SIDEBAR (Navigasi) */
        section[data-testid="stSidebar"] button {
            background-color: #3b82f6 !important; /* Biru */
            color: #ffffff !important;            /* PUTIH MUTLAK */
            border: none !important;
            border-radius: 8px !important;
            height: 45px !important;
            font-weight: 600 !important;
            margin-bottom: 8px !important;
            box-shadow: 0 2px 4px rgba(0,0,0,0.1) !important;
        }
        /* Saat kursor diarahkan ke tombol sidebar */
        section[data-testid="stSidebar"] button:hover {
            background-color: #2563eb !important;
            color: #ffffff !important; /* Tetap Putih */
        }
        /* Khusus teks di dalam tombol sidebar dipaksa putih */
        section[data-testid="stSidebar"] button p {
            color: #ffffff !important;
        }

        /* B. TOMBOL BIASA (Kembali, Cek Kupon, dll) */
        div[data-testid="stButton"] button {
            background-color: #3b82f6 !important; /* Biru */
            color: #ffffff !important;            /* PUTIH MUTLAK */
            border: none !important;
            border-radius: 8px !important;
            font-weight: 600 !important;
        }
        div[data-testid="stButton"] button:hover {
            background-color: #2563eb !important;
            color: #ffffff !important;
            box-shadow: 0 4px 6px rgba(59, 130, 246, 0.4);
        }
        /* Khusus teks di dalam tombol biasa dipaksa putih */
        div[data-testid="stButton"] button p {
            color: #ffffff !important;
        }

        /* C. TOMBOL PROSES (Primary) */
        button[kind="primary"] {
            background-color: #dc2626 !important; /* Merah/Sesuai selera */
            color: #ffffff !important;
        }
        button[kind="primary"] p {
            color: #ffffff !important;
        }

        /* =============================================
           4. CARD MENU (TIDAK DIUBAH - SESUAI PERMINTAAN)
           ============================================= */
        /* --- MENU CARD DESIGN (Shadow & Hover) --- */
        .menu-card {
            background-color: white;
            border-radius: 12px;
            padding: 15px;
            box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.1), 0 2px 4px -1px rgba(0, 0, 0, 0.06);
            border: 1px solid #e2e8f0;
            height: 100%;
            transition: transform 0.2s, box-shadow 0.2s;
            display: flex;
            flex-direction: column;
            justify-content: space-between;
        }
        
        .menu-card:hover {
            transform: translateY(-5px);
            box-shadow: 0 10px 15px -3px rgba(59, 130, 246, 0.3);
            border-color: #3b82f6;
        }

        .card-header {
            display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 8px;
        }
        .badge-cat {
            background-color: #dbeafe; color: #1e40af; font-size: 0.7rem;
            padding: 2px 8px; border-radius: 99px; font-weight: bold; text-transform: uppercase;
        }
        .menu-name {
            font-size: 1.1rem; font-weight: 700; color: #1e293b; margin-bottom: 5px; line-height: 1.2;
        }
        .menu-price {
            font-size: 1rem; color: #059669; font-weight: 800; margin-bottom: 10px;
        }
        
        /* Modifikasi Tabs agar lebih bersih */
        .stTabs [data-baseweb="tab-list"] { gap: 10px; background-color: transparent; }
        .stTabs [data-baseweb="tab"] {
            background-color: white; border-radius: 8px 8px 0 0; border: 1px solid #e2e8f0; padding: 10px 20px;
        }
        .stTabs [aria-selected="true"] {
            background-color: #3b82f6 !important; color: white !important; border: none;
        }

    </style>
    """, unsafe_allow_html=True)
# ============================================
# 3. FUNGSI UTAMA (GABUNGAN LOGIKA ASLI + UI BARU)
# ============================================
def page_kasir():
    apply_custom_css()
    
    # --- A. SIDEBAR NAVIGASI (GANTINYA TAB UTAMA) ---
    if "active_page" not in st.session_state: 
        st.session_state.active_page = "Pemesanan"

    with st.sidebar:
            st.title("NAVIGASI")
            if st.button("🛒 PEMESANAN", use_container_width=True):
                st.session_state.active_page = "Pemesanan"
            
            if st.button("📜 RIWAYAT", use_container_width=True):
                st.session_state.active_page = "Riwayat"

            curr = st.session_state.get("cabang", "Pusat")
            st.info(f"📍 Cabang: **{curr}**")
    
            # --- TAMBAHAN TOMBOL LOGOUT DISINI ---
            st.markdown("---")
            
            # Tombol Logout
            if st.button("🚪 KELUAR / LOGOUT", use_container_width=True):
                # 1. Hapus semua session state (bersih-bersih)
                for key in list(st.session_state.keys()):
                    del st.session_state[key]
                
                # 2. Rerun agar kembali ke halaman login (jika ada logic login di main.py)
                st.rerun()

    # --- B. KONTEN HALAMAN ---

    # -----------------------------------------------------
    # HALAMAN 1: PEMESANAN (UI BARU, DATA ASLI)
    # -----------------------------------------------------
    if st.session_state.active_page == "Pemesanan":
        st.header("Kasir / Transaksi")
        
        # Init State (Logika Asli)
        if "redeem_step" not in st.session_state: st.session_state.redeem_step = 1
        if "entered_code" not in st.session_state: st.session_state.entered_code = ""
        if "order_items" not in st.session_state: st.session_state.order_items = {} 
        if "diskon" not in st.session_state: st.session_state.diskon = 0
        if "isvoucher" not in st.session_state: st.session_state.isvoucher = "no"
       
        # --- STEP 1: PILIH MENU ---
        if st.session_state.redeem_step == 1:
            if "redeem_error" in st.session_state: del st.session_state["redeem_error"]

            selected_branch = st.session_state.get("cabang", "Pusat")
            st.session_state.selected_branch = selected_branch
            
            # 1. AMBIL DATA DARI DB ASLI
            raw_menu_items = get_menu_from_db(selected_branch)
            menu_items = []
            if raw_menu_items:
                for it in raw_menu_items:
                    try:
                        if pd.isna(it.get("id_menu")) or pd.isna(it.get("harga")): continue
                        menu_items.append({
                            "id_menu": int(it["id_menu"]),
                            "nama": str(it["nama"]),
                            "kategori": str(it.get("kategori", "Lainnya")),
                            "harga": (it["harga"]),
                            "satuan": it["satuan"]
                        })
                    except: continue
            
            if not menu_items:
                st.warning("Menu kosong.")
                st.stop()


            # --- 2. SEARCH BAR UI ---
            col_search, _ = st.columns([2, 1]) 
            with col_search:
                search_query = st.text_input("🔍 Cari Menu", placeholder="Ketik nama menu...").strip().lower()
            
            st.write("") # Jarak dikit biar rapi

            # --- 3. FUNGSI RENDER GRID (HELPER) ---
            # Fungsi ini dibuat biar kita gak nulis kode HTML Card berulang-ulang
            def render_grid(items_to_show, key_suffix):
                if not items_to_show:
                    st.info("Menu tidak ditemukan.")
                    return
                
                cols = st.columns(3) 
                for idx, item in enumerate(items_to_show):
                    with cols[idx % 3]:
                        # A. TAMPILAN CARD (HTML/CSS)
                        st.markdown(f"""
                        <div class="menu-card">
                            <div>
                                <div class="card-header">
                                    <span class="badge-cat">{item['kategori']}</span>
                                </div>
                                <div class="menu-name">{item['nama']}</div>
                                <div class="menu-price">Rp {item['harga']:,}</div>
                            </div>
                        </div>
                        """, unsafe_allow_html=True)
                        
                        # B. INPUT JUMLAH (QTY)
                        id_menu = item["id_menu"]
                        old_val = st.session_state.order_items.get(id_menu, 0)

                        if item["satuan"] == "kg":
                            qty = st.number_input(
                                f"qty_{id_menu}",
                                min_value=0.0,
                                value=float(old_val),
                                step=0.1,
                                format="%.2f",
                                label_visibility="collapsed",
                                key=f"inp_{id_menu}_{key_suffix}"
                            )
                        else:
                            qty = st.number_input(
                                f"qty_{id_menu}",
                                min_value=0,
                                value=int(old_val),
                                step=1,
                                label_visibility="collapsed",
                                key=f"inp_{id_menu}_{key_suffix}"
                            )

                        # ⬅️ SIMPAN TANPA int()
                        st.session_state.order_items[id_menu] = qty
                        st.write("") # Spacer bawah card

            # --- 4. LOGIKA UTAMA: SEARCH vs TABS ---
            if search_query:
                # KONDISI A: User sedang mencari sesuatu
                # Tampilkan semua hasil pencarian tanpa sekat Tab Kategori
                filtered_search = [m for m in menu_items if search_query in m["nama"].lower()]
                st.caption(f"Hasil pencarian: {len(filtered_search)} menu")
                render_grid(filtered_search, "search_mode")
            
            else:
                # KONDISI B: Tampilan Normal (Tabs Kategori)
                # Ambil daftar kategori unik & urutkan
                categories = sorted({item["kategori"] for item in menu_items}) 
                tabs = st.tabs(categories)

                for i, tab_name in enumerate(categories):
                    with tabs[i]:
                        # Filter menu yang kategorinya sesuai tab ini saja
                        filtered_tab = [m for m in menu_items if m["kategori"] == tab_name]
                        render_grid(filtered_tab, "tab_mode")

            # --- 5. FOOTER (TOTAL HARGA & TOMBOL LANJUT) ---
            # Hitung total belanja sementara
            price_map = {m["id_menu"]: m["harga"] for m in menu_items}
            total_sementara = sum(price_map.get(k,0)*v for k,v in st.session_state.order_items.items())
            
            if total_sementara > 0:
                st.markdown("---")
                st.success(f"💰 Total Sementara: **Rp {total_sementara:,}**")
                
                # Tombol Lanjut ke Step 2
                if st.button("Lanjut Bayar ➡️", type="primary", use_container_width=True):
                    st.session_state.redeem_step = 2
                    st.rerun()
           
        # --- STEP 2: KONFIRMASI (LOGIKA ASLI UTUH) ---
        elif st.session_state.redeem_step == 2:
            menu_db = get_menu_from_db(st.session_state.selected_branch)
            price_map = {m["id_menu"]: float(m["harga"]) for m in menu_db}
            name_map = {m["id_menu"]: m["nama"] for m in menu_db}
            
            cart_list = []
            subtotal = 0
            for pid, qty in st.session_state.order_items.items():
                if qty > 0 and pid in price_map:
                    tot = price_map[pid] * qty
                    subtotal += tot
                    cart_list.append({"nama": name_map[pid], "qty": qty, "harga_satuan": price_map[pid], "total": tot})

            if not cart_list:
                st.session_state.redeem_step = 1
                st.rerun()

            c1, c2 = st.columns(2)
            with c1:
                st.subheader("🧾 Rincian Pesanan")
                for item in cart_list:
                    st.write(f"**{item['nama']}** ({item['qty']}x) — Rp {item['total']:,}")
                st.markdown(f"### Total: Rp {subtotal:,}")
                if st.button("⬅️ Kembali"):
                    st.session_state.redeem_step = 1
                    st.rerun()

            with c2:
                st.subheader("💳 Pembayaran")
                # Kupon Logic
                code_in = st.text_input("Kode Kupon", value=st.session_state.entered_code).strip().upper()
                st.session_state.entered_code = code_in
                if st.button("Cek Kupon"):
                    st.session_state.isvoucher = "no"
                    st.session_state.voucher_row = None
                    row = find_voucher(code_in)
                    if row:
                        st.session_state.isvoucher = "yes"
                        st.session_state.voucher_row = row
                        st.success(f"Voucher: {row[3]} (Saldo: {row[2]:,})")
                    else:
                        st.error("Kupon tidak valid")

                # Diskon Manual Logic
                is_vou = (st.session_state.isvoucher == "yes")
                disc = st.number_input("Diskon Manual", min_value=0, step=1000, key="diskon", disabled=is_vou)
                
                # Hitung Final
                saldo_vou = 0
                if is_vou: 
                    saldo_vou = int(st.session_state.voucher_row[2])
                
                final_cash = subtotal - disc 
                if is_vou:
                    if saldo_vou >= subtotal: final_cash = 0
                    else: final_cash = subtotal - saldo_vou
                
                st.markdown(f"### Bayar Cash: Rp {final_cash:,}")

                if st.button("✅ PROSES", type="primary"):
                    items_str = ", ".join([f"{i['nama']} x{i['qty']}" for i in cart_list])
                    final_code = st.session_state.voucher_row[0] if is_vou else None
                    
                    # Call DB Logic
                    ok, msg, newbal = atomic_redeem(
                        final_code, 
                        (subtotal - disc), # Real trx amount
                        st.session_state.selected_branch, 
                        items_str, 
                        disc
                    )
                    
                    if ok:
                        transaksi_notification(
                            date.today(),
                            st.session_state.selected_branch,
                            (subtotal - disc)
                        )

                        v_details = None
                        vou_amt = 0
                        if is_vou:
                            row = st.session_state.voucher_row
                            v_details = {"code": row[0], "nama": row[3], "hp": row[4]}
                            vou_amt = subtotal if saldo_vou >= subtotal else saldo_vou

                        st.session_state.final_receipt = {
                            "cart": cart_list,
                            "subtotal": subtotal,
                            "diskon_manual": disc,
                            "voucher_amt": vou_amt,
                            "total_final": final_cash,
                            "tgl": datetime.now().strftime("%d-%m-%Y %H:%M"),
                            "cabang": st.session_state.selected_branch,
                            "sisa_saldo_voucher": newbal,
                            "voucher_details": v_details
                        }
                        st.session_state.redeem_step = 3
                        st.rerun()
                    else:
                        st.error(msg)

        # --- STEP 3: STRUK (LOGIKA ASLI) ---
        elif st.session_state.redeem_step == 3:
            receipt = st.session_state.get("final_receipt")
            st.success("Transaksi Berhasil!")
            
            col_img, col_btn = st.columns(2)
            with col_img:
                img_bytes = create_receipt_image(receipt)
                st.image(img_bytes, width=350)
            
            with col_btn:
                st.download_button("💾 Simpan Gambar", img_bytes, "struk.png", "image/png")
                if st.button("🏠 Transaksi Baru"):
                    # 1. Kosongkan Keranjang (Reset jadi list kosong)
                    st.session_state['order_items'] = {}
                    
                    # 2. Kembalikan ke Langkah 1 (Pilih Menu)
                    st.session_state['redeem_step'] = 1
                    
                    # 3. Hapus data struk lama (Cek dulu biar gak error)
                    if 'final_receipt' in st.session_state:
                        del st.session_state['final_receipt']
                    
                    # 4. Refresh Halaman
                    st.rerun()

    # -----------------------------------------------------
    # HALAMAN 2: RIWAYAT (PLACEHOLDER FUNGSI ASLI)
    # -----------------------------------------------------
    # ... (lanjutan dari if active_page == "Pemesanan") ...

    elif st.session_state.active_page == "Riwayat":
        st.header("📜 Riwayat Transaksi")
        
        # --- 1. LOAD DATA (LOGIKA ASLI) ---
        df_tx = list_transactions(limit=5000)
        df_tx = df_tx.sort_values(by="id", ascending=False).reset_index(drop=True)

        if df_tx.empty:
            st.info("Belum ada transaksi")
        else:
            # --- 2. PRE-PROCESSING (LOGIKA ASLI) ---
            df_tx["tanggal_transaksi"] = pd.to_datetime(df_tx["tanggal_transaksi"]).dt.date
            df_tx["code"] = df_tx["code"].fillna("")
            df_tx["isvoucher"] = df_tx["isvoucher"].fillna("no")
            min_date = df_tx["tanggal_transaksi"].min()
            max_date = df_tx["tanggal_transaksi"].max()

            # --- 3. FILTER UI (DIGABUNG DALAM CONTAINER BIAR RAPI) ---
            with st.container():
                st.write("**🔍 Filter Data**")
                col1, col2, col3, col4 = st.columns([2, 1.3, 1.3, 1.3])
                with col1:
                    search_code = st.text_input("Cari Kode Kupon", "", placeholder="Ketik kode...").strip()
                with col2:
                    start_date = st.date_input("Tanggal Mulai", value=min_date, min_value=min_date, max_value=max_date)
                with col3:
                    end_date = st.date_input("Tanggal Akhir", value=max_date, min_value=min_date, max_value=max_date)
                with col4:
                    filter_kupon = st.selectbox("Jenis Transaksi", ["semua", "Kupon", "Non Kupon"])

            # --- 4. FILTER LOGIC (LOGIKA ASLI) ---
            if start_date > end_date:
                st.error("❌ Tanggal Mulai tidak boleh setelah Tanggal Akhir")
                st.stop()
            
            df_tx = df_tx[(df_tx["tanggal_transaksi"] >= start_date) & (df_tx["tanggal_transaksi"] <= end_date)]

            filter_cabang = st.session_state.cabang
            # if filter_cabang != "semua": (Logic user)
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

            # --- 5. HITUNG TOTAL (LOGIKA ASLI) ---
            total_uang_filtered = df_tx["used_amount"].fillna(0).sum()
            total_cash_filtered = df_tx["tunai"].fillna(0).sum()
            total_kupon_filtered = total_uang_filtered - total_cash_filtered

            # --- 6. TAMPILAN STAT CARD (DESAIN BARU) ---
            st.markdown("<br>", unsafe_allow_html=True)
            c_stat1, c_stat2, c_stat3 = st.columns(3)
            
            with c_stat1:
                st.markdown(f"""
                <div class="stat-card">
                    <div class="stat-title">Total Pendapatan</div>
                    <div class="stat-value blue">Rp {total_uang_filtered:,}</div>
                </div>""", unsafe_allow_html=True)
            
            with c_stat2:
                st.markdown(f"""
                <div class="stat-card">
                    <div class="stat-title">Total Cash</div>
                    <div class="stat-value green">Rp {total_cash_filtered:,}</div>
                </div>""", unsafe_allow_html=True)
            
            with c_stat3:
                st.markdown(f"""
                <div class="stat-card">
                    <div class="stat-title">Total Dari Kupon</div>
                    <div class="stat-value purple">Rp {total_kupon_filtered:,}</div>
                </div>""", unsafe_allow_html=True)
            
            st.markdown("<hr>", unsafe_allow_html=True)

            # --- 7. SEARCH KUPON LOGIC (LOGIKA ASLI) ---
            if search_code:
                df_voucher_top = df_tx[df_tx["isvoucher"] == "yes"]
                df_filtered_top = df_voucher_top[df_voucher_top["code"].str.contains(search_code.upper(), case=False, na=False)]

                st.markdown(f"<div class='section-title'>Detail Kupon: {search_code.upper()}</div>", unsafe_allow_html=True)

                if df_filtered_top.empty:
                    st.warning(f"Tidak ada transaksi voucher untuk kupon {search_code}")
                else:
                    with st.expander("ℹ️ Informasi Lengkap Kupon", expanded=True):
                        total_transaksi = len(df_filtered_top)
                        total_nominal = df_filtered_top["used_amount"].sum()
                        initial_val = df_filtered_top["initial_value"].iloc[0]

                        k1, k2, k3 = st.columns(3)
                        k1.metric("Initial Value", f"Rp {initial_val:,}")
                        k2.metric("Jml Transaksi", f"{total_transaksi}")
                        k3.metric("Terpakai", f"Rp {total_nominal:,}")

                        df_tmp_display = df_filtered_top.rename(columns={
                            "tanggal_transaksi": "Tanggal_transaksi", "initial_value": "Initial_value",
                            "used_amount": "Total", "tunai": "Tunai", "branch": "Cabang", "items": "Menu", "code": "Kode"
                        })
                        st.dataframe(df_tmp_display[["Tanggal_transaksi", "Kode", "Initial_value", "Total", "Tunai", "Cabang", "Menu"]], use_container_width=True)
                        st.download_button(f"⬇️ Download CSV {search_code.upper()}", data=df_to_csv_bytes(df_tmp_display), file_name=f"transactions_{search_code.upper()}.csv", mime="text/csv")
                st.markdown("---")
            
            # --- 8. TABEL UTAMA ---
            st.markdown("<div class='section-title'>📋 Tabel Transaksi</div>", unsafe_allow_html=True)
            
            df_display = df_tx.rename(columns={
                        "code": "Kode", "used_amount": "Total", "tanggal_transaksi": "Tanggal_transaksi",
                        "branch": "Cabang", "items": "Menu", "tunai": "Tunai", "isvoucher": "kupon digunakan", "initial_value": "Initial_value"
                    })
            
            df_display["kupon digunakan"] = df_display["kupon digunakan"].apply(lambda x: "1" if x == "yes" else "0")
            df_display.loc[df_display["kupon digunakan"] == "0", "Total"] = df_display["Tunai"]

            st.dataframe(df_display[["id", "Tanggal_transaksi", "kupon digunakan", "Kode", "Initial_value", "Total", "Tunai", "Cabang", "Menu"]], use_container_width=True, height=400)

            st.download_button("⬇️ Download CSV Transaksi", data=df_to_csv_bytes(df_display), file_name="transactions.csv", mime="text/csv")
         
            # --- 9. ANALISIS MENU (LOGIKA ASLI) ---
            def normalize_name(s: str) -> str:
                if not isinstance(s, str): return ""
                s = s.strip().lower()
                s = " ".join(s.split())  
                s = unicodedata.normalize("NFKD", s)
                s = "".join(ch for ch in s if not unicodedata.combining(ch))
                return s
        
            menu_list = []
            for idx, row in df_tx.iterrows():
                menu_items = str(row.get("items", "")).split(",") 
                for item in menu_items:
                    raw = item.strip()
                    if not raw: continue
                    match = re.match(r"(.+?)\s*[xX]\s*(\d+(?:\.\d+)?)\s*$", raw)
                    if match:
                        nama_menu = match.group(1).strip()
                        jumlah = float(match.group(2))
                    else:
                        parts = raw.rsplit(" ", 1)
                        if len(parts) == 2 and parts[1].isdigit():
                            nama_menu = parts[0]; jumlah = int(parts[1])
                        else:
                            nama_menu = raw; jumlah = 1
                    menu_list.append({"Tanggal": row["tanggal_transaksi"], "Menu": normalize_name(nama_menu), "Jumlah": jumlah})
        
            df_menu = pd.DataFrame(menu_list)
            st.markdown("<br>", unsafe_allow_html=True)
            st.markdown("<div class='section-title'>📊 Analisis Penjualan Menu</div>", unsafe_allow_html=True)
            
            if df_menu.empty:
                st.info("Tidak ada menu terjual pada tanggal tersebut.")
            else:
                df_pivot = df_menu.groupby("Menu")["Jumlah"].sum().reset_index()
                df_pivot["Menu"] = df_pivot["Menu"].apply(lambda x: x.title())
                df_pivot = df_pivot.sort_values(by="Jumlah", ascending=False).reset_index(drop=True)
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





