#!/usr/bin/env node
import 'source-map-support/register';
import * as cdk from 'aws-cdk-lib';
import { ApiStack } from '../lib/api-stack';

const app = new cdk.App();

const env = {
  account: process.env.CDK_DEFAULT_ACCOUNT,
  region: process.env.CDK_DEFAULT_REGION || 'us-east-1',
};

new ApiStack(app, 'GeospatialAgentApiStack', {
  env,
  description: 'Geospatial Agent REST API wrapper (Part 4)',
});

app.synth();
