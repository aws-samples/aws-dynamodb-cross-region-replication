中国区与Global区域DynamoDB table双向同步
文档版本




0. 架构
在北京区域创建以下对象：

* 2个DynamoDB，user-cn存储用户信息，数据将于新加坡区域的user-sg保持同步，另一个表replicator-stats表用来存储复制的变更记录数，以便监控复制进度
* User-cn开启DynamoDB stream，用于记录user-cn表的所有变更
* Kinesis stream ddb_replication_stream_cn 用于记录新加坡区域DynamoDB表user-sg的所有变更
* Lambda replicator_ddb将北京区域中ddb_replication_stream_cn上的变更记录写入user-cn表
* Lambda ddb_send_to_kinesis将从北京区域的DynamoDB stream中读取变更记录然后写入到新加坡区域的ddb_replication_stream_sg
* Parameter store中存储新加坡区域IAM用户的AK/SK
* 如果客户有direct connect，可以创建VPC并在VPC内部署一个代理服务器，lambda也部署在VPC内，令lambda利用新加坡区域的代理服务器进而利用direct connect，从而减少网络延迟并提高网络稳定性
在新加坡区域创建以下对象：

* 2个DynamoDB，user-sg存储用户信息，数据将于北京区域的user-cn保持同步，另一个表replicator-stats表用来存储复制的变更记录数，以便监控复制进度
* User-sg开启DynamoDB stream，用于记录user-cn表的所有变更
* Kinesis stream ddb_replication_stream_sg 用于记录北京区域DynamoDB表user-cn的所有变更
* Lambda replicator_ddb将新加坡区域中ddb_replication_stream_sg上的变更记录写入user-sg表
* Lambda ddb_send_to_kinesis将从新加坡区域的DynamoDB stream中读取变更记录然后写入到北京区域的ddb_replication_stream_cn
* Parameter store中存储北京区域IAM用户的AK/SK
* 如果客户有direct connect，可以创建VPC并在VPC内部署一个代理服务器，lambda也部署在VPC内，令lambda利用北京区域的代理服务器进而利用direct connect，从而减少网络延迟并提高网络稳定性
因为对象较多，我们以北京区域DynamoDB表user-cn中的变更数据向新加坡区域同步过程为例，将整个数据流梳理一遍，从新加坡DynamoDB表user-sg同步数据到user-cn的过程类似将不再赘述

* 当数据写入北京区域DynamoDB表user-cn后，变更记录首先会写入北京区域的DynamoDB stream
* 北京区域的Lambda ddb_send_to_kinesis将从北京区域的DynamoDB stream中读取变更记录，首先判断记录的last_updater_region是否是新加坡区域，如果就丢弃，不是则写入到新加坡区域的ddb_replication_stream_sg，这一步将跨region，主要的网络延迟就在这一步
* 新加坡区域的Lambda replicator_ddb将新加坡区域中ddb_replication_stream_sg上的变更记录写入user-sg表，在写入时会通过condition判断变更记录的时间戳是否大于当前记录的变更时间戳，只有大于才会写入。
* 新加坡区域的user-sg的变化记录又出现在DynamoDB stream中，并被新加坡区域的Lambda ddb_send_to_kinesis读取，而后判断记录的last_updater_region是否是北京区域，因为该变化恰恰是来自北京，所以该记录被丢弃，从避免了循环复制。


1.   部署环境
1.1 准备代理主机
网络设置
* 在北京与新加坡区域中各创建一个VPC，记录两个VPC的NAT gateway所对应的EIP。
* 在两个VPC内分别创建一个EC2用作代理服务器，本DEMO中选择的是c5.large类型实例，为每一个EC2绑定一个EIP。
* 在两个VPC内部各自创建一个lambda所用的安全组，在此安全组中添加规则允许对端region代理服务器EIP网段的http/https的流量进入，譬如本例中，北京区域的lambda所用安全组就只对新加坡代理服务器EIP网段的http/https的流量开放。
* 两个区域内代理服务器的安全组中设定只允许对端region lambda所在VPC的NAT Gateway的http/https流量进来。
* 因为篇幅有限，具体步骤请见《Amazon Virtual Private Cloud用户指南》，此处不再赘述。
* 安装软件
1、因为后续操作中会大量使用AWS CLI，故而需要在2个代理服务器上确认安装python3等软件并升级AWS CLI到最新版本，以ec2-user用户运行如下命令：

yum list installed | grep -i python3sudo yum install python36 -ypython3 -m venv my_app/envsource ~/my_app/env/bin/activatepip install pip --upgradepip install boto3echo "source ${HOME}/my_app/env/bin/activate" >> ${HOME}/.bashrcsource ~/.bashrcpip install fakerpip install --upgrade awscli/2、后续模拟加载数据到DynamoDB表的脚本会用到parallel，故而也需要安装parallelhttp://git.savannah.gnu.org/cgit/parallel.git/tree/READMEwget https://ftpmirror.gnu.org/parallel/parallel-20200322.tar.bz2wget https://ftpmirror.gnu.org/parallel/parallel-20200322.tar.bz2.siggpg parallel-20200322.tar.bz2.sigbzip2 -dc parallel-20200322.tar.bz2 | tar xvf -cd parallel-20200322./configure && make && sudo make install

配置profile
接下来在2个代理服务器上通过aws configure --profile 分别配置相应中国区域和Global区域IAM用户的AK/SK，以便后续能顺利运行AWS CLI命令。请生成分别名为cn和sg的profile，以便后面加载记录时使用。

安装代理
以ec2-user运行sudo yum install squidsudo vi /etc/sysctl.confnet.ipv4.ip_forward = 1sudo sysctl -p /etc/sysctl.confsudo sysctl -a |grep -w ip_forward grep -n 'http_access deny all' /etc/squid/squid.conf56:http_access deny all这里修改http_access为allow all，那么代理服务器的安全组就需要设置为只对lambda所在VPC的NAT网段放行，否则不安全grep -n http /etc/squid/squid.conf |grep -w all56:http_access allow all

 

sudo vi /etc/squid/squid.confhttp_port 80sudo systemctl start squidsudo systemctl status squidsudo systemctl enable squid.service

 

1.2 建表
新加坡区域
在新加坡区域建立DynamoDB表user-sg，并开启DynamoDB stream（NEW_AND_OLD_IMAGES类型）,设置容量模式为On-Demand。再创建一个replicator_stats表用来记录更新记录以便比对数据同步进度。

在新加坡区域的代理服务器上通过json创建DynamoDB表，先编写json如下，读者在工作中可以按照实际情况修改相关配置。

vim user-sg.json

{

       "AttributeDefinitions": [
    
           {
    
               "AttributeName": "PK",
    
               "AttributeType": "S"
    
           }
    
       ],
    
       "BillingMode": "PAY_PER_REQUEST",
    
       "TableName": "user-sg",
    
       "StreamSpecification": {
    
           "StreamViewType": "NEW_AND_OLD_IMAGES",
    
           "StreamEnabled": true
    
       },
    
       "KeySchema": [
    
           {
    
               "KeyType": "HASH",
    
               "AttributeName": "PK"
    
           }
    
       ]

}

然后用命令创建DynamoDB表user-sg

aws dynamodb create-table --cli-input-json file://user-sg.json

最后可以验证

aws dynamodb describe-table --table-name user-sg 

然后用类似方法创建DynamoDB表replicator_stats

vim replicator_stats.json

{

       "AttributeDefinitions": [
    
           {
    
               "AttributeName": "PK",
    
               "AttributeType": "S"
    
           }
    
       ],
    
       "BillingMode": "PAY_PER_REQUEST",
    
       "TableName": "replicator_stats",
    
       "KeySchema": [
    
           {
    
               "KeyType": "HASH",
    
             "AttributeName": "PK"
    
           }
    
       ]

}

aws dynamodb create-table --cli-input-json file://replicator_stats.json

同样可以验证

aws dynamodb describe-table --table-name replicator_stats

北京区域
在北京区域建立user-cn，开启ddb stream（NEW_AND_OLD_IMAGES类型）,设置容量模式为On-Demand。再创建一个replicator_stats表用来记录更新记录以便比对数据同步速度。

在北京区域的代理服务器上通过json创建DynamoDB表，先编写json如下，读者在工作中可以按照实际情况修改相关配置。

vim user-cn.json

{

       "AttributeDefinitions": [
    
           {
    
               "AttributeName": "PK",
    
               "AttributeType": "S"
    
           }
    
       ],
    
       "BillingMode": "PAY_PER_REQUEST",
    
       "TableName": "user-cn",
    
       "StreamSpecification": {
    
           "StreamViewType": "NEW_AND_OLD_IMAGES",
    
           "StreamEnabled": true
    
       },
    
       "KeySchema": [
    
           {
    
               "KeyType": "HASH",
    
               "AttributeName": "PK"
    
           }
    
       ]

}

然后用命令创建DynamoDB表user-cn

aws dynamodb create-table --cli-input-json file://user-cn.json

最后可以验证

aws dynamodb describe-table --table-name user-cn

然后用类似方法创建DynamoDB表replicator_stats

vim replicator_stats.json

{

       "AttributeDefinitions": [
    
           {
    
             "AttributeName": "PK",
    
               "AttributeType": "S"
    
           }
    
       ],
    
       "BillingMode": "PAY_PER_REQUEST",
    
       "TableName": "replicator_stats",
    
       "KeySchema": [
    
           {
    
               "KeyType": "HASH",
    
                "AttributeName": "PK"
    
           }
    
       ]

}

aws dynamodb create-table --cli-input-json file://replicator_stats.json

同样可以验证

aws dynamodb describe-table --table-name replicator_stats

1.3 准备parameter store里的参数
新加坡区域
创建/DDBReplication/TableCN/AccessKey （String）和/DDBReplication/TableCN/SecretKey（SecureString），分别存入中国区用户的AK/SK

aws ssm put-parameter --name /DDBReplication/TableCN/AccessKey --value <access_key> --type String --profile sgaws ssm put-parameter --name /DDBReplication/TableCN/SecretKey --value <secret_key> --type SecureString --profile sg

北京区域
创建/DDBReplication/TableSG/AccessKey （String）和/DDBReplication/TableSG/SecretKey（SecureString），分别存入Global用户的AK/SK

aws ssm put-parameter --name /DDBReplication/TableSG/AccessKey --value <access_key> --type String --profile cnaws ssm put-parameter --name /DDBReplication/TableSG/SecretKey --value <secret_key> --type SecureString --profile cn

1.4 创建SQS
新加坡区域
创建2个标准SQS用于Lambda的On-failure Destination。

aws sqs create-queue --queue-name ddbstreamsg --region ap-southeast-1aws sqs create-queue --queue-name ddbreplicatorsg --region ap-southeast-1

北京区域
创建2个标准SQS用于Lambda的On-failure Destination。

aws sqs create-queue --queue-name ddbstreamcn --region cn-north-1aws sqs create-queue --queue-name ddbreplicatorcn --region cn-north-1

1.5 创建data kinesis stream
新加坡区域
创建Kinesis Data Stream，因为DEMO中写入量有限，选取一个shard，实际生产中请酌情调整

aws kinesis create-stream --stream-name ddb_replication_stream_sg --shard-count 1  

北京区域
创建Kinesis Data Stream，因为DEMO中写入量有限，选取一个shard，实际生产中请酌情调整

aws kinesis create-stream --stream-name ddb_replication_stream_cn --shard-count 1   

1.6 创建Lambda role并且授权
新加坡区域
需要创建两个role为后续lambda使用

1、将DynamoDB stream中event发送到中国区的Kinesis的lambda需要以下权限：

* 访问DynamoDB stream的权限
* 访问Parameter Store的权限
* 访问lambda On-failure destination的SQS队列的权限
* 访问VPC的权限：这部分权限我们使用现成的policyAWSLambdaBasicExecutionRole 、AWSLambdaVPCAccessExecutionRole
vi ddb_send_to_kinesis_policy.json{   "Version": "2012-10-17",   "Statement": [       {           "Effect": "Allow",           "Action": "lambda:InvokeFunction",           "Resource": "arn:aws:lambda:ap-southeast-1:<Global account>:function:ddb-replicator-sg*"       },       {           "Effect": "Allow",           "Action": [               "logs:CreateLogGroup",               "logs:CreateLogStream",               "logs:PutLogEvents"           ],           "Resource": "arn:aws:logs:ap-southeast-1:<Global account>:*"       },       {           "Effect": "Allow",           "Action": [               "dynamodb:DescribeStream",               "dynamodb:GetRecords",              "dynamodb:GetShardIterator",               "dynamodb:ListStreams"           ],           "Resource": "arn:aws:dynamodb:ap-southeast-1:<Global account>:table/user-sg/stream/*"       },       {           "Effect": "Allow",           "Action": [               "ssm:GetParameterHistory",               "ssm:GetParametersByPath",               "ssm:GetParameters",               "ssm:GetParameter"           ],           "Resource": [               "arn:aws:ssm:ap-southeast-1:<Global account>:parameter/DDBReplication/TableCN/*"           ]       },       {           "Effect": "Allow",           "Action": [               "sqs:*"           ],           "Resource": [               "arn:aws:sqs:ap-southeast-1:<Global account>:ddbstreamsg"           ]       }   ]}aws iam create-policy --policy-name ddb_send_to_kinesis_policy --policy-document file://ddb_send_to_kinesis_policy.jsonvi lambda-role-trust-policy.json{   "Version": "2012-10-17",   "Statement": {       "Effect": "Allow",       "Principal": { "Service": "lambda.amazonaws.com" },       "Action": "sts:AssumeRole"   }}aws iam create-role --role-name ddb_send_to_kinesis_role --assume-role-policy-document file://lambda-role-trust-policy.jsonaws iam attach-role-policy --role-name ddb_send_to_kinesis_role --policy-arn arn:aws:iam::aws:policy/service-role/AWSLambdaVPCAccessExecutionRoleaws iam attach-role-policy --role-name ddb_send_to_kinesis_role --policy-arn arn:aws:iam::<Global account>:policy/ddb_send_to_kinesis_policy2、将新加坡kinesis data stream中event写入新加坡区DDB需要以下权限：

* 访问kinesis data stream的权限
* 访问cloudwatch的权限
* 访问user-cn与replicator_stats2个DynamoDB表的权限
* 访问lambda On-failure destination的SQS队列的权限
* 访问VPC的权限：这部分权限我们使用现成的policy AWSLambdaBasicExecutionRole 、AWSLambdaVPCAccessExecutionRole
vi replicator_ddb_policy.json{   "Version": "2012-10-17",   "Statement": [       {           "Action": [               "kinesis:DescribeStreamSummary",               "kinesis:DescribeStream",               "kinesis:GetRecords",               "kinesis:GetShardIterator",               "kinesis:ListShards",               "kinesis:SubscribeToShard"           ],           "Resource": "arn:aws:kinesis:ap-southeast-1:<Global account>:stream/ddb_replication_stream_sg",           "Effect": "Allow"       },       {           "Action": [               "sqs:SendMessage",               "sqs:GetQueueAttributes",               "sqs:GetQueueUrl"           ],           "Resource": "arn:aws:sqs:ap-southeast-1:<Global account>:ddbreplicatorsg",           "Effect": "Allow"       },       {           "Action": [               "dynamodb:BatchGetItem",               "dynamodb:GetRecords",               "dynamodb:GetShardIterator",               "dynamodb:Query",               "dynamodb:GetItem",               "dynamodb:Scan",               "dynamodb:BatchWriteItem",               "dynamodb:PutItem",               "dynamodb:UpdateItem",               "dynamodb:DeleteItem"           ],           "Resource": [               "arn:aws:dynamodb:ap-southeast-1:<Global account>:table/replicator_stats"           ],           "Effect": "Allow"       },       {           "Action": [               "dynamodb:BatchGetItem",               "dynamodb:GetRecords",               "dynamodb:GetShardIterator",              "dynamodb:Query",               "dynamodb:GetItem",               "dynamodb:Scan",               "dynamodb:BatchWriteItem",               "dynamodb:PutItem",               "dynamodb:UpdateItem",               "dynamodb:DeleteItem"           ],           "Resource": [               "arn:aws:dynamodb:ap-southeast-1:<Global account>:table/user-sg"           ],           "Effect": "Allow"       },       {           "Action": "cloudwatch:PutMetricData",           "Resource": "*",           "Effect": "Allow"       }   ]}aws iam create-policy --policy-name replicator_ddb_policy --policy-document aws iam create-role --role-name replicator_ddb_role --assume-role-policy-document file://lambda-role-trust-policy.jsonaws iam attach-role-policy --role-name replicator_ddb_role --policy-arn arn:aws-cn:iam::aws:policy/service-role/AWSLambdaVPCAccessExecutionRoleaws iam attach-role-policy --role-name replicator_ddb_role --policy-arn arn:aws:iam::<Global account>:policy/replicator_ddb_policyaws iam attach-role-policy --role-name replicator_ddb_role --policy-arn arn:aws-cn:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole   

北京区域
需要创建两个role为后续lambda使用1、将DynamoDB stream中event发送到新加坡区的Kinesis需要以下权限：

访问DynamoDB stream的权限
访问Parameter Store的权限
访问lambda On-failure destination的SQS队列的权限
访问VPC的权限：这部分权限我们使用现成的policy AWSLambdaBasicExecutionRole 、AWSLambdaVPCAccessExecutionRole
 vi ddb_send_to_kinesis_policy.json{   "Version": "2012-10-17",   "Statement": [       {           "Effect": "Allow",           "Action": "lambda:InvokeFunction",            "Resource": "arn:aws-cn:lambda:cn-north-1:<China account>:function:ddb-replicator-cn*"       },       {           "Effect": "Allow",           "Action": [               "logs:CreateLogGroup",               "logs:CreateLogStream",                "logs:PutLogEvents"           ],           "Resource": "arn:aws-cn:logs:cn-north-1:<China account>:*"       },       {           "Effect": "Allow",           "Action": [               "dynamodb:DescribeStream",               "dynamodb:GetRecords",               "dynamodb:GetShardIterator",               "dynamodb:ListStreams"           ],           "Resource": "arn:aws-cn:dynamodb:cn-north-1:<China account>:table/user-cn/stream/*"       },       {           "Effect": "Allow",           "Action": [               "ssm:GetParameterHistory",               "ssm:GetParametersByPath",               "ssm:GetParameters",               "ssm:GetParameter"           ],           "Resource": [               "arn:aws-cn:ssm:cn-north-1:<China account>:parameter/DDBReplication/TableSG/*"           ]       },       {           "Effect": "Allow",           "Action": [               "sqs:*"           ],           "Resource": [               "arn:aws-cn:sqs:cn-north-1:<China account>:ddbstreamcn"           ]       }   ]}aws iam create-policy --policy-name ddb_send_to_kinesis_policy --policy-document file://ddb_send_to_kinesis_policy.jsonvi lambda-role-trust-policy.json{   "Version": "2012-10-17",   "Statement": {       "Effect": "Allow",       "Principal": { "Service": "lambda.amazonaws.com" },       "Action": "sts:AssumeRole"   }}aws iam create-role --role-name ddb_send_to_kinesis_role --assume-role-policy-document file://lambda-role-trust-policy.jsonaws iam attach-role-policy --role-name ddb_send_to_kinesis_role --policy-arn arn:aws-cn:iam::aws:policy/service-role/AWSLambdaVPCAccessExecutionRoleaws iam attach-role-policy --role-name ddb_send_to_kinesis_role --policy-arn arn:aws-cn:iam::<China account>:policy/ddb_send_to_kinesis_policy 2、将kinesis data stream中event写入DDB需要以下权限：

访问kinesis data stream的权限
访问cloudwatch的权限
访问user-cn与replicator_stats2个DynamoDB表的权限
访问lambda On-failure destination的SQS队列的权限
访问VPC的权限：这部分权限我们使用现成的policy AWSLambdaBasicExecutionRole 、AWSLambdaVPCAccessExecutionRole
 vi replicator_ddb_policy.json{    "Version": "2012-10-17",   "Statement": [       {           "Action": [               "kinesis:DescribeStreamSummary",               "kinesis:DescribeStream",               "kinesis:GetRecords",               "kinesis:GetShardIterator",              "kinesis:ListShards",               "kinesis:SubscribeToShard"           ],           "Resource": "arn:aws-cn:kinesis:cn-north-1:<China account>:stream/ddb_replication_stream_cn",           "Effect": "Allow"       },       {            "Action": [               "sqs:SendMessage",               "sqs:GetQueueAttributes",               "sqs:GetQueueUrl"           ],           "Resource": "arn:aws-cn:sqs:cn-north-1:<China account>:ddbreplicatorcn",           "Effect": "Allow"       },       {           "Action": [               "dynamodb:BatchGetItem",               "dynamodb:GetRecords",               "dynamodb:GetShardIterator",               "dynamodb:Query",               "dynamodb:GetItem",              "dynamodb:Scan",               "dynamodb:BatchWriteItem",               "dynamodb:PutItem",               "dynamodb:UpdateItem",               "dynamodb:DeleteItem"           ],           "Resource": [               "arn:aws-cn:dynamodb:cn-north-1:<China account>:table/replicator_stats"           ],           "Effect": "Allow"       },       {           "Action": [               "dynamodb:BatchGetItem",               "dynamodb:GetRecords",               "dynamodb:GetShardIterator",               "dynamodb:Query",               "dynamodb:GetItem",               "dynamodb:Scan",               "dynamodb:BatchWriteItem",               "dynamodb:PutItem",               "dynamodb:UpdateItem",               "dynamodb:DeleteItem"           ],           "Resource": [               "arn:aws-cn:dynamodb:cn-north-1:<China account>:table/user-cn"           ],           "Effect": "Allow"       },       {           "Action": "cloudwatch:PutMetricData",          "Resource": "*",           "Effect": "Allow"       }   ]}aws iam create-policy --policy-name replicator_ddb_policy --policy-document file://replicator_ddb_policy.jsonaws iam create-role --role-name replicator_ddb_role --assume-role-policy-document file://lambda-role-trust-policy.jsonaws iam attach-role-policy --role-name replicator_ddb_role --policy-arn arn:aws-cn:iam::aws:policy/service-role/AWSLambdaVPCAccessExecutionRoleaws iam attach-role-policy --role-name replicator_ddb_role --policy-arn arn:aws-cn:iam::<China account>:policy/replicator_ddb_policyaws iam attach-role-policy --role-name replicator_ddb_role --policy-arn arn:aws-cn:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole   

1.7 创建send to kinesis 的lambda函数
新加坡区域
创建lambda
创建python lambda function命名为ddb-send-to-kinesis，上传ddb_send_to_kinesis的Lambda代码，编辑send_to_kinesis.py，代码请见send_to_kinesis.py

zip send_kinesis.zip send_to_kinesis.pyaws lambda create-function --role arn:aws:iam::<Global account>:role/ddb_send_to_kinesis_role --runtime python3.7 --function-name ddb_send_to_kinesis --handler send_to_kinesis.lambda_handler --zip-file fileb://send_kinesis.zip --timeout 60 --region ap-southeast-1 

设置环境变量
为lambda添加五个环境变量，第一个用来从parameter store中获取中国区的Access Key和Secret Key的路径

Key

Value

PARAMETER_STORE_PATH_PREFIX

/DDBReplication/TableCN/

TARGET_REGION

cn-north-1

TARGET_STREAM

ddb_replication_stream_cn

USE_PROXY

FALSE

PROXY_SERVER

<China region proxy IP>:<port>

aws lambda update-function-configuration --function-name ddb_send_to_kinesis --environment "Variables={PARAMETER_STORE_PATH_PREFIX=/DDBReplication/TableCN/, TARGET_REGION=cn-north-1, TARGET_STREAM=ddb_replication_stream_cn, USE_PROXY=FALSE,PROXY_SERVER=<China region proxy IP>:<port>} "

创建触发器
通过lambda页面选中ddb_send_to_kinesis，而后选择add trigger，下拉框中选择DynamoDB，而后填写以下信息：

从DynamoDB console获取我们开启的DDB stream的arn，填写到DDB table处
将SQS：ddbstreamsg的arn填写到On-failure destination处
Concurrent batches per shard设为10
Batch size设为500
Retry attempts: 300
Maximum age of record: 1 Day
Timeout默认3秒，设置为1分钟
通过命令查询aws lambda list-event-source-mappings --function-name ddb_send_to_kinesis --region ap-southeast-1

将lambda置于VPC内部
在lambda页面选中ddb_send_to_kinesis，在VPC界面进行设置，选中两个AZ中的私有子网，并选择预先设置好security group，向北京区代理服务器的EIP网段开放http/https接口

北京区域
创建lambda
创建python lambda function命名为ddb-send-to-kinesis，上传ddb_send_to_kinesis的Lambda代码，编辑send_to_kinesis.py，代码请见send_to_kinesis.py

zip send_kinesis.zip send_to_kinesis.pyaws lambda create-function --role arn:aws-cn:iam::<China account>:role/ddb_send_to_kinesis_role --runtime python3.7 --function-name ddb_send_to_kinesis --handler send_to_kinesis.lambda_handler --zip-file fileb://send_kinesis.zip --timeout 60 --region cn-north-1 

设置环境变量
为lambda添加五个环境变量，第一个用来从parameter store中获取中国区的Access Key和Secret Key的路径

Key

Value

PARAMETER_STORE_PATH_PREFIX

/DDBReplication/TableSG/

TARGET_REGION

ap-southeast-1

TARGET_STREAM

ddb_replication_stream_sg

USE_PROXY

FALSE

PROXY_SERVER

<Singapore region proxy IP>:<port>

aws lambda update-function-configuration --function-name ddb_send_to_kinesis --environment "Variables={PARAMETER_STORE_PATH_PREFIX=/DDBReplication/TableSG/,TARGET_REGION=ap-southeast-1,TARGET_STREAM=ddb_replication_stream_sg,USE_PROXY=FALSE,PROXY_SERVER=<Singapore region proxy IP>:<port>}"

创建触发器
通过lambda页面选中ddb_send_to_kinesis，而后选择add trigger，下拉框中选择DynamoDB，而后填写以下信息：

从DynamoDB console获取我们开启的DDB stream的arn，填写到DDB table处
将SQS：ddbstreamcn的arn填写到On-failure destination处
Concurrent batches per shard设为10
Batch size设为500
Retry attempts: 300
Maximum age of record: 1 Day。
Timeout默认3秒，设置为1分钟
aws lambda list-event-source-mappings --function-name ddb_send_to_kinesis --region cn-north-1

将lambda置于VPC内部
在lambda页面选中ddb_send_to_kinesis，在VPC界面进行设置，选中两个AZ中的私有子网，并选择预先设置好security group，向新加坡区代理服务器的EIP网段开放http/https接口

 

1.8 创建消费Kinesis Stream的Lambda函数
新加坡区域
创建lambda
创建python lambda function命名为replicator_ddb，上传replicator_ddb的Lambda代码，编辑replicator_ddb.py，代码请见 replicator_ddb.py

zip replicator_ddb.zip replicator_ddb.pyaws lambda create-function --role arn:aws:iam::<Global account>:role/replicator_ddb_role --runtime python3.7 --function-name replicator_ddb --handler replicator_ddb.lambda_handler --zip-file fileb://replicator_ddb.zip --timeout 60 --region ap-southeast-1

设置环境变量
aws lambda update-function-configuration --function-name replicator_ddb --environment "Variables={TARGET_TABLE=user-sg}"

 

创建触发器
通过lambda页面选中replicator_ddb，而后选择add trigger，下拉框中选择Kinesis，而后填写以下信息：

下拉菜单中选取ddb_replication_stream_sg
将SQS：ddbreplicatorsg的arn填写到On-failure destination处
Concurrent batches per shard：10
Batch size：500
Retry attempts:100




北京区域
创建lambda
创建python lambda function命名为replicator_ddb，上传replicator_ddb的Lambda代码，编辑replicator_ddb.py，代码请见 replicator_ddb.py

zip replicator_ddb.zip replicator_ddb.pyaws lambda create-function --role arn:aws-cn:iam::<China account>:role/replicator_ddb_role --runtime python3.7 --function-name replicator_ddb --handler replicator_ddb.lambda_handler --zip-file fileb://replicator_ddb.zip --timeout 60 --region cn-north-1

设置环境变量
aws lambda update-function-configuration --function-name replicator_ddb --environment "Variables={TARGET_TABLE=user-cn}"

 

创建触发器
通过lambda页面选中replicator_ddb，而后选择add trigger，下拉框中选择Kinesis，而后填写以下信息：

下拉菜单中选取ddb_replication_stream_cn
将SQS：ddbreplicatorcn的arn填写到On-failure destination处
Concurrent batches per shard：10
Batch size：500
Retry attempts:100


2、测试
2.1、准备加载数据脚本
在北京和新加坡的代理服务器上，生成load_items.py,代码请见 load_items.py

2.2、测试
单进程加载执行python3 load_items.py -n 5 -r sg，其中：

-n后的参数是加载记录数量，本例中是加载5条记录
-r后是指定区域，本例是指定Singapore
多进程并发加载可以执行seq 10 | parallel -N0 --jobs 0 "python3 load_items.py -n 5 -r sg"，其中：

seq后是并发数，本例中选择10个并发进程，每个加载5行数据

## License

This library is licensed under the MIT-0 License. See the LICENSE file.