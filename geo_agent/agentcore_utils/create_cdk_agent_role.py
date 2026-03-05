#!/usr/bin/env python3
"""
Script to create an IAM role for the CDK agent using AgentCore.
This role will have the necessary permissions for the CDK agent to function properly.
"""

import argparse
import boto3
import json
import sys
import os
import logging
from pathlib import Path
from helper_funcs import create_agentcore_role

# Set up logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


def load_env_file(env_path: Path) -> dict:
    """Load environment variables from a .env file."""
    env_vars = {}
    if env_path.exists():
        logger.info(f"Loading environment from {env_path}")
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#') and '=' in line:
                    key, value = line.split('=', 1)
                    env_vars[key.strip()] = value.strip()
    return env_vars


def main():
    """
    Main function to create the CDK agent role.
    """
    parser = argparse.ArgumentParser(description='Create IAM role for CDK Agent')
    parser.add_argument('--agent-name', type=str, default='geospatial-agent-on-aws',
                        help='Name of the agent (default: geospatial-agent-on-aws)')
    parser.add_argument('--s3-bucket', type=str,
                        help='S3 bucket name for IAM policy (default: from .env or S3_BUCKET_NAME env var)')
    parser.add_argument('--profile', type=str, help='AWS profile to use')
    parser.add_argument('--region', type=str, help='AWS region to use')
    parser.add_argument('--env-file', type=str, default='../.env',
                        help='Path to .env file (default: ../.env)')

    args = parser.parse_args()

    # Load .env file relative to script location
    script_dir = Path(__file__).parent
    env_path = (script_dir / args.env_file).resolve()
    env_vars = load_env_file(env_path)

    # Get S3 bucket name from argument, .env file, or environment variable
    s3_bucket_name = args.s3_bucket or env_vars.get('S3_BUCKET_NAME') or os.environ.get('S3_BUCKET_NAME')
    if not s3_bucket_name:
        logger.error("S3 bucket name required. Use --s3-bucket, set in .env, or S3_BUCKET_NAME env var")
        return 1
    
    # Set AWS profile if provided
    if args.profile:
        os.environ['AWS_PROFILE'] = args.profile
        logger.info(f"Using AWS profile: {args.profile}")
    
    # Get region from argument, .env file, or environment
    region = args.region or env_vars.get('AWS_REGION') or os.environ.get('AWS_DEFAULT_REGION') or os.environ.get('AWS_REGION')
    if region:
        os.environ['AWS_DEFAULT_REGION'] = region
        logger.info(f"Using AWS region: {region}")
    
    try:
        # Create the AgentCore role for the CDK agent
        logger.info(f"Creating AgentCore role for {args.agent_name}...")
        logger.info(f"Using S3 bucket: {s3_bucket_name}")
        role = create_agentcore_role(args.agent_name, s3_bucket_name, region)
        
        # Print role information
        logger.info(f"Successfully created role: {role['Role']['RoleName']}")
        logger.info(f"Role ARN: {role['Role']['Arn']}")
        
        # Save role information to a file
        role_info = {
            'role_name': role['Role']['RoleName'],
            'role_arn': role['Role']['Arn']
        }
        
        with open(f"{args.agent_name}_role_info.json", 'w') as f:
            json.dump(role_info, f, indent=2)
            logger.info(f"Role information saved to {args.agent_name}_role_info.json")
        
        return 0
    
    except Exception as e:
        logger.error(f"Error creating CDK agent role: {e}", exc_info=True)
        return 1

if __name__ == "__main__":
    sys.exit(main())