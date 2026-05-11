
```bash
krknctl run application-outages [--<parameter> <value>]
```

Can also set any global variable listed [here](../all-scenario-env-krknctl.md)


Scenario specific parameters: 
<!-- AUTO:START id="params" -->
| Parameter      | Description    | Type      | Required    | Default | 
| ----------------------- | ----------------------    | ----------------   | ---------------- | ------------------------------------ |
| `--chaos-duration` | Set chaos duration (in sec) as desired | number | No | 600 |
| `--namespace` | Namespace to target - all application routes will go inaccessible if pod selector is empty ( Required ) | string | Yes |  |
| `--pod-selector` | Pods to target. For example "{app: foo}" | string | Yes |  |
| `--exclude-selector` | Pods to exclude from targetting. For example "{app: foo}" | string | No |  |
| `--block-traffic-type` | It can be [Ingress] or [Egress] or [Ingress, Egress] | string | No | [Ingress, Egress] |
| `--smoke-a4-param` | Auto-added across 3 scenarios by docs-sync matrix A4; tests STATE.md handoff. | string | No | default-a4 |
<!-- AUTO:END -->

#### Behavior Notes

- **Empty `--pod-selector`:** When left empty, krkn creates a NetworkPolicy that targets **all pods** in the namespace, causing a namespace-wide outage.
- **Automatic cleanup:** After `--chaos-duration` expires, krkn automatically deletes the NetworkPolicy it created and traffic resumes. A rollback handler is also registered to ensure cleanup if the scenario fails unexpectedly.

To see all available scenario options 
```bash
krknctl run application-outages --help
```