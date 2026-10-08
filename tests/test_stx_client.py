import base64
from datetime import datetime, timezone

import pytest

from providers import ProviderUnavailableError
from providers.stx import StxClient, StxSigner, load_private_key

TEST_KEY = """-----BEGIN PRIVATE KEY-----
MC4CAQAwBQYDK2VwBCIEIAABAgMEBQYHCAkKCwwNDg8QERITFBUWFxgZGhscHR4f
-----END PRIVATE KEY-----"""

TEST_MESSAGE_TIMESTAMP = 1700000000000
TEST_SIGNATURE = (
    "ZFJ0qEoHt8TLKbGP+UhJ77BNy/Cdf7+oqbQzwynaM5XM0Yphmq5t1YWumC+5pLaDk/"
    "xY9EG3h4brxVdodYloCA=="
)
FIXED = datetime(2026, 11, 1, 12, 0, 0, tzinfo=timezone.utc)


class FakeTransport:
    def __init__(self, payload=None, error=None):
        self.payload = payload if payload is not None else {}
        self.error = error
        self.calls = []

    def __call__(self, url, headers):
        self.calls.append((url, headers))
        if self.error is not None:
            raise self.error
        return self.payload


def test_matches_official_test_vector():
    signer = StxSigner("test-key-id", TEST_KEY)
    timestamp, signature = signer.sign_message(
        "POST", "/api/v1/me", timestamp_ms=TEST_MESSAGE_TIMESTAMP
    )
    assert timestamp == "1700000000000"
    assert signature == TEST_SIGNATURE


def test_signature_verifies_with_public_key():
    private_key = load_private_key(TEST_KEY)
    signer = StxSigner("test-key-id", private_key)
    _, signature = signer.sign_message("POST", "/api/v1/me", timestamp_ms=TEST_MESSAGE_TIMESTAMP)
    public_key = private_key.public_key()
    public_key.verify(
        base64.b64decode(signature), b"1700000000000POST/api/v1/me"
    )


def test_headers_include_required_fields():
    signer = StxSigner("key-123", TEST_KEY, clock=lambda: FIXED)
    headers = signer.headers("GET", "/api/v1/me")
    assert headers["X-STX-ACCESS-KEY"] == "key-123"
    assert headers["X-STX-ACCESS-TIMESTAMP"] == str(int(FIXED.timestamp() * 1000))
    assert set(headers) >= {
        "X-STX-ACCESS-KEY",
        "X-STX-ACCESS-TIMESTAMP",
        "X-STX-ACCESS-SIGNATURE",
        "User-Agent",
    }


def test_missing_key_id_rejected():
    with pytest.raises(ValueError):
        StxSigner("", TEST_KEY)


def test_client_signs_path_including_query_string():
    transport = FakeTransport({"markets": []})
    client = StxClient(
        "key-123", TEST_KEY, base_url="https://demo.stxapp.ca", http_get=transport, clock=lambda: FIXED
    )
    client.list_markets(sports="Baseball", limit=200)

    url, headers = transport.calls[0]
    assert url == "https://demo.stxapp.ca/api/v1/markets?sports=Baseball&limit=200"

    expected_ts, expected_sig = StxSigner("key-123", TEST_KEY).sign_message(
        "GET", "/api/v1/markets?sports=Baseball&limit=200", timestamp_ms=int(FIXED.timestamp() * 1000)
    )
    assert headers["X-STX-ACCESS-SIGNATURE"] == expected_sig
    assert headers["X-STX-ACCESS-TIMESTAMP"] == expected_ts


def test_client_returns_payload():
    transport = FakeTransport({"markets": [{"market_id": "abc"}]})
    client = StxClient("key", TEST_KEY, http_get=transport, clock=lambda: FIXED)
    assert client.get_me() == {"markets": [{"market_id": "abc"}]}


def test_transport_error_propagates():
    transport = FakeTransport(error=ProviderUnavailableError("STX rejected the request signature (401)"))
    client = StxClient("key", TEST_KEY, http_get=transport, clock=lambda: FIXED)
    with pytest.raises(ProviderUnavailableError):
        client.list_markets()
