#!/usr/bin/env python3
"""期权买卖脚本 - 使用 ib_async 连接 IBKR 进行期权交易"""

import asyncio

import click
from ib_async import IB, Contract, Option, Order, Trade
from rich.console import Console
from rich.table import Table

console = Console()


async def connect_ibkr(client_id: int = 1, port: int = 7497) -> IB:
    """连接到 IBKR TWS/IBGW"""
    ib = IB()
    await ib.connectAsync("127.0.0.1", port, clientId=client_id)
    return ib


async def place_option_order(
    ib: IB,
    symbol: str,
    expiry: str,
    strike: float,
    right: str,
    action: str,
    quantity: int,
    exchange: str = "SMART",
) -> Trade:
    """
    下期权订单

    Args:
        ib: IB 连接对象
        symbol: 标的资产代码 (e.g., "AAPL")
        expiry: 到期日 (e.g., "20240920")
        strike: 行权价
        right: 期权类型 ("C" for Call, "P" for Put)
        action: 交易动作 ("BUY" or "SELL")
        quantity: 交易数量
        exchange: 交易所 (default: "SMART")

    Returns:
        Trade 对象
    """
    # 创建期权合约
    contract = Option(
        symbol,
        expiry,
        strike,
        right,
        exchange,
        currency="USD",
    )

    # 标准化合约
    qualified = await ib.qualifyContractsAsync(contract)
    if not qualified:
        raise ValueError(f"无法标准化合约: {contract}")
    qualified_contract = qualified[0]

    console.print(f"[cyan]合约信息:[/cyan] {qualified_contract.localSymbol}")

    # 创建市价单
    order = Order(
        action=action,
        totalQuantity=quantity,
        orderType="MKT",
    )

    # 下单
    trade = ib.placeOrder(qualified_contract, order)
    return trade


async def wait_for_order_fill(trade: Trade, timeout: int = 30) -> bool:
    """等待订单成交"""
    if trade.isDone():
        return True

    event = asyncio.Event()

    def on_status(trade: Trade):
        if trade.isDone():
            event.set()

    trade.statusEvent += on_status
    try:
        await asyncio.wait_for(event.wait(), timeout=timeout)
        return True
    except asyncio.TimeoutError:
        return False
    finally:
        trade.statusEvent -= on_status


def display_order_result(trade: Trade):
    """显示订单结果"""
    table = Table(title="订单状态")
    table.add_column("字段", style="cyan")
    table.add_column("值", style="green")

    table.add_row("合约", trade.contract.localSymbol)
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
    help="标的资产代码 (e.g., AAPL)",
)
@click.option(
    "--expiry",
    required=True,
    type=str,
    help="到期日 (e.g., 20240920)",
)
@click.option(
    "--strike",
    required=True,
    type=float,
    help="行权价 (e.g., 150.0)",
)
@click.option(
    "--right",
    required=True,
    type=click.Choice(["C", "P", "CALL", "PUT"], case_sensitive=False),
    help="期权类型: C/CALL=看涨期权, P/PUT=看跌期权",
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
    "--client-id",
    default=1,
    type=int,
    help="客户端ID (default: 1)",
)
@click.option(
    "--port",
    default=7496,
    type=int,
    help="TWS/IBGW 端口 (default: 7497 for TWS, 4002 for IBGW paper)",
)
@click.option(
    "--wait/--no-wait",
    default=True,
    help="等待订单成交 (default: True)",
)
def main(
    symbol: str,
    expiry: str,
    strike: float,
    right: str,
    action: str,
    quantity: int,
    exchange: str,
    client_id: int,
    port: int,
    wait: bool,
):
    """
    期权买卖脚本

    使用市场价直接下单买卖期权。

    示例:
        python buy_option.py --symbol AAPL --expiry 20240920 --strike 150 --right C --action BUY --quantity 1
    """
    # 标准化 right 参数
    right = right.upper()[0]  # C or P

    # 标准化 action 参数
    action = action.upper()

    async def run():
        try:
            console.print(f"[bold blue]连接到 IBKR...[/bold blue]")
            ib = await connect_ibkr(client_id, port)
            console.print(f"[bold green]已连接![/bold green]")

            console.print(
                f"[cyan]下单信息:[/cyan] {action} {quantity} {symbol} {expiry} {strike} {right}"
            )

            trade = await place_option_order(
                ib=ib,
                symbol=symbol,
                expiry=expiry,
                strike=strike,
                right=right,
                action=action,
                quantity=quantity,
                exchange=exchange,
            )

            display_order_result(trade)

            if wait:
                console.print(f"[yellow]等待订单成交...[/yellow]")
                success = await wait_for_order_fill(trade)
                if success:
                    console.print(f"[bold green]订单已成交![/bold green]")
                    display_order_result(trade)
                else:
                    console.print(
                        f"[bold red]订单未在 {30} 秒内成交，当前状态: {trade.orderStatus.status}[/bold red]"
                    )

            # 断开连接
            ib.disconnect()

        except Exception as e:
            console.print(f"[bold red]错误: {e}[/bold red]")
            raise

    asyncio.run(run())


if __name__ == "__main__":
    main()
