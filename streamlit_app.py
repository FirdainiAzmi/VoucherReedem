# streamlit_app.py
import streamlit as st
import pandas as pd
from datetime import datetime
from sqlalchemy import create_engine, text
from io import BytesIO

# --------------------
# Database connection
# --------------------
DB_URL = st.secrets["DB_URL"]
engine = create_engine(DB_URL)

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
    except Exception as e:
        st.error(f"Gagal inisialisasi database: {e}")
        st.stop()

def insert_voucher(code, value):
    try:
        with engine.begin() as conn:
            conn.execute(text("""
                INSERT INTO vouchers (code, initial_value, balance, created_at)
                VALUES (:code, :val, :val, :now)
            """), {"code": code, "val": int(value), "now": datetime.utcnow()})
        return True
    except Exception:
        return False

def find_voucher(code):
    try:
        with engine.connect() as conn:
            r = conn.execute(text("""
                SELECT code, initial_value, balance, created_at
                FROM vouchers
                WHERE code = :c
            """), {"c": code}).fetchone()
            return r
    except Exception as e:
        st.error(f"DB error saat cari voucher: {e}")
        return None

def atomic_redeem(code, amount, branch, items):
    """Kurangi saldo atomik dan simpan transaksi. Return (ok, msg, new_balance)."""
    try:
        with engine.begin() as conn:
            r = conn.execute(text("SELECT balance FROM vouchers WHERE code = :c FOR UPDATE"), {"c": code}).fetchone()
            if not r:
                return False, "Voucher tidak ditemukan.", None
            balance = r[0]
            if balance < amount:
                return False, f"Saldo tidak cukup (sisa: {balance})", balance
            conn.execute(text("UPDATE vouchers SET balance = balance - :amt WHERE code = :c"),
                         {"amt": amount, "c": code})
            conn.execute(text("""
                INSERT INTO transactions (code, used_amount, used_at, branch, items)
                VALUES (:c, :amt, :now, :branch, :items)
            """), {"c": code, "amt": amount, "now": datetime.utcnow(), "branch": branch, "items": items})
            return True, "Redeem berhasil.", balance - amount
    except Exception as e:
        return False, f"DB error saat redeem: {e}", None

def list_vouchers(filter_status=None, search=None):
    q = "SELECT code, initial_value, balance, created_at FROM vouchers"
    clauses = []
    params = {}
    if filter_status == "aktif":
        clauses.append("balance > 0")
    elif filter_status == "habis":
        clauses.append("balance = 0")
    if search:
        clauses.append("code ILIKE :search")
        params["search"] = f"%{search}%"
    if clauses:
        q += " WHERE " + " AND ".join(clauses)
    q += " ORDER BY created_at DESC LIMIT 5000"
    try:
        with engine.connect() as conn:
            return pd.read_sql(text(q), conn, params=params)
    except Exception as e:
        st.error(f"Gagal ambil daftar voucher: {e}")
        return pd.DataFrame([])

def list_transactions():
    try:
        with engine.connect() as conn:
            return pd.read_sql(text("SELECT * FROM transactions ORDER BY used_at DESC LIMIT 5000"), conn)
    except Exception as e:
        st.error(f"Gagal ambil histori: {e}")
        return pd.DataFrame([])

def df_to_csv_bytes(df: pd.DataFrame):
    buf = BytesIO()
    df.to_csv(buf, index=False)
    buf.seek(0)
    return buf.read()

# --------------------
# Helper UI / state
# --------------------
def reset_redeem_state():
    for k in ["redeem_step", "entered_code", "voucher_row", "selected_branch", "order_items", "checkout_total"]:
        if k in st.session_state:
            del st.session_state[k]

def ensure_session_state():
    if "redeem_step" not in st.session_state:
        st.session_state.redeem_step = 1
    if "entered_code" not in st.session_state:
        st.session_state.entered_code = ""
    if "voucher_row" not in st.session_state:
        st.session_state.voucher_row = None
    if "selected_branch" not in st.session_state:
        st.session_state.selected_branch = None
    if "order_items" not in st.session_state:
        st.session_state.order_items = {}
    if "checkout_total" not in st.session_state:
        st.session_state.checkout_total = 0

# --------------------
# Init & UI
# --------------------
init_db()
st.set_page_config(page_title="Voucher Admin", layout="wide")
st.title("🎫 Voucher Admin")

ensure_session_state()

menu = st.sidebar.radio("Menu", ["Cari & Redeem", "Daftar Voucher", "Histori Transaksi"])

# --------- Redeem Flow ----------
if menu == "Cari & Redeem":
    st.header("Redeem Voucher")

    # Step 1: input kode & submit
    if st.session_state.redeem_step == 1:
        st.session_state.entered_code = st.text_input("Masukkan kode voucher", value=st.session_state.entered_code).strip().upper()
        col1, col2 = st.columns([1,1])
        with col1:
            if st.button("Submit Kode"):
                code = st.session_state.entered_code
                if not code:
                    st.error("Kode tidak boleh kosong")
                else:
                    row = find_voucher(code)
                    if not row:
                        st.error("❌ Voucher tidak ditemukan. Silakan cek kembali nomor voucher.", icon="🚫")
                    else:
                        st.session_state.voucher_row = row
                        st.session_state.redeem_step = 2
                        st.rerun()
        with col2:
            if st.button("Reset"):
                reset_redeem_state()
                st.rerun()
    # Step 2: Pilih cabang
    # Step 2: show voucher detail + pick branch + pick menu (menu berbeda per cabang)
    elif st.session_state.redeem_step == 2:
        row = st.session_state.voucher_row
        code, initial, balance, created_at = row
        st.subheader(f"Voucher: {code}")
        st.write(f"- Nilai awal: Rp {int(initial):,}")
        st.write(f"- Sisa saldo: Rp {int(balance):,}")
        st.write(f"- Dibuat: {created_at}")

        if int(balance) <= 0:
            st.warning("Voucher sudah tidak dapat digunakan karena nilai sudah habis.")
            if st.button("Kembali"):
                reset_redeem_state()
                st.rerun()
        else:
            branch_options = ["Sedati", "Tawangsari"]
            
            # Default branch saat pertama kali
            if st.session_state.selected_branch not in branch_options:
                st.session_state.selected_branch = branch_options[0]

            selected = st.selectbox(
                "Pilih warung yang dikunjungi",
                branch_options,
                index=branch_options.index(st.session_state.selected_branch)
            )

            # Update branch bila user mengganti pilihan
            if selected != st.session_state.selected_branch:
                st.session_state.selected_branch = selected
                st.session_state.order_items = {}
                st.session_state.checkout_total = 0
                st.rerun()

            # menu per cabang
            if st.session_state.selected_branch == "Sedati":
                menu_map = {
                    "Nasi Goreng": 20000,
                    "Ayam Goreng": 25000,
                    "Ikan Bakar": 30000,
                    "Es Teh": 5000
                }
            else:  # Tawangsari
                menu_map = {
                    "Nasi Goreng Spesial": 25000,
                    "Bakso Kuah": 18000,
                    "Es Jeruk": 7000,
                    "Teh Manis": 3000
                }

            st.markdown("**Pilih menu dan jumlahnya**")

            total = 0
            chosen = {}

            for item, price in menu_map.items():
                key = f"qty_{item.replace(' ','_')}_{code}"
                qty = st.number_input(
                    f"{item} (Rp {price:,})",
                    min_value=0,
                    value=st.session_state.order_items.get(item, 0),
                    step=1
                )
                if qty > 0:
                    chosen[item] = int(qty)
                    total += price * int(qty)

            st.session_state.order_items = chosen
            st.session_state.checkout_total = total

            st.write(f"**Total sementara: Rp {total:,}**")

            col_a, col_b = st.columns([1,1])
            with col_a:
                if st.button("Cek & Bayar"):
                    if total == 0:
                        st.warning("Silakan pilih minimal 1 menu.")
                    elif total > int(balance):
                        st.error(f"Saldo tidak cukup. Total: Rp {total:,} — Saldo: Rp {int(balance):,}")
                    else:
                        st.session_state.redeem_step = 3
                        st.rerun()
            with col_b:
                if st.button("Batal / Kembali"):
                    reset_redeem_state()
                    st.rerun()


    # Step 3: modal konfirmasi & final payment
    elif st.session_state.redeem_step == 3:
        row = st.session_state.voucher_row
        code, initial, balance, created_at = row
        st.subheader("Konfirmasi Pembayaran")
        st.write(f"- Voucher: {code}")
        st.write(f"- Cabang: {st.session_state.selected_branch}")
        st.write(f"- Sisa saldo sebelum: Rp {int(balance):,}")
        st.write("**Detail pesanan:**")
        for it, q in st.session_state.order_items.items():
            if st.session_state.selected_branch == "Sedati":
                prices = {"Nasi Goreng":20000, "Ayam Goreng":25000, "Ikan Bakar":30000, "Es Teh":5000}
            else:
                prices = {"Nasi Goreng Spesial":25000, "Bakso Kuah":18000, "Es Jeruk":7000, "Teh Manis":3000}
            st.write(f"- {it} x{q} — Rp {prices[it]*q:,}")
        
        st.write(f"### Total: Rp {st.session_state.checkout_total:,}")

        col_y, col_n = st.columns([1,1])
        with col_y:
            if st.button("Ya, Bayar ✅"):
                items_str = ", ".join([f"{k} x{v}" for k,v in st.session_state.order_items.items()])
                ok, msg, newbal = atomic_redeem(
                    code, st.session_state.checkout_total,
                    st.session_state.selected_branch, items_str
                )
                if ok:
                    st.session_state.new_balance = newbal
                    st.session_state.redeem_step = 4
                    st.rerun()
                else:
                    st.error(msg)
                    st.session_state.redeem_step = 2
                    st.rerun()

        with col_n:
            if st.button("Tidak, Kembali"):
                st.session_state.redeem_step = 2
                st.rerun()
        # Step 4: Halaman Transaksi Berhasil
    elif st.session_state.redeem_step == 4:
        st.success("🎉 TRANSAKSI BERHASIL 🎉", icon="✅")
        st.write(f"Sisa saldo sekarang: Rp {int(st.session_state.new_balance):,}")

        st.markdown("---")
        st.subheader("✅ Klik OK untuk kembali ke halaman awal")

        if st.button("OK"):
            reset_redeem_state()
            st.rerun()


# --------- Daftar Voucher ----------
elif menu == "Daftar Voucher":
    st.header("Daftar Voucher")
    search = st.text_input("Cari kode (partial)", value="")
    status = st.selectbox("Filter", ["semua", "aktif", "habis"])
    df = list_vouchers(None if status=="semua" else status, search)
    if df.empty:
        st.info("Tidak ada voucher sesuai filter")
    else:
        df_display = df.copy()
        df_display["initial_value"] = df_display["initial_value"].apply(lambda x: f"Rp {int(x):,}")
        df_display["balance"] = df_display["balance"].apply(lambda x: f"Rp {int(x):,}")
        st.dataframe(df_display, width="stretch")
        st.download_button("Download CSV", df_to_csv_bytes(df), file_name="vouchers.csv", mime="text/csv")

# --------- Histori Transaksi ----------
else:
    st.header("Histori Transaksi")
    df_tx = list_transactions()
    if df_tx.empty:
        st.info("Belum ada transaksi")
    else:
        # format waktu dan kolom
        if "used_at" in df_tx.columns:
            try:
                df_tx["used_at"] = pd.to_datetime(df_tx["used_at"])
            except Exception:
                pass
        df_tx = df_tx.rename(columns={"id":"ID","code":"Kode","used_amount":"Jumlah","used_at":"Waktu","branch":"Cabang","items":"Menu"})
        st.dataframe(df_tx, width="stretch")
        st.download_button("Download CSV", df_to_csv_bytes(df_tx), file_name="transactions.csv", mime="text/csv")
