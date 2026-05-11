# docs-sync STATE

> **Source:** [antedotee/krkn-hub#5](https://github.com/antedotee/krkn-hub/pull/5)
> **Head:** `41f02cc768ce` → **Base:** `59fe5ef01b7d`
> **Started:** 2026-05-11T12:41:50+00:00 · **Updated:** 2026-05-11T12:42:13+00:00

## Progress

| Status | Count |
| --- | --- |
| `pending` | 3 |
| `done_regen` | 1 |
| `done_draft` | 1 |

## Scenarios

- **application-outages** (modified) — pending; targets: (none)
- **container-scenarios** (modified) — pending; targets: (none)
- **pod-scenarios** (modified) — pending; targets: (none)
- **pvc-scenario** (modified) — done_regen; targets: content/en/docs/scenarios/pvc-scenario/_tab-krkn-hub.md, content/en/docs/scenarios/pvc-scenario/_tab-krknctl.md
- **dummy-disruption** (added) — done_draft; targets: content/en/docs/scenarios/dummy-disruption/_index.md

**Total LLM output tokens:** 0
**Run complete:** yes

<!-- BEGIN MACHINE STATE — do not edit by hand -->
```json
{
  "base_sha": "59fe5ef01b7d04eed2b62a64c7679d3b3e966b80",
  "completed": true,
  "head_sha": "41f02cc768ce3048ef86ea44a1b969a36cbd45ac",
  "notes": "",
  "pr_number": 5,
  "scenarios": [
    {
      "change_type": "modified",
      "name": "application-outages",
      "notes": "",
      "status": "pending",
      "target_files": []
    },
    {
      "change_type": "modified",
      "name": "container-scenarios",
      "notes": "",
      "status": "pending",
      "target_files": []
    },
    {
      "change_type": "modified",
      "name": "pod-scenarios",
      "notes": "",
      "status": "pending",
      "target_files": []
    },
    {
      "change_type": "modified",
      "name": "pvc-scenario",
      "notes": "",
      "status": "done_regen",
      "target_files": [
        "content/en/docs/scenarios/pvc-scenario/_tab-krkn-hub.md",
        "content/en/docs/scenarios/pvc-scenario/_tab-krknctl.md"
      ]
    },
    {
      "change_type": "added",
      "name": "dummy-disruption",
      "notes": "",
      "status": "done_draft",
      "target_files": [
        "content/en/docs/scenarios/dummy-disruption/_index.md"
      ]
    }
  ],
  "started_at": "2026-05-11T12:41:50+00:00",
  "token_usage_total": 0,
  "updated_at": "2026-05-11T12:42:13+00:00",
  "upstream_repo": "antedotee/krkn-hub"
}
```
<!-- END MACHINE STATE -->
