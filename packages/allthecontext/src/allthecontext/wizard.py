"""Native first-run setup wizard for All The Context."""

from __future__ import annotations

import queue
import threading
import tkinter as tk
from collections.abc import Callable
from datetime import datetime
from tkinter import messagebox
from typing import Any

from .client_config import codex_is_detected
from .desktop_runtime import RuntimeCommand
from .desktop_setup import SetupOptions, SetupResult, open_dashboard, perform_setup

INK = "#17201d"
INK_SOFT = "#26332e"
PAPER = "#f4f0e7"
PAPER_LIGHT = "#fbf9f4"
MUTED = "#6e756f"
LINE = "#d9d3c7"
AMBER = "#c7762b"
AMBER_HOVER = "#ad6121"
SUCCESS = "#39795d"
WHITE = "#ffffff"

SetupRunner = Callable[..., SetupResult]


class SetupWizard:
    def __init__(
        self,
        root: tk.Tk,
        *,
        runtime: RuntimeCommand | None = None,
        setup_runner: SetupRunner = perform_setup,
    ) -> None:
        self.root = root
        self.runtime = runtime or RuntimeCommand.current()
        self.setup_runner = setup_runner
        self.events: queue.Queue[tuple[str, Any]] = queue.Queue()
        self.result: SetupResult | None = None
        self.working = False
        self.current_step = 0
        self.codex_detected = codex_is_detected()
        self.vault_name = tk.StringVar(value="My Context")
        timezone = datetime.now().astimezone().tzname() or "Local time"
        self.timezone = tk.StringVar(value=timezone)
        self.configure_codex = tk.BooleanVar(value=self.codex_detected)
        self.start_at_login = tk.BooleanVar(value=True)
        self.progress_rows: dict[str, tuple[tk.Label, tk.Label]] = {}

        self._configure_window()
        self._build_shell()
        self.show_welcome()
        self._fade_in()

    def _configure_window(self) -> None:
        self.root.title("Set up All The Context")
        self.root.geometry("900x590")
        self.root.minsize(820, 540)
        self.root.configure(bg=PAPER)
        self.root.protocol("WM_DELETE_WINDOW", self._close)
        self.root.update_idletasks()
        x = max((self.root.winfo_screenwidth() - 900) // 2, 0)
        y = max((self.root.winfo_screenheight() - 590) // 2, 0)
        self.root.geometry(f"900x590+{x}+{y}")

    def _build_shell(self) -> None:
        self.sidebar = tk.Frame(self.root, bg=INK, width=265)
        self.sidebar.pack(side="left", fill="y")
        self.sidebar.pack_propagate(False)

        brand = tk.Frame(self.sidebar, bg=INK)
        brand.pack(fill="x", padx=30, pady=(34, 12))
        mark = tk.Canvas(brand, width=32, height=32, bg=INK, highlightthickness=0)
        mark.pack(side="left", padx=(0, 11))
        mark.create_oval(2, 2, 30, 30, outline=AMBER, width=2)
        mark.create_line(10, 11, 22, 11, fill=AMBER, width=2)
        mark.create_line(10, 16, 22, 16, fill=AMBER, width=2)
        mark.create_line(10, 21, 18, 21, fill=AMBER, width=2)
        tk.Label(
            brand,
            text="ALL THE\nCONTEXT",
            bg=INK,
            fg=WHITE,
            justify="left",
            font=("Segoe UI", 11, "bold"),
        ).pack(side="left")

        tk.Label(
            self.sidebar,
            text="Private context, owned by you.",
            bg=INK,
            fg="#aab4af",
            anchor="w",
            font=("Segoe UI", 9),
        ).pack(fill="x", padx=31, pady=(3, 38))

        self.step_names = ("Welcome", "Your setup", "Install", "Ready")
        self.step_labels: list[tk.Label] = []
        for index, text in enumerate(self.step_names):
            row = tk.Frame(self.sidebar, bg=INK)
            row.pack(fill="x", padx=30, pady=7)
            number = tk.Label(
                row,
                text=str(index + 1),
                width=2,
                bg=INK,
                fg="#6f7d77",
                font=("Segoe UI", 9, "bold"),
            )
            number.pack(side="left")
            label = tk.Label(
                row,
                text=text,
                bg=INK,
                fg="#6f7d77",
                anchor="w",
                font=("Segoe UI", 10),
            )
            label.pack(side="left", padx=(12, 0))
            self.step_labels.append(label)

        tk.Label(
            self.sidebar,
            text="Core stays on 127.0.0.1\nRaw sources never leave this device.",
            bg=INK,
            fg="#84928c",
            justify="left",
            font=("Segoe UI", 8),
        ).pack(side="bottom", anchor="w", padx=31, pady=30)

        self.stage = tk.Frame(self.root, bg=PAPER)
        self.stage.pack(side="left", fill="both", expand=True)
        self.progress_canvas = tk.Canvas(self.stage, height=3, bg=LINE, highlightthickness=0)
        self.progress_canvas.pack(fill="x")
        self.progress_fill = self.progress_canvas.create_rectangle(0, 0, 0, 3, fill=AMBER, width=0)
        self.content = tk.Frame(self.stage, bg=PAPER)
        self.content.pack(fill="both", expand=True, padx=58, pady=45)

    def _clear(self) -> None:
        for child in self.content.winfo_children():
            child.destroy()

    def _set_step(self, step: int) -> None:
        self.current_step = step
        for index, label in enumerate(self.step_labels):
            if index < step:
                label.configure(fg="#9ba9a3", text=f"✓  {self.step_names[index]}")
            elif index == step:
                label.configure(fg=WHITE, text=self.step_names[index])
            else:
                label.configure(fg="#6f7d77", text=self.step_names[index])
        self.root.update_idletasks()
        width = max(self.progress_canvas.winfo_width(), 1)
        target = width * (step + 1) / 4
        self._animate_progress(target)

    def _animate_progress(self, target: float) -> None:
        current = self.progress_canvas.coords(self.progress_fill)[2]
        distance = target - current
        if abs(distance) < 2:
            self.progress_canvas.coords(self.progress_fill, 0, 0, target, 3)
            return
        self.progress_canvas.coords(self.progress_fill, 0, 0, current + distance * 0.28, 3)
        self.root.after(16, lambda: self._animate_progress(target))

    def _eyebrow(self, text: str) -> None:
        tk.Label(
            self.content,
            text=text.upper(),
            bg=PAPER,
            fg=AMBER,
            anchor="w",
            font=("Segoe UI", 8, "bold"),
        ).pack(fill="x", pady=(0, 12))

    def _heading(self, text: str, body: str) -> None:
        tk.Label(
            self.content,
            text=text,
            bg=PAPER,
            fg=INK,
            anchor="w",
            justify="left",
            font=("Segoe UI Variable Display", 27, "bold"),
        ).pack(fill="x")
        tk.Label(
            self.content,
            text=body,
            bg=PAPER,
            fg=MUTED,
            anchor="w",
            justify="left",
            wraplength=500,
            font=("Segoe UI", 10),
        ).pack(fill="x", pady=(13, 0))

    def _button(self, parent: tk.Widget, text: str, command: Callable[[], None]) -> tk.Button:
        button = tk.Button(
            parent,
            text=text,
            command=command,
            bg=AMBER,
            fg=WHITE,
            activebackground=AMBER_HOVER,
            activeforeground=WHITE,
            relief="flat",
            bd=0,
            cursor="hand2",
            padx=22,
            pady=11,
            font=("Segoe UI", 10, "bold"),
        )
        button.bind("<Enter>", lambda _event: button.configure(bg=AMBER_HOVER))
        button.bind("<Leave>", lambda _event: button.configure(bg=AMBER))
        return button

    def _footer(self, primary_text: str, primary_command: Callable[[], None]) -> None:
        footer = tk.Frame(self.content, bg=PAPER)
        footer.pack(side="bottom", fill="x")
        self._button(footer, primary_text, primary_command).pack(side="right")

    def show_welcome(self) -> None:
        self._clear()
        self._set_step(0)
        self._eyebrow("One private memory layer")
        self._heading(
            "Set it once.\nKeep your context.",
            "All The Context gives your AI clients one approved, portable memory—without "
            "uploading your raw sources to a provider.",
        )
        proof = tk.Frame(self.content, bg=PAPER)
        proof.pack(fill="x", pady=(38, 0))
        for title, body in (
            ("Local authority", "Complete context and provenance stay in Core."),
            ("Review first", "Models propose memories; you decide what becomes canonical."),
            ("Connect once", "The wizard installs MCP and starts Core for you."),
        ):
            row = tk.Frame(proof, bg=PAPER)
            row.pack(fill="x", pady=8)
            tk.Label(row, text="●", bg=PAPER, fg=AMBER, font=("Segoe UI", 8)).pack(
                side="left", anchor="n", pady=2
            )
            copy = tk.Frame(row, bg=PAPER)
            copy.pack(side="left", fill="x", padx=(14, 0))
            tk.Label(
                copy, text=title, bg=PAPER, fg=INK, anchor="w", font=("Segoe UI", 10, "bold")
            ).pack(fill="x")
            tk.Label(copy, text=body, bg=PAPER, fg=MUTED, anchor="w", font=("Segoe UI", 9)).pack(
                fill="x", pady=(2, 0)
            )
        self._footer("Continue  →", self.show_preferences)

    def _field(self, label: str, variable: tk.StringVar) -> None:
        tk.Label(
            self.content,
            text=label,
            bg=PAPER,
            fg=INK,
            anchor="w",
            font=("Segoe UI", 9, "bold"),
        ).pack(fill="x", pady=(21, 6))
        entry = tk.Entry(
            self.content,
            textvariable=variable,
            bg=PAPER_LIGHT,
            fg=INK,
            insertbackground=INK,
            relief="flat",
            highlightthickness=1,
            highlightbackground=LINE,
            highlightcolor=AMBER,
            font=("Segoe UI", 10),
        )
        entry.pack(fill="x", ipady=10)

    def _check(
        self, text: str, body: str, variable: tk.BooleanVar, *, enabled: bool = True
    ) -> None:
        row = tk.Frame(self.content, bg=PAPER)
        row.pack(fill="x", pady=10)
        check = tk.Checkbutton(
            row,
            variable=variable,
            bg=PAPER,
            activebackground=PAPER,
            selectcolor=PAPER_LIGHT,
            fg=INK,
            state="normal" if enabled else "disabled",
            cursor="hand2" if enabled else "arrow",
        )
        check.pack(side="left", anchor="n", pady=2)
        copy = tk.Frame(row, bg=PAPER)
        copy.pack(side="left", fill="x", padx=(8, 0))
        tk.Label(
            copy,
            text=text,
            bg=PAPER,
            fg=INK if enabled else MUTED,
            anchor="w",
            font=("Segoe UI", 9, "bold"),
        ).pack(fill="x")
        tk.Label(
            copy,
            text=body,
            bg=PAPER,
            fg=MUTED,
            anchor="w",
            font=("Segoe UI", 8),
        ).pack(fill="x", pady=(2, 0))

    def show_preferences(self) -> None:
        self._clear()
        self._set_step(1)
        self._eyebrow("Your setup")
        self._heading(
            "A few local choices.",
            "These settings stay on this device. You can change access and availability later.",
        )
        self._field("Vault name", self.vault_name)
        self._field("Display timezone", self.timezone)
        self._check(
            "Start Core when I sign in",
            "Runs in your user account; no administrator access or Docker.",
            self.start_at_login,
        )
        self._check(
            "Connect Codex automatically",
            "Updates your personal config.toml and keeps a timestamped backup.",
            self.configure_codex,
            enabled=self.codex_detected,
        )
        if not self.codex_detected:
            tk.Label(
                self.content,
                text="Codex was not detected. The app will still install and run normally.",
                bg=PAPER,
                fg=MUTED,
                anchor="w",
                font=("Segoe UI", 8),
            ).pack(fill="x", padx=31)
        self._footer("Install All The Context", self.start_install)

    def start_install(self) -> None:
        if not self.vault_name.get().strip():
            messagebox.showerror("Vault name required", "Give your local context vault a name.")
            return
        self._clear()
        self._set_step(2)
        self._eyebrow("Installing")
        self._heading(
            "Setting up your Core.",
            "This usually takes a few seconds. Your context database is created locally.",
        )
        rows = tk.Frame(self.content, bg=PAPER)
        rows.pack(fill="x", pady=(36, 0))
        self.progress_rows.clear()
        for key, label in (
            ("vault", "Private local vault"),
            ("credential", "Secure client credential"),
            ("client", "MCP client connection"),
            ("startup", "Background startup"),
            ("core", "Core health check"),
        ):
            row = tk.Frame(rows, bg=PAPER)
            row.pack(fill="x", pady=7)
            status = tk.Label(row, text="○", bg=PAPER, fg=LINE, width=2, font=("Segoe UI", 11))
            status.pack(side="left")
            text = tk.Label(row, text=label, bg=PAPER, fg=MUTED, anchor="w", font=("Segoe UI", 9))
            text.pack(side="left", fill="x", padx=(12, 0))
            self.progress_rows[key] = (status, text)
        self.status_copy = tk.Label(
            self.content,
            text="Preparing…",
            bg=PAPER,
            fg=MUTED,
            anchor="w",
            font=("Segoe UI", 8),
        )
        self.status_copy.pack(side="bottom", fill="x")
        self.working = True
        options = SetupOptions(
            vault_name=self.vault_name.get().strip(),
            timezone=self.timezone.get().strip() or "UTC",
            configure_codex=self.configure_codex.get(),
            start_at_login=self.start_at_login.get(),
        )
        thread = threading.Thread(target=self._setup_worker, args=(options,), daemon=True)
        thread.start()
        self.root.after(60, self._poll_events)

    def _setup_worker(self, options: SetupOptions) -> None:
        try:
            result = self.setup_runner(
                options,
                self.runtime,
                progress=lambda step, message: self.events.put(("progress", (step, message))),
            )
            self.events.put(("done", result))
        except Exception as exc:
            self.events.put(("error", exc))

    def _poll_events(self) -> None:
        try:
            while True:
                kind, payload = self.events.get_nowait()
                if kind == "progress":
                    step, message = payload
                    self._show_progress(step, message)
                elif kind == "done":
                    self.working = False
                    self.result = payload
                    self.show_complete()
                    return
                elif kind == "error":
                    self.working = False
                    self.show_error(payload)
                    return
        except queue.Empty:
            pass
        if self.working:
            self.root.after(60, self._poll_events)

    def _show_progress(self, step: str, message: str) -> None:
        keys = list(self.progress_rows)
        active_index = keys.index(step) if step in keys else len(keys)
        for index, key in enumerate(keys):
            icon, label = self.progress_rows[key]
            if index < active_index:
                icon.configure(text="✓", fg=SUCCESS)
                label.configure(fg=INK)
            elif index == active_index:
                icon.configure(text="●", fg=AMBER)
                label.configure(fg=INK)
        self.status_copy.configure(text=message)

    def show_complete(self) -> None:
        self._clear()
        self._set_step(3)
        self._eyebrow("Setup complete")
        self._heading(
            "You're ready.",
            "Core is running privately on this device. Open the dashboard now; normal context "
            "retrieval and proposals happen through MCP from here on.",
        )
        summary = tk.Frame(self.content, bg=PAPER)
        summary.pack(fill="x", pady=(38, 0))
        details = [
            ("Core", "Running on 127.0.0.1:7337"),
            (
                "Credential",
                f"Stored in {self.result.credential_storage}" if self.result else "Ready",
            ),
            (
                "Codex",
                "Connected—restart Codex once"
                if self.result and self.result.codex
                else "Not changed",
            ),
            (
                "Startup",
                "Enabled for this user" if self.result and self.result.startup else "Manual",
            ),
        ]
        for label, value in details:
            row = tk.Frame(summary, bg=PAPER)
            row.pack(fill="x", pady=8)
            tk.Label(
                row, text=label, bg=PAPER, fg=MUTED, width=12, anchor="w", font=("Segoe UI", 9)
            ).pack(side="left")
            tk.Label(
                row, text=value, bg=PAPER, fg=INK, anchor="w", font=("Segoe UI", 9, "bold")
            ).pack(side="left")
        if self.result and self.result.warnings:
            tk.Label(
                self.content,
                text="\n".join(self.result.warnings),
                bg=PAPER,
                fg=AMBER_HOVER,
                anchor="w",
                justify="left",
                wraplength=500,
                font=("Segoe UI", 8),
            ).pack(fill="x", pady=(18, 0))
        self._footer("Open All The Context  →", self.finish)

    def show_error(self, error: Exception) -> None:
        self._clear()
        self._set_step(2)
        self._eyebrow("Setup needs attention")
        self._heading(
            "We couldn't finish setup.",
            "Nothing was uploaded. Review the message below, then try again.",
        )
        tk.Label(
            self.content,
            text=str(error),
            bg="#eee6d8",
            fg=INK,
            justify="left",
            anchor="nw",
            wraplength=500,
            padx=18,
            pady=16,
            font=("Consolas", 8),
        ).pack(fill="x", pady=(32, 0))
        self._footer("Try again", self.show_preferences)

    def finish(self) -> None:
        if self.result is not None:
            open_dashboard(self.result.dashboard_url)
        self.root.destroy()

    def _close(self) -> None:
        if self.working:
            messagebox.showinfo("Setup in progress", "Please wait for setup to finish.")
            return
        self.root.destroy()

    def _fade_in(self) -> None:
        try:
            self.root.attributes("-alpha", 0.0)
        except tk.TclError:
            return

        def tick(value: float = 0.0) -> None:
            next_value = min(value + 0.12, 1.0)
            self.root.attributes("-alpha", next_value)
            if next_value < 1.0:
                self.root.after(18, tick, next_value)

        tick()


def run_setup_wizard(runtime: RuntimeCommand | None = None) -> None:
    root = tk.Tk()
    SetupWizard(root, runtime=runtime)
    root.mainloop()
