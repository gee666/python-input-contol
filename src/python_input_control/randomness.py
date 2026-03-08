from __future__ import annotations

import hashlib
import random
from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@runtime_checkable
class RandomSource(Protocol):
    def random(self) -> float: ...
    def uniform(self, a: float, b: float) -> float: ...
    def gauss(self, mu: float, sigma: float) -> float: ...
    def randint(self, a: int, b: int) -> int: ...


@dataclass
class SeededRandom(RandomSource):
    seed: int | str | bytes | None = None

    def __post_init__(self) -> None:
        self._random = random.Random(_normalize_seed(self.seed))

    def random(self) -> float:
        return self._random.random()

    def uniform(self, a: float, b: float) -> float:
        return self._random.uniform(a, b)

    def gauss(self, mu: float, sigma: float) -> float:
        return self._random.gauss(mu, sigma)

    def randint(self, a: int, b: int) -> int:
        return self._random.randint(a, b)


def bounded_gauss(rng: RandomSource, mu: float, sigma: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(maximum, rng.gauss(mu, sigma)))


def _normalize_seed(seed: int | str | bytes | None) -> int | None:
    if seed is None:
        return None
    if isinstance(seed, int):
        return seed
    if isinstance(seed, str):
        seed = seed.encode("utf-8")
    digest = hashlib.sha256(seed).digest()
    return int.from_bytes(digest[:8], byteorder="big", signed=False)
