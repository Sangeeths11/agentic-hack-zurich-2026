# Family Shield · Echtzeit-Telefonassistent

Ein Anrufer wählt deine Agenten-Nummer. Der Agent nimmt automatisch entgegen,
begrüßt ihn als KI-Assistent und führt ein Gespräch auf Deutsch. Du kannst ihn
unterbrechen. Die bisherige reine Telefonweiterleitung wurde durch eine
bidirektionale Sprachverbindung ersetzt.

Die Architektur folgt [DigiGrad](https://github.com/gradium-ai/digigrad):
**Twilio Media Streams → Gradbot → Gradium STT → LLM → Gradium TTS → Telefon.**
Wir verwenden dessen Sprachmotor `gradbot==0.1.9` als Bibliothek. Telegram,
Datenbank und Voice-Cloning-Onboarding sind für diesen Ablauf nicht nötig.
Eine eigene Stimme lässt sich später per `GRADIUM_VOICE_ID` einsetzen.

## Welcher Anruf landet wo?

1. **Erster Test:** Du rufst mit dem Smartphone die Twilio-Nummer an.
   Der Agent auf dem Server nimmt ab und spricht mit dir.
2. **Deine bestehende Handynummer:** Richte bei deinem Mobilfunkanbieter eine
   Rufumleitung auf die Twilio-Nummer ein. Dann landet der Anruf beim Agenten.
   Verfügbarkeit und Art hängen vom Anbieter ab. Mit einem zweiten Telefon
   testen; keine Rückumleitung zur ursprünglichen Nummer einrichten.
3. **Der Agent ruft dein Handy an:** `uv run familyshield call-me` ruft die von
   dir konfigurierte `TEST_PHONE_NUMBER` an. Diesen Testanruf nimmst du selbst an.

Der Server steuert keine native Telefon-App auf dem Smartphone. Er beantwortet
den über Twilio zugestellten Anruf. Noch nicht enthalten sind Nachrichtenablage,
Kalenderzugriff, Betrugserkennung und Weitergabe an einen Menschen.

## 1. Lokal ohne API-Keys starten

Voraussetzung: [uv](https://docs.astral.sh/uv/getting-started/installation/).
Python 3.12 wird bei Bedarf von uv installiert. Unterstützte native Zielplattformen
für das festgelegte Gradbot-Paket: macOS Apple Silicon und Linux x86_64.

```bash
scripts/setup.sh
uv run familyshield simulate
```

Das Setup installiert die gesperrten Abhängigkeiten, legt eine private `.env`
an, sofern sie fehlt, installiert cloudflared mit festgelegter Version und
SHA-256-Prüfung lokal unter `.tools/` und prüft den Anrufablauf. Die Simulation sendet
einen signierten Webhook, öffnet den Media-Stream, tauscht Audio aus, prüft
`clear` bei Unterbrechung und schließt alle Sitzungen. Das Ergebnis steht in
`artifacts/simulation.wav`: **zwei Prüftöne, keine KI-Sprachdemo**.
Die Simulation ruft keine externen Dienste auf und wählt keine Nummern. Das
erstmalige Setup benötigt Internet zum Herunterladen der Abhängigkeiten.

## 2. Keys in `.env` eintragen

| Variable | Herkunft / Zweck |
| --- | --- |
| `TWILIO_ACCOUNT_SID` | [Twilio Console](https://console.twilio.com/) – Account SID |
| `TWILIO_AUTH_TOKEN` | Twilio – Auth Token für API und Signaturprüfung |
| `TWILIO_PHONE_NUMBER` | Deine Voice-fähige Twilio-Nummer, mit `+` und Ländercode |
| `GRADIUM_API_KEY` | [Gradium](https://gradium.ai/) – API-Key für STT und TTS |
| `LLM_API_KEY` | API-Key deines OpenAI-kompatiblen Modellanbieters |
| `LLM_BASE_URL` / `LLM_MODEL` | Endpoint und Modell; Vorlage: OpenAI / `gpt-4o-mini` |
| `PUBLIC_HTTP_URL` | HTTPS-Origin; das lokale Startskript setzt sie automatisch |
| `TEST_PHONE_NUMBER` | Optional: dein eigenes Handy für `call-me` |

`LLM_API_KEY` wird ausdrücklich an Gradbot übergeben; ein zusätzliches
`OPENAI_API_KEY` ist nicht nötig. `OPERATOR_NAME` legt fest, für wen der Agent
spricht. `AGENT_LANGUAGE=de` ist Standard. Leere `GRADIUM_VOICE_ID` verwendet
Gradbots Standardstimme für die gewählte Sprache.

**Vor dem Live-Test:** In der früheren versionierten `.env.example` standen
Zugangsdaten. Sie sind aus der aktuellen Vorlage entfernt. Den bisherigen
Twilio Auth Token im Anbieter-Konto ersetzen; das entfernt ihn nicht aus
alten Git-Commits. Die neue `.env` wird nicht versioniert.

## 2b. Kontakte eintragen

Eingehende Anrufe werden gegen ein lokales SQLite-Verzeichnis
(`CONTACTS_DB_PATH`, Standard `familyshield.db`) und eine Twilio-Lookup-Abfrage
geprüft (Leitungstyp, Anbieter). Ein unbekannter Anrufer erhöht den laufenden
Risiko-Score (siehe unten); bekannte Kontakte starten bei null.

```bash
uv run familyshield contacts add --name "Sarah" --phone "+41790000001" \
  --relationship daughter --role caregiver
uv run familyshield contacts list
uv run familyshield contacts import contacts.example.csv   # CSV: name,phone,relationship,role
```

Fehlerhafte oder doppelte Zeilen beim Import werden einzeln übersprungen und
mit Zeilennummer gemeldet; der Rest der Datei wird trotzdem importiert.

## 3. Echten Telefonanruf verbinden

Nach `scripts/setup.sh` und Eintragen der Keys:

```bash
scripts/run_local.sh
```

Das Skript startet einen temporären HTTPS-/WebSocket-Tunnel und den Server auf
`127.0.0.1:8082`. Es prüft die lokale Bereitschaft und aktualisiert dann die
Voice- und Status-Webhooks **der in `.env` angegebenen Twilio-Nummer**. Der
öffentliche URL wird in `.env` gespeichert, damit weitere CLI-Befehle ihn nutzen.
Der Rechner muss während des Gesprächs laufen und darf nicht schlafen.

Rufe die Twilio-Nummer an. Erwartung: Der Assistent begrüßt dich, erklärt seine
Rolle und antwortet auf deine Frage. Sprich in seine Antwort hinein: Er soll
die Ausgabe abbrechen und zuhören. `Ctrl-C` beendet App und Tunnel. Die Webhooks
bleiben auf den zuletzt eingerichteten URL gesetzt.

Provider vorab prüfen, ohne einen Telefonanruf auszulösen:

```bash
uv run familyshield doctor          # nur Konfiguration, keine API-Aufrufe
uv run familyshield doctor --live   # Twilio + echte Sprachpipeline; nutzt API-Guthaben
```

Optional in einem zweiten Terminal:

```bash
uv run familyshield call-me
```

Dieser Befehl erzeugt genau einen echten, kostenpflichtigen Anruf. Bei einem
Twilio-Testkonto gegebenenfalls zuerst die eigene Zielnummer verifizieren und
die erforderlichen Voice-/Länderberechtigungen aktivieren. Kein automatischer
Retry: Bei unklarem API-Ergebnis zuerst die Twilio-Anrufliste prüfen.

Mit einer bestehenden öffentlichen Domain: `PUBLIC_HTTP_URL` in `.env` setzen,
`uv run familyshield serve` starten und mit `uv run familyshield configure-twilio`
die Nummer verbinden. Der Proxy muss `/twilio/stream` als WebSocket durchreichen.

## Echtzeit und Messungen

- Dauerhafte WebSockets in beide Richtungen; kein Aufnehmen und anschließendes
  Hochladen ganzer Äußerungen. Gradbot streamt Erkennung, LLM-Antwort und TTS.
- Telefon-Audio: mono G.711 μ-law, 8 kHz. Der Adapter wandelt fortlaufend auf
  Gradbots 24-kHz-Eingang und dessen 48-kHz-Ausgang zurück auf 8 kHz um.
- Ausgehende Frames: maximal 160 Samples, also 20 ms Telefon-Audio. Eintreffende
  Audioportionen werden sofort weitergereicht.
- Unterbrechung: Gradbots Ereignis stoppt die Generierung; die Bridge sendet
  Twilios `clear`, um bereits gepuffertes Audio zu verwerfen.
- Logs enthalten `call_first_audio` und `call_response_audio` mit `latency_ms`.
  Antwortzeit wird ab erkanntem Turn-Ende bis zum Versand an Twilio gemessen.
  Erkennung des Turn-Endes, Telefonnetz und Gerätewiedergabe kommen hinzu.

Den lokalen Transport unter parallelen Streams messen:

```bash
uv run python scripts/benchmark.py --calls 4 --seconds 5
```

Das startet einen echten lokalen HTTP-/WebSocket-Server mit synthetischem
Audio-Echo und 50 Frames pro Sekunde und Gespräch. p50/p95 stehen danach in
`artifacts/benchmark.json`. **Diese Werte enthalten keine Cloud-KI-Latenz.**
Für den Live-Piloten: schnelle Modellvariante, kurze Antworten, naher Serverstandort
und mehrere reale Anrufe inklusive Unterbrechungen und Störgeräuschen. Eine feste
Antwortzeit ist ohne Messung der gewählten Provider nicht zugesichert.

## Betrieb und Skalierung

```bash
docker compose up --build -d
```

Compose verwendet `linux/amd64`, da Gradbot 0.1.9 kein Linux-ARM-Wheel mitliefert.
Auf Apple Silicon läuft der Container entsprechend emuliert; für die lokale
Sprachentwicklung ist das native Python-Setup vorzuziehen. Ein direkter Build
verwendet `docker build --platform linux/amd64 -t familyshield .`.
Der Container läuft ohne Root-Rechte und bindet lokal Port 8082. Davor muss ein
HTTPS-Proxy oder fester Tunnel mit WebSocket-Unterstützung stehen. `PUBLIC_HTTP_URL`
muss exakt zu dessen öffentlicher Origin passen. Ein temporärer
[Cloudflare Quick Tunnel](https://developers.cloudflare.com/tunnel/get-started/quick-tunnels/)
ist für Entwicklung; für dauerhaften Betrieb eine feste Domain verwenden.

Pro Anruf entstehen isolierte Sprachsitzungen. Webhook und Media-Stream können
auf verschiedenen Replikas landen: Ein kurz gültiger, signierter Stream-Parameter
überträgt die Anrufbindung. Es gibt keine lokale Pending-Call-Datenbank.

`MAX_CONCURRENT_CALLS=2` begrenzt **jeden Worker**; zusätzliche Anrufe erhalten busy
bzw. die Fallback-Ansage. Die Summe über alle Worker muss zu den Providerquoten
passen. Mehrere Replikas skalieren neue Verbindungen; ein laufendes Gespräch
bleibt auf seiner bestehenden Verbindung. Einen ausgefallenen Stream kann dieser
Prototyp nicht nahtlos übernehmen. Produktionskapazität mit echten Providern messen.

Alle Replikas nutzen dieselben Secrets und dieselbe öffentliche URL. Vor dem
Herunterfahren neue Verbindungen am Load Balancer stoppen und Gespräche auslaufen
lassen. Für globale Kontingente oder Historie später Redis bzw. Postgres ergänzen;
beides liegt nicht im aktuellen Audiopfad. [Architekturdetails](docs/architecture.md).

## Prüfung und Fehlersuche

```bash
uv run ruff check src tests scripts
uv run ruff format --check src tests scripts
uv run mypy src
uv run pytest -q
```

CI führt diese Checks, Offline-Simulation, Paket-Build und Docker-Health-Test aus.
Es benötigt keine Provider-Secrets.

| Symptom | Prüfung |
| --- | --- |
| `readyz` liefert 503 | `familyshield doctor`; fehlende Variablen ergänzen |
| Twilio HTTP 403 / Stream abgelehnt | Neuer Auth Token? URL in Twilio und `PUBLIC_HTTP_URL` identisch? Keine Pfad-Präfixe |
| Fallback statt Agent | `call_provider_error` / `call_timeout`, Guthaben, `doctor --live` |
| Kein Anruf am Handy | Zielnummer, Trial-Verifizierung, Länderberechtigungen, Twilio-Anrufliste |
| Langsame Antworten | `call_response_audio`, Modell und Server-/Providerregion prüfen |
| Kapazität erreicht | Workerlimit und Providerquoten gemeinsam anpassen |

`GET /healthz` ist Liveness. `GET /readyz` prüft Konfiguration und Prozessstatus,
keine Anbieter-Erreichbarkeit. Webhooks und WebSocket verlangen Twilio-Signaturen.
Es gibt keinen Authentifizierungs-Bypass und keinen öffentlichen Endpunkt für
ausgehende Anrufe. Roh-Audio und Transkripte werden von dieser App nicht dauerhaft
gespeichert; Provider verarbeiten Daten gemäß den dortigen Einstellungen.
