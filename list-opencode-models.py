#!/usr/bin/env python3
"""
Zeigt alle in der OpenCode-Konfiguration verfügbaren Modelle an.

Verwendet ~/.config/opencode/opencode.json und ruft bei Bedarf den Gateway-Endpunkt ab.
"""

import json
import os
import sys
import urllib.request


CFG = os.path.expanduser("~/.config/opencode/opencode.json")
PROVIDER = "gw-cherryin"


def load_config() -> dict:
    try:
        with open(CFG, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        print(f"Fehler: {CFG} nicht gefunden.", file=sys.stderr)
        sys.exit(1)
    except json.JSONDecodeError:
        print(f"Fehler: {CFG} ist kein gültiges JSON.", file=sys.stderr)
        sys.exit(1)


def fetch_live_models(base_url: str, api_key: str) -> list[str]:
    """
    Ruft die Liste der tatsächlich vom CherryAI-Gateway bereitgestellten Modelle ab.
    """
    url = f"{base_url.rstrip('/')}/models"
    headers = {"Authorization": f"Bearer {api_key}"}
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.load(resp)
            # Die Antwort enthält data->data->id
            models = data.get("data", [])
            return [m["id"] for m in models]
    except Exception as e:
        print(f"Warnung: Live-Modelle abrufen fehlgeschlagen: {e}", file=sys.stderr)
        return []


def main():
    cfg = load_config()

    provider = cfg["provider"].get(PROVIDER)
    if not provider:
        print(f"Fehler: Provider {PROVIDER} nicht gefunden.", file=sys.stderr)
        sys.exit(1)

    # Konfigurierte Modelle (aus opencode.json)
    configured = provider.get("models", {})
    print(f"=== Konfigurierte Modelle ({len(configured)} von {PROVIDER}) ===")
    for model_id, details in configured.items():
        name = details.get("name", model_id)
        limit = details.get("limit", {})
        ctx = limit.get("context", "?")
        out = limit.get("output", "?")
        print(f"  {model_id}")
        print(f"    Name: {name}")
        print(f"    Limit: Kontext {ctx} Token, Ausgabe {out} Token")
        if "inactive" in details or details.get("disabled"):
            print("    [INACTIVE/DISABLED]")
        print()

    # Live-Modelle (vom Gateway)
    options = provider.get("options", {})
    base = options.get("baseURL", "").strip()
    api_key = options.get("apiKey", "").strip()
    if base and api_key:
        live = fetch_live_models(base, api_key)
        if live:
            print(f"=== Live Modelle (vom Gateway {base}) ===")
            print(f"  IDs: {', '.join(live)}")
            not_configured = [m for m in live if m not in configured]
            if not_configured:
                print(f"  Nicht in {CFG}:")
                for m in not_configured:
                    print(f"    - {m}")
    else:
        print("Keine Gateway-URL/API-Key, live-Abfrage übersprungen.")


if __name__ == "__main__":
    main()