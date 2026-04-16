#!/bin/bash
# Setup script for agntdata Reddit Listener
# Run once on a fresh VPS or local machine

set -e

echo "Setting up agntdata Reddit Listener..."

# 1. Install Python dependencies
pip3 install requests

# 2. Create state directory
mkdir -p state

# 3. Create .env from template if it doesn't exist
if [ ! -f .env ]; then
    cp .env.example .env
    echo ""
    echo "Created .env — fill in your API keys before running."
fi

# 4. Test the config
echo ""
echo "Testing agntdata connection..."
python3 -c "
import os, requests
key = open('.env').read()
for line in key.splitlines():
    if line.startswith('AGNTDATA_API_KEY='):
        k = line.split('=',1)[1].strip().strip('\"')
        break
r = requests.get('https://api.agntdata.dev/v1/platforms', headers={'Authorization': 'Bearer ' + k}, timeout=10)
print('agntdata API:', 'OK' if r.json().get('success') else 'FAILED - check your key')
" 2>/dev/null || echo "Skipping API test (fill in .env first)"

echo ""
echo "Done. Next steps:"
echo "  1. Edit .env with your API keys"
echo "  2. Test: python3 listener.py"
echo "  3. Schedule: see cron.txt for crontab entry"
