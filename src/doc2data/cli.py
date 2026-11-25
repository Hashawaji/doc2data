"""Command line: generate samples, ingest PDFs, evaluate an extractor, export, serve."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import config
from .db import init_db, make_engine
from .extractors import EXTRACTORS, ExtractionError, get_extractor


def _session_factory():
    return init_db(make_engine(config.load().database_url))


def _pdfs(paths: list[Path]) -> list[Path]:
    found: list[Path] = []
    for path in paths:
        if path.is_dir():
            found.extend(sorted(p for p in path.iterdir() if p.suffix.lower() == ".pdf"))
        else:
            found.append(path)
    return found


def cmd_samples(args: argparse.Namespace) -> int:
    from .samples import generate

    specs = generate(args.out)
    print(f"Wrote {len(specs)} invoices and ground_truth.json to {args.out}/")
    return 0


def cmd_ingest(args: argparse.Namespace) -> int:
    from .pipeline import ingest_file

    extractor = get_extractor(args.extractor or config.load().extractor)
    counts: dict[str, int] = {}
    with _session_factory()() as session:
        for path in _pdfs(args.paths):
            outcome = ingest_file(session, path, extractor, force=args.force)
            document = outcome.document
            reasons = document.error or "; ".join(i.message for i in document.issues if i.severity == "error")
            if outcome.error:
                label, reasons = "kept earlier result", f"re-read failed: {outcome.error}"
            elif outcome.created:
                label = document.status
            else:
                label = "already read"
            counts[label] = counts.get(label, 0) + 1
            print(f"{path.name:<22} {label:<17} {reasons}")
    print(", ".join(f"{count} {label}" for label, count in sorted(counts.items())) or "No PDFs found.")
    return 0


def cmd_eval(args: argparse.Namespace) -> int:
    from .evaluate import run, to_markdown

    extractor = get_extractor(args.extractor or config.load().extractor)
    report = run(args.samples, extractor)
    markdown = to_markdown(report)
    print(markdown)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(markdown)
        args.out.with_suffix(".json").write_text(json.dumps(report.to_dict(), indent=2) + "\n")
        print(f"Saved {args.out} and {args.out.with_suffix('.json')}")
    if report.documents and len(report.failures) == len(report.documents):
        print("doc2data: the extractor could not read any of the documents.", file=sys.stderr)
        return 1
    return 0


def cmd_export(args: argparse.Namespace) -> int:
    from .export import write_csv

    with _session_factory()() as session, args.out.open("w", newline="") as handle:
        rows = write_csv(session, handle)
    print(f"Wrote {rows} rows to {args.out}")
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    import uvicorn

    uvicorn.run("doc2data.web.app:create_app", factory=True, host=args.host, port=args.port)
    return 0


def main(argv: list[str] | None = None) -> int:
    config.load_env_file()  # before anything reads a setting or an API key
    parser = argparse.ArgumentParser(prog="doc2data", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    samples = commands.add_parser("samples", help="generate the synthetic invoices and their ground truth")
    samples.add_argument("--out", type=Path, default=Path("samples"))
    samples.set_defaults(run=cmd_samples)

    ingest = commands.add_parser("ingest", help="read PDFs (files or folders) into the database")
    ingest.add_argument("paths", nargs="+", type=Path)
    ingest.add_argument("--extractor", choices=list(EXTRACTORS))
    ingest.add_argument("--force", action="store_true", help="read files again even if already read")
    ingest.set_defaults(run=cmd_ingest)

    evaluate = commands.add_parser("eval", help="score an extractor against the labelled samples")
    evaluate.add_argument("--samples", type=Path, default=Path("samples"))
    evaluate.add_argument("--extractor", choices=list(EXTRACTORS))
    evaluate.add_argument("--out", type=Path, help="also save the report here (.md, plus .json beside it)")
    evaluate.set_defaults(run=cmd_eval)

    export = commands.add_parser("export", help="write all invoices to a CSV file")
    export.add_argument("--out", type=Path, default=Path("invoices.csv"))
    export.set_defaults(run=cmd_export)

    serve = commands.add_parser("serve", help="run the dashboard")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    serve.set_defaults(run=cmd_serve)

    args = parser.parse_args(argv)
    try:
        return args.run(args)
    except ExtractionError as exc:  # for example a missing API key or an unknown extractor name
        print(f"doc2data: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
