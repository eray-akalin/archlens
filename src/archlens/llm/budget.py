"""Per-run budget guard (docs/LLM.md §9).

Before a call, its worst-case cost is reserved; the call is refused (BudgetExceeded) if
spent + reserved + estimate would cross the limit. After the call the reservation is replaced by
the actual cost. Reservations make concurrent sessions safe. Methods never await, so each check
is atomic on the event loop.
"""

from archlens.errors import BudgetExceeded


class BudgetGuard:
    def __init__(self, limit_usd: float) -> None:
        self.limit_usd = limit_usd
        self.spent_usd = 0.0
        self._reserved = 0.0

    @property
    def reserved_usd(self) -> float:
        return self._reserved

    def reserve(self, estimate_usd: float) -> float:
        """Reserve `estimate_usd`; returns it for `settle`/`release`. Raises BudgetExceeded."""
        projected = self.spent_usd + self._reserved + estimate_usd
        if projected > self.limit_usd:
            raise BudgetExceeded(
                f"call would bring the run to ${projected:.4f} (limit ${self.limit_usd:.2f}, "
                f"spent ${self.spent_usd:.4f})"
            )
        self._reserved += estimate_usd
        return estimate_usd

    def settle(self, reservation: float, actual_usd: float) -> None:
        self._reserved = max(self._reserved - reservation, 0.0)
        self.spent_usd += actual_usd

    def release(self, reservation: float) -> None:
        self._reserved = max(self._reserved - reservation, 0.0)
