def pytest_report_collectionfinish(items):
    connected = {mark.args[0] for item in items
                 for mark in item.iter_markers("consumer")}
    return [f"UNCONNECTED CTR-{number:02}: no real consumer tests collected"
            for number in range(1, 6) if f"CTR-{number:02}" not in connected]
