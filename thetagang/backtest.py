"""
ThetaGang Backtesting Engine

A backtesting framework for the Wheel options strategy using historical data.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Protocol

import statistics

# ─── Data Types ────────────────────────────────────────────────────────────────


@dataclass
class OHLCVBar:
    """Daily OHLCV bar for an underlying symbol."""
    symbol: str
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: int


@dataclass
class OptionQuote:
    """Option price quote with greeks."""
    symbol: str
    strike: float
    right: str  # "CALL" or "PUT"
    expiry: date
    bid: float
    ask: float
    delta: float
    gamma: float
    theta: float
    vega: float
    open_interest: int
    volume: int

    @property
    def midpoint(self) -> float:
        return (self.bid + self.ask) / 2 if self.bid and self.ask else 0.0


@dataclass
class BacktestPosition:
    """Represents a position in the backtest portfolio."""
    symbol: str
    quantity: int
    avg_cost: float
    sec_type: str = "STK"

    # For options
    strike: Optional[float] = None
    right: Optional[str] = None
    expiry: Optional[date] = None
    con_id: Optional[int] = None

    # For tracking
    unrealized_pnl: float = 0.0
    realized_pnl: float = 0.0


@dataclass
class BacktestTrade:
    """Records a trade execution in the backtest."""
    timestamp: datetime
    symbol: str
    action: str  # "BUY" or "SELL"
    quantity: int
    price: float
    sec_type: str
    strike: Optional[float] = None
    right: Optional[str] = None
    expiry: Optional[date] = None
    credit: float = 0.0
    notes: str = ""


@dataclass
class BacktestStats:
    """Statistics from a backtest run."""
    total_return: float = 0.0
    total_return_pct: float = 0.0
    sharpe_ratio: float = 0.0
    max_drawdown: float = 0.0
    max_drawdown_pct: float = 0.0
    win_rate: float = 0.0
    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    avg_hold_time_days: float = 0.0
    total_premium_collected: float = 0.0

    # Per-symbol breakdown
    symbol_stats: Dict[str, Dict[str, Any]] = field(default_factory=dict)


# ─── Data Provider Protocol ──────────────────────────────────────────────────


class BacktestDataProvider(Protocol):
    """Protocol for historical data source. Implement this to provide your data."""

    def get_ohlcv(self, symbol: str, start: date, end: date) -> List[OHLCVBar]:
        """Return daily OHLCV bars for symbol in date range."""
        ...

    def get_option_chain(
        self, symbol: str, as_of: date
    ) -> List[OptionQuote]:
        """Return available option contracts for symbol on given date."""
        ...

    def get_spot_price(self, symbol: str, as_of: date) -> float:
        """Return the spot/fair price of underlying on given date."""
        ...


# ─── CSV Data Provider ───────────────────────────────────────────────────────


@dataclass
class CSVBar:
    """Internal bar format from CSV."""
    timestamp: str
    open: float
    high: float
    low: float
    close: float
    volume: int


class CSVBacktestDataProvider:
    """Loads historical data from CSV files.

    Expected CSV format for price bars (one file per symbol):
        timestamp,open,high,low,close,volume
        2024-01-02,100.0,101.5,99.5,101.0,1000000

    Expected CSV format for option quotes (one file per symbol per date):
        strike,right,expiry,bid,ask,delta,gamma,theta,vega,open_interest,volume
        100.0,CALL,2024-01-19,2.50,2.55,0.50,0.03,0.10,0.15,500,100

    Directory structure:
        data/
            SPY_bars.csv
            AAPL_bars.csv
            SPY_2024-01-02_options.csv
            SPY_2024-01-03_options.csv
            ...
    """

    def __init__(self, data_dir: str | Path):
        self.data_dir = Path(data_dir)
        self._bar_cache: Dict[str, List[OHLCVBar]] = {}
        self._chain_cache: Dict[tuple, List[OptionQuote]] = {}

    def get_ohlcv(self, symbol: str, start: date, end: date) -> List[OHLCVBar]:
        if symbol in self._bar_cache:
            bars = self._bar_cache[symbol]
        else:
            bars = self._load_bars(symbol)
            self._bar_cache[symbol] = bars

        return [
            b for b in bars
            if start <= b.timestamp.date() <= end
        ]

    def _load_bars(self, symbol: str) -> List[OHLCVBar]:
        path = self.data_dir / f"{symbol}_bars.csv"
        if not path.exists():
            raise FileNotFoundError(f"Missing bars file: {path}")

        bars = []
        with open(path) as f:
            # Skip header
            next(f)
            for line in f:
                parts = line.strip().split(",")
                if len(parts) < 6:
                    continue
                bars.append(OHLCVBar(
                    symbol=symbol,
                    timestamp=datetime.fromisoformat(parts[0]),
                    open=float(parts[1]),
                    high=float(parts[2]),
                    low=float(parts[3]),
                    close=float(parts[4]),
                    volume=int(parts[5]),
                ))
        return sorted(bars, key=lambda x: x.timestamp)

    def get_option_chain(self, symbol: str, as_of: date) -> List[OptionQuote]:
        cache_key = (symbol, as_of.isoformat())
        if cache_key in self._chain_cache:
            return self._chain_cache[cache_key]

        # Try to load date-specific option file
        path = self.data_dir / f"{symbol}_{as_of.isoformat()}_options.csv"
        if not path.exists():
            return []

        quotes = []
        with open(path) as f:
            next(f)  # Skip header
            for line in f:
                parts = line.strip().split(",")
                if len(parts) < 11:
                    continue
                quotes.append(OptionQuote(
                    symbol=symbol,
                    strike=float(parts[0]),
                    right=parts[1],
                    expiry=date.fromisoformat(parts[2]),
                    bid=float(parts[3]),
                    ask=float(parts[4]),
                    delta=float(parts[5]),
                    gamma=float(parts[6]),
                    theta=float(parts[7]),
                    vega=float(parts[8]),
                    open_interest=int(parts[9]),
                    volume=int(parts[10]),
                ))
        self._chain_cache[cache_key] = quotes
        return quotes

    def get_spot_price(self, symbol: str, as_of: date) -> float:
        bars = self.get_ohlcv(symbol, as_of, as_of)
        if not bars:
            raise ValueError(f"No price data for {symbol} on {as_of}")
        return bars[0].close


# ─── Backtest Portfolio ──────────────────────────────────────────────────────


class BacktestPortfolio:
    """Tracks portfolio state during backtesting."""

    def __init__(
        self,
        initial_cash: float,
        buying_power_mult: int = 1,
    ):
        self.initial_cash = initial_cash
        self.cash = initial_cash
        self.buying_power_mult = buying_power_mult
        self.positions: Dict[str, BacktestPosition] = {}  # key = position_key
        self.cash_history: List[tuple[datetime, float]] = []
        self.equity_history: List[tuple[datetime, float]] = []
        self.trade_log: List[BacktestTrade] = []

    @property
    def total_value(self) -> float:
        """Total portfolio value (cash + positions)."""
        return self.cash + sum(
            self._position_value(p) for p in self.positions.values()
        )

    @property
    def total_equity_value(self) -> float:
        """Total value of equity (non-cash) positions."""
        return sum(
            self._position_value(p) for p in self.positions.values()
            if p.sec_type == "STK"
        )

    @property
    def net_liquidation(self) -> float:
        return self.total_value

    def buying_power(self) -> float:
        return self.cash * self.buying_power_mult

    def _position_key(self, symbol: str, strike: Optional[float] = None,
                       right: Optional[str] = None, expiry: Optional[date] = None) -> str:
        if strike:
            return f"{symbol}_{strike}_{right}_{expiry}"
        return symbol

    def _position_value(self, pos: BacktestPosition) -> float:
        return pos.quantity * pos.avg_cost

    def has_short_put_position(self, symbol: str) -> bool:
        for pos in self.positions.values():
            if (pos.symbol == symbol and pos.sec_type == "OPT"
                    and pos.right == "PUT" and pos.quantity < 0):
                return True
        return False

    def short_put_count(self, symbol: str) -> int:
        count = 0
        for pos in self.positions.values():
            if (pos.symbol == symbol and pos.sec_type == "OPT"
                    and pos.right == "PUT" and pos.quantity < 0):
                count += abs(pos.quantity)
        return count

    def has_short_call_position(self, symbol: str) -> bool:
        for pos in self.positions.values():
            if (pos.symbol == symbol and pos.sec_type == "OPT"
                    and pos.right == "CALL" and pos.quantity < 0):
                return True
        return False

    def short_call_count(self, symbol: str) -> int:
        count = 0
        for pos in self.positions.values():
            if (pos.symbol == symbol and pos.sec_type == "OPT"
                    and pos.right == "CALL" and pos.quantity < 0):
                count += abs(pos.quantity)
        return count

    def get_stock_position(self, symbol: str) -> Optional[BacktestPosition]:
        key = self._position_key(symbol)
        return self.positions.get(key)

    def add_position(
        self,
        symbol: str,
        quantity: int,
        price: float,
        sec_type: str = "STK",
        strike: Optional[float] = None,
        right: Optional[str] = None,
        expiry: Optional[date] = None,
        credit: float = 0.0,
        timestamp: Optional[datetime] = None,
    ) -> BacktestTrade:
        """Add or adjust a position. Returns the trade record."""
        timestamp = timestamp or datetime.now()
        key = self._position_key(symbol, strike, right, expiry)

        # Determine action for trade log
        action = "BUY" if quantity > 0 else "SELL"

        trade = BacktestTrade(
            timestamp=timestamp,
            symbol=symbol,
            action=action,
            quantity=abs(quantity),
            price=price,
            sec_type=sec_type,
            strike=strike,
            right=right,
            expiry=expiry,
            credit=credit,
        )
        self.trade_log.append(trade)

        if key in self.positions:
            pos = self.positions[key]
            old_qty = pos.quantity
            # FIFO cost basis for stock positions
            if sec_type == "STK":
                pos.avg_cost = (
                    (pos.avg_cost * pos.quantity + price * quantity)
                    / (pos.quantity + quantity)
                )
            pos.quantity += quantity
            if pos.quantity == 0:
                del self.positions[key]
            else:
                self.positions[key] = pos
        else:
            self.positions[key] = BacktestPosition(
                symbol=symbol,
                quantity=quantity,
                avg_cost=price,
                sec_type=sec_type,
                strike=strike,
                right=right,
                expiry=expiry,
            )

        # Update cash
        if sec_type == "STK":
            self.cash -= price * quantity
        elif sec_type == "OPT":
            # Credit received for writing options
            self.cash += credit

        return trade

    def record_snapshot(self, timestamp: datetime):
        self.cash_history.append((timestamp, self.cash))
        self.equity_history.append((timestamp, self.total_value))

    def compute_stats(self) -> BacktestStats:
        """Compute performance statistics from trade log."""
        stats = BacktestStats()

        if not self.equity_history:
            return stats

        # Total return
        final_value = self.equity_history[-1][1] if self.equity_history else self.initial_cash
        stats.total_return = final_value - self.initial_cash
        stats.total_return_pct = (stats.total_return / self.initial_cash) * 100

        # Equity curve for drawdown
        equity_values = [eq for _, eq in self.equity_history]
        peak = self.initial_cash
        for val in equity_values:
            if val > peak:
                peak = val
            drawdown = peak - val
            if drawdown > stats.max_drawdown:
                stats.max_drawdown = drawdown
                stats.max_drawdown_pct = (drawdown / peak) * 100 if peak > 0 else 0

        # Daily returns for Sharpe
        if len(self.equity_history) > 1:
            daily_returns = []
            for i in range(1, len(self.equity_history)):
                prev_val = self.equity_history[i - 1][1]
                curr_val = self.equity_history[i][1]
                if prev_val > 0:
                    daily_returns.append((curr_val - prev_val) / prev_val)

            if daily_returns:
                avg_ret = statistics.mean(daily_returns)
                std_ret = statistics.stdev(daily_returns) if len(daily_returns) > 1 else 0
                stats.sharpe_ratio = (avg_ret / std_ret * (252 ** 0.5)) if std_ret > 0 else 0

        # Trade statistics
        option_trades = [t for t in self.trade_log if t.sec_type == "OPT"]
        stats.total_trades = len(option_trades)

        winning = [t for t in option_trades if t.credit > 0]
        losing = [t for t in option_trades if t.credit <= 0]
        stats.winning_trades = len(winning)
        stats.losing_trades = len(losing)
        stats.win_rate = (stats.winning_trades / stats.total_trades * 100
                          if stats.total_trades > 0 else 0)
        stats.total_premium_collected = sum(t.credit for t in winning)

        return stats


# ─── Wheel Strategy Backtest Engine ─────────────────────────────────────────


@dataclass
class WheelBacktestConfig:
    """Configuration for wheel strategy backtest."""
    # Target DTE range for writing options
    target_dte: int = 30
    max_dte: int = 60
    min_dte: int = 14

    # Delta target for option selection
    target_delta: float = 0.3
    max_delta: float = 0.5

    # Strike selection
    strike_limit_pct: float = 0.05  # 5% OTM

    # Minimum requirements
    minimum_credit: float = 0.10
    minimum_open_interest: int = 10
    minimum_volume: int = 0

    # Position sizing
    max_new_contracts_pct: float = 1.0  # multiplier on strike*100; 1.0 = each contract costs ~50% of BP (realistic CSP margin)
    contract_size: int = 100  # shares per contract

    # Roll/close thresholds
    roll_dte: int = 7
    roll_min_pnl_pct: float = 0.50  # Roll when P&L >= 50% of credit
    close_at_pnl_pct: float = 0.95  # Close when P&L >= 95% of credit (almost full profit)

    # Write threshold (volatility filter)
    write_threshold: float = 0.03  # Only write when |daily_change| >= 3%


class WheelBacktestEngine:
    """Backtesting engine for the Wheel strategy.

    Simulates the Wheel options strategy:
    1. Sell puts to generate income (CSP - Cash Secured Puts)
    2. If assigned, hold shares and sell covered calls
    3. Roll positions when parameters are met
    4. Close positions for profit when threshold reached
    """

    def __init__(
        self,
        data_provider: BacktestDataProvider,
        portfolio: BacktestPortfolio,
        config: WheelBacktestConfig | None = None,
        symbols: List[str] | None = None,
        weights: Dict[str, float] | None = None,
    ):
        self.data = data_provider
        self.portfolio = portfolio
        self.config = config or WheelBacktestConfig()
        self.symbols = symbols or []
        self.weights = weights or {}

        self._current_date: Optional[date] = None
        self._spy_bars: Dict[date, OHLCVBar] = {}  # For VIX proxy / regime
        self._log: List[str] = []

    def run(self, start_date: date, end_date: date) -> BacktestPortfolio:
        """Run the backtest from start_date to end_date (inclusive)."""
        self._log.append(f"Starting backtest: {start_date} -> {end_date}")
        self._log.append(f"Initial cash: ${self.portfolio.initial_cash:,.2f}")

        current = start_date
        while current <= end_date:
            self._current_date = current
            self._run_daily_logic(current)
            current += timedelta(days=1)

        self._log.append(f"Backtest complete. Final value: ${self.portfolio.total_value:,.2f}")
        return self.portfolio

    def _run_daily_logic(self, current: date):
        """Execute daily wheel strategy logic."""
        self._log.append(f"\n=== {current} ===")

        # Step 1: Check for expiring options to close
        self._check_expirations(current)

        # Step 2: Check existing short positions for roll/close signals
        self._check_roll_close(current)

        # Step 3: Sell new puts if we have cash power
        self._write_new_puts(current)

        # Step 4: If we hold stock, consider selling covered calls
        self._write_covered_calls(current)

        # Step 5: Record daily portfolio snapshot
        self.portfolio.record_snapshot(datetime.combine(current, datetime.min.time()))

    def _check_expirations(self, current: date):
        """Close any options expiring today (ITM options get assigned)."""
        for key, pos in list(self.portfolio.positions.items()):
            if pos.sec_type != "OPT" or pos.expiry != current:
                continue

            # Get current option quote
            chain = self.data.get_option_chain(pos.symbol, current)
            quote = self._find_quote(chain, pos.strike, pos.right, pos.expiry)
            if not quote:
                continue

            # For puts: if ITM (spot > strike), we get assigned (buy shares at strike)
            # For calls: if ITM (spot < strike), we get called away (sell shares at strike)
            spot = self.data.get_spot_price(pos.symbol, current)

            if pos.right == "PUT" and spot < pos.strike:
                # Assigned - need to buy shares
                cost = pos.strike * abs(pos.quantity) * self.config.contract_size
                if self.portfolio.cash >= cost:
                    # Convert to stock position
                    self.portfolio.add_position(
                        symbol=pos.symbol,
                        quantity=pos.quantity * self.config.contract_size,
                        price=pos.strike,
                        sec_type="STK",
                        timestamp=datetime.combine(current, datetime.min.time()),
                    )
                    self._log.append(
                        f"PUT assigned: {pos.symbol} @ ${pos.strike} "
                        f"({abs(pos.quantity)} contracts)"
                    )
                    del self.portfolio.positions[key]

            elif pos.right == "CALL" and spot > pos.strike:
                # Called away - shares sold at strike
                proceeds = pos.strike * abs(pos.quantity) * self.config.contract_size
                self.portfolio.add_position(
                    symbol=pos.symbol,
                    quantity=pos.quantity * self.config.contract_size,
                    price=pos.strike,
                    sec_type="STK",
                    timestamp=datetime.combine(current, datetime.min.time()),
                )
                self._log.append(
                    f"CALL assigned: {pos.symbol} @ ${pos.strike} "
                    f"({abs(pos.quantity)} contracts)"
                )
                del self.portfolio.positions[key]

            else:
                # OTM - option expires worthless, keep premium
                self._log.append(
                    f"Option expired OTM: {pos.symbol} {pos.strike} {pos.right} "
                    f"({abs(pos.quantity)} contracts)"
                )
                del self.portfolio.positions[key]

    def _check_roll_close(self, current: date):
        """Check existing short options for roll/close signals."""
        for key, pos in list(self.portfolio.positions.items()):
            if pos.sec_type != "OPT":
                continue

            chain = self.data.get_option_chain(pos.symbol, current)
            quote = self._find_quote(chain, pos.strike, pos.right, pos.expiry)
            if not quote:
                continue

            dte = (pos.expiry - current).days
            spot = self.data.get_spot_price(pos.symbol, current)

            # Calculate current P&L
            if pos.right == "PUT":
                # Short put: profit if price moved up (spot > entry when opened)
                pnl_pct = (spot - pos.avg_cost) / pos.avg_cost if pos.avg_cost > 0 else 0
            else:
                # Short call: profit if price moved down (spot < entry)
                pnl_pct = (pos.avg_cost - spot) / pos.avg_cost if pos.avg_cost > 0 else 0

            # Close at profit threshold
            if pnl_pct >= self.config.close_at_pnl_pct:
                # Buy to close
                self._close_option(pos, quote, current)
                self._log.append(
                    f"Closed for profit: {pos.symbol} {pos.strike} {pos.right} "
                    f"P&L: {pnl_pct:.1%}"
                )

            # Roll when DTE low and still profitable
            elif dte <= self.config.roll_dte and pnl_pct >= self.config.roll_min_pnl_pct:
                # Roll to next expiration
                self._roll_option(pos, chain, current, spot)
                self._log.append(
                    f"Rolled: {pos.symbol} {pos.strike} -> new expiry "
                    f"(DTE: {dte}, P&L: {pnl_pct:.1%})"
                )

    def _close_option(
        self,
        pos: BacktestPosition,
        quote: OptionQuote,
        current: date,
    ):
        """Close an option position by buying it back."""
        cost = quote.midpoint * abs(pos.quantity) * self.config.contract_size
        if self.portfolio.cash >= cost:
            self.portfolio.add_position(
                symbol=pos.symbol,
                quantity=-pos.quantity,  # Opposite to close
                price=quote.midpoint,
                sec_type="OPT",
                strike=pos.strike,
                right=pos.right,
                expiry=pos.expiry,
                credit=-cost,  # Cost to close
                timestamp=datetime.combine(current, datetime.min.time()),
            )

    def _roll_option(
        self,
        pos: BacktestPosition,
        chain: List[OptionQuote],
        current: date,
        spot: float,
    ):
        """Roll an option to the next expiration with similar delta."""
        # Close current
        current_chain = self.data.get_option_chain(pos.symbol, current)
        current_quote = self._find_quote(current_chain, pos.strike, pos.right, pos.expiry)
        if current_quote:
            self._close_option(pos, current_quote, current)

        # Find next expiration
        expiry_candidates = sorted(set(q.expiry for q in chain if q.expiry > current))
        if not expiry_candidates:
            return

        next_expiry = expiry_candidates[0]

        # Find similar delta contract at new expiry
        target_delta = self.config.target_delta
        new_chain = [q for q in chain if q.expiry == next_expiry and q.right == pos.right]
        new_chain.sort(key=lambda q: abs(q.delta - target_delta))

        if new_chain:
            new_quote = new_chain[0]
            credit = new_quote.midpoint * self.config.contract_size
            if self.portfolio.cash >= credit:
                self.portfolio.add_position(
                    symbol=pos.symbol,
                    quantity=pos.quantity,
                    price=new_quote.midpoint,
                    sec_type="OPT",
                    strike=new_quote.strike,
                    right=new_quote.right,
                    expiry=new_quote.expiry,
                    credit=credit,
                    timestamp=datetime.combine(current, datetime.min.time()),
                )

    def _write_new_puts(self, current: date):
        """Sell cash-secured puts on symbols without existing short puts."""
        for symbol in self.symbols:
            weight = self.weights.get(symbol, 1.0 / len(self.symbols))

            # Check if we already have a short put position
            if self.portfolio.has_short_put_position(symbol):
                continue

            # Check if we already have 100 shares (would be covered put situation)
            stock_pos = self.portfolio.get_stock_position(symbol)
            if stock_pos and stock_pos.quantity >= self.config.contract_size:
                continue

            # Get option chain
            chain = self.data.get_option_chain(symbol, current)
            if not chain:
                continue

            # Filter eligible puts
            spot = self.data.get_spot_price(symbol, current)
            eligible = self._find_eligible_puts(chain, spot, current)

            if not eligible:
                continue

            # Select best eligible put (closest to target delta)
            best = eligible[0]

            # Position sizing: target by weight, capped by margin requirement
            target_qty = int(
                weight * self.portfolio.buying_power() / spot
            ) // self.config.contract_size

            # Margin requirement for CSP = strike * 100 (notional)
            max_contracts = int(
                self.portfolio.buying_power() * self.config.max_new_contracts_pct
                / (best.strike * self.config.contract_size)
            )

            contracts = min(target_qty, max_contracts, 10)  # Cap at 10 contracts
            if contracts <= 0:
                continue

            credit = best.midpoint * contracts * self.config.contract_size

            if credit < self.config.minimum_credit:
                continue

            # Write the put
            self.portfolio.add_position(
                symbol=symbol,
                quantity=-contracts,
                price=best.midpoint,
                sec_type="OPT",
                strike=best.strike,
                right="PUT",
                expiry=best.expiry,
                credit=credit,
                timestamp=datetime.combine(current, datetime.min.time()),
            )
            self._log.append(
                f"Wrote PUT: {symbol} ${best.strike} exp:{best.expiry} "
                f"{contracts} contracts @ ${best.midpoint:.2f} "
                f"(credit: ${credit:.2f}, delta: {best.delta:.3f})"
            )

    def _write_covered_calls(self, current: date):
        """Sell covered calls on held shares."""
        for symbol in self.symbols:
            stock_pos = self.portfolio.get_stock_position(symbol)
            if not stock_pos or stock_pos.quantity < self.config.contract_size:
                continue

            # Check if we already have a short call
            if self.portfolio.has_short_call_position(symbol):
                continue

            chain = self.data.get_option_chain(symbol, current)
            if not chain:
                continue

            spot = self.data.get_spot_price(symbol, current)
            eligible = self._find_eligible_calls(chain, spot, current)

            if not eligible:
                continue

            # Calculate how many calls we can write (1 contract per 100 shares)
            max_contracts = stock_pos.quantity // self.config.contract_size

            best = eligible[0]
            credit = best.midpoint * max_contracts * self.config.contract_size

            if credit < self.config.minimum_credit:
                continue

            self.portfolio.add_position(
                symbol=symbol,
                quantity=-max_contracts,
                price=best.midpoint,
                sec_type="OPT",
                strike=best.strike,
                right="CALL",
                expiry=best.expiry,
                credit=credit,
                timestamp=datetime.combine(current, datetime.min.time()),
            )
            self._log.append(
                f"Wrote CALL: {symbol} ${best.strike} exp:{best.expiry} "
                f"{max_contracts} contracts @ ${best.midpoint:.2f} "
                f"(credit: ${credit:.2f}, delta: {best.delta:.3f})"
            )

    def _find_eligible_puts(
        self,
        chain: List[OptionQuote],
        spot: float,
        current: date,
    ) -> List[OptionQuote]:
        """Find eligible put contracts to write."""
        eligible = []
        for q in chain:
            if q.right != "PUT":
                continue
            if q.expiry <= current:
                continue

            dte = (q.expiry - current).days
            if dte < self.config.min_dte or dte > self.config.max_dte:
                continue
            if abs(q.delta) > self.config.max_delta:
                continue
            if q.delta > -0.01:  # Skip puts that are too deep OTM
                continue
            if q.open_interest < self.config.minimum_open_interest:
                continue
            if q.midpoint < self.config.minimum_credit:
                continue

            # Within strike limit
            if q.strike > spot * (1 + self.config.strike_limit_pct):
                continue
            if q.strike < spot * (1 - self.config.strike_limit_pct):
                continue

            eligible.append(q)

        # Sort by delta (closest to target first)
        eligible.sort(key=lambda q: abs(q.delta - self.config.target_delta))
        return eligible

    def _find_eligible_calls(
        self,
        chain: List[OptionQuote],
        spot: float,
        current: date,
    ) -> List[OptionQuote]:
        """Find eligible call contracts to write (covered calls)."""
        eligible = []
        for q in chain:
            if q.right != "CALL":
                continue
            if q.expiry <= current:
                continue

            dte = (q.expiry - current).days
            if dte < self.config.min_dte or dte > self.config.max_dte:
                continue
            if abs(q.delta) > self.config.max_delta:
                continue
            if q.delta < 0.01:  # Skip calls too deep OTM
                continue
            if q.open_interest < self.config.minimum_open_interest:
                continue
            if q.midpoint < self.config.minimum_credit:
                continue

            # Strike above current spot
            if q.strike < spot * (1 + self.config.strike_limit_pct):
                continue
            if q.strike > spot * (1 + 2 * self.config.strike_limit_pct):
                continue

            eligible.append(q)

        eligible.sort(key=lambda q: abs(q.delta - self.config.target_delta))
        return eligible

    def _find_quote(
        self,
        chain: List[OptionQuote],
        strike: float,
        right: str,
        expiry: date,
    ) -> OptionQuote | None:
        for q in chain:
            if q.strike == strike and q.right == right and q.expiry == expiry:
                return q
        return None

    def get_log(self) -> List[str]:
        return self._log

    def print_summary(self):
        stats = self.portfolio.compute_stats()
        print("\n" + "=" * 60)
        print("BACKTEST SUMMARY")
        print("=" * 60)
        print(f"Initial Cash:     ${self.portfolio.initial_cash:>12,.2f}")
        print(f"Final Value:      ${self.portfolio.total_value:>12,.2f}")
        print(f"Total Return:     ${stats.total_return:>12,.2f} ({stats.total_return_pct:.2f}%)")
        print(f"Max Drawdown:     ${stats.max_drawdown:>12,.2f} ({stats.max_drawdown_pct:.2f}%)")
        print(f"Sharpe Ratio:     {stats.sharpe_ratio:>12.2f}")
        print(f"Win Rate:         {stats.win_rate:>12.1f}%")
        print(f"Total Trades:     {stats.total_trades:>12}")
        print(f"Premium Collected:${stats.total_premium_collected:>12,.2f}")
        print("=" * 60)


# ─── CLI Entry Point ─────────────────────────────────────────────────────────


def run_backtest_from_csv(
    data_dir: str,
    symbols: List[str],
    weights: Dict[str, float] | None = None,
    start_date: str = "2024-01-01",
    end_date: str = "2024-12-31",
    initial_cash: float = 100_000.0,
    config: WheelBacktestConfig | None = None,
) -> BacktestPortfolio:
    """Convenience function to run backtest with CSV data provider."""
    from datetime import date as date_type

    data = CSVBacktestDataProvider(data_dir)
    portfolio = BacktestPortfolio(initial_cash=initial_cash)
    engine = WheelBacktestEngine(
        data_provider=data,
        portfolio=portfolio,
        config=config,
        symbols=symbols,
        weights=weights,
    )

    start = date_type.fromisoformat(start_date)
    end = date_type.fromisoformat(end_date)

    engine.run(start, end)
    engine.print_summary()

    return portfolio


# ─── Plotly Visualization ──────────────────────────────────────────────────────


class BacktestVisualizer:
    """Generates interactive Plotly charts from backtest results."""

    def __init__(self, portfolio: BacktestPortfolio, title: str = "ThetaGang 回测"):
        self.portfolio = portfolio
        self.title = title
        self._stats = portfolio.compute_stats()
        self._equity_df: list[dict] | None = None

    def _build_equity_df(self) -> list[dict]:
        """Build equity curve dataframe."""
        rows = []
        for ts, value in self.portfolio.equity_history:
            d = ts.date() if isinstance(ts, datetime) else ts
            cash = next((c for t, c in self.portfolio.cash_history if t == ts), None)
            rows.append({"date": d, "equity": value, "cash": cash or 0})
        return rows

    def plot_equity_curve(
        self,
        filename: str | None = None,
        include_cash: bool = True,
    ) -> plotly.graph_objects.Figure:
        """Equity curve with drawdown and trade markers."""
        import plotly.graph_objects as go
        from plotly.subplots import make_subplots

        equity = self._build_equity_df()
        if not equity:
            fig = go.Figure()
            fig.add_annotation(text="无权益曲线数据")
            return fig

        dates = [r["date"] for r in equity]
        values = [r["equity"] for r in equity]

        # Compute drawdown
        peak = self.portfolio.initial_cash
        drawdowns = []
        dd_pcts = []
        for v in values:
            if v >= peak:
                peak = v
            dd = peak - v
            dd_pcts.append((dd / peak * 100) if peak > 0 else 0)
            drawdowns.append(dd)

        # Build subplots: equity top, drawdown bottom
        fig = make_subplots(
            rows=2, cols=1,
            shared_xaxes=True,
            vertical_spacing=0.08,
            row_heights=[0.75, 0.25],
            subplot_titles=("", "回撤"),
        )

        # Equity line
        fig.add_trace(go.Scatter(
            x=dates,
            y=values,
            mode="lines",
            name="组合价值",
            line=dict(color="#2196F3", width=2),
            hovertemplate="日期: %{x}<br>价值: $%{y:,.2f}<extra></extra>",
        ), row=1, col=1)

        # Fill under equity
        fig.add_trace(go.Scatter(
            x=list(dates) + list(reversed(dates)),
            y=values + [self.portfolio.initial_cash] * len(values),
            fill="toself",
            fillcolor="rgba(33,150,243,0.08)",
            line=dict(color="rgba(33,150,243,0)"),
            name="区域填充",
            showlegend=False,
            hoverinfo="skip",
        ), row=1, col=1)

        # Initial cash reference line
        fig.add_hline(
            y=self.portfolio.initial_cash,
            line_dash="dash",
            line_color="#90A4AE",
            annotation_text=f"初始资金 ${self.portfolio.initial_cash:,.0f}",
            row=1, col=1,
        )

        # Drawdown fill
        fig.add_trace(go.Scatter(
            x=dates,
            y=[-d for d in drawdowns],
            mode="lines",
            name="回撤",
            line=dict(color="#F44336", width=1.5),
            fill="tozeroy",
            fillcolor="rgba(244,67,54,0.15)",
            hovertemplate="日期: %{x}<br>回撤: $%{y:,.2f}<extra></extra>",
        ), row=2, col=1)

        # Trade markers on equity curve
        option_trades = [t for t in self.portfolio.trade_log if t.sec_type == "OPT"]
        for trade in option_trades:
            trade_date = trade.timestamp.date() if isinstance(trade.timestamp, datetime) else trade.timestamp
            if trade_date not in dates:
                continue
            idx = dates.index(trade_date)
            color = "#4CAF50" if trade.credit > 0 else "#F44336"
            symbol = "▲" if trade.action == "SELL" else "▼"
            fig.add_annotation(
                x=trade_date,
                y=values[idx],
                text=f"{symbol} {trade.right}",
                showarrow=True,
                arrowhead=2,
                arrowcolor=color,
                font=dict(color=color, size=10),
            )

        # Stats annotation
        stats_text = (
            f"<b>总收益:</b> {self._stats.total_return:+.2f} ({self._stats.total_return_pct:+.2f}%)<br>"
            f"<b>最大回撤:</b> {self._stats.max_drawdown:+.2f} ({self._stats.max_drawdown_pct:+.2f}%)<br>"
            f"<b>夏普比率:</b> {self._stats.sharpe_ratio:.2f}<br>"
            f"<b>胜率:</b> {self._stats.win_rate:.1f}% ({self._stats.winning_trades}/{self._stats.total_trades})"
        )
        fig.add_annotation(
            x=0.99, y=0.95,
            xref="paper", yref="paper",
            text=stats_text,
            showarrow=False,
            font=dict(size=11, color="#333"),
            align="left",
            bgcolor="rgba(255,255,255,0.85)",
            bordercolor="#ddd",
            borderwidth=1,
            borderpad=6,
        )

        fig.update_layout(
            title=dict(text=self.title, font=dict(size=18)),
            hovermode="x unified",
            showlegend=True,
            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
            template="plotly_white",
            height=600,
        )
        fig.update_xaxes(title_text="日期", row=2, col=1)
        fig.update_yaxes(title_text="组合价值 ($)", row=1, col=1)
        fig.update_yaxes(title_text="回撤 ($)", row=2, col=1)

        if filename:
            fig.write_html(filename)
        return fig

    def plot_returns_distribution(
        self,
        filename: str | None = None,
    ) -> plotly.graph_objects.Figure:
        """Histogram of daily returns."""
        import plotly.graph_objects as go

        equity = self._build_equity_df()
        if len(equity) < 3:
            fig = go.Figure()
            fig.add_annotation(text="数据点不足")
            return fig

        values = [r["equity"] for r in equity]
        daily_returns = []
        for i in range(1, len(values)):
            if values[i - 1] > 0:
                daily_returns.append((values[i] - values[i - 1]) / values[i - 1] * 100)

        if not daily_returns:
            fig = go.Figure()
            fig.add_annotation(text="无收益率数据")
            return fig

        fig = go.Figure()
        fig.add_trace(go.Histogram(
            x=daily_returns,
            nbinsx=30,
            marker_color="#2196F3",
            opacity=0.8,
            hovertemplate="收益率: %{x:.2f}%<br>天数: %{y}<extra></extra>",
        ))

        mean_ret = sum(daily_returns) / len(daily_returns)
        fig.add_vline(
            x=mean_ret,
            line_dash="dash",
            line_color="#FF9800",
            annotation_text=f"均值: {mean_ret:.3f}%",
        )

        fig.update_layout(
            title=dict(text="日收益率分布", font=dict(size=16)),
            xaxis_title="日收益率 (%)",
            yaxis_title="天数",
            template="plotly_white",
            height=400,
        )

        if filename:
            fig.write_html(filename)
        return fig

    def plot_trade_log(
        self,
        filename: str | None = None,
    ) -> plotly.graph_objects.Figure:
        """Table of all trades."""
        import plotly.graph_objects as go

        trades = self.portfolio.trade_log
        if not trades:
            fig = go.Figure()
            fig.add_annotation(text="无交易记录")
            return fig

        fig = go.Figure(data=[go.Table(
            header=dict(
                values=["日期", "标的", "操作", "数量", "行权价", "类型", "权利金", "备注"],
                fill_color="#1976D2",
                font=dict(color="white", size=12),
                align="center",
                height=30,
            ),
            cells=dict(
                values=[
                    [t.timestamp.strftime("%Y-%m-%d") if isinstance(t.timestamp, datetime) else str(t.timestamp) for t in trades],
                    [t.symbol for t in trades],
                    [t.action for t in trades],
                    [str(t.quantity) for t in trades],
                    [f"${t.strike:.1f}" if t.strike else "-" for t in trades],
                    [t.right or t.sec_type for t in trades],
                    [f"${t.credit:,.2f}" for t in trades],
                    [t.notes[:40] if t.notes else "" for t in trades],
                ],
                fill_color=[["#f5f5f5" if i % 2 == 0 else "white" for i in range(len(trades))]],
                align=["center"] * 7 + ["left"],
                height=28,
                font=dict(size=11),
            ),
        )])

        fig.update_layout(
            title=dict(text="交易记录", font=dict(size=16)),
            height=max(300, len(trades) * 28 + 80),
            margin=dict(l=20, r=20, t=40, b=20),
        )

        if filename:
            fig.write_html(filename)
        return fig

    def plot_position_pie(
        self,
        filename: str | None = None,
    ) -> plotly.graph_objects.Figure:
        """Pie chart of current positions."""
        import plotly.graph_objects as go

        positions = list(self.portfolio.positions.values())
        if not positions:
            fig = go.Figure()
            fig.add_annotation(text="无持仓")
            return fig

        labels = []
        values = []
        colors = ["#2196F3", "#4CAF50", "#FF9800", "#9C27B0", "#F44336"]

        for i, pos in enumerate(positions):
            val = abs(pos.quantity * pos.avg_cost)
            if pos.sec_type == "OPT":
                labels.append(f"{pos.symbol} {pos.right} {pos.strike}")
            else:
                labels.append(f"{pos.symbol} ({pos.sec_type})")
            values.append(val)

        fig = go.Figure(data=[go.Pie(
            labels=labels,
            values=values,
            marker_colors=colors[:len(labels)],
            textinfo="label+percent",
            hovertemplate="%{label}<br>市值: $%{value:,.2f}<br>占比: %{percent}<extra></extra>",
        )])

        fig.update_layout(
            title=dict(text="当前持仓分布", font=dict(size=16)),
            height=400,
            margin=dict(t=50, b=20),
        )

        if filename:
            fig.write_html(filename)
        return fig

    def plot_all(
        self,
        output_dir: str | Path | None = None,
    ) -> dict[str, plotly.graph_objects.Figure]:
        """Generate all charts and optionally save to HTML files."""
        output_dir = Path(output_dir) if output_dir else None

        figures = {}

        figures["equity"] = self.plot_equity_curve(
            filename=str(output_dir / "equity_curve.html") if output_dir else None
        )
        figures["returns"] = self.plot_returns_distribution(
            filename=str(output_dir / "returns_dist.html") if output_dir else None
        )
        figures["trades"] = self.plot_trade_log(
            filename=str(output_dir / "trade_log.html") if output_dir else None
        )
        figures["positions"] = self.plot_position_pie(
            filename=str(output_dir / "positions.html") if output_dir else None
        )

        # Combined dashboard
        from plotly.subplots import make_subplots
        import plotly.graph_objects as go

        dash = make_subplots(
            rows=2, cols=2,
            subplot_titles=["权益曲线", "回撤", "日收益率分布", "持仓分布"],
            specs=[[{"type": "scatter"}, {"type": "scatter"}],
                   [{"type": "histogram"}, {"type": "pie"}]],
        )

        equity = self._build_equity_df()
        dates = [r["date"] for r in equity]
        values = [r["equity"] for r in equity]

        # Equity
        dash.add_trace(go.Scatter(
            x=dates, y=values, mode="lines", name="组合价值",
            line=dict(color="#2196F3", width=2),
        ), row=1, col=1)
        dash.add_hline(y=self.portfolio.initial_cash, line_dash="dash",
                       line_color="#90A4AE", row=1, col=1)

        # Drawdown
        peak = self.portfolio.initial_cash
        dd_vals = []
        for v in values:
            if v >= peak:
                peak = v
            dd_vals.append(peak - v)
        dash.add_trace(go.Scatter(
            x=dates, y=dd_vals, mode="lines", name="回撤",
            line=dict(color="#F44336", width=1.5),
            fill="tozeroy", fillcolor="rgba(244,67,54,0.15)",
        ), row=1, col=2)

        # Returns histogram
        daily_returns = []
        for i in range(1, len(values)):
            if values[i - 1] > 0:
                daily_returns.append((values[i] - values[i - 1]) / values[i - 1] * 100)
        if daily_returns:
            dash.add_trace(go.Histogram(
                x=daily_returns, nbinsx=25, marker_color="#2196F3", opacity=0.8,
            ), row=2, col=1)

        # Positions pie
        positions = list(self.portfolio.positions.values())
        if positions:
            pie_labels = [f"{p.symbol} {p.right} {p.strike}" if p.sec_type == "OPT" else p.symbol
                         for p in positions]
            pie_values = [abs(p.quantity * p.avg_cost) for p in positions]
            dash.add_trace(go.Pie(labels=pie_labels, values=pie_values,
                                  textinfo="label+percent", hole=0.3), row=2, col=2)

        dash.update_layout(
            title=dict(text=f"{self.title} — Dashboard", font=dict(size=18)),
            template="plotly_white",
            height=800,
            showlegend=False,
        )

        figures["dashboard"] = dash

        if output_dir:
            dash.write_html(str(output_dir / "dashboard.html"))
            print(f"Charts saved to {output_dir}/")

        return figures


def plot_backtest(
    portfolio: BacktestPortfolio,
    title: str = "ThetaGang 回测",
    output_dir: str | Path | None = None,
    symbols: list[str] | None = None,
) -> dict[str, plotly.graph_objects.Figure]:
    """Convenience function to plot backtest results.

    Args:
        portfolio: BacktestPortfolio with results
        title: Chart title
        output_dir: If provided, saves HTML files to this directory
        symbols: Symbols in the backtest (for title)

    Returns:
        Dict of figure name -> Plotly figure
    """
    if symbols:
        title = f"{' / '.join(symbols)} — {title}"
    viz = BacktestVisualizer(portfolio, title)
    return viz.plot_all(output_dir=output_dir)
