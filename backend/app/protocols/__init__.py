"""Registry of RF protocols the workbench can synthesise (virtual remotes)."""
from .aok import AOK
from .base import Protocol
from .dooya import Dooya
from .secplus1 import SecurityPlus1

PROTOCOLS: dict[str, Protocol] = {p.name: p for p in (Dooya(), AOK(), SecurityPlus1())}


def get_protocol(name: str) -> Protocol:
    try:
        return PROTOCOLS[name]
    except KeyError as exc:
        raise ValueError(f"Unknown protocol '{name}'") from exc
