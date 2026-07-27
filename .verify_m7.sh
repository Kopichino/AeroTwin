#!/usr/bin/env bash
# One-shot M7 verification: boot the API, exercise the new endpoints, report.
set -uo pipefail
cd /home/user/aero-twin
source .venv/bin/activate

pkill -f "uvicorn at_api" 2>/dev/null
sleep 2

AT_TWIN_SUBSET=FD001 AT_REPLAY_SPEED=8 \
  uvicorn at_api.main:app --port 8000 --log-level warning >/tmp/api_m7.log 2>&1 &
API_PID=$!

for _ in $(seq 1 60); do
  if curl -sf -o /dev/null http://127.0.0.1:8000/health/live 2>/dev/null; then break; fi
  sleep 2
done

if ! curl -sf -o /dev/null http://127.0.0.1:8000/health/live; then
  echo "API failed to start"; tail -5 /tmp/api_m7.log; kill $API_PID 2>/dev/null; exit 1
fi

# Give the twin runner time to accumulate history.
sleep 25

EID=$(curl -s "http://127.0.0.1:8000/api/v1/fleet?size=1" \
  | python3 -c "import json,sys; print(json.load(sys.stdin)['items'][0]['engine_id'])")
echo "engine: $EID"

echo "--- history ---"
curl -s "http://127.0.0.1:8000/api/v1/engines/$EID/history?limit=200" | python3 -c "
import json, sys
d = json.load(sys.stdin); s = d['samples']
print(f\"  {d['count']} samples\")
print(f\"  first: c{s[0]['cycle']} HI={s[0]['health_index']} RUL={s[0]['rul_p50']}\")
print(f\"  last : c{s[-1]['cycle']} HI={s[-1]['health_index']} RUL={s[-1]['rul_p50']} anom={s[-1]['anomaly_score']}\")
print(f\"  band present: {'rul_p10' in s[-1] and s[-1]['rul_p10'] is not None}\")
print(f\"  sensors/sample: {len(s[-1]['sensors'])}, components: {len(s[-1]['components'])}\")"

echo "--- explain ---"
curl -s "http://127.0.0.1:8000/api/v1/engines/$EID/explain" | python3 -c "
import json, sys
d = json.load(sys.stdin)
print(f\"  available: {d['available']}\")
if d['available']:
    print(f\"  RUL {d['rul_p50']} [{d['rul_p10']}, {d['rul_p90']}]\")
    for a in d['attributions'][:5]:
        print(f\"    {a['name']:<9} {a['value']:.4f} {a['direction']:<5} {a['module']}\")
    top = list(d['module_scores'].items())[:3]
    print(f\"  modules: {top}\")
else:
    print(f\"  reason: {d['reason']}\")"

echo "--- system ---"
curl -s http://127.0.0.1:8000/api/v1/system | python3 -c "
import json, sys
d = json.load(sys.stdin)
print(f\"  ticks {d['ticks']} | tick p99 {d['tick_p99_ms']}ms | history {d['history']}\")"

# Capture a snapshot for the static preview.
curl -s "http://127.0.0.1:8000/api/v1/engines/$EID/history?limit=200" >/tmp/m7_history.json
curl -s "http://127.0.0.1:8000/api/v1/engines/$EID/explain" >/tmp/m7_explain.json
curl -s "http://127.0.0.1:8000/api/v1/engines/$EID" >/tmp/m7_detail.json
echo "snapshots captured"

kill $API_PID 2>/dev/null
wait $API_PID 2>/dev/null
echo "done"
