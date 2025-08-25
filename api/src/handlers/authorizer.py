"""
Lambda authorizer for API Gateway using HMAC-SHA256 signature validation
"""

import json
import logging
import os
from typing import Dict, Any

# Import our authentication utilities
from src.utils.auth import authenticate_request, AuthenticationError

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Environment variables
PSK_PARAMETER_NAME = os.environ.get('PSK_PARAMETER_NAME', '/logging/api/psk')
AWS_REGION = os.environ.get('AWS_REGION')

def generate_policy(principal_id: str, effect: str, resource: str, context: Dict[str, Any] = None) -> Dict[str, Any]:
    """
    Generate IAM policy for API Gateway authorization response
    
    Args:
        principal_id: Identifier for the principal user
        effect: Allow or Deny
        resource: API resource ARN
        context: Additional context to pass to the API
        
    Returns:
        IAM policy dictionary
    """
    auth_response = {
        'principalId': principal_id,
        'policyDocument': {
            'Version': '2012-10-17',
            'Statement': [
                {
                    'Action': 'execute-api:Invoke',
                    'Effect': effect,
                    'Resource': resource
                }
            ]
        }
    }
    
    if context:
        auth_response['context'] = context
    
    return auth_response


def lambda_handler(event: Dict[str, Any], context) -> Dict[str, Any]:
    """
    Lambda authorizer handler for API Gateway
    
    Args:
        event: API Gateway authorizer event
        context: Lambda context
        
    Returns:
        IAM policy response allowing or denying the request
    """
    try:
        print(f"AUTHORIZER: Starting authorization for {event.get('methodArn', 'unknown')}")
        
        # Extract request details from event
        method = event.get('httpMethod', '')
        path = event.get('path', '')
        headers = event.get('headers', {})
        body = event.get('body', '') or ''
        
        # For proxy integrations, the actual HTTP method may be in the requestContext
        request_context = event.get('requestContext', {})
        actual_method = request_context.get('httpMethod', method)
        
        print(f"AUTHORIZER: Event method={method}, RequestContext method={actual_method}")
        print(f"AUTHORIZER: Path={path}")
        print(f"AUTHORIZER: Full event keys: {list(event.keys())}")
        
        # Use the actual HTTP method from request context if available
        final_method = actual_method if actual_method != 'ANY' else method
        
        # Check for auth headers (API Gateway lowercases headers)
        auth_header = headers.get('Authorization', '') or headers.get('authorization', '')
        timestamp_header = headers.get('X-API-Timestamp', '') or headers.get('x-api-timestamp', '')
        
        print(f"AUTHORIZER: Final method={final_method}")
        print(f"AUTHORIZER: Auth header={auth_header}")
        print(f"AUTHORIZER: Timestamp header={timestamp_header}")
        
        # API Gateway may provide query string parameters separately
        query_string_parameters = event.get('queryStringParameters') or {}
        
        # Construct full URI with query parameters
        uri = path
        if query_string_parameters:
            query_string = '&'.join([f"{k}={v}" for k, v in query_string_parameters.items()])
            uri = f"{path}?{query_string}"
        
        print(f"AUTHORIZER: Final URI={uri}")
        print(f"AUTHORIZER: Body received: '{body}'")
        print(f"AUTHORIZER: Body length: {len(body)}")
        
        # Now restore authentication with correct method
        try:
            is_authenticated = authenticate_request(
                headers=headers,
                method=final_method,
                uri=uri,
                body=body,
                psk_parameter_name=PSK_PARAMETER_NAME,
                region=AWS_REGION
            )
            
            if is_authenticated:
                print("AUTHORIZER: Authentication successful")
                return generate_policy(
                    'authenticated-user', 
                    'Allow', 
                    event['methodArn'],
                    context={
                        'authenticated': 'true',
                        'authMethod': 'hmac-sha256'
                    }
                )
            else:
                print("AUTHORIZER: Authentication failed")
                return generate_policy('unauthenticated', 'Deny', event['methodArn'])
                
        except Exception as auth_error:
            print(f"AUTHORIZER: Auth system error: {str(auth_error)}")
            return generate_policy('auth-error', 'Deny', event['methodArn'])
            
    except Exception as e:
        logger.error(f"Unexpected error in authorizer: {str(e)}", exc_info=True)
        # Return deny policy for unexpected errors
        return generate_policy('error', 'Deny', event.get('methodArn', '*'))


# For debugging/testing purposes
if __name__ == "__main__":
    # Test event structure
    test_event = {
        "type": "REQUEST",
        "methodArn": "arn:aws:execute-api:us-east-1:123456789012:abc123/prod/GET/api/v1/health",
        "resource": "/api/v1/health",
        "path": "/api/v1/health",
        "httpMethod": "GET",
        "headers": {
            "X-API-Timestamp": "2024-01-15T10:30:00Z",
            "Authorization": "HMAC-SHA256 test-signature"
        },
        "queryStringParameters": None,
        "body": None
    }
    
    print(json.dumps(lambda_handler(test_event, None), indent=2))