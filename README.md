DynamoDB Bi-directional Replication Solution
========================================

Although DynamoDB service provides native global table feature for replication across region, there is no builtin solution for replication between AWS partitions, e.g. between AWS China regions and global (commercial) regions. 

### Feature

- Active-active tables to sync up almost real-time
- Serverless solution
- Builtin failure management in handling transfer failure over Internet
- Works for either transfer over Internet or private network
- Replication status monitoring in Cloudwatch metrics
- Provides load testing example
- Deployment
  - Includes step-by-step manual setup guide 
  - Automated deployment with CDK

### Prerequisite

- AWS global region account access (and IAM Access Key and Secret Key)
- AWS China region account access (and IAM Access Key and Secret Key)

### Architecture - How it works

![](https://neptest.s3.cn-northwest-1.amazonaws.com.cn/ddb_architecuture.jpg)

The solution is based on DynamoDB stream which captures all new/ update/delete to DynamoDB items. A Lambda function '**send_to_kinesis**' 

- Consumes DynamoDB stream and send changes in batch using put_records to a **staging Kinesis stream** in target region over Internet
- It will check “last_updater_region” in the item image and skip the changes that is already applied in the target table.

In order to stablize the network connectivity, the lambda will be placed in VPC and the Lambda traffic to Internet will go thru NAT Gateway in the VPC (with a fixed outbound IP address).

In the target region, the Lambda function '**replicator**' 

- Consumes kinesis stream in target region and replicates the change to target table (CREATE/MODIFY/DELETE event together with old/new image of the DynamoDB item)
- It only replicates the change to the target table if the target item’s timestamp is older than current one.

### Manual Setup

See [Setup Guide (Chinese version)]([SETUP_GUIDE_cn.md])

### Automated Setup with AWS CDK (Cloud Development Kit)

TBD

### License

This library is licensed under the MIT-0 License. See the LICENSE file.