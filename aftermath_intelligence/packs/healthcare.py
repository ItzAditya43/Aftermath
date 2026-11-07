"""Healthcare operations: clinical systems and equipment failures described in shift and incident reports.

Operational guidance only (downtime procedures, escalation, quarantine, audit). Nothing here is clinical advice;
every action defers to local policy and the clinical team.
"""
from ..signatures import Category, Role, Signature
from . import Pack

PACK = Pack("healthcare", "Healthcare operations incidents",
            "EHR downtime, dispensing cabinets, medical devices, cold chain, lab interfaces, patient identification", (
    Signature(
        "ehr_downtime", "EHR / clinical system downtime", Category.HEALTHCARE, Role.ORIGIN,
        (r"\b(?:ehr|emr|electronic (?:health|medical|patient) records?|clinical system|epic|cerner)\b[^.\n]{0,80}?"
         r"\b(?:unavailable|down|outage|offline|not (?:loading|responding|available)|very slow|slow to load|crash\w*)",
         r"\b(?:ehr|emr) (?:downtime|outage)\b", r"\bdowntime (?:forms|procedures?|reports?|mode)\b"),
        checks=("Confirm which units and functions are affected and when the system last worked (downtime log)",
                "Check the change calendar for patches, upgrades or network work just before the onset"),
        mitigations=("Move affected units to downtime procedures (paper MAR, downtime census and medication reports)",
                     "Tell every affected unit and the on-call clinical lead which functions are down and the expected time"),
        fixes=("Restore the failed component (roll back the change if one preceded it), then reconcile paper records "
               "into the chart",),
        avoid=("Don't restart ward workstations one by one when the clinical system itself is down",
               "Never back-enter medication times from memory: reconcile from the paper downtime record"),
        causes=("medication_delay", "dispensing_cabinet_fault", "lab_result_delay"),
        tool_hints=("config", "health", "change"),
    ),
    Signature(
        "dispensing_cabinet_fault", "automated dispensing cabinet fault / override mode", Category.HEALTHCARE, Role.MECHANISM,
        (r"dispensing cabinets?[^.\n]{0,80}?(?:fault|offline|override|could not|unable|down|not verify)",
         r"\b(?:pyxis|omnicell)\b[^.\n]{0,60}?(?:fault|offline|down|override)", r"override[- ]only mode"),
        checks=("Check whether the cabinet lost its link to the pharmacy order system or failed itself",),
        mitigations=("Use pharmacist-verified overrides with a witness, per local policy",),
        fixes=("Restore the cabinet's order interface and audit every item pulled on override during the downtime",),
        avoid=("Don't leave cabinets in override-only mode once orders verify again: audit and close it",),
        causes=("medication_delay",),
        tool_hints=("health", "config"),
    ),
    Signature(
        "medication_delay", "delayed or missed medication doses", Category.HEALTHCARE, Role.SYMPTOM,
        (r"medication (?:rounds?|doses?|administration)[^.\n]{0,60}?\b(?:were |was )?(?:delayed|late|missed)",
         r"\b(?:doses?|medications?) (?:were |was )?(?:given|administered) late", r"\bmissed (?:doses?|medications?)\b",
         r"\bdelayed (?:doses?|medications?|medication rounds?)\b"),
        checks=("List patients with late or missed doses and flag the time-critical medications for the clinical team",),
        mitigations=("Have the clinical team prioritise time-critical doses using the paper record",),
        fixes=("Review every late or missed dose with pharmacy and record it in the incident system",),
        avoid=("Don't give a delayed dose without checking the paper record for whether it was already given",),
        tool_hints=("health",),
    ),
    Signature(
        "medical_device_fault", "medical device fault / alarm failure", Category.HEALTHCARE, Role.ORIGIN,
        (r"(?:infusion pump|syringe driver|ventilator|patient monitor|telemetry (?:monitor|unit)|defibrillator)"
         r"[^.\n]{0,60}?(?:fault|failure|malfunction\w*|error code|stopped|did not alarm|alarm failed)",),
        checks=("Record the device asset number, its last service date, and whether other units of the same model are affected",),
        mitigations=("Swap in a replacement device and keep the faulty one untouched for biomedical engineering",),
        fixes=("Have biomedical engineering investigate; report to the manufacturer or regulator where required",),
        avoid=("Don't return a device that faulted to service before biomedical engineering clears it",),
        tool_hints=("sensor", "health"),
    ),
    Signature(
        "cold_chain_excursion", "vaccine / medication fridge temperature excursion", Category.HEALTHCARE, Role.ORIGIN,
        (r"(?:vaccine|medication|blood|drug|pharmacy|insulin)s?[^.\n]{0,30}?(?:fridge|refrigerator|freezer)[^.\n]{0,60}?"
         r"(?:temperature|excursion|out of range|above|below|°c)", r"cold[- ]chain (?:breach|excursion|failure)"),
        checks=("Record the minimum and maximum temperatures and how long stock was out of range",),
        mitigations=("Quarantine the affected stock and label it 'do not use' until pharmacy decides",),
        fixes=("Repair or replace the unit and add continuous temperature monitoring with alerts",),
        avoid=("Don't use or discard stock before pharmacy has assessed the excursion",),
        tool_hints=("sensor", "metrics"),
    ),
    Signature(
        "lab_result_delay", "lab results delayed / interface down", Category.HEALTHCARE, Role.SYMPTOM,
        (r"(?:lab|laboratory|pathology|radiology) (?:results?|reports?)[^.\n]{0,40}?(?:delayed|not (?:received|crossing|flowing)|missing)",
         r"\b(?:hl7|lis|ris) interface[^.\n]{0,40}?(?:down|failed|stopped|queu\w*|backlog)"),
        checks=("Check the interface queue and when the last result crossed into the chart",),
        mitigations=("Phone critical results to the ward until the interface recovers",),
        fixes=("Restart the interface, then confirm the whole backlog reached the chart",),
        avoid=("Don't assume results are normal because none arrived",),
        tool_hints=("health", "logs"),
    ),
    Signature(
        "patient_identification_error", "patient identification mismatch", Category.HEALTHCARE, Role.ORIGIN,
        (r"(?:wrong|mismatched|incorrect) (?:patient|wristband|armband|label|chart)",
         r"patient (?:identification|identity|id) (?:error|mismatch|issue)"),
        checks=("Trace where the identifiers diverged: registration, wristband printing, label printing or chart selection",),
        mitigations=("Re-verify identity with two identifiers for every affected patient and specimen",),
        fixes=("Fix the step where identities diverged and audit the other patients it touched",),
        avoid=("Don't correct the record silently: report it so the same gap is found elsewhere",),
        tool_hints=("config",),
    ),
))
