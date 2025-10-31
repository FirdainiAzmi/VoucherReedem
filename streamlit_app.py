# streamlit_app_postgres.py
import streamlit as st
import pandas as pd
import random, string
from datetime import datetime
from sqlalchemy import create_engine, text
from io import BytesIO

# --------------------
# Database connection
# --------------------
DB_URL = "postgresql://neondb_owner:npg_axNXHM4suSA6@ep-crimson-mountain-a1thvyih-pooler.ap-southeast-1.aws.neon.tech/neondb?sslmode=require&channel_binding=require"
engine = create_engine(DB_URL)

# --------------------
# Database helpers
# --------------------
def init_db():
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

def insert_voucher(code, value):
    with engine.begin() as conn:
        now = datetime.utcnow()
        try:
            conn.execute(text(
                "INSERT INTO vouchers (code, initial_value, balance, created_at) VALUES (:code,:val,:val,:now)"
            ), {"code": code, "val": int(value), "now": now})
            return True
        except:
            return False

def import_vouchers_df(df: pd.DataFrame):
    report = {"total":0, "inserted":0, "dup_file":0, "dup_db":0, "invalid":0}
    seen = set()
    for idx, row in df.iterrows():
        report["total"] += 1
        code_raw = str(row['code']).strip().upper()
        val = int(row['value'])
        if code_raw in seen:
            report["dup_file"] += 1
            continue
        seen.add(code_raw)
        if insert_voucher(code_raw, val):
            report["inserted"] += 1
        else:
            report["dup_db"] += 1
    return report

def find_voucher(code):
    with engine.connect() as conn:
        result = conn.execute(text(
            "SELECT code, initial_value, balance, created_at FROM vouchers WHERE code=:c"
        ), {"c": code})
        return result.fetchone()

def atomic_redeem(code, amount, branch, items):
    with engine.begin() as conn:
        # check balance
        result = conn.execute(text("SELECT balance FROM vouchers WHERE code=:c"), {"c": code}).fetchone()
        if not result:
            return False, "Voucher tidak ditemukan.", None
        balance = result[0]
        if balance < amount:
            return False, f"Saldo tidak cukup (sisa: {balance})", balance
        # deduct
        conn.execute(text("UPDATE vouchers SET balance=balance-:amt WHERE code=:c"), {"amt": amount, "c": code})
        # log transaction
        conn.execute(text(
            "INSERT INTO transactions (code, used_amount, used_at, branch, items) VALUES (:c,:amt,:now,:branch,:items)"
        ), {"c": code, "amt": amount, "now": datetime.utcnow(), "branch": branch, "items": items})
        new_balance = balance - amount
        return True, "Redeem berhasil.", new_balance

def list_vouchers(filter_status=None, search=None):
    q = "SELECT code, initial_value, balance, created_at FROM vouchers"
    clauses = []
    params = {}
    if filter_status == "aktif":
        clauses.append("balance > 0")
    elif filter_status == "habis":
        clauses.append("balance = 0")
    if search:
        clauses.append("code LIKE :search")
        params["search"] = f"%{search}%"
    if clauses:
        q += " WHERE " + " AND ".join(clauses)
    q += " ORDER BY created_at DESC LIMIT 5000"
    with engine.connect() as conn:
        return pd.read_sql(text(q), conn, params=params)

def list_transactions():
    with engine.connect() as conn:
        return pd.read_sql(text("SELECT * FROM transactions ORDER BY used_at DESC LIMIT 5000"), conn)

def df_to_csv_bytes(df: pd.DataFrame):
    buf = BytesIO()
    df.to_csv(buf, index=False)
    buf.seek(0)
    return buf.read()

# --------------------
# Streamlit UI
# --------------------
init_db()
st.set_page_config(page_title="Voucher Admin", layout="wide")
st.title("🎫 Voucher Admin")

menu = st.sidebar.radio("Menu", ["Cari & Redeem","Daftar Voucher","Histori Transaksi"])

# --- Cari & Redeem ---
if menu=="Cari & Redeem":
    code_in = st.text_input("Kode voucher").strip().upper()

    if code_in:  # cek voucher
        v = find_voucher(code_in)
        if not v:
            st.error("Voucher tidak ditemukan")
        else:
            code, initial, balance, created_at = v
            st.write(f"Kode: {code}")
            st.write(f"Saldo awal: Rp {initial:,}")
            st.write(f"Sisa saldo: Rp {balance:,}")
            st.write(f"Dibuat: {created_at}")

            if balance <= 0:
                st.warning("Voucher sudah tidak dapat digunakan, saldo 0. Silakan cek history.")
            else:
                # Pilih cabang
                branch = st.selectbox("Pilih cabang", ["Sedati","Tawangsari"])
                
                # Pilih menu dan jumlah
                menu_items = ["Nasi Goreng","Ayam Goreng","Ikan Bakar","Es Teh"]
                selected_items = []
                qty_dict = {}
                total_amount = 0

                st.write("Pilih menu yang dibeli dan jumlahnya:")
                for item in menu_items:
                    qty = st.number_input(f"{item} (pcs)", min_value=0, value=0, step=1, key=f"{item}_{code}")
                    if qty > 0:
                        selected_items.append(item)
                        qty_dict[item] = qty
                        # harga per menu (sesuaikan)
                        if item=="Nasi Goreng":
                            harga = 20000
                        elif item=="Ayam Goreng":
                            harga = 25000
                        elif item=="Ikan Bakar":
                            harga = 30000
                        elif item=="Es Teh":
                            harga = 5000
                        total_amount += harga * qty

                st.write(f"Total nominal: Rp {total_amount:,}")

                if st.button("Redeem", key=f"redeem_{code}"):
                    if total_amount == 0:
                        st.warning("Silakan pilih minimal 1 menu")
                    else:
                        items_str = ", ".join([f"{k} x{v}" for k,v in qty_dict.items()])
                        ok,msg,newbal = atomic_redeem(code, total_amount, branch, items_str)
                        if ok:
                            st.success(f"{msg} Sisa saldo: Rp {newbal:,}")
                        else:
                            st.error(msg)

# --- Daftar Voucher ---
elif menu=="Daftar Voucher":
    search = st.text_input("Search kode")
    status = st.selectbox("Filter", ["semua","aktif","habis"])
    df = list_vouchers(filter_status=(None if status=="semua" else status), search=search)
    st.dataframe(df)
    st.download_button("Download CSV", data=df_to_csv_bytes(df), file_name="vouchers.csv")

# --- Histori Transaksi ---
elif menu=="Histori Transaksi":
    df_tx = list_transactions()
    if df_tx.empty:
        st.info("Belum ada transaksi")
    else:
        st.dataframe(df_tx)
        st.download_button("Download CSV", data=df_to_csv_bytes(df_tx), file_name="transactions.csv")
