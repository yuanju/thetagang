from io import StringIO

from rich.box import SIMPLE_HEAD
from rich.pretty import Pretty
from rich import print as rich_print
from rich.console import Console
from rich.table import Table

# 将一个对象转为字典对象
def to_expanded_dict(obj):
    """
    递归地将对象转换为字典，绕过 __repr__ 限制，以便 rich.Pretty 可以展开显示。
    """
    if isinstance(obj, (list, tuple)):
        return [to_expanded_dict(item) for item in obj]
    elif isinstance(obj, dict):
        return {k: to_expanded_dict(v) for k, v in obj.items()}
    elif hasattr(obj, '__dict__'):
        # 处理普通对象
        result = {}
        for key, value in obj.__dict__.items():
            # 过滤掉一些内部变量，可选
            if not key.startswith('_'):
                result[key] = to_expanded_dict(value)
        # 如果 __dict__ 为空但有 __slots__ (常见于高性能库如 ibapi)
        if not result and hasattr(obj, '__slots__'):
            for slot in obj.__slots__:
                if hasattr(obj, slot):
                    result[slot] = to_expanded_dict(getattr(obj, slot))
        return result if result else f"<{type(obj).__name__}>" # 防止空对象
    elif hasattr(obj, '__slots__'):
        # 处理只有 __slots__ 的对象
        result = {}
        for slot in obj.__slots__:
            if hasattr(obj, slot):
                result[slot] = to_expanded_dict(getattr(obj, slot))
        return result if result else f"<{type(obj).__name__}>"
    else:
        return obj

# 利用rich美观打印对象
def rich_print_pretty(obj, expand_all: bool = True, indent_guides: bool = True, max_length: int = 20) -> None:
    """打印对象的结构化视图

    Args:
        obj: 要打印的对象
        expand_all: 是否展开所有层级
        indent_guides: 是否显示缩进引导线
        max_length: 最大显示长度
    """
    # if isinstance(obj, list):
    #     dict_data = [to_expanded_dict(item) for item in obj if not isinstance(item, (list, tuple))]
    #     rich_print(Pretty(dict_data, expand_all=expand_all, indent_guides=indent_guides, max_length=max_length, max_depth=5))
    #     return
    rich_print(Pretty(obj, expand_all=expand_all, indent_guides=indent_guides, max_length=max_length, max_depth=5))

# 在钉钉中打印订单信息
def print_ding_orders(orders: list, title:str) -> str:
    markdown = ''
    if not orders:
        return ""
    markdown = f"## {title} \n\n"
    headers = ["订单号", "标的", "方向", "类型", "数量", "价格", "状态"]
    data = []
    for order in orders:
        contract = order.contract
        order_info = order.order

        # 标的显示
        if hasattr(contract, 'symbol'):
            symbol = contract.symbol
            sec_type = getattr(contract, 'secType', 'STK')
            if sec_type == 'OPT':
                strike = getattr(contract, 'strike', '')
                expiry = getattr(contract, 'lastTradeDateOrContractMonth', '')
                right = getattr(contract, 'right', '')
                symbol = f"{symbol} {right}${strike} {expiry}"
        else:
            symbol = str(contract)
            sec_type = "STK"

        # 方向
        action = order_info.action.upper()
        action_str = f"{action}"

        # 订单类型
        order_type = order_info.orderType

        # 状态
        status = order.orderStatus.status if order.orderStatus else "Unknown"

        markdown += "\n".join([
            "- **订单号**: " + str(order_info.orderId),
            "- **标的**: " + symbol,
            "- **方向**: " + action_str,
            "- **类型**: " + order_type,
            "- **数量**: " + str(order_info.totalQuantity),
            "- **价格**: " + f"{order_info.lmtPrice:.2f}" if order_info.lmtPrice else "-",
            "- **状态**: " + f"{status}",
        ])
        markdown += "\n-----\n"

    return markdown
