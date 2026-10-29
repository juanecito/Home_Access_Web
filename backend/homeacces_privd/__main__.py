import asyncio
import logging

from .server import main

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
asyncio.run(main())
