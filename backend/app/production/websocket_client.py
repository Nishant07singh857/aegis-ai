"""DORMANT / MOCK — production OBS websocket client (not a real client).

Honest status (inspection H9): this class has NO real OBS websocket
transport — ``ws`` is always ``None`` and no command is ever sent
anywhere. The previous implementation lied about that: ``connect()``
set ``is_obs_connected = True`` after a 0.1s sleep and
``send_command()`` returned ``True`` without sending anything.

That fabrication is removed:

- ``connect()`` returns ``False`` and leaves
  ``state.is_obs_connected = False`` — there is nothing to connect to.
- ``send_command()`` raises ``ConnectionError`` — no command was, or
  can be, sent by this dormant mock.

The canonical OBS client for this product is
``app.broadcast.obs_client.OBSClient``. Do not wire this mock into a
live path.
"""

from .structured_logger import get_production_logger

logger = get_production_logger("websocket")


class OBSWebsocketClient:
    """Async wrapper for OBS Websocket v5 — DORMANT MOCK, not connected.

    See module docstring: no real transport exists behind this class.
    """

    #: This client is a dormant mock; there is no real OBS transport.
    DORMANT_MOCK = True

    def __init__(self, state):
        self.state = state
        self.ws = None  # No real transport is implemented (dormant mock).

    async def connect(self) -> bool:
        """Report honestly that no connection was made.

        Returns:
            False always — this dormant mock has no real OBS websocket
            transport, so ``is_obs_connected`` stays False.
        """
        self.state.is_obs_connected = False
        logger.warning(
            "OBSWebsocketClient is a dormant mock with no real OBS "
            "transport; connect() did NOT connect (is_obs_connected=False). "
            "Use app.broadcast.obs_client.OBSClient for real OBS work."
        )
        return False

    async def send_command(self, request_type: str, request_data: dict = None):
        """Fail honestly — no command can be sent by this mock.

        Raises:
            ConnectionError: always, because there is no real websocket
                transport behind this dormant client (``ws is None``),
                regardless of any connected flag.
        """
        raise ConnectionError(
            "OBS Offline: OBSWebsocketClient is a dormant mock with no "
            f"real websocket transport; command {request_type!r} was NOT sent."
        )
