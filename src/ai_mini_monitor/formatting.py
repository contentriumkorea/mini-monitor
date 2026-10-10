# SPDX-License-Identifier: GPL-3.0-or-later
"""Account balance formatting shared by the dashboard and taskbar."""

from decimal import Decimal
import re


def format_credit_balance(value: str) -> str:
    if value == "UNLIMITED":
        return "UNLTD"
    if re.fullmatch(r"[0-9]{1,12}(?:\.[0-9]{1,32})?", value) is None:
        return "--"
    return f"{int(Decimal(value)):,}"
