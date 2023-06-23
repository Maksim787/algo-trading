import tinkoff.invest as inv
from decimal import Decimal

EPS = 1e-10


def quotation_to_float(quotation: inv.Quotation) -> float:
    return quotation.units + quotation.nano / 1e9


def quotation_to_decimal(quotation: inv.Quotation | inv.MoneyValue) -> Decimal:
    return Decimal(quotation.units) + Decimal(quotation.nano) / int(1e9)


def decimal_to_quotation(value: Decimal) -> inv.Quotation:
    units = int(value)
    nano = int((value - units) * int(1e9))
    result = inv.Quotation(units=units, nano=nano)
    assert abs(float(value) - quotation_to_float(result)) < EPS
    return result
