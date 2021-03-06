import contextlib
from functools import partial
from itertools import cycle
from itertools import islice
import json
import logging
import os
import random
from typing import List

import asyncclick as click
import trio
import trio_websocket
from trio_websocket import open_websocket_url

from utils import async_suppress

logger = logging.getLogger('fake_bus')


def relaunch_on_disconnect(async_function):

    @async_suppress(trio.Cancelled)
    async def wrapped(*args, **kwargs):
        while True:
            try:
                logger.debug("%r starting", async_function.__name__)
                await async_function(*args, **kwargs)
            except (trio_websocket.ConnectionClosed, trio_websocket.HandshakeError) as ex:
                logger.error('%r %r', async_function.__name__, ex)
                await trio.sleep(1)

    return wrapped


async def consume_and_send(url, message_channel: trio.MemoryReceiveChannel):
    async with message_channel:
        await connect_and_send(url, message_channel)


@relaunch_on_disconnect
async def connect_and_send(url, message_channel: trio.MemoryReceiveChannel):
    ws: trio_websocket.WebSocketConnection
    async with open_websocket_url(url) as ws:
        async for message in message_channel:
            await ws.send_message(message)


async def run_bus(message_channel: trio.MemorySendChannel, bus_id, route, skip_coordinates=0, refresh_timeout=1):
    async with message_channel:
        coordinates = islice(cycle(route['coordinates']), skip_coordinates, None)
        bus_data = {"busId": bus_id, "route": route['name']}
        for coordinate in coordinates:
            message = json.dumps(dict(bus_data, lat=coordinate[0], lng=coordinate[1]))
            await message_channel.send(message)
            await trio.sleep(refresh_timeout)


def load_routes(directory_path='routes'):
    for filename in os.listdir(directory_path):
        if filename.endswith(".json"):
            filepath = os.path.join(directory_path, filename)
            with open(filepath, 'r', encoding='utf8') as file:
                yield json.load(file)


def generate_bus_id(route_id, bus_index, prefix):
    if prefix:
        return f"{prefix}-{route_id}-{bus_index}"
    return f"{route_id}-{bus_index}"


@click.command()
@click.option("--server", envvar='SERVER_URL', default='ws://app:8080', type=click.STRING,
              help="?????????? ??????????????")
@click.option("--routes_number", envvar='ROUTES_NUMBER', default=1, type=click.INT,
              help="???????????????????? ?????????????????? (???? ???????????? ?????? ???????????? ?? ???????????????????? route)")
@click.option("--buses_per_route", envvar='BUSES_PER_ROUTE', default=1, type=click.INT,
              help="???????????????????? ?????????????????? ???? ???????????? ????????????????")
@click.option("--websockets_number", envvar='WEBSOCKETS_NUMBER', default=1, type=click.INT,
              help="???????????????????? ???????????????? ??????-??????????????")
@click.option("--emulator_id", envvar='EMULATOR_ID', default='', type=click.STRING,
              help="?????????????? ?? busId ???? ???????????? ?????????????? ???????????????????? ?????????????????????? ??????????????????")
@click.option("--refresh_timeout", envvar='REFRESH_TIMEOUT', default=1, type=click.FLOAT,
              help="???????????????? ?? ???????????????????? ?????????????????? ?????????????? (??????.)")
@click.option('-v', '--verbose', count=True,
              help="?????????????????? ??????????????????????")
async def main(server, routes_number, buses_per_route, websockets_number, emulator_id, refresh_timeout, verbose):
    logging.basicConfig(
        level={
            1: logging.WARNING,
            2: logging.INFO,
            3: logging.DEBUG,
        }.get(verbose, os.getenv('LOG_LEVEL', logging.ERROR))
    )

    async with trio.open_nursery() as nursery:
        send_channels: List[trio.MemorySendChannel] = []
        for _ in range(websockets_number):
            send_channel: trio.MemorySendChannel
            receive_channel: trio.MemoryReceiveChannel
            send_channel, receive_channel = trio.open_memory_channel(0)
            nursery.start_soon(consume_and_send, server, receive_channel)
            send_channels.append(send_channel)

        for route in islice(load_routes(), 0, routes_number):
            for bus_index in range(buses_per_route):
                bus_id = generate_bus_id(route['name'], bus_index, emulator_id)
                nursery.start_soon(
                    partial(run_bus,
                            random.choice(send_channels),
                            bus_id, route,
                            skip_coordinates=random.randint(0, len(route['coordinates'])),
                            refresh_timeout=refresh_timeout))


if __name__ == '__main__':
    with contextlib.suppress(KeyboardInterrupt):
        main(_anyio_backend="trio")
