#!/bin/bash
# Setup script for Render frontends

export RENDER_API_KEY=$(cat .render/api-key)
OWNER_ID="tea-d2gv3pf5r7bs73fh82eg"
REPO="https://github.com/bopoadz-del/Cerebrum-Blocks"

echo "Creating Store Frontend..."
curl -s -X POST https://api.render.com/v1/services \
  -H "Accept: application/json" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $RENDER_API_KEY" \
  -d "{
    \"type\": \"static_site\",
    \"name\": \"store\",
    \"ownerId\": \"$OWNER_ID\",
    \"repo\": \"$REPO\",
    \"branch\": \"main\"
  }" | python3 -c "import json,sys; d=json.load(sys.stdin); print('Store:', d.get('service',{}).get('id','ERROR'))"

echo "Creating Platform Frontend..."
curl -s -X POST https://api.render.com/v1/services \
  -H "Accept: application/json" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $RENDER_API_KEY" \
  -d "{
    \"type\": \"static_site\",
    \"name\": \"platform\",
    \"ownerId\": \"$OWNER_ID\",
    \"repo\": \"$REPO\",
    \"branch\": \"main\"
  }" | python3 -c "import json,sys; d=json.load(sys.stdin); print('Platform:', d.get('service',{}).get('id','ERROR'))"

echo "Done. Now go to Render Dashboard and set:"
echo "  store: Build Command = cd frontend-store && npm install && npm run build"
echo "  store: Publish Directory = frontend-store/dist"
echo "  platform: Build Command = cd frontend-platform && npm install && npm run build"
echo "  platform: Publish Directory = frontend-platform/dist"