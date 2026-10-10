import time

import pytest
from broadlink import exceptions as be
from broadlink.remote import rmpro

from app import hub as hub_module
from app.hub import DeviceError, Hub
from app.store import Store


class FakeRM4(rmpro):
    """Simulates RM4 pro learning without any network: no __init__ of the real device."""

    def __init__(self, freq_after=1, data_after=1, data=b"\xb2\x00\x02\x00\x10\x20"):
        self.model, self.host, self.mac, self.devtype = "RM4 pro", ("10.0.0.5", 80), b"\xaa" * 6, 0x649B
        self.manufacturer, self.type = "Broadlink", "RM4PRO"
        self.freq_after, self.data_after, self.data = freq_after, data_after, data
        self.calls: list[str] = []
        self.sent: list[bytes] = []

    def auth(self):
        self.calls.append("auth")
        return True

    def sweep_frequency(self):
        self.calls.append("sweep")

    def check_frequency(self):
        self.freq_after -= 1
        return (self.freq_after <= 0, 433.92)

    def find_rf_packet(self, frequency=None):
        self.calls.append(f"find:{frequency}")

    def check_data(self):
        self.data_after -= 1
        if self.data_after > 0:
            raise be.ReadError(-5, "no data")
        return self.data

    def cancel_sweep_frequency(self):
        self.calls.append("cancel")

    def send_data(self, data):
        self.sent.append(data)


@pytest.fixture
def fast(monkeypatch):
    monkeypatch.setattr(hub_module, "POLL_INTERVAL_S", 0.01)
    monkeypatch.setattr(hub_module, "SWEEP_TIMEOUT_S", 0.2)
    monkeypatch.setattr(hub_module, "CAPTURE_TIMEOUT_S", 0.2)


def make_hub(tmp_path, dev):
    store = Store(tmp_path / "s.json")
    store.upsert("devices", {"id": "dev1", "name": "x", "host": "10.0.0.5", "devtype": 0x649B, "mac": "aa:aa:aa:aa:aa:aa", "rf": True})
    hub = Hub(store)
    hub._devices["dev1"] = dev
    return hub


def wait_done(hub, timeout=3):
    deadline = time.time() + timeout
    while time.time() < deadline and hub.learn_state["phase"] in ("starting", "sweeping", "waiting"):
        time.sleep(0.01)
    return hub.learn_state


def test_learn_sweep_then_capture(tmp_path, fast):
    dev = FakeRM4(freq_after=3, data_after=2)
    hub = make_hub(tmp_path, dev)
    hub.start_learn("dev1", None, sweep=True)
    state = wait_done(hub)
    assert state["phase"] == "done"
    assert state["frequency"] == 433.92
    assert state["packet_hex"] == dev.data.hex()
    assert dev.calls == ["auth", "sweep", "find:433.92"]


def test_learn_without_sweep_uses_given_frequency(tmp_path, fast):
    dev = FakeRM4()
    hub = make_hub(tmp_path, dev)
    hub.start_learn("dev1", 433.92, sweep=False)
    assert wait_done(hub)["phase"] == "done"
    assert "sweep" not in dev.calls and "find:433.92" in dev.calls


def test_learn_sweep_timeout_cancels_on_device(tmp_path, fast):
    dev = FakeRM4(freq_after=10_000)
    hub = make_hub(tmp_path, dev)
    hub.start_learn("dev1", None, sweep=True)
    state = wait_done(hub)
    assert state["phase"] == "error" and "frequency" in state["message"]
    assert dev.calls[-1] == "cancel"


def test_learn_cancel(tmp_path, fast):
    dev = FakeRM4(freq_after=10_000)
    hub = make_hub(tmp_path, dev)
    hub.start_learn("dev1", None, sweep=True)
    hub.cancel_learn()
    assert wait_done(hub)["phase"] == "cancelled"


def test_only_one_session_at_a_time(tmp_path, fast):
    dev = FakeRM4(freq_after=10_000)
    hub = make_hub(tmp_path, dev)
    hub.start_learn("dev1", None, sweep=True)
    with pytest.raises(DeviceError):
        hub.start_learn("dev1", None, sweep=True)
    hub.cancel_learn()
    wait_done(hub)


def test_send_reauths_once_on_authorization_error(tmp_path):
    dev = FakeRM4()
    attempts = []

    def flaky_send(data):
        attempts.append(data)
        if len(attempts) == 1:
            raise be.AuthorizationError(-7, "stale key")

    dev.send_data = flaky_send
    hub = make_hub(tmp_path, dev)
    hub.send_packet("dev1", b"\xb2\x00")
    assert len(attempts) == 2 and dev.calls.count("auth") == 2


def test_discover_retries_with_default_route_ip(tmp_path, monkeypatch):
    calls = []

    def fake_discover(timeout, local_ip_address=None):
        calls.append(local_ip_address)
        if local_ip_address is None:
            raise OSError(49, "Can't assign requested address")
        return [FakeRM4()]

    monkeypatch.setattr(hub_module.broadlink, "discover", fake_discover)
    monkeypatch.setattr(hub_module, "default_route_ip", lambda: "192.168.1.20")
    hub = Hub(Store(tmp_path / "s.json"))
    found = hub.discover(timeout=1)
    assert calls == [None, "192.168.1.20"]
    assert found[0]["model"] == "RM4 pro"


def test_discover_reports_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(hub_module.broadlink, "discover", lambda **kw: (_ for _ in ()).throw(OSError(49, "nope")))
    monkeypatch.setattr(hub_module, "default_route_ip", lambda: None)
    with pytest.raises(DeviceError):
        Hub(Store(tmp_path / "s.json")).discover(timeout=1)


def test_add_manual_registers_after_auth(tmp_path, monkeypatch):
    dev = FakeRM4()
    monkeypatch.setattr(hub_module.broadlink, "gendevice", lambda devtype, host, mac: dev)
    hub = Hub(Store(tmp_path / "s.json"))
    info = hub.add_manual("10.0.0.5", "aa:aa:aa:aa:aa:aa", 0x649B)
    assert info["model"] == "RM4 pro" and dev.calls == ["auth"]
    assert hub.store.get("devices", info["id"]) is not None
    with pytest.raises(ValueError):
        hub.add_manual("10.0.0.5", "zz", 0x649B)
    with pytest.raises(ValueError):
        hub.add_manual("10.0.0.5", "aa:aa:aa:aa:aa:aa", 0x1)


def test_rf_device_types_lists_rm_pro_models():
    models = {t["model"] for t in hub_module.rf_device_types()}
    assert "RM4 pro" in models and all("devtype" in t for t in hub_module.rf_device_types())
