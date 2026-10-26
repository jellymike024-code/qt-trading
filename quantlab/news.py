"""Validated append-only evidence ingestion for the scheduled research agent."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from .sentiment import load_events


def ingest(candidate, destination):
    candidate, destination = Path(candidate), Path(destination)
    new = load_events(candidate)
    old = load_events(destination) if destination.exists() else []
    seen = {json.dumps(e, sort_keys=True, ensure_ascii=False) for e in old}
    additions = []
    for event in new:
        canonical = json.dumps(event, sort_keys=True, ensure_ascii=False)
        if canonical not in seen:
            seen.add(canonical)
            additions.append(event)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(".jsonl.tmp")
    temporary.write_text(''.join(json.dumps(e, ensure_ascii=False) + '\n' for e in old + additions), encoding="utf-8")
    os.replace(temporary, destination)
    return len(additions)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Validate and ingest observed news evidence")
    parser.add_argument("candidate", type=Path)
    args = parser.parse_args()
    dest = Path(__file__).resolve().parent.parent / "data" / "news" / "events.jsonl"
    print(json.dumps({"added": ingest(args.candidate, dest), "destination": str(dest)}, ensure_ascii=False))
