import base64
import ast
import boto3
import os
from botocore.exceptions import ClientError

target_ddb_client = boto3.client('dynamodb')

stats_table = boto3.resource('dynamodb').Table('replicator_stats')

cloudwatch_client = boto3.client('cloudwatch')

'''
Send metric data to Cloudwatch: 
1. Put item count executed in current lambda invocation in metric 'Updated_count'; 
2. Update accumulated number of replicated items in 'replicator_stats' table and put the count to Cloudwatch metric 'Total_replicated'
'''
def update_stats_metrics(updated_count):
    if updated_count:

        cloudwatch_client.put_metric_data(
            MetricData=[
                {
                    'MetricName': 'Updated_count',
                    'Dimensions': [
                        {
                            'Name': 'replicator',
                            'Value': 'ddb-replicator'
                        },
                    ],
                    'Unit': 'Count',
                    'StorageResolution': 1,
                    'Value': updated_count
                },
            ],
            Namespace='DDB-Replicator'
        )

        response = stats_table.update_item(
            Key={'PK':'replicated_count'},
            UpdateExpression="set cnt = cnt + :val",
            ExpressionAttributeValues={
                ':val': updated_count
            },
        ReturnValues="UPDATED_NEW"
        )
        total = int(response['Attributes']['cnt'])
        cloudwatch_client.put_metric_data(
            MetricData=[
                {
                    'MetricName': 'Total_replicated',
                    'Dimensions': [
                        {
                            'Name': 'replicator',
                            'Value': 'ddb-replicator'
                        },
                    ],
                    'Unit': 'Count',
                    'StorageResolution': 1,
                    'Value': total
                },
            ],
            Namespace='DDB-Replicator'
        )

        print("Current total:{}".format(total))


def lambda_handler(event, context):
    stale_item = 0
    items_updated = 0
    for record in event['Records']:
        payload = base64.b64decode(record["kinesis"]["data"])
        event_s = payload.decode("utf-8")
        event_dict = ast.literal_eval(event_s)
        try:
            if 'event_name' in event_dict and event_dict['event_name'] in ['INSERT', 'MODIFY']:
                new_item = event_dict['new_image']
                #print('New/updated item:' + str(new_item))
                item_dict = {'TableName': os.environ['TARGET_TABLE'],
                                'Item': new_item,
                                'ConditionExpression': 'attribute_not_exists(PK) OR last_update_timestamp < :cur_time_stamp',
                                'ExpressionAttributeValues': {":cur_time_stamp": new_item['last_update_timestamp']}
                                }
                target_ddb_client.put_item(**item_dict)

            if 'event_name' in event_dict and event_dict['event_name'] == 'REMOVE':
                old_item = event_dict['old_image']
                print('Deleted item:'+str(old_item))
                item_dict = {'TableName': os.environ['TARGET_TABLE'],
                             'Key': {
                                 'PK': old_item['PK']
                                },
                             'ConditionExpression': 'attribute_exists(PK) AND last_update_timestamp <= :cur_time_stamp',
                             'ExpressionAttributeValues': {":cur_time_stamp": old_item['last_update_timestamp']}
                             }
                target_ddb_client.delete_item(**item_dict)

            items_updated += 1

        except ClientError as error:
           if error.response['Error']['Code'] == 'ConditionalCheckFailedException':
               #print("Stale item. Discarding the change.")
               stale_item += 1
           else:
               print("Error message: {}".format(error.response['Error']['Code']))
               update_stats_metrics(items_updated)
               raise
        except Exception as e:
           print("Unknown error: {}".format(str(e)))
           update_stats_metrics(items_updated)
           raise
    update_stats_metrics(items_updated)
    print("Replicated items:{}. Stale items (dropped): {}.".format(items_updated, stale_item))


