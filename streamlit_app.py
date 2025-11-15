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

# ---------------------------
# Config / Secrets
# ---------------------------
DB_URL = st.secrets["DB_URL"]
ADMIN_PASSWORD = st.secrets.get("ADMIN_PASSWORD")  # admin password in st.secrets
# Seller password: fallback to the one you gave if not in secrets
SELLER_PASSWORD = st.secrets.get("SELLER_PASSWORD")

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
                    created_at TIMESTAMP NOT NULL
                )
            """))
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS transactions (
                    id SERIAL PRIMARY KEY,
                    code TEXT NOT NULL,
                    used_amount INTEGER NOT NULL,
                    tanggal_transaksi TIMESTAMP NOT NULL,
                    branch TEXT,
                    items TEXT
                )
            """))
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS menu_items (
                    id SERIAL PRIMARY KEY,
                    kategori TEXT,
                    nama_item TEXT,
                    keterangan TEXT,
                    harga_sedati INTEGER,
                    harga_twsari INTEGER
                )
            """))

            # optional columns used by app
            conn.execute(text("ALTER TABLE vouchers ADD COLUMN IF NOT EXISTS nama TEXT"))
            conn.execute(text("ALTER TABLE vouchers ADD COLUMN IF NOT EXISTS no_hp TEXT"))
            conn.execute(text("ALTER TABLE vouchers ADD COLUMN IF NOT EXISTS status TEXT"))
            conn.execute(text("ALTER TABLE vouchers ADD COLUMN IF NOT EXISTS seller TEXT"))
            conn.execute(text("ALTER TABLE vouchers ADD COLUMN IF NOT EXISTS tanggal_penjualan DATE"))
            conn.execute(text("ALTER TABLE vouchers ADD COLUMN IF NOT EXISTS tanggal_aktivasi DATE"))
            conn.execute(text("UPDATE vouchers SET status = 'inactive' WHERE status IS NULL"))
    except Exception as e:
        st.error(f"Gagal inisialisasi database: {e}")
        st.stop()


# ---------------------------
# DB helpers
# ---------------------------
def find_voucher(code):
    try:
        with engine.connect() as conn:
            row = conn.execute(text("""
                SELECT code, initial_value, balance, created_at, nama, no_hp, status, seller, tanggal_aktivasi
                FROM vouchers WHERE code = :c
            """), {"c": code}).fetchone()
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

def atomic_redeem(code, amount, branch, items_str):
    try:
        with engine.begin() as conn:

            # Ambil saldo voucher (lock row)
            r = conn.execute(
                text("SELECT balance, COALESCE(tunai, 0) FROM vouchers WHERE code = :c FOR UPDATE"),
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
                (code, used_amount, tanggal_transaksi, branch, items, tunai)
                VALUES (:c, :amt, :now, :branch, :items, :tunai)
            """), {
                "c": code,
                "amt": amount,
                "now": datetime.utcnow(),
                "branch": branch,
                "items": items_str,
                "tunai": shortage
            })

            # Update penjualan menu
            items = [x.strip() for x in items_str.split(",")]
            for i in items:
                if " x" not in i:
                    continue
                nama_item, qty = i.split(" x")
                qty = int(qty)

                col = "terjual_twsari" if branch == "Tawangsari" else "terjual_sedati"

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
    q = "SELECT code, initial_value, balance, created_at, nama, no_hp, status, seller, tanggal_aktivasi FROM vouchers"
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
    q += " ORDER BY created_at DESC LIMIT :limit OFFSET :offset"
    params["limit"] = limit
    params["offset"] = offset
    with engine.connect() as conn:
        df = pd.read_sql(text(q), conn, params=params)
    if "status" in df.columns:
        df["status"] = df["status"].fillna("inactive")
    else:
        df["status"] = "inactive"
    return df


def get_menu_from_db(branch):
    try:
        with engine.connect() as conn:
            df = pd.read_sql(text("SELECT kategori, nama_item, keterangan, harga_sedati, harga_twsari FROM menu_items"), conn)
        harga_col = "harga_sedati" if branch == "Sedati" else "harga_twsari"
        menu_list = []
        for _, row in df.iterrows():
            menu_list.append({
                "nama": row["nama_item"],
                "harga": row[harga_col],
                "kategori": row["kategori"]
            })
        return menu_list
    except Exception as e:
        st.error(f"Gagal ambil menu dari DB: {e}")
        return []


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


def list_transactions(limit=5000):
    q = "SELECT * FROM transactions ORDER BY tanggal_transaksi DESC LIMIT :limit"
    with engine.connect() as conn:
        return pd.read_sql(text(q), conn, params={"limit": limit})


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


# ---------------------------
# Session helpers
# ---------------------------
def ensure_session_state():
    st.session_state.setdefault("admin_logged_in", False)
    st.session_state.setdefault("seller_logged_in", False)
    st.session_state.setdefault("id_seller", None)
    st.session_state.setdefault("nama_seller", None)
    st.session_state.setdefault("page", "Penukaran Voucher")
    st.session_state.setdefault("redeem_step", 1)
    st.session_state.setdefault("entered_code", "")
    st.session_state.setdefault("voucher_row", None)
    st.session_state.setdefault("selected_branch", None)
    st.session_state.setdefault("order_items", {})
    st.session_state.setdefault("checkout_total", 0)
    st.session_state.setdefault("edit_code", None)
    st.session_state.setdefault("vouchers_page_idx", 0)
    st.session_state.setdefault("vouchers_per_page", 10)
    # track seller login ephemeral info
    st.session_state.setdefault("seller_mode", None)  # not storing identity, seller enters name during activation


def reset_redeem_state():
    for k in ["redeem_step","entered_code","voucher_row","selected_branch","order_items","checkout_total","new_balance"]:
        if k in st.session_state:
            del st.session_state[k]
    ensure_session_state()


def admin_login(password):
    return password == ADMIN_PASSWORD

def admin_logout():
    st.session_state.admin_logged_in = False
    st.session_state.page = "Penukaran Voucher"
    st.session_state.edit_code = None


def seller_logout():
    st.session_state.seller_logged_in = False
    st.session_state.id_seller = None
    st.session_state.nama_seller = None
    st.session_state.page = "Redeem Voucher"

def ensure_session_state():
    defaults = {
        "admin_logged_in": False,
        "seller_logged_in": False,
        "page": "Redeem Voucher",
    }
    for key, val in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = val

# ---------------------------
# Start
# ---------------------------
init_db()
ensure_session_state()
st.set_page_config(page_title="Voucher Pawon Sappitoe", layout="wide")
st.title("🎫 Kupon Pawon Sappitoe")

# ---------------------------
# Sidebar / Login UI
# ---------------------------
with st.sidebar:
    # If admin logged in -> full admin menu
    if st.session_state.admin_logged_in:
        st.success("Logged in as **Admin**")
        if st.button("Logout"):
            admin_logout()
            st.rerun()

    # If seller logged in -> limited menu (only a seller activation page)
    elif st.session_state.seller_logged_in:
        st.success("Logged in as **Seller**")
        if st.button("Logout Seller"):
            seller_logout()
            st.rerun()

    # Not logged in -> show login options
    else:
        st.markdown("### Login")
        login_admin, login_seller = st.tabs(["Admin", "Seller"])
        
        with login_admin:
            pwd_admin = st.text_input("Password Admin", type="password", key="pwd_admin")
            if st.button("Login sebagai Admin"):
                if admin_login(pwd_admin):
                    st.session_state.admin_logged_in = True
                    st.session_state.page = "Aktivasi Voucher"
                    st.success("Login admin berhasil")
                    st.rerun()
                else:
                    st.error("Password admin salah")
                    
        with login_seller:
            seller_id_input = st.text_input("Masukkan ID Seller Anda")
        
            if st.button("Login sebagai Seller"):
                if not seller_id_input.strip():
                    st.error("ID tidak boleh kosong")
                else:
                    with engine.connect() as conn:
                        row = conn.execute(
                            text("SELECT id_seller, nama_seller, status FROM seller WHERE id_seller = :id"),
                            {"id": seller_id_input.upper()}
                        ).fetchone()
        
                    if not row:
                        st.error("❌ ID tidak ditemukan")
                    else:
                        seller_id, seller_name, status = row
                        if status != "Accepted":
                            st.error("⛔ Akun Anda belum disetujui admin")
                        else:
                            st.session_state.seller_logged_in = True
                            st.session_state.id_seller = seller_id
                            st.session_state.nama_seller = seller_name
                            st.session_state.page = "Aktivasi Voucher Seller"
                            st.success(f"Login berhasil, selamat datang **{seller_name}** 👋")
                            st.rerun()

# ---------------------------
# Force page for non-admin/non-seller
# ---------------------------
page = st.session_state.get("page", "Redeem Voucher")

# Jika belum login admin/seller, tetap izinkan 2 halaman umum
if not (st.session_state.admin_logged_in or st.session_state.seller_logged_in):
    if page not in ("Redeem Voucher", "Daftar Sebagai Seller"):
        page = "Redeem Voucher"

# ---------------------------
# Page: Aktivasi Voucher (admin) — inline edit (unchanged except access)
# ---------------------------
def page_admin():
    tab_edit, tab_laporan, tab_histori, tab_edit_seller = st.tabs(["Informasi Kupon", "Laporan warung", "Histori", "Edit Seller"])

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
                ["semua", "active", "habis", "inactive"]
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
                    WHEN status = 'active' THEN 1
                    WHEN status = 'habis' THEN 2
                    WHEN status = 'inactive' THEN 3
                    ELSE 4
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
                                ["inactive", "active", "sold out"],
                                index=["inactive", "active", "sold out"].index(
                                    v["status"] if v["status"] in ["inactive", "active", "sold out"] else "inactive"
                                )
                            )
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
                                with engine.begin() as conn2:
                                    conn2.execute(text("""
                                        UPDATE vouchers
                                        SET nama = :nama,
                                            no_hp = :no_hp,
                                            status = :status,
                                            tanggal_penjualan = :tgl_jual,
                                            tanggal_aktivasi = :tgl_aktif
                                        WHERE code = :code
                                    """), {
                                        "nama": nama_in.strip(),
                                        "no_hp": nohp_in.strip(),
                                        "status": status_in,
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
        if df_tx.empty:
            st.info("Belum ada transaksi")
            return
    
        df_tx["tanggal_transaksi"] = pd.to_datetime(df_tx["tanggal_transaksi"]).dt.date
        min_date = df_tx["tanggal_transaksi"].min()
        max_date = df_tx["tanggal_transaksi"].max()
        
        col1, col2, col3, col4 = st.columns([2, 1.3, 1.3, 1.3])
        with col1:
            search_code = st.text_input("Cari kode kupon untuk detail histori", "").strip()
        
        with col2:
            start_date = st.date_input(
                "Tanggal Mulai",
                value=min_date,
                min_value=min_date,
                max_value=max_date
            )
        
        with col3:
            end_date = st.date_input(
                "Tanggal Akhir",
                value=max_date,
                min_value=min_date,
                max_value=max_date
            )

        with col4:
            filter_cabang = st.selectbox(
            "Filter Cabang",
            ["semua", "Sedati", "Tawangsari"]
        )
    
        # Normalisasi format tanggal transaksi
        df_tx["tanggal_transaksi"] = pd.to_datetime(df_tx["tanggal_transaksi"]).dt.date
    
        # Filter tanggal transaksi berdasarkan rentang
        if start_date and end_date:
            if start_date > end_date:
                st.error("❌ Tanggal Mulai tidak boleh setelah Tanggal Akhir")
                st.stop()
        
            df_tx = df_tx[
                (df_tx["tanggal_transaksi"] >= start_date) &
                (df_tx["tanggal_transaksi"] <= end_date)
            ]
    
        # 🏷 Filter cabang (jika tidak 'semua')
        if filter_cabang != "semua":
            df_tx = df_tx[df_tx["branch"] == filter_cabang]
    
        # Jika tidak ada data setelah filter
        if df_tx.empty:
            st.warning("Tidak ada transaksi dengan filter tersebut.")
            return
    
        try:
            # Jika user mencari kode tertentu
            if search_code:
                df_filtered = df_tx[df_tx["code"].str.contains(search_code.upper(), case=False)]
                if df_filtered.empty:
                    st.warning(f"Tidak ada transaksi untuk kupon {search_code}")
                else:
                    st.subheader(f"Detail Kupon: {search_code.upper()}")
                    total_transaksi = len(df_filtered)
                    total_nominal = df_filtered["used_amount"].sum()
                    st.write(f"- Jumlah transaksi: {total_transaksi}")
                    st.write(f"- Total nominal terpakai: Rp {total_nominal:,}")
                    
                    df_display = df_filtered.copy()
                    df_display = df_display.rename(columns={
                        "code":"Kode","used_amount":"Jumlah","tanggal_transaksi":"Tanggal_transaksi",
                        "branch":"Cabang","items":"Menu", "tunai":"Tunai"
                    })
                    df_display["Tunai"] = df_display["Tunai"].apply(
                        lambda x: "-" if pd.isna(x) else f"Rp {int(x):,}"
                    )
    
                    st.dataframe(df_display[["Kode","Tanggal_transaksi","Jumlah","Cabang","Menu", "Tunai"]], use_container_width=True)
                    st.download_button(
                        f"Download CSV {search_code.upper()}",
                        data=df_to_csv_bytes(df_display),
                        file_name=f"transactions_{search_code.upper()}.csv",
                        mime="text/csv"
                    )
    
            # Jika tidak ada kode yang dicari
            else:
                df_tx["Tunai"] = df_tx["tunai"].apply(lambda x: "-" if pd.isna(x) else f"Rp {int(x):,}")
                df_tx = df_tx.rename(columns={
                    "code":"Kode","used_amount":"Jumlah","tanggal_transaksi":"Tanggal_transaksi",
                    "branch":"Cabang","items":"Menu"
                })
    
                st.dataframe(df_tx[["Kode","Tanggal_transaksi","Jumlah","Cabang","Menu","Tunai"]], use_container_width=True)
                st.download_button(
                    "Download CSV Transaksi",
                    data=df_to_csv_bytes(df_tx),
                    file_name="transactions.csv",
                    mime="text/csv"
                )
    
        except Exception as e:
            st.error("❌ Gagal memuat transaksi")
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
                    ["Semua", "Sedati", "Tawangsari"]
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
                "total_voucher_habis": len(vouchers[vouchers["balance"] <= 0]),  # 🔴 Tambahan baru
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

    
        # ===== TAB Transaksi ====
        with tab_transaksi:
            st.subheader("📊 Ringkasan Transaksi")
        
            # Load data transaksi
            df_tx = pd.read_sql("SELECT * FROM transactions", engine)
        
            if df_tx.empty:
                st.info("Belum ada data transaksi.")
                st.stop()
        
            df_tx["tanggal_transaksi"] = pd.to_datetime(df_tx["tanggal_transaksi"])
        
            # =============================================
            # 🔍 FILTER AREA — sama seperti filter voucher
            # =============================================
            st.markdown("### 🔎 Filter Transaksi")
        
            f1, f2, f3 = st.columns([1, 1, 1])
        
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
                cabang_list = ["Semua"] + sorted(df_tx["branch"].dropna().unique().tolist())
                selected_cabang = st.selectbox("Cabang", cabang_list)
        
            # =============================================
            # 🔄 APPLY FILTER
            # =============================================
            df_filtered = df_tx[
                (df_tx["tanggal_transaksi"].dt.date >= start_date) &
                (df_tx["tanggal_transaksi"].dt.date <= end_date)
            ]
        
            if selected_cabang != "Semua":
                df_filtered = df_filtered[df_filtered["branch"] == selected_cabang]
        
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
            if not df_filtered.empty:
        
                st.subheader("🏪 Total Transaksi per Cabang")
                tx_count = df_filtered.groupby("branch")["code"].count().reset_index()
                tx_count.columns = ["Cabang", "Jumlah Transaksi"]
                st.bar_chart(tx_count, x="Cabang", y="Jumlah Transaksi")
        
                st.subheader("💰 Total Nominal per Cabang")
                tx_sum = df_filtered.groupby("branch")["used_amount"].sum().reset_index()
                tx_sum.columns = ["Cabang", "Total Nominal"]
                st.bar_chart(tx_sum, x="Cabang", y="Total Nominal")
        
            else:
                st.info("Tidak ada transaksi pada filter yang dipilih.")
        
            st.markdown("---")
        
            # =======================================================
            # 🏆 TOP 5 KUPOIN PALING SERING DIPAKAI
            # =======================================================
            st.subheader("🏆 Top 5 Kupon Paling Sering Digunakan")
        
            if not df_filtered.empty:
                top_voucher = (
                    df_filtered.groupby("code")["code"].count()
                    .sort_values(ascending=False)
                    .head(5)
                    .reset_index(name="Jumlah Transaksi")
                )
        
                st.table(top_voucher)
                st.bar_chart(top_voucher, x="code", y="Jumlah Transaksi")
            else:
                st.info("Tidak ada data voucher pada filter ini.")
        
            st.markdown("---")
        
            # =======================================================
            # 🍽 TOP 5 MENU TERLARIS
            # =======================================================
            st.subheader("🍽 Top 5 Menu Terlaris")
        
            try:
                # Query berdasarkan cabang
                if selected_cabang == "Semua":
                    query_menu = """
                        SELECT nama_item,
                        COALESCE(terjual_twsari,0) + COALESCE(terjual_sedati,0) AS "Terjual"
                        FROM menu_items
                    """
                else:
                    col = "terjual_twsari" if selected_cabang == "Tawangsari" else "terjual_sedati"
                    query_menu = f"""
                        SELECT nama_item,
                        COALESCE({col},0) AS "Terjual"
                        FROM menu_items
                    """
        
                df_menu = pd.read_sql(query_menu, engine)
                df_menu.rename(columns={"nama_item": "Menu"}, inplace=True)
        
                df_menu = df_menu.sort_values("Terjual", ascending=False).head(5)
        
                chart_menu = alt.Chart(df_menu).mark_bar().encode(
                    x=alt.X("Menu:N", title="Menu"),
                    y=alt.Y("Terjual:Q", title="Jumlah Terjual"),
                    tooltip=["Menu", "Terjual"]
                )
                st.altair_chart(chart_menu, use_container_width=True)
        
            except Exception as e:
                st.error(f"Gagal memuat data menu terlaris: {e}")
        
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
                st.stop()
        
            df_vouchers["seller"] = df_vouchers["seller"].fillna("-")
            df_seller_only = df_vouchers[df_vouchers["seller"] != "-"].copy()
        
            if df_seller_only.empty:
                st.info("Belum ada kupon yang dibawa seller.")
                st.stop()
        
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

    with tab_edit_seller:
        st.subheader("Kelola Seller")
        tab_kepemilikan, tab_acc = st.tabs(["Kepemilikan Kupon", "Penerimaan Seller"])

        with tab_kepemilikan:
            st.subheader("🎯 Serahkan Kupon ke Seller")
    
            try:
                with engine.connect() as conn:
                    df_seller = pd.read_sql("""
                        SELECT * FROM seller
                        WHERE status = 'Accepted'
                        ORDER BY nama_seller ASC
                    """, conn)
    
                if df_seller.empty:
                    st.info("Belum ada seller yang berstatus 'Accepted'.")
                else:
                    selected_seller = st.selectbox(
                        "Pilih Seller untuk diberikan kupon",
                        df_seller["nama_seller"].tolist()
                    )
    
                    selected_row = df_seller[df_seller["nama_seller"] == selected_seller].iloc[0]
                    seller_hp = selected_row["no_hp"]
    
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
                            ORDER BY created_at ASC
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
                        WHERE status = 'not accepted'
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
                        with col3:
                            if st.button("✅ Accept", key=f"accept_{row['nama_seller']}_{idx}"):
                                try:
                                    with engine.begin() as conn2:
                                        conn2.execute(
                                            text("""
                                                UPDATE seller
                                                SET status = 'Accepted'
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
            
# ---------------------------
# Page: Seller Activation (seller-only)
# ---------------------------
def page_seller_activation():
    st.subheader("Aktivasi Kupon")

    st.info(
        "Masukkan kode kupon dan nama anda (seller), setelah itu masukkan nama dan nomer HP pembeli kupon untuk aktivasi."
    )

    with st.form(key="seller_activation_form"):
        seller_name_input = st.session_state.get("nama_seller", "-")
        st.success(f"Seller: **{seller_name_input}** (otomatis dari login)")
        kode = st.text_input("Kode Kupon").strip().upper()
        buyer_name_input = st.text_input("Nama Pembeli").strip()
        buyer_phone_input = st.text_input("No HP Pembeli").strip()
        tanggal_aktivasi = st.date_input("Tanggal Aktivasi", value=pd.to_datetime("today"), key="assign_tanggal_aktivasi")
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
                        SELECT seller, status, tanggal_penjualan FROM vouchers WHERE code = :code
                    """),
                    {"code": kode}
                ).fetchone()

                if not result:
                    st.error("Kode kupon tidak ditemukan.")
                    return

                db_seller, db_status, tanggal_penjualan = result

                # Jika voucher belum diassign seller oleh admin
                if not db_seller or db_seller.strip() == "":
                    st.error("Kupon belum diserahkan ke seller mana pun. Aktivasi ditolak.")
                    return

                # Jika seller input tidak cocok dengan seller di database
                if db_seller != st.session_state.nama_seller:
                    st.error("Voucher bukan milik Anda.")
                    return

                if tanggal_penjualan and tanggal_aktivasi < tanggal_penjualan:
                    st.error(f"❌ Tanggal Aktivasi tidak boleh sebelum Tanggal Penjualan ({tanggal_penjualan})")
                    tanggal_aktivasi = None  # opsional: reset nilai agar user pilih ulang
                    return

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
                            tanggal_aktivasi = :tgl,
                            status = 'active'
                        WHERE code = :code
                    """),
                    {
                        "nama": buyer_name_input,
                        "no_hp": buyer_phone_input,
                        "tgl": tanggal_aktivasi,
                        "code": kode,
                    }
                )

            st.success(f"✅ Kupon {kode} berhasil diaktivasi untuk pembeli {buyer_name_input}.")

        except Exception as e:
            st.error("❌ Terjadi kesalahan saat mengupdate data kupon.")
            st.code(str(e))

    st.markdown("---")
    st.info(
        "Note: Setelah berhasil diaktivasi oleh Seller, data akan dikunci (seller tidak bisa mengedit lagi). "
        "Jika perlu koreksi, minta admin untuk ubah data."
    )
        
# ---------------------------
# Init app
# ---------------------------

# Jika admin login → langsung ke halaman admin
if st.session_state.admin_logged_in and not st.session_state.seller_logged_in:
    page_admin()
    st.stop()

# Jika seller login → langsung ke halaman seller
if st.session_state.seller_logged_in and not st.session_state.admin_logged_in:
    page_seller_activation()
    st.stop()

# Jika keduanya tidak login → tampil tab publik
if not st.session_state.admin_logged_in and not st.session_state.seller_logged_in:
    tukar_kupon, daftar_seller = st.tabs(["Tukar Kupon", "Daftar sebagai Seller"])

    with tukar_kupon:
        st.header("Penukaran Kupon")
    
        # Inisialisasi state
        if "redeem_step" not in st.session_state:
            st.session_state.redeem_step = 1
        if "entered_code" not in st.session_state:
            st.session_state.entered_code = ""
    
        # STEP 1: Input kode voucher
        if st.session_state.redeem_step == 1:
            entered_code = st.text_input(
                "Masukkan kode kupon",
                key="entered_code_input",
                value=st.session_state.entered_code
            ).strip().upper()
    
            st.session_state.entered_code = entered_code
        
            if st.button("Tukar Kupon"):
                code = st.session_state.entered_code
                st.session_state.pop('redeem_error', None)
    
                if not code:
                    st.session_state['redeem_error'] = "⚠️ Kode tidak boleh kosong"
                else:
                    row = find_voucher(code)
                    if not row:
                        st.session_state['redeem_error'] = "❌ Voucher tidak ditemukan."
                    else:
                        code, initial_value, balance, created_at, nama, no_hp, status, seller, tanggal_aktivasi = row
                    
                        status_normalized = (status or "").strip().lower()
                    
                        if status_normalized == "inactive":
                            st.session_state['redeem_error'] = "⛔ Voucher belum aktif dan tidak bisa digunakan."
                        elif status_normalized == "sold out" or balance <= 0:
                            st.session_state['redeem_error'] = "⛔ Saldo voucher sudah habis dan tidak bisa digunakan."
                        elif status_normalized != "active":
                            st.session_state['redeem_error'] = f"⛔ Voucher tidak dapat digunakan. Status: {status}"
                        else:
                            if tanggal_aktivasi is None:
                                st.session_state['redeem_error'] = "⛔ Voucher belum bisa digunakan. Tanggal aktivasi belum tercatat."
                            else:
                                if hasattr(tanggal_aktivasi, "date"):
                                    tgl_aktivasi = tanggal_aktivasi.date()
                                else:
                                    try:
                                        tgl_aktivasi = datetime.strptime(str(tanggal_aktivasi), "%Y-%m-%d").date()
                                    except Exception:
                                        tgl_aktivasi = None
                    
                                if tgl_aktivasi == date.today():
                                    st.session_state['redeem_error'] = "⛔ Voucher belum bisa digunakan. Penukaran hanya bisa dilakukan H+1 setelah voucher diaktifkan."
                                else:
                                    st.session_state.voucher_row = row
                                    st.session_state.redeem_step = 2
                                    st.session_state.pop('redeem_error', None)
                                    st.rerun()
    
            if 'redeem_error' in st.session_state:
                st.error(st.session_state['redeem_error'])
    
        # STEP 2: Pilih cabang & menu
        elif st.session_state.redeem_step == 2:
            row = st.session_state.voucher_row
            code, initial_value, balance, created_at, nama, no_hp, status, seller, tanggal_penjualan = row
        
            st.subheader(f"Kupon: {code}")
            st.write(f"- Nilai awal: Rp {int(initial_value):,}")
            st.write(f"- Sisa saldo: Rp {int(balance):,}")
            st.write(f"- Nama: {nama or '-'}")
            st.write(f"- No HP: {no_hp or '-'}")
            st.write(f"- Status: {status or 'inactive'}")
        
            if int(balance) <= 0:
                st.warning("Kupon sudah tidak dapat digunakan (saldo 0).")
                if st.button("Kembali"):
                    reset_redeem_state()
                    st.rerun()
                st.stop()
    
            branch_options = ["Sedati", "Tawangsari"]
            selected_branch = st.selectbox("Pilih cabang", branch_options, index=0)
            st.session_state.selected_branch = selected_branch
    
            menu_items = get_menu_from_db(selected_branch)
    
            # Normalisasi menu
            normalized = []
            if menu_items:
                if isinstance(menu_items[0], tuple):
                    for m in menu_items:
                        try:
                            kategori = m[0]
                            nama_item = m[1]
                            keterangan = m[2]
                            harga_sedati = m[3]
                            harga_twsari = m[4]
                        except Exception:
                            continue
    
                        if selected_branch == "Sedati":
                            harga = harga_sedati
                        else:
                            harga = harga_twsari
    
                        if harga is None:
                            continue
    
                        normalized.append({
                            "kategori": kategori,
                            "nama": nama_item,
                            "keterangan": keterangan,
                            "harga": int(harga)
                        })
    
                elif isinstance(menu_items[0], dict):
                    for it in menu_items:
                        harga = it.get("harga")
                        if harga is None or pd.isna(harga):
                            continue
                        normalized.append({
                            "kategori": it.get("kategori"),
                            "nama": it.get("nama"),
                            "keterangan": it.get("keterangan", ""),
                            "harga": int(harga)
                        })
    
            if not normalized:
                st.info("Tidak ada menu yang tersedia untuk cabang ini.")
                st.stop()
    
            menu_items = normalized
    
            categories = sorted({item["kategori"] for item in menu_items if item.get("kategori") is not None})
    
            if not categories:
                st.info("Tidak ada kategori menu untuk ditampilkan.")
                st.stop()
        
            search_query = st.text_input("🔍 Cari menu", "").strip().lower()
    
            if "order_items" not in st.session_state:
                st.session_state.order_items = {}
        
            st.markdown("*Pilih menu*")
        
            if search_query:  
                filtered_items = [item for item in menu_items if search_query in item['nama'].lower()]
                if not filtered_items:
                    st.info("Menu tidak ditemukan")
                for item in filtered_items:
                    key = f"{selected_branch}_{item['nama']}"
                    old_qty = st.session_state.order_items.get(item['nama'], 0)
                    qty = st.number_input(
                        f"{item['nama']} (Rp {item['harga']:,})",
                        min_value=0,
                        value=old_qty,
                        step=1,
                        key=key
                    )
                    st.session_state.order_items[item['nama']] = qty
            else:
                tabs = st.tabs(categories)
                for i, cat in enumerate(categories):
                    with tabs[i]:
                        cat_items = [item for item in menu_items if item["kategori"] == cat]
                        for item in cat_items:
                            key = f"{selected_branch}_{item['nama']}"
                            old_qty = st.session_state.order_items.get(item['nama'], 0)
                            qty = st.number_input(
                                f"{item['nama']} (Rp {item['harga']:,})",
                                min_value=0,
                                value=old_qty,
                                step=1,
                                key=key
                            )
                            st.session_state.order_items[item['nama']] = qty
        
            checkout_total = 0
            for it, q in st.session_state.order_items.items():
                if q > 0:
                    price = next((m['harga'] for m in menu_items if m['nama'] == it), 0)
                    checkout_total += price * q
        
            st.session_state.checkout_total = checkout_total
            st.write(f"*Total sementara: Rp {checkout_total:,}*")
        
            cA, cB = st.columns([1,1])
            with cA:
                if st.button("Cek & Bayar"):
                    if checkout_total == 0:
                        st.warning("Pilih minimal 1 menu")
                    else:
                        st.session_state.redeem_step = 3
                        st.rerun()
            with cB:
                if st.button("Batal / Kembali"):
                    reset_redeem_state()
                    st.rerun()
    
        # Inisialisasi tambahan step 3
        if "redeem_step" not in st.session_state:
            st.session_state.redeem_step = 1
        if "show_success" not in st.session_state:
            st.session_state.show_success = False
        if "newbal" not in st.session_state:
            st.session_state.newbal = 0
    
        # Step 3: Konfirmasi pembayaran
        if st.session_state.redeem_step == 3:
            row = st.session_state.voucher_row
            code, initial, balance, created_at, nama, no_hp, status, seller, tanggal_penjualan = row
            
            st.header("Konfirmasi Pembayaran")
            st.write(f"- Kupon: {code}")
            st.write(f"- Cabang: {st.session_state.selected_branch}")
            st.write(f"- Sisa Voucher: Rp {int(balance):,}")
        
            menu_items = get_menu_from_db(st.session_state.selected_branch)
            price_map = {item['nama']: item['harga'] for item in menu_items}
        
            ordered_items = {k:v for k,v in st.session_state.order_items.items() if v > 0}
        
            if not ordered_items:
                st.warning("Tidak ada menu yang dipilih.")
                st.stop()
        
            total = st.session_state.checkout_total
            saldo = int(balance)
            shortage = total - saldo if total > saldo else 0
        
            st.write("Detail pesanan:")
            for it, q in ordered_items.items():
                st.write(f"- {it} x{q} — Rp {price_map.get(it,0)*q:,}")
        
            st.write(f"### Total: Rp {total:,}")
        
            if shortage > 0:
                st.write(f"#### Bayar Cash: Rp {shortage:,}")
                st.error(f"⚠️ Saldo kupon kurang Rp {shortage:,}. Sisa total harus dibayar dengan *cash* oleh pembeli.")
            else:
                st.success("Saldo kupon mencukupi 🎉")
        
            cA, cB = st.columns([1,1])
            with cA:
                if st.button("Ya, Bayar"):
                    items_str = ", ".join([f"{k} x{v}" for k,v in ordered_items.items()])
                    ok, msg, newbal = atomic_redeem(
                        code, total, st.session_state.selected_branch, items_str
                    )
                    if ok:
                        st.session_state.newbal = newbal
                        st.session_state.show_success = True
                    else:
                        st.error(msg)
                        st.session_state.redeem_step = 2
                        st.rerun()
            with cB:
                if st.button("Tidak, Kembali"):
                    st.session_state.redeem_step = 2
                    st.rerun()
    
        # Pop-up berhasil
        if st.session_state.show_success:
            st.success(
                f"🎉 TRANSAKSI BERHASIL 🎉\nSisa saldo sekarang: Rp {int(st.session_state.newbal):,}"
            )
            st.write("Tutup pop-up ini untuk kembali ke awal.")
            if st.button("Tutup"):
                reset_redeem_state() 
                st.session_state.show_success = False
                st.rerun()
    
    with daftar_seller:
        st.header("📋 Daftar Sebagai Seller")
        st.write("Silakan isi data berikut untuk mendaftar sebagai seller.")
    
        with st.form("form_daftar_seller"):
            nama = st.text_input("Nama lengkap")
            nohp = st.text_input("No HP")
    
            submit = st.form_submit_button("Daftar")
    
        if submit:
            if not nama.strip():
                st.error("Nama tidak boleh kosong.")
            elif not nohp.strip():
                st.error("No HP tidak boleh kosong.")
            else:
                try:
                    def generate_unique_id():
                        chars = string.ascii_uppercase + string.digits
                        new_id = "".join(random.choices(chars, k=3))
                        
                        # Cek apakah sudah ada di DB
                        with engine.connect() as conn_check:
                            existing = conn_check.execute(
                                text("SELECT id_seller FROM seller WHERE id_seller = :id"),
                                {"id": new_id}
                            ).fetchone()
        
                        # Jika sudah ada → ulangi
                        if existing:
                            return generate_unique_id()
                        return new_id
        
                    id_seller = generate_unique_id()
        
                    # Simpan ke database
                    with engine.begin() as conn:
                        conn.execute(
                            text("""
                                INSERT INTO seller (nama_seller, no_hp, status, id_seller)
                                VALUES (:nama, :no_hp, :status, :id_seller)
                            """),
                            {
                                "nama": nama.strip(),
                                "no_hp": nohp.strip(),
                                "status": "not accepted",
                                "id_seller": id_seller,
                            }
                        )
        
                    st.warning(
                        f"⚠️ **SANGAT PENTING!**\n"
                        f"Simpan ID berikut untuk aktivasi voucher setelah Anda disetujui admin:\n\n"
                        f"🔐 **ID Seller Anda: {id_seller}**"
                    )
                    st.success(f"🎉 Pendaftaran berhasil!")
        
                except Exception as e:
                    st.error("❌ Gagal menyimpan data ke database.")
                    st.code(str(e))






