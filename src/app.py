from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from routers import analysis

app = FastAPI()

# CORS 설정
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhoist:5173"], # 프론트 주소
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE"],
    allow_headers=["Content-Type", "Authorisztion"],
)

app.include_router(analysis.router)