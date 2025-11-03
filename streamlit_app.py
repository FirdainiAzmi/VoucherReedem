# streamlit_app.py — Full Final

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
        page_choice = st.radio("Pilih halaman", ("Daftar Voucher", "Laporan Global", "Histori Transaksi", "Seller"),
                               index=("Daftar Voucher","Laporan Global","Histori Transaksi", "Seller").index(st.session_state.get("page") if st.session_state.get("page") in ("Daftar Voucher","Laporan Global","Histori Transaksi", "Seller") else "Daftar Voucher"))
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
                        st.rerun()
        with c2:
            if st.button("Reset"):
                reset_redeem_state()
                st.rerun()
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
                    st.rerun()
        with cB:
            if st.button("Batal / Kembali"):
                reset_redeem_state()
                st.rerun()
    elif st.session_state.redeem_step == 3:
        row = st.session_state.voucher_row
        code, initial, balance, created_at, nama, no_hp, status = row
        st.header("Konfirmasi Pembayaran")
        st.write(f"- Voucher: {code}")
        st.write(f"- Cabang: {st.session_state.selected_branch}")
        st.write(f"- Sisa sebelum: Rp {int(balance):,}")
        st.write("Detail pesanan:")
        for it, q in st.session_state.order_items.items():
            prices = {"Nasi Goreng":20000, "Ayam Goreng":25000, "Ikan Bakar":30000, "Es Teh":5000} if st.session_state.selected_branch=="Sedati" else {"Nasi Goreng Spesial":25000, "Bakso Kuah":18000, "Es Jeruk":7000, "Teh Manis":3000}
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
# Page: Daftar Voucher (admin) — inline edit
# --------------------
def page_daftar_voucher():
    st.header("Daftar Voucher (Admin) — Tabel penuh")
    st.write("Cari kode, filter status. Jika kode ditemukan, langsung bisa edit di bawah.")

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
    st.dataframe(df_display[["code","nama","no_hp","status","initial_value","balance","created_at"]], use_container_width=True)

    matched_row = df[df["code"] == search.strip().upper()]
    if not matched_row.empty:
        v = matched_row.iloc[0]
        st.markdown("---")
        st.subheader(f"Edit Voucher: {v['code']}")
        with st.form(key=f"edit_form_{v['code']}"):
            nama_in = st.text_input("Nama pemilik", value=v["nama"] or "")
            nohp_in = st.text_input("No HP pemilik", value=v["no_hp"] or "")
            status_in = st.selectbox("Status", ["inactive", "active"], index=0 if (v["status"] or "inactive")!="active" else 1)
            submit = st.form_submit_button("Simpan / Aktifkan")
            if submit:
                if status_in == "active" and (not nama_in.strip() or not nohp_in.strip()):
                    st.error("Untuk mengaktifkan voucher, isi Nama dan No HP terlebih dahulu.")
                else:
                    ok = update_voucher_detail(v["code"], nama_in.strip() or None, nohp_in.strip() or None, status_in)
                    if ok:
                        st.success(f"Voucher {v['code']} berhasil diperbarui ✅")
                        st.rerun()

    st.markdown("---")
    st.download_button("Download CSV (tabel saat ini)", data=df_to_csv_bytes(df), file_name="vouchers_page.csv", mime="text/csv")

# --------------------
# Page: Histori Transaksi (admin) dengan search voucher
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
# Page: Laporan Global (admin)
# --------------------
def page_laporan_global():
    st.header("Laporan Global (Admin)")

    # Filter periode tanggal
    st.subheader("Filter Periode Transaksi")
    col1, col2 = st.columns(2)
    with col1:
        start_date = st.date_input("Mulai dari", value=None)
    with col2:
        end_date = st.date_input("Sampai", value=None)

    df_vouchers = list_vouchers(limit=5000)
    df_tx = list_transactions(limit=100000)

    # Filter berdasarkan tanggal jika dipilih
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

    if not df_tx.empty:
        # Transaksi per cabang
        branch_agg = df_tx.groupby("branch")["used_amount"].agg(["count","sum"]).reset_index().fillna("Unknown")
        st.subheader("📈 Total nominal & transaksi per cabang")
        st.table(branch_agg.rename(columns={"branch":"Cabang","count":"#Transaksi","sum":"Total (Rp)"}))
        chart_branch = alt.Chart(branch_agg).mark_bar().encode(
            x=alt.X("branch:N", title="Cabang"),
            y=alt.Y("sum:Q", title="Total Nominal Terpakai"),
            tooltip=["branch","count","sum"]
        )
        st.altair_chart(chart_branch, use_container_width=True)

        # Top 5 voucher
        top_v = df_tx.groupby("code")["used_amount"].sum().reset_index().sort_values("used_amount", ascending=False).head(5)
        st.subheader("🏆 Top 5 voucher berdasarkan total pemakaian")
        st.table(top_v.rename(columns={"code":"Kode","used_amount":"Total (Rp)"}))
        chart_v = alt.Chart(top_v).mark_bar().encode(
            x=alt.X("code:N", title="Kode Voucher"),
            y=alt.Y("used_amount:Q", title="Total Terpakai"),
            tooltip=["code","used_amount"]
        )
        st.altair_chart(chart_v, use_container_width=True)

        # Time series harian
        df_tx["date"] = pd.to_datetime(df_tx["used_at"]).dt.date
        daily = df_tx.groupby("date")["used_amount"].sum().reset_index()
        st.subheader("📅 Time series harian pemakaian")
        chart_daily = alt.Chart(daily).mark_line(point=True).encode(
            x=alt.X("date:T", title="Tanggal"),
            y=alt.Y("used_amount:Q", title="Total Nominal"),
            tooltip=["date","used_amount"]
        )
        st.altair_chart(chart_daily, use_container_width=True)


    st.markdown("---")
    st.download_button("Download CSV Semua Transaksi (filtered)", data=df_to_csv_bytes(df_tx), file_name="transactions_global_filtered.csv", mime="text/csv")

# --------------------
# Page: Seller (admin-only)
# --------------------
def page_seller():
    st.title("🎫 Voucher Admin")
    st.subheader("Seller • Aktivasi & Detail Voucher")

    if "voucher" not in st.session_state:
        st.session_state.voucher = None

    search_code = st.text_input("Masukkan Kode Voucher")

    if st.button("Cari"):
        if search_code:
            try:
                with engine.connect() as conn:
                    result = conn.execute(text("""
                        SELECT code, initial_value, balance, seller
                        FROM vouchers WHERE code = :code
                    """), {"code": search_code}).fetchone()

                if result:
                    st.session_state.voucher = result
                else:
                    st.session_state.voucher = None
                    st.error("Voucher tidak ditemukan ❌")

            except Exception as e:
                st.session_state.voucher = None
                st.error("Terjadi kesalahan pada pencarian voucher ⚠️")
                st.code(str(e))

    # ✅ Tampilkan detail voucher jika berhasil ditemukan
    if st.session_state.voucher:
        code, initial_value, balance, seller = st.session_state.voucher

        st.success("Voucher ditemukan ✅")
        st.write("### Detail Voucher")

        st.table({
            "Kode Voucher": [code],
            "Initial Value": [initial_value],
            "Balance": [balance],
            "Seller": [seller if seller else "-"]
        })

        seller_input = st.text_input("Nama Seller", value=seller if seller else "")

        # ✅ Tombol Simpan kini ada di luar button Cari
        if st.button("Simpan Seller"):
            if seller_input:
                with engine.begin() as conn2:
                    conn2.execute(text("""
                        UPDATE vouchers SET seller = :seller
                        WHERE code = :code
                    """), {"seller": seller_input, "code": code})

                st.success(f"Seller berhasil disimpan ✅ ({seller_input})")

                # Mutakhirkan state ⚡
                st.session_state.voucher = (code, initial_value, balance, seller_input)

                st.rerun()

            else:
                st.warning("Nama Seller tidak boleh kosong!")

    st.markdown("---")
    st.subheader("📋 Daftar Voucher (Seller Terisi)")

    try:
        with engine.connect() as conn:
            df_seller = pd.read_sql(text("""
                SELECT code, initial_value, balance, seller
                FROM vouchers
                WHERE seller IS NOT NULL AND seller != ''
            """), conn)

        st.dataframe(df_seller, use_container_width=True)

    except Exception as e:
        st.error("Gagal memuat data voucher dengan seller ❌")
        st.code(str(e))


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
elif page == "Seller":
    if not st.session_state.admin_logged_in:
        st.error("Hanya admin yang dapat mengakses halaman Seller.")
    else:
        page_seller()

elif page == "Laporan Global":
    if not st.session_state.admin_logged_in:
        st.error("Hanya admin yang dapat mengakses laporan.")
    else:
        page_laporan_global()
else:
    st.info("Halaman tidak ditemukan.")














