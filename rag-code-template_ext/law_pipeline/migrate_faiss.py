"""One-time migration of a trusted local IndexFlatL2; no embedding API calls."""
import argparse
import json
from pathlib import Path

from core.vector_store import PgVectorStore
from .extract import digest, read_jsonl


def migrate(directory: Path, *, trusted=False, model=None, replace=False):
    if not trusted:
        raise ValueError("pickle 역직렬화가 필요합니다. 신뢰하는 로컬 파일만 --trusted-local-files로 허용하세요.")
    import faiss
    from langchain_community.vectorstores import FAISS
    from langchain_core.embeddings import Embeddings

    class NoAPI(Embeddings):
        def embed_documents(self, texts):
            raise RuntimeError("이관에서는 임베딩 API를 호출하지 않습니다.")

        def embed_query(self, text):
            raise RuntimeError("이관에서는 임베딩 API를 호출하지 않습니다.")

    manifest_path = directory / "manifest.json"
    meta = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}
    if meta:
        for name, expected in meta.get("files", {}).items():
            # Only the known local sidecars may be opened from the manifest.
            if name not in {"index.faiss", "index.pkl", "chunks.jsonl", "supplements.jsonl"}:
                raise ValueError("알 수 없는 manifest 파일입니다.")
            if digest((directory / name).read_bytes()) != expected:
                raise ValueError(f"인덱스 파일 해시 불일치: {name}")
        if model and model != meta["embedding_model"]:
            raise ValueError("지정 모델이 manifest와 다릅니다.")
        model = meta["embedding_model"]
    if not model:
        raise ValueError("manifest가 없으면 기존 임베딩 모델을 --model로 명시하세요.")
    store = FAISS.load_local(str(directory), NoAPI(), allow_dangerous_deserialization=True)
    if not isinstance(store.index, faiss.IndexFlatL2) or store.index.ntotal == 0:
        raise ValueError("비어 있지 않은 IndexFlatL2만 이관할 수 있습니다.")
    if meta and (store.index.d != meta["vector_dimension"] or store.index.ntotal != meta["vectors"]):
        raise ValueError("FAISS 차원·개수가 manifest와 다릅니다.")
    docs = [store.docstore.search(store.index_to_docstore_id[i]) for i in range(store.index.ntotal)]
    collection = directory.name
    if collection == "pdf" and any(d.metadata.get("doc_type") != "law" or
                                  not {"law_name", "article_no", "page"} <= d.metadata.keys() for d in docs):
        raise ValueError("법령 출처 메타데이터가 부족합니다. 원본 PDF를 재적재하세요.")
    vectors = store.index.reconstruct_n(0, store.index.ntotal).tolist()
    supplements = read_jsonl(directory / "supplements.jsonl") if meta else []
    meta.pop("files", None)
    if manifest_path.exists():
        meta["managed_by"] = "law_pipeline"
    target = PgVectorStore(collection, model=model, dimensions=meta.get("requested_dimensions"))
    return target.write_vectors(docs, vectors, mode="replace" if replace else "create",
                                manifest=meta, supplements=supplements)


if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path, help="旧 인덱스의 pdf 또는 web 디렉터리")
    parser.add_argument("--trusted-local-files", action="store_true")
    parser.add_argument("--model")
    parser.add_argument("--replace", action="store_true")
    args = parser.parse_args()
    result = migrate(args.directory, trusted=args.trusted_local_files, model=args.model, replace=args.replace)
    print(json.dumps({k: result[k] for k in ("storage", "vectors", "embedding_model", "vector_dimension")}, indent=2))
