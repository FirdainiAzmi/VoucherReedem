# streamlit_app.py — Full Final All-in-One

import streamlit as st
import pandas as pd
from datetime import datetime
from sqlalchemy import create_engine, text
from io import BytesIO
import altair as alt

# --------------------
# Config & DB connect
# --------------------
DB_URL = st.secrets["DB_URL"]
ADMIN_PASSWORD = st.secrets.get("ADMIN_PASSWORD", "admin123")
engine = create_engine(DB_URL, future=True)

# --------------------
# Database helpers
# --------------------
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
                    used_at TIMESTAMP NOT NULL,
                    branch TEXT,
                    items TEXT
                )
            """))
            # Tambahan kolom admin & seller
            conn.execute(text("ALTER TABLE vouchers ADD COLUMN IF NOT EXISTS nama TEXT"))
            conn.execute(text("ALTER TABLE vouchers ADD COLUMN IF NOT EXISTS no_hp TEXT"))
            conn.execute(text("ALTER TABLE vouchers ADD COLUMN IF NOT EXISTS status TEXT"))
            conn.execute(text("ALTER TABLE vouchers ADD COLUMN IF NOT EXISTS seller TEXT"))
            conn.execute(text("UPDATE vouchers SET status = 'inactive' WHERE status IS NULL"))
    except Exception as e:
        st.error(f"Gagal inisialisasi database: {e}")
        st.stop()

def find_voucher(code):
    try:
        with engine.connect() as conn:
            row = conn.execute(text("""
                SELECT code, initial_value, balance, created_at, nama, no_hp, status, seller
                FROM vouchers WHERE code = :c
            """), {"c": code}).fetchone()
        return row
    except Exception as e:
        st.error(f"DB error saat cari voucher: {e}")
        return None

def update_voucher_detail(code, nama, no_hp, status, seller=None):
    try:
        with engine.begin() as conn:
            conn.execute(text("""
                UPDATE vouchers
                SET nama = :nama,
                    no_hp = :no_hp,
                    status = :status,
                    seller = :seller
                WHERE code = :code
            """), {"nama": nama, "no_hp": no_hp, "status": status, "seller": seller, "code": code})
        return True
    except Exception as e:
        st.error(f"Gagal update voucher: {e}")
        return False

def atomic_redeem(code, amount, branch, items):
    try:
        with engine.begin() as conn:
            r = conn.execute(text("SELECT balance FROM vouchers WHERE code = :c FOR UPDATE"), {"c": code}).fetchone()
            if not r:
                return False, "Voucher tidak ditemukan.", None
            balance = r[0]
            if balance < amount:
                return False, f"Saldo tidak cukup (sisa: {balance}).", balance
            conn.execute(text("UPDATE vouchers SET balance = balance - :amt WHERE code = :c"), {"amt": amount, "c": code})
            conn.execute(text("""
                INSERT INTO transactions (code, used_amount, used_at, branch, items)
                VALUES (:c, :amt, :now, :branch, :items)
            """), {"c": code, "amt": amount, "now": datetime.utcnow(), "branch": branch, "items": items})
            return True, "Redeem berhasil.", balance - amount
    except Exception as e:
        return False, f"DB error saat redeem: {e}", None

def list_vouchers(filter_status=None, search=None, limit=5000, offset=0):
    q = "SELECT code, initial_value, balance, created_at, nama, no_hp, status, seller FROM vouchers"
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

def list_transactions(limit=5000):
    q = "SELECT * FROM transactions ORDER BY used_at DESC LIMIT :limit"
    with engine.connect() as conn:
        return pd.read_sql(text(q), conn, params={"limit": limit})

def df_to_csv_bytes(df: pd.DataFrame):
    buf = BytesIO()
    df.to_csv(buf, index=False)
    buf.seek(0)
    return buf.read()

# --------------------
# Session helpers
# --------------------
def ensure_session_state():
    st.session_state.setdefault("admin_logged_in", False)
    st.session_state.setdefault("page", "Daftar Voucher" if st.session_state.get("admin_logged_in") else "Cari & Redeem")
    st.session_state.setdefault("redeem_step", 1)
    st.session_state.setdefault("entered_code", "")
    st.session_state.setdefault("voucher_row", None)
    st.session_state.setdefault("selected_branch", None)
    st.session_state.setdefault("order_items", {})
    st.session_state.setdefault("checkout_total", 0)
    st.session_state.setdefault("edit_code", None)
    st.session_state.setdefault("vouchers_page_idx", 0)
    st.session_state.setdefault("vouchers_per_page", 10)

def reset_redeem_state():
    for k in ["redeem_step","entered_code","voucher_row","selected_branch","order_items","checkout_total","new_balance"]:
        if k in st.session_state:
            del st.session_state[k]
    ensure_session_state()

def admin_login(password):
    return password == ADMIN_PASSWORD

def admin_logout():
    st.session_state.admin_logged_in = False
    st.session_state.page = "Cari & Redeem"
    st.session_state.edit_code = None

# --------------------
# Init
# --------------------
init_db()
ensure_session_state()
st.set_page_config(page_title="Voucher Admin", layout="wide")
st.title("🎫 Voucher Admin")

# --------------------
# Sidebar
# --------------------
with st.sidebar:
    st.markdown("## Menu")
    if st.session_state.admin_logged_in:
        st.success("Logged in as **admin**")
        if st.button("Logout"):
            admin_logout()
            st.rerun()
        st.markdown("---")
        page_choice = st.radio("Pilih halaman", ("Cari & Redeem", "Daftar Voucher", "Histori Transaksi", "Laporan Global", "Seller"),
                               index=("Cari & Redeem", "Daftar Voucher", "Histori Transaksi", "Laporan Global", "Seller").index(
                                   st.session_state.get("page") if st.session_state.get("page") in ("Cari & Redeem", "Daftar Voucher", "Histori Transaksi", "Laporan Global", "Seller") else "Cari & Redeem"))
        st.session_state.page = page_choice
    else:
        st.markdown("### Admin Login (opsional)")
        pwd = st.text_input("Password", type="password")
        if st.button("Login sebagai admin"):
            if admin_login(pwd):
                st.session_state.admin_logged_in = True
                st.session_state.page = "Daftar Voucher"
                st.success("Login admin berhasil")
                st.rerun()
            else:
                st.error("Password salah")
        st.markdown("---")
        st.info("Tanpa login: hanya halaman Cari & Redeem (user) yang bisa diakses.")

page = st.session_state.get("page", "Cari & Redeem")
if not st.session_state.admin_logged_in:
    page = "Cari & Redeem"

# --------------------
# Page: Cari & Redeem (User)
# --------------------
def page_redeem():
    st.header("Cari & Redeem (User)")
    st.session_state.entered_code = st.text_input("Masukkan kode voucher", value=st.session_state.entered_code).strip().upper()
    if st.button("Cari"):
        code = st.session_state.entered_code
        if not code:
            st.error("Kode tidak boleh kosong")
        else:
            row = find_voucher(code)
            if not row:
                st.error("❌ Voucher tidak ditemukan.")
            else:
                st.session_state.voucher_row = row
                st.session_state.redeem_step = 2
                st.experimental_rerun()

    if st.session_state.voucher_row and st.session_state.redeem_step >= 2:
        row = st.session_state.voucher_row
        code, initial, balance, created_at, nama, no_hp, status, seller = row
        st.subheader(f"Voucher: {code}")
        st.write(f"- Nilai awal: Rp {int(initial):,}")
        st.write(f"- Sisa saldo: Rp {int(balance):,}")
        st.write(f"- Nama: {nama or '-'}")
        st.write(f"- No HP: {no_hp or '-'}")
        st.write(f"- Status: {status or 'inactive'}")

        if int(balance) <= 0:
            st.warning("Voucher sudah tidak dapat digunakan (saldo 0).")
            return

        branch_options = ["Sedati", "Tawangsari"]
        selected_branch = st.selectbox("Pilih cabang", branch_options, index=0)
        st.session_state.selected_branch = selected_branch

        if selected_branch == "Sedati":
            menu_map = {"Nasi Goreng":20000, "Ayam Goreng":25000, "Ikan Bakar":30000, "Es Teh":5000}
        else:
            menu_map = {"Nasi Goreng Spesial":25000, "Bakso Kuah":18000, "Es Jeruk":7000, "Teh Manis":3000}

        st.markdown("**Pilih menu & jumlah**")
        total = 0
        chosen = {}
        for item, price in menu_map.items():
            qty = st.number_input(f"{item} (Rp {price:,})", min_value=0, value=0, step=1, key=f"u_{item}_{code}")
            if qty > 0:
                chosen[item] = int(qty)
                total += price * int(qty)
        st.session_state.order_items = chosen
        st.session_state.checkout_total = total
        st.write(f"**Total sementara: Rp {total:,}**")

        if st.button("Cek & Bayar"):
            if total == 0:
                st.warning("Pilih minimal 1 menu")
            elif total > int(balance):
                st.error(f"Saldo tidak cukup. Total: Rp {total:,} — Saldo: Rp {int(balance):,}")
            else:
                items_str = ", ".join([f"{k} x{v}" for k,v in chosen.items()])
                ok, msg, newbal = atomic_redeem(code, total, selected_branch, items_str)
                if ok:
                    st.success("🎉 TRANSAKSI BERHASIL 🎉")
                    st.write(f"Sisa saldo sekarang: Rp {int(newbal):,}")
                    if st.button("OK"):
                        reset_redeem_state()
                        st.experimental_rerun()
                else:
                    st.error(msg)

# --------------------
# Page: Daftar Voucher (Admin)
# --------------------
def page_daftar_voucher():
    st.header("Daftar Voucher (Admin) — Tabel penuh")
    col1, col2, col3 = st.columns([3,2,1])
    with col1:
        search = st.text_input("Cari kode (partial)", value="")
    with col2:
        filter_status = st.selectbox("Filter status", ["semua","aktif","habis"])
    with col3:
        per_page = st.number_input("Per halaman", min_value=5, max_value=200, value=st.session_state.vouchers_per_page, step=5)
        st.session_state.vouchers_per_page = per_page

    offset = st.session_state.vouchers_page_idx * st.session_state.vouchers_per_page
    df = list_vouchers(filter_status if filter_status!="semua" else None, search if search else None,
                       limit=st.session_state.vouchers_per_page, offset=offset)

    if df.empty:
        st.info("Tidak ada voucher sesuai filter/pencarian.")
        return

    df_display = df.copy()
    df_display["initial_value"] = df_display["initial_value"].apply(lambda x: f"Rp {int(x):,}")
    df_display["balance"] = df_display["balance"].apply(lambda x: f"Rp {int(x):,}")
    df_display["created_at"] = pd.to_datetime(df_display["created_at"]).dt.strftime("%Y-%m-%d %H:%M:%S")
    st.dataframe(df_display[["code","nama","no_hp","status","seller","initial_value","balance","created_at"]], use_container_width=True)

    matched_row = df[df["code"] == search.strip().upper()]
    if not matched_row.empty:
        v = matched_row.iloc[0]
        st.markdown("---")
        st.subheader(f"Edit Voucher: {v['code']}")
        with st.form(key=f"edit_form_{v['code']}"):
            nama_in = st.text_input("Nama pemilik", value=v["nama"] or "")
            nohp_in = st.text_input("No HP pemilik", value=v["no_hp"] or "")
            seller_in = st.text_input("Seller", value=v["seller"] or "")
            status_in = st.selectbox("Status", ["inactive", "active"], index=0 if (v["status"] or "inactive")!="active" else 1)
            submit = st.form_submit_button("Simpan / Aktifkan")
            if submit:
                if status_in == "active" and (not nama_in.strip() or not nohp_in.strip()):
                    st.error("Untuk mengaktifkan voucher, isi Nama dan No HP terlebih dahulu.")
                else:
                    ok = update_voucher_detail(v["code"], nama_in.strip() or None, nohp_in.strip() or None, status_in, seller_in.strip() or None)
                    if ok:
                        st.success(f"Voucher {v['code']} berhasil diperbarui ✅")
                        st.experimental_rerun()

    st.markdown("---")
    st.download_button("Download CSV (tabel saat ini)", data=df_to_csv_bytes(df), file_name="vouchers_page.csv", mime="text/csv")

# --------------------
# Page: Histori Transaksi (Admin)
# --------------------
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
            df_display["used_at"] = pd.to_datetime(df_display["used_at"])
            df_display = df_display.rename(columns={"id":"ID","code":"Kode","used_amount":"Jumlah","used_at":"Waktu","branch":"Cabang","items":"Menu"})
            st.dataframe(df_display[["ID","Kode","Waktu","Jumlah","Cabang","Menu"]], use_container_width=True)
            st.download_button(f"Download CSV {search_code.strip().upper()}", data=df_to_csv_bytes(df_display), file_name=f"transactions_{search_code.strip().upper()}.csv", mime="text/csv")
    else:
        df_tx["used_at"] = pd.to_datetime(df_tx["used_at"])
        df_tx = df_tx.rename(columns={"id":"ID","code":"Kode","used_amount":"Jumlah","used_at":"Waktu","branch":"Cabang","items":"Menu"})
        st.dataframe(df_tx, use_container_width=True)
        st.download_button("Download CSV Transaksi", data=df_to_csv_bytes(df_tx), file_name="transactions.csv", mime="text/csv")

# --------------------
# Page: Laporan Global (Admin)
# --------------------
def page_laporan_global():
    st.header("Laporan Global (Admin)")
    st.subheader("Filter Periode Transaksi")
    col1, col2 = st.columns(2)
    with col1:
        start_date = st.date_input("Mulai dari", value=None)
    with col2:
        end_date = st.date_input("Sampai", value=None)

    df_vouchers = list_vouchers(limit=5000)
    df_tx = list_transactions(limit=100000)

    if start_date:
        df_tx = df_tx[df_tx["used_at"].dt.date >= start_date]
    if end_date:
        df_tx = df_tx[df_tx["used_at"].dt.date <= end_date]

    st.subheader("📊 Ringkasan Voucher")
    total_voucher = len(df_vouchers)
    total_saldo_awal = df_vouchers["initial_value"].sum()
    total_saldo_tersisa = df_vouchers["balance"].sum()
    aktif_count = df_vouchers[df_vouchers["status"]=="active"].shape[0]
    inactive_count = df_vouchers[df_vouchers["status"]!="active"].shape[0]
    avg_saldo = df_vouchers["balance"].mean() if total_voucher>0 else 0

    st.write(f"- Total voucher: {total_voucher}")
    st.write(f"- Voucher aktif: {aktif_count}")
    st.write(f"- Voucher inactive: {inactive_count}")

    st.subheader("📊 Ringkasan Transaksi")
    total_tx = len(df_tx)
    total_tx_nominal = df_tx["used_amount"].sum()
    avg_tx = df_tx["used_amount"].mean() if total_tx>0 else 0

    st.write(f"- Total transaksi: {total_tx}")
    st.write(f"- Total nominal digunakan: Rp {int(total_tx_nominal):,}")
    st.write(f"- Rata-rata nominal per transaksi: Rp {int(avg_tx):,}")

# --------------------
# Page: Seller (Admin)
# --------------------
def page_seller():
    st.header("Halaman Seller (Admin)")
    df = list_vouchers(limit=5000)
    if df.empty:
        st.info("Belum ada voucher.")
        return

    df_display = df.copy()
    df_display["initial_value"] = df_display["initial_value"].apply(lambda x: f"Rp {int(x):,}")
    df_display["balance"] = df_display["balance"].apply(lambda x: f"Rp {int(x):,}")
    df_display["created_at"] = pd.to_datetime(df_display["created_at"]).dt.strftime("%Y-%m-%d %H:%M:%S")

    st.subheader("Tabel Voucher & Seller")
    st.dataframe(df_display[["code","seller","initial_value","balance"]], use_container_width=True)

    seller_name = st.text_input("Input / Update seller untuk voucher")
    voucher_code = st.text_input("Kode voucher yang ingin diassign seller")
    if st.button("Simpan Seller"):
        if not voucher_code or not seller_name:
            st.error("Isi kode voucher dan nama seller.")
        else:
            ok = update_voucher_detail(voucher_code.strip().upper(), None, None, None, seller_name.strip())
            if ok:
                st.success(f"Voucher {voucher_code.strip().upper()} berhasil diassign ke seller {seller_name.strip()}")
                st.experimental_rerun()

    # Ringkasan penjualan per seller
    df_tx = list_transactions(limit=50000)
    seller_agg = df_tx.merge(df[["code","seller"]], on="code", how="left")
    summary = seller_agg.groupby("seller")["used_amount"].agg(["count","sum"]).reset_index().fillna("-")
    st.subheader("Ringkasan Penjualan per Seller")
    st.dataframe(summary.rename(columns={"seller":"Seller","#count":"#Transaksi","sum":"Total (Rp)"}), use_container_width=True)

# --------------------
# Router
# --------------------
if page == "Cari & Redeem":
    page_redeem()
elif page == "Daftar Voucher":
    if not st.session_state.admin_logged_in:
        st.error("Hanya admin yang dapat mengakses halaman ini.")
    else:
        page_daftar_voucher()
elif page == "Histori Transaksi":
    if not st.session_state.admin_logged_in:
        st.error("Hanya admin yang dapat mengakses histori transaksi.")
    else:
        page_histori()
elif page == "Laporan Global":
    if not st.session_state.admin_logged_in:
        st.error("Hanya admin yang dapat mengakses laporan.")
    else:
        page_laporan_global()
elif page == "Seller":
    if not st.session_state.admin_logged_in:
        st.error("Hanya admin yang dapat mengakses halaman seller.")
    else:
        page_seller()
else:
    st.info("Halaman tidak ditemukan.")
