"""
Pydantic models for tenant data validation
"""

from typing import List, Optional
from pydantic import BaseModel, Field, field_validator


class TenantCreateRequest(BaseModel):
    """Model for tenant creation requests"""
    tenant_id: str = Field(..., min_length=1, max_length=128, description="Unique tenant identifier")
    log_distribution_role_arn: str = Field(..., min_length=1, description="ARN of the customer's IAM role for log delivery")
    log_group_name: str = Field(..., min_length=1, description="CloudWatch Logs group name for log delivery")
    target_region: str = Field(..., min_length=1, description="AWS region where logs should be delivered")
    enabled: Optional[bool] = Field(default=True, description="Enable/disable log processing for this tenant")
    desired_logs: Optional[List[str]] = Field(default=None, description="List of application names to process")
    
    @field_validator('tenant_id')
    @classmethod
    def validate_tenant_id(cls, v):
        """Validate tenant ID format"""
        if not v.replace('-', '').replace('_', '').isalnum():
            raise ValueError('tenant_id must contain only alphanumeric characters, hyphens, and underscores')
        return v
    
    @field_validator('log_distribution_role_arn')
    @classmethod
    def validate_role_arn(cls, v):
        """Validate IAM role ARN format"""
        if not v.startswith('arn:aws:iam::'):
            raise ValueError('log_distribution_role_arn must be a valid IAM role ARN')
        return v
    
    @field_validator('target_region')
    @classmethod
    def validate_region(cls, v):
        """Validate AWS region format"""
        if not v.replace('-', '').isalnum():
            raise ValueError('target_region must be a valid AWS region')
        return v
    
    @field_validator('desired_logs')
    @classmethod
    def validate_desired_logs(cls, v):
        """Validate desired_logs list"""
        if v is not None:
            if not isinstance(v, list):
                raise ValueError('desired_logs must be a list')
            if len(v) == 0:
                raise ValueError('desired_logs cannot be an empty list (use null to allow all applications)')
            for app in v:
                if not isinstance(app, str) or len(app.strip()) == 0:
                    raise ValueError('All items in desired_logs must be non-empty strings')
        return v


class TenantUpdateRequest(BaseModel):
    """Model for tenant update requests"""
    log_distribution_role_arn: Optional[str] = Field(None, min_length=1, description="ARN of the customer's IAM role for log delivery")
    log_group_name: Optional[str] = Field(None, min_length=1, description="CloudWatch Logs group name for log delivery")
    target_region: Optional[str] = Field(None, min_length=1, description="AWS region where logs should be delivered")
    enabled: Optional[bool] = Field(None, description="Enable/disable log processing for this tenant")
    desired_logs: Optional[List[str]] = Field(None, description="List of application names to process")
    
    @field_validator('log_distribution_role_arn')
    @classmethod
    def validate_role_arn(cls, v):
        """Validate IAM role ARN format"""
        if v is not None and not v.startswith('arn:aws:iam::'):
            raise ValueError('log_distribution_role_arn must be a valid IAM role ARN')
        return v
    
    @field_validator('target_region')
    @classmethod
    def validate_region(cls, v):
        """Validate AWS region format"""
        if v is not None and not v.replace('-', '').isalnum():
            raise ValueError('target_region must be a valid AWS region')
        return v
    
    @field_validator('desired_logs')
    @classmethod
    def validate_desired_logs(cls, v):
        """Validate desired_logs list"""
        if v is not None:
            if not isinstance(v, list):
                raise ValueError('desired_logs must be a list')
            if len(v) == 0:
                raise ValueError('desired_logs cannot be an empty list (use null to allow all applications)')
            for app in v:
                if not isinstance(app, str) or len(app.strip()) == 0:
                    raise ValueError('All items in desired_logs must be non-empty strings')
        return v


class TenantPatchRequest(BaseModel):
    """Model for tenant patch requests (partial updates)"""
    enabled: Optional[bool] = Field(None, description="Enable/disable log processing for this tenant")
    desired_logs: Optional[List[str]] = Field(None, description="List of application names to process")
    
    @field_validator('desired_logs')
    @classmethod
    def validate_desired_logs(cls, v):
        """Validate desired_logs list"""
        if v is not None:
            if not isinstance(v, list):
                raise ValueError('desired_logs must be a list')
            if len(v) == 0:
                raise ValueError('desired_logs cannot be an empty list (use null to allow all applications)')
            for app in v:
                if not isinstance(app, str) or len(app.strip()) == 0:
                    raise ValueError('All items in desired_logs must be non-empty strings')
        return v


class TenantResponse(BaseModel):
    """Model for tenant response data"""
    tenant_id: str
    log_distribution_role_arn: str
    log_group_name: str
    target_region: str
    enabled: bool = True
    desired_logs: Optional[List[str]] = None


class TenantListResponse(BaseModel):
    """Model for tenant list response"""
    tenants: List[TenantResponse]
    count: int
    limit: int
    last_key: Optional[str] = None


class ValidationCheck(BaseModel):
    """Model for individual validation check"""
    field: str
    status: str  # 'ok', 'missing', 'invalid'
    message: str


class TenantValidationResponse(BaseModel):
    """Model for tenant validation response"""
    tenant_id: str
    valid: bool
    checks: List[ValidationCheck]