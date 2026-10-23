import os, json, requests
from dotenv import load_dotenv
load_dotenv()

URL = "https://www.bizinfo.go.kr/uss/rss/bizinfoApi.do"
params = {"crtfcKey": os.environ["BIZINFO_API_KEY"], "dataType": "json", "searchCnt": 0}
res = requests.get(URL, params=params, timeout=120)
res.raise_for_status()
data = res.json()

root = data.get("jsonArray", data)
items = root.get("item", root) if isinstance(root, dict) else root
if isinstance(items, dict):
    items = [items]

print("전체 공고 수:", len(items))
print("첫 공고의 키 목록:", list(items[0].keys()))
print(json.dumps(items[0], ensure_ascii=False, indent=2)[:1500])

os.makedirs("data/bizinfo", exist_ok=True)
with open("data/bizinfo/raw.json", "w", encoding="utf-8") as f:
    json.dump(items, f, ensure_ascii=False, indent=2)
