import glob, os
from pypdf import PdfReader

for path in sorted(glob.glob("data/bizinfo/pdf/*.pdf")):
    r = PdfReader(path)
    texts = [(p.extract_text() or "") for p in r.pages]
    total = sum(len(t.strip()) for t in texts)
    empty = sum(1 for t in texts if len(t.strip()) < 20)
    flag = "OK " if total > 500 and empty < len(texts) / 2 else "확인필요 "
    print(f"{flag}{os.path.basename(path)} | {len(r.pages)}쪽 | 글자 {total}자 | 빈 페이지 {empty}")
print("\n--- 첫 파일 앞부분 미리보기 ---")
first = sorted(glob.glob("data/bizinfo/pdf/*.pdf"))[0]
print((PdfReader(first).pages[0].extract_text() or "")[:400])
