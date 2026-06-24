from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
from routers import analysis, interview, job_posting_upload, resume_analysis
from rag.loader import load_all_documents
from rag.store import init_vector_store


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 서버 시작 시 RAG 초기화
    print("[RAG] 공정채용 법령 문서 로딩 시작...")
    docs = load_all_documents()
    init_vector_store(docs)
    print("[RAG] 초기화 완료")
    yield


app = FastAPI(lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://127.0.0.1:3000"
        ], # 프론트 주소
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "PATCH"],
    allow_headers=["Content-Type", "Authorization"],
)

app.include_router(analysis.router)
app.include_router(interview.router)
app.include_router(job_posting_upload.router)
app.include_router(resume_analysis.router)
