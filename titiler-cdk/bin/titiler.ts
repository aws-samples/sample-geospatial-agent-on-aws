#!/usr/bin/env node
import 'source-map-support/register';
import * as cdk from 'aws-cdk-lib';
import { TitilerStack } from '../lib/titiler-stack';

const app = new cdk.App();

new TitilerStack(app, 'TitilerStack', {
  env: {
    account: process.env.CDK_DEFAULT_ACCOUNT,
    region: process.env.CDK_DEFAULT_REGION || 'us-east-1',
  },
  description: 'TiTiler Lambda + API Gateway with API Key',
});
