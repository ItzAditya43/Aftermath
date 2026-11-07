"""Customer support operations: ticket surges, SLA breaches and the product problem behind them."""
from ..signatures import Category, Role, Signature
from . import Pack

PACK = Pack("support", "Customer support incidents",
            "Ticket surges, SLA breaches, complaint clusters, product defects behind them, support tooling outages", (
    Signature(
        "product_defect", "product bug / defect behind the contacts", Category.CUSTOMER_SUPPORT, Role.ORIGIN,
        (r"\b(?:confirmed|found|identified) (?:a|an|the) [\w -]{0,30}?\b(?:bug|defect|regression|outage)\b",
         r"\b(?:bug|defect|regression) in (?:the )?[\w -]{0,30}?(?:service|app|system|checkout|release|website)"),
        checks=("Get the engineering incident or bug id and the exact customer-visible symptom",),
        mitigations=("Give agents a macro with the known issue, workaround and expected fix time",),
        fixes=("Fix the defect, then reach out proactively to every affected customer",),
        avoid=("Don't just add agents to the queue: the volume only drops once the shared cause is fixed",),
        causes=("complaint_cluster", "ticket_surge"),
        tool_hints=("github", "logs"),
    ),
    Signature(
        "complaint_cluster", "many customers reporting the same problem", Category.CUSTOMER_SUPPORT, Role.MECHANISM,
        (r"(?:many|most|majority of|hundreds of|thousands of) (?:the )?(?:tickets|customers|complaints|callers)[^.\n]{0,60}?"
         r"(?:same|reported|described|complain)", r"\b(?:charged twice|double[- ]charged|duplicate (?:charges?|payments?))\b"),
        checks=("Tag and count tickets by symptom to confirm one shared cause",),
        mitigations=("Post a status-page or in-app notice so customers don't need to contact support",),
        fixes=("Link every tagged ticket to the engineering incident and close them together when it's fixed",),
        avoid=("Don't handle a cluster ticket by ticket without escalating it to engineering",),
        causes=("ticket_surge", "sla_breach"),
        tool_hints=("metrics",),
    ),
    Signature(
        "ticket_surge", "ticket / contact volume surge", Category.CUSTOMER_SUPPORT, Role.SYMPTOM,
        (r"\b[\d,]+ (?:tickets|contacts|calls|chats)\b[^.\n]{0,60}?(?:times|normal|usual)",
         r"(?:spike|surge) in (?:tickets|contacts|calls|complaints|chats)", r"ticket (?:volume|spike|surge)",
         r"\b(?:\d+(?:\.\d+)?|two|three|four|five|six|seven|eight|nine|ten) times (?:the )?normal (?:ticket |contact |call )?volume"),
        checks=("Compare volume by channel and topic with the same hour last week",),
        mitigations=("Turn on overflow routing and pause non-urgent queues",),
        fixes=("Bring volume back to normal by fixing the cause and notifying affected customers",),
        tool_hints=("metrics",),
    ),
    Signature(
        "sla_breach", "support SLA breach / long waits", Category.CUSTOMER_SUPPORT, Role.SYMPTOM,
        (r"\bsla (?:was |were )?breach\w*", r"breach\w* (?:the |our )?(?:first[- ]response |resolution )?sla",
         r"(?:wait|queue|response) times? (?:in [\w ]{0,20})?(?:rose|grew|increased|reached|hit)[^.\n]{0,20}?\d+ ?(?:min|minutes|hours)"),
        checks=("List breached tickets by priority so the most urgent customers are answered first",),
        mitigations=("Send an acknowledgement with the known issue to every breached ticket",),
        fixes=("Recover the queue after the cause is fixed and review staffing for the next surge",),
        tool_hints=("metrics",),
    ),
    Signature(
        "support_tool_outage", "helpdesk / phone / chat tooling outage", Category.CUSTOMER_SUPPORT, Role.ORIGIN,
        (r"(?:helpdesk|ticketing (?:system|tool)|\bcrm\b|zendesk|freshdesk|\bivr\b|phone system|live chat|chatbot|contact centre platform)"
         r"[^.\n]{0,40}?(?:down|outage|unavailable|offline|not (?:working|loading)|failed)",),
        checks=("Check the vendor status page and which channels are affected",),
        mitigations=("Move customers to a working channel and post the change on the status page",),
        fixes=("Restore the tool, then import or replay contacts captured during the outage",),
        avoid=("Don't leave customers on a broken channel without telling them where to go",),
        causes=("sla_breach",),
        tool_hints=("health",),
    ),
))
