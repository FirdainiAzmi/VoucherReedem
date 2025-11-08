import streamlit as st
import pandas as pd
from datetime import datetime, date
from sqlalchemy import create_engine, text
from io import BytesIO
import altair as alt
import matplotlib.pyplot as plt
import math
import traceback 
from database import get_db_connection

# Config & DB connect
DB_URL = st.secrets["DB_URL"]
ADMIN_PASSWORD = st.secrets.get("ADMIN_PASSWORD", "admin123")
engine = create_engine(DB_URL, future=True)

# Database
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
                SELECT code, initial_value, balance, created_at, nama, no_hp, status, seller, tanggal_penjualan
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

def atomic_redeem(code, used_amount, branch, items_str):
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT balance FROM vouchers WHERE code=%s FOR UPDATE", (code,))
                row = cur.fetchone()
                if not row:
                    return False, "Voucher tidak ditemukan", None

                balance = row[0]
                if balance < used_amount:
                    return False, "Saldo tidak cukup", balance

                # Update saldo voucher
                new_balance = balance - used_amount
                cur.execute("UPDATE vouchers SET balance=%s, status='used' WHERE code=%s", (new_balance, code))

                # Insert transaksi
                cur.execute("""
                    INSERT INTO voucher_transactions (code, used_amount, branch, items)
                    VALUES (%s, %s, %s, %s)
                """, (code, used_amount, branch, items_str))

                # ✅ UPDATE JUMLAH TERJUAL PER CABANG
                # items_str format: "Item A x2, Item B x1"
                items = [x.strip() for x in items_str.split(",")]

                for i in items:
                    if " x" not in i:
                        continue
                    nama_item, qty = i.split(" x")
                    qty = int(qty)

                    if branch == "Tawangsari":
                        cur.execute("""
                            UPDATE menu_items
                            SET terjual_twsari = COALESCE(terjual_twsari,0) + %s
                            WHERE nama_item = %s
                        """, (qty, nama_item))
                    
                    elif branch == "Sedati":
                        cur.execute("""
                            UPDATE menu_items
                            SET terjual_sedati = COALESCE(terjual_sedati,0) + %s
                            WHERE nama_item = %s
                        """, (qty, nama_item))

                conn.commit()
                return True, "Transaksi berhasil", new_balance

    except Exception as e:
        traceback.print_exc()
        return False, str(e), None


def list_vouchers(filter_status=None, search=None, limit=5000, offset=0):
    q = "SELECT code, initial_value, balance, created_at, nama, no_hp, status, seller, tanggal_penjualan FROM vouchers"
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

# Session helpers
def ensure_session_state():
    st.session_state.setdefault("admin_logged_in", False)
    st.session_state.setdefault("page", "Aktivasi Voucher" if st.session_state.get("admin_logged_in") else "Penukaran Voucher")
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
    st.session_state.page = "Penukaran Voucher"
    st.session_state.edit_code = None

# Init
init_db()
ensure_session_state()
st.set_page_config(page_title="Voucher Pawon Sappitoe", layout="wide")
st.title("🎫 Voucher Pawon Sappitoe")

# Sidebar
with st.sidebar:
    st.markdown("## Menu")
    if st.session_state.admin_logged_in:
        st.success("Logged in as **admin**")
        if st.button("Logout"):
            admin_logout()
            st.rerun()
        st.markdown("---")
        page_choice = st.radio("Pilih halaman", ("Aktivasi Voucher", "Laporan Warung", "Histori Transaksi", "Seller"),
                               index=("Aktivasi Voucher","Laporan Warung","Histori Transaksi", "Seller").index(st.session_state.get("page") if st.session_state.get("page") in ("Aktivasi Voucher","Laporan Warung","Histori Transaksi", "Seller") else "Aktivasi Voucher"))
        st.session_state.page = page_choice
    else:
        st.markdown("### Admin Login")
        pwd = st.text_input("Password", type="password")
        if st.button("Login sebagai admin"):
            if admin_login(pwd):
                st.session_state.admin_logged_in = True
                st.session_state.page = "Aktivasi Voucher"
                st.success("Login admin berhasil")
                st.rerun()
            else:
                st.error("Password salah")
        st.markdown("---")
        st.info("Login hanya untuk admin.")

# Force page if not admin
page = st.session_state.get("page", "Penukaran Voucher")
if not st.session_state.admin_logged_in:
    page = "Penukaran Voucher"


# Page: Penukaran Voucher (public)
def page_redeem():
    st.header("Penukaran Voucher (User)")

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
                code, initial_value, balance, created_at, nama, no_hp, status, seller, tanggal_penjualan = row
    
                # ✅ Validasi status
                if status.lower() != "active":
                    st.error("⛔ Voucher belum dapat digunakan. Status masih INACTIVE.")
                    reset_redeem_state()
                    st.rerun()
    
                # ✅ Validasi tanggal_penjualan
                if tanggal_penjualan is None:
                    st.error("⛔ Voucher belum bisa digunakan. Tanggal penjualan belum tercatat.")
                    reset_redeem_state()
                    st.rerun()

                if hasattr(tanggal_penjualan, "date"):
                    tgl_penjualan = tanggal_penjualan.date()
                else:
                    tgl_penjualan = datetime.strptime(str(tanggal_penjualan), "%Y-%m-%d").date()
    
                # ✅ Tidak boleh dipakai HARI YANG SAMA
                if tgl_penjualan == date.today():
                    st.error("⛔ Voucher belum bisa digunakan. Penukaran hanya bisa dilakukan H+1 setelah voucher dibeli.")
                    reset_redeem_state()
                    st.rerun()
    
                # ✅ Jika semua valid → lanjut
                st.session_state.voucher_row = row
                st.session_state.redeem_step = 2
                st.rerun()

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

    # STEP 3: Konfirmasi pembayaran
    elif st.session_state.redeem_step == 3:
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


# Page: Aktivasi Voucher (admin) — inline edit
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
                                st.session_state["voucher_update_success"] = f"Voucher {v['code']} berhasil diaktifkan ✅"
                            
                                st.session_state.reset_search = True
                                st.session_state.vouchers_page_idx = 0
                                st.rerun()

    st.markdown("---")
    st.download_button(
        "Download CSV (tabel saat ini)",
        data=df_to_csv_bytes(df),
        file_name="vouchers_page.csv",
        mime="text/csv"
    )


# Page: Histori Transaksi (admin)
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

# Page: Laporan Warung (admin)
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
        # 🍽️ Top 5 Menu Paling Banyak Dibeli
        # =============================== #
        if "nama_item" in df_filtered.columns:
            st.subheader("🍽️ Top 5 Menu Terjual")
            top_menu = (
                df_filtered.groupby("nama_item")["nama_item"].count()
                .sort_values(ascending=False)
                .head(5)
                .reset_index(name="Jumlah Pembelian")
            )
            st.table(top_menu)
            st.bar_chart(top_menu, x="nama_item", y="Jumlah Pembelian")
        else:
            st.warning("⚠️ Kolom menu_name tidak ditemukan. Pastikan ada data nama menu.")
    
        # =============================== #
        # ⬇️ Export Data
        # =============================== #
        csv_export = df_filtered.to_csv(index=False).encode("utf-8")
        st.download_button(
            "📥 Download Data Transaksi (CSV)",
            data=csv_export,
            file_name="laporan_transaksi.csv",
            mime="text/csv"
    )


    # ===== TAB Seller =====
    with tab_seller:   
        st.subheader("📊 Ringkasan Seller - Voucher Aktif")

        if "seller" not in df_vouchers.columns:
            st.warning("Kolom 'seller' tidak tersedia di table vouchers.")
            st.stop()
        df_vouchers["seller"] = df_vouchers["seller"].fillna("-")
        df_active = df_vouchers[df_vouchers["status"] == "active"]
    
        if not df_active.empty:
            seller_active_count = (
                df_active.groupby("seller")
                .size()
                .reset_index(name="Voucher Aktif")
                .sort_values(by="Voucher Aktif", ascending=False)
            )
    
            st.table(seller_active_count.rename(columns={"seller": "Seller"}))
    
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
    if "found_voucher" not in st.session_state:
        st.session_state["found_voucher"] = None
    if "search_input" not in st.session_state:
        st.session_state["search_input"] = ""
    if "clear_search" not in st.session_state:
        st.session_state["clear_search"] = False

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


        seller_input = st.text_input(
            "Nama Seller", 
            value=seller if seller else "", 
            key="seller_input"
        )

        tanggal_input = st.date_input(
            "Tanggal Penjualan", 
            value=pd.to_datetime("today"), 
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


    st.markdown("---")
    st.subheader("📋 Aktivasi Voucher (Seller Terisi)")
    
    try:
        with engine.connect() as conn:
            df_seller = pd.read_sql(text("""
                SELECT code, initial_value, balance, seller, tanggal_penjualan
                FROM vouchers
                WHERE seller IS NOT NULL AND seller != ''
                ORDER BY code DESC
            """), conn)
        
        st.dataframe(df_seller, use_container_width=True)
    
    except Exception as e:
        st.error("Gagal memuat data voucher ❌")
        st.code(str(e))



# Router
if page == "Penukaran Voucher":
    page_redeem()
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
        page_seller()

elif page == "Laporan Warung":
    if not st.session_state.admin_logged_in:
        st.error("Hanya admin yang dapat mengakses laporan.")
    else:
        page_laporan_global()
else:
    st.info("Halaman tidak ditemukan.")






























