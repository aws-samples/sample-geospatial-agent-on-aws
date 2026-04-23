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

    // --- Add CORS to existing S3 bucket for browser image loading ---
    if (s3BucketName) {
      new cdk.custom_resources.AwsCustomResource(this, 'GeoAgentBucketCors', {
        onCreate: {
          service: 'S3',
          action: 'putBucketCors',
          parameters: {
            Bucket: s3BucketName,
            CORSConfiguration: {
              CORSRules: [{
                AllowedOrigins: ['*'],
                AllowedMethods: ['GET', 'HEAD'],
                AllowedHeaders: ['*'],
                MaxAgeSeconds: 3600,
              }],
            },
          },
          physicalResourceId: cdk.custom_resources.PhysicalResourceId.of(`${s3BucketName}-cors`),
        },
        onUpdate: {
          service: 'S3',
          action: 'putBucketCors',
          parameters: {
            Bucket: s3BucketName,
            CORSConfiguration: {
              CORSRules: [{
                AllowedOrigins: ['*'],
                AllowedMethods: ['GET', 'HEAD'],
                AllowedHeaders: ['*'],
                MaxAgeSeconds: 3600,
              }],
            },
          },
          physicalResourceId: cdk.custom_resources.PhysicalResourceId.of(`${s3BucketName}-cors`),
        },
        policy: cdk.custom_resources.AwsCustomResourcePolicy.fromStatements([
          new iam.PolicyStatement({
            actions: ['s3:PutBucketCORS'],
            resources: [`arn:aws:s3:::${s3BucketName}`],
          }),
        ]),
      });
    }

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
        TITILER_API_URL: process.env.TITILER_API_URL ?? '',
        TITILER_API_KEY: process.env.TITILER_API_KEY ?? '',
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
        actions: ['s3:GetObject', 's3:PutObject'],
        resources: s3BucketName
          ? [`arn:aws:s3:::${s3BucketName}/*`]
          : ['arn:aws:s3:::*/*'],
      }),
    );

    // --- API Lambda (sync — handles submit + poll + capabilities + MCP) ---
    const apiFn = new lambda.Function(this, 'GeoAgentApiFn', {
      runtime: lambda.Runtime.PYTHON_3_12,
      handler: 'api.handler',
      code: lambda.Code.fromAsset(path.join(__dirname, '..', 'lambda')),
      timeout: cdk.Duration.minutes(5),  // Increased: MCP tools/call waits for worker sync
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

    // MCP API Key — separate key for MCP endpoint access by the mesh
    const mcpApiKey = api.addApiKey('GeoAgentMcpApiKey', {
      apiKeyName: 'geospatial-agent-mcp-api-key',
    });

    const usagePlan = api.addUsagePlan('GeoAgentUsagePlan', {
      name: 'GeospatialAgentUsagePlan',
      throttle: { rateLimit: 10, burstLimit: 5 },
    });
    usagePlan.addApiKey(apiKey);
    usagePlan.addApiKey(mcpApiKey);
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

    // POST /mcp — MCP JSON-RPC endpoint (API Gateway key required, same as other routes)
    api.root.addResource('mcp').addMethod('POST', lambdaIntegration, {
      apiKeyRequired: true,
    });

    // --- Store MCP API key value in SSM for the mesh to retrieve ---
    // Uses a custom resource to read the API Gateway key value and write to SSM.
    // This way the mesh can read the key from a known SSM path during supplier registration.
    const mcpApiKeyRetriever = new cdk.custom_resources.AwsCustomResource(this, 'McpApiKeyToSsm', {
      onCreate: {
        service: 'APIGateway',
        action: 'getApiKey',
        parameters: {
          apiKey: mcpApiKey.keyId,
          includeValue: true,
        },
        physicalResourceId: cdk.custom_resources.PhysicalResourceId.of('mcp-api-key-retriever'),
      },
      onUpdate: {
        service: 'APIGateway',
        action: 'getApiKey',
        parameters: {
          apiKey: mcpApiKey.keyId,
          includeValue: true,
        },
        physicalResourceId: cdk.custom_resources.PhysicalResourceId.of('mcp-api-key-retriever'),
      },
      policy: cdk.custom_resources.AwsCustomResourcePolicy.fromStatements([
        new iam.PolicyStatement({
          actions: ['apigateway:GET'],
          resources: ['*'],
        }),
      ]),
    });

    const mcpApiKeyValue = mcpApiKeyRetriever.getResponseField('value');

    // Write the MCP API key to SSM so the mesh can read it
    new cdk.custom_resources.AwsCustomResource(this, 'McpApiKeySsmParam', {
      onCreate: {
        service: 'SSM',
        action: 'putParameter',
        parameters: {
          Name: '/geospatial-agent/mcp-api-key',
          Value: mcpApiKeyValue,
          Type: 'SecureString',
          Description: 'MCP API key for the Sentinel-2 geospatial agent — auto-managed by CDK',
          Overwrite: true,
        },
        physicalResourceId: cdk.custom_resources.PhysicalResourceId.of('mcp-api-key-ssm'),
      },
      onUpdate: {
        service: 'SSM',
        action: 'putParameter',
        parameters: {
          Name: '/geospatial-agent/mcp-api-key',
          Value: mcpApiKeyValue,
          Type: 'SecureString',
          Description: 'MCP API key for the Sentinel-2 geospatial agent — auto-managed by CDK',
          Overwrite: true,
        },
        physicalResourceId: cdk.custom_resources.PhysicalResourceId.of('mcp-api-key-ssm'),
      },
      policy: cdk.custom_resources.AwsCustomResourcePolicy.fromStatements([
        new iam.PolicyStatement({
          actions: ['ssm:PutParameter'],
          resources: [`arn:aws:ssm:${this.region}:${this.account}:parameter/geospatial-agent/mcp-api-key`],
        }),
      ]),
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

    new cdk.CfnOutput(this, 'McpEndpointUrl', {
      value: `${api.url}mcp`,
      description: 'MCP endpoint URL for supplier registration',
    });

    new cdk.CfnOutput(this, 'McpApiKeyId', {
      value: mcpApiKey.keyId,
      description: 'MCP API Key ID (retrieve value via AWS CLI)',
    });

    new cdk.CfnOutput(this, 'McpApiKeySsmPath', {
      value: '/geospatial-agent/mcp-api-key',
      description: 'SSM path where the MCP API key is stored',
    });
  }
}
