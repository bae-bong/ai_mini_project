"""은빈 파이프라인 결과(data/bizinfo/meta/*.json + data/bizinfo/pdf/{id}.pdf)를 /upload-notice 로 올린다.

같은 공고를 다시 올리면 덮어쓴다 (키: notice_id + 파일명). raw.json이 있으면 사업개요·신청방법도 개요 청크에 넣는다.

실행 (rag-code-template_ext 에서):
  python tools/upload_notices.py                              # 기본: compose nginx 경로
  python tools/upload_notices.py --base-url http://<IP>:8080/api/rag --only PBLN_000000000119356
토큰: .env 의 JWT_SECRET 으로 직접 발급 (eval/run_eval.py 와 같은 방식)
"""
import argparse, glob, html, json, os, re, sys, time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
from dotenv import load_dotenv
from jose import jwt

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")


def token():
    secret = os.getenv("JWT_SECRET")
    if not secret:
        sys.exit(".env에 JWT_SECRET이 없습니다.")
    now = datetime.now(timezone.utc)
    return jwt.encode({"sub": "0", "username": "notice-uploader@local", "iss": "simple-auth-server", "iat": now,
                       "exp": now + timedelta(hours=1)}, secret, algorithm="HS256")


def clean(s):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", s or ""))).strip()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", default=os.getenv("RAG_BASE_URL", "http://127.0.0.1:8080/api/rag"))
    ap.add_argument("--data", default=str(ROOT / "data/bizinfo"))
    ap.add_argument("--only", default="", help="쉼표로 notice_id 지정")
    args = ap.parse_args()

    data = Path(args.data)
    metas = sorted(glob.glob(str(data / "meta/*.json")))
    if not metas:
        sys.exit(f"meta JSON이 없습니다: {data}/meta (tools/make_notice_meta.py 먼저 실행)")
    raw = {}
    if (data / "raw.json").exists():
        raw = {it["pblancId"]: it for it in json.loads((data / "raw.json").read_text(encoding="utf-8"))}
    only = set(filter(None, args.only.split(",")))
    headers = {"Authorization": f"Bearer {token()}"}
    ok = fail = 0
    for path in metas:
        meta = json.loads(Path(path).read_text(encoding="utf-8"))
        nid = meta.get("notice_id")
        if only and nid not in only:
            continue
        it = raw.get(nid, {})
        meta.setdefault("summary", clean(it.get("bsnsSumryCn")) or None)
        meta.setdefault("apply_how", clean(it.get("reqstMthPapersCn")) or None)
        pdf = data / "pdf" / f"{nid}.pdf"
        files = {"file": (pdf.name, pdf.open("rb"), "application/pdf")} if pdf.exists() else None
        try:
            r = requests.post(f"{args.base_url}/upload-notice", headers=headers, timeout=180,
                              data={"meta": json.dumps(meta, ensure_ascii=False)}, files=files)
            if r.ok:
                ok += 1
                body = r.json()
                print("OK  ", nid, body["message"], body.get("note") or "")
            else:
                fail += 1
                print("FAIL", nid, r.status_code, r.text[:200])
        finally:
            if files:
                files["file"][1].close()
        time.sleep(0.2)
    print(f"\n완료: 성공 {ok} / 실패 {fail}")


if __name__ == "__main__":
    main()
