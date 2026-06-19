import * as cdk from 'aws-cdk-lib';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import { Construct } from 'constructs';
import { NagSuppressions } from 'cdk-nag';
import * as path from 'path';

export interface ChangeDetectionStackProps extends cdk.StackProps {
  environment: string;
}

/**
 * Optional stack for the region-wide change-scan feature (scan_region_change).
 *
 * Provisions the `lgnd-partition-query` Lambda that queries the public LGND
 * Clay v1.5 embedding archive (Source Cooperative / AWS Open Data) with DuckDB.
 * The agent fans out one invocation per geohash partition.
 *
 * This stack is OPTIONAL and deploys independently of the main application
 * stack. Deploy it (`cdk deploy ChangeDetectionStack`) and set
 * `LGND_EMBEDDINGS_ENABLED=true` on the agent runtime to enable the feature;
 * it stays off by default so the base sample needs no extra infrastructure.
 *
 * The embedding archive is a public bucket read anonymously, so the function
 * needs no S3 permissions beyond CloudWatch Logs (basic execution role).
 */
export class ChangeDetectionStack extends cdk.Stack {
  public readonly queryFunction: lambda.DockerImageFunction;

  constructor(scope: Construct, id: string, props: ChangeDetectionStackProps) {
    super(scope, id, props);

    const { environment } = props;

    this.queryFunction = new lambda.DockerImageFunction(this, 'LgndPartitionQuery', {
      functionName: 'lgnd-partition-query',
      code: lambda.DockerImageCode.fromImageAsset(
        path.join(__dirname, '..', '..', 'geo_agent', 'lambda_functions', 'lgnd_query'),
      ),
      timeout: cdk.Duration.seconds(120),
      memorySize: 2048,
      architecture: lambda.Architecture.X86_64,
      description: `LGND embedding partition query for region-wide change scan (${environment})`,
    });

    NagSuppressions.addResourceSuppressions(
      this.queryFunction,
      [
        {
          id: 'AwsSolutions-IAM4',
          reason:
            'Function uses the AWS managed AWSLambdaBasicExecutionRole for CloudWatch Logs only. ' +
            'The LGND embedding archive is a public bucket read anonymously, so no additional S3 permissions are required.',
        },
      ],
      true,
    );

    new cdk.CfnOutput(this, 'LgndPartitionQueryFunctionName', {
      value: this.queryFunction.functionName,
      description: 'LGND partition query Lambda name. Set LGND_EMBEDDINGS_ENABLED=true on the agent runtime to use it.',
    });
  }
}
