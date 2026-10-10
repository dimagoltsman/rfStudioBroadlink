import pytest

from app.protocols import PROTOCOLS, get_protocol

# Reference captures from github.com/akirjavainen/A-OK (ID 0x465016, channel mask 0x0100).
AOK_REFERENCE = {
    "close": "10100011010001100101000000010110000000010000000001000011111100001",
    "open": "10100011010001100101000000010110000000010000000000001011101110001",
    "stop": "10100011010001100101000000010110000000010000000000100011110100001",
    "program": "10100011010001100101000000010110000000010000000001010011000000001",
}


@pytest.mark.parametrize("command,bits", AOK_REFERENCE.items())
def test_aok_matches_reference_frames(command, bits):
    proto = get_protocol("aok")
    assert proto.frame_bits({"remote_id": 0x465016, "channel": 9}, command) == bits


def test_aok_frame_pulses_shape():
    proto = get_protocol("aok")
    pulses = proto.encode({"remote_id": 1, "channel": 1}, "open")
    assert len(pulses) == 2 + 65 * 2
    assert pulses[:2] == [5300, 530]
    assert pulses[-1] > 5000


def test_dooya_layout():
    proto = get_protocol("dooya")
    bits = proto.frame_bits({"remote_id": 0x123456, "channel": 0x0B}, "stop")
    assert len(bits) == 40
    assert int(bits[:24], 2) == 0x123456
    assert int(bits[24:32], 2) == 0x0B
    assert bits[32:] == "01010101"


def test_validate_rejects_out_of_range():
    proto = get_protocol("dooya")
    with pytest.raises(ValueError):
        proto.validate({"remote_id": 1 << 24, "channel": 1})
    with pytest.raises(ValueError):
        proto.validate({"remote_id": "abc", "channel": 300})
    assert proto.validate({"remote_id": "abcdef", "channel": "2"}) == {"remote_id": 0xABCDEF, "channel": 2}


def test_random_params_fit_declared_widths():
    for proto in PROTOCOLS.values():
        params = proto.random_params()
        assert proto.validate(params) == params


def test_unknown_command():
    with pytest.raises(ValueError):
        get_protocol("aok").encode({"remote_id": 1, "channel": 1}, "dance")
