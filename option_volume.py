#!/usr/bin/env python3
"""期权成交量查询脚本 - 使用 ib_async 查看某个期权近10日的成交量"""

import asyncio
import click
from ib_async import IB, Stock, Option, Contract, util
from rich.console import Console
from rich.table import Table
from rich.prompt import Prompt
import logging

logging.getLogger("ib_async.ib").setLevel(logging.ERROR)
console = Console()
util.logToConsole()

async def connect_ibkr(client_id: int = 1, port: int = 7496) -> IB:
    """连接到 IBKR TWS/IBGW"""
    ib = IB()
    await ib.connectAsync("127.0.0.1", port, clientId=client_id)

    # 获取账户列表
    accounts = ib.managedAccounts()
    if accounts:
        console.print(f"[cyan]账户:[/cyan] {accounts[0]}")
        # 订阅账户更新
        await ib.reqAccountUpdatesAsync(accounts[0])

    return ib


def create_option_contract(symbol: str, expiry: str, strike: float, right: str) -> Option:
    """
    创建期权合约

    Args:
        symbol: 标的资产代码 (如 AAPL, SPY)
        expiry: 到期日 (格式: YYYYMMDD)
        strike: 行权价
        right: 期权类型 (C=看涨, P=看跌)

    Returns:
        Option 合约对象
    """
    return Option(
        symbol=symbol,
        lastTradeDateOrContractMonth=expiry,
        strike=strike,
        right=right,
        exchange="",  # 空字符串让 IB 自动路由
        currency="USD",
    )


async def get_option_by_conid(ib: IB, con_id: int) -> Option | None:
    """通过 conId 获取期权合约"""
    contract = Contract(conId=con_id)
    details = await ib.reqContractDetailsAsync(contract)
    if details and details[0]:
        return details[0].contract
    return None


async def list_option_positions(ib: IB) -> list:
    """列出账户中所有期权持仓"""
    accounts = ib.managedAccounts()
    if not accounts:
        return []

    portfolio = ib.portfolio(accounts[0])
    options = []

    for p in portfolio:
        c = p.contract
        if hasattr(c, 'secType') and c.secType == 'OPT':
            options.append({
                'contract': c,
                'position': p.position,
                'market_value': p.marketValue,
            })

    return options


async def find_option_contract(ib: IB, symbol: str, expiry: str, strike: float, right: str) -> Option | None:
    """
    通过股票获取期权链，找到指定的期权合约
    """
    # 创建股票合约
    stock = Stock(symbol, "SMART", currency="USD", primaryExchange="NASDAQ")

    # 标准化股票合约
    qualified = await ib.qualifyContractsAsync(stock)
    if not qualified:
        console.print(f"[red]错误:[/red] 无法找到股票 {symbol}")
        return None
    stock_contract = qualified[0]

    # 获取期权链
    chains = await ib.reqSecDefOptParamsAsync(
        stock_contract.symbol, "", stock_contract.secType, stock_contract.conId
    )

    if not chains:
        console.print(f"[red]错误:[/red] 找不到 {symbol} 的期权链信息")
        return None

    # 检查到期日是否在链中
    for chain in chains:
        if expiry in chain.expirations:
            if strike in chain.strikes:
                console.print(f"[cyan]找到期权: {symbol} {expiry} ${strike} {right} 在交易所 {chain.exchange}[/cyan]")

                # 先尝试使用期权链返回的交易所 (GEMINI)
                opt = Option(
                    stock_contract.symbol,
                    expiry,
                    strike,
                    right,
                    chain.exchange,  # 使用正确的交易所
                )
                qualified_opts = await ib.qualifyContractsAsync(opt)
                if qualified_opts and qualified_opts[0]:
                    console.print(f"[green]Qualified 合约交易所: {qualified_opts[0].exchange}[/green]")
                    return qualified_opts[0]

                # 如果失败，尝试空交易所让 IB 自动选择
                opt = Option(
                    stock_contract.symbol,
                    expiry,
                    strike,
                    right,
                    "",
                )
                qualified_opts = await ib.qualifyContractsAsync(opt)

                # 如果返回的是歧义合约（多个交易所），取第一个
                if qualified_opts and len(qualified_opts) > 1:
                    # 检查是否有非 None 的结果
                    for q in qualified_opts:
                        if q is not None:
                            console.print(f"[cyan]选择交易所: {q.exchange}[/cyan]")
                            return q

                if qualified_opts and qualified_opts[0]:
                    return qualified_opts[0]

                # 如果还是失败，尝试通过 conId 直接获取
                # 先用 ambiguity resolver 获取
                import ib_async
                # 使用 IB 的 resolve_ambiguity 方法或直接取第一个
                return None

        else:
            console.print(f"[dim]到期日 {expiry} 不在 {chain.exchange} (可用的: {chain.expirations[:5]}...)[/dim]")

    # 如果通过期权链找不到，直接用 conId 构造合约
    # 通过搜索找到 conId
    for chain in chains:
        if expiry in chain.expirations and strike in chain.strikes:
            # 创建期权合约，使用 localSymbol 格式
            local_symbol = f"{symbol.ljust(6)} {expiry[-6:][:6]}{right}{str(int(strike * 1000)).zfill(7)}"
            console.print(f"[yellow]尝试通过 localSymbol: {local_symbol}[/yellow]")
            contract = Contract(
                secType="OPT",
                localSymbol=local_symbol,
            )
            details = await ib.reqContractDetailsAsync(contract)
            if details and details[0]:
                return details[0].contract

    return None


async def get_option_volume(ib: IB, contract: Option, days: int = 10) -> list:
    """
    获取期权近N日的成交量数据

    Args:
        ib: IB 连接对象
        contract: 期权合约
        days: 查询天数

    Returns:
        包含日期和成交量的列表
    """
    # 如果没有 conId，尝试通过期权链查找
    if not contract.conId:
        console.print("[yellow]合约未标准化，尝试通过期权链查找...[/yellow]")
        found = await find_option_contract(
            ib,
            contract.symbol,
            contract.lastTradeDateOrContractMonth,
            contract.strike,
            contract.right,
        )
        if found:
            contract = found
        else:
            console.print(f"[red]错误:[/red] 无法找到期权合约 {contract.symbol} {contract.lastTradeDateOrContractMonth} ${contract.strike} {contract.right}")
            console.print(f"[yellow]提示:[/yellow] 请检查到期日和行权价是否正确，可使用 --chain 参数查看可用期权")
            return []

    qualified_contract = contract
    console.print(f"[green]合约 conId:[/green] {qualified_contract.conId}")
    console.print(f"[green]合约交易所:[/green] {qualified_contract.exchange}")

    # 查询历史K线数据 - 尝试多个交易所
    duration = f"{days} D"
    exchanges_to_try = [qualified_contract.exchange, "SMART", "CBOE", "AMEX", "NYSE", "NASDAQ", "PHLY", "BATS"]

    bars = None
    for exchange in exchanges_to_try:
        if exchange == qualified_contract.exchange:
            # 已经尝试过了，跳过
            continue
        try:
            # 创建一个新合约，使用不同的交易所
            test_contract = Option(
                symbol=qualified_contract.symbol,
                lastTradeDateOrContractMonth=qualified_contract.lastTradeDateOrContractMonth,
                strike=qualified_contract.strike,
                right=qualified_contract.right,
                exchange=exchange,
                currency=qualified_contract.currency,
            )
            console.print(f"[dim]尝试交易所: {exchange}...[/dim]")
            bars = await ib.reqHistoricalDataAsync(
                test_contract,
                "",
                duration,
                "1 day",
                "TRADES",
                True,  # 使用 RTH (只交易时段)
            )
            if bars:
                console.print(f"[green]成功获取数据 from {exchange}[/green]")
                qualified_contract = test_contract
                break
        except Exception as e:
            console.print(f"[dim]交易所 {exchange} 失败: {str(e)[:50]}...[/dim]")
            continue

    # 第一次尝试用原始合约
    if not bars:
        try:
            bars = await ib.reqHistoricalDataAsync(
                qualified_contract,
                "",
                duration,
                "1 day",
                "TRADES",
                True,
            )
        except Exception as e:
            console.print(f"[yellow]原始合约查询失败: {str(e)[:80]}...[/yellow]")

    if not bars:
        console.print("[yellow]警告:[/yellow] 没有获取到历史数据（IBKR 某些期权不提供历史K线）")
        return []

    # 转换为列表并反转 (从旧到新)
    bars_list = list(bars)
    bars_list.reverse()

    return bars_list


async def get_option_realtime(ib: IB, con_id: int) -> Option | None:
    """获取期权实时行情"""
    contract = Contract(conId=con_id)

    # 订阅实时数据
    ticker = ib.reqMktData(contract)
    await asyncio.sleep(2)  # 等待数据更新

    return ticker


async def get_option_volume_by_conid(ib: IB, con_id: int, days: int = 10) -> tuple[list, Option | None]:
    """
    通过 conId 获取期权成交量数据

    Returns:
        (bars, contract)
    """
    contract = await get_option_by_conid(ib, con_id)
    if not contract:
        console.print(f"[red]错误:[/red] 无法通过 conId {con_id} 找到合约")
        return [], None

    console.print(f"[green]找到合约:[/green] {contract.symbol} ${contract.strike} {contract.right} {contract.lastTradeDateOrContractMonth}")

    # 查询历史K线数据
    duration = f"{days} D"
    try:
        bars = await ib.reqHistoricalDataAsync(
            contract,
            "",
            duration,
            "1 day",
            "TRADES",
            False,  # 不限制交易时段
        )

        if not bars:
            console.print("[yellow]警告:[/yellow] 没有获取到历史数据（IBKR 某些期权不提供历史K线），尝试获取实时数据...")

            # 尝试获取实时数据
            ticker = ib.reqMktData(contract)
            await asyncio.sleep(3)

            if ticker:
                console.print(f"\n[cyan]实时行情:[/cyan]")
                console.print(f"  买价: ${ticker.bid}" if ticker.bid else "  买价: N/A")
                console.print(f"  卖价: ${ticker.ask}" if ticker.ask else "  卖价: N/A")
                console.print(f"  最新价: ${ticker.last}" if ticker.last else "  最新价: N/A")
                console.print(f"  成交量: {ticker.volume}" if ticker.volume else "  成交量: N/A")
                console.print(f"  开盘: ${ticker.open}" if ticker.open else "  开盘: N/A")
                console.print(f"  最高: ${ticker.high}" if ticker.high else "  最高: N/A")
                console.print(f"  最低: ${ticker.low}" if ticker.low else "  最低: N/A")

                if ticker.modelGreeks:
                    console.print(f"\n[cyan]模型希腊值:[/cyan]")
                    console.print(f"  理论价: ${ticker.modelGreeks.optPrice}")
                    console.print(f"  Delta: {ticker.modelGreeks.delta}")
                    console.print(f"  Gamma: {ticker.modelGreeks.gamma}")
                    console.print(f"  Vega: {ticker.modelGreeks.vega}")
                    console.print(f"  Theta: {ticker.modelGreeks.theta}")

            return [], contract

        bars_list = list(bars)
        bars_list.reverse()

        return bars_list, contract
    except Exception as e:
        console.print(f"[yellow]查询历史数据出错:[/yellow] {e}")
        return [], contract


def display_volume_table(bars: list, contract: Option = None, title: str = None):
    """
    使用 rich 展示成交量表格
    """
    if contract:
        expiry = contract.lastTradeDateOrContractMonth
        strike = contract.strike
        right = contract.right
        symbol = contract.symbol
        if title is None:
            title = f"{symbol} {expiry} ${strike} {'Call' if right == 'C' else 'Put'} - 近{len(bars)}日成交量"

    table = Table(title=title)

    table.add_column("日期", style="cyan", no_wrap=True)
    table.add_column("开盘", justify="right")
    table.add_column("最高", justify="right")
    table.add_column("最低", justify="right")
    table.add_column("收盘", justify="right")
    table.add_column("成交量", justify="right", style="magenta")
    table.add_column("barCount", justify="right")

    total_volume = 0

    for bar in bars:
        date_str = bar.date.strftime("%Y-%m-%d") if hasattr(bar.date, 'strftime') else str(bar.date)[:10]
        volume = bar.volume if bar.volume else 0
        total_volume += volume

        table.add_row(
            date_str,
            f"{bar.open:.2f}" if bar.open else "N/A",
            f"{bar.high:.2f}" if bar.high else "N/A",
            f"{bar.low:.2f}" if bar.low else "N/A",
            f"{bar.close:.2f}" if bar.close else "N/A",
            f"{volume:,}",
            f"{bar.barCount}" if bar.barCount else "N/A",
        )

    # 添加总计行
    table.add_row(
        "[bold]总计[/bold]",
        "",
        "",
        "",
        "",
        f"[bold]{total_volume:,}[/bold]",
        "",
    )

    console.print(table)


async def show_option_chain(ib: IB, symbol: str):
    """显示期权链信息"""
    stock = Stock(symbol, "SMART", currency="USD", primaryExchange="NASDAQ")
    qualified = await ib.qualifyContractsAsync(stock)
    if not qualified:
        console.print(f"[red]错误:[/red] 无法找到股票 {symbol}")
        return

    stock_contract = qualified[0]
    console.print(f"[cyan]标的:[/cyan] {stock_contract.symbol}")

    chains = await ib.reqSecDefOptParamsAsync(
        stock_contract.symbol, "", stock_contract.secType, stock_contract.conId
    )

    if not chains:
        console.print(f"[red]错误:[/red] 找不到 {symbol} 的期权链信息")
        return

    console.print(f"[cyan]可用到期日:[/cyan] {chains[0].expirations}")
    console.print(f"[cyan]行权价范围:[/cyan] {chains[0].strikes[:20]}...")


async def query_option_volume(
    symbol: str = None,
    expiry: str = None,
    strike: float = None,
    right: str = None,
    con_id: int = None,
    days: int = 10,
    port: int = 7496,
    list_positions: bool = False,
    show_chain: bool = False,
):
    """
    查询期权成交量
    """
    console.print(f"[bold cyan]期权成交量查询工具[/bold cyan]")

    # 连接 IBKR
    try:
        ib = await connect_ibkr(port=port)
        console.print("[green]✓[/green] 已连接到 IBKR")
    except Exception as e:
        console.print(f"[red]✗[/red] 连接失败: {e}")
        return

    try:
        # 查看期权链
        if show_chain:
            if not symbol:
                console.print("[red]错误:[/red] 查看期权链需要提供 --symbol")
                return
            await show_option_chain(ib, symbol)
            return

        # 如果需要列出持仓
        if list_positions:
            options = await list_option_positions(ib)
            if options:
                console.print(f"\n[bold]账户中的期权持仓:[/bold]")
                for opt in options:
                    c = opt['contract']
                    pos = opt['position']
                    console.print(f"  {c.symbol} ${c.strike} {c.right} {c.lastTradeDateOrContractMonth} | 持仓: {pos} | conId: {c.conId}")
            else:
                console.print("[yellow]没有期权持仓[/yellow]")
            return

        # 通过 conId 查询
        if con_id:
            console.print(f"通过 conId 查询: {con_id}")
            bars, contract = await get_option_volume_by_conid(ib, con_id, days)
            if bars:
                display_volume_table(bars, contract)
            else:
                console.print("[yellow]没有获取到成交量数据[/yellow]")
            return

        # 通过 symbol/expiry/strike/right 查询
        if not symbol or not expiry or strike is None or not right:
            console.print("[red]错误:[/red] 请提供 --symbol/--expiry/--strike/--right 或 --conid")
            return

        console.print(f"标的: {symbol}, 到期日: {expiry}, 行权价: ${strike}, 类型: {'Call' if right == 'C' else 'Put'}")
        console.print(f"查询天数: {days}天")
        console.print()

        # 创建期权合约
        contract = create_option_contract(symbol, expiry, strike, right)

        # 获取成交量数据
        bars = await get_option_volume(ib, contract, days)

        if bars:
            display_volume_table(bars, contract)
        else:
            console.print("[yellow]没有获取到成交量数据[/yellow]")

    finally:
        ib.disconnect()
        console.print("[dim]已断开连接[/dim]")


@click.command()
@click.option("--symbol", "-s", help="标的资产代码 (如 AAPL, SPY)")
@click.option("--expiry", "-e", help="到期日 (格式: YYYYMMDD, 如 20240315)")
@click.option("--strike", "-k", type=float, help="行权价 (如 150.0)")
@click.option("--right", "-r", type=click.Choice(["C", "P"]), help="期权类型: C=看涨, P=看跌")
@click.option("--conid", "-i", "con_id", type=int, help="期权合约的 conId (可直接查询持仓中的期权)")
@click.option("--days", "-d", default=10, type=int, help="查询天数 (默认10天)")
@click.option("--port", "-p", default=7496, type=int, help="IBKR 端口 (默认 7496, TWS)")
@click.option("--list/--no-list", "list_positions", default=False, help="列出账户中的期权持仓")
@click.option("--chain/--no-chain", "show_chain", default=False, help="查看标的的期权链 (到期日和行权价)")
def main(symbol: str, expiry: str, strike: float, right: str, con_id: int, days: int, port: int, list_positions: bool, show_chain: bool):
    """查询期权近N日的成交量

    示例:
        python option_volume.py --list
        python option_volume.py --chain -s SMCI
        python option_volume.py --conid 798860935
        python option_volume.py -s TCOM -e 20260618 -k 50 -r P -d 10
    """
    asyncio.run(query_option_volume(symbol, expiry, strike, right, con_id, days, port, list_positions, show_chain))


if __name__ == "__main__":
    main()
