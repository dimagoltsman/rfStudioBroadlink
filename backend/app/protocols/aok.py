"""A-OK (AC114 / AC123 remotes, AM25/AM35 motors) 65-bit fixed-code protocol.

Frame (MSB first): 0xA3 start byte, 24-bit remote ID, 16-bit channel bitmask,
8-bit command, 8-bit checksum (sum of ID bytes + mask bytes + command, mod 256),
then a trailing 1 bit. PWM: 1 = 565 µs ON / 270 µs OFF, 0 = 270 µs ON / 565 µs OFF.
AGC header 5300 µs ON / 530 µs OFF, ~5 ms silence after each frame.
Reverse-engineered by akirjavainen & Jason von Nieda (github.com/akirjavainen/A-OK).
"""
from .base import Param, Protocol, pwm_bits

START_BYTE = 0xA3
HEADER = (5300, 530)
BIT_ONE = (565, 270)
BIT_ZERO = (270, 565)
FRAME_GAP_US = 5030

COMMANDS = {"open": 0x0B, "close": 0x43, "stop": 0x23, "program": 0x53}


class AOK(Protocol):
    name = "aok"
    label = "A-OK 65-bit"
    description = ("Fixed-code protocol of A-OK AC114/AC123 remotes and AM-series motors "
                   "(also sold under many projector-screen and blind brands).")
    params = (
        Param("remote_id", "Remote ID", "hex", bits=24, randomize=True,
              help="24-bit identity of the virtual remote. Randomised on creation."),
        Param("channel", "Channel", "int", minimum=1, maximum=16, default=1,
              help="Channel 1-16, sent as a one-hot bit in the 16-bit channel mask."),
    )
    commands = {"open": "Open ▲", "close": "Close ▼", "stop": "Stop ■", "program": "Program"}
    pairing_steps = (
        "Press and hold the motor's PROGRAM / SET button (or the P button on the receiver) until the motor jogs.",
        "Within 10 seconds click “Program” below. Some receivers expect “Open ▲” instead; try that if the motor does not jog.",
        "The motor jogs to confirm. Test with Open / Stop / Close.",
    )
    default_repeat = 8

    def command_code(self, command: str) -> int:
        try:
            return COMMANDS[command]
        except KeyError as exc:
            raise ValueError(f"Unknown command '{command}'") from exc

    @staticmethod
    def channel_mask(channel: int) -> int:
        return 1 << (channel - 1)

    def frame_bits(self, params: dict[str, int], command: str) -> str:
        remote_id, mask, cmd = params["remote_id"], self.channel_mask(params["channel"]), self.command_code(command)
        checksum = (sum(remote_id.to_bytes(3, "big")) + sum(mask.to_bytes(2, "big")) + cmd) & 0xFF
        word = (START_BYTE << 56) | (remote_id << 32) | (mask << 16) | (cmd << 8) | checksum
        return f"{word:064b}1"

    def frame_pulses(self, bits: str) -> list[int]:
        pulses = [*HEADER, *pwm_bits(bits, BIT_ONE, BIT_ZERO)]
        pulses[-1] += FRAME_GAP_US
        return pulses
