# Nova: an AI harness that finds the money leaking out of freight invoices

A multi-agent harness for accounts payable. It reads a freight mailbox (emails with PDF invoices, Excel statements,
contracts and amendments), remembers what the contracts say, and finds six kinds of overbilling with the amount and
the quoted lines that prove it.

**The idea: the model reads, Python decides.** An LLM turns every document into typed facts with exact quotes.
Verdicts, amounts, actions and confidence all come from plain, testable Python over those facts. No money figure
is ever written by a model, so a bad extraction shows up as a missed or short finding, never an invented one.

## Results

| Check | Result |
|---|---|
| Practice grader (`make grade`) | **99.6 / 100**: 22/22 findings with quotes on the right pages, 0 false, $14,140.09 recovered, **$0 wrongly disputed** |
| Adaptation to new documents | 11/11 (backdated amendments, extensions, late registers) |
| Learning from feedback | 3/3 |
| Clean invoices left alone | 16/16 |
| Actions per mode (supervised / autonomous) | 24/24 each, no hard-gate violations |
| Own eval suite (`make eval`) | **26/26**, 24 written by me on a new carrier, layout and contract wording |
| Model portability | same scores on Claude Opus 5 and DeepSeek |

The six classes it finds: overcharge, duplicate billing, waived-but-billed, late fees (owed or at risk), unpaid
cashback/credits, and contract expiry. Plus **conflicts** (two trusted sources disagree, so a human decides) and
**cannot_determine** (a needed document is missing, and the harness says who holds it).

## Architecture

```
 mailbox (.eml + PDF + XLSX, scans)
        |
   docs.py      pages of text, same reader the grader uses
        |
   facts.py     one forced tool call per document -> typed facts + exact quotes
        |       (contracts read one table per call; scans read by vision + local OCR cross-check;
        |        invoices self-checked: lines must sum to the printed subtotal, else re-read)
   store.py     facts keyed by document hash under --state-dir (survives between batches)
        |
   world.py     rebuild the world "as of" a date: contracts, authority (Schedule 1), invoices, events
   pricing.py   rate in force on the pickup date, by precedence (amendment > MSA, later > earlier)
   checks.py    the six classes, in code
        |
   report.py    feedback, actions per mode, confidence, `supersedes` between reports
```

Three CLI commands, each in a fresh process: `ingest <batch>`, `report --as-of DATE --mode ...`, `feedback <file>`.

## Design decisions worth reading

- **Facts, not answers, are stored.** The world is rebuilt for every report from the facts received by `as_of`, so a
  backdated amendment or a late extension simply changes the next answer. A key whose verdict or amount changes gets a
  new id that `supersedes` the old one.
- **Authority-aware.** An email changes contract terms only if the sender is named in that contract's Schedule 1 for
  that category. Quoted or forwarded text and unauthorised senders are ignored for terms.
- **Prompt-injection safe.** Instructions inside emails ("ignore the contract...") are recorded as data and never acted on.
- **Conflict vs. missing document.** A rate notice and an amendment giving different correct amounts are unranked:
  escalate with both sources. A cited rate source we don't hold gives `cannot_determine` and names the holder.
- **Safe autonomy.** Outbound actions are drafts in supervised mode. In autonomous mode they run only if the
  unattended-action test passes: complete evidence, no conflict, no scanned source, under $5,000, reversible.
- **Generalises by category, not by code.** Each contract's fee codes map to categories (`dangerous_goods`,
  `delivery`, `screening`), so nothing depends on one carrier's names. My eval suite caught the bug where it did.

Details: [DESIGN.md](DESIGN.md) (design), [SOLUTION.md](SOLUTION.md) (what works, what doesn't, bugs found by evals),
[GLOSSARY.md](GLOSSARY.md) (domain rules).

## Run it

```
make setup                    # .venv, Python 3.11+
export ANTHROPIC_API_KEY=...
make grade                    # practice mailbox -> report_card.txt / .json
make sheet                    # exam mailbox -> answer_sheet.json
make eval                     # own eval suite -> evals/results.json
```

## Layout

```
harness/harness.py, harness/nova/   the harness (about 2,700 lines of Python)
evals/                               26 cases: new carrier/layout, injection, waivers, feedback scoping, window edges
grader/                              the scoring harness
data/                                synthetic mailboxes (all companies, people and numbers are made up)
```

## Known gaps

Margin step-down credits are read but not yet checked; `override_amount` / `resolve_conflict` / `reject` feedback is
implemented from the glossary without its own eval; confidence is hand-weighted because every practice item was right,
so there was nothing to fit it on. See [SOLUTION.md](SOLUTION.md).
