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
st.title("🎫 Kupon Pawon Sappitoe")
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
        # Gunakan key agar Streamlit tidak kehilangan nilai antar rerun
        entered_code = st.text_input(
            "Masukkan kode kupon",
            key="entered_code_input",
            value=st.session_state.entered_code
        ).strip().upper()

        # Update session_state tiap kali ada perubahan input
        st.session_state.entered_code = entered_code
    
        if st.button("Tukar Kupon"):
            code = st.session_state.entered_code
            st.session_state.pop('redeem_error', None)  # hapus error lama

            if not code:
                st.session_state['redeem_error'] = "⚠️ Kode tidak boleh kosong"
            else:
                row = find_voucher(code)
                if not row:
                    st.session_state['redeem_error'] = "❌ Voucher tidak ditemukan."
                else:
                    code, initial_value, balance, created_at, nama, no_hp, status, seller, tanggal_aktivasi = row
                
                    # --- LOGIKA STATUS VOUCHER ---
                    status_normalized = (status or "").strip().lower()
                
                    if status_normalized == "inactive":
                        st.session_state['redeem_error'] = "⛔ Voucher belum aktif dan tidak bisa digunakan."
                    elif status_normalized == "sold out" or balance <= 0:
                        st.session_state['redeem_error'] = "⛔ Saldo voucher sudah habis dan tidak bisa digunakan."
                    elif status_normalized != "active":
                        # fallback untuk status tidak dikenal
                        st.session_state['redeem_error'] = f"⛔ Voucher tidak dapat digunakan. Status: {status}"
                    else:
                        # status aktif → lanjutkan pengecekan tanggal aktivasi
                        if tanggal_aktivasi is None:
                            st.session_state['redeem_error'] = "⛔ Voucher belum bisa digunakan. Tanggal aktivasi belum tercatat."
                        else:
                            # Cek tanggal aktivasi
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
                                # Semua valid → lanjut
                                st.session_state.voucher_row = row
                                st.session_state.redeem_step = 2
                                st.session_state.pop('redeem_error', None)
                                st.rerun()

        # Tampilkan error jika ada
        if 'redeem_error' in st.session_state:
            st.error(st.session_state['redeem_error'])

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

        # --- Normalisasi struktur menu_items ---
        normalized = []
        if menu_items:
            # case: list of tuples (legacy)
            if isinstance(menu_items[0], tuple):
                for m in menu_items:
                    # pastikan panjang tuple sesuai, jika tidak lewatkan
                    try:
                        kategori = m[0]
                        nama_item = m[1]
                        keterangan = m[2]
                        harga_sedati = m[3]
                        harga_twsari = m[4]
                    except Exception:
                        continue

                    # pilih harga sesuai cabang
                    if selected_branch == "Sedati":
                        harga = harga_sedati
                    else:  # Tawangsari
                        harga = harga_twsari

                    # hanya masukkan menu yang punya harga bukan None
                    if harga is None:
                        continue

                    normalized.append({
                        "kategori": kategori,
                        "nama": nama_item,
                        "keterangan": keterangan,
                        "harga": int(harga) if (harga is not None and not pd.isna(harga)) else None
                    })

            # case: list of dicts (yang di-return get_menu_from_db normal)
            elif isinstance(menu_items[0], dict):
                for it in menu_items:
                    # harga sudah diset di get_menu_from_db sebagai 'harga'
                    harga = it.get("harga")
                    # jika harga None skip
                    if harga is None or pd.isna(harga):
                        continue
                    normalized.append({
                        "kategori": it.get("kategori"),
                        "nama": it.get("nama"),
                        "keterangan": it.get("keterangan", ""),
                        "harga": int(harga)
                    })

        # jika tidak ada menu (kosong / semua harga None)
        if not normalized:
            st.info("Tidak ada menu yang tersedia untuk cabang ini.")
            return

        menu_items = normalized

        # Ambil daftar kategori dari menu_items yang sudah difilter
        categories = sorted({item["kategori"] for item in menu_items if item.get("kategori") is not None})

        # Jika tidak ada kategori (safety)
        if not categories:
            st.info("Tidak ada kategori menu untuk ditampilkan.")
            return
    
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
    
        total = st.session_state.checkout_total
        saldo = int(balance)
        shortage = total - saldo if total > saldo else 0
    
        st.write("Detail pesanan:")
        for it, q in ordered_items.items():
            st.write(f"- {it} x{q} — Rp {price_map.get(it,0)*q:,}")
    
        st.write(f"### Total: Rp {total:,}")
    
        if shortage > 0:
            st.error(f"⚠️ Saldo voucher kurang Rp {shortage:,}.")
            st.info("Sisa total harus dibayar dengan *cash* oleh pembeli.")
        else:
            st.success("Saldo voucher mencukupi 🎉")
    
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
    
    # ===== Pop-up transaksi berhasil =====
    if st.session_state.show_success:
        st.success(f"🎉 TRANSAKSI BERHASIL 🎉\nSisa saldo sekarang: Rp {int(st.session_state.newbal):,}")
        st.write("Tutup pop-up ini untuk kembali ke awal.")
        if st.button("Tutup"):
            reset_redeem_state() 
            st.session_state.show_success = False
            st.rerun()

# ---------------------------
# Sidebar / Login UI
# ---------------------------
with st.sidebar:
    # st.markdown("## Menu")

    # if not (st.session_state.admin_logged_in or st.session_state.seller_logged_in):
    #     pageumum_choice = st.radio(
    #         "Pilih Halaman",
    #         ("Redeem Voucher", "Daftar Sebagai Seller"),
    #         index=("Redeem Voucher", "Daftar Sebagai Seller").index(
    #             st.session_state.get("page")
    #             if st.session_state.get("page") in ("Redeem Voucher", "Daftar Sebagai Seller")
    #             else "Redeem Voucher"
    #         )
    #     )
    #     st.session_state.page = pageumum_choice
    
    # If admin logged in -> full admin menu
    if st.session_state.admin_logged_in:
        st.success("Logged in as **Admin**")
        if st.button("Logout"):
            admin_logout()
            st.rerun()

        st.markdown("---")
        page_choice = st.radio("Pilih halaman Admin",
                               ("Edit Voucher", "Laporan Warung", "Histori Transaksi", "Kelola Seller"),
                               index=("Edit Voucher","Laporan Warung","Histori Transaksi","Seller").index(
                                   st.session_state.get("page") if st.session_state.get("page") in ("Edit Voucher","Laporan Warung","Histori Transaksi","Seller") else "Edit Voucher"
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
            # st.markdown("---")
            pwd_seller = st.text_input("Password Seller", type="password", key="pwd_seller")
            if st.button("Login sebagai Seller"):
                if pwd_seller == SELLER_PASSWORD:
                    st.session_state.seller_logged_in = True
                    st.session_state.page = "Aktivasi Voucher Seller"
                    st.success("Login seller berhasil")
                    st.rerun()
                else:
                    st.error("Password seller salah")

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
# def page_redeem():
#     st.header("Penukaran Kupon")

#     # Inisialisasi state
#     if "redeem_step" not in st.session_state:
#         st.session_state.redeem_step = 1
#     if "entered_code" not in st.session_state:
#         st.session_state.entered_code = ""

#     # STEP 1: Input kode voucher
#     if st.session_state.redeem_step == 1:
#         # Gunakan key agar Streamlit tidak kehilangan nilai antar rerun
#         entered_code = st.text_input(
#             "Masukkan kode kupon",
#             key="entered_code_input",
#             value=st.session_state.entered_code
#         ).strip().upper()

#         # Update session_state tiap kali ada perubahan input
#         st.session_state.entered_code = entered_code
    
#         if st.button("Tukar Kupon"):
#             code = st.session_state.entered_code
#             st.session_state.pop('redeem_error', None)  # hapus error lama

#             if not code:
#                 st.session_state['redeem_error'] = "⚠️ Kode tidak boleh kosong"
#             else:
#                 row = find_voucher(code)
#                 if not row:
#                     st.session_state['redeem_error'] = "❌ Voucher tidak ditemukan."
#                 else:
#                     code, initial_value, balance, created_at, nama, no_hp, status, seller, tanggal_aktivasi = row
                
#                     # --- LOGIKA STATUS VOUCHER ---
#                     status_normalized = (status or "").strip().lower()
                
#                     if status_normalized == "inactive":
#                         st.session_state['redeem_error'] = "⛔ Voucher belum aktif dan tidak bisa digunakan."
#                     elif status_normalized == "sold out" or balance <= 0:
#                         st.session_state['redeem_error'] = "⛔ Saldo voucher sudah habis dan tidak bisa digunakan."
#                     elif status_normalized != "active":
#                         # fallback untuk status tidak dikenal
#                         st.session_state['redeem_error'] = f"⛔ Voucher tidak dapat digunakan. Status: {status}"
#                     else:
#                         # status aktif → lanjutkan pengecekan tanggal aktivasi
#                         if tanggal_aktivasi is None:
#                             st.session_state['redeem_error'] = "⛔ Voucher belum bisa digunakan. Tanggal aktivasi belum tercatat."
#                         else:
#                             # Cek tanggal aktivasi
#                             if hasattr(tanggal_aktivasi, "date"):
#                                 tgl_aktivasi = tanggal_aktivasi.date()
#                             else:
#                                 try:
#                                     tgl_aktivasi = datetime.strptime(str(tanggal_aktivasi), "%Y-%m-%d").date()
#                                 except Exception:
#                                     tgl_aktivasi = None
                
#                             if tgl_aktivasi == date.today():
#                                 st.session_state['redeem_error'] = "⛔ Voucher belum bisa digunakan. Penukaran hanya bisa dilakukan H+1 setelah voucher diaktifkan."
#                             else:
#                                 # Semua valid → lanjut
#                                 st.session_state.voucher_row = row
#                                 st.session_state.redeem_step = 2
#                                 st.session_state.pop('redeem_error', None)
#                                 st.rerun()

#         # Tampilkan error jika ada
#         if 'redeem_error' in st.session_state:
#             st.error(st.session_state['redeem_error'])

#     # STEP 2: Pilih cabang & menu
#     elif st.session_state.redeem_step == 2:
#         row = st.session_state.voucher_row
#         code, initial_value, balance, created_at, nama, no_hp, status, seller, tanggal_penjualan = row
    
#         st.subheader(f"Voucher: {code}")
#         st.write(f"- Nilai awal: Rp {int(initial_value):,}")
#         st.write(f"- Sisa saldo: Rp {int(balance):,}")
#         st.write(f"- Nama: {nama or '-'}")
#         st.write(f"- No HP: {no_hp or '-'}")
#         st.write(f"- Status: {status or 'inactive'}")
    
#         if int(balance) <= 0:
#             st.warning("Voucher sudah tidak dapat digunakan (saldo 0).")
#             if st.button("Kembali"):
#                 reset_redeem_state()
#                 st.rerun()
#             return
    
#         # Pilih cabang
#         branch_options = ["Sedati", "Tawangsari"]
#         selected_branch = st.selectbox("Pilih cabang", branch_options, index=0)
#         st.session_state.selected_branch = selected_branch

#         menu_items = get_menu_from_db(selected_branch)

#         # --- Normalisasi struktur menu_items ---
#         normalized = []
#         if menu_items:
#             # case: list of tuples (legacy)
#             if isinstance(menu_items[0], tuple):
#                 for m in menu_items:
#                     # pastikan panjang tuple sesuai, jika tidak lewatkan
#                     try:
#                         kategori = m[0]
#                         nama_item = m[1]
#                         keterangan = m[2]
#                         harga_sedati = m[3]
#                         harga_twsari = m[4]
#                     except Exception:
#                         continue

#                     # pilih harga sesuai cabang
#                     if selected_branch == "Sedati":
#                         harga = harga_sedati
#                     else:  # Tawangsari
#                         harga = harga_twsari

#                     # hanya masukkan menu yang punya harga bukan None
#                     if harga is None:
#                         continue

#                     normalized.append({
#                         "kategori": kategori,
#                         "nama": nama_item,
#                         "keterangan": keterangan,
#                         "harga": int(harga) if (harga is not None and not pd.isna(harga)) else None
#                     })

#             # case: list of dicts (yang di-return get_menu_from_db normal)
#             elif isinstance(menu_items[0], dict):
#                 for it in menu_items:
#                     # harga sudah diset di get_menu_from_db sebagai 'harga'
#                     harga = it.get("harga")
#                     # jika harga None skip
#                     if harga is None or pd.isna(harga):
#                         continue
#                     normalized.append({
#                         "kategori": it.get("kategori"),
#                         "nama": it.get("nama"),
#                         "keterangan": it.get("keterangan", ""),
#                         "harga": int(harga)
#                     })

#         # jika tidak ada menu (kosong / semua harga None)
#         if not normalized:
#             st.info("Tidak ada menu yang tersedia untuk cabang ini.")
#             return

#         menu_items = normalized

#         # Ambil daftar kategori dari menu_items yang sudah difilter
#         categories = sorted({item["kategori"] for item in menu_items if item.get("kategori") is not None})

#         # Jika tidak ada kategori (safety)
#         if not categories:
#             st.info("Tidak ada kategori menu untuk ditampilkan.")
#             return
    
#         search_query = st.text_input("🔍 Cari menu", "").strip().lower()

#         if "order_items" not in st.session_state:
#             st.session_state.order_items = {}
    
#         st.markdown("*Pilih menu*")
    
#         if search_query:  
#             filtered_items = [item for item in menu_items if search_query in item['nama'].lower()]
#             if not filtered_items:
#                 st.info("Menu tidak ditemukan")
#             for item in filtered_items:
#                 key = f"{selected_branch}_{item['nama']}"
#                 old_qty = st.session_state.order_items.get(item['nama'], 0)
#                 qty = st.number_input(
#                     f"{item['nama']} (Rp {item['harga']:,})",
#                     min_value=0,
#                     value=old_qty,
#                     step=1,
#                     key=key
#                 )
#                 st.session_state.order_items[item['nama']] = qty
#         else:  
#             tabs = st.tabs(categories)
#             for i, cat in enumerate(categories):
#                 with tabs[i]:
#                     cat_items = [item for item in menu_items if item["kategori"] == cat]
#                     for item in cat_items:
#                         key = f"{selected_branch}_{item['nama']}"
#                         old_qty = st.session_state.order_items.get(item['nama'], 0)
#                         qty = st.number_input(
#                             f"{item['nama']} (Rp {item['harga']:,})",
#                             min_value=0,
#                             value=old_qty,
#                             step=1,
#                             key=key
#                         )
#                         st.session_state.order_items[item['nama']] = qty
    
#         # Hitung total
#         checkout_total = 0
#         for it, q in st.session_state.order_items.items():
#             if q > 0:
#                 price = next((m['harga'] for m in menu_items if m['nama'] == it), 0)
#                 checkout_total += price * q
    
#         st.session_state.checkout_total = checkout_total
#         st.write(f"*Total sementara: Rp {checkout_total:,}*")
    
#         # Tombol bayar & batal
#         cA, cB = st.columns([1,1])
#         with cA:
#             if st.button("Cek & Bayar"):
#                 if checkout_total == 0:
#                     st.warning("Pilih minimal 1 menu")
#                 else:
#                     st.session_state.redeem_step = 3
#                     st.rerun()
#         with cB:
#             if st.button("Batal / Kembali"):
#                 reset_redeem_state()
#                 st.rerun()
                
#     # ===== Inisialisasi session_state =====
#     if "redeem_step" not in st.session_state:
#         st.session_state.redeem_step = 1
    
#     if "show_success" not in st.session_state:
#         st.session_state.show_success = False
    
#     if "newbal" not in st.session_state:
#         st.session_state.newbal = 0

#     # ===== Step 3: Konfirmasi Pembayaran =====
#     if st.session_state.redeem_step == 3:
#         row = st.session_state.voucher_row
#         code, initial, balance, created_at, nama, no_hp, status, seller, tanggal_penjualan = row
        
#         st.header("Konfirmasi Pembayaran")
#         st.write(f"- Voucher: {code}")
#         st.write(f"- Cabang: {st.session_state.selected_branch}")
#         st.write(f"- Sisa Voucher: Rp {int(balance):,}")
    
#         menu_items = get_menu_from_db(st.session_state.selected_branch)
#         price_map = {item['nama']: item['harga'] for item in menu_items}
    
#         ordered_items = {k:v for k,v in st.session_state.order_items.items() if v > 0}
    
#         if not ordered_items:
#             st.warning("Tidak ada menu yang dipilih.")
#             return
    
#         total = st.session_state.checkout_total
#         saldo = int(balance)
#         shortage = total - saldo if total > saldo else 0
    
#         st.write("Detail pesanan:")
#         for it, q in ordered_items.items():
#             st.write(f"- {it} x{q} — Rp {price_map.get(it,0)*q:,}")
    
#         st.write(f"### Total: Rp {total:,}")
    
#         if shortage > 0:
#             st.error(f"⚠️ Saldo voucher kurang Rp {shortage:,}.")
#             st.info("Sisa total harus dibayar dengan *cash* oleh pembeli.")
#         else:
#             st.success("Saldo voucher mencukupi 🎉")
    
#         cA, cB = st.columns([1,1])
#         with cA:
#             if st.button("Ya, Bayar"):
#                 items_str = ", ".join([f"{k} x{v}" for k,v in ordered_items.items()])
#                 ok, msg, newbal = atomic_redeem(
#                     code, total, st.session_state.selected_branch, items_str
#                 )
#                 if ok:
#                     st.session_state.newbal = newbal
#                     st.session_state.show_success = True
#                 else:
#                     st.error(msg)
#                     st.session_state.redeem_step = 2
#                     st.rerun()
#         with cB:
#             if st.button("Tidak, Kembali"):
#                 st.session_state.redeem_step = 2
#                 st.rerun()
    
#     # ===== Pop-up transaksi berhasil =====
#     if st.session_state.show_success:
#         st.success(f"🎉 TRANSAKSI BERHASIL 🎉\nSisa saldo sekarang: Rp {int(st.session_state.newbal):,}")
#         st.write("Tutup pop-up ini untuk kembali ke awal.")
#         if st.button("Tutup"):
#             reset_redeem_state() 
#             st.session_state.show_success = False
#             st.rerun()

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
                            INSERT INTO seller (nama_seller, no_hp, status)
                            VALUES (:nama, :no_hp, :status)
                        """),
                        {"nama": nama.strip(), "no_hp": nohp.strip(), "status": "not accepted"}
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
    st.header("Edit Voucher")

    # Input pencarian & filter
    col1, col2 = st.columns([2, 1])
    with col1:
        kode_cari = st.text_input("Masukkan kode voucher (opsional)").strip().upper()
    with col2:
        filter_status = st.selectbox(
            "Filter Status",
            ["semua", "inactive", "active", "habis"]
        )

    # Ambil data dari database
    try:
        query = "SELECT * FROM vouchers"
        conditions = []
        params = {}

        # Filter berdasarkan status
        if filter_status != "semua":
            conditions.append("status = :status")
            params["status"] = filter_status

        if kode_cari:
            conditions.append("UPPER(code) LIKE :code")
            params["code"] = f"%{kode_cari}%"

        if conditions:
            query += " WHERE " + " AND ".join(conditions)

        query += " ORDER BY created_at DESC"

        with engine.connect() as conn:
            df_voucher = pd.read_sql(text(query), conn, params=params)

        if df_voucher.empty:
            st.info("Tidak ada voucher sesuai filter/pencarian.")
            st.stop()

        # Format tampilan tabel
        df_voucher_display = df_voucher.copy()
        df_voucher_display["initial_value"] = df_voucher_display["initial_value"].apply(lambda x: "-" if pd.isna(x) else f"Rp {int(x):,}")
        df_voucher_display["balance"] = df_voucher_display["balance"].apply(lambda x: "-" if pd.isna(x) else f"Rp {int(x):,}")
        df_voucher_display["tunai"] = df_voucher_display["tunai"].apply(lambda x: "-" if pd.isna(x) else f"Rp {int(x):,}")

        if "tanggal_penjualan" not in df_voucher_display.columns:
            df_voucher_display["tanggal_penjualan"] = None
        if "tanggal_aktivasi" not in df_voucher_display.columns:
            df_voucher_display["tanggal_aktivasi"] = None

        st.dataframe(
            df_voucher_display[
                ["code", "nama", "no_hp", "status", "seller", "initial_value", "balance", "tanggal_penjualan", "tanggal_aktivasi", "tunai"]
            ],
            use_container_width=True
        )

        # === Jika ada kode voucher yang dicari spesifik ===
        if kode_cari:
            matched = df_voucher[df_voucher["code"].str.upper() == kode_cari]
            if matched.empty:
                st.warning("Kode voucher tidak ditemukan di hasil filter.")
            else:
                v = matched.iloc[0]
                st.markdown("---")
                st.subheader(f"✏️ Edit Voucher: {v['code']}")

                with st.form(key=f"edit_{v['code']}"):
                    nama_in = st.text_input("Nama Pembeli", value=v["nama"] or "")
                    nohp_in = st.text_input("No HP Pembeli", value=v["no_hp"] or "")
                    status_in = st.selectbox(
                        "Status",
                        ["inactive", "active", "soldout"],
                        index=["inactive", "active", "soldout"].index(v["status"] if v["status"] in ["inactive", "active", "soldout"] else "inactive")
                    )

                    tanggal_penjualan_in = st.date_input(
                        "Tanggal Penjualan",
                        value=v["tanggal_penjualan"] if isinstance(v["tanggal_penjualan"], (datetime, date)) else date.today()
                    )

                    tanggal_aktivasi_in = st.date_input(
                        "Tanggal Aktivasi",
                        value=v["tanggal_aktivasi"] if isinstance(v["tanggal_aktivasi"], (datetime, date)) else date.today()
                    )

                    submit_edit = st.form_submit_button("💾 Simpan Perubahan")

                    if submit_edit:
                        try:
                            with engine.begin() as conn2:
                                conn2.execute(
                                    text("""
                                        UPDATE vouchers
                                        SET nama = :nama,
                                            no_hp = :no_hp,
                                            status = :status,
                                            tanggal_penjualan = :tgl_jual,
                                            tanggal_aktivasi = :tgl_aktif
                                        WHERE code = :code
                                    """),
                                    {
                                        "nama": nama_in.strip(),
                                        "no_hp": nohp_in.strip(),
                                        "status": status_in,
                                        "tgl_jual": tanggal_penjualan_in.strftime("%Y-%m-%d"),
                                        "tgl_aktif": tanggal_aktivasi_in.strftime("%Y-%m-%d"),
                                        "code": v["code"]
                                    }
                                )

                            st.success(f"✅ Voucher {v['code']} berhasil diperbarui.")
                            st.rerun()

                        except NameError as e:
                            st.error("❌ Gagal memperbarui data voucher.")
                            st.code(str(e))
    except NameError as e:
        st.error("❌ Gagal memuat data voucher dari database.")
        st.code(str(e))
        
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
            df_display = df_display.rename(columns={"code":"Kode","used_amount":"Jumlah","tanggal_transaksi":"Tanggal_transaksi","branch":"Cabang","items":"Menu", "tunai":"Tunai"})
            st.dataframe(df_display[["Kode","Tanggal_transaksi","Jumlah","Cabang","Menu", "Tunai"]], use_container_width=True)
            st.download_button(f"Download CSV {search_code.strip().upper()}", data=df_to_csv_bytes(df_display), file_name=f"transactions_{search_code.strip().upper()}.csv", mime="text/csv")
    else:
        df_tx["tanggal_transaksi"] = pd.to_datetime(df_tx["tanggal_transaksi"]).dt.date
        df_tx["tunai"] = df_tx["tunai"].apply(lambda x: "-" if pd.isna(x) else f"Rp {int(x):,}")
        df_tx = df_tx.rename(columns={"code":"Kode","used_amount":"Jumlah","tanggal_transaksi":"Tanggal_transaksi","branch":"Cabang","items":"Menu", "tunai":"Tunai"})
        st.dataframe(df_tx[["Kode","Tanggal_transaksi","Jumlah","Cabang","Menu", "Tunai"]], use_container_width=True)
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
            "total_voucher_aktif": len(vouchers[vouchers["status"] == "active"]),
            "total_voucher_inaktif": len(vouchers[vouchers["status"] != "active"]),
            "total_voucher_terpakai": len(transactions["code"].unique()),
            "total_saldo_belum_terpakai": vouchers["balance"].sum(),
            "total_saldo_sudah_terpakai": vouchers["used_value"].sum(),
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

    st.info(
        "Masukkan Nama Seller (sesuai dengan data seller pada voucher), Nama Pembeli, No HP, dan Kode Voucher.\n"
        "Jika voucher belum diassign seller oleh admin → aktivasi ditolak."
    )

    with st.form(key="seller_activation_form"):
        kode = st.text_input("Kode Voucher").strip().upper()
        seller_name_input = st.text_input("Nama Seller (isi sesuai yang tercantum pada voucher)").strip()
        buyer_name_input = st.text_input("Nama Pembeli").strip()
        buyer_phone_input = st.text_input("No HP Pembeli").strip()
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

        try:
            with engine.begin() as conn:
                # Cek apakah voucher ada dan seller cocok
                result = conn.execute(
                    text("""
                        SELECT seller, status FROM vouchers WHERE code = :code
                    """),
                    {"code": kode}
                ).fetchone()

                if not result:
                    st.error("Kode voucher tidak ditemukan.")
                    return

                db_seller, db_status = result

                # Jika voucher belum diassign seller oleh admin
                if not db_seller or db_seller.strip() == "":
                    st.error("Voucher belum diassign ke seller mana pun. Aktivasi ditolak.")
                    return

                # Jika seller input tidak cocok dengan seller di database
                if db_seller.strip().lower() != seller_name_input.lower():
                    st.error(f"Nama seller tidak sesuai. Voucher ini terdaftar untuk seller: **{db_seller}**.")
                    return

                # Jika sudah aktif sebelumnya
                if db_status and db_status.lower() == "active":
                    st.warning("Voucher ini sudah diaktivasi sebelumnya.")
                    return

                # Update data voucher
                conn.execute(
                    text("""
                        UPDATE vouchers
                        SET nama = :nama,
                            no_hp = :no_hp,
                            tanggal_aktivasi = :tgl,
                            status = 'Active'
                        WHERE code = :code
                    """),
                    {
                        "nama": buyer_name_input,
                        "no_hp": buyer_phone_input,
                        "tgl": tanggal_aktivasi,
                        "code": kode,
                    }
                )

            st.success(f"✅ Voucher {kode} berhasil diaktivasi untuk pembeli {buyer_name_input}.")

        except Exception as e:
            st.error("❌ Terjadi kesalahan saat mengupdate data voucher.")
            st.code(str(e))

    st.markdown("---")
    st.info(
        "Note: Setelah berhasil diaktivasi oleh Seller, data akan dikunci (seller tidak bisa mengedit lagi). "
        "Jika perlu koreksi, minta admin untuk ubah data."
    )

    # Bagian lookup cepat
    st.subheader("Cek Status Voucher (Lookup cepat)")
    kode_lookup = st.text_input("Masukkan kode voucher untuk cek status (opsional)").strip().upper()
    if kode_lookup:
        try:
            with engine.connect() as conn:
                v = conn.execute(
                    text("""
                        SELECT code, initial_value, balance, created_at, nama, no_hp, status, seller, tanggal_penjualan, tanggal_aktivasi
                        FROM vouchers
                        WHERE code = :code
                    """),
                    {"code": kode_lookup}
                ).fetchone()

            if not v:
                st.error("Voucher tidak ditemukan.")
            else:
                (
                    code,
                    initial_value,
                    balance,
                    created_at,
                    nama,
                    no_hp,
                    status,
                    seller_db,
                    tanggal_penjualan,
                    tanggal_aktivasi_db,
                ) = v

                st.write(f"- **Kode:** {code}")
                st.write(f"- **Seller (di DB):** {seller_db or '-'}")
                st.write(f"- **Status:** {status or 'inactive'}")
                st.write(f"- **Nama pembeli:** {nama or '-'}")
                st.write(f"- **No HP pembeli:** {no_hp or '-'}")
                st.write(f"- **Tgl penjualan:** {tanggal_penjualan or '-'}")
                st.write(f"- **Tgl aktivasi:** {tanggal_aktivasi_db or '-'}")

        except Exception as e:
            st.error("❌ Gagal melakukan lookup voucher.")
            st.code(str(e))


# ---------------------------
# Page: Seller (admin-only assign seller) — keep an admin-only page to assign seller to voucher
# ---------------------------
def page_seller_admin_assign():
    tab_kepemilikan, tab_acc = st.tabs(["Kepemilikan Voucher", "Penerimaan Seller"])

    with tab_kepemilikan:
        st.header("🎯 Assign Voucher ke Seller")

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
                    "Pilih Seller untuk diberikan voucher",
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
                st.write(f"**Jumlah Voucher yang Dimiliki:** {len(df_current_voucher)}")

                if not df_current_voucher.empty:
                    st.markdown("**Voucher yang Saat Ini Dimiliki:**")
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
                st.subheader(f"🧾 Pilih Voucher Baru untuk {selected_seller}")

                if df_voucher.empty:
                    st.info("Semua voucher sudah diassign ke seller.")
                else:
                    selected_vouchers = st.multiselect(
                        "Pilih kode voucher yang akan diberikan",
                        df_voucher["code"].tolist()
                    )

                    if st.button("💾 Simpan Assign Voucher") and selected_vouchers:
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

                            st.success(f"✅ {len(selected_vouchers)} voucher berhasil diassign ke seller {selected_seller}.")
                            st.markdown("### 🔍 Voucher yang baru saja diubah:")
                            st.dataframe(df_changed, use_container_width=True)

                        except Exception as e:
                            st.error("❌ Gagal menyimpan assign voucher ke database.")
                            st.code(str(e))

        except Exception as e:
            st.error("❌ Gagal memuat data seller atau voucher.")
            st.code(str(e))

    with tab_acc:
        st.header("🧾 Daftar Calon Seller")
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
# Router
# ---------------------------
# if page == "Penukaran Voucher" or page == "Redeem Voucher":
#     page_redeem()

if page == "Daftar Sebagai Seller":
    page_daftar_seller()

elif page == "Edit Voucher":
    if not st.session_state.admin_logged_in:
        st.error("Hanya admin yang dapat mengakses halaman ini.")
    else:
        page_daftar_voucher()

elif page == "Histori Transaksi":
    if not st.session_state.admin_logged_in:
        st.error("Hanya admin yang dapat mengakses histori transaksi.")
    else:
        page_histori()

elif page == "Kelola Seller":
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






































































