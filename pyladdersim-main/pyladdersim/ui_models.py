from dataclasses import dataclass, field
from typing import Literal


ComponentKind = Literal["contact", "inverted_contact", "timer", "coil", "unknown"]


@dataclass(frozen=True)
class ComponentView:
    id: str
    name: str
    kind: ComponentKind
    state: bool = False
    timer_type: str | None = None
    clickable: bool = False


@dataclass(frozen=True)
class RungView:
    id: str
    components: list[ComponentView] = field(default_factory=list)
    power_on: bool = False


@dataclass(frozen=True)
class DiagramView:
    rungs: list[RungView] = field(default_factory=list)
