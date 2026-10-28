"""Search the native symbol catalog without issuing MT5 requests."""


def search_symbols(catalog, query, limit):
    token = query.strip().casefold()
    matches = []
    for name, record in catalog.items():
        if not name:
            continue
        description = str(record.get("Description", "") or "")
        path = str(record.get("Path", "") or "")
        if token and token not in f"{name} {description} {path}".casefold():
            continue
        matches.append({"symbol": name, "description": description, "digits": record.get("Digits")})
    matches.sort(key=lambda row: (not row["symbol"].casefold().startswith(token), row["symbol"].casefold()))
    return {"symbols": matches[:limit], "total": len(matches)}
