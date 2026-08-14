"""
batch_status.py -- show the progress of a running Message Batch.

``ask_json_batch`` polls silently, so a long run looks stalled even while it works. This
reads the batch state from a second terminal without touching the run.

    poetry run python tools/batch_status.py msgbatch_01M5EJADijvFa2V1kuFQLTS1
    poetry run python tools/batch_status.py msgbatch_... --watch

The key is read from ``.api_key`` in the project root, the same file the pipeline uses,
so nothing has to be exported into the environment.

Read-only: it never cancels or modifies the batch.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _client():
    import anthropic

    key_file = ROOT / ".api_key"
    if not key_file.exists():
        sys.exit(f"no API key at {key_file}")
    return anthropic.Anthropic(api_key=key_file.read_text(encoding="utf-8").strip())


def _line(b) -> str:
    c = b.request_counts
    done = c.succeeded + c.errored + c.canceled + c.expired
    total = done + c.processing
    pct = f"{100 * done / total:5.1f} %" if total else "  n/a"
    return (f"{b.processing_status:10} {pct}  "
            f"done {done:4}/{total:<4} "
            f"succeeded {c.succeeded:4}  errored {c.errored:3}  "
            f"processing {c.processing:4}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("batch_id", help="the msgbatch_... id printed by the run")
    ap.add_argument("--watch", action="store_true", help="keep refreshing until it ends")
    ap.add_argument("--every", type=int, default=30, help="seconds between refreshes")
    args = ap.parse_args(argv)

    client = _client()
    while True:
        b = client.messages.batches.retrieve(args.batch_id)
        print(f"{time.strftime('%H:%M:%S')}  {_line(b)}", flush=True)
        if b.processing_status == "ended":
            print("\nended -- the run will fetch the results on its next poll (<= 15 s).")
            return 0
        if not args.watch:
            return 0
        time.sleep(args.every)


if __name__ == "__main__":
    raise SystemExit(main())
