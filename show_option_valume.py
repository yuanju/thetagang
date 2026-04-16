import asyncio
import click
from datetime import datetime

from ib_async import IB, Option, util
from rich.console import Console
from rich.table import Table

console = Console()

IB_HOST = "127.0.0.1"
IB_PORT = 7497
IB_CLIENT_ID = 1


async def fetch_data(symbol, strike, expiry, right):
    ib = IB()

    # ib_insync 本身支持 asyncio
    await ib.connectAsync(IB_HOST, IB_PORT, clientId=IB_CLIENT_ID)

    try:
        contract = Option(
            symbol=symbol,
            lastTradeDateOrContractMonth=expiry.replace("-", ""),
            strike=strike,
            right=right,
            exchange="SMART",
            currency="USD"
        )

        # 确保合约有效
        await ib.qualifyContractsAsync(contract)

        bars = await ib.reqHistoricalDataAsync(
            contract,
            endDateTime="",
            durationStr="10 D",
            barSizeSetting="1 day",
            whatToShow="TRADES",
            useRTH=True
        )

        if not bars:
            console.print("[red]没有数据[/red]")
            return

        table = Table(title=f"{symbol} {expiry} {right} 成交量")
        table.add_column("日期", justify="center")
        table.add_column("成交量", justify="right")

        for bar in bars:
            table.add_row(
                bar.date.strftime("%Y-%m-%d"),
                str(bar.volume)
            )

        console.print(table)

    finally:
        ib.disconnect()


@click.command()
@click.option("--symbol", prompt=True)
@click.option("--strike", type=float, prompt=True)
@click.option("--expiry", prompt="YYYY-MM-DD")
@click.option("--right", type=click.Choice(["C", "P"]), prompt=True)
def cli(symbol, strike, expiry, right):
    asyncio.run(fetch_data(symbol, strike, expiry, right))


if __name__ == "__main__":
    cli()
