# comet-project

> 个人项目 Python 模板:`uv` + `ruff` + `pyright` + `pytest` + GitHub Actions CI。
> 用 GitHub 的 "Use this template" 创建新仓库,然后按下面三步初始化。

## 快速开始

```bash
# 1. 安装 uv(如未安装)
curl -LsSf https://astral.sh/uv/install.sh | sh

# 2. 安装依赖并注册 git 钩子
make install        # 等价于 uv sync --all-extras --dev && uv run pre-commit install

# 3. 验证环境
make check          # lint + 类型检查 + 测试
```

## 改造为你的项目

新建仓库后,把模板里的占位名 `comet_project` / `comet-project` 替换掉:

```bash
NEW=your_pkg_name   # 你的包名,用下划线
sed -i "s/comet_project/${NEW}/g" pyproject.toml pyrightconfig.json tests/*.py
sed -i "s/comet-project/${NEW//_/-}/g" pyproject.toml README.md
mv src/comet_project "src/${NEW}"
```

## 常用命令

| 命令 | 作用 |
|------|------|
| `make lint` | Ruff 检查 |
| `make format` | Ruff 自动修复 + 格式化 |
| `make type` | Pyright 类型检查 |
| `make test` | 跑测试 + 覆盖率 |
| `make check` | 本地一键全量(等价 CI) |

## 工程约定

- **代码风格**:Ruff 统一管理 lint 与格式化,行宽 100,提交时 pre-commit 自动修复。
- **类型**:`pyright` standard 模式,带 `py.typed`,鼓励完整标注。
- **测试**:`pytest`,`asyncio_mode = "auto"`,`async def test_*` 直接生效。
- **CI**:push / PR 到 main、develop 触发,跑 lint + 类型 + 多版本测试。
- **Release**:合入 `feat`/`fix` 到 main 后,`release.yml` 自动算版本号、更新 `CHANGELOG.md`、打 tag、发 GitHub Release。无需手动打 tag;不想自动可删掉 workflow 里的 `push` 触发,只保留手动 `workflow_dispatch`。

## 提交规范

启用 commitizen 后,提交信息须符合 Conventional Commits:

| 前缀 | 含义 | 版本影响(0.x) |
|------|------|------|
| `feat:` | 新功能 | minor +1 |
| `fix:` | 修复 | patch +1 |
| `feat!:` / `BREAKING CHANGE` | 破坏性变更 | minor +1(0.x 阶段不升 1.0) |
| `docs:` `chore:` `refactor:` `test:` | 其它 | 不触发发布 |

不熟格式可用交互式生成:`uvx --from commitizen cz commit`
