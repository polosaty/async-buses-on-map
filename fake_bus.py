import contextlib
from itertools import cycle
from itertools import islice
import json
import logging
import os
import random
from sys import stderr
from typing import List, Tuple

import trio
import trio_websocket
from trio_websocket import open_websocket_url

logger = logging.getLogger('fake_bus')


async def consume_and_send(url, messages_channel: trio.MemoryReceiveChannel):
    try:
        async with messages_channel:
            async with open_websocket_url(url) as ws:
                async for message in messages_channel:
                    try:
                        await ws.send_message(message)
                    except trio.Cancelled:
                        break

    except OSError as ose:
        logger.error('Connection attempt failed: %s', ose)


async def run_bus(messages_channel: trio.MemorySendChannel, bus_id, route, skip_coordinates=0):
    async with messages_channel:
        coordinates = islice(cycle(route['coordinates']), skip_coordinates, None)
        bus_data = {"busId": bus_id, "route": route['name']}
        for coordinate in coordinates:
            try:
                message = json.dumps(dict(bus_data, lat=coordinate[0], lng=coordinate[1]))
                await messages_channel.send(message)
                await trio.sleep(1)
            # await trio.sleep(0.1)
            except trio.Cancelled:
                break

def load_routes(directory_path='routes'):
    for filename in os.listdir(directory_path):
        if filename.endswith(".json"):
            filepath = os.path.join(directory_path, filename)
            with open(filepath, 'r', encoding='utf8') as file:
                yield json.load(file)


def make_uniq_bus_id(existing_ids, route_name):
    i = 0
    bus_id = f"{route_name}-{i}"
    while bus_id in existing_ids:
        bus_id = f"{route_name}-{i}"
        i += 1

    existing_ids.add(bus_id)
    return bus_id


def generate_bus_id(route_id, bus_index):
    return f"{route_id}-{bus_index}"


async def main():
    url = os.getenv('SERVER_URL', 'ws://app:8080')
    async with trio.open_nursery() as nursery:
        send_channels: List[trio.MemorySendChannel] = []
        for _ in range(10):
            send_channel: trio.MemorySendChannel
            receive_channel: trio.MemoryReceiveChannel
            send_channel, receive_channel = trio.open_memory_channel(0)
            nursery.start_soon(consume_and_send, url, receive_channel)
            send_channels.append(send_channel)

        for route in load_routes():
            for bus_index in range(20000 // 500):
                bus_id = generate_bus_id(route['name'], bus_index)
                nursery.start_soon(run_bus,
                                   random.choice(send_channels),
                                   bus_id, route, random.randint(0, len(route['coordinates'])))

            # break

if __name__ == '__main__':
    logging.basicConfig(level=os.getenv('LOG_LEVEL', logging.DEBUG))
    with contextlib.suppress(KeyboardInterrupt):
        trio.run(main)
