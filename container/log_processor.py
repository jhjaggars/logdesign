#!/usr/bin/env python3
"""
Unified log processor for multi-tenant logging pipeline
Supports Lambda runtime, SQS polling, and manual input modes
"""

import argparse
import gzip
import json
import logging
import os
import subprocess
import sys
import tempfile
import time
import urllib.parse
import uuid
from datetime import datetime
from typing import Dict, List, Any, Optional

import boto3
from botocore.exceptions import ClientError

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

# Custom exception classes
class NonRecoverableError(Exception):
    """Exception for errors that should not be retried (e.g., missing tenant config)"""
    pass

class TenantNotFoundError(NonRecoverableError):
    """Exception for when tenant configuration is not found"""
    pass

class InvalidS3NotificationError(NonRecoverableError):
    """Exception for invalid S3 notifications that cannot be processed"""
    pass

# For Lambda, we need to ensure the root logger and all handlers are set to INFO
root_logger = logging.getLogger()
root_logger.setLevel(logging.INFO)

# Set all existing handlers to INFO level (Lambda adds its own handler)
for handler in root_logger.handlers:
    handler.setLevel(logging.INFO)

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# Environment variables
TENANT_CONFIG_TABLE = os.environ.get('TENANT_CONFIG_TABLE', 'tenant-configurations')
MAX_BATCH_SIZE = int(os.environ.get('MAX_BATCH_SIZE', '1000'))
RETRY_ATTEMPTS = int(os.environ.get('RETRY_ATTEMPTS', '3'))
CENTRAL_LOG_DISTRIBUTION_ROLE_ARN = os.environ.get('CENTRAL_LOG_DISTRIBUTION_ROLE_ARN')
SQS_QUEUE_URL = os.environ.get('SQS_QUEUE_URL')
AWS_REGION = os.environ.get('AWS_REGION', 'us-east-1')

def lambda_handler(event: Dict[str, Any], context) -> Dict[str, Any]:
    """
    AWS Lambda handler for processing SQS messages containing S3 events
    
    Returns batchItemFailures to enable partial batch failure handling.
    Failed messages will be retried by SQS.
    """
    batch_item_failures = []
    successful_records = 0
    failed_records = 0
    
    logger.info(f"Processing {len(event.get('Records', []))} SQS messages")
    
    for record in event.get('Records', []):
        try:
            process_sqs_record(record)
            successful_records += 1
        except NonRecoverableError as e:
            # Non-recoverable errors should not be retried
            logger.warning(f"Non-recoverable error processing record {record.get('messageId', 'unknown')}: {str(e)}. Message will be removed from queue.")
            successful_records += 1  # Count as successful to remove from queue
        except Exception as e:
            # Recoverable errors should be retried
            logger.error(f"Recoverable error processing record {record.get('messageId', 'unknown')}: {str(e)}. Message will be retried.", exc_info=True)
            failed_records += 1
            
            # Add failed message ID to batch item failures
            # This tells Lambda to not delete this message from SQS
            if 'messageId' in record:
                batch_item_failures.append({
                    'itemIdentifier': record['messageId']
                })
            
    logger.info(f"Processing complete. Success: {successful_records}, Failed: {failed_records}")
    
    # Return partial batch failure response
    # This ensures failed messages remain in the queue for retry
    return {
        'batchItemFailures': batch_item_failures
    }

def sqs_polling_mode():
    """
    SQS polling mode for local testing
    Continuously polls SQS queue and processes messages
    """
    if not SQS_QUEUE_URL:
        logger.error("SQS_QUEUE_URL environment variable not set")
        sys.exit(1)
    
    sqs_client = boto3.client('sqs', region_name=AWS_REGION)
    logger.info(f"Starting SQS polling mode for queue: {SQS_QUEUE_URL}")
    
    while True:
        try:
            # Poll for messages
            response = sqs_client.receive_message(
                QueueUrl=SQS_QUEUE_URL,
                MaxNumberOfMessages=10,
                WaitTimeSeconds=20,  # Long polling
                VisibilityTimeout=300
            )
            
            messages = response.get('Messages', [])
            if not messages:
                logger.info("No messages received, continuing to poll...")
                continue
            
            logger.info(f"Received {len(messages)} messages from SQS")
            
            for message in messages:
                should_delete_message = False
                try:
                    # Convert SQS message to Lambda record format
                    lambda_record = {
                        'body': message['Body'],
                        'messageId': message['MessageId'],
                        'receiptHandle': message['ReceiptHandle']
                    }
                    
                    process_sqs_record(lambda_record)
                    should_delete_message = True  # Successfully processed
                    
                except NonRecoverableError as e:
                    logger.warning(f"Non-recoverable error processing message {message.get('MessageId', 'unknown')}: {str(e)}. Message will be deleted to prevent infinite retries.")
                    should_delete_message = True  # Delete to prevent infinite retries
                    
                except Exception as e:
                    logger.error(f"Recoverable error processing message {message.get('MessageId', 'unknown')}: {str(e)}. Message will be retried.")
                    should_delete_message = False  # Don't delete - allow retry
                
                # Delete message if processing succeeded or if error is non-recoverable
                if should_delete_message:
                    try:
                        sqs_client.delete_message(
                            QueueUrl=SQS_QUEUE_URL,
                            ReceiptHandle=message['ReceiptHandle']
                        )
                        logger.info(f"Successfully deleted message {message['MessageId']}")
                    except Exception as delete_error:
                        logger.error(f"Failed to delete message {message['MessageId']}: {str(delete_error)}")
                        # Continue processing other messages even if delete fails
                    
        except KeyboardInterrupt:
            logger.info("Received interrupt signal, shutting down...")
            break
        except Exception as e:
            logger.error(f"Error in SQS polling: {str(e)}")
            time.sleep(5)  # Wait before retrying

def manual_input_mode():
    """
    Manual input mode for development/testing
    Reads JSON input from stdin and processes it
    """
    logger.info("Manual input mode - reading JSON from stdin")
    logger.info("Expected format: SQS message body containing SNS message with S3 event")
    
    try:
        input_data = sys.stdin.read().strip()
        if not input_data:
            logger.error("No input data provided")
            sys.exit(1)
        
        # Parse input as SQS message body
        lambda_record = {
            'body': input_data,
            'messageId': 'manual-input',
            'receiptHandle': 'manual'
        }
        
        process_sqs_record(lambda_record)
        logger.info("Successfully processed manual input")
        
    except Exception as e:
        logger.error(f"Error processing manual input: {str(e)}")
        sys.exit(1)

def process_sqs_record(sqs_record: Dict[str, Any]) -> None:
    """
    Process a single SQS record containing S3 event notification
    """
    try:
        # Parse the SQS message body (SNS message)
        try:
            sns_message = json.loads(sqs_record['body'])
            s3_event = json.loads(sns_message['Message'])
        except (json.JSONDecodeError, KeyError) as e:
            raise InvalidS3NotificationError(f"Invalid SQS message format: {str(e)}")
        
        # Extract S3 event details
        for s3_record in s3_event['Records']:
            try:
                bucket_name = s3_record['s3']['bucket']['name']
                object_key = urllib.parse.unquote_plus(s3_record['s3']['object']['key'])
            except KeyError as e:
                raise InvalidS3NotificationError(f"Invalid S3 event format: missing {str(e)}")
            
            logger.info(f"Processing S3 object: s3://{bucket_name}/{object_key}")
            
            try:
                # Extract tenant information from object key
                tenant_info = extract_tenant_info_from_key(object_key)
                
                # Get tenant configuration
                tenant_config = get_tenant_configuration(tenant_info['tenant_id'])
                
                # Check if this application should be processed based on desired_logs filtering
                if not should_process_application(tenant_config, tenant_info['application']):
                    logger.info(f"Skipping processing for application '{tenant_info['application']}' due to desired_logs filtering")
                    continue
                
                # Download and process the log file
                log_events, s3_timestamp = download_and_process_log_file(bucket_name, object_key)
                
                # Deliver logs to customer account
                deliver_logs_to_customer(log_events, tenant_config, tenant_info, s3_timestamp)
                
            except TenantNotFoundError as e:
                logger.warning(f"Tenant not found for S3 object {object_key}: {str(e)}. Message will be removed from queue.")
                # Don't re-raise - this is a non-recoverable error
            except InvalidS3NotificationError as e:
                logger.warning(f"Invalid S3 notification for object {object_key}: {str(e)}. Message will be removed from queue.")
                # Don't re-raise - this is a non-recoverable error
            except NonRecoverableError as e:
                logger.warning(f"Non-recoverable error processing S3 object {object_key}: {str(e)}. Message will be removed from queue.")
                # Don't re-raise - this is a non-recoverable error
            except Exception as e:
                logger.error(f"Recoverable error processing S3 object {object_key}: {str(e)}. Message will be retried.")
                raise  # Re-raise recoverable errors for retry
            
    except NonRecoverableError as e:
        logger.warning(f"Non-recoverable error processing SQS record: {str(e)}. Message will be removed from queue.")
        # Don't re-raise - this is a non-recoverable error
    except Exception as e:
        logger.error(f"Recoverable error processing SQS record: {str(e)}. Message will be retried.")
        raise  # Re-raise recoverable errors for retry

def extract_tenant_info_from_key(object_key: str) -> Dict[str, str]:
    """
    Extract tenant information from S3 object key path
    Expected format: cluster_id/namespace/application/pod_name/timestamp-uuid.json.gz
    """
    path_parts = object_key.split('/')
    
    if len(path_parts) < 5:
        raise InvalidS3NotificationError(f"Invalid object key format. Expected at least 5 path segments, got {len(path_parts)}: {object_key}")

    tenant_info = {
        'cluster_id': path_parts[0],
        'tenant_id': path_parts[1], # Use customer_id as tenant_id for DynamoDB lookup
        'customer_id': path_parts[1],
        'namespace': path_parts[1],
        'application': path_parts[2],
        'pod_name': path_parts[3],
        'environment': 'production'
    }
    
    # Extract environment from cluster_id if it contains it
    if '-' in tenant_info['cluster_id']:
        env_prefix = tenant_info['cluster_id'].split('-')[0]
        env_map = {'prod': 'production', 'stg': 'staging', 'dev': 'development'}
        tenant_info['environment'] = env_map.get(env_prefix, 'production')
    
    logger.info(f"Extracted tenant info: {tenant_info}")
    return tenant_info

def get_tenant_configuration(tenant_id: str) -> Dict[str, Any]:
    """
    Retrieve tenant configuration from DynamoDB
    """
    try:
        dynamodb = boto3.resource('dynamodb', region_name=AWS_REGION)
        table = dynamodb.Table(TENANT_CONFIG_TABLE)
        response = table.get_item(Key={'tenant_id': tenant_id})
        
        if 'Item' not in response:
            raise TenantNotFoundError(f"No configuration found for tenant: {tenant_id}")
        
        config = response['Item']
        
        # Log tenant configuration details including desired_logs if present
        desired_logs = config.get('desired_logs')
        if desired_logs:
            logger.info(f"Retrieved config for tenant {tenant_id} with desired_logs filtering: {desired_logs}")
        else:
            logger.info(f"Retrieved config for tenant {tenant_id} (no desired_logs filtering - all applications will be processed)")
        
        return config
        
    except Exception as e:
        logger.error(f"Failed to get tenant configuration for {tenant_id}: {str(e)}")
        raise

def should_process_application(tenant_config: Dict[str, Any], application_name: str) -> bool:
    """
    Check if the application should be processed based on tenant's desired_logs configuration
    
    Args:
        tenant_config: Tenant configuration from DynamoDB
        application_name: Name of the application from S3 object key
    
    Returns:
        True if application should be processed, False if it should be filtered out
    """
    desired_logs = tenant_config.get('desired_logs')
    
    # If no desired_logs specified, process all applications (backward compatibility)
    if not desired_logs:
        return True
    
    # If desired_logs is not a list, log warning and process all applications
    if not isinstance(desired_logs, list):
        logger.warning(f"desired_logs is not a list: {type(desired_logs)}. Processing all applications.")
        return True
    
    # Case-insensitive matching for robustness
    desired_logs_lower = [log.lower() for log in desired_logs if isinstance(log, str)]
    application_lower = application_name.lower()
    
    should_process = application_lower in desired_logs_lower
    
    if should_process:
        logger.info(f"Application '{application_name}' is in desired_logs list - will process")
    else:
        logger.info(f"Application '{application_name}' is NOT in desired_logs list {desired_logs} - will skip processing")
    
    return should_process

def download_and_process_log_file(bucket_name: str, object_key: str) -> tuple[List[Dict[str, Any]], int]:
    """
    Download log file from S3 and extract log events
    Returns tuple of (log_events, s3_timestamp_ms)
    """
    try:
        s3_client = boto3.client('s3', region_name=AWS_REGION)
        response = s3_client.get_object(Bucket=bucket_name, Key=object_key)
        file_content = response['Body'].read()
        
        # Extract S3 object timestamp for more accurate fallback timestamp
        s3_last_modified = response['LastModified']
        s3_timestamp_ms = int(s3_last_modified.timestamp() * 1000)
        logger.info(f"S3 object timestamp: {s3_last_modified} ({s3_timestamp_ms}ms)")
        
        logger.info(f"Downloaded file size: {len(file_content)} bytes (compressed)")
        
        # Decompress if gzipped
        if object_key.endswith('.gz'):
            file_content = gzip.decompress(file_content)
            logger.info(f"Decompressed file size: {len(file_content)} bytes")
        
        # Log first 500 characters of the file for debugging
        sample = file_content[:500].decode('utf-8', errors='replace')
        logger.info(f"File content sample (first 500 chars): {sample}")
        
        log_events = process_json_file(file_content)
        return log_events, s3_timestamp_ms
        
    except Exception as e:
        logger.error(f"Failed to download/process file s3://{bucket_name}/{object_key}: {str(e)}")
        raise

def process_json_file(file_content: bytes) -> List[Dict[str, Any]]:
    """
    Process JSON file content and extract log events
    Prioritizes Vector's NDJSON (line-delimited JSON) format with JSON array fallback
    """
    try:
        log_events = []
        content = file_content.decode('utf-8')
        
        lines = content.strip().split('\n')
        logger.info(f"File contains {len(lines)} lines")
        
        # Try line-delimited JSON first (Vector NDJSON format)
        line_parse_success = 0
        line_parse_errors = 0
        
        for line_num, line in enumerate(lines):
            if line:
                try:
                    parsed_data = json.loads(line)
                    line_parse_success += 1
                    
                    # Handle if the line is a JSON array
                    if isinstance(parsed_data, list):
                        logger.info(f"Line {line_num} is a JSON array with {len(parsed_data)} items")
                        for idx, log_record in enumerate(parsed_data):
                            if idx == 0 and line_num == 0:
                                if isinstance(log_record, dict):
                                    logger.info(f"First log record keys: {list(log_record.keys())}")
                                    logger.info(f"First log record sample: {str(log_record)[:200]}...")
                            event = convert_log_record_to_event(log_record)
                            if event:
                                log_events.append(event)
                    else:
                        # Single log record
                        if line_num == 0 and isinstance(parsed_data, dict):
                            logger.info(f"First log record keys: {list(parsed_data.keys())}")
                            logger.info(f"First log record sample: {str(parsed_data)[:200]}...")
                        event = convert_log_record_to_event(parsed_data)
                        if event:
                            log_events.append(event)
                        else:
                            logger.debug(f"Line {line_num}: convert_log_record_to_event returned None")
                except json.JSONDecodeError as e:
                    line_parse_errors += 1
                    if line_num < 3:  # Log first few parse errors
                        logger.warning(f"Line {line_num} JSON parse error: {str(e)}, content: {line[:100]}...")
        
        logger.info(f"Line parsing results: {line_parse_success} successful, {line_parse_errors} errors")
        
        # If no events found via line parsing, try fallback methods
        if len(log_events) == 0 and line_parse_errors > 0:
            logger.info("No events from line parsing, trying fallback JSON parsing")
            try:
                data = json.loads(content)
                if isinstance(data, list):
                    logger.info(f"Parsed as JSON array with {len(data)} items")
                    for log_record in data:
                        event = convert_log_record_to_event(log_record)
                        if event:
                            log_events.append(event)
                else:
                    # Single JSON object
                    logger.info("Parsed as single JSON object")
                    event = convert_log_record_to_event(data)
                    if event:
                        log_events.append(event)
            except json.JSONDecodeError as e:
                logger.error(f"Fallback JSON parsing failed: {str(e)}")
        
        logger.info(f"Processed {len(log_events)} log events from JSON file")
        return log_events
        
    except Exception as e:
        logger.error(f"Failed to process JSON file: {str(e)}")
        raise

def convert_log_record_to_event(log_record: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    Convert log record to CloudWatch Logs event format
    """
    try:
        # Handle timestamp - Vector might use different field names
        timestamp = log_record.get('timestamp') or log_record.get('.timestamp') or log_record.get('time')
        if timestamp:
            if isinstance(timestamp, str):
                try:
                    # Handle ISO format timestamps
                    dt = datetime.fromisoformat(timestamp.replace('Z', '+00:00'))
                    timestamp_ms = int(dt.timestamp() * 1000)
                except ValueError:
                    # Fallback to current time
                    timestamp_ms = int(datetime.now().timestamp() * 1000)
            else:
                # Handle numeric timestamps
                timestamp_ms = int(timestamp * 1000) if timestamp < 1e12 else int(timestamp)
        else:
            timestamp_ms = int(datetime.now().timestamp() * 1000)
        
        # Vector typically stores the log message in 'message' field
        message = log_record.get('message') or log_record.get('.') or log_record.get('log')
        
        if not message:
            # If no message field found, use the entire record
            message = json.dumps(log_record)
        elif isinstance(message, (dict, list)):
            # If message is a dict/list, convert to string
            message = json.dumps(message)
        
        return {
            'timestamp': timestamp_ms,
            'message': str(message)
        }
    except Exception as e:
        logger.warning(f"Failed to convert log record: {str(e)}, record: {str(log_record)[:200]}...")
        return None

def deliver_logs_to_customer(
    log_events: List[Dict[str, Any]], 
    tenant_config: Dict[str, Any],
    tenant_info: Dict[str, str],
    s3_timestamp: int
) -> None:
    """
    Deliver log events to customer's CloudWatch Logs using Vector with double-hop cross-account role assumption
    """
    try:
        sts_client = boto3.client('sts', region_name=AWS_REGION)
        
        # Step 1: Assume the central log distribution role without session tags
        central_role_response = sts_client.assume_role(
            RoleArn=CENTRAL_LOG_DISTRIBUTION_ROLE_ARN,
            RoleSessionName=f"CentralLogDistribution-{tenant_info['tenant_id']}-{int(datetime.now().timestamp())}"
        )
        
        # Step 2: Create STS client with central role credentials
        central_sts_client = boto3.client(
            'sts',
            region_name=AWS_REGION,
            aws_access_key_id=central_role_response['Credentials']['AccessKeyId'],
            aws_secret_access_key=central_role_response['Credentials']['SecretAccessKey'],
            aws_session_token=central_role_response['Credentials']['SessionToken']
        )
        
        # Step 3: Assume the customer's log distribution role using central role
        # Get the current account ID for ExternalId
        current_account_id = boto3.client('sts').get_caller_identity()['Account']
        customer_role_response = central_sts_client.assume_role(
            RoleArn=tenant_config['log_distribution_role_arn'],
            RoleSessionName=f"CustomerLogDelivery-{tenant_info['tenant_id']}-{int(datetime.now().timestamp())}",
            ExternalId=current_account_id
        )
        
        # Extract customer credentials
        customer_credentials = customer_role_response['Credentials']
        
        # Generate unique session ID for Vector
        session_id = str(uuid.uuid4())
        
        # Prepare log group and stream names
        log_group_name = tenant_config['log_group_name']
        log_stream_name = tenant_info['pod_name']
        
        # Use Vector to deliver logs
        deliver_logs_with_vector(
            log_events=log_events,
            credentials=customer_credentials,
            region=tenant_config['target_region'],
            log_group=log_group_name,
            log_stream=log_stream_name,
            session_id=session_id,
            s3_timestamp=s3_timestamp
        )
        
        logger.info(f"Successfully delivered {len(log_events)} log events to {tenant_info['tenant_id']} using Vector")
        
    except Exception as e:
        logger.error(f"Failed to deliver logs to customer {tenant_info['tenant_id']}: {str(e)}")
        raise

def deliver_logs_with_vector(
    log_events: List[Dict[str, Any]],
    credentials: Dict[str, Any],
    region: str,
    log_group: str,
    log_stream: str,
    session_id: str,
    s3_timestamp: int
) -> None:
    """
    Use Vector subprocess to deliver logs to CloudWatch
    """
    temp_config_path = None
    vector_process = None
    
    logger.info(f"Starting Vector delivery for {len(log_events)} log events")
    logger.info(f"Target: {log_group}/{log_stream} in {region}")
    
    try:
        # Load Vector config template
        template_path = os.path.join(os.path.dirname(__file__), 'vector_config_template.yaml')
        with open(template_path, 'r') as f:
            config_template = f.read()
        
        # Substitute credentials and parameters
        config = config_template.format(
            session_id=session_id,
            region=region,
            log_group=log_group,
            log_stream=log_stream,
            access_key_id=credentials['AccessKeyId'],
            secret_access_key=credentials['SecretAccessKey'],
            session_token=credentials['SessionToken']
        )
        
        # Write config to temporary file
        temp_config_fd, temp_config_path = tempfile.mkstemp(suffix='.yaml', prefix='vector-config-')
        with os.fdopen(temp_config_fd, 'w') as f:
            f.write(config)
        # File descriptor is automatically closed by fdopen
        
        # Create data directory for Vector
        data_dir = f"/tmp/vector-{session_id}"
        os.makedirs(data_dir, exist_ok=True)
        
        # Set up Vector environment variables
        vector_env = os.environ.copy()
        vector_env.update({
            'VECTOR_DATA_DIR': data_dir,
            'AWS_ACCESS_KEY_ID': credentials['AccessKeyId'],
            'AWS_SECRET_ACCESS_KEY': credentials['SecretAccessKey'],
            'AWS_SESSION_TOKEN': credentials['SessionToken'],
            'AWS_REGION': region,
            'LOG_GROUP': log_group,
            'LOG_STREAM': log_stream
        })
        
        # Log critical environment variables (mask sensitive data)
        logger.info(f"Vector environment variables:")
        logger.info(f"  VECTOR_DATA_DIR: {data_dir}")
        logger.info(f"  AWS_ACCESS_KEY_ID: {credentials['AccessKeyId'][:10]}...")
        logger.info(f"  AWS_REGION: {region}")
        logger.info(f"  LOG_GROUP: {log_group}")
        logger.info(f"  LOG_STREAM: {log_stream}")
        
        # Start Vector subprocess
        vector_cmd = ['vector', '--config', temp_config_path]
        logger.info(f"Starting Vector with command: {' '.join(vector_cmd)}")
        
        vector_process = subprocess.Popen(
            vector_cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=vector_env
        )
        
        # Give Vector a moment to start
        import time
        time.sleep(0.5)
        
        # Check if Vector started successfully
        if vector_process.poll() is not None:
            stdout, stderr = vector_process.communicate()
            raise Exception(f"Vector failed to start. Exit code: {vector_process.returncode}, Stdout: {stdout}, Stderr: {stderr}")
        
        # Send log events to Vector via stdin
        logger.info(f"Sending {len(log_events)} log events to Vector")
        
        # Send all events as one write to avoid BrokenPipe issues

        # Send JSON objects to Vector, each on a separate line (NDJSON format)
        all_events = ""
        timestamp = s3_timestamp
        for event in log_events:
            # Extract just the message content and create a simple JSON object
            message = event.get('message', '')
            timestamp = event.get('timestamp', timestamp)
            
            # Create simple JSON object for Vector
            json_event = {
                'message': message,
                'timestamp': timestamp
            }
            all_events += json.dumps(json_event) + '\n'
        
        # Use communicate to send input and wait for completion
        logger.info("Waiting for Vector to complete log delivery")
        try:
            stdout, stderr = vector_process.communicate(input=all_events, timeout=300)
            return_code = vector_process.returncode
            
            # Log Vector output
            logger.info(f"Vector process completed with return code: {return_code}")
            
            if stdout:
                logger.info("Vector stdout:")
                for line in stdout.splitlines():
                    logger.info(f"  VECTOR: {line}")
            else:
                logger.info("Vector stdout: (empty)")
                
            if stderr:
                logger.warning("Vector stderr:")
                for line in stderr.splitlines():
                    logger.warning(f"  VECTOR: {line}")
            else:
                logger.info("Vector stderr: (empty)")
                
        except subprocess.TimeoutExpired:
            logger.error("Vector process timed out after 300 seconds")
            vector_process.kill()
            stdout, stderr = vector_process.communicate()
            logger.error(f"Vector stdout after timeout: {stdout}")
            logger.error(f"Vector stderr after timeout: {stderr}")
            raise Exception(f"Vector timed out after 300 seconds")
        except Exception as e:
            logger.error(f"Failed to communicate with Vector: {str(e)}")
            raise
        
        if return_code != 0:
            raise Exception(f"Vector exited with non-zero code {return_code}")
        
        logger.info(f"Vector successfully delivered {len(log_events)} logs to CloudWatch")
        
    except subprocess.TimeoutExpired:
        logger.error("Vector process timed out")
        if vector_process:
            vector_process.kill()
        raise Exception("Vector process timed out after 5 minutes")
        
    except Exception as e:
        logger.error(f"Error in Vector log delivery: {str(e)}")
        # Log the vector config for debugging
        if temp_config_path and os.path.exists(temp_config_path):
            with open(temp_config_path, 'r') as f:
                logger.error(f"Vector config was: {f.read()}")
        raise
        
    finally:
        # Cleanup
        if temp_config_path and os.path.exists(temp_config_path):
            os.unlink(temp_config_path)
        
        # Cleanup data directory
        data_dir = f"/tmp/vector-{session_id}"
        if os.path.exists(data_dir):
            import shutil
            shutil.rmtree(data_dir, ignore_errors=True)
        
        # Ensure process is terminated
        if vector_process and vector_process.poll() is None:
            vector_process.kill()

def ensure_log_stream_exists(logs_client, log_group_name: str, log_stream_name: str) -> None:
    """
    Ensure the log group and stream exist in the customer account
    """
    try:
        # Check if log group exists, create if not
        try:
            logs_client.describe_log_groups(logGroupNamePrefix=log_group_name)
        except ClientError as e:
            if e.response['Error']['Code'] == 'ResourceNotFoundException':
                logs_client.create_log_group(logGroupName=log_group_name)
                logger.info(f"Created log group: {log_group_name}")
        
        # Check if log stream exists, create if not
        try:
            logs_client.describe_log_streams(
                logGroupName=log_group_name,
                logStreamNamePrefix=log_stream_name
            )
        except ClientError as e:
            if e.response['Error']['Code'] == 'ResourceNotFoundException':
                logs_client.create_log_stream(
                    logGroupName=log_group_name,
                    logStreamName=log_stream_name
                )
                logger.info(f"Created log stream: {log_stream_name}")
                
    except Exception as e:
        logger.error(f"Failed to ensure log stream exists: {str(e)}")
        raise

def deliver_logs_in_batches(
    logs_client, 
    log_group_name: str, 
    log_stream_name: str, 
    log_events: List[Dict[str, Any]]
) -> None:
    """
    Deliver log events to CloudWatch Logs in batches
    """
    # Sort events by timestamp
    log_events.sort(key=lambda x: x['timestamp'])
    
    # Get current sequence token
    sequence_token = get_sequence_token(logs_client, log_group_name, log_stream_name)
    
    # Process in batches
    for i in range(0, len(log_events), MAX_BATCH_SIZE):
        batch = log_events[i:i + MAX_BATCH_SIZE]
        
        put_events_kwargs = {
            'logGroupName': log_group_name,
            'logStreamName': log_stream_name,
            'logEvents': batch
        }
        
        if sequence_token:
            put_events_kwargs['sequenceToken'] = sequence_token
        
        try:
            response = logs_client.put_log_events(**put_events_kwargs)
            sequence_token = response.get('nextSequenceToken')
            
            logger.info(f"Delivered batch of {len(batch)} log events")
            
        except ClientError as e:
            if e.response['Error']['Code'] == 'InvalidSequenceTokenException':
                # Retry with correct sequence token
                sequence_token = get_sequence_token(logs_client, log_group_name, log_stream_name)
                put_events_kwargs['sequenceToken'] = sequence_token
                response = logs_client.put_log_events(**put_events_kwargs)
                sequence_token = response.get('nextSequenceToken')
            else:
                raise

def get_sequence_token(logs_client, log_group_name: str, log_stream_name: str) -> Optional[str]:
    """
    Get the current sequence token for the log stream
    """
    try:
        response = logs_client.describe_log_streams(
            logGroupName=log_group_name,
            logStreamNamePrefix=log_stream_name
        )
        
        for stream in response['logStreams']:
            if stream['logStreamName'] == log_stream_name:
                return stream.get('uploadSequenceToken')
        
        return None
        
    except Exception as e:
        logger.error(f"Failed to get sequence token: {str(e)}")
        return None

def main():
    """
    Main entry point for standalone execution
    """
    parser = argparse.ArgumentParser(description='Multi-tenant log processor')
    parser.add_argument('--mode', choices=['sqs', 'manual'], default='sqs',
                        help='Execution mode: sqs (poll queue) or manual (stdin input)')
    
    args = parser.parse_args()
    
    if args.mode == 'sqs':
        sqs_polling_mode()
    elif args.mode == 'manual':
        manual_input_mode()

if __name__ == '__main__':
    main()