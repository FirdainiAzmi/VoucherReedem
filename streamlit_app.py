# streamlit_app.py — Full Final

import streamlit as st
import pandas as pd
from datetime import datetime, date
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
                    tanggal_transaksi TIMESTAMP NOT NULL,
                    branch TEXT,
                    items TEXT
                )
            """))
            conn.execute(text("ALTER TABLE vouchers ADD COLUMN IF NOT EXISTS nama TEXT"))
            conn.execute(text("ALTER TABLE vouchers ADD COLUMN IF NOT EXISTS no_hp TEXT"))
            conn.execute(text("ALTER TABLE vouchers ADD COLUMN IF NOT EXISTS status TEXT"))
            conn.execute(text("ALTER TABLE vouchers ADD COLUMN IF NOT EXISTS seller TEXT"))
            conn.execute(text("ALTER TABLE vouchers ADD COLUMN IF NOT EXISTS tanggal_penjualan DATE"))
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
                INSERT INTO transactions (code, used_amount, tanggal_transaksi, branch, items)
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

def get_menu_from_db(branch):
    """
    Ambil menu dari DB sesuai cabang.
    branch: "Sedati" atau "Tawangsari"
    return: list of dict {"nama":..., "harga":..., "kategori":...}
    """
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
                else:
                    st.session_state.voucher_row = row
                    st.session_state.redeem_step = 2
                    st.rerun()

    # STEP 2: Pilih cabang & menu
    elif st.session_state.redeem_step == 2:
        row = st.session_state.voucher_row
        code, initial_value, balance, created_at, nama, no_hp, status = row
    
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
    
        # Ambil menu dari database
        menu_items = get_menu_from_db(selected_branch)  # list of dict {"nama","harga","kategori"}
        categories = sorted(list(set([item["kategori"] for item in menu_items])))
    
        # Global search
        search_query = st.text_input("🔍 Cari menu (global)", "").strip().lower()
    
        # Pastikan order_items ada di session_state
        if "order_items" not in st.session_state:
            st.session_state.order_items = {}
    
        st.markdown("*Pilih menu & jumlah*")
    
        if search_query:  # Jika ada search, tampilkan semua menu yang cocok tanpa tab
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
        else:  # Jika tidak search, tampilkan tab per kategori
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





    # STEP 3: Konfirmasi pembayaran
    # STEP 3: Konfirmasi pembayaran
    elif st.session_state.redeem_step == 3:
        row = st.session_state.voucher_row
        code, initial, balance, created_at, nama, no_hp, status = row
    
        st.header("Konfirmasi Pembayaran")
        st.write(f"- Voucher: {code}")
        st.write(f"- Cabang: {st.session_state.selected_branch}")
        st.write(f"- Sisa sebelum: Rp {int(balance):,}")
    
        # Ambil menu dari db untuk harga
        menu_items = get_menu_from_db(st.session_state.selected_branch)
        price_map = {item['nama']: item['harga'] for item in menu_items}
    
        # Filter hanya yang dipesan (qty > 0)
        ordered_items = {k:v for k,v in st.session_state.order_items.items() if v > 0}
    
        if not ordered_items:
            st.warning("Tidak ada menu yang dipilih.")
            return
    
        st.write("Detail pesanan:")
        for it, q in ordered_items.items():
            st.write(f"- {it} x{q} — Rp {price_map.get(it,0)*q:,}")
    
        st.write(f"### Total: Rp {st.session_state.checkout_total:,}")
    
        # Tombol konfirmasi / kembali
        cA, cB = st.columns([1,1])
        with cA:
            if st.button("Ya, Bayar"):
                items_str = ", ".join([f"{k} x{v}" for k,v in ordered_items.items()])
                ok, msg, newbal = atomic_redeem(
                    code, st.session_state.checkout_total,
                    st.session_state.selected_branch, items_str
                )
                if ok:
                    st.success(f"🎉 TRANSAKSI BERHASIL 🎉\nSisa saldo sekarang: Rp {int(newbal):,}")
    
                    # Reset session_state
                    reset_redeem_state()
                    st.rerun()
                else:
                    st.error(msg)
                    st.session_state.redeem_step = 2
                    st.rerun()
        with cB:
            if st.button("Tidak, Kembali"):
                st.session_state.redeem_step = 2
                st.rerun()




# --------------------
# Page: Daftar Voucher (admin) — inline edit
# --------------------
def page_daftar_voucher():
    st.header("Daftar Voucher (Admin) — Tabel penuh")

    # ===== Inisialisasi session state =====
    st.session_state.setdefault("vouchers_page_idx", 0)
    st.session_state.setdefault("vouchers_per_page", 10)
    st.session_state.setdefault("search", "")
    st.session_state.setdefault("reset_search", False)

    # Reset search jika flag di-set
    if st.session_state.reset_search:
        st.session_state.search = ""
        st.session_state.reset_search = False

    # Tampilkan pesan sukses jika ada
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
        # Ambil semua voucher dulu
        df = list_vouchers(limit=5000)  # ambil banyak agar filter di pandas efektif
        # Filter hanya yang ada seller
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

    # Format tampilan tabel
    df_display = df.copy()
    df_display["initial_value"] = df_display["initial_value"].apply(lambda x: f"Rp {int(x):,}")
    df_display["balance"] = df_display["balance"].apply(lambda x: f"Rp {int(x):,}")
    df_display["created_at"] = pd.to_datetime(df_display["created_at"]).dt.strftime("%Y-%m-%d")

    st.dataframe(
        df_display[["code", "nama", "no_hp", "status", "initial_value", "balance", "created_at", "seller"]],
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
            st.warning("⚠ Voucher ini belum memiliki seller. Tidak dapat mengubah data kepemilikan.")
            st.info("Silakan tetapkan seller terlebih dahulu di menu pengelolaan voucher.")
        else:
            with st.form(key=f"edit_form_{v['code']}"):
                nama_in = st.text_input("Nama pemilik", value=v["nama"] or "")
                nohp_in = st.text_input("No HP pemilik", value=v["no_hp"] or "")
                status_in = st.selectbox(
                    "Status", ["inactive", "active"],
                    index=0 if (v["status"] or "inactive") != "active" else 1
                )
        
                submit = st.form_submit_button("Simpan / Aktifkan")
                if submit:
                    if status_in == "active" and (not nama_in.strip() or not nohp_in.strip()):
                        st.error("Untuk mengaktifkan voucher, isi Nama dan No HP terlebih dahulu.")
                    else:
                        ok = update_voucher_detail(
                            v["code"],
                            nama_in.strip() or None,
                            nohp_in.strip() or None,
                            status_in
                        )
                        if ok:
                            st.session_state["voucher_update_success"] = f"Voucher {v['code']} berhasil diperbarui ✅"
                            st.session_state.reset_search = True
                            st.session_state.vouchers_page_idx = 0
                            st.rerun()
        
                    submit = st.form_submit_button("Simpan / Aktifkan")
                    if submit:
                        if status_in == "active" and (not nama_in.strip() or not nohp_in.strip()):
                            st.error("Untuk mengaktifkan voucher, isi Nama dan No HP terlebih dahulu.")
                        else:
                            ok = update_voucher_detail(
                                v["code"],
                                nama_in.strip() or None,
                                nohp_in.strip() or None,
                                status_in
                            )
                            if ok:
                                # Tampilkan pesan sukses
                                st.session_state["voucher_update_success"] = f"Voucher {v['code']} berhasil diaktifkan ✅"
                                
                                # Reset input pencarian & halaman pakai flag
                                st.session_state.reset_search = True
                                st.session_state.vouchers_page_idx = 0
                                
                                # Rerun untuk kembali ke halaman awal
                                st.rerun()

    st.markdown("---")
    st.download_button(
        "Download CSV (tabel saat ini)",
        data=df_to_csv_bytes(df),
        file_name="vouchers_page.csv",
        mime="text/csv"
    )


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
            df_display["tanggal_transaksi"] = pd.to_datetime(df_display["tanggal_transaksi"])
            df_display = df_display.rename(columns={"id":"ID","code":"Kode","used_amount":"Jumlah","tanggal_transaksi":"Waktu","branch":"Cabang","items":"Menu"})
            st.dataframe(df_display[["ID","Kode","Waktu","Jumlah","Cabang","Menu"]], use_container_width=True)
            st.download_button(f"Download CSV {search_code.strip().upper()}", data=df_to_csv_bytes(df_display), file_name=f"transactions_{search_code.strip().upper()}.csv", mime="text/csv")
    else:
        df_tx["tanggal_transaksi"] = pd.to_datetime(df_tx["tanggal_transaksi"])
        df_tx = df_tx.rename(columns={"id":"ID","code":"Kode","used_amount":"Jumlah","tanggal_transaksi":"Waktu","branch":"Cabang","items":"Menu"})
        st.dataframe(df_tx, use_container_width=True)
        st.download_button("Download CSV Transaksi", data=df_to_csv_bytes(df_tx), file_name="transactions.csv", mime="text/csv")

# --------------------
# Page: Laporan Global (admin)
# --------------------
def page_laporan_global():
    st.header("Laporan Global (Admin)")

    # Tabs untuk membagi laporan
    tab_voucher, tab_transaksi, tab_seller = st.tabs(["Voucher", "Transaksi", "Seller"])

    # Ambil data
    df_vouchers = list_vouchers(limit=5000)
    df_tx = list_transactions(limit=100000)

    # Pastikan kolom tanggal sebagai datetime
    if "tanggal_transaksi" in df_tx.columns:
        df_tx["tanggal_transaksi"] = pd.to_datetime(df_tx["tanggal_transaksi"])

    # ===== TAB Voucher =====
    with tab_voucher:
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
        st.write(f"- Total saldo awal: Rp {int(total_saldo_awal):,}")
        st.write(f"- Total saldo tersisa: Rp {int(total_saldo_tersisa):,}")

        st.markdown("---")
        st.dataframe(df_vouchers, use_container_width=True)

    # ===== TAB Transaksi =====
    with tab_transaksi:
        st.subheader("📊 Ringkasan Transaksi")
        total_tx = len(df_tx)
        total_tx_nominal = df_tx["used_amount"].sum() if "used_amount" in df_tx.columns else 0
        avg_tx = df_tx["used_amount"].mean() if total_tx>0 and "used_amount" in df_tx.columns else 0

        st.write(f"- Total transaksi: {total_tx}")
        st.write(f"- Total nominal digunakan: Rp {int(total_tx_nominal):,}")
        st.write(f"- Rata-rata nominal per transaksi: Rp {int(avg_tx):,}")

        if not df_tx.empty and "used_amount" in df_tx.columns:
            # Transaksi per cabang
            if "branch" in df_tx.columns:
                branch_agg = df_tx.groupby("branch")["used_amount"].agg(["count","sum"]).reset_index()
                st.subheader("📈 Total nominal & transaksi per cabang")
                st.table(branch_agg.rename(columns={"branch":"Cabang","count":"#Transaksi","sum":"Total (Rp)"}))
                
                chart_branch = alt.Chart(branch_agg).mark_bar().encode(
                    x=alt.X("branch:N", title="Cabang"),
                    y=alt.Y("sum:Q", title="Total Nominal Terpakai"),
                    tooltip=["branch","count","sum"]
                )
                st.altair_chart(chart_branch, use_container_width=True)

            # Top 5 voucher
            if "code" in df_tx.columns:
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
            # ubah ke datetime tanpa jam
            df_tx["date"] = pd.to_datetime(df_tx["tanggal_transaksi"]).dt.normalize()  
            
            daily = df_tx.groupby("date")["used_amount"].sum().reset_index()
            
            st.subheader("📅 Time series harian pemakaian")
            chart_daily = alt.Chart(daily).mark_line(point=True).encode(
                x=alt.X("date:T", title="Tanggal", axis=alt.Axis(format="%Y-%m-%d")),  # format tanggal
                y=alt.Y("used_amount:Q", title="Total Nominal"),
                tooltip=[alt.Tooltip("date:T", format="%Y-%m-%d"), "used_amount"]
            )
            st.altair_chart(chart_daily, use_container_width=True)


    # ===== TAB Seller =====
    with tab_seller:   
        st.subheader("📊 Ringkasan Seller - Voucher Aktif")
    
        # Pastikan seller ada
        if "seller" not in df_vouchers.columns:
            st.warning("Kolom 'seller' tidak tersedia di table vouchers.")
            st.stop()
    
        # Isi seller kosong dengan tanda '-'
        df_vouchers["seller"] = df_vouchers["seller"].fillna("-")
    
        # Filter hanya voucher aktif
        df_active = df_vouchers[df_vouchers["status"] == "active"]
    
        if not df_active.empty:
            # Hitung jumlah voucher aktif per seller
            seller_active_count = (
                df_active.groupby("seller")
                .size()
                .reset_index(name="Voucher Aktif")
                .sort_values(by="Voucher Aktif", ascending=False)
            )
    
            st.table(seller_active_count.rename(columns={"seller": "Seller"}))
    
            # Bar chart
            chart_seller = alt.Chart(seller_active_count).mark_bar().encode(
                x=alt.X("seller:N", title="Seller"),
                y=alt.Y("Voucher Aktif:Q", title="Jumlah Voucher Aktif"),
                tooltip=["seller", "Voucher Aktif"]
            )
            st.altair_chart(chart_seller, use_container_width=True)
    
        else:
            st.info("Belum ada voucher yang berstatus aktif.")

    # Download CSV
    st.markdown("---")
    st.download_button(
        "Download CSV Semua Transaksi (filtered)",
        data=df_to_csv_bytes(df_tx),
        file_name="transactions_global_filtered.csv",
        mime="text/csv"
    )



# --------------------
# Page: Seller (admin-only)
# --------------------
def page_seller():
    st.subheader("Seller")

    # Inisialisasi state
    if "found_voucher" not in st.session_state:
        st.session_state["found_voucher"] = None
    if "search_input" not in st.session_state:
        st.session_state["search_input"] = ""
    if "clear_search" not in st.session_state:
        st.session_state["clear_search"] = False

    # Jika ada flag clear_search, reset nilai search_input
    if st.session_state.get("clear_search"):
        st.session_state["search_input"] = ""
        st.session_state["clear_search"] = False

    search_code = st.text_input("Masukkan Kode Voucher", key="search_input")

    if st.button("Cari"):
        if search_code:
            try:
                with engine.connect() as conn:
                    result = conn.execute(text("""
                        SELECT code, initial_value, balance, seller
                        FROM vouchers
                        WHERE code = :code
                    """), {"code": search_code}).fetchone()

                if result:
                    st.session_state["found_voucher"] = result
                else:
                    st.session_state["found_voucher"] = None
                    st.error("Voucher tidak ditemukan ❌")

            except Exception as e:
                st.session_state["found_voucher"] = None
                st.error("Terjadi kesalahan saat mencari voucher ⚠️")
                st.code(str(e))

    # Tampilkan detail voucher jika ada
    if st.session_state.get("found_voucher"):
        code, initial_value, balance, seller = st.session_state["found_voucher"]

        st.success("Voucher ditemukan ✅")
        st.write("### Detail Voucher")
        st.table({
            "Kode Voucher": [code],
            "Initial Value": [initial_value],
            "Balance": [balance],
            "Seller": [seller if seller else "-"]
        })

        # gunakan key berbeda untuk input seller
        # Input Nama Seller
        seller_input = st.text_input(
            "Nama Seller", 
            value=seller if seller else "", 
            key="seller_input"
        )
        
        # Input Tanggal Penjualan
        tanggal_input = st.date_input(
            "Tanggal Penjualan", 
            value=pd.to_datetime("today"),  # default hari ini
            key="tanggal_input"
        )
        
        if st.button("Simpan Seller"):
            if seller_input:
                try:
                    with engine.begin() as conn2:
                        conn2.execute(text("""
                            UPDATE vouchers 
                            SET seller = :seller,
                                tanggal_penjualan = :tanggal_penjualan
                            WHERE code = :code
                        """), {
                            "seller": seller_input, 
                            "tanggal_penjualan": tanggal_input,  # pastikan tipe kolom DATE di DB
                            "code": code
                        })
        
                    st.success("Seller dan Tanggal Penjualan berhasil disimpan ✅")
        
                    # Reset search input untuk rerun berikutnya
                    st.session_state["found_voucher"] = None
                    st.session_state["clear_search"] = True
        
                    st.rerun()
        
                except Exception as e:
                    st.error("Gagal menyimpan seller dan tanggal ❌")
                    st.code(str(e))
            else:
                st.warning("Nama Seller tidak boleh kosong!")


    # Tampilkan daftar voucher seller terisi
    st.markdown("---")
    st.subheader("📋 Daftar Voucher (Seller Terisi)")
    
    try:
        with engine.connect() as conn:
            df_seller = pd.read_sql(text("""
                SELECT code, initial_value, balance, seller, tanggal_penjualan
                FROM vouchers
                WHERE seller IS NOT NULL AND seller != ''
                ORDER BY code DESC
            """), conn)
        
        # Tampilkan tabel dengan tanggal_penjualan
        st.dataframe(df_seller, use_container_width=True)
    
    except Exception as e:
        st.error("Gagal memuat data voucher ❌")
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





