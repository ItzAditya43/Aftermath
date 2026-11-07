"""Data platforms: streaming lag, schema drift, failed or late batch jobs."""
from ..signatures import Category, Role, Signature
from . import Pack

PACK = Pack("data_pipeline", "Data pipeline incidents",
            "Consumer lag, schema drift, failed/late ETL runs and data-quality check failures", (
    Signature(
        "consumer_lag", "streaming consumer lag", Category.DATA_PIPELINE, Role.MECHANISM,
        (r"consumer (?:group )?lag", r"lag (?:is |of )?\d{4,}", r"records-lag-max", r"partition \S+ lag",
         r"rebalanc(?:e|ing) (?:loop|storm|in progress)"),
        checks=("Compare producer rate with consumer throughput per partition; look for a stuck partition",),
        mitigations=("Scale consumers up to the partition count or pause a noisy producer",),
        fixes=("Fix the slow consumer step (batching, I/O) or repartition the topic",),
        avoid=("Don't reset consumer offsets to latest: it silently drops unprocessed data",),
        causes=("job_sla_missed",),
        tool_hints=("metrics", "logs"),
    ),
    Signature(
        "schema_drift", "schema drift / parse failure", Category.DATA_PIPELINE, Role.ORIGIN,
        (r"schema (?:mismatch|evolution error|incompatible|drift)", r"unknown field", r"column \S+ (?:not found|does not exist)",
         r"failed to (?:parse|deserialize|decode) (?:record|message|row)", r"avro|protobuf .*(?:incompatible|invalid)"),
        checks=("Diff the producer's current schema against what the consumer expects; find the first bad record",),
        mitigations=("Route bad records to a dead-letter queue so the pipeline keeps flowing",),
        fixes=("Version the schema and enforce compatibility checks in the producer's CI",),
        avoid=("Don't skip failed records without a dead-letter queue: the data is lost silently",),
        causes=("job_sla_missed", "data_quality_failure"),
        tool_hints=("github", "config", "logs"),
    ),
    Signature(
        "job_sla_missed", "pipeline job failed or missed its SLA", Category.DATA_PIPELINE, Role.SYMPTOM,
        (r"dag run \S+ failed", r"task \S+ (?:failed|marked as failed)", r"sla (?:miss|missed|breach)",
         r"job \S+ (?:failed|exceeded (?:its )?(?:sla|deadline))", r"etl (?:run )?failed"),
        checks=("Find the first failing task and its error; check upstream data arrived on time",),
        mitigations=("Re-run the failed task after fixing its input (idempotently)",),
        fixes=("Fix the failing step; add retries with idempotent writes",),
        tool_hints=("logs", "deployment"),
    ),
    Signature(
        "data_quality_failure", "data-quality check failure", Category.DATA_PIPELINE, Role.SYMPTOM,
        (r"(?:data quality|dq|great[_ ]expectations) (?:check )?failed", r"null rate (?:exceeded|above)",
         r"row count (?:dropped|anomaly|below expected)", r"duplicate (?:keys|rows) detected"),
        checks=("Compare today's metric with its history and trace the bad rows to their source",),
        mitigations=("Stop downstream publishing of the affected tables",),
        fixes=("Fix the upstream cause and backfill the affected partitions",),
        avoid=("Don't lower the threshold just to make the check pass",),
        tool_hints=("metrics", "logs"),
    ),
))
