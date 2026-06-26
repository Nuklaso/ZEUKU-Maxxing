# ZEUKU-Maxxing

## keep_awake.py — Anti-Sperr-Tool für Windows

Verhindert, dass der Rechner nach kurzer Inaktivität schläft, den Bildschirm
abschaltet oder den (passwortgeschützten) Sperrbildschirm zeigt.

- **Keine Abhängigkeiten** — nur Python-Standardbibliothek (`ctypes`, Windows-API).
- **Keine Admin-Rechte** nötig.
- Getestet mit Python 3.12, 64-bit, Windows 11.

### Schnellstart

Doppelklick auf **`start.bat`** (Standard = Smart-Modus). Stoppen mit **Strg+C**
im Fenster.

Oder direkt:

```
python keep_awake.py                 # Smart-Modus (Standard)
python keep_awake.py --force         # klassischer Jiggler: wackelt immer
```

### Zwei Modi

- **Smart (Standard):** Tut nichts, solange du Maus/Tastatur benutzt. Erst wenn
  du länger als `--idle-threshold` Sekunden (Standard 240) wirklich inaktiv
  warst — kurz bevor Windows sperren würde — schickt es eine **unsichtbare**
  Eingabe (F15-Taste bzw. 0-Pixel-Mausnudge) und setzt damit den Inaktivitäts-
  Timer zurück.
- **`--force`:** Schickt die Eingabe stur in jedem `--interval`, egal ob du
  gerade tippst.

### Optionen

| Flag | Standard | Bedeutung |
|------|----------|-----------|
| `--interval SEC` | `30` | Prüf-/Wackel-Takt in Sekunden (>= 1) |
| `--idle-threshold SEC` | `240` | Smart-Modus: erst ab so viel Inaktivität auslösen. **Worst-Case-Verzögerung = `idle-threshold + interval`** — halte das deutlich unter deinem echten Sperr-Timeout (typisch 300 s) |
| `--method {key,mouse,both}` | `key` | Was eingeschleust wird: F15-Taste, 1px-Mausnudge (netto 0) oder beides |
| `--force` | aus | Immer wackeln (ignoriert `--idle-threshold`) |
| `--quiet` | aus | Banner + INFO-Zeilen unterdrücken |
| `--no-keep-display` | aus | `SetThreadExecutionState`-Power-Keep abschalten (ist sonst an) |

### Wichtiger Hinweis (Firmen-Laptop)

Das Tool setzt die **Standard**-Windows-Timer für Idle/Sleep/Screensaver zurück.
Manche unternehmensseitig erzwungenen Sperren (Smartcard-Entnahme, bestimmte
Gruppenrichtlinien, Modern Standby) ignorieren synthetische Eingaben und lassen
sich damit **nicht** aushebeln. Ob du es einsetzen *darfst*, ist eine
Compliance-Frage — technisch braucht es keine Admin-Rechte.

---

## GUI — `keep_awake_gui.py`

Ein kleines Fenster-Frontend (tkinter/ttk, **nur Standardbibliothek**) um
dieselben getesteten Bausteine aus `keep_awake.py`. Die ctypes-/SendInput-Logik
wird **wiederverwendet**, nicht neu geschrieben.

### Starten

```
python keep_awake_gui.py
```

(oder Doppelklick auf die fertige `ZEUKU-KeepAwake.exe`, siehe unten).

### Bedienung

- **Modus:** *Smart* (nur bei Inaktivität) oder *Force* (immer wackeln). Im
  Force-Modus ist das Feld **Idle-Schwelle** deaktiviert.
- **Intervall (Sek.):** Prüf-/Wackel-Takt, 1–3600, Standard 30.
- **Idle-Schwelle (Sek.):** Smart-Modus, 1–7200, Standard 240.
- **Methode:** `key` (F15), `mouse` (0-Pixel-Nudge) oder `both`. Standard `key`.
- **Bildschirm wachhalten:** `SetThreadExecutionState`-Power-Keep, Standard an.
- **Start / Stop** und eine **Status**-Zeile.
- Ein **Hinweis** zeigt die Worst-Case-Verzögerung (`Schwelle + Intervall`) und
  färbt sich, wenn sie sich der typischen 300-s-Sperre nähert.
- Ein mitlaufendes, schreibgeschütztes **Log** (mit „Log leeren"-Knopf).

Während ein Lauf aktiv ist, sind alle Einstellungs-Felder gesperrt — Änderungen
wirken sich erst auf den nächsten Start aus. Beim Schließen des Fensters wird der
Worker sauber gestoppt und der Power-Keep zurückgesetzt (kein Zombie-Thread,
keine hängende Execution-State-Latch).

#### Technik (kurz)

Die Keep-Awake-Schleife läuft in einem Hintergrund-Thread, der **nie** ein
Tk-Widget anfasst. Kommunikation läuft ausschließlich über eine `queue.Queue`
(Log/Status) und ein `threading.Event` (Stop-Flag); der UI-Thread leert die
Queue periodisch per `root.after(...)`. Alle Parameter werden beim Start
eingefroren. `keep_awake.log` wird auf eine GUI-Senke umgebogen, damit auch
Primitiv-Warnungen (z. B. „SendInput blockiert") im Log landen — wichtig, weil
unter `--windowed` kein `stdout`/`stderr` existiert.

---

## EXE bauen — `build_exe.bat`

Erzeugt eine eigenständige `.exe` (kein Python nötig zum Ausführen) mit
PyInstaller — **onefile**, **windowed** (ohne Konsolenfenster).

### Voraussetzung

```
py -m pip install pyinstaller
```

### Bauen

Doppelklick auf **`build_exe.bat`** (oder im Terminal aufrufen). Das Skript:

1. wechselt in seinen eigenen Ordner,
2. wählt den Interpreter (`py`, sonst `python`),
3. prüft, ob PyInstaller installiert ist (sonst freundlicher Hinweis),
4. baut `keep_awake_gui.py` — `keep_awake.py` wird automatisch mitgebündelt,
   weil es importiert wird.

Ergebnis:

```
dist\ZEUKU-KeepAwake.exe
```

Diese Datei läuft eigenständig per Doppelklick. Der gesamte Build-Aufruf lautet:

```
pyinstaller --noconfirm --onefile --windowed --name ZEUKU-KeepAwake keep_awake_gui.py
```
