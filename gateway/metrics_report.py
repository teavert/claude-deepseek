"""Summarize metadata-only gateway logs. Run from any working directory."""
import json
from pathlib import Path
from statistics import median


def summarize(records):
    done = [r for r in records if r.get('event') == 'complete' and 'duration_ms' in r]
    usage = [r.get('usage', {}) for r in done]
    cached = sum(u.get('cache_read_input_tokens', 0) for u in usage)
    fresh = sum(u.get('input_tokens', 0) + u.get('cache_creation_input_tokens', 0) for u in usage)
    ttft = [r['ttft_ms'] for r in done if r.get('ttft_ms') is not None]
    sizes = [r for r in records if r.get('event') == 'request_size']
    return {
        'measured_completed_requests':len(done),
        'median_first_output_ms':median(ttft) if ttft else None,
        'median_duration_ms':median([r['duration_ms'] for r in done]) if done else None,
        'fresh_input_tokens':fresh, 'cached_input_tokens':cached,
        'cache_hit_percent':round(100*cached/(fresh+cached),1) if fresh+cached else None,
        'output_tokens':sum(u.get('output_tokens',0) for u in usage),
        'retries':sum(r.get('event')=='retry' for r in records),
        'fallbacks':sum(r.get('event')=='fallback' for r in records),
        'capacity_skips':sum(r.get('event')=='fallback_skipped' for r in records),
        'latest_request_size':{k:v for k,v in sizes[-1].items() if k.endswith('_bytes') or k=='tool_count'} if sizes else None,
    }


if __name__ == '__main__':
    root=Path(__file__).resolve().parent
    records=[]
    port=json.loads((root/'config.json').read_text())['listen_port']
    base=root/f'routes-{port}.jsonl'
    for path in [Path(str(base)+'.2'),Path(str(base)+'.1'),base]:
        if path.exists():
            for line in path.read_text(encoding='utf-8').splitlines():
                try: records.append(json.loads(line))
                except ValueError: pass
    print(json.dumps(summarize(records),indent=2))
