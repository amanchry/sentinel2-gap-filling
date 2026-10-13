"""Output time periods (dekads or fixed N-day steps)."""

from __future__ import annotations

import calendar
import datetime as dt
from dataclasses import dataclass


@dataclass(frozen=True)
class Period:
    start: dt.date
    end: dt.date  # inclusive

    @property
    def label(self) -> str:
        return f"{self.start.isoformat()}_{self.end.isoformat()}"

    @property
    def center(self) -> float:
        """Midpoint as a day ordinal (may be x.5)."""
        return (self.start.toordinal() + self.end.toordinal()) / 2.0


def build_periods(start: dt.date, end: dt.date, period: str | int) -> list[Period]:
    """Periods that lie fully inside start..end."""
    out: list[Period] = []
    if period == "dekad":
        year, month = start.year, start.month
        while dt.date(year, month, 1) <= end:
            last = calendar.monthrange(year, month)[1]
            for d0, d1 in ((1, 10), (11, 20), (21, last)):
                p = Period(dt.date(year, month, d0), dt.date(year, month, d1))
                if p.start >= start and p.end <= end:
                    out.append(p)
            year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    else:
        step = int(period)
        if step < 1:
            raise SystemExit("PERIOD must be 'dekad' or a positive number of days.")
        cur = start
        while cur + dt.timedelta(days=step - 1) <= end:
            out.append(Period(cur, cur + dt.timedelta(days=step - 1)))
            cur += dt.timedelta(days=step)
    if not out:
        raise SystemExit("No complete period fits between START and END.")
    return out
