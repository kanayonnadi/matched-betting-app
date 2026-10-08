"""Configuration for live odds providers.

Read from environment variables so no secrets live in source:

* ``THE_ODDS_API_KEY``       - your The Odds API key (required to enable live)
* ``THE_ODDS_API_BOOKMAKERS``- comma-separated bookmaker keys, one provider each
  (e.g. ``playnow_ca,betmgm_ca_on,sportsinteraction_ca_on``)
* ``THE_ODDS_API_BOOKMAKER`` - single-bookmaker fallback for the above
* ``THE_ODDS_API_REGION``    - region, default ``us`` (use ``ca`` for Canada)
* ``THE_ODDS_API_SPORTS``    - comma-separated sport keys, e.g.
  ``icehockey_nhl,basketball_nba,soccer_epl``

STX (Ontario exchange) reads:

* ``STX_KEY_ID``            - API key id from Account -> API Keys
* ``STX_PRIVATE_KEY_PATH``  - path to the Ed25519 private key PEM (never in git)
* ``STX_BASE_URL``          - ``https://demo.stxapp.ca`` or ``https://stxapp.ca``
* ``STX_FEE_FACTOR``        - per-wager taker factor, default ``0.10`` (maker ``0.00``)
"""

import os
from dataclasses import dataclass
from typing import Optional, Tuple


@dataclass(frozen=True)
class LiveOddsConfig:
    api_key: Optional[str] = None
    bookmakers: Tuple[str, ...] = ()
    region: str = "us"
    markets: Tuple[str, ...] = ("h2h",)
    sport_keys: Tuple[str, ...] = ()

    @property
    def enabled(self) -> bool:
        return bool(self.api_key and self.bookmakers)

    @property
    def bookmaker(self) -> Optional[str]:
        return self.bookmakers[0] if self.bookmakers else None

    @classmethod
    def from_env(cls, env=None) -> "LiveOddsConfig":
        source = os.environ if env is None else env
        books = (
            source.get("THE_ODDS_API_BOOKMAKERS")
            or source.get("THE_ODDS_API_BOOKMAKER")
            or ""
        )
        sports = source.get("THE_ODDS_API_SPORTS", "")
        return cls(
            api_key=source.get("THE_ODDS_API_KEY") or None,
            bookmakers=tuple(part.strip() for part in books.split(",") if part.strip()),
            region=source.get("THE_ODDS_API_REGION", "us"),
            sport_keys=tuple(part.strip() for part in sports.split(",") if part.strip()),
        )


@dataclass(frozen=True)
class StxConfig:
    key_id: Optional[str] = None
    private_key_path: Optional[str] = None
    base_url: str = "https://demo.stxapp.ca"
    fee_factor: str = "0.10"

    @property
    def enabled(self) -> bool:
        return bool(self.key_id and self.private_key_path)

    @classmethod
    def from_env(cls, env=None) -> "StxConfig":
        source = os.environ if env is None else env
        return cls(
            key_id=source.get("STX_KEY_ID") or None,
            private_key_path=source.get("STX_PRIVATE_KEY_PATH") or None,
            base_url=source.get("STX_BASE_URL", "https://demo.stxapp.ca"),
            fee_factor=source.get("STX_FEE_FACTOR", "0.10"),
        )
