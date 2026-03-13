#!/usr/bin/env python3
"""期权链查询脚本 - 使用 ib_async 查看某个标的的期权链"""

import asyncio

import click
from ib_async import IB, Stock, Option, Contract
from rich.console import Console
from rich.table import Table
import logging
logging.getLogger("ib_async.ib").setLevel(logging.ERROR)
console = Console()


async def connect_ibkr(client_id: int = 1, port: int = 7496) -> IB:
    """连接到 IBKR TWS/IBGW"""
    ib = IB()
    await ib.connectAsync("127.0.0.1", port, clientId=client_id)
    return ib


async def get_option_chain(
    ib: IB,
    symbol: str,
    exchange: str = "SMART",
    primary_exchange: str = "NASDAQ",
) -> tuple[list[Option], list[Option]]:
    """
    获取期权链

    Args:
        ib: IB 连接对象
        symbol: 标的资产代码
        exchange: 交易交易所
        primary_exchange: 主交易所

    Returns:
        (看涨期权列表, 看跌期权列表)
    """
    # 创建股票合约
    stock = Stock(symbol, exchange, currency="USD", primaryExchange=primary_exchange)

    # 标准化股票合约
    qualified = await ib.qualifyContractsAsync(stock)
    if not qualified:
        raise ValueError(f"无法标准化股票合约: {stock}")
    stock_contract = qualified[0]

    console.print(f"[cyan]标的:[/cyan] {stock_contract.symbol} ({stock_contract.primaryExchange})")

    # 获取期权的行权价和到期日信息
    chains = await ib.reqSecDefOptParamsAsync(
        stock_contract.symbol, "", stock_contract.secType, stock_contract.conId
    )

    if not chains:
        console.print(f"[yellow]警告:[/yellow] 找不到 {symbol} 的期权链信息")
        console.print(f"[yellow]可能原因:[/yellow]")
        console.print(f"  1. {symbol} 在 IBKR 没有可交易的期权")
        console.print(f"  2. 账户没有开通期权交易权限")
        console.print(f"  3. 该标的已被暂停交易期权")
        return [], []

    # 收集所有期权合约 - 只取第一个 chain
    all_calls: list[Option] = []
    all_puts: list[Option] = []

    # 取第一个 chain
    chain = chains[0]
    console.print(f"[cyan]交易所:[/cyan] {chain.exchange}")
    console.print(f"[cyan]到期日范围:[/cyan] {chain.expirations}")
    console.print(f"[cyan]行权价范围:[/cyan] {chain.strikes[:10]}..." if len(chain.strikes) > 10 else f"[cyan]行权价:[/cyan] {chain.strikes}")

    # 获取最近的几个到期日
    expirations = sorted(chain.expirations)[-5:] if len(chain.expirations) > 5 else chain.expirations
    strikes = chain.strikes

    # 限制行权价范围 - 取 ATM 附近的一部分
    if len(strikes) > 20:
        # 限制为前20个行权价
        strikes = strikes[:20]
        console.print(f"[yellow]限制行权价数量到:[/yellow] {strikes[-5]}...")

    # 创建期权合约列表 - 不指定交易所，让 IB 自动路由
    contracts = []
    for expiry in expirations:
        for strike in strikes:
            # 看涨期权 - 不指定 exchange，让 IB 自动识别
            call = Option(
                stock_contract.symbol,
                expiry,
                strike,
                "C",
                "",  # 空字符串让 IB 自动路由
            )
            contracts.append(call)

            # 看跌期权
            put = Option(
                stock_contract.symbol,
                expiry,
                strike,
                "P",
                "",
            )
            contracts.append(put)

    # 标准化期权合约
    if contracts:
        qualified_options = await ib.qualifyContractsAsync(*contracts)
        success_count = 0
        fail_count = 0
        for opt in qualified_options:
            if opt and opt.conId:
                success_count += 1
                if opt.right == "C":
                    all_calls.append(opt)
                elif opt.right == "P":
                    all_puts.append(opt)
            else:
                fail_count += 1
        console.print(f"[green]成功:[/green] {success_count}, [red]失败:[/red] {fail_count}")

    return all_calls, all_puts


async def get_option_details(ib: IB, contracts: list[Option]) -> list[dict]:
    """获取期权合约的详细市场数据"""
    if not contracts:
        return []

    # 限制并发请求数量
    batch_size = 20
    results = []

    for i in range(0, len(contracts), batch_size):
        batch = contracts[i:i + batch_size]

        # 请求市场数据
        tickers = []
        for contract in batch:
            ticker = ib.reqMktData(contract, "", False, False)
            tickers.append((contract, ticker))

        # 等待数据返回 (简短等待)
        await asyncio.sleep(1)

        # 提取数据
        for contract, ticker in tickers:
            if ticker.modelGreeks:
                results.append({
                    "contract": contract,
                    "bid": ticker.bid if ticker.bid else 0,
                    "ask": ticker.ask if ticker.ask else 0,
                    "last": ticker.last if ticker.last else 0,
                    "delta": ticker.modelGreeks.delta if ticker.modelGreeks.delta else 0,
                    "gamma": ticker.modelGreeks.gamma if ticker.modelGreeks.gamma else 0,
                    "theta": ticker.modelGreeks.theta if ticker.modelGreeks.theta else 0,
                    "vega": ticker.modelGreeks.vega if ticker.modelGreeks.vega else 0,
                    "iv": ticker.modelGreeks.impliedVol if ticker.modelGreeks.impliedVol else 0,
                })
            else:
                results.append({
                    "contract": contract,
                    "bid": ticker.bid if ticker.bid else 0,
                    "ask": ticker.ask if ticker.ask else 0,
                    "last": ticker.last if ticker.last else 0,
                    "delta": 0,
                    "gamma": 0,
                    "theta": 0,
                    "vega": 0,
                    "iv": 0,
                })

    return results


def display_option_chain(
    calls: list[Option],
    puts: list[Option],
    include_details: bool = False,
    ib: IB = None,
):
    """显示期权链"""
    # 按到期日分组
    call_expirations = {}
    put_expirations = {}

    for call in calls:
        exp = call.lastTradeDateOrContractMonth
        if exp not in call_expirations:
            call_expirations[exp] = []
        call_expirations[exp].append(call)

    for put in puts:
        exp = put.lastTradeDateOrContractMonth
        if exp not in put_expirations:
            put_expirations[exp] = []
        put_expirations[exp].append(put)

    # 显示看涨期权
    console.print("\n[bold green]=== 看涨期权 (CALLS) ===[/bold green]")

    for expiry in sorted(call_expirations.keys()):
        contracts = call_expirations[expiry]
        table = Table(title=f"到期日: {expiry}")
        table.add_column("行权价", justify="right")
        table.add_column("合约", style="cyan")
        table.add_column("交易所")

        # 按行权价排序
        for contract in sorted(contracts, key=lambda x: x.strike):
            table.add_row(
                f"${contract.strike:.2f}" if contract.strike else "N/A",
                contract.localSymbol or contract.symbol,
                contract.exchange,
            )

        console.print(table)

    # 显示看跌期权
    console.print("\n[bold red]=== 看跌期权 (PUTS) ===[/bold red]")

    for expiry in sorted(put_expirations.keys()):
        contracts = put_expirations[expiry]
        table = Table(title=f"到期日: {expiry}")
        table.add_column("行权价", justify="right")
        table.add_column("合约", style="cyan")
        table.add_column("交易所")

        # 按行权价排序
        for contract in sorted(contracts, key=lambda x: x.strike):
            table.add_row(
                f"${contract.strike:.2f}" if contract.strike else "N/A",
                contract.localSymbol or contract.symbol,
                contract.exchange,
            )

        console.print(table)

    console.print(f"\n[bold]总计:[/bold] {len(calls)} Calls, {len(puts)} Puts")


async def display_option_chain_with_prices(ib: IB, calls: list[Option], puts: list[Option]):
    """显示带价格的期权链"""
    console.print("\n[bold yellow]获取期权价格数据...[/bold yellow]")

    # 获取期权价格
    console.print("正在获取 Calls 数据...")
    call_details = await get_option_details(ib, calls[:50])  # 限制数量
    console.print("正在获取 Puts 数据...")
    put_details = await get_option_details(ib, puts[:50])  # 限制数量

    # 显示看涨期权
    console.print("\n[bold green]=== 看涨期权 (CALLS) - 含价格 ===[/bold green]")

    # 按到期日分组显示
    call_by_expiry = {}
    for detail in call_details:
        exp = detail["contract"].lastTradeDateOrContractMonth
        if exp not in call_by_expiry:
            call_by_expiry[exp] = []
        call_by_expiry[exp].append(detail)

    for expiry in sorted(call_by_expiry.keys()):
        contracts = call_by_expiry[expiry]
        table = Table(title=f"到期日: {expiry}")
        table.add_column("行权价", justify="right")
        table.add_column("Bid", justify="right")
        table.add_column("Ask", justify="right")
        table.add_column("Last", justify="right")
        table.add_column("IV %", justify="right")

        for detail in sorted(contracts, key=lambda x: x["contract"].strike):
            table.add_row(
                f"${detail['contract'].strike:.2f}",
                f"{detail['bid']:.2f}" if detail['bid'] else "-",
                f"{detail['ask']:.2f}" if detail['ask'] else "-",
                f"{detail['last']:.2f}" if detail['last'] else "-",
                f"{detail['iv']*100:.1f}%" if detail['iv'] else "-",
            )

        console.print(table)

    # 显示看跌期权
    console.print("\n[bold red]=== 看跌期权 (PUTS) - 含价格 ===[/bold red]")

    put_by_expiry = {}
    for detail in put_details:
        exp = detail["contract"].lastTradeDateOrContractMonth
        if exp not in put_by_expiry:
            put_by_expiry[exp] = []
        put_by_expiry[exp].append(detail)

    for expiry in sorted(put_by_expiry.keys()):
        contracts = put_by_expiry[expiry]
        table = Table(title=f"到期日: {expiry}")
        table.add_column("行权价", justify="right")
        table.add_column("Bid", justify="right")
        table.add_column("Ask", justify="right")
        table.add_column("Last", justify="right")
        table.add_column("IV %", justify="right")

        for detail in sorted(contracts, key=lambda x: x["contract"].strike):
            table.add_row(
                f"${detail['contract'].strike:.2f}",
                f"{detail['bid']:.2f}" if detail['bid'] else "-",
                f"{detail['ask']:.2f}" if detail['ask'] else "-",
                f"{detail['last']:.2f}" if detail['last'] else "-",
                f"{detail['iv']*100:.1f}%" if detail['iv'] else "-",
            )

        console.print(table)


@click.command()
@click.option(
    "--symbol",
    required=True,
    type=str,
    help="标的资产代码 (e.g., AAPL, SPY)",
)
@click.option(
    "--exchange",
    default="SMART",
    type=str,
    help="交易交易所 (default: SMART)",
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
@click.option(
    "--prices/--no-prices",
    default=True,
    help="显示期权价格和希腊字母 (default: True)",
)
def main(
    symbol: str,
    exchange: str,
    primary_exchange: str,
    client_id: int,
    port: int,
    prices: bool,
):
    """
    期权链查询脚本

    查看某个标的的期权链信息。

    示例:
        python option_chain.py --symbol AAPL
        python option_chain.py --symbol SPY --prices
    """
    async def run():
        try:
            console.print(f"[bold blue]连接到 IBKR...[/bold blue]")
            ib = await connect_ibkr(client_id, port)
            console.print(f"[bold green]已连接![/bold green]")

            console.print(f"\n[bold]查询 {symbol} 的期权链...[/bold]\n")

            calls, puts = await get_option_chain(
                ib=ib,
                symbol=symbol,
                exchange=exchange,
                primary_exchange=primary_exchange,
            )

            if prices:
                await display_option_chain_with_prices(ib, calls, puts)
            else:
                display_option_chain(calls, puts, ib=ib)

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

