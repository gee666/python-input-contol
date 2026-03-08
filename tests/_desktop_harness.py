from __future__ import annotations

import argparse
import json
import signal
import sys
import tempfile
from pathlib import Path
from typing import Any

try:
    import tkinter as tk
except ImportError as exc:  # pragma: no cover - exercised only when integration tests are enabled
    raise SystemExit(f"tkinter is required for the desktop integration harness: {exc}")


class DesktopHarnessApp:
    def __init__(self, state_path: Path, focus_target: str, entry_text: str) -> None:
        self.state_path = state_path
        self.focus_target = focus_target
        self.root = tk.Tk(className="PythonInputControlIntegrationHarness")
        self.root.title("python-input-control integration harness")
        self.root.geometry("560x420+120+120")
        self.root.minsize(560, 420)
        try:
            self.root.attributes("-topmost", True)
        except tk.TclError:
            pass

        self.root.protocol("WM_DELETE_WINDOW", self._shutdown)
        self.root.bind("<FocusIn>", lambda _event: self._write_state())
        self.root.bind("<FocusOut>", lambda _event: self._write_state())

        self.click_count = 0
        self.scroll_units_y = 0
        self.raw_wheel_events: list[int] = []
        self.ready_announced = False

        self.viewport = tk.Frame(self.root, bg="#f3f4f6", width=520, height=360)
        self.viewport.pack(fill="both", expand=True, padx=16, pady=16)
        self.viewport.pack_propagate(False)

        heading = tk.Label(
            self.viewport,
            text="python-input-control integration harness",
            anchor="w",
            bg="#f3f4f6",
            font=("TkDefaultFont", 12, "bold"),
        )
        heading.place(x=12, y=12, width=496, height=24)

        self.click_target = tk.Frame(
            self.viewport,
            width=160,
            height=90,
            bg="#93c5fd",
            highlightthickness=2,
            highlightbackground="#1d4ed8",
            takefocus=1,
        )
        self.click_target.place(x=12, y=52)
        self.click_label = tk.Label(self.click_target, text="Click target", bg="#93c5fd")
        self.click_label.place(relx=0.5, rely=0.5, anchor="center")
        for widget in (self.click_target, self.click_label):
            widget.bind("<Button-1>", self._on_click_target)
            widget.bind("<FocusIn>", lambda _event: self._write_state())

        self.entry = tk.Entry(self.viewport, font=("TkDefaultFont", 11))
        self.entry.place(x=12, y=170, width=320, height=32)
        if entry_text:
            self.entry.insert(0, entry_text)
        self.entry.bind("<KeyRelease>", lambda _event: self._write_state())
        self.entry.bind("<FocusIn>", lambda _event: self._write_state())
        self.entry.bind("<FocusOut>", lambda _event: self._write_state())

        self.scroll_canvas = tk.Canvas(
            self.viewport,
            width=420,
            height=120,
            bg="#ffffff",
            highlightthickness=2,
            highlightbackground="#6b7280",
            takefocus=1,
        )
        self.scroll_canvas.place(x=12, y=224)
        self.scroll_canvas.configure(scrollregion=(0, 0, 400, 1800))
        for index in range(60):
            top = 12 + index * 28
            self.scroll_canvas.create_text(
                16,
                top,
                anchor="nw",
                text=f"Scrollable row {index + 1:02d}",
                fill="#111827",
                font=("TkDefaultFont", 10),
            )
        self.scroll_canvas.bind("<MouseWheel>", self._on_mouse_wheel)
        self.scroll_canvas.bind("<Button-4>", self._on_mouse_wheel)
        self.scroll_canvas.bind("<Button-5>", self._on_mouse_wheel)
        self.scroll_canvas.bind("<FocusIn>", lambda _event: self._write_state())
        self.scroll_canvas.bind("<FocusOut>", lambda _event: self._write_state())

        self.status_label = tk.Label(self.viewport, anchor="w", bg="#f3f4f6", justify="left")
        self.status_label.place(x=344, y=170, width=164, height=48)

        self.root.after(150, self._finalize_ready_state)
        self.root.after(250, self._heartbeat)

    def _shutdown(self) -> None:
        self._write_state(terminated=True)
        self.root.after_idle(self.root.destroy)

    def _focused_widget_name(self) -> str | None:
        focused = self.root.focus_get()
        if focused is None:
            return None
        if focused == self.entry:
            return "entry"
        if focused == self.scroll_canvas:
            return "scroll"
        if focused in {self.click_target, self.click_label}:
            return "click"
        return focused.winfo_class().lower()

    def _widget_target(self, widget: tk.Misc) -> dict[str, float]:
        return {
            "x": float(widget.winfo_x()),
            "y": float(widget.winfo_y()),
            "width": float(widget.winfo_width()),
            "height": float(widget.winfo_height()),
            "center_x": float(widget.winfo_x() + widget.winfo_width() / 2.0),
            "center_y": float(widget.winfo_y() + widget.winfo_height() / 2.0),
        }

    def _current_state(self, *, terminated: bool = False) -> dict[str, Any]:
        self.root.update_idletasks()
        yview = self.scroll_canvas.yview()
        state = {
            "ready": True,
            "terminated": terminated,
            "focused_widget": self._focused_widget_name(),
            "viewport": {
                "root_x": float(self.viewport.winfo_rootx()),
                "root_y": float(self.viewport.winfo_rooty()),
                "width": float(self.viewport.winfo_width()),
                "height": float(self.viewport.winfo_height()),
            },
            "targets": {
                "click": self._widget_target(self.click_target),
                "entry": self._widget_target(self.entry),
                "scroll": self._widget_target(self.scroll_canvas),
            },
            "entry_text": self.entry.get(),
            "click_count": self.click_count,
            "scroll_units_y": self.scroll_units_y,
            "raw_wheel_events": self.raw_wheel_events,
            "scroll_yview": [float(yview[0]), float(yview[1])],
        }
        self.status_label.configure(
            text=(
                f"focus={state['focused_widget'] or 'none'}\n"
                f"clicks={self.click_count} scroll={self.scroll_units_y}"
            )
        )
        return state

    def _write_state(self, *, terminated: bool = False) -> dict[str, Any]:
        state = self._current_state(terminated=terminated)
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=str(self.state_path.parent), delete=False) as handle:
            json.dump(state, handle, ensure_ascii=False, sort_keys=True)
            temp_path = Path(handle.name)
        temp_path.replace(self.state_path)
        if not self.ready_announced:
            self.ready_announced = True
            print(json.dumps(state, ensure_ascii=False), flush=True)
        return state

    def _on_click_target(self, _event: tk.Event) -> str:
        self.click_count += 1
        self._write_state()
        return "break"

    def _normalize_mouse_wheel_units(self, event: tk.Event) -> int:
        event_num = getattr(event, "num", None)
        if event_num == 4:
            return 1
        if event_num == 5:
            return -1

        delta = int(getattr(event, "delta", 0))
        if delta == 0:
            return 0
        magnitude = abs(delta)
        if magnitude >= 120:
            units = max(1, int(round(magnitude / 120.0)))
        else:
            units = max(1, magnitude)
        return units if delta > 0 else -units

    def _on_mouse_wheel(self, event: tk.Event) -> str:
        units = self._normalize_mouse_wheel_units(event)
        raw_value = int(getattr(event, "delta", 0)) if hasattr(event, "delta") else int(getattr(event, "num", 0) or 0)
        self.raw_wheel_events.append(raw_value)
        if units:
            self.scroll_units_y += units
            self.scroll_canvas.yview_scroll(-units, "units")
        self._write_state()
        return "break"

    def _apply_requested_focus(self) -> None:
        if self.focus_target == "entry":
            self.entry.focus_force()
            self.entry.icursor("end")
            return
        if self.focus_target == "scroll":
            self.scroll_canvas.focus_force()
            return
        if self.focus_target == "click":
            self.click_target.focus_force()
            return
        self.root.focus_force()

    def _finalize_ready_state(self) -> None:
        self.root.update_idletasks()
        self.root.deiconify()
        self.root.lift()
        self._apply_requested_focus()
        self._write_state()
        self.root.after(1000, self._clear_topmost)

    def _clear_topmost(self) -> None:
        try:
            self.root.attributes("-topmost", False)
        except tk.TclError:
            return

    def _heartbeat(self) -> None:
        if not self.root.winfo_exists():
            return
        self._write_state()
        self.root.after(250, self._heartbeat)


def _install_signal_handlers(app: DesktopHarnessApp) -> None:
    def _handler(_signum, _frame) -> None:
        app.root.after_idle(app._shutdown)

    for signum in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(signum, _handler)
        except ValueError:
            continue


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Desktop harness for integration tests")
    parser.add_argument("--state-file", required=True)
    parser.add_argument("--focus", choices=("none", "entry", "scroll", "click"), default="none")
    parser.add_argument("--entry-text", default="")
    args = parser.parse_args(argv)

    app = DesktopHarnessApp(state_path=Path(args.state_file), focus_target=args.focus, entry_text=args.entry_text)
    _install_signal_handlers(app)
    app.root.mainloop()
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised only in explicit integration runs
    raise SystemExit(main(sys.argv[1:]))
