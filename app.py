"""Homebase: a fully local Python desktop inventory application.

Run: python app.py
Check the database without opening the desktop UI: python app.py --check
"""
from __future__ import annotations

import argparse
import base64
import io
import math
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

from inventory import CATEGORIES, LOCATION_TYPES, MAX_PHOTO_BYTES, Inventory, InventoryError

# Python.org desktop installers include Tkinter. Pillow is optional.
try:
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk
except ImportError:
    tk = None

try:
    from PIL import Image, ImageOps, ImageTk
except ImportError:
    Image = ImageOps = ImageTk = None

ROOT = Path(__file__).resolve().parent
FONT = "Helvetica Neue" if sys.platform == "darwin" else "Segoe UI"
GREEN, INK, MUTED, BACKGROUND = "#175b4c", "#243b30", "#748275", "#f4f6f0"


def currency(value):
    return f"${value:,.2f}"


def paint_photo(canvas, data=b"", title="Photo placeholder"):
    """Display an embedded photo, or draw a useful placeholder with no assets."""
    canvas.delete("all")
    canvas.photo_ref = None  # Tk images must stay referenced for their lifetime.
    width = int(canvas.cget("width"))
    height = int(canvas.cget("height"))
    if data:
        try:
            if Image is not None:
                with Image.open(io.BytesIO(data)) as source:
                    image = ImageOps.exif_transpose(source).convert("RGB")
                    image.thumbnail((width - 16, height - 16))
                    picture = ImageTk.PhotoImage(image)
            else:
                picture = tk.PhotoImage(data=base64.b64encode(data).decode("ascii"))
                factor = max(1, math.ceil(picture.width() / (width - 16)), math.ceil(picture.height() / (height - 16)))
                picture = picture.subsample(factor, factor)
            canvas.photo_ref = picture
            canvas.create_image(width // 2, height // 2, image=picture)
            return True
        except Exception:
            title = "Photo saved · preview unavailable"
    x, y = width // 2, height // 2 - 12
    canvas.create_polygon(x - 36, y - 17, x, y - 35, x + 36, y - 17, x, y + 2,
                          fill="#d7e1cc", outline="#8da07e", width=2)
    canvas.create_polygon(x - 36, y - 17, x, y + 2, x, y + 40, x - 36, y + 21,
                          fill="#bdcdb0", outline="#8da07e", width=2)
    canvas.create_polygon(x, y + 2, x + 36, y - 17, x + 36, y + 21, x, y + 40,
                          fill="#a4bd98", outline="#8da07e", width=2)
    canvas.create_line(x - 18, y - 26, x + 18, y - 8, x + 18, y + 11, fill="#7c936e", width=3)
    canvas.create_text(x, height - 17, text=title, fill="#87967b", font=(FONT, 9))
    return not data


def scroll_frame(parent, width=280, padding=0):
    """A scrollable native panel, so forms and item details fit smaller screens."""
    outer = ttk.Frame(parent, width=width)
    viewport = tk.Canvas(outer, width=width, height=300, highlightthickness=0, bg=BACKGROUND)
    bar = ttk.Scrollbar(outer, orient="vertical", command=viewport.yview)
    viewport.configure(yscrollcommand=bar.set)
    viewport.pack(side="left", fill="both", expand=True)
    bar.pack(side="right", fill="y")
    inner = ttk.Frame(viewport, padding=padding)
    window_id = viewport.create_window((0, 0), window=inner, anchor="nw")
    inner.bind("<Configure>", lambda event: viewport.configure(scrollregion=viewport.bbox("all")))
    viewport.bind("<Configure>", lambda event: viewport.itemconfigure(window_id, width=event.width))

    def wheel(event):
        widget = event.widget
        # A Text widget scrolls its own notes; other controls scroll the panel.
        if isinstance(widget, tk.Text):
            return None
        while widget is not None:
            if widget is outer:
                delta = -1 if getattr(event, "num", None) == 4 else 1 if getattr(event, "num", None) == 5 else 0
                if not delta and getattr(event, "delta", 0):
                    delta = -1 if event.delta > 0 else 1
                if delta:
                    viewport.yview_scroll(delta, "units")
                    return "break"
            widget = getattr(widget, "master", None)
        return None

    top = outer.winfo_toplevel()
    sequences = ("<MouseWheel>",)
    if sys.platform.startswith("linux"):
        sequences += ("<Button-4>", "<Button-5>")
    for sequence in sequences:
        top.bind(sequence, wheel, add="+")
    return outer, inner


class ItemDialog:
    """Add/edit form; all input is validated by the Python Inventory class."""

    def __init__(self, app, item_id=None):
        self.app, self.item_id = app, item_id
        self.item = app.inventory.get_item(item_id) if item_id else None
        self.photo = self.item["photo"] if self.item else b""
        self.photo_name = self.item["photo_name"] if self.item else ""
        self.window = tk.Toplevel(app.root)
        self.window.title("Edit item" if self.item else "Add item")
        self.window.transient(app.root)
        self.window.resizable(False, False)
        self.window.configure(bg="#ffffff")
        self.window.grab_set()
        outer, form = scroll_frame(self.window, width=560, padding=24)
        outer.pack(fill="both", expand=True)
        form.columnconfigure(0, weight=1)
        form.columnconfigure(1, weight=1)
        ttk.Label(form, text="Edit item" if self.item else "Add an item", style="Title.TLabel").grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 15))
        photo_row = ttk.Frame(form)
        photo_row.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(0, 10))
        self.preview = tk.Canvas(photo_row, width=125, height=110, bg="#f1f5eb", highlightthickness=0)
        self.preview.pack(side="left", padx=(0, 15))
        buttons = ttk.Frame(photo_row)
        buttons.pack(side="left")
        ttk.Button(buttons, text="Choose photo…", command=self.choose_photo).pack(anchor="w", pady=3)
        ttk.Button(buttons, text="Remove photo", command=self.remove_photo).pack(anchor="w", pady=3)
        ttk.Label(buttons, text="PNG or GIF. JPG/WebP with optional Pillow.\nUp to 10 MB; photo is stored in the database.", style="Hint.TLabel").pack(anchor="w", pady=(6, 0))
        paint_photo(self.preview, self.photo)
        self.values = {}
        initial = self.item or {"name": "", "category": CATEGORIES[0], "quantity": 1, "value": 0, "low_stock": 0}
        for key in ("name", "category", "quantity", "value", "low_stock"):
            self.values[key] = tk.StringVar(value=str(initial[key]))
        self.values["tags"] = tk.StringVar(value=", ".join(self.item["tags"]) if self.item else "")
        ttk.Label(form, text="Item name *").grid(row=2, column=0, columnspan=2, sticky="w", pady=(8, 4))
        self.name_entry = ttk.Entry(form, textvariable=self.values["name"], width=63)
        self.name_entry.grid(row=3, column=0, columnspan=2, sticky="ew")
        ttk.Label(form, text="Category *").grid(row=4, column=0, sticky="w", pady=(12, 4))
        ttk.Label(form, text="Quantity *").grid(row=4, column=1, sticky="w", padx=(14, 0), pady=(12, 4))
        ttk.Combobox(form, textvariable=self.values["category"], values=CATEGORIES, state="readonly", width=25).grid(row=5, column=0, sticky="ew")
        ttk.Spinbox(form, textvariable=self.values["quantity"], from_=0, to=999999, width=25).grid(row=5, column=1, sticky="ew", padx=(14, 0))
        ttk.Label(form, text="Location *").grid(row=6, column=0, columnspan=2, sticky="w", pady=(12, 4))
        locations = app.inventory.locations()
        self.location_ids = {app.inventory.path_text(loc["id"]): loc["id"] for loc in locations}
        location_id = self.item["location_id"] if self.item else app.location_filter or (locations[0]["id"] if locations else "")
        self.location_value = tk.StringVar(value=app.inventory.path_text(location_id) if location_id else "")
        self.location_combo = ttk.Combobox(form, textvariable=self.location_value, values=sorted(self.location_ids), state="readonly")
        self.location_combo.grid(row=7, column=0, columnspan=2, sticky="ew")
        ttk.Label(form, text="Tags (separate with commas)").grid(row=8, column=0, columnspan=2, sticky="w", pady=(12, 4))
        ttk.Entry(form, textvariable=self.values["tags"]).grid(row=9, column=0, columnspan=2, sticky="ew")
        ttk.Label(form, text="Unit value ($)").grid(row=10, column=0, sticky="w", pady=(12, 4))
        ttk.Label(form, text="Low stock at").grid(row=10, column=1, sticky="w", padx=(14, 0), pady=(12, 4))
        ttk.Entry(form, textvariable=self.values["value"]).grid(row=11, column=0, sticky="ew")
        ttk.Spinbox(form, textvariable=self.values["low_stock"], from_=0, to=999999).grid(row=11, column=1, sticky="ew", padx=(14, 0))
        ttk.Label(form, text="Notes").grid(row=12, column=0, columnspan=2, sticky="w", pady=(12, 4))
        self.notes = tk.Text(form, height=4, width=58, font=(FONT, 10), wrap="word", relief="solid", bd=1, highlightthickness=0)
        self.notes.grid(row=13, column=0, columnspan=2, sticky="ew")
        self.notes.insert("1.0", self.item["notes"] if self.item else "")
        self.error = tk.StringVar()
        ttk.Label(form, textvariable=self.error, style="Error.TLabel", wraplength=510).grid(row=14, column=0, columnspan=2, sticky="w", pady=(10, 0))
        footer = ttk.Frame(form)
        footer.grid(row=15, column=0, columnspan=2, sticky="e", pady=(15, 0))
        ttk.Button(footer, text="Cancel", command=self.window.destroy).pack(side="left", padx=8)
        ttk.Button(footer, text="Save changes" if self.item else "Add item", style="Primary.TButton", command=self.save).pack(side="left")
        self.window.bind("<Escape>", lambda event: self.window.destroy())
        self.window.bind("<Control-Return>", lambda event: self.save())
        if sys.platform == "darwin":
            self.window.bind("<Command-Return>", lambda event: self.save())
        self.window.update_idletasks()
        height = min(form.winfo_reqheight(), self.window.winfo_screenheight() - 100)
        self.window.geometry(f"600x{max(420, height)}")
        app.center_dialog(self.window)
        self.name_entry.focus_set()

    def choose_photo(self):
        filename = filedialog.askopenfilename(parent=self.window, title="Choose item photo", filetypes=[("Photos", "*.png *.gif *.jpg *.jpeg *.webp"), ("All files", "*.*")])
        if not filename:
            return
        try:
            file = Path(filename)
            if file.stat().st_size > MAX_PHOTO_BYTES:
                raise InventoryError("Choose a photo under 10 MB.")
            data = file.read_bytes()
            if Image is not None:
                with Image.open(io.BytesIO(data)) as image:
                    image.verify()
            else:
                try:
                    tk.PhotoImage(data=base64.b64encode(data).decode("ascii"))
                except tk.TclError:
                    raise InventoryError("Use PNG/GIF, or install optional Pillow for JPG/WebP photos.") from None
            self.photo, self.photo_name = data, file.name
            paint_photo(self.preview, data)
            self.error.set("")
        except Exception as error:
            self.error.set(f"Could not use this photo: {error}")

    def remove_photo(self):
        self.photo, self.photo_name = b"", ""
        paint_photo(self.preview)

    def save(self):
        data = {key: value.get() for key, value in self.values.items()}
        data.update(location_id=self.location_ids.get(self.location_value.get(), ""), notes=self.notes.get("1.0", "end-1c"), photo=self.photo, photo_name=self.photo_name)
        try:
            saved = self.app.inventory.save_item(data, self.item_id)
        except (InventoryError, sqlite3.Error) as error:
            self.error.set(str(error))
            return
        self.window.destroy()
        self.app.clear_filters(refresh=False)
        self.app.selected_id = saved["id"]
        self.app.notebook.select(self.app.inventory_tab)
        self.app.refresh()
        self.app.set_status(f"Saved {saved['name']}")


class LocationDialog:
    def __init__(self, app, location_id=None, parent_id=None):
        self.app, self.location_id = app, location_id
        old = app.inventory.get_location(location_id) if location_id else None
        self.window = tk.Toplevel(app.root)
        self.window.title("Edit location" if old else "Add location")
        self.window.transient(app.root)
        self.window.resizable(False, False)
        self.window.grab_set()
        frame = ttk.Frame(self.window, padding=24)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="Edit location" if old else "Add a location", style="Title.TLabel").pack(anchor="w", pady=(0, 20))
        self.name = tk.StringVar(value=old["name"] if old else "")
        self.kind = tk.StringVar(value=old["kind"] if old else "Storage" if parent_id else "House")
        ttk.Label(frame, text="Location name *").pack(anchor="w", pady=(0, 4))
        entry = ttk.Entry(frame, textvariable=self.name, width=55)
        entry.pack(fill="x")
        ttk.Label(frame, text="Type").pack(anchor="w", pady=(14, 4))
        ttk.Combobox(frame, textvariable=self.kind, values=LOCATION_TYPES, state="readonly").pack(fill="x")
        excluded = app.inventory.descendants(location_id) if location_id else set()
        self.parents = {"No parent (top level)": None}
        self.parents.update({app.inventory.path_text(loc["id"]): loc["id"] for loc in app.inventory.locations() if loc["id"] not in excluded})
        parent = old["parent_id"] if old else parent_id
        self.parent = tk.StringVar(value=app.inventory.path_text(parent) if parent else "No parent (top level)")
        ttk.Label(frame, text="Inside of").pack(anchor="w", pady=(14, 4))
        ttk.Combobox(frame, textvariable=self.parent, values=list(self.parents), state="readonly").pack(fill="x")
        self.path_preview = tk.StringVar()
        ttk.Label(frame, textvariable=self.path_preview, wraplength=440, style="Hint.TLabel").pack(anchor="w", pady=15)
        self.error = tk.StringVar()
        ttk.Label(frame, textvariable=self.error, wraplength=440, style="Error.TLabel").pack(anchor="w")
        footer = ttk.Frame(frame)
        footer.pack(anchor="e", pady=(16, 0))
        ttk.Button(footer, text="Cancel", command=self.window.destroy).pack(side="left", padx=8)
        ttk.Button(footer, text="Save location", style="Primary.TButton", command=self.save).pack(side="left")
        self.name.trace_add("write", self.update_path)
        self.parent.trace_add("write", self.update_path)
        self.update_path()
        self.window.bind("<Escape>", lambda event: self.window.destroy())
        self.window.bind("<Return>", lambda event: self.save())
        app.center_dialog(self.window)
        entry.focus_set()

    def update_path(self, *_):
        parent = "" if self.parents.get(self.parent.get()) is None else self.parent.get() + " → "
        self.path_preview.set(parent + (self.name.get().strip() or "New location"))

    def save(self):
        try:
            loc = self.app.inventory.save_location({"name": self.name.get(), "kind": self.kind.get(), "parent_id": self.parents.get(self.parent.get())}, self.location_id)
        except (InventoryError, sqlite3.Error) as error:
            self.error.set(str(error))
            return
        self.window.destroy()
        self.app.refresh()
        self.app.set_status(f"Saved location: {loc['name']}")


class HomebaseApp:
    def __init__(self, root, inventory):
        self.root, self.inventory = root, inventory
        self.location_filter = None
        self.selected_id = None
        self.deleted_item = None
        self.filtered_items = []
        self._refreshing = False
        self._search_job = None
        root.title("Homebase · Local Inventory")
        root.geometry("1380x850")
        root.minsize(1080, 700)
        root.configure(bg=BACKGROUND)
        root.protocol("WM_DELETE_WINDOW", self.close)
        self.configure_styles()
        self.build_header()
        body = ttk.Panedwindow(root, orient="horizontal")
        body.pack(fill="both", expand=True, padx=20, pady=(0, 12))
        sidebar = ttk.Frame(body, style="Card.TFrame", padding=14, width=220)
        body.add(sidebar, weight=0)
        ttk.Label(sidebar, text="MY LOCATIONS", style="Eyebrow.TLabel").pack(anchor="w", pady=(3, 12))
        ttk.Button(sidebar, text="Show all locations", command=self.clear_location).pack(fill="x", pady=(0, 10))
        location_area = ttk.Frame(sidebar)
        location_area.pack(fill="both", expand=True)
        self.location_tree = ttk.Treeview(location_area, show="tree", selectmode="browse", height=16)
        self.location_tree.column("#0", width=195, minwidth=150, stretch=True)
        scroll = ttk.Scrollbar(location_area, orient="vertical", command=self.location_tree.yview)
        self.location_tree.configure(yscrollcommand=scroll.set)
        self.location_tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        self.location_tree.bind("<<TreeviewSelect>>", self.on_location_select)
        ttk.Label(sidebar, text="Select a place to include\neverything nested inside it.", style="Hint.TLabel").pack(anchor="w", pady=12)
        ttk.Button(sidebar, text="+ Add location", command=self.add_location).pack(fill="x", pady=3)
        ttk.Button(sidebar, text="Edit selected location", command=self.edit_location).pack(fill="x", pady=3)
        main = ttk.Frame(body, padding=(18, 0, 0, 0))
        body.add(main, weight=1)
        self.build_stats(main)
        self.notebook = ttk.Notebook(main)
        self.notebook.pack(fill="both", expand=True, pady=(14, 0))
        self.inventory_tab = ttk.Frame(self.notebook, padding=15)
        self.locations_tab = ttk.Frame(self.notebook, padding=15)
        self.activity_tab = ttk.Frame(self.notebook, padding=15)
        self.notebook.add(self.inventory_tab, text="  Inventory  ")
        self.notebook.add(self.locations_tab, text="  Locations  ")
        self.notebook.add(self.activity_tab, text="  Activity  ")
        self.build_inventory_tab()
        self.build_locations_tab()
        self.build_activity_tab()
        footer = ttk.Frame(root, padding=(22, 8, 22, 12))
        footer.pack(fill="x")
        self.status = tk.StringVar(value=f"Local database: {inventory.path}")
        ttk.Label(footer, textvariable=self.status, style="Hint.TLabel").pack(side="left", fill="x", expand=True)
        self.undo_button = ttk.Button(footer, text="Undo last delete", command=self.undo_delete, state="disabled")
        self.undo_button.pack(side="right")
        root.bind("<Control-n>", lambda event: self.add_item())
        root.bind("<Control-f>", self.focus_search)
        if sys.platform == "darwin":
            root.bind("<Command-n>", lambda event: self.add_item())
            root.bind("<Command-f>", self.focus_search)
        self.refresh()

    def configure_styles(self):
        style = ttk.Style(self.root)
        style.theme_use("clam")
        style.configure(".", font=(FONT, 10), foreground=INK, background=BACKGROUND)
        style.configure("TFrame", background=BACKGROUND)
        style.configure("Card.TFrame", background="#ffffff")
        style.configure("TLabel", background=BACKGROUND, padding=0)
        style.configure("Title.TLabel", font=(FONT, 19, "bold"), foreground=INK)
        style.configure("Hint.TLabel", font=(FONT, 9), foreground=MUTED)
        style.configure("Error.TLabel", font=(FONT, 10), foreground="#ab443b")
        style.configure("Eyebrow.TLabel", font=(FONT, 9, "bold"), foreground="#7d9275")
        style.configure("TButton", padding=(12, 8), background="#e7eee1", borderwidth=0, focusthickness=2, focuscolor="#86ab78")
        style.map("TButton", background=[("active", "#dbe8d3")])
        style.configure("Primary.TButton", background=GREEN, foreground="white", padding=(15, 9))
        style.map("Primary.TButton", background=[("active", "#104737")], foreground=[("disabled", "#adc3b8")])
        style.configure("Danger.TButton", foreground="#a65245")
        style.configure("TEntry", padding=7, fieldbackground="white")
        style.configure("TCombobox", padding=6, fieldbackground="white")
        style.map("TCombobox", fieldbackground=[("readonly", "white")])
        style.configure("TSpinbox", padding=6)
        style.configure("Treeview", background="white", fieldbackground="white", borderwidth=0, rowheight=36, font=(FONT, 10))
        style.configure("Treeview.Heading", background="#e9eee3", foreground="#6e8062", font=(FONT, 9, "bold"), padding=(8, 10), relief="flat")
        style.map("Treeview", background=[("selected", "#dcebd5")], foreground=[("selected", GREEN)])
        style.configure("TNotebook", borderwidth=0)
        style.configure("TNotebook.Tab", padding=(15, 10), background="#e8eee2")
        style.map("TNotebook.Tab", background=[("selected", "#ffffff")], foreground=[("selected", GREEN)])

    def build_header(self):
        frame = ttk.Frame(self.root, padding=(24, 22, 24, 18))
        frame.pack(fill="x")
        left = ttk.Frame(frame)
        left.pack(side="left")
        ttk.Label(left, text="homebase.", font=(FONT, 26, "bold"), foreground=GREEN).pack(anchor="w")
        ttk.Label(left, text="Universal home / locational inventory · Python desktop · Local only", style="Hint.TLabel").pack(anchor="w", pady=(2, 0))
        ttk.Button(frame, text="+ Add item", style="Primary.TButton", command=self.add_item).pack(side="right", padx=(8, 0))
        ttk.Button(frame, text="Back up database…", command=self.backup).pack(side="right", padx=8)
        ttk.Button(frame, text="About", command=self.about).pack(side="right", padx=4)

    def build_stats(self, parent):
        frame = ttk.Frame(parent)
        frame.pack(fill="x")
        self.stat_values = {}
        for number, (key, title) in enumerate((("items", "UNIQUE ITEMS"), ("units", "TOTAL UNITS"), ("locations", "LOCATIONS"), ("low", "LOW STOCK"))):
            frame.columnconfigure(number, weight=1, uniform="stats")
            card = tk.Frame(frame, bg="white", highlightbackground="#dfe7d8", highlightthickness=1, padx=17, pady=13)
            card.grid(row=0, column=number, sticky="ew", padx=(0 if number == 0 else 10, 0))
            tk.Label(card, text=title, bg="white", fg=MUTED, font=(FONT, 8, "bold")).pack(anchor="w")
            self.stat_values[key] = tk.StringVar(value="0")
            tk.Label(card, textvariable=self.stat_values[key], bg="white", fg="#9c7843" if key == "low" else INK, font=(FONT, 25, "bold")).pack(anchor="w", pady=(4, 0))

    def build_inventory_tab(self):
        toolbar = ttk.Frame(self.inventory_tab)
        toolbar.pack(fill="x", pady=(0, 10))
        self.query = tk.StringVar()
        self.category = tk.StringVar(value="All categories")
        self.tag = tk.StringVar(value="All tags")
        self.low_only = tk.BooleanVar(value=False)
        self.sort = tk.StringVar(value="Recently updated")
        ttk.Label(toolbar, text="Search").pack(side="left", padx=(0, 8))
        self.search_entry = ttk.Entry(toolbar, textvariable=self.query)
        self.search_entry.pack(side="left", fill="x", expand=True)
        ttk.Button(toolbar, text="Clear filters", command=self.clear_filters).pack(side="left", padx=(10, 0))
        filters = ttk.Frame(self.inventory_tab)
        filters.pack(fill="x", pady=(0, 13))
        category_combo = ttk.Combobox(filters, textvariable=self.category, values=("All categories", *CATEGORIES), state="readonly", width=17)
        category_combo.pack(side="left", padx=(0, 8))
        self.tag_combo = ttk.Combobox(filters, textvariable=self.tag, values=("All tags",), state="readonly", width=17)
        self.tag_combo.pack(side="left", padx=(0, 8))
        ttk.Checkbutton(filters, text="Low stock", variable=self.low_only, command=self.refresh_items).pack(side="left", padx=7)
        sort_combo = ttk.Combobox(filters, textvariable=self.sort, values=("Recently updated", "Name A–Z", "Quantity: low first", "Value: high first"), state="readonly", width=20)
        sort_combo.pack(side="right")
        for combo in (category_combo, self.tag_combo, sort_combo):
            combo.bind("<<ComboboxSelected>>", lambda event: self.refresh_items())
        self.query.trace_add("write", self.queue_search)
        self.scope = tk.StringVar(value="All locations")
        ttk.Label(self.inventory_tab, textvariable=self.scope, style="Hint.TLabel").pack(anchor="w", pady=(0, 10))
        content = ttk.Panedwindow(self.inventory_tab, orient="horizontal")
        content.pack(fill="both", expand=True)
        table_frame = ttk.Frame(content)
        content.add(table_frame, weight=1)
        columns = ("name", "category", "quantity", "location", "tags", "value")
        self.item_tree = ttk.Treeview(table_frame, columns=columns, show="headings", selectmode="browse")
        for key, title, width in zip(columns, ("Item name", "Category", "Qty", "Location", "Tags", "Total value"), (220, 105, 55, 220, 150, 95)):
            self.item_tree.heading(key, text=title)
            self.item_tree.column(key, width=width, minwidth=50, stretch=key in ("name", "location"), anchor="e" if key in ("quantity", "value") else "w")
        vertical = ttk.Scrollbar(table_frame, orient="vertical", command=self.item_tree.yview)
        horizontal = ttk.Scrollbar(table_frame, orient="horizontal", command=self.item_tree.xview)
        self.item_tree.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
        self.item_tree.grid(row=0, column=0, sticky="nsew")
        vertical.grid(row=0, column=1, sticky="ns")
        horizontal.grid(row=1, column=0, sticky="ew")
        table_frame.rowconfigure(0, weight=1)
        table_frame.columnconfigure(0, weight=1)
        self.item_tree.tag_configure("alternate", background="#f8faf5")
        self.item_tree.tag_configure("low", foreground="#a77b3d")
        self.item_tree.bind("<<TreeviewSelect>>", self.on_item_select)
        self.item_tree.bind("<Double-1>", lambda event: self.edit_item())
        self.item_tree.bind("<Return>", lambda event: self.edit_item())
        self.item_tree.bind("<Delete>", lambda event: self.delete_item())
        self.build_detail(content)
        actions = ttk.Frame(self.inventory_tab)
        actions.pack(fill="x", pady=(12, 0))
        self.results = tk.StringVar()
        ttk.Label(actions, textvariable=self.results, style="Hint.TLabel").pack(side="left")
        ttk.Button(actions, text="Export shown items…", command=self.export_csv).pack(side="right")
        ttk.Button(actions, text="Reset samples…", command=self.reset_samples).pack(side="right", padx=8)

    def build_detail(self, parent):
        outer, frame = scroll_frame(parent, width=285, padding=(15, 0, 0, 8))
        parent.add(outer, weight=0)
        self.photo_canvas = tk.Canvas(frame, width=250, height=170, bg="#eaf0e3", highlightthickness=0)
        self.photo_canvas.pack(fill="x", pady=(0, 14))
        self.detail_name = tk.StringVar(value="Select an item")
        ttk.Label(frame, textvariable=self.detail_name, font=(FONT, 16, "bold"), wraplength=250).pack(anchor="w", pady=(0, 8))
        self.detail_category = tk.StringVar()
        ttk.Label(frame, textvariable=self.detail_category, style="Hint.TLabel").pack(anchor="w")
        quantity = ttk.Frame(frame)
        quantity.pack(fill="x", pady=14)
        self.minus_button = ttk.Button(quantity, text="−", width=3, command=lambda: self.adjust_quantity(-1))
        self.minus_button.pack(side="left")
        self.detail_qty = tk.StringVar(value="—")
        ttk.Label(quantity, textvariable=self.detail_qty, font=(FONT, 14, "bold")).pack(side="left", padx=14)
        self.plus_button = ttk.Button(quantity, text="+", width=3, command=lambda: self.adjust_quantity(1))
        self.plus_button.pack(side="left")
        self.detail_values = tk.StringVar()
        ttk.Label(frame, textvariable=self.detail_values, style="Hint.TLabel", wraplength=250).pack(anchor="w", pady=(0, 14))
        ttk.Label(frame, text="LOCATION", style="Eyebrow.TLabel").pack(anchor="w", pady=(4, 4))
        self.detail_location = tk.StringVar()
        ttk.Label(frame, textvariable=self.detail_location, wraplength=250, foreground=GREEN).pack(anchor="w", pady=(0, 12))
        ttk.Label(frame, text="TAGS", style="Eyebrow.TLabel").pack(anchor="w", pady=(4, 4))
        self.detail_tags = tk.StringVar()
        ttk.Label(frame, textvariable=self.detail_tags, wraplength=250, style="Hint.TLabel").pack(anchor="w", pady=(0, 12))
        ttk.Label(frame, text="NOTES", style="Eyebrow.TLabel").pack(anchor="w", pady=(4, 4))
        self.detail_notes = tk.Text(frame, height=4, width=27, font=(FONT, 9), bg=BACKGROUND, fg=MUTED, wrap="word", bd=0, highlightthickness=0)
        self.detail_notes.pack(fill="both", expand=True, pady=(0, 10))
        self.detail_notes.configure(state="disabled")
        self.edit_button = ttk.Button(frame, text="Edit / move item", command=self.edit_item, style="Primary.TButton")
        self.edit_button.pack(fill="x", pady=4)
        self.delete_button = ttk.Button(frame, text="Delete item…", command=self.delete_item, style="Danger.TButton")
        self.delete_button.pack(fill="x", pady=4)

    def build_locations_tab(self):
        ttk.Label(self.locations_tab, text="Organize from your house to your smallest drawer.", style="Hint.TLabel").pack(anchor="w", pady=(0, 12))
        area = ttk.Frame(self.locations_tab)
        area.pack(fill="both", expand=True)
        self.manage_tree = ttk.Treeview(area, columns=("type", "path", "count"), show="tree headings", selectmode="browse")
        self.manage_tree.heading("#0", text="Location")
        self.manage_tree.column("#0", width=240)
        for key, text, width in (("type", "Type", 90), ("path", "Full path", 450), ("count", "Items inside", 100)):
            self.manage_tree.heading(key, text=text)
            self.manage_tree.column(key, width=width)
        scroll = ttk.Scrollbar(area, orient="vertical", command=self.manage_tree.yview)
        horizontal = ttk.Scrollbar(area, orient="horizontal", command=self.manage_tree.xview)
        self.manage_tree.configure(yscrollcommand=scroll.set, xscrollcommand=horizontal.set)
        self.manage_tree.grid(row=0, column=0, sticky="nsew")
        scroll.grid(row=0, column=1, sticky="ns")
        horizontal.grid(row=1, column=0, sticky="ew")
        area.columnconfigure(0, weight=1)
        area.rowconfigure(0, weight=1)
        self.manage_tree.bind("<Double-1>", lambda event: self.edit_location(manage=True))
        controls = ttk.Frame(self.locations_tab)
        controls.pack(fill="x", pady=(14, 0))
        ttk.Button(controls, text="+ Add location", style="Primary.TButton", command=lambda: self.add_location(manage=True)).pack(side="left", padx=(0, 8))
        ttk.Button(controls, text="Edit / move selected", command=lambda: self.edit_location(manage=True)).pack(side="left", padx=(0, 8))
        ttk.Button(controls, text="Delete empty location…", command=self.delete_location).pack(side="left")
        ttk.Label(controls, text="Nonempty locations are protected.", style="Hint.TLabel").pack(side="right")

    def build_activity_tab(self):
        frame = ttk.Frame(self.activity_tab)
        frame.pack(fill="both", expand=True)
        self.activity_tree = ttk.Treeview(frame, columns=("when", "what"), show="headings", selectmode="browse")
        self.activity_tree.heading("when", text="Date and time")
        self.activity_tree.heading("what", text="Activity")
        self.activity_tree.column("when", width=175, stretch=False)
        self.activity_tree.column("what", width=650)
        scroll = ttk.Scrollbar(frame, orient="vertical", command=self.activity_tree.yview)
        self.activity_tree.configure(yscrollcommand=scroll.set)
        self.activity_tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        ttk.Label(self.activity_tab, text="The most recent 200 changes, saved in your local database.", style="Hint.TLabel").pack(anchor="w", pady=(12, 0))

    def center_dialog(self, window):
        window.update_idletasks()
        x = max(0, self.root.winfo_rootx() + (self.root.winfo_width() - window.winfo_width()) // 2)
        y = max(0, self.root.winfo_rooty() + (self.root.winfo_height() - window.winfo_height()) // 2)
        window.geometry(f"+{x}+{y}")

    def set_status(self, text):
        self.status.set(f"{text}  ·  Saved locally")

    def queue_search(self, *_):
        if self._search_job:
            self.root.after_cancel(self._search_job)
        self._search_job = self.root.after(160, self.run_search)

    def run_search(self):
        self._search_job = None
        self.refresh_items()

    def current_filters(self):
        return {"query": self.query.get(), "location_id": self.location_filter,
                "category": "" if self.category.get() == "All categories" else self.category.get(),
                "tag": "" if self.tag.get() == "All tags" else self.tag.get(),
                "low_only": self.low_only.get(), "sort": self.sort.get()}

    def refresh(self):
        totals = self.inventory.stats()
        for key, var in self.stat_values.items():
            var.set(f"{totals[key]:,}")
        all_tags = self.inventory.all_tags()
        if self.tag.get() not in ("All tags", *all_tags):
            self.tag.set("All tags")
        self.tag_combo.configure(values=("All tags", *all_tags))
        self.refresh_locations()
        self.refresh_items()
        self.activity_tree.delete(*self.activity_tree.get_children())
        for entry in self.inventory.activity():
            when = datetime.fromisoformat(entry["created_at"]).astimezone().strftime("%b %d, %Y  %I:%M %p")
            self.activity_tree.insert("", "end", iid=str(entry["id"]), values=(when, entry["message"]))

    def refresh_locations(self):
        self._refreshing = True
        old_manage = self.manage_tree.selection()
        locations = self.inventory.locations()
        valid = {loc["id"] for loc in locations}
        if self.location_filter not in valid:
            self.location_filter = None
        self.location_tree.delete(*self.location_tree.get_children())
        self.manage_tree.delete(*self.manage_tree.get_children())
        remaining = locations[:]
        while remaining:
            progress = False
            for loc in remaining[:]:
                parent = loc["parent_id"] or ""
                if parent and not self.location_tree.exists(parent):
                    continue
                count = len(self.inventory.items(location_id=loc["id"]))
                self.location_tree.insert(parent, "end", iid=loc["id"], text=f"{loc['name']}  ({count})", open=True)
                self.manage_tree.insert(parent, "end", iid=loc["id"], text=loc["name"], values=(loc["kind"], self.inventory.path_text(loc["id"]), count), open=True)
                remaining.remove(loc)
                progress = True
            if not progress:
                raise InventoryError("A location hierarchy is invalid. Restore a database backup.")
        if self.location_filter:
            self.location_tree.selection_set(self.location_filter)
        if old_manage and old_manage[0] in valid:
            self.manage_tree.selection_set(old_manage[0])
        self.root.after_idle(self.end_refresh)

    def end_refresh(self):
        self._refreshing = False

    def refresh_items(self):
        self.filtered_items = self.inventory.items(**self.current_filters())
        self.item_tree.delete(*self.item_tree.get_children())
        for index, item in enumerate(self.filtered_items):
            tags = ["alternate"] if index % 2 else []
            if item["quantity"] <= item["low_stock"]:
                tags.append("low")
            self.item_tree.insert("", "end", iid=item["id"], values=(item["name"], item["category"], item["quantity"], self.inventory.path_text(item["location_id"]), ", ".join(item["tags"]), currency(item["quantity"] * item["value"])), tags=tags)
        visible_ids = {item["id"] for item in self.filtered_items}
        if self.selected_id not in visible_ids:
            self.selected_id = self.filtered_items[0]["id"] if self.filtered_items else None
        if self.selected_id:
            self.item_tree.selection_set(self.selected_id)
            self.item_tree.focus(self.selected_id)
            self.item_tree.see(self.selected_id)
        self.show_detail()
        count = len(self.filtered_items)
        self.results.set(f"{count} item{'s' if count != 1 else ''} shown" + (" · No matches. Try clearing your filters." if not count else " · Double-click an item to edit"))
        self.scope.set(self.inventory.path_text(self.location_filter) if self.location_filter else "All locations · every room, shelf, and drawer")

    def show_detail(self):
        item = self.inventory.get_item(self.selected_id) if self.selected_id else None
        self.detail_name.set(item["name"] if item else "Select an item")
        self.detail_category.set((item["category"] + ("  ·  LOW STOCK" if item["quantity"] <= item["low_stock"] else "")) if item else "Add an item or clear your filters.")
        self.detail_qty.set(f"{item['quantity']} units" if item else "—")
        self.detail_values.set(f"Unit value {currency(item['value'])}  ·  Low stock at {item['low_stock']}" if item else "")
        self.detail_location.set(self.inventory.path_text(item["location_id"]) if item else "—")
        self.detail_tags.set(", ".join(item["tags"]) or "No tags" if item else "—")
        self.detail_notes.configure(state="normal")
        self.detail_notes.delete("1.0", "end")
        self.detail_notes.insert("1.0", item["notes"] or "No notes added." if item else "")
        self.detail_notes.configure(state="disabled")
        paint_photo(self.photo_canvas, item["photo"] if item else b"")
        for button in (self.edit_button, self.delete_button):
            button.configure(state="normal" if item else "disabled")
        self.minus_button.configure(state="normal" if item and item["quantity"] > 0 else "disabled")
        self.plus_button.configure(state="normal" if item and item["quantity"] < 999999 else "disabled")

    def on_item_select(self, _event=None):
        selected = self.item_tree.selection()
        if selected:
            self.selected_id = selected[0]
            self.show_detail()

    def on_location_select(self, _event=None):
        if self._refreshing:
            return
        selected = self.location_tree.selection()
        if selected:
            self.location_filter = selected[0]
            self.notebook.select(self.inventory_tab)
            self.refresh_items()

    def clear_location(self):
        self.location_filter = None
        self.location_tree.selection_remove(*self.location_tree.selection())
        self.notebook.select(self.inventory_tab)
        self.refresh_items()

    def clear_filters(self, refresh=True):
        self.query.set("")
        self.category.set("All categories")
        self.tag.set("All tags")
        self.low_only.set(False)
        self.sort.set("Recently updated")
        self.location_filter = None
        self.location_tree.selection_remove(*self.location_tree.selection())
        if refresh:
            self.refresh_items()

    def focus_search(self, _event=None):
        if not self.root.grab_current():
            self.notebook.select(self.inventory_tab)
            self.search_entry.focus_set()
            self.search_entry.selection_range(0, "end")
        return "break"

    def add_item(self):
        if self.root.grab_current():
            return
        if not self.inventory.locations():
            messagebox.showinfo("Add a location first", "Create a home, room, shelf, or drawer before adding an item.", parent=self.root)
            self.add_location()
            return
        ItemDialog(self)

    def edit_item(self):
        if self.selected_id and not self.root.grab_current():
            ItemDialog(self, self.selected_id)

    def adjust_quantity(self, delta):
        if self.selected_id:
            try:
                item = self.inventory.adjust_quantity(self.selected_id, delta)
                self.refresh()
                self.set_status(f"{item['name']}: {item['quantity']} units")
            except (InventoryError, sqlite3.Error) as error:
                messagebox.showerror("Quantity not changed", str(error), parent=self.root)

    def delete_item(self):
        if not self.selected_id:
            return
        item = self.inventory.get_item(self.selected_id)
        if not messagebox.askyesno("Delete item?", f"Delete {item['name']} and its {item['quantity']} units?\n\nYou can undo the last item deletion while this app stays open.", parent=self.root):
            return
        try:
            self.deleted_item = self.inventory.delete_item(item["id"])
            self.selected_id = None
            self.undo_button.configure(state="normal")
            self.refresh()
            self.set_status("Item deleted · use Undo last delete to restore it")
        except (InventoryError, sqlite3.Error) as error:
            messagebox.showerror("Item not deleted", str(error), parent=self.root)

    def undo_delete(self):
        if self.deleted_item:
            try:
                item = self.inventory.restore_item(self.deleted_item)
                self.deleted_item = None
                self.undo_button.configure(state="disabled")
                self.selected_id = item["id"]
                self.clear_filters(refresh=False)
                self.refresh()
                self.set_status("Item restored")
            except (InventoryError, sqlite3.Error) as error:
                messagebox.showerror("Cannot restore item", str(error), parent=self.root)

    def location_selection(self, manage=False):
        tree = self.manage_tree if manage else self.location_tree
        selected = tree.selection()
        return selected[0] if selected else None

    def add_location(self, manage=False):
        if not self.root.grab_current():
            LocationDialog(self, parent_id=self.location_selection(manage))

    def edit_location(self, manage=False):
        location_id = self.location_selection(manage)
        if location_id and not self.root.grab_current():
            LocationDialog(self, location_id)
        elif not self.root.grab_current():
            messagebox.showinfo("Select a location", "Select a location to edit it.", parent=self.root)

    def delete_location(self):
        location_id = self.location_selection(manage=True)
        if not location_id:
            messagebox.showinfo("Select a location", "Select an empty location to delete it.", parent=self.root)
            return
        location = self.inventory.get_location(location_id)
        if not messagebox.askyesno("Delete empty location?", f"Delete {location['name']}?\n\nLocations containing items or child locations cannot be deleted.", parent=self.root):
            return
        try:
            self.inventory.delete_location(location_id)
            self.refresh()
            self.set_status("Location deleted")
        except (InventoryError, sqlite3.Error) as error:
            messagebox.showerror("Location not deleted", str(error), parent=self.root)

    def backup(self):
        filename = filedialog.asksaveasfilename(parent=self.root, title="Back up inventory and photos", defaultextension=".sqlite3", initialfile=f"Homebase_Backup_{datetime.now():%Y-%m-%d}.sqlite3", filetypes=[("SQLite database", "*.sqlite3")])
        if filename:
            try:
                self.inventory.backup(filename)
                self.set_status("Database backup saved, including all photos")
            except (OSError, InventoryError, sqlite3.Error) as error:
                messagebox.showerror("Backup failed", str(error), parent=self.root)

    def export_csv(self):
        filename = filedialog.asksaveasfilename(parent=self.root, title="Export shown inventory items", defaultextension=".csv", initialfile="Homebase_Inventory.csv", filetypes=[("CSV spreadsheet", "*.csv")])
        if filename:
            try:
                self.inventory.export_csv(filename, self.filtered_items)
                self.set_status(f"Exported {len(self.filtered_items)} items · photos stay in the database backup")
            except (OSError, InventoryError) as error:
                messagebox.showerror("Export failed", str(error), parent=self.root)

    def reset_samples(self):
        if not messagebox.askyesno("Replace inventory with samples?", "This replaces every item, location, photo, and activity entry in this database with the sample inventory.\n\nBack up your database first if you want to keep your changes. Continue?", icon="warning", parent=self.root):
            return
        try:
            self.inventory.reset_samples()
            self.deleted_item = None
            self.undo_button.configure(state="disabled")
            self.selected_id = None
            self.clear_filters(refresh=False)
            self.refresh()
            self.set_status("Original sample inventory restored")
        except (InventoryError, sqlite3.Error) as error:
            messagebox.showerror("Reset failed", str(error), parent=self.root)

    def about(self):
        messagebox.showinfo("Homebase · Python desktop", "Universal Home/Locational Inventory Logging Management System\n\nPython + Tkinter + SQLite. Runs entirely on your computer, without an account or an internet connection.\n\nItems, photos, tags, locations, and activity are saved in:\n" + str(self.inventory.path) + "\n\nCtrl+N: add item\nCtrl+F: search\nDouble-click item: edit or move\n\nUse Back up database to copy everything, including photos.\n\nCS project · Gregory Edison, Carlos Martinez & Benjamin Samok", parent=self.root)

    def close(self):
        if self.root.grab_current():
            self.root.grab_current().destroy()
        if self._search_job:
            self.root.after_cancel(self._search_job)
        self.inventory.close()
        self.root.destroy()


def main(argv=None):
    parser = argparse.ArgumentParser(description="Homebase local Python desktop inventory")
    parser.add_argument("--db", type=Path, default=ROOT / "data" / "homebase.sqlite3", help="SQLite database file to open")
    parser.add_argument("--check", action="store_true", help="Check the database without opening a window")
    args = parser.parse_args(argv)
    try:
        inventory = Inventory(args.db)
        if args.check:
            check = inventory.db.execute("PRAGMA integrity_check").fetchone()[0]
            totals = inventory.stats()
            inventory.close()
            if check != "ok":
                raise InventoryError(f"Database integrity check failed: {check}")
            print(f"Database OK: {totals['items']} items, {totals['units']} units, {totals['locations']} locations.")
            return 0
        if tk is None:
            inventory.close()
            print("Tkinter is missing. On macOS, use a Python.org installer with Tkinter. On Windows, enable Tcl/Tk in the installer. On Ubuntu/Debian, install python3-tk.", file=sys.stderr)
            return 1
        root = tk.Tk()
        HomebaseApp(root, inventory)
        root.mainloop()
        return 0
    except Exception as error:
        print(f"Homebase could not start: {error}", file=sys.stderr)
        if tk is not None and isinstance(error, tk.TclError):
            print("A desktop display is required. Run python app.py on your Windows, Mac, or Linux desktop.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
