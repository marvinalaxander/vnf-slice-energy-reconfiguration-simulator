from models import VNFProfile


# Flexible ranges are experimental parameters. They can be edited from the UI
# or replaced by measured/catalogue values without changing the simulator.
VNF_PROFILES = {
    "vDU": VNFProfile("vDU", ("RAN", "EDGE"), (4, 10), (6, 16), (10, 35), (4, 12), 5, (1, 4), True),
    "vCU": VNFProfile("vCU", ("RAN", "EDGE"), (4, 12), (8, 24), (15, 45), (3, 10), 10, (2, 6), True),
    "UPF": VNFProfile("UPF", ("EDGE", "CORE"), (4, 12), (6, 20), (10, 40), (5, 15), 12, (2, 10), True),
    "AMF": VNFProfile("AMF", ("CORE",), (2, 6), (4, 12), (8, 25), (1, 4), 30, (1, 4), True),
    "SMF": VNFProfile("SMF", ("CORE",), (2, 7), (4, 14), (8, 30), (1, 5), 30, (1, 5), True),
    "AUSF": VNFProfile("AUSF", ("CORE",), (1, 4), (2, 8), (5, 18), (0.5, 2), 40, (0.5, 2), True),
    "UDM": VNFProfile("UDM", ("CORE",), (2, 6), (6, 18), (20, 80), (1, 4), 35, (4, 18), True),
    "Firewall": VNFProfile("Firewall", ("EDGE", "CORE"), (2, 8), (4, 16), (5, 20), (3, 12), 20, (0.5, 3), True),
    "IDS_IPS": VNFProfile("IDS_IPS", ("EDGE", "CORE"), (4, 12), (8, 24), (15, 50), (4, 14), 22, (1, 6), True),
    "LoadBalancer": VNFProfile("LoadBalancer", ("EDGE", "CORE"), (2, 7), (4, 12), (5, 18), (4, 16), 15, (0.5, 2), True),
    "Cache": VNFProfile("Cache", ("EDGE", "CORE"), (3, 10), (12, 40), (50, 220), (4, 14), 18, (8, 40), True),
    "IoTGateway": VNFProfile("IoTGateway", ("EDGE", "CORE"), (2, 8), (4, 16), (10, 45), (1, 8), 25, (1, 8), True),
    "NAT": VNFProfile("NAT", ("EDGE", "CORE"), (2, 6), (3, 12), (5, 20), (3, 12), 22, (0.5, 2), True),
    "vRouter": VNFProfile("vRouter", ("TRANSPORT", "EDGE", "CORE"), (2, 8), (4, 14), (5, 25), (5, 18), 15, (0.5, 3), True),
}


SLICE_CHAINS = {
    "eMBB": ["vDU", "vCU", "UPF", "Cache", "Firewall"],
    "URLLC": ["vDU", "vCU", "UPF", "LoadBalancer", "Firewall"],
    "mMTC": ["vDU", "vCU", "IoTGateway", "AMF", "SMF", "UDM"],
}


SLICE_QOS = {
    "eMBB": {"latency_ms": 35, "jitter_ms": 10, "loss_pct": 1.0, "throughput_mbps": 100},
    "URLLC": {"latency_ms": 10, "jitter_ms": 3, "loss_pct": 0.1, "throughput_mbps": 30},
    "mMTC": {"latency_ms": 80, "jitter_ms": 20, "loss_pct": 2.0, "throughput_mbps": 10},
}

