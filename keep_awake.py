#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
keep_awake.py  --  Smart anti-idle / keep-awake tool for Windows (stdlib only).

WHAT IT DOES
------------
Stops your PC from sleeping, switching the display off, or showing the
screensaver. It works on two independent levels:

  1. Power level  (SetThreadExecutionState):
     Tells the Windows power manager "system + display are required", which
     resets the sleep-to-idle and display-off timers. This is policy-neutral
     and harmless. ON by default; disable with --no-keep-display.

  2. Input-idle level  (SendInput):
     In SMART mode the script watches the OS-reported idle time and injects a
     benign no-op input event (F15 key and/or a 0-net-pixel mouse nudge) ONLY
     once you have been idle long enough -- just before Windows would lock.
     While you are actively using mouse/keyboard it injects nothing.

THE CORPORATE-LOCK CAVEAT (read this)
-------------------------------------
SetThreadExecutionState ONLY suppresses the power/idle-to-sleep and display-off
timers. It does NOT stop a corporate auto-lock. The lock driven by
"Interactive logon: Machine inactivity limit" (Group Policy / InactivityTimeoutSecs)
and the password-protected screensaver is governed by the LAST USER INPUT TIME,
which Windows reads via GetLastInputInfo -- a completely separate subsystem from
the power manager. Execution-state flags have zero effect on that input-idle
clock.

To defeat the input-idle lock you must refresh the last-input time, which is
exactly what the SMART/--force injection does (SendInput updates the input
clock). On a standard machine this resets the lock/screensaver timer reliably.
HOWEVER some corporate setups enforce lock through a separate mechanism
(smartcard-removal lock, Modern-Standby idle source, certain GPO combinations)
that synthetic input cannot defeat. The injection here beats the STANDARD
user-idle timeout; it cannot override a forced-lock policy that ignores
synthetic input.

COMPLIANCE NOTE: injecting synthetic input can circumvent a security control
your IT department mandates. It works technically without administrator rights,
but whether you SHOULD run it is a compliance question, not a permissions one.

ADMIN RIGHTS: none required. Both SetThreadExecutionState and SendInput operate
within the current interactive user session and run fine as a standard user.

Pure Python standard library only (argparse, ctypes, ctypes.wintypes, time,
signal, sys, datetime, threading). No third-party packages.
Tested on Windows 11, Python 3.12.6, 64-bit.
"""

import argparse
import ctypes
import datetime
import signal
import sys
import threading
import time
from ctypes import wintypes

# ---------------------------------------------------------------------------
# Win32 DLL handles (use_last_error so we can read GetLastError on failures)
# ---------------------------------------------------------------------------
user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
# SendInput input types / event flags (winuser.h)
INPUT_MOUSE = 0
INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 0x0002
MOUSEEVENTF_MOVE = 0x0001
VK_F15 = 0x7E  # F15: generates no character, no cursor movement, ~never bound.

# SetThreadExecutionState flags (winbase.h)
ES_CONTINUOUS = 0x80000000        # state persists until the next call
ES_SYSTEM_REQUIRED = 0x00000001   # reset the system idle (sleep) timer
ES_DISPLAY_REQUIRED = 0x00000002  # reset the display-off idle timer

# ---------------------------------------------------------------------------
# ULONG_PTR  --  pointer-sized unsigned int.
#
# IMPORTANT GOTCHA: ctypes.wintypes does NOT export ULONG_PTR (AttributeError),
# so we define it ourselves. If you fall back to a plain DWORD (4 bytes) for
# dwExtraInfo, the INPUT struct shrinks to 36 bytes on x64, SendInput reads
# garbage past the buffer and silently returns 0. The c_uint64-on-64-bit
# definition below is what makes sizeof(INPUT) == 40 and SendInput succeed.
# (Verified on this machine: sizeof(INPUT) == 40, SendInput returned 2.)
# ---------------------------------------------------------------------------
if ctypes.sizeof(ctypes.c_void_p) == 8:
    ULONG_PTR = ctypes.c_uint64   # x64
else:
    ULONG_PTR = ctypes.c_ulong    # x86


# ---------------------------------------------------------------------------
# SendInput struct layout (VERIFIED -- do not "improve" the layout).
# ---------------------------------------------------------------------------
class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [
        ("uMsg", wintypes.DWORD),
        ("wParamL", wintypes.WORD),
        ("wParamH", wintypes.WORD),
    ]


class _INPUT_UNION(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("hi", HARDWAREINPUT)]


class INPUT(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = [("type", wintypes.DWORD), ("u", _INPUT_UNION)]


class LASTINPUTINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.UINT), ("dwTime", wintypes.DWORD)]


# ---------------------------------------------------------------------------
# WinAPI prototypes -- set argtypes/restype on EVERY function we call.
# This is required for correctness on 64-bit (otherwise ctypes assumes int
# args and can corrupt pointer arguments).
# ---------------------------------------------------------------------------
user32.SendInput.argtypes = (wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int)
user32.SendInput.restype = wintypes.UINT

user32.GetLastInputInfo.argtypes = (ctypes.POINTER(LASTINPUTINFO),)
user32.GetLastInputInfo.restype = wintypes.BOOL

kernel32.GetTickCount.argtypes = ()
kernel32.GetTickCount.restype = wintypes.DWORD

kernel32.SetThreadExecutionState.argtypes = (wintypes.DWORD,)  # EXECUTION_STATE
kernel32.SetThreadExecutionState.restype = wintypes.DWORD       # previous state, 0 = fail


# ===========================================================================
# Logging  --  single source of truth for timestamp + level formatting.
# Format:  [YYYY-MM-DD HH:MM:SS] LEVEL  message
# INFO -> stdout;  WARN/ERROR -> stderr.
# ===========================================================================
def _timestamp():
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def log(level, msg, *, force_stream=None):
    """Emit a timestamped, level-padded log line.

    level: "INFO", "WARN" or "ERROR" (padded to 5 chars for column alignment).
    WARN/ERROR go to stderr; INFO goes to stdout, unless force_stream is given.
    """
    line = "[{ts}] {lvl:<5}  {msg}".format(ts=_timestamp(), lvl=level, msg=msg)
    stream = force_stream if force_stream is not None else (
        sys.stderr if level in ("WARN", "ERROR") else sys.stdout
    )
    print(line, file=stream, flush=True)


# ===========================================================================
# Power level: SetThreadExecutionState
# ===========================================================================
def keep_display_on():
    """Latch system + display 'required' so power timers don't fire.

    ES_CONTINUOUS makes the SYSTEM/DISPLAY flags STICK until we clear them
    (otherwise it would be a one-shot reset). Returns True on success.
    """
    prev = kernel32.SetThreadExecutionState(
        ES_CONTINUOUS | ES_SYSTEM_REQUIRED | ES_DISPLAY_REQUIRED
    )
    if prev == 0:
        log("WARN", "Could not set execution state (display-keep disabled).")
        return False
    return True


def restore_execution_state():
    """Clear the SYSTEM/DISPLAY requirement -> normal idle timers resume.

    ES_CONTINUOUS alone (no SYSTEM/DISPLAY) clears the latched requirement.
    Note: the per-thread continuous state is also dropped automatically when
    the process exits, so this is belt-and-braces.
    """
    kernel32.SetThreadExecutionState(ES_CONTINUOUS)


# ===========================================================================
# Input-idle level: idle reader + injection primitives
# ===========================================================================
def idle_seconds():
    """Return seconds since the last *real or injected* user input.

    idle = (GetTickCount() - LASTINPUTINFO.dwTime) / 1000.

    GetTickCount wraps every ~49.7 days; the subtraction is kept in 32-bit
    unsigned space so it stays correct across that wrap. Raises OSError if the
    WinAPI call fails (callers in the loop catch this and retry next cycle).
    """
    info = LASTINPUTINFO()
    info.cbSize = ctypes.sizeof(LASTINPUTINFO)
    if not user32.GetLastInputInfo(ctypes.byref(info)):
        raise ctypes.WinError(ctypes.get_last_error())
    # Keep the subtraction in 32-bit unsigned space so the ~49.7-day wrap works.
    millis = (kernel32.GetTickCount() - info.dwTime) & 0xFFFFFFFF
    return millis / 1000.0


def _send(arr, n, what):
    """Call SendInput and warn if it returns 0 (injection blocked / bad cb).

    SendInput returns the COUNT of events injected (not a boolean); 0 means the
    injection was blocked or the struct/cb size is wrong. Always pass
    sizeof(INPUT) as the third arg -- never a hard-coded number.
    """
    sent = user32.SendInput(n, arr, ctypes.sizeof(INPUT))
    if sent != n:
        err = ctypes.get_last_error()
        log("WARN", "SendInput({what}) returned {sent}/{n} (input may be "
                    "blocked; GetLastError={err}).".format(
                        what=what, sent=sent, n=n, err=err))
    return sent


def press_f15():
    """Primitive 1 -- benign F15 keypress (down + up). Returns SendInput count."""
    down = INPUT(type=INPUT_KEYBOARD)
    down.ki = KEYBDINPUT(wVk=VK_F15, wScan=0, dwFlags=0, time=0, dwExtraInfo=0)
    up = INPUT(type=INPUT_KEYBOARD)
    up.ki = KEYBDINPUT(wVk=VK_F15, wScan=0, dwFlags=KEYEVENTF_KEYUP,
                       time=0, dwExtraInfo=0)
    arr = (INPUT * 2)(down, up)
    return _send(arr, 2, "key")


def nudge_mouse():
    """Primitive 2 -- 1px relative nudge (+1 then -1). Net displacement zero.

    Returns the cursor to its origin so there is no visible drift. Useful as a
    fallback if an app filters injected keystrokes but not mouse moves.
    """
    plus = INPUT(type=INPUT_MOUSE)
    plus.mi = MOUSEINPUT(dx=1, dy=0, mouseData=0, dwFlags=MOUSEEVENTF_MOVE,
                         time=0, dwExtraInfo=0)
    minus = INPUT(type=INPUT_MOUSE)
    minus.mi = MOUSEINPUT(dx=-1, dy=0, mouseData=0, dwFlags=MOUSEEVENTF_MOVE,
                          time=0, dwExtraInfo=0)
    arr = (INPUT * 2)(plus, minus)
    return _send(arr, 2, "mouse")


def inject(method):
    """Inject according to method: 'key', 'mouse' or 'both' (key then mouse)."""
    if method in ("key", "both"):
        press_f15()
    if method in ("mouse", "both"):
        nudge_mouse()


def _method_verb(method):
    """Human-readable verb for the heartbeat line."""
    return {"key": "key", "mouse": "mouse", "both": "key+mouse"}[method]


# ===========================================================================
# Banner + argument parsing
# ===========================================================================
def build_parser():
    p = argparse.ArgumentParser(
        prog="keep_awake.py",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=(
            "Smart anti-idle / keep-awake for Windows (stdlib only).\n\n"
            "SMART mode (default) does nothing while you actively use the PC; "
            "it sends a harmless F15 key and/or 0-net-pixel mouse nudge only "
            "after you have been idle past --idle-threshold, just before "
            "Windows would lock. --force ignores idle and nudges every "
            "--interval seconds.\n\n"
            "CAVEAT: this resets the STANDARD Windows idle/sleep/screensaver "
            "timers. Some corporate forced-lock policies (smartcard, certain "
            "GPOs, Modern Standby) ignore synthetic input and cannot be "
            "defeated. No admin rights required."
        ),
        epilog=(
            "Examples:\n"
            "  keep_awake.py                      # smart, 240s threshold, 30s checks\n"
            "  keep_awake.py --force --interval 60\n"
            "  keep_awake.py --method both --idle-threshold 120\n"
            "  keep_awake.py --no-keep-display --quiet\n"
        ),
    )
    p.add_argument(
        "--interval", type=int, default=30, metavar="SEC",
        help="Polling cadence in seconds: how often the loop wakes to check "
             "idle (smart) / inject (force). Must be >= 1. Default: 30.",
    )
    p.add_argument(
        "--idle-threshold", type=int, default=None, metavar="SEC",
        help="Smart-mode trigger: inject only when OS idle time >= this many "
             "seconds. Default 240. NOTE: the worst-case delay before a nudge "
             "is (idle-threshold + interval), because the loop only checks "
             "every interval. Keep (idle-threshold + interval) comfortably "
             "below your real lock timeout (a typical corporate lock is 300s). "
             "Ignored with --force. Must be >= 1.",
    )
    p.add_argument(
        "--method", choices=("key", "mouse", "both"), default="key",
        help="What to inject: 'key' = benign F15, 'mouse' = +1/-1px nudge "
             "(net zero), 'both' = key then mouse. Default: key.",
    )
    p.add_argument(
        "--force", action="store_true",
        help="Naive jiggler: inject every --interval seconds REGARDLESS of "
             "idle time. --idle-threshold is ignored.",
    )
    p.add_argument(
        "--quiet", action="store_true",
        help="Suppress the banner and periodic INFO lines. WARN/ERROR, a "
             "one-line startup notice and the shutdown line are still shown.",
    )
    p.add_argument(
        "--no-keep-display", action="store_true",
        help="Disable the SetThreadExecutionState power-keep (which is ON by "
             "default). The script then relies solely on input injection.",
    )
    return p


def print_banner(args):
    """Full startup banner (skipped under --quiet)."""
    keep_display = not args.no_keep_display
    bar = "=" * 60
    if args.force:
        title = "  KEEP-AWAKE  --  FORCE jiggler for Windows"
        what = (
            "    FORCE mode: sends a nudge every interval no matter what,\n"
            "    even while you are typing. (You asked for this.)"
        )
        mode_line = "    Mode .............. FORCE (nudges every interval, always)"
        thresh_line = "    Idle threshold .... (ignored in force mode)"
        interval_line = "    Check interval .... {}s   (nudge cadence)".format(args.interval)
    else:
        title = "  KEEP-AWAKE  --  smart anti-idle for Windows"
        what = (
            "    Stops your PC from locking, sleeping, or showing the\n"
            "    screensaver. In SMART mode it does nothing while you are\n"
            "    actively using the mouse or keyboard -- it only sends a\n"
            "    harmless key/mouse nudge after you have been idle long\n"
            "    enough, just before Windows would lock."
        )
        mode_line = "    Mode .............. SMART (only acts when you are idle)"
        thresh_line = "    Idle threshold .... {}s  (nudge after this much inactivity)".format(args.idle_threshold)
        interval_line = "    Check interval .... {}s   (how often it checks)".format(args.interval)

    lines = [
        bar,
        title,
        bar,
        "  What this does:",
        what,
        "",
        "  Active settings:",
        mode_line,
        thresh_line,
        interval_line,
        "    Inject method ..... {:<5} (sends F15 / mouse nudge -- no-op)".format(args.method),
        "    Keep display on ... {}".format("YES   (SetThreadExecutionState)" if keep_display else "NO"),
        "    Quiet ............. {}".format("YES" if args.quiet else "NO"),
        "",
        "  How to stop:",
        "    Press  Ctrl+C  in this window. The script will restore",
        "    Windows power settings and exit cleanly.",
        bar,
    ]
    print("\n".join(lines), flush=True)


# ===========================================================================
# Main loop
# ===========================================================================
def run(args, stop_event):
    """Main keep-awake loop. Returns process exit code (0 normal, 1 on error)."""
    keep_display = not args.no_keep_display
    method = args.method
    verb = _method_verb(method)
    start_time = time.monotonic()
    display_active = False
    cleaned_up = threading.Event()

    def cleanup():
        # Idempotent: only ever runs the restore + shutdown line once.
        if cleaned_up.is_set():
            return
        cleaned_up.set()
        if display_active:
            restore_execution_state()
        elapsed = int(time.monotonic() - start_time)
        mins, secs = divmod(elapsed, 60)
        if keep_display:
            tail = ("Shutting down -- execution state restored. "
                    "Stayed awake for {}m {}s. Bye.".format(mins, secs))
        else:
            tail = "Shutting down. Stayed awake for {}m {}s. Bye.".format(mins, secs)
        # Shutdown line is always printed, even under --quiet.
        log("INFO", tail)

    try:
        # --- Banner / startup notice ---
        if not args.quiet:
            print_banner(args)
        else:
            mode = "force" if args.force else "smart"
            if args.force:
                detail = "interval {}s".format(args.interval)
            else:
                detail = "threshold {}s, interval {}s".format(
                    args.idle_threshold, args.interval)
            # The one allowed line under --quiet (to stderr).
            log("INFO", "keep_awake running ({}, {}). Ctrl+C to stop.".format(
                mode, detail), force_stream=sys.stderr)

        # --- One-time WARN if both --force and an explicit threshold are set ---
        if args.force and args.threshold_explicit:
            log("WARN", "--idle-threshold ignored because --force is set.")

        # --- Smart-mode worst-case-latency advisory ---
        # The loop only checks idle every `interval`, so the longest a nudge can
        # be delayed after the user goes idle is (idle_threshold + interval).
        # If that approaches a typical 300s lock, warn so the lock can't sneak in.
        if not args.force:
            worst = args.idle_threshold + args.interval
            if worst >= 285:
                log("WARN", "Worst-case nudge latency is ~{}s (threshold {}s + "
                            "interval {}s). If your Windows lock is at or below "
                            "this, lower --idle-threshold and/or --interval."
                            .format(worst, args.idle_threshold, args.interval))

        # --- Power-keep ---
        if keep_display:
            display_active = keep_display_on()  # logs its own WARN on failure

        # --- Loop ---
        # Smart-mode skip-line throttle: emit "user active" at most ~every 5 min,
        # but always on the first skip after an injection.
        SKIP_THROTTLE = 300.0  # seconds
        last_skip_log = 0.0
        prev_iteration_injected = True  # so the first skip always logs

        while not stop_event.is_set():
            if args.force:
                # FORCE: inject unconditionally every interval.
                inject(method)
                if not args.quiet:
                    log("INFO", "Injected {} (force mode) -- next in {}s.".format(
                        verb, args.interval))
            else:
                # SMART: inject only when idle past the threshold.
                try:
                    idle = idle_seconds()
                except OSError as exc:
                    # A transient idle-read failure must NOT kill a long-running
                    # daemon (that would let the machine lock -- the exact thing
                    # we prevent). Log and try again on the next cycle.
                    log("WARN", "Idle read failed ({}); retrying next cycle."
                        .format(exc))
                    stop_event.wait(timeout=args.interval)
                    continue
                if idle >= args.idle_threshold:
                    inject(method)
                    prev_iteration_injected = True
                    if not args.quiet:
                        log("INFO", "Injected {} -- user idle {}s (>= {}s "
                                    "threshold). Idle clock reset.".format(
                                        verb, int(idle), args.idle_threshold))
                else:
                    now = time.monotonic()
                    should_log = (
                        not args.quiet and (
                            prev_iteration_injected
                            or (now - last_skip_log) >= SKIP_THROTTLE
                        )
                    )
                    if should_log:
                        log("INFO", "User active (idle {}s < {}s) -- no input "
                                    "injected.".format(int(idle), args.idle_threshold))
                        last_skip_log = now
                    prev_iteration_injected = False

            # Wait on the stop event so SIGTERM / Ctrl+C wake us immediately
            # instead of blocking the full interval in time.sleep().
            stop_event.wait(timeout=args.interval)

        return 0

    except KeyboardInterrupt:
        # Ctrl+C: clean exit, code 0.
        return 0
    except Exception as exc:  # noqa: BLE001 -- log + exit 1 on anything unexpected
        log("ERROR", "Unexpected error: {}".format(exc))
        return 1
    finally:
        cleanup()


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)

    # --idle-threshold defaults to None so we can tell "user set it" from the
    # default. Record explicitness, then substitute the real default of 240.
    args.threshold_explicit = args.idle_threshold is not None
    if args.idle_threshold is None:
        args.idle_threshold = 240

    # Manual validation (argparse handles unknown args / bad choices -> exit 2).
    if args.interval < 1:
        print("error: --interval must be >= 1", file=sys.stderr)
        return 2
    if args.idle_threshold < 1:
        print("error: --idle-threshold must be >= 1", file=sys.stderr)
        return 2

    # Cooperative stop flag, shared with the SIGTERM handler so the loop's
    # stop_event.wait() returns promptly on a terminate signal.
    stop_event = threading.Event()

    def _sigterm_handler(signum, frame):
        stop_event.set()

    try:
        signal.signal(signal.SIGTERM, _sigterm_handler)
    except (ValueError, OSError, AttributeError):
        # Not on the main thread, or SIGTERM unavailable -- Ctrl+C still works.
        pass

    try:
        return run(args, stop_event)
    except KeyboardInterrupt:
        # Belt-and-braces: run() already handles Ctrl+C, but catch it here too
        # so a Ctrl+C in the narrow setup window still exits cleanly (code 0).
        return 0


if __name__ == "__main__":
    sys.exit(main())
