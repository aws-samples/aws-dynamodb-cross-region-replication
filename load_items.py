import boto3
from botocore.exceptions import ClientError
from faker import Factory
from time import sleep
import time
import uuid
from decimal import Decimal
import argparse
from datetime import datetime

fake = Factory.create()

def item_gen(id, region_name):
    p = fake.profile()

    i = dict()
    i['PK'] = str(id)
    i['first_name'] = fake.first_name()
    i['last_name'] = fake.last_name()
    i['email'] = p['mail']
    i['sex'] = p['sex']
    i['street_address'] = fake.street_address()
    i['state'] = fake.state()
    i['city'] = fake.city()
    i['zipcode'] = fake.zipcode()
    i['country'] = fake.country()
    i['govid'] = fake.ssn()
    i['last_update_timestamp'] = Decimal(time.time())
    i['last_updater_region'] = region_name

    return i

def update_stats_metrics(stats_table, cloudwatch, loaded_count):
    if loaded_count:

        response = stats_table.update_item(
            Key={'PK': 'loaded_count'},
            UpdateExpression="set cnt = cnt + :val",
            ExpressionAttributeValues={
                ':val': loaded_count
            },
            ReturnValues="UPDATED_NEW"
        )
        total = int(response['Attributes']['cnt'])
        cloudwatch.put_metric_data(
            MetricData=[
                {
                    'MetricName': 'Total_loaded',
                    'Dimensions': [
                        {
                            'Name': 'loader',
                            'Value': 'ddb-loader'
                        },
                    ],
                    'Unit': 'Count',
                    'StorageResolution': 1,
                    'Value': total
                },
            ],
            Namespace='DDB-Loader'
        )


if __name__ == "__main__":
    retries = 0  # used for backoff function
    RETRY_EXCEPTIONS = ('ProvisionedThroughputExceededException',
                        'ThrottlingException')
    parser = argparse.ArgumentParser()
    parser.add_argument('-n', type=int, default=10000, help='Number of items to generate and write to DynamoDB table')
    parser.add_argument('-r', type=str, default='sg', choices=['cn', 'sg', 'us'], help='Region of source DynamoDB table')
    parser.add_argument('-b', action='store_true', help='Write to DynamoDB table using batched write for higher load')
    parser.add_argument('-s', action='store_true', help='Reset the statistics table replicator_stats in target region')
    parser.add_argument('-t', type=str, default='cn', choices=['cn', 'sg', 'us'], help='Region of target DynamoDB table')
    args = parser.parse_args()
    count = args.n
    region = args.r
    batched = args.b
    reset_stats = args.s
    target_region = args.t

    PROFILE = {'cn': 'cn',
               'us':'default',
               'sg': 'default'
               }
    TABLE = {'cn': 'user-cn',
             'sg': 'user-sg',
             'us': 'user-us'
             }
    REGION = {'cn': 'cn-north-1',
              'us': 'us-west-2',
              'sg': 'ap-southeast-1'
              }
    region_name = REGION[region]
    table_name = TABLE[region]
    profile_name = PROFILE[region]

    target_region_name = REGION[target_region]
    target_profile_name = PROFILE[target_region]

    session = boto3.Session(profile_name=profile_name, region_name=region_name)
    ddb_source = session.resource('dynamodb')
    cloudwatch = session.client('cloudwatch')
    loader_stats_table = ddb_source.Table('loader_stats')
    if reset_stats:
        target_session = boto3.Session(profile_name = target_profile_name,
                                        region_name = target_region_name)
        target_ddb_table = target_session.resource('dynamodb').Table('replicator_stats')
        target_ddb_table.put_item(Item = {'PK':'replicated_count', 'cnt':Decimal(0)})

    start_time = datetime.now()
    source_table = ddb_source.Table(table_name)
    if batched:
        with source_table.batch_writer() as batch:
            for i in range(count):
                user = item_gen(uuid.uuid4(), region_name)
                batch.put_item(Item=user)
                if i % 100 == 0:
                    print("Done for {} items.".format(i))
                    update_stats_metrics(loader_stats_table, cloudwatch, 100)

    else:
        for i in range(count):
            user = item_gen(uuid.uuid4(), region_name)
            while True:
                try:
                    source_table.put_item(Item=user)
                    retries = 0
                    break
                except ClientError as err:
                    if err.response['Error']['Code'] not in RETRY_EXCEPTIONS:
                        raise
                    print("Retrying for item {}. Retry count: {}".format(i, retries))
                    sleep(2 ** retries)
                    retries += 1
            if i % 100 == 0:
                print("Done for {} items.".format(i))
                update_stats_metrics(loader_stats_table,cloudwatch,100)

    print("Test done for {}".format(count))
    #update_stats_metrics(loader_stats_table,cloudwatch,count)
    end_time = datetime.now()
    duration = end_time - start_time
    print("Test from {} to {}. Run time: {}".format(str(start_time), str(end_time), str(duration)))