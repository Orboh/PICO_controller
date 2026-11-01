#!/usr/bin/env bash
# Extract replay frames from a sender log: bash tools/frames.sh runs/<tag>-sender.jsonl > <out>.frames.jsonl
python3 -c '
import json,sys
for l in open(sys.argv[1]):
    d=json.loads(l)
    if "frame" in d: print(json.dumps(d["frame"]))
' "$1"
