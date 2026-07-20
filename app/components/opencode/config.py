"""连接 opencode 所需的两类配置：服务端地址 + 使用哪个模型。

设计要点
--------
opencode 是「服务端 + 客户端」结构：真正调大模型的是服务端。所以
「自定义模型」并不是在 Python 这边直接填 url / api_key，而是要在 opencode
服务端注册一个 **provider（供应商）**，里面写好 baseURL / apiKey / 模型清单；
Python 客户端发消息时，只需要报出 `provider_id` + `model_id` 两个名字即可。

因此这里的 `ModelConfig` 一次性把两边需要的东西都装下：
  * `provider_id` / `model_id`  —— 聊天模块（本模块）真正用到的
  * `base_url` / `api_key` / `model_name` —— 供「初始化模块」把它写进 opencode
    配置（provider 注册）时使用；聊天模块本身不直接消费

`to_provider_config()` 就是给初始化模块用的：把自定义模型翻译成 opencode
配置文件里 `provider` 字段期望的结构（OpenAI 兼容供应商）。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class ServerConfig:
    """opencode 服务端连接参数。"""

    base_url: str
    trust_env: bool = False          # 不走系统代理，和示例脚本保持一致
    timeout: float | None = None     # None = 不设超时（长任务如生成 PPT 需要）

    @classmethod
    def from_env(cls, var: str = "OPENCODE_SERVER") -> ServerConfig:
        url = os.environ.get(var)
        if not url:
            raise RuntimeError(f"环境变量 {var} 未设置，无法连接 opencode 服务端")
        return cls(base_url=url)


@dataclass(slots=True)
class ModelConfig:
    """一个「自定义模型」的完整描述。

    聊天时只用到 provider_id / model_id；base_url / api_key / model_name
    留给初始化模块注册 provider 用。
    """

    provider_id: str                 # opencode 里这个供应商的名字，例如 "custom"
    model_id: str                    # 该供应商下的模型名，例如 "gpt-4o"

    # ↓ 以下字段供「初始化模块」注册 provider 时使用（聊天模块不直接读）
    base_url: str | None = None      # 自定义 url（OpenAI 兼容接口的 baseURL）
    api_key: str | None = None       # 自定义 api_key
    model_name: str | None = None    # 展示名；缺省用 model_id
    npm: str = "@ai-sdk/openai-compatible"  # opencode 加载该 provider 用的 npm 包
    options: dict[str, Any] = field(default_factory=dict)  # 额外 provider options

    def to_provider_config(self) -> dict[str, Any]:
        """翻译成 opencode 配置文件 `provider.<id>` 期望的结构。

        供初始化模块把它 merge 进 opencode.jsonc / opencode.json。
        参考 opencode 的 openai-compatible provider 配置格式。
        """
        if not self.base_url or not self.api_key:
            raise ValueError(
                "注册自定义 provider 需要同时提供 base_url 和 api_key"
            )
        opts: dict[str, Any] = {"baseURL": self.base_url, "apiKey": self.api_key}
        opts.update(self.options)
        return {
            "npm": self.npm,
            "options": opts,
            "models": {
                self.model_id: {"name": self.model_name or self.model_id},
            },
        }

    @classmethod
    def from_env(cls, prefix: str = "OPENCODE_MODEL") -> ModelConfig:
        """从环境变量构造，方便本地调试。

        约定的变量名：
            {prefix}_PROVIDER_ID / {prefix}_MODEL_ID
            {prefix}_BASE_URL / {prefix}_API_KEY / {prefix}_MODEL_NAME
        """
        def g(suffix: str) -> str | None:
            return os.environ.get(f"{prefix}_{suffix}")

        provider_id = g("PROVIDER_ID")
        model_id = g("MODEL_ID")
        if not provider_id or not model_id:
            raise RuntimeError(
                f"{prefix}_PROVIDER_ID / {prefix}_MODEL_ID 未设置"
            )
        return cls(
            provider_id=provider_id,
            model_id=model_id,
            base_url=g("BASE_URL"),
            api_key=g("API_KEY"),
            model_name=g("MODEL_NAME"),
        )
