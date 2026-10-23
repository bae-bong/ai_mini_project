#!/usr/bin/env bash
# repo 루트 어디서든 RAG 평가 실행: ./eval.sh --set holdout_set.csv --label holdout
exec python "$(cd "$(dirname "$0")" && pwd)/rag-code-template_ext/eval/run_eval.py" "$@"
