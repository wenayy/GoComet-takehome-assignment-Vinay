"""The world as of a date: every fact from documents received by then, joined up and ranked by authority.

Built fresh for each report from the stored facts, so an amendment that arrives later simply changes the next world.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date

from .money import D, to_date

# authority levels, GLOSSARY "Which document wins"
SIGNED, AUTHORISED, RECORD, OTHER, NONE = 1, 2, 3, 4, 5


def norm_id(s) -> str:
    """Invoice and contract ids as the grader keys them: capitals, no spaces, '#' or ':'."""
    return re.sub(r"[\s#:]", "", str(s or "")).upper()


def norm_lane(s) -> str:
    return re.sub(r"\s", "", str(s or "")).upper()


def norm_awb(s) -> str:
    return re.sub(r"\s", "", str(s or "")).upper()


def norm_name(s) -> str:
    return " ".join(str(s or "").lower().split())


@dataclass
class Cite:
    doc: str
    page: int | None
    quote: str | None

    def ev(self) -> dict | None:
        if not self.quote or not self.page:
            return None
        return {"doc": self.doc, "page": int(self.page), "quote": self.quote}


def cite(ref: str, node: dict | None) -> Cite:
    node = node or {}
    return Cite(ref, node.get("page"), node.get("quote"))


@dataclass
class Person:
    name: str
    title: str | None
    party: str | None
    categories: set
    email: str | None
    cite: Cite
    learned: dict | None = None      # set when the authority came from feedback
    valid_from: date | None = None
    valid_to: date | None = None


@dataclass
class RateRow:
    contract: str
    lane: str
    minimum: bool
    min_kg: object
    max_kg: object
    valid_from: date | None
    valid_to: date | None
    rate: object
    source: str            # msa | amendment | rate_notice | feedback
    authority: int
    effective: date | None  # for ranking amendments / notices: later wins
    cite: Cite
    received: str
    learned: dict | None = None
    label: str = ""        # e.g. "TBX-US-EXP-01/AMD-1"

    def covers(self, d: date) -> bool:
        return (self.valid_from is None or d >= self.valid_from) and (self.valid_to is None or d <= self.valid_to)

    def holds(self, kg) -> bool:
        if self.minimum:
            return False
        kg = D(kg)
        lo, hi = D(self.min_kg), D(self.max_kg)
        return kg is not None and (lo is None or kg >= lo) and (hi is None or kg <= hi)


@dataclass
class Contract:
    id: str
    ref: str                      # document holding the MSA
    received: str
    terms: dict
    shipper_code: str | None = None
    carrier_code: str | None = None
    effective: date | None = None
    end: date | None = None
    end_cite: Cite | None = None
    auto_renews: bool = False
    notice_days: int | None = None
    amendments: list = field(default_factory=list)   # (facts, ref, received)
    people: list = field(default_factory=list)
    fuel: dict = field(default_factory=dict)          # 'YYYY-MM' -> (pct, Cite)
    fx: dict = field(default_factory=dict)            # 'YYYY-MM' -> (inr_per_usd, Cite)
    fees: dict = field(default_factory=dict)          # code -> accessorial dict with cite
    labels: dict = field(default_factory=dict)        # normalised label -> code
    missing_refs: list = field(default_factory=list)  # referenced documents not held
    clauses: dict = field(default_factory=dict)       # name -> Cite

    @property
    def currency(self) -> str:
        cur = {str((r or {}).get("currency") or "").upper() for r in self.terms.get("rates") or []}
        cur.discard("")
        return cur.pop() if len(cur) == 1 else "USD"

    def clause(self, *names) -> Cite | None:
        for n in names:
            c = self.clauses.get(n)
            if c and c.quote:
                return c
        return None

    def persons(self, category: str, party: str | None = None, on: date | None = None) -> list[Person]:
        out = []
        for p in self.people:
            if category not in p.categories:
                continue
            if party and p.party and party.upper() != p.party.upper():
                continue
            if on and ((p.valid_from and on < p.valid_from) or (p.valid_to and on > p.valid_to)):
                continue
            out.append(p)
        return out

    def is_authorised(self, category: str, email: str | None, name: str | None, on: date | None = None) -> Person | None:
        for p in self.persons(category, on=on):
            if p.learned:  # feedback names a person, not an address
                if name and norm_name(name) == norm_name(p.name):
                    return p
                continue
            if email and p.email and email.lower() == p.email.lower():
                return p
            if not email and name and norm_name(name) == norm_name(p.name):
                return p
        return None


@dataclass
class Line:
    invoice: str
    idx: int
    awb: str | None
    pickup: date | None
    description: str
    code: str | None
    lane: str | None
    qty: object
    unit: str | None
    base: object
    rate: object
    amount: object
    reference: str | None
    cite: Cite


@dataclass
class Invoice:
    id: str
    number: str
    kind: str              # invoice | statement | credit_note
    ref: str
    email_ref: str
    received: str
    received_at: str
    date: date | None
    due: date | None
    contract: str | None
    currency: str
    lines: list
    total: object
    total_cite: Cite | None
    is_copy: bool
    requests_payment: bool
    credit_type: str | None = None
    credit_period: str | None = None
    references: list = field(default_factory=list)
    email_cite: Cite | None = None
    scan: bool = False
    ocr_agreement: float | None = None   # share of the scan's amounts the second OCR reading confirms


@dataclass
class World:
    as_of: date
    contracts: dict = field(default_factory=dict)
    invoices: dict = field(default_factory=dict)      # id -> [Invoice copies in arrival order]
    credits: list = field(default_factory=list)
    shipments: dict = field(default_factory=dict)     # awb -> [shipment dict + ref]
    events: dict = field(default_factory=dict)        # awb -> [event dict + ref + source]
    instructions: list = field(default_factory=list)  # instruction dict + ref + sender
    payments: dict = field(default_factory=dict)      # invoice id -> (row, Cite, register date)
    rate_rows: list = field(default_factory=list)
    notices: list = field(default_factory=list)       # rate notices with their authority
    feedback: list = field(default_factory=list)
    docs: dict = field(default_factory=dict)          # ref -> doc (pages, received)

    def contract_of(self, inv: Invoice) -> Contract | None:
        if inv.contract and inv.contract in self.contracts:
            return self.contracts[inv.contract]
        return None

    def fact_list(self, awb: str) -> list:
        return self.events.get(norm_awb(awb), [])


# ---- building -----------------------------------------------------------------------------------------------------------
def _cat_set(cats) -> set:
    return {str(c).strip().lower() for c in cats or []}


def _people(ref: str, rows) -> list[Person]:
    out = []
    for p in rows or []:
        if not p.get("name"):
            continue
        out.append(Person(p["name"], p.get("title"), (p.get("party") or "").upper() or None, _cat_set(p.get("categories")),
                          (p.get("email") or "").lower() or None, cite(ref, p)))
    return out


# what each contract's own charge codes mean, so the checks never depend on one carrier's code names
CANON = {"freight": "FRT", "fuel": "FUEL", "security": "SEC", "peak": "PEAK", "dangerous_goods": "DG",
         "dry_ice": "DRYICE", "storage": "STOR", "delivery": "DLV", "customs": "CUSTOMS", "late_fee": "LATE",
         "tax": "TAX", "duty": "DUTY", "carrier_cost": "COST", "margin": "MARGIN", "special_handling": "HDL"}


def _canon(code, category) -> str:
    code = str(code or "").upper().strip()
    return CANON.get(str(category or "").lower()) or code


def _label_map(rows, code_map: dict) -> dict:
    m = {}
    for r in rows or []:
        raw = str(r.get("code") or "").upper()
        code = CANON.get(str(r.get("category") or "").lower()) or code_map.get(raw) or raw
        code_map.setdefault(raw, code)
        m[norm_name(raw)] = code
        for lab in r.get("labels") or []:
            m[norm_name(lab)] = code
    return m


def _contract_from(facts: dict, doc: dict) -> Contract | None:
    c = facts.get("contract") or {}
    cid = norm_id(c.get("contract_id") or (facts.get("contract_refs") or [None])[0])
    if not cid:
        return None
    k = Contract(id=cid, ref=doc["ref"], received=doc["received"], terms=c,
                 shipper_code=(c.get("shipper_code") or "").upper() or None,
                 carrier_code=(c.get("carrier_code") or "").upper() or None,
                 effective=to_date(c.get("effective_date")), end=to_date(c.get("end_date")),
                 end_cite=cite(doc["ref"], c.get("term_quote")),
                 auto_renews=bool(c.get("auto_renews")), notice_days=c.get("non_renewal_notice_days"))
    k.people = _people(doc["ref"], c.get("schedule1"))
    for f in c.get("fuel") or []:
        if f.get("month") and D(f.get("pct")) is not None:
            k.fuel[str(f["month"])[:7]] = (D(f["pct"]), cite(doc["ref"], f))
    for f in c.get("fx") or []:
        if f.get("month") and D(f.get("inr_per_usd")) is not None:
            k.fx[str(f["month"])[:7]] = (D(f["inr_per_usd"]), cite(doc["ref"], f))
    code_map = {}
    for a in c.get("accessorials") or []:
        if a.get("code"):
            canon = _canon(a["code"], a.get("category"))
            code_map[str(a["code"]).upper()] = canon
            k.fees[canon] = a | {"_cite": cite(doc["ref"], a), "_code": str(a["code"]).upper()}
    k.labels = _label_map(c.get("charge_labels"), code_map)
    for a in c.get("accessorials") or []:
        if a.get("code") and a.get("name"):
            k.labels.setdefault(norm_name(a["name"]), code_map[str(a["code"]).upper()])
    for name, q in (c.get("clause_quotes") or {}).items():
        if isinstance(q, dict):
            k.clauses[name] = cite(doc["ref"], q)
    return k


def _periods(c: dict) -> dict:
    out = {}
    periods = c.get("rate_periods") or []
    for i, p in enumerate(periods):
        last = i == len(periods) - 1
        # clause 3.1: the last period's rates continue until an amendment replaces them
        out[str(p.get("name"))] = (to_date(p.get("from")), None if last else to_date(p.get("to")))
    return out


def _rate_rows(contract: str, facts: dict, doc: dict, source: str, authority: int, label: str,
               effective: date | None) -> list[RateRow]:
    c = facts.get("contract") or {}
    periods = _periods(c)
    rows = []
    for r in c.get("rates") or []:
        lane = norm_lane(r.get("lane"))
        if not lane or D(r.get("rate")) is None:
            continue
        lo, hi = periods.get(str(r.get("period")), (None, None)) if r.get("period") else (None, None)
        vf = to_date(r.get("valid_from")) or lo or (effective if source == "amendment" else None)
        vt = to_date(r.get("valid_to")) or hi
        band = str(r.get("band") or "").lower()
        rows.append(RateRow(contract, lane, "min" in band, r.get("min_kg"), r.get("max_kg"), vf, vt, r.get("rate"),
                            source, authority, effective or vf, cite(doc["ref"], r), doc["received"], label=label))
    return rows


def _norm_charge(contract: Contract | None, line: dict) -> str | None:
    desc = norm_name(line.get("description"))
    if contract:
        # longest label contained in the description wins ('Screening fee (adj.)' before 'Screening fee')
        best = None
        for lab, code in contract.labels.items():
            if lab and (desc == lab or desc.startswith(lab) or f" {lab}" in f" {desc}"):
                if best is None or len(lab) > len(best[0]):
                    best = (lab, code)
        if best:
            return best[1]
    code = str(line.get("code") or "").upper().strip()
    return code or None


def build_world(store, as_of: str) -> World:
    w = World(as_of=to_date(as_of))
    entries = []
    for e in store.index():
        doc = store.doc(e["sha"])
        if doc is None or doc["received"] > as_of:
            continue
        facts = store.facts(e["sha"])
        doc = doc | {"ref": e["ref"]}
        w.docs[e["ref"]] = doc
        if facts:
            entries.append((doc, facts))
    w.feedback = store.feedback()

    # contracts first: everything else is read against them
    for doc, f in entries:
        if f.get("kind") == "contract" and f.get("contract"):
            k = _contract_from(f, doc)
            if k and k.id not in w.contracts:
                w.contracts[k.id] = k
                w.rate_rows += _rate_rows(k.id, f, doc, "msa", SIGNED, k.id, None)
                for m in (f["contract"].get("referenced_documents") or []):
                    k.missing_refs.append(m | {"_cite": cite(doc["ref"], m), "_contract": k.id})
    for doc, f in entries:
        if f.get("kind") == "amendment" and f.get("contract"):
            c = f["contract"]
            am = c.get("amendment") or {}
            cid = norm_id(am.get("amends_contract_id") or c.get("contract_id"))
            k = w.contracts.get(cid)
            if k is None:
                continue
            if c.get("signed_by_both") is False:
                continue  # an unsigned draft changes nothing
            eff = to_date(am.get("effective_from")) or to_date(c.get("effective_date"))
            k.amendments.append((f, doc["ref"], doc["received"]))
            label = am.get("reference") or f"{cid}/AMD"
            w.rate_rows += _rate_rows(cid, f, doc, "amendment", SIGNED, label, eff)
            if am.get("new_end_date"):
                k.end = to_date(am["new_end_date"])
                k.end_cite = cite(doc["ref"], c.get("term_quote"))
            for p in _people(doc["ref"], c.get("schedule1")):
                k.people.append(p)
            for m in c.get("referenced_documents") or []:
                k.missing_refs.append(m | {"_cite": cite(doc["ref"], m), "_contract": cid, "_effective": eff})
            for name, q in (c.get("clause_quotes") or {}).items():
                if isinstance(q, dict):
                    k.clauses.setdefault(name, cite(doc["ref"], q))
            for mth, (pct, ct) in ((ff["month"][:7], (D(ff["pct"]), cite(doc["ref"], ff)))
                                   for ff in c.get("fuel") or [] if ff.get("month") and D(ff.get("pct")) is not None):
                k.fuel[mth] = (pct, ct)
            for ff in c.get("fx") or []:
                if ff.get("month") and D(ff.get("inr_per_usd")) is not None:
                    k.fx[ff["month"][:7]] = (D(ff["inr_per_usd"]), cite(doc["ref"], ff))

    for doc, f in entries:
        kind = f.get("kind")
        ref = doc["ref"]
        email = w.docs.get(doc["email_ref"])
        sender_email, sender_name = _sender(email)
        if kind in ("invoice", "statement", "credit_note") and f.get("invoice"):
            inv = _invoice(w, f, doc, email)
            if inv:
                if inv.kind == "credit_note":
                    w.credits.append(inv)
                else:
                    w.invoices.setdefault(inv.id, []).append(inv)
        if kind == "rate_notice" and f.get("rate_notice"):
            _notice(w, f["rate_notice"], doc, sender_email, sender_name)
        for s in f.get("shipments") or []:
            if s.get("awb"):
                w.shipments.setdefault(norm_awb(s["awb"]), []).append(s | {"_ref": ref, "_kind": kind})
        for ev in f.get("events") or []:
            if ev.get("awb") and ev.get("event"):
                w.events.setdefault(norm_awb(ev["awb"]), []).append(
                    ev | {"_ref": ref, "_kind": kind, "_sender": sender_email})
        for ins in f.get("instructions") or []:
            w.instructions.append(ins | {"_ref": ref, "_email": doc["email_ref"], "_sender_email": sender_email,
                                         "_sender_name": sender_name, "_received": doc["received"]})
        for p in f.get("payments") or []:
            iid = norm_id(p.get("invoice_id"))
            if iid:
                prev = w.payments.get(iid)
                if prev is None or prev[2] <= doc["received"]:
                    w.payments[iid] = (p, cite(ref, p), doc["received"])
    for copies in w.invoices.values():
        copies.sort(key=lambda i: (i.received_at, i.ref))
    return w


def _sender(email: dict | None) -> tuple[str | None, str | None]:
    if not email:
        return None, None
    first = email["pages"][0].split("\n")[0] if email.get("pages") else ""
    m = re.match(r"From:\s*(.*?)\s*<([^>]*)>", first)
    return (m.group(2).lower(), m.group(1)) if m else (None, None)


def _invoice(w: World, f: dict, doc: dict, email: dict | None) -> Invoice | None:
    v = f["invoice"]
    number = v.get("number")
    if not number:
        return None
    kind = "credit_note" if f["kind"] == "credit_note" else f["kind"]
    cid = norm_id(v.get("contract_ref") or (f.get("contract_refs") or [None])[0]) or None
    contract = w.contracts.get(cid) if cid else None
    if doc["ext"] == "eml" and not v.get("lines"):
        return None  # an email that mentions an invoice is not a copy of it
    lines = []
    for i, ln in enumerate(v.get("lines") or []):
        lines.append(Line(invoice=norm_id(number), idx=i, awb=_house_awb(ln),
                          pickup=to_date(ln.get("pickup_date")), description=ln.get("description") or "",
                          code=_norm_charge(contract, ln), lane=norm_lane(ln.get("lane")) or None, qty=ln.get("qty"),
                          unit=ln.get("unit"), base=ln.get("base"), rate=ln.get("rate"), amount=ln.get("amount"),
                          reference=ln.get("reference"), cite=cite(doc["ref"], ln)))
    email_cite = None
    for ins in f.get("instructions") or []:
        if ins.get("quote"):
            email_cite = cite(doc["ref"], ins)
    return Invoice(id=norm_id(number), number=number, kind=kind, ref=doc["ref"], email_ref=doc["email_ref"],
                   received=doc["received"], received_at=doc["received_at"], date=to_date(v.get("doc_date")),
                   due=to_date(v.get("due_date")), contract=cid, currency=(v.get("currency") or "USD").upper(),
                   lines=lines, total=v.get("total"), total_cite=cite(doc["ref"], v.get("total_quote")),
                   is_copy=bool(v.get("is_copy")), requests_payment=v.get("requests_payment") is not False,
                   credit_type=v.get("credit_type"), credit_period=v.get("credit_period"),
                   references=[norm_id(r) for r in v.get("references_invoices") or []], email_cite=email_cite,
                   scan=bool(doc.get("scan")), ocr_agreement=(doc.get("ocr") or {}).get("agreement"))


def _house_awb(ln: dict) -> str | None:
    """The line's own waybill. A printed house number ('<master>-H2') wins over the master the model may have given."""
    awb = norm_awb(ln.get("awb")) or None
    if awb:
        m = re.search(re.escape(awb.split("-H")[0]) + r"-H\d+", str(ln.get("quote") or "").upper())
        if m:
            return m.group(0)
    return awb


def _notice(w: World, rn: dict, doc: dict, sender_email: str | None, sender_name: str | None) -> None:
    cid = norm_id(rn.get("contract_id"))
    lane = norm_lane(rn.get("lane"))
    k = w.contracts.get(cid)
    if k is None and len(w.contracts) == 1:
        k = next(iter(w.contracts.values()))
    if k is None or not lane:
        return
    who = k.is_authorised("rates", sender_email, sender_name)
    auth = AUTHORISED if who and (not who.party or who.party == (k.carrier_code or who.party)) else OTHER
    vf, vt = to_date(rn.get("effective_from")), to_date(rn.get("effective_to"))
    notice = {"contract": k.id, "lane": lane, "from": vf, "to": vt, "ref": doc["ref"], "sender": sender_name,
              "sender_email": sender_email, "authority": auth, "received": doc["received"], "rows": []}
    for r in rn.get("rates") or []:
        if D(r.get("rate")) is None:
            continue
        band = str(r.get("band") or "").lower()
        row = RateRow(k.id, lane, "min" in band, r.get("min_kg"), r.get("max_kg"), vf, vt, r.get("rate"),
                      "rate_notice", auth, vf, cite(doc["ref"], r), doc["received"],
                      label=rn.get("reference") or f"rate notice {lane}")
        notice["rows"].append(row)
        w.rate_rows.append(row)
    w.notices.append(notice)
