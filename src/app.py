from fastapi import FastAPI
from routers import analysis

app = FastAPI()
app.include_router(analysis.router)