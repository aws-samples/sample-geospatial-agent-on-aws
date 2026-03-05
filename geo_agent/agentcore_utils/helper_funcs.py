"""Helper functions for AgentCore IAM role creation."""

import json
import os
import time
from typing import Any

import boto3
from boto3.session import Session


def create_agentcore_role(agent_name: str, s3_bucket_name: str, region: str = None) -> dict[str, Any]:
    iam_client = boto3.client('iam')
    agentcore_role_name = f'agentcore-{agent_name}-role'
    
    # Use provided region, or fall back to environment/config
    if not region:
        region = os.environ.get('AWS_DEFAULT_REGION') or os.environ.get('AWS_REGION') or Session().region_name
    
    account_id = boto3.client("sts").get_caller_identity()["Account"]
    role_policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Sid": "BedrockPermissions",
                "Effect": "Allow",
                "Action": [
                    "bedrock:InvokeModel",
                    "bedrock:InvokeModelWithResponseStream"
                ],
                "Resource": "*"
            },
            {
                "Sid": "ECRImageAccess",
                "Effect": "Allow",
                "Action": [
                    "ecr:BatchGetImage",
                    "ecr:GetDownloadUrlForLayer"
                ],
                "Resource": [
                    f"arn:aws:ecr:{region}:{account_id}:repository/*"
                ]
            },
            {
                "Effect": "Allow",
                "Action": [
                    "logs:DescribeLogStreams",
                    "logs:CreateLogGroup"
                ],
                "Resource": [
                    f"arn:aws:logs:{region}:{account_id}:log-group:/aws/bedrock-agentcore/runtimes/*"
                ]
            },
            {
                "Effect": "Allow",
                "Action": [
                    "logs:DescribeLogGroups"
                ],
                "Resource": [
                    f"arn:aws:logs:{region}:{account_id}:log-group:*"
                ]
            },
            {
                "Effect": "Allow",
                "Action": [
                    "logs:CreateLogStream",
                    "logs:PutLogEvents"
                ],
                "Resource": [
                    f"arn:aws:logs:{region}:{account_id}:log-group:/aws/bedrock-agentcore/runtimes/*:log-stream:*"
                ]
            },
            {
                "Sid": "ECRTokenAccess",
                "Effect": "Allow",
                "Action": [
                    "ecr:GetAuthorizationToken"
                ],
                "Resource": "*"
            },
            {
            "Effect": "Allow",
            "Action": [
                "xray:PutTraceSegments",
                "xray:PutTelemetryRecords",
                "xray:GetSamplingRules",
                "xray:GetSamplingTargets"
                ],
             "Resource": [ "*" ]
             },
             {
                "Effect": "Allow",
                "Resource": "*",
                "Action": "cloudwatch:PutMetricData",
                "Condition": {
                    "StringEquals": {
                        "cloudwatch:namespace": "bedrock-agentcore"
                    }
                }
            },
            {
                "Sid": "GetAgentAccessToken",
                "Effect": "Allow",
                "Action": [
                    "bedrock-agentcore:GetWorkloadAccessToken",
                    "bedrock-agentcore:GetWorkloadAccessTokenForJWT",
                    "bedrock-agentcore:GetWorkloadAccessTokenForUserId"
                ],
                "Resource": [
                  f"arn:aws:bedrock-agentcore:{region}:{account_id}:workload-identity-directory/default",
                  f"arn:aws:bedrock-agentcore:{region}:{account_id}:workload-identity-directory/default/workload-identity/{agent_name}-*"
                ]
            },
            {
                "Sid": "AgentCoreFullAccess",
                "Effect": "Allow",
                "Action": "bedrock-agentcore:*",
                "Resource": "*"
            },
            {
                "Sid": "S3BucketFullAccess",
                "Effect": "Allow",
                "Action": "s3:*",
                "Resource": [
                    f"arn:aws:s3:::{s3_bucket_name}",
                    f"arn:aws:s3:::{s3_bucket_name}/*"
                ]
            },
            {
                "Sid": "LocationServiceGeoCodingFullAccess",
                "Effect": "Allow",
                "Action": [
                    "geo-places:Geocode",
                    "geo-places:ReverseGeocode",
                    "geo-places:SearchNearby",
                    "geo-places:SearchText",
                    "geo-places:Suggest",
                    "geo-places:GetPlace",
                    "geo-places:Autocomplete"
                    ],
                "Resource": "*"
		    }
        ]
    }
    assume_role_policy_document = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Sid": "AssumeRolePolicy",
                "Effect": "Allow",
                "Principal": {
                    "Service": "bedrock-agentcore.amazonaws.com"
                },
                "Action": "sts:AssumeRole",
                "Condition": {
                    "StringEquals": {
                        "aws:SourceAccount": f"{account_id}"
                    },
                    "ArnLike": {
                        "aws:SourceArn": f"arn:aws:bedrock-agentcore:{region}:{account_id}:*"
                    }
                }
            }
        ]
    }

    assume_role_policy_document_json = json.dumps(
        assume_role_policy_document
    )
    role_policy_document = json.dumps(role_policy)

    # Create or get existing IAM Role
    try:
        agentcore_iam_role = iam_client.create_role(
            RoleName=agentcore_role_name,
            AssumeRolePolicyDocument=assume_role_policy_document_json
        )
        print(f"Created new role: {agentcore_role_name}")
        # Pause to make sure role is created
        time.sleep(10)
    except iam_client.exceptions.EntityAlreadyExistsException:
        print(f"Role {agentcore_role_name} already exists - updating policy")
        # Get existing role info
        agentcore_iam_role = iam_client.get_role(RoleName=agentcore_role_name)

    # Update/attach the policy (idempotent - overwrites if exists)
    print(f"Updating role policy for {agentcore_role_name}")
    try:
        iam_client.put_role_policy(
            PolicyDocument=role_policy_document,
            PolicyName="AgentCorePolicy",
            RoleName=agentcore_role_name
        )
        print("Policy updated successfully")
    except Exception as e:
        print(f"Error updating policy: {e}")

    return agentcore_iam_role