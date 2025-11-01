# streamlit_app.py — FULL FINAL (admin login A + laporan 2)
import streamlit as st
import pandas as pd
from datetime import datetime
from sqlalchemy import create_engine, text
from io import BytesIO
import altair as alt

# --------------------
# Config & DB connect
# --------------------
# Required in Streamlit secrets:
# DB_URL = "postgresql://user:pass@host:port/dbname?sslmode=require"
# ADMIN_PASSWORD = "your_admin_password"
DB_URL = st.secrets["DB_URL"]
ADMIN_PASSWORD = st.secrets.get("ADMIN_PASSWORD", "admin123")
engine = create_engine(DB_URL, future=True)

# --------------------
# Database helpers
# --------------------
def init_db():
    """Create tables if missing and ensure columns exist (idempotent)."""
    try:
        with engine.begin() as conn:
            # base tables (if not exists)
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
            # add optional columns if not exists (Postgres syntax)
            conn.execute(text("ALTER TABLE vouchers ADD COLUMN IF NOT EXISTS nama TEXT"))
            conn.execute(text("ALTER TABLE vouchers ADD COLUMN IF NOT EXISTS no_hp TEXT"))
            conn.execute(text("ALTER TABLE vouchers ADD COLUMN IF NOT EXISTS status TEXT"))
            # ensure null statuses become 'inactive'
            conn.execute(text("UPDATE vouchers SET status = 'inactive' WHERE status IS NULL"))
    except Exception as e:
        st.error(f"Gagal inisialisasi database: {e}")
        st.stop()

def find_voucher(code):
    try:
        with engine.connect() as conn:
            r = conn.execute(text("""
                SELECT code, initial_value, balance, created_at, nama, no_hp, status
                FROM vouchers WHERE code = :c
            """), {"c": code}).fetchone()
        return r
    except Exception as e:
        st.error(f"DB error saat cari voucher: {e}")
        return None

def update_voucher_detail(code, nama, no_hp, status):
    try:
        with engine.begin() as conn:
            conn.execute(text("""
                UPDATE vouchers
                SET nama = :nama,
                    no_hp = :no_hp,
                    status = :status
                WHERE code = :code
            """), {"nama": nama, "no_hp": no_hp, "status": status, "code": code})
        return True
    except Exception as e:
        st.error(f"Gagal update voucher: {e}")
        return False

def atomic_redeem(code, amount, branch, items):
    """Reduce balance atomically and insert transaction. Returns (ok,msg,new_balance)."""
    try:
        with engine.begin() as conn:
            row = conn.execute(text("SELECT balance FROM vouchers WHERE code = :c FOR UPDATE"), {"c": code}).fetchone()
            if not row:
                return False, "Voucher tidak ditemukan.", None
            balance = row[0]
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

def list_vouchers(filter_status=None, search=None, limit=5000):
    q = "SELECT code, initial_value, balance, created_at, nama, no_hp, status FROM vouchers"
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
    q += " ORDER BY created_at DESC LIMIT :limit"
    params["limit"] = limit
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
# Session helpers & UI state
# --------------------
def ensure_session_state():
    st.session_state.setdefault("admin_logged_in", False)
    st.session_state.setdefault("page", "Cari & Redeem")  # default for non-admin
    st.session_state.setdefault("redeem_step", 1)
    st.session_state.setdefault("entered_code", "")
    st.session_state.setdefault("voucher_row", None)
    st.session_state.setdefault("selected_branch", None)
    st.session_state.setdefault("order_items", {})
    st.session_state.setdefault("checkout_total", 0)
    st.session_state.setdefault("edit_code", None)
    st.session_state.setdefault("report_filters", {})

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
# Sidebar: login + navigation (single nav for admin/user)
# --------------------
with st.sidebar:
    st.markdown("## Menu")
    if st.session_state.admin_logged_in:
        st.success("Logged in as **admin**")
        if st.button("Logout"):
            admin_logout()
            st.rerun()
        st.markdown("---")
        st.markdown("**Halaman Admin**")
        page = st.radio("Pilih halaman", ("Daftar Voucher", "Laporan Global", "Histori Transaksi"), index=["Daftar Voucher","Laporan Global","Histori Transaksi"].index(st.session_state.get("page") if st.session_state.get("page") in ["Daftar Voucher","Laporan Global","Histori Transaksi"] else "Daftar Voucher"))
        st.session_state.page = page
    else:
        st.markdown("### Admin Login (opsional, bukan untuk user)")
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
        st.info("Tanpa login: kamu hanya bisa mengakses halaman Cari & Redeem (user).")

# Determine page to render
page = st.session_state.get("page", "Cari & Redeem")
if not st.session_state.admin_logged_in:
    page = "Cari & Redeem"  # force for non-admin

# --------------------
# Page: Cari & Redeem (public) - simplified, kept for users
# --------------------
def page_redeem():
    st.header("Cari & Redeem (User)")
    # Step 1: input kode
    if st.session_state.redeem_step == 1:
        st.session_state.entered_code = st.text_input("Masukkan kode voucher", value=st.session_state.entered_code).strip().upper()
        c1, c2 = st.columns([1,1])
        with c1:
            if st.button("Submit Kode"):
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
                        st.rerun()
        with c2:
            if st.button("Reset"):
                reset_redeem_state()
                st.rerun()

    # Step 2: show voucher & choose branch/menu
    elif st.session_state.redeem_step == 2:
        row = st.session_state.voucher_row
        code, initial, balance, created_at, nama, no_hp, status = row
        st.subheader(f"Voucher: {code}")
        st.write(f"- Nilai awal: Rp {int(initial):,}")
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

        # choose branch without forcing rerun immediately
        branch_options = ["Sedati", "Tawangsari"]
        selected_branch = st.selectbox("Pilih cabang", branch_options, index=0)
        st.session_state.selected_branch = selected_branch

        # menu per branch
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

        cA, cB = st.columns([1,1])
        with cA:
            if st.button("Cek & Bayar"):
                if total == 0:
                    st.warning("Pilih minimal 1 menu")
                elif total > int(balance):
                    st.error(f"Saldo tidak cukup. Total: Rp {total:,} — Saldo: Rp {int(balance):,}")
                else:
                    st.session_state.redeem_step = 3
                    st.rerun()
        with cB:
            if st.button("Batal / Kembali"):
                reset_redeem_state()
                st.rerun()

    # Step 3: confirmation
    elif st.session_state.redeem_step == 3:
        row = st.session_state.voucher_row
        code, initial, balance, created_at, nama, no_hp, status = row
        st.header("Konfirmasi Pembayaran")
        st.write(f"- Voucher: {code}")
        st.write(f"- Cabang: {st.session_state.selected_branch}")
        st.write(f"- Sisa sebelum: Rp {int(balance):,}")
        st.write("Detail pesanan:")
        for it, q in st.session_state.order_items.items():
            if st.session_state.selected_branch == "Sedati":
                prices = {"Nasi Goreng":20000, "Ayam Goreng":25000, "Ikan Bakar":30000, "Es Teh":5000}
            else:
                prices = {"Nasi Goreng Spesial":25000, "Bakso Kuah":18000, "Es Jeruk":7000, "Teh Manis":3000}
            st.write(f"- {it} x{q} — Rp {prices[it]*q:,}")
        st.write(f"### Total: Rp {st.session_state.checkout_total:,}")

        cy, cn = st.columns([1,1])
        with cy:
            if st.button("Ya, Bayar"):
                items_str = ", ".join([f"{k} x{v}" for k,v in st.session_state.order_items.items()])
                ok, msg, newbal = atomic_redeem(code, st.session_state.checkout_total, st.session_state.selected_branch, items_str)
                if ok:
                    st.success("🎉 TRANSAKSI BERHASIL 🎉")
                    st.write(f"Sisa saldo sekarang: Rp {int(newbal):,}")
                    if st.button("OK"):
                        reset_redeem_state()
                        st.rerun()
                else:
                    st.error(msg)
                    st.session_state.redeem_step = 2
                    st.rerun()
        with cn:
            if st.button("Tidak, Kembali"):
                st.session_state.redeem_step = 2
                st.rerun()

# --------------------
# Page: Daftar Voucher (admin) - search-only + Edit page navigation
# --------------------
def page_daftar_voucher():
    st.header("Daftar Voucher (Admin)")
    st.info("Masukkan kode voucher lalu tekan Cari. Hasil akan tampil satu baris. Klik Edit/Aktifkan untuk pindah ke halaman edit.")

    search_code = st.text_input("Cari kode voucher (ex: AB12CD)").strip().upper()
    if st.button("Cari"):
        if not search_code:
            st.warning("Masukkan kode untuk mencari")
        else:
            row = find_voucher(search_code)
            if not row:
                st.error("Voucher tidak ditemukan ❌")
            else:
                code, init_val, balance, created_at, nama, no_hp, status = row
                st.subheader("Hasil Pencarian")
                df_show = pd.DataFrame([{
                    "Kode": code,
                    "Nilai Awal": f"Rp {int(init_val):,}",
                    "Sisa Saldo": f"Rp {int(balance):,}",
                    "Dibuat": created_at.strftime("%Y-%m-%d %H:%M:%S"),
                    "Nama": nama or "-",
                    "No HP": no_hp or "-",
                    "Status": status or "inactive"
                }])
                st.table(df_show)
                if st.button("Edit / Aktifkan Voucher", key=f"edit_{code}"):
                    st.session_state.edit_code = code
                    st.session_state.page = "Edit Voucher"
                    st.rerun()

    st.markdown("---")
    st.write("Atau export semua voucher:")
    df_all = list_vouchers(limit=5000)
    st.download_button("Download semua voucher (CSV)", data=df_to_csv_bytes(df_all), file_name="vouchers_all.csv", mime="text/csv")

# --------------------
# Page: Edit Voucher (admin separate page)
# --------------------
def page_edit_voucher():
    code = st.session_state.get("edit_code")
    if not code:
        st.error("Tidak ada voucher yang dipilih untuk diedit.")
        return
    row = find_voucher(code)
    if not row:
        st.error("Voucher tidak ditemukan.")
        return
    code, initial, balance, created_at, nama, no_hp, status = row
    st.header(f"Edit Voucher — {code}")
    st.write(f"- Nilai awal: Rp {int(initial):,}")
    st.write(f"- Sisa saldo: Rp {int(balance):,}")
    st.write(f"- Dibuat: {created_at}")

    with st.form(key=f"form_edit_{code}"):
        nama_in = st.text_input("Nama pemilik", value=nama or "")
        nohp_in = st.text_input("No HP pemilik", value=no_hp or "")
        status_in = st.selectbox("Status", ["inactive", "active"], index=0 if (status or "inactive")!="active" else 1)
        submit = st.form_submit_button("Simpan Perubahan")
        cancel = st.form_submit_button("Batal")
        if submit:
            if status_in == "active" and (not nama_in.strip() or not nohp_in.strip()):
                st.error("Untuk mengaktifkan voucher, isi Nama dan No HP terlebih dahulu.")
            else:
                ok = update_voucher_detail(code, nama_in.strip() or None, nohp_in.strip() or None, status_in)
                if ok:
                    st.success("Perubahan tersimpan ✅")
                    st.session_state.page = "Daftar Voucher"
                    st.session_state.edit_code = None
                    st.rerun()
        if cancel:
            st.session_state.page = "Daftar Voucher"
            st.session_state.edit_code = None
            st.rerun()

# --------------------
# Page: Histori Transaksi (admin)
# --------------------
def page_histori():
    st.header("Histori Transaksi (Admin)")
    df_tx = list_transactions(limit=5000)
    if df_tx.empty:
        st.info("Belum ada transaksi")
        return
    # format
    try:
        df_tx["used_at"] = pd.to_datetime(df_tx["used_at"])
    except Exception:
        pass
    df_tx = df_tx.rename(columns={"id":"ID","code":"Kode","used_amount":"Jumlah","used_at":"Waktu","branch":"Cabang","items":"Menu"})
    st.dataframe(df_tx, use_container_width=True)
    st.download_button("Download CSV Transaksi", data=df_to_csv_bytes(df_tx), file_name="transactions.csv", mime="text/csv")

# --------------------
# Page: Laporan Global (admin) - charts (pilihan 2)
# --------------------
def page_laporan_global():
    st.header("Laporan Global (Grafik & Leaderboard)")

    # load transactions and vouchers
    df_tx = list_transactions(limit=100000)
    df_v = list_vouchers(limit=100000)

    if df_tx.empty:
        st.info("Belum ada transaksi untuk membuat laporan")
        return

    # Total transaksi per cabang (bar chart)
    st.subheader("Transaksi per Cabang")
    tx_by_branch = df_tx.groupby("branch", dropna=False)["used_amount"].agg(["count","sum"]).reset_index().fillna("Unknown")
    tx_by_branch = tx_by_branch.sort_values("sum", ascending=False)
    if not tx_by_branch.empty:
        chart = alt.Chart(tx_by_branch).mark_bar().encode(
            x=alt.X("branch:N", title="Cabang"),
            y=alt.Y("sum:Q", title="Total Nominal Terpakai"),
            tooltip=["branch","count","sum"]
        )
        st.altair_chart(chart, use_container_width=True)
        st.table(tx_by_branch.rename(columns={"branch":"Cabang","count":"#Transaksi","sum":"Total Terpakai (Rp)"}))

    # Top vouchers by total used_amount
    st.subheader("Top Voucher — Total Penggunaan (Nominal)")
    top_voucher = df_tx.groupby("code")["used_amount"].sum().reset_index().sort_values("used_amount", ascending=False)
    if not top_voucher.empty:
        top10 = top_voucher.head(10)
        chart2 = alt.Chart(top10).mark_bar().encode(
            x=alt.X("code:N", title="Kode Voucher"),
            y=alt.Y("used_amount:Q", title="Total Terpakai"),
            tooltip=["code","used_amount"]
        )
        st.altair_chart(chart2, use_container_width=True)
        st.table(top10.rename(columns={"code":"Kode","used_amount":"Total Terpakai (Rp)"}))

    # Time series: daily total
    st.subheader("Waktu — Total Harian")
    try:
        df_tx["date"] = pd.to_datetime(df_tx["used_at"]).dt.date
        daily = df_tx.groupby("date")["used_amount"].sum().reset_index()
        chart3 = alt.Chart(daily).mark_line(point=True).encode(
            x=alt.X("date:T", title="Tanggal"),
            y=alt.Y("used_amount:Q", title="Total Terpakai"),
            tooltip=["date","used_amount"]
        )
        st.altair_chart(chart3, use_container_width=True)
    except Exception:
        st.info("Tidak dapat membuat time series.")

    # Leaderboard branches by count of transactions
    st.subheader("Leaderboard Cabang (Jumlah Transaksi)")
    branch_count = df_tx.groupby("branch").size().reset_index(name="jumlah").sort_values("jumlah", ascending=False)
    st.table(branch_count.rename(columns={"branch":"Cabang","jumlah":"Jumlah Transaksi"}))

# --------------------
# Render page based on role & selection
# --------------------
if page == "Cari & Redeem":
    page_redeem()
elif page == "Daftar Voucher":
    page_daftar_voucher()
elif page == "Edit Voucher" or st.session_state.get("edit_code"):
    # keep edit page when edit_code is set
    page_edit_voucher()
elif page == "Histori Transaksi":
    page_histori()
elif page == "Laporan Global":
    page_laporan_global()
else:
    st.info("Halaman tidak ditemukan.")
