import asyncio
import yaml
import tinkoff.invest as inv
from tinkoff.invest.async_services import AsyncServices

from market_manager import MarketManager
from order_manager import OrderManager
from strategy import Strategy
from log import Logging


async def main():
    with open('keys.yaml') as f:
        keys = yaml.safe_load(f)
    token = keys['token']
    account_id = str(keys['account_id'])
    ticker = keys['ticker']

    Logging.set_log_directory('logs/')
    logger = Logging.get_logger('main')

    async with inv.AsyncClient(token=token) as client:
        client: AsyncServices
        logger.info('Get instrument')
        instrument = (await client.instruments.share_by(id_type=inv.InstrumentIdType.INSTRUMENT_ID_TYPE_TICKER, class_code='TQBR', id=ticker)).instrument
        logger.info('Construct MarketManager')
        mm = MarketManager(client, instrument)
        logger.info('Construct OrderManager')
        position = await OrderManager.get_initial_position(client, account_id, instrument)
        om = OrderManager(client, position, instrument, account_id)
        logger.info('Construct Strategy')
        strat = Strategy(mm, om, instrument)
        logger.info('Run Loop')

        workers = [mm.run_loop(), om.run_loop()]
        try:
            await asyncio.gather(*workers)
        except KeyboardInterrupt as ex:
            logger.info('KeyboardInterrupt in main.py')
            logger.exception(ex)
            logger.info('Normal exit via KeyboardInterrupt')
        except Exception as ex:
            logger.error('Exception in main.py')
            logger.exception(ex)
            logger.error('Error exit')
        logger.info('Final Exit')

if __name__ == '__main__':
    asyncio.run(main())
