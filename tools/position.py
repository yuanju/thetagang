import asyncio
from asyncio import Future
from typing import List, Tuple

from ib_async import IB, PortfolioItem, util
from rich.panel import Panel
from rich.table import Table

from thetagang import log
from rich.console import Console
from rich import print as rich_print

from common.print import rich_print_pretty

from thetagang.fmt import dfmt, pfmt
from thetagang.ibkr import IBKR, IBKRRequestTimeout
from thetagang.util import portfolio_positions_to_dict, account_summary_to_dict

console = Console()
account_number = "U15981136"
attempts = 2

async def connect_ibkr(ib: IB, client_id: int = 1, port: int = 4001) -> IB:
    """连接到 IBKR TWS/IBGW"""
    # ib = IB()
    await ib.connectAsync("127.0.0.1", port, clientId=client_id)
    return ib

def partition_positions(
    self, portfolio_positions: List[PortfolioItem]
) -> Tuple[List[PortfolioItem], List[PortfolioItem]]:
    symbols = self.get_symbols()
    tracked_positions: List[PortfolioItem] = []
    untracked_positions: List[PortfolioItem] = []
    for item in portfolio_positions:
        if item.account != self.account_number or item.position == 0:
            continue
        if (
                item.contract.symbol in symbols
                or item.contract.symbol == "VIX"
                or item.contract.symbol
                == self.config.strategies.cash_management.cash_fund
        ):
            tracked_positions.append(item)
        else:
            untracked_positions.append(item)
    return (tracked_positions, untracked_positions)

async def get_position(ib: IB):
    rich_print(f"[bold green]已连接![/bold green]\n")
    ibkr = IBKR(ib, 60, 'SMART' )

    # 重要
    ibkr.set_market_data_type(1)

    account_summary = await ibkr.account_summary(account_number)
    account_summary = account_summary_to_dict(account_summary)

    if "NetLiquidation" not in account_summary:
        raise RuntimeError(
            f"Account number {account_number} appears invalid (no account data returned)"
        )

    table = Table(title="Account summary")
    table.add_column("Item")
    table.add_column("Value", justify="right")
    table.add_row(
        "NetLiquidation 净清算价值", dfmt(account_summary["NetLiquidation"].value, 0)
    )
    table.add_row(
        "ExcessLiquidity 超额流动性", dfmt(account_summary["ExcessLiquidity"].value, 0)
    )
    table.add_row("InitMarginReq 初始保证金要求", dfmt(account_summary["InitMarginReq"].value, 0))
    table.add_row(
        "FullMaintMarginReq 全额维持保证金要求", dfmt(account_summary["FullMaintMarginReq"].value, 0)
    )
    table.add_row("BuyingPower 购买力", dfmt(account_summary["BuyingPower"].value, 0))
    table.add_row("TotalCashValue 总现金", dfmt(account_summary["TotalCashValue"].value, 0))
    table.add_row("Cushion", pfmt(account_summary["Cushion"].value, 0))
    table.add_section()
    table.add_row(
        "Target buying power usage 目标购买力使用", 'dfmt(get_buying_power(account_summary), 0)'
    )
    log.print(Panel(table))

    # rprint(Pretty(ib.wrapper, expand_all=True, indent_guides=True, max_length=5))

    for attempt in range(1, attempts + 1):
        try:
            await ibkr.refresh_account_updates(account_number)
        except IBKRRequestTimeout as exc:
            if attempt == attempts:
                log.warning(
                    (
                        f"Attempt {attempt}/{attempts}: {exc}. "
                        "Proceeding without a fresh account update snapshot."
                    )
                )
            else:
                log.warning(
                    f"Attempt {attempt}/{attempts}: {exc}. Retrying account update request..."
                )
                await asyncio.sleep(1)
                continue

        portfolio_positions = ibkr.portfolio(account=account_number)
        rich_print(portfolio_positions, account_number)
        # 这里的filtered_position是指被追踪的持仓
        filtered_positions, untracked_positions = partition_positions(
            portfolio_positions
        )
        portfolio_by_symbol = portfolio_positions_to_dict(filtered_positions)
        filtered_conids = {item.contract.conId for item in filtered_positions}

        if portfolio_by_symbol:
            # Still verify against the latest positions snapshot to ensure we didn't
            # lose any holdings in the portfolio view.
            try:
                positions_snapshot = await ibkr.refresh_positions()
            except IBKRRequestTimeout as exc:
                log.warning(
                    f"Attempt {attempt}/{attempts}: {exc}. Retrying positions snapshot request..."
                )
                if attempt == attempts:
                    raise
                await asyncio.sleep(1)
                continue

            tracked_positions = [
                pos for pos in positions_snapshot
            ]
            # 从这里可以看出self.ibkr.portfolio中的数据可能是不全的，self.ibkr.refresh_positions中的数据是全的，参见: [[obsidian://open?vault=mine&file=ib.portfolio()%20vs%20ib.positions()]]
            missing_positions = [
                pos
                for pos in tracked_positions
                if pos.contract.conId not in filtered_conids
            ]

            if not missing_positions:
                ib.disconnect()
                return portfolio_by_symbol

            missing_symbols = ", ".join(
                sorted({pos.contract.symbol for pos in missing_positions})
            )
            log.warning(
                (
                    f"Attempt {attempt}/{attempts}: Portfolio snapshot is missing "
                    f"{len(missing_positions)} of {len(tracked_positions)} tracked "
                    f"positions (symbols: {missing_symbols}). Waiting briefly before retrying..."
                )
            )
            continue

        try:
            positions_snapshot = await ibkr.refresh_positions()
        except IBKRRequestTimeout as exc:
            log.warning(
                f"Attempt {attempt}/{attempts}: {exc}. Retrying positions snapshot request..."
            )
            if attempt == attempts:
                raise
            continue

        tracked_positions = [
            pos for pos in positions_snapshot
        ]

        ib.disconnect()

        if not tracked_positions:
            return portfolio_by_symbol

        log.warning(
            (
                f"Attempt {attempt}/{attempts}: IBKR reported {len(tracked_positions)} "
                "tracked positions but returned an empty portfolio snapshot. "
                "Waiting briefly before retrying..."
            )
        )

def run():
    console.print(f"[bold blue]连接到 IBKR...[/bold blue]")
    completion_future: Future[bool] = util.getLoop().create_future()
    ib = IB()
    async def onConnected() -> None:
        log.info(f"Connected to IB Gateway, serverVersion={ib.client.serverVersion()}")
        # rich_print_pretty(ib.wrapper)
        await get_position(ib)
        # 获取完持仓后立即完成，触发断开连接
        completion_future.set_result(True)
    async def updatePortfolio(obj) -> None:
        print('portfolio updated')
        rich_print_pretty(obj)
    ib.connectedEvent += onConnected
    ib.updatePortfolioEvent.connect(updatePortfolio)
    ib.connect("127.0.0.1", 4001, clientId=1, account=account_number,timeout=4)
    ib.run(completion_future)


if __name__ == "__main__":
    run()
