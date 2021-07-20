from functools import partial
import json
import logging
import os

import trio
from trio_websocket import ConnectionClosed
from trio_websocket import serve_websocket
from trio_websocket import WebSocketConnection
from trio_websocket import WebSocketRequest

logger = logging.getLogger('server')
BUSES = {}


async def serve_outcome(request: WebSocketRequest):
    ws: WebSocketConnection = await request.accept()

    while True:
        try:
            # message = await ws.get_message()
            # message = json.dumps({
            #     "msgType": "Buses",
            #     "buses": [
            #         {"busId": "c790сс", "lat": 55.7500, "lng": 37.600, "route": "120"},
            #         {"busId": "a134aa", "lat": 55.7494, "lng": 37.621, "route": "670к"},
            #     ]
            # })

            message = json.dumps({
                "msgType": "Buses",
                "buses": [
                    {"busId": bus_id,
                     "lat": bus_data['lat'],
                     "lng": bus_data['lng'],
                     "route": bus_data['route']}
                    for bus_id, bus_data in BUSES.items()
                    if bus_data.get('lat') and bus_data.get('lng')
                ]
            })
            await ws.send_message(message)
            await trio.sleep(1)
        except ConnectionClosed:
            break


async def serve_income(request: WebSocketRequest):
    ws: WebSocketConnection = await request.accept()

    while True:
        try:
            bus_data = json.loads(await ws.get_message())
            bus_id = bus_data['busId']
            BUSES[bus_id] = bus_data
            logger.debug('%r',
                         {"busId": bus_data['busId'],
                          "lat": bus_data['lat'],
                          "lng": bus_data['lng'],
                          "route": bus_data['route']})
        except ConnectionClosed:
            break


async def main():
    async with trio.open_nursery() as nursery:
        nursery.start_soon(partial(serve_websocket, serve_outcome, '0.0.0.0', 8000, ssl_context=None))
        nursery.start_soon(partial(serve_websocket, serve_income, '0.0.0.0', 8080, ssl_context=None))


if __name__ == '__main__':
    logging.basicConfig(level=os.getenv('LOG_LEVEL', logging.DEBUG))
    trio.run(main)
