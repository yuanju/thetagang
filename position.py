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
        price = None
        if ticker:
            if ticker.last:
                price = float(ticker.last)
            elif ticker.bid and ticker.ask and ticker.bid > 0:
                price = (float(ticker.bid) + float(ticker.ask)) / 2
        prices[contract.conId] = price

    # 处理期权 - 需要更长时间获取数据
    for pos, contract in opt_contracts:
        # 期权需要获取期权链
        ticker = ib.reqMktData(contract, genericTickList="100,101,104,105", regulatorySnapshot=False)
        await asyncio.sleep(1.0)  # 期权需要更长等待时间
        price = None
        if ticker:
            # 期权通常用 bid/ask 中间价
            if ticker.bid and ticker.ask and float(ticker.bid) > 0:
                price = (float(ticker.bid) + float(ticker.ask)) / 2
            elif ticker.last:
                price = float(ticker.last)
            elif ticker.modelGreeks and ticker.modelGreeks.optPrice:
                # 使用模型价格
                price = float(ticker.modelGreeks.optPrice)
        prices[contract.conId] = price

    return prices


async def display_positions_with_prices(positions: list, portfolio: list = None):
    """显示持仓（带实时价格）"""
    if not positions:
        console.print("[yellow]没有持仓[/yellow]")
        return

    # 构建 portfolio 映射（用于获取市值和盈亏数据）
    # portfolio 数据更完整，包含 marketValue 和 unrealizedPNL
    portfolio_map = {}
    if portfolio:
        for item in portfolio:
            con_id = item.contract.conId
            portfolio_map[con_id] = item

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

        # 优先使用 portfolio 数据
        pf_item = portfolio_map.get(con_id)

        if pf_item:
            # 使用 portfolio 的完整数据
            market_value = float(pf_item.marketValue or 0)
            unrealized_pnl = float(pf_item.unrealizedPNL or 0)
            avg_cost = float(pf_item.avgCost or 0)

            # 计算当前价（每股）
            if quantity and market_value:
                market_price = market_value / quantity / multiplier
            else:
                market_price = 0

            # 计算成本（每股）
            if avg_cost:
                cost_per_share = avg_cost / multiplier
            else:
                cost_per_share = 0

            # 计算盈亏百分比
            cost_basis = market_value - unrealized_pnl
            pnl_percent = (unrealized_pnl / cost_basis * 100) if cost_basis else 0
        else:
            # 没有 portfolio 数据，尝试使用 position 数据
            avg_cost = float(pos.avgCost or 0)
            cost_per_share = avg_cost / multiplier if avg_cost else 0
            market_value = 0
            unrealized_pnl = 0
            market_price = 0
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

        market_value = item.marketValue or 0
        market_price = market_value / quantity if quantity and market_value else 0

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

            # 获取投资组合数据（用于补充市值和盈亏）
            portfolio_items = await get_portfolio(ib, account_id) if (positions or portfolio) else []

            # 显示持仓
            if positions:
                console.print("[bold]=== 持仓 ===[/bold]")
                pos_list = await get_positions(ib, account_id)
                await display_positions_with_prices(pos_list, portfolio_items)
                console.print()

            # 显示投资组合
            if portfolio:
                console.print("[bold]=== 投资组合 ===[/bold]")
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
