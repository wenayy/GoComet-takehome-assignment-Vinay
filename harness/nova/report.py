"""report: build the world as of a date, run the checks, apply a person's decisions, choose actions per mode."""
from __future__ import annotations

from decimal import Decimal

from . import checks
from .checks import Item
from .money import money, to_date
from .store import Store
from .world import build_world, norm_id

THRESHOLD = Decimal("5000.00")
OUTBOUND_CLASSES = {"overcharge", "duplicate", "waived_but_billed", "late_fee_not_owed"}


def build_report(as_of: str, mode: str, state_dir: str) -> dict:
    store = Store(state_dir)
    world = build_world(store, as_of)
    items = run_checks(world)
    items = apply_feedback(world, items)
    choose_actions(items, mode)
    out = serialise(store, items, as_of, mode)
    return {"schema_version": "1.0", "as_of": as_of, "mode": mode,
            "findings": [i for i in out if i["class"] in OUTBOUND_CLASSES],
            "portfolio": {"cashback": [i for i in out if i["class"] == "cashback"],
                          "expiry": [i for i in out if i["class"] == "expiry"],
                          "late_fee_risk": [i for i in out if i["class"] == "late_fee_risk"]}}


def run_checks(world) -> list[Item]:
    items: list[Item] = []
    for check in (checks.check_overcharge, checks.check_duplicate, checks.check_waivers, checks.check_late_fees):
        items += check(world)
    disputed: dict = {}
    for it in items:
        if it.verdict == "finding" and it.native is not None and it.invoice is not None:
            disputed[it.key["invoice_id"]] = disputed.get(it.key["invoice_id"], Decimal(0)) + it.native
    items += checks.check_late_fee_risk(world, disputed)
    items += checks.check_cashback(world)
    items += checks.check_expiry(world)
    seen, uniq = set(), []
    for it in items:  # one item per key: the first check to claim it
        if it.key_tuple not in seen:
            seen.add(it.key_tuple)
            uniq.append(it)
    return uniq


def _fk(it: dict) -> tuple | None:
    fk = it.get("finding_key") or {}
    if not fk.get("class"):
        return None
    if fk.get("invoice_id"):
        return (fk["class"], norm_id(fk["invoice_id"]))
    if fk.get("contract_id"):
        return (fk["class"],) + tuple(str(fk.get(x)) for x in ("contract_id", "period", "credit_type") if fk.get(x))
    return None


def apply_feedback(world, items: list[Item]) -> list[Item]:
    """close / reject stop exactly the key they name; override_amount replaces its amount. Standing facts (authority,
    accepted rates, precedence, one-off waivers) were already applied where the checks read them."""
    out = []
    by_key = {}
    for fb in world.feedback:
        k = _fk(fb)
        if k:
            by_key.setdefault(k, []).append(fb)
    for it in items:
        k = it.key_tuple
        fbs = by_key.get(k, [])
        drop = False
        for fb in fbs:
            d = fb.get("decision")
            if d in ("close", "reject"):
                drop = True
            elif d == "override_amount":
                amt = ((fb.get("fact") or {}).get("value") or {}).get("amount_usd")
                if amt is not None and it.verdict == "finding":
                    it.amount = Decimal(str(amt))
                    it.learned = {"feedback_item_id": fb["item_id"], "batch": fb.get("_batch")}
        if not drop:
            out.append(it)
    return out


def _unattended_problems(it: Item, conflicts: set) -> list[str]:
    probs = list(it.weak)
    if it.missing_doc:
        probs.append("missing document")
    for lk in getattr(it, "lane_keys", []) or []:
        if lk in conflicts:
            probs.append("open conflict on the lane")
    if it.invoice is not None and it.invoice.scan:
        probs.append("read from a scan")
    return probs


def choose_actions(items: list[Item], mode: str) -> None:
    conflicts = {it.lane_key for it in items if it.verdict == "conflict" and it.lane_key}
    per_key: dict = {}
    for it in items:
        if it.verdict == "finding" and it.outbound in ("dispute", "claim_credit"):
            k = it.key["invoice_id"] if "invoice_id" in it.key else it.key_tuple
            per_key[k] = per_key.get(k, Decimal(0)) + (it.amount or Decimal(0))
    for it in items:
        if it.verdict == "conflict":
            it.action = "escalate"
        elif it.verdict == "cannot_determine":
            it.action = ("execute:" if mode == "autonomous" else "draft:") + "request_document"
        elif it.internal:
            it.action = "internal:" + it.internal
        elif it.outbound:
            k = it.key["invoice_id"] if "invoice_id" in it.key else it.key_tuple
            if per_key.get(k, Decimal(0)) >= THRESHOLD:
                it.action = "human_review"
            elif mode == "autonomous":
                it.action = "human_review" if _unattended_problems(it, conflicts) else "execute:" + it.outbound
            else:
                it.action = "draft:" + it.outbound
        else:
            it.action = "none"
        it.confidence = confidence(it, conflicts)


def confidence(it: Item, conflicts: set) -> float:
    """Probability the verdict and amount are both right, from what the evidence looks like."""
    c = {"conflict": 0.7, "cannot_determine": 0.72}.get(it.verdict, 0.9)
    c = {"expiry": 0.93, "late_fee_risk": 0.88, "cashback": 0.8}.get(it.cls, c) if it.verdict == "finding" else c
    if "pickup date not from tracking" in it.weak:
        c -= 0.12
    if "rate source lacks authority" in it.weak:
        c -= 0.15
    if it.invoice is not None and it.invoice.scan:
        c -= 0.1
    if len([e for e in it.evidence if e.ev()]) < 2:
        c -= 0.1
    if it.learned:
        c = max(c, 0.88)
    return round(min(0.97, max(0.05, c)), 2)


def _state(it: dict) -> list:
    return [it["verdict"], it.get("amount_usd"), it.get("amount_range"), it.get("deadline")]


def serialise(store: Store, items: list[Item], as_of: str, mode: str) -> list[dict]:
    history = store.previous_items(mode)              # {as_of: {key: {"id", "state", "invoice_seen"}}}
    earlier = sorted(d for d in history if d < as_of)
    prev = history[earlier[-1]] if earlier else {}
    prev_as_of = earlier[-1] if earlier else None
    out, snap = [], {}
    for it in items:
        ks = "|".join(it.key_tuple)
        base = f"{it.cls}:" + ":".join(str(v) for v in it.key.values())
        ev = []
        for c in it.evidence:
            e = c.ev()
            if e and e not in ev and len(e["quote"].replace(" ", "")) >= 12:
                ev.append(e)
        d = {"id": base, "class": it.cls, **it.key, "variant": it.variant, "verdict": it.verdict,
             "amount_usd": money(it.amount) if it.verdict == "finding" and it.amount is not None else None,
             "amount_range": [money(x) for x in it.amount_range] if it.amount_range else None,
             "deadline": it.deadline.isoformat() if it.deadline else None, "action": it.action,
             "confidence": it.confidence, "evidence": ev, "missing_doc": it.missing_doc, "supersedes": [],
             "learned_at": it.learned}
        if not ev:
            continue  # nothing we can cite: not reportable
        p = prev.get(ks)
        if p and p["state"] != _state(d):
            n = int(p["id"].rsplit("#", 1)[1]) + 1 if "#" in p["id"] else 2
            d["id"] = f"{base}#{n}"
            d["supersedes"] = [p["id"]]
        elif p:
            d["id"] = p["id"]
        elif prev_as_of and it.invoice is not None and it.invoice.received <= prev_as_of and \
                it.deciding_received and it.deciding_received > prev_as_of:
            d["retro"] = True
        if it.learned:
            d["learned_at"] = it.learned
        snap[ks] = {"id": d["id"], "state": _state(d)}
        out.append(d)
    history[as_of] = snap
    store.save_items(mode, history)
    return out
