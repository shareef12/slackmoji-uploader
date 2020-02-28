# slackmoji-uploader

Automatically upload emojis from slackmojis.com to a Slack workspace.


## Installation

This package can be installed with pip. It also requires selenium's Chrome
driver.

```
python3 -m pip install -U .
```

The chrome driver can be downloaded from
<https://chromedriver.chromium.org/downloads> and must be installed somewhere
in your PATH.


## Usage

To auto-upload emojis, specify your full workspace URL on the command line. The
script will check the environment for credentials.

```
# Store your workspace credentials in the environment
export SLACK_EMAIL=<your-email>
read -s SLACK_PASSWORD
<your-password>
export SLACK_PASSWORD

# Upload emojis!
slackmoji-uploader https://<workspace>.slack.com
```
