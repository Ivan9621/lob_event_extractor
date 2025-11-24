from lob_event_extractor import parse_file
for idx, evs, mid in parse_file("example.ndjson", max_depth=10):
    print("LINE", idx, "MID", mid)
    for e in evs:
        print(" ", e)
