#!/usr/bin/env python3
import logging
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError
from config import slack_token, slack_channel

logging.basicConfig(level=logging.INFO)

client = WebClient(token=slack_token)

def post_message(text):
    try:
        response = client.chat_postMessage(channel=slack_channel, text=text)
        logging.info("Message posted to Slack: %s", response['ts'])
    except SlackApiError as e:
        logging.error("Error posting to Slack: %s", e.response['error'])

