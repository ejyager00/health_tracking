#!/usr/bin/env bash
# Import historical workouts from historical_test_data.json to the workout API.
#
# Usage:
#   chmod +x scripts/import_historical.sh
#   API_KEY=wk_yourkey BASE_URL=https://your-app.workers.dev ./scripts/import_historical.sh
#
# Or inline:
#   API_KEY=wk_yourkey BASE_URL=https://your-app.workers.dev bash scripts/import_historical.sh

set -euo pipefail

API_KEY="${API_KEY:-YOUR_API_KEY_HERE}"
BASE_URL="${BASE_URL:-https://YOUR_APP.workers.dev}"
INPUT="${INPUT:-historical_test_data.json}"

if [[ "$API_KEY" == "YOUR_API_KEY_HERE" ]]; then
  echo "Error: set API_KEY env var before running." >&2
  exit 1
fi

if [[ "$BASE_URL" == *"YOUR_APP"* ]]; then
  echo "Error: set BASE_URL env var before running." >&2
  exit 1
fi

total=$(jq 'length' "$INPUT")
echo "Importing $total workouts from $INPUT → $BASE_URL"
echo

jq -c '.[]' "$INPUT" | while IFS= read -r workout; do
  date=$(echo "$workout" | jq -r '.date | split(" ")[0]')

  payload=$(echo "$workout" | jq '{
    date: (.date | split(" ")[0]),
    notes: (.notes // ""),
    lifts: [
      .lifts | to_entries[] | {
        lift_name: .value.exercise,
        superset_id: null,
        position: .key,
        sets: [
          .value.sets | to_entries[] | {
            set_number: (.key + 1),
            reps: .value.reps,
            weight: (.value.weight_lbs | if . <= 0 then 0 else . end)
          }
        ]
      }
    ]
  }')

  echo -n "  POST $date ... "

  http_code=$(curl -s -o /tmp/api_response.json -w "%{http_code}" \
    -X POST "$BASE_URL/api/workouts" \
    -H "Authorization: ApiKey $API_KEY" \
    -H "Content-Type: application/json" \
    -d "$payload")

  if [[ "$http_code" == "200" || "$http_code" == "201" ]]; then
    id=$(jq -r '.id // "ok"' /tmp/api_response.json 2>/dev/null || echo "ok")
    echo "✓ ($http_code) id=$id"
  else
    echo "✗ ($http_code)"
    jq '.' /tmp/api_response.json 2>/dev/null || cat /tmp/api_response.json
  fi
done

echo
echo "Done."
