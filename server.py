import contextlib
from functools import partial
import json
import logging
import os

import asyncclick as click
from pydantic import BaseModel
from pydantic import ValidationError
from pydantic import validator
import trio
from trio_websocket import ConnectionClosed
from trio_websocket import serve_websocket
from trio_websocket import WebSocketConnection
from trio_websocket import WebSocketRequest

from utils import async_suppress

logger = logging.getLogger('server')
BUSES = {}


class WebsocketMessageParseError(Exception):
    def __init__(self, error, *args):
        self.error = error
        super().__init__(*args)

    def __str__(self):
        return str(self.error)


@contextlib.asynccontextmanager
async def notify_client_about_message_format_error(ws: WebSocketConnection):
    try:
        yield
    except WebsocketMessageParseError as ex:
        await ws.send_message(json.dumps({"errors": [ex.error], "msgType": "Errors"}))


class Bus(BaseModel):
    lat: float
    lng: float
    busId: str
    route: str


class WindowBounds(BaseModel):
    south_lat: float
    north_lat: float
    west_lng: float
    east_lng: float

    @classmethod
    def make_empty(cls):
        return cls(south_lat=0, north_lat=-1, west_lng=0, east_lng=-1)

    def is_inside(self, bus: Bus):
        return (
            self.south_lat <= bus.lat <= self.north_lat
            and self.west_lng <= bus.lng <= self.east_lng
        )

    def is_real(self):
        return self.south_lat < self.north_lat and self.west_lng < self.east_lng

    @staticmethod
    def validate_new_bounds(message: dict) -> "WindowBounds":
        try:
            return WindowBoundsMessage.validate(message).data
        except ValidationError as ex:
            raise WebsocketMessageParseError(
                f"Wrong format of newBounds message: {str(ex)}"
            )

    def update(self, *, south_lat, north_lat, west_lng, east_lng):
        self.__init__(
            south_lat=south_lat,
            north_lat=north_lat,
            west_lng=west_lng,
            east_lng=east_lng
        )


class WindowBoundsMessage(BaseModel):
    msgType: str
    data: WindowBounds

    @validator("data", pre=True)
    @classmethod
    def validate_data(cls, value: dict):
        if not WindowBounds.validate(value).is_real():
            raise ValueError('newBounds.data is not real')

        unwanted_felds = value.keys() - WindowBounds.__fields__.keys()
        if unwanted_felds:
            raise ValueError(f'newBounds.data has unwanted fields {unwanted_felds!r}')

        if not WindowBounds.validate(value).is_real():
            raise ValueError('newBounds.data is not real')
        return value


@async_suppress(ConnectionClosed)
async def serve_outcome(request: WebSocketRequest):
    ws: WebSocketConnection = await request.accept()
    bounds = WindowBounds.make_empty()

    async with trio.open_nursery() as nursery:
        nursery.start_soon(listen_browser, ws, bounds)
        nursery.start_soon(talk_to_browser, ws, bounds)


async def talk_to_browser(ws: WebSocketConnection, bounds: WindowBounds):
    while True:
        if bounds.is_real():
            message_data = {
                "msgType": "Buses",
                "buses": [
                    bus.dict()
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
        async with notify_client_about_message_format_error(ws):
            try:
                message = json.loads(await ws.get_message())
            except json.JSONDecodeError as ex:
                logger.warning('%r', ex)
                raise WebsocketMessageParseError('Requires valid JSON')

            if not message.get("msgType"):
                logger.warning('msgType not in message: %r', message)
                raise WebsocketMessageParseError("Requires msgType specified")

            if message["msgType"] == "newBounds":
                # newBounds message example:
                #  {"msgType":"newBounds",
                #   "data":{"south_lat":55.7256116937982,"north_lat":55.77435239625299,
                #           "west_lng":37.54019737243653,"east_lng":37.65984535217286}}
                new_bounds = bounds.validate_new_bounds(message)
                bounds.update(
                    south_lat=new_bounds.south_lat,
                    north_lat=new_bounds.north_lat,
                    west_lng=new_bounds.west_lng,
                    east_lng=new_bounds.east_lng
                )

            logger.debug('%r', message)


@async_suppress(ConnectionClosed)
async def serve_income(request: WebSocketRequest):
    ws: WebSocketConnection = await request.accept()

    while True:
        async with notify_client_about_message_format_error(ws):
            try:
                bus_data = json.loads(await ws.get_message())
            except json.JSONDecodeError as ex:
                logger.warning('%r', ex)
                raise WebsocketMessageParseError('Requires valid JSON')

            try:
                bus = Bus(**bus_data)
            except (TypeError, AssertionError, ValidationError):
                raise WebsocketMessageParseError('Requires busId: str, lat: float, lng: float and route: str specified')

            BUSES[bus.busId] = bus
            logger.debug('%r', bus.dict())


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
    with contextlib.suppress(KeyboardInterrupt, ConnectionClosed):
        main(_anyio_backend="trio")
