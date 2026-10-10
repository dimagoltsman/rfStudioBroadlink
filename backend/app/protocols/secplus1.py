"""Chamberlain / LiftMaster / Craftsman Security+ 1.0 rolling code (315 MHz).

Payload: 40 base-3 symbols. A 32-bit rolling counter (lowest bit ignored, bit order
reversed) gives 20 trits; a 20-trit fixed code (remote identity + button) is folded in
through a running accumulator that restarts every 10 trits. The 40 symbols go out as two
frames of sync + 20 symbols. OOK symbol v: (3 - v) × 500 µs OFF then (v + 1) × 500 µs ON;
sync is 0 for the first frame and 2 for the second; 58 ms of silence after each frame.
Remotes count up by three per press; receivers accept any counter at least two above
the last one they saw. Reference: github.com/argilo/secplus (rtl_433 and the Flipper Zero
use the same layout).
"""
from __future__ import annotations

import secrets

from .base import Param, Protocol

SYMBOL_US = 500
TRITS = 20  # base-3 digits of each of the rolling and fixed codes
HALF_SYMBOLS = 20  # symbols per frame after the sync: 10 rolling + 10 fixed
SYNCS = (0, 2)
FRAME_GAP_US = 58_000
ROLLING_STEP = 3
ROLLING_MASK = 0xFFFFFFFF
ROLLING_START_LIMIT = 1 << 28  # fresh remotes start low so the counter never wraps in practice
MAX_REMOTE_ID = 3 ** 17 - 1
MAX_FIXED = 3 ** 20 - 1
SYMBOL_TOLERANCE_US = 200
FRAME_END_US = 5_000  # an OFF longer than this ends a frame in a capture
FIXED_FIELDS = ("remote_id", "id1", "id0", "button")


def fixed_code(remote_id: int, id1: int, id0: int, button: int) -> int:
    """fixed = remote_id·27 + id1·9 + id0·3 + button, the field layout rtl_433 and Flipper decode.

    id1/id0 tell the opener what kind of transmitter this is: id1 == 1 is a door button, id1 == 2 a light
    button (the opener toggles its light instead of moving the door), id1 == 0 a keypad. button is the
    switch position on a multi-button remote."""
    return remote_id * 27 + id1 * 9 + id0 * 3 + button


def fixed_fields(fixed: int) -> dict[str, int]:
    return {"remote_id": fixed // 27, "id1": (fixed // 9) % 3, "id0": (fixed // 3) % 3, "button": fixed % 3}


def _reverse_bits(value: int, width: int) -> int:
    return int(f"{value:0{width}b}"[::-1], 2)


def _trits(value: int, count: int) -> list[int]:
    digits = []
    for _ in range(count):
        digits.append(value % 3)
        value //= 3
    return digits[::-1]


def encode_symbols(rolling: int, fixed: int) -> list[int]:
    """The 40 payload symbols (without syncs) for a rolling/fixed pair."""
    if not 0 <= rolling <= ROLLING_MASK:
        raise ValueError("Rolling code must fit in 32 bits")
    if not 0 <= fixed <= MAX_FIXED:
        raise ValueError("Fixed code must fit in 20 trits")
    rolling_trits = _trits(_reverse_bits(rolling & 0xFFFFFFFE, 32), TRITS)
    fixed_trits = _trits(fixed, TRITS)
    code: list[int] = []
    acc = 0
    for i in range(TRITS):
        if i % (TRITS // 2) == 0:
            acc = 0
        code.append(rolling_trits[i])
        acc += rolling_trits[i] + fixed_trits[i]
        code.append(acc % 3)
    return code


def decode_symbols(code: list[int]) -> tuple[int, int]:
    """Inverse of encode_symbols: (rolling, fixed). The rolling code comes back with bit 0 clear."""
    if len(code) != 2 * TRITS:
        raise ValueError("Expected 40 symbols")
    rolling = fixed = acc = 0
    for i in range(0, len(code), 2):
        if i % HALF_SYMBOLS == 0:
            acc = 0
        rolling = rolling * 3 + code[i]
        acc += code[i]
        digit = (code[i + 1] - acc) % 3
        fixed = fixed * 3 + digit
        acc += digit
    return _reverse_bits(rolling, 32), fixed


def _on(symbol: int) -> int:
    return (symbol + 1) * SYMBOL_US


def _off(symbol: int) -> int:
    return (3 - symbol) * SYMBOL_US


def _symbol_for_on(on_us: int) -> int | None:
    n = round(on_us / SYMBOL_US)
    if 1 <= n <= 3 and abs(on_us - n * SYMBOL_US) <= SYMBOL_TOLERANCE_US:
        return n - 1
    return None


def decode_pulses(pulses: list[int]) -> dict | None:
    """Find a Security+ 1.0 frame pair (sync 0 frame, then sync 2 frame) in a captured ON/OFF
    pulse train and return its fields, or None when the capture is not Security+ 1.0."""
    frames: list[list[int]] = []
    current: list[int] | None = None
    for i in range(0, len(pulses) - 1, 2):
        symbol = _symbol_for_on(pulses[i])
        if symbol is None:
            current = None
            continue
        current = [symbol] if current is None else [*current, symbol]
        if pulses[i + 1] > FRAME_END_US:
            if len(current) == HALF_SYMBOLS + 1:
                frames.append(current)
            current = None
    for first, second in zip(frames, frames[1:]):
        if first[0] == 0 and second[0] == 2:
            rolling, fixed = decode_symbols(first[1:] + second[1:])
            return {"rolling": rolling, "fixed": fixed, **fixed_fields(fixed)}
    return None


class SecurityPlus1(Protocol):
    name = "secplus1"
    label = "Security+ 1.0 (315 MHz)"
    description = ("Chamberlain / LiftMaster / Craftsman garage openers with an ORANGE or RED learn button "
                   "(roughly 2005–2011; 371LM / 373LM / 139.53753 remotes). Rolling code: the workbench "
                   "keeps the counter and advances it on every press, so the opener accepts it like a "
                   "factory remote. Purple-button (390 MHz) and yellow-button (Security+ 2.0) openers "
                   "are not supported.")
    params = (
        Param("remote_id", "Remote ID", "int", minimum=0, maximum=MAX_REMOTE_ID, randomize=True,
              help="Identity of the virtual remote (what rtl_433 / Flipper show as remote_id). Randomised on creation."),
        Param("id1", "Type digit id1", "int", minimum=1, maximum=2, default=1,
              help="Transmitter type: 1 = door button (verified on a Craftsman 41AC175-2A), 2 = light button "
                   "(the opener toggles its light instead of moving the door). 0 would mean a keypad."),
        Param("id0", "Type digit id0", "int", minimum=0, maximum=2, default=0,
              help="Second transmitter-type digit, 0–2. Copy from a working remote."),
        Param("button", "Button", "int", minimum=0, maximum=2, default=1,
              help="Switch position of the button: 0 middle, 1 left, 2 right. Copy from a working remote."),
        Param("rolling", "Rolling counter", "int", minimum=0, maximum=ROLLING_MASK, randomize=True,
              help="Advances by 3 on every press. The opener rejects any code it has already seen; "
                   "only raise it by hand if you need to re-sync."),
    )
    commands = {"toggle": "Door ▲▼"}
    pairing_steps = (
        "Put the Broadlink within a few metres of the opener (line of sight to the motor head helps).",
        "On the motor head, press and release the orange/red LEARN button. Its LED stays lit for 30 seconds.",
        "Within those 30 seconds click “Door ▲▼” below. The opener's light blinks (or the unit clicks) once it has learned this remote.",
        "Test with “Door ▲▼”: every press cycles open → stop → close, exactly like a button on a real remote.",
        "Nothing happens? Press once more (the opener ignores a counter it has already seen), or redo the LEARN step. "
        "The opener forgets all remotes if you hold LEARN for 6 seconds, so avoid that unless you mean it.",
    )
    default_repeat = 4
    frequency_mhz = 315.0
    symbol_base = 3

    def random_params(self) -> dict[str, int]:
        return {**super().random_params(), "rolling": secrets.randbelow(ROLLING_START_LIMIT)}

    def frame_bits(self, params: dict[str, int], command: str) -> str:
        if command not in self.commands:
            raise ValueError(f"Unknown command '{command}'")
        fixed = fixed_code(*(params[field] for field in FIXED_FIELDS))
        return "".join(map(str, encode_symbols(params["rolling"], fixed)))

    def frame_pulses(self, bits: str) -> list[int]:
        """Both frames, ON-first: sync pulse, 20 × (OFF, ON), then the gap merged with the next sync's OFF."""
        symbols = [int(c) for c in bits]
        halves = (symbols[:HALF_SYMBOLS], symbols[HALF_SYMBOLS:])
        pulses: list[int] = []
        for index, (sync, half) in enumerate(zip(SYNCS, halves)):
            next_sync = SYNCS[(index + 1) % len(SYNCS)]
            pulses.append(_on(sync))
            for symbol in half:
                pulses += [_off(symbol), _on(symbol)]
            pulses.append(FRAME_GAP_US + _off(next_sync))
        return pulses

    def advance(self, params: dict[str, int]) -> dict[str, int]:
        return {**params, "rolling": (params["rolling"] + ROLLING_STEP) & ROLLING_MASK}
