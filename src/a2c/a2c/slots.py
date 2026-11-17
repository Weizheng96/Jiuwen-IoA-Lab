"""Template signatures and the card's signature slots (design: 设计/合同协议.md §5, 设计/线上编码.md §3)."""
from __future__ import annotations

import copy
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any, Mapping, Protocol

from .card import card_digest
from .codec import digest, parse_duration, parse_time
from .errors import BAD_SIGNATURE, CARD_INVALID, DEADLINE_PASSED, INVALID_OBJECT, OUT_OF_ORDER, fail
from .jws import Signer, header_of, sign, verify
from .objects import object_digest, verify_signed
from . import shape
from .template import deliverable_names, template_digest


class Keys(Protocol):
    """Resolves keys published in a JWKS (providers, markets, certified services, payment providers)."""

    def jwk(self, party: str, kid: str | None) -> dict: ...


# ── template ────────────────────────────────────────────────────────────

def sign_template(template: Mapping[str, Any], signer: Signer, role: str, ts: datetime) -> dict:
    out = copy.deepcopy(dict(template))
    out["templateDigest"] = template_digest(out)
    sigs = dict(out.get("signatures") or {})
    sigs[role] = sign(out["templateDigest"], signer, role, ts)
    out["signatures"] = sigs
    return out


def market_of(template: Mapping[str, Any]) -> str:
    """The Market that endorsed the template (its signature's a2c-signer)."""
    sig = (template.get("signatures") or {}).get("market")
    if sig is None:
        fail(BAD_SIGNATURE, "template has no Market signature")
    return header_of(sig)["a2c-signer"]


def verify_template(template: Mapping[str, Any], keys: Keys, *, endorsed: bool = True) -> dict:
    """Verify the Provider (and Market) signatures; returns the headers by role."""
    td = template_digest(template)
    sigs = template.get("signatures") or {}
    out = {}
    h = header_of(sigs.get("provider"))
    out["provider"] = verify(sigs["provider"], td, keys.jwk(template["provider"], h.get("kid")), slot="provider",
                             signer=template["provider"], expect_kid=True)
    if endorsed:
        h = header_of(sigs.get("market"))
        out["market"] = verify(sigs["market"], td, keys.jwk(h["a2c-signer"], h.get("kid")), slot="market",
                               signer=h["a2c-signer"], expect_kid=True)
    return out


# ── card slots ──────────────────────────────────────────────────────────

def period_manifest(card: Mapping[str, Any], period: int) -> Mapping[str, Any]:
    try:
        return card["fulfillment"]["periods"][period - 1]["manifest"]
    except (KeyError, IndexError, TypeError):
        fail(OUT_OF_ORDER, f"no manifest for period {period}")


def period_report(card: Mapping[str, Any], period: int) -> Mapping[str, Any]:
    try:
        return card["fulfillment"]["periods"][period - 1]["report"]
    except (KeyError, IndexError, TypeError):
        fail(OUT_OF_ORDER, f"no report for period {period}")


def sign_slot(card: Mapping[str, Any], slot: int, signer: Signer, ts: datetime, *, period: int | None = None) -> dict:
    """Append the signature for ``slot`` (and ``period`` for slots 4–7)."""
    out = copy.deepcopy(dict(card))
    cd = card_digest(out)
    if slot in (1, 2, 3):
        if period is not None:
            fail(INVALID_OBJECT, "slots 1–3 have no period")
        if out["signatures"][str(slot)] is not None:
            fail(OUT_OF_ORDER, f"slot {slot} is already signed")
        out["signatures"][str(slot)] = sign(cd, signer, str(slot), ts)
        return out
    if "termination" in out["fulfillment"]:
        fail(OUT_OF_ORDER, "the card is terminated")
    if period is None or len(out["signatures"][str(slot)]) != period - 1:
        fail(OUT_OF_ORDER, f"slot {slot}({period}) is not the next one")
    manifest = digest(period_manifest(out, period))
    report = digest(period_report(out, period)) if slot == 7 else None
    out["signatures"][str(slot)].append(sign(cd, signer, str(slot), ts, period=period,
                                             manifest=manifest if slot != 7 else None, report=report))
    return out


def _party_jwk(template, card, slot: int, header: Mapping[str, Any], keys: Keys, market: str | None):
    """(expected signer id, jwk, expect_kid) for a card slot."""
    if slot in (1, 7):
        who = card["signers"][f"slot{slot}"]
        party = card[who]
        return party["id"], party["jwk"], False
    if slot == 6:
        return card["client"]["id"], card["client"]["jwk"], False
    if slot in (2, 4):
        return template["provider"], keys.jwk(template["provider"], header.get("kid")), True
    return market, keys.jwk(market, header.get("kid")), True


@dataclass
class Chain:
    """What a verified card says: formation times and each period's slot times."""

    effective: datetime | None = None
    receipt: datetime | None = None
    ts: dict[int, datetime] = field(default_factory=dict)              # slots 1–3
    periods: dict[int, list[datetime]] = field(default_factory=dict)   # slots 4–7 → per period
    market: str | None = None
    terminated: datetime | None = None

    def period_ts(self, slot: int, period: int) -> datetime | None:
        times = self.periods.get(slot, [])
        return times[period - 1] if len(times) >= period else None

    @property
    def accepted(self) -> int:
        """Number of periods with slot 7."""
        return len(self.periods.get(7, []))


def verify_card(template: Mapping[str, Any], card: Mapping[str, Any], keys: Keys, *,
                request_ts: datetime | None = None) -> Chain:
    """Verify every signature on the card, the receipt and the slot order rules."""
    cd = card_digest(card)
    formation = template["formation"]
    sigs = card["signatures"]
    market = market_of(template)
    respond = parse_duration(template["deadlines"]["respondWithin"])
    chain = Chain(market=market)

    for slot in (1, 2, 3):
        sig = sigs[str(slot)]
        if sig is None:
            continue
        if slot == 2 and formation == "preapproval" or slot == 3 and formation != "proposal":
            fail(OUT_OF_ORDER, f"a {formation} card has no slot {slot}")
        h = header_of(sig)
        who, jwk, kid = _party_jwk(template, card, slot, h, keys, market)
        verify(sig, cd, jwk, slot=str(slot), signer=who, expect_kid=kid)
        chain.ts[slot] = datetime.fromisoformat(h["a2c-ts"][:-1] + "+00:00")

    ts = chain.ts
    if formation == "countersign" and 2 in ts:
        if 1 not in ts or ts[2] < ts[1]:
            fail(OUT_OF_ORDER, "slot 2 needs slot 1 first")
        if ts[2] > ts[1] + respond:
            fail(DEADLINE_PASSED, "slot 2 came after respondWithin")
    if formation == "proposal":
        if 3 in ts and (2 not in ts or ts[3] < ts[2]):
            fail(OUT_OF_ORDER, "slot 3 needs slot 2 first")
        if 1 in ts and (3 not in ts or ts[1] < ts[3]):
            fail(OUT_OF_ORDER, "slot 1 adopts a card that already has slot 3")
        if 1 in ts and ts[1] > ts[3] + respond:
            fail(DEADLINE_PASSED, "slot 1 came after respondWithin from slot 3")
        if request_ts is not None:
            if 2 in ts and ts[2] < request_ts:
                fail(OUT_OF_ORDER, "slot 2 precedes the Request Card")
            if 3 in ts and ts[3] > request_ts + respond:      # the drafted card counts as answered at slot 3
                fail(DEADLINE_PASSED, "slot 3 came after respondWithin from the Request Card")
    chain.effective = ts.get(2) if formation == "countersign" else ts.get(1)

    receipt = card["fulfillment"].get("receipt")
    if receipt is not None:
        if chain.effective is None:
            fail(OUT_OF_ORDER, "a receipt needs an effective card")
        h = header_of(receipt.get("signature"))
        verify_signed(receipt, keys.jwk(template["provider"], h.get("kid")), signer=template["provider"],
                      expect_kid=True)
        if receipt["cardDigest"] != cd:
            fail(INVALID_OBJECT, "receipt is for another card")
        chain.receipt = datetime.fromisoformat(h["a2c-ts"][:-1] + "+00:00")
        if chain.receipt < chain.effective:
            fail(OUT_OF_ORDER, "receipt before the effective time")
        if chain.receipt > chain.effective + parse_duration(template["deadlines"]["startWithin"]):
            fail(DEADLINE_PASSED, "receipt after startWithin")

    n = len(deliverable_names(template))
    counts = [len(sigs[str(s)]) for s in (4, 5, 6, 7)]
    if counts[0] > n or not counts[0] >= counts[1] >= counts[2] >= counts[3] or counts[0] > counts[3] + 1:
        fail(OUT_OF_ORDER, f"slot counts {counts} break 4(i) ≥ 5(i) ≥ 6(i) ≥ 7(i) and 4(i+1) after 7(i)")
    if counts[0] and chain.receipt is None:
        fail(OUT_OF_ORDER, "slot 4 needs a receipt")
    for i in range(1, counts[0] + 1):
        m = period_manifest(card, i)
        if m.get("cardDigest") != cd or m.get("period") != i:
            fail(INVALID_OBJECT, f"manifest of period {i} names another card or period")
    for slot in (4, 5, 6, 7):
        chain.periods[slot] = []
        for i, sig in enumerate(sigs[str(slot)], start=1):
            h = header_of(sig)
            who, jwk, kid = _party_jwk(template, card, slot, h, keys, market)
            manifest = digest(period_manifest(card, i))
            report = digest(period_report(card, i)) if slot == 7 else None
            verify(sig, cd, jwk, slot=str(slot), signer=who, period=i, manifest=manifest if slot != 7 else None,
                   report=report, expect_kid=kid)
            chain.periods[slot].append(datetime.fromisoformat(h["a2c-ts"][:-1] + "+00:00"))
    for i in range(1, counts[0] + 1):
        before = chain.receipt if i == 1 else chain.period_ts(7, i - 1)
        seq = [before] + [chain.period_ts(s, i) for s in (4, 5, 6, 7)]
        seq = [t for t in seq if t is not None]
        if seq != sorted(seq):
            fail(OUT_OF_ORDER, f"period {i}: slot times out of order")
    term = card["fulfillment"].get("termination")
    if term is not None:
        shape.members(term, ('termination',), ('quote',), where='termination record')
        notice = term['termination']
        if not isinstance(notice, Mapping) or notice.get('kind') != 'termination' or notice.get('cardDigest') != cd:
            fail(CARD_INVALID, 'the termination notice must name this original card')
        h = verify_signed(notice,card['client']['jwk'],signer=card['client']['id'])
        chain.terminated = parse_time(h['a2c-ts'])
        if chain.effective is None or chain.terminated < chain.effective or chain.accepted >= len(template['deliverables']):
            fail(OUT_OF_ORDER, 'termination requires effective, unaccepted work')
        if 'terminationQuoteDigest' in notice:
            quote = term.get('quote')
            if (not isinstance(quote, Mapping) or quote.get('kind') != 'termination-quote'
                    or quote.get('cardDigest') != cd or object_digest(quote) != notice['terminationQuoteDigest']):
                fail(CARD_INVALID, 'the termination does not accept this exact quote')
            qh = verify_signed(quote,keys.jwk(template['provider'],header_of(quote.get('signature',{})).get('kid')),
                               signer=template['provider'],expect_kid=True)
            period = chain.accepted + 1
            if quote['period'] != period or not 0 <= Decimal(str(quote['fee'])) <= Decimal(str(card['derived']['periods'][period-1]['amount'])):
                fail(CARD_INVALID, 'the quote must cover the first unaccepted period within its amount')
            quoted = parse_time(qh['a2c-ts'])
            if not quoted <= chain.terminated <= quoted + respond:
                fail(CARD_INVALID, 'the signed termination is outside the quote response window')
        later = [t for s in (4, 5, 6, 7) for t in chain.periods[s] if t > chain.terminated]
        if later:
            fail(OUT_OF_ORDER, "slots 4–7 after the termination")
    return chain


__all__ = ["Keys", "sign_template", "market_of", "verify_template", "period_manifest", "period_report", "sign_slot",
           "Chain", "verify_card"]
