from pathlib import Path

from dotenv import load_dotenv
from pydantic import BaseModel

from .settings import Settings, get_settings

load_dotenv()


class OpenCodeConfig(BaseModel):
    opencode_server: str
    model_provider_id: str
    model_id: str
    # 阶段二 provider 注册用（先建好字段）
    model_base_url: str
    model_api_key: str
    model_name: str

    @property
    def model_label(self) -> str:
        return f"{self.model_provider_id}/{self.model_id}"


class LLMConfig(BaseModel):
    url: str
    model_name: str
    api_key: str


class DockerConfig(BaseModel):
    runtime_mode: str
    docker_image: str
    docker_network: str
    workspace_root: Path
    idle_timeout: float
    max_iteration: int | None = None


class ModelConfig(BaseModel):
    llm: LLMConfig


class BaseConfig(BaseModel):
    models: ModelConfig
    opencode: OpenCodeConfig
    docker: DockerConfig


class AppConfig(BaseModel):
    base: BaseConfig


class ConfigEngine:
    def __init__(
        self,
        settings: Settings,
    ):
        self.settings = settings
        self.base_config = BaseConfig(
            models=ModelConfig(
                llm=LLMConfig(
                    url=settings.llm_base_url,
                    model_name=settings.llm_model,
                    api_key=settings.llm_api_key,
                )
            ),
            opencode=OpenCodeConfig(
                opencode_server=settings.opencode_server,
                model_provider_id=settings.model_provider_id,
                model_id=settings.model_id,
                model_base_url=settings.model_base_url,
                model_api_key=settings.model_api_key,
                model_name=settings.model_name,
            ),
            docker=DockerConfig(
                runtime_mode=settings.runtime_mode,
                docker_image=settings.docker_image,
                docker_network=settings.docker_network,
                workspace_root=settings.workspace_root,
                idle_timeout=settings.idle_timeout,
                max_iteration=settings.max_iteration,
            ),
        )


config_engine = ConfigEngine(get_settings())
