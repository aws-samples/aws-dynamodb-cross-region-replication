## Automated Deployment with Cloud Development Kit (CDK)

### Prerequisite

- AWS global region account access (and IAM Access Key and Secret Key)
- AWS China region account access (and IAM Access Key and Secret Key)
- AWS CDK installed (See [Getting Started with AWS CDK](https://docs.aws.amazon.com/cdk/latest/guide/getting_started.html) )
- SSH key pair in both accounts (used to access load testing machine)

### Setup

1. Download the git repository
2. Store Access Key and Secret Key in System Manager Parameter Store
3. Specify the parameters in cdk/app.py
4. Bootstrap CDK if it's first time running CDK in the region
5. Deploy CDK stacks. There are 2 CDK stacks for both regions (total 4 stacks): 
   - one setting up DynamoDB table, lambda function, VPC and NAT gateway, loader instance and loader statistics table
   - one setting up Kinesis stream, replicator lambda, replicator statistics table
   - 
6. Initialize loader replicator statistics table
7. Load test

