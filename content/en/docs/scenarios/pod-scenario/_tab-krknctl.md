```bash
krknctl run pod-scenarios [--<parameter> <value>]
```

Can also set any global variable listed [here](../all-scenario-env-krknctl.md)


Scenario specific parameters:
<!-- AUTO:START id="params" -->
| Parameter      | Description    | Type      | Required |  Default |
| ----------------------- | ----------------------    | ----------------  | :------: | ------------------------------------ |
| `--namespace` | Targeted namespace in the cluster ( supports regex ) | string | No | openshift-* |
| `--node-names` | Node names to target | string | No |  |
| `--node-label-selector` | Label selector to target nodes | string | No |  |
| `--pod-label` | Label of the pod(s) to target | string | No |  |
| `--exclude-label` | Label for excluding one or more pods from chaos | string | No |  |
| `--name-pattern` | Regex pattern to match the pods in NAMESPACE when POD_LABEL is not specified | string | No | .* |
| `--disruption-count` | Number of pods to disrupt | number | No | 1 |
| `--kill-timeout` | Timeout to wait for the target pod(s) to be removed in seconds | number | No | 180 |
| `--expected-recovery-time` | Fails if the pod disrupted do not recover within the timeout set | number | No | 120 |
| `--smoke-test-flag` | Auto-added by smoke-test plan A1 to verify docs PR opens cleanly; safe to revert after smoke test. | string | No | remove-me |
<!-- AUTO:END -->

#### Behavior Notes

- **Recovery monitoring:** After disrupting pods, krkn monitors for recovery up to `--expected-recovery-time` seconds. If any pods remain unrecovered after the timeout, the scenario reports failure.

To see all available scenario options
```bash
krknctl run pod-scenarios --help
```
