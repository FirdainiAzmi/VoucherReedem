# streamlit_app.py
import streamlit as st
import pandas as pd
from datetime import datetime
from sqlalchemy import create_engine, text
from io import BytesIO

# --------------------
# Database connection
# --------------------
DB_URL = st.secrets["DB_URL"]  # pastikan sudah diset di Streamlit secrets
engine = create_engine(DB_URL)

# --------------------
# Database helpers
# --------------------
def init_db():
    """Pastikan tabel ada. Juga set status NULL -> 'inactive' (sesuai pilihan A)."""
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
            # Set semua NULL status menjadi 'inactive' (pilihan A)
            conn.execute(text("UPDATE vouchers SET status = 'inactive' WHERE status IS NULL"))
    except Exception as e:
        st.error(f"Gagal inisialisasi database: {e}")
        st.stop()

def find_voucher(code):
    try:
        with engine.connect() as conn:
            row = conn.execute(text("""
                SELECT code, initial_value, balance, created_at, nama, no_hp, status
                FROM vouchers
                WHERE code = :c
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
    """Kurangi saldo secara atomik; insert transaksi."""
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
    # visual default
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
# UI State helpers
# --------------------
def reset_redeem_state():
    keys = ["redeem_step","entered_code","voucher_row","selected_branch","order_items","checkout_total","new_balance"]
    for k in keys:
        if k in st.session_state:
            del st.session_state[k]

def ensure_session_state():
    st.session_state.setdefault("redeem_step", 1)
    st.session_state.setdefault("order_items", {})
    st.session_state.setdefault("checkout_total", 0)
    # untuk halaman daftar voucher: edit flow
    st.session_state.setdefault("editing_code", None)  # code yang sedang diedit (None = tidak edit)

# --------------------
# Init & UI
# --------------------
init_db()
st.set_page_config(page_title="Voucher Admin", layout="wide")
ensure_session_state()

st.title("🎫 Voucher Admin")

# Sidebar menu
menu = st.sidebar.radio("Menu", ["Cari & Redeem", "Daftar Voucher", "Histori Transaksi"])

# --------------------
# Page: Cari & Redeem
# --------------------
if menu == "Cari & Redeem":
    st.header("Redeem Voucher")

    # Step 1: input kode
    if st.session_state.redeem_step == 1:
        st.session_state.entered_code = st.text_input("Masukkan kode voucher", value=st.session_state.get("entered_code","")).strip().upper()
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

    # Step 2: show detail, pilih cabang & menu
    elif st.session_state.redeem_step == 2:
        row = st.session_state.voucher_row
        code, initial, balance, created_at, nama, no_hp, status = row
        st.subheader(f"Voucher: {code}")
        st.write(f"- Nilai awal: Rp {int(initial):,}")
        st.write(f"- Sisa saldo: Rp {int(balance):,}")
        st.write(f"- Dibuat: {created_at}")
        st.write(f"- Nama pemilik: {nama or '-'}")
        st.write(f"- No HP: {no_hp or '-'}")
        st.write(f"- Status: {status or 'inactive'}")

        if int(balance) <= 0:
            st.warning("Voucher sudah tidak dapat digunakan karena nilai sudah habis.")
            if st.button("Kembali"):
                reset_redeem_state()
                st.rerun()
        else:
            # pilih cabang
            branch_options = ["Sedati", "Tawangsari"]
            # default
            if st.session_state.get("selected_branch") not in branch_options:
                st.session_state.selected_branch = branch_options[0]
            selected = st.selectbox("Pilih warung yang dikunjungi", branch_options, index=branch_options.index(st.session_state.selected_branch))
            if selected != st.session_state.selected_branch:
                st.session_state.selected_branch = selected
                st.session_state.order_items = {}
                st.session_state.checkout_total = 0
                st.rerun()

            if st.session_state.selected_branch == "Sedati":
                menu_map = {"Nasi Goreng":20000, "Ayam Goreng":25000, "Ikan Bakar":30000, "Es Teh":5000}
            else:
                menu_map = {"Nasi Goreng Spesial":25000, "Bakso Kuah":18000, "Es Jeruk":7000, "Teh Manis":3000}

            st.markdown("*Pilih menu dan jumlahnya*")
            total = 0
            chosen = {}
            for item, price in menu_map.items():
                key = f"qty_{item.replace(' ','_')}_{code}"
                qty = st.number_input(f"{item} (Rp {price:,})", min_value=0, value=st.session_state.order_items.get(item,0), step=1, key=key)
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

    # Step 3: konfirmasi dan bayar
    elif st.session_state.redeem_step == 3:
        row = st.session_state.voucher_row
        code, initial, balance, created_at, nama, no_hp, status = row
        st.subheader("Konfirmasi Pembayaran")
        st.write(f"- Voucher: {code}")
        st.write(f"- Cabang: {st.session_state.selected_branch}")
        st.write(f"- Sisa saldo sebelum: Rp {int(balance):,}")
        st.write("**Detail pesanan:**")
        for it, q in st.session_state.order_items.items():
            # tentukan harga utk tampilan
            if st.session_state.selected_branch == "Sedati":
                prices = {"Nasi Goreng":20000, "Ayam Goreng":25000, "Ikan Bakar":30000, "Es Teh":5000}
            else:
                prices = {"Nasi Goreng Spesial":25000, "Bakso Kuah":18000, "Es Jeruk":7000, "Teh Manis":3000}
            st.write(f"- {it} x{q} — Rp {prices[it]*q:,}")
        st.write(f"### Total: Rp {st.session_state.checkout_total:,}")

        col_y, col_n = st.columns([1,1])
        with col_y:
            if st.button("Ya, Bayar"):
                items_str = ", ".join([f"{k} x{v}" for k,v in st.session_state.order_items.items()])
                ok, msg, newbal = atomic_redeem(code, st.session_state.checkout_total, st.session_state.selected_branch, items_str)
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

    # Step 4: sukses -> OK kembali ke awal (harus klik OK)
    elif st.session_state.redeem_step == 4:
        st.success("🎉 TRANSAKSI BERHASIL 🎉", icon="✅")
        st.write(f"Sisa saldo sekarang: Rp {int(st.session_state.new_balance):,}")
        st.markdown("---")
        st.subheader("✅ Klik OK untuk kembali ke halaman awal")
        if st.button("OK"):
            reset_redeem_state()
            st.rerun()

# --------------------
# Page: Daftar Voucher (Admin) — EDIT via tombol per kode
# --------------------
elif menu == "Daftar Voucher":
    st.header("Daftar Voucher")
    search = st.text_input("Cari kode (partial)", value="")
    status_filter = st.selectbox("Filter", ["semua","aktif","habis"])
    df = list_vouchers(None if status_filter=="semua" else status_filter, search, limit=5000)

    if df.empty:
        st.info("Tidak ada voucher sesuai filter")
    else:
        # show table (read-only)
        df_display = df.copy()
        df_display["initial_value"] = df_display["initial_value"].apply(lambda x: f"Rp {int(x):,}")
        df_display["balance"] = df_display["balance"].apply(lambda x: f"Rp {int(x):,}")
        st.dataframe(df_display[["code","nama","no_hp","status","initial_value","balance","created_at"]], width="stretch")

        st.markdown("**Klik tombol Edit pada kode voucher untuk membuka halaman detail edit.**")

        # Render list with per-row Edit button
        # We'll show at most first 200 rows to avoid UI overload; user can search/filter to find specific voucher
        max_rows_shown = min(len(df), 200)
        for idx in range(max_rows_shown):
            row = df.iloc[idx]
            c_code = row["code"]
            c_nama = row.get("nama") or "-"
            c_hp = row.get("no_hp") or "-"
            c_status = row.get("status") or "inactive"
            c_initial = int(row["initial_value"])
            c_balance = int(row["balance"])
            col1, col2, col3, col4 = st.columns([2,3,3,2])
            col1.write(f"**{c_code}**")
            col2.write(f"{c_nama}")
            col3.write(f"{c_status.upper()}")
            # Edit button
            if col4.button("Edit", key=f"edit_{c_code}"):
                st.session_state.editing_code = c_code
                st.rerun()

        # If user clicked Edit -> show detail/edit page (separate area)
        if st.session_state.get("editing_code"):
            code = st.session_state.editing_code
            row = find_voucher(code)
            if row:
                code, initial, balance, created_at, nama, no_hp, status = row
                st.markdown("---")
                st.subheader(f"Edit Voucher — {code}")
                st.write(f"- Nilai awal: Rp {int(initial):,}")
                st.write(f"- Sisa saldo: Rp {int(balance):,}")
                st.write(f"- Dibuat: {created_at}")

                with st.form(key=f"form_edit_{code}"):
                    nama_in = st.text_input("Nama pemilik", value=nama or "")
                    nohp_in = st.text_input("No HP pemilik", value=no_hp or "")
                    status_in = st.selectbox("Status", ["inactive","active"], index=0 if (status or "inactive")!="active" else 1)
                    submitted = st.form_submit_button("Simpan Perubahan")
                    if submitted:
                        # If admin chooses to set active, optional: require nama & phone filled (we won't force unless you want)
                        ok = update_voucher_detail(code, nama_in.strip() or None, nohp_in.strip() or None, status_in)
                        if ok:
                            st.success("Perubahan tersimpan ✅")
                            # clear editing state and refresh
                            st.session_state.editing_code = None
                            st.rerun()
            else:
                st.error("Voucher tidak ditemukan (mungkin sudah dihapus).")
                st.session_state.editing_code = None
                st.rerun()

        # export
        st.download_button("Download CSV", data=df_to_csv_bytes(df), file_name="vouchers.csv", mime="text/csv")

# --------------------
# Page: Histori Transaksi
# --------------------
else:
    st.header("Histori Transaksi")
    df_tx = list_transactions(limit=5000)
    if df_tx.empty:
        st.info("Belum ada transaksi")
    else:
        if "used_at" in df_tx.columns:
            try:
                df_tx["used_at"] = pd.to_datetime(df_tx["used_at"])
            except Exception:
                pass
        df_tx = df_tx.rename(columns={"id":"ID","code":"Kode","used_amount":"Jumlah","used_at":"Waktu","branch":"Cabang","items":"Menu"})
        st.dataframe(df_tx, width="stretch")
        st.download_button("Download CSV", data=df_to_csv_bytes(df_tx), file_name="transactions.csv", mime="text/csv")
