import tinkoff.invest as inv
from enum import Enum, auto

from order_manager import OrderManager, NewOrder, CancelOrder, Side, OrderEvent, OrderEventType, OrderStatus
from market_manager import MarketManager, OrderBook
from utils import quotation_to_decimal
from log import Logging


class Action(Enum):
    # {BUY, SELL} -> WAIT after sending post order
    # WAIT -> {BUY, SELL} after getting cancel order response

    BUY = auto()
    SELL = auto()
    WAIT = auto()

    def get_side(self):
        match self:
            case self.BUY:
                return Side.BUY
            case self.SELL:
                return Side.SELL
            case _:
                assert False, 'Unreachable'


class Strategy:
    def __init__(self, mm: MarketManager, om: OrderManager, instrument: inv.Share):
        self.om = om
        self.mm = mm
        self.mm.subscribe(self, orderbook_depth=10)
        self.om.subscribe(self)
        self.px_step = quotation_to_decimal(instrument.min_price_increment)
        self._logger = Logging.get_logger('Strategy')
        Logging.set_stdout_log_level('Strategy', Logging.INFO)

        assert om.position.qty in [0, 1]
        self.action = Action.BUY if om.position.qty == 0 else Action.SELL

    def on_orderbook_update(self):
        self._logger.debug('OrderBook Update')
        if self.om.orders.is_any_pending():
            return
        if self.action == Action.WAIT:
            # cancel order that is not on the best bid/ask
            self.possible_cancel_action()
        else:
            # create new order
            self.buy_or_sell_action()

    def on_order_event(self, order_event: OrderEvent):
        self._logger.info(f'Order event: {order_event}. Position: {self.om.position}. Orders: {self.om.orders}')
        if order_event.event_type in [OrderEventType.FILLED, OrderEventType.CANCELLED]:
            self.buy_or_sell_action()

    def buy_or_sell_action(self):
        self.action = Action.BUY if self.om.position.qty == 0 else Action.SELL
        ob = self.mm.ob
        side = self.action.get_side()
        new_order = NewOrder(
            qty=1,
            px=ob.bids[0].px if side == Side.BUY else ob.asks[0].px,
            side=side
        )
        self._logger.info(f'Create new order: {new_order}')
        self.om.post_order(new_order)
        self.action = Action.WAIT

    def possible_cancel_action(self):
        orders = self.om.orders.get_all_orders()
        assert len(orders) == 1, orders
        order = orders[0]
        ob_px = self.mm.ob.bids[0].px if order.side == Side.BUY else self.mm.ob.asks[0].px
        if order.side * order.px < order.side * ob_px:
            self._logger.info(f'Cancel {order}: ob_px={ob_px}')
            self.om.cancel_order(CancelOrder(orders[0]))
