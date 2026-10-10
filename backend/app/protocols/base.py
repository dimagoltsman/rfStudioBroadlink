from __future__ import annotations

import secrets
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class Param:
    key: str
    label: str
    kind: str  # "hex" (`bits` wide) or "int" (range)
    help: str = ""
    bits: int = 0
    minimum: int = 0
    maximum: int = 0
    default: int | None = None
    randomize: bool = False  # pick a random value on creation (identities, counters)

    def random_value(self) -> int:
        if self.kind == "hex":
            return secrets.randbits(self.bits)
        return self.minimum + secrets.randbelow(self.maximum - self.minimum + 1)


class Protocol(ABC):
    name: str
    label: str
    description: str
    params: tuple[Param, ...]
    commands: dict[str, str]  # command key -> label
    pairing_steps: tuple[str, ...]
    default_repeat: int
    frequency_mhz: float = 433.92
    symbol_base: int = 2  # radix of the strings frame_bits() returns (3 for ternary protocols)

    @abstractmethod
    def frame_bits(self, params: dict[str, int], command: str) -> str:
        """Return the symbol string of one frame."""

    @abstractmethod
    def frame_pulses(self, bits: str) -> list[int]:
        """Return one full frame (header, bits, trailing gap) as µs ON/OFF pulses."""

    def encode(self, params: dict[str, int], command: str) -> list[int]:
        return self.frame_pulses(self.frame_bits(self.validate(params), command))

    def advance(self, params: dict[str, int]) -> dict[str, int]:
        """Params to persist after a press. Rolling-code protocols bump their counter here."""
        return params

    def random_params(self) -> dict[str, int]:
        out = {}
        for p in self.params:
            if p.randomize:
                out[p.key] = p.random_value()
            else:
                out[p.key] = p.default if p.default is not None else p.minimum
        return out

    def validate(self, params: dict) -> dict[str, int]:
        clean = {}
        for p in self.params:
            raw = params.get(p.key, p.default)
            if raw is None:
                raise ValueError(f"Missing parameter '{p.key}'")
            value = int(raw, 16) if isinstance(raw, str) and p.kind == "hex" else int(raw)
            if p.kind == "hex" and not 0 <= value < (1 << p.bits):
                raise ValueError(f"'{p.key}' must fit in {p.bits} bits")
            if p.kind == "int" and not p.minimum <= value <= p.maximum:
                raise ValueError(f"'{p.key}' must be between {p.minimum} and {p.maximum}")
            clean[p.key] = value
        return clean

    def command_code(self, command: str) -> int:
        raise NotImplementedError

    def describe(self) -> dict:
        return {
            "name": self.name,
            "label": self.label,
            "description": self.description,
            "params": [asdict(p) for p in self.params],
            "commands": self.commands,
            "pairing_steps": list(self.pairing_steps),
            "default_repeat": self.default_repeat,
            "frequency_mhz": self.frequency_mhz,
        }


def pwm_bits(bits: str, one: tuple[int, int], zero: tuple[int, int]) -> list[int]:
    pulses: list[int] = []
    for b in bits:
        pulses.extend(one if b == "1" else zero)
    return pulses
