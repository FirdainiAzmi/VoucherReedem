# streamlit_app.py
import streamlit as st
import pandas as pd
from datetime import datetime
from sqlalchemy import create_engine, text
from io import BytesIO

# AgGrid imports
from st_aggrid import AgGrid, GridOptionsBuilder, JsCode

# --------------------
# Config & DB connect
# --------------------
# Pastikan set DB_URL di Streamlit secrets:
# DB_URL = "postgresql://user:pass@host:port/dbname?sslmode=require"
DB_URL = st.secrets["DB_URL"]
engine = create_engine(DB_URL)

# Admin credentials (prefer: put in secrets as dict)
# Example in secrets: ADMINS = {"admin1":"pass1","admin2":"pass2"}
ADMINS = st.secrets.get("ADMINS", {"admin": "admin123"})

# --------------------
# Database helpers
# --------------------
def init_db():
    """Create tables if not exists. Convert NULL status -> 'inactive'."""
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
            # set null status to 'inactive'
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
    # ensure status not null
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
    st.session_state.setdefault("admin_user", None)  # username if logged in
    st.session_state.setdefault("editing_code", None) # code being edited
    st.session_state.setdefault("grid_last_selected", None)  # to avoid re-trigger loops

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

# --------------------
# Init
# --------------------
init_db()
ensure_session_state()
st.set_page_config(page_title="Voucher Admin", layout="wide")

# --------------------
# Top UI: header + sidebar login
# --------------------
st.title("🎫 Voucher Admin")

with st.sidebar:
    st.markdown("## Menu")
    # Admin login/logout box
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
        st.info("Catatan: Halaman Cari & Redeem tetap bisa diakses tanpa login (user biasa).")

# Sidebar top-level navigation (single)
if st.session_state.admin_user:
    menu_options = ["Cari & Redeem", "Daftar Voucher", "Histori Transaksi"]
else:
    menu_options = ["Cari & Redeem"]
menu = st.sidebar.radio("Pilih halaman", menu_options, index=0)

# --------------------
# Page: Cari & Redeem (available to all)
# --------------------
if menu == "Cari & Redeem":
    st.header("Cari & Redeem")
    # Step 1
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
    # Step 2: choose branch & items
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
            # menu per branch
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
    # Step 3: confirm & pay
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
            # prepare df for grid: show columns and formatted values
            df_grid = df.copy()
            df_grid["initial_value_display"] = df_grid["initial_value"].apply(lambda x: f"Rp {int(x):,}")
            df_grid["balance_display"] = df_grid["balance"].apply(lambda x: f"Rp {int(x):,}")
            # We'll show: code (as clickable), nama, no_hp, status, initial_value_display, balance_display, created_at
            display_df = df_grid[["code","nama","no_hp","status","initial_value_display","balance_display","created_at"]].rename(columns={
                "initial_value_display":"initial_value",
                "balance_display":"balance"
            })

            # Use AgGrid with a JS cell renderer for the code column that acts like a button.
            # We'll configure grid to NOT select row on general click (suppressRowClickSelection=True).
            # The JS button will explicitly select the row when clicked (so only clicking the code button triggers selection).
            cell_renderer_js = JsCode("""
            class BtnCellRenderer {
              init(params) {
                this.params = params;
                const code = params.value;
                this.eGui = document.createElement('button');
                this.eGui.innerText = code;
                this.eGui.style = "background:none;border:none;color:#0b66c3;cursor:pointer;text-decoration:underline;padding:0;font-weight:600";
                this.onClick = this.onClick.bind(this);
                this.eGui.addEventListener('click', this.onClick);
              }
              onClick(e) {
                // Deselect others, select this row
                params.api.deselectAll();
                params.node.setSelected(true);
              }
              getGui() {
                return this.eGui;
              }
              destroy() {
                this.eGui.removeEventListener('click', this.onClick);
              }
            }
            """)  # noqa: E501

            # Build grid options
            gb = GridOptionsBuilder.from_dataframe(display_df)
            gb.configure_default_column(editable=False, resizable=True)
            # set code column to use the JS renderer
            gb.configure_column("code", header_name="Kode", cellRenderer=cell_renderer_js, pinned="left", width=120)
            gb.configure_column("nama", header_name="Nama", width=180)
            gb.configure_column("no_hp", header_name="No HP", width=130)
            gb.configure_column("status", header_name="Status", width=90)
            gb.configure_column("initial_value", header_name="Nilai Awal", width=120)
            gb.configure_column("balance", header_name="Saldo", width=120)
            gb.configure_column("created_at", header_name="Dibuat", width=180)
            # disable row click selection
            gb.configure_grid_options(suppressRowClickSelection=True, rowSelection='single')
            gridOptions = gb.build()

            grid_response = AgGrid(
                display_df,
                gridOptions=gridOptions,
                enable_enterprise_modules=False,
                allow_unsafe_jscode=True,
                theme="light"
            )

            # When the user clicks the code button, the JS selects the row => selected rows become available here.
            selected = grid_response.get("selected_rows", [])
            if selected:
                sel = selected[0]
                sel_code = sel.get("code")
                # avoid retrigger loops: check last selected stored in session_state
                if st.session_state.get("grid_last_selected") != sel_code:
                    st.session_state["grid_last_selected"] = sel_code
                    # set editing and move to detail view
                    st.session_state.editing_code = sel_code
                    # go to detail page (we keep menu choice same, but show detail area)
                    st.rerun()

            st.download_button("Download CSV", data=df_to_csv_bytes(df), file_name="vouchers.csv", mime="text/csv")

# --------------------
# Page: Detail Voucher (admin) - separate view
# --------------------
# We show detail view if editing_code is set (triggered by AgGrid click)
if st.session_state.get("editing_code"):
    # ensure only show detail when admin on Daftar page or explicitly editing
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
            # Edit form
            with st.form(key=f"edit_form_{code}"):
                nama_in = st.text_input("Nama pemilik", value=nama or "")
                nohp_in = st.text_input("No HP pemilik", value=no_hp or "")
                status_in = st.selectbox("Status", ["inactive","active"], index=0 if (status or "inactive")!="active" else 1)
                submitted = st.form_submit_button("Simpan Perubahan")
                cancel = st.form_submit_button("Batal")
                # handle submit / cancel
                if submitted:
                    if status_in == "active" and (not nama_in.strip() or not nohp_in.strip()):
                        st.error("Untuk mengaktifkan voucher, isi Nama dan No HP terlebih dahulu.")
                    else:
                        ok = update_voucher_detail(code, nama_in.strip() or None, nohp_in.strip() or None, status_in)
                        if ok:
                            st.success("Perubahan tersimpan ✅")
                            # clear editing_code to hide detail view and return to daftar voucher
                            st.session_state.editing_code = None
                            # clear last selection to avoid immediate retrigger
                            st.session_state.grid_last_selected = None
                            st.rerun()
                elif cancel:
                    st.session_state.editing_code = None
                    st.session_state.grid_last_selected = None
                    st.rerun()
        else:
            st.error("Voucher tidak ditemukan.")
            st.session_state.editing_code = None
            st.session_state.grid_last_selected = None

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

            s
