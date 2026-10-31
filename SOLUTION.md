# My solution

## How to run it

```
make setup                    # .venv with harness/requirements.txt (nothing else to install)
export ANTHROPIC_API_KEY=...  # the grader sets this and ANTHROPIC_BASE_URL itself
make grade                    # practice mailbox -> report_card.txt / .json
make sheet                    # exam mailbox -> answer_sheet.json
make eval                     # our eval set -> evals/results.json
```

The harness reads documents with Claude (default model `claude-sonnet-5`, through the `anthropic` package already
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

> PENDING: final practice card and eval results.

## What doesn't

> PENDING.

## The eval case that caught a bug

`evals/cases/dry_ice_not_hazardous.yaml` (and `new_layout_overcharge.yaml`). Our eval mailboxes use a different carrier
whose contract calls the hazardous-goods fee `HAZ`, delivery `DOOR` and screening `SCR`. My checks were keyed on the
practice carrier's codes (`DG`, `DLV`, `SEC`), so on this contract the dry-ice case produced no finding at all: the
check never ran. The practice set could not have shown this, since every practice contract uses the same codes. Fix:
the extractor now records what each fee *is* (a category such as `dangerous_goods`), each contract's codes are
mapped to those categories, and the checks use the category. The same contract's milestone export calls the pickup
`COLLECTED_FROM_SHIPPER` and its invoices print the pickup date under "Collected"; both were missed, so the new-layout
overcharge vanished. Both cases fail on commit `f1ad993^` and pass after it.
