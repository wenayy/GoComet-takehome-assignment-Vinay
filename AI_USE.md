# AI use

## Tools

- **Claude Code** (model: Claude Opus 5.5) in the terminal: reading the brief and data with me, design discussion, writing
  and testing the harness code, git commits.
- **Claude (Anthropic API)** inside the harness itself: reading documents into facts and transcribing the scanned invoice. It never decides a verdict or an amount; Python does that from the glossary rules.
- **DeepSeek** (`deepseek-chat`, through its Anthropic-compatible API) for cheap local runs while building; then the
  same mailboxes on Claude Opus 5 and Sonnet 5 with a real Anthropic key, to compare the models.
- **graphify**, to map the harness code into a graph so questions about it don't need every file re-read.

## How I worked with them

I used Claude Code as a pair: first to understand the brief, then to build in small steps, testing each one and
committing it before moving on.

1. **Understanding the brief.** i thoroughly understood the repo with examples and the glossary.md or all the relevant files but took some help to understand a example of overcharged to get better understanding and then take insights of it where we can think  in what direction

2. **Architecture.** We settled on: the model *reads* each document into typed facts with exact quotes; deterministic
   Python *decides* (pricing by precedence, authority from Schedule 1, the six checks, actions per mode). Reason: the
   brief warns that layout-tuned rules collapse on the hidden set, and money must be exact and repeatable.
3. **Decisions I made along the way**
   - Keep the grader's own reader (`grader/extract.py`) as the only text source for normal documents, so every quote we
     cite is checked against the same text the grader uses.
   - I looked at an external LLM-aided OCR project for the scanned invoices. We decided not to bring it in: it needs
     Tesseract and poppler (not promised in the grading container) and corrects OCR text with a language model that
     never sees the image, which risks changing numbers. Instead I asked to keep both readers we already had and use
     them to check each other: the vision transcription is the text, the local OCR (rapidocr) is a second reading,
     and any amount the two disagree on lowers confidence and blocks unattended actions.
   - Cheaper models for local test runs (through `NOVA_MODEL` / `ANTHROPIC_BASE_URL` in a gitignored `.env`), while the
     submitted code defaults to Anthropic, since the grading container can only reach api.anthropic.com.
   - Compare models on the same data instead of trusting one. Running Claude Opus 5 next to DeepSeek is what exposed
     the `"23.54%"` number bug and the dropped rate notice in the exam; afterwards both models give identical exam
     sheets, so the rules don't depend on one model's habits.
   - Hold the code to the brief's "avoid" list (no regex per invoice template, no fixed column order, no single waiver
     phrase). Auditing against it removed two practice-shaped patterns: the late-fee invoice lookup and the statement
     month format.
   - Write an eval for every edge case the brief describes (authority, forwarded waivers, credits in full / in part /
     for another charge / in a later batch, one shipment only, other wordings, injection). Writing the credit cases
     found a real bug: an already-credited waived fee would have been disputed again.
   - Ask for the evidence behind "will it work on unseen data": that led to the invoice self-check (lines must add up
     to the printed subtotal), a line-by-line audit of the whole exam sheet, and prompt caching to cut cost.
4. **What I checked myself.** The worked-example arithmetic by hand (196.92 for the overcharge example), the engine's
   output on that example against `grader/validate.py`, and the local OCR output on the exam's scanned invoice.

## Where it got things wrong

- An item with no citable evidence produced a schema-invalid report (empty `evidence`). Caught by `grader/validate.py`
  on the first engine run; fixed by finding the line that quotes the date, and dropping items with nothing to cite.
- The shell already pointed `ANTHROPIC_BASE_URL` at a local proxy, so values in `.env` were being ignored. Fixed by
  letting `.env` override the shell for local runs.
- The model call passed `temperature`, which the pinned SDK no longer accepts; a too-broad `except` turned that into
  silent retries. Found on the first live call; now only transient API errors are retried.
- Reading a whole contract in one call let the model skip tables: once an amendment's rate table (so a conflict
  vanished), once a contract's fuel table (so every fuel line went unpriced and the practice score fell from 97.1 to
  85.3). Contracts are now read one table per call, with one re-ask when a table comes back empty.
- The model filed both halves of a split shipment under the master AWB, which looked like a double bill; the printed
  house number now wins.
- Our own eval mailboxes (a new carrier) showed the checks only knew the practice carrier's charge codes; fixed by
  mapping each contract's fees to categories.
- The model comparison on my own Anthropic key cost about $40: it ran every mailbox on Opus 5 in one go, without
  measuring a small sample first, and a second copy of the run started in parallel. Lesson: measure the cost on one
  mailbox, then decide. The run did pay for itself in bugs found (the `%` parser, the multi-contract rate notice).
- Opus writes percentages as `"23.54%"`; the number parser dropped them, so on Claude every fuel line went unpriced
  (practice 83.0 on Opus vs 99.6 on DeepSeek). Fixed; Opus now scores 99.6.
- In the exam's two-contract mailbox, a rate notice without a contract id was thrown away, turning a required conflict
  into a plain overcharge. The notice's contract now comes from its reference, else the one contract pricing the lane.
- A credit note for a waived fee wasn't netted off. Credits are now matched to the line by waybill and charge.
- A first "this eval fails on the old code" check was itself wrong (the copy of the old code had no settings, so it
  read nothing). Re-run properly before relying on it.

## Written without AI

 
I told Claude what to build and test next, based on the brief: an eval for every edge case it lists, written the evals,(unauthorised and forwarded waivers, credits in full, in part, for another charge and in a later batch, one shipment only, other wordings, an email telling the AI to ignore the contract), a self-check that invoice lines add up to the printed total, and a full hand-check of the exam sheet.

I questioned results instead of accepting them: the cost of a model run,whether the code assumed one invoice layout, and whether it would hold up on unseen data.

 _price_shipment() in checks.py: "what should this invoice have cost?", It recalculates each charge line of a shipment

  choose_actions() in report.py, supervised mode or autonomous mode 
 
## Prompt log

 1. llm dont do the final findings? the sequence of things right ocr, harness and then persistent storage is that
   > we can use https://github.com/Dicklesworthstone/llm_aided_ocr ... keep this as well
   > dont remove the current checking of the reader, grader one ... this could be a great fit for the better output or understanding of the documents if the batch brings the ammendment 
 

   *Confirmed the model only reads and Python decides. Reviewed the OCR project (Tesseract + text-only LLM correction)
   and recommended against adding it; implemented the two-reader cross-check for scans instead and tested the local
   reader on the exam scan.*

2. > i have put it in the .env i have ANTHROPIC_BASE_URL=https://api.deepseek.com/anthropic NOVA_MODEL=deepseek-chat
   > done this and then in anthropic api key i have put the deepseek api key can u check now

   *The settings had gone into `.env.example` (tracked by git), so the key was moved to the gitignored `.env` before
   anything was committed. First live runs through DeepSeek: fixed the SDK call, then ran the 8 worked examples
   (6 of 8, then 8 of 8 after the contract and AWB fixes), the practice grader (97.1), and our first 13 eval cases
   (15 of 15 with the samples). Found the generalisation bugs above and a fuel-table omission that the next practice
   run exposed.*


3. > now can we use the anthropic api key also real one not the deepseek compatible to see which one gave the better
   > otput like that … lets do one call with opus 5 and then with sonnet 5

   *One test call to each model first, then the practice, exam and eval mailboxes on Opus 5 and Sonnet 5. Opus scored
   lower than DeepSeek; replaying its saved answers (no further spend) traced it to the `%` parser and the dropped
   rate notice. After both fixes, Opus and DeepSeek produce identical exam sheets and 99.6 on practice.*

4. > how are u sure that the qeustions like, in the brief.pdf , performance improvement and with real life data it would
   > work better like that

   *Separated what the tests prove from what they can't; the gaps became the next three items.*

5. > Turn on prompt caching to halve the cost … Hand-check the whole exam sheet, not just the 3 disputed rows …
   > Self-check on every invoice. If the lines we read don't add up to the printed total, re-read the document.

   *All three: invoices are re-read once when their lines don't add up (1 of 84 needed it); Claude calls mark the
   schema and instructions cacheable, with a fallback if the API rejects it (checked against a local fake API); every
   line of all 25 exam invoices re-priced by a separate script with no model, and every other row checked by hand.*

6. > also the prompt injection is or should be taken care the obvious ones / Avoid: Regexes designed for one invoice
   > template / ssuming columns always occur in the same order / Treating a particular phrase as the only way to
   > express a waiver

   *Audited every regex in the harness against the list; two assumed the practice layout and were replaced.*

7. > All three CLI commands run from fresh processes. State stays inside --state-dir. … is all of that done?

   *Checked every file write: Python's `.pyc` files were the one thing landing outside `--state-dir`; turned off.*

8. > Extract meaningful facts and save them under --state-dir. Each fact should retain: … Received date …

   *The received date lived only on the document record; every fact record now carries it too.*

9. > Wrong or missing authority … Already credited … One shipment only … using differnt wordings for the mai llis not
   > changin gthings right,and vary where the important details appear … Test partial credits, credits for unrelated
   > charges, and credits that arrive in a later batch, - Include an email that tells the AI to ignore the contract

   *Seven new eval mailboxes, one per case. Writing them found the credit-netting bug; 25 of 25 pass after the fix,
   and three fail with the netting switched off.*

10. > there are 2 .env's as well idk what is wron fix this as well and some are not gitignore the importatnat files
    > they shoudl n't be in commit

    *The extra `.env` files were outside the repo; the result files are deliverables and stay committed. `.gitignore`
    now ignores every `.env.*` except the example.*

11. > use @graphify as well for the better things so i can save mymore tokens always use the relevant skills we have

    *Mapped `harness/` into a code graph (structural, no model tokens), refreshed after each change.*

12. > u write that and writein what i write with ai things like that

    *Written by Claude Code at my request (not by me): `month_of()` now also reads months printed as `Dec-2034`, and a
    decoy eval, `screening_correctly_billed_no_overcharge`, checks that a correctly billed screening charge next to a
    waived, partly credited fee is not flagged as an overcharge. 26 of 26 evals pass.*

13. > make the whole code btter then if somethigns are missing then

    *Written by Claude Code at my request: amounts printed in accounting style, `(1,234.50)`, now read as negative;
    year-first dates (`2035/07/18`) are parsed; pickup events named `TENDERED` or `RECEIVED_FROM_SHIPPER` count as
    pickups, while consignee-side events never do. Practice 99.6, exam sheet unchanged, 26 of 26 evals.*
