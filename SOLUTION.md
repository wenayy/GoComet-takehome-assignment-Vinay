# My solution

## How to run it

```
make setup                    # .venv with harness/requirements.txt (nothing else to install)
export ANTHROPIC_API_KEY=...  # the grader sets this and ANTHROPIC_BASE_URL itself
make grade                    # practice mailbox -> report_card.txt / .json
make sheet                    # exam mailbox -> answer_sheet.json
make eval                     # our eval set -> evals/results.json
```

The harness reads documents with Claude (default model `claude-opus-5`, through the `anthropic` package already
pinned). Everything else is plain Python with the pinned requirements; the scanned invoice is also read by the local
OCR package (`rapidocr-onnxruntime`, already in the requirements) as a cross-check.

Local-only settings live in a gitignored `.env` (see `.env.example`): `NOVA_MODEL` to try another model or an
Anthropic-compatible endpoint while developing, and `NOVA_CACHE_DIR` to share model answers between runs. The grader
passes neither, so it always runs the defaults.

Layout: `harness/harness.py` (the three commands) and `harness/nova/`:
`docs.py` (mailbox to pages, via `grader/extract.py`), `facts.py` (model extraction, quote repair, scans),
`world.py` (facts as of a date: contracts, authority, invoices, events), `pricing.py` (the rate in force, by
precedence), `checks.py` (the six classes), `report.py` (feedback, actions per mode, confidence, supersedes),
`store.py` (state dir), `llm.py` (the only model call, cached).

## What works

- **Practice card: 99.6 / 100** (`report_card.txt`). 22 of 22 findings with quotes on their pages, 0 false, every
  amount exact ($14,140.09 recovered, $0 wrongly disputed), 11 of 11 adaptation events, 3 of 3 learning checks, the
  conflict and the missing document right, all 16 clean controls left alone, 24 of 24 actions right in each mode,
  no hard gate. The 0.4 lost is confidence calibration (Brier 0.019; AUROC can't be measured with no wrong item).
- **Exam sheet** (`answer_sheet.json`), checked row by row: every line of all 25 invoices re-priced by a separate
  throwaway script that reads the contract tables straight from the text (no model), and every non-pricing row checked
  by hand against the emails, register and contracts. Every row agreed, and nothing was found that the sheet misses:
  the notice-vs-amendment conflict on CDU-3051302 [0.00, 61.93], Annex A rev 2 missing for CDU-3051286 (holder: the
  Pricing Manager), the scanned INR advice CDI-3031-0306 at 85.46, the duplicate CDU-3051273, the two waivers by the
  waivers contact, the late fee not owed after an extension (and the one still owed after a later one), the 2030-Q3
  rebate at 4,307.34 (Q4 is $5,134 under the tier), and the India improvement credit already paid in full. Claude
  Opus 5 and DeepSeek produce identical sheets.
- **Every invoice is self-checked**: its lines must add up to its printed subtotal, or it is re-read once. On practice
  and exam, 1 of 84 needed it.
- **Our evals: 18 of 18** (`evals/results.json`), 16 of them ours, built on a new carrier and customer with new
  contract wording, a new invoice layout (DD/MM/YYYY, thousands separators, other column names and charge codes) and
  the traps the brief lists: injection, unauthorised and quoted waivers, a one-off waiver that must not spread, the
  $5,000 line in autonomous mode, expiry window edges, feedback scoped to one lane, a backdated variation.
- **The same results on two different models.** Practice and evals score the same on Claude Opus 5 (the default) and
  on DeepSeek, which is how I know the rules don't depend on one model's habits.

## What doesn't

- **Margin step-down credits** aren't checked: the extractor reads the clause, but no check uses it yet.
- **Claude Sonnet 5 is untested end to end** (its comparison run was cut short); the default is Opus 5, which is.
- **Extraction is the risk.** Models sometimes skip a table or write a number their own way; each case I found is now
  handled in code (one table per call with a re-ask, tolerant number parsing, waybills found from the line text), but a
  new kind of omission on your set would show up as a missed or short finding, never as an invented one: every
  amount is recomputed in Python from cited lines.
- **Confidence is hand-weighted**, not fitted: every practice item was right, so there was nothing to fit it on.
- Feedback kinds the practice set doesn't show (`override_amount`, `resolve_conflict`, `reject`) are implemented from
  the glossary but have no eval of their own.
- Retro detection relies on arrival dates across reports; the backdated eval checks the new amount, not the flag.

## The eval case that caught a bug

`evals/cases/dry_ice_not_hazardous.yaml` (and `new_layout_overcharge.yaml`). Our eval mailboxes use a different carrier
whose contract calls the hazardous-goods fee `HAZ`, delivery `DOOR` and screening `SCR`. My checks were keyed on the
practice carrier's codes (`DG`, `DLV`, `SEC`), so on this contract the dry-ice case produced no finding at all: the
check never ran. The practice set could not have shown this, since every practice contract uses the same codes. Fix:
the extractor now records what each fee *is* (a category such as `dangerous_goods`), each contract's codes are
mapped to those categories, and the checks use the category. The same contract's milestone export calls the pickup
`COLLECTED_FROM_SHIPPER` and its invoices print the pickup date under "Collected"; both were missed, so the new-layout
overcharge vanished. Both cases fail on the code before commit `f1ad993` and pass after it.

A second bug came from comparing models rather than from a case: replayed through Claude Opus 5, practice dropped to
83.0 because Opus writes percentages as `"23.54%"` and my number parser rejected them, so no fuel line was ever
repriced. Fixed in `a0f37af` (numbers parse however a model writes them); Opus now scores 99.6 like DeepSeek.
