import asyncio
import ccxt.async_support as ccxt
from engine.net_utils import patch_ccxt_resolver
import traceback

async def main():
    e = ccxt.okx({
        'options': {'defaultType': 'swap', 'fetchMarkets': {'types': ['swap']}}
    })
    patch_ccxt_resolver(e)
    e.set_sandbox_mode(True)
    print('Fetching markets via default okx domain...')
    try:
        await e.load_markets()
        print('Success! loaded', len(e.markets), 'markets')
    except Exception as ex:
        print('Error type:', type(ex))
        traceback.print_exc()
    finally:
        await e.close()

if __name__ == '__main__':
    asyncio.run(main())
