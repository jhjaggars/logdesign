"""
Comprehensive unit tests for the Lambda authorizer with HMAC authentication
"""

import json
import pytest
import time
from datetime import datetime, timezone, timedelta
from unittest.mock import Mock, patch, MagicMock
from botocore.exceptions import ClientError

# Import the modules we're testing
from src.handlers.authorizer import lambda_handler, generate_policy
from src.utils.auth import (
    get_psk_from_ssm,
    generate_signature,
    validate_timestamp,
    validate_request_signature,
    extract_auth_headers,
    authenticate_request,
    AuthenticationError
)


class TestGeneratePolicy:
    """Test cases for IAM policy generation"""
    
    def test_generate_policy_allow(self):
        """Test generating an Allow policy"""
        policy = generate_policy(
            principal_id="test-user",
            effect="Allow",
            resource="arn:aws:execute-api:us-east-1:123456789012:abc123/prod/GET/api/v1/health"
        )
        
        assert policy["principalId"] == "test-user"
        assert policy["policyDocument"]["Version"] == "2012-10-17"
        assert len(policy["policyDocument"]["Statement"]) == 1
        
        statement = policy["policyDocument"]["Statement"][0]
        assert statement["Action"] == "execute-api:Invoke"
        assert statement["Effect"] == "Allow"
        assert statement["Resource"] == "arn:aws:execute-api:us-east-1:123456789012:abc123/prod/GET/api/v1/health"
    
    def test_generate_policy_deny(self):
        """Test generating a Deny policy"""
        policy = generate_policy(
            principal_id="unauthorized",
            effect="Deny",
            resource="*"
        )
        
        assert policy["principalId"] == "unauthorized"
        statement = policy["policyDocument"]["Statement"][0]
        assert statement["Effect"] == "Deny"
        assert statement["Resource"] == "*"
    
    def test_generate_policy_with_context(self):
        """Test generating a policy with additional context"""
        context = {
            "authenticated": "true",
            "authMethod": "hmac-sha256"
        }
        
        policy = generate_policy(
            principal_id="test-user",
            effect="Allow",
            resource="arn:aws:execute-api:us-east-1:123456789012:abc123/prod/*",
            context=context
        )
        
        assert policy["context"] == context
    
    def test_generate_policy_without_context(self):
        """Test generating a policy without context"""
        policy = generate_policy(
            principal_id="test-user",
            effect="Allow",
            resource="arn:aws:execute-api:us-east-1:123456789012:abc123/prod/*"
        )
        
        assert "context" not in policy


class TestGetPskFromSsm:
    """Test cases for PSK retrieval from SSM"""
    
    @patch('src.utils.auth.boto3.client')
    def test_get_psk_success(self, mock_boto3):
        """Test successful PSK retrieval"""
        mock_ssm = Mock()
        mock_boto3.return_value = mock_ssm
        mock_ssm.get_parameter.return_value = {
            'Parameter': {'Value': 'test-secret-key'}
        }
        
        psk = get_psk_from_ssm('/test/parameter', 'us-east-1')
        
        assert psk == 'test-secret-key'
        mock_boto3.assert_called_once_with('ssm', region_name='us-east-1')
        mock_ssm.get_parameter.assert_called_once_with(
            Name='/test/parameter',
            WithDecryption=True
        )
    
    @patch('src.utils.auth.boto3.client')
    @patch('src.utils.auth._psk_cache', {})  # Clear cache for this test
    def test_get_psk_caching(self, mock_boto3):
        """Test PSK caching functionality"""
        mock_ssm = Mock()
        mock_boto3.return_value = mock_ssm
        mock_ssm.get_parameter.return_value = {
            'Parameter': {'Value': 'test-secret-key'}
        }
        
        # First call should hit SSM
        psk1 = get_psk_from_ssm('/test/parameter', 'us-east-1')
        
        # Second call should use cache
        psk2 = get_psk_from_ssm('/test/parameter', 'us-east-1')
        
        assert psk1 == psk2 == 'test-secret-key'
        # Should only call SSM once due to caching
        assert mock_ssm.get_parameter.call_count == 1
    
    @patch('src.utils.auth.boto3.client')
    @patch('src.utils.auth.time.time')
    @patch('src.utils.auth._psk_cache', {})  # Clear cache for this test
    def test_get_psk_cache_expiry(self, mock_time, mock_boto3):
        """Test PSK cache expiry"""
        mock_ssm = Mock()
        mock_boto3.return_value = mock_ssm
        mock_ssm.get_parameter.return_value = {
            'Parameter': {'Value': 'test-secret-key'}
        }
        
        # First call at time 0
        mock_time.return_value = 0
        psk1 = get_psk_from_ssm('/test/parameter', 'us-east-1')
        
        # Second call after cache expiry (301 seconds)
        mock_time.return_value = 301
        psk2 = get_psk_from_ssm('/test/parameter', 'us-east-1')
        
        assert psk1 == psk2 == 'test-secret-key'
        # Should call SSM twice due to cache expiry
        assert mock_ssm.get_parameter.call_count == 2
    
    @patch('src.utils.auth.boto3.client')
    def test_get_psk_ssm_error(self, mock_boto3):
        """Test PSK retrieval when SSM fails"""
        mock_ssm = Mock()
        mock_boto3.return_value = mock_ssm
        mock_ssm.get_parameter.side_effect = ClientError(
            error_response={'Error': {'Code': 'ParameterNotFound'}},
            operation_name='GetParameter'
        )
        
        with pytest.raises(AuthenticationError, match="Failed to retrieve authentication key"):
            get_psk_from_ssm('/nonexistent/parameter', 'us-east-1')


class TestGenerateSignature:
    """Test cases for HMAC signature generation"""
    
    def test_generate_signature_with_body(self):
        """Test signature generation including body hash"""
        psk = "secret-key"
        method = "POST"
        uri = "/api/v1/tenants"
        timestamp = "2024-01-15T10:30:00Z"
        body = '{"tenant_id": "test"}'
        
        signature = generate_signature(psk, method, uri, timestamp, body, include_body=True)
        
        # Signature should be a 64-character hex string (SHA256)
        assert len(signature) == 64
        assert all(c in '0123456789abcdef' for c in signature)
    
    def test_generate_signature_without_body(self):
        """Test signature generation excluding body hash"""
        psk = "secret-key"
        method = "GET"
        uri = "/api/v1/tenants"
        timestamp = "2024-01-15T10:30:00Z"
        body = ""
        
        signature = generate_signature(psk, method, uri, timestamp, body, include_body=False)
        
        # Signature should be a 64-character hex string (SHA256)
        assert len(signature) == 64
        assert all(c in '0123456789abcdef' for c in signature)
    
    def test_generate_signature_consistency(self):
        """Test that same inputs produce same signature"""
        psk = "secret-key"
        method = "GET"
        uri = "/api/v1/health"
        timestamp = "2024-01-15T10:30:00Z"
        
        sig1 = generate_signature(psk, method, uri, timestamp, "", include_body=False)
        sig2 = generate_signature(psk, method, uri, timestamp, "", include_body=False)
        
        assert sig1 == sig2
    
    def test_generate_signature_different_methods(self):
        """Test that different methods produce different signatures"""
        psk = "secret-key"
        uri = "/api/v1/health"
        timestamp = "2024-01-15T10:30:00Z"
        
        get_sig = generate_signature(psk, "GET", uri, timestamp, "", include_body=False)
        post_sig = generate_signature(psk, "POST", uri, timestamp, "", include_body=False)
        
        assert get_sig != post_sig
    
    def test_generate_signature_case_insensitive_method(self):
        """Test that method is normalized to uppercase"""
        psk = "secret-key"
        uri = "/api/v1/health"
        timestamp = "2024-01-15T10:30:00Z"
        
        upper_sig = generate_signature(psk, "GET", uri, timestamp, "", include_body=False)
        lower_sig = generate_signature(psk, "get", uri, timestamp, "", include_body=False)
        
        assert upper_sig == lower_sig


class TestValidateTimestamp:
    """Test cases for timestamp validation"""
    
    def test_validate_timestamp_current(self):
        """Test validation of current timestamp"""
        current_time = datetime.now(timezone.utc)
        timestamp_str = current_time.isoformat().replace('+00:00', 'Z')
        
        assert validate_timestamp(timestamp_str) is True
    
    def test_validate_timestamp_within_window(self):
        """Test validation of timestamp within acceptable window"""
        # 2 minutes ago
        past_time = datetime.now(timezone.utc) - timedelta(minutes=2)
        timestamp_str = past_time.isoformat().replace('+00:00', 'Z')
        
        assert validate_timestamp(timestamp_str) is True
    
    def test_validate_timestamp_expired(self):
        """Test validation of expired timestamp"""
        # 10 minutes ago (beyond default 5-minute window)
        expired_time = datetime.now(timezone.utc) - timedelta(minutes=10)
        timestamp_str = expired_time.isoformat().replace('+00:00', 'Z')
        
        assert validate_timestamp(timestamp_str) is False
    
    def test_validate_timestamp_future(self):
        """Test validation of future timestamp"""
        # 10 minutes in the future
        future_time = datetime.now(timezone.utc) + timedelta(minutes=10)
        timestamp_str = future_time.isoformat().replace('+00:00', 'Z')
        
        assert validate_timestamp(timestamp_str) is False
    
    def test_validate_timestamp_custom_window(self):
        """Test validation with custom time window"""
        # 8 minutes ago
        past_time = datetime.now(timezone.utc) - timedelta(minutes=8)
        timestamp_str = past_time.isoformat().replace('+00:00', 'Z')
        
        # Should fail with default 5-minute window
        assert validate_timestamp(timestamp_str) is False
        
        # Should pass with 10-minute window
        assert validate_timestamp(timestamp_str, max_age_seconds=600) is True
    
    def test_validate_timestamp_invalid_format(self):
        """Test validation of malformed timestamp"""
        invalid_timestamps = [
            "not-a-timestamp",
            "2024-13-45T25:70:80Z",
            "",
            None
        ]
        
        for invalid_ts in invalid_timestamps:
            if invalid_ts is not None:
                assert validate_timestamp(invalid_ts) is False
    
    def test_validate_timestamp_no_timezone(self):
        """Test validation of timestamp without timezone (should default to UTC)"""
        current_time = datetime.now(timezone.utc)
        timestamp_str = current_time.strftime('%Y-%m-%dT%H:%M:%S')  # No timezone
        
        assert validate_timestamp(timestamp_str) is True


class TestValidateRequestSignature:
    """Test cases for request signature validation"""
    
    def test_validate_signature_success(self):
        """Test successful signature validation"""
        psk = "secret-key"
        method = "GET"
        uri = "/api/v1/health"
        timestamp = "2024-01-15T10:30:00Z"
        body = ""
        
        # Generate expected signature
        expected_signature = generate_signature(psk, method, uri, timestamp, body, include_body=False)
        
        # Validate should return True
        assert validate_request_signature(
            psk, method, uri, timestamp, expected_signature, body, include_body=False
        ) is True
    
    def test_validate_signature_failure(self):
        """Test signature validation failure"""
        psk = "secret-key"
        method = "GET"
        uri = "/api/v1/health"
        timestamp = "2024-01-15T10:30:00Z"
        body = ""
        
        wrong_signature = "0123456789abcdef" * 4  # 64-char hex string but wrong
        
        assert validate_request_signature(
            psk, method, uri, timestamp, wrong_signature, body, include_body=False
        ) is False
    
    def test_validate_signature_with_body(self):
        """Test signature validation with body included"""
        psk = "secret-key"
        method = "POST"
        uri = "/api/v1/tenants"
        timestamp = "2024-01-15T10:30:00Z"
        body = '{"tenant_id": "test"}'
        
        # Generate expected signature with body
        expected_signature = generate_signature(psk, method, uri, timestamp, body, include_body=True)
        
        assert validate_request_signature(
            psk, method, uri, timestamp, expected_signature, body, include_body=True
        ) is True
    
    def test_validate_signature_wrong_psk(self):
        """Test signature validation with wrong PSK"""
        psk = "secret-key"
        wrong_psk = "wrong-key"
        method = "GET"
        uri = "/api/v1/health"
        timestamp = "2024-01-15T10:30:00Z"
        
        # Generate signature with correct PSK
        signature = generate_signature(psk, method, uri, timestamp, "", include_body=False)
        
        # Validate with wrong PSK should fail
        assert validate_request_signature(
            wrong_psk, method, uri, timestamp, signature, "", include_body=False
        ) is False


class TestExtractAuthHeaders:
    """Test cases for authentication header extraction"""
    
    def test_extract_auth_headers_success(self):
        """Test successful header extraction"""
        headers = {
            "Authorization": "HMAC-SHA256 abcdef1234567890",
            "X-API-Timestamp": "2024-01-15T10:30:00Z",
            "Content-Type": "application/json"
        }
        
        timestamp, signature = extract_auth_headers(headers)
        
        assert timestamp == "2024-01-15T10:30:00Z"
        assert signature == "abcdef1234567890"
    
    def test_extract_auth_headers_case_insensitive(self):
        """Test header extraction with different cases"""
        headers = {
            "authorization": "HMAC-SHA256 abcdef1234567890",
            "x-api-timestamp": "2024-01-15T10:30:00Z"
        }
        
        timestamp, signature = extract_auth_headers(headers)
        
        assert timestamp == "2024-01-15T10:30:00Z"
        assert signature == "abcdef1234567890"
    
    def test_extract_auth_headers_missing_authorization(self):
        """Test header extraction with missing Authorization header"""
        headers = {
            "X-API-Timestamp": "2024-01-15T10:30:00Z"
        }
        
        timestamp, signature = extract_auth_headers(headers)
        
        assert timestamp == "2024-01-15T10:30:00Z"
        assert signature is None
    
    def test_extract_auth_headers_missing_timestamp(self):
        """Test header extraction with missing timestamp"""
        headers = {
            "Authorization": "HMAC-SHA256 abcdef1234567890"
        }
        
        timestamp, signature = extract_auth_headers(headers)
        
        assert timestamp is None
        assert signature == "abcdef1234567890"
    
    def test_extract_auth_headers_wrong_auth_format(self):
        """Test header extraction with wrong Authorization format"""
        headers = {
            "Authorization": "Bearer some-jwt-token",
            "X-API-Timestamp": "2024-01-15T10:30:00Z"
        }
        
        timestamp, signature = extract_auth_headers(headers)
        
        assert timestamp == "2024-01-15T10:30:00Z"
        assert signature is None
    
    def test_extract_auth_headers_empty(self):
        """Test header extraction with empty headers"""
        headers = {}
        
        timestamp, signature = extract_auth_headers(headers)
        
        assert timestamp is None
        assert signature is None


class TestAuthenticateRequest:
    """Test cases for complete authentication workflow"""
    
    @patch('src.utils.auth.get_psk_from_ssm')
    @patch('src.utils.auth.validate_timestamp')
    @patch('src.utils.auth.validate_request_signature')
    def test_authenticate_request_success(self, mock_validate_sig, mock_validate_ts, mock_get_psk):
        """Test successful request authentication"""
        mock_get_psk.return_value = "secret-key"
        mock_validate_ts.return_value = True
        mock_validate_sig.return_value = True
        
        headers = {
            "Authorization": "HMAC-SHA256 abcdef1234567890",
            "X-API-Timestamp": "2024-01-15T10:30:00Z"
        }
        
        result = authenticate_request(
            headers=headers,
            method="GET",
            uri="/api/v1/health",
            body="",
            psk_parameter_name="/test/psk",
            region="us-east-1"
        )
        
        assert result is True
        mock_get_psk.assert_called_once_with("/test/psk", "us-east-1")
        mock_validate_ts.assert_called_once_with("2024-01-15T10:30:00Z")
        mock_validate_sig.assert_called_once()
    
    def test_authenticate_request_missing_headers(self):
        """Test authentication with missing headers"""
        headers = {
            "Content-Type": "application/json"
        }
        
        result = authenticate_request(
            headers=headers,
            method="GET",
            uri="/api/v1/health",
            body="",
            psk_parameter_name="/test/psk",
            region="us-east-1"
        )
        
        assert result is False
    
    @patch('src.utils.auth.get_psk_from_ssm')
    @patch('src.utils.auth.validate_timestamp')
    def test_authenticate_request_invalid_timestamp(self, mock_validate_ts, mock_get_psk):
        """Test authentication with invalid timestamp"""
        mock_validate_ts.return_value = False
        
        headers = {
            "Authorization": "HMAC-SHA256 abcdef1234567890",
            "X-API-Timestamp": "2024-01-15T10:30:00Z"
        }
        
        result = authenticate_request(
            headers=headers,
            method="GET",
            uri="/api/v1/health",
            body="",
            psk_parameter_name="/test/psk",
            region="us-east-1"
        )
        
        assert result is False
        # Should not call get_psk if timestamp is invalid
        mock_get_psk.assert_not_called()
    
    @patch('src.utils.auth.get_psk_from_ssm')
    @patch('src.utils.auth.validate_timestamp')
    @patch('src.utils.auth.validate_request_signature')
    def test_authenticate_request_invalid_signature(self, mock_validate_sig, mock_validate_ts, mock_get_psk):
        """Test authentication with invalid signature"""
        mock_get_psk.return_value = "secret-key"
        mock_validate_ts.return_value = True
        mock_validate_sig.return_value = False
        
        headers = {
            "Authorization": "HMAC-SHA256 wrongsignature",
            "X-API-Timestamp": "2024-01-15T10:30:00Z"
        }
        
        result = authenticate_request(
            headers=headers,
            method="GET",
            uri="/api/v1/health",
            body="",
            psk_parameter_name="/test/psk",
            region="us-east-1"
        )
        
        assert result is False
    
    @patch('src.utils.auth.get_psk_from_ssm')
    @patch('src.utils.auth.validate_timestamp')
    def test_authenticate_request_psk_error(self, mock_validate_ts, mock_get_psk):
        """Test authentication when PSK retrieval fails"""
        mock_validate_ts.return_value = True
        mock_get_psk.side_effect = AuthenticationError("Failed to retrieve PSK")
        
        headers = {
            "Authorization": "HMAC-SHA256 abcdef1234567890",
            "X-API-Timestamp": "2024-01-15T10:30:00Z"
        }
        
        with pytest.raises(AuthenticationError, match="Failed to retrieve PSK"):
            authenticate_request(
                headers=headers,
                method="GET",
                uri="/api/v1/health",
                body="",
                psk_parameter_name="/test/psk",
                region="us-east-1"
            )


class TestLambdaHandler:
    """Test cases for the Lambda authorizer handler"""
    
    @patch('src.handlers.authorizer.authenticate_request')
    def test_lambda_handler_success(self, mock_authenticate):
        """Test successful authorization"""
        mock_authenticate.return_value = True
        
        event = {
            "type": "REQUEST",
            "methodArn": "arn:aws:execute-api:us-east-1:123456789012:abc123/prod/GET/api/v1/health",
            "resource": "/api/v1/health",
            "path": "/api/v1/health",
            "httpMethod": "GET",
            "headers": {
                "Authorization": "HMAC-SHA256 abcdef1234567890",
                "X-API-Timestamp": "2024-01-15T10:30:00Z"
            },
            "queryStringParameters": None,
            "body": None,
            "requestContext": {
                "httpMethod": "GET"
            }
        }
        
        result = lambda_handler(event, None)
        
        assert result["principalId"] == "authenticated-user"
        assert result["policyDocument"]["Statement"][0]["Effect"] == "Allow"
        assert result["context"]["authenticated"] == "true"
        assert result["context"]["authMethod"] == "hmac-sha256"
        
        mock_authenticate.assert_called_once()
    
    @patch('src.handlers.authorizer.authenticate_request')
    def test_lambda_handler_failure(self, mock_authenticate):
        """Test failed authorization"""
        mock_authenticate.return_value = False
        
        event = {
            "type": "REQUEST",
            "methodArn": "arn:aws:execute-api:us-east-1:123456789012:abc123/prod/GET/api/v1/health",
            "resource": "/api/v1/health",
            "path": "/api/v1/health",
            "httpMethod": "GET",
            "headers": {
                "Authorization": "HMAC-SHA256 wrongsignature",
                "X-API-Timestamp": "2024-01-15T10:30:00Z"
            },
            "queryStringParameters": None,
            "body": None,
            "requestContext": {
                "httpMethod": "GET"
            }
        }
        
        result = lambda_handler(event, None)
        
        assert result["principalId"] == "unauthenticated"
        assert result["policyDocument"]["Statement"][0]["Effect"] == "Deny"
    
    @patch('src.handlers.authorizer.authenticate_request')
    def test_lambda_handler_auth_error(self, mock_authenticate):
        """Test authorization with authentication system error"""
        mock_authenticate.side_effect = Exception("SSM connection failed")
        
        event = {
            "type": "REQUEST",
            "methodArn": "arn:aws:execute-api:us-east-1:123456789012:abc123/prod/GET/api/v1/health",
            "resource": "/api/v1/health",
            "path": "/api/v1/health",
            "httpMethod": "GET",
            "headers": {
                "Authorization": "HMAC-SHA256 abcdef1234567890",
                "X-API-Timestamp": "2024-01-15T10:30:00Z"
            },
            "queryStringParameters": None,
            "body": None,
            "requestContext": {
                "httpMethod": "GET"
            }
        }
        
        result = lambda_handler(event, None)
        
        assert result["principalId"] == "auth-error"
        assert result["policyDocument"]["Statement"][0]["Effect"] == "Deny"
    
    def test_lambda_handler_with_query_params(self):
        """Test handler with query parameters"""
        with patch('src.handlers.authorizer.authenticate_request') as mock_authenticate:
            mock_authenticate.return_value = True
            
            event = {
                "type": "REQUEST",
                "methodArn": "arn:aws:execute-api:us-east-1:123456789012:abc123/prod/GET/api/v1/tenants",
                "resource": "/api/v1/tenants",
                "path": "/api/v1/tenants",
                "httpMethod": "GET",
                "headers": {
                    "Authorization": "HMAC-SHA256 abcdef1234567890",
                    "X-API-Timestamp": "2024-01-15T10:30:00Z"
                },
                "queryStringParameters": {
                    "limit": "10",
                    "last_key": "tenant-1"
                },
                "body": None,
                "requestContext": {
                    "httpMethod": "GET"
                }
            }
            
            result = lambda_handler(event, None)
            
            assert result["policyDocument"]["Statement"][0]["Effect"] == "Allow"
            
            # Verify authenticate_request was called with query parameters in URI
            call_args = mock_authenticate.call_args
            assert "limit=10" in call_args[1]["uri"]
            assert "last_key=tenant-1" in call_args[1]["uri"]
    
    def test_lambda_handler_with_body(self):
        """Test handler with request body"""
        with patch('src.handlers.authorizer.authenticate_request') as mock_authenticate:
            mock_authenticate.return_value = True
            
            event = {
                "type": "REQUEST",
                "methodArn": "arn:aws:execute-api:us-east-1:123456789012:abc123/prod/POST/api/v1/tenants",
                "resource": "/api/v1/tenants",
                "path": "/api/v1/tenants",
                "httpMethod": "POST",
                "headers": {
                    "Authorization": "HMAC-SHA256 abcdef1234567890",
                    "X-API-Timestamp": "2024-01-15T10:30:00Z",
                    "Content-Type": "application/json"
                },
                "queryStringParameters": None,
                "body": '{"tenant_id": "test-tenant"}',
                "requestContext": {
                    "httpMethod": "POST"
                }
            }
            
            result = lambda_handler(event, None)
            
            assert result["policyDocument"]["Statement"][0]["Effect"] == "Allow"
            
            # Verify authenticate_request was called with body
            call_args = mock_authenticate.call_args
            assert call_args[1]["body"] == '{"tenant_id": "test-tenant"}'
    
    def test_lambda_handler_case_insensitive_headers(self):
        """Test handler with lowercase headers (API Gateway normalization)"""
        with patch('src.handlers.authorizer.authenticate_request') as mock_authenticate:
            mock_authenticate.return_value = True
            
            event = {
                "type": "REQUEST",
                "methodArn": "arn:aws:execute-api:us-east-1:123456789012:abc123/prod/GET/api/v1/health",
                "resource": "/api/v1/health",
                "path": "/api/v1/health",
                "httpMethod": "GET",
                "headers": {
                    "authorization": "HMAC-SHA256 abcdef1234567890",
                    "x-api-timestamp": "2024-01-15T10:30:00Z"
                },
                "queryStringParameters": None,
                "body": None,
                "requestContext": {
                    "httpMethod": "GET"
                }
            }
            
            result = lambda_handler(event, None)
            
            assert result["policyDocument"]["Statement"][0]["Effect"] == "Allow"
    
    def test_lambda_handler_unexpected_error(self):
        """Test handler with unexpected error"""
        # Simulate an unexpected error by passing malformed event
        event = {
            # Missing required fields like methodArn
            "httpMethod": "GET"
        }
        
        result = lambda_handler(event, None)
        
        assert result["principalId"] == "error"
        assert result["policyDocument"]["Statement"][0]["Effect"] == "Deny"
        assert result["policyDocument"]["Statement"][0]["Resource"] == "*"
    
    def test_lambda_handler_method_normalization(self):
        """Test handler properly normalizes HTTP method from request context"""
        with patch('src.handlers.authorizer.authenticate_request') as mock_authenticate:
            mock_authenticate.return_value = True
            
            event = {
                "type": "REQUEST",
                "methodArn": "arn:aws:execute-api:us-east-1:123456789012:abc123/prod/ANY/api/v1/health",
                "resource": "/api/v1/health",
                "path": "/api/v1/health",
                "httpMethod": "ANY",  # Generic method
                "headers": {
                    "Authorization": "HMAC-SHA256 abcdef1234567890",
                    "X-API-Timestamp": "2024-01-15T10:30:00Z"
                },
                "queryStringParameters": None,
                "body": None,
                "requestContext": {
                    "httpMethod": "GET"  # Actual method
                }
            }
            
            result = lambda_handler(event, None)
            
            assert result["policyDocument"]["Statement"][0]["Effect"] == "Allow"
            
            # Verify authenticate_request was called with the actual method
            call_args = mock_authenticate.call_args
            assert call_args[1]["method"] == "GET"


class TestEdgeCases:
    """Test edge cases and error conditions"""
    
    def test_empty_signature(self):
        """Test handling of empty signature"""
        assert validate_request_signature(
            psk="secret-key",
            method="GET",
            uri="/api/v1/health",
            timestamp="2024-01-15T10:30:00Z",
            provided_signature="",
            body="",
            include_body=False
        ) is False
    
    def test_malformed_signature_header(self):
        """Test handling of malformed Authorization header"""
        headers = {
            "Authorization": "InvalidFormat",
            "X-API-Timestamp": "2024-01-15T10:30:00Z"
        }
        
        timestamp, signature = extract_auth_headers(headers)
        assert timestamp == "2024-01-15T10:30:00Z"
        assert signature is None
    
    def test_unicode_in_body(self):
        """Test handling of unicode characters in request body"""
        psk = "secret-key"
        method = "POST"
        uri = "/api/v1/tenants"
        timestamp = "2024-01-15T10:30:00Z"
        body = '{"tenant_id": "café-tenant", "description": "Tëst tenant with ūnicōde"}'
        
        # Should not raise an exception
        signature = generate_signature(psk, method, uri, timestamp, body, include_body=True)
        assert len(signature) == 64
        
        # Validation should work with unicode
        assert validate_request_signature(
            psk, method, uri, timestamp, signature, body, include_body=True
        ) is True
    
    def test_very_long_uri(self):
        """Test handling of very long URIs"""
        psk = "secret-key"
        method = "GET"
        # Create a very long URI with many query parameters
        long_uri = "/api/v1/tenants?" + "&".join([f"param{i}=value{i}" for i in range(100)])
        timestamp = "2024-01-15T10:30:00Z"
        
        # Should not raise an exception
        signature = generate_signature(psk, method, long_uri, timestamp, "", include_body=False)
        assert len(signature) == 64
    
    @patch('src.utils.auth.logger')
    def test_logging_on_validation_failure(self, mock_logger):
        """Test that validation failures are properly logged"""
        authenticate_request(
            headers={},  # Missing headers
            method="GET",
            uri="/api/v1/health",
            body="",
            psk_parameter_name="/test/psk",
            region="us-east-1"
        )
        
        # Should log warning about missing headers
        mock_logger.warning.assert_called_with("Missing authentication headers")