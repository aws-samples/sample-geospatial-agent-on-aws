#!/usr/bin/env node
import 'source-map-support/register';
import * as cdk from 'aws-cdk-lib';
import { GeospatialAgentStack } from '../lib/geospatial-agent-stack';
import { ChangeDetectionStack } from '../lib/change-detection-stack';
import { AwsSolutionsChecks } from 'cdk-nag';

const app = new cdk.App();

// Get configuration from context or environment
const env = {
  account: process.env.CDK_DEFAULT_ACCOUNT,
  region: process.env.CDK_DEFAULT_REGION || 'us-east-1',
};

const stackName = app.node.tryGetContext('stackName') || 'GeospatialAgentStack';
const environment = app.node.tryGetContext('environment') || 'dev';

const stack = new GeospatialAgentStack(app, stackName, {
  env,
  description: 'Geospatial Agent on AWS - Satellite Image Analysis Application',
  tags: {
    Project: 'GeospatialAgent',
    Environment: environment,
    ManagedBy: 'CDK',
  },
  stackName,
  environment,
});

// Optional stack for the region-wide change-scan feature (scan_region_change).
// Deploy separately (`cdk deploy ChangeDetectionStack`) and set
// LGND_EMBEDDINGS_ENABLED=true on the agent runtime to enable the feature.
new ChangeDetectionStack(app, 'ChangeDetectionStack', {
  env,
  description: 'Optional LGND embedding Lambda for region-wide change scan',
  tags: {
    Project: 'GeospatialAgent',
    Environment: environment,
    ManagedBy: 'CDK',
  },
  stackName: 'ChangeDetectionStack',
  environment,
});

// Add CDK Nag checks
cdk.Aspects.of(app).add(new AwsSolutionsChecks({ verbose: true }));

app.synth();
