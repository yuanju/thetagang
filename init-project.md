# ThetaGang 项目初始化

## 环境准备

### 1. 安装 uv 包管理器

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

安装完成后，需要将 uv 添加到 PATH：

```bash
export PATH="$HOME/.local/bin:$PATH"
```

### 2. 安装项目依赖

```bash
uv sync
```

这会安装 `pyproject.toml` 中所有依赖，包括：
- `ib-async` - 用于连接盈透证券 TWS/Gateway
- `pydantic` - 配置管理
- `sqlalchemy` - 数据库
- 其他交易相关依赖

## 盈透证券连接配置

### 安装 TWS 或 Gateway

1. 下载并安装 [TWS (Trader Workstation)](https://www.interactivebrokers.com/en/trading/tws.php) 或 [IB Gateway](https://www.interactivebrokers.com/en/trading/ibgateway.php)

2. 登录您的 IBKR 账户

3. 启用 API 连接：
   - TWS: `设置 → API → 启用 ActiveX 和 Socket Clients`
   - 或 `配置 → API → 启用 ActiveX 和 Socket Clients`

4. 记下 Socket 端口号：
   - TWS Demo: 7496
   - TWS Live: 7497
   - IB Gateway Demo: 7496
   - IB Gateway Live: 4001

### 配置 ThetaGang

编辑 `thetagang.toml` 配置文件，设置正确的连接参数。

## 运行项目

### Dry-run 模式（推荐首次运行）

```bash
uv run thetagang --config thetagang.toml --dry-run
```

### 正式运行

```bash
uv run thetagang --config thetagang.toml
```

## 常用开发命令

```bash
# 运行测试
uv run pytest

# 代码检查
uv run ruff check .

# 代码格式化
uv run ruff format .

# 类型检查
uv run ty check

# 覆盖率
uv run pytest --cov=thetagang
```
