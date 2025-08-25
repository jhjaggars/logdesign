"""
Main API handler for the tenant management service
"""

import json
import logging
import os
from typing import Dict, Any

from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse
from mangum import Mangum
from pydantic import ValidationError

# Import our utilities
from src.models.responses import success_response, error_response, not_found_response, validation_error_response
from src.utils.logger import setup_logging
from src.services.dynamo import TenantService, TenantNotFoundError, DynamoDBError
from src.models.tenant import (
    TenantCreateRequest, TenantUpdateRequest, TenantPatchRequest,
    TenantResponse, TenantListResponse, TenantValidationResponse
)

# Set up logging
logger = setup_logging()

# Create FastAPI app
app = FastAPI(
    title="Tenant Management API",
    description="REST API for managing multi-tenant logging configuration",
    version="1.0.0",
    docs_url="/api/v1/docs",
    redoc_url="/api/v1/redoc"
)

# CORS middleware removed - API does not require browser access

# Environment variables
TENANT_CONFIG_TABLE = os.environ.get('TENANT_CONFIG_TABLE', 'tenant-configurations')
AWS_REGION = os.environ.get('AWS_REGION', 'us-east-1')

# Initialize tenant service
tenant_service = TenantService(table_name=TENANT_CONFIG_TABLE, region=AWS_REGION)

@app.get("/api/v1/health")
async def health_check():
    """Health check endpoint - no authentication required"""
    try:
        from src.handlers.health import get_health_status
        return get_health_status()
    except Exception as e:
        logger.error(f"Health check failed: {str(e)}")
        raise HTTPException(status_code=500, detail="Health check failed")


@app.get("/api/v1/tenants", response_model=Dict[str, Any])
async def list_tenants(limit: int = 50, last_key: str = None):
    """List all tenants with pagination"""
    try:
        logger.info(f"Listing tenants with limit={limit}, last_key={last_key}")
        
        result = tenant_service.list_tenants(limit=limit, last_key=last_key)
        
        # Convert to expected format for backward compatibility
        response_data = {
            "tenants": result["tenants"],
            "total": result["count"],
            "limit": result["limit"]
        }
        
        if "last_key" in result:
            response_data["next_key"] = result["last_key"]
            
        return {"data": response_data, "status": "success"}
        
    except DynamoDBError as e:
        logger.error(f"DynamoDB error listing tenants: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to list tenants")
    except Exception as e:
        logger.error(f"Unexpected error listing tenants: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to list tenants")


@app.get("/api/v1/tenants/{tenant_id}")
async def get_tenant(tenant_id: str):
    """Get a specific tenant configuration"""
    try:
        logger.info(f"Getting tenant: {tenant_id}")
        
        tenant_data = tenant_service.get_tenant(tenant_id)
        return {"data": tenant_data, "status": "success"}
        
    except TenantNotFoundError as e:
        logger.warning(f"Tenant not found: {tenant_id}")
        raise HTTPException(status_code=404, detail=f"Tenant '{tenant_id}' not found")
    except DynamoDBError as e:
        logger.error(f"DynamoDB error getting tenant {tenant_id}: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to get tenant")
    except Exception as e:
        logger.error(f"Unexpected error getting tenant {tenant_id}: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to get tenant")


@app.post("/api/v1/tenants")
async def create_tenant(tenant_data: TenantCreateRequest):
    """Create a new tenant"""
    try:
        logger.info(f"Creating tenant: {tenant_data.tenant_id}")
        
        # Convert pydantic model to dict for DynamoDB
        tenant_dict = tenant_data.model_dump()
        
        # Create tenant in DynamoDB
        created_tenant = tenant_service.create_tenant(tenant_dict)
        
        return JSONResponse(
            status_code=201,
            content={"data": created_tenant, "status": "success", "message": "Tenant created successfully"}
        )
        
    except DynamoDBError as e:
        logger.error(f"DynamoDB error creating tenant: {str(e)}")
        if "already exists" in str(e).lower():
            raise HTTPException(status_code=400, detail=str(e))
        else:
            raise HTTPException(status_code=500, detail="Failed to create tenant")
    except ValidationError as e:
        logger.warning(f"Validation error creating tenant: {str(e)}")
        raise HTTPException(status_code=422, detail=e.errors())
    except Exception as e:
        logger.error(f"Unexpected error creating tenant: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to create tenant")


@app.put("/api/v1/tenants/{tenant_id}")
async def update_tenant(tenant_id: str, tenant_data: TenantUpdateRequest):
    """Update a tenant configuration"""
    try:
        logger.info(f"Updating tenant: {tenant_id}")
        
        # Convert pydantic model to dict, excluding None values
        update_dict = tenant_data.model_dump(exclude_none=True)
        
        if not update_dict:
            raise HTTPException(status_code=400, detail="No fields provided for update")
        
        # Update tenant in DynamoDB
        updated_tenant = tenant_service.update_tenant(tenant_id, update_dict)
        
        return {"data": updated_tenant, "status": "success", "message": "Tenant updated successfully"}
        
    except TenantNotFoundError as e:
        logger.warning(f"Tenant not found for update: {tenant_id}")
        raise HTTPException(status_code=404, detail=f"Tenant '{tenant_id}' not found")
    except DynamoDBError as e:
        logger.error(f"DynamoDB error updating tenant {tenant_id}: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to update tenant")
    except ValidationError as e:
        logger.warning(f"Validation error updating tenant {tenant_id}: {str(e)}")
        raise HTTPException(status_code=422, detail=e.errors())
    except Exception as e:
        logger.error(f"Unexpected error updating tenant {tenant_id}: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to update tenant")


@app.patch("/api/v1/tenants/{tenant_id}")
async def patch_tenant(tenant_id: str, tenant_data: TenantPatchRequest):
    """Partially update a tenant (e.g., enable/disable)"""
    try:
        logger.info(f"Patching tenant: {tenant_id}")
        
        # Convert pydantic model to dict, excluding None values
        patch_dict = tenant_data.model_dump(exclude_none=True)
        
        if not patch_dict:
            raise HTTPException(status_code=400, detail="No fields provided for patch")
        
        # Patch tenant in DynamoDB
        patched_tenant = tenant_service.patch_tenant(tenant_id, patch_dict)
        
        return {"data": patched_tenant, "status": "success", "message": "Tenant patched successfully"}
        
    except TenantNotFoundError as e:
        logger.warning(f"Tenant not found for patch: {tenant_id}")
        raise HTTPException(status_code=404, detail=f"Tenant '{tenant_id}' not found")
    except DynamoDBError as e:
        logger.error(f"DynamoDB error patching tenant {tenant_id}: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to patch tenant")
    except ValidationError as e:
        logger.warning(f"Validation error patching tenant {tenant_id}: {str(e)}")
        raise HTTPException(status_code=422, detail=e.errors())
    except Exception as e:
        logger.error(f"Unexpected error patching tenant {tenant_id}: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to patch tenant")


@app.delete("/api/v1/tenants/{tenant_id}")
async def delete_tenant(tenant_id: str):
    """Delete a tenant configuration"""
    try:
        logger.info(f"Deleting tenant: {tenant_id}")
        
        # Delete tenant from DynamoDB
        tenant_service.delete_tenant(tenant_id)
        
        return {"status": "success", "message": f"Tenant '{tenant_id}' deleted successfully"}
        
    except TenantNotFoundError as e:
        logger.warning(f"Tenant not found for deletion: {tenant_id}")
        raise HTTPException(status_code=404, detail=f"Tenant '{tenant_id}' not found")
    except DynamoDBError as e:
        logger.error(f"DynamoDB error deleting tenant {tenant_id}: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to delete tenant")
    except Exception as e:
        logger.error(f"Unexpected error deleting tenant {tenant_id}: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to delete tenant")


@app.get("/api/v1/tenants/{tenant_id}/validate")
async def validate_tenant(tenant_id: str):
    """Validate a tenant configuration"""
    try:
        logger.info(f"Validating tenant: {tenant_id}")
        
        # Validate tenant configuration
        validation_result = tenant_service.validate_tenant_config(tenant_id)
        
        return {"data": validation_result, "status": "success"}
        
    except TenantNotFoundError as e:
        logger.warning(f"Tenant not found for validation: {tenant_id}")
        raise HTTPException(status_code=404, detail=f"Tenant '{tenant_id}' not found")
    except DynamoDBError as e:
        logger.error(f"DynamoDB error validating tenant {tenant_id}: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to validate tenant")
    except Exception as e:
        logger.error(f"Unexpected error validating tenant {tenant_id}: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to validate tenant")


# Lambda handler using Mangum
def lambda_handler(event: Dict[str, Any], context) -> Dict[str, Any]:
    """
    AWS Lambda handler using Mangum to adapt FastAPI to Lambda
    
    Args:
        event: API Gateway event
        context: Lambda context
        
    Returns:
        API Gateway response
    """
    # Set up Mangum adapter
    handler = Mangum(app, lifespan="off")
    
    try:
        logger.info(f"Processing request: {event.get('httpMethod', 'unknown')} {event.get('path', 'unknown')}")
        return handler(event, context)
    except Exception as e:
        logger.error(f"Unhandled error in Lambda handler: {str(e)}", exc_info=True)
        return error_response("Internal server error", status_code=500)


# For local testing with uvicorn
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)