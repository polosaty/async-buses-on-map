from contextlib import suppress
from functools import partial
import json

from async_generator import async_generator
from async_generator import yield_
import pytest
import trio
from trio_websocket import ConnectionClosed
from trio_websocket import open_websocket
from trio_websocket import serve_websocket
from trio_websocket import WebSocketConnection

from server import listen_browser
from server import serve_income
from server import WebsocketMessageParseError
from server import WindowBounds

HOST = '127.0.0.1'
RESOURCE = '/resource'


class TestBrowserServer:
    bounds = WindowBounds.make_empty()

    @pytest.fixture
    @async_generator
    async def browser_test_server(self, nursery):
        """A server that reads one message, sends back the answer message, if needed
        then closes the connection."""
        serve_fn = partial(serve_websocket, self._test_server_handler, HOST, 0, ssl_context=None)
        server = await nursery.start(serve_fn)
        await yield_(server)

    @pytest.fixture
    @async_generator
    async def browser_test_conn(self, browser_test_server):
        """Return a client connection instance that is connected to an test server."""

        conn: WebSocketConnection
        async with open_websocket(HOST, browser_test_server.port, RESOURCE, use_ssl=False) as conn:
            await yield_(conn)

    async def _test_server_handler(self, request):
        """Accept incoming request and then pass off to echo connection handler."""
        conn = await request.accept()
        with suppress(ConnectionClosed):
            await listen_browser(conn, self.bounds)

    async def test_client_send_and_receive(self, browser_test_conn):
        bounds = self.bounds
        async with browser_test_conn as conn:
            await conn.send_message('This is a test message.')
            received_msg = await conn.get_message()
            assert received_msg == '{"errors": ["Requires valid JSON"], "msgType": "Errors"}'

            await conn.send_message(json.dumps({'some': 'thing'}))
            received_msg = await conn.get_message()
            assert received_msg == '{"errors": ["Requires msgType specified"], "msgType": "Errors"}'

            assert not bounds.is_real()
            bounds_message_dict = {
                "msgType": "newBounds",
                "data": {"south_lat": 55.7256116937982, "north_lat": 55.77435239625299,
                         "west_lng": 37.54019737243653, "east_lng": 37.65984535217286}}
            await conn.send_message(json.dumps(bounds_message_dict))
            await trio.sleep(0.1)  # don't know how to wait for the message to be processed
            assert bounds.is_real()
            current_bounds = WindowBounds(**bounds_message_dict['data'])
            assert bounds == current_bounds

            bounds_message_dict['data'].pop('east_lng')
            await conn.send_message(json.dumps(bounds_message_dict))
            received_msg = await conn.get_message()
            assert received_msg == ('{"errors": ["Wrong format of newBounds message: '
                                    '1 validation error for WindowBoundsMessage\\n'
                                    'data -> east_lng\\n'
                                    '  field required (type=value_error.missing)"], "msgType": "Errors"}')
            assert bounds == current_bounds

            bounds_message_dict['data']['east_lng'] = 37.65984535217286
            bounds_message_dict['data']['other'] = 'other value'
            await conn.send_message(json.dumps(bounds_message_dict))
            received_msg = await conn.get_message()
            assert received_msg == (
                '{"errors": ["Wrong format of newBounds message:'
                ' 1 validation error for WindowBoundsMessage\\ndata\\n  '
                'newBounds.data has unwanted fields {\'other\'} '
                '(type=value_error)"], "msgType": "Errors"}')
            assert bounds == current_bounds

    def test_bounds_update(self):
        bounds = WindowBounds.make_empty()
        with pytest.raises(WebsocketMessageParseError) as ex:
            bounds.validate_new_bounds({"data": {"south_lat": 1, }, "msgType": "newBounds"})
        assert ex.match(r'Wrong format of newBounds message: 3 validation errors')
        assert ex.match(r'data -> west_lng\n  field required')
        assert ex.match(r'data -> east_lng\n  field required')
        assert ex.match(r'data -> north_lat\n  field required')

        with pytest.raises(WebsocketMessageParseError) as ex:
            bounds.validate_new_bounds({"data": {
                "south_lat": 55.7256116937982, "north_lat": 55.77435239625299,
                "west_lng": 37.54019737243653, "east_lng": "37.65984535217286", 'a': 12}, "msgType": "newBounds"})
        assert ex.match(r'Wrong format of newBounds message:')
        assert ex.match(r"1 validation error for WindowBoundsMessage\ndata\n  newBounds.data has unwanted fields {'a'}")


class TestIncomeServer(TestBrowserServer):

    async def _test_server_handler(self, request):
        return await serve_income(request)

    async def test_client_send_and_receive(self, browser_test_conn):
        async with browser_test_conn as conn:
            await conn.send_message('This is a test message.')
            received_msg = json.loads(await conn.get_message())
            assert received_msg == {"errors": ["Requires valid JSON"], "msgType": "Errors"}

            message = {'busId': 'test', 'route': 'test-route', 'lat': 'x', 'lng': 12}
            await conn.send_message(json.dumps(message))
            received_msg = json.loads(await conn.get_message())
            assert received_msg == {
                "errors": ["Requires busId: str, lat: float, lng: float and route: str specified"],
                "msgType": "Errors"}

            await conn.send_message(json.dumps({'some': 'thing'}))
            received_msg = json.loads(await conn.get_message())
            assert received_msg == {
                "errors": ["Requires busId: str, lat: float, lng: float and route: str specified"],
                "msgType": "Errors"}
