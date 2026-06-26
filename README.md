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
