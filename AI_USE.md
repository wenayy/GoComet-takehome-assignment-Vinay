# AI use

## Tools

- **Claude Code** (model: Claude Opus 5.5) in the terminal: reading the brief and data with me, design discussion, writing
  and testing the harness code, git commits.
- **Claude (Anthropic API)** inside the harness itself: reading documents into facts and transcribing the scanned invoice. It never decides a verdict or an amount; Python does that from the glossary rules.
uses the anthropic compatible api key deepseek as well for the examples and then compare them with the real api key of anthropic key as well 

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

## Written without AI


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

