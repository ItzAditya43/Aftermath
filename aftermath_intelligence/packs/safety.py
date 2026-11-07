"""Workplace safety: injuries, near misses and the control failures behind them (from safety reports).

The failure chain looks for the control that failed (a defeated alarm, missing segregation, skipped lockout),
not just the event, so the plan fixes the system rather than blaming the person nearest to it.
"""
from ..signatures import Category, Role, Signature
from . import Pack

PACK = Pack("safety", "Workplace safety incidents",
            "Vehicle-pedestrian collisions, defeated safety devices, segregation failures, slips and falls, spills, lockout", (
    Signature(
        "safety_device_defeated", "safety device disabled / defeated", Category.SAFETY, Role.ORIGIN,
        (r"(?:alarm|interlock|guard|sensor|beacon|light curtain|limit switch|horn)s?[^.\n]{0,40}?"
         r"(?:disconnected|disabled|bypassed|removed|not working|defeated|missing|taped over)",),
        checks=("Inspect the device and find out when and why it was disabled (maintenance records, pre-use checks)",),
        mitigations=("Take the equipment out of service until the safety device is repaired and tested",),
        fixes=("Repair the device, add it to the pre-use check, and make defeating it a reportable event",),
        avoid=("Don't return the equipment to service until the safety device is repaired and tested",
               "Don't treat this as operator error alone: fix why the device could be defeated"),
        causes=("vehicle_pedestrian_collision", "injury"),
        tool_hints=("sensor", "config"),
    ),
    Signature(
        "segregation_failure", "no separation between people and vehicles", Category.SAFETY, Role.CONTRIBUTOR,
        (r"\bno (?:physical )?(?:barrier|segregation|separation)\b", r"walkway (?:was |were )?(?:blocked|obstructed)",
         r"(?:pedestrians?|workers?|staff) (?:were |was )?(?:using|walking in|in) the (?:forklift|vehicle|truck) (?:lane|aisle|route)"),
        checks=("Walk the route and mark where pedestrian and vehicle paths cross or are blocked",),
        mitigations=("Keep the walkway clear and stop pedestrian access to vehicle lanes in the area",),
        fixes=("Install physical barriers and crossing points; keep walkways free of stock",),
        avoid=("Don't rely on painted lines alone where vehicles reverse",),
        causes=("vehicle_pedestrian_collision",),
        tool_hints=("config",),
    ),
    Signature(
        "ignored_near_miss", "earlier near miss without follow-up", Category.SAFETY, Role.CONTRIBUTOR,
        (r"near[- ]miss[^.\n]{0,80}?(?:earlier|before|previous|prior|no action|not (?:acted|followed|actioned))",),
        checks=("Find the earlier near-miss report and what happened to it",),
        mitigations=("Review open near-miss reports for the same area now",),
        fixes=("Give every near miss an owner, an action and a due date, and review overdue ones weekly",),
        avoid=("Don't close a near miss without an action and an owner",),
        causes=("vehicle_pedestrian_collision", "slip_trip_fall"),
        tool_hints=("config",),
    ),
    Signature(
        "vehicle_pedestrian_collision", "vehicle / forklift struck a person", Category.SAFETY, Role.MECHANISM,
        (r"(?:forklift|reach truck|pallet truck|tug|vehicle|truck)[^.\n]{0,60}?(?:struck|hit|collided with|ran over|reversed into)"
         r"[^.\n]{0,30}?(?:pedestrian|worker|person|employee|colleague)", r"struck by (?:a |the )?(?:forklift|vehicle|truck)"),
        checks=("Reconstruct the movement: vehicle direction, speed, visibility and where the person was",),
        mitigations=("Cordon off the area and take the vehicle out of service",),
        fixes=("Fix the controls that failed (segregation, alarms, speed limits) before the area reopens",),
        causes=("injury",),
        tool_hints=("sensor",),
    ),
    Signature(
        "slip_trip_fall", "slip, trip or fall", Category.SAFETY, Role.MECHANISM,
        (r"\b(?:slipped|tripped|fell)\b[^.\n]{0,40}?\b(?:on|over|from|down|off)\b",
         r"fell from (?:a |the )?(?:ladder|height|scaffold|platform|roof|mezzanine)"),
        checks=("Record the surface, lighting, footwear and what the person was carrying",),
        mitigations=("Make the area safe (clean, sign, barrier) and keep it closed until it is",),
        fixes=("Remove the hazard at source (drainage, matting, edge protection, housekeeping schedule)",),
        causes=("injury",),
        tool_hints=("config",),
    ),
    Signature(
        "hazardous_release", "chemical / hazardous spill or release", Category.SAFETY, Role.ORIGIN,
        (r"(?:chemical|acid|alkali|solvent|diesel|fuel|oil|ammonia|chlorine|hazardous (?:substance|material))s?"
         r"[^.\n]{0,20}?(?:spill\w*|leak\w*|release\w*)",),
        checks=("Identify the substance and check its safety data sheet for exposure and clean-up",),
        mitigations=("Evacuate and isolate the area; contain the spill with the right kit and PPE",),
        fixes=("Fix the container or line that failed and review storage and handling",),
        avoid=("Don't clean up an unidentified substance without the safety data sheet and PPE",),
        causes=("injury",),
        tool_hints=("sensor",),
    ),
    Signature(
        "lockout_failure", "lockout / tagout not applied", Category.SAFETY, Role.ORIGIN,
        (r"(?:lockout|lock[- ]out|loto|tag[- ]?out)[^.\n]{0,40}?(?:not|wasn't|was not|missing|bypassed|skipped|removed)",),
        checks=("Check the isolation record and who held the locks",),
        mitigations=("Stop work on the equipment and apply a full isolation",),
        fixes=("Retrain on isolation, audit the procedure and add a sign-off before work starts",),
        avoid=("Never restart equipment until every lock is accounted for",),
        causes=("injury",),
        tool_hints=("config",),
    ),
    Signature(
        "ppe_not_used", "required PPE not worn", Category.SAFETY, Role.CONTRIBUTOR,
        (r"(?:not wearing|without) (?:a |the |their |his |her )?(?:ppe|hard hat|helmet|gloves|goggles|safety glasses|"
         r"harness|hi-?vis|safety boots|face shield)",),
        checks=("Check whether the right PPE was available, fitted and required for the task",),
        mitigations=("Stop the task until the right PPE is in use",),
        fixes=("Fix why PPE wasn't worn (availability, fit, comfort, supervision), not just the reminder",),
        causes=("injury",),
        tool_hints=("config",),
    ),
    Signature(
        "injury", "person injured", Category.SAFETY, Role.SYMPTOM,
        (r"(?:suffered|sustained|received)[^.\n]{0,40}?(?:fractur\w*|injur\w*|laceration|burns?|sprain|concussion|cuts?)",
         r"(?:taken|sent|admitted) to hospital", r"lost[- ]time injur\w*", r"\bneeded first aid\b"),
        checks=("Confirm the injured person's status and that the incident has been reported as required",),
        mitigations=("Make sure the injured person is cared for and the scene is preserved for investigation",),
        fixes=("Complete the investigation and act on the control failures it finds",),
        tool_hints=("config",),
    ),
))
