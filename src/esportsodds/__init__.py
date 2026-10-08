"""Python client for the esportsodds.gg CS2 (Counter-Strike 2) match data and stats API.

    from esportsodds import Client

    client = Client()  # reads ESPORTSODDS_API_KEY
    for match in client.matches.iter(status="completed", limit=100, max_pages=1):
        print(match["team_a_name"], match["score_a"], match["score_b"], match["team_b_name"])

Responses are plain dicts as the API sends them; list calls return a `Page`. The package has no
dependencies. See the README for pagination, the bulk export, the change feed, errors and retries.
"""

import logging

from ._client import Client, RateLimit, ResponseInfo
from ._errors import (
    ApiError,
    AuthenticationError,
    ConflictError,
    EsportsOddsError,
    InvalidCursorError,
    InvalidRequestError,
    InvalidResponseError,
    NotFoundError,
    PermissionDeniedError,
    QuotaExceededError,
    RateLimitError,
    ServerError,
    ServiceUnavailableError,
    TransportError,
)
from ._pagination import Page
from ._time import parse_rfc3339
from ._transport import Transport, TransportRequest, TransportResponse, UrllibTransport
from ._version import __version__

# A library must not configure logging, only offer it: retries are logged to the "esportsodds"
# logger (never with the key), and this keeps Python from printing them when the app set up none.
logging.getLogger("esportsodds").addHandler(logging.NullHandler())

__all__ = [
    "ApiError",
    "AuthenticationError",
    "Client",
    "ConflictError",
    "EsportsOddsError",
    "InvalidCursorError",
    "InvalidRequestError",
    "InvalidResponseError",
    "NotFoundError",
    "Page",
    "PermissionDeniedError",
    "QuotaExceededError",
    "RateLimit",
    "RateLimitError",
    "ResponseInfo",
    "ServerError",
    "ServiceUnavailableError",
    "Transport",
    "TransportError",
    "TransportRequest",
    "TransportResponse",
    "UrllibTransport",
    "__version__",
    "parse_rfc3339",
]
