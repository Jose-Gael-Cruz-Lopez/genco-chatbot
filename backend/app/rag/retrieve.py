from app.db import get_supabase
from app.rag.embeddings import embed_text


def retrieve(query: str, k: int = 5, threshold: float = 0.0) -> list[dict]:
    sb = get_supabase()
    qvec = embed_text(query)
    resp = sb.rpc("match_documents", {
        "query_embedding": qvec,
        "match_count": k,
        "similarity_threshold": threshold,
    }).execute()
    return resp.data or []
