# streamlit_app.py  — Final (with full table view on Daftar Voucher)
import streamlit as st
import pandas as pd
from datetime import datetime
from sqlalchemy import create_engine, text
from io import BytesIO
import altair as alt
import math

# --------------------
# Config & DB connect
# --------------------
# Put DB_URL and ADMIN_PASSWORD in Streamlit secrets
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
            # Add optional columns if missing (Postgres supports IF NOT EXISTS)
            conn.execute(text("ALTER TABLE vouchers ADD COLUMN IF NOT EXISTS nama TEXT"))
            conn.execute(text("ALTER TABLE vouchers ADD COLUMN IF NOT EXISTS no_hp TEXT"))
            conn.execute(text("ALTER TABLE vouchers ADD COLUMN IF NOT EXISTS status TEXT"))
            # Normalize null statuses
            conn.execute(text("UPDATE vouchers SET status = 'inactive' WHERE status IS NULL"))
    except Exception as e:
        st.error(f"Gagal inisialisasi database: {e}")
        st.stop()

def find_voucher(code):
    try:
        with engine.connect() as conn:
            row = conn.execute(text("""
                SELECT code, initial_value, balance, created_at, nama, no_hp, status
                FROM vouchers WHERE code = :c
            """), {"c": code}).fetchone()
        return row
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
    st.session_state.setdefault("page", "Daftar Voucher" if st.session_state.get("admin_logged_in") else "Cari & Redeem")
    st.session_state.setdefault("redeem_step", 1)
    st.session_state.setdefault("entered_code", "")
    st.session_state.setdefault("voucher_row", None)
    st.session_state.setdefault("selected_branch", None)
    st.session_state.setdefault("order_items", {})
    st.session_state.setdefault("checkout_total", 0)
    st.session_state.setdefault("edit_code", None)
    st.session_state.setdefault("vouchers_page_idx", 0)  # pagination
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
# Sidebar: login + navigation
# --------------------
with st.sidebar:
    st.markdown("## Menu")
    if st.session_state.admin_logged_in:
        st.success("Logged in as **admin**")
        if st.button("Logout"):
            admin_logout()
            st.experimental_rerun()
        st.markdown("---")
        page_choice = st.radio("Pilih halaman", ("Daftar Voucher", "Laporan Global", "Histori Transaksi"),
                               index=("Daftar Voucher","Laporan Global","Histori Transaksi").index(st.session_state.get("page") if st.session_state.get("page") in ("Daftar Voucher","Laporan Global","Histori Transaksi") else "Daftar Voucher"))
        st.session_state.page = page_choice
    else:
        st.markdown("### Admin Login (opsional)")
        pwd = st.text_input("Password", type="password")
        if st.button("Login sebagai admin"):
            if admin_login(pwd):
                st.session_state.admin_logged_in = True
                st.session_state.page = "Daftar Voucher"
                st.success("Login admin berhasil")
                st.experimental_rerun()
            else:
                st.error("Password salah")
        st.markdown("---")
        st.info("Tanpa login: kamu hanya bisa mengakses halaman Cari & Redeem (user).")

# Force page if not admin
page = st.session_state.get("page", "Cari & Redeem")
if not st.session_state.admin_logged_in:
    page = "Cari & Redeem"

# --------------------
# Page: Cari & Redeem (public)
# --------------------
def page_redeem():
    st.header("Cari & Redeem (User)")
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
                        st.experimental_rerun()
        with c2:
            if st.button("Reset"):
                reset_redeem_state()
                st.experimental_rerun()

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
                st.experimental_rerun()
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

        cA, cB = st.columns([1,1])
        with cA:
            if st.button("Cek & Bayar"):
                if total == 0:
                    st.warning("Pilih minimal 1 menu")
                elif total > int(balance):
                    st.error(f"Saldo tidak cukup. Total: Rp {total:,} — Saldo: Rp {int(balance):,}")
                else:
                    st.session_state.redeem_step = 3
                    st.experimental_rerun()
        with cB:
            if st.button("Batal / Kembali"):
                reset_redeem_state()
                st.experimental_rerun()

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
                        st.experimental_rerun()
                else:
                    st.error(msg)
                    st.session_state.redeem_step = 2
                    st.experimental_rerun()
        with cn:
            if st.button("Tidak, Kembali"):
                st.session_state.redeem_step = 2
                st.experimental_rerun()

# --------------------
# Page: Daftar Voucher (admin) — FULL TABLE VISIBLE + Edit buttons per row
# --------------------
def page_daftar_voucher():
    st.header("Daftar Voucher (Admin) — Tabel penuh")
    st.write("Gunakan kotak 'Cari kode' untuk menemukan voucher. Filter akan menyaring tabel. Klik tombol **Edit / Aktifkan** di baris untuk membuka halaman edit terpisah.")

    # Controls: search + filter + pagination size
    col1, col2, col3 = st.columns([3,2,1])
    with col1:
        search = st.text_input("Cari kode (partial)", value="")
    with col2:
        filter_status = st.selectbox("Filter status", ["semua","aktif","habis"])
    with col3:
        per_page = st.number_input("Per halaman", min_value=5, max_value=200, value=st.session_state.vouchers_per_page, step=5)
        st.session_state.vouchers_per_page = per_page

    # Count & pagination
    total_count = count_vouchers(filter_status if filter_status!="semua" else None, search if search else None)
    pages = max(1, math.ceil(total_count / st.session_state.vouchers_per_page))
    idx = st.session_state.vouchers_page_idx
    colp1, colp2, colp3 = st.columns([1,1,3])
    with colp1:
        if st.button("<< Prev"):
            if st.session_state.vouchers_page_idx > 0:
                st.session_state.vouchers_page_idx -= 1
    with colp2:
        if st.button("Next >>"):
            if st.session_state.vouchers_page_idx < pages-1:
                st.session_state.vouchers_page_idx += 1
    with colp3:
        st.markdown(f"**Halaman {st.session_state.vouchers_page_idx+1} / {pages} — Total voucher: {total_count}**")

    offset = st.session_state.vouchers_page_idx * st.session_state.vouchers_per_page

    # Load page of vouchers
    df = list_vouchers(filter_status if filter_status!="semua" else None, search if search else None,
                       limit=st.session_state.vouchers_per_page, offset=offset)

    if df.empty:
        st.info("Tidak ada voucher sesuai filter/pencarian.")
        return

    # Display table (simple)
    df_display = df.copy()
    df_display["initial_value"] = df_display["initial_value"].apply(lambda x: f"Rp {int(x):,}")
    df_display["balance"] = df_display["balance"].apply(lambda x: f"Rp {int(x):,}")
    df_display["created_at"] = pd.to_datetime(df_display["created_at"]).dt.strftime("%Y-%m-%d %H:%M:%S")
    st.dataframe(df_display[["code","nama","no_hp","status","initial_value","balance","created_at"]], use_container_width=True)

    st.markdown("### Aksi per baris")
    # Render rows with Edit button (for visible page)
    for _, r in df.iterrows():
        code = r["code"]
        col_code, col_nama, col_hp, col_status, col_val, col_bal, col_action = st.columns([2,2,2,1,2,2,2])
        col_code.write(code)
        col_nama.write(r.get("nama") or "-")
        col_hp.write(r.get("no_hp") or "-")
        col_status.write(r.get("status") or "inactive")
        col_val.write(f"Rp {int(r.get('initial_value') or 0):,}")
        col_bal.write(f"Rp {int(r.get('balance') or 0):,}")
        if col_action.button("Edit / Aktifkan", key=f"edit_btn_{code}"):
            st.session_state.edit_code = code
            st.session_state.page = "Edit Voucher"
            st.experimental_rerun()

    # Export CSV of current page/filter
    st.markdown("---")
    st.download_button("Download CSV (tabel saat ini)", data=df_to_csv_bytes(df), file_name="vouchers_page.csv", mime="text/csv")

# --------------------
# Page: Edit Voucher (admin)
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
                    st.session_state.edit_code = None
                    st.session_state.page = "Daftar Voucher"
                    st.experimental_rerun()
        if cancel:
            st.session_state.edit_code = None
            st.session_state.page = "Daftar Voucher"
            st.experimental_rerun()

# --------------------
# Page: Histori Transaksi (admin)
# --------------------
def page_histori():
    st.header("Histori Transaksi (Admin)")
    df_tx = list_transactions(limit=5000)
    if df_tx.empty:
        st.info("Belum ada transaksi")
        return
    try:
        df_tx["used_at"] = pd.to_datetime(df_tx["used_at"])
    except Exception:
        pass
    df_tx = df_tx.rename(columns={"id":"ID","code":"Kode","used_amount":"Jumlah","used_at":"Waktu","branch":"Cabang","items":"Menu"})
    st.dataframe(df_tx, use_container_width=True)
    st.download_button("Download CSV Transaksi", data=df_to_csv_bytes(df_tx), file_name="transactions.csv", mime="text/csv")

# --------------------
# Page: Laporan Global (charts)
# --------------------
def page_laporan_global():
    st.header("Laporan Global (Grafik & Leaderboard)")
    df_tx = list_transactions(limit=100000)
    if df_tx.empty:
        st.info("Belum ada transaksi untuk membuat laporan")
        return
    # charts
    try:
        # branch aggregation
        branch_agg = df_tx.groupby("branch", dropna=False)["used_amount"].agg(["count","sum"]).reset_index().fillna("Unknown")
        branch_agg = branch_agg.sort_values("sum", ascending=False)
        st.subheader("Total nominal terpakai per cabang")
        chart = alt.Chart(branch_agg).mark_bar().encode(
            x=alt.X("branch:N", title="Cabang"),
            y=alt.Y("sum:Q", title="Total Nominal Terpakai"),
            tooltip=["branch","count","sum"]
        )
        st.altair_chart(chart, use_container_width=True)
        st.table(branch_agg.rename(columns={"branch":"Cabang","count":"#Transaksi","sum":"Total (Rp)"}))

        st.subheader("Top 10 voucher menurut total nominal terpakai")
        top_v = df_tx.groupby("code")["used_amount"].sum().reset_index().sort_values("used_amount", ascending=False).head(10)
        chart2 = alt.Chart(top_v).mark_bar().encode(
            x=alt.X("code:N", title="Kode Voucher"),
            y=alt.Y("used_amount:Q", title="Total Terpakai"),
            tooltip=["code","used_amount"]
        )
        st.altair_chart(chart2, use_container_width=True)
        st.table(top_v.rename(columns={"code":"Kode","used_amount":"Total (Rp)"}))

        # time series daily
        st.subheader("Time series: total harian")
        df_tx["date"] = pd.to_datetime(df_tx["used_at"]).dt.date
        daily = df_tx.groupby("date")["used_amount"].sum().reset_index()
        chart3 = alt.Chart(daily).mark_line(point=True).encode(
            x=alt.X("date:T", title="Tanggal"),
            y=alt.Y("used_amount:Q", title="Total Nominal"),
            tooltip=["date","used_amount"]
        )
        st.altair_chart(chart3, use_container_width=True)
    except Exception as e:
        st.error(f"Gagal membuat laporan: {e}")

# --------------------
# Router: render page
# --------------------
if page == "Cari & Redeem":
    page_redeem()
elif page == "Daftar Voucher":
    if not st.session_state.admin_logged_in:
        st.error("Hanya admin yang dapat mengakses halaman ini. Silakan login di sidebar.")
    else:
        page_daftar_voucher()
elif page == "Edit Voucher" or st.session_state.get("edit_code"):
    if not st.session_state.admin_logged_in:
        st.error("Hanya admin yang dapat mengedit voucher.")
    else:
        page_edit_voucher()
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
else:
    st.info("Halaman tidak ditemukan.")
