from __future__ import annotations

import json
import re
import threading
import tkinter as tk
from pathlib import Path
import shutil
from tkinter import filedialog, messagebox, simpledialog, ttk

from project_core import KNOWN_VENDOR_EXTERNALS, HierarchyNode, ProjectResult, ProjectTranslator
from vivado_verify import DEFAULT_PART, VivadoVerificationResult, discover_vivado, verify_vhdl_files


# FPGA Discovery light-workspace palette
NAVY = "#0B1F33"
NAVY_2 = "#12324F"
BLUE = "#1976D2"
BLUE_LIGHT = "#EAF3FC"
GREEN = "#1F9D67"
GREEN_LIGHT = "#EAF7F1"
PURPLE = "#6B55B8"
PURPLE_LIGHT = "#F2EEFB"
BG = "#F4F7FA"
CARD = "#FFFFFF"
BORDER = "#D8E0E8"
TEXT = "#172433"
MUTED = "#627184"
EDITOR_BG = "#FBFCFE"
EDITOR_FG = "#18212B"
WARN = "#A56500"
ERROR = "#C53A3A"


BOARD_PRESETS = {
    "Basys 3": "xc7a35tcpg236-1",
    "Nexys A7-50T": "xc7a50ticsg324-1L",
    "Nexys A7-100T": "xc7a100tcsg324-1",
    "Cmod A7-15T": "xc7a15tcpg236-1",
    "Cmod A7-35T": "xc7a35tcpg236-1",
}
DEFAULT_BOARD = "Basys 3"



class TranslatorApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("FPGA Discovery — Verilog → VHDL Translator — v1.0.0")
        self.geometry("1600x920")
        self.minsize(1200, 760)
        self.configure(bg=BG)

        self.project_translator = ProjectTranslator()
        self.project: ProjectResult | None = None
        self.source_paths: list[Path] = []
        self.selected_module: str | None = None
        self.translation_done = False
        self.tree_item_modules: dict[str, str] = {}
        self.verify_result: VivadoVerificationResult | None = None
        self.verify_running = False
        self.settings_path = Path.home() / ".fpga_discovery_verilog_vhdl.json"
        # Private working folder used only for the no-project quick-translate
        # workflow. It lets the normal project translator, hierarchy builder,
        # exporter, and Vivado verifier operate on pasted editor text without
        # requiring the user to save a temporary .v file.
        self.quick_translate_dir = None
        self.settings = self._load_settings()

        self._configure_styles()
        self._build_ui()

    def _load_settings(self):
        defaults = {"vivado_path": "", "vivado_part": DEFAULT_PART, "vivado_board": DEFAULT_BOARD}
        try:
            data = json.loads(self.settings_path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                defaults.update({k: v for k, v in data.items() if k in defaults and isinstance(v, str)})
        except (OSError, ValueError, TypeError):
            pass
        return defaults

    def _save_settings(self):
        try:
            self.settings_path.write_text(json.dumps(self.settings, indent=2), encoding="utf-8")
        except OSError:
            pass

    def _configure_styles(self):
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass

        style.configure("App.TFrame", background=BG)
        style.configure("Card.TFrame", background=CARD)
        style.configure("Toolbar.TFrame", background=CARD)
        style.configure("Status.TFrame", background=NAVY)

        style.configure("TLabel", background=BG, foreground=TEXT, font=("Segoe UI", 9))
        style.configure("Muted.TLabel", background=CARD, foreground=MUTED, font=("Segoe UI", 9))
        style.configure("CardTitle.TLabel", background=CARD, foreground=TEXT, font=("Segoe UI Semibold", 10))
        style.configure("HeaderTitle.TLabel", background=NAVY, foreground="white", font=("Segoe UI Semibold", 18))
        style.configure("HeaderSub.TLabel", background=NAVY, foreground="#B8C8D8", font=("Segoe UI", 9))
        style.configure("Status.TLabel", background=NAVY, foreground="#DCE8F3", font=("Segoe UI", 9))
        style.configure("ProjectBadge.TLabel", background=BLUE_LIGHT, foreground=BLUE, font=("Segoe UI Semibold", 9), padding=(9, 4))

        # Buttons
        style.configure("Primary.TButton", font=("Segoe UI Semibold", 9), padding=(14, 8), background=BLUE, foreground="white", borderwidth=0)
        style.map("Primary.TButton", background=[("active", "#155FA8"), ("pressed", "#104E8A")], foreground=[("disabled", "#DDE6EF")])
        style.configure("Success.TButton", font=("Segoe UI Semibold", 9), padding=(14, 8), background=GREEN, foreground="white", borderwidth=0)
        style.map("Success.TButton", background=[("active", "#187F54"), ("pressed", "#126943")])
        style.configure("Secondary.TButton", font=("Segoe UI", 9), padding=(12, 8), background="#EDF2F7", foreground=TEXT, borderwidth=0)
        style.map("Secondary.TButton", background=[("active", "#E1E8EF"), ("pressed", "#D4DEE8")])
        style.configure("Copy.TButton", font=("Segoe UI Semibold", 9), padding=(12, 8), background=PURPLE, foreground="white", borderwidth=0)
        style.map("Copy.TButton", background=[("active", "#59459F"), ("pressed", "#493A84")])

        style.configure("Treeview", background=CARD, fieldbackground=CARD, foreground=TEXT,
                        rowheight=25, borderwidth=0, font=("Segoe UI", 9))
        style.map("Treeview", background=[("selected", BLUE_LIGHT)], foreground=[("selected", NAVY)])
        style.configure("Vertical.TScrollbar", background="#E6EDF4", troughcolor=CARD, borderwidth=0, arrowsize=13)
        style.configure("Horizontal.TScrollbar", background="#E6EDF4", troughcolor=CARD, borderwidth=0, arrowsize=13)
        style.configure("TPanedwindow", background=BG, sashwidth=6)

    def _build_ui(self):
        self.columnconfigure(0, weight=1)
        self.rowconfigure(2, weight=1)
        self.report_expanded = False
        self.report_collapsed = False

        # Branded header
        header = tk.Frame(self, bg=NAVY, height=76)
        header.grid(row=0, column=0, sticky="ew")
        header.grid_propagate(False)
        header.columnconfigure(0, weight=1)

        title_box = ttk.Frame(header, style="Status.TFrame")
        title_box.grid(row=0, column=0, sticky="w", padx=20, pady=(13, 0))
        ttk.Label(title_box, text="FPGA Discovery", style="HeaderTitle.TLabel").pack(side="left")
        tk.Label(title_box, text="  /  ", bg=NAVY, fg="#66809A", font=("Segoe UI", 17)).pack(side="left")
        tk.Label(title_box, text="Verilog → VHDL Translator", bg=NAVY, fg="#63B3ED", font=("Segoe UI Semibold", 17)).pack(side="left")
        ttk.Label(header, text="Deterministic RTL project translation • hierarchy-aware • real-project diagnostics",
                  style="HeaderSub.TLabel").grid(row=1, column=0, sticky="w", padx=21, pady=(0, 10))

        # Action toolbar
        toolbar = tk.Frame(self, bg=CARD, highlightbackground=BORDER, highlightthickness=1)
        toolbar.grid(row=1, column=0, sticky="ew", padx=12, pady=(10, 8))
        toolbar.columnconfigure(9, weight=1)

        ttk.Button(toolbar, text="＋  Import Verilog Files", style="Secondary.TButton", command=self.open_files).grid(row=0, column=0, padx=(10, 4), pady=9)
        ttk.Separator(toolbar, orient="vertical").grid(row=0, column=1, sticky="ns", padx=8, pady=9)
        ttk.Button(toolbar, text="▶  Translate Files", style="Primary.TButton", command=self.translate_project).grid(row=0, column=2, padx=4, pady=9)
        ttk.Button(toolbar, text="⚙  Vivado Setup", style="Secondary.TButton", command=self.vivado_setup).grid(row=0, column=3, padx=4, pady=9)
        self.verify_button = ttk.Button(toolbar, text="✓  Verify VHDL", style="Success.TButton", command=self.verify_vhdl)
        self.verify_button.grid(row=0, column=4, padx=4, pady=9)
        ttk.Button(toolbar, text="⇩  Export VHDL Files", style="Success.TButton", command=self.export_project).grid(row=0, column=5, padx=4, pady=9)
        ttk.Button(toolbar, text="⧉  Copy VHDL", style="Copy.TButton", command=self.copy_vhdl).grid(row=0, column=6, padx=4, pady=9)
        ttk.Button(toolbar, text="Clear Workspace", style="Secondary.TButton", command=self.clear).grid(row=0, column=7, padx=(4, 10), pady=9)
        self.project_label = ttk.Label(toolbar, text="NO PROJECT", style="ProjectBadge.TLabel")
        self.project_label.grid(row=0, column=10, padx=(8, 12), pady=9, sticky="e")

        # Main workspace
        workspace = ttk.Panedwindow(self, orient="horizontal")
        self.workspace = workspace
        workspace.grid(row=2, column=0, sticky="nsew", padx=12)

        hierarchy_panel = tk.Frame(workspace, bg=BG)
        editors = ttk.Panedwindow(workspace, orient="horizontal")
        workspace.add(hierarchy_panel, weight=1)
        workspace.add(editors, weight=4)

        hierarchy_panel.columnconfigure(0, weight=1)
        hierarchy_panel.rowconfigure(0, weight=1)
        hierarchy_panel.rowconfigure(1, weight=1)

        src_card, src_body = self._card(hierarchy_panel, "VERILOG MODULE HIERARCHY", BLUE, 0, (0, 6))
        self.src_tree = ttk.Treeview(src_body, show="tree")
        self.src_tree.grid(row=0, column=0, sticky="nsew", padx=(1, 0), pady=1)
        src_scroll = ttk.Scrollbar(src_body, orient="vertical", command=self.src_tree.yview)
        src_scroll.grid(row=0, column=1, sticky="ns", pady=1)
        self.src_tree.configure(yscrollcommand=src_scroll.set)
        self.src_tree.bind("<<TreeviewSelect>>", self._tree_selected)

        dst_card, dst_body = self._card(hierarchy_panel, "VHDL ENTITY HIERARCHY", GREEN, 1, (6, 0))
        self.dst_tree = ttk.Treeview(dst_body, show="tree")
        self.dst_tree.grid(row=0, column=0, sticky="nsew", padx=(1, 0), pady=1)
        dst_scroll = ttk.Scrollbar(dst_body, orient="vertical", command=self.dst_tree.yview)
        dst_scroll.grid(row=0, column=1, sticky="ns", pady=1)
        self.dst_tree.configure(yscrollcommand=dst_scroll.set)
        self.dst_tree.bind("<<TreeviewSelect>>", self._tree_selected)

        self.editors = editors
        left = self._editor_card(editors, "VERILOG SOURCE", "INPUT RTL", BLUE,
                                 toggle_text="□  Maximize Verilog",
                                 toggle_command=lambda: self._maximize_editor("verilog"))
        right = self._editor_card(editors, "GENERATED VHDL", "TRANSLATED OUTPUT", GREEN,
                                  toggle_text="□  Maximize VHDL",
                                  toggle_command=lambda: self._maximize_editor("vhdl"))
        self.verilog_editor_card = left
        self.vhdl_editor_card = right
        self.maximized_editor = None
        editors.add(left, weight=1)
        editors.add(right, weight=1)

        self.src = tk.Text(left.body, wrap="none", undo=True, font=("Consolas", 10),
                           bg=EDITOR_BG, fg=EDITOR_FG, insertbackground=BLUE,
                           selectbackground="#CFE4F8", relief="flat", borderwidth=0,
                           padx=10, pady=9)
        self.dst = tk.Text(right.body, wrap="none", font=("Consolas", 10),
                           bg=EDITOR_BG, fg=EDITOR_FG, insertbackground=GREEN,
                           selectbackground="#D3F0E2", relief="flat", borderwidth=0,
                           padx=10, pady=9)
        self._with_scrollbars(left.body, self.src)
        self._with_scrollbars(right.body, self.dst)

        # Report card
        report_outer = tk.Frame(self, bg=CARD, highlightbackground=BORDER, highlightthickness=1)
        self.report_outer = report_outer
        report_outer.grid(row=3, column=0, sticky="ew", padx=12, pady=(8, 8))
        report_outer.columnconfigure(0, weight=1)
        report_head = tk.Frame(report_outer, bg=CARD)
        report_head.grid(row=0, column=0, sticky="ew")
        tk.Frame(report_head, bg=PURPLE, width=4, height=30).pack(side="left", fill="y")
        tk.Label(report_head, text="PROJECT TRANSLATION REPORT", bg=CARD, fg=TEXT,
                 font=("Segoe UI Semibold", 9)).pack(side="left", padx=10, pady=8)
        tk.Label(report_head, text="analysis • diagnostics • Vivado verification • export status", bg=CARD, fg=MUTED,
                 font=("Segoe UI", 8)).pack(side="left", pady=8)
        self.report_toggle = ttk.Button(report_head, text="⤢  Expand Report", style="Secondary.TButton", command=self.toggle_report)
        self.report_toggle.pack(side="right", padx=(4, 8), pady=3)
        self.report_collapse = ttk.Button(report_head, text="▁  Minimize Report", style="Secondary.TButton", command=self.toggle_report_collapse)
        self.report_collapse.pack(side="right", padx=(4, 0), pady=3)
        report_outer.rowconfigure(1, weight=1)
        diag_frame = tk.Frame(report_outer, bg="#FAFBFC")
        self.diag_frame = diag_frame
        diag_frame.grid(row=1, column=0, sticky="nsew", padx=1, pady=(0, 1))
        diag_frame.rowconfigure(0, weight=1); diag_frame.columnconfigure(0, weight=1)
        self.diag = tk.Text(diag_frame, height=8, wrap="none", state="disabled", font=("Consolas", 9),
                            bg="#FAFBFC", fg=TEXT, relief="flat", borderwidth=0, padx=10, pady=7)
        self.diag.grid(row=0, column=0, sticky="nsew")
        diag_y = ttk.Scrollbar(diag_frame, orient="vertical", command=self.diag.yview)
        diag_y.grid(row=0, column=1, sticky="ns")
        diag_x = ttk.Scrollbar(diag_frame, orient="horizontal", command=self.diag.xview)
        diag_x.grid(row=1, column=0, sticky="ew")
        self.diag.configure(yscrollcommand=diag_y.set, xscrollcommand=diag_x.set)
        self.diag.tag_configure("pass", foreground=GREEN)
        self.diag.tag_configure("fail", foreground=ERROR)
        self.diag.tag_configure("warn", foreground=WARN)
        self.diag.tag_configure("info", foreground=BLUE)
        self.diag.tag_configure("muted", foreground=MUTED)

        # Strong footer/status bar
        status_bar = ttk.Frame(self, style="Status.TFrame")
        status_bar.grid(row=4, column=0, sticky="ew")
        status_bar.columnconfigure(1, weight=1)
        self.status_dot = tk.Label(status_bar, text="●", bg=NAVY, fg="#63B3ED", font=("Segoe UI", 9))
        self.status_dot.grid(row=0, column=0, padx=(14, 6), pady=7)
        self.status = ttk.Label(status_bar, text="Ready — open a Verilog project to begin", style="Status.TLabel")
        self.status.grid(row=0, column=1, sticky="w", pady=7)
        tk.Label(status_bar, text="v1.0.0", bg=NAVY_2, fg="#DCE8F3",
                 font=("Segoe UI Semibold", 8), padx=10, pady=4).grid(row=0, column=1, padx=10, pady=4)

    class _EditorCard:
        def __init__(self, frame, body):
            self.frame = frame
            self.body = body

    def _card(self, parent, title, accent, row, pady):
        outer = tk.Frame(parent, bg=CARD, highlightbackground=BORDER, highlightthickness=1)
        outer.grid(row=row, column=0, sticky="nsew", pady=pady, padx=(0, 6))
        outer.columnconfigure(0, weight=1)
        outer.rowconfigure(1, weight=1)
        head = tk.Frame(outer, bg=CARD)
        head.grid(row=0, column=0, sticky="ew")
        tk.Frame(head, bg=accent, width=4, height=30).pack(side="left", fill="y")
        tk.Label(head, text=title, bg=CARD, fg=TEXT, font=("Segoe UI Semibold", 9)).pack(side="left", padx=9, pady=8)
        body = tk.Frame(outer, bg=CARD)
        body.grid(row=1, column=0, sticky="nsew")
        body.columnconfigure(0, weight=1)
        body.rowconfigure(0, weight=1)
        return outer, body

    def _editor_card(self, parent, title, subtitle, accent, toggle_text=None, toggle_command=None):
        outer = tk.Frame(parent, bg=CARD, highlightbackground=BORDER, highlightthickness=1)
        outer.columnconfigure(0, weight=1)
        outer.rowconfigure(1, weight=1)
        head = tk.Frame(outer, bg=CARD, height=39)
        head.grid(row=0, column=0, sticky="ew")
        head.grid_propagate(False)
        tk.Frame(head, bg=accent, width=4).pack(side="left", fill="y")
        tk.Label(head, text=title, bg=CARD, fg=TEXT, font=("Segoe UI Semibold", 10)).pack(side="left", padx=(10, 7), pady=10)
        tk.Label(head, text=subtitle, bg=CARD, fg=MUTED, font=("Segoe UI", 8)).pack(side="left", pady=11)
        if toggle_text and toggle_command:
            ttk.Button(head, text=toggle_text, style="Secondary.TButton",
                       command=toggle_command).pack(side="right", padx=(6, 8), pady=5)
        body = tk.Frame(outer, bg=EDITOR_BG)
        body.grid(row=1, column=0, sticky="nsew")
        body.columnconfigure(0, weight=1)
        body.rowconfigure(0, weight=1)
        card = self._EditorCard(outer, body)
        # PanedWindow expects widget; expose body as attribute while returning frame.
        outer.body = body  # convenient application-owned attribute
        return outer

    def _maximize_editor(self, which):
        """Maximize one editor pane; a single Restore Split View control returns to 50/50."""
        if which == "verilog":
            keep = self.verilog_editor_card
            hide = self.vhdl_editor_card
        else:
            keep = self.vhdl_editor_card
            hide = self.verilog_editor_card

        # If already maximized, do nothing.
        if getattr(self, "maximized_editor", None) == which:
            return

        # Rebuild the editor pane list with only the selected editor.
        for pane in (self.verilog_editor_card, self.vhdl_editor_card):
            try:
                self.editors.forget(pane)
            except Exception:
                pass
        self.editors.add(keep, weight=1)
        self.maximized_editor = which

        # Remove any old restore control and add a fresh one to the visible header.
        if hasattr(self, "_editor_restore_button") and self._editor_restore_button.winfo_exists():
            self._editor_restore_button.destroy()
        head = keep.winfo_children()[0]
        self._editor_restore_button = ttk.Button(
            head, text="↔  Restore Split View", style="Secondary.TButton",
            command=self._restore_split_view)
        self._editor_restore_button.pack(side="right", padx=(6, 8), pady=5)

    def _restore_split_view(self):
        """Restore the normal side-by-side Verilog/VHDL editor layout."""
        if hasattr(self, "_editor_restore_button") and self._editor_restore_button.winfo_exists():
            self._editor_restore_button.destroy()

        for pane in (self.verilog_editor_card, self.vhdl_editor_card):
            try:
                self.editors.forget(pane)
            except Exception:
                pass

        self.editors.add(self.verilog_editor_card, weight=1)
        self.editors.add(self.vhdl_editor_card, weight=1)
        self.maximized_editor = None

    def _with_scrollbars(self, parent, text):
        """Install scrollbars and a synchronized line-number gutter."""
        gutter = tk.Text(parent, width=5, padx=6, pady=9, takefocus=0,
                         borderwidth=0, relief="flat", wrap="none",
                         font=("Consolas", 10), bg="#F0F3F6", fg=MUTED,
                         cursor="arrow", state="disabled")
        parent.columnconfigure(0, weight=0)
        parent.columnconfigure(1, weight=1)
        text.grid(row=0, column=1, sticky="nsew")
        gutter.grid(row=0, column=0, sticky="ns")

        def refresh_lines(event=None):
            count = max(1, int(text.index("end-1c").split(".")[0]))
            gutter.configure(state="normal")
            gutter.delete("1.0", "end")
            gutter.insert("1.0", "\n".join(str(i) for i in range(1, count + 1)))
            gutter.configure(state="disabled")
            gutter.yview_moveto(text.yview()[0])

        def yscroll(*args):
            text.yview(*args)
            gutter.yview(*args)

        y = ttk.Scrollbar(parent, orient="vertical", command=yscroll)
        x = ttk.Scrollbar(parent, orient="horizontal", command=text.xview)
        def sync_y(first, last):
            y.set(first, last)
            gutter.yview_moveto(first)
        text.configure(yscrollcommand=sync_y, xscrollcommand=x.set)
        y.grid(row=0, column=2, sticky="ns")
        x.grid(row=1, column=1, sticky="ew")
        text._line_number_gutter = gutter
        text._refresh_line_numbers = refresh_lines

        def modified(event=None):
            if text.edit_modified():
                text.edit_modified(False)
                refresh_lines()
        text.bind("<<Modified>>", modified, add="+")
        text.bind("<Configure>", refresh_lines, add="+")
        text.bind("<KeyRelease>", refresh_lines, add="+")
        text.bind("<ButtonRelease-1>", refresh_lines, add="+")
        text.bind("<MouseWheel>", lambda e: text.after_idle(refresh_lines), add="+")
        text.bind("<Button-4>", lambda e: text.after_idle(refresh_lines), add="+")
        text.bind("<Button-5>", lambda e: text.after_idle(refresh_lines), add="+")
        text.after_idle(refresh_lines)

    def _set_status(self, text: str, kind: str = "info"):
        colors = {"info": "#63B3ED", "pass": "#4DD69A", "warn": "#F5B84B", "fail": "#FF7777"}
        self.status.configure(text=text)
        self.status_dot.configure(fg=colors.get(kind, colors["info"]))

    def open_files(self):
        names = filedialog.askopenfilenames(filetypes=[
            ("Verilog project files", "*.v *.vh *.svh *.mem"),
            ("Verilog source", "*.v"),
            ("Verilog headers", "*.vh *.svh"),
            ("Memory initialization", "*.mem"),
            ("All files", "*.*"),
        ])
        if not names:
            return
        self.source_paths = [Path(n) for n in names]
        self._load_project()

    def open_folder(self):
        name = filedialog.askdirectory()
        if not name:
            return
        folder = Path(name)
        self.source_paths = sorted(folder.glob("*.v"))
        if not self.source_paths:
            messagebox.showinfo("No Verilog files", "No .v files were found in that folder.")
            return
        self._load_project()

    def _load_project(self):
        self.translation_done = False
        self.verify_result = None
        self.project = self.project_translator.analyze_files(self.source_paths)
        self.project_label.configure(text=f"{len(self.source_paths)} SOURCE FILE(S)")
        self._populate_trees()
        self._update_report()
        if self.project.roots:
            self._show_module(self.project.roots[0].module_name)
        self._set_status(f"Analyzed {len(self.project.modules)} module(s), {len(self.project.headers)} header(s) — ready to translate", "info")

    def translate_project(self):
        if not self.source_paths:
            pasted_source = self.src.get("1.0", "end-1c")
            if not pasted_source.strip():
                messagebox.showinfo(
                    "Nothing to translate",
                    "Import Verilog files or paste Verilog source into the editor first.",
                )
                return

            # Quick Translate: preserve the editor text in a private temporary
            # .v file so the existing deterministic project pipeline remains
            # the single source of truth for parsing, hierarchy, generation,
            # export, and Vivado verification.
            import tempfile
            if self.quick_translate_dir:
                shutil.rmtree(self.quick_translate_dir, ignore_errors=True)
            self.quick_translate_dir = Path(
                tempfile.mkdtemp(prefix="fpga_discovery_quick_translate_")
            )
            quick_source = self.quick_translate_dir / "pasted_verilog.v"
            quick_source.write_text(pasted_source, encoding="utf-8")
            self.source_paths = [quick_source]

        self.project = self.project_translator.translate_files(self.source_paths)
        self.translation_done = True
        self.verify_result = None
        if self.quick_translate_dir and self.source_paths and self.source_paths[0].parent == self.quick_translate_dir:
            self.project_label.configure(text="PASTED SOURCE")
        self._populate_trees()
        self._update_report()
        if self.selected_module and self.selected_module in self.project.modules:
            self._show_module(self.selected_module)
        elif self.project.roots:
            self._show_module(self.project.roots[0].module_name)
        if self.project.ok:
            self._set_status(f"PASS — translated {self.project.translated_count} VHDL entit(ies)", "pass")
        else:
            self._set_status("Translation completed with errors — see project report", "fail")

    def _populate_trees(self):
        for tree in (self.src_tree, self.dst_tree):
            tree.delete(*tree.get_children())
        self.tree_item_modules.clear()
        if not self.project:
            return
        for root in self.project.roots:
            self._insert_hierarchy(self.src_tree, "", root, vhdl=False)
            if self.translation_done:
                self._insert_hierarchy(self.dst_tree, "", root, vhdl=True)

    def _insert_hierarchy(self, tree: ttk.Treeview, parent: str, node: HierarchyNode, vhdl: bool):
        if node.instance_name:
            label = f"{node.instance_name}  :  {node.module_name}"
        else:
            kind = "entity" if vhdl else "module"
            label = f"{node.module_name}   [{kind} root]"
        if not node.resolved:
            label += "   ⚠ unresolved"
        elif node.module_name in KNOWN_VENDOR_EXTERNALS and node.source_path is None:
            label += "   [vendor primitive]"
        item = tree.insert(parent, "end", text=label, open=True)
        if node.resolved and node.module_name in (self.project.modules if self.project else {}):
            self.tree_item_modules[f"{str(tree)}::{item}"] = node.module_name
        for child in node.children:
            self._insert_hierarchy(tree, item, child, vhdl)

    def _tree_selected(self, event):
        tree: ttk.Treeview = event.widget
        sel = tree.selection()
        if not sel:
            return
        module_name = self.tree_item_modules.get(f"{str(tree)}::{sel[0]}")
        if module_name:
            self._show_module(module_name)

    def _show_module(self, module_name: str):
        if not self.project or module_name not in self.project.modules:
            return
        pm = self.project.modules[module_name]
        self.selected_module = module_name
        try:
            source = pm.path.read_text(encoding="utf-8")
        except OSError as exc:
            source = f"// Could not read {pm.path}: {exc}"
        self.src.delete("1.0", "end")
        self.src.insert("1.0", source)
        self.dst.delete("1.0", "end")
        if pm.result.vhdl:
            self.dst.insert("1.0", pm.result.vhdl)
        if self.translation_done:
            self._set_status(f"Viewing {pm.path.name}  →  {module_name}.vhdl", "pass" if pm.result.ok else "warn")
        else:
            self._set_status(f"Viewing {pm.path.name} — VHDL not generated yet", "info")

    def toggle_report(self):
        """Maximize/restore the diagnostics pane without opening another window."""
        # Expanding a collapsed report first makes its body visible.
        if self.report_collapsed:
            self.report_collapsed = False
            self.diag_frame.grid()
            self.report_collapse.configure(text="▁  Minimize Report")
        self.report_expanded = not self.report_expanded
        if self.report_expanded:
            self.workspace.grid_remove()
            self.rowconfigure(2, weight=0)
            self.rowconfigure(3, weight=1)
            self.report_outer.grid_configure(sticky="nsew")
            self.diag.configure(height=30)
            self.report_toggle.configure(text="⤡  Restore Workspace")
            self._set_status("Project Translation Report expanded", "info")
        else:
            self.rowconfigure(3, weight=0)
            self.rowconfigure(2, weight=1)
            self.workspace.grid()
            self.report_outer.grid_configure(sticky="ew")
            self.diag.configure(height=8)
            self.report_toggle.configure(text="⤢  Expand Report")
            self._set_status("Workspace restored", "info")

    def toggle_report_collapse(self):
        """Collapse the report body to its header, or show it again."""
        # If currently maximized, restore the workspace first so collapse really
        # gives the hierarchy/source editors the available vertical space.
        if self.report_expanded:
            self.report_expanded = False
            self.rowconfigure(3, weight=0)
            self.rowconfigure(2, weight=1)
            self.workspace.grid()
            self.report_outer.grid_configure(sticky="ew")
            self.diag.configure(height=8)
            self.report_toggle.configure(text="⤢  Expand Report")

        self.report_collapsed = not self.report_collapsed
        if self.report_collapsed:
            self.diag_frame.grid_remove()
            self.report_collapse.configure(text="▴  Show Report")
            self._set_status("Project Translation Report minimized", "info")
        else:
            self.diag_frame.grid()
            self.report_collapse.configure(text="▁  Minimize Report")
            self._set_status("Project Translation Report restored", "info")

    @staticmethod
    def _group_vivado_warnings(warnings: list[str]) -> list[tuple[str, list[str]]]:
        """Group Vivado warnings by generated VHDL file and shorten temp paths."""
        groups: dict[str, list[str]] = {}
        for warning in warnings:
            m = re.search(r"[\[/]([^\]/]+\.vhdl)(?::(\d+))?\]?", warning, re.I)
            source = m.group(1) if m else "Vivado / project"
            line = m.group(2) if m else None
            cleaned = warning
            # Replace the long temporary verification path with a compact source marker.
            cleaned = re.sub(r"\[[A-Za-z]:[^\]]*[/\\]([^/\\]+\.vhdl):(\d+)\]",
                             lambda x: f"[{x.group(1)}:{x.group(2)}]", cleaned)
            if line and f"[{source}:{line}]" not in cleaned:
                cleaned += f" [{source}:{line}]"
            groups.setdefault(source, []).append(cleaned)
        return sorted(groups.items(), key=lambda item: item[0].lower())

    @staticmethod
    def _classify_vivado_warning(warning: str) -> str:
        """Classify Vivado warnings without hiding them.

        XPM macros expand into fairly large vendor-owned implementation modules.
        Vivado commonly reports dozens of unused/unconnected-port warnings from
        those internals even when the user's XPM instance is valid.  Keep those
        visible as a summarized vendor category while preserving user/project
        warnings individually.
        """
        low = warning.lower()
        if (
            "xpm_memory" in low
            or "/xpm/" in low
            or "\\xpm\\" in low
            or "xpm.vcomponents" in low
        ):
            return "Vendor XPM internals"
        return "Project / generated VHDL"

    @classmethod
    def _summarize_vendor_warnings(cls, warnings: list[str]) -> list[str]:
        """Collapse repetitive vendor warnings by Vivado message code."""
        buckets: dict[str, list[str]] = {}
        for warning in warnings:
            m = re.search(r"\[(Synth\s+\d+-\d+)\]", warning, re.I)
            code = m.group(1) if m else "Vivado warning"
            buckets.setdefault(code, []).append(warning)

        lines: list[str] = []
        for code, items in sorted(buckets.items(), key=lambda kv: kv[0].lower()):
            if len(items) == 1:
                lines.append(items[0])
                continue

            sample = items[0]
            if code.lower() == "synth 8-7129":
                lines.append(
                    f"[{code}] {len(items)} vendor XPM port/no-load warnings "
                    "(collapsed; generated inside AMD/Xilinx XPM implementation)"
                )
            else:
                lines.append(f"[{code}] {len(items)} vendor XPM warning(s) (collapsed)")
                lines.append(f"    example: {sample}")
        return lines

    def _update_report(self):
        entries: list[tuple[str, str]] = []
        if self.project:
            for name, pm in sorted(self.project.modules.items()):
                if self.translation_done:
                    if pm.result.ok:
                        entries.append((f"PASS   {pm.path.name}  →  {name}.vhdl\n", "pass"))
                    else:
                        entries.append((f"FAIL   {pm.path.name}  →  translation incomplete\n", "fail"))
                else:
                    entries.append((f"ANALYZED   {pm.path.name}  →  module {name}\n", "info"))
                for d in pm.result.diagnostics:
                    txt = d.format()
                    tag = "fail" if "ERROR" in txt.upper() else "warn"
                    entries.append((f"       {txt}\n", tag))
            for d in self.project.diagnostics:
                tag = "fail" if "error" in d.lower() else "warn"
                entries.append((f"{d}\n", tag))
            roots = ", ".join(r.module_name for r in self.project.roots) or "none"
            entries.append((f"Hierarchy root(s): {roots}\n", "muted"))
            if self.translation_done:
                entries.append((f"Translated: {self.project.translated_count}/{len(self.project.modules)} modules\n", "pass" if self.project.ok else "warn"))
                if self.verify_running:
                    entries.append(("Vivado verification: RUNNING...", "info"))
                elif self.verify_result is None:
                    entries.append(("Vivado verification: not run yet", "muted"))
                elif self.verify_result.ok:
                    if self.verify_result.warning_count:
                        entries.append((f"Vivado VERIFIED WITH WARNINGS: top={self.verify_result.top} | part={self.verify_result.part} | 0 errors | {self.verify_result.warning_count} warning(s)\n", "warn"))
                        # Separate actionable project/generated warnings from
                        # repetitive vendor-XPM implementation noise.
                        categories: dict[str, list[str]] = {}
                        for warning in self.verify_result.warnings:
                            category = self._classify_vivado_warning(warning)
                            categories.setdefault(category, []).append(warning)

                        project_warnings = categories.get("Project / generated VHDL", [])
                        vendor_warnings = categories.get("Vendor XPM internals", [])

                        if project_warnings:
                            entries.append((f"Project / generated VHDL — {len(project_warnings)} warning(s)\n", "warn"))
                            for source, warnings in self._group_vivado_warnings(project_warnings):
                                entries.append((f"    {source} — {len(warnings)} warning(s)\n", "warn"))
                                for warning in warnings:
                                    entries.append((f"        {warning}\n", "warn"))

                        if vendor_warnings:
                            entries.append((
                                f"Vendor XPM internals — {len(vendor_warnings)} warning(s) "
                                "(summarized; full count preserved)\n",
                                "muted",
                            ))
                            for warning in self._summarize_vendor_warnings(vendor_warnings):
                                entries.append((f"    {warning}\n", "muted"))
                    else:
                        entries.append((f"Vivado VERIFIED CLEAN: top={self.verify_result.top} | part={self.verify_result.part} | 0 errors | 0 warnings\n", "pass"))
                else:
                    entries.append((f"Vivado verification FAILED: {self.verify_result.error_count} error(s) | {self.verify_result.warning_count} warning(s)\n", "fail"))
                    for error in self.verify_result.errors:
                        entries.append((f"       {error}\n", "fail"))
                    for warning in self.verify_result.warnings:
                        entries.append((f"       {warning}\n", "warn"))
            else:
                entries.append(("Translation: not run yet", "muted"))
        else:
            entries.append(("No project loaded.", "muted"))

        self.diag.configure(state="normal")
        self.diag.delete("1.0", "end")
        for text, tag in entries:
            self.diag.insert("end", text, tag)
        self.diag.configure(state="disabled")

    def vivado_setup(self):
        """Configure Vivado launcher and FPGA Discovery board target.

        The verification backend still needs an FPGA part because Vivado's
        synth_design -part elaborates against a concrete device. Users select a
        board here; the exact part string is derived automatically.
        """
        dialog = tk.Toplevel(self)
        dialog.title("Vivado Setup")
        dialog.transient(self)
        dialog.grab_set()
        dialog.resizable(False, False)
        dialog.configure(bg=BG)

        outer = ttk.Frame(dialog, padding=18)
        outer.grid(row=0, column=0, sticky="nsew")

        ttk.Label(
            outer,
            text="Vivado Verification Setup",
            font=("Segoe UI", 12, "bold"),
        ).grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 4))

        ttk.Label(
            outer,
            text="Choose the FPGA Discovery board used as the Vivado synthesis target.",
            foreground=MUTED,
        ).grid(row=1, column=0, columnspan=3, sticky="w", pady=(0, 14))

        ttk.Label(outer, text="Vivado launcher:").grid(row=2, column=0, sticky="w", pady=4)

        current = discover_vivado(self.settings.get("vivado_path", "")) or self.settings.get("vivado_path", "")
        path_var = tk.StringVar(value=current)
        path_entry = ttk.Entry(outer, textvariable=path_var, width=66)
        path_entry.grid(row=2, column=1, sticky="ew", padx=(8, 8), pady=4)

        def browse_vivado():
            chosen = filedialog.askopenfilename(
                parent=dialog,
                title="Select Vivado launcher",
                initialfile=Path(path_var.get()).name if path_var.get() else "vivado.bat",
                filetypes=[
                    ("Vivado launcher", "vivado.bat vivado.exe"),
                    ("Batch files", "*.bat"),
                    ("Executables", "*.exe"),
                    ("All files", "*.*"),
                ],
            )
            if chosen:
                path_var.set(chosen)

        ttk.Button(outer, text="Browse…", command=browse_vivado).grid(row=2, column=2, pady=4)

        ttk.Separator(outer, orient="horizontal").grid(
            row=3, column=0, columnspan=3, sticky="ew", pady=(12, 12)
        )

        ttk.Label(
            outer,
            text="Verification board",
            font=("Segoe UI", 10, "bold"),
        ).grid(row=4, column=0, columnspan=3, sticky="w", pady=(0, 6))

        saved_board = self.settings.get("vivado_board", DEFAULT_BOARD)
        if saved_board not in BOARD_PRESETS:
            # Migrate older settings that only stored a raw part.
            saved_part = self.settings.get("vivado_part", DEFAULT_PART)
            matches = [name for name, part in BOARD_PRESETS.items() if part == saved_part]
            saved_board = matches[0] if matches else DEFAULT_BOARD

        board_var = tk.StringVar(value=saved_board)
        part_var = tk.StringVar(value=BOARD_PRESETS[saved_board])

        def board_changed():
            part_var.set(BOARD_PRESETS[board_var.get()])

        board_frame = ttk.Frame(outer)
        board_frame.grid(row=5, column=0, columnspan=3, sticky="w")

        for row, board_name in enumerate(BOARD_PRESETS):
            rb = ttk.Radiobutton(
                board_frame,
                text=board_name,
                value=board_name,
                variable=board_var,
                command=board_changed,
            )
            rb.grid(row=row, column=0, sticky="w", pady=2)

        ttk.Label(board_frame, text="Vivado part:", foreground=MUTED).grid(
            row=0, column=1, sticky="w", padx=(28, 8)
        )
        ttk.Label(board_frame, textvariable=part_var).grid(
            row=0, column=2, sticky="w"
        )

        ttk.Label(
            outer,
            text=(
                "The board choice is used only for Vivado RTL verification. "
                "It does not make otherwise portable RTL board-specific."
            ),
            foreground=MUTED,
            wraplength=650,
        ).grid(row=6, column=0, columnspan=3, sticky="w", pady=(12, 12))

        buttons = ttk.Frame(outer)
        buttons.grid(row=7, column=0, columnspan=3, sticky="e")

        def save_and_close():
            self.settings["vivado_path"] = path_var.get().strip()
            self.settings["vivado_board"] = board_var.get()
            self.settings["vivado_part"] = BOARD_PRESETS[board_var.get()]
            self._save_settings()

            found = discover_vivado(self.settings.get("vivado_path", ""))
            if found:
                self.settings["vivado_path"] = found
                self._save_settings()
                self._set_status(
                    f"Vivado configured: {Path(found).name} | "
                    f"{self.settings['vivado_board']} | {self.settings['vivado_part']}",
                    "pass",
                )
            else:
                self._set_status("Vivado launcher is not configured", "warn")
            dialog.destroy()

        ttk.Button(buttons, text="Cancel", command=dialog.destroy).grid(row=0, column=0, padx=(0, 8))
        ttk.Button(buttons, text="Save", command=save_and_close).grid(row=0, column=1)

        outer.columnconfigure(1, weight=1)
        dialog.update_idletasks()
        dialog.geometry(f"+{self.winfo_rootx()+140}+{self.winfo_rooty()+110}")
        dialog.wait_window()

    def verify_vhdl(self):
        if self.verify_running:
            return
        if not self.project or not self.translation_done:
            messagebox.showinfo("Project not translated", "Translate the project before running Vivado verification.")
            return
        if not self.project.ok:
            messagebox.showwarning("Translation incomplete", "Fix translator errors before running Vivado verification.")
            return
        roots = [r.module_name for r in self.project.roots if r.resolved]
        if len(roots) != 1:
            messagebox.showwarning(
                "Top entity is ambiguous",
                "Vivado verification currently requires one hierarchy root.\n\nDetected roots: " + (", ".join(roots) or "none"),
            )
            return

        found = discover_vivado(self.settings.get("vivado_path", ""))
        if not found:
            if messagebox.askyesno("Vivado not found", "Vivado was not detected automatically. Open Vivado Setup now?"):
                self.vivado_setup()
                found = discover_vivado(self.settings.get("vivado_path", ""))
            if not found:
                return
        self.settings["vivado_path"] = found
        self._save_settings()

        # Export generated VHDL into a private temporary source folder.  Vivado
        # never touches the user's Verilog files or exported project directory.
        import tempfile
        temp_src = Path(tempfile.mkdtemp(prefix="fpga_discovery_verify_src_"))
        vhdl_files = self.project_translator.export_vhdl(self.project, temp_src)
        top = roots[0]
        part = self.settings.get("vivado_part", DEFAULT_PART).strip() or DEFAULT_PART

        self.verify_running = True
        self.verify_result = None
        self.verify_button.state(["disabled"])
        self._set_status(f"Vivado verification running in background — top: {top}", "info")
        self._update_report()

        def worker():
            result = verify_vhdl_files(
                found, vhdl_files, top, part,
                resource_files=list(getattr(self.project, "resources", [])),
            )
            self.after(0, lambda: self._verification_finished(result))

        threading.Thread(target=worker, daemon=True).start()

    def _verification_finished(self, result: VivadoVerificationResult):
        self.verify_running = False
        self.verify_result = result
        self.verify_button.state(["!disabled"])
        self._update_report()
        if result.ok:
            if result.warning_count:
                self._set_status(f"VIVADO VERIFIED WITH WARNINGS — {result.top} | 0 errors | {result.warning_count} warning(s)", "warn")
            else:
                self._set_status(f"VIVADO VERIFIED CLEAN — {result.top} | 0 errors | 0 warnings", "pass")
        else:
            self._set_status(f"Vivado verification FAILED — {result.error_count} error(s)", "fail")
            # Keep the detailed diagnostics in the report; a compact dialog is
            # enough to tell the user where to look without flooding the screen.
            messagebox.showerror(
                "Vivado Verification Failed",
                f"Vivado found {result.error_count} error(s) while checking generated VHDL.\n\nSee the Project Translation Report for details.",
                parent=self,
            )

    def copy_vhdl(self):
        vhdl = self.dst.get("1.0", "end-1c")
        if not vhdl.strip():
            messagebox.showinfo("Nothing to copy", "Select a translated module/entity first.")
            return
        self.clipboard_clear()
        self.clipboard_append(vhdl)
        self.update_idletasks()
        self._set_status(f"Copied {self.selected_module or 'generated'} VHDL to clipboard", "pass")

    def export_project(self):
        if not self.project or not self.translation_done:
            messagebox.showinfo("Project not translated", "Press Translate Files before exporting VHDL files.")
            return
        name = filedialog.askdirectory(title="Choose VHDL output folder")
        if not name:
            return
        written = self.project_translator.export_vhdl(self.project, Path(name))
        if not written:
            messagebox.showwarning("Nothing exported", "No successfully translated VHDL entities were available.")
            return
        self._set_status(f"Exported {len(written)} VHDL file(s) to {name}", "pass")

    def clear(self):
        if self.quick_translate_dir:
            shutil.rmtree(self.quick_translate_dir, ignore_errors=True)
            self.quick_translate_dir = None
        self.project = None
        self.source_paths = []
        self.selected_module = None
        self.translation_done = False
        self.verify_result = None
        self.verify_running = False
        self.src.delete("1.0", "end")
        self.dst.delete("1.0", "end")
        self.src_tree.delete(*self.src_tree.get_children())
        self.dst_tree.delete(*self.dst_tree.get_children())
        self.diag.configure(state="normal")
        self.diag.delete("1.0", "end")
        self.diag.insert("1.0", "No project loaded.", "muted")
        self.diag.configure(state="disabled")
        self.project_label.configure(text="NO PROJECT")
        self._set_status("Ready — import Verilog files or paste Verilog source to begin", "info")


if __name__ == "__main__":
    TranslatorApp().mainloop()
