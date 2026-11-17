"""Deadlines and pauses (design: 设计/合同协议.md §9).

Deadlines count only the Provider's time: from a declared step request to its answer the
clock stops, up to ``authorizeWithin``. Period 1 runs from the receipt, period i from
slot 7(i−1). ``acceptWithin`` runs from slot 5(i).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any, Iterable, Mapping

from .codec import parse_duration
from .slots import Chain
from .template import accept_within, deliverable_of


@dataclass(frozen=True)
class StepWindow:
    """A declared step request and, if answered, when."""

    period: int
    requested: datetime
    answered: datetime | None


def pause(template: Mapping[str, Any], steps: Iterable[StepWindow], period: int, now: datetime) -> timedelta:
    limit = parse_duration(template["deadlines"].get("authorizeWithin", "PT0S")) if template.get(
        "authorizableSteps") else timedelta(0)
    total = timedelta(0)
    for s in steps:
        if s.period != period:
            continue
        end = min(s.answered or now, s.requested + limit)
        total += max(end - s.requested, timedelta(0))
    return total


def start_deadline(template: Mapping[str, Any], chain: Chain) -> datetime | None:
    if chain.effective is None:
        return None
    return chain.effective + parse_duration(template["deadlines"]["startWithin"])


def deliver_deadline(template: Mapping[str, Any], chain: Chain, period: int, steps: Iterable[StepWindow],
                     now: datetime) -> datetime | None:
    base = chain.receipt if period == 1 else chain.period_ts(7, period - 1)
    if base is None:
        return None
    d = deliverable_of(template, period)
    return base + parse_duration(template["deadlines"]["deliverWithin"][d]) + pause(template, steps, period, now)


def accept_deadline(template: Mapping[str, Any], card: Mapping[str, Any], chain: Chain, period: int) -> datetime | None:
    anchored = chain.period_ts(5, period)
    if anchored is None:
        return None
    return anchored + parse_duration(accept_within(template, card, deliverable_of(template, period)))


@dataclass
class Status:
    """Where a card stands at ``now`` and which dispute grounds exist."""

    phase: str
    period: int | None
    deadline: datetime | None
    client_grounds: list[str]
    provider_grounds: list[str]


def status(template: Mapping[str, Any], card: Mapping[str, Any], chain: Chain, now: datetime,
           steps: Iterable[StepWindow] = (), vouchers: Iterable[Mapping[str, Any]] = ()) -> Status:
    steps, vouchers = list(steps), list(vouchers)
    n = len(template["deliverables"])
    client, provider = [], []
    if "termination" in card["fulfillment"]:
        return Status("terminated", None, None, client, provider)
    if chain.effective is None:
        return Status("forming", None, None, client, provider)
    if chain.receipt is None:
        dl = start_deadline(template, chain)
        if now > dl:
            client.append("startWithin passed without a receipt")
            return Status("lapsed", None, dl, client, provider)
        return Status("awaiting-start", None, dl, client, provider)
    respond = parse_duration(template["deadlines"]["respondWithin"])
    paid = {v["period"] for v in vouchers if v.get("purpose") == "installment"}
    for j, t7 in enumerate(chain.periods.get(7, []), start=1):
        due = Decimal(str(card["derived"]["periods"][j - 1]["amount"])) > 0
        if due and j not in paid and now > t7 + respond:
            provider.append(f"period {j} not paid within respondWithin of slot 7")
    if chain.accepted == n:
        return Status("completed", None, None, client, provider)
    i = chain.accepted + 1
    dl = deliver_deadline(template, chain, i, steps, now)
    anchored = chain.period_ts(5, i)
    if anchored is None:
        if now > dl:
            client.append(f"deliverWithin of period {i} passed without slot 5")
        limit = parse_duration(template["deadlines"].get("authorizeWithin", "PT0S"))
        for s in steps:
            if s.period == i and s.answered is None and now > s.requested + limit:
                provider.append(f"step request of period {i} unanswered within authorizeWithin")
        return Status("delivering", i, dl, client, provider)
    if anchored > dl:
        client.append(f"slot 5 of period {i} came after deliverWithin")
    periods = card["fulfillment"].get("periods", [])
    report = periods[i - 1].get("report") if len(periods) >= i else None
    if report is not None and any(r["status"] != "pass" for r in report["results"]):
        client.append(f"the report of period {i} fails")
        return Status("disputed", i, None, client, provider)
    adl = accept_deadline(template, card, chain, i)
    if now > adl:
        provider.append(f"acceptWithin of period {i} passed without slot 7 or a failing report")
    return Status("accepting", i, adl, client, provider)


__all__ = ["StepWindow", "pause", "start_deadline", "deliver_deadline", "accept_deadline", "Status", "status"]
