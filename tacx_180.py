#!/usr/bin/env python3
"""Tacx Flow Smart per Bluetooth LE auf eine feste Leistung (ERG) setzen.

Beispiel:
    tacx-180            # sucht den Tacx Flow und setzt 180 W
    tacx-180 200        # 200 W
    tacx-180 --list     # nur BLE-Geräte in der Nähe anzeigen

Das Skript muss während der Fahrt laufen. Ctrl+C beendet die Steuerung.
"""
from __future__ import annotations

import argparse
import asyncio
import struct
import sys
import time
from dataclasses import dataclass

from bleak import BleakClient, BleakScanner
from bleak.backends.device import BLEDevice
from bleak.backends.scanner import AdvertisementData

FTMS_CONTROL_POINT = "00002ad9-0000-1000-8000-00805f9b34fb"
CYCLING_POWER_MEASUREMENT = "00002a63-0000-1000-8000-00805f9b34fb"

OP_REQUEST_CONTROL = 0x00
OP_SET_TARGET_POWER = 0x05
OP_START_RESUME = 0x07
OP_STOP_PAUSE = 0x08
RESPONSE_CODE = 0x80
RESULT_SUCCESS = 0x01


@dataclass
class Trainer:
    device: BLEDevice
    rssi: int


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Setzt einen Tacx Flow Smart per Bluetooth auf eine feste Wattzahl (ERG).",
    )
    p.add_argument("watts", nargs="?", type=int, default=180,
                   help="Zielleistung in Watt (Standard: 180)")
    p.add_argument("--name", default="tacx",
                   help="Namensfilter für die Gerätesuche (Standard: tacx)")
    p.add_argument("--address", help="BLE-Adresse direkt angeben, z. B. AA:BB:CC:DD:EE:FF")
    p.add_argument("--timeout", type=float, default=10.0,
                   help="Suchdauer in Sekunden (Standard: 10)")
    p.add_argument("--duration", type=float, default=0,
                   help="Nach N Sekunden automatisch beenden (0 = bis Ctrl+C)")
    p.add_argument("--list", action="store_true",
                   help="Nur BLE-Geräte in der Nähe auflisten und beenden")
    args = p.parse_args()
    if not 0 <= args.watts <= 2000:
        p.error("Watt muss zwischen 0 und 2000 liegen")
    return args


async def scan(timeout: float) -> list[Trainer]:
    print(f"Suche BLE-Geräte ({timeout:.0f} s) ...")
    found = await BleakScanner.discover(timeout=timeout, return_adv=True)
    trainers: list[Trainer] = []
    for device, adv in found.values():
        rssi = adv.rssi if adv and adv.rssi is not None else -999
        trainers.append(Trainer(device, rssi))
    trainers.sort(key=lambda t: t.rssi, reverse=True)
    return trainers


def describe(t: Trainer) -> str:
    name = t.device.name or "(ohne Namen)"
    return f"{name}  [{t.device.address}]  RSSI {t.rssi} dBm"


def choose(trainers: list[Trainer], name_filter: str) -> Trainer | None:
    matches = [t for t in trainers if name_filter.lower() in (t.device.name or "").lower()]
    if not matches:
        return None
    if len(matches) > 1:
        print("Mehrere passende Trainer gefunden:")
        for t in matches:
            print("  " + describe(t))
        print("Verbinde mit dem stärksten Signal. Sonst --address verwenden.")
    return matches[0]


def parse_cycling_power(data: bytes) -> int | None:
    """Momentane Leistung (W) aus Cycling Power Measurement (0x2A63).

    Erkenntnis aus tacx_watt.py: Flags auswerten und das Int16-Feld
    „Instantaneous Power“ (Bit 6) lesen.
    """
    if len(data) < 4:
        return None
    flags = int.from_bytes(data[0:2], "little")
    if not flags & 0x0040:  # Bit 6: Instantaneous Power Present
        return None
    return struct.unpack_from("<h", data, 2)[0]


async def run(args: argparse.Namespace) -> int:
    if args.address:
        print(f"Verbinde direkt mit {args.address} ...")
        device_or_address: str | BLEDevice = args.address
    else:
        trainers = await scan(args.timeout)
        if args.list:
            if not trainers:
                print("Keine BLE-Geräte gefunden.")
            for t in trainers:
                if t.device.name:
                    print("  " + describe(t))
            return 0
        chosen = choose(trainers, args.name)
        if chosen is None:
            print(f"Kein Gerät mit „{args.name}“ im Namen gefunden.")
            named = [t for t in trainers if t.device.name]
            if named:
                print("Gefundene Geräte:")
                for t in named:
                    print("  " + describe(t))
            print("Tipp: Trainer einschalten und keine andere App verbinden lassen.")
            return 1
        print("Gefunden: " + describe(chosen))
        device_or_address = chosen.device

    responses: asyncio.Queue[bytes] = asyncio.Queue()
    connected = False

    def on_control_point(_: object, data: bytearray) -> None:
        raw = bytes(data)
        print(f"  Trainer-Antwort: {raw.hex(' ')}")
        responses.put_nowait(raw)

    async def send(client: BleakClient, opcode: int, payload: bytes = b"",
                   timeout: float = 5.0) -> bool | None:
        command = bytes([opcode]) + payload
        print(f"  → Befehl {command.hex(' ')}")
        await client.write_gatt_char(FTMS_CONTROL_POINT, command, response=True)
        try:
            while True:
                resp = await asyncio.wait_for(responses.get(), timeout)
                # 0x80 = Response, [1] = Opcode, [2] = Result
                if len(resp) >= 3 and resp[0] == RESPONSE_CODE and resp[1] == opcode:
                    return resp[2] == RESULT_SUCCESS
        except asyncio.TimeoutError:
            print("  (keine Antwort – Befehl wird trotzdem gesendet)")
            return None


    try:
        async with BleakClient(device_or_address, timeout=25) as client:
            connected = True
            print(f"Verbunden mit {client.address}")
            if client.services.get_characteristic(FTMS_CONTROL_POINT) is None:
                print("FEHLER: Der Trainer bietet keinen FTMS-Control-Point (2AD9).")
                print("       Möglich ist ein älteres Tacx-Flow-Modell ohne elektronische Steuerung.")
                return 1

            await client.start_notify(FTMS_CONTROL_POINT, on_control_point)
            await asyncio.sleep(0.3)

            await send(client, OP_REQUEST_CONTROL)
            # Reihenfolge: Ziel setzen, starten, Ziel erneut setzen.
            # Manche Trainer ignorieren das Ziel, bevor die Session läuft.
            await send(client, OP_SET_TARGET_POWER,
                       args.watts.to_bytes(2, "little", signed=True))
            await send(client, OP_START_RESUME)
            await asyncio.sleep(0.5)
            await send(client, OP_SET_TARGET_POWER,
                       args.watts.to_bytes(2, "little", signed=True))

            # Ist-Leistung mitlesen (ähnlich wie tacx_watt.py), damit man sieht,
            # ob der Trainer das Ziel hält.
            actual_power: int | None = None
            power_available = (
                client.services.get_characteristic(CYCLING_POWER_MEASUREMENT)
                is not None
            )

            def on_power(_: object, data: bytearray) -> None:
                nonlocal actual_power
                value = parse_cycling_power(bytes(data))
                if value is not None:
                    actual_power = value

            if power_available:
                await client.start_notify(CYCLING_POWER_MEASUREMENT, on_power)
                await asyncio.sleep(0.2)

            print()
            print(f"✔ {args.watts} W als Ziel gesetzt.")
            print("  Gleichmäßig treten, nicht schalten. Der Trainer regelt den Widerstand.")
            if power_available:
                print("  Ist-Leistung wird mitgelesen.")
            print("  Ctrl+C beendet die Steuerung.")
            print()

            start = time.monotonic()
            try:
                if args.duration > 0:
                    while time.monotonic() - start < args.duration:
                        if actual_power is not None:
                            print(f"\r  Ist-Leistung: {actual_power:4d} W   ",
                                  end="", flush=True)
                        await asyncio.sleep(0.5)
                else:
                    while True:
                        if actual_power is not None:
                            print(f"\r  Ist-Leistung: {actual_power:4d} W   ",
                                  end="", flush=True)
                        await asyncio.sleep(0.5)
            finally:
                print()
            return 0
    except asyncio.CancelledError:
        return 0
    except Exception as exc:  # noqa: BLE001 - verständliche Meldung für CLI
        print(f"FEHLER: {exc}", file=sys.stderr)
        print("Prüfe: Trainer an? Bluetooth aktiv? Andere App/Verbindung getrennt?", file=sys.stderr)
        return 1
    finally:
        # Beim Trennen beendet der Trainer normalerweise den ERG-Modus.
        if connected:
            print("Verbindung getrennt – ERG-Steuerung beendet.")


def main() -> int:
    args = parse_args()
    try:
        return asyncio.run(run(args))
    except KeyboardInterrupt:
        print("\nAbgebrochen.")
        return 0


if __name__ == "__main__":
    sys.exit(main())
