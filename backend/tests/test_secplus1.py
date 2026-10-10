import pytest

from app.protocols import get_protocol
from app import rf
from app.protocols.secplus1 import decode_pulses, decode_symbols, encode_symbols, fixed_code, fixed_fields

# Reference vectors from github.com/argilo/secplus test_secplus.py (real remote, fixed 876029923).
VECTORS = [
    ("0010000211122102222101112211012101001110", 2320616984, 876029923),
    ("0021121200000001021001111102210202010102", 2320616988, 876029923),
    ("1122021222211211102201001020111022200012", 2320616990, 876029923),
]
PARAMS = {"remote_id": 32445552, "id1": 2, "id0": 0, "button": 1, "rolling": 2320616984}


@pytest.mark.parametrize("code,rolling,fixed", VECTORS)
def test_symbols_match_reference(code, rolling, fixed):
    assert "".join(map(str, encode_symbols(rolling, fixed))) == code
    assert decode_symbols([int(c) for c in code]) == (rolling, fixed)


def test_fixed_code_layout_matches_reference_remote():
    # secplus README: fixed=876029923 decodes to id1=2 id0=0 switch=1 (left) remote_id=32445552
    assert fixed_code(32445552, 2, 0, 1) == 876029923
    assert fixed_fields(876029923) == {"remote_id": 32445552, "id1": 2, "id0": 0, "button": 1}


def test_lowest_rolling_bit_is_ignored():
    assert encode_symbols(2320616984, 876029923) == encode_symbols(2320616985, 876029923)


def test_frame_bits_use_reference_vector():
    assert get_protocol("secplus1").frame_bits(PARAMS, "toggle") == VECTORS[0][0]


def test_frame_pulses_layout():
    proto = get_protocol("secplus1")
    pulses = proto.frame_pulses(VECTORS[0][0])
    assert len(pulses) == 2 * (1 + 2 * 20 + 1)
    assert pulses[0] == 500 and pulses[42] == 1500  # sync 0 / sync 2 ON pulses
    assert pulses[1:3] == [1500, 500]  # first payload symbol is 0: 1.5 ms OFF, 0.5 ms ON
    assert pulses[41] == 58_000 + 500 and pulses[83] == 58_000 + 1500  # gap + next sync's OFF
    assert all(pulses[i] + pulses[i + 1] == 2000 for i in range(1, 41, 2))
    assert all(pulses[i] + pulses[i + 1] == 2000 for i in range(43, 83, 2))


def test_advance_counts_by_three_and_wraps():
    proto = get_protocol("secplus1")
    assert proto.advance({**PARAMS, "rolling": 10}) == {**PARAMS, "rolling": 13}
    assert proto.advance({**PARAMS, "rolling": 0xFFFFFFFF})["rolling"] == 2


def test_random_params_start_low_and_validate():
    proto = get_protocol("secplus1")
    params = proto.random_params()
    assert proto.validate(params) == params
    assert params["rolling"] < 1 << 28


def test_unknown_command_and_out_of_range():
    proto = get_protocol("secplus1")
    with pytest.raises(ValueError):
        proto.encode(PARAMS, "open")
    with pytest.raises(ValueError):
        proto.validate({**PARAMS, "remote_id": 3 ** 17})
    with pytest.raises(ValueError):
        proto.validate({**PARAMS, "id1": 0})


def test_type_digits_default_to_a_door_button():
    proto = get_protocol("secplus1")
    assert proto.validate({"remote_id": 1, "rolling": 2}) == {"remote_id": 1, "id1": 1, "id0": 0, "button": 1, "rolling": 2}


def test_decode_pulses_round_trip_with_noise_and_repeats():
    proto = get_protocol("secplus1")
    frame_pair = proto.encode(PARAMS, "toggle")
    capture = [700, 300, 1000, 9000, *frame_pair[30:], *frame_pair, *frame_pair[:20]]  # junk, partial, full, partial
    decoded = decode_pulses(capture)
    assert decoded == {"rolling": 2320616984, "fixed": 876029923, **fixed_fields(876029923)}
    assert decode_pulses(get_protocol("dooya").encode({"remote_id": 1, "channel": 1}, "open")) is None


def test_analyze_recognises_secplus1():
    proto = get_protocol("secplus1")
    analysis = rf.analyze_pulses(proto.encode(PARAMS, "toggle"), rf.KIND_RF315)
    assert analysis.guess == "secplus1" and analysis.decoded["remote_id"] == 32445552
    assert analysis.to_dict()["decoded"]["button"] == 1
