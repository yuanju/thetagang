# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

ThetaGang is an IBKR (Interactive Brokers) trading bot that implements "The Wheel" options strategy with configurable portfolio automation. It sells puts to acquire positions, then sells calls against held shares, while rebalancing across a target portfolio allocation.

## Architecture

### Module Organization

- **Core trading logic**: `thetagang/portfolio_manager.py` - main orchestration for the trading loop
- **CLI entry point**: `thetagang/entry.py` re-exports from `thetagang/main.py` (Click commands)
- **Runtime wiring**: `thetagang/thetagang.py` - config loading, IBKR/IBC setup, event loop, dry-run control
- **Broker integration**: `thetagang/ibkr.py` - IBKR API wrappers; `thetagang/orders.py` - order execution helpers
- **Configuration**: `thetagang/config.py` - main Config model; `thetagang/config_models.py` - Pydantic models for all config sections; `thetagang/legacy_config.py` - v1 config parsing
- **Strategies**: `thetagang/strategies/` - strategy engines (wheel, regime rebalance, VIX hedge, cash management)
- **Supporting**: `thetagang/db.py` (SQLAlchemy + Alembic), `thetagang/trades.py`, `thetagang/util.py`

### Strategy Stage System

The bot runs through configurable **stages** defined in `thetagang/config.py`:

```
STAGE_KIND_BY_ID maps stage IDs to kinds:
- options_write_puts, options_write_calls
- equity_regime_rebalance, equity_buy_rebalance, equity_sell_rebalance
- options_roll_positions, options_close_positions
- post_vix_call_hedge, post_cash_management
```

Strategies (wheel, regime_rebalance, vix_call_hedge, cash_management) map to sets of stage IDs. The stage system enforces:
- No cycles in dependencies
- Disabled stages cannot be depended upon
- Call writing depends on put-writing (share quantity computation)

### Configuration Schema

The v2 config schema (required) lives in `thetagang/config.py` with Pydantic models. Key sections:
- `meta.schema_version` must be 2
- `run.strategies` or `run.stages` (mutually exclusive)
- `portfolio.symbols` with weight allocations (must sum to 1.0)
- `strategies.wheel`, `strategies.regime_rebalance`, `strategies.vix_call_hedge`, `strategies.cash_management`

Config migration from v1 is handled in `thetagang/config_migration/`.

## Development Commands

```bash
# Run the bot (dry-run first)
uv run thetagang --config thetagang.toml --dry-run

# Run tests
uv run pytest
uv run pytest tests/test_portfolio_manager.py  # specific file

# Coverage
uv run pytest --cov=thetagang

# Linting and formatting
uv run ruff check .
uv run ruff format .

# Type checking
uv run ty check

# Pre-commit hooks
uv run pre-commit run --all-files
```

## Coding Conventions

- Python ≥3.10 with 4-space indentation
- Ruff enforces 88-character line length
- Use snake_case for functions/variables, CapWords for classes
- Pydantic models in `config_models.py` for new config fields
- Add type hints to all new functions
- Conventional commits: `fix:`, `feat:`, `chore:` with optional scope

## Testing Guidelines

- Tests use `pytest` and `pytest-asyncio`
- Async tests for IBKR flows; fixtures in `tests/conftest.py` stub external calls
- Name test files `test_<module>.py`
- Cover edge cases for buy/sell-only rebalancing and order routing

## Risk-Sensitive Changes

This is a trading bot that places real orders. Prioritize safety in reviews:

1. **Never submit live orders unexpectedly** - changes must respect `dry_run` mode
2. **Guard trading behavior** - flag changes to quantity calculations, margin usage, strike limits, DTE filters, rolling/closing criteria
3. **Stage orchestration** - stage ordering must maintain invariants (collect_state before actions, no cycles)
4. **Config migration** - preserve v2 schema guarantees, backup files before mutation

Required validation for risky changes: `uv run pytest`, `uv run ruff check .`, `uv run ruff format --check .`, `uv run ty check`
