import math
from operator import itemgetter
from typing import Dict, List, Optional, Tuple

import ib_async.objects
import ib_async.ticker
from ib_async import AccountValue, Order, PortfolioItem, Ticker, util
from ib_async.contract import Option

from thetagang.config import Config
from thetagang.options import option_dte


def account_summary_to_dict(
    account_summary: List[AccountValue],
) -> Dict[str, AccountValue]:
    d: Dict[str, AccountValue] = dict()
    for s in account_summary:
        d[s.tag] = s
    return d


def portfolio_positions_to_dict(
    portfolio_positions: List[PortfolioItem],
) -> Dict[str, List[PortfolioItem]]:
    d: Dict[str, List[PortfolioItem]] = dict()
    for p in portfolio_positions:
        symbol = p.contract.symbol
        if symbol not in d:
            d[symbol] = []
        d[symbol].append(p)
    return d

# 仓位净收益
def position_pnl(position: ib_async.objects.PortfolioItem) -> float:
    denominator = position.averageCost * position.position
    if denominator == 0:
        return 0.0
    return position.unrealizedPNL / abs(denominator)

# 查询卖出期权的仓位
def get_short_positions(
    positions: List[PortfolioItem], right: str
) -> List[PortfolioItem]:
    return [
        p
        for p in positions
        if isinstance(p.contract, Option)
        and p.contract.right.upper().startswith(right.upper())
        and p.position < 0
    ]

# 查询买入期权的持仓
def get_long_positions(
    positions: List[PortfolioItem], right: str
) -> List[PortfolioItem]:
    return [
        p
        for p in positions
        if isinstance(p.contract, Option)
        and p.contract.right.upper().startswith(right.upper())
        and p.position > 0
    ]

# 计算卖出仓位
def count_short_option_positions(positions: List[PortfolioItem], right: str) -> int:
    return math.floor(-sum([p.position for p in get_short_positions(positions, right)]))


# 计算卖出平均持仓价
def weighted_avg_short_strike(
    positions: List[PortfolioItem], right: str
) -> Optional[float]:
    shorts = [
        (abs(p.position), p.contract.strike)
        for p in get_short_positions(positions, right)
    ]
    num = sum([p[0] * p[1] for p in shorts])
    den = sum([p[0] for p in shorts])
    if den > 0:
        return num / den

# 买入期权的平均持仓价
def weighted_avg_long_strike(
    positions: List[PortfolioItem], right: str
) -> Optional[float]:
    shorts = [
        (abs(p.position), p.contract.strike)
        for p in get_long_positions(positions, right)
    ]
    num = sum([p[0] * p[1] for p in shorts])
    den = sum([p[0] for p in shorts])
    if den > 0:
        return num / den

# 计算买入期权的持仓
def count_long_option_positions(positions: List[PortfolioItem], right: str) -> int:
    return math.floor(sum([p.position for p in get_long_positions(positions, right)]))

# 计算净卖出仓位
def calculate_net_short_positions(positions: List[PortfolioItem], right: str) -> int:
    shorts: List[Tuple[int, float, float]] = [
        (
            option_dte(p.contract.lastTradeDateOrContractMonth),
            float(p.contract.strike),
            float(p.position),
        )
        for p in get_short_positions(positions, right)
    ]
    longs: List[Tuple[int, float, float]] = [
        (
            option_dte(p.contract.lastTradeDateOrContractMonth),
            float(p.contract.strike),
            float(p.position),
        )
        for p in get_long_positions(positions, right)
    ]
    shorts = sorted(shorts, key=itemgetter(0, 1), reverse=right.upper().startswith("P"))
    longs = sorted(longs, key=itemgetter(0, 1), reverse=right.upper().startswith("P"))

    def calc_net(short_dte: int, short_strike: float, short_position: float) -> float:
        for i in range(len(longs)):
            if short_position > -1:
                break
            (long_dte, long_strike, long_position) = longs[i]
            if long_position < 1:
                # ignore empty long positions
                continue
            if long_dte >= short_dte:
                if (
                    math.isclose(short_strike, long_strike)
                    or (right.upper().startswith("P") and long_strike >= short_strike)
                    or (right.upper().startswith("C") and long_strike <= short_strike)
                ):
                    if short_position + long_position > 0:
                        long_position = short_position + long_position
                        short_position = 0
                    else:
                        short_position += long_position
                        long_position = 0
            longs[i] = (long_dte, long_strike, long_position)
        return min([0.0, short_position])

    nets = [calc_net(*short) for short in shorts]

    return math.floor(-sum(nets))


def net_option_positions(
    symbol: str,
    portfolio_positions: Dict[str, List[PortfolioItem]],
    right: str,
    ignore_dte: Optional[int] = None,
) -> int:
    if symbol in portfolio_positions:
        return math.floor(
            sum(
                [
                    p.position
                    for p in portfolio_positions[symbol]
                    if isinstance(p.contract, Option)
                    and p.contract.right.upper().startswith(right.upper())
                    and option_dte(p.contract.lastTradeDateOrContractMonth) >= 0
                    and (
                        not ignore_dte
                        or option_dte(p.contract.lastTradeDateOrContractMonth)
                        > ignore_dte
                    )
                ]
            )
        )

    return 0


def get_higher_price(ticker: Ticker) -> float:
    # Returns the highest of either the option model price, the midpoint, or the
    # market price. The midpoint is usually a bit higher than the IB model's
    # pricing, but we want to avoid leaving money on the table in cases where
    # the spread might be messed up. This may in some cases make it harder for
    # orders to fill in a given day, but I think that's a reasonable tradeoff to
    # avoid leaving money on the table.
    if ticker.modelGreeks and ticker.modelGreeks.optPrice:
        return max([midpoint_or_market_price(ticker), ticker.modelGreeks.optPrice])
    return midpoint_or_market_price(ticker)


def get_lower_price(ticker: Ticker) -> float:
    # Same as get_highest_price(), except get the lower price instead.
    if ticker.modelGreeks and ticker.modelGreeks.optPrice:
        return min([midpoint_or_market_price(ticker), ticker.modelGreeks.optPrice])
    return midpoint_or_market_price(ticker)


def midpoint_or_market_price(ticker: Ticker) -> float:
    # As per the ib_async docs, marketPrice returns the last price first, but
    # we often prefer the midpoint over the last price. This function pulls the
    # midpoint first, then falls back to marketPrice() if midpoint is nan.
    if util.isNan(ticker.midpoint()):
        if (
            util.isNan(ticker.marketPrice())
            and ticker.modelGreeks
            and ticker.modelGreeks.optPrice
        ):
            # Fallback to the model price if the greeks are available
            return ticker.modelGreeks.optPrice
        else:
            return ticker.marketPrice() if not util.isNan(ticker.marketPrice()) else 0.0

    return ticker.midpoint()

# 计算可以卖出的 Call 合约数量
def get_target_calls(
    config: Config, symbol: str, current_shares: int, target_shares: int
) -> int:
    # 只卖出多余的股票对应的 Call
    # 举例:
    # 当前股数	目标股数	可卖 call
    # 300	   100	     2 张
    # 200	   100	     1 张
    # 100	   100	     0 张
    if config.write_excess_calls_only(symbol):
        return max([0, (current_shares - target_shares) // 100])
    else:
        # 例子 假设：

        # 当前持有: 500 股
        # 目标持有: 300 股
        # cap_factor: 0.9 (90%)
        # cap_target_floor: 0.8 (80%)

        # min_uncovered = 300 × 0.8 ÷ 100 = 2 张 (240股)
        # max_covered   = 500 × 0.9 ÷ 100 = 4 张 (400股)
        # total_coverable = 500 ÷ 100 = 5 张

        # 可卖 call = min(4, 5 - 2) = min(4, 3) = 3 张
        cap_factor = config.get_cap_factor(symbol)                  # 覆盖率上限
        cap_target_floor = config.get_cap_target_floor(symbol)      # 目标底限
        min_uncovered = (target_shares * cap_target_floor) // 100   # 最小不覆盖
        max_covered = (current_shares * cap_factor) // 100          # 最大可覆盖
        total_coverable = current_shares // 100                     # 总可覆盖
        return max([0, math.floor(min([max_covered, total_coverable - min_uncovered]))])
    # 两种模式对比
    # 模式	                    特点	       风险
    # write_excess_calls_only  只卖多余股票	    低风险，不会卖飞
    # 普通模式	                 可卖部分持仓	  可能会被行权

    # 普通模式允许保留一部分股票作为"安全垫"，即使被行权也不会卖光。


def would_increase_spread(order: Order, updated_price: float) -> bool:
    return (
        order.action == "BUY"
        and order.lmtPrice is not None
        and updated_price < order.lmtPrice
        or order.action == "SELL"
        and order.lmtPrice is not None
        and updated_price > order.lmtPrice
    )
