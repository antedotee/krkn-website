
```bash
krknctl run container-scenarios [--<parameter> <value>]
```

Can also set any global variable listed [here](../all-scenario-env-krknctl.md)


Scenario specific parameters: 
<!-- AUTO:START id="params" -->
| Parameter      | Description    | Type      | Required |  Default | 
| ----------------------- | ----------------------    | ----------------  | :------: | ------------------------------------ |
| `--namespace` | Targeted namespace in the cluster | string | No | openshift-etcd |
| `--label-selector` | Label of the container(s) to target | string | No | k8s-app=etcd |
| `--exclude-selector` | Pods to exclude from targetting. For example "{app: foo}" | string | No |  |
| `--disruption-count` | Number of container to disrupt | number | No | 1 |
| `--container-name` | Name of the container to disrupt | string | No | etcd |
| `--action` | kill signal to run. For example 1 ( hang up ) or 9 | string | No | 1 |
| `--expected-recovery-time` | Time to wait before checking if all containers that were affected recover properly | number | No | 60 |
| `--smoke-a4-param` | Auto-added across 3 scenarios by docs-sync matrix A4; tests STATE.md handoff. | string | No | default-a4 |
<!-- AUTO:END -->


#### Behavior Notes

- **Recovery monitoring:** After disrupting containers, krkn monitors for recovery up to `--expected-recovery-time` seconds. If any containers remain unrecovered after the timeout, the scenario reports failure.

To see all available scenario options 
```bash
krknctl run container-scenarios --help
```