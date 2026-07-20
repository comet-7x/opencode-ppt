import uvicorn
from fastapi import FastAPI

from app.api.generator import router as generator_router
from app.core import lifespan
from app.core.errors import register_exception_handlers

app = FastAPI(
    lifespan=lifespan,
    title="内容生成接口文档",
    description="内容生成相关的接口文档",
    version="1.0.0",
)
app.include_router(generator_router)
register_exception_handlers(app)


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=7946)  # noqa: S104 - 本地开发绑定
