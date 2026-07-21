"""统一响应信封：所有接口都返回 BaseResponse，业务数据一律放 data，code 给状态码。

用法：
    return ApiResult.data(TaskRef(...))          # 带数据
    return ApiResult.ok(msg="任务已启动")          # 只报成功
    return ApiResult.error(code=404, msg="不存在")  # 报错
"""

from __future__ import annotations

from typing import Any

from fastapi import status
from pydantic import BaseModel


class BaseResponse[T](BaseModel):
    """基础响应模型。

    用于封装所有 API 接口的返回结果，提供统一的响应格式。

    参数:
        code (int): 响应状态码，200 表示成功，其他表示错误。
        msg (str): 响应消息，描述操作结果或错误详情。
        data (T | None): 响应数据，包含具体业务逻辑返回的数据，可为 None。
    """

    code: int
    msg: str
    data: T | None = None


class ApiResult:
    DEFAULT_SUCCESS_MESSAGE = "Success"
    DEFAULT_ERROR_MESSAGE = "Unknown Error"

    @staticmethod
    def ok(msg: str = DEFAULT_SUCCESS_MESSAGE) -> BaseResponse[Any]:
        return BaseResponse[Any](code=status.HTTP_200_OK, msg=msg, data=None)

    @staticmethod
    def data[T](
        data: T | None = None, msg: str = DEFAULT_SUCCESS_MESSAGE
    ) -> BaseResponse[T]:
        return BaseResponse[T](code=status.HTTP_200_OK, msg=msg, data=data)

    @staticmethod
    def error(
        code: int = status.HTTP_400_BAD_REQUEST, msg: str = DEFAULT_ERROR_MESSAGE
    ) -> BaseResponse[Any]:
        return BaseResponse[Any](code=code, msg=msg, data=None)
