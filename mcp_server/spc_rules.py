"""Statistical Process Control for weekly KPIs (I-MR chart + Western Electric rules).

This is where the Six Sigma background shows: the agent does not "guess" anomalies –
it applies the same control-chart rules a Black Belt would use.

I-MR (individuals / moving range):
    centre line  CL  = mean(x)
    moving range MR  = |x_i − x_{i−1}|
    sigma estimate   = mean(MR) / d2,   d2 = 1.128 for n = 2
    control limits   = CL ± 3·sigma  (equivalent to CL ± 2.66·mean(MR))

Western Electric rules (one side = above or below CL):
    1  one point beyond 3σ
    2  2 of 3 consecutive points beyond 2σ on the same side
    3  4 of 5 consecutive points beyond 1σ on the same side
    4  8 consecutive points on the same side of CL
"""
from __future__ import annotations

from collections.abc import Sequence

D2 = 1.128


def imr_limits(values: Sequence[float]) -> dict:
    x = [float(v) for v in values if v is not None]
    if len(x) < 3:
        raise ValueError("Need at least 3 values to estimate control limits")
    cl = sum(x) / len(x)
    mr = [abs(b - a) for a, b in zip(x, x[1:])]
    sigma = (sum(mr) / len(mr)) / D2
    return {"cl": cl, "sigma": sigma, "ucl": cl + 3 * sigma, "lcl": cl - 3 * sigma}


def _side(value: float, cl: float) -> int:
    return 1 if value > cl else (-1 if value < cl else 0)


def western_electric(values: Sequence[float], cl: float, sigma: float) -> list[dict]:
    """Return one entry per (point, rule) violation. Index refers to `values`."""
    x = [float(v) for v in values]
    z = [(v - cl) / sigma if sigma > 0 else 0.0 for v in x]
    out: list[dict] = []

    def add(i: int, rule: int, text: str) -> None:
        out.append({"index": i, "value": x[i], "rule": rule, "description": text})

    for i in range(len(x)):
        if abs(z[i]) > 3:
            add(i, 1, "point beyond 3 sigma")
        if i >= 2:
            window = z[i - 2 : i + 1]
            for side in (1, -1):
                if sum(1 for v in window if v * side > 2) >= 2 and z[i] * side > 2:
                    add(i, 2, "2 of 3 points beyond 2 sigma, same side")
        if i >= 4:
            window = z[i - 4 : i + 1]
            for side in (1, -1):
                if sum(1 for v in window if v * side > 1) >= 4 and z[i] * side > 1:
                    add(i, 3, "4 of 5 points beyond 1 sigma, same side")
        if i >= 7:
            sides = {_side(v, cl) for v in x[i - 7 : i + 1]}
            if len(sides) == 1 and 0 not in sides:
                add(i, 4, "8 points in a row on the same side of the centre line")
    return out


def assess(values: Sequence[float], baseline: int | None = None) -> dict:
    """Limits from the first `baseline` points (Phase I), rules applied to all points (Phase II)."""
    base = values[:baseline] if baseline else values
    limits = imr_limits(base)
    violations = western_electric(values, limits["cl"], limits["sigma"])
    return {"limits": {k: round(v, 4) for k, v in limits.items()}, "violations": violations,
            "in_control": not violations}
