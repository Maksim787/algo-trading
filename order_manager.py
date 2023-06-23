import tinkoff.invest as inv
import asyncio
from tinkoff.invest.async_services import AsyncServices
from decimal import Decimal
from enum import IntEnum, Enum, auto
from dataclasses import dataclass, field
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from strategy import Strategy

from utils import decimal_to_quotation, quotation_to_decimal
from log import Logging


class Side(IntEnum):
    BUY = 1
    SELL = -1

    @classmethod
    def from_direction(cls, direction: inv.OrderDirection):
        match direction:
            case inv.OrderDirection.ORDER_DIRECTION_BUY:
                return cls.BUY
            case inv.OrderDirection.ORDER_DIRECTION_SELL:
                return cls.SELL
            case _:
                assert False, 'Unreachable'


@dataclass
class NewOrder:
    # only limit orders are supported
    qty: int
    px: Decimal
    side: Side


@dataclass
class CancelOrder:
    order: 'Order'


class OrderStatus(Enum):
    # PENDING -> OPEN -> FILLED
    PENDING = auto()
    OPEN = auto()
    FILLED = auto()
    CANCELLED = auto()

    def is_active(self) -> bool:
        return self not in [self.FILLED, self.CANCELLED]


@dataclass
class Order:
    qty: int
    px: Decimal
    side: Side
    status: OrderStatus
    id: str | None = None


class OrderEventType(Enum):
    PENDING = auto()
    OPEN = auto()
    FILLED = auto()
    CANCELLED = auto()


@dataclass
class OrderEvent:
    event_type: OrderEventType
    order: Order


@dataclass
class Orders:
    by_side: dict[Side, list[Order]] = field(default_factory=lambda: {Side.BUY: [], Side.SELL: []})

    def __iter__(self) -> list['Orders']:
        return iter(self.get_all_orders())

    def get_all_orders(self) -> list[Order]:
        return self.by_side[Side.BUY] + self.by_side[Side.SELL]

    def is_any_pending(self) -> bool:
        return any([order.status == OrderStatus.PENDING for order in self.get_all_orders()])

    def clear_inactive_orders(self):
        for side in [Side.BUY, Side.SELL]:
            self.by_side[side] = [order for order in self.by_side[side] if order.status.is_active()]

    def create_pending_order(self, request: NewOrder) -> OrderEvent | None:
        # insert new order
        new_order = Order(
            qty=request.qty,
            px=request.px,
            side=request.side,
            status=OrderStatus.PENDING
        )
        self._insert_order(new_order)

        # return pending order event
        return OrderEvent(
            event_type=OrderEventType.PENDING,
            order=new_order
        )

    def process_pending_cancel(self, request: CancelOrder) -> OrderEvent | None:
        # update status
        request.order.status = OrderStatus.PENDING

        # return pending order event
        return OrderEvent(
            event_type=OrderEventType.PENDING,
            order=request.order
        )

    def process_post_order_response(self, response: inv.PostOrderResponse) -> OrderEvent | None:
        # find order
        side = Side.from_direction(response.direction)
        px = quotation_to_decimal(response.initial_security_price)
        qty = response.lots_requested
        order = self._find_order_by_side_px_qty(side, px, qty)

        # update status
        order.id = response.order_id
        if order.qty == response.lots_executed:
            order.status = OrderStatus.FILLED
        else:
            order.status = OrderStatus.OPEN

        # return open or filled order event
        return OrderEvent(
            event_type=OrderEventType.OPEN if order.status == OrderStatus.OPEN else OrderEventType.FILLED,
            order=order
        )

    def process_cancel_order_response(self, request: CancelOrder, response: inv.CancelOrderResponse) -> OrderEvent | None:
        order = request.order
        order.status = OrderStatus.CANCELLED

        # return cancelled order event
        return OrderEvent(
            event_type=OrderEventType.CANCELLED,
            order=order
        )

    def process_get_order(self, logger, response: inv.OrderState) -> OrderEvent | None:
        # find order
        side = Side.from_direction(response.direction)
        order_id = response.order_id
        try:
            order = self._find_order_by_id(side, order_id)
        except:
            logger.error(f'{response} was not found in orders')
            return None

        # update status
        if order.qty == response.lots_executed:
            order.status = OrderStatus.FILLED
            # return filled order event
            return OrderEvent(
                event_type=OrderEventType.FILLED,
                order=order
            )

        # no update
        return None

    def _insert_order(self, order: Order):
        side = self.by_side[order.side]
        side.append(order)
        # sort from best price to worst price
        side.sort(key=lambda order: order.px * order.side, reverse=True)

    def _find_order_by_side_px_qty(self, side: Side, px: Decimal, qty: int) -> Order:
        return next(filter(lambda order: order.px == px and order.qty == qty, self.by_side[side]))

    def _find_order_by_id(self, side: Side, order_id: int) -> Order:
        return next(filter(lambda order: order.id == order_id, self.by_side[side]))


@dataclass
class Position:
    qty: int

    def update_by_filled_order(self, order: Order):
        self.qty += order.qty * order.side


class OrderManager:
    N_ORDER_CONSUMERS = 5
    N_MS_BETWEEN_MONITOR_ORDERS = 500

    async def get_initial_position(client: AsyncServices, account_id: int, instrument: inv.Share) -> int:
        resposne = await client.operations.get_positions(account_id=account_id)
        position = 0
        for security in resposne.securities:
            if security.figi == instrument.figi:
                position = security.balance // instrument.lot
        return position

    def __init__(self, client: AsyncServices, position: int, instrument: inv.Share, account_id: str):
        self._client = client
        self._instrument = instrument
        self._account_id = account_id

        self.orders = Orders()
        self.position = Position(qty=position)

        self._strategy = None

        self._order_queue = asyncio.Queue()
        self._logger = Logging.get_logger('OrderManager')
        Logging.set_stdout_log_level('OrderManager', Logging.INFO)

        self._logger.info(f'Initial position: {position}')

    def subscribe(self, strategy: 'Strategy'):
        self._strategy = strategy

    def post_order(self, order: NewOrder):
        self._logger.info(f'Post order: {order=}')
        self._order_queue.put_nowait(order)

    def cancel_order(self, order: CancelOrder):
        self._logger.info(f'Cancel order: {order=}')
        self._order_queue.put_nowait(order)

    async def run_loop(self):
        order_consumers = [asyncio.create_task(self._order_consumer()) for _ in range(self.N_ORDER_CONSUMERS)]
        await asyncio.gather(*order_consumers, self._monitor_orders())

    async def _order_consumer(self):
        while True:
            order = await self._order_queue.get()
            if isinstance(order, NewOrder):
                await self._post_order_consumer(order)
            elif isinstance(order, CancelOrder):
                await self._cancel_order_consumer(order)

    async def _post_order_consumer(self, request: NewOrder):
        self._logger.info(f'Post order: {request=}')
        self._notify_strategy(self.orders.create_pending_order(request))
        response = await self._client.orders.post_order(
            figi=self._instrument.figi,
            quantity=request.qty,
            price=decimal_to_quotation(request.px),
            direction=inv.OrderDirection.ORDER_DIRECTION_BUY if request.side == Side.BUY else inv.OrderDirection.ORDER_DIRECTION_SELL,
            order_type=inv.OrderType.ORDER_TYPE_LIMIT,
            account_id=self._account_id
        )
        self._notify_strategy(self.orders.process_post_order_response(response))

    async def _cancel_order_consumer(self, request: CancelOrder):
        self._logger.info(f'Cancel order: {request=}')
        self._notify_strategy(self.orders.process_pending_cancel(request))
        response = await self._client.orders.cancel_order(
            account_id=self._account_id,
            order_id=request.order.id
        )
        self._notify_strategy(self.orders.process_cancel_order_response(request, response))

    async def _monitor_orders(self):
        while True:
            await asyncio.sleep(self.N_MS_BETWEEN_MONITOR_ORDERS / 100)
            for order in self.orders:
                if order.id is None or order.status == OrderStatus.FILLED:
                    continue
                order_state = await self._client.orders.get_order_state(account_id=self._account_id, order_id=order.id)
                self._logger.debug(f'order_id={order.id}: {order_state}')
                self._notify_strategy(self.orders.process_get_order(self._logger, order_state))

    def _notify_strategy(self, order_event: OrderEvent | None):
        if order_event is not None:
            # notify strategy about order event
            if order_event.event_type == OrderEventType.FILLED:
                self.position.update_by_filled_order(order_event.order)
            self._strategy.on_order_event(order_event)
            # remove filled order
            self.orders.clear_inactive_orders()
