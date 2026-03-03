"""
=============================================================================
TOAST POS → QUICKBOOKS DESKTOP — DESKTOP APP (GUI)
=============================================================================

Giao diện đồ họa để đồng bộ doanh thu từ Toast POS sang QuickBooks Desktop.
Chỉ cần mở app, điền thông tin, và nhấn Sync.

Yêu cầu:
    pip install pywin32 requests schedule

Chạy:
    python toast_qb_app.py
    hoặc double-click file toast_qb_app.pyw (ẩn console)

=============================================================================
"""

import tkinter as tk
from tkinter import ttk, messagebox, scrolledtext, filedialog
import json
import threading
import logging
import importlib.util
import sys
import os
from pathlib import Path
from datetime import datetime, timedelta
from decimal import Decimal

# ── Đường dẫn ───────────────────────────────────────────────────────────────
APP_DIR = Path(__file__).parent
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))
CONFIG_FILE = APP_DIR / "config.json"
LOG_DIR = APP_DIR / "logs"
LOG_DIR.mkdir(exist_ok=True)


# =============================================================================
# CUSTOM LOG HANDLER → redirect log vào Text widget
# =============================================================================

class TextWidgetHandler(logging.Handler):
    """Gửi log messages vào tkinter Text widget."""

    def __init__(self, text_widget: tk.Text):
        super().__init__()
        self.text_widget = text_widget

    def emit(self, record):
        msg = self.format(record) + "\n"
        # Schedule trên main thread (tkinter không thread-safe)
        self.text_widget.after(0, self._append, msg, record.levelno)

    def _append(self, msg, level):
        self.text_widget.configure(state="normal")
        tag = "info"
        if level >= logging.ERROR:
            tag = "error"
        elif level >= logging.WARNING:
            tag = "warning"
        elif "✅" in msg or "🎉" in msg:
            tag = "success"
        self.text_widget.insert(tk.END, msg, tag)
        self.text_widget.see(tk.END)
        self.text_widget.configure(state="disabled")


# =============================================================================
# MAIN APP
# =============================================================================

class ToastQBApp:
    """Desktop application cho Toast → QuickBooks sync."""

    # Màu sắc chủ đạo
    BG = "#1a1a2e"
    BG2 = "#16213e"
    BG3 = "#0f3460"
    ACCENT = "#e94560"
    ACCENT2 = "#533483"
    TEXT = "#eaeaea"
    TEXT_DIM = "#8892a0"
    SUCCESS = "#00d26a"
    WARNING = "#f8c32a"
    ERROR = "#ff4757"
    ENTRY_BG = "#222640"
    ENTRY_FG = "#f0f0f0"

    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("Toast → QuickBooks Sync")
        self.root.geometry("920x740")
        self.root.minsize(800, 650)
        self.root.configure(bg=self.BG)

        # Icon (nếu có)
        try:
            self.root.iconbitmap(APP_DIR / "icon.ico")
        except Exception:
            pass

        # Config state
        self.config = {}
        self.is_running = False

        self._build_ui()
        self._setup_logging()
        self._load_saved_config()

    # ─── UI LAYOUT ──────────────────────────────────────────────────────────

    def _build_ui(self):
        # Style cho ttk
        style = ttk.Style()
        style.theme_use("clam")
        style.configure("App.TFrame", background=self.BG)
        style.configure("Card.TFrame", background=self.BG2)
        style.configure("App.TLabel", background=self.BG, foreground=self.TEXT,
                         font=("Segoe UI", 10))
        style.configure("Header.TLabel", background=self.BG, foreground=self.TEXT,
                         font=("Segoe UI", 22, "bold"))
        style.configure("SubHeader.TLabel", background=self.BG, foreground=self.TEXT_DIM,
                         font=("Segoe UI", 10))
        style.configure("CardTitle.TLabel", background=self.BG2, foreground=self.ACCENT,
                         font=("Segoe UI", 11, "bold"))
        style.configure("CardLabel.TLabel", background=self.BG2, foreground=self.TEXT_DIM,
                         font=("Segoe UI", 9))
        style.configure("Status.TLabel", background=self.BG, foreground=self.SUCCESS,
                         font=("Segoe UI", 10, "bold"))

        # ── Header ──
        header_frame = ttk.Frame(self.root, style="App.TFrame")
        header_frame.pack(fill="x", padx=24, pady=(18, 4))

        ttk.Label(header_frame, text="🍞 Toast → QuickBooks", style="Header.TLabel").pack(
            side="left"
        )

        self.status_label = ttk.Label(header_frame, text="● Chưa kết nối", style="Status.TLabel")
        self.status_label.pack(side="right", padx=8)

        ttk.Label(self.root, text="Đồng bộ doanh thu hàng ngày tự động",
                  style="SubHeader.TLabel").pack(anchor="w", padx=26)

        # ── Notebook (tabs) ──
        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill="both", expand=True, padx=20, pady=(12, 6))

        # Tab 1: Cài đặt
        self.tab_settings = ttk.Frame(self.notebook, style="App.TFrame")
        self.notebook.add(self.tab_settings, text="  ⚙  Cài đặt  ")

        # Tab 2: Đồng bộ
        self.tab_sync = ttk.Frame(self.notebook, style="App.TFrame")
        self.notebook.add(self.tab_sync, text="  🔄  Đồng bộ  ")

        # Tab 3: Log
        self.tab_log = ttk.Frame(self.notebook, style="App.TFrame")
        self.notebook.add(self.tab_log, text="  📋  Nhật ký  ")

        self._build_settings_tab()
        self._build_sync_tab()
        self._build_log_tab()

        # ── Footer ──
        footer = ttk.Frame(self.root, style="App.TFrame")
        footer.pack(fill="x", padx=24, pady=(0, 10))
        ttk.Label(footer, text="Toast POS → QuickBooks Desktop Enterprise  •  v2.0",
                  style="SubHeader.TLabel").pack(side="left")

    # ── TAB: CÀI ĐẶT ────────────────────────────────────────────────────────

    def _build_settings_tab(self):
        canvas = tk.Canvas(self.tab_settings, bg=self.BG, highlightthickness=0)
        scrollbar = ttk.Scrollbar(self.tab_settings, orient="vertical", command=canvas.yview)
        scroll_frame = ttk.Frame(canvas, style="App.TFrame")

        scroll_frame.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=scroll_frame, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)

        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        # Mousewheel scroll
        def _on_mousewheel(event):
            canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
        canvas.bind_all("<MouseWheel>", _on_mousewheel)

        # ── Toast API Card ──
        self.entries = {}
        toast_card = self._make_card(scroll_frame, "🌐  Toast API Credentials", row=0)
        self._make_field(toast_card, "Client ID", "toast_client_id", row=0)
        self._make_field(toast_card, "Client Secret", "toast_client_secret", row=1, show="•")
        self._make_field(toast_card, "Restaurant GUID", "toast_restaurant_guid", row=2)
        self._make_field(toast_card, "API Hostname", "toast_api_hostname", row=3,
                         default="https://ws-api.toasttab.com")

        # ── QuickBooks Card ──
        qb_card = self._make_card(scroll_frame, "📚  QuickBooks Desktop", row=1)
        self._make_field(qb_card, "App Name", "qb_app_name", row=0, default="Toast POS Sync")
        self._make_field(qb_card, "Company File (để trống = mặc định)", "qb_company_file", row=1)
        self._make_field(qb_card, "Customer Name (Sold To)", "qb_customer_name", row=2,
                         default="Toast")
        self._make_field(qb_card, "Sale No. Prefix", "qb_sale_prefix", row=3, default="")

        # ── Item Mapping Card ──
        item_card = self._make_card(scroll_frame, "🏷️  Item Mapping (khớp QB Item List)", row=2)
        mapping_items = [
            ("Food Sales item", "item_sales_revenue", "Food Sales"),
            ("Bar Sales item", "item_bar_sales", "Bar Sales"),
            ("Discount item", "item_discounts", "Discount/comp/error Adjustment"),
            ("Refund item", "item_refunds", "Discount/comp/error Adjustment:Refunds"),
            ("Sales Tax item", "item_sales_tax", "boe"),
            ("Tips item", "item_tips", "Tips Paid Out"),
            ("Service Charge item", "item_service_charges", "Customer Contribution"),
            ("Credit Card item", "item_credit_card", "CC25"),
            ("Cash item", "item_cash", "Cash"),
            ("Gift Card item", "item_gift_card", "Gift Certificates"),
            ("Over/Short item", "item_over_short", "Over/Short"),
        ]
        for i, (label, key, default) in enumerate(mapping_items):
            self._make_field(item_card, label, key, row=i, default=default)

        # ── Delivery Channels Card ──
        delivery_card = self._make_card(scroll_frame, "🚗  Delivery Channel Items", row=3)
        delivery_items = [
            ("DoorDash item", "item_doordash", "DOORD"),
            ("GrubHub item", "item_grubhub", "GrubH"),
            ("Uber Eats item", "item_uber_eats", "Ube"),
        ]
        for i, (label, key, default) in enumerate(delivery_items):
            self._make_field(delivery_card, label, key, row=i, default=default)

        # ── Schedule Card ──
        sched_card = self._make_card(scroll_frame, "⏰  Tự động", row=4)
        self._make_field(sched_card, "Giờ chạy hàng ngày (HH:MM)", "schedule_time", row=0,
                         default="06:00")

        # ── Buttons ──
        btn_frame = ttk.Frame(scroll_frame, style="App.TFrame")
        btn_frame.grid(row=5, column=0, sticky="ew", padx=12, pady=(14, 20))

        self._make_button(btn_frame, "💾  Lưu cấu hình", self._save_config, side="left")
        self._make_button(btn_frame, "🧪  Test Toast", self._test_toast, side="left",
                          bg=self.ACCENT2)
        self._make_button(btn_frame, "🧪  Test QB", self._test_qb, side="left",
                          bg=self.ACCENT2)
        self._make_button(btn_frame, "📋  Xem Items QB", self._list_items, side="left",
                          bg=self.BG3)

    # ── TAB: ĐỒNG BỘ ────────────────────────────────────────────────────────

    def _build_sync_tab(self):
        frame = ttk.Frame(self.tab_sync, style="App.TFrame")
        frame.pack(fill="both", expand=True, padx=16, pady=12)

        # Sync single date
        card1 = self._make_card(frame, "📅  Đồng bộ theo ngày", row=0)

        date_frame = ttk.Frame(card1, style="Card.TFrame")
        date_frame.grid(row=0, column=0, sticky="ew", padx=8, pady=6)

        ttk.Label(date_frame, text="Ngày (YYYY-MM-DD):", style="CardLabel.TLabel").pack(
            side="left", padx=(0, 8)
        )
        self.date_entry = tk.Entry(date_frame, width=16, font=("Consolas", 11),
                                    bg=self.ENTRY_BG, fg=self.ENTRY_FG,
                                    insertbackground=self.ENTRY_FG, relief="flat")
        yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
        self.date_entry.insert(0, yesterday)
        self.date_entry.pack(side="left", padx=4)

        self._make_button(date_frame, "▶  Sync ngày này", self._sync_date, side="left")

        # Sync range
        card2 = self._make_card(frame, "📆  Đồng bộ khoảng ngày", row=1)

        range_frame = ttk.Frame(card2, style="Card.TFrame")
        range_frame.grid(row=0, column=0, sticky="ew", padx=8, pady=6)

        ttk.Label(range_frame, text="Từ:", style="CardLabel.TLabel").pack(side="left")
        self.start_entry = tk.Entry(range_frame, width=14, font=("Consolas", 11),
                                     bg=self.ENTRY_BG, fg=self.ENTRY_FG,
                                     insertbackground=self.ENTRY_FG, relief="flat")
        self.start_entry.pack(side="left", padx=4)

        ttk.Label(range_frame, text="Đến:", style="CardLabel.TLabel").pack(side="left", padx=(12, 0))
        self.end_entry = tk.Entry(range_frame, width=14, font=("Consolas", 11),
                                   bg=self.ENTRY_BG, fg=self.ENTRY_FG,
                                   insertbackground=self.ENTRY_FG, relief="flat")
        self.end_entry.pack(side="left", padx=4)

        self._make_button(range_frame, "▶  Sync khoảng", self._sync_range, side="left")

        # Quick buttons
        card3 = self._make_card(frame, "⚡  Thao tác nhanh", row=2)
        quick_frame = ttk.Frame(card3, style="Card.TFrame")
        quick_frame.grid(row=0, column=0, sticky="ew", padx=8, pady=6)

        self._make_button(quick_frame, "🔄  Sync hôm qua", self._sync_yesterday, side="left")
        self._make_button(quick_frame, "🕐  Bật tự động hàng ngày", self._toggle_auto,
                          side="left", bg=self.SUCCESS)

        self.auto_label = ttk.Label(card3, text="", style="CardLabel.TLabel")
        self.auto_label.grid(row=1, column=0, sticky="w", padx=12, pady=(0, 6))

    # ── TAB: LOG ─────────────────────────────────────────────────────────────

    def _build_log_tab(self):
        self.log_text = scrolledtext.ScrolledText(
            self.tab_log,
            font=("Consolas", 9),
            bg="#0d1117",
            fg="#c9d1d9",
            insertbackground="#c9d1d9",
            selectbackground=self.ACCENT,
            relief="flat",
            state="disabled",
            wrap="word",
        )
        self.log_text.pack(fill="both", expand=True, padx=8, pady=8)

        # Tags cho màu
        self.log_text.tag_configure("info", foreground="#c9d1d9")
        self.log_text.tag_configure("error", foreground=self.ERROR)
        self.log_text.tag_configure("warning", foreground=self.WARNING)
        self.log_text.tag_configure("success", foreground=self.SUCCESS)

        # Clear button
        btn_frame = ttk.Frame(self.tab_log, style="App.TFrame")
        btn_frame.pack(fill="x", padx=8, pady=(0, 8))
        self._make_button(btn_frame, "🗑  Xóa log", self._clear_log, side="right", bg=self.BG3)

    # ─── UI HELPERS ─────────────────────────────────────────────────────────

    def _make_card(self, parent, title, row):
        """Tạo card container với tiêu đề."""
        outer = ttk.Frame(parent, style="App.TFrame")
        outer.grid(row=row, column=0, sticky="ew", padx=4, pady=6)
        outer.columnconfigure(0, weight=1)

        card = tk.Frame(outer, bg=self.BG2, highlightbackground=self.BG3,
                        highlightthickness=1)
        card.pack(fill="x")
        card.columnconfigure(0, weight=1)

        ttk.Label(card, text=title, style="CardTitle.TLabel").grid(
            row=0, column=0, sticky="w", padx=12, pady=(10, 6), columnspan=2
        )

        inner = ttk.Frame(card, style="Card.TFrame")
        inner.grid(row=1, column=0, sticky="ew", padx=8, pady=(0, 10))
        inner.columnconfigure(1, weight=1)
        return inner

    def _make_field(self, parent, label, key, row, default="", show=""):
        """Tạo label + entry field."""
        ttk.Label(parent, text=label, style="CardLabel.TLabel").grid(
            row=row, column=0, sticky="w", padx=(8, 12), pady=3
        )
        entry = tk.Entry(parent, font=("Consolas", 10), bg=self.ENTRY_BG, fg=self.ENTRY_FG,
                          insertbackground=self.ENTRY_FG, relief="flat",
                          show=show if show else "")
        entry.grid(row=row, column=1, sticky="ew", padx=(0, 8), pady=3, ipady=3)
        if default:
            entry.insert(0, default)
        self.entries[key] = entry

    def _make_button(self, parent, text, command, side="left", bg=None):
        """Tạo button đẹp."""
        bg = bg or self.ACCENT
        btn = tk.Button(
            parent, text=text, command=command,
            font=("Segoe UI", 9, "bold"),
            bg=bg, fg="white", activebackground=self.ACCENT2,
            activeforeground="white", relief="flat", cursor="hand2",
            padx=14, pady=5,
        )
        btn.pack(side=side, padx=4, pady=4)
        return btn

    # ─── CONFIG SAVE / LOAD ─────────────────────────────────────────────────

    def _get_entry(self, key, fallback=""):
        e = self.entries.get(key)
        return e.get().strip() if e else fallback

    def _build_config_dict(self) -> dict:
        """Xây dựng config dict từ UI entries."""
        return {
            "toast": {
                "client_id": self._get_entry("toast_client_id"),
                "client_secret": self._get_entry("toast_client_secret"),
                "restaurant_guid": self._get_entry("toast_restaurant_guid"),
                "api_hostname": self._get_entry("toast_api_hostname",
                                                 "https://ws-api.toasttab.com"),
            },
            "quickbooks": {
                "app_name": self._get_entry("qb_app_name", "Toast POS Sync"),
                "company_file": self._get_entry("qb_company_file"),
                "qbxml_version": "13.0",
                "customer_name": self._get_entry("qb_customer_name", "Toast"),
                "sale_no_prefix": self._get_entry("qb_sale_prefix"),
            },
            "category_rules": {
                "menu_group_to_category": {
                    "appet": "food", "entree": "food", "entre": "food",
                    "soup": "food", "salad": "food", "sandwich": "food",
                    "burger": "food", "pizza": "food", "pasta": "food",
                    "dessert": "food", "side": "food", "breakfast": "food",
                    "lunch": "food", "dinner": "food", "kids": "food",
                    "seafood": "food", "steak": "food", "chicken": "food",
                    "pho": "food", "rice": "food", "noodle": "food",
                    "beer": "bar", "wine": "bar", "cocktail": "bar",
                    "spirit": "bar", "liquor": "bar", "drink": "bar",
                    "bar": "bar", "sake": "bar", "soju": "bar",
                },
                "source_to_channel": {
                    "in store": "in_store", "pos": "in_store",
                    "online": "online", "toast online": "online",
                    "doordash": "doordash", "door dash": "doordash",
                    "uber": "uber_eats",
                    "grubhub": "grubhub", "grub hub": "grubhub",
                },
                "default_category": "food",
                "default_channel": "in_store",
            },
            "item_mapping": {
                "sales_revenue": self._get_entry("item_sales_revenue", "Food Sales"),
                "discounts": self._get_entry("item_discounts",
                                              "Discount/comp/error Adjustment"),
                "refunds": self._get_entry("item_refunds",
                                            "Discount/comp/error Adjustment:Refunds"),
                "sales_tax": self._get_entry("item_sales_tax", "boe"),
                "tips": self._get_entry("item_tips", "Tips Paid Out"),
                "service_charges": self._get_entry("item_service_charges",
                                                    "Customer Contribution"),
                "credit_card": self._get_entry("item_credit_card", "CC25"),
                "cash": self._get_entry("item_cash", "Cash"),
                "gift_card": self._get_entry("item_gift_card", "Gift Certificates"),
                "over_short": self._get_entry("item_over_short", "Over/Short"),
                "category_item_map": {
                    "food": self._get_entry("item_sales_revenue", "Food Sales"),
                    "bar": self._get_entry("item_bar_sales", "Bar Sales"),
                    "other": self._get_entry("item_sales_revenue", "Food Sales"),
                },
                "source_item_map": {
                    "doordash": self._get_entry("item_doordash", "DOORD"),
                    "grubhub": self._get_entry("item_grubhub", "GrubH"),
                    "uber_eats": self._get_entry("item_uber_eats", "Ube"),
                },
            },
            "schedule": {
                "sync_time": self._get_entry("schedule_time", "06:00"),
            },
        }

    def _save_config(self):
        """Lưu config ra file JSON."""
        config = self._build_config_dict()
        try:
            with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(config, f, indent=4, ensure_ascii=False)
            self.config = config
            self._update_status("● Đã lưu config", self.SUCCESS)
            self.logger.info("💾 Đã lưu cấu hình vào config.json")
        except Exception as e:
            messagebox.showerror("Lỗi", f"Không thể lưu config:\n{e}")

    def _load_saved_config(self):
        """Đọc config đã lưu và điền vào UI."""
        if not CONFIG_FILE.exists():
            self._update_status("● Chưa có config — điền thông tin và nhấn Lưu", self.WARNING)
            return

        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                self.config = json.load(f)
        except Exception as e:
            self._update_status(f"● Lỗi đọc config: {e}", self.ERROR)
            return

        c = self.config
        field_map = {
            "toast_client_id":       c.get("toast", {}).get("client_id", ""),
            "toast_client_secret":   c.get("toast", {}).get("client_secret", ""),
            "toast_restaurant_guid": c.get("toast", {}).get("restaurant_guid", ""),
            "toast_api_hostname":    c.get("toast", {}).get("api_hostname",
                                                             "https://ws-api.toasttab.com"),
            "qb_app_name":           c.get("quickbooks", {}).get("app_name", "Toast POS Sync"),
            "qb_company_file":       c.get("quickbooks", {}).get("company_file", ""),
            "qb_customer_name":      c.get("quickbooks", {}).get("customer_name", "Toast"),
            "qb_sale_prefix":        c.get("quickbooks", {}).get("sale_no_prefix", ""),
            "schedule_time":         c.get("schedule", {}).get("sync_time", "06:00"),
        }

        im = c.get("item_mapping", {})
        field_map.update({
            "item_sales_revenue":   im.get("sales_revenue", "Food Sales"),
            "item_bar_sales":       im.get("category_item_map", {}).get("bar", "Bar Sales"),
            "item_discounts":       im.get("discounts", "Discount/comp/error Adjustment"),
            "item_refunds":         im.get("refunds", "Discount/comp/error Adjustment:Refunds"),
            "item_sales_tax":       im.get("sales_tax", "boe"),
            "item_tips":            im.get("tips", "Tips Paid Out"),
            "item_service_charges": im.get("service_charges", "Customer Contribution"),
            "item_credit_card":     im.get("credit_card", "CC25"),
            "item_cash":            im.get("cash", "Cash"),
            "item_gift_card":       im.get("gift_card", "Gift Certificates"),
            "item_over_short":      im.get("over_short", "Over/Short"),
            "item_doordash":        im.get("source_item_map", {}).get("doordash", "DOORD"),
            "item_grubhub":         im.get("source_item_map", {}).get("grubhub", "GrubH"),
            "item_uber_eats":       im.get("source_item_map", {}).get("uber_eats", "Ube"),
        })

        for key, val in field_map.items():
            if key in self.entries:
                entry = self.entries[key]
                entry.delete(0, tk.END)
                entry.insert(0, val or "")

        self._update_status("● Config loaded", self.SUCCESS)
        self.logger.info("📂 Đã load cấu hình từ config.json")

    # ─── LOGGING SETUP ──────────────────────────────────────────────────────

    def _setup_logging(self):
        """Cấu hình logging → Text widget + file."""
        self.logger = logging.getLogger("ToastToQB")
        self.logger.setLevel(logging.INFO)
        # Xóa handler cũ nếu có
        self.logger.handlers.clear()

        # Handler cho Text widget
        text_handler = TextWidgetHandler(self.log_text)
        text_handler.setFormatter(logging.Formatter("%(asctime)s  %(message)s",
                                                     datefmt="%H:%M:%S"))
        self.logger.addHandler(text_handler)

        # Handler cho file
        file_handler = logging.FileHandler(
            LOG_DIR / f"sync_{datetime.now():%Y%m%d}.log", encoding="utf-8"
        )
        file_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
        self.logger.addHandler(file_handler)

    def _update_status(self, text, color=None):
        self.status_label.configure(text=text, foreground=color or self.TEXT)

    def _clear_log(self):
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", tk.END)
        self.log_text.configure(state="disabled")

    # ─── ENGINE HELPERS ─────────────────────────────────────────────────────
    def _load_sync_module(self):
        """Load đúng module toast_to_quickbooks.py trong cùng thư mục app."""
        module_path = APP_DIR / "toast_to_quickbooks.py"
        spec = importlib.util.spec_from_file_location("toast_to_quickbooks_local", module_path)
        if spec is None or spec.loader is None:
            raise ImportError(f"Không thể load module: {module_path}")

        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def _get_engine(self):
        """Tạo sync engine từ config hiện tại."""
        module = self._load_sync_module()
        if not self.config:
            self.config = self._build_config_dict()
        return module.ToastQBSyncEngine(self.config)

    def _run_in_thread(self, func, *args):
        """Chạy hàm trong background thread."""
        if self.is_running:
            messagebox.showwarning("Đang chạy", "Đang có tác vụ đang chạy. Vui lòng đợi.")
            return

        def wrapper():
            self.is_running = True
            self._update_status("● Đang chạy...", self.WARNING)
            self.notebook.select(self.tab_log)  # Chuyển sang tab log
            try:
                func(*args)
            except Exception as e:
                self.logger.error(f"❌ Lỗi: {e}")
            finally:
                self.is_running = False
                self._update_status("● Sẵn sàng", self.SUCCESS)

        t = threading.Thread(target=wrapper, daemon=True)
        t.start()

    # ─── ACTIONS ────────────────────────────────────────────────────────────

    def _test_toast(self):
        """Test kết nối Toast API."""
        self.config = self._build_config_dict()

        def do():
            try:
                module = self._load_sync_module()
                self.logger.info("🧪 Test kết nối Toast API...")
                client = module.ToastAPIClient(self.config)
                client.authenticate()
                self.logger.info("✅ Kết nối Toast API thành công!")
            except Exception as e:
                self.logger.error(f"❌ Toast API lỗi: {e}")

        self._run_in_thread(do)

    def _test_qb(self):
        """Test kết nối QuickBooks."""
        self.config = self._build_config_dict()

        def do():
            try:
                module = self._load_sync_module()
                self.logger.info("🧪 Test kết nối QuickBooks Desktop...")
                client = module.QuickBooksDesktopClient(self.config)
                client.connect()
                self.logger.info("✅ Kết nối QuickBooks Desktop thành công!")
                client.disconnect()
            except Exception as e:
                self.logger.error(f"❌ QuickBooks lỗi: {e}")

        self._run_in_thread(do)

    def _list_items(self):
        """Liệt kê Items từ QuickBooks."""
        self.config = self._build_config_dict()

        def do():
            try:
                engine = self._get_engine()
                engine.list_qb_items()
            except Exception as e:
                self.logger.error(f"❌ Lỗi: {e}")

        self._run_in_thread(do)

    def _sync_date(self):
        """Sync 1 ngày."""
        date_str = self.date_entry.get().strip()
        if not date_str:
            messagebox.showwarning("Thiếu ngày", "Nhập ngày cần sync (YYYY-MM-DD)")
            return

        self.config = self._build_config_dict()

        def do():
            try:
                date = datetime.strptime(date_str, "%Y-%m-%d")
                engine = self._get_engine()
                result = engine.sync_date(date)
                if result.get("success"):
                    self.logger.info("🎉 Đồng bộ hoàn tất!")
                else:
                    self.logger.error(f"Đồng bộ thất bại: {result.get('message')}")
            except ValueError:
                self.logger.error(f"❌ Ngày không hợp lệ: {date_str} (cần YYYY-MM-DD)")
            except Exception as e:
                self.logger.error(f"❌ Lỗi: {e}")

        self._run_in_thread(do)

    def _sync_range(self):
        """Sync khoảng ngày."""
        start_str = self.start_entry.get().strip()
        end_str = self.end_entry.get().strip()
        if not start_str or not end_str:
            messagebox.showwarning("Thiếu ngày", "Nhập ngày bắt đầu và kết thúc")
            return

        self.config = self._build_config_dict()

        def do():
            try:
                start = datetime.strptime(start_str, "%Y-%m-%d")
                end = datetime.strptime(end_str, "%Y-%m-%d")
                engine = self._get_engine()
                results = engine.sync_date_range(start, end)
                success = sum(1 for r in results if r.get("success"))
                self.logger.info(f"🎉 Hoàn tất: {success}/{len(results)} ngày thành công")
            except ValueError as e:
                self.logger.error(f"❌ Ngày không hợp lệ: {e}")
            except Exception as e:
                self.logger.error(f"❌ Lỗi: {e}")

        self._run_in_thread(do)

    def _sync_yesterday(self):
        """Sync hôm qua."""
        self.config = self._build_config_dict()

        def do():
            try:
                engine = self._get_engine()
                result = engine.sync_yesterday()
                if result.get("success"):
                    self.logger.info("🎉 Đồng bộ hôm qua hoàn tất!")
                else:
                    self.logger.error(f"Đồng bộ thất bại: {result.get('message')}")
            except Exception as e:
                self.logger.error(f"❌ Lỗi: {e}")

        self._run_in_thread(do)

    def _toggle_auto(self):
        """Bật/tắt auto sync hàng ngày."""
        self.config = self._build_config_dict()

        if hasattr(self, '_auto_running') and self._auto_running:
            self._auto_running = False
            self.auto_label.configure(text="⏹ Đã tắt tự động")
            self.logger.info("⏹ Đã tắt chế độ tự động")
            return

        import schedule as sched_lib

        sync_time = self._get_entry("schedule_time", "06:00")
        self._auto_running = True
        self.auto_label.configure(text=f"✅ Tự động sync mỗi ngày lúc {sync_time}")
        self.logger.info(f"🕐 Bật tự động sync lúc {sync_time} mỗi ngày")

        def auto_loop():
            try:
                engine = self._get_engine()
                sched_lib.every().day.at(sync_time).do(engine.sync_yesterday)
                while self._auto_running:
                    sched_lib.run_pending()
                    import time
                    time.sleep(30)
            except Exception as e:
                self.logger.error(f"❌ Auto sync lỗi: {e}")
                self._auto_running = False

        t = threading.Thread(target=auto_loop, daemon=True)
        t.start()


# =============================================================================
# ENTRY POINT
# =============================================================================

def main():
    root = tk.Tk()

    # DPI awareness cho Windows
    try:
        from ctypes import windll
        windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass

    app = ToastQBApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
