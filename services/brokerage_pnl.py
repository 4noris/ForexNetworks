"""Audited MT5 broker P&L calculations adapted from the user-provided report.

The RMS API owns the single configured Manager connection; this module never
connects or stores credentials. Historical snapshots are end-of-day UTC.
"""
import io
import fnmatch
import json
import math
import re
from collections import defaultdict
from datetime import datetime, time, timedelta, timezone
from pathlib import Path

import pandas as pd

try:
    import MT5Manager
    MT5_IMPORT_ERROR = None
except (ImportError, OSError) as exc:
    MT5Manager = None
    MT5_IMPORT_ERROR = str(exc)

REPORT_VERSION = 7
TRANSACTION_RULES_STORE = Path(__file__).resolve().parent.parent / "config" / "transaction_rules.json"


def load_transaction_rules():
    """Explicit, ticket-specific reviewed allocations; never infer corrections."""
    if not TRANSACTION_RULES_STORE.exists():
        return []
    rules = json.loads(TRANSACTION_RULES_STORE.read_text(encoding="utf-8-sig"))
    if not isinstance(rules, list):
        raise ValueError("transaction_rules.json must contain a list of reviewed rules.")
    if any(not isinstance(rule, dict) for rule in rules):
        raise ValueError("Each reviewed transaction rule must be an object.")
    return rules

def _last_error_text():
    try:
        err = MT5Manager.LastError()
    except Exception as exc:
        return str(exc)

    if isinstance(err, tuple):
        native_code = err[0] if len(err) > 0 else ""
        retcode = err[1] if len(err) > 1 else ""
        message = err[2] if len(err) > 2 else ""
        if message:
            return f"{message} (native={native_code}, retcode={retcode})"
        return f"native={native_code}, retcode={retcode}"
    return str(err)

def _retcode_to_int(code):
    try:
        return int(code)
    except Exception:
        try:
            return int(getattr(code, "value"))
        except Exception:
            return None

def _value(obj, *names, default=None):
    if obj is None:
        return default

    if isinstance(obj, dict):
        for name in names:
            snake = re.sub(r"(?<!^)(?=[A-Z])", "_", str(name)).lower()
            for key in (name, str(name).lower(), str(name).upper(), snake):
                if key in obj:
                    return obj.get(key, default)

    if hasattr(obj, "_asdict"):
        try:
            data = obj._asdict()
            if isinstance(data, dict):
                for name in names:
                    snake = re.sub(r"(?<!^)(?=[A-Z])", "_", str(name)).lower()
                    for key in (name, str(name).lower(), str(name).upper(), snake):
                        if key in data:
                            return data.get(key, default)
        except Exception:
            pass

    checked = set()
    for name in names:
        for candidate in (
            name,
            str(name).lower(),
            str(name).upper(),
            str(name)[:1].lower() + str(name)[1:],
            re.sub(r"(?<!^)(?=[A-Z])", "_", str(name)).lower(),
        ):
            if candidate in checked:
                continue
            checked.add(candidate)
            try:
                attr = getattr(obj, candidate)
            except Exception:
                continue
            try:
                return attr() if callable(attr) else attr
            except Exception:
                continue

    return default

def _to_int(value, default=None):
    try:
        if value is None:
            return default
        return int(value)
    except Exception:
        return default

def _enum_int(default, *path):
    obj = MT5Manager
    try:
        for part in path:
            obj = getattr(obj, part)
        return int(obj)
    except Exception:
        return int(default)

def _date_bounds_utc(day):
    start_dt = datetime.combine(day, time.min, tzinfo=timezone.utc)
    end_dt = datetime.combine(day, time.max, tzinfo=timezone.utc)
    return int(start_dt.timestamp()), int(end_dt.timestamp())


def _to_float(value, default=None):
    try:
        number = float(value)
        return number if math.isfinite(number) else default
    except (TypeError, ValueError, OverflowError):
        return default


def _pick_float(*values, default=None):
    for value in values:
        number = _to_float(value)
        if number is not None:
            return number
    return default


def _is_retcode_like(value):
    return isinstance(value, int) and not isinstance(value, bool) or "EnMTAPIRetcode" in type(value).__name__


def _unwrap_response(resp):
    """Preserve tuple compatibility and support envelope objects/dictionaries."""
    if isinstance(resp, tuple):
        if len(resp) >= 2 and isinstance(resp[0], bool):
            return (0 if resp[0] else -1), resp[1], str(resp[2]) if len(resp) > 2 else ""
        if len(resp) >= 2 and _is_retcode_like(resp[0]):
            return _retcode_to_int(resp[0]), resp[1], str(resp[2]) if len(resp) > 2 else ""
        if len(resp) == 1:
            return None, resp[0], ""
    code = _value(resp, "retcode", "RetCode", default=None)
    if code is not None:
        return _retcode_to_int(code), _value(resp, "data", "Data"), str(_value(resp, "message", "Message", default=""))
    return None, resp, ""


def _as_list(value):
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return list(value)
    if isinstance(value, dict):
        # A record dictionary is a single record, not an empty array.
        return [value]
    if isinstance(value, (str, bytes, bool, int, float)):
        return []
    try:
        return list(value)
    except TypeError:
        pass
    total = _value(value, "Total", "Count", default=None)
    getter = getattr(value, "Next", None) or getattr(value, "Get", None)
    if total is not None and getter is not None:
        return [getter(i) for i in range(int(total))]
    return [value]


def _last_error_code():
    try:
        error = MT5Manager.LastError()
        if isinstance(error, tuple):
            return _retcode_to_int(error[1]) if len(error) > 1 else None
        return _retcode_to_int(_value(error, "RetCode", "Code"))
    except Exception:
        return None


def _response_to_data(resp, ctx, allow_empty=False):
    code, data, message = _unwrap_response(resp)
    # This wrapper returns False + MT_RET_ERR_NOTFOUND for empty history,
    # including groups with accounts but no deals. It is not a request failure.
    empty_codes = {1, 13}
    if allow_empty and (code in empty_codes or
                        (code is None and (data is None or data is False) and _last_error_code() in empty_codes)):
        return [], None
    # MT_RET_OK_NONE is an explicitly empty successful request.
    if code == _enum_int(1, "EnMTAPIRetcode", "MT_RET_OK_NONE") and data is None:
        return [], None
    if code is not None and code != 0:
        return None, f"{ctx} failed ({code}): {message or _last_error_text()}"
    if data is None or data is False:
        return None, f"{ctx} failed: {_last_error_text()}"
    if _is_retcode_like(data):
        value = _retcode_to_int(data)
        if value == 1 or (allow_empty and value == 13):
            return [], None
        return None, f"{ctx} returned status {value} without data: {_last_error_text()}"
    if data is True:
        return None, f"{ctx} returned success without data."
    return data, None


def _call_succeeded(resp):
    if isinstance(resp, bool):
        return resp
    code, data, _ = _unwrap_response(resp)
    if code is not None:
        return code == 0
    if _is_retcode_like(data):
        return _retcode_to_int(data) == 0
    return False


def _request_array(api, method_name, args, container_names=()):
    """Reference direct-return request plus output-container fallback."""
    method = getattr(api, method_name, None)
    if method is None:
        return [], f"{method_name} is not available in this wrapper."
    context = f"{method_name}{args}"
    errors = []
    try:
        allow_empty = method_name in {"UserGetByGroup", "DealRequestByGroup", "DailyRequestByGroup",
                                     "DealRequestByLogins", "DailyRequestByLogins", "DealRequest", "DailyRequest"}
        data, error = _response_to_data(method(*args), context, allow_empty=allow_empty)
        if error is None:
            return _as_list(data), None
        errors.append(error)
    except Exception as exc:
        errors.append(f"{context}: {exc}")
    for name in container_names:
        cls = getattr(MT5Manager, name, None)
        if cls is None:
            continue
        try:
            container = cls()
            if _call_succeeded(method(*args, container)):
                return _as_list(container), None
            errors.append(f"{context}: {_last_error_text()}")
        except Exception as exc:
            errors.append(f"{context}: {exc}")
    return [], "; ".join(errors[-3:])


def request_groups(api):
    errors = []
    for args in [("*",), ("",), ()]:
        records, error = _request_array(api, "GroupRequestArray", args, ("GroupArray", "MTGroupArray"))
        if error is None:
            names = [str(g) if isinstance(g, str) else str(_value(g, "Group", "Name", default="")) for g in records]
            names = sorted({name for name in names if name})
            if names:
                return names, None
            errors.append("GroupRequestArray returned no group names.")
        else:
            errors.append(error)
    # Native Python wrapper enumerates cached configurations by position.
    if hasattr(api, "GroupTotal") and hasattr(api, "GroupNext"):
        try:
            code, total, message = _unwrap_response(api.GroupTotal())
            count = _to_int(total)
            if code not in (None, 0) or count is None or count < 0:
                errors.append(f"GroupTotal failed: {message or _last_error_text()}")
            else:
                names, enumeration_errors = set(), []
                for index in range(count):
                    data, error = _response_to_data(api.GroupNext(index), f"GroupNext({index})")
                    if error:
                        enumeration_errors.append(error)
                        continue
                    name = data if isinstance(data, str) else _value(data, "Group", "Name", default="")
                    if name:
                        names.add(str(name))
                    else:
                        enumeration_errors.append(f"GroupNext({index}) returned no group name.")
                if names:
                    return sorted(names), "; ".join(enumeration_errors) or None
                errors.extend(enumeration_errors or ["GroupTotal returned no available groups."])
        except Exception as exc:
            errors.append(f"Group enumeration failed: {exc}")
    # Keep the reference's GroupGet output-container compatibility path.
    cls = getattr(MT5Manager, "GroupArray", None)
    if cls is not None:
        try:
            container = cls()
            if _call_succeeded(api.GroupGet(container)):
                return sorted({str(_value(g, "Group", "Name")) for g in _as_list(container)}), None
        except Exception as exc:
            errors.append(str(exc))
    return [], "; ".join(errors[-3:])


def request_users_by_group(api, group):
    return _request_array(api, "UserGetByGroup", (group,), ("UserArray", "MTUserArray"))


def request_deals_by_group(api, group, from_ts, to_ts):
    return _request_array(api, "DealRequestByGroup", (group, from_ts, to_ts), ("DealArray", "MTDealArray"))


def request_daily_by_group(api, group, from_ts, to_ts):
    return _request_array(api, "DailyRequestByGroup", (group, from_ts, to_ts), ("DailyArray", "MTDailyArray"))


def request_account(api, login):
    rows, error = _request_array(api, "UserAccountGet", (login,), ("UserAccount", "MTAccount"))
    return (rows[0] if rows else None), error


def parse_logins(text):
    parts = [part for part in re.split(r"[,;\s]+", str(text).strip()) if part]
    if any(not re.fullmatch(r"[0-9]+", part) or int(part) <= 0 for part in parts):
        raise ValueError("Enter positive login numbers separated by commas, spaces or new lines.")
    return sorted({int(part) for part in parts})


def resolve_login_scope(api, logins, allowed_groups=None, progress=None):
    users, errors = {}, []
    identifiers = sorted(set(logins))
    bulk_users = {}
    if hasattr(api, "UserRequestByLogins"):
        for offset in range(0, len(identifiers), 500):
            if progress:
                progress(offset, len(identifiers))
            records, error = _request_array(api, "UserRequestByLogins", (identifiers[offset:offset + 500],))
            if error:
                raise ValueError(f"Account lookup failed: {error}")
            for user in records:
                identifier = _to_int(_value(user, "Login"))
                if identifier in identifiers:
                    bulk_users[identifier] = user
    for index, login in enumerate(identifiers):
        if hasattr(api, "UserRequestByLogins"):
            records, error = ([bulk_users[login]] if login in bulk_users else []), None
        else:
            if progress:
                progress(index, len(identifiers))
            records, error = _request_array(api, "UserRequest", (login,), ("UserArray", "MTUserArray"))
        if error and not hasattr(api, "UserRequest"):
            records, error = _request_array(api, "UserGet", (login,))
        user = records[0] if records else None
        group = _value(user, "Group")
        if error or not group or _to_int(_value(user, "Login")) != login:
            errors.append(f"Login {login}: {error or 'Account/group not available from server.'}")
        elif allowed_groups is not None and str(group) not in allowed_groups:
            errors.append(f"Login {login}: group {group} is outside the selected groups.")
        else:
            users[login] = user
    if errors:
        raise ValueError("; ".join(errors))
    if progress:
        progress(len(identifiers), len(identifiers))
    return users


def _request_login_history(api, kind, logins, from_ts, to_ts):
    rows = []
    method = f"{kind}RequestByLogins"
    identifiers = sorted(set(logins))
    if hasattr(api, method):
        for offset in range(0, len(identifiers), 500):
            chunk, error = _request_array(api, method, (identifiers[offset:offset + 500], from_ts, to_ts))
            if error:
                return [], error
            rows.extend(chunk)
        return rows, None
    for login in identifiers:
        chunk, error = _request_array(api, f"{kind}Request", (login, from_ts, to_ts))
        if error:
            return [], f"Login {login}: {error}"
        rows.extend(chunk)
    return rows, None


def request_group_currency(api, group):
    errors = []
    for method in ("GroupRequest", "GroupGet"):
        rows, error = _request_array(api, method, (group,))
        if error:
            errors.append(error)
        elif rows:
            currency = _value(rows[0], "Currency", default="")
            if currency:
                return str(currency), None
    return "Unknown", f"Group {group}: currency unavailable; {'; '.join(errors)}"


DEAL_BALANCE = _enum_int(2, "MTDeal", "EnDealAction", "DEAL_BALANCE")
DEAL_CREDIT = _enum_int(3, "MTDeal", "EnDealAction", "DEAL_CREDIT")
DEAL_CREDIT_ACTIONS = {DEAL_CREDIT, _enum_int(20, "MTDeal", "EnDealAction", "DEAL_SO_COMPENSATION_CREDIT")}
DEAL_BONUS = _enum_int(6, "MTDeal", "EnDealAction", "DEAL_BONUS")
DEAL_CORRECTION = _enum_int(5, "MTDeal", "EnDealAction", "DEAL_CORRECTION")
DEAL_COMPENSATION = _enum_int(19, "MTDeal", "EnDealAction", "DEAL_SO_COMPENSATION")
COMMISSION_ACTIONS = {_enum_int(n, "MTDeal", "EnDealAction", name) for n, name in
                      [(7, "DEAL_COMMISSION"), (8, "DEAL_COMMISSION_DAILY"), (9, "DEAL_COMMISSION_MONTHLY")]}
CLOSED_TRADE_ACTIONS = {_enum_int(n, "MTDeal", "EnDealAction", key) for n, key in
                        [(0, "DEAL_BUY"), (1, "DEAL_SELL"), (13, "DEAL_BUY_CANCELED"), (14, "DEAL_SELL_CANCELED")]}
CLOSED_ENTRIES = {_enum_int(n, "MTDeal", "EnDealEntry", key) for n, key in
                  [(1, "ENTRY_OUT"), (2, "ENTRY_INOUT"), (3, "ENTRY_OUT_BY")]}
FLOW_COLUMNS = ["Deposit", "Credit In", "Credit Out", "Additional", "Withdrawal"]
SOURCE_COLUMNS = ["Opening Equity", "Closing Equity", *FLOW_COLUMNS]
CHARGE_COLUMNS = ["Client Commission", "Client Swap"]
PNL_COLUMNS = ["Client PnL", "Broker PnL", *CHARGE_COLUMNS, "Broker Commission", "Broker Swap",
               "Client PnL Before Posted Charges", "Broker PnL Before Posted Charges"]
MONEY_COLUMNS = ["Opening Equity", "Closing Equity", "Equity Difference", *FLOW_COLUMNS, "Net PnL", "Closed P/L", *PNL_COLUMNS]
REPORT_COLUMNS = ["Login", "Name", "Group", "Currency", "Opening Equity", "Closing Equity",
                  "Equity Difference", "Deposit", "Credit In", "Credit Out", "Additional", "Withdrawal",
                  "Net PnL", "B-Book Result", *PNL_COLUMNS, "Closed P/L", "Opening Source", "Closing Source",
                  "Opening Group", "Closing Group", "Data Quality"]
FORMULA = "(Closing Equity - Opening Equity) - Deposit - Credit In - Additional + Withdrawal + Credit Out"


def calculate_net_pnl(opening_equity, closing_equity, deposit, credit_in, additional, withdrawal, credit_out):
    """Authoritative formula. Keep precision until display/export formatting."""
    return math.fsum([closing_equity, -opening_equity, -deposit, -credit_in,
                      -additional, withdrawal, credit_out])


def b_book_result(net_pnl):
    if _to_float(net_pnl) is None:
        return "UNAVAILABLE"
    if net_pnl == 0:
        return "BREAK-EVEN"
    return "BROKER PROFIT" if net_pnl < 0 else "BROKER LOSS"


def classify_balance_transaction(deal, rules=None):
    """One transaction belongs to at most one flow category.

    Native credit/bonus actions take precedence. Balance comments containing
    whole words credit, bonus or additional support legacy balance postings.
    Other balance postings are deposits/withdrawals. Charges, corrections,
    commissions and trading results remain equity performance, not cash flows.
    """
    action = _to_int(_value(deal, "Action"), -1)
    matches = matching_transaction_rules(deal, rules)
    if matches:
        if action not in {DEAL_BALANCE, DEAL_CORRECTION}:
            raise ValueError("Reviewed flow rules apply only to balance/correction actions.")
        if len(matches) != 1:
            raise ValueError("Multiple reviewed rules match this transaction.")
        rule = matches[0]
        if not rule.get("reason") or _to_int(rule.get("action")) != action:
            raise ValueError("Reviewed rule requires a reason and matching action.")
        amount = _to_float(_value(deal, "Profit"))
        if amount is None or ("deal" in rule and "expected_profit" not in rule) or ("expected_profit" in rule and _to_float(rule.get("expected_profit")) != amount):
            raise ValueError("Reviewed rule amount does not match transaction Profit.")
        category = rule.get("category")
        if category == "Equity performance":
            return None, 0.0
        if category not in FLOW_COLUMNS:
            raise ValueError("Reviewed rule has an invalid flow category.")
        if category in {"Deposit", "Credit In"} and amount < 0 or category in {"Withdrawal", "Credit Out"} and amount > 0:
            raise ValueError("Reviewed rule category conflicts with transaction sign.")
        return category, amount if category == "Additional" else abs(amount)
    comment = str(_value(deal, "Comment", default=""))
    comment_flow = action in {DEAL_BALANCE, DEAL_CORRECTION}
    if action == DEAL_BALANCE and re.fullmatch(r"\s*NON-LOSABLE BONUS OUT\s*\|\s*AUTO\s*", comment, re.I):
        amount = _to_float(_value(deal, "Profit"))
        if amount is None or amount > 0:
            raise ValueError("Bonus-out balance posting requires a finite non-positive amount.")
        return "Withdrawal", -amount
    credit = action in DEAL_CREDIT_ACTIONS or (comment_flow and re.search(r"\bcredit\b", comment, re.I))
    bonus = action == DEAL_BONUS or (comment_flow and re.search(r"\b(bonus|additional)\b", comment, re.I))
    if comment_flow and credit and bonus:
        raise ValueError("Transaction mentions both credit and bonus; flow allocation requires review.")
    if action == DEAL_BALANCE and not credit and not bonus and _charge_posting_kind(deal):
        return None, 0.0  # A posted charge is equity performance, not a withdrawal.
    if not (credit or bonus or action in {DEAL_BALANCE, DEAL_COMPENSATION}):
        return None, 0.0
    amount = _to_float(_value(deal, "Profit"))
    if amount is None:
        raise ValueError("Cash-flow deal has no finite Profit amount.")
    if credit:
        return ("Credit In", amount) if amount >= 0 else ("Credit Out", -amount)
    if bonus or action == DEAL_COMPENSATION:
        return "Additional", amount
    return ("Deposit", amount) if amount >= 0 else ("Withdrawal", -amount)


def matching_transaction_rules(deal, rules):
    """Ticket rules or general action + comment rules; every rule is explicit."""
    matches = []
    for rule in rules or []:
        if not isinstance(rule, dict):
            raise ValueError("Each transaction rule must be an object.")
        ticket_rule = "deal" in rule
        if ticket_rule:
            if _to_int(rule.get("deal"), 0) <= 0 or "login" not in rule:
                raise ValueError("Ticket rules require a positive deal and login.")
            if _to_int(rule["deal"]) != _to_int(_value(deal, "Deal", "Ticket")):
                continue
        elif not rule.get("comment_regex") or "action" not in rule:
            raise ValueError("General rules require action and comment_regex.")
        if "login" in rule and _to_int(rule["login"]) != _to_int(_value(deal, "Login")):
            continue
        if rule.get("group_regex") and not re.search(rule["group_regex"], str(_value(deal, "Group", default="")), re.I):
            continue
        if not ticket_rule and _to_int(rule["action"]) != _to_int(_value(deal, "Action")):
            continue
        if rule.get("comment_regex") and not re.search(rule["comment_regex"], str(_value(deal, "Comment", default="")), re.I):
            continue
        matches.append(rule)
    return matches


def _charge_posting_kind(deal):
    action = _to_int(_value(deal, "Action"), -1)
    if action in COMMISSION_ACTIONS:
        return "Commission"
    if action == DEAL_BALANCE:
        comment = str(_value(deal, "Comment", default=""))
        if re.search(r"\b(credit|bonus|additional)\b", comment, re.I):
            return None
        commission = bool(re.search(r"\bcommission\b", comment, re.I))
        swap = bool(re.search(r"\b(swap|rollover)\b", comment, re.I))
        if commission and swap:
            return "Unallocated charges"
        if commission or swap:
            return "Commission" if commission else "Swap"
    return None


def deal_charges(deal):
    """Signed client postings. Dedicated charge deals use Profit exactly once.

    Trading deals use Commission and Storage/Swap, including opening-entry
    commission. Floating/unposted swap is not inferred from posted history.
    """
    kind = _charge_posting_kind(deal)
    if kind:
        amount = _to_float(_value(deal, "Profit"))
        if kind == "Unallocated charges":
            return {key: None for key in CHARGE_COLUMNS}
        return {"Client Commission": amount if kind == "Commission" else 0.0,
                "Client Swap": amount if kind == "Swap" else 0.0}
    if _to_int(_value(deal, "Action")) in CLOSED_TRADE_ACTIONS:
        return {"Client Commission": _to_float(_value(deal, "Commission")),
                "Client Swap": _pick_float(_value(deal, "Storage"), _value(deal, "Swap"))}
    return dict.fromkeys(CHARGE_COLUMNS, 0.0)


def pnl_views(net_pnl, commission, swap, book_mode="B-Book"):
    """B-book perspective; equity PnL already includes all account charges."""
    net, commission, swap = map(_to_float, (net_pnl, commission, swap))
    before = math.fsum([net, -commission, -swap]) if all(v is not None for v in (net, commission, swap)) else None
    if book_mode not in {"A-Book", "B-Book"}:
        raise ValueError("Choose A-Book or B-Book.")
    broker = (-math.fsum([commission, swap]) if commission is not None and swap is not None else None) if book_mode == "A-Book" else (-net if net is not None else None)
    return {"Client PnL": net, "Broker PnL": broker,
            "Client Commission": commission, "Broker Commission": -commission if commission is not None else None,
            "Client Swap": swap, "Broker Swap": -swap if swap is not None else None,
            "Client PnL Before Posted Charges": before,
            "Broker PnL Before Posted Charges": (0.0 if broker is not None else None) if book_mode == "A-Book" else (-before if before is not None else None)}


def apply_book_mode(frame, book_mode):
    frame = frame.copy()
    for index, row in frame.iterrows():
        views = pnl_views(row['Net PnL'], row['Client Commission'], row['Client Swap'], book_mode)
        for key, value in views.items():
            frame.loc[index, key] = value
        frame.loc[index, 'Broker Result'] = b_book_result(-views['Broker PnL']) if views['Broker PnL'] is not None else 'UNAVAILABLE'
    frame['Book Mode'] = book_mode
    if book_mode == 'A-Book':
        frame = frame.drop(columns=['B-Book Result'], errors='ignore')
    return frame


def _record_time(record, daily=False):
    value = _value(record, "Datetime", "Time") if daily else _value(record, "Time")
    if (value is None or value == 0) and not daily:
        milliseconds = _to_float(_value(record, "TimeMsc"))
        return milliseconds / 1000 if milliseconds is not None else None
    if isinstance(value, datetime):
        return value.replace(tzinfo=timezone.utc).timestamp() if value.tzinfo is None else value.timestamp()
    return _to_float(value)


def _snapshot_equity(daily):
    # Do not derive an incomplete equity estimate from balance alone.
    return _pick_float(_value(daily, "Equity"), _value(daily, "ProfitEquity"))


def build_report(api, groups, opening_date, closing_date, flow_start, flow_end,
                 allow_live=False, now=None, progress=None, selected_logins=None,
                 resolved_users=None, excluded_logins=None):
    """Fresh per-run retrieval. API clients and credentials never share a cache."""
    now = now or datetime.now(timezone.utc)
    transaction_rules = load_transaction_rules()
    if opening_date > closing_date or closing_date > now.date():
        raise ValueError("Equity dates must be ordered and cannot be in the future (UTC).")
    if flow_start > flow_end and opening_date != closing_date:
        raise ValueError("Transaction start must not be after transaction end.")
    groups = sorted(set(groups))
    login_filter = set(selected_logins) if selected_logins is not None else None
    excluded_logins = set(excluded_logins or ())
    if login_filter is not None:
        if not login_filter:
            raise ValueError("Select at least one login.")
        resolved_users = resolved_users or resolve_login_scope(api, login_filter, allowed_groups=set(groups))
        if any(login not in resolved_users or str(_value(resolved_users[login], "Group")) not in groups for login in login_filter):
            raise ValueError("Selected login membership does not match the report groups.")
        groups = sorted({str(_value(resolved_users[login], "Group")) for login in login_filter})
    warnings, rows, audit, diagnostics = [], [], [], []
    excluded_new_accounts = 0
    coverage = True
    seen_tickets, login_groups = {}, {}
    from_ts = _date_bounds_utc(flow_start)[0]
    to_ts = min(_date_bounds_utc(flow_end)[1], int(now.timestamp()))
    for group_index, group in enumerate(groups):
        if progress is not None:
            progress(group_index, len(groups), group)
        local, users_by_login, daily_maps = [], {}, {}
        diagnostic = {"Group": group, "Users": 0, "Opening Daily Rows": 0,
                      "Closing Daily Rows": 0, "Deals": 0, "Accounts Included": 0,
                      "Created After Closing": 0, "Request Errors": []}
        currency, error = request_group_currency(api, group)
        if error:
            warnings.append(error)
        group_logins = [login for login in (login_filter or []) if str(_value(resolved_users[login], "Group")) == group]
        if login_filter is not None and not group_logins:
            continue
        users, error = ([resolved_users[login] for login in group_logins], None) if login_filter is not None else request_users_by_group(api, group)
        if error:
            coverage = False
            diagnostic["Request Errors"].append(error)
            warnings.append(f"Group {group}: account universe may be incomplete. {error}")
        for user in users:
            login = _to_int(_value(user, "Login"))
            if login in excluded_logins:
                continue
            user_group = _value(user, "Group")
            if user_group and str(user_group) != group:
                warnings.append(f"Group {group}: excluded login {login} returned in group {user_group}.")
                continue
            if login is not None:
                users_by_login[login] = user
        diagnostic["Users"] = len(users_by_login)
        for day in set((opening_date, closing_date)):
            start, end = _date_bounds_utc(day)
            daily, error = (_request_login_history(api, "Daily", group_logins, start, min(end, int(now.timestamp())))
                            if login_filter is not None else request_daily_by_group(api, group, start, min(end, int(now.timestamp()))))
            if error:
                diagnostic["Request Errors"].append(error)
                warnings.append(f"Group {group}, snapshot {day}: {error}")
            latest = {}
            for record in daily:
                login = _to_int(_value(record, "Login"))
                if login in excluded_logins:
                    continue
                if login_filter is not None and login not in group_logins:
                    continue
                stamp = _record_time(record, daily=True)
                record_group = _value(record, "Group")
                if record_group and str(record_group) != group and login not in users_by_login:
                    warnings.append(f"Group {group}, snapshot {day}, login {login}: historical group {record_group} differs and current membership could not be verified.")
                    continue
                if login is None or stamp is None or not start <= stamp <= min(end, now.timestamp()):
                    warnings.append(f"Group {group}, snapshot {day}: ignored row with missing/out-of-range login/time.")
                    continue
                if (login not in latest or
                        (_snapshot_equity(record) is not None and _snapshot_equity(latest[login][1]) is None) or
                        (_snapshot_equity(record) is not None) == (_snapshot_equity(latest[login][1]) is not None)
                        and stamp > latest[login][0]):
                    latest[login] = (stamp, record)
            daily_maps[day] = {login: record for login, (_, record) in latest.items()}
            if day == opening_date:
                diagnostic["Opening Daily Rows"] = len(latest)
            if day == closing_date:
                diagnostic["Closing Daily Rows"] = len(latest)
        deals, error = ((_request_login_history(api, "Deal", group_logins, from_ts, to_ts)
                        if login_filter is not None else request_deals_by_group(api, group, from_ts, to_ts))
                        if from_ts <= to_ts else ([], None))
        flows_ok = error is None
        if error:
            diagnostic["Request Errors"].append(error)
            warnings.append(f"Group {group}: transaction data unavailable; Net PnL is incomplete. {error}")
        diagnostic["Deals"] = len(deals)
        flows = defaultdict(lambda: {key: [] for key in [*FLOW_COLUMNS, "Closed P/L", *CHARGE_COLUMNS]})
        bad_logins = set()
        uncertain_columns = defaultdict(set)
        for deal in deals:
            login = _to_int(_value(deal, "Login"))
            if login in excluded_logins:
                continue
            if login_filter is not None and login not in group_logins:
                continue
            stamp = _record_time(deal)
            if login is None:
                flows_ok = False
                warnings.append(f"Group {group}: deal missing Login; flow completeness cannot be verified.")
                continue
            if stamp is None or stamp <= 0:
                bad_logins.add(login)
                uncertain_columns[login].update(FLOW_COLUMNS)
                uncertain_columns[login].update(CHARGE_COLUMNS)
                warnings.append(f"Group {group}, login {login}: deal missing time; not classified.")
                continue
            if not from_ts <= stamp < to_ts + 1:
                continue
            # A stable MT5 deal ticket prevents duplicate counting across arrays/groups.
            ticket = _to_int(_value(deal, "Deal", "Ticket"), 0)
            if ticket > 0:
                fingerprint = tuple(str(_value(deal, key, default="")) for key in
                                    ("Login", "Time", "TimeMsc", "Action", "Entry", "Profit", "Commission", "Storage", "Swap", "Comment"))
                if ticket in seen_tickets:
                    if seen_tickets[ticket] != fingerprint:
                        raise ValueError(f"Deal {ticket} has conflicting duplicate records; reconcile history before calculating PnL.")
                    continue
                seen_tickets[ticket] = fingerprint
            else:
                bad_logins.add(login)
                uncertain_columns[login].update(FLOW_COLUMNS)
                uncertain_columns[login].update(CHARGE_COLUMNS)
                warnings.append(f"Group {group}, login {login}: deal lacks a ticket; duplicate detection unavailable.")
            # Initialize historical accounts without a bare expression:
            # Streamlit magic renders a standalone dictionary as JSON.
            deal_stats = flows[login]
            try:
                rule_record = {"Login": login, "Deal": ticket, "Action": _value(deal, "Action"),
                               "Profit": _value(deal, "Profit"), "Comment": _value(deal, "Comment", default=""), "Group": group}
                category, amount = classify_balance_transaction(rule_record, transaction_rules)
            except ValueError as exc:
                bad_logins.add(login)
                # Missing amount affects its classification, not every known flow.
                action = _to_int(_value(deal, "Action"), -1)
                if action in DEAL_CREDIT_ACTIONS:
                    uncertain_columns[login].update(["Credit In", "Credit Out"])
                elif action == DEAL_BONUS:
                    uncertain_columns[login].add("Additional")
                else:
                    uncertain_columns[login].update(FLOW_COLUMNS)
                warnings.append(f"Group {group}, login {login}, deal {ticket}: {exc}")
                category, amount = "Unclassified", None
            action = _to_int(_value(deal, "Action"), -1)
            charges = deal_charges(deal)
            for key, value in charges.items():
                if value is None:
                    uncertain_columns[login].add(key)
                    warnings.append(f"Group {group}, login {login}, deal {ticket}: {key} is missing or unallocated; charge attribution is incomplete.")
                else:
                    deal_stats[key].append(value)
            if category in FLOW_COLUMNS:
                deal_stats[category].append(amount)
            if action in CLOSED_TRADE_ACTIONS and _to_int(_value(deal, "Entry")) in CLOSED_ENTRIES:
                profit = _to_float(_value(deal, "Profit"))
                if profit is not None:
                    deal_stats["Closed P/L"].append(profit)
            if action not in range(21):
                bad_logins.add(login)
                uncertain_columns[login].update(FLOW_COLUMNS)
                uncertain_columns[login].update(CHARGE_COLUMNS)
                warnings.append(f"Group {group}, login {login}, deal {ticket}: unknown action {action}; review classification.")
            matched_rules = matching_transaction_rules(rule_record, transaction_rules)
            reviewed = bool(matched_rules)
            if action == DEAL_CORRECTION and category is None and not reviewed and _to_float(_value(deal, "Profit")) != 0:
                warnings.append(f"Group {group}, login {login}, deal {ticket}: correction amount {_value(deal, 'Profit')} has no explicit credit/bonus classification; Additional and Net PnL require review. Comment: {_value(deal, 'Comment', default='')}")
                bad_logins.add(login)
                uncertain_columns[login].add("Additional")
                category, amount = "Correction requires review", None
            audit.append({"Login": login, "Group": group, "Deal": ticket or None,
                          "Time UTC": datetime.fromtimestamp(stamp, timezone.utc).isoformat(),
                          "Action": action, "Raw Amount": _to_float(_value(deal, "Profit")),
                          "Category": category or "Equity performance", "Reported Amount": amount,
                          **charges,
                          "Review": "Unresolved" if category in {"Unclassified", "Correction requires review"} else "Reviewed rule" if reviewed else "",
                          "Review Reason": "; ".join(str(rule.get("reason", "")) for rule in matched_rules),
                          "Comment": str(_value(deal, "Comment", default=""))})
        logins = set(users_by_login) | set(flows) | bad_logins
        for snapshot in daily_maps.values():
            logins.update(snapshot)
        # Recover historical accounts missing from group snapshots by login.
        if login_filter is None and (hasattr(api, "DailyRequestByLogins") or hasattr(api, "DailyRequest")):
            for day in daily_maps:
                missing = sorted(login for login in logins
                                 if _snapshot_equity(daily_maps[day].get(login)) is None)
                if not missing:
                    continue
                start, end = _date_bounds_utc(day)
                recovered, error = _request_login_history(api, "Daily", missing, start, min(end, int(now.timestamp())))
                if error:
                    warnings.append(f"Group {group}, snapshot {day}: targeted recovery failed. {error}")
                for record in recovered:
                    identifier = _to_int(_value(record, "Login"))
                    if identifier in excluded_logins:
                        continue
                    stamp = _record_time(record, daily=True)
                    if (identifier in missing and _snapshot_equity(record) is not None and
                            stamp is not None and start <= stamp <= min(end, now.timestamp())):
                        previous = daily_maps[day].get(identifier)
                        if previous is None or _snapshot_equity(previous) is None or stamp > _record_time(previous, daily=True):
                            daily_maps[day][identifier] = record
        for login in sorted(logins):
            user = users_by_login.get(login)
            registration = _to_float(_value(user, "Registration"))
            historical_evidence = login in flows or any(login in snapshot for snapshot in daily_maps.values())
            if registration is not None and registration > _date_bounds_utc(closing_date)[1] and not historical_evidence:
                # A present-day user created after this report did not belong to
                # its historical account universe; it must not poison the totals.
                excluded_new_accounts += 1
                diagnostic["Created After Closing"] += 1
                continue
            if login in login_groups:
                coverage = False
                warnings.append(f"Login {login} appeared in groups {login_groups[login]} and {group}; duplicate account excluded, review group scope.")
                continue
            login_groups[login] = group
            quality, values, sources = [], {}, {}
            account = None
            for label, day in (("Opening", opening_date), ("Closing", closing_date)):
                daily = daily_maps[day].get(login)
                equity = _snapshot_equity(daily)
                source = f"Daily {day} UTC" if equity is not None else "Missing"
                if equity is None and registration is not None and registration > _date_bounds_utc(day)[1]:
                    equity = 0.0
                    source = f"Not yet registered: {datetime.fromtimestamp(registration, timezone.utc).isoformat()}"
                if equity is None and allow_live and day == now.date():
                    if account is None:
                        account, error = request_account(api, login)
                        if error:
                            warnings.append(f"Group {group}, login {login}: {error}")
                    equity = _pick_float(_value(account, "Equity"))
                    if equity is not None:
                        source = f"Live fallback, fetched {datetime.now(timezone.utc).isoformat()}"
                        quality.append(f"{label} is a live provisional snapshot")
                if equity is None:
                    quality.append(f"Missing {label.lower()} equity for {day}")
                    warnings.append(f"Group {group}, login {login}: {label} equity missing for {day}; no historical live substitution.")
                values[f"{label} Equity"] = equity
                sources[f"{label} Source"] = source
            stats = flows[login]
            row = {"Login": login, "Name": str(_value(user, "Name", default="") or
                    _value(daily_maps[closing_date].get(login), "Name", default="")), "Group": group,
                   "Currency": currency, **values, **sources,
                   "Opening Group": str(_value(daily_maps[opening_date].get(login), "Group", default="")),
                   "Closing Group": str(_value(daily_maps[closing_date].get(login), "Group", default=""))}
            snapshot_currencies = {str(_value(daily_maps[day].get(login), "Currency")) for day in daily_maps
                                   if _value(daily_maps[day].get(login), "Currency")}
            if len(snapshot_currencies) == 1:
                row["Currency"] = snapshot_currencies.pop()
                if currency != "Unknown" and row["Currency"] != currency:
                    quality.append("Snapshot currency differs from current group currency; flow currency requires review")
            elif len(snapshot_currencies) > 1:
                row["Currency"] = "Unknown"
                quality.append("Currency changed between snapshots; conversion required")
            if row["Currency"] == "Unknown":
                quality.append("Unknown currency")
            for key in FLOW_COLUMNS:
                row[key] = math.fsum(stats[key]) if flows_ok and key not in uncertain_columns[login] else None
            row["Closed P/L"] = math.fsum(stats["Closed P/L"]) if flows_ok else None
            if not flows_ok or login in bad_logins:
                quality.append("Transaction data incomplete or requires review")
            if uncertain_columns[login]:
                quality.append("Unresolved fields: " + ", ".join(sorted(uncertain_columns[login])))
            if not coverage:
                quality.append("Group account coverage incomplete")
            row["Equity Difference"] = (values["Closing Equity"] - values["Opening Equity"]
                                         if all(value is not None for value in values.values()) else None)
            row["Net PnL"] = calculate_net_pnl(*(row[key] for key in
                ["Opening Equity", "Closing Equity", "Deposit", "Credit In", "Additional", "Withdrawal", "Credit Out"])) if all(row[k] is not None for k in SOURCE_COLUMNS) else None
            if len({str(_value(daily_maps[day].get(login), "Currency")) for day in daily_maps
                    if _value(daily_maps[day].get(login), "Currency")}) > 1:
                row["Net PnL"] = None
                row["Equity Difference"] = None
            row["B-Book Result"] = b_book_result(row["Net PnL"])
            charge_totals = {key: math.fsum(stats[key]) if flows_ok and key not in uncertain_columns[login] else None for key in CHARGE_COLUMNS}
            row.update(pnl_views(row["Net PnL"], charge_totals["Client Commission"], charge_totals["Client Swap"]))
            if any(value is None for value in charge_totals.values()):
                quality.append("Posted commission/swap attribution incomplete")
            row["Data Quality"] = "; ".join(quality) or "Complete"
            local.append(row)
        rows.extend(local)
        diagnostic["Accounts Included"] = len(local)
        diagnostic["Request Errors"] = "; ".join(diagnostic["Request Errors"])
        diagnostics.append(diagnostic)
    if progress is not None:
        progress(len(groups), len(groups), "Complete")
    frame = pd.DataFrame(rows, columns=REPORT_COLUMNS)
    if not coverage and not frame.empty:
        frame["Data Quality"] = frame["Data Quality"].map(lambda q: q if "coverage incomplete" in q else q + "; Group account coverage incomplete")
    if closing_date == now.date():
        warnings.append("The closing date is the current UTC day. End-of-day equity and transaction history are not yet final.")
        if not frame.empty:
            frame["Data Quality"] = frame["Data Quality"] + "; Current UTC day is provisional"
    expected = (opening_date + timedelta(days=1), closing_date)
    if (flow_start, flow_end) != expected:
        warnings.append("Custom transaction range differs from the interval between equity snapshots; the report may not reconcile as a period PnL.")
    return {"accounts": frame, "warnings": list(dict.fromkeys(warnings)), "audit": pd.DataFrame(audit),
            "diagnostics": pd.DataFrame(diagnostics), "excluded_new_accounts": excluded_new_accounts,
            "coverage": coverage, "opening": opening_date, "closing": closing_date,
            "flow_start": flow_start, "flow_end": flow_end, "groups": groups,
            "logins": sorted(login_filter) if login_filter is not None else None,
            "generated": datetime.now(timezone.utc).isoformat(), "aligned": (flow_start, flow_end) == expected}


def aggregate_summary(frame, coverage=True, aligned=True, complete_only=False):
    """Show known source subtotals; calculate PnL only for a coherent cohort.

    Missing values never become zero. The All Clients PnL remains unavailable
    when inputs are incomplete. A separate, explicitly scoped summary can use
    only accounts for which all seven source values have been retrieved.
    """
    summaries = []
    if frame.empty:
        return pd.DataFrame()
    grouping = frame["Currency"].where(frame["Currency"] != "Unknown", "Unknown (" + frame["Group"] + ")")
    for currency, subset in frame.groupby(grouping, dropna=False, sort=True):
        total_accounts = len(subset)
        complete_mask = subset[SOURCE_COLUMNS].apply(lambda column: column.map(lambda v: _to_float(v) is not None)).all(axis=1)
        complete_mask &= ~subset["Data Quality"].str.contains("Currency changed", regex=False)
        complete_accounts = int(complete_mask.sum())
        if complete_only:
            subset = subset.loc[complete_mask]
            if subset.empty:
                continue
        row = {"Scope": "Accounts with complete source data" if complete_only else "All Clients",
               "Currency": currency, "Accounts": len(subset), "Total Accounts": total_accounts,
               "Complete Accounts": complete_accounts, "Excluded Accounts": total_accounts - len(subset)}
        for key in SOURCE_COLUMNS:
            values = [_to_float(v) for v in subset[key]]
            known = [v for v in values if v is not None]
            row[key] = math.fsum(known) if known else None
            row[f"Missing {key}"] = len(values) - len(known)
        snapshots_complete = row["Missing Closing Equity"] == row["Missing Opening Equity"] == 0
        row["Equity Difference"] = row["Closing Equity"] - row["Opening Equity"] if snapshots_complete else None
        row["Net PnL"] = calculate_net_pnl(*(row[key] for key in
            ["Opening Equity", "Closing Equity", "Deposit", "Credit In", "Additional", "Withdrawal", "Credit Out"])) if all(row[f"Missing {k}"] == 0 for k in SOURCE_COLUMNS) else None
        if not coverage and not complete_only:
            row["Net PnL"] = None
        if subset["Data Quality"].str.contains("Currency changed", regex=False).any():
            row["Net PnL"] = None
            row["Equity Difference"] = None
        row["B-Book Result"] = b_book_result(row["Net PnL"])
        charge_totals = {}
        for key in CHARGE_COLUMNS:
            values = [_to_float(value) for value in subset[key]] if key in subset else [None] * len(subset)
            row[f"Missing {key}"] = sum(value is None for value in values)
            charge_totals[key] = math.fsum(values) if all(value is not None for value in values) else None
        modes = subset['Book Mode'].unique() if 'Book Mode' in subset else ['B-Book']
        if len(modes) != 1:
            raise ValueError("Mixed book modes cannot share a summary.")
        row['Book Mode'] = modes[0]
        row.update(pnl_views(row["Net PnL"], charge_totals["Client Commission"], charge_totals["Client Swap"], modes[0]))
        row['Broker Result'] = b_book_result(-row['Broker PnL']) if row['Broker PnL'] is not None else 'UNAVAILABLE'
        row["Data Quality"] = "Complete" if coverage and aligned and total_accounts == len(subset) and subset["Data Quality"].eq("Complete").all() else "INCOMPLETE / PROVISIONAL"
        if modes[0] == 'A-Book':
            row.pop('B-Book Result', None)
        summaries.append(row)
    return pd.DataFrame(summaries)


def reconciliation(summary):
    steps = [("Equity Difference", "Equity Difference", 1), ("Less: Deposit", "Deposit", -1),
             ("Less: Credit In", "Credit In", -1), ("Less: Additional (Bonus)", "Additional", -1),
             ("Add: Withdrawal", "Withdrawal", 1), ("Add: Credit Out", "Credit Out", 1),
             ("Net PnL", "Net PnL", 1)]
    return pd.DataFrame([{"Component": label, "Contribution": sign * summary[key] if _to_float(summary[key]) is not None else None} for label, key, sign in steps])


def _safe_export(frame):
    frame = frame.copy()
    for key in frame.select_dtypes(include=["object", "str"]).columns:
        frame[key] = frame[key].map(lambda value: "'" + value if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@")) else value)
    return frame


def csv_export(frame):
    return _safe_export(frame).to_csv(index=False, float_format="%.2f", na_rep="MISSING").encode("utf-8-sig")


def excel_export(report, summary):
    output = io.BytesIO()
    metadata = pd.DataFrame([{"Field": key, "Value": str(report[key])} for key in
                            ["opening", "closing", "flow_start", "flow_end", "groups", "logins", "generated", "aligned", "coverage"]] +
                           [{"Field": "Net PnL formula", "Value": FORMULA},
                            {"Field": "Book mode", "Value": report.get('book_mode', 'B-Book')},
                            {"Field": "Broker policy", "Value": "B-Book: -Client PnL. A-Book: -Client Commission - Client Swap; before these charges = zero. LP costs and markup income are not supplied."},
                            {"Field": "Reconciliation policy", "Value": "Compare identical account scope, currency and UTC dates. Equity PnL includes changes in floating P/L and unposted swap; Closed P/L alone need not match. Missing snapshots are not zero. Corrections require reviewed allocation. Current membership may omit deleted/moved accounts not returned by server history."},
                            {"Field": "Charge policy", "Value": "Posted deal Commission/Storage and dedicated charge postings. Already included in equity PnL; never added twice. Floating/unposted swap remains in equity PnL."}])
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        sheets = [("Accounts", report["accounts"]), ("All Clients", summary),
                            ("Transactions", report["audit"]), ("Warnings", pd.DataFrame({"Warning": report["warnings"]})),
                            ("Report Scope", metadata)]
        available = aggregate_summary(report["accounts"], report["coverage"], report["aligned"], complete_only=True)
        if not available.empty and available["Excluded Accounts"].gt(0).any():
            sheets.append(("Available Accounts", available))
        if "diagnostics" in report:
            sheets.append(("Retrieval Diagnostics", report["diagnostics"]))
        for name, frame in sheets:
            _safe_export(frame).to_excel(writer, sheet_name=name, index=False, na_rep="MISSING")
            worksheet = writer.sheets[name]
            worksheet.freeze_panes = "A2"
            worksheet.auto_filter.ref = worksheet.dimensions
            for column in worksheet.columns:
                worksheet.column_dimensions[column[0].column_letter].width = min(55, max(16, len(str(column[0].value)) + 3))
                for cell in column[1:]:
                    if isinstance(cell.value, (float, int)) and column[0].value in [*MONEY_COLUMNS, "Raw Amount", "Reported Amount"]:
                        cell.number_format = "#,##0.00;[Red]-#,##0.00"
    return output.getvalue()


