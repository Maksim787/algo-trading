import tinkoff.invest as inv
from tinkoff.invest.async_services import AsyncServices
from tinkoff.invest.async_services import AsyncMarketDataStreamManager
from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from strategy import Strategy

from utils import quotation_to_decimal


@dataclass
class Quote:
    px: Decimal
    qty: int

    def __init__(self, order: inv.Order):
        self.px = quotation_to_decimal(order.price)
        self.qty = order.quantity


@dataclass
class OrderBook:
    bids: list[Quote]
    asks: list[Quote]

    def __init__(self, orderbook: inv.OrderBook):
        self.bids = [Quote(bid) for bid in orderbook.bids]
        self.asks = [Quote(ask) for ask in orderbook.asks]


class MarketManager:
    def __init__(self, client: AsyncServices, instrument: inv.Share):
        self.client = client
        self.instrument = instrument
        self.strategy: 'Strategy' = None
        self.depth = None

        self.ob = None

        self.market_data_stream: AsyncMarketDataStreamManager = self.client.create_market_data_stream()

    def subscribe(self, strategy: 'Strategy', orderbook_depth: int):
        assert orderbook_depth in [1, 10, 20, 30, 40, 50]
        self.strategy = strategy
        self.depth = orderbook_depth
        self.market_data_stream.order_book.subscribe(instruments=[inv.OrderBookInstrument(figi=self.instrument.figi, depth=self.depth)])

    async def run_loop(self):
        # get initial orderbook
        raw_orderbook = await self.client.market_data.get_order_book(figi=self.instrument.figi, depth=self.depth)
        self.ob = OrderBook(raw_orderbook)
        self.strategy.on_orderbook_update()
        # process orderbooks from subscription
        async for marketdata in self.market_data_stream:
            raw_orderbook = marketdata.orderbook
            if raw_orderbook is not None:
                self.ob = OrderBook(raw_orderbook)
                self.strategy.on_orderbook_update()
