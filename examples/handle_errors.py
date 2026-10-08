"""Read the request budget and handle each kind of failure.

    ESPORTSODDS_API_KEY=... python examples/handle_errors.py

The client already retries what is safe to retry (see the README), so what reaches you here has
used up its retries or cannot be fixed by retrying.
"""

from __future__ import annotations

from esportsodds import (
    AuthenticationError,
    Client,
    NotFoundError,
    QuotaExceededError,
    RateLimitError,
    ServerError,
    ServiceUnavailableError,
    TransportError,
)


def main() -> None:
    client = Client()
    try:
        match = client.matches.get("00000000-0000-7000-8000-000000000001")
        print(match["status"])
    except NotFoundError:
        print("no such match; the id may be wrong (an unknown id is a 404, never a 400)")
    except AuthenticationError as error:
        print("the key was refused:", error.code, "- check ESPORTSODDS_API_KEY")
    except QuotaExceededError as error:
        print("out of requests until", error.reset_at, "- retrying cannot help")
    except RateLimitError as error:
        print("still rate limited after retrying; the API asked for", error.retry_after, "seconds")
    except (ServerError, ServiceUnavailableError) as error:
        print("the API is struggling; quote this id if it persists:", error.request_id)
    except TransportError as error:
        print("could not reach the API:", error)

    # The budget the API reported on the last response, whatever its status.
    info = client.last_response
    if info is not None:
        print("request id:", info.request_id)
        print(
            "left this month:", info.rate_limit.quota_remaining, "of", info.rate_limit.quota_limit
        )


if __name__ == "__main__":
    main()
