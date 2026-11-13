#!/usr/bin/env bash
# Push the current working tree to the demo EC2 server and restart the app.
#   scripts/deploy_ec2.sh <server-ip> [path-to-ssh-key]
# One-time server setup (Ubuntu 24.04): python3-venv, rsync, caddy; /opt/voicebot owned by ubuntu;
# /etc/yash-voicebot.env (the .env values, mode 600); the voicebot.service unit and Caddyfile from deploy/.
set -euo pipefail
IP="${1:?usage: deploy_ec2.sh <server-ip> [ssh-key]}"
KEY="${2:-$HOME/.ssh/yash-voicebot-key.pem}"
cd "$(dirname "$0")/.."
SSH=(ssh -i "$KEY" -o ConnectTimeout=10 "ubuntu@$IP")

rsync -az --delete -e "ssh -i $KEY -o ConnectTimeout=10" --exclude='__pycache__' app knowledge static requirements.txt "ubuntu@$IP:/opt/voicebot/"
"${SSH[@]}" 'cd /opt/voicebot && ( [ -d .venv ] || python3 -m venv .venv ) && .venv/bin/pip install -q -r requirements.txt && sudo systemctl restart voicebot && sleep 4 && curl -fsS localhost:8000/api/health && echo'
echo "deployed to $IP"
