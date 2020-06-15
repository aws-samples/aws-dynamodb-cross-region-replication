from aws_cdk import (
    aws_lambda as lambda_,
    aws_sqs as sqs,
    aws_dynamodb as ddb,
    aws_ec2 as ec2,
    aws_kinesis as kinesis,
    aws_ssm as ssm,
    core
)
from aws_cdk.aws_dynamodb import StreamViewType
from aws_cdk.aws_ec2 import SubnetSelection, SubnetType
from aws_cdk.aws_iam import PolicyStatement, Effect
from aws_cdk.aws_lambda_event_sources import DynamoEventSource, SqsDlq, KinesisEventSource
from aws_cdk.core import RemovalPolicy

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

'''
3. (Optional) Specify proxy server here if used.
'''
PROXY_SERVER = {
    REGION_A:"<proxy_server_ip>:<port>",
    REGION_B:"<proxy_server_ip>:<port>"
}

TABLE_NAME_PREFIX = 'user_cdk'
STREAM_NAME = 'ddb_replication_stream'
LOADER_STATS_TABLE_NAME = 'loader_stats'
REPLICATOR_STATS_TABLE_NAME = 'replicator_stats'
region_list = [REGION_A, REGION_B]
TABLE_NAME = { region:''.join([TABLE_NAME_PREFIX,'-',region]) for region in region_list }


class SourceDynamoStack(core.Stack):

    def __init__(self, scope: core.Construct, _id: str,
                 key_name,
                 table_name,
                 parameter_store_prefix,
                 target_region,
                 proxy_server, **kwargs) -> None:

        super().__init__(scope, _id, **kwargs)

        # Create VPC, NAT Gateway
        vpc = ec2.Vpc(self, "VPC",
                      max_azs=2,
                      cidr="10.10.0.0/16",
                      # configuration will create 2 public subnets and 2 private subnets
                      subnet_configuration=[ec2.SubnetConfiguration(
                          subnet_type=SubnetType.PUBLIC,
                          name="Public",
                          cidr_mask=24
                      ),
                          ec2.SubnetConfiguration(
                              subnet_type=SubnetType.PRIVATE,
                              name="Private",
                              cidr_mask=24
                          )
                      ],
                      nat_gateways=1,
                      )

        # Create source table
        source_ddb_table = ddb.Table(self, table_name,
                                     table_name=table_name,
                                    partition_key=ddb.Attribute(name="PK", type=ddb.AttributeType.STRING),
                                    billing_mode=ddb.BillingMode.PAY_PER_REQUEST,
                                    stream=StreamViewType.NEW_AND_OLD_IMAGES,
                                     removal_policy=RemovalPolicy.DESTROY)
        # Create loader_stats table for statistics of source table
        source_loader_stats_table = ddb.Table(self, LOADER_STATS_TABLE_NAME,
                                              table_name=LOADER_STATS_TABLE_NAME,
                                              partition_key=ddb.Attribute(name="PK", type=ddb.AttributeType.STRING),
                                              billing_mode=ddb.BillingMode.PAY_PER_REQUEST,
                                              removal_policy=RemovalPolicy.DESTROY
                                              )

        # Create EC2 instance for load testing
        public_subnets = vpc.public_subnets

        amzn_linux = ec2.MachineImage.latest_amazon_linux(
            generation=ec2.AmazonLinuxGeneration.AMAZON_LINUX_2,
            edition=ec2.AmazonLinuxEdition.STANDARD,
            virtualization=ec2.AmazonLinuxVirt.HVM,
            storage=ec2.AmazonLinuxStorage.GENERAL_PURPOSE
        )

        loader_instance = ec2.Instance(self, 'loader-dynamodb',
                                       instance_type=ec2.InstanceType("c5.large"),
                                       machine_image=amzn_linux,
                                       vpc=vpc,
                                       vpc_subnets=SubnetSelection(subnets=public_subnets),
                                       key_name=key_name
                                       )
        loader_instance.connections.allow_from_any_ipv4(ec2.Port.tcp(22), "Allow from SSH")
        # The loader EC2 will write to source table and loader statistics table
        source_ddb_table.grant_read_write_data(loader_instance)
        source_loader_stats_table.grant_read_write_data(loader_instance)
        # The loader EC2 also needs to put metrics in Cloudwatch
        put_metrics_policy = PolicyStatement(
            actions=['cloudwatch:PutMetricData'],
            effect=Effect.ALLOW,
            resources=['*']
        )

        loader_instance.add_to_role_policy(put_metrics_policy)

        # Create Lambda function send_to_kinesis in private subnet of VPC and set up environment variables
        private_subnets = vpc.private_subnets
        dest_sqs = sqs.Queue(self, 'send_to_kinesis_dest_Q')
        send_to_kinesis_lambda = lambda_.Function(self, 'ddb_send_to_kinesis',
                                                  code=lambda_.Code.asset("../lambda_send_to_kinesis"),
                                                  runtime=lambda_.Runtime.PYTHON_3_7,
                                                     handler='send_to_kinesis.lambda_handler',
                                                     timeout=core.Duration.seconds(60),
                                                  vpc=vpc,
                                                  vpc_subnets=SubnetSelection(subnets=private_subnets),
                                                     environment={
                                                         'PARAMETER_STORE_PATH_PREFIX':parameter_store_prefix,
                                                         'TARGET_REGION':target_region,
                                                          'TARGET_STREAM':STREAM_NAME,
                                                        'USE_PROXY':"FALSE",
                                                         'PROXY_SERVER':proxy_server}
                                                     )
        # Add event source of DynamoDB source table to lambda function
        send_to_kinesis_lambda.add_event_source(DynamoEventSource(table=source_ddb_table,
                                              starting_position=lambda_.StartingPosition.LATEST,
                                              batch_size=500,
                                              retry_attempts=300,
                                              parallelization_factor=10,
                                              on_failure=SqsDlq(dest_sqs),
                                              bisect_batch_on_error=True
                                              ))
        # allow lambda to access the AKSK in SSM parameter store
        access_key_parameter_name = ''.join([parameter_store_prefix,'AccessKey'])
        secret_key_parameter_name = ''.join([parameter_store_prefix,'SecretKey'])

        ak = ssm.StringParameter.from_string_parameter_attributes(self, access_key_parameter_name,
                                                                  parameter_name=access_key_parameter_name,
                                                                  version=1)
        ak.grant_read(send_to_kinesis_lambda)
        sk = ssm.StringParameter.from_secure_string_parameter_attributes(self, secret_key_parameter_name,
                                                                        parameter_name=secret_key_parameter_name,
                                                                         version=1)
        sk.grant_read(send_to_kinesis_lambda)
        core.CfnOutput(self, "Output",
                       value=loader_instance.instance_public_dns_name)


class ReplicatorStack(core.Stack):
    def __init__(self, scope: core.Construct, _id: str, **kwargs) -> None:

        new_kwargs = {'env':kwargs['env']}

        super().__init__(scope, _id, **new_kwargs)

        target_table_name = kwargs['target_table_name']
        kinesis_stream = kinesis.Stream(self, STREAM_NAME,
                                        stream_name=STREAM_NAME,
                                        shard_count=1)
        dlq_sqs = sqs.Queue(self, 'replicator_failure_Q')
        replicator_lambda = lambda_.Function(self, 'replicator_kinesis',
                                                     code=lambda_.Code.asset("../lambda_replicator"),
                                                     runtime=lambda_.Runtime.PYTHON_3_7,
                                                     handler='replicator_kinesis.lambda_handler',
                                                     timeout=core.Duration.seconds(60),
                                                     environment={
                                                         'TARGET_TABLE':target_table_name
                                                     }
                                                     )
        kinesis_stream.grant_read(replicator_lambda)
        replicator_lambda.add_event_source(KinesisEventSource(
            stream=kinesis_stream,
            starting_position=lambda_.StartingPosition.LATEST,
            batch_size=500,
            retry_attempts=100,
            parallelization_factor=10,
            on_failure=SqsDlq(dlq_sqs)
        ))
        target_table = ddb.Table.from_table_name(self,target_table_name,target_table_name)
        target_table.grant_read_write_data(replicator_lambda)
        replicator_stats_table = ddb.Table(self, REPLICATOR_STATS_TABLE_NAME,
                                           table_name=REPLICATOR_STATS_TABLE_NAME,
                                           partition_key=ddb.Attribute(name="PK", type=ddb.AttributeType.STRING),
                                           billing_mode=ddb.BillingMode.PAY_PER_REQUEST,
                                           removal_policy=RemovalPolicy.DESTROY
                                           )
        replicator_stats_table.grant_read_write_data(replicator_lambda)
        put_metrics_policy = PolicyStatement(
            actions=['cloudwatch:PutMetricData'],
            effect=Effect.ALLOW,
            resources=['*']
        )
        replicator_lambda.add_to_role_policy(put_metrics_policy)


app = core.App()
for region in region_list:

    SourceDynamoStack(app, "source-dynamo-"+region,
                      key_name = KEY_NAME[region],
                      table_name=TABLE_NAME[region],
                      target_region=(set(region_list) - set([region])).pop(),
                      parameter_store_prefix=PARAMETER_STORE_PREFIX[region],
                      proxy_server=PROXY_SERVER[region],
                      env = {'region':region}
                    )
    ReplicatorStack(app, "replicator-"+region, env={'region':region},
                    target_table_name = TABLE_NAME[region])


app.synth()
