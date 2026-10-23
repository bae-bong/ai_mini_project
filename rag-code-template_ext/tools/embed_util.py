import os

def get_embeddings():
    if os.environ.get("EMBED_MODE") == "local":
        try:
            from langchain_huggingface import HuggingFaceEmbeddings
        except ImportError:
            from langchain_community.embeddings import HuggingFaceEmbeddings
        return HuggingFaceEmbeddings(
            model_name="jhgan/ko-sroberta-multitask",
            encode_kwargs={"normalize_embeddings": True},
        )
    from langchain_openai import OpenAIEmbeddings
    return OpenAIEmbeddings()

def index_dir():
    return "faiss_db/notice_test_local" if os.environ.get("EMBED_MODE") == "local" else "faiss_db/notice_test"
