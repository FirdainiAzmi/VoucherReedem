# 🎫 Aplikasi Manajemen Voucher Pawon Sappitoe
**Aplikasi Manajemen Voucher Pawon Sappitoe** adalah aplikasi berbasis Streamlit yang dirancang untuk membantu digitalisasi sistem pengelolaan voucher pada warung makan. Tujuan utama aplikasi ini adalah untuk memberikan solusi praktis bagi warung dalam mengatur transaksi menggunakan voucher digital, mulai dari proses aktivasi, penggunaan, hingga pelaporan, semuanya dapat dilakukan dalam satu platform terpusat. 

Aplikasi ini menggunakan PostgreSQL sebagai basis data utama untuk memastikan semua pengguna (admin, kasir, dan seller) memiliki akses terhadap data yang sinkron dan real-time, sehingga setiap perubahan pada saldo, transaksi, atau status voucher akan langsung tercermin di seluruh sistem. Dengan antarmuka berbasis Streamlit, aplikasi ini menawarkan pengalaman pengguna yang sederhana namun interaktif. Semua proses mulai dari login, input voucher, pemilihan menu, hingga laporan transaksi dapat dilakukan melalui antarmuka web tanpa perlu instalasi tambahan di sisi klien.

> 🔹 Proyek ini dibuat sebagai implementasi praktis dari konsep multi-role system, database integration, dan state management di Streamlit, dengan fokus pada efisiensi transaksi berbasis voucher.

## 🔧 Fitur Utama
Aplikasi ini memiliki sistem multi-role dengan fungsi yang berbeda untuk setiap jenis pengguna: Kasir (User), Admin, dan Seller. Setiap fitur dirancang untuk saling terhubung melalui database PostgreSQL agar seluruh data tetap sinkron, konsisten, dan real-time.

### 🧾 1. Fitur untuk Admin
Admin memiliki kontrol penuh terhadap sistem dan berperan dalam pengelolaan seluruh data voucher serta aktivitas pengguna lainnya.

**Fitur yang tersedia:**
- 🪪 **Manajemen Voucher:** Membuat, mengedit, menonaktifkan, atau menghapus voucher yang terdaftar.  
- 📊 **Laporan Warung:** Melihat total transaksi, saldo voucher yang terpakai, dan performa penjualan voucher.  
- 🕒 **Riwayat Transaksi (History):** Melacak aktivitas penggunaan voucher oleh pelanggan dan kasir.  
- 👥 **Kelola Seller:** Menambahkan atau menghapus seller yang berwenang menjual dan mengaktifkan voucher.  
- 🧮 **Monitoring Saldo & Status Voucher:** Memastikan voucher aktif memiliki saldo dan validitas yang sesuai.  

---

### 💼 2. Fitur untuk Seller
Seller berfungsi sebagai pihak yang **mengaktivasi dan menjual voucher** ke pelanggan.  
Semua aktivitas seller tercatat di sistem agar admin dapat melakukan pelacakan dan verifikasi.

**Fitur yang tersedia:**
- 🔑 **Aktivasi Voucher:** Mengaktifkan voucher baru sebelum dijual ke pelanggan.  
- 💳 **Pencatatan Penjualan:** Menyimpan data voucher yang telah diaktivasi beserta pembeli.  
- 📅 **Histori Aktivasi:** Melihat daftar voucher yang telah dijual dan status penggunaannya.  

---

### 💰 3. Fitur untuk User (Kasir)
User atau kasir menggunakan aplikasi untuk melakukan transaksi harian dengan pelanggan menggunakan voucher.

**Fitur yang tersedia:**
- 🎟️ **Input Kode Voucher:** Memasukkan kode voucher pelanggan sebelum melakukan transaksi.  
- 🍛 **Pilih Menu & Harga:** Menentukan menu dan jumlah harga yang akan dibayarkan menggunakan saldo voucher.  
- 🔄 **Kalkulasi Otomatis Saldo:** Setelah transaksi, saldo voucher akan otomatis berkurang sesuai total pembelian.  
- 🧾 **Riwayat Pesanan:** Menampilkan daftar transaksi sebelumnya berdasarkan kode voucher pelanggan.  
- 🔐 **Keamanan Input:** Fitur seperti tombol *show/hide password* untuk menjaga privasi pengguna.  

---

### ⚙️ 4. Fitur Tambahan (Global)
Selain fitur utama per-role, aplikasi juga memiliki beberapa fitur pendukung:

- 🌐 **Satu Database Terpusat (PostgreSQL):**  
  Semua data disimpan dalam satu sistem database agar seluruh user memiliki akses real-time terhadap data yang sama.  
- 🖥️ **Antarmuka Interaktif:**  
  Dibangun dengan **Streamlit**, seluruh proses dilakukan melalui web app yang responsif dan mudah digunakan tanpa instalasi tambahan.  
- 📈 **Kemudahan Monitoring:**  
  Admin dapat memantau saldo, transaksi, dan aktivitas penjualan voucher secara langsung dari dashboard.  

---

> ✨ Dengan pembagian fitur berdasarkan peran, aplikasi ini memastikan alur kerja yang terstruktur, transparan, dan efisien untuk seluruh pihak yang terlibat dalam pengelolaan voucher warung makan.
