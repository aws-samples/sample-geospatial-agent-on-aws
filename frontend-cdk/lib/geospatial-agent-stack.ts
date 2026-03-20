import * as cdk from 'aws-cdk-lib';
import * as ec2 from 'aws-cdk-lib/aws-ec2';
import * as ecs from 'aws-cdk-lib/aws-ecs';
import * as ecs_patterns from 'aws-cdk-lib/aws-ecs-patterns';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as logs from 'aws-cdk-lib/aws-logs';
import * as ecr_assets from 'aws-cdk-lib/aws-ecr-assets';
import * as cognito from 'aws-cdk-lib/aws-cognito';
import * as cloudfront from 'aws-cdk-lib/aws-cloudfront';
import * as origins from 'aws-cdk-lib/aws-cloudfront-origins';
import * as acm from 'aws-cdk-lib/aws-certificatemanager';
import * as secretsmanager from 'aws-cdk-lib/aws-secretsmanager';
import * as elbv2 from 'aws-cdk-lib/aws-elasticloadbalancingv2';
import * as s3 from 'aws-cdk-lib/aws-s3';
import * as wafv2 from 'aws-cdk-lib/aws-wafv2';
import { Construct } from 'constructs';
import { NagSuppressions } from 'cdk-nag';
import * as path from 'path';
import * as crypto from 'crypto';

export interface GeospatialAgentStackProps extends cdk.StackProps {
  stackName: string;
  environment: string;
}

export class GeospatialAgentStack extends cdk.Stack {
  constructor(scope: Construct, id: string, props: GeospatialAgentStackProps) {
    super(scope, id, props);

    const { environment } = props;

    // Get configuration from context
    const config = {
      // Required: Bedrock AgentCore Runtime ARN (deployed separately)
      agentRuntimeArn: this.node.tryGetContext('agentRuntimeArn') || process.env.AGENT_RUNTIME_ARN,

      // S3 bucket for satellite data
      s3BucketName: this.node.tryGetContext('s3BucketName') || process.env.S3_BUCKET_NAME,

      // AWS Region
      awsRegion: this.node.tryGetContext('awsRegion') || process.env.AWS_REGION || 'us-east-1',

      // Admin user email for Cognito
      adminEmail: this.node.tryGetContext('adminEmail') || process.env.ADMIN_EMAIL,

      // Container settings
      cpu: 1024, // 1 vCPU
      memoryLimitMiB: 2048, // 2 GB
      desiredCount: 2, // Number of tasks for high availability

      // Optional: Custom domain
      domainName: this.node.tryGetContext('domainName'),
      certificateArn: this.node.tryGetContext('certificateArn'),
    };

    // Validate required config
    if (!config.agentRuntimeArn) {
      throw new Error('agentRuntimeArn is required. Set via context or environment variable AGENT_RUNTIME_ARN');
    }
    if (!config.s3BucketName) {
      throw new Error('s3BucketName is required. Set via context or environment variable S3_BUCKET_NAME');
    }
    if (!config.adminEmail) {
      throw new Error('adminEmail is required. Set via context or environment variable ADMIN_EMAIL');
    }

    // ========================================
    // Generate CloudFront Custom Header for ALB Security
    // ========================================
    // Generate a deterministic but secure random value for the custom header
    // This ensures ALB only accepts traffic from CloudFront
    const customHeaderName = 'X-CloudFront-Secret';
    const customHeaderValue = crypto.randomBytes(32).toString('hex');

    // ========================================
    // VPC - Use existing default VPC or create new one
    // ========================================
    const vpc = new ec2.Vpc(this, 'AgenticAIVpc', {
      maxAzs: 2,
      natGateways: 1, // Cost optimization: 1 NAT gateway
      subnetConfiguration: [
        {
          cidrMask: 24,
          name: 'Public',
          subnetType: ec2.SubnetType.PUBLIC,
        },
        {
          cidrMask: 24,
          name: 'Private',
          subnetType: ec2.SubnetType.PRIVATE_WITH_EGRESS,
        },
      ],
    });

    // VPC Flow Logs for network monitoring (AwsSolutions-VPC7)
    vpc.addFlowLog('FlowLog', {
      destination: ec2.FlowLogDestination.toCloudWatchLogs(
        new logs.LogGroup(this, 'VpcFlowLogGroup', {
          logGroupName: `/vpc/geospatial-agent-${environment}/flow-logs`,
          retention: logs.RetentionDays.ONE_MONTH,
          removalPolicy: cdk.RemovalPolicy.DESTROY,
        }),
      ),
      trafficType: ec2.FlowLogTrafficType.ALL,
    });

    // ========================================
    // Cognito User Pool - Admin Only Access
    // ========================================
    const userPool = new cognito.UserPool(this, 'UserPool', {
      userPoolName: `geospatial-agent-${environment}`,
      selfSignUpEnabled: false, // Disable self-signup - admin creates users only
      signInAliases: {
        email: true,
      },
      autoVerify: {
        email: true,
      },
      standardAttributes: {
        email: {
          required: true,
          mutable: false,
        },
      },
      passwordPolicy: {
        minLength: 12,
        requireLowercase: true,
        requireUppercase: true,
        requireDigits: true,
        requireSymbols: true,
        tempPasswordValidity: cdk.Duration.days(7),
      },
      accountRecovery: cognito.AccountRecovery.EMAIL_ONLY,
      featurePlan: cognito.FeaturePlan.PLUS,
      standardThreatProtectionMode: cognito.StandardThreatProtectionMode.FULL_FUNCTION, // (AwsSolutions-COG3)
      // MFA: Optional for demo, set to REQUIRED for production deployments
      mfa: cognito.Mfa.OPTIONAL,
      mfaSecondFactor: {
        sms: false,
        otp: true, // TOTP-based MFA (e.g., Google Authenticator)
      },
      removalPolicy: cdk.RemovalPolicy.DESTROY, // For dev/test - use RETAIN for production
    });

    // Create User Pool Client for the web application
    const userPoolClient = new cognito.UserPoolClient(this, 'UserPoolClient', {
      userPool,
      userPoolClientName: `geospatial-agent-client-${environment}`,
      authFlows: {
        userPassword: true,
        userSrp: true,
      },
      oAuth: {
        flows: {
          authorizationCodeGrant: true,
        },
        scopes: [
          cognito.OAuthScope.EMAIL,
          cognito.OAuthScope.OPENID,
          cognito.OAuthScope.PROFILE,
        ],
      },
      // Token validity settings - 24-hour sessions for demo convenience
      idTokenValidity: cdk.Duration.hours(24),
      accessTokenValidity: cdk.Duration.hours(24),
      refreshTokenValidity: cdk.Duration.days(30),
      preventUserExistenceErrors: true,
      generateSecret: false, // Required for JavaScript/browser clients
    });

    // Create admin user with temporary password
    const adminUser = new cognito.CfnUserPoolUser(this, 'AdminUser', {
      userPoolId: userPool.userPoolId,
      username: config.adminEmail,
      userAttributes: [
        {
          name: 'email',
          value: config.adminEmail,
        },
        {
          name: 'email_verified',
          value: 'true',
        },
      ],
      desiredDeliveryMediums: ['EMAIL'],
    });

    // Note: Cognito generates and emails a temporary password to the admin user directly.
    // No separate Secrets Manager secret is needed for the temp password.

    // Store custom header metadata in Secrets Manager for operational reference.
    // Note: The header value itself must appear in the CloudFormation template because
    // CloudFront OriginCustomHeaders and ALB ListenerRule conditions require literal values
    // (dynamic references are not supported). This is a known AWS architectural limitation.
    // The Secrets Manager entry stores the header name for operational convenience only.
    const customHeaderSecret = new secretsmanager.Secret(this, 'CloudFrontCustomHeader', {
      secretName: `geospatial-agent/${environment}/cloudfront-custom-header`,
      description: 'CloudFront-to-ALB custom header name reference. The header value is managed by CloudFormation and regenerated on each deployment.',
      generateSecretString: {
        secretStringTemplate: JSON.stringify({ headerName: customHeaderName }),
        generateStringKey: 'headerValue',
        excludePunctuation: true,
        passwordLength: 64,
      },
      removalPolicy: cdk.RemovalPolicy.DESTROY,
    });
    // The actual header value used in CloudFront/ALB is generated at synth time
    // and embedded in the CloudFormation template (required by both resources)

    // ========================================
    // ECS Cluster
    // ========================================
    const cluster = new ecs.Cluster(this, 'AgenticAICluster', {
      vpc,
      clusterName: `geospatial-agent-${environment}`,
      containerInsights: true, // Enable CloudWatch Container Insights
    });

    // ========================================
    // IAM Role for ECS Task
    // ========================================
    const taskRole = new iam.Role(this, 'EcsTaskRole', {
      assumedBy: new iam.ServicePrincipal('ecs-tasks.amazonaws.com'),
      description: 'Role for Agentic AI ECS tasks to access AWS services',
    });

    // Scoped Bedrock permissions instead of AmazonBedrockFullAccess (AwsSolutions-IAM4)
    taskRole.addToPolicy(new iam.PolicyStatement({
      effect: iam.Effect.ALLOW,
      actions: [
        'bedrock:InvokeModel',
        'bedrock:InvokeModelWithResponseStream',
      ],
      resources: [
        `arn:aws:bedrock:${config.awsRegion}::foundation-model/*`,
      ],
    }));

    // Grant S3 read access to the satellite data bucket
    taskRole.addToPolicy(new iam.PolicyStatement({
      effect: iam.Effect.ALLOW,
      actions: [
        's3:GetObject',
        's3:ListBucket',
        's3:PutObject',
      ],
      resources: [
        `arn:aws:s3:::${config.s3BucketName}`,
        `arn:aws:s3:::${config.s3BucketName}/*`,
      ],
    }));

    // Grant access to invoke Bedrock AgentCore Runtime
    taskRole.addToPolicy(new iam.PolicyStatement({
      effect: iam.Effect.ALLOW,
      actions: [
        'bedrock-agentcore:InvokeAgentRuntime',
        'bedrock-agentcore:StopRuntimeSession',
      ],
      resources: [
        config.agentRuntimeArn,
        `${config.agentRuntimeArn}/*`, // Include runtime endpoints
      ],
    }));

    // ========================================
    // CloudWatch Log Group
    // ========================================
    const logGroup = new logs.LogGroup(this, 'AppLogGroup', {
      logGroupName: `/ecs/geospatial-agent-${environment}`,
      retention: logs.RetentionDays.ONE_WEEK,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
    });

    // ========================================
    // Build Docker Image
    // ========================================
    const dockerImage = new ecr_assets.DockerImageAsset(this, 'AppImage', {
      directory: path.join(__dirname, '../../react-ui'),
      file: 'Dockerfile',
      platform: ecr_assets.Platform.LINUX_AMD64,
    });

    // ========================================
    // ALB Access Logs Bucket (AwsSolutions-ELB2)
    // ========================================
    // ========================================
    // S3 Access Logs Bucket (AwsSolutions-S1) - Centralized access logging
    // ========================================
    const s3AccessLogsBucket = new s3.Bucket(this, 'S3AccessLogsBucket', {
      // Let CDK auto-generate bucket name to avoid conflicts on redeploy
      encryption: s3.BucketEncryption.S3_MANAGED,
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
      enforceSSL: true,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
      autoDeleteObjects: true,
      lifecycleRules: [{ expiration: cdk.Duration.days(90) }],
    });

    const albLogsBucket = new s3.Bucket(this, 'AlbLogsBucket', {
      // Let CDK auto-generate bucket name to avoid conflicts on redeploy
      encryption: s3.BucketEncryption.S3_MANAGED,
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
      enforceSSL: true, // (AwsSolutions-S10)
      removalPolicy: cdk.RemovalPolicy.DESTROY,
      autoDeleteObjects: true,
      lifecycleRules: [{ expiration: cdk.Duration.days(90) }],
      serverAccessLogsBucket: s3AccessLogsBucket,
      serverAccessLogsPrefix: 'alb-logs-bucket/',
    });

    // ========================================
    // Fargate Service with Application Load Balancer
    // ========================================
    const fargateService = new ecs_patterns.ApplicationLoadBalancedFargateService(this, 'FargateService', {
      cluster,
      serviceName: `geospatial-agent-${environment}`,
      cpu: config.cpu,
      memoryLimitMiB: config.memoryLimitMiB,
      desiredCount: config.desiredCount,
      taskImageOptions: {
        image: ecs.ContainerImage.fromDockerImageAsset(dockerImage),
        containerName: 'app',
        containerPort: 3001,
        environment: {
          NODE_ENV: 'production',
          PORT: '3001',
          AWS_REGION: config.awsRegion,
          AGENT_RUNTIME_ARN: config.agentRuntimeArn,
          S3_BUCKET_NAME: config.s3BucketName,
          COGNITO_USER_POOL_ID: userPool.userPoolId,
          COGNITO_CLIENT_ID: userPoolClient.userPoolClientId,
          COGNITO_REGION: config.awsRegion,
        },
        taskRole: taskRole,
        logDriver: ecs.LogDrivers.awsLogs({
          streamPrefix: 'app',
          logGroup: logGroup,
        }),
      },
      publicLoadBalancer: true,
      listenerPort: 80,
    });

    // Enable ALB access logs (AwsSolutions-ELB2)
    fargateService.loadBalancer.logAccessLogs(albLogsBucket);

    // Increase ALB idle timeout for long-running SSE/streaming agent responses
    fargateService.loadBalancer.setAttribute('idle_timeout.timeout_seconds', '120');

    // ========================================
    // ALB Listener Rules - Secure ALB to only accept CloudFront traffic
    // ========================================
    const listener = fargateService.listener;

    // Remove the default action and replace with custom header validation
    // Priority 1: Allow traffic with valid CloudFront custom header
    new elbv2.ApplicationListenerRule(this, 'CloudFrontHeaderRule', {
      listener: listener,
      priority: 1,
      conditions: [
        elbv2.ListenerCondition.httpHeader(customHeaderName, [customHeaderValue]),
      ],
      action: elbv2.ListenerAction.forward([fargateService.targetGroup]),
    });

    // Update default action to return 403 for direct ALB access
    listener.addAction('DefaultAction', {
      action: elbv2.ListenerAction.fixedResponse(403, {
        contentType: 'text/plain',
        messageBody: 'Direct access not allowed. Please use CloudFront.',
      }),
    });

    // ========================================
    // Health Check Configuration
    // ========================================
    fargateService.targetGroup.configureHealthCheck({
      path: '/health',
      interval: cdk.Duration.seconds(30),
      timeout: cdk.Duration.seconds(5),
      healthyThresholdCount: 2,
      unhealthyThresholdCount: 3,
      healthyHttpCodes: '200',
    });

    // ========================================
    // Auto Scaling
    // ========================================
    const scaling = fargateService.service.autoScaleTaskCount({
      minCapacity: 1,
      maxCapacity: 10,
    });

    // Scale based on CPU utilization
    scaling.scaleOnCpuUtilization('CpuScaling', {
      targetUtilizationPercent: 70,
      scaleInCooldown: cdk.Duration.seconds(60),
      scaleOutCooldown: cdk.Duration.seconds(60),
    });

    // Scale based on memory utilization
    scaling.scaleOnMemoryUtilization('MemoryScaling', {
      targetUtilizationPercent: 80,
      scaleInCooldown: cdk.Duration.seconds(60),
      scaleOutCooldown: cdk.Duration.seconds(60),
    });

    // ========================================
    // Security Group Configuration
    // ========================================
    // Allow inbound traffic on port 3001 from load balancer
    fargateService.service.connections.allowFrom(
      fargateService.loadBalancer,
      ec2.Port.tcp(3001),
      'Allow inbound from ALB'
    );

    // Allow outbound to internet for AWS API calls
    fargateService.service.connections.allowToAnyIpv4(
      ec2.Port.tcp(443),
      'Allow HTTPS outbound for AWS APIs'
    );

    // ========================================
    // CloudFront Access Logs Bucket (AwsSolutions-S1, S10)
    // ========================================
    const cfLogsBucket = new s3.Bucket(this, 'CloudFrontLogsBucket', {
      // Let CDK auto-generate bucket name to avoid conflicts on redeploy
      encryption: s3.BucketEncryption.S3_MANAGED,
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
      enforceSSL: true,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
      autoDeleteObjects: true,
      objectOwnership: s3.ObjectOwnership.OBJECT_WRITER,
      lifecycleRules: [{ expiration: cdk.Duration.days(90) }],
      serverAccessLogsBucket: s3AccessLogsBucket,
      serverAccessLogsPrefix: 'cf-logs-bucket/',
    });

    // ========================================
    // AWS WAF WebACL for CloudFront (AwsSolutions-CFR2)
    // ========================================
    // WAF with CLOUDFRONT scope can only be created in us-east-1
    const isUsEast1 = this.region === 'us-east-1';

    let webAclArn: string | undefined;
    if (isUsEast1) {
      const webAcl = new wafv2.CfnWebACL(this, 'WebACL', {
        scope: 'CLOUDFRONT',
        defaultAction: { allow: {} },
        visibilityConfig: {
          sampledRequestsEnabled: true,
          cloudWatchMetricsEnabled: true,
          metricName: `geospatial-agent-${environment}-waf`,
        },
        rules: [
          {
            name: 'AWSManagedRulesCommonRuleSet',
            priority: 1,
            statement: {
              managedRuleGroupStatement: {
                vendorName: 'AWS',
                name: 'AWSManagedRulesCommonRuleSet',
              },
            },
            overrideAction: { none: {} },
            visibilityConfig: {
              sampledRequestsEnabled: true,
              cloudWatchMetricsEnabled: true,
              metricName: 'CommonRuleSetMetric',
            },
          },
          {
            name: 'AWSManagedRulesKnownBadInputsRuleSet',
            priority: 2,
            statement: {
              managedRuleGroupStatement: {
                vendorName: 'AWS',
                name: 'AWSManagedRulesKnownBadInputsRuleSet',
              },
            },
            overrideAction: { none: {} },
            visibilityConfig: {
              sampledRequestsEnabled: true,
              cloudWatchMetricsEnabled: true,
              metricName: 'KnownBadInputsMetric',
            },
          },
          {
            name: 'AWSManagedRulesAmazonIpReputationList',
            priority: 3,
            statement: {
              managedRuleGroupStatement: {
                vendorName: 'AWS',
                name: 'AWSManagedRulesAmazonIpReputationList',
              },
            },
            overrideAction: { none: {} },
            visibilityConfig: {
              sampledRequestsEnabled: true,
              cloudWatchMetricsEnabled: true,
              metricName: 'IpReputationMetric',
            },
          },
        ],
      });
      webAclArn = webAcl.attrArn;
    }

    // ========================================
    // CloudFront Distribution
    // ========================================
    const distribution = new cloudfront.Distribution(this, 'Distribution', {
      ...(webAclArn && { webAclId: webAclArn }),
      comment: `Agentic AI for Earth - ${environment}`,
      defaultBehavior: {
        origin: new origins.LoadBalancerV2Origin(fargateService.loadBalancer, {
          protocolPolicy: cloudfront.OriginProtocolPolicy.HTTP_ONLY,
          httpPort: 80,
          readTimeout: cdk.Duration.seconds(60),
          customHeaders: {
            [customHeaderName]: customHeaderValue,
          },
        }),
        viewerProtocolPolicy: cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
        allowedMethods: cloudfront.AllowedMethods.ALLOW_ALL,
        cachedMethods: cloudfront.CachedMethods.CACHE_GET_HEAD_OPTIONS,
        cachePolicy: new cloudfront.CachePolicy(this, 'ApiCachePolicy', {
          cachePolicyName: `agentic-ai-cache-${environment}`,
          defaultTtl: cdk.Duration.seconds(0), // No caching for API calls
          minTtl: cdk.Duration.seconds(0),
          maxTtl: cdk.Duration.seconds(1),
          cookieBehavior: cloudfront.CacheCookieBehavior.all(),
          headerBehavior: cloudfront.CacheHeaderBehavior.allowList(
            'Authorization',  // Authorization must be in CachePolicy
            'Content-Type',
            'Accept',
            'Origin'
          ),
          queryStringBehavior: cloudfront.CacheQueryStringBehavior.all(),
          enableAcceptEncodingGzip: true,
          enableAcceptEncodingBrotli: true,
        }),
        originRequestPolicy: new cloudfront.OriginRequestPolicy(this, 'ApiOriginRequestPolicy', {
          originRequestPolicyName: `agentic-ai-origin-${environment}`,
          cookieBehavior: cloudfront.OriginRequestCookieBehavior.all(),
          headerBehavior: cloudfront.OriginRequestHeaderBehavior.allowList(
            // Do NOT include Authorization or Accept-Encoding here
            'Content-Type',
            'Accept',
            'Origin',
            'Referer',
            'User-Agent'
          ),
          queryStringBehavior: cloudfront.OriginRequestQueryStringBehavior.all(),
        }),
      },
      // Cache static assets
      additionalBehaviors: {
        '/assets/*': {
          origin: new origins.LoadBalancerV2Origin(fargateService.loadBalancer, {
            protocolPolicy: cloudfront.OriginProtocolPolicy.HTTP_ONLY,
            customHeaders: {
              [customHeaderName]: customHeaderValue,
            },
          }),
          viewerProtocolPolicy: cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
          cachePolicy: cloudfront.CachePolicy.CACHING_OPTIMIZED,
          compress: true,
        },
        '*.js': {
          origin: new origins.LoadBalancerV2Origin(fargateService.loadBalancer, {
            protocolPolicy: cloudfront.OriginProtocolPolicy.HTTP_ONLY,
            customHeaders: {
              [customHeaderName]: customHeaderValue,
            },
          }),
          viewerProtocolPolicy: cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
          cachePolicy: cloudfront.CachePolicy.CACHING_OPTIMIZED,
          compress: true,
        },
        '*.css': {
          origin: new origins.LoadBalancerV2Origin(fargateService.loadBalancer, {
            protocolPolicy: cloudfront.OriginProtocolPolicy.HTTP_ONLY,
            customHeaders: {
              [customHeaderName]: customHeaderValue,
            },
          }),
          viewerProtocolPolicy: cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
          cachePolicy: cloudfront.CachePolicy.CACHING_OPTIMIZED,
          compress: true,
        },
      },
      priceClass: cloudfront.PriceClass.PRICE_CLASS_100, // North America & Europe only
      enableLogging: true,
      logBucket: cfLogsBucket,
      minimumProtocolVersion: cloudfront.SecurityPolicyProtocol.TLS_V1_2_2021,
      httpVersion: cloudfront.HttpVersion.HTTP2_AND_3,
    });

    // ========================================
    // CloudFormation Outputs
    // ========================================
    new cdk.CfnOutput(this, 'LoadBalancerDNS', {
      value: fargateService.loadBalancer.loadBalancerDnsName,
      description: 'Application Load Balancer DNS',
      exportName: `${props.stackName}-LoadBalancerDNS`,
    });

    new cdk.CfnOutput(this, 'ServiceURL', {
      value: `http://${fargateService.loadBalancer.loadBalancerDnsName}`,
      description: 'Application URL',
      exportName: `${props.stackName}-ServiceURL`,
    });

    new cdk.CfnOutput(this, 'EcsClusterName', {
      value: cluster.clusterName,
      description: 'ECS Cluster Name',
      exportName: `${props.stackName}-EcsClusterName`,
    });

    new cdk.CfnOutput(this, 'EcsServiceName', {
      value: fargateService.service.serviceName,
      description: 'ECS Service Name',
      exportName: `${props.stackName}-EcsServiceName`,
    });

    new cdk.CfnOutput(this, 'CloudFrontDomain', {
      value: distribution.distributionDomainName,
      description: 'CloudFront Distribution Domain',
      exportName: `${props.stackName}-CloudFrontDomain`,
    });

    new cdk.CfnOutput(this, 'ApplicationURL', {
      value: `https://${distribution.distributionDomainName}`,
      description: '⭐ Main Application URL (use this to access the app)',
      exportName: `${props.stackName}-ApplicationURL`,
    });

    new cdk.CfnOutput(this, 'CognitoUserPoolId', {
      value: userPool.userPoolId,
      description: 'Cognito User Pool ID',
      exportName: `${props.stackName}-UserPoolId`,
    });

    new cdk.CfnOutput(this, 'CognitoClientId', {
      value: userPoolClient.userPoolClientId,
      description: 'Cognito User Pool Client ID',
      exportName: `${props.stackName}-ClientId`,
    });

    new cdk.CfnOutput(this, 'AdminEmail', {
      value: config.adminEmail,
      description: 'Admin user email',
    });

    // Note: Temporary password is emailed directly by Cognito to the admin user.
    // Check your email for the temporary password after deployment.

    new cdk.CfnOutput(this, 'CustomHeaderSecretArn', {
      value: customHeaderSecret.secretArn,
      description: '🔒 CloudFront custom header secret (for ALB security)',
    });

    // ========================================
    // CDK Nag Suppressions - Acceptable findings for sample code
    // ========================================

    // COG2: MFA is set to OPTIONAL for demo convenience. Set to REQUIRED for production.
    NagSuppressions.addResourceSuppressions(userPool, [
      {
        id: 'AwsSolutions-COG2',
        reason: 'MFA is enabled as OPTIONAL for demo convenience. Users can enroll in TOTP-based MFA. Production deployments should set mfa: cognito.Mfa.REQUIRED.',
      },
    ]);

    // SMG4: Secret rotation - CF header is static config
    NagSuppressions.addResourceSuppressions(customHeaderSecret, [
      {
        id: 'AwsSolutions-SMG4',
        reason: 'CloudFront custom header is a static configuration value regenerated on each deployment. Rotation would require coordinated CloudFront + ALB updates.',
      },
    ]);

    // IAM5: Wildcard resources on task role - scoped to specific bucket and agent runtime
    NagSuppressions.addResourceSuppressions(
      taskRole,
      [
        {
          id: 'AwsSolutions-IAM5',
          reason: 'Wildcard is scoped to specific S3 bucket objects and specific Bedrock AgentCore runtime endpoints. This is the minimum scope needed for the application.',
          appliesTo: [
            `Resource::arn:aws:s3:::${config.s3BucketName}/*`,
            `Resource::${config.agentRuntimeArn}/*`,
          ],
        },
      ],
      true, // Apply to children
    );

    // IAM5: Execution role wildcard - CDK-generated for ECR image pull
    NagSuppressions.addResourceSuppressions(
      fargateService.taskDefinition.executionRole!,
      [
        {
          id: 'AwsSolutions-IAM5',
          reason: 'Execution role wildcard permissions are CDK-generated defaults required for pulling ECR images and writing CloudWatch logs.',
          appliesTo: ['Resource::*'],
        },
      ],
      true,
    );

    // EC23: ALB Security Group allows 0.0.0.0/0 - by design, ALB is public-facing but protected by CloudFront custom header validation
    NagSuppressions.addResourceSuppressions(
      fargateService.loadBalancer,
      [
        {
          id: 'AwsSolutions-EC23',
          reason: 'ALB is intentionally public-facing. Direct access is blocked by ALB listener rules that require a CloudFront custom header. Only CloudFront can reach the application.',
        },
      ],
      true,
    );

    // ECS2: Environment variables in task definition - non-sensitive configuration only
    NagSuppressions.addResourceSuppressions(
      fargateService.taskDefinition,
      [
        {
          id: 'AwsSolutions-ECS2',
          reason: 'Environment variables contain only non-sensitive configuration (region, resource names, pool IDs). Secrets are managed via IAM roles and Secrets Manager. No credentials are passed as env vars.',
        },
      ],
      true,
    );

    // CFR1: Geo restrictions - not required for sample code
    NagSuppressions.addResourceSuppressions(distribution, [
      {
        id: 'AwsSolutions-CFR1',
        reason: 'Sample code does not require geo restrictions. Production deployments should configure restrictions based on target audience.',
      },
    ]);

    // CFR2: WAF is associated when deployed in us-east-1; suppress when not
    if (!isUsEast1) {
      NagSuppressions.addResourceSuppressions(distribution, [
        {
          id: 'AwsSolutions-CFR2',
          reason: 'WAF WebACL with CLOUDFRONT scope can only be created in us-east-1. This stack is deployed outside us-east-1, so WAF association is skipped. For production, deploy a cross-region WAF or use a us-east-1 stack.',
        },
      ]);
    }

    // CFR4/CFR5: CloudFront uses default certificate which forces TLSv1 minimum for viewer connections.
    // The minimumProtocolVersion is set to TLS_V1_2_2021 but only applies with custom domains/certificates.
    // Origin uses HTTP to ALB which is internal traffic protected by custom header.
    NagSuppressions.addResourceSuppressions(distribution, [
      {
        id: 'AwsSolutions-CFR4',
        reason: 'Using default CloudFront certificate (*.cloudfront.net) which does not allow setting minimum TLS version. Custom domain with ACM certificate would resolve this. Origin communication is HTTP to internal ALB, protected by custom header validation.',
      },
      {
        id: 'AwsSolutions-CFR5',
        reason: 'Origin protocol is HTTP-only to internal ALB by design. The ALB is not exposed to the internet (blocked by listener rules requiring CloudFront custom header). HTTPS to origin would require ACM certificate on ALB.',
      },
    ]);

    // S1: The centralized access logs bucket itself cannot have access logging (infinite loop)
    NagSuppressions.addResourceSuppressions(s3AccessLogsBucket, [
      {
        id: 'AwsSolutions-S1',
        reason: 'This is the centralized access logs destination bucket. Enabling server access logs on this bucket would create an infinite logging loop.',
      },
    ]);

    // IAM5: Wildcard on bedrock foundation model resources - required since model selection is dynamic
    NagSuppressions.addResourceSuppressions(
      taskRole,
      [
        {
          id: 'AwsSolutions-IAM5',
          reason: 'Wildcard on foundation-model/* is required because the application may invoke different Bedrock models. The scope is limited to bedrock:InvokeModel actions only.',
          appliesTo: [
            `Resource::arn:aws:bedrock:${config.awsRegion}::foundation-model/*`,
          ],
        },
      ],
      true,
    );

    // Add tags to all resources
    cdk.Tags.of(this).add('Project', 'GeospatialAgent');
    cdk.Tags.of(this).add('Environment', environment);
  }
}
