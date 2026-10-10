"""Dooya 40-bit fixed-code protocol (also used by many rebadged tubular motors).

Frame: 24-bit remote ID, 8-bit channel, 4-bit button, 4-bit check (== button for
UP/DOWN/STOP/P2). PWM: 1 = 750 µs ON / 350 µs OFF, 0 = 350 µs ON / 750 µs OFF.
Header ≈ 4800 µs ON / 1500 µs OFF, frames separated by ~8 ms silence.
"""
from .base import Param, Protocol, pwm_bits

HEADER = (4800, 1500)
BIT_ONE = (750, 350)
BIT_ZERO = (350, 750)
FRAME_GAP_US = 8000

BUTTONS = {"open": 0x1, "close": 0x3, "stop": 0x5, "program": 0xC}


class Dooya(Protocol):
    name = "dooya"
    label = "Dooya 40-bit"
    description = ("Fixed-code protocol used by Dooya motors and many rebadges (Acmeda, Zemismart, "
                   "Rollerhouse, generic DC/DM tubular motors). Pairing uses the P2 button.")
    params = (
        Param("remote_id", "Remote ID", "hex", bits=24, randomize=True,
              help="24-bit identity of the virtual remote. Randomised on creation; keep it unique per remote."),
        Param("channel", "Channel", "int", minimum=0, maximum=255, default=1,
              help="8-bit channel byte. 0 is 'all channels' on most remotes."),
    )
    commands = {"open": "Open ▲", "close": "Close ▼", "stop": "Stop ■", "program": "P2 / Program"}
    pairing_steps = (
        "On the ORIGINAL remote, select the channel that drives the motor.",
        "Press the P2 button (back of the remote) twice. The motor jogs/beeps after each press.",
        "Within 10 seconds click “P2 / Program” below. The motor jogs to confirm it learned this virtual remote.",
        "Test with Open / Stop / Close. If nothing happens, repeat and try a higher repeat count.",
        "No working remote? Power-cycle the motor (or press its setting button) and send “P2 / Program” inside the 10 s window instead.",
    )
    default_repeat = 3

    def command_code(self, command: str) -> int:
        try:
            nibble = BUTTONS[command]
        except KeyError as exc:
            raise ValueError(f"Unknown command '{command}'") from exc
        return (nibble << 4) | nibble

    def frame_bits(self, params: dict[str, int], command: str) -> str:
        word = (params["remote_id"] << 16) | (params["channel"] << 8) | self.command_code(command)
        return f"{word:040b}"

    def frame_pulses(self, bits: str) -> list[int]:
        pulses = [*HEADER, *pwm_bits(bits, BIT_ONE, BIT_ZERO)]
        pulses[-1] += FRAME_GAP_US
        return pulses
