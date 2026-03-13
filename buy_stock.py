#!/usr/bin/env python3
"""股票买卖脚本 - 使用 ib_async 连接 IBKR 进行股票交易"""

import asyncio

import click
from ib_async import IB, Stock, Order, Trade
from rich.console import Console
from rich.table import Table

console = Console()


async def connect_ibkr(client_id: int = 1, port: int = 7496) -> IB:
    """连接到 IBKR TWS/IBGW"""
    ib = IB()
    await ib.connectAsync("127.0.0.1", port, clientId=client_id)
    return ib


async def place_stock_order(
    ib: IB,
    symbol: str,
    action: str,
    quantity: int,
    exchange: str = "SMART",
    primary_exchange: str = "NASDAQ",
) -> Trade:
    """
    下股票订单

    Args:
        ib: IB 连接对象
        symbol: 股票代码 (e.g., "AAPL")
        action: 交易动作 ("BUY" or "SELL")
        quantity: 交易数量
        exchange: 交易所 (default: "SMART")
        primary_exchange: 主交易所 (default: "NASDAQ")

    Returns:
        Trade 对象
    """
    # 创建股票合约
    stock = Stock(
        symbol,
        exchange,
        currency="USD",
        primaryExchange=primary_exchange,
    )

    # 标准化合约
    qualified = await ib.qualifyContractsAsync(stock)
    if not qualified:
        raise ValueError(f"无法标准化合约: {stock}")
    qualified_contract = qualified[0]

    console.print(f"[cyan]合约信息:[/cyan] {qualified_contract.symbol} ({qualified_contract.primaryExchange})")

    # 创建市价单
    order = Order(
        action=action.upper(),
        totalQuantity=quantity,
        orderType="MKT",
    )

    # 下单
    trade = ib.placeOrder(qualified_contract, order)
    return trade


def display_order_result(trade: Trade):
    """显示订单结果"""
    table = Table(title="订单状态")
    table.add_column("字段", style="cyan")
    table.add_column("值", style="green")

    table.add_row("合约", trade.contract.localSymbol if hasattr(trade.contract, 'localSymbol') else str(trade.contract))
    table.add_row("订单ID", str(trade.order.orderId))
    table.add_row("状态", trade.orderStatus.status)
    table.add_row("动作", trade.order.action)
    table.add_row("数量", str(trade.order.totalQuantity))
    table.add_row("已成交", str(trade.orderStatus.filled))
    table.add_row("剩余", str(trade.orderStatus.remaining))

    console.print(table)


@click.command()
@click.option(
    "--symbol",
    required=True,
    type=str,
    help="股票代码 (e.g., AAPL, TSLA)",
)
@click.option(
    "--action",
    required=True,
    type=click.Choice(["BUY", "SELL"], case_sensitive=False),
    help="交易动作: BUY=买入, SELL=卖出",
)
@click.option(
    "--quantity",
    required=True,
    type=int,
    help="交易数量",
)
@click.option(
    "--exchange",
    default="SMART",
    type=str,
    help="交易所 (default: SMART)",
)
@click.option(
    "--primary-exchange",
    default="NASDAQ",
    type=str,
    help="主交易所 (default: NASDAQ)",
)
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
def main(
    symbol: str,
    action: str,
    quantity: int,
    exchange: str,
    primary_exchange: str,
    client_id: int,
    port: int,
):
    """
    股票买卖脚本

    使用市场价直接下单买卖股票，挂单成功即可返回。

    示例:
        python buy_stock.py --symbol AAPL --action BUY --quantity 100
        python buy_stock.py --symbol TSLA --action SELL --quantity 50 --port 4002
    """
    # 标准化 action 参数
    action = action.upper()

    async def run():
        try:
            console.print(f"[bold blue]连接到 IBKR...[/bold blue]")
            ib = await connect_ibkr(client_id, port)
            console.print(f"[bold green]已连接![/bold green]")

            console.print(f"[cyan]下单信息:[/cyan] {action} {quantity} {symbol}")

            trade = await place_stock_order(
                ib=ib,
                symbol=symbol,
                action=action,
                quantity=quantity,
                exchange=exchange,
                primary_exchange=primary_exchange,
            )

            display_order_result(trade)

            # 断开连接
            ib.disconnect()

        except Exception as e:
            console.print(f"[bold red]错误: {e}[/bold red]")
            raise

    asyncio.run(run())


if __name__ == "__main__":
    main()

