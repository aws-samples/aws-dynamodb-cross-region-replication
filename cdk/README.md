# Automated Deployment with Cloud Development Kit (CDK)

## Prerequisite

- AWS global region account access (and IAM Access Key and Secret Key)
- AWS China region account access (and IAM Access Key and Secret Key)
- AWS CDK (See [Getting Started with AWS CDK](https://docs.aws.amazon.com/cdk/latest/guide/getting_started.html) )
- AWS CLI
- SSH key pair in both accounts (used to access load testing machine)

## Setup

### Download the git repository

```bash
git clone https://github.com/aws-samples/aws-dynamodb-cross-region-replication.git
```

### Put AKSK in Parameter Store

Since the AWS commercial region and China region are of different account systems, **AKSK is needed to access resources in the target region and will be stored as secure string in SSM.**

```bash
PROFILE_A=<profile for region A>
PROFILE_B=<profile for region B>
#AKSK for accessing region B, stored in SSM of region A
aws ssm put-parameter --name /DDBReplication/Table_B/AccessKey --value <access_key> --type String --profile $PROFILE_A 
aws ssm put-parameter --name /DDBReplication/Table_B/SecretKey --value <secret_key> --type SecureString --profile $PROFILE_A 
#AKSK for accessing region A, stored in SSM of region B
aws ssm put-parameter --name /DDBReplication/Table_A/AccessKey --value <access_key> --type String --profile $PROFILE_B 
aws ssm put-parameter --name /DDBReplication/Table_A/SecretKey --value <secret_key> --type SecureString --profile $PROFILE_B
```

### Update the parameters in cdk/app.py

Replace the parameters in cdk/app.py

- REGION_A/REGION_B 
- PARAMETER_STORE_PREFIX
- KEY_NAME 

```python
'''
0. Specify the regions in REGION_A and REGION_B for replication
'''
REGION_A = 'ap-southeast-1'
REGION_B = 'cn-north-1'

'''
1. Credential setting in SSM Parameter Store for the target region. 
CDK is not allowed to deploy secure string for AKSK, you will need to set up parameter store in SSM manually
and provide the parameter prefix here, e.g.
for access_key, "/DDBReplication/TableCN/Access_Key" (StringType)
for secret_key, "/DDBReplication/TableCN/Secret_Key" (SecureStringType)
'''
PARAMETER_STORE_PREFIX = {
    REGION_A:'/DDBReplication/TableCN/',  # IMPORTANT! This is path to the AKSK to access REGION_B
    REGION_B:'/DDBReplication/TableSG/'  # IMPORTANT! This is path to the AKSK to access REGION_A
    }
'''
2. Specify the existing key name here for SSH. 
'''
KEY_NAME = {
    REGION_A:'<key_pair_name_A>',  # Key pair for loader EC2 in REGION_A
    REGION_B:'<key_pair_name_B>'  # Key pair for loader EC2 in REGION_B
    }
```

### Deploy CDK stacks

There are 2 CDK stacks for both regions (total 4 stacks): 

- **Source-dynamo-region-name**: Stack setting up DynamoDB table, lambda function, VPC and NAT gateway, loader instance and loader statistics table
- **Replicator-region-name**: Stack setting up Kinesis stream, replicator lambda, replicator statistics table

```bash
cd cdk/
cdk list
# Deploy each of the four stacks
cdk deploy <stack_name> --profile $PROFILE_A or $PROFILE_B
# In output of the stack, take note of the loader instance DNS name
```

### Initialize the statistics table

Set intial count in both loader_stats and replicator_stats table

```bash
aws dynamodb put-item --table-name loader_stats --item '{ "PK": {"S":"loaded_count"}, "cnt": {"N":"0"}}' --profile $PROFILE_A
aws dynamodb put-item --table-name loader_stats --item '{ "PK": {"S":"loaded_count"}, "cnt": {"N":"0"}}' --profile $PROFILE_B
aws dynamodb put-item --table-name replicator_stats --item '{ "PK": {"S":"replicated_count"}, "cnt": {"N":"0"}}' --profile $PROFILE_A
aws dynamodb put-item --table-name replicator_stats --item '{ "PK": {"S":"replicated_count"}, "cnt": {"N":"0"}}' --profile $PROFILE_B
```

### Load test

Install dependancies in loader instance

```bash
ssh -i <key_pair> ec2-user@<loader instance DNS name>
sudo yum install python3 -y
python3 -m venv my_app/env
source ~/my_app/env/bin/activate
pip install pip --upgrade
pip install boto3
echo "source ${HOME}/my_app/env/bin/activate" >> ${HOME}/.bashrc
source ~/.bashrc
pip install faker uuid
pip install --upgrade awscli
```

Load test with the loader.py

```bash
git clone https://github.com/aws-samples/aws-dynamodb-cross-region-replication.git
cd aws-dynamodb-cross-region-replication
python3 load_items.py -t user_cdk-cn-north-1 -r cn-north-1 -n 10000
```

