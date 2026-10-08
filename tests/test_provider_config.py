from providers import (
    LiveOddsConfig,
    MockExchangeProvider,
    MockSportsbookProvider,
    MultiSportsbookProvider,
    StxConfig,
    StxProvider,
    TheOddsApiProvider,
    build_exchange_provider,
    build_providers,
    build_sportsbook_provider,
    build_sportsbook_providers,
)

# STX's publicly documented test-vector key (not a real credential).
TEST_KEY = """-----BEGIN PRIVATE KEY-----
MC4CAQAwBQYDK2VwBCIEIAABAgMEBQYHCAkKCwwNDg8QERITFBUWFxgZGhscHR4f
-----END PRIVATE KEY-----"""


def test_config_disabled_by_default():
    config = LiveOddsConfig.from_env({})
    assert config.enabled is False
    assert config.region == "us"
    assert config.markets == ("h2h",)
    assert config.sport_keys == ()


def test_config_reads_single_and_multiple_bookmakers():
    single = LiveOddsConfig.from_env(
        {
            "THE_ODDS_API_KEY": "key",
            "THE_ODDS_API_BOOKMAKER": "draftkings",
            "THE_ODDS_API_REGION": "uk",
            "THE_ODDS_API_SPORTS": "basketball_nba, soccer_epl",
        }
    )
    assert single.enabled is True
    assert single.bookmaker == "draftkings"
    assert single.region == "uk"
    assert single.sport_keys == ("basketball_nba", "soccer_epl")

    multiple = LiveOddsConfig.from_env(
        {
            "THE_ODDS_API_KEY": "key",
            "THE_ODDS_API_BOOKMAKERS": "playnow_ca, betmgm_ca_on",
            "THE_ODDS_API_REGION": "ca",
        }
    )
    assert multiple.bookmakers == ("playnow_ca", "betmgm_ca_on")
    assert multiple.region == "ca"


def test_factory_falls_back_to_mock_without_credentials():
    providers = build_providers(LiveOddsConfig(), StxConfig())
    assert providers.live is False
    assert providers.exchange_live is False
    assert isinstance(providers.sportsbook, MockSportsbookProvider)
    assert isinstance(providers.exchange, MockExchangeProvider)


def test_factory_builds_single_live_sportsbook():
    config = LiveOddsConfig(api_key="key", bookmakers=("draftkings",), region="ca")
    provider = build_sportsbook_provider(config)
    assert isinstance(provider, TheOddsApiProvider)
    assert provider.name == "theoddsapi:draftkings"


def test_factory_combines_multiple_live_sportsbooks():
    config = LiveOddsConfig(
        api_key="key", bookmakers=("playnow_ca", "betmgm_ca_on"), region="ca"
    )
    providers = build_sportsbook_providers(config)
    assert len(providers) == 2
    combined = build_sportsbook_provider(config)
    assert isinstance(combined, MultiSportsbookProvider)


def test_exchange_falls_back_to_mock():
    assert isinstance(build_exchange_provider(StxConfig()), MockExchangeProvider)


def test_exchange_builds_stx_when_configured(tmp_path):
    pem = tmp_path / "stx.pem"
    pem.write_text(TEST_KEY)
    provider = build_exchange_provider(StxConfig(key_id="key-1", private_key_path=str(pem)))
    assert isinstance(provider, StxProvider)
    assert provider.name == "stx"
