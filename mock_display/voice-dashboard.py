"""
Coffee Maker Voice Interface Dashboard
======================================
Reads voice-recognition events from an external device via UART and
displays a coffee-shop themed interface for browsing and brewing drinks.

State machine
-------------
    IDLE   — waiting for the user
    ACK    — awaiting user confirmation before brewing
    BREWA  — brewing, stage A (settings can still be changed)
    BREWB  — brewing, stage B (settings locked)

UART Protocol (line-based, 115200 baud):
    ---RESET COMPLETE---
    ---WAKE WORD DETECTED---
    ---ShowDrinks[VAL=X]---               X in {Cold, Hot, Extra Shot}
    ---StartDrink[VAL=X]---               X is a drink name
    ---CustomDrink[VAL=N]---              N in {1,2,3,4}
    ---ToggleDoubleCup---
    ---ToggleExtraShot---
    ---ToggleColdBrew---
    ---TempSetting[VAL=X]---              X in {High, Medium, Low}
    ---StrengthSetting[VAL=X]---          X in {High, Medium, Low}
    ---VolumeSetting[VAL=X]---            X in {High, Medium, Low}
    ---Stop---
    ---Acknowledge[VAL=X]---              X in {Yes, No}
    ---favorite---

Usage:
    python voice-dashboard.py [COM_PORT] [--test]
    python voice-dashboard.py                # defaults to COM5
    python voice-dashboard.py COM3           # specify port
    python voice-dashboard.py --test         # test mode: type commands in
                                             # the console instead of UART
                                             # (no serial port is opened)

Requires:
    pip install customtkinter pyserial
"""

import math
import os
import re
import sys
import threading
import time

try:
    import customtkinter as ctk
except ImportError:
    print("ERROR: customtkinter not installed.  Run: pip install customtkinter")
    sys.exit(1)

try:
    import serial
except ImportError:
    print("ERROR: pyserial not installed.  Run: pip install pyserial")
    sys.exit(1)

from collections import deque


# ─── Configuration ────────────────────────────────────────────────────────────

COM_PORT  = "COM5"
BAUD_RATE = 115200

# Drinks shown by ShowDrinks, keyed by lowercase value token.
# Per spec the same list is used for "cold" and "extra shot".
_DRINKS_COLD_OR_EXTRA = [
    "Coffee",
    "Americano",
    "Cappuccino",
    "Espresso",
    "Cortado",
]

_DRINKS_HOT = [
    "Coffee",
    "Americano",
    "Cappuccino",
    "Espresso",
    "Cortado",
    "Latte Machiato",
    "Cafe Latte",
    "Cafe Barista",
    "Hot Water",
]

DRINK_MENU = {
    "cold":        _DRINKS_COLD_OR_EXTRA,
    "hot":         _DRINKS_HOT,
    "extra shot":  _DRINKS_COLD_OR_EXTRA,
}

# Pretty display label for each ShowDrinks value.
DRINK_TYPE_LABEL = {
    "cold":       "COLD",
    "hot":        "HOT",
    "extra shot": "EXTRA SHOT",
}

# Settings vocabulary (used by TempSetting / StrengthSetting / VolumeSetting).
SETTING_LEVELS = ("High", "Medium", "Low")

# Hard-coded custom drink presets used by CustomDrink[VAL=N].
# Keys are 1..4. Each preset is a dict that fully describes the drink.
CUSTOM_DRINKS = {
    1: {
        "drink":      "Cortado",
        "temp":       "Medium",
        "strength":   "High",
        "volume":     "Medium",
        "double_cup": False,
        "extra_shot": True,
        "cold_brew":  False,
    },
    2: {
        "drink":      "Cafe Latte",
        "temp":       "Medium",
        "strength":   "High",
        "volume":     "High",
        "double_cup": False,
        "extra_shot": True,
        "cold_brew":  True,
    },
    3: {
        "drink":      "Cappuccino",
        "temp":       "High",
        "strength":   "High",
        "volume":     "Low",
        "double_cup": False,
        "extra_shot": True,
        "cold_brew":  False,
    },
    4: {
        "drink":      "Cafe Barista",
        "temp":       "Medium",
        "strength":   "High",
        "volume":     "Medium",
        "double_cup": False,
        "extra_shot": True,
        "cold_brew":  False,
    },
}

# Hard-coded preset used by `favorite` (equivalent to custom slot #4).
FAVORITE_DRINK_PRESET = {
    "drink":      "Espresso",
    "temp":       "Medium",
    "strength":   "High",
    "volume":     "Medium",
    "double_cup": False,
    "extra_shot": True,
    "cold_brew":  False,
}

DISPLAY_HOLD_SECONDS = 12.0   # how long the drink list stays on screen
BREW_DURATION_SECONDS = 10.0  # total brew length (BREWA 5s + BREWB 5s)
LISTENING_TIMEOUT     = 3.0   # auto-clear listening if no command arrives
HISTORY_LEN           = 80


# ─── Design Tokens — "Coffee Machine" ──────────────────────────────────────────

BG_BASE      = "#1B130D"   # deep espresso
BG_SURFACE   = "#2A1E14"   # dark roast
BG_ELEVATED  = "#3A2A1C"   # mocha
BORDER       = "#5A4030"
BORDER_WARM  = "#C68B47"

FG_PRIMARY   = "#F5E6D3"   # cream
FG_SECONDARY = "#C9B89A"   # latte
FG_TERTIARY  = "#8A7560"
FG_DIM       = "#4A3A2A"

CREAM        = "#F5E6D3"
CARAMEL      = "#C68B47"
CARAMEL_DIM  = "#7A5028"
CARAMEL_HOT  = "#FFB066"
ESPRESSO     = "#3B1F0E"
STEAM        = "#E8DDC8"
GREEN        = "#8FBC5A"   # ready / go
GREEN_DIM    = "#4F6A30"
RED          = "#D9594C"
RED_DIM      = "#7A2A22"
BLUE_COLD    = "#7BB6D9"
BLUE_COLD_DIM= "#3A6A85"

# Status states
STATUS_OFFLINE = RED
STATUS_READY   = GREEN
STATUS_LISTEN  = CARAMEL_HOT
STATUS_BREW    = CARAMEL


# ─── UART Line Parsing ───────────────────────────────────────────────────────

# Lines like ---NAME[VAR=value]--- where value may contain spaces.
# Command names use mixed case (e.g. "ShowDrinks", "favorite");
# the parameter name is always "VAL".
_CMD_PAYLOAD_RE = re.compile(r"^---([A-Za-z]+)\[([A-Za-z_]+)=([^\]]+)\]---$")
# Lines like ---NAME--- with no payload. Allows spaces so legacy control
# lines like "RESET COMPLETE" / "WAKE WORD DETECTED" still parse.
_CMD_BARE_RE    = re.compile(r"^---([A-Za-z_ ]+)---$")

# Map of command name -> (event_type, payload-key).
# event_type is what the dashboard's _on_event will see.
_PAYLOAD_COMMANDS = {
    # All parameterised commands use the uniform COMMAND[VAL=X] form.
    # name             event_type           payload key
    "ShowDrinks":      ("show_drinks",      "type"),
    "StartDrink":      ("start_drink",      "drink"),
    "CustomDrink":     ("custom_drink",     "number"),
    "TempSetting":     ("temp_setting",     "setting"),
    "StrengthSetting": ("strength_setting", "setting"),
    "VolumeSetting":   ("volume_setting",   "setting"),
    "Acknowledge":     ("acknowledge",      "val"),
}

_BARE_COMMANDS = {
    "ToggleDoubleCup": "toggle_double_cup",
    "ToggleExtraShot": "toggle_extra_shot",
    "ToggleColdBrew":  "toggle_cold_brew",
    "Stop":            "stop",
    "favorite":        "favorite_drink",
}


def parse_line(line: str):
    """Return (event_type, payload_dict) or (None, None) for unknown lines."""
    line = line.strip()
    if not line:
        return None, None

    # Bare control lines first (these include space-containing names).
    if line == "---RESET COMPLETE---":
        return "reset", {}
    if line == "---WAKE WORD DETECTED---":
        return "wakeword", {}

    m = _CMD_PAYLOAD_RE.match(line)
    if m:
        name, var, value = m.group(1), m.group(2), m.group(3).strip()
        spec = _PAYLOAD_COMMANDS.get(name)
        if spec is not None and var == "VAL":
            evt, key = spec
            return evt, {key: value}
        return None, None

    m = _CMD_BARE_RE.match(line)
    if m:
        name = m.group(1).strip()
        evt = _BARE_COMMANDS.get(name)
        if evt is not None:
            return evt, {}

    return None, None


# ─── Serial Reader ───────────────────────────────────────────────────────────

class SerialReader(threading.Thread):
    """Background thread: reads lines from the serial port and dispatches events."""

    def __init__(self, port: str, baud: int, callback):
        super().__init__(daemon=True)
        self.port      = port
        self.baud      = baud
        self.callback  = callback
        self.running   = True
        self.connected = False

    def run(self):
        while self.running:
            try:
                ser = serial.Serial(self.port, self.baud, timeout=1)
                self.connected = True
                time.sleep(0.5)
                while self.running:
                    if ser.in_waiting > 0:
                        raw  = ser.readline()
                        line = raw.decode("utf-8", errors="replace").strip()
                        evt, payload = parse_line(line)
                        if evt is not None:
                            self.callback(evt, payload)
                    else:
                        time.sleep(0.02)
            except serial.SerialException:
                self.connected = False
                time.sleep(2)
            except Exception:
                self.connected = False
                time.sleep(1)

    def stop(self):
        self.running = False


# ─── Stdin Reader (test mode) ────────────────────────────────────────────────

class StdinReader(threading.Thread):
    """Background thread: reads UART-style lines from stdin (test mode).

    Mimics SerialReader's interface so the dashboard doesn't care which
    one is feeding it. Lines may be typed with or without the surrounding
    `---` markers — if missing they're added automatically. Type 'quit'
    or 'exit' (or send EOF / Ctrl+Z) to stop the reader.
    """

    HELP_BANNER = (
        "\n"
        + "─" * 70 + "\n"
        " UART TEST CONSOLE  —  type commands to feed the dashboard.\n"
        " The leading/trailing '---' markers are optional.\n\n"
        " Examples:\n"
        "   RESET COMPLETE\n"
        "   WAKE WORD DETECTED\n"
        "   ShowDrinks[VAL=Cold]\n"
        "   ShowDrinks[VAL=Hot]\n"
        "   StartDrink[VAL=Cafe Latte]\n"
        "   CustomDrink[VAL=2]\n"
        "   ToggleDoubleCup\n"
        "   TempSetting[VAL=Medium]\n"
        "   Stop   |   Acknowledge[VAL=Yes]   |   favorite\n\n"
        " Special:\n"
        "   help            — show this banner again\n"
        "   quit / exit     — stop reading (dashboard keeps running)\n"
        + "─" * 70 + "\n"
    )

    def __init__(self, callback):
        super().__init__(daemon=True)
        self.callback  = callback
        self.running   = True
        self.connected = True   # "link" is always up in test mode

    def run(self):
        print(self.HELP_BANNER, flush=True)
        while self.running:
            try:
                raw = input("uart> ")
            except EOFError:
                print("[test-console] EOF — stopping input reader.", flush=True)
                self.running = False
                self.connected = False
                return
            except Exception as e:
                print(f"[test-console] input error: {e}", flush=True)
                continue

            line = raw.strip()
            if not line:
                continue

            low = line.lower()
            if low in ("quit", "exit"):
                print("[test-console] stopping input reader.", flush=True)
                self.running = False
                self.connected = False
                return
            if low == "help":
                print(self.HELP_BANNER, flush=True)
                continue

            # Auto-wrap with --- markers if the user omitted them.
            if not line.startswith("---"):
                line = "---" + line
            if not line.endswith("---"):
                line = line + "---"

            evt, payload = parse_line(line)
            if evt is None:
                print(f"[test-console] unrecognised: {line}", flush=True)
                continue

            self.callback(evt, payload)

    def stop(self):
        self.running = False


# ─── Dashboard ───────────────────────────────────────────────────────────────

class CoffeeDashboard:
    """Voice interface demo for a coffee machine."""

    # View states (what's drawn on the main panel)
    VIEW_STANDBY  = "standby"
    VIEW_DRINKS   = "drinks"
    VIEW_ACK      = "ack"
    VIEW_BREWING  = "brewing"
    VIEW_DONE     = "done"

    # Machine states (logical coffee-maker state)
    STATE_IDLE  = "IDLE"
    STATE_ACK   = "ACK"
    STATE_BREWA = "BREWA"
    STATE_BREWB = "BREWB"
    ALL_STATES  = (STATE_IDLE, STATE_ACK, STATE_BREWA, STATE_BREWB)

    # Which machine states each command is accepted in.
    # Commands not listed here are accepted in all states.
    # Hooks for non-SHOW_DRINKS commands are stubs; entries below are
    # placeholders to be tightened as full specs arrive.
    COMMAND_ALLOWED_STATES = {
        "show_drinks":      {STATE_IDLE},
        "start_drink":      {STATE_IDLE},
        "custom_drink":     {STATE_IDLE},
        "favorite_drink":   {STATE_IDLE},
        "acknowledge":      {STATE_ACK},
        "toggle_double_cup":{STATE_IDLE},
        "toggle_extra_shot":{STATE_IDLE},
        "toggle_cold_brew": {STATE_IDLE},
        "temp_setting":     {STATE_IDLE, STATE_BREWA},
        "strength_setting": {STATE_IDLE, STATE_BREWA},
        "volume_setting":   {STATE_IDLE, STATE_BREWA},
        "stop":             {STATE_ACK, STATE_BREWA, STATE_BREWB},
    }

    def __init__(self, app: ctk.CTk, port: str, test_mode: bool = False):
        self.app = app
        title = "BrewVoice  ▸  Coffee Maker Voice Interface"
        if test_mode:
            title += "  [TEST MODE]"
        self.app.title(title)
        self.app.geometry("980x720")
        self.app.minsize(820, 640)
        self.app.configure(fg_color=BG_BASE)

        # ── State ──
        self._lock           = threading.Lock()
        self._test_mode      = test_mode
        self._device_ready   = False
        self._machine_state  = self.STATE_IDLE
        self._view           = self.VIEW_STANDBY
        self._view_started   = 0.0
        # Wake-word state — tracked independently of the main view.
        # In test mode wake gating is disabled, so commands flow without
        # needing a prior WAKE WORD DETECTED event.
        self._wake_active    = False
        self._wake_started   = 0.0
        self._drinks_type    = ""
        self._drinks_list    = []
        self._brew_drink     = ""
        self._pending_drink  = None  # dict in CUSTOM_DRINKS shape, or None
        # Persistent toggles, displayed on the dashboard at all times.
        self._toggles = {
            "double_cup": False,
            "extra_shot": False,
            "cold_brew":  False,
        }
        # Persistent level settings (Temp / Strength / Volume).
        # Defaults applied on startup and on every RESET COMPLETE.
        self._settings = {
            "temp":     "Medium",
            "strength": "Medium",
            "volume":   "Medium",
        }
        self._history        = deque(maxlen=HISTORY_LEN)
        self._event_count    = 0

        self._anim_frame = 0

        # Dispatch table: event_type -> handler (called with self._lock held).
        self._command_handlers = {
            "show_drinks":       self._cmd_show_drinks,
            "start_drink":       self._cmd_start_drink,
            "custom_drink":      self._cmd_custom_drink,
            "toggle_double_cup": self._cmd_toggle_double_cup,
            "toggle_extra_shot": self._cmd_toggle_extra_shot,
            "toggle_cold_brew":  self._cmd_toggle_cold_brew,
            "temp_setting":      self._cmd_temp_setting,
            "strength_setting":  self._cmd_strength_setting,
            "volume_setting":    self._cmd_volume_setting,
            "stop":              self._cmd_stop,
            "acknowledge":       self._cmd_acknowledge,
            "favorite_drink":    self._cmd_favorite_drink,
        }

        self._build_ui()

        if test_mode:
            self.reader = StdinReader(self._on_event)
        else:
            self.reader = SerialReader(port, BAUD_RATE, self._on_event)
        self.reader.start()

        self._tick()

    # ── Event callback (serial thread) ────────────────────────────────────────

    def _on_event(self, event_type: str, payload: dict):
        now = time.monotonic()
        with self._lock:
            self._event_count += 1

            if event_type == "reset":
                self._device_ready = True
                self._machine_state = self.STATE_IDLE
                self._wake_active = False
                self._settings = {
                    "temp":     "Medium",
                    "strength": "Medium",
                    "volume":   "Medium",
                }
                self._set_view(self.VIEW_STANDBY, now)
                self._log("SYSTEM", "Coffee maker online — state: IDLE")
                return

            if event_type == "wakeword":
                # Arm the wake-word window. Doesn't change the main view
                # or the machine state; the dedicated wake-word indicator
                # shows the listening animation until a command consumes
                # it or LISTENING_TIMEOUT expires.
                self._wake_active = True
                self._wake_started = now
                self._log("WAKE", "Wake word detected — listening…")
                return

            handler = self._command_handlers.get(event_type)
            if handler is None:
                self._log("UNKNOWN", f"Unknown event: {event_type}")
                return

            # Wake-word gate — every command requires a recent wake word,
            # except in test mode where typed commands flow directly.
            if not self._test_mode:
                wake_ok = (self._wake_active and
                           (now - self._wake_started) <= LISTENING_TIMEOUT)
                if not wake_ok:
                    self._log("NO_WAKE",
                              f"{event_type.upper()} ignored — "
                              f"say wake word first")
                    return
                # Consume the wake word — next command needs another one.
                self._wake_active = False

            allowed = self.COMMAND_ALLOWED_STATES.get(
                event_type, set(self.ALL_STATES))
            if self._machine_state not in allowed:
                self._log("IGNORED",
                          f"{event_type.upper()} ignored in state "
                          f"{self._machine_state}")
                return

            handler(payload, now)

    # ── Command hooks (call with self._lock held) ─────────────────────────────

    def _cmd_show_drinks(self, payload: dict, now: float):
        """ShowDrinks[VAL=Cold|Hot|Extra Shot] — IDLE only, no state change."""
        raw = payload.get("type", "").strip()
        key = raw.lower()
        drinks = DRINK_MENU.get(key)
        if drinks is None:
            self._log("MENU", f"Unknown drink type: {raw!r}")
            self._drinks_type = key
            self._drinks_list = []
        else:
            self._drinks_type = key
            self._drinks_list = list(drinks)
            label = DRINK_TYPE_LABEL.get(key, raw.upper())
            self._log("MENU",
                      f"Showing {label} drinks ({len(drinks)} options)")
        self._set_view(self.VIEW_DRINKS, now)
        # Machine state unchanged (still IDLE).

    # —— Stubs: log only; full behaviour to be filled in later. ——

    def _cmd_start_drink(self, payload: dict, now: float):
        """StartDrink[VAL=<name>] — IDLE only; transitions IDLE → ACK."""
        drink = payload.get("drink", "").strip()
        # Case-insensitive lookup against the known-good hot drinks menu.
        known = {d.lower(): d for d in _DRINKS_HOT}
        canonical = known.get(drink.lower())

        if canonical is None:
            # Unknown drink — stay in IDLE, show a transient error panel.
            self._pending_drink = {
                "drink":    drink or "(unknown)",
                "unknown":  True,
                "number":   None,
            }
            self._set_view(self.VIEW_ACK, now)
            self._log("START", f"Unknown drink: {drink!r}")
            return

        # Valid — snapshot current toggles, transition IDLE → ACK.
        self._pending_drink = {
            "drink":      canonical,
            "number":     None,
            "unknown":    False,
            "temp":       self._settings.get("temp", "Medium"),
            "strength":   self._settings.get("strength", "Medium"),
            "volume":     self._settings.get("volume", "Medium"),
            "double_cup": self._toggles.get("double_cup", False),
            "extra_shot": self._toggles.get("extra_shot", False),
            "cold_brew":  self._toggles.get("cold_brew", False),
        }
        self._machine_state = self.STATE_ACK
        self._set_view(self.VIEW_ACK, now)
        self._log("START", f"{canonical} — awaiting ACK")

    def _cmd_custom_drink(self, payload: dict, now: float):
        """CustomDrink[VAL=1..4] — IDLE only; transitions IDLE → ACK."""
        raw = payload.get("number", "").strip()
        try:
            number = int(raw)
        except ValueError:
            self._log("CUSTOM", f"Invalid CustomDrink number: {raw!r}")
            return

        preset = CUSTOM_DRINKS.get(number)
        if preset is None:
            # Unknown slot — stay in IDLE, just show a transient message.
            self._pending_drink = {
                "number":     number,
                "unknown":    True,
            }
            self._set_view(self.VIEW_ACK, now)
            self._log("CUSTOM", f"No Custom Drink #{number}")
            return

        # Valid preset — transition IDLE → ACK and show the details for
        # the user to confirm via ACKNOWLEDGE.
        self._pending_drink = dict(preset, number=number, unknown=False)
        self._machine_state = self.STATE_ACK
        self._set_view(self.VIEW_ACK, now)
        self._log("CUSTOM",
                  f"Custom #{number}: {preset['drink']} — awaiting ACK")

    def _cmd_toggle_double_cup(self, payload: dict, now: float):
        """ToggleDoubleCup — IDLE only; state unchanged."""
        self._toggle_mode("double_cup", "Double Cup")

    def _cmd_toggle_extra_shot(self, payload: dict, now: float):
        """ToggleExtraShot — IDLE only; state unchanged."""
        self._toggle_mode("extra_shot", "Extra Shot")

    def _cmd_toggle_cold_brew(self, payload: dict, now: float):
        """ToggleColdBrew — IDLE only; state unchanged."""
        self._toggle_mode("cold_brew", "Cold Brew")

    def _toggle_mode(self, key: str, label: str):
        new_val = not self._toggles.get(key, False)
        self._toggles[key] = new_val
        self._log("TOGGLE", f"{label}: {'Yes' if new_val else 'No'}")

    def _cmd_temp_setting(self, payload: dict, now: float):
        """TempSetting[VAL=High|Medium|Low] — IDLE/BREWA; no state change."""
        self._set_level("temp", "Temperature", payload.get("setting", ""))

    def _cmd_strength_setting(self, payload: dict, now: float):
        """StrengthSetting[VAL=High|Medium|Low] — IDLE/BREWA; no state change."""
        self._set_level("strength", "Strength", payload.get("setting", ""))

    def _cmd_volume_setting(self, payload: dict, now: float):
        """VolumeSetting[VAL=High|Medium|Low] — IDLE/BREWA; no state change."""
        self._set_level("volume", "Volume", payload.get("setting", ""))

    def _set_level(self, key: str, label: str, raw: str):
        raw = raw.strip()
        # Case-insensitive match against the allowed levels.
        canonical = next((l for l in SETTING_LEVELS if l.lower() == raw.lower()),
                         None)
        if canonical is None:
            self._log("SETTING", f"{label}: invalid value {raw!r}")
            return
        self._settings[key] = canonical
        self._log("SETTING", f"{label}: {canonical}")

    def _cmd_stop(self, payload: dict, now: float):
        """Stop — ACK/BREWA/BREWB; returns to IDLE.

        From ACK: cancels the pending drink before brewing starts.
        From BREWA/BREWB: aborts the in-progress brew.
        """
        was_ack = self._machine_state == self.STATE_ACK
        if was_ack:
            drink = (self._pending_drink or {}).get("drink", "") or ""
        else:
            drink = self._brew_drink or ""
        self._machine_state = self.STATE_IDLE
        self._pending_drink = None
        self._brew_drink = ""
        self._set_view(self.VIEW_STANDBY, now)
        if was_ack:
            self._log("STOP", f"Cancelled{(': ' + drink) if drink else ''}")
        elif drink:
            self._log("STOP", f"Brew stopped: {drink}")
        else:
            self._log("STOP", "Brew stopped")

    def _cmd_acknowledge(self, payload: dict, now: float):
        """Acknowledge[VAL=Yes|No] — ACK only.

        Yes → begin brewing (ACK → BREWA).
        No  → cancel and return to IDLE.
        """
        raw = payload.get("val", "").strip().lower()
        if raw not in ("yes", "no"):
            self._log("ACK", f"Invalid Acknowledge value: {payload.get('val', '')!r}")
            return

        if raw == "no":
            drink = (self._pending_drink or {}).get("drink", "")
            self._pending_drink = None
            self._machine_state = self.STATE_IDLE
            self._set_view(self.VIEW_STANDBY, now)
            self._log("ACK", f"Cancelled{(': ' + drink) if drink else ''}")
            return

        # Yes — must have a valid pending drink to brew.
        pending = self._pending_drink
        if not pending or pending.get("unknown") or not pending.get("drink"):
            self._pending_drink = None
            self._machine_state = self.STATE_IDLE
            self._set_view(self.VIEW_STANDBY, now)
            self._log("ACK", "Nothing to brew")
            return

        self._brew_drink = pending["drink"]
        self._machine_state = self.STATE_BREWA
        self._set_view(self.VIEW_BREWING, now)
        self._log("ACK", f"Brewing {self._brew_drink}")

    def _cmd_favorite_drink(self, payload: dict, now: float):
        """`favorite` — IDLE only; loads the hardcoded preset into ACK."""
        self._pending_drink = dict(FAVORITE_DRINK_PRESET,
                                   number=None, unknown=False, favorite=True)
        self._machine_state = self.STATE_ACK
        self._set_view(self.VIEW_ACK, now)
        self._log("FAVORITE",
                  f"Favorite drink: {FAVORITE_DRINK_PRESET['drink']} — awaiting ACK")

    def _set_view(self, view: str, now: float):
        self._view = view
        self._view_started = now

    def _log(self, tag: str, msg: str):
        self._history.append((tag, msg))

    # ── UI Construction ───────────────────────────────────────────────────────

    def _build_ui(self):
        self.app.grid_columnconfigure(0, weight=1)
        self.app.grid_rowconfigure(2, weight=1)

        self._build_header(row=0)
        self._build_separator(row=1)
        self._build_centre(row=2)
        self._build_separator(row=3)
        self._build_log(row=4)

    # ── Header ────────────────────────────────────────────────────────────────

    def _build_header(self, row: int):
        hdr = ctk.CTkFrame(self.app, fg_color=BG_BASE, corner_radius=0)
        hdr.grid(row=row, column=0, sticky="ew")
        hdr.grid_columnconfigure(1, weight=1)

        left = ctk.CTkFrame(hdr, fg_color="transparent")
        left.grid(row=0, column=0, padx=28, pady=16, sticky="w")

        ctk.CTkLabel(
            left, text="☕  BREWVOICE",
            font=ctk.CTkFont(family="Consolas", size=22, weight="bold"),
            text_color=CARAMEL,
        ).pack(side="left")

        ctk.CTkLabel(
            left, text="   //  Coffee Maker Voice Interface",
            font=ctk.CTkFont(family="Consolas", size=14),
            text_color=FG_TERTIARY,
        ).pack(side="left")

        right = ctk.CTkFrame(hdr, fg_color="transparent")
        right.grid(row=0, column=2, padx=28, pady=16, sticky="e")

        self.status_frame = ctk.CTkFrame(right, fg_color=BG_SURFACE,
                                          corner_radius=6, border_width=1,
                                          border_color=BORDER)
        self.status_frame.pack(side="left", padx=4)

        self.status_dot = ctk.CTkLabel(self.status_frame, text="●",
                                        font=ctk.CTkFont(size=16),
                                        text_color=STATUS_OFFLINE, width=20)
        self.status_dot.pack(side="left", padx=(10, 2), pady=7)

        self.status_text = ctk.CTkLabel(self.status_frame, text="OFFLINE",
                                         font=ctk.CTkFont(family="Consolas", size=14),
                                         text_color=FG_SECONDARY)
        self.status_text.pack(side="left", padx=(0, 12), pady=7)

        self.counter_lbl = ctk.CTkLabel(right, text="0 events",
                                         font=ctk.CTkFont(family="Consolas", size=14),
                                         text_color=FG_TERTIARY)
        self.counter_lbl.pack(side="left", padx=(12, 0))

    def _build_separator(self, row: int):
        sep = ctk.CTkFrame(self.app, fg_color=BORDER, height=1, corner_radius=0)
        sep.grid(row=row, column=0, sticky="ew", padx=0)

    # ── Centre ────────────────────────────────────────────────────────────────

    def _build_centre(self, row: int):
        centre = ctk.CTkFrame(self.app, fg_color=BG_BASE, corner_radius=0)
        centre.grid(row=row, column=0, sticky="nsew", padx=28, pady=20)
        # Fixed-width left/right columns so cards don't reflow when
        # their content changes; only the centre column stretches.
        centre.grid_columnconfigure(0, weight=0, minsize=240)
        centre.grid_columnconfigure(1, weight=1, minsize=280)
        centre.grid_columnconfigure(2, weight=0, minsize=360)
        centre.grid_rowconfigure(1, weight=1)

        # Row 0: persistent wake-word indicator spanning all columns.
        self._build_wake_indicator(centre, row=0)

        # Row 1: device status (left) + main display (centre) + commands (right).
        self._build_device_panel(centre, col=0, row=1)
        self._build_main_display(centre, col=1, row=1)
        self._build_commands_panel(centre, col=2, row=1)

    # ── Wake-word indicator (always visible) ──────────────────────────────────

    def _build_wake_indicator(self, parent, row: int):
        card = ctk.CTkFrame(parent, fg_color=BG_SURFACE, corner_radius=12,
                             border_width=1, border_color=BORDER)
        card.grid(row=row, column=0, columnspan=3, sticky="ew",
                   padx=0, pady=(0, 12))
        card.grid_columnconfigure(1, weight=1)

        # Left side: label + status text
        text_col = ctk.CTkFrame(card, fg_color="transparent")
        text_col.grid(row=0, column=0, sticky="w", padx=(20, 12), pady=12)

        ctk.CTkLabel(text_col, text="WAKE WORD",
                      font=ctk.CTkFont(family="Consolas", size=11,
                                        weight="bold"),
                      text_color=FG_TERTIARY).pack(anchor="w")

        self.wake_status_lbl = ctk.CTkLabel(
            text_col, text='Waiting for "Coffee Maker"',
            font=ctk.CTkFont(family="Consolas", size=18, weight="bold"),
            text_color=FG_TERTIARY,
        )
        self.wake_status_lbl.pack(anchor="w", pady=(2, 0))

        # Right side: equalizer animation (idle = flat dim bars,
        # listening = animated bars)
        bars = ctk.CTkFrame(card, fg_color="transparent",
                             width=140, height=60)
        bars.grid(row=0, column=1, sticky="e", padx=(12, 20), pady=12)
        bars.grid_propagate(False)
        self._wake_bars = []
        for _ in range(9):
            b = ctk.CTkFrame(bars, fg_color=FG_DIM, corner_radius=4,
                              width=10, height=10)
            b.pack(side="left", padx=3)
            b.pack_propagate(False)
            self._wake_bars.append(b)

        # In test mode wake gating is disabled — show a clear badge.
        if self._test_mode:
            badge = ctk.CTkLabel(
                card, text="TEST MODE — gate disabled",
                font=ctk.CTkFont(family="Consolas", size=11, weight="bold"),
                text_color=BG_BASE,
                fg_color=CARAMEL_HOT, corner_radius=6,
            )
            badge.grid(row=0, column=2, sticky="e", padx=(0, 16),
                       pady=12, ipadx=10, ipady=4)

    # ── Left: Device Status ───────────────────────────────────────────────────

    def _build_device_panel(self, parent, col: int, row: int = 0):
        card = ctk.CTkFrame(parent, fg_color=BG_SURFACE, corner_radius=12,
                             border_width=1, border_color=BORDER,
                             width=240)
        card.grid(row=row, column=col, sticky="nsew", padx=(0, 12))
        # Lock the card's requested width so changing inner content
        # (status text, settings values, etc.) cannot push the column wider.
        card.grid_propagate(False)
        card.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(card, text="MACHINE STATUS",
                     font=ctk.CTkFont(family="Consolas", size=14),
                     text_color=FG_TERTIARY).grid(row=0, column=0, pady=(18, 4))

        self.device_icon = ctk.CTkLabel(card, text="☕",
                                         font=ctk.CTkFont(size=64),
                                         text_color=FG_DIM)
        self.device_icon.grid(row=1, column=0, pady=(4, 4))

        self.device_status_lbl = ctk.CTkLabel(card, text="Waiting for\nmachine…",
                                               font=ctk.CTkFont(family="Consolas", size=14),
                                               text_color=FG_TERTIARY,
                                               justify="center")
        self.device_status_lbl.grid(row=2, column=0, pady=(0, 8))

        self.uart_lbl = ctk.CTkLabel(card, text="UART —",
                                      font=ctk.CTkFont(family="Consolas", size=13),
                                      text_color=FG_DIM)
        self.uart_lbl.grid(row=3, column=0, pady=(0, 6))

        # Machine state pill
        self.state_pill = ctk.CTkLabel(
            card, text="STATE: IDLE",
            font=ctk.CTkFont(family="Consolas", size=12, weight="bold"),
            text_color=CARAMEL_HOT,
            fg_color=BG_ELEVATED,
            corner_radius=6,
        )
        self.state_pill.grid(row=4, column=0, padx=14, pady=(0, 8),
                              sticky="ew", ipadx=8, ipady=4)

        # Toggles (Double Cup / Extra Shot / Cold Brew)
        toggles_box = ctk.CTkFrame(card, fg_color=BG_ELEVATED, corner_radius=8,
                                     border_width=1, border_color=BORDER)
        toggles_box.grid(row=5, column=0, sticky="ew", padx=14, pady=(4, 8))
        toggles_box.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(toggles_box, text="MODES",
                      font=ctk.CTkFont(family="Consolas", size=11,
                                        weight="bold"),
                      text_color=CARAMEL).grid(row=0, column=0, columnspan=2,
                                                sticky="w", padx=10,
                                                pady=(8, 4))

        self._toggle_labels = {}
        toggle_rows = [
            ("double_cup", "Double Cup"),
            ("extra_shot", "Extra Shot"),
            ("cold_brew",  "Cold Brew"),
        ]
        for i, (key, label) in enumerate(toggle_rows, start=1):
            ctk.CTkLabel(
                toggles_box, text=label,
                font=ctk.CTkFont(family="Consolas", size=13),
                text_color=FG_SECONDARY,
                anchor="w",
            ).grid(row=i, column=0, sticky="w", padx=(10, 6), pady=2)

            v = ctk.CTkLabel(
                toggles_box, text="No",
                font=ctk.CTkFont(family="Consolas", size=13, weight="bold"),
                text_color=FG_TERTIARY,
                anchor="e",
            )
            v.grid(row=i, column=1, sticky="e", padx=(6, 10), pady=2)
            self._toggle_labels[key] = v

        # Bottom padding row
        ctk.CTkFrame(toggles_box, fg_color="transparent", height=4).grid(
            row=len(toggle_rows) + 1, column=0, columnspan=2)

        # Settings (Temperature / Strength / Volume)
        settings_box = ctk.CTkFrame(card, fg_color=BG_ELEVATED, corner_radius=8,
                                      border_width=1, border_color=BORDER)
        settings_box.grid(row=6, column=0, sticky="ew", padx=14, pady=(4, 8))
        settings_box.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(settings_box, text="SETTINGS",
                      font=ctk.CTkFont(family="Consolas", size=11,
                                        weight="bold"),
                      text_color=CARAMEL).grid(row=0, column=0, columnspan=2,
                                                sticky="w", padx=10,
                                                pady=(8, 4))

        self._setting_labels = {}
        setting_rows = [
            ("temp",     "Temperature"),
            ("strength", "Strength"),
            ("volume",   "Volume"),
        ]
        for i, (key, label) in enumerate(setting_rows, start=1):
            ctk.CTkLabel(
                settings_box, text=label,
                font=ctk.CTkFont(family="Consolas", size=13),
                text_color=FG_SECONDARY,
                anchor="w",
            ).grid(row=i, column=0, sticky="w", padx=(10, 6), pady=2)

            v = ctk.CTkLabel(
                settings_box, text="—",
                font=ctk.CTkFont(family="Consolas", size=13, weight="bold"),
                text_color=FG_TERTIARY,
                anchor="e",
            )
            v.grid(row=i, column=1, sticky="e", padx=(6, 10), pady=2)
            self._setting_labels[key] = v

        # Bottom padding row
        ctk.CTkFrame(settings_box, fg_color="transparent", height=4).grid(
            row=len(setting_rows) + 1, column=0, columnspan=2)

        # Hint area: shows what to say
        hint_box = ctk.CTkFrame(card, fg_color=BG_ELEVATED, corner_radius=8,
                                 border_width=1, border_color=BORDER)
        hint_box.grid(row=7, column=0, sticky="ew", padx=14, pady=(4, 14))

        ctk.CTkLabel(hint_box, text="TRY SAYING",
                     font=ctk.CTkFont(family="Consolas", size=11, weight="bold"),
                     text_color=CARAMEL).pack(anchor="w", padx=10, pady=(8, 2))

        ctk.CTkLabel(
            hint_box,
            text='"Coffee Maker, make me a Cappuccino"',
            font=ctk.CTkFont(family="Consolas", size=12),
            text_color=FG_SECONDARY,
            justify="left",
        ).pack(anchor="w", padx=10, pady=(0, 10))

    # ── Centre: Main Display ──────────────────────────────────────────────────

    def _build_main_display(self, parent, col: int, row: int = 0):
        card = ctk.CTkFrame(parent, fg_color=BG_SURFACE, corner_radius=12,
                             border_width=1, border_color=BORDER)
        card.grid(row=row, column=col, sticky="nsew", padx=12)
        card.grid_columnconfigure(0, weight=1)
        card.grid_rowconfigure(2, weight=1)

        self.main_card = card

        self.main_header = ctk.CTkLabel(
            card, text="STANDING BY",
            font=ctk.CTkFont(family="Consolas", size=14),
            text_color=FG_TERTIARY,
        )
        self.main_header.grid(row=0, column=0, pady=(18, 0))

        # Stack of swappable view containers; we'll show/hide them.
        self._views_frame = ctk.CTkFrame(card, fg_color="transparent")
        self._views_frame.grid(row=1, column=0, rowspan=2, sticky="nsew",
                               padx=20, pady=(8, 20))
        self._views_frame.grid_columnconfigure(0, weight=1)
        self._views_frame.grid_rowconfigure(0, weight=1)

        self._build_view_standby(self._views_frame)
        self._build_view_drinks(self._views_frame)
        self._build_view_ack(self._views_frame)
        self._build_view_brewing(self._views_frame)

        self._show_view_widget(self._standby_frame)

    # ── Right: Voice Commands panel ───────────────────────────────────────────

    # Per-state list of voice commands shown to the user. Empty list means
    # the panel shows a "no commands" placeholder for that state.
    COMMANDS_BY_STATE = {
        "IDLE": [
            "Which drinks can I brew [cold/hot]",
            "Double cup mode",
            "Extra shot mode",
            "Cold brew mode",
            "Temp setting [low/medium/high]",
            "Strength setting [low/medium/high]",
            "Volume setting [low/medium/high]",
            "Make my favorite drink",
            'Brew/start/make [drink name]',
            "Custom brew [1/2/3/4]",
        ],
        "ACK": ["Yes", "No", "Stop", "Cancel"],
        "BREWA": [
            "Temp setting [low/medium/high]",
            "Strength setting [low/medium/high]",
            "Volume setting [low/medium/high]",
            "Stop/Cancel",
        ],
        "BREWB": [
            "Stop/Cancel",
        ],
    }

    def _build_commands_panel(self, parent, col: int, row: int = 0):
        card = ctk.CTkFrame(parent, fg_color=BG_SURFACE, corner_radius=12,
                             border_width=1, border_color=BORDER,
                             width=360)
        card.grid(row=row, column=col, sticky="nsew", padx=(12, 0))
        # Lock the card's requested width so the per-state command list
        # cannot push the column wider when it changes.
        card.grid_propagate(False)
        card.grid_columnconfigure(0, weight=1)
        card.grid_rowconfigure(1, weight=1)

        ctk.CTkLabel(card, text="AVAILABLE COMMANDS",
                     font=ctk.CTkFont(family="Consolas", size=14),
                     text_color=FG_TERTIARY).grid(row=0, column=0,
                                                   pady=(18, 8))

        self._commands_list_frame = ctk.CTkFrame(card, fg_color="transparent")
        self._commands_list_frame.grid(row=1, column=0, sticky="nsew",
                                        padx=14, pady=(0, 14))
        self._commands_list_frame.grid_columnconfigure(0, weight=1)

        self._command_pills = []
        # Sentinel that won't match any state, forces first populate.
        self._commands_state = object()

    def _populate_commands(self, state: str):
        for w in self._command_pills:
            w.destroy()
        self._command_pills.clear()

        cmds = self.COMMANDS_BY_STATE.get(state, [])
        if not cmds:
            lbl = ctk.CTkLabel(
                self._commands_list_frame,
                text="(no commands)",
                font=ctk.CTkFont(family="Consolas", size=12, slant="italic"),
                text_color=FG_DIM,
            )
            lbl.grid(row=0, column=0, sticky="ew", pady=4)
            self._command_pills.append(lbl)
            return

        for i, cmd in enumerate(cmds):
            pill = ctk.CTkLabel(
                self._commands_list_frame,
                text=f'"{cmd}"',
                font=ctk.CTkFont(family="Consolas", size=14, weight="bold"),
                text_color=CREAM,
                fg_color=BG_ELEVATED,
                corner_radius=6,
                anchor="w",
            )
            pill.grid(row=i, column=0, sticky="ew", padx=4, pady=4,
                       ipadx=10, ipady=6)
            self._command_pills.append(pill)

    # ── View: Standby ─────────────────────────────────────────────────────────

    def _build_view_standby(self, parent):
        f = ctk.CTkFrame(parent, fg_color="transparent")
        f.grid(row=0, column=0, sticky="nsew")
        f.grid_columnconfigure(0, weight=1)

        self.standby_icon = ctk.CTkLabel(f, text="☕",
                                          font=ctk.CTkFont(size=110),
                                          text_color=CARAMEL_DIM)
        self.standby_icon.pack(pady=(20, 10))

        self.standby_title = ctk.CTkLabel(
            f, text="Ready to brew",
            font=ctk.CTkFont(family="Consolas", size=28, weight="bold"),
            text_color=CARAMEL,
        )
        self.standby_title.pack()

        self.standby_subtitle = ctk.CTkLabel(
            f, text='Say "Coffee Maker" to get started',
            font=ctk.CTkFont(family="Consolas", size=15),
            text_color=FG_TERTIARY,
        )
        self.standby_subtitle.pack(pady=(6, 0))

        self._standby_frame = f

    # ── View: Drinks list ─────────────────────────────────────────────────────

    def _build_view_drinks(self, parent):
        f = ctk.CTkFrame(parent, fg_color="transparent")
        f.grid(row=0, column=0, sticky="nsew")
        f.grid_columnconfigure(0, weight=1)
        f.grid_rowconfigure(1, weight=1)

        self.drinks_title = ctk.CTkLabel(
            f, text="OUR DRINKS",
            font=ctk.CTkFont(family="Consolas", size=24, weight="bold"),
            text_color=CARAMEL_HOT,
        )
        self.drinks_title.grid(row=0, column=0, pady=(8, 12), sticky="n")

        # Scrollable list area
        self.drinks_list_frame = ctk.CTkScrollableFrame(
            f, fg_color=BG_ELEVATED, corner_radius=8,
            scrollbar_button_color=CARAMEL_DIM,
        )
        self.drinks_list_frame.grid(row=1, column=0, sticky="nsew")
        self.drinks_list_frame.grid_columnconfigure(0, weight=1)

        self._drink_row_widgets = []

        self._drinks_frame = f

    def _populate_drinks(self, drink_type: str, drinks: list):
        # Clear previous rows
        for w in self._drink_row_widgets:
            w.destroy()
        self._drink_row_widgets.clear()

        # Title shows the type
        type_pretty = DRINK_TYPE_LABEL.get(drink_type, drink_type.upper())
        accent = BLUE_COLD if drink_type == "cold" else CARAMEL_HOT
        self.drinks_title.configure(text=f"{type_pretty} DRINKS",
                                     text_color=accent)

        if not drinks:
            row = ctk.CTkFrame(self.drinks_list_frame, fg_color=BG_SURFACE,
                                corner_radius=6, border_width=1,
                                border_color=BORDER)
            row.grid(row=0, column=0, sticky="ew", padx=8, pady=4)
            ctk.CTkLabel(row,
                          text=f"  No drinks for type: {drink_type or '—'}",
                          font=ctk.CTkFont(family="Consolas", size=14),
                          text_color=FG_TERTIARY,
                          anchor="w").pack(anchor="w", padx=12, pady=10)
            self._drink_row_widgets.append(row)
            return

        for i, name in enumerate(drinks):
            row = ctk.CTkFrame(self.drinks_list_frame, fg_color=BG_SURFACE,
                                corner_radius=6, border_width=1,
                                border_color=BORDER)
            row.grid(row=i, column=0, sticky="ew", padx=8, pady=4)
            row.grid_columnconfigure(1, weight=1)

            num = ctk.CTkLabel(row, text=f"{i+1:02d}",
                                font=ctk.CTkFont(family="Consolas", size=18,
                                                  weight="bold"),
                                text_color=accent, width=44)
            num.grid(row=0, column=0, padx=(12, 4), pady=10, sticky="w")

            ctk.CTkLabel(row, text=name,
                          font=ctk.CTkFont(family="Consolas", size=16,
                                            weight="bold"),
                          text_color=FG_PRIMARY,
                          anchor="w").grid(row=0, column=1, sticky="ew",
                                            padx=(4, 12), pady=10)

            self._drink_row_widgets.append(row)

    # ── View: ACK (pending custom drink confirmation) ─────────────────────────

    def _build_view_ack(self, parent):
        f = ctk.CTkFrame(parent, fg_color="transparent")
        f.grid(row=0, column=0, sticky="nsew")
        f.grid_columnconfigure(0, weight=1)

        self.ack_title = ctk.CTkLabel(
            f, text="CUSTOM DRINK",
            font=ctk.CTkFont(family="Consolas", size=14, weight="bold"),
            text_color=FG_TERTIARY,
        )
        self.ack_title.pack(pady=(6, 0))

        self.ack_drink_name = ctk.CTkLabel(
            f, text="—",
            font=ctk.CTkFont(family="Consolas", size=28, weight="bold"),
            text_color=CARAMEL_HOT,
        )
        self.ack_drink_name.pack(pady=(2, 10))

        # Settings grid: 7 rows of label / value
        grid = ctk.CTkFrame(f, fg_color=BG_ELEVATED, corner_radius=8,
                             border_width=1, border_color=BORDER)
        grid.pack(fill="x", padx=10, pady=(0, 10))
        grid.grid_columnconfigure(1, weight=1)

        self._ack_value_lbls = {}
        rows = [
            ("temp",       "Temperature"),
            ("strength",   "Strength"),
            ("volume",     "Volume"),
            ("double_cup", "Double Cup"),
            ("extra_shot", "Extra Shot"),
            ("cold_brew",  "Cold Brew"),
        ]
        for i, (key, label) in enumerate(rows):
            ctk.CTkLabel(
                grid, text=label,
                font=ctk.CTkFont(family="Consolas", size=13),
                text_color=FG_TERTIARY,
                anchor="w",
            ).grid(row=i, column=0, sticky="w", padx=(14, 8), pady=4)

            v = ctk.CTkLabel(
                grid, text="—",
                font=ctk.CTkFont(family="Consolas", size=14, weight="bold"),
                text_color=FG_PRIMARY,
                anchor="e",
            )
            v.grid(row=i, column=1, sticky="e", padx=(8, 14), pady=4)
            self._ack_value_lbls[key] = v

        self.ack_prompt = ctk.CTkLabel(
            f, text='Say "Acknowledge" to start brewing',
            font=ctk.CTkFont(family="Consolas", size=14),
            text_color=CARAMEL,
        )
        self.ack_prompt.pack(pady=(4, 0))

        self._ack_frame = f

    # ── View: Brewing animation ───────────────────────────────────────────────

    def _build_view_brewing(self, parent):
        f = ctk.CTkFrame(parent, fg_color="transparent")
        f.grid(row=0, column=0, sticky="nsew")
        f.grid_columnconfigure(0, weight=1)

        # Steam line above the cup
        self.brew_steam = ctk.CTkLabel(
            f, text="    ",
            font=ctk.CTkFont(family="Consolas", size=22),
            text_color=STEAM,
        )
        self.brew_steam.pack(pady=(16, 0))

        self.brew_icon = ctk.CTkLabel(
            f, text="☕",
            font=ctk.CTkFont(size=110),
            text_color=CARAMEL,
        )
        self.brew_icon.pack(pady=(0, 6))

        self.brew_title = ctk.CTkLabel(
            f, text="BREWING",
            font=ctk.CTkFont(family="Consolas", size=26, weight="bold"),
            text_color=CARAMEL_HOT,
        )
        self.brew_title.pack()

        self.brew_drink_name = ctk.CTkLabel(
            f, text="—",
            font=ctk.CTkFont(family="Consolas", size=20, weight="bold"),
            text_color=FG_PRIMARY,
        )
        self.brew_drink_name.pack(pady=(4, 14))

        # Progress bar
        self.brew_progress = ctk.CTkProgressBar(
            f, width=420, height=14,
            progress_color=CARAMEL_HOT,
            fg_color=BG_ELEVATED,
            border_color=BORDER, border_width=1,
            corner_radius=6,
        )
        self.brew_progress.set(0.0)
        self.brew_progress.pack(pady=(0, 6))

        self.brew_pct = ctk.CTkLabel(
            f, text="0%",
            font=ctk.CTkFont(family="Consolas", size=14),
            text_color=FG_SECONDARY,
        )
        self.brew_pct.pack()

        self._brewing_frame = f

    def _show_view_widget(self, target):
        for w in (self._standby_frame, self._drinks_frame,
                  self._ack_frame, self._brewing_frame):
            if w is target:
                w.tkraise()
                w.grid()
            else:
                w.grid_remove()

    # ── Bottom: Event log ─────────────────────────────────────────────────────

    def _build_log(self, row: int):
        outer = ctk.CTkFrame(self.app, fg_color=BG_BASE, corner_radius=0)
        outer.grid(row=row, column=0, sticky="sew")
        outer.grid_columnconfigure(0, weight=1)

        pad = ctk.CTkFrame(outer, fg_color="transparent")
        pad.pack(fill="x", padx=28, pady=(12, 20))

        ctk.CTkLabel(pad, text="☕  ACTIVITY LOG",
                     font=ctk.CTkFont(family="Consolas", size=14),
                     text_color=FG_TERTIARY).pack(anchor="w", pady=(0, 6))

        self.log_var = ctk.StringVar(value="  No activity yet.")
        self.log_lbl = ctk.CTkLabel(
            pad, textvariable=self.log_var,
            font=ctk.CTkFont(family="Consolas", size=14),
            text_color=FG_SECONDARY,
            fg_color=BG_SURFACE,
            corner_radius=6,
            wraplength=900,
            justify="left",
            anchor="nw",
            height=92,
        )
        self.log_lbl.pack(fill="x", ipadx=14)
        self.log_lbl.pack_propagate(False)

    # ── UI Refresh Loop (~20 fps) ─────────────────────────────────────────────

    def _tick(self):
        now = time.monotonic()
        self._anim_frame += 1

        with self._lock:
            device_ready    = self._device_ready
            machine_state   = self._machine_state
            view            = self._view
            view_started    = self._view_started
            wake_active     = self._wake_active
            wake_started    = self._wake_started
            drinks_type     = self._drinks_type
            drinks_list     = list(self._drinks_list)
            brew_drink      = self._brew_drink
            pending_drink   = (dict(self._pending_drink)
                                if self._pending_drink else None)
            toggles         = dict(self._toggles)
            settings        = dict(self._settings)
            history         = list(self._history)
            event_count     = self._event_count

        serial_ok = self.reader.connected
        view_elapsed = now - view_started if view_started else 0.0

        # ── Auto-transitions ──
        # Wake-word window expiry — indicator returns to idle.
        if (wake_active and not self._test_mode
                and (now - wake_started) > LISTENING_TIMEOUT):
            with self._lock:
                self._wake_active = False
                self._log("WAKE", "Wake-word window timed out")
            wake_active = False

        if view == self.VIEW_DRINKS and view_elapsed > DISPLAY_HOLD_SECONDS:
            with self._lock:
                self._set_view(self.VIEW_STANDBY, now)
            view = self.VIEW_STANDBY

        elif view == self.VIEW_BREWING and view_elapsed > BREW_DURATION_SECONDS:
            with self._lock:
                self._machine_state = self.STATE_IDLE
                self._brew_drink = ""
                self._pending_drink = None
                self._set_view(self.VIEW_DONE, now)
                self._log("DONE", f"Enjoy your {brew_drink}!")
            view = self.VIEW_DONE

        elif (view == self.VIEW_BREWING
              and self._machine_state == self.STATE_BREWA
              and view_elapsed > BREW_DURATION_SECONDS / 2):
            with self._lock:
                self._machine_state = self.STATE_BREWB
                self._log("BREW", "Entering BREWB (settings locked)")

        elif view == self.VIEW_DONE and view_elapsed > 4.0:
            with self._lock:
                self._set_view(self.VIEW_STANDBY, now)
            view = self.VIEW_STANDBY

        # ── Header status chip ──
        if not serial_ok:
            self.status_dot.configure(text_color=STATUS_OFFLINE)
            self.status_text.configure(text="NO LINK")
        elif not device_ready:
            self.status_dot.configure(text_color=STATUS_OFFLINE)
            self.status_text.configure(text="CONNECTING")
        elif wake_active:
            pulse = 0.5 + 0.5 * math.sin(self._anim_frame * 0.4)
            self.status_dot.configure(
                text_color=CARAMEL_HOT if pulse > 0.5 else CARAMEL_DIM)
            self.status_text.configure(text="LISTENING")
        elif view == self.VIEW_BREWING:
            pulse = 0.5 + 0.5 * math.sin(self._anim_frame * 0.25)
            self.status_dot.configure(
                text_color=CARAMEL_HOT if pulse > 0.4 else CARAMEL)
            self.status_text.configure(text="BREWING")
        else:
            self.status_dot.configure(text_color=STATUS_READY)
            self.status_text.configure(text="READY")

        self.counter_lbl.configure(text=f"{event_count} events")

        # ── Device panel ──
        if not serial_ok:
            self.device_icon.configure(text="⊘", text_color=RED)
            self.device_status_lbl.configure(text="No serial\nconnection",
                                              text_color=RED)
            self.uart_lbl.configure(text="UART ✗", text_color=RED_DIM)
        elif not device_ready:
            self.device_icon.configure(text="◌", text_color=FG_TERTIARY)
            self.device_status_lbl.configure(text="Waiting for\nRESET_COMPLETE",
                                              text_color=FG_TERTIARY)
            self.uart_lbl.configure(text="UART ●", text_color=CARAMEL_DIM)
        else:
            self.device_icon.configure(text="☕", text_color=CARAMEL)
            self.device_status_lbl.configure(text="Coffee maker\nonline",
                                              text_color=GREEN)
            self.uart_lbl.configure(text="UART ●", text_color=GREEN_DIM)

        # Machine-state pill colour
        state_colors = {
            self.STATE_IDLE:  (CARAMEL_HOT, BG_ELEVATED),
            self.STATE_ACK:   (CREAM,       CARAMEL_DIM),
            self.STATE_BREWA: (CARAMEL_HOT, CARAMEL_DIM),
            self.STATE_BREWB: (CREAM,       CARAMEL),
        }
        fg, bg = state_colors.get(machine_state, (FG_TERTIARY, BG_ELEVATED))
        if not device_ready:
            fg, bg = FG_DIM, BG_ELEVATED
        state_display = {
            self.STATE_IDLE:  "Waiting for input",
            self.STATE_ACK:   "Waiting for your confirmation",
            self.STATE_BREWA: "Starting your drink",
            self.STATE_BREWB: "Finishing your drink",
        }.get(machine_state, machine_state)
        self.state_pill.configure(text=f"STATE: {state_display}",
                                   text_color=fg, fg_color=bg)

        # ── Voice commands panel (refresh only when state changes) ──
        if machine_state != self._commands_state:
            self._populate_commands(machine_state)
            self._commands_state = machine_state

        # ── Toggle statuses ──
        for key, lbl in self._toggle_labels.items():
            on = toggles.get(key, False)
            lbl.configure(text="Yes" if on else "No",
                           text_color=GREEN if on else FG_TERTIARY)

        # ── Level settings ──
        level_colors = {
            "High":   CARAMEL_HOT,
            "Medium": CARAMEL,
            "Low":    BLUE_COLD,
        }
        for key, lbl in self._setting_labels.items():
            val = settings.get(key)
            if val is None:
                lbl.configure(text="—", text_color=FG_TERTIARY)
            else:
                lbl.configure(text=val,
                               text_color=level_colors.get(val, FG_PRIMARY))

        # ── Wake-word indicator (always visible) ──
        if wake_active:
            self.wake_status_lbl.configure(
                text="LISTENING… (say a command)",
                text_color=CARAMEL_HOT)
            for i, bar in enumerate(self._wake_bars):
                phase = self._anim_frame * 0.4 + i * 0.55
                amp   = 0.5 + 0.5 * math.sin(phase)
                h     = int(10 + amp * 40)        # 10 .. 50 px
                color = CARAMEL_HOT if amp > 0.6 else CARAMEL
                bar.configure(height=h, fg_color=color)
        else:
            self.wake_status_lbl.configure(
                text='Waiting for "Coffee Maker"',
                text_color=FG_TERTIARY)
            for bar in self._wake_bars:
                bar.configure(height=10, fg_color=FG_DIM)

        # ── Main display per view ──
        if view == self.VIEW_STANDBY:
            self._show_view_widget(self._standby_frame)
            if device_ready:
                self.main_header.configure(text="READY", text_color=GREEN_DIM)
                self.standby_icon.configure(text_color=CARAMEL)
                self.standby_title.configure(text="Ready to brew",
                                              text_color=CARAMEL)
                self.standby_subtitle.configure(
                    text='Say "Coffee Maker" to get started')
                self.main_card.configure(border_color=BORDER, border_width=1)
            else:
                self.main_header.configure(text="OFFLINE", text_color=RED_DIM)
                self.standby_icon.configure(text_color=FG_DIM)
                self.standby_title.configure(text="Coffee maker offline",
                                              text_color=FG_TERTIARY)
                self.standby_subtitle.configure(text="Awaiting machine link")
                self.main_card.configure(border_color=BORDER, border_width=1)

        elif view == self.VIEW_DRINKS:
            self._show_view_widget(self._drinks_frame)
            type_label = DRINK_TYPE_LABEL.get(drinks_type,
                                               drinks_type.upper())
            accent = BLUE_COLD if drinks_type == "cold" else CARAMEL_HOT
            self.main_header.configure(
                text=f"DRINKS MENU  ▸  {type_label}",
                text_color=accent)
            # Re-populate only when type/list changes (cheap check by title/len)
            current_title = (f"{type_label} DRINKS"
                              if drinks_type else "OUR DRINKS")
            if (self.drinks_title.cget("text") != current_title
                    or len(self._drink_row_widgets) != len(drinks_list)):
                self._populate_drinks(drinks_type, drinks_list)
            self.main_card.configure(border_color=accent, border_width=2)

        elif view == self.VIEW_ACK:
            self._show_view_widget(self._ack_frame)
            self.main_header.configure(text="AWAITING CONFIRMATION",
                                        text_color=CARAMEL_HOT)
            self.main_card.configure(border_color=CARAMEL_HOT, border_width=2)

            if pending_drink and pending_drink.get("unknown"):
                # Unknown selection — friendly error display. For CUSTOM_DRINK
                # this is a bad slot number; for START_DRINK it's an
                # unrecognised drink name.
                num = pending_drink.get("number")
                if num is not None:
                    header_text = "NOT AVAILABLE"
                    body_text = f"No Custom Drink #{num}"
                    prompt = "Pick a slot from 1 to 4"
                else:
                    header_text = "NOT AVAILABLE"
                    body_text = f"Can't brew: {pending_drink.get('drink', '—')}"
                    prompt = "Try one of the menu drinks"
                self.ack_title.configure(text=header_text, text_color=RED)
                self.ack_drink_name.configure(text=body_text, text_color=RED)
                for v in self._ack_value_lbls.values():
                    v.configure(text="—", text_color=FG_DIM)
                self.ack_prompt.configure(text=prompt, text_color=RED_DIM)
            elif pending_drink:
                num = pending_drink.get("number")
                if pending_drink.get("favorite"):
                    self.ack_title.configure(
                        text="FAVORITE DRINK",
                        text_color=FG_TERTIARY)
                elif num is not None:
                    self.ack_title.configure(
                        text=f"CUSTOM DRINK  #{num}",
                        text_color=FG_TERTIARY)
                else:
                    self.ack_title.configure(
                        text="START DRINK",
                        text_color=FG_TERTIARY)
                self.ack_drink_name.configure(
                    text=pending_drink.get("drink", "—"),
                    text_color=CARAMEL_HOT)
                self._ack_value_lbls["temp"].configure(
                    text=pending_drink.get("temp", "—"),
                    text_color=FG_PRIMARY)
                self._ack_value_lbls["strength"].configure(
                    text=pending_drink.get("strength", "—"),
                    text_color=FG_PRIMARY)
                self._ack_value_lbls["volume"].configure(
                    text=pending_drink.get("volume", "—"),
                    text_color=FG_PRIMARY)
                for key in ("double_cup", "extra_shot", "cold_brew"):
                    on = bool(pending_drink.get(key))
                    self._ack_value_lbls[key].configure(
                        text="Yes" if on else "No",
                        text_color=GREEN if on else FG_TERTIARY)
                self.ack_prompt.configure(
                    text='Say "Coffee Maker Yes" to brew or "Coffee Maker No" to cancel',
                    text_color=CARAMEL)

        elif view == self.VIEW_BREWING:
            self._show_view_widget(self._brewing_frame)
            self.main_header.configure(text="BREWING IN PROGRESS",
                                        text_color=CARAMEL_HOT)
            self.brew_drink_name.configure(text=brew_drink or "—")

            # Steam animation: shifting "≈" patterns
            steam_frames = [
                "  ʃ   ʃ   ʃ  ",
                "  ʃ ʃ ʃ ʃ ʃ ",
                "  ʃ   ʃ   ʃ  ",
                " ʃ ʃ ʃ ʃ ʃ ʃ",
            ]
            self.brew_steam.configure(
                text=steam_frames[(self._anim_frame // 4) % len(steam_frames)])

            # Cup pulse
            pulse = 0.5 + 0.5 * math.sin(self._anim_frame * 0.25)
            self.brew_icon.configure(
                text_color=CARAMEL_HOT if pulse > 0.5 else CARAMEL)

            # Progress bar (smooth)
            pct = max(0.0, min(1.0, view_elapsed / BREW_DURATION_SECONDS))
            self.brew_progress.set(pct)
            self.brew_pct.configure(text=f"{int(pct * 100)}%")
            self.main_card.configure(border_color=CARAMEL_HOT, border_width=2)

        elif view == self.VIEW_DONE:
            self._show_view_widget(self._brewing_frame)
            self.main_header.configure(text="ENJOY!", text_color=GREEN)
            self.brew_drink_name.configure(text=brew_drink or "—")
            self.brew_steam.configure(text="  ✓ ready  ")
            self.brew_icon.configure(text="☕", text_color=GREEN)
            self.brew_progress.set(1.0)
            self.brew_pct.configure(text="100% — done", text_color=GREEN)
            self.main_card.configure(border_color=GREEN, border_width=2)

        # ── Log ──
        if history:
            lines = []
            for tag, msg in history[-4:]:
                lines.append(f"  [{tag:>7s}]  {msg}")
            self.log_var.set("\n".join(lines))

        self.app.after(50, self._tick)

    def on_close(self):
        self.reader.stop()
        self.app.destroy()


# ─── Entry Point ──────────────────────────────────────────────────────────────

def main():
    args = list(sys.argv[1:])
    test_mode = False
    for flag in ("-t", "--test", "--test-mode"):
        if flag in args:
            args.remove(flag)
            test_mode = True

    port = args[0] if args else COM_PORT

    try:
        from ctypes import windll
        windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass

    ctk.set_appearance_mode("dark")
    ctk.set_default_color_theme("dark-blue")

    app = ctk.CTk()
    dashboard = CoffeeDashboard(app, port, test_mode=test_mode)
    app.protocol("WM_DELETE_WINDOW", dashboard.on_close)
    app.mainloop()


if __name__ == "__main__":
    main()
