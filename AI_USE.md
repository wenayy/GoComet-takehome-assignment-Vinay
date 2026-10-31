# AI use

## Tools

- **Claude Code** (model: Claude Opus 5.5) in the terminal: reading the brief and data with me, design discussion, writing
  and testing the harness code, git commits.
- **Claude (Anthropic API)** inside the harness itself: reading documents into facts and transcribing the scanned
  invoice. It never decides a verdict or an amount; Python does that from the glossary rules.

## How I worked with them

I used Claude Code as a pair: first to understand the brief, then to build in small steps, testing each one and
committing it before moving on.

1. **Understanding the brief.** I asked it to read the repo, the JD and the recruiter email and explain the task and any
   traps. It walked me through the overcharge worked example line by line until the arithmetic made sense to me (rate
   per kg by weight band, pickup date picks the period, fuel is a percentage of corrected freight).
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

## Written without AI

Nothing yet. I plan to write some of the eval cases (`evals/cases/`) and their mailboxes myself.

## Prompt log

Session with Claude Code, 2026-10-31. My prompts are copied as I typed them (trimmed only of file paths into my local
caches); the tool's work is summarised in italics.

1. > https://github.com/gocomet-india/nova-takehome can u check this i have been given assignment i need to solve can
   > u check this whole is there any catch or any trap in the how can i solve this i am not able to understand what is
   > it trying to say [JD PDF and recruiter email attached]

   *Read README, GLOSSARY, SUBMISSION, the grader, workflows and the JD. Explained the task, the three levels, and the
   traps: practice answers are public (hardcoding fails the hidden set), instructions inside emails, one-off waivers,
   no execute in supervised mode, escalation cap, decoys, Schedule 1 authority, the scanned exam invoice.*

2. > yes [walk me through one worked example]

   *Walked through `data/examples/overcharge`: invoice line, pickup date from tracking, Annex A band and period, fuel
   recalculated on corrected freight, 196.92, and why the 44 kg decoy is correct.*

3. > should i first clone the repo or how can i submit it ... i have only understood that i need to read the email
   > .eml files with the invoice ... i need not to change that reading file of grader.py ... rather than anthropic key
   > i would also like to add there gpt or deepseek key cause that would be cheap for me

   *Explained submission (private repo, collaborator, commit hash), that we audit carrier invoices rather than make
   them, weight bands, using `grader/extract.py` as is, and that the grader can only reach api.anthropic.com, so
   cheaper providers are for local runs through an Anthropic-compatible base URL.*

4. > also i need to make my own evals since the BRIEF.pdf says ... the code might behave something else so it should
   > learn as well

   *Explained evals (own mailboxes + YAML expectations) versus learning (the `feedback` command), and listed 13 eval
   ideas covering layouts, authority, injection, one-off waivers, band edges, backdated amendments, the $5,000 edge,
   feedback scope.*

5. > can u start writing it and keep making the relevant commits ... and start building it whole and i will think or
   > chip in if i think where we can make it better

   *Built in steps, each tested and committed: skeleton (reader, state store, CLI); LLM fact extraction with quote
   repair and column-mapped spreadsheets; the rule engine (world model, pricing by precedence, six checks, actions,
   confidence, supersedes); `.env` handling. Tested the engine on hand-written facts for the overcharge example.*

6. > also llm dont do the final findings? the sequence of things right ocr, harness and then persistent storage is that
   > how things are happening? and can we use https://github.com/Dicklesworthstone/llm_aided_ocr ... keep this as well
   > dont remove the current checking of the reader, grader one ... can u check if that is a great thing or make our
   > product better

   *Confirmed the model only reads and Python decides. Reviewed the OCR project (Tesseract + text-only LLM correction)
   and recommended against adding it; implemented the two-reader cross-check for scans instead and tested the local
   reader on the exam scan.*
