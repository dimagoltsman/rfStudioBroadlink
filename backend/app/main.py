"""HTTP API + static UI for the Broadlink RF workbench."""
from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import Literal

from broadlink import exceptions as be
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import rf
from .hub import DeviceError, Hub, rf_device_types
from .protocols import PROTOCOLS, get_protocol
from .store import Store

DATA_DIR = Path(os.environ.get("RFW_DATA_DIR", Path(__file__).resolve().parents[2] / "data"))
STATIC_DIR = Path(os.environ.get("RFW_STATIC_DIR", Path(__file__).resolve().parents[2] / "frontend"))

app = FastAPI(title="RF Workbench", version="0.1.0")
store = Store(DATA_DIR / "store.json")
hub = Hub(store)
press_lock = threading.Lock()  # serialises counter advances of rolling-code remotes


# ---- error mapping -------------------------------------------------------
@app.exception_handler(ValueError)
async def _value_error(_: Request, exc: ValueError) -> JSONResponse:
    return JSONResponse({"detail": str(exc)}, status_code=400)


@app.exception_handler(KeyError)
async def _key_error(_: Request, exc: KeyError) -> JSONResponse:
    return JSONResponse({"detail": exc.args[0] if exc.args else "Not found"}, status_code=404)


@app.exception_handler(DeviceError)
@app.exception_handler(be.BroadlinkException)
async def _device_error(_: Request, exc: Exception) -> JSONResponse:
    return JSONResponse({"detail": str(exc)}, status_code=502)


# ---- schemas -------------------------------------------------------------
class DiscoverRequest(BaseModel):
    timeout: int = Field(5, ge=1, le=30)
    local_ip: str | None = None


class AddDeviceRequest(BaseModel):
    host: str


class ManualDeviceRequest(BaseModel):
    host: str
    mac: str
    devtype: int


class RenameRequest(BaseModel):
    name: str


class LearnStartRequest(BaseModel):
    device_id: str
    frequency: float | None = Field(None, description="MHz, e.g. 433.92; used when sweep is false")
    sweep: bool = True


class AnalyzeRequest(BaseModel):
    packet_hex: str


class CodeCreate(BaseModel):
    name: str
    packet_hex: str
    device_id: str | None = None
    frequency: float | None = None
    notes: str = ""


class SendRequest(BaseModel):
    device_id: str
    repeat: int | None = Field(None, ge=0, le=50, description="Overrides the packet repeat byte")


class RawSendRequest(BaseModel):
    device_id: str
    pulses: list[int]
    repeat: int = Field(0, ge=0, le=50)


class CompareRequest(BaseModel):
    code_ids: list[str]


class PreviewRequest(BaseModel):
    params: dict
    command: str


class RemoteUpsert(BaseModel):
    name: str
    device_id: str
    mode: Literal["learned", "virtual"]
    codes: dict[str, str] = {}
    protocol: str | None = None
    params: dict = {}
    repeat: int | None = Field(None, ge=1, le=50)


class CommandRequest(BaseModel):
    repeat: int | None = Field(None, ge=1, le=50)


# ---- devices -------------------------------------------------------------
@app.get("/api/health")
def health() -> dict:
    return {"ok": True}


@app.get("/api/devices")
def list_devices() -> list[dict]:
    return store.list("devices")


@app.post("/api/devices/discover")
def discover_devices(body: DiscoverRequest) -> list[dict]:
    return hub.discover(body.timeout, body.local_ip)


@app.post("/api/devices")
def add_device(body: AddDeviceRequest) -> dict:
    return hub.add_by_host(body.host.strip())


@app.post("/api/devices/manual")
def add_device_manual(body: ManualDeviceRequest) -> dict:
    return hub.add_manual(body.host.strip(), body.mac.strip(), body.devtype)


@app.get("/api/device-types")
def device_types() -> list[dict]:
    return rf_device_types()


@app.patch("/api/devices/{device_id}")
def rename_device(device_id: str, body: RenameRequest) -> dict:
    info = store.get("devices", device_id)
    if info is None:
        raise KeyError(f"Unknown device '{device_id}'")
    info["name"] = body.name.strip() or info["name"]
    return store.upsert("devices", info)


@app.delete("/api/devices/{device_id}")
def delete_device(device_id: str) -> dict:
    hub.forget(device_id)
    return {"ok": True}


# ---- learning ------------------------------------------------------------
@app.post("/api/learn/start")
def learn_start(body: LearnStartRequest) -> dict:
    return hub.start_learn(body.device_id, body.frequency, body.sweep)


@app.get("/api/learn/status")
def learn_status() -> dict:
    state = hub.learn_state
    if state["packet_hex"]:
        state["analysis"] = rf.analyze_packet(bytes.fromhex(state["packet_hex"])).to_dict()
    return state


@app.post("/api/learn/cancel")
def learn_cancel() -> dict:
    return hub.cancel_learn()


@app.post("/api/analyze")
def analyze(body: AnalyzeRequest) -> dict:
    return rf.analyze_packet(bytes.fromhex(body.packet_hex)).to_dict()


# ---- codes ---------------------------------------------------------------
def _packet_with_repeat(packet: bytes, repeat: int | None) -> bytes:
    if repeat is None:
        return packet
    return bytes([packet[0], repeat]) + packet[2:]


def _code(code_id: str) -> dict:
    code = store.get("codes", code_id)
    if code is None:
        raise KeyError(f"Unknown code '{code_id}'")
    return code


@app.get("/api/codes")
def list_codes() -> list[dict]:
    return store.list("codes")


@app.post("/api/codes")
def create_code(body: CodeCreate) -> dict:
    packet = bytes.fromhex(body.packet_hex)
    analysis = rf.analyze_packet(packet)
    return store.upsert("codes", {
        "name": body.name.strip(),
        "packet_hex": packet.hex(),
        "device_id": body.device_id,
        "frequency": body.frequency,
        "notes": body.notes,
        "bits": analysis.consensus,
        "guess": analysis.guess,
    })


@app.delete("/api/codes/{code_id}")
def delete_code(code_id: str) -> dict:
    if not store.delete("codes", code_id):
        raise KeyError(f"Unknown code '{code_id}'")
    return {"ok": True}


@app.get("/api/codes/{code_id}/analysis")
def code_analysis(code_id: str) -> dict:
    return rf.analyze_packet(bytes.fromhex(_code(code_id)["packet_hex"])).to_dict()


@app.post("/api/codes/{code_id}/send")
def send_code(code_id: str, body: SendRequest) -> dict:
    packet = _packet_with_repeat(bytes.fromhex(_code(code_id)["packet_hex"]), body.repeat)
    hub.send_packet(body.device_id, packet)
    return {"ok": True, "bytes": len(packet)}


@app.post("/api/codes/compare")
def compare_codes(body: CompareRequest) -> dict:
    rows = []
    for code_id in body.code_ids:
        code = _code(code_id)
        analysis = rf.analyze_packet(bytes.fromhex(code["packet_hex"]))
        rows.append((code["name"], analysis.consensus or ""))
    return rf.compare_frames(rows)


@app.post("/api/send/raw")
def send_raw(body: RawSendRequest) -> dict:
    packet = rf.pulses_to_packet(body.pulses, rf.KIND_RF433, body.repeat)
    hub.send_packet(body.device_id, packet)
    return {"ok": True, "bytes": len(packet)}


# ---- protocols -----------------------------------------------------------
@app.get("/api/protocols")
def list_protocols() -> list[dict]:
    return [p.describe() for p in PROTOCOLS.values()]


@app.post("/api/protocols/{name}/random")
def random_params(name: str) -> dict:
    return get_protocol(name).random_params()


@app.post("/api/protocols/{name}/preview")
def preview_protocol(name: str, body: PreviewRequest) -> dict:
    proto = get_protocol(name)
    bits = proto.frame_bits(proto.validate(body.params), body.command)
    pulses = proto.frame_pulses(bits)
    kind = rf.kind_for_frequency(proto.frequency_mhz)
    return {"bits": bits, "hex": rf.bits_to_hex(bits, proto.symbol_base), "pulses": pulses,
            "packet_hex": rf.pulses_to_packet(pulses, kind, proto.default_repeat - 1).hex()}


# ---- remotes ------------------------------------------------------------
def _validate_remote(body: RemoteUpsert) -> dict:
    if store.get("devices", body.device_id) is None:
        raise ValueError("Pick a radio")
    data = body.model_dump()
    data["name"] = body.name.strip() or "Remote"
    if body.mode == "virtual":
        if not body.protocol:
            raise ValueError("Pick a protocol for a virtual remote")
        proto = get_protocol(body.protocol)
        data["params"] = proto.validate(body.params)
        data["repeat"] = body.repeat or proto.default_repeat
        data["codes"] = {}
    else:
        for action, code_id in body.codes.items():
            if code_id and store.get("codes", code_id) is None:
                raise ValueError(f"Code for '{action}' no longer exists")
        data["codes"] = {k: v for k, v in body.codes.items() if v}
        data["protocol"], data["params"] = None, {}
    return data


@app.get("/api/remotes")
def list_remotes() -> list[dict]:
    return store.list("remotes")


@app.post("/api/remotes")
def create_remote(body: RemoteUpsert) -> dict:
    return store.upsert("remotes", _validate_remote(body))


@app.put("/api/remotes/{remote_id}")
def update_remote(remote_id: str, body: RemoteUpsert) -> dict:
    if store.get("remotes", remote_id) is None:
        raise KeyError(f"Unknown remote '{remote_id}'")
    data = _validate_remote(body)
    data["id"] = remote_id
    return store.upsert("remotes", data)


@app.delete("/api/remotes/{remote_id}")
def delete_remote(remote_id: str) -> dict:
    if not store.delete("remotes", remote_id):
        raise KeyError(f"Unknown remote '{remote_id}'")
    return {"ok": True}


def _virtual_press(remote: dict, command: str, repeat: int | None) -> tuple[bytes, dict]:
    """Encode one press of a virtual remote and return (packet, params after the press).

    Rolling-code protocols advance their counter; it is persisted before the packet leaves so
    two quick presses never reuse a counter, which the receiver would silently drop."""
    proto = get_protocol(remote["protocol"])
    with press_lock:
        current = store.get("remotes", remote["id"])["params"]
        pulses = proto.encode(current, command)
        advanced = proto.advance(current)
        if advanced != current:
            store.upsert("remotes", {**remote, "params": advanced})
    kind = rf.kind_for_frequency(proto.frequency_mhz)
    return rf.pulses_to_packet(pulses, kind, (repeat or remote["repeat"]) - 1), advanced


@app.post("/api/remotes/{remote_id}/commands/{command}")
def remote_command(remote_id: str, command: str, body: CommandRequest | None = None) -> dict:
    remote = store.get("remotes", remote_id)
    if remote is None:
        raise KeyError(f"Unknown remote '{remote_id}'")
    repeat = body.repeat if body and body.repeat else None
    params = None
    if remote["mode"] == "virtual":
        packet, params = _virtual_press(remote, command, repeat)
    else:
        code_id = remote["codes"].get(command)
        if not code_id:
            raise ValueError(f"No learned code bound to '{command}'")
        packet = _packet_with_repeat(bytes.fromhex(_code(code_id)["packet_hex"]), None if repeat is None else repeat - 1)
    hub.send_packet(remote["device_id"], packet)
    return {"ok": True, "command": command, "bytes": len(packet), "params": params}


if STATIC_DIR.is_dir():
    app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="ui")
