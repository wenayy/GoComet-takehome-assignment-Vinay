"""What a shipment should have cost: the rate in force on its pickup date, by precedence, or why we can't say.

Ranking (MSA clause 14 and GLOSSARY): signed amendment > MSA; a later amendment beats an earlier one for the lane; an
authorised rate notice replaces Annex A (clause 3.5); a notice against an amendment for the same lane and period is
unranked (clause 13.3) unless a person's precedence decision settles it. Unauthorised notices are ignored for terms.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from .money import D, to_date
from .world import AUTHORISED, Contract, RateRow, World, norm_lane, norm_name


@dataclass
class RateAnswer:
    status: str                         # ok | conflict | missing | unknown
    rate: RateRow | None = None
    minimum: RateRow | None = None
    alternatives: list = field(default_factory=list)   # [(rate, minimum)] when status == conflict
    missing: dict | None = None
    learned: dict | None = None
    note: str = ""


def _band(rows: list[RateRow], kg) -> RateRow | None:
    """The band that contains the chargeable weight: the highest lower bound at or below it."""
    kg = D(kg)
    if kg is None:
        return None
    best = None
    for r in rows:
        if r.minimum:
            continue
        lo = D(r.min_kg) if D(r.min_kg) is not None else Decimal(0)
        hi = D(r.max_kg)
        if lo <= kg and (hi is None or kg <= hi + Decimal("0.4")):
            if best is None or lo > (D(best.min_kg) or Decimal(0)):
                best = r
    return best


def _pick(rows: list[RateRow], kg) -> tuple[RateRow | None, RateRow | None]:
    return _band(rows, kg), next((r for r in rows if r.minimum), None)


def _latest(rows: list[RateRow]) -> list[RateRow]:
    """Among rows of one kind, keep the instrument with the latest effective date."""
    if not rows:
        return []
    best = max(rows, key=lambda r: (r.effective or date.min, r.received))
    return [r for r in rows if r.label == best.label and r.effective == best.effective]


def _feedback_precedence(world: World, contract: str, lane: str, pickup: date) -> dict | None:
    for it in world.feedback:
        f = it.get("fact") or {}
        if it.get("decision") != "resolve_conflict" or f.get("type") != "precedence":
            continue
        sc = (it.get("scope") or {}).get("applies_to") or {}
        if norm_lane(sc.get("lane") or (f.get("subject") or {}).get("lane")) != lane:
            continue
        if (sc.get("contract_id") or (f.get("subject") or {}).get("contract_id")) not in (None, contract):
            continue
        vf = to_date((it.get("scope") or {}).get("valid_from"))
        if vf and pickup < vf:
            continue
        return it
    return None


def _accepted_rates(world: World, contract: str, lane: str, pickup: date) -> tuple[list[RateRow], dict | None]:
    """A person's close + rate_accepted: the named notice's rates count for that lane inside the scope dates."""
    for it in world.feedback:
        f = it.get("fact") or {}
        if f.get("type") != "rate_accepted":
            continue
        scope = it.get("scope") or {}
        ap = scope.get("applies_to") or {}
        if norm_lane(ap.get("lane") or (f.get("subject") or {}).get("lane")) != lane:
            continue
        if (ap.get("contract_id") or (f.get("subject") or {}).get("contract_id")) not in (None, contract):
            continue
        vf, vt = to_date(scope.get("valid_from")), to_date(scope.get("valid_to"))
        if (vf and pickup < vf) or (vt and pickup > vt):
            continue
        val = f.get("value") or {}
        for n in world.notices:
            if n["contract"] != contract or n["lane"] != lane:
                continue
            if val.get("sent_by") and norm_name(val["sent_by"]) != norm_name(n["sender"]):
                continue
            rows = [RateRow(**{**r.__dict__, "authority": AUTHORISED, "source": "feedback",
                               "learned": {"feedback_item_id": it["item_id"], "batch": it.get("_batch")}})
                    for r in n["rows"]]
            return rows, it
    return [], None


def rate_for(world: World, k: Contract, lane: str, pickup: date, kg) -> RateAnswer:
    rows = [r for r in world.rate_rows if r.contract == k.id and r.lane == lane and r.covers(pickup)]
    msa = [r for r in rows if r.source == "msa"]
    amd = _latest([r for r in rows if r.source == "amendment"])
    notices = _latest([r for r in rows if r.source == "rate_notice" and r.authority <= AUTHORISED])
    accepted, fb = _accepted_rates(world, k.id, lane, pickup)
    if accepted:
        notices = accepted

    # a document the contract says applies, that we don't hold
    for m in k.missing_refs:
        lanes = {norm_lane(x) for x in m.get("lanes") or []}
        frm, to = to_date(m.get("applies_from")) or m.get("_effective"), to_date(m.get("applies_to"))
        if lanes and lane not in lanes:
            continue
        if (frm and pickup < frm) or (to and pickup > to):
            continue
        if _held(world, k, m, lane):
            continue
        # a later signed amendment for the lane supersedes the missing annex
        if amd and frm and amd[0].effective and amd[0].effective > frm:
            continue
        return RateAnswer("missing", missing=m)

    base = None
    if amd and notices:
        a, n = _pick(amd, kg), _pick(notices, kg)
        if a[0] and n[0] and D(a[0].rate) != D(n[0].rate):
            pref = _feedback_precedence(world, k.id, lane, pickup)
            if pref:
                use = str(((pref.get("fact") or {}).get("value") or {}).get("use") or "").lower()
                learned = {"feedback_item_id": pref["item_id"], "batch": pref.get("_batch")}
                chosen = n if "notice" in use else a
                # a later signed amendment still beats a person's decision
                if "notice" in use and amd[0].effective and to_date(pref["scope"].get("valid_from")) and \
                        amd[0].effective > to_date(pref["scope"]["valid_from"]):
                    chosen = a
                return RateAnswer("ok", chosen[0], chosen[1], learned=learned)
            return RateAnswer("conflict", alternatives=[a, n])
        base = a if a[0] else n
    elif amd:
        base = _pick(amd, kg)
    elif notices:
        base = _pick(notices, kg)
    if base is None or base[0] is None:
        base = _pick(msa, kg)
        if base[0] is None:
            return RateAnswer("unknown", note=f"no rate for {lane} on {pickup}")
    return RateAnswer("ok", base[0], base[1], learned=base[0].learned)


def _held(world: World, k: Contract, m: dict, lane: str) -> bool:
    """Do we hold the referenced document? Some other document that names it and carries rates for the lane."""
    name = norm_name(m.get("name"))
    if not name:
        return True
    cited_in = m["_cite"].doc
    # an annex that is part of the citing document itself ('Annex A: Express rates', or its rates for the lane)
    own = world.docs.get(cited_in) or {}
    if any(norm_name(line).startswith(name + ":") for p in own.get("pages") or [] for line in p.split("\n")):
        return True
    if any(r.cite.doc == cited_in and r.lane == lane for r in world.rate_rows):
        return True
    for r in world.rate_rows:
        if r.contract == k.id and r.lane == lane and r.cite.doc != cited_in and r.source in ("amendment", "msa") \
                and r.cite.doc != k.ref:
            doc = world.docs.get(r.cite.doc)
            if doc and any(name in norm_name(p) for p in doc.get("pages") or []):
                return True
    return False
