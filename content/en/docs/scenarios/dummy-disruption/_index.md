---
title: Dummy Disruption
description:
weight: 99
---

The `dummy-disruption` scenario introduces a non-impactful disruption within a specified Kubernetes namespace, lasting for a configurable duration. This scenario serves as a safe and controlled method to test the underlying chaos engineering framework without causing actual service degradation.

## Why this matters

While the `dummy-disruption` scenario does not aim to cause actual service degradation, it is a foundational tool for validating the operational readiness of your chaos engineering platform. It allows engineers to confirm that the chaos injection mechanism is functioning correctly within a targeted `namespace` and that the system's monitoring and alerting infrastructure is robust enough to detect and respond to *any* form of disruption, regardless of its severity. This scenario provides a safe, controlled environment to test the end-to-end workflow of a chaos experiment, from initiation to detection and potential automated response, ensuring that your infrastructure is prepared to react effectively to unexpected events before more impactful experiments are conducted.

## Use cases

*   **Validating Chaos Platform Functionality**: Use this scenario to confirm that the chaos injection framework is correctly configured and capable of targeting specific Kubernetes `namespace` values, including those specified using regular expressions, without causing actual service interruptions.
*   **Testing Monitoring and Alerting Systems**: Deploy a dummy disruption to verify that your observability stack—including metrics, logs, and alerts—accurately detects and reports the presence of *any* disruption within the specified `namespace`, ensuring timely notification for operational teams.
*   **Onboarding and Training**: Provide new team members with a safe, hands-on experience of running a chaos experiment, allowing them to understand the process and observe system reactions without fear of impacting production services.
*   **Parameter Validation**: Confirm that scenario parameters, such as `duration`, are correctly interpreted and applied by the chaos engine, ensuring that disruptions start and end precisely as intended.
*   **Pre-flight Checks**: Integrate this scenario into automated pre-flight checks before executing more aggressive chaos experiments, ensuring the environment is stable and the chaos platform is ready for action.

## Configuration

Detailed configuration options for the `dummy-disruption` scenario
