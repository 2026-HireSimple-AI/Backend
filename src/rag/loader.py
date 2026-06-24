"""
PDF 문서를 로딩하고 청크 단위로 분할하는 모듈
"""

import os
import pdfplumber
from typing import List, Dict

# RAG 문서 디렉토리 (Backend/rag_docs/ 에 PDF 파일 복사)
RAG_DOCS_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "rag_docs")


def extract_text_from_pdf(pdf_path: str) -> str:
    """PDF에서 텍스트 추출"""
    text = ""
    try:
        with pdfplumber.open(pdf_path) as pdf:
            for page in pdf.pages:
                page_text = page.extract_text()
                if page_text:
                    text += page_text + "\n"
    except Exception as e:
        print(f"[RAG] PDF 추출 실패 {pdf_path}: {e}")
    return text


def chunk_text(text: str, chunk_size: int = 500, overlap: int = 100) -> List[str]:
    """텍스트를 청크 단위로 분할"""
    chunks = []
    start = 0
    while start < len(text):
        end = start + chunk_size
        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)
        start += chunk_size - overlap
    return chunks


def load_all_documents() -> List[Dict]:
    """rag_docs/ 폴더의 모든 PDF를 로딩하여 청크 목록 반환"""
    docs = []
    if not os.path.exists(RAG_DOCS_DIR):
        print(f"[RAG] rag_docs 디렉토리 없음: {RAG_DOCS_DIR}")
        return docs

    for filename in os.listdir(RAG_DOCS_DIR):
        if not filename.endswith(".pdf"):
            continue
        pdf_path = os.path.join(RAG_DOCS_DIR, filename)
        print(f"[RAG] 문서 로딩: {filename}")
        text = extract_text_from_pdf(pdf_path)
        chunks = chunk_text(text)
        for i, chunk in enumerate(chunks):
            docs.append({
                "id": f"{filename}_{i}",
                "text": chunk,
                "source": filename
            })
        print(f"[RAG]   → {len(chunks)}개 청크 생성")

    print(f"[RAG] 총 {len(docs)}개 청크 로딩 완료")
    return docs
