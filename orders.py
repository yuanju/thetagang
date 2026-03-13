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


def get_open_orders(ib: IB) -> list:
    """获取当前挂单"""
    return ib.reqAllOpenOrders()


def display_executions(fills: list):
    """显示成交记录"""
    if not fills:
        console.print("[yellow]没有成交记录[/yellow]")
        return

    table = Table(title=f"成交记录 Executions (共 {len(fills)} 条)")
    table.add_column("时间", style="cyan")
    table.add_column("标的", justify="right")
    table.add_column("类型", justify="right")
    table.add_column("方向", justify="right")
    table.add_column("数量", justify="right")
    table.add_column("价格", justify="right")
    table.add_column("成交价", justify="right")
    table.add_column("佣金", justify="right")
    table.add_column("订单号", justify="right")

    for fill in fills:
        execution = fill.execution
        contract = fill.contract
        commission = fill.commissionReport.commission if fill.commissionReport else 0

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

        # 方向
        side = execution.side.upper() if execution.side else ''
        action_color = "green" if side == "BUY" else "red"
        action = f"[{action_color}]{side}[/{action_color}]"

        # 时间格式化
        exec_time = execution.time.strftime("%Y-%m-%d %H:%M") if execution.time else "-"

        table.add_row(
            exec_time,
            symbol,
            sec_type if 'sec_type' in locals() else "STK",
            action,
            str(execution.shares),
            f"{execution.price:.2f}" if execution.price else "-",
            f"{execution.avgSharePrice:.2f}" if execution.avgSharePrice else "-",
            f"{commission:.2f}" if commission else "-",
            str(execution.orderId),
        )

    console.print(table)


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
    table.add_column("提交时间", justify="right")

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
        status = order.orderState.status
        status_color = "yellow" if status == "Submitted" else "green" if status == "Filled" else "white"

        # 时间
        submit_time = order_info.submittedTime.strftime("%Y-%m-%d %H:%M") if order_info.submittedTime else "-"

        table.add_row(
            str(order_info.orderId),
            symbol,
            action_str,
            order_type,
            str(order_info.totalQuantity),
            f"{order_info.lmtPrice:.2f}" if order_info.lmtPrice else "-",
            f"[{status_color}]{status}[/{status_color}]",
            submit_time,
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
def main(
    client_id: int,
    port: int,
    days: int,
    executions: bool,
    orders: bool,
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
                console.print(f"[bold]=== 最近 {days} 天成交记录 ===[/bold]")
                fills = await get_executions(ib, days)
                display_executions(fills)
                console.print()

            # 显示当前挂单
            if orders:
                console.print("[bold]=== 当前挂单 ===[/bold]")
                open_orders = get_open_orders(ib)
                display_orders(open_orders)

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
