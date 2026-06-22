from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from routers import analysis
from routers import analyze

app = FastAPI()

# CORS 설정
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"], # 프론트 주소
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE"],
    allow_headers=["Content-Type", "Authorization"],
)

app.include_router(analysis.router)
app.include_router(analyze.router)