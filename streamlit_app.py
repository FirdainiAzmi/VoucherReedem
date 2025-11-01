# streamlit_app.py
import streamlit as st
import pandas as pd
from datetime import datetime
from sqlalchemy import create_engine, text
from io import BytesIO

# AgGrid + JS helper
from st_aggrid import AgGrid, GridOptionsBuilder, JsCode
from streamlit_javascript import st_javascript

# --------------------
# Config & DB connect
# --------------------
# Pastikan set DB_URL di Streamlit secrets:
# DB_URL = "postgresql://user:pass@host:port/dbname?sslmode=require"
DB_URL = st.secrets["DB_URL"]
engine = create_engine(DB_URL)

# Admin credentials (prefer: put in secrets as dict)
ADMINS = st.secrets.get("ADMINS", {"admin": "admin123"})

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
            # set null status to 'inactive' (one-time idempotent)
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
# UI/Session helpers
# --------------------
def ensure_session_state():
    st.session_state.setdefault("redeem_step", 1)
    st.session_state.setdefault("entered_code", "")
    st.session_state.setdefault("voucher_row", None)
    st.session_state.setdefault("selected_branch", None)
    st.session_state.setdefault("order_items", {})
    st.session_state.setdefault("checkout_total", 0)
    st.session_state.setdefault("new_balance", None)
    st.session_state.setdefault("admin_user", None)
    st.session_state.setdefault("editing_code", None)
    st.session_state.setdefault("grid_last_selected", None)

def reset_redeem_state():
    for k in ["redeem_step","entered_code","voucher_row","selected_branch","order_items","checkout_total","new_balance"]:
        if k in st.session_state:
            del st.session_state[k]
    ensure_session_state()

def admin_login(username, password):
    pw = ADMINS.get(username)
    if pw is None:
        return False
    return pw == password

def admin_logout():
    st.session_state.admin_user = None
    st.session_state.editing_code = None
    st.session_state.grid_last_selected = None

# --------------------
# Init
# --------------------
init_db()
ensure_session_state()
st.set_page_config(page_title="Voucher Admin", layout="wide")
st.title("🎫 Voucher Admin")

# --------------------
# Sidebar (login + nav)
# --------------------
with st.sidebar:
    st.markdown("## Menu")
    if st.session_state.admin_user:
        st.success(f"Logged in as **{st.session_state.admin_user}**")
        if st.button("Logout"):
            admin_logout()
            st.rerun()
        st.markdown("---")
    else:
        st.markdown("### Admin Login")
        user_in = st.text_input("Username", value="")
        pass_in = st.text_input("Password", type="password", value="")
        if st.button("Login"):
            if admin_login(user_in, pass_in):
                st.session_state.admin_user = user_in
                st.success("Login berhasil")
                st.rerun()
            else:
                st.error("Login gagal — cek username/password")
        st.markdown("---")
        st.info("Catatan: Halaman Cari & Redeem tetap bisa diakses tanpa login.")

# Sidebar single nav
if st.session_state.admin_user:
    menu_options = ["Cari & Redeem", "Daftar Voucher", "Histori Transaksi"]
else:
    menu_options = ["Cari & Redeem"]
menu = st.sidebar.radio("Pilih halaman", menu_options, index=0)

# --------------------
# Page: Cari & Redeem (public)
# --------------------
if menu == "Cari & Redeem":
    st.header("Cari & Redeem")
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
        else:
            branch_options = ["Sedati", "Tawangsari"]
            if st.session_state.get("selected_branch") not in branch_options:
                st.session_state.selected_branch = branch_options[0]
            selected = st.selectbox("Pilih cabang", branch_options, index=branch_options.index(st.session_state.selected_branch))
            if selected != st.session_state.selected_branch:
                st.session_state.selected_branch = selected
                st.session_state.order_items = {}
                st.session_state.checkout_total = 0
                st.rerun()
            if st.session_state.selected_branch == "Sedati":
                menu_map = {"Nasi Goreng":20000, "Ayam Goreng":25000, "Ikan Bakar":30000, "Es Teh":5000}
            else:
                menu_map = {"Nasi Goreng Spesial":25000, "Bakso Kuah":18000, "Es Jeruk":7000, "Teh Manis":3000}
            st.markdown("**Pilih menu & jumlah**")
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
            ca, cb = st.columns([1,1])
            with ca:
                if st.button("Cek & Bayar"):
                    if total == 0:
                        st.warning("Pilih minimal 1 menu")
                    elif total > int(balance):
                        st.error(f"Saldo tidak cukup. Total: Rp {total:,} — Saldo: Rp {int(balance):,}")
                    else:
                        st.session_state.redeem_step = 3
                        st.rerun()
            with cb:
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
                    reset_redeem_state()
                else:
                    st.error(msg)
                    st.session_state.redeem_step = 2
                    st.rerun()
        with cn:
            if st.button("Tidak, Kembali"):
                st.session_state.redeem_step = 2
                st.rerun()

# --------------------
# Page: Daftar Voucher (admin only) with AgGrid clickable code cell
# --------------------
elif menu == "Daftar Voucher":
    if not st.session_state.admin_user:
        st.error("Hanya admin yang dapat mengakses halaman ini. Silakan login di sidebar.")
    else:
        st.header("Daftar Voucher")
        search = st.text_input("Cari kode (partial)", value="")
        status_filter = st.selectbox("Filter", ["semua","aktif","habis"])
        df = list_vouchers(None if status_filter == "semua" else status_filter, search, limit=5000)
        if df.empty:
            st.info("Tidak ada voucher sesuai filter")
        else:
            df_grid = df.copy()
            df_grid["initial_value_display"] = df_grid["initial_value"].apply(lambda x: f"Rp {int(x):,}")
            df_grid["balance_display"] = df_grid["balance"].apply(lambda x: f"Rp {int(x):,}")
            display_df = df_grid[["code","nama","no_hp","status","initial_value_display","balance_display","created_at"]].rename(columns={
                "initial_value_display":"initial_value",
                "balance_display":"balance"
            })

            # JS code for clickable code cell. It dispatches a CustomEvent streamlit:aggrid with code detail.
            cell_renderer_js = JsCode("""
            class BtnCellRenderer {
              init(params) {
                this.params = params;
                this.eGui = document.createElement('button');
                this.eGui.innerText = params.value;
                this.eGui.style = "background:none;border:none;color:#0b66c3;cursor:pointer;text-decoration:underline;padding:0;font-weight:600";
                this.eGui.addEventListener('click', () => {
                  // send CustomEvent with code detail for streamlit_javascript to catch
                  const ev = new CustomEvent("streamlit:aggrid", { detail: { code: params.value }});
                  window.dispatchEvent(ev);
                });
              }
              getGui() {
                return this.eGui;
              }
              destroy() {}
            }
            """)

            gb = GridOptionsBuilder.from_dataframe(display_df)
            gb.configure_default_column(editable=False, resizable=True)
            gb.configure_column("code", header_name="Kode", cellRenderer=cell_renderer_js, pinned="left", width=120)
            gb.configure_column("nama", header_name="Nama", width=180)
            gb.configure_column("no_hp", header_name="No HP", width=130)
            gb.configure_column("status", header_name="Status", width=90)
            gb.configure_column("initial_value", header_name="Nilai Awal", width=120)
            gb.configure_column("balance", header_name="Saldo", width=120)
            gb.configure_column("created_at", header_name="Dibuat", width=180)
            gb.configure_grid_options(suppressRowClickSelection=True, rowSelection='single')
            gridOptions = gb.build()

            grid_response = AgGrid(
                display_df,
                gridOptions=gridOptions,
                enable_enterprise_modules=False,
                allow_unsafe_jscode=True,
                theme="light"
            )

            # Use streamlit_javascript to catch the custom event fired by the JS cell renderer
            clicked_code = st_javascript("""
            () => {
                return new Promise(resolve => {
                    function handler(e) {
                        // e.detail.code contains the voucher code
                        resolve(e.detail.code);
                        window.removeEventListener("streamlit:aggrid", handler);
                    }
                    window.addEventListener("streamlit:aggrid", handler);
                });
            }
            """)

            if clicked_code:
                # set editing_code (navigates to detail view)
                st.session_state.editing_code = clicked_code
                # store last selected to avoid duplicate triggers
                st.session_state.grid_last_selected = clicked_code
                st.rerun()

            st.download_button("Download CSV", data=df_to_csv_bytes(df), file_name="vouchers.csv", mime="text/csv")

# --------------------
# Page: Detail Voucher (admin) - separate full page
# --------------------
if st.session_state.get("editing_code"):
    if not st.session_state.admin_user:
        st.error("Hanya admin yang dapat mengedit voucher.")
    else:
        code = st.session_state.editing_code
        row = find_voucher(code)
        if row:
            code, initial, balance, created_at, nama, no_hp, status = row
            st.markdown("---")
            st.header(f"Detail Voucher — {code}")
            st.write(f"- Nilai awal: Rp {int(initial):,}")
            st.write(f"- Sisa saldo: Rp {int(balance):,}")
            st.write(f"- Dibuat: {created_at}")
            with st.form(key=f"edit_form_{code}"):
                nama_in = st.text_input("Nama pemilik", value=nama or "")
                nohp_in = st.text_input("No HP pemilik", value=no_hp or "")
                status_in = st.selectbox("Status", ["inactive","active"], index=0 if (status or "inactive")!="active" else 1)
                submitted = st.form_submit_button("Simpan Perubahan")
                cancel = st.form_submit_button("Batal")
                if submitted:
                    if status_in == "active" and (not nama_in.strip() or not nohp_in.strip()):
                        st.error("Untuk mengaktifkan voucher, isi Nama dan No HP terlebih dahulu.")
                    else:
                        ok = update_voucher_detail(code, nama_in.strip() or None, nohp_in.strip() or None, status_in)
                        if ok:
                            st.success("Perubahan tersimpan ✅")
                            st.session_state.editing_code = None
                            st.session_state.grid_last_selected = None
                            st.rerun()
                elif cancel:
                    st.session_state.editing_code = None
                    st.session_state.grid_last_selected = None
                    st.rerun()

# --------------------
# Page: Histori Transaksi (admin)
# --------------------
if menu == "Histori Transaksi":
    if not st.session_state.admin_user:
        st.error("Hanya admin yang dapat mengakses histori transaksi.")
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
