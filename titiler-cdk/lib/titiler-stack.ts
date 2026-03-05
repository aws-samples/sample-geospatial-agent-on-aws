import * as cdk from 'aws-cdk-lib';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import * as apigateway from 'aws-cdk-lib/aws-apigateway';
import * as iam from 'aws-cdk-lib/aws-iam';
import { Platform } from 'aws-cdk-lib/aws-ecr-assets';
import { Construct } from 'constructs';

export class TitilerStack extends cdk.Stack {
  constructor(scope: Construct, id: string, props?: cdk.StackProps) {
    super(scope, id, props);

    // Lambda function with Web Adapter (bridges web server to Lambda)
    const titilerFunction = new lambda.DockerImageFunction(this, 'TitilerFunction', {
      functionName: 'titiler',
      code: lambda.DockerImageCode.fromImageAsset('.', {
        file: 'Dockerfile',
        platform: Platform.LINUX_AMD64, // Force x86_64 build even on Apple Silicon
      }),
      memorySize: 3008,
      timeout: cdk.Duration.seconds(30), // Increased for tile processing
      architecture: lambda.Architecture.X86_64,
      environment: {
        CACHE_TTL: '3600',
        CORS_ORIGINS: '*',
        AWS_LAMBDA_EXEC_WRAPPER: '/opt/bootstrap',
        READINESS_CHECK_PATH: '/healthz',
      },
    });

    // Add S3 read permissions
    titilerFunction.addToRolePolicy(
      new iam.PolicyStatement({
        effect: iam.Effect.ALLOW,
        actions: ['s3:GetObject', 's3:ListBucket'],
        resources: ['*'], // Restrict to your bucket if needed
      })
    );

    // API Gateway REST API
    const api = new apigateway.RestApi(this, 'TitilerApi', {
      restApiName: 'titiler-api',
      description: 'TiTiler API with API Key authentication',
      binaryMediaTypes: ['image/png', 'image/jpeg', 'image/jpg', 'image/webp', 'image/*'],
      deployOptions: {
        stageName: 'prod',
        throttlingRateLimit: 100,
        throttlingBurstLimit: 200,
      },
      defaultCorsPreflightOptions: {
        allowOrigins: apigateway.Cors.ALL_ORIGINS,
        allowMethods: apigateway.Cors.ALL_METHODS,
        allowHeaders: ['Content-Type', 'X-Amz-Date', 'Authorization', 'X-Api-Key', 'X-Amz-Security-Token'],
        exposeHeaders: ['Content-Type', 'Content-Length', 'Cache-Control', 'ETag'],
        allowCredentials: false,
        maxAge: cdk.Duration.hours(1),
      },
    });

    // Lambda integration
    const lambdaIntegration = new apigateway.LambdaIntegration(titilerFunction, {
      proxy: true,
    });

    // Create API Key
    const apiKey = api.addApiKey('TitilerApiKey', {
      apiKeyName: 'titiler-demo-key',
      description: 'API Key for TiTiler demo',
    });

    // Create Usage Plan
    const usagePlan = api.addUsagePlan('TitilerUsagePlan', {
      name: 'titiler-usage-plan',
      description: 'Usage plan for TiTiler API',
      throttle: {
        rateLimit: 100,
        burstLimit: 200,
      },
      quota: {
        limit: 20000,
        period: apigateway.Period.DAY,
      },
    });

    // Add API stage to usage plan
    usagePlan.addApiStage({
      stage: api.deploymentStage,
    });

    // Associate API key with usage plan
    usagePlan.addApiKey(apiKey);

    // Add proxy resource with API key requirement
    api.root.addProxy({
      defaultIntegration: lambdaIntegration,
      anyMethod: true,
      defaultMethodOptions: {
        apiKeyRequired: true,
      },
    });

    // Outputs
    new cdk.CfnOutput(this, 'ApiUrl', {
      value: api.url,
      description: 'TiTiler API URL',
      exportName: 'TitilerApiUrl',
    });

    new cdk.CfnOutput(this, 'ApiKeyId', {
      value: apiKey.keyId,
      description: 'API Key ID (use AWS Console or CLI to get the actual key value)',
      exportName: 'TitilerApiKeyId',
    });

    new cdk.CfnOutput(this, 'GetApiKeyCommand', {
      value: `aws apigateway get-api-key --api-key ${apiKey.keyId} --include-value --query 'value' --output text`,
      description: 'Command to retrieve API key value',
    });
  }
}
