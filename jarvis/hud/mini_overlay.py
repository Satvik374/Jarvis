"""Native Windows Always-On-Top Floating Cyber Mini HUD Overlay for Jarvis."""

from __future__ import annotations

import math
import queue
import threading
import time
from typing import Any, Callable, Dict, List, Optional
import tkinter as tk
from tkinter import ttk

from ..utils import logging as log

STATE_COLORS = {
    "idle": "#00f0ff",
    "booting": "#00f0ff",
    "listening": "#00ffdd",
    "perceiving": "#bb44ff",
    "thinking": "#7c3aff",
    "planning": "#6622ff",
    "verifying": "#5544ff",
    "acting": "#0099ff",
    "working": "#0088ff",
    "speaking": "#00f0ff",
    "healing": "#ffaa00",
    "success": "#00ff9d",
    "warning": "#ffaa00",
    "error": "#ff4e45",
}


class FloatingMiniHUD:
    """Tkinter-based floating HUD capsule overlay."""

    def __init__(
        self,
        on_submit_command: Optional[Callable[[str], None]] = None,
        on_voice_toggle: Optional[Callable[[], None]] = None,
        on_vision_trigger: Optional[Callable[[], None]] = None,
        on_macro_toggle: Optional[Callable[[], None]] = None,
        position: str = "bottom_right",
        opacity: float = 0.94,
    ):
        self.on_submit_command = on_submit_command
        self.on_voice_toggle = on_voice_toggle
        self.on_vision_trigger = on_vision_trigger
        self.on_macro_toggle = on_macro_toggle
        self.default_position = position
        self.opacity = opacity

        self.state = "idle"
        self.detail_text = "Neural Link Active · Ready"
        self.is_expanded = False
        self.is_macro_recording = False
        self.is_voice_active = False

        self._root: Optional[tk.Tk] = None
        self._canvas: Optional[tk.Canvas] = None
        self._entry: Optional[tk.Entry] = None
        self._msg_queue: queue.Queue = queue.Queue()
        self._running = False
        self._pulse_phase = 0.0

        # Drag tracking
        self._drag_start_x = 0
        self._drag_start_y = 0

    def start(self) -> None:
        """Launch the HUD on its own GUI thread."""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._run_tk, daemon=True, name="jarvis-mini-hud")
        self._thread.start()

    def _run_tk(self) -> None:
        try:
            self._root = tk.Tk()
            self._root.title("JARVIS HUD")
            self._root.overrideredirect(True)
            self._root.attributes("-topmost", True)
            self._root.attributes("-alpha", self.opacity)
            self._root.configure(bg="#02070d")

            self._setup_ui()
            self._position_window()
            self._bind_events()

            self._root.after(40, self._tick_loop)
            self._root.mainloop()
        except Exception as exc:
            log.warn(f"HUD Tkinter runtime closed: {exc}")
        finally:
            self._running = False

    def _setup_ui(self) -> None:
        if not self._root:
            return

        self.width = 420
        self.height = 200

        # Main frame with cyber border
        self._outer_frame = tk.Frame(
            self._root,
            bg="#02070d",
            highlightbackground="#00f0ff",
            highlightcolor="#00f0ff",
            highlightthickness=1,
        )
        self._outer_frame.pack(fill=tk.BOTH, expand=True)

        # Header Bar (Draggable)
        self._header = tk.Frame(self._outer_frame, bg="#05121e", height=26)
        self._header.pack(fill=tk.X, side=tk.TOP)

        self._title_lbl = tk.Label(
            self._header,
            text="JARVIS // NEURAL CORE",
            font=("Consolas", 8, "bold"),
            fg="#00f0ff",
            bg="#05121e",
        )
        self._title_lbl.pack(side=tk.LEFT, padx=8, pady=3)

        self._close_btn = tk.Label(
            self._header,
            text="—",
            font=("Consolas", 9, "bold"),
            fg="#8ba8b7",
            bg="#05121e",
            cursor="hand2",
        )
        self._close_btn.pack(side=tk.RIGHT, padx=8, pady=3)
        self._close_btn.bind("<Button-1>", lambda e: self.toggle_expand())

        # Body Container
        self._body = tk.Frame(self._outer_frame, bg="#02070d")
        self._body.pack(fill=tk.X, padx=8, pady=4)

        # Left: Reactor Core Canvas
        self._canvas = tk.Canvas(
            self._body,
            width=48,
            height=48,
            bg="#02070d",
            highlightthickness=0,
        )
        self._canvas.pack(side=tk.LEFT, padx=(2, 6))

        # Center: State & Details
        self._info_frame = tk.Frame(self._body, bg="#02070d")
        self._info_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        self._state_lbl = tk.Label(
            self._info_frame,
            text="ONLINE",
            font=("Consolas", 10, "bold"),
            fg="#00f0ff",
            bg="#02070d",
            anchor="w",
        )
        self._state_lbl.pack(fill=tk.X)

        self._detail_lbl = tk.Label(
            self._info_frame,
            text="Neural Link Active · Ready",
            font=("Consolas", 7),
            fg="#8ba8b7",
            bg="#02070d",
            anchor="w",
        )
        self._detail_lbl.pack(fill=tk.X, pady=(1, 0))

        # Right: Quick Action Buttons Bar
        self._actions_bar = tk.Frame(self._body, bg="#02070d")
        self._actions_bar.pack(side=tk.RIGHT, padx=2)

        btn_style = {"font": ("Segoe UI", 8), "bg": "#071828", "fg": "#00f0ff", "relief": "flat", "padx": 4, "pady": 2, "cursor": "hand2"}

        self._btn_voice = tk.Button(self._actions_bar, text="🎤", command=self._handle_voice, **btn_style)
        self._btn_voice.pack(side=tk.LEFT, padx=2)

        self._btn_see = tk.Button(self._actions_bar, text="👁️", command=self._handle_vision, **btn_style)
        self._btn_see.pack(side=tk.LEFT, padx=2)

        self._btn_macro = tk.Button(self._actions_bar, text="🔴", command=self._handle_macro, **btn_style)
        self._btn_macro.pack(side=tk.LEFT, padx=2)

        # Middle: Live Conversation & Response Frame
        self._response_frame = tk.Frame(
            self._outer_frame,
            bg="#030c17",
            highlightbackground="#0a324a",
            highlightcolor="#0a324a",
            highlightthickness=1,
        )
        self._response_frame.pack(fill=tk.BOTH, expand=True, padx=8, pady=(0, 4))

        self._response_lbl = tk.Label(
            self._response_frame,
            text="✦ JARVIS: Ready for directive.",
            font=("Consolas", 8),
            fg="#cbe9ff",
            bg="#030c17",
            anchor="nw",
            justify=tk.LEFT,
            wraplength=390,
        )
        self._response_lbl.pack(fill=tk.BOTH, expand=True, padx=6, pady=4)

        # Bottom: Expandable Input Bar
        self._input_frame = tk.Frame(self._outer_frame, bg="#02070d")
        self._input_frame.pack(fill=tk.X, padx=8, pady=(0, 6))

        self._entry = tk.Entry(
            self._input_frame,
            font=("Consolas", 9),
            fg="#e5f8ff",
            bg="#06121f",
            insertbackground="#00f0ff",
            relief="flat",
            highlightbackground="#0a324a",
            highlightcolor="#00f0ff",
            highlightthickness=1,
        )
        self._entry.pack(fill=tk.X, side=tk.LEFT, expand=True, ipady=4, padx=(0, 4))
        self._entry.insert(0, "")
        self._entry.bind("<Return>", self._on_entry_submit)

        self._send_btn = tk.Button(
            self._input_frame,
            text="SEND",
            font=("Consolas", 7, "bold"),
            bg="#00f0ff",
            fg="#010610",
            relief="flat",
            cursor="hand2",
            command=self._on_send_click,
        )
        self._send_btn.pack(side=tk.RIGHT, ipady=2, ipadx=4)

    def _position_window(self) -> None:
        if not self._root:
            return
        sw = self._root.winfo_screenwidth()
        sh = self._root.winfo_screenheight()

        if self.default_position == "top_center":
            x = (sw - self.width) // 2
            y = 20
        elif self.default_position == "top_right":
            x = sw - self.width - 24
            y = 24
        elif self.default_position == "center":
            x = (sw - self.width) // 2
            y = (sh - self.height) // 2
        elif self.default_position == "bottom_left":
            x = 24
            y = sh - self.height - 54
        else:  # bottom_right (default)
            x = sw - self.width - 24
            y = sh - self.height - 54

        self._root.geometry(f"{self.width}x{self.height}+{x}+{y}")

    def _bind_events(self) -> None:
        if not self._header or not self._root:
            return

        # Drag Window logic
        for widget in (self._header, self._title_lbl, self._canvas):
            widget.bind("<Button-1>", self._start_drag)
            widget.bind("<B1-Motion>", self._on_drag)

    def _start_drag(self, event: tk.Event) -> None:
        self._drag_start_x = event.x_root - self._root.winfo_x()
        self._drag_start_y = event.y_root - self._root.winfo_y()

    def _on_drag(self, event: tk.Event) -> None:
        new_x = event.x_root - self._drag_start_x
        new_y = event.y_root - self._drag_start_y
        self._root.geometry(f"+{new_x}+{new_y}")

    # -- Actions ----------------------------------------------------------- #
    def _handle_voice(self) -> None:
        if self.on_voice_toggle:
            threading.Thread(target=self.on_voice_toggle, daemon=True).start()

    def _handle_vision(self) -> None:
        if self.on_vision_trigger:
            threading.Thread(target=self.on_vision_trigger, daemon=True).start()

    def _handle_macro(self) -> None:
        if self.on_macro_toggle:
            threading.Thread(target=self.on_macro_toggle, daemon=True).start()

    def _on_send_click(self) -> None:
        self._on_entry_submit(None)

    def _on_entry_submit(self, event: Optional[tk.Event]) -> None:
        if not self._entry:
            return
        text = self._entry.get().strip()
        if not text:
            return
        self._entry.delete(0, tk.END)
        self.set_state("thinking", detail=f"Processing: {text[:28]}...")

        if self.on_submit_command:
            threading.Thread(target=self.on_submit_command, args=(text,), daemon=True).start()

    def toggle_expand(self) -> None:
        if not self._root:
            return
        self.is_expanded = not self.is_expanded
        cur_geom = self._root.geometry().split("+")
        pos = f"+{cur_geom[1]}+{cur_geom[2]}" if len(cur_geom) > 2 else ""

        if self.is_expanded:
            if hasattr(self, "_response_frame"):
                self._response_frame.pack_forget()
            if self._input_frame:
                self._input_frame.pack_forget()
            self._root.geometry(f"{self.width}x70{pos}")
            self._close_btn.config(text="+")
        else:
            if hasattr(self, "_response_frame"):
                self._response_frame.pack(fill=tk.BOTH, expand=True, padx=8, pady=(0, 4))
            if self._input_frame:
                self._input_frame.pack(fill=tk.X, padx=8, pady=(0, 6))
            self._root.geometry(f"{self.width}x{self.height}{pos}")
            self._close_btn.config(text="—")

    def toggle_visibility(self) -> None:
        if not self._root:
            return
        if self._root.state() == "withdrawn":
            self._root.deiconify()
            self._root.attributes("-topmost", True)
            if self._entry:
                self._entry.focus_set()
        else:
            self._root.withdraw()

    def show(self) -> None:
        if self._root:
            self._root.deiconify()
            self._root.attributes("-topmost", True)
            if self._entry:
                self._entry.focus_set()

    def hide(self) -> None:
        if self._root:
            self._root.withdraw()

    # -- State Updates ----------------------------------------------------- #
    def set_state(self, state_name: str, detail: Optional[str] = None) -> None:
        self._msg_queue.put(("state", state_name.lower(), detail))

    def set_response(self, prompt: str, reply: str) -> None:
        self._msg_queue.put(("response", prompt, reply))

    def set_voice_active(self, active: bool) -> None:
        self._msg_queue.put(("voice", active))

    def set_macro_recording(self, recording: bool) -> None:
        self._msg_queue.put(("macro", recording))

    def _tick_loop(self) -> None:
        if not self._root:
            return

        # 1. Process queued messages
        while not self._msg_queue.empty():
            try:
                item = self._msg_queue.get_nowait()
                if item[0] == "state":
                    _, st, dt = item
                    self.state = st
                    if dt:
                        self.detail_text = dt
                    color = STATE_COLORS.get(self.state, "#00f0ff")
                    if self._state_lbl:
                        self._state_lbl.config(text=self.state.upper(), fg=color)
                    if self._detail_lbl and dt:
                        self._detail_lbl.config(text=dt)
                    if self._outer_frame:
                        self._outer_frame.config(highlightbackground=color, highlightcolor=color)

                elif item[0] == "response":
                    _, prompt, reply = item
                    if self._response_lbl:
                        display_text = f"▸ YOU: {prompt}\n✦ JARVIS: {reply}"
                        self._response_lbl.config(text=display_text)

                elif item[0] == "voice":
                    self.is_voice_active = item[1]
                    if self._btn_voice:
                        self._btn_voice.config(bg="#00ffdd" if self.is_voice_active else "#071828",
                                               fg="#010610" if self.is_voice_active else "#00f0ff")

                elif item[0] == "macro":
                    self.is_macro_recording = item[1]
                    if self._btn_macro:
                        self._btn_macro.config(bg="#ff4e45" if self.is_macro_recording else "#071828",
                                               fg="#ffffff" if self.is_macro_recording else "#00f0ff")
            except Exception:
                break

        # 2. Render Reactor Core Canvas
        self._render_reactor()

        # 3. Schedule next frame (30 FPS)
        self._root.after(33, self._tick_loop)

    def _render_reactor(self) -> None:
        if not self._canvas:
            return

        self._canvas.delete("all")
        color = STATE_COLORS.get(self.state, "#00f0ff")
        self._pulse_phase += 0.12

        cx, cy = 27, 27
        pulse = math.sin(self._pulse_phase) * 2.0

        # Outer arc ring
        r1 = 22 + pulse
        self._canvas.create_oval(cx - r1, cy - r1, cx + r1, cy + r1, outline=color, width=1.5)

        # Rotating dash arcs
        angle = (self._pulse_phase * 40) % 360
        self._canvas.create_arc(cx - r1 + 3, cy - r1 + 3, cx + r1 - 3, cy + r1 - 3,
                                start=angle, extent=65, outline="#ffffff", width=1.5, style="arc")
        self._canvas.create_arc(cx - r1 + 3, cy - r1 + 3, cx + r1 - 3, cy + r1 - 3,
                                start=angle + 180, extent=65, outline="#ffffff", width=1.5, style="arc")

        # Inner pulsing core dot
        r2 = 7 + math.sin(self._pulse_phase * 1.5) * 1.5
        self._canvas.create_oval(cx - r2, cy - r2, cx + r2, cy + r2, fill=color, outline="")

    def stop(self) -> None:
        if self._root:
            try:
                self._root.destroy()
            except Exception:
                pass
        self._running = False
