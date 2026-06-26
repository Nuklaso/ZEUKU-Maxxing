#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
keep_awake_gui.py  --  tkinter/ttk front-end for keep_awake.py (stdlib only).

A small desktop GUI around the TESTED primitives in keep_awake.py. It does NOT
reimplement the ctypes/SendInput layer -- it reuses idle_seconds(), inject(),
keep_display_on(), restore_execution_state() and friends from that module.

THREADING MODEL (the important part)
------------------------------------
The keep-awake loop runs in a background worker thread. That worker NEVER
touches a Tk widget. It talks to the GUI only through a queue.Queue (log lines
and status updates) and reads a threading.Event stop flag. The main/UI thread
drains that queue on a periodic root.after(...) tick and is the only thread that
updates widgets. All loop parameters are snapshotted at Start time, so changing
a spinbox mid-run cannot affect the running worker.

PYINSTALLER NOTES
-----------------
Designed to run as a --windowed --onefile exe. Under --windowed,
sys.stdout/sys.stderr are None, so we route ALL logging through the GUI by
monkeypatching keep_awake.log to our queue sink -- this also captures primitive
warnings (e.g. "SendInput blocked"). 'import keep_awake' resolves both as a
plain script and when frozen, because PyInstaller bundles the imported local
module alongside this one.

Pure Python standard library only (tkinter, ttk, queue, threading, time).
Tested on Windows 11, Python 3.12.6, 64-bit.
"""

import queue
import threading
import time
import tkinter as tk
from tkinter import ttk

import keep_awake


# ---------------------------------------------------------------------------
# Tunables
# ---------------------------------------------------------------------------
QUEUE_POLL_MS = 150          # how often the UI drains the worker->UI queue
LATENCY_WARN_S = 300         # worst-case latency at/above this -> warn color
LATENCY_NEAR_S = 270         # "getting close" advisory color
MAX_LOG_LINES = 2000         # trim the log Text so it can't grow unbounded

INTERVAL_MIN, INTERVAL_MAX, INTERVAL_DEFAULT = 1, 3600, 30
THRESHOLD_MIN, THRESHOLD_MAX, THRESHOLD_DEFAULT = 1, 7200, 240


# ===========================================================================
# Worker: runs in a background thread, NEVER touches Tk.
# ===========================================================================
class KeepAwakeWorker(threading.Thread):
    """Background keep-awake loop mirroring keep_awake.run() semantics.

    Communicates with the GUI exclusively via `out_queue` (tuples of
    (kind, payload)) and the shared `stop_event`. All parameters are plain
    values snapshotted by the caller at Start time.
    """

    def __init__(self, *, session_id, mode, interval, threshold, method,
                 keep_display, out_queue, stop_event):
        super().__init__(name="KeepAwakeWorker", daemon=True)
        self.session_id = session_id      # which run this worker belongs to
        self.mode = mode                  # "smart" or "force"
        self.interval = interval          # int seconds
        self.threshold = threshold        # int seconds (smart mode only)
        self.method = method              # "key" | "mouse" | "both"
        self.keep_display = keep_display  # bool
        self.q = out_queue
        self.stop_event = stop_event

    # -- queue helpers (worker side) ----------------------------------------
    def _emit_log(self, level, msg):
        self.q.put(("log", (level, msg)))

    def _emit_status(self, text):
        self.q.put(("status", text))

    # -- main loop ----------------------------------------------------------
    def run(self):
        verb = keep_awake._method_verb(self.method)
        force = self.mode == "force"
        start = time.monotonic()
        try:
            if force:
                self._emit_log("INFO", "Started (force, interval {}s, method {}).".format(
                    self.interval, verb))
            else:
                self._emit_log("INFO", "Started (smart, threshold {}s, interval {}s, "
                                       "method {}).".format(self.threshold,
                                                            self.interval, verb))

            # Smart-mode "user active" throttle, same spirit as keep_awake.run().
            SKIP_THROTTLE = 300.0
            last_skip_log = 0.0
            prev_iteration_injected = True  # so the first skip always logs

            while not self.stop_event.is_set():
                if force:
                    keep_awake.inject(self.method)
                    self._emit_log("INFO", "Injected {} (force) -- next in {}s.".format(
                        verb, self.interval))
                else:
                    try:
                        idle = keep_awake.idle_seconds()
                    except OSError as exc:
                        # A transient idle-read failure must never kill the loop.
                        self._emit_log("WARN", "Idle read failed ({}); retrying "
                                               "next cycle.".format(exc))
                        self.stop_event.wait(timeout=self.interval)
                        continue
                    if idle >= self.threshold:
                        keep_awake.inject(self.method)
                        prev_iteration_injected = True
                        self._emit_log("INFO", "Injected {} -- idle {}s (>= {}s). "
                                               "Idle clock reset.".format(
                                                   verb, int(idle), self.threshold))
                    else:
                        now = time.monotonic()
                        if prev_iteration_injected or (now - last_skip_log) >= SKIP_THROTTLE:
                            self._emit_log("INFO", "User active (idle {}s < {}s) -- "
                                                   "nothing injected.".format(
                                                       int(idle), self.threshold))
                            last_skip_log = now
                        prev_iteration_injected = False

                # Wait on the stop event so Stop is immediate (not a full sleep).
                self.stop_event.wait(timeout=self.interval)

        except Exception as exc:  # noqa: BLE001 -- never let the thread die silently
            self._emit_log("ERROR", "Worker crashed: {}".format(exc))
        finally:
            elapsed = int(time.monotonic() - start)
            mins, secs = divmod(elapsed, 60)
            self._emit_log("INFO", "Stopped. Active for {}m {}s.".format(mins, secs))
            # Signal the UI that THIS worker (by session id) has fully exited.
            # The id lets the UI ignore a stale 'done' from a previous run.
            self.q.put(("done", self.session_id))


# ===========================================================================
# GUI
# ===========================================================================
class KeepAwakeGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("ZEUKU Keep-Awake")
        self.root.minsize(560, 480)

        # Runtime state
        self.worker = None
        self.stop_event = None
        self.out_queue = queue.Queue()    # worker -> UI messages
        self.display_latched = False      # did we call keep_display_on()?
        self.running = False              # single source of truth: is a run live?
        self._session_id = 0              # bumped each Start; tags worker 'done'
        self._closing = False             # set in on_close to stop the drain
        self._drain_after = None          # id of the pending drain after-callback

        # Route keep_awake's module logger into our queue sink so primitive
        # warnings (e.g. SendInput blocked) appear in the GUI log instead of
        # being printed to a (possibly None) stdout/stderr.
        keep_awake.log = self._module_log_sink

        # Tk variables
        self.var_mode = tk.StringVar(value="smart")
        self.var_interval = tk.IntVar(value=INTERVAL_DEFAULT)
        self.var_threshold = tk.IntVar(value=THRESHOLD_DEFAULT)
        self.var_method = tk.StringVar(value="key")
        self.var_keep_display = tk.BooleanVar(value=True)
        self.var_status = tk.StringVar(value="Gestoppt")

        self._build_ui()
        self._sync_mode_state()
        self._update_latency_hint()

        # Lifecycle hooks
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        # Start the periodic queue drain (remember the id so we can cancel it).
        self._drain_after = self.root.after(QUEUE_POLL_MS, self._drain_queue)

    # -- UI construction ----------------------------------------------------
    def _build_ui(self):
        pad = {"padx": 6, "pady": 4}
        main = ttk.Frame(self.root, padding=10)
        main.pack(fill="both", expand=True)
        main.columnconfigure(0, weight=1)

        # --- Settings frame ---
        cfg = ttk.LabelFrame(main, text="Einstellungen", padding=8)
        cfg.grid(row=0, column=0, sticky="ew")
        for c in range(4):
            cfg.columnconfigure(c, weight=1 if c in (1, 3) else 0)

        # Mode radios
        ttk.Label(cfg, text="Modus:").grid(row=0, column=0, sticky="w", **pad)
        mode_box = ttk.Frame(cfg)
        mode_box.grid(row=0, column=1, columnspan=3, sticky="w", **pad)
        self.rb_smart = ttk.Radiobutton(
            mode_box, text="Smart", value="smart",
            variable=self.var_mode, command=self._on_mode_change)
        self.rb_force = ttk.Radiobutton(
            mode_box, text="Force", value="force",
            variable=self.var_mode, command=self._on_mode_change)
        self.rb_smart.pack(side="left", padx=(0, 12))
        self.rb_force.pack(side="left")

        # Interval spinbox
        ttk.Label(cfg, text="Intervall (Sek.):").grid(row=1, column=0, sticky="w", **pad)
        self.sp_interval = ttk.Spinbox(
            cfg, from_=INTERVAL_MIN, to=INTERVAL_MAX, textvariable=self.var_interval,
            width=8, command=self._update_latency_hint)
        self.sp_interval.grid(row=1, column=1, sticky="w", **pad)
        # Recompute the hint on any keystroke edit too.
        self.var_interval.trace_add("write", lambda *_: self._update_latency_hint())

        # Idle-threshold spinbox
        ttk.Label(cfg, text="Idle-Schwelle (Sek.):").grid(row=1, column=2, sticky="w", **pad)
        self.sp_threshold = ttk.Spinbox(
            cfg, from_=THRESHOLD_MIN, to=THRESHOLD_MAX, textvariable=self.var_threshold,
            width=8, command=self._update_latency_hint)
        self.sp_threshold.grid(row=1, column=3, sticky="w", **pad)
        self.var_threshold.trace_add("write", lambda *_: self._update_latency_hint())

        # Method combobox
        ttk.Label(cfg, text="Methode:").grid(row=2, column=0, sticky="w", **pad)
        self.cb_method = ttk.Combobox(
            cfg, textvariable=self.var_method, state="readonly",
            values=("key", "mouse", "both"), width=8)
        self.cb_method.grid(row=2, column=1, sticky="w", **pad)

        # Keep-display checkbox
        self.chk_display = ttk.Checkbutton(
            cfg, text="Bildschirm wachhalten", variable=self.var_keep_display)
        self.chk_display.grid(row=2, column=2, columnspan=2, sticky="w", **pad)

        # Latency hint
        self.lbl_hint = ttk.Label(main, text="", wraplength=520, justify="left")
        self.lbl_hint.grid(row=1, column=0, sticky="ew", pady=(8, 4))

        # --- Buttons + status ---
        ctrl = ttk.Frame(main)
        ctrl.grid(row=2, column=0, sticky="ew", pady=(0, 6))
        self.btn_start = ttk.Button(ctrl, text="Start", command=self.on_start)
        self.btn_stop = ttk.Button(ctrl, text="Stop", command=self.on_stop, state="disabled")
        self.btn_start.pack(side="left")
        self.btn_stop.pack(side="left", padx=(8, 0))
        ttk.Label(ctrl, text="Status:").pack(side="left", padx=(16, 4))
        self.lbl_status = ttk.Label(ctrl, textvariable=self.var_status,
                                    font=("Segoe UI", 9, "bold"))
        self.lbl_status.pack(side="left")

        # --- Log area ---
        logframe = ttk.LabelFrame(main, text="Log", padding=6)
        logframe.grid(row=3, column=0, sticky="nsew")
        main.rowconfigure(3, weight=1)
        logframe.rowconfigure(0, weight=1)
        logframe.columnconfigure(0, weight=1)

        self.txt_log = tk.Text(logframe, height=12, wrap="word", state="disabled",
                               background="#f5f5f5", relief="flat")
        self.txt_log.grid(row=0, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(logframe, orient="vertical", command=self.txt_log.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        self.txt_log.configure(yscrollcommand=scroll.set)

        ttk.Button(logframe, text="Log leeren", command=self.clear_log).grid(
            row=1, column=0, columnspan=2, sticky="e", pady=(6, 0))

        # Collect param widgets disabled while running.
        self._param_widgets = [
            self.rb_smart, self.rb_force, self.sp_interval, self.sp_threshold,
            self.cb_method, self.chk_display,
        ]

    # -- helpers ------------------------------------------------------------
    def _read_int(self, var, default, lo, hi):
        """Read a Tk IntVar tolerantly (an in-progress edit may be empty)."""
        try:
            val = int(var.get())
        except (tk.TclError, ValueError):
            return default
        return max(lo, min(hi, val))

    def _on_mode_change(self):
        self._sync_mode_state()
        self._update_latency_hint()

    def _sync_mode_state(self):
        """Disable the idle-threshold spinbox in Force mode."""
        if self.var_mode.get() == "force":
            self.sp_threshold.configure(state="disabled")
        elif not self.running:  # only re-enable if no run is live
            self.sp_threshold.configure(state="normal")

    def _update_latency_hint(self):
        """Show worst-case nudge latency (threshold + interval) in Smart mode."""
        if self.var_mode.get() == "force":
            interval = self._read_int(self.var_interval, INTERVAL_DEFAULT,
                                      INTERVAL_MIN, INTERVAL_MAX)
            self.lbl_hint.configure(
                text="Force-Modus: Eingabe wird alle {}s gesendet (egal ob aktiv).".format(
                    interval),
                foreground="")
            return
        interval = self._read_int(self.var_interval, INTERVAL_DEFAULT,
                                  INTERVAL_MIN, INTERVAL_MAX)
        threshold = self._read_int(self.var_threshold, THRESHOLD_DEFAULT,
                                   THRESHOLD_MIN, THRESHOLD_MAX)
        worst = threshold + interval
        text = ("Worst-Case-Verzoegerung bis zum Nudge: ~{}s "
                "(Schwelle {}s + Intervall {}s). "
                "Halte das deutlich unter deinem Sperr-Timeout (typisch 300s).".format(
                    worst, threshold, interval))
        if worst >= LATENCY_WARN_S:
            color = "#c00000"  # red: at/over a typical 300s lock
        elif worst >= LATENCY_NEAR_S:
            color = "#b07000"  # amber: getting close
        else:
            color = ""
        self.lbl_hint.configure(text=text, foreground=color)

    # -- logging (UI thread only) -------------------------------------------
    def _module_log_sink(self, level, msg, *, force_stream=None):
        """Replacement for keep_awake.log. Called from BOTH the worker thread
        (via inject/idle primitives) and the UI thread. It must be thread-safe,
        so it only enqueues -- the actual widget write happens on the UI tick.
        The `force_stream` kwarg is accepted for signature compatibility and
        ignored (there is no stream, only the GUI log)."""
        self.out_queue.put(("log", (level, msg)))

    def _append_log(self, level, msg):
        """Write a line to the log Text. UI thread only."""
        ts = time.strftime("%H:%M:%S")
        line = "[{}] {:<5} {}\n".format(ts, level, msg)
        self.txt_log.configure(state="normal")
        self.txt_log.insert("end", line)
        # Trim to keep the widget bounded.
        line_count = int(self.txt_log.index("end-1c").split(".")[0])
        if line_count > MAX_LOG_LINES:
            self.txt_log.delete("1.0", "{}.0".format(line_count - MAX_LOG_LINES + 1))
        self.txt_log.see("end")  # auto-scroll
        self.txt_log.configure(state="disabled")

    def clear_log(self):
        self.txt_log.configure(state="normal")
        self.txt_log.delete("1.0", "end")
        self.txt_log.configure(state="disabled")

    # -- queue drain (periodic UI tick) -------------------------------------
    def _drain_queue(self):
        # Once we are tearing down, stop touching widgets and do not re-arm.
        if self._closing:
            return
        try:
            while True:
                kind, payload = self.out_queue.get_nowait()
                if kind == "log":
                    level, msg = payload
                    self._append_log(level, msg)
                elif kind == "status":
                    self.var_status.set(payload)
                elif kind == "done":
                    self._finalize_session(payload)  # payload = worker session id
        except queue.Empty:
            pass
        except tk.TclError:
            # Widget went away mid-drain (window closing) -- stop quietly.
            return
        finally:
            if not self._closing:
                self._drain_after = self.root.after(QUEUE_POLL_MS, self._drain_queue)

    # -- lifecycle ----------------------------------------------------------
    def on_start(self):
        if self.running:
            return  # guard against double-start

        mode = self.var_mode.get()
        interval = self._read_int(self.var_interval, INTERVAL_DEFAULT,
                                  INTERVAL_MIN, INTERVAL_MAX)
        threshold = self._read_int(self.var_threshold, THRESHOLD_DEFAULT,
                                   THRESHOLD_MIN, THRESHOLD_MAX)
        method = self.var_method.get()
        keep_display = bool(self.var_keep_display.get())

        # Normalize the spinboxes back to the clamped values we actually use.
        self.var_interval.set(interval)
        self.var_threshold.set(threshold)

        # Power-keep latch (UI thread; cheap WinAPI call).
        self.display_latched = False
        if keep_display:
            if keep_awake.keep_display_on():  # logs its own WARN via our sink
                self.display_latched = True

        # New session id tags this run's worker so a stale 'done' from an
        # earlier run can never tear down this one.
        self._session_id += 1

        # Fresh stop flag + worker every run.
        self.stop_event = threading.Event()
        self.worker = KeepAwakeWorker(
            session_id=self._session_id, mode=mode, interval=interval,
            threshold=threshold, method=method, keep_display=keep_display,
            out_queue=self.out_queue, stop_event=self.stop_event)
        self.running = True

        # Disable params, flip buttons, set status.
        for w in self._param_widgets:
            w.configure(state="disabled")
        self.cb_method.configure(state="disabled")  # combobox: plain disabled
        self.btn_start.configure(state="disabled")
        self.btn_stop.configure(state="normal")
        if mode == "force":
            self.var_status.set("Laeuft (force) -- Intervall {}s".format(interval))
        else:
            self.var_status.set("Laeuft (smart) -- Schwelle {}s / Intervall {}s".format(
                threshold, interval))

        self.worker.start()

    def on_stop(self):
        """Request a stop. Non-blocking: we only set the stop flag here. The
        daemon worker wakes immediately from stop_event.wait(), enqueues its
        'done', and the drain turns that into _finalize_session() on the UI
        thread -- so the UI never freezes joining the worker. Idempotent."""
        if not self.running:
            return
        self.running = False
        if self.stop_event is not None:
            self.stop_event.set()
        # Lock both buttons until the worker's 'done' finalizes teardown; this
        # also closes the old Stop->quick-Start race (no restart mid-stop).
        self.btn_stop.configure(state="disabled")
        self.btn_start.configure(state="disabled")
        self.var_status.set("Stoppe ...")

    def _finalize_session(self, session_id):
        """Teardown once a worker reports 'done'. UI thread; idempotent. A
        'done' whose id != the current session is a stale message from an
        earlier run and is ignored (prevents reaping a freshly-started worker)."""
        if session_id != self._session_id:
            return  # stale worker from a previous Start -- ignore
        if self.worker is None:
            return  # already finalized
        self.worker = None
        self.stop_event = None
        self.running = False
        self._restore_display()
        # Re-enable params.
        for w in self._param_widgets:
            w.configure(state="normal")
        self.cb_method.configure(state="readonly")
        self._sync_mode_state()  # re-disable threshold if Force is selected
        self.btn_start.configure(state="normal")
        self.btn_stop.configure(state="disabled")
        self.var_status.set("Gestoppt")

    def _restore_display(self):
        """Clear the execution-state latch if we set it. Idempotent."""
        if self.display_latched:
            keep_awake.restore_execution_state()
            self.display_latched = False

    def on_close(self):
        """WM_DELETE_WINDOW: stop cleanly, never leak a thread or the latch."""
        self._closing = True
        # Cancel the pending drain so no after-callback fires against dead widgets.
        if self._drain_after is not None:
            try:
                self.root.after_cancel(self._drain_after)
            except tk.TclError:
                pass
            self._drain_after = None
        # Ask the worker to stop. It's a daemon thread, so even if it is mid
        # inject we don't need to join -- it cannot block process exit.
        if self.stop_event is not None:
            self.stop_event.set()
        self.running = False
        self.worker = None
        self.stop_event = None
        # Always clear the execution-state latch on exit (unconditional belt-and-
        # braces so the SetThreadExecutionState latch is never left set).
        self._restore_display()
        keep_awake.restore_execution_state()
        self.root.destroy()


def main():
    root = tk.Tk()
    KeepAwakeGUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()
