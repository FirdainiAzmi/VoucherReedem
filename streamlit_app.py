# app.py
import streamlit as st
import pandas as pd
from datetime import datetime, date
from sqlalchemy import create_engine, text
from io import BytesIO
import altair as alt
import matplotlib.pyplot as plt
import math
import traceback

# ---------------------------
# Config / Secrets
# ---------------------------
DB_URL = st.secrets["DB_URL"]
ADMIN_PASSWORD = st.secrets.get("ADMIN_PASSWORD")  # admin password in st.secrets
# Seller password: fallback to the one you gave if not in secrets
SELLER_PASSWORD = st.secrets.get("SELLER_PASSWORD", "sellerpwspt")

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
            r = conn.execute(
                text("SELECT balance FROM vouchers WHERE code = :c FOR UPDATE"),
                {"c": code}
            ).fetchone()

            if not r:
                return False, "Voucher tidak ditemukan.", None

            balance = r[0]
            if balance < amount:
                return False, f"Saldo tidak cukup (sisa: {balance}).", balance

            conn.execute(
                text("UPDATE vouchers SET balance = balance - :amt WHERE code = :c"),
                {"amt": amount, "c": code}
            )

            conn.execute(text("""
                INSERT INTO transactions (code, used_amount, tanggal_transaksi, branch, items)
                VALUES (:c, :amt, :now, :branch, :items)
            """), {
                "c": code,
                "amt": amount,
                "now": datetime.utcnow(),
                "branch": branch,
                "items": items_str
            })

            # update menu sold count
            items = [x.strip() for x in items_str.split(",")]
            for i in items:
                if " x" not in i:
                    continue
                nama_item, qty = i.split(" x")
                qty = int(qty)
                column = "terjual_twsari" if branch == "Tawangsari" else "terjual_sedati"
                conn.execute(text(f"""
                    UPDATE menu_items
                    SET {column} = COALESCE({column}, 0) + :qty
                    WHERE nama_item = :item
                """), {"qty": qty, "item": nama_item})

            return True, "Redeem berhasil ✅", balance - amount

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
    st.session_state.page = "Penukaran Voucher"


# ---------------------------
# Init app
# ---------------------------
init_db()
ensure_session_state()
st.set_page_config(page_title="Voucher Pawon Sappitoe", layout="wide")
st.title("🎫 Voucher Pawon Sappitoe")


# ---------------------------
# Sidebar / Login UI
# ---------------------------
with st.sidebar:
    st.markdown("## Menu")

    pageumum_choice = st.radio(
        "Pilih Halaman",
        ("Redeem Voucher", "Daftar Sebagai Seller"),
        index=("Redeem Voucher", "Daftar Sebagai Seller").index(
            st.session_state.get("page")
            if st.session_state.get("page") in ("Redeem Voucher", "Daftar Sebagai Seller")
            else "Redeem Voucher"
        )
    )
    st.session_state.page = pageumum_choice
    
    # If admin logged in -> full admin menu
    if st.session_state.admin_logged_in:
        st.success("Logged in as **Admin**")
        if st.button("Logout"):
            admin_logout()
            st.rerun()

        st.markdown("---")
        page_choice = st.radio("Pilih halaman Admin",
                               ("Aktivasi Voucher", "Laporan Warung", "Histori Transaksi", "Seller"),
                               index=("Aktivasi Voucher","Laporan Warung","Histori Transaksi","Seller").index(
                                   st.session_state.get("page") if st.session_state.get("page") in ("Aktivasi Voucher","Laporan Warung","Histori Transaksi","Seller") else "Aktivasi Voucher"
                               ))
        st.session_state.page = page_choice

    # If seller logged in -> limited menu (only a seller activation page)
    elif st.session_state.seller_logged_in:
        st.success("Logged in as **Seller**")
        if st.button("Logout Seller"):
            seller_logout()
            st.rerun()

        st.markdown("---")
        page_choice = st.radio("Pilih halaman Seller", ("Aktivasi Voucher Seller",), index=0)
        st.session_state.page = page_choice

    # Not logged in -> show login options
    else:
        st.markdown("### Login")
        pwd_admin = st.text_input("Password Admin", type="password", key="pwd_admin")
        if st.button("Login sebagai Admin"):
            if admin_login(pwd_admin):
                st.session_state.admin_logged_in = True
                st.session_state.page = "Aktivasi Voucher"
                st.success("Login admin berhasil")
                st.rerun()
            else:
                st.error("Password admin salah")

        st.markdown("---")
        pwd_seller = st.text_input("Password Seller", type="password", key="pwd_seller")
        if st.button("Login sebagai Seller"):
            if pwd_seller == SELLER_PASSWORD:
                st.session_state.seller_logged_in = True
                st.session_state.page = "Aktivasi Voucher Seller"
                st.success("Login seller berhasil")
                st.rerun()
            else:
                st.error("Password seller salah")

        st.markdown("---")
        st.info("Admin: akses penuh. Seller: akses terbatas (Aktivasi Voucher).")


# ---------------------------
# Force page for non-admin/non-seller
# ---------------------------
page = st.session_state.get("page", "Redeem Voucher")

# Jika belum login admin/seller, tetap izinkan 2 halaman umum
if not (st.session_state.admin_logged_in or st.session_state.seller_logged_in):
    if page not in ("Redeem Voucher", "Daftar Sebagai Seller"):
        page = "Redeem Voucher"

# ---------------------------
# Page: Penukaran Voucher (public)
# ---------------------------
def page_redeem():
    st.header("Penukaran Voucher")

    # STEP 1: Input kode voucher
    if st.session_state.redeem_step == 1:
        st.session_state.entered_code = st.text_input(
            "Masukkan kode voucher", 
            value=st.session_state.entered_code
        ).strip().upper()
    
        if st.button("Submit Kode"):
            code = st.session_state.entered_code
            if not code:
                st.error("Kode tidak boleh kosong")
            else:
                row = find_voucher(code)
                if not row:
                    st.error("❌ Voucher tidak ditemukan.")
                    reset_redeem_state()
                    st.rerun()
    
                # Ambil data row
                code, initial_value, balance, created_at, nama, no_hp, status, seller, tanggal_aktivasi = row
    
                # ✅ Validasi status
                if status is None or str(status).lower() != "active":
                    st.error("⛔ Voucher belum dapat digunakan. Status masih INACTIVE.")
                    reset_redeem_state()
                    st.rerun()
    
                # ✅ Validasi tanggal_aktivasi
                if tanggal_aktivasi is None:
                    st.error("⛔ Voucher belum bisa digunakan. Tanggal aktivasi belum tercatat.")
                    reset_redeem_state()
                    st.rerun()

                if hasattr(tanggal_aktivasi, "date"):
                    tgl_aktivasi = tanggal_aktivasi.date()
                else:
                    try:
                        tgl_aktivasi = datetime.strptime(str(tanggal_aktivasi), "%Y-%m-%d").date()
                    except Exception:
                        tgl_aktivasi = None
    
                # ✅ Tidak boleh dipakai HARI YANG SAMA
                if tgl_aktivasi == date.today():
                    st.session_state['redeem_error'] = "⛔ Voucher belum bisa digunakan. Penukaran hanya bisa dilakukan H+1 setelah voucher diaktifkan."
                    reset_redeem_state()
                    st.rerun()
    
                # ✅ Jika semua valid → lanjut
                st.session_state.voucher_row = row
                st.session_state.redeem_step = 2
                st.rerun()

        if 'redeem_error' in st.session_state:
            st.error(st.session_state['redeem_error'])
            del st.session_state['redeem_error']

    # STEP 2: Pilih cabang & menu
    elif st.session_state.redeem_step == 2:
        row = st.session_state.voucher_row
        code, initial_value, balance, created_at, nama, no_hp, status, seller, tanggal_penjualan = row
    
        st.subheader(f"Voucher: {code}")
        st.write(f"- Nilai awal: Rp {int(initial_value):,}")
        st.write(f"- Sisa saldo: Rp {int(balance):,}")
        st.write(f"- Nama: {nama or '-'}")
        st.write(f"- No HP: {no_hp or '-'}")
        st.write(f"- Status: {status or 'inactive'}")
    
        if int(balance) <= 0:
            st.warning("Voucher sudah tidak dapat digunakan (saldo 0).")
            if st.button("Kembali"):
                reset_redeem_state()
                st.rerun()
            return
    
        # Pilih cabang
        branch_options = ["Sedati", "Tawangsari"]
        selected_branch = st.selectbox("Pilih cabang", branch_options, index=0)
        st.session_state.selected_branch = selected_branch
 
        menu_items = get_menu_from_db(selected_branch)  
        categories = sorted(list(set([item["kategori"] for item in menu_items])))
    
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
    
        # Hitung total
        checkout_total = 0
        for it, q in st.session_state.order_items.items():
            if q > 0:
                price = next((m['harga'] for m in menu_items if m['nama'] == it), 0)
                checkout_total += price * q
    
        st.session_state.checkout_total = checkout_total
        st.write(f"*Total sementara: Rp {checkout_total:,}*")
    
        # Tombol bayar & batal
        cA, cB = st.columns([1,1])
        with cA:
            if st.button("Cek & Bayar"):
                if checkout_total == 0:
                    st.warning("Pilih minimal 1 menu")
                elif checkout_total > int(balance):
                    st.error(f"Saldo tidak cukup. Total: Rp {checkout_total:,} — Saldo: Rp {int(balance):,}")
                else:
                    st.session_state.redeem_step = 3
                    st.rerun()
        with cB:
            if st.button("Batal / Kembali"):
                reset_redeem_state()
                st.rerun()
                
    # ===== Inisialisasi session_state =====
    if "redeem_step" not in st.session_state:
        st.session_state.redeem_step = 1
    
    if "show_success" not in st.session_state:
        st.session_state.show_success = False
    
    if "newbal" not in st.session_state:
        st.session_state.newbal = 0

    # ===== Step 3: Konfirmasi Pembayaran =====
    if st.session_state.redeem_step == 3:
        row = st.session_state.voucher_row
        code, initial, balance, created_at, nama, no_hp, status, seller, tanggal_penjualan = row
        
        st.header("Konfirmasi Pembayaran")
        st.write(f"- Voucher: {code}")
        st.write(f"- Cabang: {st.session_state.selected_branch}")
        st.write(f"- Sisa Voucher: Rp {int(balance):,}")
   
        menu_items = get_menu_from_db(st.session_state.selected_branch)
        price_map = {item['nama']: item['harga'] for item in menu_items}
    
        ordered_items = {k:v for k,v in st.session_state.order_items.items() if v > 0}
    
        if not ordered_items:
            st.warning("Tidak ada menu yang dipilih.")
            return
    
        st.write("Detail pesanan:")
        for it, q in ordered_items.items():
            st.write(f"- {it} x{q} — Rp {price_map.get(it,0)*q:,}")
    
        st.write(f"### Total: Rp {st.session_state.checkout_total:,}")
    
        cA, cB = st.columns([1,1])
        with cA:
            if st.button("Ya, Bayar"):
                items_str = ", ".join([f"{k} x{v}" for k,v in ordered_items.items()])
                ok, msg, newbal = atomic_redeem(
                    code, st.session_state.checkout_total,
                    st.session_state.selected_branch, items_str
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
    
    # ===== Pop-up transaksi berhasil =====
    if st.session_state.show_success:
        st.success(f"🎉 TRANSAKSI BERHASIL 🎉\nSisa saldo sekarang: Rp {int(st.session_state.newbal):,}")
        st.write("Tutup pop-up ini untuk kembali ke awal.")
        if st.button("Tutup"):
            reset_redeem_state() 
            st.session_state.show_success = False
            st.rerun()

def page_daftar_seller():
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
                # Simpan ke database
                with engine.begin() as conn:
                    conn.execute(
                        text("""
                            INSERT INTO seller (nama_seller, no_hp)
                            VALUES (:nama, :no_hp)
                        """),
                        {"nama": nama.strip(), "no_hp": nohp.strip()}
                    )

                st.success("✅ Pendaftaran berhasil! Data Anda telah disimpan ke database.")
                st.info("Admin akan menghubungi Anda untuk proses verifikasi.")

            except Exception as e:
                st.error("❌ Gagal menyimpan data ke database.")
                st.code(str(e))

# ---------------------------
# Page: Aktivasi Voucher (admin) — inline edit (unchanged except access)
# ---------------------------
def page_daftar_voucher():
    st.header("Aktivasi Voucher (Admin)")

    st.session_state.setdefault("vouchers_page_idx", 0)
    st.session_state.setdefault("vouchers_per_page", 10)
    st.session_state.setdefault("search", "")
    st.session_state.setdefault("reset_search", False)

    if st.session_state.reset_search:
        st.session_state.search = ""
        st.session_state.reset_search = False

    if "voucher_update_success" in st.session_state:
        st.success(st.session_state["voucher_update_success"])
        del st.session_state["voucher_update_success"]

    st.write("Cari kode, filter status. Jika kode ditemukan, langsung bisa edit di bawah.")

    # ===== Filter & pagination =====
    col1, col2, col3 = st.columns([3, 2, 1])
    with col1:
        search = st.text_input("Cari kode (partial)", key="search")
    with col2:
        filter_status = st.selectbox("Filter status", ["semua", "aktif", "habis", "seller"])
    with col3:
        per_page = st.number_input(
            "Per halaman", min_value=5, max_value=200,
            value=st.session_state.vouchers_per_page, step=5
        )
        st.session_state.vouchers_per_page = per_page

    offset = st.session_state.vouchers_page_idx * st.session_state.vouchers_per_page

    # ===== Ambil data voucher =====
    if filter_status == "seller":
        df = list_vouchers(limit=5000)  
        df = df[df["seller"].notna() & (df["seller"] != "")]
    else:
        df = list_vouchers(
            filter_status if filter_status != "semua" else None,
            search if search else None,
            limit=st.session_state.vouchers_per_page,
            offset=offset
        )

    if df.empty:
        st.info("Tidak ada voucher sesuai filter/pencarian.")
        return
    df_display = df.copy()
    df_display["initial_value"] = df_display["initial_value"].apply(lambda x: f"Rp {int(x):,}")
    df_display["balance"] = df_display["balance"].apply(lambda x: f"Rp {int(x):,}")
    df_display["created_at"] = pd.to_datetime(df_display["created_at"]).dt.strftime("%Y-%m-%d")
        
    # Cek aman untuk tanggal_penjualan
    if "tanggal_penjualan" in df_display.columns:
        df_display["tanggal_penjualan"] = (
            pd.to_datetime(df_display["tanggal_penjualan"], errors="coerce")
            .dt.strftime("%Y-%m-%d")
            .fillna("-")
        )
    else:
        df_display["tanggal_penjualan"] = "-"
        
    st.dataframe(
        df_display[
            [
                "code", "nama", "no_hp", "status",
                "initial_value", "balance", "created_at",
                "seller", "tanggal_penjualan"
            ]
        ],
        use_container_width=True
    )
    
    # ===== Form edit voucher jika kode dicari ditemukan =====
    matched_row = df[df["code"] == search.strip().upper()]
    if not matched_row.empty:
        v = matched_row.iloc[0]
        st.markdown("---")
        st.subheader(f"Edit Voucher: {v['code']}")

        seller_data = v.get("seller")
        
        if not seller_data or str(seller_data).strip() == "":
            st.warning("⚠ Voucher ini belum memiliki seller.")
            st.info("Silakan tetapkan seller terlebih dahulu di menu seller.")
        else:
            with st.form(key=f"edit_form_{v['code']}"):
                nama_in = st.text_input("Nama pemilik", value=v["nama"] or "")
                nohp_in = st.text_input("No HP pemilik", value=v["no_hp"] or "")
                status_in = st.selectbox(
                    "Status", ["inactive", "active"],
                    index=0 if (v["status"] or "inactive") != "active" else 1
                )
        
                submit = st.form_submit_button("Simpan")
                if submit:
                    if status_in == "active" and (not nama_in.strip() or not nohp_in.strip()):
                        st.error("Untuk mengaktifkan voucher, isi Nama dan No HP terlebih dahulu.")
                    else:
                        tanggal_aktivasi = datetime.now().strftime("%Y-%m-%d")
                        
                        ok = update_voucher_detail(
                            v["code"],
                            nama_in.strip() or None,
                            nohp_in.strip() or None,
                            status_in,
                            tanggal_aktivasi
                        )
                        if ok:
                            st.session_state["voucher_update_success"] = f"Voucher {v['code']} berhasil diperbarui ✅"
                            st.session_state.reset_search = True
                            st.session_state.vouchers_page_idx = 0
                            st.rerun()
        
                    # Duplicate button removed — keep only one submit to avoid confusion

    st.markdown("---")
    st.download_button(
        "Download CSV (tabel saat ini)",
        data=df_to_csv_bytes(df),
        file_name="vouchers_page.csv",
        mime="text/csv"
    )


# ---------------------------
# Page: Histori Transaksi (admin)
# ---------------------------
def page_histori():
    st.header("Histori Transaksi (Admin)")
    df_tx = list_transactions(limit=5000)
    if df_tx.empty:
        st.info("Belum ada transaksi")
        return

    search_code = st.text_input("Cari kode voucher untuk detail histori")
    if search_code:
        df_filtered = df_tx[df_tx["code"].str.contains(search_code.strip().upper(), case=False)]
        if df_filtered.empty:
            st.warning(f"Tidak ada transaksi untuk voucher {search_code}")
        else:
            st.subheader(f"Detail Voucher: {search_code.strip().upper()}")
            total_transaksi = len(df_filtered)
            total_nominal = df_filtered["used_amount"].sum()
            st.write(f"- Jumlah transaksi: {total_transaksi}")
            st.write(f"- Total nominal terpakai: Rp {total_nominal:,}")
            df_display = df_filtered.copy()
            df_display["tanggal_transaksi"] = pd.to_datetime(df_display["tanggal_transaksi"])
            df_display = df_display.rename(columns={"id":"ID","code":"Kode","used_amount":"Jumlah","tanggal_transaksi":"Waktu","branch":"Cabang","items":"Menu"})
            st.dataframe(df_display[["ID","Kode","Waktu","Jumlah","Cabang","Menu"]], use_container_width=True)
            st.download_button(f"Download CSV {search_code.strip().upper()}", data=df_to_csv_bytes(df_display), file_name=f"transactions_{search_code.strip().upper()}.csv", mime="text/csv")
    else:
        df_tx["tanggal_transaksi"] = pd.to_datetime(df_tx["tanggal_transaksi"]).dt.date
        df_tx = df_tx.rename(columns={"id":"ID","code":"Kode","used_amount":"Jumlah","tanggal_transaksi":"Waktu","branch":"Cabang","items":"Menu"})
        st.dataframe(df_tx, use_container_width=True)
        st.download_button("Download CSV Transaksi", data=df_to_csv_bytes(df_tx), file_name="transactions.csv", mime="text/csv")


# ---------------------------
# Page: Laporan Warung (admin)
# ---------------------------
def page_laporan_global():
    st.header("Laporan Warung (Admin)")

    # Tabs untuk membagi laporan
    tab_voucher, tab_transaksi, tab_seller = st.tabs(["Voucher", "Transaksi", "Seller"])

    df_vouchers = list_vouchers(limit=5000)
    df_tx = list_transactions(limit=100000)

    if "tanggal_transaksi" in df_tx.columns:
        df_tx["tanggal_transaksi"] = pd.to_datetime(df_tx["tanggal_transaksi"])

    # # ===== TAB Voucher =====
    with tab_voucher:
        st.subheader("📊 Laporan Voucher")
    
        vouchers = pd.read_sql("SELECT * FROM vouchers", engine)
        transactions = pd.read_sql("SELECT * FROM transactions", engine)
    
        # Ensure numeric & date columns
        vouchers["initial_value"] = vouchers["initial_value"].fillna(0).astype(float)
        vouchers["balance"] = vouchers["balance"].fillna(0).astype(float)
        vouchers["tanggal_penjualan"] = pd.to_datetime(vouchers["tanggal_penjualan"], errors="coerce")
        transactions["tanggal_transaksi"] = pd.to_datetime(transactions.get("tanggal_transaksi"), errors="coerce")
    
        # Perhitungan
        vouchers["used_value"] = vouchers["initial_value"] - vouchers["balance"]
        summary = {
            "total_voucher_dijual": len(vouchers),
            "total_voucher_terpakai": len(transactions["code"].unique()),
            "total_saldo_belum_terpakai": vouchers["balance"].sum(),
            "total_saldo_sudah_terpakai": vouchers["used_value"].sum(),
            "total_voucher_aktif": len(vouchers[vouchers["status"] == "active"]),
            "total_voucher_inaktif": len(vouchers[vouchers["status"] != "active"]),
        }
    
        # ✅ Summary Cards
        col1, col2, col3 = st.columns(3)
        col1.metric("🎫 Total Voucher Dijual", summary["total_voucher_dijual"])
        col2.metric("✅ Total Voucher Terpakai", summary["total_voucher_terpakai"])
        col3.metric("💰 Saldo Belum Terpakai", f"Rp {summary['total_saldo_belum_terpakai']:,.0f}")
    
        col4, col5, col6 = st.columns(3)
        col4.metric("💸 Saldo Sudah Terpakai", f"Rp {summary['total_saldo_sudah_terpakai']:,.0f}")
        col5.metric("📌 Voucher Aktif", summary["total_voucher_aktif"])
        col6.metric("🚫 Voucher Inaktif", summary["total_voucher_inaktif"])
    
        st.markdown("---")
    
        # ✅ Grafik Penukaran Voucher per Hari
        if not transactions.empty:
            redeem_daily = transactions.groupby(transactions["tanggal_transaksi"].dt.date).size()
            st.subheader("📈 Penukaran Voucher per Hari")
            st.line_chart(redeem_daily)
        else:
            st.info("Belum ada transaksi penukaran voucher ✅")
    
        st.markdown("---")
    
        # ✅ Grafik Total Nilai Transaksi
        if not transactions.empty:
            total_transaksi = transactions.groupby(transactions["tanggal_transaksi"].dt.date)["used_amount"].sum()
            st.subheader("📊 Total Nilai Transaksi per Hari")
            st.bar_chart(total_transaksi)
        
        st.markdown("---")
    
        # ✅ Pie Chart Status Voucher Breakdown
        st.subheader("🧩 Status Voucher")
        status_count = vouchers["status"].value_counts()
        fig, ax = plt.subplots()
        ax.pie(status_count, labels=status_count.index, autopct="%1.1f%%")
        ax.axis("equal")
        st.pyplot(fig)
    
        st.markdown("---")

    
        # ✅ Export CSV
        csv = vouchers.to_csv(index=False).encode("utf-8")
        st.download_button(
            label="📥 Download Laporan Voucher (CSV)",
            data=csv,
            file_name="voucher_report.csv",
            mime="text/csv",
        )

    # ===== TAB Transaksi =====
    with tab_transaksi:
        st.subheader("📊 Ringkasan Transaksi")
    
        # 🔹 Filter tanggal
        min_date = pd.to_datetime(df_tx["tanggal_transaksi"]).min()
        max_date = pd.to_datetime(df_tx["tanggal_transaksi"]).max()
        date_filter = st.date_input("Filter tanggal", [min_date, max_date])
    
        df_tx["tanggal_transaksi"] = pd.to_datetime(df_tx["tanggal_transaksi"])
        df_filtered = df_tx[
            (df_tx["tanggal_transaksi"] >= pd.to_datetime(date_filter[0])) &
            (df_tx["tanggal_transaksi"] <= pd.to_datetime(date_filter[1]))
        ]
    
        # 🔹 Filter cabang
        if "branch" in df_filtered.columns:
            cabang_list = ["Semua"] + sorted(df_filtered["branch"].dropna().unique().tolist())
            selected_cabang = st.selectbox("Filter Cabang", cabang_list)
    
            if selected_cabang != "Semua":
                df_filtered = df_filtered[df_filtered["branch"] == selected_cabang]
    
        # 🔹 Summary
        total_tx = len(df_filtered)
        total_tx_nominal = df_filtered["used_amount"].sum()
        avg_tx = df_filtered["used_amount"].mean() if total_tx > 0 else 0
    
        st.write(f"- Total transaksi: {total_tx:,}")
        st.write(f"- Total nominal digunakan: Rp {int(total_tx_nominal):,}")
        st.write(f"- Rata-rata nominal transaksi: Rp {int(avg_tx):,}")
    
        # =============================== #
        # 📌 Chart Transaksi per Cabang
        # =============================== #
        if "branch" in df_filtered.columns:
            st.subheader("🏪 Total Transaksi per Cabang")
            tx_count = df_filtered.groupby("branch")["code"].count().reset_index()
            tx_count.columns = ["Cabang", "Jumlah Transaksi"]
            st.bar_chart(tx_count, x="Cabang", y="Jumlah Transaksi")
    
            st.subheader("💰 Total Nominal per Cabang")
            tx_sum = df_filtered.groupby("branch")["used_amount"].sum().reset_index()
            tx_sum.columns = ["Cabang", "Total Nominal"]
            st.bar_chart(tx_sum, x="Cabang", y="Total Nominal")
    
        # =============================== #
        # 🏆 Top 5 Voucher Berdasarkan Jumlah Transaksi
        # =============================== #
        if "code" in df_filtered.columns:
            st.subheader("🏆 Top 5 Voucher Paling Sering Digunakan")
            top_voucher = (
                df_filtered.groupby("code")["code"].count()
                .sort_values(ascending=False)
                .head(5)
                .reset_index(name="Jumlah Transaksi")
            )
            st.table(top_voucher)
            st.bar_chart(top_voucher, x="code", y="Jumlah Transaksi")
       
        # =============================== #
        # 🍽 Top 5 Menu Terlaris
        # =============================== #
        
        st.subheader("🍽 Top 5 Menu Terlaris")
        
        try:
            # Query tergantung cabang yang dipilih
            if "selected_cabang" not in locals():
                st.warning("Silakan pilih cabang terlebih dahulu untuk melihat menu terlaris.")
            else:
                if selected_cabang == "Semua":
                    query_menu = """
                        SELECT nama_item,
                        COALESCE(terjual_twsari,0) + COALESCE(terjual_sedati,0) AS "Terjual"
                        FROM menu_items
                    """
                else:
                    column = "terjual_twsari" if selected_cabang == "Tawangsari" else "terjual_sedati"
                    query_menu = f"""
                        SELECT nama_item,
                        COALESCE({column},0) AS "Terjual"
                        FROM menu_items
                    """
        
                df_menu = pd.read_sql(query_menu, engine)
                df_menu = df_menu.rename(columns={"nama_item": "Menu"})
        
                if "Terjual" not in df_menu.columns:
                    st.info("Kolom 'Terjual' tidak ditemukan di hasil query.")
                elif df_menu.empty:
                    st.info("Belum ada data penjualan menu.")
                else:
                    df_menu = df_menu.sort_values("Terjual", ascending=False).head(5)
                    chart_menu = alt.Chart(df_menu).mark_bar().encode(
                        x=alt.X("Menu:N", title="Menu"),
                        y=alt.Y("Terjual:Q", title="Jumlah Terjual"),
                        tooltip=["Menu", "Terjual"]
                    )
                    st.altair_chart(chart_menu, use_container_width=True)
        
        except Exception as e:
            st.error(f"Gagal memuat data menu terlaris: {e}")


    # ===== TAB Seller =====
    with tab_seller:
        st.subheader("📊 Analisis Seller")
    
        # Pastikan kolom seller tersedia
        if "seller" not in df_vouchers.columns:
            st.warning("Kolom 'seller' tidak tersedia di tabel vouchers.")
            st.stop()
    
        df_vouchers["seller"] = df_vouchers["seller"].fillna("-")
    
        # ========================= #
        # Filter Tanggal
        # ========================= #
        min_date = pd.to_datetime(df_vouchers["created_at"]).min()
        max_date = pd.to_datetime(df_vouchers["created_at"]).max()
    
        date_filter = st.date_input(
            "📅 Filter Tanggal Voucher Dibuat",
            [min_date, max_date],
            key="seller_date_filter"
        )
    
        df_filtered_seller = df_vouchers.copy()
        df_filtered_seller["created_at"] = pd.to_datetime(df_filtered_seller["created_at"])
        df_filtered_seller = df_filtered_seller[
            (df_filtered_seller["created_at"] >= pd.to_datetime(date_filter[0])) &
            (df_filtered_seller["created_at"] <= pd.to_datetime(date_filter[1]))
        ]
    
        # ========================= #
        # Filter Cabang
        # ========================= #
        # (This assumes vouchers table might have 'branch' column; if not, skip)
        if "branch" in df_filtered_seller.columns:
            cabang_list = ["Semua"] + sorted(df_filtered_seller["branch"].dropna().unique().tolist())
            selected_branch_seller = st.selectbox(
                "🏬 Filter Cabang Seller",
                cabang_list,
                key="seller_branch_filter"
            )
    
            if selected_branch_seller != "Semua":
                df_filtered_seller = df_filtered_seller[
                    df_filtered_seller["branch"] == selected_branch_seller
                ]
    
        # ========================= #
        # Card Metrics
        # ========================= #
        
        # Hanya voucher yang benar-benar dibawa seller
        df_seller_only = df_filtered_seller[df_filtered_seller["seller"] != "-"]
        
        # ✅ Total Seller (count unique seller)
        total_seller = df_seller_only["seller"].nunique()
        
        # ✅ Total Voucher Dibawa Seller (count voucher)
        total_voucher = len(df_seller_only)
        
        st.success(f"👤 Total Seller: **{total_seller}**")
        st.info(f"🎟️ Total Voucher Dibawa Seller: **{total_voucher:,}**")
        
        # Filter voucher aktif oleh seller
        df_active = df_seller_only[df_seller_only["status"] == "active"]
    
        # ========================= #
        # Voucher Aktif Per Seller
        # ========================= #
        st.subheader("✅ Voucher Aktif per Seller")
    
        df_active = df_filtered_seller[df_filtered_seller["status"] == "active"]
    
        if not df_active.empty:
            seller_active = (
                df_active.groupby("seller")
                .size()
                .reset_index(name="Voucher Aktif")
                .sort_values(by="Voucher Aktif", ascending=False)
            )
    
            st.table(seller_active.rename(columns={"seller": "Seller"}))
            st.bar_chart(seller_active, x="seller", y="Voucher Aktif")
    
        else:
            st.info("Tidak ada voucher aktif.")

        # ========================= #
        # 🎟️ Total Voucher Dibawa per Seller
        # ========================= #
        st.subheader("🎟️ Total Voucher Dibawa per Seller")
        
        if not df_seller_only.empty:
            voucher_by_seller = (
                df_seller_only.groupby("seller")
                .size()
                .reset_index(name="Total Voucher Dibawa")
                .sort_values(by="Total Voucher Dibawa", ascending=False)
            )
        
            st.table(voucher_by_seller.rename(columns={"seller": "Seller"}))
        
            st.bar_chart(voucher_by_seller, x="seller", y="Total Voucher Dibawa")
        
        else:
            st.info("Belum ada seller yang membawa voucher.")


# ---------------------------
# Page: Seller Activation (seller-only)
# ---------------------------
def page_seller_activation():
    st.header("Aktivasi Voucher (Seller)")

    st.info("Masukkan Nama Seller (sesuai dengan data seller pada voucher), Nama Pembeli, No HP, dan Kode Voucher.\nJika voucher belum diassign seller oleh admin → aktivasi ditolak.")

    with st.form(key="seller_activation_form"):
        kode = st.text_input("Kode Voucher").strip().upper()
        seller_name_input = st.text_input("Nama Seller (isi sesuai yang tercantum pada voucher)")
        buyer_name_input = st.text_input("Nama Pembeli")
        buyer_phone_input = st.text_input("No HP Pembeli")
        tanggal_aktivasi = st.date_input("Tanggal Aktivasi", value=pd.to_datetime("today"), key="assign_tanggal_aktivasi")
        submit = st.form_submit_button("Simpan dan Aktifkan")
        reset = st.form_submit_button("Kembali")

    if submit:
        if not kode:
            st.error("Masukkan kode voucher.")
            return

        if not seller_name_input:
            st.error("Masukkan nama seller (sesuai yang terdaftar pada voucher).")
            return

        # attempt activation
        ok, msg = seller_activate_voucher(kode, seller_name_input, buyer_name_input, buyer_phone_input)
        if ok:
            st.success(msg)
        else:
            st.error(msg)
    
    st.markdown("---")
    st.info("Note: Setelah berhasil diaktivasi oleh Seller, data akan dikunci (seller tidak bisa mengedit lagi). Jika perlu koreksi, minta admin untuk ubah data.")

    # Seller should not see full voucher table — but show a quick lookup area
    st.subheader("Cek Status Voucher (Lookup cepat)")
    kode_lookup = st.text_input("Masukkan kode voucher untuk cek status (opsional)").strip().upper()
    if kode_lookup:
        v = find_voucher(kode_lookup)
        if not v:
            st.error("Voucher tidak ditemukan.")
        else:
            code, initial_value, balance, created_at, nama, no_hp, status, seller_db, tanggal_penjualan = v
            st.write(f"- Kode: {code}")
            st.write(f"- Seller (di DB): {seller_db or '-'}")
            st.write(f"- Status: {status or 'inactive'}")
            st.write(f"- Nama pembeli: {nama or '-'}")
            st.write(f"- No HP pembeli: {no_hp or '-'}")
            st.write(f"- Tgl penjualan: {tanggal_penjualan or '-'}")


# ---------------------------
# Page: Seller (admin-only assign seller) — keep an admin-only page to assign seller to voucher
# ---------------------------
def page_seller_admin_assign():
    tab_kepemilikan, tab_acc = st.tabs(["Kepemilikan Voucher", "Penerimaan Seller"])

    with tab_kepemilikan:
        st.subheader("Seller (Admin) — assign seller ke voucher")
        if "found_voucher" not in st.session_state:
            st.session_state["found_voucher"] = None
        if "search_input" not in st.session_state:
            st.session_state["search_input"] = ""
        if "clear_search" not in st.session_state:
            st.session_state["clear_search"] = False
    
        if st.session_state.get("clear_search"):
            st.session_state["search_input"] = ""
            st.session_state["clear_search"] = False
    
        search_code = st.text_input("Masukkan Kode Voucher", key="admin_assign_search")
    
        if st.button("Cari", key="admin_assign_search_btn"):
            if search_code:
                try:
                    with engine.connect() as conn:
                        result = conn.execute(text("""
                            SELECT code, initial_value, balance, seller, nama, no_hp, status, tanggal_penjualan
                            FROM vouchers
                            WHERE code = :code
                        """), {"code": search_code.strip().upper()}).fetchone()
    
                    if result:
                        st.session_state["found_voucher"] = result
                    else:
                        st.session_state["found_voucher"] = None
                        st.error("Voucher tidak ditemukan ❌")
    
                except Exception as e:
                    st.session_state["found_voucher"] = None
                    st.error("Terjadi kesalahan saat mencari voucher ⚠️")
                    st.code(str(e))
        
        if st.session_state.get("found_voucher"):
            code, initial_value, balance, seller, nama, no_hp, status, tanggal_penjualan = st.session_state["found_voucher"]
    
            st.success("Voucher ditemukan ✅")
            st.write("### Detail Voucher")
            st.table({
                "Kode Voucher": [code],
                "Initial Value": [initial_value],
                "Balance": [balance],
                "Seller (DB)": [seller if seller else "-"],
                "Status": [status if status else "-"]
            })
    
            seller_input_admin = st.text_input("Tetapkan Nama Seller untuk voucher ini:", value=seller if seller else "", key="assign_seller_input")
            tanggal_input = st.date_input("Tanggal Penjualan (opsional)", value=pd.to_datetime("today"), key="assign_tanggal_input")
            
            if st.button("Simpan Assignment", key="assign_seller_btn"):
                if seller_input_admin:
                    try:
                        with engine.begin() as conn2:
                            conn2.execute(text("""
                                UPDATE vouchers 
                                SET seller = :seller,
                                    tanggal_penjualan = :tanggal_penjualan
                                WHERE code = :code
                            """), {
                                "seller": seller_input_admin.strip(), 
                                "tanggal_penjualan": tanggal_input,  
                                "code": code
                            })
            
                        st.success("Seller dan Tanggal Penjualan berhasil disimpan ✅")
            
                        st.session_state["found_voucher"] = None
                        st.session_state["clear_search"] = True
            
                        st.rerun()
            
                    except Exception as e:
                        st.error("Gagal menyimpan seller dan tanggal ❌")
                        st.code(str(e))
                else:
                    st.warning("Nama Seller tidak boleh kosong!")
    
        df = list_vouchers(limit=5000)  
        df_display = df.copy()
        df_display["initial_value"] = df_display["initial_value"].apply(lambda x: f"Rp {int(x):,}")
        df_display["balance"] = df_display["balance"].apply(lambda x: f"Rp {int(x):,}")
        df_display["created_at"] = pd.to_datetime(df_display["created_at"]).dt.strftime("%Y-%m-%d")
            
        # Cek aman untuk tanggal_penjualan
        if "tanggal_penjualan" in df_display.columns:
            df_display["tanggal_penjualan"] = (
                pd.to_datetime(df_display["tanggal_penjualan"], errors="coerce")
                .dt.strftime("%Y-%m-%d")
                .fillna("-")
            )
        else:
            df_display["tanggal_penjualan"] = "-"
            
        st.dataframe(
            df_display[
                [
                    "code",
                    "initial_value",
                    "seller", "tanggal_penjualan"
                ]
            ],
            use_container_width=True
        )

    with tab_acc:
        st.header("🧾 Daftar Calon Seller")
        st.write("Berikut adalah daftar seller yang mendaftar. Klik 'Accept' untuk menyetujui pendaftaran.")
    
        try:
            # Ambil data seller dari database
            with engine.connect() as conn:
                df_seller = pd.read_sql("SELECT * FROM seller ORDER BY nama ASC", conn)
    
            if df_seller.empty:
                st.info("Belum ada data seller yang mendaftar.")
                return
    
            # Pastikan kolom ada
            if "status" not in df_seller.columns:
                st.warning("Kolom 'status' tidak ditemukan pada tabel seller.")
                return
    
            # Tampilkan tabel seller
            for idx, row in df_seller.iterrows():
                col1, col2, col3 = st.columns([3, 3, 2])
                with col1:
                    st.write(f"**Nama:** {row['nama']}")
                    st.write(f"No HP: {row['no_hp']}")
                with col2:
                    st.write(f"Status: {row['status'] or '-'}")
                with col3:
                    if st.button("✅ Accept", key=f"accept_{row['nama']}_{idx}"):
                        try:
                            with engine.begin() as conn2:
                                conn2.execute(
                                    text("UPDATE seller SET status = 'Accepted' WHERE nama = :nama AND no_hp = :no_hp"),
                                    {"nama": row["nama"], "no_hp": row["no_hp"]}
                                )
                            st.success(f"Seller {row['nama']} diterima ✅")
                            st.rerun()
                        except Exception as e:
                            st.error(f"Gagal update status seller: {e}")
    
        except Exception as e:
            st.error("❌ Gagal mengambil data seller dari database.")
            st.code(str(e))
        
# ---------------------------
# Router
# ---------------------------
if page == "Penukaran Voucher" or page == "Redeem Voucher":
    page_redeem()

elif page == "Daftar Sebagai Seller":
    page_daftar_seller()

elif page == "Aktivasi Voucher":
    if not st.session_state.admin_logged_in:
        st.error("Hanya admin yang dapat mengakses halaman ini.")
    else:
        page_daftar_voucher()

elif page == "Histori Transaksi":
    if not st.session_state.admin_logged_in:
        st.error("Hanya admin yang dapat mengakses histori transaksi.")
    else:
        page_histori()

elif page == "Seller":
    if not st.session_state.admin_logged_in:
        st.error("Hanya admin yang dapat mengakses halaman Seller.")
    else:
        page_seller_admin_assign()

elif page == "Laporan Warung":
    if not st.session_state.admin_logged_in:
        st.error("Hanya admin yang dapat mengakses laporan.")
    else:
        page_laporan_global()

elif page == "Aktivasi Voucher Seller":
    if not st.session_state.seller_logged_in:
        st.error("Hanya seller yang dapat mengakses halaman Seller Activation.")
    else:
        page_seller_activation()

else:
    st.info("Halaman tidak ditemukan.")








