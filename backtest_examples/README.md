# ThetaGang 回测示例

## 数据目录结构

```
backtest_examples/
├── SPY_bars.csv              # 标的历史价格
├── AAPL_bars.csv             # 其他标的
├── SPY_2024-01-02_options.csv # 每日期权链数据
├── SPY_2024-01-03_options.csv
└── ...
```

## 数据格式说明

### 1. 历史价格数据 (xxx_bars.csv)

每只股票一个文件，命名格式：`{SYMBOL}_bars.csv`

| 字段 | 类型 | 说明 |
|------|------|------|
| timestamp | YYYY-MM-DD | 日期 |
| open | float | 开盘价 |
| high | float | 最高价 |
| low | float | 最低价 |
| close | float | 收盘价 |
| volume | int | 成交量 |

示例：
```csv
timestamp,open,high,low,close,volume
2024-01-02,474.50,476.20,472.80,475.10,82000000
2024-01-03,475.20,477.50,473.90,476.80,78500000
```

### 2. 期权链数据 (xxx_YYYY-MM-DD_options.csv)

每个交易日每个标的生成一个文件，命名格式：`{SYMBOL}_{DATE}_options.csv`

| 字段 | 类型 | 说明 |
|------|------|------|
| strike | float | 行权价 |
| right | CALL/PUT | 期权类型 |
| expiry | YYYY-MM-DD | 到期日 |
| bid | float | 买价 |
| ask | float | 卖价 |
| delta | float | Delta |
| gamma | float | Gamma |
| theta | float | Theta |
| vega | float | Vega |
| open_interest | int | 未平仓合约数 |
| volume | int | 成交量 |

示例：
```csv
strike,right,expiry,bid,ask,delta,gamma,theta,vega,open_interest,volume
470.0,CALL,2024-02-16,8.20,8.35,0.62,0.018,0.12,0.15,1240,890
475.0,PUT,2024-02-16,5.45,5.60,-0.52,0.025,0.16,0.19,2100,920
```

## 使用方法

```python
from thetagang.backtest import (
    run_backtest_from_csv,
    WheelBacktestConfig,
    WheelBacktestEngine,
    CSVBacktestDataProvider,
    BacktestPortfolio,
)
from datetime import date

# 方法1：使用便捷函数
portfolio = run_backtest_from_csv(
    data_dir="path/to/backtest_examples",
    symbols=["SPY", "AAPL"],
    weights={"SPY": 0.6, "AAPL": 0.4},
    start_date="2024-01-02",
    end_date="2024-12-31",
    initial_cash=100_000.0,
)

# 方法2：详细配置
config = WheelBacktestConfig(
    target_dte=30,
    max_dte=45,
    min_dte=14,
    target_delta=0.30,
    max_delta=0.50,
    minimum_credit=0.15,
    minimum_open_interest=10,
    roll_dte=7,
    close_at_pnl_pct=0.95,
    roll_min_pnl_pct=0.50,
)

data = CSVBacktestDataProvider("path/to/data")
portfolio = BacktestPortfolio(initial_cash=50_000.0)
engine = WheelBacktestEngine(
    data_provider=data,
    portfolio=portfolio,
    config=config,
    symbols=["SPY"],
    weights={"SPY": 1.0},
)
engine.run(date(2024, 1, 1), date(2024, 12, 31))
engine.print_summary()

# 查看交易记录
for trade in portfolio.trade_log:
    print(trade.timestamp, trade.symbol, trade.action, trade.quantity, trade.price)
```

## 自定义数据源

实现 `BacktestDataProvider` 协议来接入自己的数据：

```python
class MyDataProvider:
    def get_ohlcv(self, symbol: str, start: date, end: date) -> List[OHLCVBar]:
        # 从你的数据源获取OHLCV数据
        ...

    def get_option_chain(self, symbol: str, as_of: date) -> List[OptionQuote]:
        # 从你的数据源获取期权链数据
        ...

    def get_spot_price(self, symbol: str, as_of: date) -> float:
        # 返回标的当前价格
        ...
```
