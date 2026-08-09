"""
utils/r2.py — Cloudflare R2 boto3 S3-compatible storage helper.
"""

from __future__ import annotations

import logging
import boto3
from botocore.config import Config

from config import settings

logger = logging.getLogger(__name__)


def get_r2_client():
    """Build and return a boto3 S3 client configured for Cloudflare R2."""
    if not settings.r2_access_key_id or not settings.r2_secret_access_key:
        logger.warning("R2 credentials not fully configured")
        return None

    return boto3.client(
        "s3",
        endpoint_url=settings.r2_endpoint,
        aws_access_key_id=settings.r2_access_key_id,
        aws_secret_access_key=settings.r2_secret_access_key,
        config=Config(signature_version="s3v4"),
        region_name=settings.r2_region,
    )


async def upload_to_r2(file_bytes: bytes, file_name: str, content_type: str = "application/octet-stream") -> str | None:
    """
    Upload a file to R2 bucket.

    Returns:
        r2_key (str) if successful, None otherwise.
    """
    client = get_r2_client()
    if not client:
        return None

    try:
        r2_key = f"documents/{file_name}"
        client.put_object(
            Bucket=settings.r2_bucket,
            Key=r2_key,
            Body=file_bytes,
            ContentType=content_type,
        )
        logger.info(f"Uploaded file to R2: {r2_key}")
        return r2_key
    except Exception as e:
        logger.error(f"Failed to upload to R2: {e}")
        return None
