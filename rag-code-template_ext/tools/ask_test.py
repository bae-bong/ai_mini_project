import sys
from dotenv import load_dotenv
load_dotenv()
try:
    from langchain_community.vectorstores import FAISS
except ImportError:
    from langchain.vectorstores import FAISS
from embed_util import get_embeddings, index_dir

db = FAISS.load_local(index_dir(), get_embeddings(), allow_dangerous_deserialization=True)
for q in sys.argv[1:]:
    print("\n질문:", q)
    for doc, score in db.similarity_search_with_score(q, k=4):
        m = doc.metadata
        print(f"  [{score:.3f}] {m['title'][:35]} | {m['period']} | p.{m['page']} ({m['section']})")
