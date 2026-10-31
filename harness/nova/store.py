"""Everything the harness knows lives under --state-dir, as JSON, so a fresh process can pick up where the last stopped.

    docs/<sha>.json        a Document (pages of text) per document
    facts/<sha>.json       what the extractor read from it
    index.json             doc ref -> sha, in arrival order
    feedback.json          every feedback item received, with the batch it came after
    reports.json           the items of the last report, so the next one can say what it supersedes
    llm_cache/             model responses keyed by prompt hash
"""
from __future__ import annotations

import json
import os
from pathlib import Path


class Store:
    def __init__(self, state_dir: str | Path):
        self.root = Path(state_dir)
        for sub in ("docs", "facts", "llm_cache"):
            (self.root / sub).mkdir(parents=True, exist_ok=True)

    # ---- generic json ------------------------------------------------------------------------------------------------
    def _read(self, rel: str, default):
        p = self.root / rel
        if not p.is_file():
            return default
        return json.loads(p.read_text())

    def _write(self, rel: str, value) -> None:
        p = self.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(p.suffix + ".tmp")
        tmp.write_text(json.dumps(value, indent=1, sort_keys=False, default=str))
        os.replace(tmp, p)

    # ---- documents ---------------------------------------------------------------------------------------------------
    def index(self) -> list[dict]:
        return self._read("index.json", [])

    def save_doc(self, doc: dict) -> None:
        self._write(f"docs/{doc['sha']}.json", doc)
        idx = [e for e in self.index() if e["ref"] != doc["ref"]]
        idx.append({"ref": doc["ref"], "sha": doc["sha"], "received_at": doc["received_at"], "batch": doc["batch"]})
        idx.sort(key=lambda e: (e["received_at"], e["ref"]))
        self._write("index.json", idx)

    def doc(self, sha: str) -> dict:
        return self._read(f"docs/{sha}.json", None)

    def docs(self) -> list[dict]:
        return [self.doc(e["sha"]) | {"ref": e["ref"]} for e in self.index()]

    # ---- facts -------------------------------------------------------------------------------------------------------
    def facts(self, sha: str):
        return self._read(f"facts/{sha}.json", None)

    def save_facts(self, sha: str, facts: dict) -> None:
        self._write(f"facts/{sha}.json", facts)

    # ---- feedback and reports ----------------------------------------------------------------------------------------
    def feedback(self) -> list[dict]:
        return self._read("feedback.json", [])

    def save_feedback(self, items: list[dict]) -> None:
        self._write("feedback.json", items)

    def last_batch(self) -> str | None:
        idx = self.index()
        return max((e["batch"] for e in idx), default=None)

    def previous_items(self, mode: str) -> dict:
        return self._read(f"reports_{mode}.json", {})

    def save_items(self, mode: str, items: dict) -> None:
        self._write(f"reports_{mode}.json", items)

    # ---- llm cache ---------------------------------------------------------------------------------------------------
    # NOVA_CACHE_DIR (local runs only, set in .env) shares answers across state dirs, so replaying the mailbox in both
    # modes or re-running the grader doesn't pay for the same document twice. The grader never sets it.
    def _shared(self, key: str) -> Path | None:
        d = os.environ.get("NOVA_CACHE_DIR")
        return Path(d) / f"{key}.json" if d else None

    def cache_get(self, key: str):
        hit = self._read(f"llm_cache/{key}.json", None)
        shared = self._shared(key)
        if hit is None and shared is not None and shared.is_file():
            hit = json.loads(shared.read_text())
        return hit

    def cache_put(self, key: str, value) -> None:
        self._write(f"llm_cache/{key}.json", value)
        shared = self._shared(key)
        if shared is not None:
            shared.parent.mkdir(parents=True, exist_ok=True)
            tmp = shared.with_suffix(f".{os.getpid()}.tmp")
            tmp.write_text(json.dumps(value))
            os.replace(tmp, shared)
