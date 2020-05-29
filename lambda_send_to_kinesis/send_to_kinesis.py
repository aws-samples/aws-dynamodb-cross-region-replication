import boto3
import os
from botocore.exceptions import ClientError
from botocore.client import Config

print('Loading function')

ssm_client = boto3.client('ssm')
ps_path_prefix = os.environ['PARAMETER_STORE_PATH_PREFIX']
target_access_key_id = ssm_client.get_parameter(Name=ps_path_prefix+'AccessKey')['Parameter']['Value']
target_secret_key = \
     ssm_client.get_parameter(Name=ps_path_prefix+'SecretKey', WithDecryption=True)['Parameter']['Value']

config = Config(proxies={'https':os.environ['PROXY_SERVER']}) if os.environ['USE_PROXY'] == 'TRUE' else None

class PartialRecordsSuccess(Exception):
    def __init__(self, response):
        failed_record_count = response['FailedRecordCount']
        self.message = "Partial success. {} of records failed. Error message unknown.".format(failed_record_count)
        for record in response['Records']:
            if 'ErrorMessage' in record:
                first_error_message = record['ErrorMessage']
                self.message = "Partial success. {} of records failed. First error message: {}"\
                    .format(failed_record_count, first_error_message)
                break
        super(PartialRecordsSuccess, self).__init__(self.message)


def lambda_handler(event, context):

    target_region = os.environ['TARGET_REGION']
    target_kinesis_client = boto3.client('kinesis',
                                     region_name=target_region,
                                     aws_access_key_id=target_access_key_id,
                                     aws_secret_access_key=target_secret_key,
                                     config=config)
    kinesis_record_list = list()

    skipped_items = 0

    for record in event['Records']:
        old_item = record['dynamodb']['OldImage'] if 'OldImage' in record['dynamodb'] else None
        new_item = record['dynamodb']['NewImage'] if 'NewImage' in record['dynamodb'] else None

        event_name = record['eventName']

        if event_name in ['MODIFY', 'INSERT']:
            # If the update is generated from the target region, there is no need to resend the change there
            if 'last_updater_region' in new_item and new_item['last_updater_region']['S'] == target_region:
                skipped_items += 1
                #print('Skipping changes generated from region {}'.format(target_region))
                continue
            #print('New/updated item:'+str(new_item))

            event_data = {'event_name': event_name, 'new_image': new_item}
            partition_key = new_item['PK']['S']
            record = {'Data': bytes(str(event_data), 'utf-8'), 'PartitionKey': partition_key}

            kinesis_record_list.append(record)

        if event_name == 'REMOVE':
            print('Deleted item:'+str(old_item))
            event_data = {'event_name': event_name, 'old_image': old_item}
            partition_key = old_item['PK']['S']
            record = {'Data': bytes(str(event_data), 'utf-8'), 'PartitionKey': partition_key}

            kinesis_record_list.append(record)

    if skipped_items:
        print('Skipped items {}.'.format(skipped_items))

    try:
        if kinesis_record_list:
            print("Trying to send {} events...".format(len(kinesis_record_list)))
            response = target_kinesis_client.put_records(Records=kinesis_record_list,
                                                        StreamName=os.environ['TARGET_STREAM'])
            if response['FailedRecordCount']:
                raise PartialRecordsSuccess(response)
            print("Sent {} events to Kinesis stream.".format(len(kinesis_record_list)))

    except ClientError as error:
        print("Error message: {}".format(error.response['Error']['Code']))
        raise
    except PartialRecordsSuccess as partial_error:
        print(partial_error.message)
        raise
    except Exception as e:
        print("Unknown error: {}".format(str(e)))
        raise

