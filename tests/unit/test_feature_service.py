"""Unit tests for the FeatureService."""

import json
from unittest.mock import AsyncMock

import pytest

from app.core.feature_service import CustomerFeatures, FeatureService


@pytest.fixture()
def mock_redis():
    """Create a mock Redis client."""
    redis = AsyncMock()
    redis.get = AsyncMock(return_value=None)
    return redis


@pytest.fixture()
def feature_service(mock_redis):
    """Create a FeatureService with mocked dependencies."""
    return FeatureService(mock_redis)


class TestCustomerFeatures:
    """Tests for the CustomerFeatures dataclass."""

    def test_default_values(self):
        features = CustomerFeatures(customer_id="cust-001")
        assert features.customer_id == "cust-001"
        assert features.txn_count_5m == 0
        assert features.txn_count_10m == 0
        assert features.txn_count_1h == 0
        assert features.txn_count_24h == 0
        assert features.total_amount_24h == 0.0
        assert features.unique_merchants_24h == 0
        assert features.avg_amount == 0.0

    def test_custom_values(self):
        features = CustomerFeatures(
            customer_id="cust-002",
            txn_count_5m=3,
            txn_count_1h=10,
            total_amount_24h=5000.0,
            avg_amount=250.0,
        )
        assert features.txn_count_5m == 3
        assert features.txn_count_1h == 10
        assert features.total_amount_24h == 5000.0
        assert features.avg_amount == 250.0


class TestGetCustomerFeatures:
    """Tests for FeatureService.get_customer_features."""

    async def test_returns_cached_features(self, feature_service, mock_redis):
        cached_data = {
            "txn_count_5m": 5,
            "txn_count_10m": 12,
            "txn_count_1h": 30,
            "txn_count_24h": 100,
            "total_amount_24h": 25000.0,
            "unique_merchants_24h": 8,
            "avg_amount": 500.0,
        }
        mock_redis.get.return_value = json.dumps(cached_data)

        features = await feature_service.get_customer_features("cust-001")

        mock_redis.get.assert_called_once_with("features:customer:cust-001")
        assert features.customer_id == "cust-001"
        assert features.txn_count_5m == 5
        assert features.txn_count_10m == 12
        assert features.txn_count_1h == 30
        assert features.txn_count_24h == 100
        assert features.total_amount_24h == 25000.0
        assert features.unique_merchants_24h == 8
        assert features.avg_amount == 500.0

    async def test_returns_defaults_when_not_cached(self, feature_service, mock_redis):
        mock_redis.get.return_value = None

        features = await feature_service.get_customer_features("cust-new")

        assert features.customer_id == "cust-new"
        assert features.txn_count_5m == 0
        assert features.avg_amount == 0.0

    async def test_returns_defaults_on_redis_error(self, feature_service, mock_redis):
        mock_redis.get.side_effect = ConnectionError("Redis unavailable")

        features = await feature_service.get_customer_features("cust-err")

        assert features.customer_id == "cust-err"
        assert features.txn_count_5m == 0

    async def test_handles_partial_cached_data(self, feature_service, mock_redis):
        """Cached data missing some fields should use defaults for missing ones."""
        mock_redis.get.return_value = json.dumps({"txn_count_5m": 7})

        features = await feature_service.get_customer_features("cust-partial")

        assert features.txn_count_5m == 7
        assert features.txn_count_10m == 0
        assert features.avg_amount == 0.0


class TestGetMerchantRisk:
    """Tests for FeatureService.get_merchant_risk."""

    async def test_returns_cached_risk_score(self, feature_service, mock_redis):
        mock_redis.get.return_value = json.dumps({"risk_score": 75})

        score = await feature_service.get_merchant_risk("MERCH-001")

        mock_redis.get.assert_called_once_with("merchant:risk:MERCH-001")
        assert score == 75

    async def test_returns_zero_when_not_cached(self, feature_service, mock_redis):
        mock_redis.get.return_value = None

        score = await feature_service.get_merchant_risk("MERCH-NEW")

        assert score == 0

    async def test_returns_zero_on_redis_error(self, feature_service, mock_redis):
        mock_redis.get.side_effect = ConnectionError("Redis down")

        score = await feature_service.get_merchant_risk("MERCH-ERR")

        assert score == 0

    async def test_returns_zero_when_risk_score_missing(self, feature_service, mock_redis):
        mock_redis.get.return_value = json.dumps({"other_field": 42})

        score = await feature_service.get_merchant_risk("MERCH-BAD")

        assert score == 0


class TestEnrichTransaction:
    """Tests for FeatureService.enrich_transaction."""

    async def test_enriches_with_velocity_features(self, feature_service, mock_redis):
        cached = json.dumps(
            {
                "txn_count_5m": 2,
                "txn_count_10m": 5,
                "txn_count_1h": 15,
                "txn_count_24h": 40,
                "total_amount_24h": 10000.0,
                "unique_merchants_24h": 6,
                "avg_amount": 250.0,
            }
        )
        # First call is get_customer_features, second is get_merchant_risk
        mock_redis.get.side_effect = [cached, None]

        txn = {
            "customer_id": "cust-001",
            "merchant_id": "MERCH-001",
            "amount": "500.00",
        }

        result = await feature_service.enrich_transaction(txn)

        assert result["txn_count_5m"] == 2
        assert result["txn_count_10m"] == 5
        assert result["txn_count_1h"] == 15
        assert result["txn_count_24h"] == 40
        assert result["total_amount_24h"] == 10000.0
        assert result["unique_merchants_24h"] == 6

    async def test_computes_amount_vs_avg_ratio(self, feature_service, mock_redis):
        cached = json.dumps({"avg_amount": 200.0})
        mock_redis.get.side_effect = [cached, None]

        txn = {
            "customer_id": "cust-001",
            "merchant_id": "MERCH-001",
            "amount": "600.00",
        }

        result = await feature_service.enrich_transaction(txn)

        assert result["amount_vs_avg_ratio"] == 3.0  # 600 / 200

    async def test_ratio_is_1_when_no_avg(self, feature_service, mock_redis):
        mock_redis.get.side_effect = [None, None]

        txn = {
            "customer_id": "cust-001",
            "merchant_id": "MERCH-001",
            "amount": "100.00",
        }

        result = await feature_service.enrich_transaction(txn)

        assert result["amount_vs_avg_ratio"] == 1.0

    async def test_adds_merchant_risk_score(self, feature_service, mock_redis):
        mock_redis.get.side_effect = [None, json.dumps({"risk_score": 80})]

        txn = {
            "customer_id": "cust-001",
            "merchant_id": "MERCH-HIGH",
            "amount": "100.00",
        }

        result = await feature_service.enrich_transaction(txn)

        assert result["merchant_risk_score"] == 80

    async def test_no_merchant_risk_when_no_merchant_id(self, feature_service, mock_redis):
        mock_redis.get.side_effect = [None]

        txn = {
            "customer_id": "cust-001",
            "amount": "100.00",
        }

        result = await feature_service.enrich_transaction(txn)

        assert "merchant_risk_score" not in result
