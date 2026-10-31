"""Read one batch folder of .eml files into documents: the email body and each attachment, page by page.

Text comes from grader/extract.py so that every quote we cite is checked against the same text the grader reads.
"""
from __future__ import annotations

import hashlib
import sys
from dataclasses import asdict, dataclass, field
from email.utils import parsedate_to_datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "grader"))
import extract  # noqa: E402  (the grader's reader)


@dataclass
class Document:
    ref: str                 # 'batch_01/0014.eml' or 'batch_01/0014.eml#INV-1.pdf'
    batch: str
    email_ref: str           # the email this document came with
    name: str                # file name ('0014.eml', 'INV-1.pdf')
    ext: str
    received: str            # UTC date of the carrying email, YYYY-MM-DD
    received_at: str         # full UTC timestamp, for ordering
    sha: str
    pages: list[str] = field(default_factory=list)
    scan: bool = False       # an image-only PDF: pages hold OCR text, quotes can't be checked

    def to_json(self) -> dict:
        return asdict(self)


def _email_docs(batch: str, path: Path) -> list[tuple[Document, bytes]]:
    data = path.read_bytes()
    msg = extract.parse_eml(data)
    dt = parsedate_to_datetime(str(msg["Date"]))
    if dt.tzinfo is not None:
        from datetime import timezone
        dt = dt.astimezone(timezone.utc)
    received, received_at = dt.strftime("%Y-%m-%d"), dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    email_ref = f"{batch}/{path.name}"
    out = [(Document(ref=email_ref, batch=batch, email_ref=email_ref, name=path.name, ext="eml", received=received,
                     received_at=received_at, sha=hashlib.sha256(data).hexdigest(),
                     pages=extract.pages_of(data, "eml")), data)]
    for name, att in extract.attachments(data):
        ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
        doc = Document(ref=f"{email_ref}#{name}", batch=batch, email_ref=email_ref, name=name, ext=ext,
                       received=received, received_at=received_at, sha=hashlib.sha256(att).hexdigest())
        if ext == "pdf" and extract.pdf_is_image_only(att):
            doc.scan = True
        elif ext in ("pdf", "xlsx"):
            doc.pages = extract.pages_of(att, ext)
        else:
            continue  # nothing we can read
        out.append((doc, att))
    return out


def read_batch(batch_dir: str | Path) -> list[tuple[Document, bytes]]:
    """Every document in the batch with its raw bytes (the bytes are needed to OCR scans)."""
    batch_dir = Path(batch_dir)
    docs: list[tuple[Document, bytes]] = []
    for f in sorted(batch_dir.glob("*.eml")):
        docs.extend(_email_docs(batch_dir.name, f))
    return docs


def norm(s: str) -> str:
    return extract.norm(s)


def find_quote(pages: list[str], quote: str) -> int | None:
    """1-based page holding the quote (whitespace-insensitive), or None."""
    q = norm(quote)
    for i, p in enumerate(pages, 1):
        if q and q in norm(p):
            return i
    return None
