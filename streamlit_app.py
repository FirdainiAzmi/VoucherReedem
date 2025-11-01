# streamlit_app.py
import streamlit as st
import pandas as pd
from datetime import datetime
from sqlalchemy import create_engine, text
from io import BytesIO

DB_URL = st.secrets["DB_URL"]
engine = create_engine(DB_URL)

# --------------------
# DB Helper
# --------------------
def init_db():
    try:
        with engine.begin() as conn:
            conn.execute(text("""
            CREATE TABLE IF NOT EXISTS vouchers (
                code TEXT PRIMARY KEY,
                initial_value INTEGER NOT NULL,
                balance INTEGER NOT NULL,
                created_at TIMESTAMP NOT NULL,
                nama TEXT,
                no_hp TEXT,
                status TEXT
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
        st.error(f"Init DB error: {e}")
        st.stop()

def find_voucher(code):
    try:
        with engine.connect() as conn:
            row = conn.execute(text("""
                SELECT code, initial_value, balance, created_at, nama, no_hp, status
                FROM vouchers WHERE code = :c
            """), {"c": code}).fetchone()
        return row
    except:
        return None

def update_voucher_detail(code, nama, no_hp, status):
    try:
        with engine.begin() as conn:
            conn.execute(text("""
                UPDATE vouchers
                SET nama=:nama, no_hp=:no_hp, status=:status
                WHERE code=:code
            """), {"nama":nama, "no_hp":no_hp, "status":status, "code":code})
        return True
    except Exception as e:
        st.error(f"Gagal update: {e}")
        return False

def atomic_redeem(code, amount, branch, items):
    try:
        with engine.begin() as conn:
            r = conn.execute(text("SELECT balance FROM vouchers WHERE code=:c FOR UPDATE"), {"c": code}).fetchone()
            if not r:
                return False, "Voucher tidak ditemukan.", None
            balance = r[0]
            if balance < amount:
                return False, "Saldo tidak cukup.", balance
            conn.execute(text("UPDATE vouchers SET balance = balance - :amt WHERE code=:c"),
                         {"amt": amount, "c": code})
            conn.execute(text("""
                INSERT INTO transactions (code, used_amount, used_at, branch, items)
                VALUES (:c, :amt, :now, :branch, :items)
            """), {"c":code, "amt":amount, "now":datetime.utcnow(), "branch":branch, "items":items})
            return True, "", balance - amount
    except Exception as e:
        return False, str(e), None

def list_vouchers(status_filter=None, search=None):
    q = "SELECT code, initial_value, balance, created_at, nama, no_hp, status FROM vouchers"
    cond = []
    params = {}
    if status_filter == "aktif":
        cond.append("status ILIKE 'active'")
    elif status_filter == "habis":
        cond.append("balance = 0")
    if search:
        cond.append("code ILIKE :s")
        params["s"] = f"%{search}%"
    if cond:
        q += " WHERE " + " AND ".join(cond)
    q += " ORDER BY created_at DESC"
    
    with engine.connect() as conn:
        df = pd.read_sql(text(q), conn, params=params)
    df["status"] = df["status"].fillna("inactive")
    return df

def list_transactions():
    try:
        with engine.connect() as conn:
            return pd.read_sql(text("SELECT * FROM transactions ORDER BY used_at DESC"), conn)
    except:
        return pd.DataFrame([])

def df_to_csv_bytes(df):
    buf = BytesIO()
    df.to_csv(buf, index=False)
    buf.seek(0)
    return buf.read()

# --------------------
# UI State
# --------------------
def reset_redeem():
    for k in ["redeem_step","entered_code","voucher_row","selected_branch","order_items","checkout_total"]:
        if k in st.session_state:
            del st.session_state[k]

def init_state():
    st.session_state.setdefault("redeem_step", 1)
    st.session_state.setdefault("order_items", {})

init_db()
st.set_page_config(layout="wide")
init_state()

menu = st.sidebar.radio("Menu", ["Cari & Redeem", "Daftar Voucher", "Histori Transaksi"])


# --- Page: Cari & Redeem ---
if menu == "Cari & Redeem":
    st.header("Redeem Voucher")
    
    if st.session_state.redeem_step == 1:
        st.session_state.entered_code = st.text_input("Kode Voucher", "").strip().upper()
        if st.button("Submit"):
            v = find_voucher(st.session_state.entered_code)
            if v:
                st.session_state.voucher_row = v
                st.session_state.redeem_step = 2
                st.rerun()
            else:
                st.error("Voucher tidak ditemukan")
        st.button("Reset", on_click=reset_redeem)

    elif st.session_state.redeem_step == 2:
        code, initial, balance, created_at, nama, no_hp, status = st.session_state.voucher_row
        st.write(f"Saldo: Rp {balance:,}")
        st.session_state.selected_branch = st.selectbox("Cabang", ["Sedati","Tawangsari"])

        menu_map = {
            "Sedati": {"Nasi Goreng":20000, "Ayam Goreng":25000, "Es Teh":5000},
            "Tawangsari":{"Bakso Kuah":18000, "Nasi Goreng Spesial":25000}
        }[st.session_state.selected_branch]

        total = 0
        order = {}
        for item, price in menu_map.items():
            qty = st.number_input(item, 0, 10, 0, key=item)
            if qty > 0:
                order[item] = qty
                total += price * qty

        st.session_state.order_items = order
        st.session_state.checkout_total = total
        st.write(f"Total: Rp {total:,}")

        if st.button("Bayar") and total > 0:
            st.session_state.redeem_step = 3
            st.rerun()
        st.button("Kembali", on_click=reset_redeem)

    elif st.session_state.redeem_step == 3:
        code, initial, balance, *_ = st.session_state.voucher_row
        total = st.session_state.checkout_total
        branch = st.session_state.selected_branch
        items_str = ", ".join([f"{k} x{v}" for k,v in st.session_state.order_items.items()])
        ok, msg, newbal = atomic_redeem(code, total, branch, items_str)
        if ok:
            st.success(f"✅ Berhasil! Sisa saldo: Rp {newbal:,}")
            if st.button("OK", on_click=reset_redeem):
                st.rerun()
        else:
            st.error(msg)
            st.session_state.redeem_step = 2
            st.rerun()


# --- Page: Daftar Voucher ---
elif menu == "Daftar Voucher":
    st.header("Daftar Voucher")
    search = st.text_input("Cari kode")
    status = st.selectbox("Status", ["semua","aktif","habis"])
    df = list_vouchers(status if status!="semua" else None, search)

    if not df.empty:
        st.dataframe(df[["code","nama","no_hp","status","initial_value","balance"]], width="stretch")
        code_sel = st.selectbox("Pilih kode untuk edit:", ["--"]+df["code"].tolist())
        if code_sel != "--":
            row = df[df["code"]==code_sel].iloc[0]
            st.subheader(f"Edit Voucher: {code_sel}")
            nama = st.text_input("Nama", row["nama"] or "")
            nohp = st.text_input("No HP", row["no_hp"] or "")
            status = st.selectbox("Status", ["inactive","active"], index=(1 if row["status"]=="active" else 0))
            if st.button("Simpan"):
                update_voucher_detail(code_sel, nama, nohp, status)
                st.success("Tersimpan ✅")
                st.rerun()

        st.download_button("Download CSV", df_to_csv_bytes(df), "vouchers.csv")
    else:
        st.info("Data kosong")


# --- Page: Histori Transaksi ---
else:
    st.header("Histori Transaksi")
    tx = list_transactions()
    if not tx.empty:
        st.dataframe(tx, width="stretch")
        st.download_button("Download CSV", df_to_csv_bytes(tx), "transactions.csv")
    else:
        st.info("Belum ada transaksi")
