"""Broadlink device registry plus a single RF learning session."""
from __future__ import annotations

import socket
import threading
import time
from dataclasses import asdict, dataclass

import broadlink
from broadlink import exceptions as be
from broadlink.remote import rmpro

from .store import Store

SWEEP_TIMEOUT_S = 30
CAPTURE_TIMEOUT_S = 30
POLL_INTERVAL_S = 0.5
DEVICE_PORT = 80


class DeviceError(Exception):
    """Talking to a Broadlink device failed."""


@dataclass
class LearnState:
    phase: str = "idle"  # idle | sweeping | waiting | done | error | cancelled
    device_id: str | None = None
    frequency: float | None = None
    packet_hex: str | None = None
    message: str = "No learning session"
    started_at: float = 0.0
    updated_at: float = 0.0

    def to_dict(self) -> dict:
        return asdict(self)


def _device_info(dev: broadlink.Device, name: str | None = None) -> dict:
    return {
        "id": dev.mac.hex(),
        "name": name or f"{dev.model} ({dev.host[0]})",
        "host": dev.host[0],
        "devtype": dev.devtype,
        "mac": dev.mac.hex(":"),
        "model": dev.model,
        "manufacturer": dev.manufacturer,
        "type": dev.type,
        "rf": isinstance(dev, rmpro),
    }


class Hub:
    def __init__(self, store: Store):
        self.store = store
        self._devices: dict[str, broadlink.Device] = {}
        self._authed: set[str] = set()
        self._lock = threading.RLock()
        self._learn = LearnState()
        self._learn_thread: threading.Thread | None = None
        self._cancel = threading.Event()

    # ---- devices -------------------------------------------------------
    def discover(self, timeout: int = 5, local_ip: str | None = None) -> list[dict]:
        candidates = [local_ip] if local_ip else [None, default_route_ip()]
        last_error: OSError | None = None
        for bind_ip in candidates:
            try:
                found = broadlink.discover(timeout=timeout, local_ip_address=bind_ip)
                break
            except OSError as exc:  # e.g. macOS "Can't assign requested address" on multi-homed hosts
                last_error = exc
        else:
            raise DeviceError(f"Broadcast discovery failed ({last_error}). Add the device by IP instead.") from last_error
        return [self._register(dev) for dev in found]

    def add_by_host(self, host: str, timeout: int = 5) -> dict:
        try:
            dev = broadlink.hello(host, timeout=timeout)
        except (be.BroadlinkException, OSError) as exc:
            raise DeviceError(f"No Broadlink device answered at {host} ({exc})") from exc
        return self._register(dev)

    def add_manual(self, host: str, mac: str, devtype: int, timeout: int = 5) -> dict:
        """Register a device without the hello handshake (which cannot cross NAT), verifying it by auth."""
        try:
            mac_bytes = bytes.fromhex(mac.replace(":", "").replace("-", ""))
        except ValueError as exc:
            raise ValueError("MAC must look like a0:43:b0:12:34:56") from exc
        if len(mac_bytes) != 6:
            raise ValueError("MAC must be 6 bytes")
        if devtype not in {t["devtype"] for t in rf_device_types()}:
            raise ValueError(f"Device type {devtype:#x} is not a known RF-capable Broadlink model")
        dev = broadlink.gendevice(devtype, (host, DEVICE_PORT), mac_bytes)
        dev.timeout = timeout
        try:
            dev.auth()
        except (be.BroadlinkException, OSError) as exc:
            raise DeviceError(f"{dev.model} at {host} did not accept authentication ({exc}). "
                              "Check the IP/MAC and that the device is not locked in the Broadlink app.") from exc
        info = self._register(dev)
        self._authed.add(info["id"])
        return info

    def _register(self, dev: broadlink.Device) -> dict:
        existing = self.store.get("devices", dev.mac.hex())
        info = _device_info(dev, existing["name"] if existing else None)
        with self._lock:
            self._devices[info["id"]] = dev
            self._authed.discard(info["id"])
        return self.store.upsert("devices", info)

    def forget(self, device_id: str) -> None:
        with self._lock:
            self._devices.pop(device_id, None)
            self._authed.discard(device_id)
        self.store.delete("devices", device_id)

    def _connect(self, device_id: str) -> broadlink.Device:
        with self._lock:
            dev = self._devices.get(device_id)
            if dev is None:
                info = self.store.get("devices", device_id)
                if info is None:
                    raise KeyError(f"Unknown device '{device_id}'")
                mac = bytes.fromhex(info["mac"].replace(":", ""))
                dev = broadlink.gendevice(info["devtype"], (info["host"], DEVICE_PORT), mac)
                self._devices[device_id] = dev
            if device_id not in self._authed:
                try:
                    dev.auth()
                except (be.BroadlinkException, OSError) as exc:
                    raise DeviceError(f"Could not authenticate with {info_host(dev)}: {exc}") from exc
                self._authed.add(device_id)
            return dev

    def _rf_device(self, device_id: str) -> rmpro:
        dev = self._connect(device_id)
        if not isinstance(dev, rmpro):
            raise DeviceError(f"{dev.model} has no RF radio; use an RM pro / RM4 pro")
        return dev

    def send_packet(self, device_id: str, packet: bytes) -> None:
        dev = self._connect(device_id)
        with self._lock:
            try:
                dev.send_data(packet)
            except be.AuthorizationError:
                self._authed.discard(device_id)
                self._connect(device_id).send_data(packet)
            except (be.BroadlinkException, OSError) as exc:
                raise DeviceError(f"Send failed: {exc}") from exc

    # ---- learning ------------------------------------------------------
    @property
    def learn_state(self) -> dict:
        return self._learn.to_dict()

    def start_learn(self, device_id: str, frequency: float | None, sweep: bool) -> dict:
        with self._lock:
            if self._learn_thread and self._learn_thread.is_alive():
                raise DeviceError("A learning session is already running")
            dev = self._rf_device(device_id)
            self._cancel.clear()
            now = time.time()
            self._learn = LearnState("starting", device_id, frequency, None, "Starting…", now, now)
            self._learn_thread = threading.Thread(
                target=self._learn_worker, args=(dev, frequency, sweep), daemon=True)
            self._learn_thread.start()
        return self.learn_state

    def cancel_learn(self) -> dict:
        self._cancel.set()
        return self.learn_state

    def _set(self, phase: str, message: str, **extra) -> None:
        self._learn.phase = phase
        self._learn.message = message
        self._learn.updated_at = time.time()
        for key, value in extra.items():
            setattr(self._learn, key, value)

    def _wait(self, seconds: float, probe) -> bool:
        """Poll `probe` until it returns truthy, cancellation, or timeout."""
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline and not self._cancel.is_set():
            time.sleep(POLL_INTERVAL_S)
            if probe():
                return True
        return False

    def _learn_worker(self, dev: rmpro, frequency: float | None, sweep: bool) -> None:
        try:
            if sweep:
                dev.sweep_frequency()
                self._set("sweeping", "Press and HOLD the remote button until the frequency is found…")
                found: list[float] = []

                def check_freq() -> bool:
                    ok, freq = dev.check_frequency()
                    if ok:
                        found.append(freq)
                    return ok

                if not self._wait(SWEEP_TIMEOUT_S, check_freq):
                    dev.cancel_sweep_frequency()
                    if self._cancel.is_set():
                        self._set("cancelled", "Learning cancelled")
                    else:
                        self._set("error", "No RF frequency detected. Hold the button the whole time, within ~1 m of the device.")
                    return
                frequency = found[0]
                self._learn.frequency = frequency

            dev.find_rf_packet(frequency or None)
            self._set("waiting", "Release the button, then press it again briefly…", frequency=frequency)
            captured: list[bytes] = []

            def check_data() -> bool:
                try:
                    captured.append(dev.check_data())
                    return True
                except (be.ReadError, be.StorageError):
                    return False

            if not self._wait(CAPTURE_TIMEOUT_S, check_data):
                dev.cancel_sweep_frequency()
                if self._cancel.is_set():
                    self._set("cancelled", "Learning cancelled")
                else:
                    self._set("error", "No code captured. Try a slightly longer press, or a different distance.")
                return
            self._set("done", "Code captured", packet_hex=captured[0].hex())
        except (be.BroadlinkException, OSError) as exc:
            self._set("error", f"Device error: {exc}")


def rf_device_types() -> list[dict]:
    """Broadlink models with an RF radio, for the manual-add form."""
    return sorted(
        ({"devtype": devtype, "model": model, "manufacturer": manufacturer}
         for cls, models in broadlink.SUPPORTED_TYPES.items() if issubclass(cls, rmpro)
         for devtype, (model, manufacturer) in models.items()),
        key=lambda t: (t["model"], t["devtype"]))


def default_route_ip() -> str | None:
    """IP of the interface that carries the default route (no packets are sent)."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("192.0.2.1", 9))  # TEST-NET-1, never routed anywhere
            return sock.getsockname()[0]
    except OSError:
        return None


def info_host(dev: broadlink.Device) -> str:
    return f"{dev.model} at {dev.host[0]}"
