#!/usr/bin/env python3
"""持仓查询脚本 - 查看当前账户持仓情况"""

import asyncio
from dataclasses import asdict
from typing import Dict, List

import click
from ib_async import IB, util, PortfolioItem, Option, Stock
from rich.console import Console
from rich.table import Table

from thetagang.fmt import ifmt, ffmt, dfmt, pfmt
from thetagang.ibkr import IBKR
from thetagang.options import option_dte
from thetagang.util import position_pnl

console = Console()


async def connect_ibkr(client_id: int = 1, port: int = 4001, account:str = '') -> IB:
    """连接到 IBKR TWS/IBGW"""
    ib = IB()
    await ib.connectAsync("127.0.0.1", port, clientId=client_id, account=account, timeout=5)
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


async def fetch_market_prices(ib: IB, positions: list) -> dict:
    """获取持仓的实时市场价格"""
    prices = {}

    # 股票和期权分开处理
    stock_contracts = []
    opt_contracts = []

    for pos in positions:
        contract = pos.contract
        sec_type = getattr(contract, 'secType', 'STK')
        if sec_type == 'OPT':
            opt_contracts.append((pos, contract))
        else:
            stock_contracts.append((pos, contract))

    # 处理股票/ETF
    for pos, contract in stock_contracts:
        ticker = ib.reqMktData(contract, genericTickList="", regulatorySnapshot=False)
        await asyncio.sleep(0.3)
        print(ticker)
        price = None
        if ticker:
            if ticker.last:
                price = float(ticker.last)
            elif ticker.bid and ticker.ask and ticker.bid > 0:
                price = (float(ticker.bid) + float(ticker.ask)) / 2
        prices[contract.conId] = price
    print('=====')
    # 处理期权 - 需要更长时间获取数据
    for pos, contract in opt_contracts:
        # 期权需要获取期权链
        ib.reqMarketDataType(1)
        ticker = ib.reqMktData(contract)
        await asyncio.sleep(2)
        print(pos)
        current_price = None
        if hasattr(ticker, 'last') and ticker.last and ticker.last != 0:
            current_price = ticker.last
        elif hasattr(ticker, 'modelGreeks') and ticker.modelGreeks and ticker.modelGreeks.optPrice:
            current_price = ticker.modelGreeks.optPrice

        prices[contract.conId] = current_price

    return prices


async def display_positions_with_prices(ib: IB, positions: list):
    """显示持仓（带实时价格）"""
    if not positions:
        console.print("[yellow]没有持仓[/yellow]")
        return

    # 获取实时市场价格
    prices = await fetch_market_prices(ib, positions)
    print(prices)
    table = Table(title="持仓 Position (带实时价格)")
    table.add_column("标的", style="cyan")
    table.add_column("类型", justify="right")
    table.add_column("数量", justify="right")
    table.add_column("平均成本", justify="right")
    table.add_column("当前价", justify="right")
    table.add_column("市值", justify="right")
    table.add_column("盈亏", justify="right")
    table.add_column("盈亏%", justify="right")

    for pos in positions:
        contract = pos.contract
        con_id = contract.conId
        quantity = int(pos.position)

        # 获取合约乘数（期权默认100，股票为1）
        multiplier = float(getattr(contract, 'multiplier', None) or 1)

        # 获取实时价格
        market_price = float(prices.get(con_id) or 0)

        # 从 position 获取成本
        avg_cost = float(pos.avgCost or 0)
        cost_per_share = avg_cost / multiplier if avg_cost else 0

        # 使用实时价格计算市值和盈亏
        if market_price and quantity and avg_cost:
            market_value = market_price * quantity * multiplier
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

        # 合约类型简称
        sec_type = getattr(contract, 'secType', 'STK')

        # 对于期权，显示行权价和到期日
        if sec_type == "OPT":
            strike = getattr(contract, 'strike', None)
            expiry = getattr(contract, 'lastTradeDateOrContractMonth', None)
            right = getattr(contract, 'right', '')
            if strike and expiry:
                display_symbol = f"{contract.symbol} {right}${strike} {expiry}"
            else:
                display_symbol = contract.symbol
        else:
            display_symbol = contract.symbol

        table.add_row(
            display_symbol,
            sec_type,
            str(quantity),
            f"{cost_per_share:.2f}" if cost_per_share else "-",
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

    position_values: Dict[int, Dict[str, str]] = {}

    def load_position_task(portfolioItem: PortfolioItem) -> None:
        qty = portfolioItem.position
        if isinstance(qty, float):
            qty_display = ifmt(int(qty)) if qty.is_integer() else ffmt(qty, 4)
        else:
            qty_display = ifmt(int(qty))
        position_values[portfolioItem.contract.conId] = {
            "qty": qty_display,
            "mktprice": dfmt(portfolioItem.marketPrice),
            "avgprice": dfmt(portfolioItem.averageCost),
            "value": dfmt(portfolioItem.marketValue, 0),
            "cost": dfmt(portfolioItem.averageCost * portfolioItem.position, 0),
            "unrealized": dfmt(portfolioItem.unrealizedPNL, 0),
            "p&l": pfmt(position_pnl(portfolioItem), 1),
        }
        if isinstance(portfolioItem.contract, Option):
            position_values[portfolioItem.contract.conId]["avgprice"] = dfmt(
                portfolioItem.averageCost / float(portfolioItem.contract.multiplier)
            )
            position_values[portfolioItem.contract.conId]["strike"] = dfmt(
                portfolioItem.contract.strike
            )
            position_values[portfolioItem.contract.conId]["dte"] = str(
                option_dte(portfolioItem.contract.lastTradeDateOrContractMonth)
            )
            position_values[portfolioItem.contract.conId]["exp"] = str(
                portfolioItem.contract.lastTradeDateOrContractMonth
            )
    for portfolioItem in portfolio:
        load_position_task(portfolioItem)

    table = Table(title="投资组合 Portfolio")
    table.add_column("Symbol")
    table.add_column("R")
    table.add_column("Qty", justify="right")
    table.add_column("MktPrice", justify="right")
    table.add_column("AvgPrice", justify="right")
    table.add_column("Value", justify="right")
    table.add_column("Cost", justify="right")
    table.add_column("Unrealized P&L", justify="right")
    table.add_column("P&L", justify="right")
    table.add_column("Strike", justify="right")
    table.add_column("Exp", justify="right")
    table.add_column("DTE", justify="right")

    def getval(col: str, conId: int) -> str:
        return position_values[conId][col]

    def add_symbol_positions(symbol: str, positions: List[PortfolioItem]) -> None:
        table.add_row(symbol)
        sorted_positions = sorted(
            positions,
            key=lambda p: (
                option_dte(p.contract.lastTradeDateOrContractMonth)
                if isinstance(p.contract, Option)
                else -1
            ),  # Keep stonks on top
        )

        for pos in sorted_positions:
            conId = pos.contract.conId
            if isinstance(pos.contract, Stock):
                table.add_row(
                    "",
                    "S",
                    getval("qty", conId),
                    getval("mktprice", conId),
                    getval("avgprice", conId),
                    getval("value", conId),
                    getval("cost", conId),
                    getval("unrealized", conId),
                    getval("p&l", conId),
                )
            elif isinstance(pos.contract, Option):
                table.add_row(
                    "",
                    pos.contract.right,
                    getval("qty", conId),
                    getval("mktprice", conId),
                    getval("avgprice", conId),
                    getval("value", conId),
                    getval("cost", conId),
                    getval("unrealized", conId),
                    getval("p&l", conId),
                    getval("strike", conId),
                    getval("exp", conId),
                    getval("dte", conId),
                )

    first = True
    for portfolioItem in portfolio:
        if not first:
            table.add_section()
        first = False
        add_symbol_positions(portfolioItem.contract.symbol, [portfolioItem])

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
        "NetLiquidation": "净资产 (NetLiquidation)",
        "TotalCashValue": "现金价值 (TotalCashValue)",
        "BuyingPower": "购买力 (BuyingPower)",
        "ExcessLiquidity": "超额流动性 (ExcessLiquidity)",
        "Cushion": "缓冲 (Cushion)",
        "InitMarginReq": "初始保证金 (InitMarginReq)",
        "MaintMarginReq": "维持保证金 (MaintMarginReq)",
        "UnrealizedPnL": "未实现盈亏 (UnrealizedPnL)",
        "RealizedPnL": "已实现盈亏 (RealizedPnL)",
        "DividendBalance": "股息余额 (DividendBalance)",
        "AccruedCash": "应计现金 (AccruedCash)",
        "AvailableFunds": "可用资金 (AvailableFunds)",
        "FullInitMarginReq": "完整初始保证金 (FullInitMarginReq)",
        "FullMaintMarginReq": "完整维持保证金 (FullMaintMarginReq)",
        "GrossPositionValue": "持仓总值 (GrossPositionValue)",
        "NetOptionValue": "期权净值 (NetOptionValue)",
        "Leverage": "杠杆率 (Leverage)",
        "EquityWithLoanValue": "含贷款股权价值 (EquityWithLoanValue)",
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
    default=4001,
    type=int,
    help="TWS/IBGW 端口 (default: 4001 for TWS, 4002 for IBGW paper)",
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
            ib = await connect_ibkr(client_id, port, account)
            console.print(f"[bold green]已连接![/bold green]\n")

            # 获取账户列表
            accounts = ib.managedAccounts()
            print(accounts)
            account_id = account if account else (accounts[0] if accounts else None)

            console.print(f"[cyan]账户:[/cyan] {account_id}\n")

            # 显示账户摘要
            if summary:
                # console.print("[bold]=== 账户摘要 ===[/bold]")
                account_summary = await get_account_summary(ib, account_id)
                display_account_summary(account_summary)
                console.print()


            # ibkr = IBKR(ib, 60, 'SMART', None)
            # 获取投资组合数据（用于补充市值和盈亏）
            # portfolio_positions = ibkr.portfolio('U15981136')
            # 关键：使用 reqPositionsAsync

            # console.print("[cyan]请求账户更新...[/cyan]")
            # await ib.reqAccountUpdatesAsync(account_id)
            # console.print("[cyan]获取持仓...[/cyan]")
            positions = await ib.reqPositionsAsync()
            console.print(f"[green]获取到 {len(positions)} 个持仓[/green]")
            # 关键：使用 await 等待数据更新
            await asyncio.sleep(2) # 或者使用 app.waitOnUpdate() 的异步版本
            console.print("[cyan]获取投资组合...[/cyan]")
            portfolio_items = await get_portfolio(ib, account_id)
            console.print(f"[green]获取到 {len(portfolio_items)} 个投资组合项目[/green]")
            # # 显示持仓
            # if positions:
            #     console.print("[bold]=== 持仓 ===[/bold]")
            #     pos_list = await get_positions(ib, 'U15981136')
            #     await display_positions_with_prices(ib, pos_list)
            #     console.print()

            # 显示投资组合
            if portfolio_items:
                # console.print("[bold]=== 投资组合 ===[/bold]")
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
