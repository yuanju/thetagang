#!/usr/bin/env python3
"""订单查询脚本 - 查看最近7日内的订单和成交记录"""

import asyncio
from datetime import datetime, timedelta

import click
from ib_async import (
    ExecutionFilter,
    IB,
    Trade,
)
from rich.console import Console
from rich.table import Table

console = Console()


async def connect_ibkr(client_id: int = 1, port: int = 7496) -> IB:
    """连接到 IBKR TWS/IBGW"""
    ib = IB()
    await ib.connectAsync("127.0.0.1", port, clientId=client_id)
    return ib


async def get_executions(ib: IB, days: int = 7) -> list:
    """获取最近N天的成交记录"""
    # 计算开始时间
    start_time = datetime.now() - timedelta(days=days)

    # 创建过滤条件
    exec_filter = ExecutionFilter(time=start_time.strftime("%Y%m%d %H:%M:%S"))

    # 获取成交记录
    fills = await ib.reqExecutionsAsync(exec_filter)
    return fills


async def get_open_orders(ib: IB) -> list:
    """获取当前挂单"""
    return await ib.reqAllOpenOrdersAsync()

async def get_complete_orders(ib: IB) -> list:
    """获取所有完成订单"""
    return await ib.reqCompletedOrdersAsync(apiOnly=False)

async def get_trades(ib: IB) -> list:
    """获取最近成交的订单（包括完成的和部分完成的）"""
    return ib.trades()


def display_executions(fills: list):
    """显示成交记录"""
    if not fills:
        console.print("[yellow]没有成交记录[/yellow]")
        return

    # 按时间排序（最新的在前）
    sorted_fills = sorted(fills, key=lambda f: f.execution.time or datetime.min, reverse=True)

    table = Table(title=f"成交记录 Executions (共 {len(sorted_fills)} 条)")
    table.add_column("时间", style="cyan")
    table.add_column("标的", justify="right")
    table.add_column("类型", justify="right")
    table.add_column("方向", justify="right")
    table.add_column("数量", justify="right")
    table.add_column("成交价", justify="right")
    table.add_column("总金额", justify="right")
    table.add_column("佣金", justify="right")
    table.add_column("订单号", justify="right")

    total_commission = 0
    total_value = 0

    for fill in sorted_fills:
        execution = fill.execution
        contract = fill.contract
        commission = fill.commissionReport.commission if fill.commissionReport else 0
        total_commission += commission

        # 标的显示
        if hasattr(contract, 'symbol'):
            symbol = contract.symbol
            sec_type = getattr(contract, 'secType', 'STK')
            if sec_type == 'OPT':
                strike = getattr(contract, 'strike', '')
                expiry = getattr(contract, 'lastTradeDateOrContractMonth', '')
                right = getattr(contract, 'right', '')
                symbol = f"{symbol} {right}${strike} {expiry}"
        else:
            symbol = str(contract)
            sec_type = "STK"

        # 方向
        side = execution.side.upper() if execution.side else ''
        action_color = "green" if side == "BOT" or side == "BUY" else "red"
        action = f"[{action_color}]{side}[/{action_color}]"

        # 时间格式化
        exec_time = execution.time.strftime("%m-%d %H:%M") if execution.time else "-"

        # 价格
        exec_price = execution.price if execution.price else 0

        # 总金额 = 价格 × 数量
        total_amount = exec_price * execution.shares if exec_price else 0
        total_value += total_amount

        table.add_row(
            exec_time,
            symbol,
            sec_type,
            action,
            str(execution.shares),
            f"{exec_price:.2f}" if exec_price else "-",
            f"{total_amount:,.2f}" if total_amount else "-",
            f"{commission:.2f}" if commission else "-",
            str(execution.orderId),
        )

    console.print(table)

    # 显示统计信息
    # console.print(f"\n[bold]统计:[/bold]")
    # console.print(f"  总成交金额: ${total_value:,.2f}")
    # console.print(f"  总佣金: ${total_commission:,.2f}")
    # console.print(f"  净支出: ${total_value + total_commission:,.2f}")


def display_orders(orders: list):
    """显示当前挂单"""
    if not orders:
        console.print("[yellow]没有挂单[/yellow]")
        return

    table = Table(title=f"当前挂单 Open Orders (共 {len(orders)} 条)")
    table.add_column("订单号", justify="right")
    table.add_column("标的", style="cyan")
    table.add_column("方向", justify="right")
    table.add_column("类型", justify="right")
    table.add_column("数量", justify="right")
    table.add_column("价格", justify="right")
    table.add_column("状态", justify="right")

    for order in orders:
        contract = order.contract
        order_info = order.order

        # 标的显示
        if hasattr(contract, 'symbol'):
            symbol = contract.symbol
            sec_type = getattr(contract, 'secType', 'STK')
            if sec_type == 'OPT':
                strike = getattr(contract, 'strike', '')
                expiry = getattr(contract, 'lastTradeDateOrContractMonth', '')
                right = getattr(contract, 'right', '')
                symbol = f"{symbol} {right}${strike} {expiry}"
        else:
            symbol = str(contract)
            sec_type = "STK"

        # 方向
        action = order_info.action.upper()
        action_color = "green" if action == "BUY" else "red"
        action_str = f"[{action_color}]{action}[/{action_color}]"

        # 订单类型
        order_type = order_info.orderType

        # 状态
        status = order.orderStatus.status if order.orderStatus else "Unknown"
        status_color = "yellow" if status == "Submitted" else "green" if status == "Filled" else "white"

        table.add_row(
            str(order_info.orderId),
            symbol,
            action_str,
            order_type,
            str(order_info.totalQuantity),
            f"{order_info.lmtPrice:.2f}" if order_info.lmtPrice else "-",
            f"[{status_color}]{status}[/{status_color}]",
        )

    console.print(table)


def display_trades(trades: list):
    """显示最近成交的订单"""
    if not trades:
        console.print("[yellow]没有交易订单[/yellow]")
        return

    # 按时间排序（最新的在前）
    sorted_trades = sorted(trades, key=lambda t: t.orderStatus.permId or 0, reverse=True)

    table = Table(title=f"最近交易记录 Trades (共 {len(sorted_trades)} 条)")
    table.add_column("订单号", justify="right")
    table.add_column("标的", style="cyan")
    table.add_column("方向", justify="right")
    table.add_column("类型", justify="right")
    table.add_column("证券类型", justify="right")
    table.add_column("数量", justify="right")
    table.add_column("均价", justify="right")
    table.add_column("状态", justify="right")

    for trade in sorted_trades:
        contract = trade.contract
        order_info = trade.order
        # 标的显示
        if hasattr(contract, 'symbol'):
            symbol = contract.symbol
            sec_type = getattr(contract, 'secType', 'STK')
            if sec_type == 'OPT':
                strike = getattr(contract, 'strike', '')
                expiry = getattr(contract, 'lastTradeDateOrContractMonth', '')
                right = getattr(contract, 'right', '')
                symbol = f"{symbol} {right}${strike} {expiry}"
            elif sec_type == 'STK':
                symbol = f"{symbol}"
        else:
            symbol = str(contract)

        # 方向
        action = order_info.action.upper()
        action_color = "green" if action == "BUY" else "red"
        action_str = f"[{action_color}]{action}[/{action_color}]"

        # 订单类型
        order_type = order_info.orderType

        # 状态
        status = trade.orderStatus.status if trade.orderStatus else "Unknown"
        status_color = "yellow" if status == "Submitted" else "green" if status == "Filled" else "white"

        # 均价
        avg_price = trade.orderStatus.avgFillPrice if trade.orderStatus else None

        table.add_row(
            str(order_info.orderId),
            symbol,
            action_str,
            order_type,
            sec_type,
            str(order_info.totalQuantity if status == "Submitted" else order_info.filledQuantity),
            f"{avg_price:.2f}" if avg_price else "-",
            f"[{status_color}]{status}[/{status_color}]",
        )

    console.print(table)


@click.command()
@click.option(
    "--client-id",
    default=1,
    type=int,
    help="客户端ID (default: 1)",
)
@click.option(
    "--port",
    default=7496,
    type=int,
    help="TWS/IBGW 端口 (default: 7496 for TWS, 4002 for IBGW paper)",
)
@click.option(
    "--days",
    default=7,
    type=int,
    help="查询天数 (default: 7)",
)
@click.option(
    "--executions/--no-executions",
    default=True,
    help="显示成交记录 (default: True)",
)
@click.option(
    "--orders/--no-orders",
    default=True,
    help="显示当前挂单 (default: True)",
)
@click.option(
    "--trades/--no-trades",
    default=True,
    help="显示最近成交订单 (default: True)",
)
def main(
    client_id: int,
    port: int,
    days: int,
    executions: bool,
    orders: bool,
    trades: bool,
):
    """
    订单查询脚本

    查看最近N天的成交记录和当前挂单。

    示例:
        python orders.py
        python orders.py --days 30
        python orders.py --port 4002
    """
    async def run():
        try:
            console.print(f"[bold blue]连接到 IBKR...[/bold blue]")
            ib = await connect_ibkr(client_id, port)
            console.print(f"[bold green]已连接![/bold green]\n")

            # 显示成交记录
            if executions:
                # console.print(f"[bold]=== 最近 {days} 天成交记录 ===[/bold]")
                fills = await get_executions(ib, days)
                display_executions(fills)
                console.print()

            # 显示最近成交订单
            if trades:
                # console.print("[bold]=== 最近成交订单 ===[/bold]")
                recent_trades = await get_trades(ib)
                display_trades(recent_trades)

            # # 显示当前挂单
            # if orders:
            #     # console.print("[bold]=== 当前挂单 ===[/bold]")
            #     open_orders = await get_open_orders(ib)
            #     display_orders(open_orders)
            #     complete_orders = await get_complete_orders(ib)
            #     display_orders(complete_orders)
            #     console.print()




            # 断开连接
            ib.disconnect()

        except Exception as e:
            console.print(f"[bold red]错误: {e}[/bold red]")
            import traceback
            traceback.print_exc()
            raise

    asyncio.run(run())


if __name__ == "__main__":
    main()
