"""
DynamoDB service layer for tenant configuration management
"""

import logging
from typing import Dict, List, Any, Optional
from botocore.exceptions import ClientError
import boto3

logger = logging.getLogger(__name__)


class TenantNotFoundError(Exception):
    """Raised when a tenant is not found in DynamoDB"""
    pass


class DynamoDBError(Exception):
    """Raised for general DynamoDB operation errors"""
    pass


class TenantService:
    """Service class for managing tenant configurations in DynamoDB"""
    
    def __init__(self, table_name: str, region: str = "us-east-1"):
        """
        Initialize the tenant service
        
        Args:
            table_name: Name of the DynamoDB table
            region: AWS region for DynamoDB
        """
        self.table_name = table_name
        self.region = region
        self._dynamodb = None
        self._table = None
    
    @property
    def dynamodb(self):
        """Lazy initialization of DynamoDB resource"""
        if self._dynamodb is None:
            self._dynamodb = boto3.resource('dynamodb', region_name=self.region)
        return self._dynamodb
    
    @property
    def table(self):
        """Lazy initialization of DynamoDB table"""
        if self._table is None:
            self._table = self.dynamodb.Table(self.table_name)
        return self._table
    
    def get_tenant(self, tenant_id: str) -> Dict[str, Any]:
        """
        Get a tenant configuration by ID
        
        Args:
            tenant_id: The tenant identifier
            
        Returns:
            Dictionary containing tenant configuration
            
        Raises:
            TenantNotFoundError: If tenant is not found
            DynamoDBError: For other DynamoDB errors
        """
        try:
            response = self.table.get_item(Key={'tenant_id': tenant_id})
            
            if 'Item' not in response:
                raise TenantNotFoundError(f"Tenant '{tenant_id}' not found")
            
            return dict(response['Item'])
            
        except ClientError as e:
            error_code = e.response['Error']['Code']
            logger.error(f"DynamoDB error getting tenant {tenant_id}: {error_code}")
            raise DynamoDBError(f"Failed to get tenant: {error_code}")
    
    def create_tenant(self, tenant_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Create a new tenant configuration
        
        Args:
            tenant_data: Dictionary containing tenant configuration
            
        Returns:
            The created tenant data
            
        Raises:
            DynamoDBError: For DynamoDB operation errors
        """
        try:
            # Add default values
            item = tenant_data.copy()
            if 'enabled' not in item:
                item['enabled'] = True
                
            self.table.put_item(
                Item=item,
                ConditionExpression='attribute_not_exists(tenant_id)'
            )
            
            logger.info(f"Created tenant: {tenant_data['tenant_id']}")
            return item
            
        except ClientError as e:
            error_code = e.response['Error']['Code']
            if error_code == 'ConditionalCheckFailedException':
                raise DynamoDBError(f"Tenant '{tenant_data['tenant_id']}' already exists")
            logger.error(f"DynamoDB error creating tenant: {error_code}")
            raise DynamoDBError(f"Failed to create tenant: {error_code}")
    
    def update_tenant(self, tenant_id: str, tenant_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Update an existing tenant configuration
        
        Args:
            tenant_id: The tenant identifier
            tenant_data: Dictionary containing updated tenant configuration
            
        Returns:
            The updated tenant data
            
        Raises:
            TenantNotFoundError: If tenant is not found
            DynamoDBError: For other DynamoDB errors
        """
        try:
            # Build update expression
            update_expr_parts = []
            expr_attr_values = {}
            expr_attr_names = {}
            
            for key, value in tenant_data.items():
                if key != 'tenant_id':  # Don't update the primary key
                    placeholder = f":{key}"
                    attr_name = f"#{key}"
                    update_expr_parts.append(f"{attr_name} = {placeholder}")
                    expr_attr_values[placeholder] = value
                    expr_attr_names[attr_name] = key
            
            if not update_expr_parts:
                raise DynamoDBError("No fields to update")
            
            update_expression = "SET " + ", ".join(update_expr_parts)
            
            response = self.table.update_item(
                Key={'tenant_id': tenant_id},
                UpdateExpression=update_expression,
                ExpressionAttributeValues=expr_attr_values,
                ExpressionAttributeNames=expr_attr_names,
                ConditionExpression='attribute_exists(tenant_id)',
                ReturnValues='ALL_NEW'
            )
            
            logger.info(f"Updated tenant: {tenant_id}")
            return dict(response['Attributes'])
            
        except ClientError as e:
            error_code = e.response['Error']['Code']
            if error_code == 'ConditionalCheckFailedException':
                raise TenantNotFoundError(f"Tenant '{tenant_id}' not found")
            logger.error(f"DynamoDB error updating tenant {tenant_id}: {error_code}")
            raise DynamoDBError(f"Failed to update tenant: {error_code}")
    
    def patch_tenant(self, tenant_id: str, patch_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Partially update a tenant configuration
        
        Args:
            tenant_id: The tenant identifier
            patch_data: Dictionary containing fields to update
            
        Returns:
            The updated tenant data
            
        Raises:
            TenantNotFoundError: If tenant is not found
            DynamoDBError: For other DynamoDB errors
        """
        return self.update_tenant(tenant_id, patch_data)
    
    def delete_tenant(self, tenant_id: str) -> bool:
        """
        Delete a tenant configuration
        
        Args:
            tenant_id: The tenant identifier
            
        Returns:
            True if tenant was deleted
            
        Raises:
            TenantNotFoundError: If tenant is not found
            DynamoDBError: For other DynamoDB errors
        """
        try:
            self.table.delete_item(
                Key={'tenant_id': tenant_id},
                ConditionExpression='attribute_exists(tenant_id)'
            )
            
            logger.info(f"Deleted tenant: {tenant_id}")
            return True
            
        except ClientError as e:
            error_code = e.response['Error']['Code']
            if error_code == 'ConditionalCheckFailedException':
                raise TenantNotFoundError(f"Tenant '{tenant_id}' not found")
            logger.error(f"DynamoDB error deleting tenant {tenant_id}: {error_code}")
            raise DynamoDBError(f"Failed to delete tenant: {error_code}")
    
    def list_tenants(self, limit: int = 50, last_key: Optional[str] = None) -> Dict[str, Any]:
        """
        List tenant configurations with pagination
        
        Args:
            limit: Maximum number of tenants to return
            last_key: Last evaluated key for pagination
            
        Returns:
            Dictionary containing tenants list and pagination info
            
        Raises:
            DynamoDBError: For DynamoDB operation errors
        """
        try:
            scan_kwargs = {
                'Limit': limit
            }
            
            if last_key:
                scan_kwargs['ExclusiveStartKey'] = {'tenant_id': last_key}
            
            response = self.table.scan(**scan_kwargs)
            
            tenants = [dict(item) for item in response.get('Items', [])]
            
            result = {
                'tenants': tenants,
                'count': len(tenants),
                'limit': limit
            }
            
            if 'LastEvaluatedKey' in response:
                result['last_key'] = response['LastEvaluatedKey']['tenant_id']
            
            return result
            
        except ClientError as e:
            error_code = e.response['Error']['Code']
            logger.error(f"DynamoDB error listing tenants: {error_code}")
            raise DynamoDBError(f"Failed to list tenants: {error_code}")
    
    def validate_tenant_config(self, tenant_id: str) -> Dict[str, Any]:
        """
        Validate a tenant configuration
        
        Args:
            tenant_id: The tenant identifier
            
        Returns:
            Dictionary containing validation results
            
        Raises:
            TenantNotFoundError: If tenant is not found
            DynamoDBError: For other DynamoDB errors
        """
        tenant = self.get_tenant(tenant_id)
        
        validation_results = {
            'tenant_id': tenant_id,
            'valid': True,
            'checks': []
        }
        
        # Required fields validation
        required_fields = [
            'log_distribution_role_arn',
            'log_group_name',
            'target_region'
        ]
        
        for field in required_fields:
            if field not in tenant or not tenant[field]:
                validation_results['valid'] = False
                validation_results['checks'].append({
                    'field': field,
                    'status': 'missing',
                    'message': f'Required field {field} is missing or empty'
                })
            else:
                validation_results['checks'].append({
                    'field': field,
                    'status': 'ok',
                    'message': f'Field {field} is present'
                })
        
        # Role ARN format validation
        role_arn = tenant.get('log_distribution_role_arn', '')
        if role_arn and not role_arn.startswith('arn:aws:iam::'):
            validation_results['valid'] = False
            validation_results['checks'].append({
                'field': 'log_distribution_role_arn',
                'status': 'invalid',
                'message': 'Role ARN format is invalid'
            })
        
        # Region validation (basic)
        target_region = tenant.get('target_region', '')
        if target_region and not target_region.replace('-', '').replace('_', '').isalnum():
            validation_results['valid'] = False
            validation_results['checks'].append({
                'field': 'target_region',
                'status': 'invalid',
                'message': 'Target region format appears invalid'
            })
        
        return validation_results