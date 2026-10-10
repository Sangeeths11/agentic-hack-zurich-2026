# Scam the Scamers

Ein lokaler Sprachagent, der neben deinem Handy mithört und über den Laptop
antwortet. Handy auf Lautsprecher, Laptop daneben, Listener aktivieren.

## Starten

Voraussetzung: [uv](https://docs.astral.sh/uv/getting-started/installation/).
Python 3.12 wird beim Setup installiert. Gradbot 0.1.9 unterstützt nativ
macOS Apple Silicon und Linux x86_64.

```bash
scripts/setup.sh
# GRADIUM_API_KEY und LLM_API_KEY in .env eintragen
scripts/run_local.sh
```

**http://localhost:8082** öffnen, **Listener aktivieren** klicken und das Mikrofon
freigeben. Der Browser muss AudioWorklet unterstützen. Tab und Rechner bleiben
während des Gesprächs aktiv.

## So funktioniert es

- Du nimmst den Anruf am Handy an und schaltest den Lautsprecher ein.
- Der animierte Kreis zeigt den Mikrofonpegel. Nach 180 ms oberhalb der
  einstellbaren Schwelle startet der Agent automatisch. Ein kurzer Vorlauf
  erhält den Satzanfang während des Verbindungsaufbaus.
- Der Agent stellt sich als KI-Assistent vor, hört zu und antwortet über die
  Laptop-Lautsprecher. Du kannst seine Antwort unterbrechen.
- **Gespräch beenden** lässt den Listener auf das nächste Gespräch warten.
  Nach 30 Sekunden Stille oder dem Sitzungslimit geschieht dies automatisch.
- **Listener pausieren** beendet die Sitzung, Wiedergabe und den Mikrofonzugriff.

Der Auslöser erkennt Lautstärke, keinen Anrufstatus. Auch Klingeln oder Musik
können starten. Schwelle und Lautstärke an den Aufbau anpassen. Die angeforderte
Echo-Unterdrückung hängt vom Browser und Gerät ab; mit einem zweiten Telefon testen.

## Konfiguration

Nur `GRADIUM_API_KEY` und `LLM_API_KEY` sind erforderlich. `.env.example` enthält
die weiteren Optionen: Modellanbieter, Modell, Name, Sprache, Stimme und Zeitlimits.
Standard: Deutsch, `gpt-4o-mini`, eine Sitzung gleichzeitig, maximal fünf Minuten.
Nach Änderungen den Server neu starten.

```bash
uv run scam-the-scamers serve          # Server auf 127.0.0.1:8082
uv run scam-the-scamers doctor         # nur Konfiguration prüfen
uv run scam-the-scamers doctor --live  # echte Sprachdienste prüfen; nutzt API-Guthaben
```

Im Wartezustand bleibt Audio im Browser. Während einer Sitzung verarbeiten
Gradium und der konfigurierte Modellanbieter die Sprache. Die App speichert
keine Aufnahmen oder Transkripte. API-Schlüssel bleiben auf dem Server.
Verbindungsfehler pausieren den Listener und werden sichtbar angezeigt.

## Repository

```text
src/scam_the_scamers/
  agent.py          Sprachagent und Gradbot-Anbindung
  bridge.py         Audiostream, Unterbrechungen und Sitzungslebenszyklus
  config.py         Einstellungen und Provider-Schlüssel
  main.py           Lokale HTTP-/WebSocket-Endpunkte
  cli.py            Start und Diagnose
  static/           Listener-Oberfläche und Browser-Audio
scripts/            Setup und lokaler Start
tests/              Audio-, Protokoll- und Browserprüfungen
```

## Prüfen

```bash
uv run ruff check src tests
uv run ruff format --check src tests
uv run mypy src
uv run pytest -q
node --test tests/test_browser_audio.mjs  # Node 22.7+
uv build
```

Optional mit installiertem Chrome:

```bash
uv run --with playwright python tests/browser_listener.py
```

Die Tests verwenden synthetisches Audio und benötigen keine API-Schlüssel.
Der Browser-Test prüft automatische Aktivierung, Wiedergabe, Unterbrechung,
Pause, Rückkehr nach Stille und verweigerte Mikrofonrechte.
