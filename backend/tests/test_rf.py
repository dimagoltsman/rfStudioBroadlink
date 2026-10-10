from app import rf
from app.protocols import get_protocol


def test_packet_roundtrip_preserves_timings_within_a_tick():
    pulses = [4800, 1500, 350, 750, 750, 350, 9000, 300]
    packet = rf.pulses_to_packet(pulses, rf.KIND_RF433, repeat=2)
    assert packet[0] == 0xB2 and packet[1] == 2
    kind, repeat, decoded = rf.packet_to_pulses(packet)
    assert (kind, repeat) == (0xB2, 2)
    assert all(abs(a - b) <= rf.TICK_US for a, b in zip(pulses, decoded))


def test_long_pulse_uses_three_byte_encoding():
    packet = rf.pulses_to_packet([9000, 300])
    body = packet[4:]
    assert body[0] == 0 and (body[1] << 8 | body[2]) == round(9000 / rf.TICK_US)
    assert len(body) == 4


def test_odd_pulse_train_gets_trailing_silence():
    _, _, decoded = rf.packet_to_pulses(rf.pulses_to_packet([500, 500, 500]))
    assert len(decoded) == 4


def test_analyze_recognises_aok_frames():
    proto = get_protocol("aok")
    params = {"remote_id": 0x465016, "channel": 9}
    pulses = proto.encode(params, "close") * 3
    analysis = rf.analyze_pulses(pulses)
    assert analysis.guess == "aok"
    assert len(analysis.frames) == 3
    assert analysis.consensus == proto.frame_bits(params, "close")
    assert len(analysis.unique_frames) == 1


def test_analyze_recognises_dooya_frames():
    proto = get_protocol("dooya")
    params = {"remote_id": 0xABCDEF, "channel": 1}
    analysis = rf.analyze_pulses(proto.encode(params, "open") * 2)
    assert analysis.guess == "dooya"
    assert analysis.consensus == proto.frame_bits(params, "open")
    assert 300 <= analysis.short_us <= 400 and 700 <= analysis.long_us <= 800


def test_compare_frames_flags_changing_bits():
    rows = [("a", "1010"), ("b", "1011"), ("c", "1010")]
    result = rf.compare_frames(rows)
    assert result["verdict"] == "differs" and result["positions"] == [3]
    assert rf.compare_frames([("a", "1010"), ("b", "1010")])["verdict"] == "identical"
    assert rf.compare_frames([("a", "1010"), ("b", "101")])["verdict"] == "length_mismatch"


def _manchester_pulses(bits: str, unit: int = 400, gap: int = 6400) -> list[int]:
    halves = []
    for b in bits:
        halves += [1, 0] if b == "1" else [0, 1]
    pulses, run, level = [], 0, 1
    for h in halves:
        if h == level:
            run += 1
        else:
            pulses.append(run * unit); level, run = h, 1
    pulses.append(run * unit)
    if level == 0:
        pulses[-1] += gap
    else:
        pulses.append(gap)
    return pulses


def test_analyze_detects_manchester_frames():
    bits = "1" + "0110100110010110" * 4  # 65 bits, like the Alpha capture
    pulses = _manchester_pulses(bits) * 3
    analysis = rf.analyze_pulses(pulses)
    assert analysis.encoding == "manchester"
    assert analysis.consensus == bits
    assert len(analysis.frames) == 3 and analysis.guess == "unknown"


def test_pwm_frames_are_not_mistaken_for_manchester():
    proto = get_protocol("aok")
    analysis = rf.analyze_pulses(proto.encode({"remote_id": 0x465016, "channel": 9}, "open") * 3)
    assert analysis.encoding == "pwm" and analysis.guess == "aok"
