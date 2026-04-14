import * as cdk from 'aws-cdk-lib';
import * as apigateway from 'aws-cdk-lib/aws-apigateway';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import * as dynamodb from 'aws-cdk-lib/aws-dynamodb';
import * as iam from 'aws-cdk-lib/aws-iam';
import { Construct } from 'constructs';
import * as dotenv from 'dotenv';
import * as path from 'path';

dotenv.config({ path: path.resolve(__dirname, '..', '.env') });

export class ApiStack extends cdk.Stack {
  constructor(scope: Construct, id: string, props?: cdk.StackProps) {
    super(scope, id, props);

    const agentRuntimeArn = process.env.AGENT_RUNTIME_ARN ?? '';
    const s3BucketName = process.env.S3_BUCKET_NAME ?? '';

    // --- DynamoDB table for job tracking ---
    const jobsTable = new dynamodb.Table(this, 'GeoAgentJobsTable', {
      tableName: 'geospatial-agent-jobs',
      partitionKey: { name: 'jobId', type: dynamodb.AttributeType.STRING },
      billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
      timeToLiveAttribute: 'ttl',
      removalPolicy: cdk.RemovalPolicy.DESTROY,
    });

    // --- Worker Lambda (async — invoked by submit, runs up to 5 min) ---
    const workerFn = new lambda.Function(this, 'GeoAgentWorkerFn', {
      runtime: lambda.Runtime.PYTHON_3_12,
      handler: 'worker.handler',
      code: lambda.Code.fromAsset(path.join(__dirname, '..', 'lambda')),
      timeout: cdk.Duration.minutes(5),
      memorySize: 256,
      environment: {
        AGENT_RUNTIME_ARN: agentRuntimeArn,
        S3_BUCKET_NAME: s3BucketName,
        JOBS_TABLE_NAME: jobsTable.tableName,
      },
    });

    jobsTable.grantReadWriteData(workerFn);

    workerFn.addToRolePolicy(
      new iam.PolicyStatement({
        actions: ['bedrock-agentcore:InvokeAgentRuntime'],
        resources: agentRuntimeArn ? [agentRuntimeArn, `${agentRuntimeArn}/*`] : ['*'],
      }),
    );

    workerFn.addToRolePolicy(
      new iam.PolicyStatement({
        actions: ['s3:GetObject'],
        resources: s3BucketName
          ? [`arn:aws:s3:::${s3BucketName}/*`]
          : ['arn:aws:s3:::*/*'],
      }),
    );

    // --- API Lambda (sync — handles submit + poll + capabilities) ---
    const apiFn = new lambda.Function(this, 'GeoAgentApiFn', {
      runtime: lambda.Runtime.PYTHON_3_12,
      handler: 'api.handler',
      code: lambda.Code.fromAsset(path.join(__dirname, '..', 'lambda')),
      timeout: cdk.Duration.seconds(10),
      memorySize: 128,
      environment: {
        JOBS_TABLE_NAME: jobsTable.tableName,
        WORKER_FUNCTION_NAME: workerFn.functionName,
      },
    });

    jobsTable.grantReadWriteData(apiFn);
    workerFn.grantInvoke(apiFn);

    // --- API Gateway REST API ---
    const api = new apigateway.RestApi(this, 'GeoAgentApi', {
      restApiName: 'Geospatial Agent API',
      description: 'Async REST API wrapper for the Geospatial Agent (Part 4)',
      deployOptions: { stageName: 'prod' },
    });

    const apiKey = api.addApiKey('GeoAgentApiKey', {
      apiKeyName: 'geospatial-agent-api-key',
    });

    const usagePlan = api.addUsagePlan('GeoAgentUsagePlan', {
      name: 'GeospatialAgentUsagePlan',
      throttle: { rateLimit: 10, burstLimit: 5 },
    });
    usagePlan.addApiKey(apiKey);
    usagePlan.addApiStage({ stage: api.deploymentStage });

    const lambdaIntegration = new apigateway.LambdaIntegration(apiFn);

    // POST /analyze — submit a job
    api.root.addResource('analyze').addMethod('POST', lambdaIntegration, {
      apiKeyRequired: true,
    });

    // GET /jobs/{jobId} — poll for results
    const jobsResource = api.root.addResource('jobs');
    jobsResource.addResource('{jobId}').addMethod('GET', lambdaIntegration, {
      apiKeyRequired: true,
    });

    // GET /capabilities — static metadata
    api.root.addResource('capabilities').addMethod('GET', lambdaIntegration, {
      apiKeyRequired: true,
    });

    // --- Outputs ---
    new cdk.CfnOutput(this, 'ApiUrl', {
      value: api.url,
      description: 'API Gateway endpoint URL',
    });

    new cdk.CfnOutput(this, 'ApiKeyId', {
      value: apiKey.keyId,
      description: 'API Key ID (retrieve value via AWS CLI)',
    });
  }
}
