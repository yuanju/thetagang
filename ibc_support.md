# IBC 配置指南

本文档说明如何使用 IBC (Interactive Brokers Controller) 启动 ThetaGang。

## 什么是 IBC

IBC (Interactive Brokers Controller) 是一个用于自动管理 IB Gateway/TWS 的工具。它可以自动登录、自动处理二次验证、自动重启等。

## 1. 安装 IBC

需要先安装 IBC。可以从 [IBC GitHub](https://github.com/IbcAlpha/IBC) 下载并安装。

## 2. 配置 `ibc-config.ini`

项目已有一个示例配置文件 `ibc-config.ini`，关键配置项包括：

```ini
# ============================================
# 认证设置 (必须配置)
# ============================================

# 你的 TWS 用户名
IbLoginId=你的IB账户名

# 你的 TWS 密码
IbPassword=你的IB密码

# ============================================
# 交易模式
# ============================================

# live 或 paper (模拟交易)
TradingMode=live

# ============================================
# API 端口配置
# ============================================

# TWS 默认端口: 7497 (paper) / 7496 (live)
# Gateway 默认端口: 4001 (paper) / 4002 (live)
OverrideTwsApiPort=7497

# ============================================
# 其他推荐配置
# ============================================

# 启动时最小化窗口
MinimizeMainWindow=yes

# 自动接受 API 连接请求
AcceptIncomingConnectionAction=accept

# 允许盲交易 (无市场数据时下单)
AllowBlindTrading=no

# 接受模拟交易账户警告
AcceptNonBrokerageAccountWarning=yes
```

## 3. 配置 `thetagang.toml`

在 `[runtime.ibc]` 部分配置：

```toml
[runtime.ibc]
# IBC 配置参数。详见:
# https://ib-insync.readthedocs.io/api.html#ibc

# false 使用 TWS, true 使用 Gateway
gateway = false

# IBC 安装路径
ibcPath = '/opt/ibc'

# 交易模式: live 或 paper
tradingMode = 'live'

# 是否在请求错误时抛出异常
# 正常运行时建议设为 false
RaiseRequestErrors = false

# IB 账户密码
password = '你的IB密码'

# IB 账户用户名
userid = '你的IB账户名'

# IBC 配置文件路径
ibcIni = './ibc-config.ini'

# Java 路径 (可选)
javaPath = '/opt/java/openjdk/bin'
```

## 4. 运行方式

```bash
# 使用 IBC 启动 (默认方式)
uv run thetagang --config thetagang.toml

# 先用 dry-run 测试
uv run thetagang --config thetagang.toml --dry-run
```

## 5. 注意事项

### 安全提示

- 避免在配置文件中明文存储密码，考虑使用环境变量
- 生产环境建议使用更安全的认证方式

### 端口配置

确保以下端口匹配：

| 组件 | Paper Trading | Live Trading |
|------|--------------|--------------|
| TWS | 7497 | 7496 |
| Gateway | 4001 | 4002 |

在 `ibc-config.ini` 中设置 `OverrideTwsApiPort`，在 `thetagang.toml` 中设置 `watchdog.port`。

### 首次运行

- 建议先用 `--dry-run` 测试
- 建议先使用 paper trading 模式验证配置

## 6. 不使用 IBC

如果不想使用 IBC，可以连接到手动启动的 IB Gateway/TWS：

```bash
uv run thetagang --config thetagang.toml --without-ibc
```

确保以下配置匹配你的网关设置：

- `watchdog.host`
- `watchdog.port`
- `watchdog.clientId`
- `ib_async.api_response_wait_time`

## 相关链接

- [IBC GitHub](https://github.com/IbcAlpha/IBC)
- [IBC 文档](https://github.com/IbcAlpha/IBC/blob/master/userguide.md)
- [ib-insync IBC 配置](https://ib-insync.readthedocs.io/api.html#ibc)
