"""Kubernetes and cloud platform failures beyond application crashes."""
from ..signatures import Category, Role, Signature
from . import Pack

PACK = Pack("kubernetes", "Kubernetes and cloud platform incidents",
            "Image pulls, unschedulable pods, node failures, evictions, quota and cloud-provider limits", (
    Signature(
        "image_pull_failure", "image pull failure", Category.DEPLOYMENT_REGRESSION, Role.ORIGIN,
        (r"imagepullbackoff", r"errimagepull", r"failed to pull image", r"manifest (?:for \S+ )?(?:unknown|not found)",
         r"pull access denied"),
        checks=("Check the image tag exists in the registry and the node can authenticate to it",),
        mitigations=("Roll back to the last image tag that pulled successfully",),
        fixes=("Fix the tag or registry credentials in the deployment pipeline",),
        avoid=("Deleting pods repeatedly: they will fail to pull the same image",),
        causes=("crash_loop",),
        tool_hints=("container", "deployment", "config"),
    ),
    Signature(
        "pod_unschedulable", "pods unschedulable (insufficient capacity)", Category.CAPACITY, Role.MECHANISM,
        (r"failedscheduling", r"0/\d+ nodes are available", r"insufficient (?:cpu|memory)", r"unschedulable",
         r"didn't match (?:pod's )?node (?:affinity|selector)"),
        checks=("Compare requested resources with allocatable capacity; check taints, affinity and quotas",),
        mitigations=("Add node capacity or lower non-critical workloads' replicas",),
        fixes=("Right-size resource requests and configure cluster autoscaling",),
        causes=("capacity_quota_exceeded",),
        tool_hints=("container", "resource", "metrics"),
    ),
    Signature(
        "node_not_ready", "node NotReady / node failure", Category.INFRASTRUCTURE, Role.ORIGIN,
        (r"node \S+ (?:status is now: )?notready", r"nodenotready", r"kubelet stopped posting node status",
         r"node (?:is )?unreachable", r"instance (?:terminated|retired|stopped) by (?:the )?(?:cloud|provider)"),
        checks=("Check the node's kubelet, container runtime and cloud instance status",),
        mitigations=("Cordon and drain the node so workloads reschedule",),
        fixes=("Replace the node; fix the kubelet/runtime or capacity cause",),
        causes=("pod_evicted", "crash_loop", "upstream_timeout"),
        tool_hints=("container", "health", "infrastructure"),
    ),
    Signature(
        "pod_evicted", "pod eviction (node pressure)", Category.RESOURCE_EXHAUSTION, Role.MECHANISM,
        (r"\bevicted\b", r"(?:disk|memory|pid)pressure", r"the node was low on resource", r"eviction threshold"),
        checks=("Check which resource was under pressure on the node and the largest consumers",),
        mitigations=("Free the pressured resource on the node or move workloads",),
        fixes=("Set requests/limits and ephemeral-storage limits so one workload cannot starve the node",),
        causes=("crash_loop",),
        tool_hints=("container", "resource", "metrics"),
    ),
    Signature(
        "capacity_quota_exceeded", "cloud quota / resource limit exceeded", Category.CAPACITY, Role.ORIGIN,
        (r"quota exceeded", r"exceeded (?:your |the )?quota", r"limitexceeded", r"resource_exhausted: quota",
         r"insufficientinstancecapacity", r"forbidden: exceeded quota"),
        checks=("Identify which quota is exhausted and what consumed it",),
        mitigations=("Request a quota increase or release unused resources",),
        fixes=("Alert on quota usage before the limit and clean up leaked resources",),
        tool_hints=("config", "resource", "deployment"),
    ),
))
