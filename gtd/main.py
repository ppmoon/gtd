from contextlib import asynccontextmanager

from fastapi import FastAPI

from gtd.channels.api import router as api_router
from gtd.channels.feishu import start_feishu
from gtd.db import init_db


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    start_feishu()
    yield


app = FastAPI(lifespan=lifespan)
app.include_router(api_router)
