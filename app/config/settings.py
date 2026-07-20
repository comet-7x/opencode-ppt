from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlsplit

from dotenv import load_dotenv

# 项目根：本文件在 <root>/app/core/ 下，上溯两级到 opencode_ppt/
PROJECT_ROOT = Path(__file__).resolve().parents[2]

# 载入项目根的 .env（override=True 与项目其余部分一致）
load_dotenv(PROJECT_ROOT / ".env", override=True)


def _bypass_proxy_for(*urls: str) -> None:
    """把这些 URL 的主机名并入 NO_PROXY/no_proxy。

    本服务出站只连内网（模型端点、本机 opencode server），不该走代理；
    但 shell 常挂着全局 socks/http 代理会把内网主机名也拦下（表现成 503/超时）。
    在配置加载时自动放行这些主机，跑 app / 冒烟脚本都无需手动清代理。
    只做「增量放行」，不动其它代理设置。
    """
    hosts = {h for u in urls if u and (h := urlsplit(u).hostname)}
    if not hosts:
        return
    for var in ("NO_PROXY", "no_proxy"):
        current = {x.strip() for x in os.environ.get(var, "").split(",") if x.strip()}
        os.environ[var] = ",".join(sorted(current | hosts))


class Settings:
    """最小配置对象（第一版不引入 pydantic-settings，直接读环境变量）。"""

    def __init__(self) -> None:
        # opencode 服务端（LOCAL 运行模式用；DOCKER 模式由 runtime 自造）
        self.opencode_server: str = os.environ.get("OPENCODE_SERVER", "http://127.0.0.1:37217")
        # 自定义模型（opencode 渲染用）
        self.model_provider_id: str = os.environ.get(
            "OPENCODE_MODEL_PROVIDER_ID", "steins-middleware"
        )
        self.model_id: str = os.environ.get("OPENCODE_MODEL_MODEL_ID", "Qwen/Qwen3.5-27B")
        # opencode 渲染用 provider 的注册参数（阶段二 initializer 用；先建好字段）
        self.model_base_url: str = os.environ.get(
            "OPENCODE_MODEL_BASE_URL", "http://steins-middleware.steins.net:19912/v1"
        )
        self.model_api_key: str = os.environ.get("OPENCODE_MODEL_API_KEY", "EMPTY")
        self.model_name: str = os.environ.get("OPENCODE_MODEL_MODEL_NAME", "Qwen3.5-27B")
        # CAMEL 内容生成用的 LLM（教案/作业）。缺省回退到 OPENCODE_MODEL_* 那套同一端点。
        self.llm_model: str = os.environ.get(
            "LLM_MODEL", os.environ.get("OPENCODE_MODEL_MODEL_ID", "Qwen/Qwen3.5-27B")
        )
        self.llm_base_url: str = os.environ.get(
            "LLM_BASE_URL",
            os.environ.get(
                "OPENCODE_MODEL_BASE_URL",
                "http://steins-middleware.steins.net:19912/v1",
            ),
        )
        self.llm_api_key: str = os.environ.get(
            "LLM_API_KEY", os.environ.get("OPENCODE_MODEL_API_KEY", "EMPTY")
        )
        # 运行模式：docker（容器 per job）| local（直连宿主机 server）
        self.runtime_mode: str = os.environ.get("OCPPT_RUNTIME", "docker")
        # Docker 相关
        self.docker_image: str = os.environ.get("OCPPT_IMAGE", "ocppt-opencode:latest")
        self.docker_network: str = os.environ.get("OCPPT_DOCKER_NETWORK", "host")
        # 任务工作区根（宿主机侧；每个 job 一个子目录）
        self.workspace_root: Path = Path(
            os.environ.get("OCPPT_WORKSPACE", str(PROJECT_ROOT / "ws"))
        )
        # 单轮生成等待 idle 的超时（秒）
        self.idle_timeout: float = float(os.environ.get("OCPPT_IDLE_TIMEOUT", "1800"))
        # 单轮 step-finish 上限（防 agent 在一轮里无限工具循环）。缺省不设限；
        # 注意 PPT 生成本就步数很多，设太小会误杀正常任务。
        _mi = os.environ.get("OCPPT_MAX_ITERATION")
        self.max_iteration: int | None = int(_mi) if _mi else None

        # 让内网出站目标绕过全局代理（模型端点 / opencode server）
        _bypass_proxy_for(self.llm_base_url, self.model_base_url, self.opencode_server)

    @property
    def model_label(self) -> str:
        return f"{self.model_provider_id}/{self.model_id}"


@lru_cache
def get_settings() -> Settings:
    return Settings()
