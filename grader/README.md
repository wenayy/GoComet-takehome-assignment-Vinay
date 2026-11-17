# Grader

Replays a mailbox through the harness one batch at a time (`ingest`, `report`, `feedback`), in supervised and
autonomous mode, and scores the reports out of 100: detection with quotes, amounts, adaptation to new documents,
learning from feedback, conflicts and missing documents, actions per mode, confidence calibration and clean controls.

- `extract.py`: the text layer for `.eml`, PDF and Excel attachments (also used by the harness, so cited quotes match).
- `run_grader.py`, `score.py`: replay and scoring (`make grade`).
- `make_sheet.py`, `grade_sheet.py`: the exam summary sheet (`make sheet`).
- `schema/report.schema.json`: the report format.
