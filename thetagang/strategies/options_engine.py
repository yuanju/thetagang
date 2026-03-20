from __future__ import annotations

import math
import sys
from typing import Any, Coroutine, Dict, List, Optional, Protocol, Tuple

from ib_async import AccountValue, PortfolioItem, Ticker, util
from ib_async.contract import ComboLeg, Contract, Index, Option, Stock
from rich.console import Group
from rich.table import Table

from thetagang import log
from thetagang.config import Config
from thetagang.fmt import dfmt, ifmt, pfmt
from thetagang.ibkr import IBKR, RequiredFieldValidationError, TickerField
from thetagang.options import option_dte
from thetagang.strategies.runtime_services import resolve_symbol_configs
from thetagang.trading_operations import (
    NoValidContractsError,
    OptionChainScanner,
    OrderOperations,
)
from thetagang.util import (
    calculate_net_short_positions,
    count_long_option_positions,
    count_short_option_positions,
    get_higher_price,
    get_lower_price,
    get_short_positions,
    get_target_calls,
    midpoint_or_market_price,
    position_pnl,
    weighted_avg_long_strike,
    weighted_avg_short_strike,
)


class OptionsRuntimeServices(Protocol):
    def get_symbols(self) -> List[str]: ...

    def get_primary_exchange(self, symbol: str) -> str: ...

    def get_buying_power(self, account_summary: Dict[str, AccountValue]) -> int: ...

    async def get_maximum_new_contracts_for(
        self,
        symbol: str,
        primary_exchange: str,
        account_summary: Dict[str, AccountValue],
    ) -> int: ...

    async def get_write_threshold(
        self, ticker: Ticker, right: str
    ) -> tuple[float, float]: ...

    def get_close_price(self, ticker: Ticker) -> float: ...


class OptionsStrategyEngine:
    def __init__(
        self,
        *,
        config: Config,
        ibkr: IBKR,
        option_scanner: OptionChainScanner,
        order_ops: OrderOperations,
        services: OptionsRuntimeServices,
        target_quantities: Dict[str, int],
        has_excess_puts: set[str],
        has_excess_calls: set[str],
        qualified_contracts: Dict[int, Contract],
    ) -> None:
        self.config = config
        self.ibkr = ibkr
        self.option_scanner = option_scanner
        self.order_ops = order_ops
        self.services = services
        self.target_quantities = target_quantities
        self.has_excess_puts = has_excess_puts
        self.has_excess_calls = has_excess_calls
        self.qualified_contracts = qualified_contracts

    def get_symbols(self) -> List[str]:
        return self.services.get_symbols()

    def get_primary_exchange(self, symbol: str) -> str:
        return self.services.get_primary_exchange(symbol)

    def get_buying_power(self, account_summary: Dict[str, AccountValue]) -> int:
        return self.services.get_buying_power(account_summary)

    async def get_maximum_new_contracts_for(
        self,
        symbol: str,
        primary_exchange: str,
        account_summary: Dict[str, AccountValue],
    ) -> int:
        return await self.services.get_maximum_new_contracts_for(
            symbol, primary_exchange, account_summary
        )

    async def get_write_threshold(
        self, ticker: Ticker, right: str
    ) -> tuple[float, float]:
        return await self.services.get_write_threshold(ticker, right)

    def format_weight_info(
        self,
        symbol: str,
        position_values: Dict[str, float],
        weight_base_value: float,
        symbol_configs: Dict[str, Any],
    ) -> Tuple[str, str]:
        if weight_base_value <= 0:
            return "", ""

        current_value = position_values.get(symbol, 0)
        current_weight = current_value / weight_base_value
        target_weight = symbol_configs[symbol].weight
        abs_diff = current_weight - target_weight
        rel_diff = (abs_diff / target_weight) if target_weight > 0 else 0

        weight_info = f"当前权重: {current_weight:.1%} (目标权重: {target_weight:.1%})"
        diff_info = f"绝对差额: {abs_diff:+.1%} (相对差额: {rel_diff:+.1%})"

        if abs(rel_diff) < 0.1:
            color = "[green]"
        elif abs(rel_diff) < 0.25:
            color = "[yellow]"
        else:
            color = "[red]"

        formatted_weight = f"{color}{weight_info}[/]"
        formatted_diff = f"{color}{diff_info}[/]"
        return formatted_weight, formatted_diff

    async def check_for_uncovered_positions(
        self,
        account_summary: Dict[str, AccountValue],
        portfolio_positions: Dict[str, List[PortfolioItem]],
    ) -> Tuple[Table, List[Tuple[str, str, int, int]]]:
        call_actions_table = Table(title="Call writing summary")
        call_actions_table.add_column("Symbol")
        call_actions_table.add_column("Action")
        call_actions_table.add_column("Detail")
        calculate_net_contracts = (
            self.config.strategies.wheel.defaults.write_when.calculate_net_contracts
        )

        to_write: List[Tuple[str, str, int, int]] = []
        symbols = set(self.get_symbols())
        symbol_configs = resolve_symbol_configs(
            self.config, context="options uncovered position check"
        )

        async def update_to_write_task(symbol: str) -> None:
            if symbol not in symbols:
                return

            # 净卖call仓位
            short_call_count = (
                calculate_net_short_positions(portfolio_positions[symbol], "C")
                if calculate_net_contracts
                else count_short_option_positions(portfolio_positions[symbol], "C")
            )

            # 标的股票仓位
            stock_count = math.floor(
                sum(
                    [
                        p.position
                        for p in portfolio_positions[symbol]
                        if isinstance(p.contract, Stock)
                    ]
                )
            )

            # 限价 = max([配置限价] + [平均成本,...]) python中列表相加操作即列表合并
            strike_limit = math.ceil(
                max(
                    [self.config.get_strike_limit(symbol, "C") or 0]
                    + [
                        p.averageCost or 0
                        for p in portfolio_positions[symbol]
                        if isinstance(p.contract, Stock)
                    ]
                )
            )

            # 当前标的的目标售卖股票数量
            # self.target_quantities这个值是在check_if_can_write_puts中的calculate_target_position_task函数中算出来的
            target_quantity = self.target_quantities.get(symbol)
            if target_quantity is None:
                log.warning(
                    f"{symbol}: Missing target share quantity before call-write planning; defaulting to current stock count."
                )
                target_quantity = stock_count

            # 当前标的可卖call的数量
            target_short_calls = get_target_calls(
                self.config, symbol, stock_count, target_quantity
            )
            # 还需购买call的数量 = 可卖数量 - 净持仓数量
            new_contracts_needed = target_short_calls - short_call_count
            # 当前超卖的call数量
            excess_calls = short_call_count - target_short_calls

            if excess_calls > 0:
                self.has_excess_calls.add(symbol)
                call_actions_table.add_row(
                    symbol,
                    "[yellow]None",
                    f"[yellow]Warning: excess_calls={excess_calls} stock_count={stock_count},"
                    f" short_call_count={short_call_count}, target_short_calls={target_short_calls}",
                )

            # 获取最大新合约数(受maximum_new_contracts_percent限制)
            maximum_new_contracts = await self.get_maximum_new_contracts_for(
                symbol,
                self.get_primary_exchange(symbol),
                account_summary,
            )

            # 真实需要卖的合约数
            calls_to_write = max(
                [0, min([new_contracts_needed, maximum_new_contracts])]
            )

            ticker = await self.ibkr.get_ticker_for_stock(
                symbol, self.get_primary_exchange(symbol)
            )

            (write_threshold, absolute_daily_change) = (None, None)

            async def is_ok_to_write_calls(
                symbol: str,
                ticker: Optional[Ticker],
                calls_to_write: int,
                stock_count: int,
            ) -> bool:
                nonlocal write_threshold, absolute_daily_change
                if (
                    not ticker
                    or calls_to_write <= 0
                    or not self.config.trading_is_allowed(symbol)
                ):
                    return False

                if self.config.is_sell_only_rebalancing(symbol):
                    return False

                (can_write_when_green, can_write_when_red) = self.config.can_write_when(
                    symbol, "C"
                )

                close_price = self.services.get_close_price(ticker)
                if not can_write_when_green and ticker.marketPrice() > close_price:
                    call_actions_table.add_row(
                        symbol,
                        "[cyan1]None",
                        f"[cyan1]Skipping because can_write_when_green={can_write_when_green} and marketPrice={ticker.marketPrice():.2f} > close={close_price}",
                    )
                    return False
                if not can_write_when_red and ticker.marketPrice() < close_price:
                    call_actions_table.add_row(
                        symbol,
                        "[cyan1]None",
                        f"[cyan1]Skipping because can_write_when_red={can_write_when_red} and marketPrice={ticker.marketPrice():.2f} < close={close_price}",
                    )
                    return False

                (
                    write_threshold,
                    absolute_daily_change,
                ) = await self.get_write_threshold(ticker, "C")
                # 当日波动率没达到阈值
                if absolute_daily_change < write_threshold:
                    call_actions_table.add_row(
                        symbol,
                        "[cyan1]None",
                        f"[cyan1]Need to write {calls_to_write} calls, "
                        f"but skipping because absolute_daily_change={absolute_daily_change:.2f}"
                        f" less than write_threshold={write_threshold:.2f}",
                    )
                    return False

                symbol_config = symbol_configs[symbol]
                min_percent = symbol_config.write_calls_only_min_threshold_percent
                if min_percent is None:
                    min_percent = self.config.strategies.wheel.defaults.write_when.calls.min_threshold_percent

                min_percent_relative = (
                    symbol_config.write_calls_only_min_threshold_percent_relative
                )
                if min_percent_relative is None:
                    min_percent_relative = self.config.strategies.wheel.defaults.write_when.calls.min_threshold_percent_relative

                if min_percent is not None or min_percent_relative is not None:
                    current_stock_value = stock_count * ticker.marketPrice()

                    if min_percent is not None:
                        net_liquidation_value = float(
                            account_summary["NetLiquidation"].value
                        )
                        position_percent = current_stock_value / net_liquidation_value

                        if position_percent < min_percent:
                            call_actions_table.add_row(
                                symbol,
                                "[yellow]None",
                                f"[yellow]Position {position_percent:.1%} of NLV below threshold {min_percent:.1%}",
                            )
                            return False

                    if (
                        min_percent_relative is not None
                        and self.target_quantities.get(symbol, 0) > 0
                    ):
                        target_value = (
                            self.target_quantities[symbol] * ticker.marketPrice()
                        )
                        if target_value > 0:
                            relative_excess = (
                                current_stock_value - target_value
                            ) / target_value
                            if relative_excess < min_percent_relative:
                                call_actions_table.add_row(
                                    symbol,
                                    "[yellow]None",
                                    f"[yellow]Position excess {relative_excess:.1%} below threshold {min_percent_relative:.1%}",
                                )
                                return False

                return True

            ok_to_write = await is_ok_to_write_calls(
                symbol, ticker, calls_to_write, stock_count
            )
            # 哪个高取哪个
            strike_limit = math.ceil(max([strike_limit, ticker.marketPrice()]))

            if calls_to_write > 0 and ok_to_write:
                call_actions_table.add_row(
                    symbol,
                    "[green]Write",
                    f"[green]Will write {calls_to_write} calls, {new_contracts_needed} needed, "
                    f"limited to {maximum_new_contracts} new contracts, at or above strike {dfmt(strike_limit)}"
                    f" (target_short_calls={target_short_calls} short_call_count={short_call_count} "
                    f"absolute_daily_change={absolute_daily_change:.2f} write_threshold={write_threshold:.2f})",
                )
                to_write.append(
                    (
                        symbol,
                        self.get_primary_exchange(symbol),
                        calls_to_write,
                        strike_limit,
                    )
                )
            elif calls_to_write > 0 and self.config.is_sell_only_rebalancing(symbol):
                call_actions_table.add_row(
                    symbol,
                    "[cyan1]None",
                    "[cyan1]Skipping call writing for sell-only rebalancing symbol",
                )

        tasks: List[Coroutine[Any, Any, None]] = [
            update_to_write_task(symbol) for symbol in portfolio_positions
        ]
        await log.track_async(tasks, description="Checking for uncovered positions...")
        return (call_actions_table, to_write)

    async def write_calls(self, calls: List[Any]) -> None:
        for symbol, primary_exchange, quantity, strike_limit in calls:
            try:
                sell_ticker = await self.option_scanner.find_eligible_contracts(
                    Stock(
                        symbol,
                        self.order_ops.get_order_exchange(),
                        currency="USD",
                        primaryExchange=primary_exchange,
                    ),
                    "C",
                    strike_limit,
                    minimum_price=lambda: self.config.runtime.orders.minimum_credit,
                )
            except (RuntimeError, NoValidContractsError):
                log.error(
                    f"{symbol}: Finding eligible contracts failed. Continuing anyway..."
                )
                continue

            order = self.order_ops.create_limit_order(
                action="SELL",
                quantity=quantity,
                limit_price=round(get_higher_price(sell_ticker), 2),
            )
            self.order_ops.enqueue_order(sell_ticker.contract, order)

    async def write_puts(
        self, puts: List[Tuple[str, str, int, Optional[float]]]
    ) -> None:
        for symbol, primary_exchange, quantity, strike_limit in puts:
            try:
                sell_ticker = await self.option_scanner.find_eligible_contracts(
                    Stock(
                        symbol,
                        self.order_ops.get_order_exchange(),
                        currency="USD",
                        primaryExchange=primary_exchange,
                    ),
                    "P",
                    strike_limit,
                    minimum_price=lambda: self.config.runtime.orders.minimum_credit,
                )
            except (RuntimeError, NoValidContractsError):
                log.error(
                    f"{symbol}: Finding eligible contracts failed. Continuing anyway..."
                )
                continue

            order = self.order_ops.create_limit_order(
                action="SELL",
                quantity=quantity,
                limit_price=round(get_higher_price(sell_ticker), 2),
            )
            self.order_ops.enqueue_order(sell_ticker.contract, order)
    # 它是一个决策分析函数，用于判断当前市场环境下，哪些股票应该卖出看跌期权（Cash Secured Put），以及卖出多少合约。
    async def check_if_can_write_puts(
        self,
        account_summary: Dict[str, AccountValue],
        portfolio_positions: Dict[str, List[PortfolioItem]],
    ) -> Tuple[Table, Table, List[Tuple[str, str, int, Optional[float]]]]:
        stock_positions = [
            position
            for symbol in portfolio_positions
            for position in portfolio_positions[symbol]
            if isinstance(position.contract, Stock)
        ]

        total_buying_power = self.get_buying_power(account_summary)

        # 计算没只股票的仓位
        stock_symbols: Dict[str, PortfolioItem] = {}
        for stock in stock_positions:
            symbol = stock.contract.symbol
            stock_symbols[symbol] = stock

        # 每支股票的市场价
        position_values: Dict[str, float] = {}
        for stock in stock_positions:
            symbol = stock.contract.symbol
            if (
                symbol != "VIX"
                and symbol != self.config.strategies.cash_management.cash_fund
            ):
                position_values[symbol] = stock.marketValue

        targets: Dict[str, float] = {}
        target_additional_quantity: Dict[str, Dict[str, int | bool]] = {}
        calculate_net_contracts = (
            self.config.strategies.wheel.defaults.write_when.calculate_net_contracts
        )

        positions_summary_table = Table(title="Positions summary", show_edge=True)
        positions_summary_table.add_column("Symbol")
        positions_summary_table.add_column("Shares 股票仓位", justify="right")
        positions_summary_table.add_column("Short puts 卖出看跌期权", justify="right")
        positions_summary_table.add_column("Long puts 买入看跌期权", justify="right")
        if calculate_net_contracts:
            positions_summary_table.add_column("Net short puts 净卖出看跌期权", justify="right")
        positions_summary_table.add_column("Short calls 卖出看涨期权", justify="right")
        positions_summary_table.add_column("Long calls 买入看涨期权", justify="right")
        if calculate_net_contracts:
            positions_summary_table.add_column("Net short calls 净卖出看涨期权", justify="right")
        positions_summary_table.add_column("Target value 目标价值", justify="right")
        positions_summary_table.add_column("Target share qty 目标股份数量", justify="right")
        positions_summary_table.add_column("Net target shares 净目标股份数", justify="right")
        positions_summary_table.add_column("Net target contracts 净目标合约数", justify="right")

        put_actions_table = Table(title="Put writing summary", show_lines=True)
        put_actions_table.add_column("Symbol")
        put_actions_table.add_column("Action")
        put_actions_table.add_column("Detail")

        symbol_configs = resolve_symbol_configs(
            self.config, context="options put write check"
        )

        # 计算目标仓位
        async def calculate_target_position_task(symbol: str) -> None:
            ticker = await self.ibkr.get_ticker_for_stock(
                symbol, self.get_primary_exchange(symbol)
            )
            # 配置中的标的的当前股票仓位
            current_position = math.floor(
                stock_symbols[symbol].position if symbol in stock_symbols else 0
            )

            # 当前标的的目标预算
            targets[symbol] = round(
                symbol_configs[symbol].weight * total_buying_power, 2
            )
            # 当前标的的市场价
            market_price = ticker.marketPrice()

            if (
                not market_price
                or math.isnan(market_price)
                or math.isclose(market_price, 0)
            ):
                log.error(
                    f"Invalid market price for {symbol} (market_price={market_price}), skipping for now"
                )
                return
            # 当前标的的目标售卖数量
            self.target_quantities[symbol] = math.floor(targets[symbol] / market_price)
            if symbol not in position_values:
                position_values[symbol] = current_position * market_price

            # 当前标的是否已有持仓
            if symbol in portfolio_positions:
                # 卖出看跌持仓
                net_short_put_count = short_put_count = count_short_option_positions(
                    portfolio_positions[symbol], "P"
                )
                # 卖出看跌平均持仓价
                short_put_avg_strike = weighted_avg_short_strike(
                    portfolio_positions[symbol], "P"
                )
                # 买入看跌的持仓
                long_put_count = count_long_option_positions(
                    portfolio_positions[symbol], "P"
                )
                # 买入看跌的平均持仓价
                long_put_avg_strike = weighted_avg_long_strike(
                    portfolio_positions[symbol], "P"
                )
                # 净卖出看涨仓位
                net_short_call_count = short_call_count = count_short_option_positions(
                    portfolio_positions[symbol], "C"
                )
                # 卖出看涨平均持仓价
                short_call_avg_strike = weighted_avg_short_strike(
                    portfolio_positions[symbol], "C"
                )
                # 买入看涨仓位
                long_call_count = count_long_option_positions(
                    portfolio_positions[symbol], "C"
                )
                # 买入看涨平均持仓价
                long_call_avg_strike = weighted_avg_long_strike(
                    portfolio_positions[symbol], "C"
                )

                if calculate_net_contracts:
                    # 净卖出看跌仓位
                    net_short_put_count = calculate_net_short_positions(
                        portfolio_positions[symbol], "P"
                    )
                    # 净卖出看涨仓位
                    net_short_call_count = calculate_net_short_positions(
                        portfolio_positions[symbol], "C"
                    )
            else:
                net_short_put_count = short_put_count = long_put_count = 0
                short_put_avg_strike = long_put_avg_strike = None
                net_short_call_count = short_call_count = long_call_count = 0
                short_call_avg_strike = long_call_avg_strike = None

            # 当前 equity_rebalance 没有配置，没配置默认是关闭的
            if self.config.is_buy_only_rebalancing(symbol):
                qty_to_write = 0
                net_target_shares = self.target_quantities[symbol] - current_position
                net_target_puts = 0
            else:
                qty_to_write = math.floor(
                    self.target_quantities[symbol]
                    - current_position
                    - 100 * net_short_put_count # 卖出put，将来可能会被行权买入的，所以这里这净卖出的put也给算进去
                )
                # 净还可买入的股票数
                net_target_shares = qty_to_write
                # 净还可卖出的put数
                net_target_puts = net_target_shares // 100

            if calculate_net_contracts:
                positions_summary_table.add_row(
                    symbol,
                    ifmt(current_position),
                    ifmt(short_put_count),
                    ifmt(long_put_count),
                    ifmt(net_short_put_count),
                    ifmt(short_call_count),
                    ifmt(long_call_count),
                    ifmt(net_short_call_count),
                    dfmt(targets[symbol]),
                    ifmt(self.target_quantities[symbol]),
                    ifmt(net_target_shares),
                    ifmt(net_target_puts),
                )
                positions_summary_table.add_row(
                    "",
                    "",
                    dfmt(short_put_avg_strike),
                    dfmt(long_put_avg_strike),
                    "",
                    dfmt(short_call_avg_strike),
                    dfmt(long_call_avg_strike),
                )

                weight_info, diff_info = self.format_weight_info(
                    symbol, position_values, total_buying_power, symbol_configs
                )
                if weight_info:
                    positions_summary_table.add_row(
                        "",
                        "",
                        "",
                        "",
                        "",
                        "",
                        "",
                        "",
                        weight_info,
                        "",
                        diff_info,
                        "",
                    )
            else:
                positions_summary_table.add_row(
                    symbol,
                    ifmt(current_position),
                    ifmt(short_put_count),
                    ifmt(long_put_count),
                    ifmt(short_call_count),
                    ifmt(long_call_count),
                    dfmt(targets[symbol]),
                    ifmt(self.target_quantities[symbol]),
                    ifmt(net_target_shares),
                    ifmt(net_target_puts),
                )
                positions_summary_table.add_row(
                    "",
                    "",
                    dfmt(short_put_avg_strike),
                    dfmt(long_put_avg_strike),
                    dfmt(short_call_avg_strike),
                    dfmt(long_call_avg_strike),
                )

                weight_info, diff_info = self.format_weight_info(
                    symbol, position_values, total_buying_power, symbol_configs
                )
                if weight_info:
                    positions_summary_table.add_row(
                        "",
                        "",
                        "",
                        "",
                        "",
                        "",
                        weight_info,
                        "",
                        diff_info,
                        "",
                    )
            positions_summary_table.add_section()


            async def is_ok_to_write_puts(
                symbol: str,
                ticker: Ticker,
                puts_to_write: int,
            ) -> bool:
                if puts_to_write <= 0 or not self.config.trading_is_allowed(symbol):
                    return False

                if self.config.is_buy_only_rebalancing(symbol):
                    return False

                (can_write_when_green, can_write_when_red) = self.config.can_write_when(
                    symbol, "P"
                )

                close_price = self.services.get_close_price(ticker)
                if not can_write_when_green and ticker.marketPrice() > close_price:
                    put_actions_table.add_row(
                        symbol,
                        "[cyan1]None",
                        f"[cyan1]Skipping because can_write_when_green={can_write_when_green} and marketPrice={ticker.marketPrice():.2f} > close={close_price}",
                    )
                    return False
                if not can_write_when_red and ticker.marketPrice() < close_price:
                    put_actions_table.add_row(
                        symbol,
                        "[cyan1]None",
                        f"[cyan1]Skipping because can_write_when_red={can_write_when_red} and marketPrice={ticker.marketPrice():.2f} < close={close_price}",
                    )
                    return False

                (
                    write_threshold,
                    absolute_daily_change,
                ) = await self.get_write_threshold(ticker, "P")
                if absolute_daily_change < write_threshold:
                    put_actions_table.add_row(
                        symbol,
                        "[cyan1]None",
                        f"[cyan1]Need to write {puts_to_write} puts, but skipping because absolute_daily_change={absolute_daily_change:.2f} less than write_threshold={write_threshold:.2f}[/cyan1]",
                    )
                    return False
                return True

            ok_to_write = await is_ok_to_write_puts(symbol, ticker, net_target_puts)
            target_additional_quantity[symbol] = {
                "qty": net_target_puts,
                "ok_to_write": ok_to_write,
            }

        tasks: List[Coroutine[Any, Any, None]] = [
            calculate_target_position_task(symbol) for symbol in symbol_configs.keys()
        ]
        await log.track_async(tasks, description="Calculating target positions...")

        to_write: List[Tuple[str, str, int, Optional[float]]] = []

        async def update_to_write_task(
            symbol: str, target: Dict[str, int | bool]
        ) -> None:
            ok_to_write = target["ok_to_write"]
            additional_quantity = target["qty"]
            if additional_quantity >= 1 and ok_to_write:
                maximum_new_contracts = await self.get_maximum_new_contracts_for(
                    symbol,
                    self.get_primary_exchange(symbol),
                    account_summary,
                )
                puts_to_write = min([additional_quantity, maximum_new_contracts])
                if puts_to_write > 0:
                    strike_limit = self.config.get_strike_limit(symbol, "P")
                    if strike_limit:
                        put_actions_table.add_row(
                            symbol,
                            "[green]Write",
                            f"[green]Will write {puts_to_write} puts, {additional_quantity}"
                            f" needed, capped at {maximum_new_contracts}, at or below strike ${strike_limit}",
                        )
                    else:
                        put_actions_table.add_row(
                            symbol,
                            "[green]Write",
                            f"[green]Will write {puts_to_write} puts, {additional_quantity}"
                            f" needed, capped at {maximum_new_contracts}",
                        )
                    to_write.append(
                        (
                            symbol,
                            self.get_primary_exchange(symbol),
                            puts_to_write,
                            strike_limit,
                        )
                    )
            elif additional_quantity < 0:
                self.has_excess_puts.add(symbol)
                put_actions_table.add_row(
                    symbol,
                    "[yellow]None",
                    "[yellow]Warning: excess positions based "
                    "on net liquidation and target margin usage",
                )

        tasks = [
            update_to_write_task(symbol, target)
            for symbol, target in target_additional_quantity.items()
        ]
        await log.track_async(tasks, description="Generating positions summary...")

        return (positions_summary_table, put_actions_table, to_write)

    # 获取卖出合约
    def get_short_contracts(
        self, portfolio_positions: Dict[str, List[PortfolioItem]], right: str
    ) -> List[PortfolioItem]:
        ret: List[PortfolioItem] = []
        for symbol in portfolio_positions:
            ret = ret + get_short_positions(portfolio_positions[symbol], right)
        return ret

    def get_short_calls(
        self, portfolio_positions: Dict[str, List[PortfolioItem]]
    ) -> List[PortfolioItem]:
        return self.get_short_contracts(portfolio_positions, "C")

    # 获取持仓中已卖出的put
    def get_short_puts(
        self, portfolio_positions: Dict[str, List[PortfolioItem]]
    ) -> List[PortfolioItem]:
        return self.get_short_contracts(portfolio_positions, "P")

    async def put_is_itm(self, contract: Contract) -> bool:
        ticker = await self.ibkr.get_ticker_for_stock(
            contract.symbol, contract.primaryExchange
        )
        return contract.strike >= ticker.marketPrice()

    async def call_is_itm(self, contract: Contract) -> bool:
        if contract.symbol == "VIX":
            vix_contract = Index("VIX", "CBOE", "USD")
            ticker = await self.ibkr.get_ticker_for_contract(vix_contract)
        else:
            ticker = await self.ibkr.get_ticker_for_stock(
                contract.symbol, contract.primaryExchange
            )
        return contract.strike <= ticker.marketPrice()

    def position_can_be_closed(self, position: PortfolioItem, table: Table) -> bool:
        if not self.config.trading_is_allowed(position.contract.symbol):
            return False

        close_at_pnl = self.config.strategies.wheel.defaults.roll_when.close_at_pnl
        if close_at_pnl:
            pnl = position_pnl(position)
            if pnl > close_at_pnl:
                table.add_row(
                    f"{position.contract.localSymbol}",
                    "[deep_sky_blue1]Close",
                    f"[deep_sky_blue1]Will be closed because P&L of {pfmt(pnl, 1)} is > {pfmt(close_at_pnl, 1)}",
                )
                return True
        return False

    def put_can_be_closed(self, put: PortfolioItem, table: Table) -> bool:
        return self.position_can_be_closed(put, table)

    def call_can_be_closed(self, call: PortfolioItem, table: Table) -> bool:
        return self.position_can_be_closed(call, table)

    # put期权是否可以被滚动
    async def put_can_be_rolled(self, put: PortfolioItem, table: Table) -> bool:
        # put.position > 0 表示有多头仓位
        if put.position > 0:
            return False
        if not self.config.trading_is_allowed(put.contract.symbol):
            return False
        try:
            itm = await self.put_is_itm(put.contract)
        except RequiredFieldValidationError:
            log.error(
                f"Checking rollable puts failed for #{put.contract.symbol}. Continuing anyway..."
            )
            return False

        # 如果当前期权已是ITM 且 配置了有itm强制roll
        if (
            isinstance(put.contract, Option)
            and itm
            and self.config.strategies.wheel.defaults.roll_when.puts.always_when_itm
        ):
            table.add_row(
                f"{put.contract.localSymbol}",
                "[blue]Roll",
                f"[blue]Will be rolled because put is ITM and always_when_itm={self.config.strategies.wheel.defaults.roll_when.puts.always_when_itm}",
            )
            return True

        # 如果当前期权已是ITM 且 配置了itm时不允许roll
        # todo 为什么有两条完全相反的配置？
        if (
            not self.config.strategies.wheel.defaults.roll_when.puts.itm
            and isinstance(put.contract, Option)
            and itm
        ):
            return False

        # 如果当前有excess put 且 配置了有超卖put时禁止roll
        if (
            put.contract.symbol in self.has_excess_puts
            and not self.config.strategies.wheel.defaults.roll_when.puts.has_excess
        ):
            table.add_row(
                f"{put.contract.localSymbol}",
                "[cyan1]None",
                "[cyan1]Won't be rolled because there are excess puts",
            )
            return False

        dte = option_dte(put.contract.lastTradeDateOrContractMonth)
        pnl = position_pnl(put)
        roll_when_dte = self.config.strategies.wheel.defaults.roll_when.dte
        roll_when_pnl = self.config.strategies.wheel.defaults.roll_when.pnl
        roll_when_min_pnl = self.config.strategies.wheel.defaults.roll_when.min_pnl

        # 未接近到期日期，不需要提前roll
        if (
            self.config.strategies.wheel.defaults.roll_when.max_dte
            and dte > self.config.strategies.wheel.defaults.roll_when.max_dte
        ):
            return False

        # 临近到期且有收益，且收益已达到一个最小值，因时就可以roll了
        if dte <= roll_when_dte:
            if pnl >= roll_when_min_pnl:
                table.add_row(
                    f"{put.contract.localSymbol}",
                    "[blue]Roll",
                    f"[blue]Can be rolled because DTE of {dte} is <= {self.config.strategies.wheel.defaults.roll_when.dte} and P&L of {pfmt(pnl, 1)} is >= {pfmt(roll_when_min_pnl, 1)}",
                )
                return True
            table.add_row(
                f"{put.contract.localSymbol}",
                "[cyan1]None",
                f"[cyan1]Can't be rolled because P&L of {pfmt(pnl, 1)} is < {pfmt(roll_when_min_pnl, 1)}",
            )

        # 收益已达标，也可以roll
        if pnl >= roll_when_pnl:
            if self.config.strategies.wheel.defaults.roll_when.max_dte is not None:
                table.add_row(
                    f"{put.contract.localSymbol}",
                    "[blue]Roll",
                    f"[blue]Can be rolled because DTE of {dte} is <= {self.config.strategies.wheel.defaults.roll_when.max_dte} and P&L of {pfmt(pnl, 1)} is >= {pfmt(roll_when_pnl, 1)}",
                )
            else:
                table.add_row(
                    f"{put.contract.localSymbol}",
                    "[blue]Roll",
                    f"[blue]Can be rolled because P&L of {pfmt(pnl, 1)} is >= {pfmt(roll_when_pnl, 1)}",
                )
            return True

        return False

    # call期权是否可被滚动
    async def call_can_be_rolled(self, call: PortfolioItem, table: Table) -> bool:
        if call.position > 0:
            return False
        if not self.config.trading_is_allowed(call.contract.symbol):
            return False

        # 当前期权已是ITM 且 配置了ITM期权总进行roll
        if (
            isinstance(call.contract, Option)
            and await self.call_is_itm(call.contract)
            and self.config.strategies.wheel.defaults.roll_when.calls.always_when_itm
        ):
            table.add_row(
                f"{call.contract.localSymbol}",
                "[blue]Roll",
                f"[blue]Will be rolled because call is ITM and always_when_itm={self.config.strategies.wheel.defaults.roll_when.calls.always_when_itm}",
            )
            return True

        # 当前期权已是ITM 且 配置了ITM期权不允许roll
        if (
            not self.config.strategies.wheel.defaults.roll_when.calls.itm
            and isinstance(call.contract, Option)
            and await self.call_is_itm(call.contract)
        ):
            return False

        # 有超卖CALL 并且 配置的有超卖call的不允许roll
        if (
            call.contract.symbol in self.has_excess_calls
            and not self.config.strategies.wheel.defaults.roll_when.calls.has_excess
        ):
            table.add_row(
                f"{call.contract.localSymbol}",
                "[cyan1]None",
                f"[cyan1]Won't be rolled because there are excess calls for {call.contract.symbol}",
            )
            return False

        dte = option_dte(call.contract.lastTradeDateOrContractMonth)
        pnl = position_pnl(call)
        roll_when_dte = self.config.strategies.wheel.defaults.roll_when.dte
        roll_when_pnl = self.config.strategies.wheel.defaults.roll_when.pnl
        roll_when_min_pnl = self.config.strategies.wheel.defaults.roll_when.min_pnl

        if (
            self.config.strategies.wheel.defaults.roll_when.max_dte
            and dte > self.config.strategies.wheel.defaults.roll_when.max_dte
        ):
            return False
        if dte <= roll_when_dte:
            if pnl >= roll_when_min_pnl:
                table.add_row(
                    f"{call.contract.localSymbol}",
                    "[blue]Roll",
                    f"[blue]Can be rolled because DTE of {dte} is <= {self.config.strategies.wheel.defaults.roll_when.dte} and P&L of {pfmt(pnl, 1)} is >= {pfmt(roll_when_min_pnl, 1)}",
                )
                return True
            table.add_row(
                f"{call.contract.localSymbol}",
                "[cyan1]None",
                f"[cyan1]Can't be rolled because P&L of {pfmt(pnl, 1)} is < {pfmt(roll_when_min_pnl, 1)}",
            )

        if pnl >= roll_when_pnl:
            if self.config.strategies.wheel.defaults.roll_when.max_dte:
                table.add_row(
                    f"{call.contract.localSymbol}",
                    "[blue]Roll",
                    f"[blue]Can be rolled because DTE of {dte} is <= {self.config.strategies.wheel.defaults.roll_when.max_dte} and P&L of {pfmt(pnl, 1)} is >= {pfmt(roll_when_pnl, 1)}",
                )
            else:
                table.add_row(
                    f"{call.contract.localSymbol}",
                    "[blue]Roll",
                    f"[blue]Can be rolled because P&L of {pfmt(pnl, 1)} is >= {pfmt(roll_when_pnl, 1)}",
                )
            return True

        return False

    # 检查哪些pull可roll, 哪些put可close
    async def check_puts(
        self, portfolio_positions: Dict[str, List[PortfolioItem]]
    ) -> Tuple[List[Any], List[Any], Group]:
        # 当前已卖出的put
        puts = self.get_short_puts(portfolio_positions)
        puts = [put for put in puts if put.contract.symbol != "VIX"]
        rollable_puts: List[PortfolioItem] = []
        closeable_puts: List[PortfolioItem] = []

        table = Table(title="Rollable & closeable puts")
        table.add_column("Contract")
        table.add_column("Action")
        table.add_column("Detail")

        async def check_put_can_be_rolled_task(
            put: PortfolioItem, table: Table
        ) -> None:
            if await self.put_can_be_rolled(put, table):
                rollable_puts.append(put)
            elif self.put_can_be_closed(put, table):
                closeable_puts.append(put)

        tasks: List[Coroutine[Any, Any, None]] = [
            check_put_can_be_rolled_task(put, table) for put in puts
        ]
        await log.track_async(tasks, "Checking rollable/closeable puts...")

        total_rollable_puts = math.floor(sum([abs(p.position) for p in rollable_puts]))
        total_closeable_puts = math.floor(
            sum([abs(p.position) for p in closeable_puts])
        )
        text1 = f"[magenta]{total_rollable_puts} puts can be rolled"
        text2 = f"[magenta]{total_closeable_puts} puts can be closed"
        group = (
            Group(text1, text2, table)
            if total_closeable_puts + total_rollable_puts > 0
            else Group(text1, text2)
        )
        return (rollable_puts, closeable_puts, group)

    # 检查哪些call可roll，哪些call可close
    async def check_calls(
        self, portfolio_positions: Dict[str, List[PortfolioItem]]
    ) -> Tuple[List[Any], List[Any], Group]:
        calls = self.get_short_calls(portfolio_positions)
        calls = [call for call in calls if call.contract.symbol != "VIX"]
        rollable_calls: List[PortfolioItem] = []
        closeable_calls: List[PortfolioItem] = []

        table = Table(title="Rollable & closeable calls")
        table.add_column("Contract")
        table.add_column("Action")
        table.add_column("Detail")

        async def check_call_can_be_rolled_task(
            call: PortfolioItem, table: Table
        ) -> None:
            if await self.call_can_be_rolled(call, table):
                rollable_calls.append(call)
            elif self.call_can_be_closed(call, table):
                closeable_calls.append(call)

        tasks: List[Coroutine[Any, Any, None]] = [
            check_call_can_be_rolled_task(call, table) for call in calls
        ]
        await log.track_async(tasks, "Checking rollable/closeable calls...")

        total_rollable_calls = math.floor(
            sum([abs(p.position) for p in rollable_calls])
        )
        total_closeable_calls = math.floor(
            sum([abs(p.position) for p in closeable_calls])
        )
        text1 = f"[magenta]{total_rollable_calls} calls can be rolled"
        text2 = f"[magenta]{total_closeable_calls} calls can be closed"
        group = (
            Group(text1, text2, table)
            if total_closeable_calls + total_rollable_calls > 0
            else Group(text1, text2)
        )
        return (rollable_calls, closeable_calls, group)

    async def close_puts(self, puts: List[PortfolioItem]) -> None:
        await self.close_positions("P", puts)

    async def close_calls(self, calls: List[PortfolioItem]) -> None:
        await self.close_positions("C", calls)

    async def roll_puts(
        self,
        puts: List[PortfolioItem],
        account_summary: Dict[str, AccountValue],
    ) -> List[PortfolioItem]:
        return await self.roll_positions(puts, "P", account_summary)

    async def roll_calls(
        self,
        calls: List[PortfolioItem],
        account_summary: Dict[str, AccountValue],
        portfolio_positions: Dict[str, List[PortfolioItem]],
    ) -> List[PortfolioItem]:
        return await self.roll_positions(
            calls, "C", account_summary, portfolio_positions
        )

    async def close_positions(self, right: str, positions: List[PortfolioItem]) -> None:
        log.notice(f"Close {right} positions...")
        for position in positions:
            try:
                position.contract.exchange = self.order_ops.get_order_exchange()
                ticker = await self.ibkr.get_ticker_for_contract(
                    position.contract,
                    required_fields=[],
                    optional_fields=[TickerField.MIDPOINT, TickerField.MARKET_PRICE],
                )
                is_short = position.position < 0
                price = (
                    round(get_lower_price(ticker), 2)
                    if is_short
                    else round(get_higher_price(ticker), 2)
                )
                if not price or util.isNan(price) or math.isnan(price):
                    log.warning(
                        f"Market price data unavailable for {position.contract.localSymbol}, using ticker.minTick={ticker.minTick}"
                    )
                    price = ticker.minTick

                if position.contract.symbol == "VIX":
                    price = self.order_ops.round_vix_price(price)

                qty = abs(position.position)
                order = self.order_ops.create_limit_order(
                    action="BUY" if is_short else "SELL",
                    quantity=qty,
                    limit_price=price,
                    transmit=True,
                )
                self.order_ops.enqueue_order(ticker.contract, order)
            except RuntimeError:
                log.error(
                    "Error occurred when trying to close position. Continuing anyway..."
                )
                continue

    async def roll_positions(
        self,
        positions: List[PortfolioItem],
        right: str,
        account_summary: Dict[str, AccountValue],
        portfolio_positions: Optional[Dict[str, List[PortfolioItem]]] = None,
    ) -> List[PortfolioItem]:
        closeable_positions: List[PortfolioItem] = []
        log.notice(f"Rolling {right} positions...")

        for position in positions:
            try:
                symbol = position.contract.symbol
                position.contract.exchange = self.order_ops.get_order_exchange()
                buy_ticker = await self.ibkr.get_ticker_for_contract(
                    position.contract,
                    required_fields=[],
                    optional_fields=[TickerField.MIDPOINT, TickerField.MARKET_PRICE],
                )

                strike_limit = self.config.get_strike_limit(symbol, right)
                if right.startswith("C"):
                    average_cost = (
                        [
                            p.averageCost
                            for p in portfolio_positions[symbol]
                            if isinstance(p.contract, Stock)
                        ]
                        if portfolio_positions and symbol in portfolio_positions
                        else [0]
                    )
                    strike_limit = round(max([strike_limit or 0] + average_cost), 2)
                    if self.config.maintain_high_water_mark(symbol):
                        strike_limit = max([strike_limit, position.contract.strike])
                elif right.startswith("P"):
                    strike_limit = round(
                        min(
                            [strike_limit or sys.float_info.max]
                            + [
                                max(
                                    [
                                        position.contract.strike,
                                        position.contract.strike
                                        + (
                                            position.averageCost
                                            / float(position.contract.multiplier)
                                        )
                                        - midpoint_or_market_price(buy_ticker),
                                    ]
                                )
                            ]
                        ),
                        2,
                    )
                    if isinstance(position.contract, Option) and await self.put_is_itm(
                        position.contract
                    ):
                        strike_limit = min([strike_limit, position.contract.strike])

                kind = "calls" if right.startswith("C") else "puts"
                minimum_price = (
                    (lambda: self.config.runtime.orders.minimum_credit)
                    if not getattr(
                        self.config.strategies.wheel.defaults.roll_when, kind
                    ).credit_only
                    else (
                        lambda: midpoint_or_market_price(buy_ticker)
                        + self.config.runtime.orders.minimum_credit
                    )
                )

                def fallback_minimum_price() -> float:
                    return midpoint_or_market_price(buy_ticker)

                sell_ticker = await self.option_scanner.find_eligible_contracts(
                    Stock(
                        symbol,
                        self.order_ops.get_order_exchange(),
                        "USD",
                        primaryExchange=self.get_primary_exchange(symbol),
                    ),
                    right,
                    strike_limit,
                    exclude_expirations_before=position.contract.lastTradeDateOrContractMonth,
                    exclude_exp_strike=(
                        position.contract.strike,
                        position.contract.lastTradeDateOrContractMonth,
                    ),
                    minimum_price=minimum_price,
                    fallback_minimum_price=fallback_minimum_price,
                )
                if not sell_ticker.contract:
                    raise RuntimeError(f"Invalid ticker (no contract): {sell_ticker}")

                qty_to_roll = math.floor(abs(position.position))
                maximum_new_contracts = await self.get_maximum_new_contracts_for(
                    symbol,
                    self.get_primary_exchange(symbol),
                    account_summary,
                )
                from_dte = option_dte(position.contract.lastTradeDateOrContractMonth)
                roll_when_dte = self.config.strategies.wheel.defaults.roll_when.dte
                if from_dte > roll_when_dte:
                    qty_to_roll = min([qty_to_roll, maximum_new_contracts])

                price = midpoint_or_market_price(buy_ticker) - midpoint_or_market_price(
                    sell_ticker
                )
                price = (
                    min([price, -self.config.runtime.orders.minimum_credit])
                    if getattr(
                        self.config.strategies.wheel.defaults.roll_when, kind
                    ).credit_only
                    else price
                )

                if position.contract.symbol == "VIX":
                    price = self.order_ops.round_vix_price(price)

                self.qualified_contracts[position.contract.conId] = position.contract
                self.qualified_contracts[sell_ticker.contract.conId] = (
                    sell_ticker.contract
                )

                combo_legs = [
                    ComboLeg(
                        conId=position.contract.conId,
                        ratio=1,
                        exchange=self.order_ops.get_order_exchange(),
                        action="BUY",
                    ),
                    ComboLeg(
                        conId=sell_ticker.contract.conId,
                        ratio=1,
                        exchange=self.order_ops.get_order_exchange(),
                        action="SELL",
                    ),
                ]
                combo = Contract(
                    secType="BAG",
                    symbol=symbol,
                    currency="USD",
                    exchange=self.order_ops.get_order_exchange(),
                    comboLegs=combo_legs,
                )
                order = self.order_ops.create_limit_order(
                    action="BUY",
                    quantity=qty_to_roll,
                    limit_price=round(price, 2),
                    use_default_algo=False,
                    transmit=True,
                )

                to_dte = option_dte(sell_ticker.contract.lastTradeDateOrContractMonth)
                log.info(
                    f"{symbol}: Rolling from_strike={position.contract.strike} to_strike={sell_ticker.contract.strike} from_dte={from_dte} to_dte={to_dte} price={dfmt(price, 3)} qty_to_roll={qty_to_roll}"
                )
                self.order_ops.enqueue_order(combo, order)
            except NoValidContractsError:
                dte = option_dte(position.contract.lastTradeDateOrContractMonth)
                if (
                    self.config.close_if_unable_to_roll(position.contract.symbol)
                    and self.config.strategies.wheel.defaults.roll_when.max_dte
                    and dte <= self.config.strategies.wheel.defaults.roll_when.max_dte
                    and position_pnl(position) > 0
                ):
                    log.warning(
                        f"{position.contract.symbol}: Unable to find a suitable contract to roll to for {position.contract.localSymbol}. Closing position instead..."
                    )
                    closeable_positions.append(position)
                    continue
                log.error(
                    f"{position.contract.symbol}: Error occurred when trying to roll position. Continuing anyway..."
                )
            except RuntimeError:
                log.error(
                    f"{position.contract.symbol}: Error occurred when trying to roll position. Continuing anyway..."
                )
                continue

        return closeable_positions
