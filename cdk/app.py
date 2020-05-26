from aws_cdk import (
    aws_lambda as lambda_,
    aws_sqs as sqs,
    aws_dynamodb as ddb,
    aws_kinesis as kinesis,
    aws_ssm as ssm,
    core
)
from aws_cdk.aws_dynamodb import StreamViewType
from aws_cdk.aws_ec2 import SubnetSelection
from aws_cdk.aws_iam import PolicyStatement, Effect
from aws_cdk.aws_lambda_event_sources import DynamoEventSource, SqsDlq, KinesisEventSource
from cdk_vpc_stack.cdk_vpc_stack import VpcStack

REGION_A = 'ap-southeast-1'
REGION_B = 'cn-north-1'
'''
The target region credential setting in SSM Parameter Store. CDK is not allowed to deploy secure string for AKSK,
you will need to set up parameter store in SSM and provide the parameter prefix here
for access_key, "/DDBReplication/TableCN/Access_Key" (StringType)
for secret_key, "/DDBReplication/TableCN/Secret_Key" (SecureStringType)
'''
PARAMETER_STORE_PREFIX = {REGION_A:'/DDBReplication/TableCN/', REGION_B:'/DDBReplication/TableSG/'}

TABLE_NAME_PREFIX = 'user_cdk'
STREAM_NAME = 'ddb_replication_stream'
region_list = [REGION_A, REGION_B]
TABLE_NAME = { region:''.join([TABLE_NAME_PREFIX,'-',region]) for region in region_list }

PROXY_SERVER = {REGION_A:"<proxy_server_ip>:<port>", REGION_B:"<proxy_server_ip>:<port>"}

class SourceDynamoStack(core.Stack):

    def __init__(self, scope: core.Construct, _id: str,
                 vpc,
                 table_name,
                 parameter_store_prefix,
                 target_region,
                 proxy_server, **kwargs) -> None:

        super().__init__(scope, _id, **kwargs)

        source_ddb_table = ddb.Table(self, table_name,
                                     table_name=table_name,
                                    partition_key=ddb.Attribute(name="PK", type=ddb.AttributeType.STRING),
                                    billing_mode=ddb.BillingMode.PAY_PER_REQUEST,
                                    stream=StreamViewType.NEW_AND_OLD_IMAGES)
        private_subnets = vpc.private_subnets

        dest_sqs = sqs.Queue(self, 'send_to_kinesis_dest_Q')
        send_to_kinesis_lambda = lambda_.Function(self, 'ddb_send_to_kinesis',
                                                     code=lambda_.Code.asset("./lambda_send_to_kinesis"),
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

        send_to_kinesis_lambda.add_event_source(DynamoEventSource(table=source_ddb_table,
                                              starting_position=lambda_.StartingPosition.LATEST,
                                              batch_size=500,
                                              retry_attempts=300,
                                              parallelization_factor=10,
                                              on_failure=SqsDlq(dest_sqs)
                                              ))
        access_key_parameter_name = ''.join([parameter_store_prefix,'AccessKey'])
        secret_key_parameter_name = ''.join([parameter_store_prefix,'SecretKey'])

        ak = ssm.StringParameter.from_string_parameter_attributes(self, access_key_parameter_name,
                                                                  parameter_name=access_key_parameter_name,
                                                                  version=1)
        sk = ssm.StringParameter.from_secure_string_parameter_attributes(self, secret_key_parameter_name,
                                                                        parameter_name=secret_key_parameter_name,
                                                                         version=1)
        sk.grant_read(send_to_kinesis_lambda)
        ak.grant_read(send_to_kinesis_lambda)

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
                                                     code=lambda_.Code.asset("./lambda_replicator"),
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
        replicator_stats_table_name = 'replicator_stats'
        replicator_stats_table = ddb.Table(self, replicator_stats_table_name,
                                     table_name=replicator_stats_table_name,
                                    partition_key=ddb.Attribute(name="PK", type=ddb.AttributeType.STRING),
                                    billing_mode=ddb.BillingMode.PAY_PER_REQUEST
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
    vpc_stack = VpcStack(app, "vpc-nat-"+region, env={'region':region})

    SourceDynamoStack(app, "dynamodb-replication-"+region,
                      vpc = vpc_stack.vpc,
                      table_name=TABLE_NAME[region],
                      target_region=(set(region_list) - set([region])).pop(),
                      parameter_store_prefix=PARAMETER_STORE_PREFIX[region],
                      proxy_server=PROXY_SERVER[region],
                      env = {'region':region}
                    )
    ReplicatorStack(app, "replicator-"+region, env={'region':region},
                    target_table_name = TABLE_NAME[region])

app.synth()
