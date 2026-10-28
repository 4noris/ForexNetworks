import random
from mt5_connection import MT5ConnectionManager


def test_validated_native_totals_match_existing_financial_calculations():
    rng=random.Random(7)
    rows=[]
    for login in range(1,2001):
        row={"Login":login,"Name":str(login),"Group":"test-only","Enabled":login%3!=0}
        for field in ("Balance","Equity","Margin","Free Margin","Floating PnL","Live Floating PnL","Margin Level %"):
            row[field]=round(rng.uniform(-1000,100000),2)
        row["Credit"]=0 if login%2 else 10
        rows.append(row)
    positions=[{"Login":1}]*17
    assert MT5ConnectionManager._build_native_metrics(rows,positions)==MT5ConnectionManager._build_metrics(rows,positions)


def test_native_totals_preserve_empty_book_and_integer_financial_fields():
    assert MT5ConnectionManager._build_native_metrics([],[])==MT5ConnectionManager._build_metrics([],[])
    row={"Login":7,"Name":"test","Group":"test-only","Enabled":True,"Balance":100,"Credit":2,
         "Equity":111,"Margin":3,"Free Margin":108,"Floating PnL":9,"Live Floating PnL":9,"Margin Level %":3700}
    assert MT5ConnectionManager._build_native_metrics([row],[])==MT5ConnectionManager._build_metrics([row],[])
