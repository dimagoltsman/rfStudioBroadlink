"""Broadlink RF packet encoding and pulse-train analysis.

A Broadlink IR/RF packet is: [kind, repeat, len_lo, len_hi, pulse ticks...].
Each tick is 2^-15 s (~30.45 µs). A pulse longer than 255 ticks is stored as
0x00 followed by a big-endian 16-bit value. Pulses alternate ON, OFF, ON, OFF...
"""
from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass, field
from statistics import median

from .protocols import secplus1

TICK_US = 8192 / 269  # one Broadlink timing unit ≈ 30.45 µs
KIND_IR, KIND_RF433, KIND_RF315 = 0x26, 0xB2, 0xD7
KIND_LABELS = {KIND_IR: "ir", KIND_RF433: "rf433", KIND_RF315: "rf315"}
MAX_TICKS = 0xFFFF
TRAILING_SILENCE_US = 10_000

# Any pulse longer than this is treated as a sync header or an inter-frame gap.
SYNC_RATIO = 2.5
# Data pulses of common 433 MHz protocols are well below this; longer pulses are headers/gaps.
DATA_PULSE_LIMIT_US = 1600
MIN_FRAME_BITS = 8


def kind_for_frequency(mhz: float) -> int:
    """Broadlink only knows two RF bands; anything nearer 315 than 433 goes out on the 315 MHz one."""
    return KIND_RF315 if mhz < (315 + 433.92) / 2 else KIND_RF433


def us_to_ticks(us: int) -> int:
    return max(1, min(MAX_TICKS, round(us / TICK_US)))


def pulses_to_packet(pulses: list[int], kind: int = KIND_RF433, repeat: int = 0) -> bytes:
    """Build a Broadlink send packet from a microsecond ON/OFF pulse train."""
    if not pulses:
        raise ValueError("Empty pulse train")
    if len(pulses) % 2:
        pulses = [*pulses, TRAILING_SILENCE_US]
    body = bytearray()
    for us in pulses:
        ticks = us_to_ticks(us)
        if ticks > 0xFF:
            body += bytes([0, ticks >> 8, ticks & 0xFF])
        else:
            body.append(ticks)
    header = bytes([kind & 0xFF, repeat & 0xFF, len(body) & 0xFF, len(body) >> 8])
    return header + bytes(body)


def packet_to_pulses(packet: bytes) -> tuple[int, int, list[int]]:
    """Parse a Broadlink packet into (kind, repeat, pulses_us)."""
    if len(packet) < 4:
        raise ValueError("Packet too short")
    kind, repeat = packet[0], packet[1]
    length = packet[2] | (packet[3] << 8)
    data = packet[4:4 + length]
    pulses: list[int] = []
    i = 0
    while i < len(data):
        ticks = data[i]
        i += 1
        if ticks == 0:
            if i + 1 >= len(data):
                break  # truncated long-pulse marker at the very end; ignore
            ticks = (data[i] << 8) | data[i + 1]
            i += 2
        pulses.append(round(ticks * TICK_US))
    return kind, repeat, pulses


def _two_means(values: list[int]) -> tuple[int, int]:
    """Split durations into a short and a long cluster (1-D k-means, k=2)."""
    lo, hi = float(min(values)), float(max(values))
    if hi - lo < 1:
        return int(lo), int(hi)
    for _ in range(12):
        split = (lo + hi) / 2
        short = [v for v in values if v <= split]
        long = [v for v in values if v > split]
        if not short or not long:
            break
        lo, hi = median(short), median(long)
    return int(lo), int(hi)


# A cluster this sparse is header/sync pulses (e.g. Dooya's 1500 µs header low), not data.
HEADER_CLUSTER_SHARE = 0.08


def _data_clusters(values: list[int]) -> tuple[int, int]:
    """Short/long data pulse durations, ignoring sparse header pulses."""
    short, long = _two_means(values)
    split = (short + long) / 2
    longs = [v for v in values if v > split]
    if 0 < len(longs) < HEADER_CLUSTER_SHARE * len(values):
        return _data_clusters([v for v in values if v <= split])
    return short, long


@dataclass
class Frame:
    bits: str
    hex: str
    length: int
    pulse_offset: int


@dataclass
class Analysis:
    kind: str
    repeat: int
    pulse_count: int
    duration_us: int
    short_us: int
    long_us: int
    sync_us: list[int]
    frames: list[Frame]
    unique_frames: list[dict]
    consensus: str | None
    guess: str
    encoding: str = "pwm"
    warnings: list[str] = field(default_factory=list)
    decoded: dict | None = None  # protocol-specific fields when a decoder recognised the capture

    def to_dict(self) -> dict:
        return asdict(self)


def bits_to_hex(bits: str, base: int = 2) -> str:
    if not bits:
        return ""
    value = int(bits, base)
    width = (len(bits) + 3) // 4 if base == 2 else max(1, (value.bit_length() + 3) // 4)
    return f"{value:0{width}x}"


def analyze_pulses(pulses: list[int], kind: int = KIND_RF433, repeat: int = 0) -> Analysis:
    """Decode a pulse train as PWM (long ON = 1, short ON = 0), split into frames."""
    warnings: list[str] = []
    secplus = secplus1.decode_pulses(pulses)
    if secplus:
        return Analysis(KIND_LABELS.get(kind, hex(kind)), repeat, len(pulses), sum(pulses), 0, 0, [], [], [], None,
                        "secplus1", "ternary", ["Security+ 1.0 rolling code: replay will not work, use a virtual remote"],
                        secplus)
    data_pulses = [p for p in pulses if p < DATA_PULSE_LIMIT_US]
    if len(data_pulses) < MIN_FRAME_BITS * 2:
        return Analysis(KIND_LABELS.get(kind, hex(kind)), repeat, len(pulses), sum(pulses),
                        0, 0, [], [], [], None, "unknown", "pwm", ["Too few data pulses to decode"])

    short_us, long_us = _data_clusters(data_pulses)
    threshold = (short_us + long_us) / 2
    sync_limit = SYNC_RATIO * long_us

    frames: list[Frame] = []
    sync_us: list[int] = []
    current: list[str] = []
    start = 0

    def flush(end_index: int) -> None:
        nonlocal current, start
        if len(current) >= MIN_FRAME_BITS:
            bits = "".join(current)
            frames.append(Frame(bits, bits_to_hex(bits), len(bits), start))
        current = []
        start = end_index

    for k in range(0, len(pulses) - 1, 2):
        mark, space = pulses[k], pulses[k + 1]
        if mark > sync_limit:
            flush(k)
            sync_us.append(mark)
            start = k + 2
            continue
        current.append("1" if mark > threshold else "0")
        if space > sync_limit:
            flush(k + 2)
    flush(len(pulses))

    encoding = "pwm"
    manchester = try_manchester(pulses, short_us, sync_limit) if abs(long_us - 2 * short_us) < 0.25 * long_us else None
    if manchester:
        frames, encoding = manchester, "manchester"

    counts = Counter(f.bits for f in frames)
    unique = [{"bits": b, "hex": bits_to_hex(b), "length": len(b), "count": n}
              for b, n in counts.most_common()]
    consensus = unique[0]["bits"] if unique else None
    if len(unique) > 1:
        warnings.append("Capture contains differing frames; the remote may use a rolling code or the capture is noisy")

    guess = guess_protocol(consensus, sync_us, short_us, long_us) if encoding == "pwm" else "unknown"
    return Analysis(KIND_LABELS.get(kind, hex(kind)), repeat, len(pulses), sum(pulses),
                    short_us, long_us, sync_us, frames, unique, consensus, guess, encoding, warnings)


MANCHESTER_MAX_VIOLATION_SHARE = 0.03


def _manchester_frame(frame: list[int], unit_us: int) -> tuple[int, str]:
    """Decode one ON-first pulse frame as Manchester (ON→OFF = 1, OFF→ON = 0).

    Returns (coding violations, bits). A violation is a bit period whose two halves are equal,
    which valid Manchester never produces, so a near-zero count identifies the encoding."""
    halves: list[int] = []
    for i, width in enumerate(frame):
        halves += [1 if i % 2 == 0 else 0] * max(1, round(width / unit_us))
    if len(halves) % 2:
        halves.append(0)  # trailing OFF half merges into the inter-frame gap
    pairs = list(zip(halves[0::2], halves[1::2]))
    violations = sum(a == b for a, b in pairs)
    bits = "".join("1" if p == (1, 0) else "0" if p == (0, 1) else "?" for p in pairs)
    return violations, bits


def _frame_pulses(pulses: list[int], sync_limit: float) -> list[list[int]]:
    """Split the train into raw pulse frames at long OFF gaps (ON pulses longer than a header are dropped)."""
    frames: list[list[int]] = []
    current: list[int] = []
    for i, width in enumerate(pulses):
        on = i % 2 == 0
        if width > sync_limit:
            if not on and current:
                frames.append(current)
            current = []
            continue
        if not current and not on:
            continue
        current.append(width)
    if current:
        frames.append(current)
    return frames


def try_manchester(pulses: list[int], short_us: int, sync_limit: float) -> list[Frame] | None:
    """Return Manchester-decoded frames when the train is valid Manchester, else None."""
    raw = [f for f in _frame_pulses(pulses, sync_limit) if len(f) >= MIN_FRAME_BITS]
    if not raw:
        return None
    decoded = [(_manchester_frame(f, short_us), f) for f in raw]
    full_len = Counter(len(bits) for (_, bits), _ in decoded).most_common(1)[0][0]
    complete = [(v, bits) for (v, bits), _ in decoded if len(bits) == full_len]
    total_bits = sum(len(b) for _, b in complete)
    if not complete or sum(v for v, _ in complete) > MANCHESTER_MAX_VIOLATION_SHARE * total_bits:
        return None
    frames = []
    offset = 0
    for (violations, bits), f in decoded:
        if len(bits) == full_len and violations == 0:
            frames.append(Frame(bits, bits_to_hex(bits), len(bits), offset))
        offset += len(f)
    return frames or None


def guess_protocol(bits: str | None, sync_us: list[int], short_us: int, long_us: int) -> str:
    if not bits:
        return "unknown"
    sync = median(sync_us) if sync_us else 0
    if len(bits) in (64, 65) and bits.startswith("10100011") and 4500 <= sync <= 6000:
        return "aok"
    if len(bits) == 40 and 4300 <= sync <= 5600 and 250 <= short_us <= 450:
        return "dooya"
    return "unknown"


def analyze_packet(packet: bytes) -> Analysis:
    kind, repeat, pulses = packet_to_pulses(packet)
    return analyze_pulses(pulses, kind, repeat)


def compare_frames(named_bits: list[tuple[str, str]]) -> dict:
    """Diff the consensus frame of several captures bit by bit."""
    lengths = {len(b) for _, b in named_bits if b}
    if len(named_bits) < 2:
        return {"verdict": "need_two", "positions": [], "rows": named_bits}
    if len(lengths) != 1:
        return {"verdict": "length_mismatch", "positions": [], "rows": named_bits,
                "message": "Captures decoded to different bit lengths; re-capture or check the sync detection"}
    length = lengths.pop()
    positions = [i for i in range(length) if len({b[i] for _, b in named_bits}) > 1]
    verdict = "identical" if not positions else "differs"
    return {"verdict": verdict, "positions": positions, "rows": named_bits, "length": length}
