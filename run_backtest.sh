#!/bin/bash
# 运行 ThetaGang 回测
#
# 用法:
#   ./run_backtest.sh                      # 使用默认配置
#   ./run_backtest.sh 2024-01-01 2024-12-31  # 指定日期范围
#   ./run_backtest.sh --log                # 显示详细日志

set -e

DATA_DIR="${DATA_DIR:-backtest_examples}"
SYMBOLS="${SYMBOLS:-SPY}"
START_DATE="${START_DATE:-2024-01-02}"
END_DATE="${END_DATE:-2024-02-13}"
INITIAL_CASH="${INITIAL_CASH:-100000}"
SHOW_LOG="${SHOW_LOG:-0}"

if [ "$1" == "--log" ] || [ "$1" == "-l" ]; then
    SHOW_LOG=1
    shift
fi

if [ -n "$1" ]; then
    START_DATE="$1"
fi
if [ -n "$2" ]; then
    END_DATE="$2"
fi

echo "=========================================="
echo "ThetaGang 回测"
echo "=========================================="
echo "数据目录:   $DATA_DIR"
echo "标的:       $SYMBOLS"
echo "起始日期:   $START_DATE"
echo "结束日期:   $END_DATE"
echo "初始资金:   \$$INITIAL_CASH"
echo "=========================================="

# 检查数据目录
if [ ! -d "$DATA_DIR" ]; then
    echo "错误: 数据目录不存在: $DATA_DIR"
    exit 1
fi

# 检查标的文件
for sym in $SYMBOLS; do
    if [ ! -f "$DATA_DIR/${sym}_bars.csv" ]; then
        echo "错误: 缺少价格数据文件: $DATA_DIR/${sym}_bars.csv"
        exit 1
    fi
done

echo ""

cd "$(dirname "$0")"

# 构建 Python 命令
PY_CMD="
import sys
sys.path.insert(0, '.')
from thetagang.backtest import *
from datetime import date
import os

data = CSVBacktestDataProvider('$DATA_DIR')

config = WheelBacktestConfig(
    target_delta=0.30,
    max_delta=0.50,
    min_dte=14,
    max_dte=60,
    minimum_credit=0.10,
    minimum_open_interest=10,
    strike_limit_pct=0.05,
    max_new_contracts_pct=1.0,
    roll_dte=7,
    close_at_pnl_pct=0.95,
    roll_min_pnl_pct=0.50,
)

portfolio = BacktestPortfolio(initial_cash=float($INITIAL_CASH))

# 解析标的
symbols = '$SYMBOLS'.split(',')

# 计算等权重
weights = {s: 1.0 / len(symbols) for s in symbols}

engine = WheelBacktestEngine(
    data_provider=data,
    portfolio=portfolio,
    config=config,
    symbols=symbols,
    weights=weights,
)

start = date.fromisoformat('$START_DATE')
end = date.fromisoformat('$END_DATE')
engine.run(start, end)
engine.print_summary()

if $SHOW_LOG:
    print()
    print('=== 详细日志 ===')
    for line in engine.get_log():
        print(line)

# 生成图表
output_dir = 'backtest_output'
os.makedirs(output_dir, exist_ok=True)

sym_title = symbols[0] if len(symbols) == 1 else '/'.join(symbols)
figs = plot_backtest(
    portfolio,
    title=f'ThetaGang 回测 ({sym_title})',
    symbols=symbols,
    output_dir=output_dir,
)
print()
print('图表已保存到 backtest_output/ 目录:')
for name in ['dashboard', 'equity_curve', 'returns_dist', 'trade_log', 'positions']:
    print(f'  backtest_output/{name}.html')
"

uv run python -c "$PY_CMD"
