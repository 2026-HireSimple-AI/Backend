from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from routers import analysis, interview, job_posting_upload, resume_analysis

app = FastAPI()

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
