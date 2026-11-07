"""Network and telecom: links, routing, DNS, packet loss and latency."""
from ..signatures import Category, Role, Signature
from . import Pack

PACK = Pack("network", "Network and telecom incidents",
            "Link and interface failures, BGP/routing, DNS outages, packet loss, latency and jitter", (
    Signature(
        "link_down", "link / interface down or flapping", Category.NETWORK, Role.ORIGIN,
        (r"%link-3-updown", r"interface \S+,? changed state to down", r"link (?:is )?down", r"line protocol on interface \S+,? changed state to down",
         r"interface flap", r"carrier lost", r"los alarm|loss of signal"),
        checks=("Check interface error counters, optics levels and the peer's view of the link",),
        mitigations=("Shift traffic to a redundant path while the link is investigated",),
        fixes=("Replace the faulty optic/cable/port; fix the physical or configuration fault",),
        avoid=("Don't keep bouncing the interface: flapping can trigger route dampening upstream",),
        causes=("bgp_session_down", "packet_loss", "network_failure"),
        tool_hints=("network", "metrics"),
    ),
    Signature(
        "bgp_session_down", "BGP / routing session down", Category.NETWORK, Role.MECHANISM,
        (r"bgp[- ]5[- ]adjchange.*down", r"bgp (?:session|neighbor|peer) \S* ?(?:is )?down", r"neighbor \S+ down",
         r"route withdraw", r"ospf.*(?:adjacency|neighbor) (?:down|lost)", r"hold timer expired"),
        checks=("Check the session state on both peers and the reason code (hold timer, notification, config)",
                "Compare advertised/received prefixes before and after"),
        mitigations=("Prefer an alternate peer or path while the session is down",),
        fixes=("Correct the peer configuration, filters or the underlying link fault",),
        avoid=("Don't clear all BGP sessions at once to fix one peer",),
        causes=("packet_loss", "network_failure", "upstream_timeout"),
        tool_hints=("network", "config"),
    ),
    Signature(
        "dns_outage", "DNS resolution outage", Category.NETWORK, Role.MECHANISM,
        (r"servfail", r"dns (?:resolution )?(?:timeout|timed out|failure|failed)", r"no servers could be reached",
         r"could not resolve host", r"dns server .* not responding"),
        checks=("Resolve the failing names against each resolver directly; check resolver health and upstream",),
        mitigations=("Fail over to a secondary resolver",),
        fixes=("Fix the resolver or zone fault; add resolver redundancy and monitoring",),
        avoid=("Don't hard-code IP addresses in production config as a workaround",),
        causes=("network_failure", "upstream_timeout"),
        tool_hints=("network", "dns", "health"),
    ),
    Signature(
        "packet_loss", "packet loss / high latency on the network path", Category.NETWORK, Role.MECHANISM,
        (r"packet loss[^\d]{0,12}(?:[3-9]|\d{2,})(?:\.\d+)?\s?%", r"\b(?:[3-9]|\d{2,})(?:\.\d+)?% packet loss",
         r"jitter (?:exceeded|high)", r"latency (?:spike|exceeded)", r"rtt (?:exceeded|high)", r"crc errors? (?:increasing|detected)"),
        checks=("Trace the path (mtr/traceroute) to find the hop where loss starts",),
        mitigations=("Reroute traffic away from the lossy segment",),
        fixes=("Fix the congested or faulty segment (capacity, hardware, QoS)",),
        causes=("upstream_timeout", "retry_storm"),
        tool_hints=("network", "metrics"),
    ),
))
