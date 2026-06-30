from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from routers import analysis, interview, job_posting_upload, resume_analysis, resume_upload, criteria
from routers.auth import router as auth_router

from contextlib import asynccontextmanager
from routers.resume_service import _get_ner

@asynccontextmanager
async def lifespan(app: FastAPI):
    _get_ner()  # 서버 시작 시 NER 모델 미리 로드
    yield

app = FastAPI(lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://127.0.0.1:3000"
        ], # 프론트 주소
    allow_credentials=True,
    # allow_methods=["GET", "POST", "PUT", "DELETE", "PATCH"],
    allow_methods=["*"],
    allow_headers=["Content-Type", "Authorization"],
)

app.include_router(analysis.router)
app.include_router(interview.router)
app.include_router(job_posting_upload.router)
app.include_router(resume_analysis.router)
app.include_router(resume_upload.router)
app.include_router(criteria.router)
app.include_router(auth_router)
