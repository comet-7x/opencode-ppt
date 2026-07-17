"""示例测试,验证 pytest + 异步 + 覆盖率配置可用,记得替换成真实用例。"""

import asyncio

import comet_project


def test_package_importable() -> None:
    """smoke test:包能被导入,覆盖率有数据可采。"""
    assert comet_project.__version__


def test_sync_example() -> None:
    assert 1 + 1 == 2


async def test_async_example() -> None:
    """asyncio_mode = "auto",async def 测试无需额外标记。"""
    await asyncio.sleep(0)
    assert True
