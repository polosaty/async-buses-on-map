import contextlib
from dataclasses import asdict
from dataclasses import dataclass
from functools import partial
import json
import logging
import os

import asyncclick as click
import trio
from trio_websocket import ConnectionClosed
from trio_websocket import serve_websocket
from trio_websocket import WebSocketConnection
from trio_websocket import WebSocketRequest

logger = logging.getLogger('server')
BUSES = {}


class WebsocketMessageParseError(Exception):
    def __init__(self, error, *args):
        self.error = error
        super().__init__(*args)


@dataclass
class Bus:
    lat: float
    lng: float
    busId: str
    route: str


@dataclass
class WindowBounds:
    south_lat: float = 0
    north_lat: float = -1
    west_lng: float = 0
    east_lng: float = -1

    def is_inside(self, bus: Bus):
        try:
            return (
                self.south_lat <= bus.lat <= self.north_lat
                and self.west_lng <= bus.lng <= self.east_lng
            )
        except Exception as ex:
            logger.exception('WindowBounds.is_inside %r', ex)

        return False

    def is_real(self):
        return self.south_lat < self.north_lat and self.west_lng < self.east_lng

    def update(self, south_lat, north_lat, west_lng, east_lng):
        self.south_lat = south_lat
        self.north_lat = north_lat
        self.west_lng = west_lng
        self.east_lng = east_lng


async def serve_outcome(request: WebSocketRequest):
    ws: WebSocketConnection = await request.accept()
    bounds = WindowBounds()
    with contextlib.suppress(ConnectionClosed):
        async with trio.open_nursery() as nursery:
            nursery.start_soon(listen_browser, ws, bounds)
            nursery.start_soon(talk_to_browser, ws, bounds)


async def talk_to_browser(ws: WebSocketConnection, bounds: WindowBounds):
    while True:
        if bounds.is_real():
            message_data = {
                "msgType": "Buses",
                "buses": [
                    asdict(bus)
                    for bus in BUSES.values()
                    if (bounds.is_inside(bus))
                ]
            }
            logger.debug('%r buses inside bounds', len(message_data['buses']))
            message = json.dumps(message_data)
            await ws.send_message(message)
        await trio.sleep(1)


async def listen_browser(ws: WebSocketConnection, bounds: WindowBounds):
    """Update bounds by newBounds from ws."""
    while True:
        try:
            message = json.loads(await ws.get_message())

            if not message.get("msgType"):
                logger.warning('msgType not in message: %r', message)
                raise WebsocketMessageParseError("Requires msgType specified")

            if message["msgType"] == "newBounds":
                # newBounds message example:
                #  {"msgType":"newBounds",
                #   "data":{"south_lat":55.7256116937982,"north_lat":55.77435239625299,
                #           "west_lng":37.54019737243653,"east_lng":37.65984535217286}}
                bounds.update(**message['data'])

            logger.debug('%r', message)

        except json.JSONDecodeError as ex:
            logger.warning('%r', ex)
            await ws.send_message(json.dumps({"errors": ['Requires valid JSON'], "msgType": "Errors"}))

        except WebsocketMessageParseError as ex:
            await ws.send_message(json.dumps({"errors": [ex.error], "msgType": "Errors"}))

        except ConnectionClosed:
            break


async def serve_income(request: WebSocketRequest):
    ws: WebSocketConnection = await request.accept()

    while True:
        try:
            bus_data = json.loads(await ws.get_message())

            try:
                bus = Bus(**bus_data)
                assert isinstance(bus.lng, (int, float)) and isinstance(bus.lat, (int, float))
            except (TypeError, AssertionError):
                raise WebsocketMessageParseError('Requires busId: str, lat: float, lng: float and route: str specified')

            BUSES[bus.busId] = bus
            logger.debug('%r', asdict(bus))

        except json.JSONDecodeError as ex:
            logger.warning('%r', ex)
            await ws.send_message(json.dumps({"errors": ['Requires valid JSON'], "msgType": "Errors"}))

        except WebsocketMessageParseError as ex:
            await ws.send_message(json.dumps({"errors": [ex.error], "msgType": "Errors"}))

        except ConnectionClosed:
            break


@click.command()
@click.option("--browser_port", envvar='BROWSER_PORT', default=8000, type=click.INT,
              help="порт для браузера")
@click.option("--bus_port", envvar='BUS_PORT', default=8080, type=click.INT,
              help="порт для имитатора автобусов")
@click.option('-v', '--verbose', count=True,
              help="настройка логирования")
async def main(browser_port, bus_port, verbose):
    logging.basicConfig(
        level={
            1: logging.WARNING,
            2: logging.INFO,
            3: logging.DEBUG,
        }.get(verbose, os.getenv('LOG_LEVEL', logging.ERROR))
    )

    async with trio.open_nursery() as nursery:
        nursery.start_soon(partial(serve_websocket, serve_outcome, '0.0.0.0', browser_port, ssl_context=None))
        nursery.start_soon(partial(serve_websocket, serve_income, '0.0.0.0', bus_port, ssl_context=None))


if __name__ == '__main__':
    with contextlib.suppress(KeyboardInterrupt):
        main(_anyio_backend="trio")
