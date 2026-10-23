# Using Aether in CI

1. In Aether, open **Settings** and create an API key (e.g. `github-actions`).
2. Make sure the agent has a ready test suite and, optionally, a release gate (agent page, **Release gate**).
3. Add the gate step to your pipeline.

## GitHub Actions

```yaml
name: agent-eval
on: [pull_request]
jobs:
  gate:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/setup-python@v5
        with: { python-version: "3.12" }
      - run: pip install "aether @ git+https://github.com/<you>/aether#subdirectory=backend"
      - run: aether gate --agent order-support --model openai/gpt-4o-mini --trials 3
        env:
          AETHER_URL: ${{ vars.AETHER_URL }}
          AETHER_API_KEY: ${{ secrets.AETHER_API_KEY }}
```

If your pipeline changes the agent's prompt, push it first as a new version
(`POST /api/v1/agents/{slug}/versions`) and pass `--version N`.

## Exit codes

| Code | Meaning |
|---|---|
| 0 | Run completed and the release gate passed |
| 1 | Run completed and the release gate failed |
| 2 | The run could not be started or did not complete |

## Raw API

```bash
curl -X POST "$AETHER_URL/api/v1/ci/runs" -H "Authorization: Bearer $AETHER_API_KEY" \
  -H 'Content-Type: application/json' \
  -d '{"agent": "order-support", "models": ["openai/gpt-4o-mini"], "trials": 3}'
# poll until "done": true
curl "$AETHER_URL/api/v1/runs/<id>/gate" -H "Authorization: Bearer $AETHER_API_KEY"
```

## Gate rules

```json
{
  "name": "production",
  "rules": [
    {"metric": "pass_rate", "op": ">=", "value": 0.85, "label": "Attempts passed"},
    {"metric": "all_pass_rate", "op": ">=", "value": 0.7, "label": "Tasks passed on every attempt"},
    {"metric": "category:policy:pass_rate", "op": ">=", "value": 1.0, "label": "Policy tasks"},
    {"metric": "latency_p95_ms", "op": "<=", "value": 8000, "label": "p95 latency"},
    {"metric": "cost_per_trial_usd", "op": "<=", "value": 0.03, "label": "Cost per attempt"}
  ]
}
```
