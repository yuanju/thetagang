#!/usr/bin/env python3
"""持仓查询脚本 - 查看当前账户持仓情况"""

import asyncio

import click
from ib_async import IB
from rich.console import Console
from rich.table import Table

console = Console()


async def connect_ibkr(client_id: int = 1, port: int = 7496) -> IB:
    """连接到 IBKR TWS/IBGW"""
    ib = IB()
    await ib.connectAsync("127.0.0.1", port, clientId=client_id)
    return ib


async def get_positions(ib: IB, account: str = None) -> list:
    """获取持仓"""
    if account:
        return ib.positions(account)
    return ib.positions()


async def get_portfolio(ib: IB, account: str = None) -> list:
    """获取投资组合"""
    if account:
        return ib.portfolio(account)
    return ib.portfolio()


async def get_account_summary(ib: IB, account: str = None) -> dict:
    """获取账户摘要"""
    if not account:
        # 获取第一个账户
        accounts = ib.managedAccounts()
        if accounts:
            account = accounts[0]
        else:
            return {}

    summary = await ib.accountSummaryAsync(account)
    result = {}
    for item in summary:
        result[item.tag] = item.value
    return result


def display_positions(positions: list):
    """显示持仓"""
    if not positions:
        console.print("[yellow]没有持仓[/yellow]")
        return

    table = Table(title="持仓 Position")
    table.add_column("标的", style="cyan")
    table.add_column("合约ID", justify="right")
    table.add_column("合约类型", justify="right")
    table.add_column("数量", justify="right")
    table.add_column("平均成本", justify="right")
    table.add_column("当前价", justify="right")
    table.add_column("市值", justify="right")
    table.add_column("盈亏", justify="right")
    table.add_column("盈亏%", justify="right")

    for pos in positions:
        contract = pos.contract
        avg_cost = pos.avgCost or 0
        quantity = pos.position
        print(contract)
        # Position 对象没有 marketPrice，使用 avgCost 和 position 估算
        # 如果有 marketValue，可以用它来计算当前价
        market_value = getattr(pos, 'marketValue', None)
        if market_value and quantity:
            market_price = market_value / quantity
        else:
            market_price = 0

        # 计算市值和盈亏
        if market_price and quantity and avg_cost:
            cost_basis = avg_cost * quantity
            unrealized_pnl = market_value - cost_basis
            pnl_percent = (unrealized_pnl / cost_basis * 100) if cost_basis else 0
        else:
            market_value = 0
            unrealized_pnl = 0
            pnl_percent = 0

        # 格式化显示
        pnl_color = "green" if unrealized_pnl >= 0 else "red"
        pnl_str = f"[{pnl_color}]{unrealized_pnl:,.2f}[/{pnl_color}]"
        pnl_pct_str = f"[{pnl_color}]{pnl_percent:,.2f}%[/{pnl_color}]" if pnl_percent else "-"

        table.add_row(
            contract.symbol,
            str(contract.conId) if contract.conId else "-",
            contract.secType if contract.conId else "-",
            str(quantity),
            f"{avg_cost:.2f}" if avg_cost else "-",
            f"{market_price:.2f}" if market_price else "-",
            f"{market_value:,.2f}" if market_value else "-",
            pnl_str,
            pnl_pct_str,
        )

    console.print(table)


def display_portfolio(portfolio: list):
    """显示投资组合（包含未实现盈亏）"""
    if not portfolio:
        console.print("[yellow]投资组合为空[/yellow]")
        return

    table = Table(title="投资组合 Portfolio")
    table.add_column("标的", style="cyan")
    table.add_column("数量", justify="right")
    table.add_column("平均成本", justify="right")
    table.add_column("当前价", justify="right")
    table.add_column("市值", justify="right")
    table.add_column("未实现盈亏", justify="right")

    for item in portfolio:
        contract = item.contract
        quantity = item.position or 0
        avg_cost = item.avgCost or 0
        market_price = item.marketValue / quantity if quantity and item.marketValue else 0

        market_value = item.marketValue or 0
        unrealized_pnl = item.unrealizedPNL or 0

        pnl_color = "green" if unrealized_pnl >= 0 else "red"
        pnl_str = f"[{pnl_color}]{unrealized_pnl:,.2f}[/{pnl_color}]" if unrealized_pnl else "-"

        table.add_row(
            contract.symbol if hasattr(contract, 'symbol') else str(contract),
            str(quantity),
            f"{avg_cost:.2f}" if avg_cost else "-",
            f"{market_price:.2f}" if market_price else "-",
            f"{market_value:,.2f}" if market_value else "-",
            pnl_str,
        )

    console.print(table)


def display_account_summary(summary: dict):
    """显示账户摘要"""
    if not summary:
        console.print("[yellow]无法获取账户摘要[/yellow]")
        return

    table = Table(title="账户摘要 Account Summary")
    table.add_column("项目", style="cyan")
    table.add_column("值", justify="right", style="green")

    key_names = {
        "NetLiquidation": "净资产",
        "TotalCashValue": "现金价值",
        "BuyingPower": "购买力",
        "ExcessLiquidity": "超额流动性",
        "Cushion": "缓冲",
        "InitMarginReq": "初始保证金",
        "MaintMarginReq": "维持保证金",
        "UnrealizedPnL": "未实现盈亏",
        "RealizedPnL": "已实现盈亏",
        "DividendBalance": "股息余额",
        "AccruedCash": "应计现金",
    }

    for key, value in summary.items():
        name = key_names.get(key, key)
        try:
            # 尝试转换为数字
            num_value = float(value)
            display_value = f"{num_value:,.2f}"
        except (ValueError, TypeError):
            display_value = str(value)

        table.add_row(name, display_value)

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
    "--account",
    default=None,
    type=str,
    help="账户ID (默认使用第一个账户)",
)
@click.option(
    "--positions/--no-positions",
    default=True,
    help="显示持仓 (default: True)",
)
@click.option(
    "--portfolio/--no-portfolio",
    default=True,
    help="显示投资组合 (default: True)",
)
@click.option(
    "--summary/--no-summary",
    default=True,
    help="显示账户摘要 (default: True)",
)
def main(
    client_id: int,
    port: int,
    account: str,
    positions: bool,
    portfolio: bool,
    summary: bool,
):
    """
    持仓查询脚本

    查看当前账户的持仓、投资组合和账户摘要。

    示例:
        python position.py
        python position.py --port 4002
    """
    async def run():
        try:
            console.print(f"[bold blue]连接到 IBKR...[/bold blue]")
            ib = await connect_ibkr(client_id, port)
            console.print(f"[bold green]已连接![/bold green]\n")

            # 获取账户列表
            accounts = ib.managedAccounts()
            account_id = account if account else (accounts[0] if accounts else None)

            console.print(f"[cyan]账户:[/cyan] {account_id}\n")

            # 显示账户摘要
            if summary:
                console.print("[bold]=== 账户摘要 ===[/bold]")
                account_summary = await get_account_summary(ib, account_id)
                display_account_summary(account_summary)
                console.print()

            # 显示持仓
            if positions:
                console.print("[bold]=== 持仓 ===[/bold]")
                pos_list = await get_positions(ib, account_id)
                display_positions(pos_list)
                console.print()

            # 显示投资组合
            if portfolio:
                console.print("[bold]=== 投资组合 ===[/bold]")
                portfolio_items = await get_portfolio(ib, account_id)
                display_portfolio(portfolio_items)

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

