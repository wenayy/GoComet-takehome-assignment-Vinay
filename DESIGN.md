# Design

The model reads; Python decides. `ingest` turns every document into typed facts with exact quotes and stores them;
`report` rebuilds the world as of the date from those facts and applies the glossary rules in plain code. No verdict,
amount or action comes from a model.

## Fact model

Each email and attachment is stored as pages of text from `grader/extract.py`, so every quote we cite is checked
against the grader's own text. One forced tool call per document returns a typed record: contract terms (rates per
lane/band/period, fuel, FX, fees with a *category*, payment, rebate, Schedule 1, documents cited but not included),
invoice lines, shipments, tracking events, payment rows and the instructions an email carries (author, quoted or
not, AWB, charge, typed condition). Contracts are read one table per call (terms, rates, fuel, FX, fees, Schedule 1),
re-asked once if a table comes back empty; long spreadsheets by a column map plus Python. Facts are keyed by document hash under `--state-dir`; every fact keeps
`doc`, `page` and its quote, snapped to a real line of the page. Scans are read by a vision model and cross-checked
by local OCR. Charge codes are mapped to categories per contract, so the checks never depend on one carrier's names.

## Conflicts

All rate sources for the lane and pickup date are collected and ranked: signed amendment over MSA, later amendment
over earlier, an authorised rate notice over Annex A. An email changes terms only if its sender address is in that
contract's Schedule 1 for the category; others are ignored for terms. A notice and an amendment that give different
correct amounts on the same lane and date are unranked: `conflict`, both sources cited, range = billed minus each
correct total. A rate source the contract cites but we don't hold (Annex A rev 2) makes the key `cannot_determine`,
with the holder taken from Schedule 1.

## Confidence

Start from the verdict and class (0.9 for a priced finding, lower for conflicts and cashback). Subtract for a pickup
date not from tracking, a rate source without authority, a scanned invoice (more when the two OCR readings disagree)
and thin evidence. Feedback-backed items stay high. On practice every reported item was right, so AUROC can't be
measured there; the Brier score was 0.019.

## People in the loop

Internal actions (expiry alert, payment reminder, credit tracking) run on their own. Outbound actions are drafts in
supervised mode. A key with $5,000 or more of outbound amounts on one invoice or cashback key goes to
`human_review` in both modes. In autonomous mode a dispute or claim is executed only if the unattended-action test
passes: pickup date from tracking or a carrier email, every term from level 1-2 sources, no missing document, no open
conflict on the contract and lane, not read from a scan, under the threshold. Conflicts always escalate. Document
requests are sent in autonomous mode to the Schedule 1 contact. Text aimed at automated readers is recorded as data
and never acted on.

## Feedback

Every item is stored with the batch it followed. `close` and `reject` stop exactly the key named. `override_amount`
replaces that key's amount. Standing facts are applied where the checks read them, only when every `applies_to`
filter matches and the date is inside the scope: `sender_authority` (that person, category and contract),
`rate_accepted` (that contract and lane: later invoices are measured against the accepted notice), `precedence`
(that lane, unless a later signed amendment arrives). A `one_off_approval` covers its key and named months only.
Anything changed by feedback carries `learned_at`.

## New documents

The world is rebuilt for every report from facts received by `as_of`, so an amendment, a notice, a register or an
extension simply changes the next answer. Each report remembers its items per mode: a key whose verdict or amount
changes gets a new id that `supersedes` the old one; a key that appears only because a backdated document arrived
after the invoice is marked `retro`; keys that no longer hold (paid, expired) disappear.

## Cost

Measured from logged token usage on real runs over the practice, exam and eval mailboxes: one call per email or
attachment plus six per contract, about 1.2 calls per document, averaging about 8,800 input and 1,030 output tokens a
call (most of the input is the extraction schema, sent every time). At list prices that is about **$83 per 1,000
documents on Claude Opus 5** ($0.070 a call) and **$34 on Sonnet 5** ($0.029). Report time is pure Python and free.
With 8 parallel workers, 1,000 documents take roughly 30-40 minutes (estimated from per-call latency). Claude calls
mark the fixed schema and instructions cacheable, which should roughly halve input cost; verified against a local fake
API (markers sent, a rejection falls back to no caching) but not yet measured on the real one.

## What I'd do next

- Margin step-down credits: the schema reads them, the check isn't written yet.
- The extraction is the weak point: the model sometimes skips a table or writes numbers its own way. Every bug of that
  kind was caught by comparing models and by our evals, then fixed in code (one table per call, tolerant number
  parsing). Invoices are now self-checked (lines must add up to the printed subtotal, else one re-read).
- Measure prompt caching on the real API, and fit the confidence model once there are labelled mistakes.
- Evals for feedback kinds the practice set doesn't show (`override_amount`, `resolve_conflict`, `reject`).
