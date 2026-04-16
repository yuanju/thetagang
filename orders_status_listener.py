#!/usr/bin/env python3
"""订单查询脚本 - 查看最近7日内的订单和成交记录"""

import asyncio
import signal
from datetime import datetime, timedelta

import click
from ib_async import (
    ExecutionFilter,
    IB,
    Trade,
)
from rich.console import Console
from rich.table import Table

from thetagang.db import sqlite_db_path, DataStore
from thetagang.ding import send_markdown

from common.print import print_ding_orders

console = Console()

data_store = None
sqlite_path = sqlite_db_path('sqlite:///data/thetagang.db')
if sqlite_path:
    sqlite_path.parent.mkdir(parents=True, exist_ok=True)
data_store = DataStore('sqlite:///data/thetagang.db', './thetagang.toml', False, None)

async def connect_ibkr(client_id: int = 1, port: int = 7496) -> IB:
    """连接到 IBKR TWS/IBGW"""
    ib = IB()
    await ib.connectAsync("127.0.0.1", port, clientId=client_id)
    return ib
def orderStatusChanged(trade: Trade):
    if "Filled" in trade.orderStatus.status:
        console.print(f"{trade.contract.symbol}: Order filled" )
        console.print(print_ding_orders([trade], "订单已成交"), style="debug")
    if "Fill" in trade.orderStatus.status:
        console.print(
            f"{trade.contract.symbol}: {trade.orderStatus.filled} filled, {trade.orderStatus.remaining} remaining"
        )
        console.print(print_ding_orders([trade], "订单已成交"), style="debug")
    if "Cancelled" in trade.orderStatus.status:
        console.print(f"{trade.contract.symbol}: Order cancelled, trade={trade}", style="warning")
        console.print(print_ding_orders([trade], "订单已取消"), style="debug")
    else:
        console.print(
            f"{trade.contract.symbol}: Order updated with status={trade.orderStatus.status}"
        )
        console.print(print_ding_orders([trade], "订单已更新"), style="debug")
    if data_store:
        data_store.record_order_status(trade)
@click.command()
@click.option(
    "--client-id",
    default=1,
    type=int,
    help="客户端ID (default: 1)",
)
@click.option(
    "--port",
    default=4001,
    type=int,
    help="TWS/IBGW 端口 (default: 7496 for TWS, 4002 for IBGW paper)",
)
def main(
    client_id: int,
    port: int
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
        ib = None
        try:
            console.print(f"[bold blue]连接到 IBKR...[/bold blue]")
            ib = await connect_ibkr(client_id, port)
            console.print(f"[bold green]已连接![/bold green]\n")

            console.print(f"[bold green]订单状态监控中 (按 Ctrl+C 退出)...[/bold green]\n")

            ib.orderStatusEvent += orderStatusChanged

            # 等待中断信号
            shutdown_event = asyncio.Event()

            def shutdown_handler():
                console.print("\n[yellow]收到退出信号，正在关闭...[/yellow]")
                shutdown_event.set()

            loop = asyncio.get_running_loop()
            loop.add_signal_handler(signal.SIGINT, shutdown_handler)
            loop.add_signal_handler(signal.SIGTERM, shutdown_handler)

            await shutdown_event.wait()

        except Exception as e:
            console.print(f"[bold red]错误: {e}[/bold red]")
            import traceback
            traceback.print_exc()
            raise
        finally:
            if ib and ib.isConnected():
                ib.disconnect()
                console.print("[dim]已断开 IBKR 连接[/dim]")

    asyncio.run(run())


if __name__ == "__main__":
    main()
