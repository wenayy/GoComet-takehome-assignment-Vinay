"""The six checks, as GLOSSARY.md defines them. Each returns Items: a verdict, an amount and the citations behind it.

Amounts are worked in the invoice's own currency and converted to USD once per finding. Anything that would make an
unattended action unsafe is recorded on the item (`weak`), and the action step decides what that means per mode.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal

from .money import D, add_days, add_years, ceil_div, quarter_bounds, quarter_of, r2, to_date
from .pricing import RateAnswer, rate_for
from .world import AUTHORISED, Cite, Contract, Invoice, Line, World, cite, norm_awb, norm_id, norm_name

ZERO = Decimal("0")
STRONG_PICKUP = ("tracking_export", "shipment_status")
FREIGHT, FUEL = "FRT", "FUEL"
PER_KG = ("per_kg",)


@dataclass
class Item:
    cls: str
    key: dict
    verdict: str = "finding"
    amount: Decimal | None = None
    amount_range: list | None = None
    deadline: date | None = None
    evidence: list = field(default_factory=list)
    missing_doc: dict | None = None
    outbound: str | None = None          # dispute | claim_credit | request_document
    internal: str | None = None          # alert | payment_reminder | track_credit
    lane_key: tuple | None = None        # (contract, lane, period) an open conflict blocks
    weak: list = field(default_factory=list)
    learned: dict | None = None
    retro: bool = False
    variant: str | None = None
    invoice: Invoice | None = None
    native: Decimal | None = None        # amount in the invoice currency
    deciding_received: str | None = None  # when the document that decides it arrived

    @property
    def key_tuple(self) -> tuple:
        return (self.cls,) + tuple(str(v) for v in self.key.values())

    def cite(self, *cs) -> None:
        for c in cs:
            if c is not None and c.quote and c not in self.evidence:
                self.evidence.append(c)


# ---- shared helpers ---------------------------------------------------------------------------------------------------
def to_usd(world: World, inv: Invoice, native: Decimal, k: Contract | None) -> tuple[Decimal | None, Cite | None]:
    if inv.currency == "USD":
        return r2(native), None
    if inv.currency == "INR" and k is not None and inv.date:
        fx = k.fx.get(inv.date.strftime("%Y-%m"))
        if fx:
            return r2(native / fx[0]), fx[1]
    return None, None


def pickup_of(world: World, awb: str | None, line: Line | None) -> tuple[date | None, str, Cite | None]:
    """GLOSSARY order: tracking PICKED_UP, carrier 'picked up' email, AWB date, the invoice's ship date."""
    if awb:
        base = norm_awb(awb).split("-H")[0]
        evs = world.events.get(norm_awb(awb), []) + (world.events.get(base, []) if base != norm_awb(awb) else [])
        for kind in STRONG_PICKUP:
            for e in evs:
                if e["_kind"] == kind and str(e["event"]).upper() in ("PICKED_UP", "PICKUP", "COLLECTED") and e.get("date"):
                    return to_date(e["date"]), kind, cite(e["_ref"], e)
        for s in world.shipments.get(norm_awb(awb), []) + world.shipments.get(base, []):
            if s.get("_kind") == "air_waybill" and s.get("date"):
                return to_date(s["date"]), "air_waybill", cite(s["_ref"], s)
    if line and line.pickup:
        return line.pickup, "invoice", line.cite
    return None, "", None


def chargeable_kg(world: World, awb: str | None, billed) -> tuple[Decimal | None, Cite | None]:
    """Billed weight, unless the AWB shows a lower chargeable weight (clause 3.3)."""
    billed = D(billed)
    if not awb or "-H" in awb:
        return billed, None
    for s in world.shipments.get(norm_awb(awb), []):
        if s.get("_kind") != "air_waybill":
            continue
        cw = D(s.get("chargeable_weight"))
        actual = D(s.get("actual_weight"))
        vol = ZERO
        for d in s.get("dimensions") or []:
            l, w_, h, n = D(d.get("l")), D(d.get("w")), D(d.get("h")), D(d.get("count")) or Decimal(1)
            if l and w_ and h:
                vol += l * w_ * h * n / Decimal(6000)
        if cw is None and actual is not None:
            raw = max(actual, vol)
            cw = (raw * 2).to_integral_value(rounding="ROUND_CEILING") / 2
        if cw is not None and billed is not None and cw < billed:
            return cw, cite(s["_ref"], s)
    return billed, None


def dg_status(world: World, awb: str | None) -> str | None:
    """'dg' (dangerous goods other than dry ice), 'dry_ice' (dry ice only), 'none' (declared non-DG) or None."""
    if not awb:
        return None
    uns, seen = set(), False
    for s in world.shipments.get(norm_awb(awb), []):
        if s.get("un_numbers"):
            seen = True
            uns |= {re.sub(r"\D", "", str(u)) for u in s["un_numbers"]}
        elif s.get("_kind") == "dg_declaration":
            seen = True
    if not seen:
        return None
    if uns - {"1845", ""}:
        return "dg"
    return "dry_ice" if "1845" in uns else "none"


def dg_cite(world: World, awb: str) -> Cite | None:
    for s in world.shipments.get(norm_awb(awb), []):
        if s.get("_kind") == "dg_declaration" and s.get("quote"):
            return cite(s["_ref"], s)
    return None


def events_of(world: World, awb: str, *names) -> list[dict]:
    names = {n.upper() for n in names}
    return [e for e in world.events.get(norm_awb(awb), []) if str(e["event"]).upper() in names and e.get("date")]


def find_line(world: World, ref: str, needle: str) -> Cite | None:
    """The first line of a document that contains `needle`: a citation when the extractor gave none."""
    doc = world.docs.get(ref)
    for i, page in enumerate((doc or {}).get("pages") or [], 1):
        for line in page.split("\n"):
            if needle in line and len(line.replace(" ", "")) >= 12:
                return Cite(ref, i, line)
    return None


# ---- 1. overcharge ----------------------------------------------------------------------------------------------------
@dataclass
class Priced:
    line: Line
    correct: Decimal | None
    why: list = field(default_factory=list)   # Cites


def _fee(k: Contract, code: str) -> tuple[Decimal | None, dict | None]:
    a = k.fees.get(code)
    return (D(a.get("fee")), a) if a else (None, None)


def _price_shipment(world: World, k: Contract, lines: list[Line], rate: RateAnswer | None, kg, pickup: date | None
                    ) -> list[Priced]:
    out: list[Priced] = []
    freight_correct = None
    for ln in lines:
        if ln.code == FREIGHT and rate and rate.status == "ok" and kg is not None:
            amt = r2(D(rate.rate.rate) * kg)
            why = [rate.rate.cite]
            if rate.minimum and D(rate.minimum.rate) is not None and amt < D(rate.minimum.rate):
                amt = r2(D(rate.minimum.rate))
                why.append(rate.minimum.cite)
            freight_correct = amt
            out.append(Priced(ln, amt, why))
    for ln in lines:
        code = ln.code
        if code == FREIGHT:
            continue
        p = Priced(ln, None)
        if code == FUEL and pickup is not None:
            fuel = k.fuel.get(pickup.strftime("%Y-%m"))
            base = freight_correct if freight_correct is not None else D(ln.base)
            if fuel and base is not None:
                p.correct = r2(fuel[0] / Decimal(100) * base)
                p.why.append(fuel[1])
        elif code in ("SEC", "PEAK") and kg is not None:
            fee, a = _fee(k, code)
            if fee is not None:
                months = a.get("months")
                if months and pickup is not None and pickup.month not in [int(m) for m in months]:
                    p.correct = ZERO
                else:
                    p.correct = r2(fee * kg)
                p.why.append(a["_cite"])
        elif code == "DG":
            st = dg_status(world, ln.awb)
            fee, a = _fee(k, "DG")
            if st in ("dry_ice", "none"):
                p.correct = ZERO
                p.why += [k.clause("dry_ice", "dg"), dg_cite(world, ln.awb)]
            elif st == "dg" and fee is not None:
                p.correct = fee
                p.why.append(a["_cite"])
        elif code == "DRYICE":
            st = dg_status(world, ln.awb)
            fee, a = _fee(k, "DRYICE")
            if st == "dg":
                p.correct = ZERO               # clause 5.3: only the DG fee
                p.why += [k.clause("dry_ice", "dg"), dg_cite(world, ln.awb)]
            elif fee is not None:
                p.correct = fee                # once per shipment, never per package or kg
                p.why.append(a["_cite"])
        elif code == "STOR" and ln.awb:
            fee, a = _fee(k, "STOR")
            arr = events_of(world, ln.awb, "ARRIVED_FACILITY")
            rel = events_of(world, ln.awb, "DELIVERED", "COLLECTED_BY_CONSIGNEE")
            free = k.terms.get("storage_free_days")
            if fee is not None and arr and rel and free is not None:
                a_d = max(to_date(e["date"]) for e in arr)
                r_d = min(to_date(e["date"]) for e in rel)
                n = max(0, (r_d - a_d).days - int(free))
                p.correct = r2(fee * n)
                p.why += [a["_cite"], k.clause("storage"), cite(arr[-1]["_ref"], arr[-1]), cite(rel[0]["_ref"], rel[0])]
        elif code == "DLV" and ln.awb:
            fee, a = _fee(k, "DLV")
            if events_of(world, ln.awb, "COLLECTED_BY_CONSIGNEE") and not events_of(world, ln.awb, "DELIVERED"):
                p.correct = ZERO
                e = events_of(world, ln.awb, "COLLECTED_BY_CONSIGNEE")[0]
                p.why += [k.clause("delivery"), cite(e["_ref"], e)]
            elif events_of(world, ln.awb, "DELIVERED") and fee is not None:
                p.correct = fee
                p.why.append(a["_cite"])
        elif code == "CUSTOMS" and ln.awb:
            fee, a = _fee(k, "CUSTOMS")
            holds = [e for e in world.events.get(norm_awb(ln.awb), []) if e.get("carrier_fault") is not None]
            if any(e["carrier_fault"] for e in holds):
                p.correct = ZERO
                e = next(e for e in holds if e["carrier_fault"])
                p.why += [k.clause("customs"), cite(e["_ref"], e)]
            elif holds and fee is not None:
                p.correct = fee
        elif code in k.fees and code not in ("LATE",):
            fee, a = _fee(k, code)
            basis = a.get("basis")
            qty = D(ln.qty) or Decimal(1)
            if fee is not None and basis in ("per_shipment", "per_month", "per_day"):
                p.correct = r2(fee * (Decimal(1) if basis == "per_shipment" else qty))
                p.why.append(a["_cite"])
        out.append(p)
    return out


def _variant_of(diffs: list[Priced]) -> str:
    codes = {p.line.code for p in diffs}
    if codes & {"DG", "DRYICE"}:
        return "OC-02"
    return "OC-01"


def check_overcharge(world: World) -> list[Item]:
    items = []
    for iid, copies in world.invoices.items():
        inv = copies[0]
        k = world.contract_of(inv)
        if k is None:
            continue
        by_awb: dict[str, list[Line]] = {}
        for ln in inv.lines:
            if ln.code in ("LATE", "TAX", "DUTY"):
                continue
            by_awb.setdefault(ln.awb or f"#{ln.idx}", []).append(ln)
        priced: list[Priced] = []
        conflict = missing = None
        weak: list[str] = []
        lane_keys = []
        learned = None
        alt_priced: list[list[Priced]] = []
        deciding = []
        for awb, lines in by_awb.items():
            real = awb if not awb.startswith("#") else None
            fl = next((ln for ln in lines if ln.code == FREIGHT), None)
            pickup, src, pcite = pickup_of(world, real, fl or lines[0])
            kg, kg_cite = chargeable_kg(world, real, fl.qty if fl else None)
            rate = None
            if fl and fl.lane and pickup:
                rate = rate_for(world, k, fl.lane, pickup, kg)
                lane_keys.append((k.id, fl.lane))
            if rate and rate.status == "missing":
                missing = (rate, fl, pickup)
                continue
            if rate and rate.status == "conflict":
                conflict = (rate, fl, pickup)
                for alt in rate.alternatives:
                    ra = RateAnswer("ok", alt[0], alt[1])
                    alt_priced.append(_price_shipment(world, k, lines, ra, kg, pickup))
                continue
            ps = _price_shipment(world, k, lines, rate, kg, pickup)
            if any(p.correct is not None and D(p.line.amount) is not None and D(p.line.amount) > p.correct
                   for p in ps):
                if src not in STRONG_PICKUP:
                    weak.append("pickup date not from tracking")
                for p in ps:
                    if p.correct is not None and D(p.line.amount) is not None and D(p.line.amount) > p.correct:
                        p.why += [pcite, kg_cite]
                        if rate and rate.status == "ok" and p.line.code in (FREIGHT, FUEL):
                            if rate.learned:
                                learned = rate.learned
                            deciding.append(rate.rate.received)
                            if rate.rate.authority > AUTHORISED:
                                weak.append("rate source lacks authority")
            priced += ps
        if missing:
            rate, fl, pickup = missing
            m = rate.missing
            cat = m.get("category") if m.get("category") in ("rates", "treasury", "payment_terms") else "rates"
            holder = _holder(k, cat, "carrier")
            it = Item("overcharge", {"invoice_id": iid}, verdict="cannot_determine", outbound="request_document",
                      missing_doc={"name": m.get("name"), "holder": holder}, invoice=inv, variant="XC-02")
            it.cite(m["_cite"], fl.cite if fl else None)
            items.append(it)
            continue
        if conflict:
            rate, fl, pickup = conflict
            billed = sum((D(p.line.amount) or ZERO) for p in alt_priced[0] if p.correct is not None)
            totals = []
            for ap in alt_priced:
                totals.append(sum((D(p.line.amount) or ZERO) - p.correct for p in ap if p.correct is not None))
            lo, hi = min(totals), max(totals)
            lo_u, _ = to_usd(world, inv, max(lo, ZERO), k)
            hi_u, _ = to_usd(world, inv, max(hi, ZERO), k)
            if hi > 0 and lo_u is not None:
                it = Item("overcharge", {"invoice_id": iid}, verdict="conflict", amount_range=[lo_u, hi_u],
                          invoice=inv, variant="XC-01", lane_key=(k.id, fl.lane))
                it.cite(fl.cite, rate.alternatives[0][0].cite, rate.alternatives[1][0].cite,
                        k.clause("unranked", "precedence"))
                items.append(it)
            continue
        diffs = [p for p in priced if p.correct is not None and D(p.line.amount) is not None
                 and D(p.line.amount) > p.correct]
        native = sum((D(p.line.amount) - p.correct for p in diffs), ZERO)
        if native <= 0:
            continue
        usd, fx_cite = to_usd(world, inv, native, k)
        if usd is None:
            holder = _holder(k, "treasury", "carrier")
            it = Item("overcharge", {"invoice_id": iid}, verdict="cannot_determine", outbound="request_document",
                      missing_doc={"name": "Annex F", "holder": holder}, invoice=inv)
            it.cite(diffs[0].line.cite)
            items.append(it)
            continue
        it = Item("overcharge", {"invoice_id": iid}, amount=usd, native=native, outbound="dispute", invoice=inv,
                  weak=sorted(set(weak)), learned=learned, variant=_variant_of(diffs),
                  lane_key=None, deciding_received=max(deciding) if deciding else None)
        it.lane_keys = lane_keys
        for p in diffs:
            it.cite(p.line.cite)
        for p in diffs:
            it.cite(*p.why)
        it.cite(k.clause("pricing_date"), fx_cite)
        items.append(it)
    return items


def _holder(k: Contract, category: str, side: str) -> dict:
    party = k.carrier_code if side == "carrier" else k.shipper_code
    people = [p for p in k.persons(category) if not party or (p.party or "").upper() == (party or "").upper()]
    if not people:
        people = [p for p in k.persons("notices") if not party or (p.party or "").upper() == (party or "").upper()]
    h = {"party": party} if party else {}
    if people:
        h |= {"person": people[0].name, "role": people[0].title}
    return h or {"party": "carrier"}


# ---- 2. duplicate -----------------------------------------------------------------------------------------------------
def _credited(world: World, iid: str) -> Decimal:
    tot = ZERO
    for c in world.credits:
        if iid in c.references and (c.credit_type in (None, "billing", "other")):
            tot += abs(D(c.total) or ZERO)
    return tot


def _paid_or_scheduled(world: World, iid: str) -> tuple[bool, Cite | None]:
    p = world.payments.get(iid)
    if not p:
        return False, None
    status = str(p[0].get("status") or "").lower()
    return (("paid" in status and "unpaid" not in status) or "sched" in status or bool(p[0].get("paid_date"))), p[1]


def check_duplicate(world: World) -> list[Item]:
    items = []
    flagged: set = set()
    # the same invoice number sent again, asking for payment of a copy already paid
    for iid, copies in world.invoices.items():
        if len(copies) < 2:
            continue
        for later in copies[1:]:
            if later.is_copy or not later.requests_payment:
                continue
            paid, pc = _paid_or_scheduled(world, iid)
            if not paid:
                continue
            k = world.contract_of(later)
            native = D(later.total) or sum((D(ln.amount) or ZERO for ln in later.lines), ZERO)
            native -= _credited(world, iid)
            usd, fx = to_usd(world, later, native, k)
            if usd is None or usd <= 0:
                continue
            it = Item("duplicate", {"invoice_id": iid}, amount=usd, native=native, outbound="dispute",
                      invoice=later, variant="DU-01")
            ask = _payment_ask(world, later)
            it.cite(pc, ask, later.total_cite, fx)
            items.append(it)
            flagged.add(iid)
            break
    # the same shipment charge on two invoice numbers: flag the later invoice
    seen: dict[tuple, Line] = {}
    owner: dict[tuple, Invoice] = {}
    dup_lines: dict[str, list[tuple[Line, Line]]] = {}
    ordered = sorted((copies[0] for copies in world.invoices.values()),
                     key=lambda i: (i.date or date.min, i.received_at))
    for inv in ordered:
        for ln in inv.lines:
            key = _line_key(inv, ln)
            if key is None:
                continue
            if key in seen and owner[key].id != inv.id:
                dup_lines.setdefault(inv.id, []).append((ln, seen[key]))
            else:
                seen.setdefault(key, ln)
                owner.setdefault(key, inv)
    for iid, pairs in dup_lines.items():
        if iid in flagged:
            continue
        inv = world.invoices[iid][0]
        k = world.contract_of(inv)
        overlap = sum((D(ln.amount) or ZERO for ln, _ in pairs), ZERO)
        native = overlap - _credited(world, iid)
        if native <= 0:
            continue
        usd, fx = to_usd(world, inv, native, k)
        if usd is None:
            continue
        it = Item("duplicate", {"invoice_id": iid}, amount=usd, native=native, outbound="dispute", invoice=inv,
                  variant="DU-02")
        for ln, first in pairs:
            it.cite(ln.cite, first.cite)
        it.cite(fx)
        items.append(it)
    return items


def _line_key(inv: Invoice, ln: Line) -> tuple | None:
    if not ln.code or ln.code in ("LATE", "TAX", "DUTY"):
        return None
    if ln.awb:
        return ("awb", inv.contract, ln.awb, ln.code)
    ref = str(ln.reference or "").strip()
    if inv.kind == "statement" and re.match(r"\d{4}-\d{2}", ref):
        return ("month", inv.contract, ref[:7], ln.code)
    return None


def _payment_ask(world: World, inv: Invoice) -> Cite | None:
    for ins in world.instructions:
        if ins["_email"] == inv.email_ref and ins.get("quote") and not ins.get("quoted"):
            return cite(ins["_ref"], ins)
    if inv.email_cite:
        return inv.email_cite
    doc = world.docs.get(inv.email_ref)
    if doc:
        for line in doc["pages"][0].split("\n"):
            if re.search(r"\bpay", line, re.I) and len(line.replace(" ", "")) >= 12 and not line.startswith("Subject"):
                return Cite(inv.email_ref, 1, line)
    return None


# ---- 3. waived but billed ---------------------------------------------------------------------------------------------
def _sender_authority(world: World, k: Contract, ins: dict, category: str, on: date | None):
    """Schedule 1 by address, or a person's confirm + sender_authority feedback for this contract and category."""
    p = k.is_authorised(category, ins.get("_sender_email"), ins.get("_sender_name"))
    if p:
        return p, None
    for it in world.feedback:
        f = it.get("fact") or {}
        if f.get("type") != "sender_authority" or it.get("decision") not in ("confirm", None):
            continue
        sc = it.get("scope") or {}
        ap = sc.get("applies_to") or {}
        person = ap.get("person") or (f.get("subject") or {}).get("person")
        cats = {str(c).lower() for c in ((f.get("value") or {}).get("categories") or [])} | \
            ({str(ap["category"]).lower()} if ap.get("category") else set())
        if norm_name(person) != norm_name(ins.get("_sender_name") or ins.get("author_name")):
            continue
        if category not in cats or (ap.get("contract_id") and norm_id(ap["contract_id"]) != k.id):
            continue
        vf, vt = to_date(sc.get("valid_from")), to_date(sc.get("valid_to"))
        if on and ((vf and on < vf) or (vt and on > vt)):
            continue
        return True, {"feedback_item_id": it["item_id"], "batch": it.get("_batch")}
    return None, None


def _charge_code(k: Contract, text: str | None) -> str | None:
    t = norm_name(text)
    if not t:
        return None
    best = None
    for lab, code in k.labels.items():
        if lab and lab in t and (best is None or len(lab) > len(best[0])):
            best = (lab, code)
    return best[1] if best else None


def check_waivers(world: World) -> list[Item]:
    items = []
    waivers = [i for i in world.instructions if i.get("type") == "waiver"]
    for iid, copies in world.invoices.items():
        inv = copies[0]
        k = world.contract_of(inv)
        if k is None:
            continue
        hit_lines, cites, learned, quoted_only = [], [], None, None
        for w in waivers:
            if w.get("condition"):
                continue  # clause 11.3: a conditional waiver needs proof its condition was met
            code = (str(w.get("charge_code") or "").upper() or None)
            code = _charge_code(k, w.get("charge")) or code
            awb = norm_awb(w.get("awb")) if w.get("awb") else None
            months = {str(m)[:7] for m in (w.get("months") or [])}
            lines = [ln for ln in inv.lines if (code is None or ln.code == code) and (
                (awb and ln.awb == awb) or (months and _line_month(inv, ln) in months))]
            fb_one_off = _one_off(world, iid, "waived_but_billed")
            if not lines and fb_one_off is None:
                continue
            if w.get("quoted"):
                if lines:
                    quoted_only = (w, lines)
                continue
            who, fb = _sender_authority(world, k, w, "waivers", to_date(w["_received"]))
            if not who:
                continue
            hit_lines += [ln for ln in lines if ln not in hit_lines]
            cites.append(cite(w["_ref"], w))
            sched = who if not isinstance(who, bool) else None
            if sched is not None:
                cites.append(sched.cite)
            learned = learned or fb
        if hit_lines:
            native = sum((D(ln.amount) or ZERO for ln in hit_lines), ZERO)
            usd, fx = to_usd(world, inv, native, k)
            if usd and usd > 0:
                it = Item("waived_but_billed", {"invoice_id": iid}, amount=usd, native=native, outbound="dispute",
                          invoice=inv, learned=learned, variant="WB-01")
                it.cite(*cites)
                for ln in hit_lines:
                    it.cite(ln.cite)
                it.cite(fx)
                items.append(it)
            continue
        if quoted_only:
            w, lines = quoted_only
            author = w.get("author_name")
            sched = k.is_authorised("waivers", w.get("author_email"), author)
            if sched:
                holder = {"party": sched.party, "person": sched.name, "role": sched.title}
            else:
                holder = _holder(k, "notices", "carrier")
            it = Item("waived_but_billed", {"invoice_id": iid}, verdict="cannot_determine",
                      outbound="request_document", invoice=inv, variant="WB-02",
                      missing_doc={"name": f"original email from {author or 'the sender'} waiving the "
                                           f"{w.get('charge') or 'charge'}", "holder": holder})
            it.cite(cite(w["_ref"], w), *[ln.cite for ln in lines])
            items.append(it)
    return items


def _line_month(inv: Invoice, ln: Line) -> str | None:
    if ln.pickup:
        return ln.pickup.strftime("%Y-%m")
    ref = str(ln.reference or "")
    if re.match(r"\d{4}-\d{2}", ref):
        return ref[:7]
    return inv.date.strftime("%Y-%m") if inv.date else None


def _one_off(world: World, iid: str, cls: str) -> dict | None:
    for it in world.feedback:
        fk = it.get("finding_key") or {}
        if it.get("decision") == "one_off_approval" and norm_id(fk.get("invoice_id")) == iid and fk.get("class") == cls:
            return it
    return None


# ---- 4. late fees -----------------------------------------------------------------------------------------------------
def _extension(world: World, k: Contract, iid: str) -> tuple[date | None, Cite | None]:
    best = None
    for ins in world.instructions:
        if ins.get("type") != "due_date_extension" or ins.get("quoted"):
            continue
        if norm_id(ins.get("invoice_id")) != iid or not ins.get("new_date"):
            continue
        who, _ = _sender_authority(world, k, ins, "payment_terms", to_date(ins["_received"]))
        if not who:
            continue
        if best is None or ins["_received"] >= best[2]:
            best = (to_date(ins["new_date"]), cite(ins["_ref"], ins), ins["_received"])
    return (best[0], best[1]) if best else (None, None)


def _due_of(world: World, k: Contract, iid: str) -> tuple[date | None, list]:
    cites = []
    due = None
    p = world.payments.get(iid)
    if p and p[0].get("due_date"):
        due = to_date(p[0]["due_date"])
        cites.append(p[1])
    elif iid in world.invoices:
        due = world.invoices[iid][0].due
    ext, ec = _extension(world, k, iid)
    if ext:
        due = ext
        cites.insert(0, ec)
    return due, cites


def _referenced_invoice(ln: Line) -> str | None:
    for text in (ln.reference, ln.description):
        m = re.search(r"([A-Z]{2,}[A-Z0-9]*(?:[-/][A-Z0-9]+){1,})", str(text or "").upper())
        if m:
            return norm_id(m.group(1))
    return None


def late_fee_due(k: Contract, principal: Decimal, days_late: int) -> Decimal:
    pay = k.terms.get("payment") or {}
    grace = int(pay.get("grace_days") or 0)
    pct = D(pay.get("late_fee_pct_per_month"))
    if pct is None or days_late <= grace:
        return ZERO
    n = ceil_div(days_late, 30)
    total, base = ZERO, principal
    for _ in range(n):
        fee = r2(pct / Decimal(100) * base)
        total += fee
        if pay.get("compounding"):
            base += fee
    return total


def check_late_fees(world: World) -> list[Item]:
    items = []
    for iid, copies in world.invoices.items():
        inv = copies[0]
        k = world.contract_of(inv)
        if k is None:
            continue
        late = [ln for ln in inv.lines if ln.code == "LATE" or re.search(r"late (payment )?fee|overdue", ln.description,
                                                                         re.I)]
        if not late:
            continue
        native, cites = ZERO, []
        for ln in late:
            ref = _referenced_invoice(ln)
            if not ref or ref == iid:
                continue
            p = world.payments.get(ref)
            if not p or not p[0].get("paid_date"):
                continue
            due, dcites = _due_of(world, k, ref)
            if due is None:
                continue
            principal = D(p[0].get("amount")) or (D(world.invoices[ref][0].total) if ref in world.invoices else None)
            if principal is None:
                continue
            principal -= D(p[0].get("disputed_amount")) or ZERO
            days_late = (to_date(p[0]["paid_date"]) - due).days
            correct = late_fee_due(k, principal, days_late)
            billed = D(ln.amount) or ZERO
            if billed > correct:
                native += billed - correct
                cites += dcites + [ln.cite]
        if native <= 0:
            continue
        usd, fx = to_usd(world, inv, native, k)
        if usd is None:
            continue
        it = Item("late_fee_not_owed", {"invoice_id": iid}, amount=usd, native=native, outbound="dispute",
                  invoice=inv, variant="LF-01")
        it.cite(*cites, k.clause("late_fee"), fx)
        items.append(it)
    return items


def check_late_fee_risk(world: World, disputed: dict) -> list[Item]:
    """Unpaid invoices due 1-14 days after as_of: one month's fee on the open amount less what we dispute."""
    items = []
    for iid, (row, pc, _) in world.payments.items():
        status = str(row.get("status") or "").lower()
        if row.get("paid_date") or ("paid" in status and "unpaid" not in status):
            continue
        inv = world.invoices.get(iid, [None])[0]
        k = world.contract_of(inv) if inv else None
        if k is None and inv is None and len(world.contracts) == 1:
            k = next(iter(world.contracts.values()))
        if k is None:
            continue
        due, dcites = _due_of(world, k, iid)
        if due is None or not (0 < (due - world.as_of).days <= 14):
            continue
        pay = k.terms.get("payment") or {}
        pct = D(pay.get("late_fee_pct_per_month"))
        principal = D(row.get("amount")) or (D(inv.total) if inv else None)
        if pct is None or principal is None:
            continue
        principal -= disputed.get(iid, ZERO)
        principal -= D(row.get("disputed_amount")) or ZERO
        fee = r2(pct / Decimal(100) * principal)
        currency = (row.get("currency") or (inv.currency if inv else "USD")).upper()
        fx = None
        if currency != "USD":
            stub = inv or Invoice(iid, iid, "invoice", "", "", "", "", to_date(row.get("invoice_date")), due, k.id,
                                  currency, [], None, None, False, True)
            fee, fx = to_usd(world, stub, fee, k)
            if fee is None:
                continue
        it = Item("late_fee_risk", {"invoice_id": iid}, amount=fee, deadline=due, internal="payment_reminder",
                  invoice=inv, variant="LF-03")
        it.cite(*dcites, pc, k.clause("late_fee"), fx)
        items.append(it)
    return items


# ---- 5. cashback and credits ------------------------------------------------------------------------------------------
QS_CODES = {"FRT", "FUEL", "SEC"}


def _qualifying(world: World, k: Contract, start: date, end: date) -> tuple[Decimal | None, list]:
    codes = QS_CODES | set(k.fees)
    total, cites = ZERO, []
    for copies in world.invoices.values():
        for inv in copies[:1]:
            if inv.contract != k.id or not inv.date or not (start <= inv.date <= end):
                continue
            s = sum((D(ln.amount) or ZERO for ln in inv.lines if ln.code in codes), ZERO)
            if inv.currency != "USD":
                s, _ = to_usd(world, inv, s, k)
                if s is None:
                    return None, []
            total += s
    for c in world.credits:
        if c.contract != k.id or not c.date or not (start <= c.date <= end):
            continue
        if c.credit_type in ("rebate", "improvement", "margin"):
            continue
        amt = abs(D(c.total) or ZERO)
        if c.currency != "USD":
            amt, _ = to_usd(world, c, amt, k)
            if amt is None:
                return None, []
        total -= amt
    return total, cites


def _issued(world: World, k: Contract, period: str, types: tuple) -> tuple[Decimal, list]:
    tot, cites = ZERO, []
    for c in world.credits:
        if c.contract != k.id or c.credit_type not in types:
            continue
        if str(c.credit_period or "").upper().replace(" ", "") != period.upper():
            continue
        amt = abs(D(c.total) or ZERO)
        if c.currency != "USD":
            amt, _ = to_usd(world, c, amt, k)
        tot += amt or ZERO
        cites.append(c.total_cite)
    return tot, cites


def check_cashback(world: World) -> list[Item]:
    items = []
    for k in world.contracts.values():
        if not k.effective:
            continue
        end_all = k.end or world.as_of
        rb = k.terms.get("rebate")
        if rb and rb.get("tiers"):
            q = quarter_of(k.effective)
            while True:
                qs, qe = quarter_bounds(q)
                if qs > min(end_all, world.as_of) or qe >= world.as_of:
                    break
                spend, _ = _qualifying(world, k, max(qs, k.effective), qe)
                if spend is not None:
                    tier = max((t for t in rb["tiers"] if D(t.get("threshold")) is not None and spend >= D(t["threshold"])),
                               key=lambda t: D(t["threshold"]), default=None)
                    if tier:
                        earned = r2(spend * D(tier["pct"]) / Decimal(100))
                        issued, icites = _issued(world, k, q, ("rebate",))
                        owed = earned - issued
                        if owed > 0:
                            due = qe + timedelta(days=int(rb.get("due_days_after_quarter") or 0))
                            it = Item("cashback", {"contract_id": k.id, "period": q, "credit_type": "rebate"},
                                      amount=owed, variant="CB-01")
                            if world.as_of > due:
                                it.outbound = "claim_credit"
                            else:
                                it.internal = "track_credit"
                            it.cite(cite(k.ref, rb), k.clause("rebate"), _qs_cite(k), *icites)
                            items.append(it)
                y, n = int(q[:4]), int(q[-1])
                q = f"{y + (n == 4)}-Q{n % 4 + 1}"
        ic = k.terms.get("improvement_credit")
        if ic and D(ic.get("pct")) is not None:
            n = 1
            while True:
                ys, ye = add_years(k.effective, n - 1), add_years(k.effective, n) - timedelta(days=1)
                if ye >= world.as_of or ys > end_all:
                    break
                spend, _ = _qualifying(world, k, ys, ye)
                if spend is not None and spend > 0:
                    earned = r2(spend * D(ic["pct"]) / Decimal(100))
                    issued, icites = _issued(world, k, f"CY{n}", ("improvement", "other"))
                    owed = earned - issued
                    if owed > 0:
                        due = add_days(add_years(k.effective, n), int(ic.get("due_days_after_anniversary") or 0))
                        it = Item("cashback", {"contract_id": k.id, "period": f"CY{n}", "credit_type": "promised_credit"},
                                  amount=owed, variant="CB-03")
                        if world.as_of > due:
                            it.outbound = "claim_credit"
                        else:
                            it.internal = "track_credit"
                        it.cite(cite(k.ref, ic), k.clause("improvement"), _qs_cite(k), *icites)
                        items.append(it)
                n += 1
    return items


def _qs_cite(k: Contract) -> Cite | None:
    q = k.terms.get("qualifying_spend")
    return cite(k.ref, q) if q else None


# ---- 6. expiry --------------------------------------------------------------------------------------------------------
def check_expiry(world: World) -> list[Item]:
    items = []
    for k in world.contracts.values():
        if not k.end:
            continue
        deadline = k.end
        if k.auto_renews and k.notice_days:
            deadline = k.end - timedelta(days=int(k.notice_days))
        if 0 <= (deadline - world.as_of).days <= 60:
            it = Item("expiry", {"contract_id": k.id}, deadline=deadline, internal="alert", variant="EX-01")
            it.cite(k.end_cite if k.end_cite and k.end_cite.quote else find_line(world, k.ref, k.end.isoformat()),
                    k.clause("extension"))
            items.append(it)
    return items
