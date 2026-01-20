from fastapi import Request, HTTPException
from fastapi.responses import JSONResponse
import logging

logger = logging.getLogger(__name__)

async def global_exception_handler(request: Request, exc: Exception):
    """
    アプリケーション全体の未キャッチ例外を処理する
    """
    logger.exception(f"Unhandled exception: {exc}")
    return JSONResponse(
        status_code=500,
        content={
            "error": "Internal Server Error",
            "message": str(exc),
            "type": exc.__class__.__name__
        }
    )

async def http_exception_handler(request: Request, exc: HTTPException):
    """
    HTTPException を統一された JSON 形式に変換する
    """
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "error": exc.detail,
            "status_code": exc.status_code
        }
    )

def setup_exception_handlers(app):
    """
    FastAPI アプリに例外ハンドラーを登録する
    """
    app.add_exception_handler(Exception, global_exception_handler)
    app.add_exception_handler(HTTPException, http_exception_handler)
    # 404 Not Found を明示的にトラップする
    app.add_exception_handler(404, http_exception_handler)
