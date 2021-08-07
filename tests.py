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
from server import WindowBounds

HOST = '127.0.0.1'
RESOURCE = '/resource'


class TestBrowserServer:
    bounds = WindowBounds()

    @pytest.fixture
    async def browser_test_server(self, nursery):
        """A server that reads one message, sends back the answer message, if needed
        then closes the connection."""
        serve_fn = partial(serve_websocket, self._test_server_handler, HOST, 0, ssl_context=None)
        server = await nursery.start(serve_fn)
        yield server

    @pytest.fixture
    async def browser_test_conn(self, browser_test_server):
        """Return a client connection instance that is connected to an test server."""
        conn: WebSocketConnection
        async with open_websocket(HOST, browser_test_server.port, RESOURCE, use_ssl=False) as conn:
            yield conn

    async def _test_server_handler(self, request):
        """Accept incoming request and then pass off to echo connection handler."""
        conn = await request.accept()
        try:
            await listen_browser(conn, self.bounds)
        except ConnectionClosed:
            pass

    async def test_client_send_and_receive(self, browser_test_conn):
        bounds = self.bounds

        await browser_test_conn.send_message('This is a test message.')
        received_msg = await browser_test_conn.get_message()
        assert received_msg == '{"errors": ["Requires valid JSON"], "msgType": "Errors"}'

        await browser_test_conn.send_message(json.dumps({'some': 'thing'}))
        received_msg = await browser_test_conn.get_message()
        assert received_msg == '{"errors": ["Requires msgType specified"], "msgType": "Errors"}'

        assert not bounds.is_real()
        bounds_message_dict = {
            "msgType": "newBounds",
            "data": {"south_lat": 55.7256116937982, "north_lat": 55.77435239625299,
                     "west_lng": 37.54019737243653, "east_lng": 37.65984535217286}}
        await browser_test_conn.send_message(json.dumps(bounds_message_dict))
        await trio.sleep(0.1)  # don't know how to wait for the message to be processed
        assert bounds.is_real()
        current_bounds = WindowBounds(**bounds_message_dict['data'])
        assert bounds == current_bounds

        bounds_message_dict = {
            "msgType": "newBounds",
            "data": {"south_lat": 55, "north_lat": 55.77435239625299,
                     "west_lng": 37.54019737243653, "east_lng": "37.65984535217286"}}
        await browser_test_conn.send_message(json.dumps(bounds_message_dict))
        received_msg = await browser_test_conn.get_message()
        assert received_msg == '{"errors": ["Wrong format of newBounds message"], "msgType": "Errors"}'
        assert bounds == current_bounds

        bounds_message_dict['data'].pop('east_lng')
        await browser_test_conn.send_message(json.dumps(bounds_message_dict))
        received_msg = await browser_test_conn.get_message()
        assert received_msg == '{"errors": ["Wrong format of newBounds message"], "msgType": "Errors"}'
        assert bounds == current_bounds

    def test_bounds_update(self):
        bounds = WindowBounds()
        with pytest.raises(TypeError) as ex:
            bounds.validate_new_bounds(south_lat=1)
        assert ex.match(r'missing 3 required keyword-only arguments')

        with pytest.raises(AssertionError) as ex:
            bounds.validate_new_bounds(**{
                "south_lat": 55.7256116937982, "north_lat": 55.77435239625299,
                "west_lng": 37.54019737243653, "east_lng": "37.65984535217286"})


class TestIncomeServer(TestBrowserServer):

    async def _test_server_handler(self, request):
        return await serve_income(request)

    async def test_client_send_and_receive(self, browser_test_conn):

        await browser_test_conn.send_message('This is a test message.')
        received_msg = json.loads(await browser_test_conn.get_message())
        assert received_msg == {"errors": ["Requires valid JSON"], "msgType": "Errors"}

        message = {'busId': 'test', 'route': 'test-route', 'lat': 'x', 'lng': 12}
        await browser_test_conn.send_message(json.dumps(message))
        received_msg = json.loads(await browser_test_conn.get_message())
        assert received_msg == {
            "errors": ["Requires busId: str, lat: float, lng: float and route: str specified"],
            "msgType": "Errors"}

        await browser_test_conn.send_message(json.dumps({'some': 'thing'}))
        received_msg = json.loads(await browser_test_conn.get_message())
        assert received_msg == {
            "errors": ["Requires busId: str, lat: float, lng: float and route: str specified"],
            "msgType": "Errors"}
