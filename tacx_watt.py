#!/usr/bin/env python3
"""Aktuelle Leistung und Geschwindigkeit eines Hometrainers live anzeigen.

Beispiel:
    tacx-watt                 # sucht einen Tacx und zeigt Watt + Tempo live an
    tacx-watt --list          # BLE-Geräte in der Nähe anzeigen
    tacx-watt --address AA:BB:CC:DD:EE:FF
    tacx-watt --start         # FTMS-Session starten, falls keine Daten kommen

Die Geschwindigkeit wird standardmäßig für ein 28-Zoll-Rad mit 700x28c
berechnet (Radumfang 2,136 m). Mit --wheel-circumference lässt sich der
Radumfang in Metern anpassen.

Das Skript liest nur mit und verändert den Widerstand nicht.
Ctrl+C beendet die Anzeige.
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

INDOOR_BIKE_DATA = "00002ad2-0000-1000-8000-00805f9b34fb"
CYCLING_POWER_MEASUREMENT = "00002a63-0000-1000-8000-00805f9b34fb"
FTMS_CONTROL_POINT = "00002ad9-0000-1000-8000-00805f9b34fb"

OP_REQUEST_CONTROL = 0x00
OP_START_RESUME = 0x07

# 28 Zoll / 700x28c: 622 mm Felge + 28 mm Reifen
DEFAULT_WHEEL_CIRCUMFERENCE_M = 2.136


@dataclass
class Trainer:
    device: BLEDevice
    rssi: int


@dataclass
class Reading:
    power: int | None = None
    cadence: float | None = None
    speed_kmh: float | None = None
    heart_rate: int | None = None
    resistance: int | None = None
    # Nur intern: Rohdaten für die Geschwindigkeitsberechnung
    wheel_revs: int | None = None
    wheel_event_time: int | None = None


class WheelSpeed:
    """Berechnet die Geschwindigkeit aus kumulierten Radumdrehungen."""

    def __init__(self, circumference_m: float) -> None:
        self.circumference_m = circumference_m
        self._last_revs: int | None = None
        self._last_time: int | None = None

    def update(self, revs: int, event_time: int) -> float | None:
        """Gibt die Geschwindigkeit in km/h zurück (None beim ersten Messwert)."""
        speed: float | None = None
        if self._last_revs is not None and self._last_time is not None:
            delta_revs = (revs - self._last_revs) & 0xFFFFFFFF
            # Event-Zeit ist uint16 in 1/1024 s und läuft alle 64 s über.
            delta_time = ((event_time - self._last_time) & 0xFFFF) / 1024.0
            if delta_revs > 1000:      # unplausibler Sprung, z. B. nach Reset
                delta_revs = 0
            if delta_time > 0:
                speed = delta_revs * self.circumference_m / delta_time * 3.6
            elif delta_revs == 0:      # Rad steht, Event-Zeit bleibt stehen
                speed = 0.0
        self._last_revs = revs
        self._last_time = event_time
        return speed


@dataclass
class Stats:
    samples: int = 0
    total: int = 0
    peak: int | None = None

    def add(self, power: int | None) -> None:
        if power is None:
            return
        self.samples += 1
        self.total += power
        self.peak = power if self.peak is None else max(self.peak, power)

    def summary(self) -> str:
        if self.samples == 0:
            return "Keine Leistungsdaten empfangen."
        average = self.total / self.samples
        return (f"Max {self.peak} W  |  Ø {average:.0f} W  |  "
                f"{self.samples} Messwerte")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Zeigt Leistung und Geschwindigkeit eines Hometrainers live an.",
    )
    p.add_argument("--name", default="tacx",
                   help="Namensfilter für die Gerätesuche (Standard: tacx)")
    p.add_argument("--address", help="BLE-Adresse direkt angeben, z. B. AA:BB:CC:DD:EE:FF")
    p.add_argument("--timeout", type=float, default=10.0,
                   help="Suchdauer in Sekunden (Standard: 10)")
    p.add_argument("--duration", type=float, default=0,
                   help="Nach N Sekunden automatisch beenden (0 = bis Ctrl+C)")
    p.add_argument("--wheel-circumference", type=float,
                   default=DEFAULT_WHEEL_CIRCUMFERENCE_M, metavar="METER",
                   help="Radumfang in Metern für die Geschwindigkeit "
                        "(Standard: 2.136 = 28 Zoll / 700x28c)")
    p.add_argument("--start", action="store_true",
                   help="FTMS-Session starten, falls der Trainer keine Daten sendet")
    p.add_argument("--raw", action="store_true",
                   help="Rohdaten der Benachrichtigungen anzeigen (stderr)")
    p.add_argument("--list", action="store_true",
                   help="Nur BLE-Geräte in der Nähe auflisten und beenden")
    args = p.parse_args()
    if not 0.5 <= args.wheel_circumference <= 3.0:
        p.error("Radumfang muss zwischen 0.5 und 3.0 Metern liegen")
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


def parse_indoor_bike_data(data: bytes) -> Reading | None:
    """FTMS Indoor Bike Data (0x2AD2) auswerten."""
    if len(data) < 2:
        return None
    flags = int.from_bytes(data[0:2], "little")
    offset = 2
    reading = Reading()

    def need(size: int) -> None:
        if offset + size > len(data):
            raise ValueError("FTMS-Paket zu kurz")

    def u8() -> int:
        nonlocal offset
        need(1)
        value = data[offset]
        offset += 1
        return value

    def u16() -> int:
        nonlocal offset
        need(2)
        value = struct.unpack_from("<H", data, offset)[0]
        offset += 2
        return value

    def s16() -> int:
        nonlocal offset
        need(2)
        value = struct.unpack_from("<h", data, offset)[0]
        offset += 2
        return value

    def u24() -> int:
        nonlocal offset
        need(3)
        value = int.from_bytes(data[offset:offset + 3], "little")
        offset += 3
        return value

    # Bit 0 = "More Data": 0 bedeutet, dass die momentane Geschwindigkeit folgt.
    if not flags & 0x0001:
        reading.speed_kmh = u16() / 100.0
    if flags & 0x0002:          # Durchschnittsgeschwindigkeit
        u16()
    if flags & 0x0004:          # momentane Trittfrequenz (0,5 rpm)
        reading.cadence = u16() / 2.0
    if flags & 0x0008:          # Durchschnitts-Trittfrequenz
        u16()
    if flags & 0x0010:          # Gesamtdistanz (uint24)
        u24()
    if flags & 0x0020:          # Widerstandsstufe
        reading.resistance = s16()
    if flags & 0x0040:          # momentane Leistung
        reading.power = s16()
    if flags & 0x0080:          # Durchschnittsleistung
        s16()
    if flags & 0x0100:          # Energie: total, pro Stunde, pro Minute
        u16()
        u16()
        u8()
    if flags & 0x0200:          # Herzfrequenz
        reading.heart_rate = u8()
    if flags & 0x0400:          # metabolisches Äquivalent
        u8()
    if flags & 0x0800:          # verstrichene Zeit
        u16()
    if flags & 0x1000:          # verbleibende Zeit
        u16()
    return reading


def parse_cycling_power(data: bytes) -> Reading | None:
    """Cycling Power Measurement (0x2A63) auswerten.

    Liest die momentane Leistung und – falls vorhanden – die kumulierten
    Radumdrehungen samt Event-Zeit für die Geschwindigkeitsberechnung.
    """
    if len(data) < 4:
        return None
    flags = int.from_bytes(data[0:2], "little")
    offset = 2
    reading = Reading(power=struct.unpack_from("<h", data, offset)[0])
    offset += 2

    def need(size: int) -> None:
        if offset + size > len(data):
            raise ValueError("Cycling-Power-Paket zu kurz")

    def u8() -> int:
        nonlocal offset
        need(1)
        value = data[offset]
        offset += 1
        return value

    def u16() -> int:
        nonlocal offset
        need(2)
        value = struct.unpack_from("<H", data, offset)[0]
        offset += 2
        return value

    def u32() -> int:
        nonlocal offset
        need(4)
        value = struct.unpack_from("<I", data, offset)[0]
        offset += 4
        return value

    if flags & 0x0001:          # Pedal Power Balance
        u8()
    if flags & 0x0004:          # Accumulated Torque
        u16()
    if flags & 0x0010:          # Wheel Revolution Data
        reading.wheel_revs = u32()
        reading.wheel_event_time = u16()
    # Weitere optionale Felder sind für Leistung und Geschwindigkeit irrelevant.
    return reading


def format_reading(reading: Reading) -> str:
    parts = [
        f"Leistung {reading.power:4d} W" if reading.power is not None else "Leistung  -- W"
    ]
    if reading.cadence is not None:
        parts.append(f"TF {reading.cadence:3.0f} rpm")
    if reading.speed_kmh is not None:
        parts.append(f"Tempo {reading.speed_kmh:4.1f} km/h")
    if reading.heart_rate is not None:
        parts.append(f"HF {reading.heart_rate:3d} bpm")
    if reading.resistance is not None:
        parts.append(f"Widerstand {reading.resistance}")
    return "   ".join(parts)


def write_live(text: str) -> None:
    if sys.stdout.isatty():
        sys.stdout.write("\r" + text + "\x1b[K")
    else:
        sys.stdout.write(text + "\n")
    sys.stdout.flush()


async def start_session(client: BleakClient) -> None:
    """Request Control + Start/Resume senden, ohne ein Leistungsziel zu setzen."""
    if client.services.get_characteristic(FTMS_CONTROL_POINT) is None:
        print("Hinweis: Kein FTMS-Control-Point vorhanden – --start wird ignoriert.")
        return
    await client.start_notify(FTMS_CONTROL_POINT, lambda *_: None)
    await client.write_gatt_char(FTMS_CONTROL_POINT, bytes([OP_REQUEST_CONTROL]), response=True)
    await asyncio.sleep(0.2)
    await client.write_gatt_char(FTMS_CONTROL_POINT, bytes([OP_START_RESUME]), response=True)
    await asyncio.sleep(0.2)


async def hint_after(event: asyncio.Event, timeout: float) -> None:
    try:
        await asyncio.wait_for(event.wait(), timeout=timeout)
    except asyncio.TimeoutError:
        if sys.stdout.isatty():
            sys.stdout.write("\r\x1b[K")
            sys.stdout.flush()
        print("Noch keine Daten. Bitte treten – manche Trainer senden erst "
              "nach dem Start einer Session (--start).")


async def run(args: argparse.Namespace) -> int:
    if args.address:
        target: str | BLEDevice = args.address
        label = args.address
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
        target = chosen.device
        label = chosen.device.name or chosen.device.address

    stats = Stats()
    started: float | None = None
    first_data = asyncio.Event()
    use_indoor = False
    hint_task: asyncio.Task[None] | None = None
    wheel_speed = WheelSpeed(args.wheel_circumference)

    def on_data(_: object, payload: bytearray) -> None:
        nonlocal started
        raw = bytes(payload)
        if args.raw:
            print(f"\nraw: {raw.hex(' ')}", file=sys.stderr, flush=True)
        try:
            reading = (parse_indoor_bike_data(raw) if use_indoor
                       else parse_cycling_power(raw))
        except (ValueError, struct.error) as exc:
            print(f"\nUngültiges Datenpaket ({exc}): {raw.hex(' ')}", file=sys.stderr)
            return
        if reading is None:
            return
        if reading.wheel_revs is not None and reading.wheel_event_time is not None:
            speed = wheel_speed.update(reading.wheel_revs, reading.wheel_event_time)
            if speed is not None:
                reading.speed_kmh = speed
        first_data.set()
        stats.add(reading.power)
        now = time.monotonic()
        if started is None:
            started = now
        elapsed = int(now - started)
        stamp = f"{elapsed // 60:02d}:{elapsed % 60:02d}"
        write_live(f"[{stamp}] {format_reading(reading)}")

    try:
        async with BleakClient(target, timeout=25) as client:
            print(f"Verbunden: {label}  [{client.address}]")

            if client.services.get_characteristic(INDOOR_BIKE_DATA) is not None:
                use_indoor = True
                data_uuid = INDOOR_BIKE_DATA
            elif client.services.get_characteristic(CYCLING_POWER_MEASUREMENT) is not None:
                data_uuid = CYCLING_POWER_MEASUREMENT
            else:
                print("FEHLER: Der Trainer sendet weder FTMS-Indoor-Bike-Data (2AD2) "
                      "noch Cycling Power (2A63).", file=sys.stderr)
                return 1

            if args.start:
                await start_session(client)

            if not use_indoor:
                print(f"Radumfang für Tempo: {args.wheel_circumference:.3f} m")

            await client.start_notify(data_uuid, on_data)
            print("Warte auf Daten ... (Ctrl+C beendet)")
            if sys.stdout.isatty():
                hint_task = asyncio.create_task(hint_after(first_data, 6.0))

            deadline = (time.monotonic() + args.duration) if args.duration > 0 else None
            while client.is_connected:
                if deadline is not None and time.monotonic() >= deadline:
                    break
                await asyncio.sleep(0.2)

            if not client.is_connected:
                print("\nVerbindung verloren.", file=sys.stderr)
                return 1
            return 0
    except asyncio.CancelledError:
        return 0
    except Exception as exc:  # noqa: BLE001 - verständliche Meldung für CLI
        print(f"FEHLER: {exc}", file=sys.stderr)
        print("Prüfe: Trainer an? Bluetooth aktiv? Andere App/Verbindung getrennt?",
              file=sys.stderr)
        return 1
    finally:
        if hint_task is not None:
            hint_task.cancel()
        if sys.stdout.isatty():
            sys.stdout.write("\n")
            sys.stdout.flush()
        print(stats.summary())


def main() -> int:
    args = parse_args()
    try:
        return asyncio.run(run(args))
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
