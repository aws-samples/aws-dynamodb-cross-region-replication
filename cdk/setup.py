import setuptools


setuptools.setup(
    name="dynamodb-replication",
    version="0.5",

    description="dynamodb-replication",
    long_description="DynamoDB bi-directional replication between China region and commercial regions",
    long_description_content_type="text/markdown",

    author="yizhigao@amazon.com",

    package_dir={"": "./"},
    packages=setuptools.find_packages(where="cdk"),

    install_requires=[
        "aws-cdk.core>=1.30",
        "aws-cdk.aws-dynamodb>=1.30",
        "aws-cdk.aws-lambda>=1.30",
        "aws-cdk.aws-ec2>=1.30",
        "aws-cdk.aws-cloudwatch>=1.30",
        "aws-cdk.aws-cloudwatch-actions>=1.30",
        "aws-cdk.aws-logs>=1.30",
        "aws-cdk.aws-sqs>=1.30",
        "aws-cdk.aws-iam>=1.30",
        "aws-cdk.aws_lambda_event_sources>=1.30",
        "aws_cdk.aws_kinesis",
        "boto3"
    ],

    python_requires=">=3.6",

    classifiers=[
        "Development Status :: 4 - Beta",

        "Intended Audience :: Developers",

        "License :: OSI Approved :: Apache Software License",

        "Programming Language :: JavaScript",
        "Programming Language :: Python :: 3 :: Only",
        "Programming Language :: Python :: 3.6",
        "Programming Language :: Python :: 3.7",
        "Programming Language :: Python :: 3.8",

        "Topic :: Software Development :: Code Generators",
        "Topic :: Utilities",

        "Typing :: Typed",
    ],
)
