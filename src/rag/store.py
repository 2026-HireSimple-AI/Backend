"""
Supabase pgvector 벡터 스토어 - 문서 임베딩 저장 및 유사도 검색
"""

import os
import httpx
from typing import List, Dict
from database import supabase

EMBEDDING_MODEL = "text-embedding-3-small"
EMBEDDING_DIM = 1536


async def _get_embedding(text: str) -> List[float]:
    """OpenAI API로 텍스트 임베딩 생성"""
    api_key = os.getenv("OPENAI_API_KEY", "")
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            "https://api.openai.com/v1/embeddings",
            headers={"Authorization": f"Bearer {api_key}"},
            json={"model": EMBEDDING_MODEL, "input": text}
        )
        resp.raise_for_status()
        return resp.json()["data"][0]["embedding"]


def _get_embedding_sync(text: str) -> List[float]:
    """동기 버전 임베딩 (초기화 시 사용)"""
    import httpx as _httpx
    api_key = os.getenv("OPENAI_API_KEY", "")
    with _httpx.Client(timeout=30.0) as client:
        resp = client.post(
            "https://api.openai.com/v1/embeddings",
            headers={"Authorization": f"Bearer {api_key}"},
            json={"model": EMBEDDING_MODEL, "input": text}
        )
        resp.raise_for_status()
        return resp.json()["data"][0]["embedding"]


def init_vector_store(docs: List[Dict]) -> None:
    """법령 문서 청크를 Supabase law_documents 테이블에 임베딩하여 저장"""
    if not docs:
        print("[RAG] 임베딩할 문서 없음 - rag_docs/ 폴더에 PDF를 추가하세요")
        return

    # 이미 데이터가 있으면 재사용
    existing = supabase.table("law_documents").select("id", count="exact").execute()
    if existing.count and existing.count > 0:
        print(f"[RAG] Supabase law_documents 기존 데이터 재사용 ({existing.count}개)")
        return

    print(f"[RAG] Supabase에 {len(docs)}개 청크 임베딩 중...")

    batch = []
    for i, doc in enumerate(docs):
        try:
            embedding = _get_embedding_sync(doc["text"])
            batch.append({
                "id": doc["id"],
                "content": doc["text"],
                "source": doc["source"],
                "embedding": embedding
            })

            # 10개씩 배치 저장
            if len(batch) >= 10:
                supabase.table("law_documents").upsert(batch).execute()
                print(f"[RAG]   {i + 1}/{len(docs)} 청크 저장 완료")
                batch = []

        except Exception as e:
            print(f"[RAG] 청크 임베딩 실패 ({doc['id']}): {e}")

    # 남은 배치 저장
    if batch:
        supabase.table("law_documents").upsert(batch).execute()

    print(f"[RAG] Supabase 임베딩 저장 완료")


async def retrieve(query: str, n_results: int = 6) -> str:
    """쿼리와 유사한 법령 청크를 Supabase RPC로 검색"""
    try:
        query_embedding = await _get_embedding(query)

        result = supabase.rpc("match_law_documents", {
            "query_embedding": query_embedding,
            "match_count": n_results
        }).execute()

        if not result.data:
            return ""

        parts = []
        for row in result.data:
            parts.append(f"[출처: {row.get('source', '')}]\n{row.get('content', '')}")

        return "\n\n---\n\n".join(parts)

    except Exception as e:
        print(f"[RAG] Supabase 검색 실패: {e}")
        return ""
