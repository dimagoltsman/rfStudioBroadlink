import pytest
from fastapi.testclient import TestClient

import app.main as main
from app.protocols import get_protocol


@pytest.fixture
def client(tmp_path, monkeypatch):
    from app.store import Store
    from app.hub import Hub

    store = Store(tmp_path / "store.json")
    hub = Hub(store)
    sent: list[tuple[str, bytes]] = []
    monkeypatch.setattr(hub, "send_packet", lambda device_id, packet: sent.append((device_id, packet)))
    monkeypatch.setattr(main, "store", store)
    monkeypatch.setattr(main, "hub", hub)
    store.upsert("devices", {"id": "aabbcc", "name": "RM4", "host": "10.0.0.5", "devtype": 0x649B,
                             "mac": "aa:bb:cc:00:00:00", "model": "RM4 pro", "rf": True})
    c = TestClient(main.app)
    c.sent = sent
    return c


def test_protocol_preview_and_analyze(client):
    protocols = client.get("/api/protocols").json()
    assert {p["name"] for p in protocols} == {"aok", "dooya", "secplus1"}
    preview = client.post("/api/protocols/aok/preview",
                          json={"params": {"remote_id": "465016", "channel": 9}, "command": "open"}).json()
    assert preview["bits"].startswith("10100011")
    analysis = client.post("/api/analyze", json={"packet_hex": preview["packet_hex"]}).json()
    assert analysis["guess"] == "aok" and analysis["consensus"] == preview["bits"]


def test_codes_create_send_compare(client):
    proto = get_protocol("dooya")
    ids = []
    for n, channel in enumerate((1, 1, 2)):
        packet_hex = client.post("/api/protocols/dooya/preview",
                                 json={"params": {"remote_id": "abcdef", "channel": channel},
                                       "command": "open"}).json()["packet_hex"]
        code = client.post("/api/codes", json={"name": f"up {n}", "packet_hex": packet_hex}).json()
        assert code["guess"] == "dooya"
        ids.append(code["id"])

    same = client.post("/api/codes/compare", json={"code_ids": ids[:2]}).json()
    assert same["verdict"] == "identical"
    diff = client.post("/api/codes/compare", json={"code_ids": ids}).json()
    assert diff["verdict"] == "differs" and diff["positions"] == [30, 31]

    resp = client.post(f"/api/codes/{ids[0]}/send", json={"device_id": "aabbcc", "repeat": 4})
    assert resp.status_code == 200
    assert client.sent[-1][1][1] == 4


def test_virtual_remote_commands(client):
    body = {"name": "Living", "device_id": "aabbcc", "mode": "virtual", "protocol": "aok",
            "params": {"remote_id": "123456", "channel": 3}}
    remote = client.post("/api/remotes", json=body).json()
    assert remote["repeat"] == 8 and remote["params"]["remote_id"] == 0x123456

    assert client.post(f"/api/remotes/{remote['id']}/commands/open").status_code == 200
    device_id, packet = client.sent[-1]
    assert device_id == "aabbcc" and packet[0] == 0xB2 and packet[1] == 7

    assert client.post(f"/api/remotes/{remote['id']}/commands/dance").status_code == 400
    assert client.post("/api/remotes", json={**body, "device_id": "nope"}).status_code == 400


def test_learned_remote_requires_bound_code(client):
    remote = client.post("/api/remotes", json={"name": "Bed", "device_id": "aabbcc",
                                                 "mode": "learned", "codes": {}}).json()
    resp = client.post(f"/api/remotes/{remote['id']}/commands/open")
    assert resp.status_code == 400 and "No learned code" in resp.json()["detail"]
    assert client.post("/api/remotes", json={"name": "x", "device_id": "aabbcc", "mode": "learned",
                                              "codes": {"open": "missing"}}).status_code == 400


def test_secplus1_remote_advances_and_persists_counter(client):
    body = {"name": "Garage", "device_id": "aabbcc", "mode": "virtual", "protocol": "secplus1",
            "params": {"remote_id": 32445552, "rolling": 2320616984}}
    remote = client.post("/api/remotes", json=body).json()
    assert remote["repeat"] == 4

    first = client.post(f"/api/remotes/{remote['id']}/commands/toggle").json()
    _, packet = client.sent[-1]
    assert packet[0] == 0xD7 and packet[1] == 3  # 315 MHz, 3 extra repeats
    assert first["params"]["rolling"] == 2320616984 + 3
    assert client.get("/api/remotes").json()[0]["params"]["rolling"] == 2320616984 + 3

    client.post(f"/api/remotes/{remote['id']}/commands/toggle")
    assert client.sent[-1][1] != packet  # next press carries the next counter

    assert client.post(f"/api/remotes/{remote['id']}/commands/open").status_code == 400
    assert client.get("/api/remotes").json()[0]["params"]["rolling"] == 2320616984 + 6  # rejected press burns nothing
