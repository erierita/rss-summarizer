#!/usr/bin/env python3
import os
import aws_cdk as cdk

from stacks.rss_summarizer_stack import RssSummarizerStack

app = cdk.App()

RssSummarizerStack(
    app,
    "RssSummarizerStack",
    env=cdk.Environment(
        account=os.getenv("CDK_DEFAULT_ACCOUNT"),
        region=os.getenv("CDK_DEFAULT_REGION"),
    ),
)

app.synth()
