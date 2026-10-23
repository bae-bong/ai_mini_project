from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .extract import preprocess

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description="법령 PDF 전처리·FAISS 적재·근거 검색")
    parser.add_argument("--env-file", type=Path, default=ROOT / ".env")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("preprocess")
    p.add_argument("--manifest", type=Path, default=ROOT / "data/laws/sources.json")
    p.add_argument("--raw-dir", type=Path, default=ROOT.parent / "upload-paper")
    p.add_argument("--output", type=Path, default=ROOT / "data/laws/processed")
    p.add_argument("--as-of")
    b = sub.add_parser("build")
    b.add_argument("--processed", type=Path, default=ROOT / "data/laws/processed")
    b.add_argument("--index", type=Path, default=ROOT / "faiss_db/pdf")
    b.add_argument("--model", default="text-embedding-ada-002")
    b.add_argument("--dimensions", type=int)
    b.add_argument("--replace", action="store_true")
    s = sub.add_parser("search")
    s.add_argument("question")
    s.add_argument("--index", type=Path, default=ROOT / "faiss_db/pdf")
    s.add_argument("--k", type=int, default=5)
    s.add_argument("--mode", choices=["hybrid", "dense"], default="hybrid")
    s.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        if args.command != "preprocess":
            from dotenv import load_dotenv
            load_dotenv(args.env_file, override=False)
        if args.command == "preprocess":
            report = preprocess(args.manifest, args.raw_dir, args.output, args.as_of)
            print(json.dumps({k: v for k, v in report.items() if k != "sources"}, ensure_ascii=False, indent=2))
        elif args.command == "build":
            from .index import build_index
            result = build_index(args.processed, args.index, args.model, args.dimensions, args.replace)
            print(json.dumps({k: result[k] for k in ["as_of", "vectors", "vector_dimension", "embedding_model"]}, indent=2))
        elif args.command == "search":
            from .index import search
            result = search(args.index, args.question, args.k, mode=args.mode)
            text = json.dumps(result, ensure_ascii=False, indent=2)
            if args.output:
                args.output.parent.mkdir(parents=True, exist_ok=True)
                args.output.write_text(text)
            else:
                print(text)
    except Exception as exc:
        # Provider errors may include endpoint URLs or request data; don't dump credentials/tracebacks.
        if isinstance(exc, (ValueError, FileNotFoundError)):
            print(f"실패: {exc}", file=sys.stderr)
        else:
            print(f"실패: {type(exc).__name__}. API 접근·패키지·입력 파일을 확인하세요.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
